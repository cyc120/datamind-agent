"""Durable staged-file jobs exercised against the disposable metadata clone."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import secrets
import threading
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import Json
from pydantic import ValidationError

from datamind import file_jobs as jobs, cad_store, cad_dimensions
from datamind.agent import _write_dsn
from datamind.api import app
import test_cad_assets as assets
import test_cad_dimensions as dimensions


class FileJobTests(unittest.TestCase):
    payload = assets.CadAssetTests.payload
    mp = dimensions.CadDimensionServiceTests.mp
    write_measurement = dimensions.CadDimensionServiceTests.write_measurement

    def setUp(self):
        dimensions.CadDimensionServiceTests.setUp(self)
        self.job_ids = []; self.extra_mids = []
        self.identity = self.actor(self.admin)
        self.dsn = _write_dsn()

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM file_job_items WHERE job_id=ANY(%s)', (self.job_ids,))
            cur.execute('DELETE FROM file_jobs WHERE job_id=ANY(%s)', (self.job_ids,))
            cur.execute('DELETE FROM dimension_measurement_versions WHERE measurement_id=ANY(%s)', (self.extra_mids,))
            cur.execute('DELETE FROM dimension_measurements WHERE measurement_id=ANY(%s)', (self.extra_mids,))
        dimensions.CadDimensionServiceTests.tearDown(self)

    def actor(self, client):
        return {'user_id': client._fixture_users[0], '_session_hash': hashlib.sha256(client.cookies.get('datamind_session').encode()).hexdigest()}

    def batch(self, items=None, **changes):
        result = {'request_id': uuid.uuid4().hex, 'max_concurrency': 2,
                  'items': items if items is not None else [{'item_id': 'drawing', 'type': 'drawing', 'payload': self.payload()},
                                    {'item_id': 'measurement', 'type': 'measurement', 'payload': self.mp()}]}
        result.update(changes); return result

    def submit(self, p=None):
        result = jobs.submit(p or self.batch(), self.dsn, self.identity)
        if result['job_id'] not in self.job_ids: self.job_ids.append(result['job_id'])
        return result

    def poll(self, job): return jobs.poll(job['job_id'], self.dsn, self.identity)

    def drawing_items(self, count):
        result = []
        for index in range(count):
            did = 'cad-job-test-' + uuid.uuid4().hex[:20]; self.ids.append(did)
            result.append({'item_id': 'file-' + str(index), 'type': 'drawing', 'payload': self.payload(document_id=did)})
        return result

    def error(self, code, action):
        with self.assertRaises(HTTPException) as e: action()
        self.assertEqual(e.exception.detail['error_code'], code)

    def expire(self, claim, attempts=None):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("UPDATE file_job_items SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s AND item_id=%s",
                        (claim['job_id'], claim['item_id']))
            if attempts is not None:
                cur.execute('UPDATE file_job_items SET attempts=%s,total_attempts=%s WHERE job_id=%s AND item_id=%s',
                            (attempts, attempts, claim['job_id'], claim['item_id']))

    def test_strict_bounds_types_and_duplicate_item_ids(self):
        for p in [self.batch(items=[]), self.batch(max_concurrency=5), self.batch(max_concurrency=True),
                  self.batch(items=self.drawing_items(20)+self.drawing_items(1)),
                  self.batch(items=[{'item_id':'wrong','type':'measurement','payload':self.payload()}]),
                  self.batch(items=[{'item_id':'same','type':'drawing','payload':self.payload()}]*2),
                  self.batch(actor={'user_id':'spoof'})]:
            with self.assertRaises(ValidationError): jobs.FileJobRequest.model_validate(p)
        self.assertEqual(jobs.FileJobRequest.model_validate(self.batch()).max_concurrency,2)
        with self.assertRaises(ValueError): jobs.work_once(self.dsn, workers=5)

    def test_idempotent_submission_and_private_session_material(self):
        p = self.batch(); first = self.submit(p); second = self.submit(copy.deepcopy(p))
        self.assertEqual(first, second)
        self.assertNotIn('session', json.dumps(first, default=str))
        self.assertNotIn(self.identity['_session_hash'], json.dumps(first, default=str))
        changed = copy.deepcopy(p); changed['items'][0]['payload']['title'] = 'changed'
        self.error('FILE_JOB_REQUEST_ID_REUSED', lambda: self.submit(changed))
        other = self.actor(self.reviewer)
        self.error('PERMISSION_DENIED', lambda: jobs.submit(p, self.dsn, other))
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT child_request_id,input_payload FROM file_job_items WHERE job_id=%s ORDER BY position',(first['job_id'],))
            records=cur.fetchall()
        self.assertTrue(all(x[0]==x[1]['request_id'] and x[0].startswith('fj_') for x in records))
        self.assertEqual(len({x[0] for x in records}),2)

    def test_api_requires_admin_csrf_origin_and_hides_execution_session(self):
        p=self.batch()
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get('/v1/files/jobs').status_code,401)
            self.assertEqual(anonymous.post('/v1/files/jobs',json=p).status_code,401)
        for client in (self.viewer,self.reviewer,self.approver):
            self.assertEqual(client.get('/v1/files/jobs').status_code,403)
            self.assertEqual(client.post('/v1/files/jobs',json=p).status_code,403)
        csrf=self.admin.headers.pop('X-CSRF-Token')
        self.assertEqual(self.admin.post('/v1/files/jobs',json=p).status_code,403)
        self.admin.headers['X-CSRF-Token']=csrf
        self.assertEqual(self.admin.post('/v1/files/jobs',json=p,headers={'Origin':'https://untrusted.invalid'}).status_code,403)
        created=self.admin.post('/v1/files/jobs',json=p);self.assertEqual(created.status_code,200,created.text)
        job=created.json();self.job_ids.append(job['job_id'])
        self.assertEqual(self.admin.post('/v1/files/jobs',json=p).json(),job)
        jobs.drain(self.dsn,job['job_id'])
        response=self.admin.get('/v1/files/jobs/'+job['job_id']);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.json()['status'],'succeeded')
        self.assertNotIn(self.identity['_session_hash'],response.text)

    def test_mixed_batch_commits_each_valid_file_and_isolates_failure(self):
        bad = 'measurement-job-' + uuid.uuid4().hex[:12]; self.extra_mids.append(bad)
        items = self.batch()['items'] + [{'item_id':'invalid','type':'measurement','payload':self.mp(measurement_id=bad,source_path='../outside.json')}]
        job = self.submit(self.batch(items=items)); result = jobs.drain(self.dsn,job['job_id'],workers=4)
        self.assertEqual(result['claimed'],3); final=self.poll(job)
        self.assertEqual(final['status'],'partial_failure'); self.assertEqual(final['counts']['succeeded'],2)
        self.assertEqual(final['items'][2]['error_code'],'DIMENSION_STAGED_SOURCE_INVALID')
        self.assertEqual(final['items'][0]['result']['document_version'],1)
        self.assertEqual(final['items'][1]['result']['payload']['source']['record_version'],1)
        self.assertEqual(jobs.drain(self.dsn,job['job_id'])['claimed'],0)

    def test_changed_staged_source_fails_instead_of_mutating_submitted_sha(self):
        job=self.submit(self.batch(items=[{'item_id':'measurement','type':'measurement','payload':self.mp()}]))
        self.mpath.write_text('{}')
        jobs.drain(self.dsn,job['job_id']); final=self.poll(job)
        self.assertEqual(final['items'][0]['error_code'],'DIMENSION_SOURCE_CHANGED')
        self.assertEqual(final['items'][0]['source_sha256'],job['items'][0]['source_sha256'])
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM dimension_measurements WHERE measurement_id=%s',(self.mid,)); self.assertEqual(cur.fetchone()[0],0)

    def test_global_and_per_job_caps_across_independent_claimers(self):
        first=self.submit(self.batch(items=self.drawing_items(5),max_concurrency=2))
        second=self.submit(self.batch(items=self.drawing_items(5),max_concurrency=4))
        with ThreadPoolExecutor(max_workers=8) as pool: claims=list(pool.map(lambda _: jobs._claim(self.dsn),range(8)))
        claims=[x for x in claims if x]
        self.assertEqual(len(claims),4); self.assertLessEqual(sum(x['job_id']==first['job_id'] for x in claims),2)
        self.assertEqual(self.poll(first)['counts']['running']+self.poll(second)['counts']['running'],4)
        for claim in claims: self.assertTrue(jobs._finish(self.dsn,claim,'cancelled'))

    def test_multiple_worker_process_waves_have_no_duplicate_versions(self):
        job=self.submit(self.batch(items=self.drawing_items(6),max_concurrency=2))
        lock=threading.Lock(); active=0; high=0; entered=threading.Event(); release=threading.Event()
        original=cad_store.register
        def controlled(*args):
            nonlocal active,high
            with lock: active+=1; high=max(high,active); entered.set()
            try:
                self.assertTrue(release.wait(10)); return original(*args)
            finally:
                with lock: active-=1
        with patch.object(cad_store,'register',side_effect=controlled),ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(jobs.work_once,self.dsn,job['job_id'],4) for _ in range(3)]
            self.assertTrue(entered.wait(10)); self.assertEqual(self.poll(job)['counts']['running'],2); release.set()
            for f in futures: f.result(20)
        self.assertLessEqual(high,2); jobs.drain(self.dsn,job['job_id'],workers=4)
        final=self.poll(job);self.assertEqual(final['counts']['succeeded'],6)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM cad_versions WHERE document_id=ANY(%s)',([x['target_id'] for x in final['items']],))
            self.assertEqual(cur.fetchone()[0],6)

    def test_same_asset_concurrent_inputs_keep_existing_version_cas(self):
        job=self.submit(self.batch(items=[{'item_id':'a','type':'drawing','payload':self.payload()},
                                          {'item_id':'b','type':'drawing','payload':self.payload()}]))
        jobs.drain(self.dsn,job['job_id']); final=self.poll(job)
        self.assertEqual(final['counts']['succeeded'],1);self.assertEqual(final['counts']['failed'],1)
        self.assertEqual(next(x for x in final['items'] if x['status']=='failed')['error_code'],'CAD_VERSION_CHANGED')
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM cad_versions WHERE document_id=%s',(self.id,));self.assertEqual(cur.fetchone()[0],1)

    def test_expired_lease_recovers_uncertain_commit_with_stable_child_request(self):
        job=self.submit(self.batch(items=[{'item_id':'cad','type':'drawing','payload':self.payload()}]))
        stale=jobs._claim(self.dsn,job['job_id'])
        registered=cad_store.register(stale['payload'],self.dsn,stale['identity'])
        self.path.write_bytes(b'changed after commit');self.expire(stale)
        result=jobs.work_once(self.dsn,job['job_id']);self.assertEqual(result['claimed'],0)
        self.assertFalse(jobs._finish(self.dsn,stale,'failed',error_code='STALE_WORKER'))
        final=self.poll(job);self.assertEqual(final['items'][0]['attempts'],1)
        self.assertEqual(final['items'][0]['result']['source_sha256'],registered['source_sha256'])
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM cad_versions WHERE document_id=%s',(self.id,));self.assertEqual(cur.fetchone()[0],1)

    def test_expired_lease_attempt_limit_requires_explicit_retry(self):
        job=self.submit();stale=jobs._claim(self.dsn,job['job_id']);self.expire(stale,attempts=3)
        jobs.drain(self.dsn,job['job_id']);final=self.poll(job)
        self.assertEqual(final['items'][0]['error_code'],'FILE_JOB_LEASE_RETRIES_EXHAUSTED')
        self.assertEqual(final['items'][1]['status'],'succeeded')
        jobs.retry(job['job_id'],self.dsn,self.identity);jobs.drain(self.dsn,job['job_id'])
        final=self.poll(job);self.assertEqual(final['status'],'succeeded');self.assertEqual(final['items'][0]['attempts'],4)

    def test_queued_actor_permission_is_rechecked_inside_registry_transaction(self):
        job=self.submit()
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles=ARRAY['viewer'] WHERE user_id=%s",(self.identity['user_id'],))
        jobs.drain(self.dsn,job['job_id']);final=self.poll(job)
        self.assertTrue(all(x['error_code']=='PERMISSION_DENIED' for x in final['items']))
        self.error('PERMISSION_DENIED',lambda:jobs.retry(job['job_id'],self.dsn,self.identity))

    def test_expired_session_fails_and_same_actor_can_refresh_only_failed_items(self):
        job=self.submit();jobs.work_once(self.dsn,job['job_id'],workers=1)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE token_hash=%s",(self.identity['_session_hash'],))
        jobs.drain(self.dsn,job['job_id']);self.error('SESSION_INVALID',lambda:self.poll(job))
        new_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('INSERT INTO auth_sessions(token_hash,user_id,csrf_token,expires_at) VALUES(%s,%s,%s,%s)',
                        (new_hash,self.identity['user_id'],secrets.token_urlsafe(32),datetime.now(timezone.utc)+timedelta(hours=1)))
        self.identity={**self.identity,'_session_hash':new_hash};final=self.poll(job)
        self.assertEqual(final['items'][1]['error_code'],'SESSION_INVALID')
        first_result=copy.deepcopy(final['items'][0]['result'])
        jobs.retry(job['job_id'],self.dsn,self.identity);jobs.drain(self.dsn,job['job_id'])
        final=self.poll(job);self.assertEqual(final['status'],'succeeded');self.assertEqual(final['items'][0]['result'],first_result)
        self.assertEqual(final['items'][0]['attempts'],1);self.assertEqual(final['items'][1]['attempts'],2)

    def test_retry_preserves_request_sha_and_does_not_reset_successes(self):
        job=self.submit(self.batch(items=[{'item_id':'a','type':'drawing','payload':self.payload()},
                                          {'item_id':'b','type':'drawing','payload':self.payload()}]))
        jobs.drain(self.dsn,job['job_id']);before=self.poll(job)
        succeeded=next(x for x in before['items'] if x['status']=='succeeded')
        jobs.retry(job['job_id'],self.dsn,self.identity)
        self.error('FILE_JOB_NO_FAILED_ITEMS',lambda:jobs.retry(job['job_id'],self.dsn,self.identity))
        jobs.drain(self.dsn,job['job_id']);after=self.poll(job)
        self.assertEqual(next(x for x in after['items'] if x['status']=='succeeded'),succeeded)
        self.assertEqual(after['status'],'partial_failure')

    def test_cancel_pending_never_claims_work_or_undoes_completed_items(self):
        job=self.submit();jobs.work_once(self.dsn,job['job_id'],workers=1)
        before=self.poll(job)['items'][0]
        jobs.cancel(job['job_id'],self.dsn,self.identity);jobs.cancel(job['job_id'],self.dsn,self.identity)
        self.assertEqual(jobs.drain(self.dsn,job['job_id'])['claimed'],0);final=self.poll(job)
        self.assertEqual(final['status'],'cancelled');self.assertEqual(final['items'][0],before)
        self.assertEqual(final['items'][1]['status'],'cancelled')
        self.error('FILE_JOB_CANCELLED',lambda:jobs.retry(job['job_id'],self.dsn,self.identity))

    def test_cancel_running_before_mutation_is_cooperative(self):
        job=self.submit(self.batch(items=[{'item_id':'cad','type':'drawing','payload':self.payload()}]))
        claim=jobs._claim(self.dsn,job['job_id']);jobs.cancel(job['job_id'],self.dsn,self.identity)
        self.assertEqual(jobs._execute(self.dsn,claim)['outcome'],'cancelled')
        self.assertEqual(self.poll(job)['counts']['cancelled'],1)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM cad_assets WHERE document_id=%s',(self.id,));self.assertEqual(cur.fetchone()[0],0)

    def test_cancel_after_registry_commit_records_truthful_success(self):
        job=self.submit(self.batch(items=[{'item_id':'cad','type':'drawing','payload':self.payload()}]))
        committed=threading.Event();release=threading.Event();original=cad_store.register
        def controlled(*args):
            result=original(*args);committed.set();self.assertTrue(release.wait(10));return result
        with patch.object(cad_store,'register',side_effect=controlled),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(jobs.work_once,self.dsn,job['job_id'])
            self.assertTrue(committed.wait(10));jobs.cancel(job['job_id'],self.dsn,self.identity);release.set();future.result(20)
        final=self.poll(job);self.assertTrue(final['cancel_requested']);self.assertEqual(final['status'],'succeeded')
        self.assertEqual(final['items'][0]['result']['document_version'],1)

    def test_cancel_crashed_worker_recovers_visible_commit_or_marks_uncertainty(self):
        committed=self.submit(self.batch(items=[{'item_id':'cad','type':'drawing','payload':self.payload()}]))
        claim=jobs._claim(self.dsn,committed['job_id'])
        cad_store.register(claim['payload'],self.dsn,claim['identity'])
        jobs.cancel(committed['job_id'],self.dsn,self.identity);self.expire(claim)
        jobs.work_once(self.dsn,committed['job_id']);final=self.poll(committed)
        self.assertEqual(final['status'],'succeeded');self.assertTrue(final['cancel_requested'])
        uncertain=self.submit(self.batch(items=self.drawing_items(1)))
        second=jobs._claim(self.dsn,uncertain['job_id'])
        jobs.cancel(uncertain['job_id'],self.dsn,self.identity);self.expire(second)
        jobs.work_once(self.dsn,uncertain['job_id']);final=self.poll(uncertain)
        self.assertEqual(final['status'],'failed')
        self.assertEqual(final['items'][0]['error_code'],'FILE_JOB_CANCELLED_OUTCOME_UNCERTAIN')
        self.assertEqual(jobs._execute(self.dsn,second)['outcome'],'lease_lost')

    def test_expired_token_cannot_execute_renew_or_finish_before_reclaim(self):
        job=self.submit(self.batch(items=self.drawing_items(1)))
        claim=jobs._claim(self.dsn,job['job_id']);self.expire(claim)
        self.assertFalse(jobs._finish(self.dsn,claim,'cancelled'))
        self.assertEqual(jobs._execute(self.dsn,claim)['outcome'],'lease_lost')
        jobs.work_once(self.dsn,job['job_id']);self.assertEqual(self.poll(job)['status'],'succeeded')

    def test_evidence_and_result_tampering_is_refused(self):
        job=self.submit();jobs.drain(self.dsn,job['job_id'])
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE file_job_items SET result_sha256=%s WHERE job_id=%s AND position=0',('0'*64,job['job_id']))
        self.error('FILE_JOB_EVIDENCE_INVALID',lambda:self.poll(job))
        queued=self.submit(self.batch(items=self.drawing_items(1)))
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE file_job_items SET input_sha256=%s WHERE job_id=%s',('0'*64,queued['job_id']))
        self.error('FILE_JOB_EVIDENCE_INVALID',lambda:jobs.work_once(self.dsn,queued['job_id']))

    def test_unexpected_worker_error_exposes_no_exception_detail(self):
        job=self.submit(self.batch(items=[{'item_id':'cad','type':'drawing','payload':self.payload()}]))
        with patch.object(cad_store,'register',side_effect=RuntimeError('private-dsn-password-secret')):
            result=jobs.work_once(self.dsn,job['job_id'])
        self.assertNotIn('password',json.dumps(result));final=self.poll(job)
        self.assertEqual(final['items'][0]['error_code'],'FILE_JOB_EXECUTION_FAILED')
        self.assertNotIn('private',json.dumps(final,default=str))

    def test_bounded_deadline_routes_the_registration_to_a_child_process(self):
        # The runner's own hard-deadline behaviour is covered in test_task_budget with
        # real processes. Here we verify the wiring: the claim, deadline and an explicit
        # child DSN are handed off instead of using the parent connection.
        job=self.submit(self.batch(items=[{'item_id':'drawing','type':'drawing','payload':self.payload()}]))
        captured={}
        def fake(claim,dsn,deadline_s):
            captured.update(claim=claim,dsn=dsn,deadline_s=deadline_s)
            return {'status':'ok','value':{'document_id':claim['payload']['document_id'],'synthetic':True},
                    'elapsed_ms':1.0,'deadline_seconds':deadline_s}
        with patch.object(jobs,'_execute_bounded',side_effect=fake):
            wave=jobs.work_once(self.dsn,deadline_s=60,workers=1)
        self.assertEqual(wave['outcomes'][0]['outcome'],'succeeded',wave['outcomes'][0])
        self.assertEqual(captured['deadline_s'],60.0)
        self.assertEqual(captured['claim']['type'],'drawing')
        self.assertEqual(captured['claim']['payload']['request_id'],
                         jobs._child_id(job['job_id'],'drawing'))
        with psycopg2.connect(captured['dsn']) as conn,conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            self.assertTrue(cur.fetchone()[0].startswith('datamind_test_'))
        final=self.poll(job)
        self.assertEqual(final['items'][0]['status'],'succeeded')

    def test_expired_deadline_is_recorded_as_a_stable_failure(self):
        job=self.submit(self.batch(items=[{'item_id':'drawing','type':'drawing','payload':self.payload()}]))
        timeout={'status':'timeout','error_code':'TASK_DEADLINE_EXCEEDED','elapsed_ms':1000.0,'deadline_seconds':1.0}
        with patch.object(jobs,'_execute_bounded',return_value=timeout):
            wave=jobs.work_once(self.dsn,deadline_s=1,workers=1)
        self.assertEqual(wave['outcomes'][0]['error_code'],'FILE_JOB_DEADLINE_EXCEEDED')
        final=self.poll(job)
        self.assertEqual(final['items'][0]['status'],'failed')
        self.assertEqual(final['items'][0]['error_code'],'FILE_JOB_DEADLINE_EXCEEDED')

    def test_child_error_code_is_propagated_without_internals(self):
        job=self.submit(self.batch(items=[{'item_id':'drawing','type':'drawing','payload':self.payload()}]))
        failure={'status':'failed','error_code':'CAD_SYNTHETIC_DENIED','elapsed_ms':5.0,'deadline_seconds':30.0,
                 'stderr':'private-dsn-password-secret'}
        with patch.object(jobs,'_execute_bounded',return_value=failure):
            wave=jobs.work_once(self.dsn,deadline_s=30,workers=1)
        self.assertEqual(wave['outcomes'][0]['error_code'],'CAD_SYNTHETIC_DENIED')
        self.assertNotIn('password',json.dumps(wave,default=str))
        final=self.poll(job)
        self.assertEqual(final['items'][0]['error_code'],'CAD_SYNTHETIC_DENIED')
        self.assertNotIn('private',json.dumps(final,default=str))

    def test_invalid_deadline_is_rejected_before_claiming(self):
        for value in (0,99999,True,'x'):
            with self.assertRaises(ValueError): jobs.work_once(self.dsn,deadline_s=value)
