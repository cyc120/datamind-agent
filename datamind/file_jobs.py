"""Durable bounded registration jobs; each file commits independently.

Workers use the original actor's active session and the existing version/CAS
registries. Stable child request IDs recover uncertain commits. A database lease
fences result writes; a heartbeat keeps long parses from being reclaimed early.
Cancellation is cooperative and never claims to undo a committed registration.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
from typing import Annotated, Literal
import uuid

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from pydantic import Field, model_validator

from ingestion.cad_contract import ContractModel, Sha256
from .auth import lock_identity
from . import cad_store, cad_dimensions, task_budget

RequestId = Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')]
JobId = Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')]
LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
GLOBAL_CONCURRENCY = 4
# Registration targets that can run under the shared process deadline budget.
_BOUNDED_TARGETS = {'drawing': ('datamind.cad_store', 'register'),
                    'measurement': ('datamind.cad_dimensions', 'register_measurement')}
DEADLINE_ERROR_CODE = 'FILE_JOB_DEADLINE_EXCEEDED'
FALLBACK_ERROR_CODE = 'FILE_JOB_EXECUTION_FAILED'


def _safe_code(value, fallback=FALLBACK_ERROR_CODE):
    if isinstance(value, str) and 0 < len(value) <= 96 and value.replace('_', '').isalnum():
        return value
    return fallback


class DrawingRegistration(ContractModel):
    request_id: RequestId
    document_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    source_path: Annotated[str, Field(min_length=1, max_length=500)]
    source_sha256: Sha256
    provenance: Literal['synthetic_demo', 'authorized_external']
    previous_version: Annotated[int, Field(ge=0)]
    previous_source_sha256: Sha256 | None


class FileJobItem(ContractModel):
    item_id: RequestId
    type: Literal['drawing', 'measurement']
    payload: DrawingRegistration | cad_dimensions.MeasurementRegistration

    @model_validator(mode='after')
    def matching_type(self):
        expected = DrawingRegistration if self.type == 'drawing' else cad_dimensions.MeasurementRegistration
        if not isinstance(self.payload, expected):
            raise ValueError('FILE_JOB_ITEM_TYPE_MISMATCH')
        return self


class FileJobRequest(ContractModel):
    request_id: RequestId
    max_concurrency: Annotated[int, Field(ge=1, le=4)] = 2
    items: Annotated[list[FileJobItem], Field(min_length=1, max_length=20)]

    @model_validator(mode='after')
    def unique_items(self):
        if len({x.item_id for x in self.items}) != len(self.items):
            raise ValueError('FILE_JOB_ITEM_ID_REUSED')
        return self


def _validate(payload):
    p = FileJobRequest.model_validate(payload).model_dump()
    for item in p['items']:
        if item['type'] == 'drawing':
            item['payload']['title'] = item['payload']['title'].strip()
            if not item['payload']['title']:
                cad_store.fail('CAD_TITLE_REQUIRED', 422)
    return p


def _child_id(job_id, item_id):
    return 'fj_' + hashlib.sha256((job_id + ':' + item_id).encode()).hexdigest()[:60]


def _checked(job, items):
    """Verify immutable requests/results before displaying or executing them."""
    if not job:
        cad_store.fail('FILE_JOB_NOT_FOUND', 404)
    try:
        request = _validate(job['request_payload'])
        if (cad_store.digest(request) != job['request_sha256']
                or request['request_id'] != job['request_id']
                or request['max_concurrency'] != job['max_concurrency']
                or job['actor']['user_id'] != job['owner_user_id']
                or 'admin' not in job['actor']['roles'] or len(items) != len(request['items'])):
            cad_store.fail('FILE_JOB_EVIDENCE_INVALID')
        for index, item in enumerate(items):
            original = request['items'][index]
            expected = {**original['payload'], 'request_id': _child_id(job['job_id'], original['item_id'])}
            if (item['position'] != index or item['item_id'] != original['item_id']
                    or item['kind'] != original['type'] or item['input_payload'] != expected
                    or item['child_request_id'] != expected['request_id']
                    or cad_store.digest(expected) != item['input_sha256']
                    or (item['result'] is not None and cad_store.digest(item['result']) != item['result_sha256'])):
                cad_store.fail('FILE_JOB_EVIDENCE_INVALID')
    except (KeyError, ValueError, TypeError):
        cad_store.fail('FILE_JOB_EVIDENCE_INVALID')
    return job, items


def _load(cur, job_id, locked=False):
    cur.execute('SELECT * FROM file_jobs WHERE job_id=%s' + (' FOR UPDATE' if locked else ''), (job_id,))
    job = cur.fetchone()
    if not job:
        cad_store.fail('FILE_JOB_NOT_FOUND', 404)
    cur.execute('SELECT * FROM file_job_items WHERE job_id=%s ORDER BY position', (job_id,))
    return _checked(job, cur.fetchall())


def _public(job, items):
    counts = {s: sum(x['status'] == s for x in items) for s in ('pending', 'running', 'succeeded', 'failed', 'cancelled')}
    if counts['running']:
        status = 'running'
    elif counts['pending']:
        status = 'queued' if counts['pending'] == len(items) else 'running'
    elif counts['failed']:
        status = 'partial_failure' if counts['succeeded'] else 'failed'
    elif counts['cancelled']:
        status = 'cancelled'
    else:
        status = 'succeeded'
    return {'job_id': job['job_id'], 'request_id': job['request_id'], 'status': status,
            'max_concurrency': job['max_concurrency'], 'cancel_requested': job['cancel_requested'],
            'actor': job['actor'], 'created_at': job['created_at'], 'updated_at': job['updated_at'], 'counts': counts,
            'items': [{'item_id': x['item_id'], 'type': x['kind'], 'status': x['status'],
                       'attempts': x['total_attempts'], 'result': x['result'], 'error_code': x['error_code'],
                       'source_sha256': x['input_payload']['source_sha256'],
                       'target_id': x['input_payload'].get('document_id', x['input_payload'].get('measurement_id')),
                       'started_at': x['started_at'], 'finished_at': x['finished_at']} for x in items]}


def _authorized(cur, identity, job, mutation=False, owner_only=False):
    actor = lock_identity(cur, identity, 'admin' if mutation else 'viewer')
    # Non-viewer roles may read jobs they own; API may choose an admin-only policy.
    if (owner_only and actor['user_id'] != job['owner_user_id']) or (
            actor['user_id'] != job['owner_user_id'] and 'admin' not in actor['roles']):
        cad_store.fail('PERMISSION_DENIED', 403)
    return actor


def submit(payload, dsn, identity):
    p = _validate(payload)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor = lock_identity(cur, identity, 'admin')
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('file-job-request:' + p['request_id'],))
        cur.execute('SELECT * FROM file_jobs WHERE request_id=%s', (p['request_id'],))
        old = cur.fetchone()
        if old:
            if old['owner_user_id'] != actor['user_id'] or old['request_payload'] != p:
                cad_store.fail('FILE_JOB_REQUEST_ID_REUSED')
            return _public(*_load(cur, old['job_id']))
        job_id = 'file-job-' + uuid.uuid4().hex
        cur.execute('''INSERT INTO file_jobs(job_id,request_id,owner_user_id,actor,session_hash,
            request_payload,request_sha256,max_concurrency) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (job_id, p['request_id'], actor['user_id'], Json(actor), identity['_session_hash'],
                     Json(p), cad_store.digest(p), p['max_concurrency']))
        for index, item in enumerate(p['items']):
            child = _child_id(job_id, item['item_id']); data = {**item['payload'], 'request_id': child}
            cur.execute('''INSERT INTO file_job_items(job_id,item_id,position,kind,input_payload,input_sha256,child_request_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                        (job_id, item['item_id'], index, item['type'], Json(data), cad_store.digest(data), child))
        return _public(*_load(cur, job_id))


def poll(job_id, dsn, identity):
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        conn.set_session(isolation_level='REPEATABLE READ')
        job, items = _load(cur, job_id)
        _authorized(cur, identity, job)
        return _public(job, items)


def listing(dsn, identity):
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        conn.set_session(isolation_level='REPEATABLE READ')
        actor = lock_identity(cur, identity, 'admin')
        cur.execute('SELECT job_id FROM file_jobs ORDER BY created_at DESC LIMIT 100')
        ids = [r['job_id'] for r in cur.fetchall()]
        return [_public(*_load(cur, job_id)) for job_id in ids]


def cancel(job_id, dsn, identity):
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Same auth-before-job lock order as submit and retry.
        lock_identity(cur, identity, 'admin')
        job, items = _load(cur, job_id, locked=True)
        _authorized(cur, identity, job, mutation=True)
        cur.execute('UPDATE file_jobs SET cancel_requested=TRUE,updated_at=clock_timestamp() WHERE job_id=%s', (job_id,))
        cur.execute('''UPDATE file_job_items SET status='cancelled',finished_at=clock_timestamp(),error_code=NULL
            WHERE job_id=%s AND status IN ('pending','failed')''', (job_id,))
        return _public(*_load(cur, job_id))


def retry(job_id, dsn, identity):
    """Retry failed items only, retaining every succeeded item and immutable input."""
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        lock_identity(cur, identity, 'admin')
        job, items = _load(cur, job_id, locked=True)
        _authorized(cur, identity, job, mutation=True, owner_only=True)
        if job['cancel_requested']:
            cad_store.fail('FILE_JOB_CANCELLED')
        if not any(x['status'] == 'failed' for x in items):
            cad_store.fail('FILE_JOB_NO_FAILED_ITEMS')
        cur.execute('''UPDATE file_jobs SET session_hash=%s,updated_at=clock_timestamp() WHERE job_id=%s''',
                    (identity['_session_hash'], job_id))
        cur.execute('''UPDATE file_job_items SET status='pending',attempts=0,error_code=NULL,finished_at=NULL,
            result=NULL,result_sha256=NULL WHERE job_id=%s AND status='failed' ''', (job_id,))
        return _public(*_load(cur, job_id))


def _registered(cur, item, owner_user_id):
    """Recover a visible committed registry result without repeating a mutation."""
    if item['kind'] == 'drawing':
        cur.execute('SELECT * FROM cad_versions WHERE request_id=%s', (item['child_request_id'],))
        row = cur.fetchone()
        if not row:
            return None
        if row['request_payload'] != item['input_payload'] or row['registrar_user_id'] != owner_user_id:
            cad_store.fail('FILE_JOB_COMMITTED_INPUT_MISMATCH')
        return jsonable_encoder(cad_store.public_version(cad_store.checked(row)))
    cur.execute('SELECT * FROM dimension_measurement_versions WHERE request_id=%s', (item['child_request_id'],))
    row = cur.fetchone()
    if not row:
        return None
    record = cad_dimensions.measured(row)
    if record['request'] != item['input_payload'] or record['actor']['user_id'] != owner_user_id:
        cad_store.fail('FILE_JOB_COMMITTED_INPUT_MISMATCH')
    return jsonable_encoder(cad_dimensions.public(record))


def _claim(dsn, job_id=None):
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Serialize only short claim/reclaim transactions across all worker processes.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('file-job-claim-cap',0))")
        cur.execute('''SELECT j.job_id,j.cancel_requested FROM file_jobs j WHERE EXISTS
            (SELECT 1 FROM file_job_items i WHERE i.job_id=j.job_id AND i.status='running'
             AND i.lease_expires_at<=clock_timestamp()) ORDER BY j.job_id FOR UPDATE OF j SKIP LOCKED''')
        expired = cur.fetchall()
        for job in expired:
            evidence, items = _load(cur, job['job_id'])
            cur.execute('''SELECT item_id FROM file_job_items WHERE job_id=%s AND status='running'
                AND lease_expires_at<=clock_timestamp() FOR UPDATE''', (job['job_id'],))
            expired_ids = {x['item_id'] for x in cur.fetchall()}
            for item in items:
                if item['item_id'] not in expired_ids:
                    continue
                result = _registered(cur, item, evidence['owner_user_id'])
                if result is not None:
                    cur.execute('''UPDATE file_job_items SET status='succeeded',result=%s,result_sha256=%s,
                        error_code=NULL,lease_token=NULL,lease_expires_at=NULL,finished_at=clock_timestamp()
                        WHERE job_id=%s AND item_id=%s''',
                                (Json(result),cad_store.digest(result),job['job_id'],item['item_id']))
            cur.execute('''UPDATE file_job_items SET status=CASE WHEN %s THEN 'failed'
                WHEN attempts>=%s THEN 'failed' ELSE 'pending' END,
                lease_token=NULL,lease_expires_at=NULL,
                finished_at=CASE WHEN %s OR attempts>=%s THEN clock_timestamp() ELSE NULL END,
                error_code=CASE WHEN %s THEN 'FILE_JOB_CANCELLED_OUTCOME_UNCERTAIN'
                  WHEN attempts>=%s THEN 'FILE_JOB_LEASE_RETRIES_EXHAUSTED' ELSE NULL END
                WHERE job_id=%s AND status='running' AND lease_expires_at<=clock_timestamp()''',
                        (job['cancel_requested'],MAX_ATTEMPTS,job['cancel_requested'],MAX_ATTEMPTS,
                         job['cancel_requested'],MAX_ATTEMPTS,job['job_id']))
            cur.execute('UPDATE file_jobs SET updated_at=clock_timestamp() WHERE job_id=%s', (job['job_id'],))
        cur.execute("SELECT count(*) AS n FROM file_job_items WHERE status='running'")
        if cur.fetchone()['n'] >= GLOBAL_CONCURRENCY:
            return None
        cur.execute('''SELECT j.job_id FROM file_jobs j
            WHERE NOT j.cancel_requested AND (%s IS NULL OR j.job_id=%s)
            AND EXISTS(SELECT 1 FROM file_job_items i WHERE i.job_id=j.job_id AND i.status='pending')
            AND (SELECT count(*) FROM file_job_items running WHERE running.job_id=j.job_id
                 AND running.status='running')<j.max_concurrency
            ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1''', (job_id, job_id))
        candidate = cur.fetchone()
        if not candidate:
            return None
        job, items = _load(cur, candidate['job_id'])
        cur.execute('''SELECT item_id FROM file_job_items WHERE job_id=%s AND status='pending'
            ORDER BY position FOR UPDATE SKIP LOCKED LIMIT 1''', (job['job_id'],))
        selected = cur.fetchone()
        if not selected:
            return None
        item = next(x for x in items if x['item_id'] == selected['item_id'])
        token = uuid.uuid4().hex
        cur.execute('''UPDATE file_job_items SET status='running',attempts=attempts+1,total_attempts=total_attempts+1,
            lease_token=%s,lease_expires_at=clock_timestamp()+%s*interval '1 second',
            started_at=clock_timestamp(),finished_at=NULL,error_code=NULL WHERE job_id=%s AND item_id=%s''',
                    (token, LEASE_SECONDS, job['job_id'], item['item_id']))
        cur.execute('UPDATE file_jobs SET updated_at=clock_timestamp() WHERE job_id=%s', (job['job_id'],))
        return {'job_id': job['job_id'], 'item_id': item['item_id'], 'lease_token': token,
                'type': item['kind'], 'payload': item['input_payload'],
                'identity': {'user_id': job['owner_user_id'], '_session_hash': job['session_hash']}}


def _finish(dsn, claim, status, result=None, error_code=None):
    if result is not None:
        result = jsonable_encoder(result)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('SELECT job_id FROM file_jobs WHERE job_id=%s FOR UPDATE', (claim['job_id'],))
        cur.execute('''UPDATE file_job_items SET status=%s,result=%s,result_sha256=%s,error_code=%s,
            lease_token=NULL,lease_expires_at=NULL,finished_at=clock_timestamp()
            WHERE job_id=%s AND item_id=%s AND status='running' AND lease_token=%s
            AND lease_expires_at>clock_timestamp() RETURNING job_id''',
                    (status, Json(result) if result is not None else None,
                     cad_store.digest(result) if result is not None else None, error_code,
                     claim['job_id'], claim['item_id'], claim['lease_token']))
        if not cur.fetchone():
            return False
        cur.execute('UPDATE file_jobs SET updated_at=clock_timestamp() WHERE job_id=%s', (claim['job_id'],))
        return True


def _heartbeat(dsn, claim, stop):
    while not stop.wait(max(1, LEASE_SECONDS / 3)):
        try:
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute('''UPDATE file_job_items SET lease_expires_at=clock_timestamp()+%s*interval '1 second'
                    WHERE job_id=%s AND item_id=%s AND status='running' AND lease_token=%s
                    AND lease_expires_at>clock_timestamp()''',
                            (LEASE_SECONDS, claim['job_id'], claim['item_id'], claim['lease_token']))
                if not cur.rowcount:
                    return
        except psycopg2.Error:
            # Lease recovery and stable registry request IDs cover loss of connectivity.
            return


def _execute_bounded(claim, dsn, deadline_s):
    """Run one registration in its own process group under the shared deadline budget.

    A cooperative cancel request in the database terminates the running group; the
    outcome is reported as `cancelled` and never claims to undo a committed write.
    """
    module, function = _BOUNDED_TARGETS[claim['type']]
    cancel, stop = threading.Event(), threading.Event()

    def watch():
        while not stop.wait(1.0):
            try:
                with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                    cur.execute('SELECT cancel_requested FROM file_jobs WHERE job_id=%s', (claim['job_id'],))
                    row = cur.fetchone()
            except psycopg2.Error:
                return
            if row is None or row[0]:
                cancel.set()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        return task_budget.run_bounded(module, function,
                                       [claim['payload'], dsn, claim['identity']],
                                       deadline_s=deadline_s, fallback_error_code=FALLBACK_ERROR_CODE,
                                       cancel_event=cancel)
    finally:
        stop.set(); watcher.join(timeout=1)


def _execute(dsn, claim, *, deadline_s=None, child_dsn=None):
    stop = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(dsn, claim, stop), daemon=True)
    heartbeat.start()
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''SELECT j.cancel_requested,i.status,i.lease_token,
                i.lease_expires_at>clock_timestamp() AS lease_active FROM file_job_items i
                JOIN file_jobs j ON j.job_id=i.job_id WHERE i.job_id=%s AND i.item_id=%s''',
                        (claim['job_id'], claim['item_id']))
            current = cur.fetchone()
        if (not current or current['status'] != 'running' or current['lease_token'] != claim['lease_token']
                or not current['lease_active']):
            return {'outcome': 'lease_lost'}
        with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            result = _registered(cur, {'kind':claim['type'],'child_request_id':claim['payload']['request_id'],
                                      'input_payload':claim['payload']}, claim['identity']['user_id'])
        if result is not None:
            saved = _finish(dsn,claim,'succeeded',result=result)
            return {'outcome':'succeeded' if saved else 'lease_lost'}
        if current['cancel_requested']:
            saved = _finish(dsn, claim, 'cancelled')
            return {'outcome': 'cancelled' if saved else 'lease_lost'}
        if deadline_s is None:
            register = cad_store.register if claim['type'] == 'drawing' else cad_dimensions.register_measurement
            result = jsonable_encoder(register(claim['payload'], dsn, claim['identity']))
            saved = _finish(dsn, claim, 'succeeded', result=result)
            return {'outcome': 'succeeded' if saved else 'lease_lost'}
        outcome = _execute_bounded(claim, child_dsn or dsn, deadline_s)
        if outcome['status'] == 'ok':
            saved = _finish(dsn, claim, 'succeeded', result=outcome['value'])
            return {'outcome': 'succeeded' if saved else 'lease_lost',
                    'elapsed_ms': outcome['elapsed_ms']}
        if outcome['status'] == 'cancelled':
            saved = _finish(dsn, claim, 'cancelled')
            return {'outcome': 'cancelled' if saved else 'lease_lost',
                    'error_code': outcome['error_code'], 'elapsed_ms': outcome['elapsed_ms']}
        code = DEADLINE_ERROR_CODE if outcome['status'] == 'timeout' else _safe_code(outcome.get('error_code'))
        saved = _finish(dsn, claim, 'failed', error_code=code)
        return {'outcome': 'failed' if saved else 'lease_lost', 'error_code': code,
                'elapsed_ms': outcome['elapsed_ms'], 'deadline_seconds': outcome['deadline_seconds']}
    except HTTPException as error:
        code = error.detail.get('error_code') if isinstance(error.detail, dict) else None
        code = _safe_code(code, 'FILE_JOB_REGISTRATION_FAILED')
        saved = _finish(dsn, claim, 'failed', error_code=code)
        return {'outcome': 'failed' if saved else 'lease_lost', 'error_code': code}
    except Exception:
        # Never return exception text, stack traces, paths or connection secrets.
        saved = _finish(dsn, claim, 'failed', error_code=FALLBACK_ERROR_CODE)
        return {'outcome': 'failed' if saved else 'lease_lost', 'error_code': FALLBACK_ERROR_CODE}
    finally:
        stop.set(); heartbeat.join(timeout=1)


def work_once(dsn, job_id=None, workers=2, deadline_s=None):
    """Claim and finish at most one wave; this never waits for unrelated workers.

    Passing `deadline_s` runs each registration in its own process group under the
    shared task budget; the default keeps the established in-process path.
    """
    if type(workers) is not int or not 1 <= workers <= GLOBAL_CONCURRENCY:
        raise ValueError('FILE_JOB_WORKER_BOUND_INVALID')
    child_dsn = None
    if deadline_s is not None:
        deadline_s = task_budget.validate_deadline(deadline_s)
        # Isolated runs redirect metadata connections in-process; the child must be
        # handed a DSN that already names the same database.
        child_dsn = task_budget.effective_dsn(dsn)
    claims = []
    for _ in range(workers):
        claim = _claim(dsn, job_id)
        if not claim:
            break
        claims.append(claim)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda x: _execute(dsn, x, deadline_s=deadline_s, child_dsn=child_dsn), claims))
    return {'claimed': len(claims), 'outcomes': results}


def drain(dsn, job_id=None, workers=2, deadline_s=None):
    """Drain currently claimable work; a later invocation recovers expired leases."""
    waves = 0; claimed = 0; outcomes = []
    while True:
        wave = work_once(dsn, job_id, workers, deadline_s)
        if not wave['claimed']:
            break
        waves += 1; claimed += wave['claimed']; outcomes.extend(wave['outcomes'])
    return {'waves': waves, 'claimed': claimed, 'outcomes': outcomes}
