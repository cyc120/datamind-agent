"""Private native-PDF vector publication with a recoverable frozen-plan outbox.

Embedding and vector writes run outside metadata transactions. A database lease
fences every plan/result/head write after the row lock and a fresh clock check.
Partial or losing vector writes are retained; only an atomic published/head commit
makes this private publication readable. Public RAG and legacy tables are untouched.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import wraps
import re
import threading
from typing import Annotated
import uuid

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import pdf_publications as registry
from ingestion import pdf_prepare as prepared

LEASE_SECONDS=120
MAX_ATTEMPTS=3
GLOBAL_CONCURRENCY=2
LOCK_SECONDS=5
MAX_PLAN_BYTES=16*1024*1024
_PUB=re.compile(r'^pdf-pub-[a-f0-9]{32}$')
_SHA=re.compile(r'^[a-f0-9]{64}$')
_CODE=re.compile(r'^[A-Z0-9_]{1,96}$')
_BOUNDARIES={'public_knowledge_visible':False,'source_authority_verified':False}


class PublicationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    request_id: registry.Id
    document_id: Annotated[str,Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    document_version: Annotated[int,Field(ge=1,le=2_147_483_647)]
    expected_manifest_sha256: registry.Sha
    previous_publication_id: Annotated[str,Field(pattern=r'^pdf-pub-[a-f0-9]{32}$')] | None


def fail(code,status=409):
    raise HTTPException(status,detail={'error_code':code})


def _vectors():
    from . import pdf_vectors
    return pdf_vectors


def _payload(value):
    try: return PublicationRequest.model_validate(value).model_dump()
    except (ValidationError,TypeError,ValueError): fail('PDF_PUBLICATION_REQUEST_INVALID',422)


def _timestamp(value):
    if not isinstance(value,datetime) or value.tzinfo is None: raise ValueError()
    return value.astimezone(timezone.utc).isoformat()


def _evidence(row):
    return {k:row[k] for k in ('publication_id','request_id','document_id','document_version','source_sha256',
        'parser_version','manifest_sha256','previous_publication_id','owner_user_id','actor','request_sha256',
        'target_snapshot_sha256','target_registration_sha256')} | {'created_at':_timestamp(row['created_at'])}


def _result(row,published_at):
    return {'publication_id':row['publication_id'],'document_id':row['document_id'],
            'document_version':row['document_version'],'source_sha256':row['source_sha256'],
            'manifest_sha256':row['manifest_sha256'],'plan_sha256':row['vector_plan_sha256'],
            'chunk_count':row['target_snapshot']['counts']['chunk_count'],'published_at':published_at,
            'private_indexed':True,**_BOUNDARIES}


def _checked(row):
    if row is None: fail('PDF_PUBLICATION_NOT_FOUND',404)
    row=dict(row)
    try:
        payload=_payload(row['request_payload']);native=registry._snapshot(row['target_snapshot'])
        actor=row['actor']
        if (not isinstance(row['publication_id'],str) or not _PUB.fullmatch(row['publication_id'])
                or payload!=row['request_payload'] or registry.digest(payload)!=row['request_sha256']
                or row['request_id']!=payload['request_id'] or row['document_id']!=payload['document_id']
                or type(row['document_version']) is not int or row['document_version']!=payload['document_version']
                or row['manifest_sha256']!=payload['expected_manifest_sha256']
                or row['previous_publication_id']!=payload['previous_publication_id']
                or native.document_id!=row['document_id'] or native.document_version!=row['document_version']
                or native.source_sha256!=row['source_sha256'] or row['parser_version']!=row['target_snapshot']['parser_version']
                or row['manifest_sha256']!=row['target_snapshot']['manifest_sha256']
                or registry.digest(row['target_snapshot'])!=row['target_snapshot_sha256']
                or not isinstance(row['target_registration_sha256'],str) or not _SHA.fullmatch(row['target_registration_sha256'])
                or not isinstance(actor,dict) or set(actor)!={'user_id','display_name','roles'}
                or not isinstance(actor['user_id'],str) or not registry._ID.fullmatch(actor['user_id'])
                or actor['user_id']!=row['owner_user_id'] or not isinstance(actor['display_name'],str)
                or not actor['display_name'].strip() or len(actor['display_name'])>120
                or not isinstance(actor['roles'],list) or 'admin' not in actor['roles']
                or len(actor['roles'])!=len(set(actor['roles'])) or not set(actor['roles'])<=registry._ROLES
                or not isinstance(row['session_hash'],str) or not _SHA.fullmatch(row['session_hash'])
                or registry.digest(_evidence(row))!=row['evidence_sha256']
                or row['status'] not in ('queued','running','failed','published')
                or type(row['attempts']) is not int or not 0<=row['attempts']<=MAX_ATTEMPTS
                or type(row['total_attempts']) is not int or row['total_attempts']<row['attempts']
                or ((row['status']=='running')!=(row['lease_token'] is not None and row['lease_expires_at'] is not None))
                or (row['status']!='running' and (row['lease_token'] is not None or row['lease_expires_at'] is not None))
                or ((row['vector_plan'] is None)!=(row['vector_plan_sha256'] is None))
                or ((row['result'] is None)!=(row['result_sha256'] is None))): raise ValueError()
        if row['vector_plan'] is not None:
            if not isinstance(row['vector_plan'],dict) or registry.digest(row['vector_plan'])!=row['vector_plan_sha256']:
                raise ValueError()
        if row['status']=='published':
            result=row['result']
            if (not isinstance(result,dict) or row['vector_plan'] is None or registry.digest(result)!=row['result_sha256']
                    or not isinstance(result.get('published_at'),str)
                    or registry.digest(result)!=registry.digest(_result(row,result['published_at']))
                    or _timestamp(row['finished_at'])!=result['published_at']): raise ValueError()
        elif row['result'] is not None: raise ValueError()
        if row['error_code'] is not None and (not isinstance(row['error_code'],str) or not _CODE.fullmatch(row['error_code'])):
            raise ValueError()
        if row['status']=='running':
            if not isinstance(row['lease_token'],str) or not re.fullmatch(r'[a-f0-9]{32}',row['lease_token']): raise ValueError()
            _timestamp(row['lease_expires_at'])
        if row['status'] in ('running','failed','published'):
            if row['attempts']<1: raise ValueError()
            _timestamp(row['started_at'])
        if row['status']=='failed':
            if row['error_code'] is None: raise ValueError()
            _timestamp(row['finished_at'])
        if row['status'] in ('queued','running','published') and row['error_code'] is not None: raise ValueError()
    except HTTPException as exc:
        if exc.status_code==422: fail('PDF_PUBLICATION_EVIDENCE_INVALID')
        raise
    except (KeyError,TypeError,ValueError): fail('PDF_PUBLICATION_EVIDENCE_INVALID')
    return row


def public(row):
    row=_checked(row)
    return {**{k:row[k] for k in ('publication_id','request_id','document_id','document_version','source_sha256',
        'parser_version','manifest_sha256','previous_publication_id','status','attempts','total_attempts','error_code',
        'created_at','started_at','finished_at')},'owner':row['actor'],
        'plan_sha256':row['vector_plan_sha256'],'chunk_count':row['target_snapshot']['counts']['chunk_count'],
        'private_indexed':row['status']=='published',**_BOUNDARIES,
        'vector_integrity_checked':bool(row.get('_vectors_verified'))}


def _load(cur,publication_id,locked=False):
    cur.execute('SELECT * FROM pdf_knowledge_requests WHERE publication_id=%s'+(' FOR UPDATE' if locked else ''),(publication_id,))
    return _checked(cur.fetchone())


def _busy(function):
    @wraps(function)
    def guarded(*args,**kwargs):
        try: return function(*args,**kwargs)
        except psycopg2.errors.LockNotAvailable: fail('PDF_PUBLICATION_BUSY',503)
    return guarded


def _lock(cur,document_id,request_id=None):
    cur.execute('SET LOCAL lock_timeout=%s',(str(LOCK_SECONDS)+'s',))
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('pdf-document:'+document_id,))
    if request_id is not None:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('pdf-knowledge-request:'+request_id,))


def _snapshot_match(row,snapshot_row):
    snapshot_row=registry._metadata(snapshot_row)
    if (snapshot_row['document_id']!=row['document_id'] or snapshot_row['document_version']!=row['document_version']
            or snapshot_row['snapshot']!=row['target_snapshot'] or snapshot_row['snapshot_sha256']!=row['target_snapshot_sha256']
            or snapshot_row['registration_sha256']!=row['target_registration_sha256']): fail('PDF_PUBLICATION_SNAPSHOT_CHANGED')
    return snapshot_row


def _db_target(cur,row,current=False):
    cur.execute('SELECT * FROM pdf_snapshot_versions WHERE document_id=%s AND document_version=%s',
                (row['document_id'],row['document_version']))
    _snapshot_match(row,cur.fetchone())
    if current:
        cur.execute('SELECT current_version FROM pdf_snapshot_assets WHERE document_id=%s',(row['document_id'],))
        head=cur.fetchone()
        if not head or head['current_version']!=row['document_version']: fail('PDF_PUBLICATION_SNAPSHOT_CHANGED')


def _head(cur,document_id,locked=False):
    cur.execute('SELECT current_publication_id FROM pdf_knowledge_heads WHERE document_id=%s'+(' FOR UPDATE' if locked else ''),(document_id,))
    head=cur.fetchone()
    return head['current_publication_id'] if head else None


def _baseline(cur,row,locked=False):
    if _head(cur,row['document_id'],locked)!=row['previous_publication_id']: fail('PDF_PUBLICATION_HEAD_CHANGED')


@_busy
def submit(payload,dsn,identity):
    request=_payload(payload)
    # Resolve owner/payload reuse before potentially reading a different target.
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,request['document_id'],request['request_id']);actor=registry._identity(cur,identity)
        cur.execute('SELECT * FROM pdf_knowledge_requests WHERE request_id=%s',(request['request_id'],));prior=cur.fetchone()
        if prior:
            prior=_checked(prior)
            if prior['request_payload']!=request or prior['owner_user_id']!=actor['user_id']: fail('PDF_PUBLICATION_REQUEST_ID_REUSED')
    snapshot=registry.read_version(request['document_id'],dsn,request['document_version'])
    if snapshot['manifest_sha256']!=request['expected_manifest_sha256']: fail('PDF_PUBLICATION_SNAPSHOT_SELECTION_CHANGED')
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,request['document_id'],request['request_id']);actor=registry._identity(cur,identity)
        cur.execute('SELECT * FROM pdf_knowledge_requests WHERE request_id=%s',(request['request_id'],));prior=cur.fetchone()
        if prior:
            prior=_checked(prior)
            if prior['request_payload']!=request or prior['owner_user_id']!=actor['user_id']: fail('PDF_PUBLICATION_REQUEST_ID_REUSED')
            _snapshot_match(prior,snapshot);_db_target(cur,prior)
            return public(prior)
        row={**request,'publication_id':'pdf-pub-'+uuid.uuid4().hex,'manifest_sha256':snapshot['manifest_sha256'],
             'source_sha256':snapshot['source_sha256'],'parser_version':snapshot['parser_version'],
             'owner_user_id':actor['user_id'],'actor':actor,'session_hash':identity['_session_hash'],
             'request_payload':request,'request_sha256':registry.digest(request),'target_snapshot':snapshot['snapshot'],
             'target_snapshot_sha256':snapshot['snapshot_sha256'],'target_registration_sha256':snapshot['registration_sha256'],
             'created_at':datetime.now(timezone.utc)}
        _db_target(cur,row,current=True);_baseline(cur,row)
        row['evidence_sha256']=registry.digest(_evidence(row))
        columns=['publication_id','request_id','document_id','document_version','source_sha256','parser_version','manifest_sha256',
            'previous_publication_id','owner_user_id','actor','session_hash','request_payload','request_sha256','target_snapshot',
            'target_snapshot_sha256','target_registration_sha256','evidence_sha256','created_at']
        values=[Json(row[k]) if k in ('actor','request_payload','target_snapshot') else row[k] for k in columns]
        cur.execute('INSERT INTO pdf_knowledge_requests('+','.join(columns)+') VALUES ('+','.join(['%s']*len(columns))+') RETURNING *',values)
        return public(cur.fetchone())


def _row(dsn,publication_id):
    if not isinstance(publication_id,str) or not _PUB.fullmatch(publication_id): fail('PDF_PUBLICATION_ID_INVALID',422)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur: return _load(cur,publication_id)


def poll(publication_id,dsn):
    return public(_row(dsn,publication_id))


def listing(dsn,limit=20,offset=0):
    registry._pagination(limit,offset)
    # Plans are bounded individually but may still be large. A server-side
    # cursor avoids buffering a full page of vector JSON in libpq/Python.
    publications=[];has_more=False
    with psycopg2.connect(dsn) as conn,conn.cursor(name='pdf_index_list_'+uuid.uuid4().hex,cursor_factory=RealDictCursor) as cur:
        cur.itersize=1
        cur.execute('SELECT * FROM pdf_knowledge_requests ORDER BY created_at DESC,publication_id LIMIT %s OFFSET %s',(limit+1,offset))
        for position,row in enumerate(cur):
            if position==limit:
                has_more=True;break
            publications.append(public(row))
    return {'publications':publications,'limit':limit,'offset':offset,'has_more':has_more}


@_busy
def retry(publication_id,dsn,identity):
    selected=_row(dsn,publication_id)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,selected['document_id']);registry._identity(cur,identity);row=_load(cur,publication_id,locked=True)
        actor=registry._identity(cur,identity)
        if row['owner_user_id']!=actor['user_id']: fail('PDF_PUBLICATION_OWNER_REQUIRED',403)
        if row['status']!='failed': fail('PDF_PUBLICATION_NOT_FAILED')
        cur.execute("UPDATE pdf_knowledge_requests SET status='queued',attempts=0,session_hash=%s,error_code=NULL,finished_at=NULL WHERE publication_id=%s RETURNING *",
                    (identity['_session_hash'],publication_id));return public(cur.fetchone())


def _snapshot_bundle(row,dsn):
    snapshot=registry.read_version(row['document_id'],dsn,row['document_version'])
    _snapshot_match(row,snapshot)
    bundle=prepared._json(registry.read_artifact(snapshot,'bundle'))
    return snapshot,bundle


def read_chunks(publication_id,dsn,*,client=None):
    row=_row(dsn,publication_id)
    if row['status']!='published': fail('PDF_PUBLICATION_NOT_PUBLISHED')
    snapshot,bundle=_snapshot_bundle(row,dsn);vectors=_vectors()
    vectors.validate_plan(row['vector_plan'],snapshot,bundle,publication_id)
    vectors.verify_plan(row['vector_plan'],client=client)
    chunks=vectors.safe_chunks(row['vector_plan'])
    after=_row(dsn,publication_id)
    if (after['status']!='published' or after['evidence_sha256']!=row['evidence_sha256']
            or after['vector_plan_sha256']!=row['vector_plan_sha256'] or after['result_sha256']!=row['result_sha256']):
        fail('PDF_PUBLICATION_EVIDENCE_CHANGED')
    final_snapshot,final_bundle=_snapshot_bundle(after,dsn)
    vectors.validate_plan(after['vector_plan'],final_snapshot,final_bundle,publication_id)
    after['_vectors_verified']=True
    return {'publication':public(after),'chunks':chunks,'vector_integrity_checked':True}


def _current_selection(document_id,dsn):
    if not isinstance(document_id,str) or not registry._DOC.fullmatch(document_id): fail('PDF_DOCUMENT_ID_INVALID',422)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        conn.set_session(isolation_level='REPEATABLE READ')
        publication_id=_head(cur,document_id)
        if publication_id is None: fail('PDF_PUBLICATION_HEAD_NOT_FOUND',404)
        row=_load(cur,publication_id)
        if row['document_id']!=document_id or row['status']!='published': fail('PDF_PUBLICATION_HEAD_INVALID')
        _db_target(cur,row,current=True)
        return publication_id


def current(document_id,dsn):
    publication_id=_current_selection(document_id,dsn);result=poll(publication_id,dsn)
    if _current_selection(document_id,dsn)!=publication_id: fail('PDF_PUBLICATION_HEAD_CHANGED')
    return result


def _lease(cur,claim):
    row=_load(cur,claim['publication_id'],locked=True)
    cur.execute('SELECT clock_timestamp() AS checked_at');now=cur.fetchone()['checked_at']
    if row['status']!='running' or row['lease_token']!=claim['lease_token'] or row['lease_expires_at']<=now: return None
    return row


@_busy
def _claim(dsn,publication_id=None):
    if publication_id is not None and (not isinstance(publication_id,str) or not _PUB.fullmatch(publication_id)):
        fail('PDF_PUBLICATION_ID_INVALID',422)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SET LOCAL lock_timeout=%s',(str(LOCK_SECONDS)+'s',))
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('pdf-knowledge-claim-cap',0))")
        cur.execute("SELECT publication_id,document_id FROM pdf_knowledge_requests WHERE status='running' AND lease_expires_at<=clock_timestamp() ORDER BY document_id,publication_id")
        for expired in cur.fetchall():
            _lock(cur,expired['document_id']);row=_load(cur,expired['publication_id'],locked=True)
            cur.execute('SELECT clock_timestamp() AS checked_at');now=cur.fetchone()['checked_at']
            if row['status']!='running' or row['lease_expires_at']>now: continue
            exhausted=row['attempts']>=MAX_ATTEMPTS
            cur.execute('''UPDATE pdf_knowledge_requests SET status=%s,lease_token=NULL,lease_expires_at=NULL,
                error_code=%s,finished_at=%s WHERE publication_id=%s''',
                ('failed' if exhausted else 'queued','PDF_PUBLICATION_LEASE_RETRIES_EXHAUSTED' if exhausted else None,
                 now if exhausted else None,row['publication_id']))
        cur.execute("SELECT count(*) AS count FROM pdf_knowledge_requests WHERE status='running'")
        if cur.fetchone()['count']>=GLOBAL_CONCURRENCY: return None
        cur.execute("SELECT publication_id,document_id FROM pdf_knowledge_requests WHERE status='queued' AND (%s IS NULL OR publication_id=%s) ORDER BY created_at,publication_id LIMIT 1",(publication_id,publication_id))
        selected=cur.fetchone()
        if not selected: return None
        _lock(cur,selected['document_id']);row=_load(cur,selected['publication_id'],locked=True)
        if row['status']!='queued': return None
        token=uuid.uuid4().hex
        cur.execute("""UPDATE pdf_knowledge_requests SET status='running',attempts=attempts+1,total_attempts=total_attempts+1,
            lease_token=%s,lease_expires_at=clock_timestamp()+%s*interval '1 second',started_at=clock_timestamp(),finished_at=NULL,error_code=NULL
            WHERE publication_id=%s RETURNING *""",(token,LEASE_SECONDS,row['publication_id']))
        row=_checked(cur.fetchone())
        return {'publication_id':row['publication_id'],'document_id':row['document_id'],'lease_token':token,
                'identity':{'user_id':row['owner_user_id'],'_session_hash':row['session_hash']}}


@_busy
def _authorize(dsn,claim,*,baseline=False):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,claim['document_id']);registry._identity(cur,claim['identity']);row=_lease(cur,claim)
        if row is None: return None
        _db_target(cur,row,current=baseline)
        if baseline: _baseline(cur,row)
        registry._identity(cur,claim['identity'])
        cur.execute('SELECT clock_timestamp() AS checked_at')
        if row['lease_expires_at']<=cur.fetchone()['checked_at']: return None
        return row


@_busy
def _freeze_plan(dsn,claim,plan):
    encoded=prepared._canonical(plan)
    if len(encoded)>MAX_PLAN_BYTES: fail('PDF_VECTOR_PLAN_BYTE_LIMIT',422)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,claim['document_id']);registry._identity(cur,claim['identity']);row=_lease(cur,claim)
        if row is None: return None
        _db_target(cur,row)
        if row['vector_plan'] is not None:
            if row['vector_plan']!=plan: fail('PDF_VECTOR_PLAN_ALREADY_FROZEN')
            registry._identity(cur,claim['identity'])
            return row
        registry._identity(cur,claim['identity'])
        cur.execute('''UPDATE pdf_knowledge_requests SET vector_plan=%s,vector_plan_sha256=%s
            WHERE publication_id=%s AND status='running' AND lease_token=%s AND lease_expires_at>clock_timestamp() RETURNING *''',
                    (Json(plan),prepared._sha(encoded),claim['publication_id'],claim['lease_token']))
        saved=cur.fetchone()
        return _checked(saved) if saved else None


@_busy
def _publish(dsn,claim):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,claim['document_id']);registry._identity(cur,claim['identity']);row=_lease(cur,claim)
        if row is None: return False
        _db_target(cur,row,current=True);_baseline(cur,row,locked=True)
        if row['vector_plan'] is None: fail('PDF_VECTOR_PLAN_REQUIRED')
        if row['vector_plan_sha256']!=claim.get('plan_sha256'): fail('PDF_VECTOR_PLAN_CHANGED')
        registry._identity(cur,claim['identity'])
        cur.execute('SELECT clock_timestamp() AS published_at');now=cur.fetchone()['published_at']
        if row['lease_expires_at']<=now: return False
        result=_result(row,_timestamp(now))
        if row['previous_publication_id'] is None:
            cur.execute('INSERT INTO pdf_knowledge_heads(document_id,current_publication_id) VALUES(%s,%s)',(row['document_id'],row['publication_id']))
        else:
            cur.execute('UPDATE pdf_knowledge_heads SET current_publication_id=%s WHERE document_id=%s AND current_publication_id=%s',
                        (row['publication_id'],row['document_id'],row['previous_publication_id']))
            if cur.rowcount!=1: fail('PDF_PUBLICATION_HEAD_CHANGED')
        cur.execute("""UPDATE pdf_knowledge_requests SET status='published',lease_token=NULL,lease_expires_at=NULL,
            result=%s,result_sha256=%s,finished_at=%s,error_code=NULL WHERE publication_id=%s
            AND status='running' AND lease_token=%s AND lease_expires_at>clock_timestamp()""",
            (Json(result),registry.digest(result),now,row['publication_id'],claim['lease_token']))
        if cur.rowcount!=1: fail('PDF_PUBLICATION_LEASE_LOST')
        return True


@_busy
def _fail(dsn,claim,code):
    if not isinstance(code,str) or not _CODE.fullmatch(code): code='PDF_PUBLICATION_EXECUTION_FAILED'
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,claim['document_id']);row=_lease(cur,claim)
        if row is None: return False
        cur.execute("""UPDATE pdf_knowledge_requests SET status='failed',lease_token=NULL,lease_expires_at=NULL,
            error_code=%s,finished_at=clock_timestamp() WHERE publication_id=%s
            AND status='running' AND lease_token=%s AND lease_expires_at>clock_timestamp()""",(code,row['publication_id'],claim['lease_token']))
        return bool(cur.rowcount)


@_busy
def _renew(dsn,claim):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock(cur,claim['document_id']);row=_lease(cur,claim)
        if row is None: return False
        cur.execute("""UPDATE pdf_knowledge_requests SET lease_expires_at=clock_timestamp()+%s*interval '1 second'
            WHERE publication_id=%s AND status='running' AND lease_token=%s AND lease_expires_at>clock_timestamp()""",
                    (LEASE_SECONDS,row['publication_id'],claim['lease_token']));return bool(cur.rowcount)


def _heartbeat(dsn,claim,stop):
    while not stop.wait(max(1,LEASE_SECONDS/3)):
        try:
            if not _renew(dsn,claim): return
        except (psycopg2.Error,HTTPException): return


def _execute(dsn,claim,*,embedder=None,client=None):
    stop=threading.Event();heartbeat=threading.Thread(target=_heartbeat,args=(dsn,claim,stop),daemon=True);heartbeat.start()
    try:
        row=_authorize(dsn,claim,baseline=True)
        if row is None: return {'outcome':'lease_lost'}
        snapshot,bundle=_snapshot_bundle(row,dsn);vectors=_vectors()
        plan=row['vector_plan']
        if plan is None:
            plan=vectors.build_plan(snapshot,bundle,row['publication_id'],embedder=embedder)
            vectors.validate_plan(plan,snapshot,bundle,row['publication_id'])
            row=_freeze_plan(dsn,claim,plan)
            if row is None: return {'outcome':'lease_lost'}
        else: vectors.validate_plan(plan,snapshot,bundle,row['publication_id'])
        claim['plan_sha256']=registry.digest(plan)
        # Recheck source artifacts after expensive embedding; vector I/O itself
        # remains outside the transaction. The final commit repeats auth/CAS.
        snapshot,bundle=_snapshot_bundle(row,dsn)
        vectors.validate_plan(plan,snapshot,bundle,row['publication_id'])
        if _authorize(dsn,claim,baseline=True) is None: return {'outcome':'lease_lost'}
        vectors.write_and_verify(plan,client=client)
        snapshot,bundle=_snapshot_bundle(row,dsn)
        vectors.validate_plan(plan,snapshot,bundle,row['publication_id'])
        saved=_publish(dsn,claim)
        return {'outcome':'published' if saved else 'lease_lost'}
    except HTTPException as exc:
        code=exc.detail.get('error_code') if isinstance(exc.detail,dict) else None
        if not isinstance(code,str) or not _CODE.fullmatch(code): code='PDF_PUBLICATION_EXECUTION_FAILED'
        try: saved=_fail(dsn,claim,code)
        except (psycopg2.Error,HTTPException): saved=False
        return {'outcome':'failed' if saved else 'lease_lost','error_code':code}
    except Exception:
        try: saved=_fail(dsn,claim,'PDF_PUBLICATION_EXECUTION_FAILED')
        except (psycopg2.Error,HTTPException): saved=False
        return {'outcome':'failed' if saved else 'lease_lost','error_code':'PDF_PUBLICATION_EXECUTION_FAILED'}
    finally: stop.set();heartbeat.join(timeout=1)


def work_once(dsn,publication_id=None,workers=1,*,embedder=None,client=None):
    if type(workers) is not int or not 1<=workers<=GLOBAL_CONCURRENCY: raise ValueError('PDF_PUBLICATION_WORKER_BOUND_INVALID')
    claims=[]
    for _ in range(workers):
        claim=_claim(dsn,publication_id)
        if claim is None: break
        claims.append(claim)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes=list(pool.map(lambda claim:_execute(dsn,claim,embedder=embedder,client=client),claims))
    return {'claimed':len(claims),'outcomes':outcomes}


def drain(dsn,publication_id=None,workers=1,*,embedder=None,client=None):
    waves=0;claimed=0;outcomes=[]
    while True:
        wave=work_once(dsn,publication_id,workers,embedder=embedder,client=client)
        if not wave['claimed']: break
        waves+=1;claimed+=wave['claimed'];outcomes.extend(wave['outcomes'])
    return {'waves':waves,'claimed':claimed,'outcomes':outcomes}
