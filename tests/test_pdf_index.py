"""Disposable metadata outbox tests with a deliberate fake vector adapter.

These tests cover SQL/auth/lease/recovery behavior, not embedding quality or
Qdrant compatibility. The separate vector and HTTP suites exercise real Qdrant.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from contextlib import redirect_stdout,redirect_stderr
import copy
import hashlib
import json
import importlib.util
import io
import secrets
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import Json
from pydantic import ValidationError

from datamind import pdf_index as index,pdf_publications as registry
import test_pdf_publications as fixtures


class FakeVectors:
    """A recoverable storage stub; no actual embedding or vector SDK involved."""
    def __init__(self):
        self.storage={};self.builds=0;self.writes=0;self.fail_confirmation=False

    def build_plan(self,snapshot,bundle,publication_id,embedder=None):
        self.builds+=1
        return {'publication_id':publication_id,'document_id':snapshot['document_id'],
                'document_version':snapshot['document_version'],'snapshot_sha256':snapshot['snapshot_sha256'],
                'chunks':copy.deepcopy(bundle['chunks']),'points':[{'id':publication_id,'vector':[float(self.builds)]}]}

    def validate_plan(self,plan,snapshot,bundle,publication_id):
        if (plan['publication_id']!=publication_id or plan['document_id']!=snapshot['document_id']
                or plan['document_version']!=snapshot['document_version'] or plan['snapshot_sha256']!=snapshot['snapshot_sha256']
                or plan['chunks']!=bundle['chunks']): index.fail('PDF_VECTOR_PLAN_INVALID')

    def write_and_verify(self,plan,client=None):
        self.writes+=1;self.storage[plan['publication_id']]=copy.deepcopy(plan)
        if self.fail_confirmation: index.fail('PDF_VECTOR_READBACK_FAILED',503)
        self.verify_plan(plan,client)

    def verify_plan(self,plan,client=None):
        if self.storage.get(plan['publication_id'])!=plan: index.fail('PDF_VECTOR_POINT_MISMATCH')

    def safe_chunks(self,plan): return copy.deepcopy(plan['chunks'])


class PdfIndexTests(unittest.TestCase):
    actor=fixtures.PdfPublicationTests.actor
    new_id=fixtures.PdfPublicationTests.new_id
    prepare=fixtures.PdfPublicationTests.prepare
    error=fixtures.PdfPublicationTests.error

    def setUp(self):
        fixtures.PdfPublicationTests.setUp(self)
        registry.register(fixtures.PdfPublicationTests.payload(self),self.dsn,self.identity)
        self.vectors=FakeVectors();self.vector_patch=patch.object(index,'_vectors',return_value=self.vectors);self.vector_patch.start()

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('DELETE FROM pdf_knowledge_heads WHERE document_id=ANY(%s)',(self.ids,))
            cur.execute('DELETE FROM pdf_knowledge_requests WHERE document_id=ANY(%s)',(self.ids,))
        self.vector_patch.stop();fixtures.PdfPublicationTests.tearDown(self)

    def payload(self,**changes):
        result={'request_id':uuid.uuid4().hex,'document_id':self.did,'document_version':1,
                'expected_manifest_sha256':self.first['manifest_sha256'],'previous_publication_id':None}
        result.update(changes);return result

    def submit(self,p=None,identity=None): return index.submit(p or self.payload(),self.dsn,identity or self.identity)
    def poll(self,p): return index.poll(p['publication_id'],self.dsn)
    def drain(self,p=None,workers=1): return index.drain(self.dsn,p['publication_id'] if p else None,workers)

    def _new_registered(self,did=None,version=1):
        native=self.prepare(did or self.new_id(),version)
        payload=fixtures.PdfPublicationTests.payload(self,native,previous_source_sha256=self.first['request']['source_sha256'] if version==2 else None)
        registry.register(payload,self.dsn,self.identity)
        return native

    def _freeze(self,publication):
        claim=index._claim(self.dsn,publication['publication_id']);row=index._row(self.dsn,publication['publication_id'])
        snapshot,bundle=index._snapshot_bundle(row,self.dsn)
        plan=self.vectors.build_plan(snapshot,bundle,row['publication_id'])
        row=index._freeze_plan(self.dsn,claim,plan);claim['plan_sha256']=row['vector_plan_sha256']
        return claim,plan

    def _expire(self,claim,attempts=None):
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute("UPDATE pdf_knowledge_requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE publication_id=%s",(claim['publication_id'],))
            if attempts is not None:
                cur.execute('UPDATE pdf_knowledge_requests SET attempts=%s,total_attempts=%s WHERE publication_id=%s',(attempts,attempts,claim['publication_id']))

    def _session(self,identity):
        value=hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('INSERT INTO auth_sessions(token_hash,user_id,csrf_token,expires_at) VALUES(%s,%s,%s,%s)',
                        (value,identity['user_id'],secrets.token_urlsafe(24),datetime.now(timezone.utc)+timedelta(hours=1)))
        return {**identity,'_session_hash':value}

    def test_strict_request_has_no_runtime_auth_embedding_or_storage_parameters(self):
        p=self.payload();invalid=[dict(p,document_version=True),dict(p,document_version=0),dict(p,previous_publication_id='old'),
            dict(p,expected_manifest_sha256='A'*64),dict(p,embedder='synthetic'),dict(p,client='custom'),
            dict(p,roles=['admin']),dict(p,source_path='/secret'),dict(p,indexed=True)]
        omitted=dict(p);omitted.pop('previous_publication_id');invalid.append(omitted)
        for value in invalid:
            with self.assertRaises(ValidationError): index.PublicationRequest.model_validate(value)

    def test_submit_is_queued_without_embedding_and_safe_metadata(self):
        p=self.submit();self.assertEqual(p['status'],'queued');self.assertIsNone(p['plan_sha256'])
        self.assertFalse(p['private_indexed']);self.assertFalse(p['public_knowledge_visible']);self.assertFalse(p['source_authority_verified'])
        self.assertFalse(p['vector_integrity_checked']);self.assertEqual(self.vectors.builds,0);self.assertEqual(self.vectors.writes,0)
        serialized=json.dumps(p,default=str)
        self.assertNotIn(self.identity['_session_hash'],serialized);self.assertNotIn(str(self.store),serialized)
        self.assertNotIn('vector_plan',serialized);self.assertEqual(self.poll(p),p)

    def test_owner_payload_idempotence_and_live_admin(self):
        p=self.payload();first=self.submit(p);self.assertEqual(self.submit(copy.deepcopy(p)),first)
        self.error('PDF_PUBLICATION_REQUEST_ID_REUSED',lambda:self.submit(p,self.other))
        self.error('PDF_PUBLICATION_REQUEST_ID_REUSED',lambda:self.submit(dict(p,expected_manifest_sha256='a'*64)))
        self.error('PERMISSION_DENIED',lambda:self.submit(self.payload(),{**self.viewer,'roles':['admin']}))

    def test_target_snapshot_and_publication_baseline_cas(self):
        self.error('PDF_PUBLICATION_SNAPSHOT_SELECTION_CHANGED',lambda:self.submit(self.payload(expected_manifest_sha256='a'*64)))
        self.error('PDF_PUBLICATION_HEAD_CHANGED',lambda:self.submit(self.payload(previous_publication_id='pdf-pub-'+uuid.uuid4().hex)))
        native=self._new_registered(self.did,2)
        self.error('PDF_PUBLICATION_SNAPSHOT_CHANGED',lambda:self.submit())
        self.assertEqual(self.submit(self.payload(document_version=2,expected_manifest_sha256=native['manifest_sha256']))['status'],'queued')

    def test_published_request_replay_after_snapshot_v2_keeps_historical_head(self):
        request=self.payload();first=self.submit(request);self.drain(first)
        before=self.poll(first);self._new_registered(self.did,2)
        self.assertEqual(self.submit(request),before)
        self.error('PDF_PUBLICATION_SNAPSHOT_CHANGED',lambda:index.current(self.did,self.dsn))
        chunks=index.read_chunks(first['publication_id'],self.dsn)
        self.assertTrue(chunks['publication']['private_indexed']);self.assertTrue(chunks['vector_integrity_checked'])
        self.assertEqual(chunks['chunks'][0]['document_version'],1)

    def test_success_atomically_publishes_head_and_result_and_uses_frozen_plan(self):
        p=self.submit();result=self.drain(p);self.assertEqual(result['outcomes'][0]['outcome'],'published')
        final=self.poll(p);self.assertTrue(final['private_indexed']);self.assertFalse(final['vector_integrity_checked'])
        self.assertIsNotNone(final['plan_sha256']);self.assertEqual(index.current(self.did,self.dsn),final)
        checked=index.read_chunks(p['publication_id'],self.dsn);self.assertTrue(checked['publication']['vector_integrity_checked'])
        self.assertEqual(checked['publication']['plan_sha256'],final['plan_sha256']);self.assertEqual(self.vectors.builds,1)
        self.assertEqual(self.drain(p)['claimed'],0)

    def test_frozen_plan_confirmation_failure_retries_without_reembedding(self):
        p=self.submit();self.vectors.fail_confirmation=True;self.drain(p);failed=self.poll(p)
        self.assertEqual(failed['status'],'failed');self.assertEqual(failed['error_code'],'PDF_VECTOR_READBACK_FAILED')
        plan=copy.deepcopy(self.vectors.storage[p['publication_id']]);sha=failed['plan_sha256']
        self.vectors.fail_confirmation=False;index.retry(p['publication_id'],self.dsn,self.identity)
        with patch.object(self.vectors,'build_plan',side_effect=AssertionError('frozen plan must recover offline')): self.drain(p)
        self.assertEqual(self.poll(p)['plan_sha256'],sha);self.assertEqual(self.vectors.storage[p['publication_id']],plan)

    def test_crash_after_upsert_expired_lease_replays_exact_frozen_plan(self):
        p=self.submit();claim,plan=self._freeze(p);self.vectors.write_and_verify(plan);self._expire(claim)
        with patch.object(self.vectors,'build_plan',side_effect=AssertionError('must not rebuild recovered plan')): self.drain(p)
        self.assertEqual(self.poll(p)['status'],'published');self.assertEqual(self.vectors.storage[p['publication_id']],plan)
        self.assertFalse(index._publish(self.dsn,claim));self.assertFalse(index._fail(self.dsn,claim,'OLD_OWNER_FAILED'))

    def test_late_owner_cannot_freeze_replace_publish_fail_or_renew_new_lease(self):
        p=self.submit();old=index._claim(self.dsn,p['publication_id']);self._expire(old);new=index._claim(self.dsn,p['publication_id'])
        row=index._row(self.dsn,p['publication_id']);snapshot,bundle=index._snapshot_bundle(row,self.dsn)
        plan=self.vectors.build_plan(snapshot,bundle,p['publication_id']);saved=index._freeze_plan(self.dsn,new,plan)
        new['plan_sha256']=saved['vector_plan_sha256'];self.vectors.write_and_verify(plan)
        self.assertIsNone(index._freeze_plan(self.dsn,old,dict(plan,points=[{'vector':[9.0]}])))
        self.assertFalse(index._publish(self.dsn,old));self.assertFalse(index._fail(self.dsn,old,'STALE_OWNER'))
        self.assertFalse(index._renew(self.dsn,old));self.assertTrue(index._publish(self.dsn,new))

    def test_expired_attempt_cap_fails_and_owner_retry_keeps_plan(self):
        p=self.submit();claim,plan=self._freeze(p);self._expire(claim,3)
        self.assertIsNone(index._claim(self.dsn,p['publication_id']));failed=self.poll(p)
        self.assertEqual(failed['error_code'],'PDF_PUBLICATION_LEASE_RETRIES_EXHAUSTED')
        sha=failed['plan_sha256'];retried=index.retry(p['publication_id'],self.dsn,self.identity)
        self.assertEqual(retried['attempts'],0);self.assertEqual(retried['total_attempts'],3);self.assertEqual(retried['plan_sha256'],sha)
        self.drain(p);self.assertEqual(self.poll(p)['total_attempts'],4)

    def test_retry_requires_original_live_admin_and_only_failed_status(self):
        p=self.submit();self.error('PDF_PUBLICATION_NOT_FAILED',lambda:index.retry(p['publication_id'],self.dsn,self.identity))
        self.vectors.fail_confirmation=True;self.drain(p)
        self.error('PDF_PUBLICATION_OWNER_REQUIRED',lambda:index.retry(p['publication_id'],self.dsn,self.other))
        self.error('PERMISSION_DENIED',lambda:index.retry(p['publication_id'],self.dsn,self.viewer))
        new_identity=self._session(self.identity);index.retry(p['publication_id'],self.dsn,new_identity)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT session_hash,request_payload FROM pdf_knowledge_requests WHERE publication_id=%s',(p['publication_id'],))
            session,payload=cur.fetchone();self.assertEqual(session,new_identity['_session_hash']);self.assertEqual(payload['request_id'],p['request_id'])

    def test_auth_changed_after_submission_is_failed_without_vector_operations(self):
        p=self.submit()
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE token_hash=%s',(self.identity['_session_hash'],))
        self.drain(p);self.assertEqual(self.poll(p)['error_code'],'SESSION_INVALID');self.assertEqual(self.vectors.writes,0)

    def test_admin_demotion_during_embedding_prevents_freeze_and_vector_write(self):
        p=self.submit();original=self.vectors.build_plan
        def demote(*args,**kwargs):
            result=original(*args,**kwargs)
            with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
                cur.execute("UPDATE auth_users SET roles=ARRAY['viewer'] WHERE user_id=%s",(self.identity['user_id'],))
            return result
        with patch.object(self.vectors,'build_plan',side_effect=demote): self.drain(p)
        self.assertEqual(self.poll(p)['error_code'],'PERMISSION_DENIED');self.assertIsNone(self.poll(p)['plan_sha256']);self.assertEqual(self.vectors.writes,0)

    def test_auth_revocation_during_vector_write_preserves_orphan_and_blocks_commit(self):
        p=self.submit();original=self.vectors.write_and_verify
        def revoke(*args,**kwargs):
            original(*args,**kwargs)
            with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
                cur.execute('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE token_hash=%s',(self.identity['_session_hash'],))
        with patch.object(self.vectors,'write_and_verify',side_effect=revoke): self.drain(p)
        self.assertEqual(self.poll(p)['error_code'],'SESSION_INVALID');self.assertIn(p['publication_id'],self.vectors.storage)
        self.error('PDF_PUBLICATION_HEAD_NOT_FOUND',lambda:index.current(self.did,self.dsn))

    def test_session_expires_while_waiting_for_publication_row_lock(self):
        p=self.submit();claim,plan=self._freeze(p);entered=threading.Event();original=registry._identity
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET expires_at=clock_timestamp()+interval '1 second' WHERE token_hash=%s",(self.identity['_session_hash'],))
        def signal(*args):
            result=original(*args);entered.set();return result
        locker=psycopg2.connect(self.dsn)
        try:
            with locker.cursor() as cur: cur.execute('SELECT publication_id FROM pdf_knowledge_requests WHERE publication_id=%s FOR UPDATE',(p['publication_id'],))
            with patch.object(registry,'_identity',side_effect=signal),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(index._publish,self.dsn,claim);self.assertTrue(entered.wait(10));time.sleep(1.3);locker.commit()
                self.error('SESSION_INVALID',lambda:future.result(10))
        finally: locker.close()
        self.assertEqual(self.poll(p)['status'],'running')
        self.error('PDF_PUBLICATION_HEAD_NOT_FOUND',lambda:index.current(self.did,self.dsn))

    def test_lease_expires_while_waiting_for_row_lock_all_writes_are_fenced(self):
        p=self.submit();claim=index._claim(self.dsn,p['publication_id']);row=index._row(self.dsn,p['publication_id'])
        snapshot,bundle=index._snapshot_bundle(row,self.dsn);plan=self.vectors.build_plan(snapshot,bundle,p['publication_id'])
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute("UPDATE pdf_knowledge_requests SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE publication_id=%s",(p['publication_id'],))
        locker=psycopg2.connect(self.dsn)
        try:
            with locker.cursor() as cur: cur.execute('SELECT publication_id FROM pdf_knowledge_requests WHERE publication_id=%s FOR UPDATE',(p['publication_id'],))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(index._freeze_plan,self.dsn,claim,plan);time.sleep(1.3);locker.commit();self.assertIsNone(future.result(10))
        finally: locker.close()
        self.assertIsNone(self.poll(p)['plan_sha256']);self.assertFalse(index._renew(self.dsn,claim));self.assertFalse(index._fail(self.dsn,claim,'LATE_FAILED'))

    def test_head_row_wait_expiring_lease_does_not_move_current_pointer(self):
        first=self.submit();self.drain(first);second=self.submit(self.payload(previous_publication_id=first['publication_id']))
        claim,plan=self._freeze(second)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute("UPDATE pdf_knowledge_requests SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE publication_id=%s",(second['publication_id'],))
        locker=psycopg2.connect(self.dsn)
        try:
            with locker.cursor() as cur: cur.execute('SELECT current_publication_id FROM pdf_knowledge_heads WHERE document_id=%s FOR UPDATE',(self.did,))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(index._publish,self.dsn,claim);time.sleep(1.3);locker.commit();self.assertFalse(future.result(10))
        finally: locker.close()
        self.assertEqual(index.current(self.did,self.dsn)['publication_id'],first['publication_id'])

    def test_global_two_running_cap_across_independent_claimers(self):
        publications=[self.submit()]
        for _ in range(2):
            native=self._new_registered();publications.append(self.submit(self.payload(document_id=native['request']['document_id'],expected_manifest_sha256=native['manifest_sha256'])))
        with ThreadPoolExecutor(max_workers=6) as pool: claims=list(pool.map(lambda _:index._claim(self.dsn),range(6)))
        self.assertEqual(sum(x is not None for x in claims),2)
        for claim in claims:
            if claim: self.assertTrue(index._fail(self.dsn,claim,'TEST_RELEASE'))

    def test_competing_publications_one_cas_winner_retains_losing_vectors(self):
        first=self.submit();second=self.submit();barrier=threading.Barrier(2);original=self.vectors.write_and_verify
        def synchronize(*args,**kwargs): original(*args,**kwargs);barrier.wait(10)
        with patch.object(self.vectors,'write_and_verify',side_effect=synchronize): result=self.drain(workers=2)
        outcomes=[x['outcome'] for x in result['outcomes']];self.assertEqual(outcomes.count('published'),1);self.assertEqual(outcomes.count('failed'),1)
        failed=next(p for p in (first,second) if self.poll(p)['status']=='failed')
        self.assertEqual(self.poll(failed)['error_code'],'PDF_PUBLICATION_HEAD_CHANGED');self.assertEqual(len(self.vectors.storage),2)
        index.retry(failed['publication_id'],self.dsn,self.identity);self.drain(failed)
        self.assertEqual(self.poll(failed)['error_code'],'PDF_PUBLICATION_HEAD_CHANGED')

    def test_new_snapshot_during_vector_write_cannot_publish_old_version(self):
        p=self.submit();original=self.vectors.write_and_verify;native=self.prepare(self.did,2)
        def advance(*args,**kwargs):
            original(*args,**kwargs)
            registry.register(fixtures.PdfPublicationTests.payload(self,native,previous_source_sha256=self.first['request']['source_sha256']),self.dsn,self.identity)
        with patch.object(self.vectors,'write_and_verify',side_effect=advance): self.drain(p)
        self.assertEqual(self.poll(p)['error_code'],'PDF_PUBLICATION_SNAPSHOT_CHANGED');self.assertIn(p['publication_id'],self.vectors.storage)

    def test_atomic_publish_rollback_preserves_frozen_plan_and_retries(self):
        p=self.submit();claim,plan=self._freeze(p);original=index._result
        def fault(*args): raise RuntimeError('private secret path /never/expose')
        with patch.object(index,'_result',side_effect=fault):
            with self.assertRaises(RuntimeError): index._publish(self.dsn,claim)
        self.error('PDF_PUBLICATION_HEAD_NOT_FOUND',lambda:index.current(self.did,self.dsn))
        self.assertEqual(self.poll(p)['status'],'running');self.assertTrue(index._publish(self.dsn,claim))
        self.assertEqual(self.poll(p)['status'],'published')

    def test_external_missing_or_altered_vectors_fail_history_read_without_republishing(self):
        p=self.submit();self.drain(p);saved=self.vectors.storage.pop(p['publication_id'])
        self.error('PDF_VECTOR_POINT_MISMATCH',lambda:index.read_chunks(p['publication_id'],self.dsn))
        self.assertEqual(self.poll(p)['status'],'published');self.error('PDF_PUBLICATION_NOT_FAILED',lambda:index.retry(p['publication_id'],self.dsn,self.identity))
        self.vectors.storage[p['publication_id']]=saved;self.assertTrue(index.read_chunks(p['publication_id'],self.dsn)['vector_integrity_checked'])

    def test_database_request_snapshot_plan_and_owner_tampering_fail_closed(self):
        p=self.submit();self.drain(p)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE pdf_knowledge_requests SET owner_user_id=%s WHERE publication_id=%s',(self.other['user_id'],p['publication_id']))
        self.error('PDF_PUBLICATION_EVIDENCE_INVALID',lambda:self.poll(p))

    def test_database_plan_digest_tampering_blocks_metadata_and_chunk_reads(self):
        p=self.submit();self.drain(p)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE pdf_knowledge_requests SET vector_plan=%s WHERE publication_id=%s',(Json({'publication_id':p['publication_id']}),p['publication_id']))
        self.error('PDF_PUBLICATION_EVIDENCE_INVALID',lambda:index.read_chunks(p['publication_id'],self.dsn))

    def test_database_request_and_snapshot_hashes_are_checked_for_metadata(self):
        p=self.submit();original=index._row(self.dsn,p['publication_id'])
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            altered=copy.deepcopy(original['request_payload']);altered['request_id']='forged'
            cur.execute('UPDATE pdf_knowledge_requests SET request_payload=%s WHERE publication_id=%s',(Json(altered),p['publication_id']))
        self.error('PDF_PUBLICATION_EVIDENCE_INVALID',lambda:self.poll(p))
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE pdf_knowledge_requests SET request_payload=%s,target_snapshot_sha256=%s WHERE publication_id=%s',
                        (Json(original['request_payload']),'a'*64,p['publication_id']))
        self.error('PDF_PUBLICATION_EVIDENCE_INVALID',lambda:self.poll(p))

    def test_confirmed_plan_cannot_be_replaced_before_database_publication(self):
        p=self.submit();claim,plan=self._freeze(p);replacement=copy.deepcopy(plan);replacement['points'][0]['vector']=[9.0]
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE pdf_knowledge_requests SET vector_plan=%s,vector_plan_sha256=%s WHERE publication_id=%s',
                        (Json(replacement),registry.digest(replacement),p['publication_id']))
        self.error('PDF_VECTOR_PLAN_CHANGED',lambda:index._publish(self.dsn,claim))

    def test_filesystem_corruption_blocks_worker_without_reparsing(self):
        p=self.submit();path=self.store/self.first['bundle_path'];path.chmod(0o600);path.write_bytes(b'{}')
        self.drain(p);self.assertEqual(self.poll(p)['error_code'],'PDF_ARTIFACT_TAMPERED');self.assertEqual(self.vectors.builds,0)

    def test_listing_is_bounded_metadata_only_and_no_vector_or_session_leaks(self):
        p=self.submit();self.drain(p)
        with patch.object(self.vectors,'verify_plan',side_effect=AssertionError('poll/list/current only metadata')):
            self.poll(p);index.current(self.did,self.dsn);listing=index.listing(self.dsn,1)
        self.assertFalse(listing['publications'][0]['vector_integrity_checked'])
        self.assertNotIn(self.identity['_session_hash'],json.dumps(listing,default=str))
        self.assertNotIn('points',json.dumps(listing,default=str))
        self.error('PDF_PAGINATION_INVALID',lambda:index.listing(self.dsn,True,0))

    def test_original_snapshot_and_legacy_tables_remain_unchanged_no_readonly_grant(self):
        tables=('document_assets','document_blocks','chunks','document_facts','document_fact_reviews','file_jobs','file_job_items')
        def counts():
            with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
                result={}
                for table in tables:
                    cur.execute('SELECT count(*) FROM '+table);result[table]=cur.fetchone()[0]
                return result
        before=counts();snapshot=registry.read_version(self.did,self.dsn);p=self.submit();self.drain(p)
        self.assertEqual(registry.public_version(registry.read_version(self.did,self.dsn)),registry.public_version(snapshot));self.assertEqual(counts(),before)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            for table in ('pdf_knowledge_requests','pdf_knowledge_heads'):
                cur.execute("SELECT has_table_privilege('readonly_user',%s,'SELECT')",(table,));self.assertFalse(cur.fetchone()[0])

    def test_worker_failure_is_sanitized_and_worker_count_is_bounded(self):
        p=self.submit()
        with patch.object(self.vectors,'build_plan',side_effect=RuntimeError('password /private/data secret')): result=self.drain(p)
        self.assertEqual(result['outcomes'][0]['error_code'],'PDF_PUBLICATION_EXECUTION_FAILED')
        self.assertNotIn('password',json.dumps(result));self.assertNotIn('/private/data',json.dumps(result))
        for count in (0,3,True):
            with self.assertRaises(ValueError): index.work_once(self.dsn,workers=count)

    def test_cli_drain_failure_exits_nonzero_once_success_and_mutually_exclusive_modes(self):
        spec=importlib.util.spec_from_file_location('pdf_index_worker_cli',registry.ROOT/'scripts/pdf_index_worker.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        failed={'waves':1,'claimed':1,'outcomes':[{'outcome':'failed','error_code':'PDF_VECTOR_READBACK_FAILED'}]}
        with patch('sys.argv',['pdf_index_worker','--drain']),patch.object(cli,'drain',return_value=failed),redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(),1)
        with patch('sys.argv',['pdf_index_worker','--once']),patch.object(cli,'work_once',return_value={'claimed':0,'outcomes':[]}),redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(),0)
        with patch('sys.argv',['pdf_index_worker','--once','--drain']),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught: cli.main()
            self.assertEqual(caught.exception.code,2)
