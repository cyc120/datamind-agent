"""Authenticated append-only native PDF preparation registrations.

This registry does not approve source authority, publish knowledge, or write any
legacy document/fact/vector tables. Hashes bind evidence, not a trusted-owner signature.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import re
import threading
from typing import Annotated

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ingestion import pdf_prepare as prepared

ROOT = Path(__file__).resolve().parents[1]
PREPARED_STORE = ROOT / 'data/object_store/pdf_prepared'
_VERIFY_SLOTS = threading.BoundedSemaphore(2)
REGISTRATION_LOCK_SECONDS = 5
_SHA = re.compile(r'^[a-f0-9]{64}$')
_DOC = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_ROLES = {'admin','viewer','reviewer','approver'}
BOUNDARIES = {'registration_only': True, 'indexed': False, 'knowledge_visible': False,
              'source_authority_verified': False, 'registrar_authentication_verified': True}
Id = Annotated[str, Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')]
Sha = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,frozen=True)
    request_id: Id
    document_id: Annotated[str, Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    title: Annotated[str, Field(min_length=1,max_length=200)]
    job_id: Id
    attempt_token: Id
    manifest_sha256: Sha
    previous_version: Annotated[int, Field(ge=0,le=2_147_483_646)]
    previous_source_sha256: Sha | None

    @field_validator('title')
    @classmethod
    def title_required(cls,value):
        value=value.strip()
        if not value: raise ValueError('PDF_TITLE_REQUIRED')
        return value

    @model_validator(mode='after')
    def previous_identity(self):
        if (self.previous_version == 0) != (self.previous_source_sha256 is None):
            raise ValueError('PDF_PREVIOUS_IDENTITY_INVALID')
        return self


def fail(code,status=409):
    raise HTTPException(status,detail={'error_code':code})


def digest(value):
    return prepared._sha(prepared._canonical(value))


def _payload(value):
    try: return RegistrationRequest.model_validate(value).model_dump()
    except (ValidationError,ValueError,TypeError): fail('PDF_REGISTRATION_REQUEST_INVALID',422)


@contextmanager
def _verification_slot():
    if not _VERIFY_SLOTS.acquire(blocking=False): fail('PDF_VERIFICATION_BUSY',503)
    try: yield
    finally: _VERIFY_SLOTS.release()


def _verify(job_id,attempt_token):
    try: return prepared.verify_prepared(PREPARED_STORE,job_id,attempt_token)
    except prepared.PdfPrepareError as exc: fail(exc.code)


def _snapshot(value):
    """Strict metadata checks only; never reads the filesystem."""
    try:
        keys={'schema_version','parser_version','request','request_sha256','source_path','bundle_path',
              'artifacts','limits','warnings','counts','manifest_sha256','verified',*prepared.BOUNDARIES}
        if not isinstance(value,dict) or set(value)!=keys: raise ValueError()
        request=prepared.PdfPrepareRequest.model_validate(value['request'])
        if (value['schema_version']!=prepared.SCHEMA_VERSION or value['parser_version']!=prepared.PARSER_VERSION
                or value['limits']!=dict(prepared.LIMITS) or value['warnings']!=list(prepared.WARNINGS)
                or value['verified'] is not True or value['request_sha256']!=prepared._request_digest(request)
                or any(type(value[k]) is not type(v) or value[k]!=v for k,v in prepared.BOUNDARIES.items())):
            raise ValueError()
        if not isinstance(value['manifest_sha256'],str) or not _SHA.fullmatch(value['manifest_sha256']): raise ValueError()
        manifest={k:v for k,v in value.items() if k not in ('manifest_sha256','verified')}
        if digest(manifest)!=value['manifest_sha256']: raise ValueError()
        prefix=f'attempts/{request.job_id}/{request.attempt_token}'
        source=f'sources/{request.source_sha256}.pdf';bundle=prefix+'/bundle.json'
        counts=value['counts']; limits={'page_count':prepared.MAX_PAGES,'block_count':prepared.MAX_TEXT_BLOCKS,
            'chunk_count':prepared.MAX_CHUNKS,'text_utf8_bytes':prepared.MAX_TEXT_BYTES,'total_pixels':prepared.MAX_TOTAL_PIXELS,
            'embedded_images':prepared.MAX_EMBEDDED_IMAGES,'embedded_total_pixels':prepared.MAX_EMBEDDED_TOTAL_PIXELS}
        if not isinstance(counts,dict) or set(counts)!=set(limits): raise ValueError()
        if any(type(counts[k]) is not int or not 0<=counts[k]<=maximum for k,maximum in limits.items()): raise ValueError()
        if min(counts[k] for k in ('page_count','block_count','chunk_count','text_utf8_bytes','total_pixels'))<1: raise ValueError()
        artifacts=value['artifacts']; expected=[source,bundle]+[f'{prefix}/pages/page-{i:04d}.png' for i in range(1,counts['page_count']+1)]
        if (value['source_path']!=source or value['bundle_path']!=bundle or not isinstance(artifacts,list)
                or len(artifacts)!=len(expected)): raise ValueError()
        for i,(artifact,path) in enumerate(zip(artifacts,expected)):
            kind,maximum=('source',prepared.MAX_PDF_BYTES) if i==0 else ('bundle',prepared.MAX_BUNDLE_BYTES) if i==1 else ('page_image',prepared.MAX_IMAGE_BYTES)
            if (not isinstance(artifact,dict) or set(artifact)!={'path','sha256','bytes','kind'} or artifact['path']!=path
                    or artifact['kind']!=kind or type(artifact['bytes']) is not int or not 1<=artifact['bytes']<=maximum
                    or not isinstance(artifact['sha256'],str) or not _SHA.fullmatch(artifact['sha256'])): raise ValueError()
        if artifacts[0]['sha256']!=request.source_sha256: raise ValueError()
        return request
    except (KeyError,TypeError,ValueError,ValidationError): fail('PDF_SNAPSHOT_EVIDENCE_INVALID')


def _registration_evidence(row):
    timestamp=row['created_at']
    if not isinstance(timestamp,datetime) or timestamp.tzinfo is None: raise ValueError()
    return {k:row[k] for k in ('document_id','document_version','source_sha256','parser_version','title',
        'request_id','job_id','attempt_token','manifest_sha256','snapshot_sha256','request_sha256',
        'registrar_user_id','registrar_label','registrar_roles')} | {'created_at':timestamp.astimezone(timezone.utc).isoformat()}


def _metadata(row):
    if row is None: fail('PDF_VERSION_NOT_FOUND',404)
    row=dict(row)
    try:
        request=_payload(row['request_payload']); native=_snapshot(row['snapshot'])
        roles=row['registrar_roles']
        if (digest(row['snapshot'])!=row['snapshot_sha256'] or digest(request)!=row['request_sha256']
                or request!=row['request_payload'] or row['request_id']!=request['request_id']
                or row['title']!=request['title'] or row['document_id']!=request['document_id']
                or type(row['document_version']) is not int or row['document_version']!=request['previous_version']+1
                or row['document_id']!=native.document_id or row['document_version']!=native.document_version
                or row['source_sha256']!=native.source_sha256 or row['parser_version']!=row['snapshot']['parser_version']
                or row['job_id']!=request['job_id'] or row['job_id']!=native.job_id
                or row['attempt_token']!=request['attempt_token'] or row['attempt_token']!=native.attempt_token
                or row['manifest_sha256']!=request['manifest_sha256'] or row['manifest_sha256']!=row['snapshot']['manifest_sha256']
                or not isinstance(row['registrar_user_id'],str) or not _ID.fullmatch(row['registrar_user_id'])
                or not isinstance(row['registrar_label'],str) or not row['registrar_label'].strip() or len(row['registrar_label'])>120
                or not isinstance(roles,list) or not roles or len(roles)!=len(set(roles)) or not set(roles)<=_ROLES or 'admin' not in roles
                or digest(_registration_evidence(row))!=row['registration_sha256']): raise ValueError()
    except HTTPException as exc:
        if exc.status_code==422: fail('PDF_REGISTRATION_EVIDENCE_INVALID')
        raise
    except (KeyError,TypeError,ValueError): fail('PDF_REGISTRATION_EVIDENCE_INVALID')
    row['_artifact_integrity_checked']=False
    return row


def public_version(row):
    row=_metadata(row)
    return {**{k:row[k] for k in ('document_id','document_version','source_sha256','parser_version','title',
        'manifest_sha256','registrar_user_id','registrar_label','registrar_roles','created_at')},
        'counts':row['snapshot']['counts'],'warnings':row['snapshot']['warnings'],**BOUNDARIES,
        'artifact_integrity_checked':bool(row.get('_verified_result'))}


def _identity(cur,identity):
    if not isinstance(identity,dict) or not isinstance(identity.get('user_id'),str) or not isinstance(identity.get('_session_hash'),str):
        fail('SESSION_INVALID',401)
    # LockRows may wait after its WHERE expression was evaluated. Capture the
    # expiry and check a new wall-clock value after *both* account/session locks.
    cur.execute('''SELECT u.user_id,u.display_name,u.roles,u.active,s.revoked_at,s.expires_at
        FROM auth_users u JOIN auth_sessions s USING(user_id)
        WHERE u.user_id=%s AND s.token_hash=%s FOR SHARE OF u,s''',(identity['user_id'],identity['_session_hash']))
    actor=cur.fetchone()
    if not actor: fail('SESSION_INVALID',401)
    cur.execute('SELECT clock_timestamp() AS checked_at');checked_at=cur.fetchone()['checked_at']
    if not actor['active'] or actor['revoked_at'] is not None or actor['expires_at']<=checked_at: fail('SESSION_INVALID',401)
    if 'admin' not in actor['roles']: fail('PERMISSION_DENIED',403)
    return {k:actor[k] for k in ('user_id','display_name','roles')}


def _bounded_database_lock(function):
    @wraps(function)
    def bounded(*args,**kwargs):
        try: return function(*args,**kwargs)
        except psycopg2.errors.LockNotAvailable: fail('PDF_REGISTRATION_BUSY',503)
    return bounded


@_bounded_database_lock
def register(payload,dsn,identity):
    request=_payload(payload)
    with _verification_slot():
        snapshot=_verify(request['job_id'],request['attempt_token'])
        native=_snapshot(snapshot)
        if (snapshot['manifest_sha256']!=request['manifest_sha256'] or native.document_id!=request['document_id']
                or native.document_version!=request['previous_version']+1): fail('PDF_PREPARED_IDENTITY_MISMATCH')
        with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SET LOCAL lock_timeout = %s',(str(REGISTRATION_LOCK_SECONDS)+'s',))
            locks=['pdf-document:'+request['document_id'],'pdf-request:'+request['request_id'],
                   'pdf-attempt:'+digest([request['job_id'],request['attempt_token']])]
            for key in sorted(locks): cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
            actor=_identity(cur,identity)
            cur.execute('SELECT * FROM pdf_snapshot_versions WHERE request_id=%s',(request['request_id'],))
            previous_request=cur.fetchone()
            if previous_request:
                prior=_metadata(previous_request)
                if prior['request_payload']!=request or prior['registrar_user_id']!=actor['user_id']: fail('PDF_REQUEST_ID_REUSED')
                if prior['snapshot']!=snapshot: fail('PDF_SNAPSHOT_CHANGED')
                prior['_verified_result']=snapshot
                return public_version(prior)
            cur.execute('SELECT request_id FROM pdf_snapshot_versions WHERE job_id=%s AND attempt_token=%s',
                        (request['job_id'],request['attempt_token']))
            if cur.fetchone(): fail('PDF_ATTEMPT_ALREADY_REGISTERED')
            cur.execute('SELECT current_version FROM pdf_snapshot_assets WHERE document_id=%s FOR UPDATE',(request['document_id'],))
            asset=cur.fetchone();current=asset['current_version'] if asset else 0;prior=None
            if asset:
                cur.execute('SELECT * FROM pdf_snapshot_versions WHERE document_id=%s AND document_version=%s',(request['document_id'],current))
                prior=_metadata(cur.fetchone())
            if request['previous_version']!=current or request['previous_source_sha256']!=(prior['source_sha256'] if prior else None):
                fail('PDF_VERSION_CHANGED')
            row={**request,'document_version':native.document_version,'source_sha256':native.source_sha256,
                 'parser_version':snapshot['parser_version'],'request_payload':request,'request_sha256':digest(request),
                 'snapshot':snapshot,'snapshot_sha256':digest(snapshot),'registrar_user_id':actor['user_id'],
                 'registrar_label':actor['display_name'],'registrar_roles':actor['roles'],'created_at':datetime.now(timezone.utc)}
            row['registration_sha256']=digest(_registration_evidence(row))
            if not asset: cur.execute('INSERT INTO pdf_snapshot_assets(document_id,current_version) VALUES (%s,%s)',(request['document_id'],native.document_version))
            columns=['document_id','document_version','source_sha256','parser_version','title','request_id','job_id','attempt_token',
                     'manifest_sha256','snapshot','snapshot_sha256','request_payload','request_sha256','registrar_user_id',
                     'registrar_label','registrar_roles','created_at','registration_sha256']
            values=[Json(row[k]) if k in ('snapshot','request_payload') else row[k] for k in columns]
            cur.execute('INSERT INTO pdf_snapshot_versions('+','.join(columns)+') VALUES ('+','.join(['%s']*len(columns))+') RETURNING *',values)
            row=cur.fetchone()
            if asset:
                cur.execute('UPDATE pdf_snapshot_assets SET current_version=%s WHERE document_id=%s AND current_version=%s',
                            (native.document_version,request['document_id'],current))
                if cur.rowcount!=1: fail('PDF_VERSION_CHANGED')
            row['_verified_result']=snapshot
            return public_version(row)


def read_version(document_id,dsn,version=None):
    if not isinstance(document_id,str) or not _DOC.fullmatch(document_id): fail('PDF_DOCUMENT_ID_INVALID',422)
    if version is not None and (type(version) is not int or version<1): fail('PDF_VERSION_INVALID',422)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT v.* FROM pdf_snapshot_versions v JOIN pdf_snapshot_assets a USING(document_id)
            WHERE v.document_id=%s AND v.document_version=COALESCE(%s,a.current_version)''',(document_id,version))
        row=_metadata(cur.fetchone())
    with _verification_slot():
        verified=_verify(row['job_id'],row['attempt_token'])
        if verified!=row['snapshot']: fail('PDF_SNAPSHOT_CHANGED')
        row['_verified_result']=verified
    return row


def read_artifact(row,kind,page_no=None):
    row=_metadata(row)
    if kind not in ('source','bundle','page_image'): fail('PDF_ARTIFACT_KIND_INVALID',422)
    if kind=='page_image':
        if type(page_no) is not int or not 1<=page_no<=row['snapshot']['counts']['page_count']: fail('PDF_PAGE_NOT_FOUND',404)
        index=page_no+1
    else:
        if page_no is not None: fail('PDF_ARTIFACT_PAGE_INVALID',422)
        index=0 if kind=='source' else 1
    artifact=row['snapshot']['artifacts'][index]
    maximum={'source':prepared.MAX_PDF_BYTES,'bundle':prepared.MAX_BUNDLE_BYTES,'page_image':prepared.MAX_IMAGE_BYTES}[kind]
    with _verification_slot():
        try:
            with prepared._open_directory(PREPARED_STORE) as root, prepared._lock(root,prepared._attempt_lock_name(row['job_id'],row['attempt_token']),shared=True):
                data=prepared._artifact(root,artifact['path'],maximum)
        except prepared.PdfPrepareError as exc: fail(exc.code)
        if len(data)!=artifact['bytes'] or prepared._sha(data)!=artifact['sha256']: fail('PDF_ARTIFACT_TAMPERED')
    return data


def _pagination(limit,offset):
    if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=10000:
        fail('PDF_PAGINATION_INVALID',422)


def list_assets(dsn,limit=20,offset=0):
    _pagination(limit,offset)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT v.* FROM pdf_snapshot_assets a JOIN pdf_snapshot_versions v
            ON v.document_id=a.document_id AND v.document_version=a.current_version
            ORDER BY v.created_at DESC,v.document_id LIMIT %s OFFSET %s''',(limit+1,offset))
        rows=cur.fetchall()
    return {'snapshots':[public_version(row) for row in rows[:limit]],'limit':limit,'offset':offset,'has_more':len(rows)>limit}


def history(document_id,dsn,limit=20,offset=0):
    if not isinstance(document_id,str) or not _DOC.fullmatch(document_id): fail('PDF_DOCUMENT_ID_INVALID',422)
    _pagination(limit,offset)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        conn.set_session(isolation_level='REPEATABLE READ')
        cur.execute('SELECT current_version FROM pdf_snapshot_assets WHERE document_id=%s',(document_id,))
        asset=cur.fetchone()
        if not asset: fail('PDF_ASSET_NOT_FOUND',404)
        cur.execute('SELECT * FROM pdf_snapshot_versions WHERE document_id=%s ORDER BY document_version DESC LIMIT %s OFFSET %s',
                    (document_id,limit+1,offset));rows=cur.fetchall()
    return {'document_id':document_id,'current_version':asset['current_version'],
            'versions':[public_version(row) for row in rows[:limit]],'limit':limit,'offset':offset,'has_more':len(rows)>limit}
