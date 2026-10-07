"""Association audit overlays, version isolation and fail-closed review gates."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import unittest
import uuid
import psycopg2
from psycopg2.extras import Json,RealDictCursor
from fastapi.testclient import TestClient
from datamind.api import app
from datamind.agent import _write_dsn,_read_dsn
from datamind import cad_reviews,cad_store
import test_cad_assets as assets
from test_cad_dxf import drawing,source
import auth_fixtures


class CadReviewTests(unittest.TestCase):
    setUp=assets.CadAssetTests.setUp
    tearDown=assets.CadAssetTests.tearDown
    payload=assets.CadAssetTests.payload
    register=assets.CadAssetTests.register
    new_source=assets.CadAssetTests.new_source

    def start(self,reference=True,units=4,radius=10):
        self.doc,self.annotation,self.circle=drawing(reference=reference)
        self.doc.header['$INSUNITS']=units;self.circle.dxf.radius=radius
        self.path.write_bytes(source(self.doc));self.version=self.register()
        self.base=f'/v1/drawings/{self.id}'
        self.data=self.viewer.get(self.base+'/features').json();self.f=self.data['features'][0]
        self.url=self.base+'/features/'+self.f['feature_id']+'/reviews'

    def review_payload(self,**changes):
        state=self.viewer.get(self.base+'/features').json()['association_reviews'][0];latest=state['latest_review']
        p={k:self.version[k] for k in ('document_version','source_sha256','parser_version','bundle_sha256')}
        p.update(request_id=uuid.uuid4().hex,feature_sha256=state['feature_sha256'],previous_review_id=latest['review_id'] if latest else None,
                 decision='confirmed',selected_target=self.circle.dxf.handle,reason='合成材料：逐项核对原文与候选圆，确认对象关联。')
        p.update(changes);return p

    def record(self,p=None):
        r=self.reviewer.post(self.url,json=p or self.review_payload());self.assertEqual(r.status_code,200,r.text);return r.json()['review']

    def test_roles_login_and_no_approval_privilege(self):
        self.start()
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get(self.url).status_code,401)
            self.assertEqual(anonymous.post(self.url,json=self.review_payload()).status_code,401)
        self.assertEqual(self.viewer.post(self.url,json=self.review_payload()).status_code,403)
        c=TestClient(app);self.clients.append(c);auth_fixtures.login(c,roles=('approver',))
        self.assertEqual(c.post(self.url,json=self.review_payload()).status_code,403)
        self.assertEqual(self.reviewer.post('/v1/drawings',json=self.payload()).status_code,403)
        self.assertEqual(self.viewer.get(self.url).status_code,200)
        r=self.record();self.assertEqual(r['reviewer_label'],'隔离reviewer');self.assertIn('reviewer',r['reviewer_roles'])

    def test_csrf_origin_strict_types_and_spoofed_identity(self):
        self.start();p=self.review_payload()
        token=self.reviewer.headers.pop('X-CSRF-Token');self.assertEqual(self.reviewer.post(self.url,json=p).status_code,403);self.reviewer.headers['X-CSRF-Token']=token
        self.assertEqual(self.reviewer.post(self.url,json=p,headers={'Origin':'https://untrusted.invalid'}).status_code,403)
        for changes in [dict(reviewer_label='spoof'),dict(reviewer_user_id='spoof'),dict(document_version='1')]:
            self.assertEqual(self.reviewer.post(self.url,json={**p,**changes}).status_code,422)
        self.assertEqual(self.viewer.get(self.url).json()['reviews'],[])

    def test_confirmation_preserves_original_source_snapshot_and_extraction(self):
        self.start();before={suffix:self.viewer.get(self.base+suffix).content for suffix in ['/file','/preview','/viewer','/entities','']}
        self.assertEqual(self.data['review_gate']['excluded'][0]['reason'],'CAD_ASSOCIATION_NOT_REVIEWED')
        r=self.record();data=self.viewer.get(self.base+'/features').json()
        self.assertEqual(data['features'],self.data['features']);self.assertEqual(data['inspection_gate'],self.data['inspection_gate'])
        self.assertEqual(data['review_gate']['status'],'ready');self.assertEqual(data['association_reviews'][0]['latest_review']['review_id'],r['review_id'])
        for suffix,content in before.items():self.assertEqual(self.viewer.get(self.base+suffix).content,content)
        self.assertNotIn('approval',r)

    def test_manual_candidate_choice_resolves_only_review_overlay(self):
        self.start(reference=False);self.assertEqual(self.f['association_status'],'ambiguous')
        target=self.f['target_candidates'][-1];self.record(self.review_payload(selected_target=target))
        data=self.viewer.get(self.base+'/features').json()
        self.assertEqual(data['features'][0]['association_status'],'ambiguous');self.assertEqual(data['inspection_gate']['status'],'needs_review')
        self.assertEqual(data['review_gate']['status'],'ready');self.assertEqual(data['review_gate']['features'][0]['target_handle'],target)

    def test_needs_review_and_rejection_replace_effective_decision_with_history(self):
        self.start();first=self.record();second=self.record(self.review_payload(decision='needs_review',selected_target=None))
        self.assertEqual(second['previous_review_id'],first['review_id'])
        self.assertEqual(self.viewer.get(self.base+'/features').json()['review_gate']['excluded'][0]['reason'],'CAD_ASSOCIATION_REVIEW_PENDING')
        third=self.record(self.review_payload(decision='rejected',selected_target=None))
        self.assertEqual(third['previous_review_id'],second['review_id'])
        self.assertEqual(self.viewer.get(self.base+'/features').json()['review_gate']['excluded'][0]['reason'],'CAD_ASSOCIATION_REJECTED')
        self.assertEqual(len(self.viewer.get(self.url).json()['reviews']),3)
        self.assertEqual(self.viewer.get(self.base+'/features').json()['inspection_gate']['status'],'ready')

    def test_invalid_target_nonconfirmation_target_and_blank_reason_rejected(self):
        self.start()
        other=next(e for e in self.doc.modelspace().query('CIRCLE') if e.dxf.handle!=self.circle.dxf.handle)
        for changes in [dict(selected_target=other.dxf.handle),dict(selected_target=self.annotation.dxf.handle),dict(selected_target='FFFF'),dict(selected_target=None),dict(decision='rejected'),dict(reason='   ')]:
            r=self.reviewer.post(self.url,json=self.review_payload(**changes));self.assertEqual(r.status_code,422,r.text)
        self.assertEqual(self.viewer.get(self.url).json()['reviews'],[])
        self.assertEqual(self.reviewer.post(self.base+'/features/missing/reviews',json=self.review_payload()).status_code,404)

    def test_full_version_binding_and_stale_previous_review_are_rejected(self):
        self.start()
        for changes in [dict(document_version=2),dict(source_sha256='0'*64),dict(parser_version='other'),dict(bundle_sha256='0'*64),dict(feature_sha256='0'*64)]:
            r=self.reviewer.post(self.url,json=self.review_payload(**changes));self.assertEqual(r.status_code,409,r.text)
            self.assertEqual(r.json()['detail']['error_code'],'CAD_REVIEW_VERSION_CHANGED')
        self.record();r=self.reviewer.post(self.url,json=self.review_payload(previous_review_id=None));self.assertEqual(r.status_code,409)
        self.assertEqual(r.json()['detail']['error_code'],'CAD_REVIEW_STATE_CHANGED')

    def test_new_source_never_inherits_review_old_replay_and_retry_remain_bound(self):
        self.start();p=self.review_payload();first=self.record(p)
        old=self.viewer.get(self.base+'/features?version=1').json();oldsource=self.viewer.get(self.base+'/file?version=1').content
        self.new_source();v2=self.register(self.payload(previous_version=1,previous_source_sha256=self.version['source_sha256']))
        new=self.viewer.get(self.base+'/features').json();self.assertEqual(new['association_reviews'][0]['latest_review'],None)
        self.assertEqual(new['review_gate']['excluded'][0]['reason'],'CAD_ASSOCIATION_NOT_REVIEWED')
        self.assertEqual(self.viewer.get(self.base+'/features?version=1').json(),old)
        self.assertEqual(self.viewer.get(self.base+'/file?version=1').content,oldsource)
        r=self.reviewer.post(self.url,json={**p,'request_id':uuid.uuid4().hex});self.assertEqual(r.status_code,409)
        self.assertEqual(self.record(p)['review_id'],first['review_id']);self.assertEqual(self.viewer.get(self.base).json()['version']['document_version'],v2['document_version'])

    def test_idempotency_owner_payload_and_concurrent_state_changes(self):
        self.start();p=self.review_payload()
        with ThreadPoolExecutor(max_workers=2) as pool:replies=list(pool.map(lambda _:self.reviewer.post(self.url,json=p),range(2)))
        self.assertEqual([r.status_code for r in replies],[200,200]);self.assertEqual(replies[0].json()['review']['review_id'],replies[1].json()['review']['review_id'])
        self.assertEqual(self.admin.post(self.url,json=p).status_code,409)
        self.assertEqual(self.reviewer.post(self.url,json={**p,'reason':'different'}).status_code,409)
        contenders=[self.review_payload(decision='needs_review',selected_target=None),self.review_payload(decision='rejected',selected_target=None)]
        with ThreadPoolExecutor(max_workers=2) as pool:replies=list(pool.map(lambda p:self.reviewer.post(self.url,json=p),contenders))
        self.assertEqual(sorted(r.status_code for r in replies),[200,409]);self.assertEqual(len(self.viewer.get(self.url).json()['reviews']),2)
        self.assertEqual(self.record(p)['review_id'],self.viewer.get(self.url).json()['reviews'][0]['review_id'])

    def test_confirmation_cannot_bypass_geometry_conflict_or_unknown_units(self):
        self.start(radius=9);self.record()
        self.assertEqual(self.viewer.get(self.base+'/features').json()['review_gate']['excluded'][0]['reason'],'CAD_GEOMETRY_ANNOTATION_CONFLICT')
        self.new_source();self.doc.header['$INSUNITS']=0;self.path.write_bytes(source(self.doc));self.version=self.register(self.payload(previous_version=1,previous_source_sha256=self.version['source_sha256']))
        self.record();data=self.viewer.get(self.base+'/features').json()
        self.assertEqual(data['review_gate']['excluded'][0]['reason'],'CAD_VALUE_OR_UNIT_UNKNOWN');self.assertEqual(data['features'][0]['nominal_value'],None)

    def test_corrupt_record_snapshot_or_chain_blocks_review_reads(self):
        self.start();first=self.record();second=self.record(self.review_payload(decision='needs_review',selected_target=None))
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM cad_association_reviews WHERE review_id=%s',(second['review_id'],));original=dict(cur.fetchone())
        def update(changes,recompute=False):
            row={**original,**changes}
            if recompute:row['record_sha256']=cad_store.digest({k:row[k] for k in cad_reviews.AUDIT_FIELDS});changes={**changes,'record_sha256':row['record_sha256']}
            with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
                for k,v in changes.items():cur.execute('UPDATE cad_association_reviews SET '+k+'=%s WHERE review_id=%s',(Json(v) if k=='feature_snapshot' else v,second['review_id']))
        update({'reason':'tampered'})
        self.assertEqual(self.viewer.get(self.url).json()['detail']['error_code'],'CAD_REVIEW_HASH_MISMATCH')
        update({'reason':original['reason'],'feature_snapshot':{**original['feature_snapshot'],'annotation_raw':'wrong'}},True)
        self.assertEqual(self.viewer.get(self.base+'/features').json()['detail']['error_code'],'CAD_REVIEW_SNAPSHOT_MISMATCH')
        update({'feature_snapshot':original['feature_snapshot'],'previous_review_id':None},True)
        # Request payload must also match; restoring it isolates chain checking.
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM cad_association_reviews WHERE review_id=%s',(second['review_id'],));row=dict(cur.fetchone());row['request_payload']['previous_review_id']=None
            cur.execute('UPDATE cad_association_reviews SET request_payload=%s,record_sha256=%s WHERE review_id=%s',
                        (Json(row['request_payload']),cad_store.digest({k:row[k] for k in cad_reviews.AUDIT_FIELDS}),second['review_id']))
        self.assertEqual(self.viewer.get(self.url).json()['detail']['error_code'],'CAD_REVIEW_CHAIN_INVALID')
        self.assertEqual(self.viewer.get(self.base+'/file').status_code,200)

    def test_role_changes_logout_source_corruption_and_readonly_grants(self):
        self.start();p=self.review_payload();user=self.reviewer.get('/v1/auth/me').json()['user']
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:cur.execute("UPDATE auth_users SET roles=ARRAY['viewer'] WHERE user_id=%s",(user['user_id'],))
        self.assertEqual(self.reviewer.post(self.url,json=p).status_code,403)
        self.reviewer.post('/v1/auth/logout');self.assertEqual(self.reviewer.get(self.url).status_code,401)
        self.record_admin=self.admin.post(self.url,json=p);self.assertEqual(self.record_admin.status_code,200)
        with psycopg2.connect(_read_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM cad_association_reviews');self.assertGreater(cur.fetchone()[0],0)
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):cur.execute('DELETE FROM cad_association_reviews')
        (self.storage/(self.version['source_sha256']+'.dxf')).write_bytes(b'corrupt')
        self.assertEqual(self.viewer.get(self.url).status_code,409)
        self.assertEqual(self.admin.post(self.url,json=p).status_code,409)
