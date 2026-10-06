"""Authenticated registration and source-bound, append-only CAD version reads.

Content copies are published atomically; version/pointer changes share a locked
database transaction. Database owners can still manage stored records.
"""
import hashlib
import json
import os
from pathlib import Path
import uuid

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import Json, RealDictCursor

from .auth import lock_identity
from .knowledge import ROOT
from ingestion.cad_contract import DrawingSnapshot
from ingestion.cad_dxf import CadReadError, MAX_BYTES, PARSER_VERSION, parse_bytes

STAGING_ROOTS = {'synthetic_demo': ROOT/'data/cad_demo/development',
                 'authorized_external': ROOT/'data/cad_demo/authorized'}
SOURCE_STORE = ROOT/'data/object_store/cad_sources'


def fail(code, status=409):
    raise HTTPException(status, detail={'error_code': code})


def digest(payload):
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),
                                     allow_nan=False).encode()).hexdigest()


def staged_path(relative, provenance):
    if Path(relative).is_absolute():fail('CAD_STAGED_SOURCE_INVALID',422)
    path=(ROOT/relative).resolve()
    allowed=STAGING_ROOTS[provenance].resolve()
    if (not path.is_relative_to(ROOT.resolve()) or not path.is_relative_to(allowed)
            or path.suffix.lower()!='.dxf' or not path.is_file()):
        fail('CAD_STAGED_SOURCE_INVALID',422)
    return path


def sources():
    items=[]
    for provenance,folder in STAGING_ROOTS.items():
        if not folder.is_dir():continue
        for source in sorted(folder.glob('*.dxf'))[:200]:
            path=source.resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_relative_to(ROOT.resolve()) or not path.is_file():continue
            with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
            if not data or len(data)>MAX_BYTES:continue
            items.append({'source_path':str(path.relative_to(ROOT.resolve())), 'name':path.name,
                          'source_sha256':hashlib.sha256(data).hexdigest(),'provenance':provenance})
    return items


def source_bytes(sha):
    # Paths are derived from a validated hash, never from stored object_uri.
    import re
    if not re.fullmatch(r'[a-f0-9]{64}',sha):fail('CAD_SOURCE_IDENTITY_INVALID')
    path=(SOURCE_STORE/(sha+'.dxf')).resolve()
    if not path.is_relative_to(SOURCE_STORE.resolve()) or not path.is_file():fail('CAD_SOURCE_UNAVAILABLE',404)
    with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES or hashlib.sha256(data).hexdigest()!=sha:fail('CAD_SOURCE_VERSION_MISMATCH')
    return data


def persist_source(data,sha):
    SOURCE_STORE.mkdir(parents=True,exist_ok=True)
    target=SOURCE_STORE/(sha+'.dxf')
    temporary=SOURCE_STORE/('.'+sha+'-'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary,target)
        except FileExistsError:source_bytes(sha)
    finally:
        temporary.unlink(missing_ok=True)
    source_bytes(sha)


def checked(row):
    if not row:fail('CAD_VERSION_NOT_FOUND',404)
    row=dict(row);bundle=row['bundle']
    try:
        if digest(bundle)!=row['bundle_sha256']:fail('CAD_SNAPSHOT_HASH_MISMATCH')
        drawing=DrawingSnapshot.model_validate(bundle['snapshot'])
        if (drawing.source.document_id!=row['document_id'] or drawing.source.document_version!=row['document_version']
                or drawing.source.source_sha256!=row['source_sha256'] or drawing.source.parser_version!=row['parser_version']
                or drawing.source.provenance!=row['request_payload']['provenance']
                or bundle['metadata']['parser_version']!=row['parser_version']):
            fail('CAD_SNAPSHOT_IDENTITY_MISMATCH')
    except (ValueError,KeyError,TypeError):fail('CAD_SNAPSHOT_INVALID')
    data=source_bytes(row['source_sha256'])
    row['_source_bytes']=data
    return row


def public_version(row):
    return {**{key:row[key] for key in ('document_id','document_version','source_sha256','parser_version','title',
                                      'bundle_sha256','registrar_label','created_at')},
            'provenance':row['bundle']['snapshot']['source']['provenance']}


def register(payload,dsn,identity):
    payload=dict(payload);payload['title']=payload['title'].strip()
    if not payload['title']:fail('CAD_TITLE_REQUIRED',422)
    document_id=payload['document_id']
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'admin')
        for key in sorted(['cad-document:'+document_id,'cad-request:'+payload['request_id']]):
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
        cur.execute('SELECT * FROM cad_versions WHERE request_id=%s',(payload['request_id'],));prior=cur.fetchone()
        if prior:
            if prior['request_payload']!=payload or prior['registrar_user_id']!=actor['user_id']:
                fail('CAD_REQUEST_ID_REUSED')
            return public_version(checked(prior))
        cur.execute('SELECT current_version FROM cad_assets WHERE document_id=%s FOR UPDATE',(document_id,))
        asset=cur.fetchone();version=asset['current_version'] if asset else 0;previous=None
        if asset:
            cur.execute('SELECT * FROM cad_versions WHERE document_id=%s AND document_version=%s',(document_id,version))
            previous=checked(cur.fetchone())
        if (payload['previous_version']!=version
                or payload['previous_source_sha256']!=(previous['source_sha256'] if previous else None)):
            fail('CAD_VERSION_CHANGED')
        source=staged_path(payload['source_path'],payload['provenance'])
        with source.open('rb') as stream:data=stream.read(MAX_BYTES+1)
        if hashlib.sha256(data).hexdigest()!=payload['source_sha256']:fail('CAD_STAGED_SOURCE_CHANGED')
        if previous and previous['source_sha256']==payload['source_sha256']:fail('CAD_SOURCE_ALREADY_CURRENT')
        try:
            bundle=parse_bytes(data,document_id=document_id,document_version=version+1,provenance=payload['provenance'])
        except CadReadError as error:
            raise HTTPException(422,detail={'error_code':error.code,'details':error.details}) from error
        if previous:
            for field in ('drawing_id','part_id'):
                if previous['bundle']['snapshot']['identity'][field]['value']!=bundle['snapshot']['identity'][field]['value']:
                    fail('CAD_ASSET_IDENTITY_CHANGED')
        persist_source(data,payload['source_sha256'])
        if not asset:
            cur.execute('INSERT INTO cad_assets(document_id,current_version) VALUES (%s,%s)',(document_id,version+1))
        cur.execute('''INSERT INTO cad_versions(document_id,document_version,source_sha256,parser_version,title,
            request_id,request_payload,bundle,bundle_sha256,registrar_user_id,registrar_label,registrar_roles)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (document_id,version+1,payload['source_sha256'],PARSER_VERSION,payload['title'],payload['request_id'],
             Json(payload),Json(bundle),digest(bundle),actor['user_id'],actor['display_name'],actor['roles']))
        record=cur.fetchone()
        if asset:cur.execute('UPDATE cad_assets SET current_version=%s WHERE document_id=%s',(version+1,document_id))
        return public_version(record)


def read_version(document_id,dsn,version=None):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT v.* FROM cad_versions v JOIN cad_assets a USING(document_id)
            WHERE v.document_id=%s AND v.document_version=COALESCE(%s,a.current_version)''',(document_id,version))
        return checked(cur.fetchone())


def list_assets(dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT v.document_id,v.document_version,v.source_sha256,v.parser_version,v.title,
            v.bundle_sha256,v.registrar_label,v.created_at,v.bundle#>>'{snapshot,source,provenance}' AS provenance
            FROM cad_assets a JOIN cad_versions v
            ON v.document_id=a.document_id AND v.document_version=a.current_version ORDER BY v.created_at DESC LIMIT 200''')
        return [dict(row) for row in cur.fetchall()]


def history(document_id,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT current_version FROM cad_assets WHERE document_id=%s',(document_id,))
        asset=cur.fetchone()
        if not asset:fail('CAD_ASSET_NOT_FOUND',404)
        cur.execute('SELECT * FROM cad_versions WHERE document_id=%s ORDER BY document_version',(document_id,))
        return {'current_version':asset['current_version'],'versions':[public_version(checked(row)) for row in cur.fetchall()]}
