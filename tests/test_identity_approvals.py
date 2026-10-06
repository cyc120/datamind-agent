"""Server identity, request integrity and independent version-bound approvals."""
import hashlib
import secrets
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from fastapi.testclient import TestClient
from datamind.api import app, _public_document
from datamind.agent import _read_dsn, _write_dsn
from datamind.auth import COOKIE, provision
from datamind.field_store import read_verified_facts
import auth_fixtures
import test_document_fields as fixtures


class IdentityApprovalIntegrationTest(unittest.TestCase):
    def setUp(self):
        fixtures.FieldAPIIntegrationTest.setUp(self)
        self.clients=[]
        self.reviewer=self.client.get('/v1/auth/me').json()['user']
        self.approver=self.new_client(('approver',),'审批测试')
        self.viewer=self.new_client(('viewer',),'浏览测试')
        self.admin=self.new_client(('admin',),'管理测试')

    def new_client(self,roles,name):
        client=TestClient(app,client=('fixture-'+uuid.uuid4().hex[:12],50000))
        auth_fixtures.login(client,roles=roles,name=name);self.clients.append(client)
        return client

    def tearDown(self):
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('DELETE FROM document_fact_approvals WHERE document_id=%s',(self.doc_id,))
            cur.execute('DELETE FROM document_fact_reviews WHERE document_id=%s',(self.doc_id,))
        for client in self.clients:auth_fixtures.cleanup(client);client.close()
        fixtures.FieldAPIIntegrationTest.tearDown(self)

    get=fixtures.FieldAPIIntegrationTest.get
    update=fixtures.FieldAPIIntegrationTest.update

    def fact(self):return self.get().json()['facts'][0]

    def review(self,*,client=None,decision='confirmed',**changes):
        fact=self.fact();client=client or self.client
        payload=dict(request_id='req-'+uuid.uuid4().hex,document_version=1,source_sha256=self.sha,
            parser_version='field-test-v1',payload_sha256=fact['payload_sha256'],decision=decision,
            previous_review_id=(fact['latest_review'] or {}).get('review_id'),reason='按原文核对提取字段')
        payload.update(changes)
        return client.post(f'/v1/documents/{self.doc_id}/facts/{fact["fact_id"]}/reviews',json=payload)

    def approval_request(self,decision='approved',**changes):
        fact=self.fact()
        payload=dict(request_id='approve-'+uuid.uuid4().hex,document_version=1,source_sha256=self.sha,
            parser_version='field-test-v1',payload_sha256=fact['payload_sha256'],decision=decision,
            review_id=(fact['latest_review'] or {}).get('review_id','missing'),
            previous_approval_id=(fact['latest_approval'] or {}).get('approval_id'),reason='独立审批依据')
        payload.update(changes)
        return f'/v1/documents/{self.doc_id}/facts/{fact["fact_id"]}/approvals',payload

    def usable(self):return read_verified_facts(_public_document(self.doc_id),self.path,_read_dsn(),usable_only=True)

    def test_anonymous_viewer_and_wrong_roles_cannot_review_or_manage_models(self):
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get('/v1/documents').status_code,200)
            self.assertEqual(self.review(client=anonymous).status_code,401)
            self.assertEqual(anonymous.get('/v1/model/profiles').status_code,401)
        self.assertEqual(self.review(client=self.viewer).status_code,403)
        self.assertEqual(self.review(client=self.approver).status_code,403)
        self.assertEqual(self.client.get('/v1/model/profiles').status_code,403)
        self.assertEqual(self.admin.get('/v1/model/profiles').status_code,200)
        url,payload=self.approval_request()
        self.assertEqual(self.client.post(url,json=payload).status_code,403)

    def test_review_identity_is_server_owned_and_legacy_is_not_promoted(self):
        self.assertEqual(self.review(reviewer_label='冒用审批人').status_code,422)
        self.assertEqual(self.review(reviewer_user_id=self.reviewer['user_id']).status_code,422)
        response=self.review();self.assertEqual(response.status_code,200,response.text)
        review=response.json()['review']
        self.assertEqual(review['reviewer_user_id'],self.reviewer['user_id'])
        self.assertEqual(review['reviewer_label'],self.reviewer['display_name'])
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('UPDATE document_fact_reviews SET reviewer_user_id=NULL WHERE review_id=%s',(review['review_id'],))
        url,payload=self.approval_request()
        response=self.approver.post(url,json=payload)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['error_code'],'CONFIRMED_AUTHENTICATED_REVIEW_REQUIRED')

    def test_csrf_and_cross_origin_mutations_are_rejected(self):
        csrf=self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.review().status_code,403)
        self.client.headers['X-CSRF-Token']='wrong'
        self.assertEqual(self.review().status_code,403)
        self.client.headers['X-CSRF-Token']=csrf;self.client.headers['Origin']='https://untrusted.invalid'
        self.assertEqual(self.review().status_code,403)
        del self.client.headers['Origin']
        self.assertEqual(self.review().status_code,200)

    def test_logout_expiry_disabled_account_and_changed_role_revoke_access(self):
        token=self.client.cookies.get(COOKIE);digest=hashlib.sha256(token.encode()).hexdigest()
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('UPDATE auth_sessions SET expires_at=now()-interval \'1 second\' WHERE token_hash=%s',(digest,))
        self.assertEqual(self.review().status_code,401)
        auth_fixtures.login(self.client)
        self.reviewer=self.client.get('/v1/auth/me').json()['user']
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles='{viewer}' WHERE user_id=%s",(self.reviewer['user_id'],))
        self.assertEqual(self.review().status_code,403)
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles='{reviewer}',active=false WHERE user_id=%s",(self.reviewer['user_id'],))
        self.assertEqual(self.review().status_code,401)
        auth_fixtures.login(self.client);old=self.client.cookies.get(COOKIE)
        self.assertEqual(self.client.post('/v1/auth/logout').status_code,200)
        self.client.cookies.set(COOKIE,old)
        self.assertEqual(self.review().status_code,401)

    def test_password_hash_cookie_flags_and_query_role_cannot_read_sessions(self):
        username='isolated-'+uuid.uuid4().hex[:12];password=secrets.token_urlsafe(24)
        user_id=provision(username,'秘密测试',password,['viewer'],_write_dsn());self.client._fixture_users.append(user_id)
        response=self.client.post('/v1/auth/login',json={'username':username,'password':password})
        self.assertEqual(response.status_code,200)
        cookie=response.headers['set-cookie'].lower()
        self.assertIn('httponly',cookie);self.assertIn('samesite=strict',cookie)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertNotIn('password_hash',response.text);self.assertNotIn(password,response.text)
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT password_hash FROM auth_users WHERE user_id=%s',(user_id,))
            encoded=cur.fetchone()[0];self.assertTrue(encoded.startswith('scrypt$'));self.assertNotIn(password,encoded)
            token=response.cookies.get(COOKIE);cur.execute('SELECT token_hash FROM auth_sessions WHERE user_id=%s',(user_id,))
            self.assertEqual(cur.fetchone()[0],hashlib.sha256(token.encode()).hexdigest())
        with psycopg2.connect(_read_dsn()) as conn,conn.cursor() as cur:
            for table in ('auth_users','auth_sessions','auth_login_limits'):
                cur.execute('SELECT has_table_privilege(current_user,%s,\'SELECT\')',(table,))
                self.assertFalse(cur.fetchone()[0])
            cur.execute("SELECT has_table_privilege(current_user,'document_fact_approvals','INSERT,UPDATE,DELETE')")
            self.assertFalse(cur.fetchone()[0])

    def test_login_failed_unknown_disabled_and_cross_site_rate_limit(self):
        username='rate-'+uuid.uuid4().hex[:12]
        with TestClient(app,client=(username,50000)) as client:
            for _ in range(10):
                response=client.post('/v1/auth/login',json={'username':username,'password':'bad'})
                self.assertEqual(response.status_code,401)
            self.assertEqual(client.post('/v1/auth/login',json={'username':username,'password':'bad'}).status_code,429)
            self.assertEqual(client.post('/v1/auth/login',json={'username':username,'password':'bad'},headers={'Origin':'https://untrusted.invalid'}).status_code,403)

    def test_approval_requires_confirmed_review_and_independent_actor(self):
        url,payload=self.approval_request()
        self.assertEqual(self.approver.post(url,json=payload).status_code,409)
        self.assertEqual(self.review(decision='needs_review').status_code,200)
        url,payload=self.approval_request();self.assertEqual(self.approver.post(url,json=payload).status_code,409)
        self.assertEqual(self.review(client=self.admin).status_code,200)
        url,payload=self.approval_request()
        self.assertEqual(self.admin.post(url,json=payload).json()['detail']['error_code'],'REVIEWER_CANNOT_APPROVE_OWN_REVIEW')
        self.assertEqual(self.approver.post(url,json=payload).status_code,200)

    def test_approve_revoke_history_and_source_declarations_remain_separate(self):
        fact=self.fact();self.assertEqual(self.review().status_code,200)
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT payload,payload_sha256,approval_status FROM document_facts WHERE fact_id=%s',(fact['fact_id'],))
            original=cur.fetchone()
        url,payload=self.approval_request();first=self.approver.post(url,json=payload)
        self.assertEqual(first.status_code,200,first.text)
        approved=self.fact();self.assertEqual(approved['formal_approval_status'],'approved')
        self.assertEqual(approved['approval_status'],fact['approval_status'])
        url,payload=self.approval_request(decision='revoked')
        self.assertEqual(self.approver.post(url,json=payload).status_code,200)
        self.assertEqual(self.fact()['formal_approval_status'],'revoked')
        self.assertEqual(len(self.usable()),7)
        history=self.approver.get(url).json()['approvals'];self.assertEqual([a['decision'] for a in history],['approved','revoked'])
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT payload,payload_sha256,approval_status FROM document_facts WHERE fact_id=%s',(fact['fact_id'],))
            self.assertEqual(cur.fetchone(),original)

    def test_idempotence_stale_state_wrong_version_and_spoofed_approver(self):
        self.assertEqual(self.review().status_code,200)
        url,payload=self.approval_request()
        self.assertEqual(self.approver.post(url,json=dict(payload,approver_user_id='forged')).status_code,422)
        self.assertEqual(self.approver.post(url,json=dict(payload,document_version=2)).status_code,409)
        first=self.approver.post(url,json=payload);second=self.approver.post(url,json=payload)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['approval']['approval_id'],second.json()['approval']['approval_id'])
        self.assertEqual(self.approver.post(url,json=dict(payload,decision='rejected')).status_code,409)
        stale=dict(payload,request_id='stale-'+uuid.uuid4().hex)
        self.assertEqual(self.approver.post(url,json=stale).status_code,409)
        self.assertEqual(len(self.approver.get(url).json()['approvals']),1)

    def test_new_review_invalidates_previous_approval(self):
        self.assertEqual(self.review().status_code,200)
        url,payload=self.approval_request();self.assertEqual(self.approver.post(url,json=payload).status_code,200)
        self.assertEqual(self.review().status_code,200)
        self.assertEqual(self.fact()['formal_approval_status'],'stale')
        self.assertEqual(len(self.usable()),7)
        replay=self.approver.post(url,json=payload)
        self.assertEqual(replay.status_code,200)
        self.assertEqual(replay.json()['field_state']['formal_approval_status'],'stale')
        self.assertEqual(self.approver.post(url,json=dict(payload,request_id='new-'+uuid.uuid4().hex)).status_code,409)

    def test_new_review_cannot_bypass_rejection_or_revocation(self):
        self.assertEqual(self.review().status_code,200)
        url,payload=self.approval_request(decision='rejected')
        self.assertEqual(self.approver.post(url,json=payload).status_code,200)
        self.assertEqual(len(self.usable()),7)
        self.assertEqual(self.review().status_code,200)
        self.assertEqual(self.fact()['formal_approval_status'],'stale')
        self.assertEqual(len(self.usable()),7)
        url,payload=self.approval_request()
        self.assertEqual(self.approver.post(url,json=payload).status_code,200)
        self.assertEqual(len(self.usable()),8)

    def test_concurrent_approval_request_id_is_one_audit_record(self):
        self.assertEqual(self.review().status_code,200)
        url,payload=self.approval_request()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:self.approver.post(url,json=payload),range(2)))
        self.assertEqual([r.status_code for r in results],[200,200])
        self.assertEqual(len({r.json()['approval']['approval_id'] for r in results}),1)
        self.assertEqual(len(self.approver.get(url).json()['approvals']),1)

    def test_private_stale_facts_and_revocation_without_approval_fail(self):
        self.assertEqual(self.review().status_code,200)
        url,payload=self.approval_request(decision='revoked')
        self.assertEqual(self.approver.post(url,json=payload).json()['detail']['error_code'],'NO_ACTIVE_APPROVAL_TO_REVOKE')
        self.update("UPDATE document_assets SET acl='{private}' WHERE document_id=%s")
        self.assertEqual(self.approver.post(url,json=payload).status_code,404)
        self.update("UPDATE document_assets SET acl='{public}' WHERE document_id=%s")
        self.update('UPDATE document_facts SET document_version=2 WHERE document_id=%s')
        self.assertEqual(self.approver.post(url,json=payload).status_code,409)


if __name__=='__main__':unittest.main()
