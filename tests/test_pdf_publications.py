"""Real native artifacts and transactions in the disposable metadata clone."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import Json
from pydantic import ValidationError
import pymupdf

from datamind import pdf_publications as registry
from datamind.agent import _write_dsn
from datamind.auth import provision
from ingestion import pdf_prepare as prepared


class PdfPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pdf-publication-')
        self.base=Path(self.temp.name).resolve();self.store=self.base/'prepared'
        self.patcher=patch.object(registry,'PREPARED_STORE',self.store);self.patcher.start()
        self.dsn=_write_dsn();self.users=[];self.ids=[]
        self.identity=self.actor('admin');self.other=self.actor('admin');self.viewer=self.actor('viewer')
        self.did=self.new_id();self.first=self.prepare(self.did)

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('DELETE FROM pdf_snapshot_versions WHERE document_id=ANY(%s)',(self.ids,))
            cur.execute('DELETE FROM pdf_snapshot_assets WHERE document_id=ANY(%s)',(self.ids,))
            cur.execute('DELETE FROM auth_users WHERE user_id=ANY(%s)',(self.users,))
        self.patcher.stop();self.temp.cleanup()

    def actor(self,role):
        uid=provision('pdf-'+uuid.uuid4().hex[:22],'隔离 PDF '+role,secrets.token_urlsafe(24),[role],self.dsn)
        self.users.append(uid);token=hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('INSERT INTO auth_sessions(token_hash,user_id,csrf_token,expires_at) VALUES(%s,%s,%s,%s)',
                        (token,uid,secrets.token_urlsafe(24),datetime.now(timezone.utc)+timedelta(hours=1)))
        return {'user_id':uid,'_session_hash':token}

    def new_id(self):
        value='pdf-test-'+uuid.uuid4().hex[:20];self.ids.append(value);return value

    def prepare(self,did,version=1,text=None):
        source=self.base/(uuid.uuid4().hex+'.pdf')
        with pymupdf.open() as doc:
            page=doc.new_page(width=300,height=220)
            page.insert_text((10,20),text or 'Synthetic native PDF: measurement value 20.04 mm and identifier '+uuid.uuid4().hex[:6],fontsize=8)
            source.write_bytes(doc.tobytes(garbage=4,deflate=True))
        return prepared.prepare_pdf(source,self.store,{'document_id':did,'document_version':version,
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'job_id':'job-'+uuid.uuid4().hex,
            'attempt_token':'attempt-'+uuid.uuid4().hex})

    def payload(self,snapshot=None,**overrides):
        snapshot=snapshot or self.first
        p={'request_id':uuid.uuid4().hex,'document_id':snapshot['request']['document_id'],'title':'  原生 PDF 隔离登记  ',
           'job_id':snapshot['request']['job_id'],'attempt_token':snapshot['request']['attempt_token'],
           'manifest_sha256':snapshot['manifest_sha256'],'previous_version':snapshot['request']['document_version']-1,
           'previous_source_sha256':None}
        p.update(overrides);return p

    def register(self,p=None,identity=None):
        return registry.register(p or self.payload(),self.dsn,identity or self.identity)

    def error(self,code,fn):
        with self.assertRaises(HTTPException) as caught: fn()
        self.assertEqual(caught.exception.detail['error_code'],code)
        return caught.exception

    def mutate(self,column,value,version=1):
        self.assertIn(column,('snapshot','snapshot_sha256','request_payload','request_sha256','registrar_label',
                             'registrar_roles','registrar_user_id','registration_sha256','source_sha256','manifest_sha256'))
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('UPDATE pdf_snapshot_versions SET '+column+'=%s WHERE document_id=%s AND document_version=%s',
                        (Json(value) if column in ('snapshot','request_payload') else value,self.did,version))

    def test_request_strict_types_previous_identity_title_and_unknown_fields(self):
        p=self.payload()
        invalid=[dict(p,previous_version=True),dict(p,previous_source_sha256='a'*64),dict(p,previous_version=1),
                 dict(p,title='  '),dict(p,provenance='authorized_external'),dict(p,registrar_roles=['admin']),
                 dict(p,indexed=True),dict(p,source_path='/private/file.pdf'),dict(p,manifest_sha256='A'*64)]
        missing=dict(p);missing.pop('previous_source_sha256');invalid.append(missing)
        for value in invalid:
            with self.assertRaises(ValidationError): registry.RegistrationRequest.model_validate(value)
        self.assertEqual(registry.RegistrationRequest.model_validate(p).title,'原生 PDF 隔离登记')

    def test_authenticated_identity_and_prepare_only_boundary(self):
        p=self.payload();value=self.register(p,{**self.identity,'display_name':'spoof','roles':['viewer']})
        self.assertEqual(value['registrar_user_id'],self.identity['user_id'])
        self.assertEqual(value['registrar_label'],'隔离 PDF admin');self.assertEqual(value['registrar_roles'],['admin'])
        self.assertTrue(value['registration_only']);self.assertTrue(value['registrar_authentication_verified'])
        self.assertTrue(value['artifact_integrity_checked'])
        for field in ('indexed','knowledge_visible','source_authority_verified'): self.assertFalse(value[field])
        serialized=json.dumps(value,default=str)
        self.assertNotIn(self.identity['_session_hash'],serialized);self.assertNotIn(str(self.store),serialized)
        self.error('PERMISSION_DENIED',lambda:self.register(self.payload(),{**self.viewer,'roles':['admin']}))

    def test_idempotent_request_remains_valid_after_next_version(self):
        p=self.payload();first=self.register(p);second_native=self.prepare(self.did,2)
        second=self.register(self.payload(second_native,previous_source_sha256=first['source_sha256']))
        self.assertEqual(second['document_version'],2);self.assertEqual(self.register(copy.deepcopy(p)),first)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM pdf_snapshot_versions WHERE document_id=%s',(self.did,));self.assertEqual(cur.fetchone()[0],2)

    def test_request_owner_and_normalized_payload_reuse_rejected(self):
        p=self.payload();self.register(p)
        self.error('PDF_REQUEST_ID_REUSED',lambda:self.register(dict(p,title='different')))
        self.error('PDF_REQUEST_ID_REUSED',lambda:self.register(p,self.other))
        self.assertEqual(self.register(dict(p,title=p['title'].strip()))['document_version'],1)

    def test_attempt_cannot_be_registered_under_another_request(self):
        self.register();self.error('PDF_ATTEMPT_ALREADY_REGISTERED',lambda:self.register())

    def test_prepared_document_version_and_manifest_are_derived(self):
        self.error('PDF_PREPARED_IDENTITY_MISMATCH',lambda:self.register(self.payload(manifest_sha256='a'*64)))
        self.error('PDF_PREPARED_IDENTITY_MISMATCH',lambda:self.register(self.payload(document_id=self.new_id())))
        self.error('PDF_PREPARED_IDENTITY_MISMATCH',lambda:self.register(self.payload(previous_version=1,previous_source_sha256='a'*64)))

    def test_same_head_competition_one_commits_one_cas_rejects(self):
        other=self.prepare(self.did);payloads=[self.payload(),self.payload(other)]
        barrier=threading.Barrier(2);original=registry._verify
        def synchronize(*args):
            result=original(*args);barrier.wait(10);return result
        def execute(p):
            try: return self.register(p)
            except HTTPException as exc: return exc.detail['error_code']
        with patch.object(registry,'_verify',side_effect=synchronize),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(execute,payloads))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1);self.assertIn('PDF_VERSION_CHANGED',results)

    def test_distinct_documents_register_concurrently(self):
        other=self.prepare(self.new_id());barrier=threading.Barrier(2);original=registry._verify
        def synchronize(*args):
            result=original(*args);barrier.wait(10);return result
        with patch.object(registry,'_verify',side_effect=synchronize),ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.register,[self.payload(),self.payload(other)]))
        self.assertEqual(len({x['document_id'] for x in results}),2)

    def _change_after_verify(self,sql,value=None):
        original=registry._verify
        def changed(*args):
            result=original(*args)
            with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
                cur.execute(sql,(value or self.identity['user_id'],))
            return result
        return patch.object(registry,'_verify',side_effect=changed)

    def test_session_revocation_after_verification_rejected(self):
        with self._change_after_verify('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE token_hash=%s',self.identity['_session_hash']):
            self.error('SESSION_INVALID',lambda:self.register())

    def test_account_deactivation_after_verification_rejected(self):
        with self._change_after_verify('UPDATE auth_users SET active=false WHERE user_id=%s'):
            self.error('SESSION_INVALID',lambda:self.register())

    def test_admin_demotion_after_verification_rejected(self):
        with self._change_after_verify("UPDATE auth_users SET roles=ARRAY['viewer'] WHERE user_id=%s"):
            self.error('PERMISSION_DENIED',lambda:self.register())

    def test_session_expiry_after_lock_wait_uses_wall_clock(self):
        p=self.payload();entered=threading.Event();original=registry._verify
        def signal(*args):
            result=original(*args);entered.set();return result
        locker=psycopg2.connect(self.dsn)
        try:
            with locker.cursor() as cur: cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('pdf-document:'+self.did,))
            with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
                cur.execute("UPDATE auth_sessions SET expires_at=clock_timestamp()+interval '1 second' WHERE token_hash=%s",(self.identity['_session_hash'],))
            with patch.object(registry,'_verify',side_effect=signal),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.register,p);self.assertTrue(entered.wait(10));time.sleep(1.3);locker.commit()
                self.error('SESSION_INVALID',lambda:future.result(10))
        finally: locker.close()

    def test_session_expiry_while_waiting_for_unchanged_auth_row_lock_rejected(self):
        p=self.payload();entered=threading.Event();original=registry._identity
        def signal(*args):
            entered.set();return original(*args)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET expires_at=clock_timestamp()+interval '1 second' WHERE token_hash=%s",(self.identity['_session_hash'],))
        locker=psycopg2.connect(self.dsn)
        try:
            with locker.cursor() as cur: cur.execute('SELECT token_hash FROM auth_sessions WHERE token_hash=%s FOR UPDATE',(self.identity['_session_hash'],))
            with patch.object(registry,'_identity',side_effect=signal),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.register,p);self.assertTrue(entered.wait(10));time.sleep(1.3);locker.commit()
                self.error('SESSION_INVALID',lambda:future.result(10))
        finally: locker.close()

    def test_database_lock_wait_has_stable_busy_failure_and_retry(self):
        p=self.payload();locker=psycopg2.connect(self.dsn)
        try:
            with locker.cursor() as cur: cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('pdf-document:'+self.did,))
            with patch.object(registry,'REGISTRATION_LOCK_SECONDS',1),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.register,p);failure=self.error('PDF_REGISTRATION_BUSY',lambda:future.result(10))
                self.assertEqual(failure.status_code,503)
            locker.commit();self.assertEqual(self.register(p)['document_version'],1)
        finally: locker.close()

    def test_transaction_failure_rolls_back_registry_preserves_prepare_and_can_retry(self):
        p=self.payload()
        with patch.object(registry,'public_version',side_effect=RuntimeError('forced rollback')):
            with self.assertRaises(RuntimeError): self.register(p)
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM pdf_snapshot_assets WHERE document_id=%s',(self.did,));self.assertEqual(cur.fetchone()[0],0)
        self.assertEqual(prepared.verify_prepared(self.store,p['job_id'],p['attempt_token']),self.first)
        self.assertEqual(self.register(p)['document_version'],1)

    def test_previous_sha_and_version_cas_both_required(self):
        first=self.register();native=self.prepare(self.did,2)
        self.error('PDF_VERSION_CHANGED',lambda:self.register(self.payload(native,previous_source_sha256='a'*64)))
        self.assertEqual(self.register(self.payload(native,previous_source_sha256=first['source_sha256']))['document_version'],2)

    def test_v2_preserves_v1_metadata_and_all_artifact_bytes(self):
        first=self.register();row=registry.read_version(self.did,self.dsn,1)
        captured={kind:registry.read_artifact(row,kind,1 if kind=='page_image' else None) for kind in ('source','bundle','page_image')}
        second=self.prepare(self.did,2);self.register(self.payload(second,previous_source_sha256=first['source_sha256']))
        old=registry.read_version(self.did,self.dsn,1);self.assertEqual(registry.public_version(old),first)
        for kind,data in captured.items(): self.assertEqual(registry.read_artifact(old,kind,1 if kind=='page_image' else None),data)
        self.assertEqual(registry.read_version(self.did,self.dsn)['document_version'],2)

    def test_list_history_metadata_only_and_bounded_pagination(self):
        first=self.register();native=self.prepare(self.did,2);self.register(self.payload(native,previous_source_sha256=first['source_sha256']))
        with patch.object(registry,'_verify',side_effect=AssertionError('metadata query must not read files')):
            listed=registry.list_assets(self.dsn,1);history=registry.history(self.did,self.dsn,1)
        self.assertFalse(listed['snapshots'][0]['artifact_integrity_checked']);self.assertFalse(history['versions'][0]['artifact_integrity_checked'])
        self.assertEqual(history['current_version'],2);self.assertTrue(history['has_more'])
        self.assertEqual(registry.history(self.did,self.dsn,1,1)['versions'][0]['document_version'],1)
        for limit,offset in ((True,0),(101,0),(1,-1),(1,10001)):
            self.error('PDF_PAGINATION_INVALID',lambda:registry.list_assets(self.dsn,limit,offset))

    def test_database_snapshot_digest_tampering_rejected_without_files(self):
        self.register();value=copy.deepcopy(self.first);value['counts']['page_count']=2;self.mutate('snapshot',value)
        self.error('PDF_SNAPSHOT_EVIDENCE_INVALID',lambda:registry.history(self.did,self.dsn))

    def test_database_request_tampering_even_with_recomputed_request_hash_rejected(self):
        p=self.payload();self.register(p);p['title']='altered';p['title']=p['title'].strip()
        self.mutate('request_payload',p);self.mutate('request_sha256',registry.digest(p))
        self.error('PDF_REGISTRATION_EVIDENCE_INVALID',lambda:registry.history(self.did,self.dsn))

    def test_database_registrar_identity_and_flags_cannot_be_silently_changed(self):
        self.register();self.mutate('registrar_label','forged administrator')
        self.error('PDF_REGISTRATION_EVIDENCE_INVALID',lambda:registry.history(self.did,self.dsn))

    def test_filesystem_corruption_blocks_read_and_idempotent_replay(self):
        p=self.payload();self.register(p);path=self.store/self.first['bundle_path'];path.chmod(0o600);path.write_bytes(b'{}')
        self.error('PDF_ARTIFACT_TAMPERED',lambda:registry.read_version(self.did,self.dsn))
        self.error('PDF_ARTIFACT_TAMPERED',lambda:self.register(p))
        self.assertFalse(registry.history(self.did,self.dsn)['versions'][0]['artifact_integrity_checked'])

    def test_post_verification_artifact_change_is_checked_on_captured_bytes(self):
        self.register();row=registry.read_version(self.did,self.dsn)
        path=self.store/self.first['source_path'];path.chmod(0o600);path.write_bytes(b'%PDF-changed')
        self.error('PDF_ARTIFACT_TAMPERED',lambda:registry.read_artifact(row,'source'))

    def test_artifact_symlink_and_fifo_are_rejected_without_blocking(self):
        self.register();row=registry.read_version(self.did,self.dsn);path=self.store/self.first['source_path']
        data=path.read_bytes();path.unlink();outside=self.base/'outside.pdf';outside.write_bytes(data);path.symlink_to(outside)
        self.error('PDF_ARTIFACT_INVALID',lambda:registry.read_artifact(row,'source'))
        path.unlink();os.mkfifo(path);start=time.monotonic()
        self.error('PDF_ARTIFACT_INVALID',lambda:registry.read_artifact(row,'source'))
        self.assertLess(time.monotonic()-start,2)

    def test_verification_slots_are_bounded_and_release_after_error(self):
        slots=threading.BoundedSemaphore(1)
        with patch.object(registry,'_VERIFY_SLOTS',slots):
            slots.acquire()
            try: self.error('PDF_VERIFICATION_BUSY',lambda:self.register())
            finally: slots.release()
            self.error('PDF_PREPARED_IDENTITY_MISMATCH',lambda:self.register(self.payload(manifest_sha256='a'*64)))
            self.assertEqual(self.register()['document_version'],1)

    def test_registry_never_writes_legacy_knowledge_or_review_tables(self):
        tables=('document_assets','document_blocks','chunks','document_facts','document_fact_reviews',
                'document_fact_approvals','cad_assets','cad_versions','file_jobs','file_job_items')
        def counts():
            with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
                values={}
                for table in tables:
                    cur.execute('SELECT count(*) FROM '+table);values[table]=cur.fetchone()[0]
                return values
        before=counts();self.register();registry.read_version(self.did,self.dsn);self.assertEqual(counts(),before)

    def test_registry_has_no_readonly_role_grant(self):
        with psycopg2.connect(self.dsn) as conn,conn.cursor() as cur:
            for table in ('pdf_snapshot_assets','pdf_snapshot_versions'):
                cur.execute("SELECT has_table_privilege('readonly_user',%s,'SELECT')",(table,))
                self.assertFalse(cur.fetchone()[0])
