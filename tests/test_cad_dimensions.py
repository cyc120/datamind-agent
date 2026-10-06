"""Authenticated service lifecycle against a disposable metadata clone."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
import psycopg2
from psycopg2.extras import Json
from fastapi.testclient import TestClient
from datamind.api import app
from datamind.agent import _write_dsn
from datamind import cad_dimensions as service,cad_store,auth
from ingestion.cad_dimension_contract import digest
import test_cad_assets as assets
import test_cad_reviews as reviews
import auth_fixtures


class CadDimensionServiceTests(unittest.TestCase):
    payload=assets.CadAssetTests.payload
    register=assets.CadAssetTests.register
    new_source=assets.CadAssetTests.new_source
    start=reviews.CadReviewTests.start
    review_payload=reviews.CadReviewTests.review_payload
    record=reviews.CadReviewTests.record

    def setUp(self):
        assets.CadAssetTests.setUp(self)
        self.dstage=self.root/'data/dimension_demo/development';self.dstage.mkdir(parents=True)
        for p in [patch.object(service,'ROOT',self.root),patch.object(service,'STAGING_ROOTS',
            {'synthetic_demo':self.dstage,'authorized_external':self.root/'data/dimension_demo/authorized'})]:
            p.start();self.patches.append(p)
        self.approver=TestClient(app,client=('approval-'+uuid.uuid4().hex[:10],50000));self.clients.append(self.approver)
        auth_fixtures.login(self.approver,roles=('approver',),name='隔离approver')
        self.mid='dim-test-'+uuid.uuid4().hex[:16];self.bid='binding-test-'+uuid.uuid4().hex[:16]
        self.src=json.loads((Path(__file__).resolve().parents[1]/'data/dimension_demo/development/demo-diameter.json').read_text())
        self.src['measurements'][0]['part_id']='A104'
        self.src['source_id']='source-'+self.mid;self.src['measurements'][0]['measurement_id']=self.mid
        self.mpath=self.dstage/'measurement.json';self.write_measurement()

    def tearDown(self):
        assets.CadAssetTests.tearDown(self)
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('DELETE FROM dimension_measurement_versions WHERE measurement_id=%s',(self.mid,))
            cur.execute('DELETE FROM dimension_measurements WHERE measurement_id=%s',(self.mid,))

    def write_measurement(self):self.mpath.write_text(json.dumps(self.src),encoding='utf-8')

    def mp(self,**changes):
        p={'request_id':uuid.uuid4().hex,'measurement_id':self.mid,'source_path':str(self.mpath.relative_to(self.root)),
           'source_sha256':hashlib.sha256(self.mpath.read_bytes()).hexdigest(),'provenance':'synthetic_demo',
           'previous_version':0,'previous_sha256':None};p.update(changes);return p

    def measurement(self,p=None):
        r=self.admin.post('/v1/dimensions/measurements',json=p or self.mp());self.assertEqual(r.status_code,200,r.text)
        self.m=r.json()['measurement'];return self.m

    def bp(self,**changes):
        review=service.projection(self.review)
        p={'request_id':uuid.uuid4().hex,'binding_id':self.bid,'previous_version':0,'previous_sha256':None,
           'document_id':self.id,'document_version':self.version['document_version'],'source_sha256':self.version['source_sha256'],
           'bundle_sha256':self.version['bundle_sha256'],'feature_id':self.f['feature_id'],'feature_sha256':digest(self.f),
           'target_handle':self.circle.dxf.handle,'measurement_id':self.mid,'measurement_record_version':self.m['payload']['source']['record_version'],
           'measurement_sha256':self.m['sha256'],'review_id':review['review_id'],'review_sha256':digest(review),
           'valid_from':'2026-10-05T00:00:00+08:00','valid_to':'2026-10-06T00:00:00+08:00',
           'rationale':'合成尺寸记录与此标注显式关联；工艺版号独立登记。'};p.update(changes);return p

    def bind(self,p=None):
        r=self.admin.post('/v1/dimensions/bindings',json=p or self.bp());self.assertEqual(r.status_code,200,r.text)
        self.b=r.json()['binding'];return self.b

    def setup_bound(self):
        self.start();self.review=self.record();self.measurement();self.bind()

    def ap(self,**changes):
        p={'request_id':uuid.uuid4().hex,'binding_version':self.b['payload']['binding_version'],'binding_sha256':self.b['sha256'],
           'previous_approval_id':None,'decision':'approved','reason':'独立核查合成输入，仅批准本地标注比较。'};p.update(changes);return p

    def approve(self,p=None):
        r=self.approver.post(self.au(),json=p or self.ap());self.assertEqual(r.status_code,200,r.text);return r.json()['approval']

    def au(self):return '/v1/dimensions/bindings/'+self.bid+'/approvals'
    def detail(self,version=None):return self.viewer.get('/v1/dimensions/bindings/'+self.bid+('' if not version else '?version='+str(version)))
    def compare(self,p=None):
        body=p or {'request_id':uuid.uuid4().hex,'binding_version':self.b['payload']['binding_version'],'binding_sha256':self.b['sha256']}
        r=self.admin.post('/v1/dimensions/bindings/'+self.bid+'/comparisons',json=body);self.assertEqual(r.status_code,200,r.text);return r.json()
    def blocked(self,reason):
        r=self.detail();self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['current_result']['reason'],reason)

    def test_roles_login_csrf_origin_and_server_owned_actors(self):
        with TestClient(app) as anonymous:
            for path in ['/v1/dimensions/sources','/v1/dimensions/measurements','/v1/dimensions/bindings', '/v1/dimensions/comparisons/missing']:
                self.assertEqual(anonymous.get(path).status_code,401)
        for c in [self.viewer,self.reviewer,self.approver]:
            self.assertEqual(c.get('/v1/dimensions/sources').status_code,403)
            self.assertEqual(c.post('/v1/dimensions/measurements',json=self.mp()).status_code,403)
        token=self.admin.headers.pop('X-CSRF-Token');self.assertEqual(self.admin.post('/v1/dimensions/measurements',json=self.mp()).status_code,403)
        self.admin.headers['X-CSRF-Token']=token
        self.assertEqual(self.admin.post('/v1/dimensions/measurements',json=self.mp(),headers={'Origin':'https://invalid.example'}).status_code,403)
        self.assertEqual(self.admin.post('/v1/dimensions/measurements',json=self.mp(registrar_user_id='spoof')).status_code,422)
        m=self.measurement();self.assertEqual(m['actor']['user_id'],self.admin._fixture_users[0])

    def test_fixed_source_copy_and_version_cas(self):
        p=self.mp();first=self.measurement(p);original=self.mpath.read_bytes();self.mpath.write_text('changed staging')
        replay=self.admin.post('/v1/dimensions/measurements',json=p);self.assertEqual(replay.status_code,200,replay.text)
        self.assertEqual(replay.json()['measurement'],first)
        read=self.viewer.get('/v1/dimensions/measurements/'+self.mid);self.assertEqual(read.json()['measurement'],first)
        self.src['record_version']=2;self.src['measurements'][0]['value']='20.08';self.write_measurement()
        self.assertEqual(self.admin.post('/v1/dimensions/measurements',json=self.mp()).status_code,409)
        self.measurement(self.mp(previous_version=1,previous_sha256=first['sha256']))
        self.assertEqual(self.viewer.get('/v1/dimensions/measurements/'+self.mid+'?version=1').json()['measurement'],first)
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT source_bytes FROM dimension_measurement_versions WHERE measurement_id=%s AND record_version=1',(self.mid,))
            self.assertEqual(bytes(cur.fetchone()[0]),original)

    def test_source_path_sha_schema_units_and_declared_version_guards(self):
        for changes in [dict(source_path='../outside.json'),dict(source_path=str(self.mpath)),dict(source_sha256='0'*64)]:
            self.assertIn(self.admin.post('/v1/dimensions/measurements',json=self.mp(**changes)).status_code,[409,422])
        original=copy.deepcopy(self.src)
        for change in ['multi','bad_version','duplicate','exponent','timezone']:
            self.src=copy.deepcopy(original)
            if change=='multi':self.src['measurements'].append(copy.deepcopy(self.src['measurements'][0]))
            elif change=='bad_version':self.src['record_version']=2
            elif change=='exponent':self.src['measurements'][0]['value']='2e1'
            elif change=='timezone':self.src['measurements'][0]['measured_at']='2026-10-05T08:00:00'
            self.write_measurement()
            if change=='duplicate':self.mpath.write_text(self.mpath.read_text().replace('{','{"source_id":"duplicate",',1))
            self.assertEqual(self.admin.post('/v1/dimensions/measurements',json=self.mp()).status_code,422)
        self.assertEqual(self.viewer.get('/v1/dimensions/measurements/'+self.mid).status_code,404)

    def test_pending_approved_revoked_and_byte_identical_historical_replay(self):
        self.setup_bound();self.blocked('DIMENSION_USE_APPROVAL_REQUIRED');pending=self.compare()
        self.assertFalse(pending['payload']['result']['authentication_verified']);self.assertTrue(pending['payload']['authentication']['verified'])
        event=self.approve();result=self.compare();self.assertEqual(result['payload']['result']['status'],'compared')
        self.assertEqual(result['payload']['result']['comparison']['normalized_mm']['measurement'],'20.04')
        self.assertFalse(result['payload']['result']['product_quality_conclusion'])
        cid=result['payload']['comparison_id'];path='/v1/dimensions/comparisons/'+cid
        before=self.viewer.get(path);self.assertEqual(before.status_code,200,before.text)
        self.approve(self.ap(decision='revoked',previous_approval_id=event['payload']['approval_id']))
        self.blocked('DIMENSION_USE_APPROVAL_REVOKED');self.assertEqual(self.compare()['payload']['result']['reason'],'DIMENSION_USE_APPROVAL_REVOKED')
        self.assertEqual(self.viewer.get(path).content,before.content);self.assertEqual(before.headers['cache-control'],'no-store')

    def test_registrar_and_reviewer_cannot_approve_even_as_admin(self):
        self.setup_bound()
        self.assertEqual(self.admin.post(self.au(),json=self.ap()).status_code,403)
        uid=self.reviewer._fixture_users[0]
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:cur.execute("UPDATE auth_users SET roles=ARRAY['admin'] WHERE user_id=%s",(uid,))
        r=self.reviewer.post(self.au(),json=self.ap());self.assertEqual(r.status_code,403,r.text)
        self.assertEqual(r.json()['detail']['error_code'],'DIMENSION_INDEPENDENT_APPROVER_REQUIRED')
        self.approve()

    def test_drawing_change_blocks_approval_and_new_comparison_but_allows_revocation(self):
        self.setup_bound();event=self.approve();saved=self.compare();old=self.viewer.get('/v1/dimensions/comparisons/'+saved['payload']['comparison_id']).content
        self.new_source();self.register(self.payload(previous_version=1,previous_source_sha256=self.version['source_sha256']))
        self.blocked('DIMENSION_CURRENT_HEAD_MISMATCH')
        self.assertEqual(self.approver.post(self.au(),json=self.ap(previous_approval_id=event['payload']['approval_id'])).status_code,409)
        self.approve(self.ap(decision='revoked',previous_approval_id=event['payload']['approval_id']))
        self.assertEqual(self.viewer.get('/v1/dimensions/comparisons/'+saved['payload']['comparison_id']).content,old)

    def test_measurement_and_review_changes_invalidate_without_inheriting(self):
        self.setup_bound();self.approve();self.review=self.record(self.review_payload(decision='needs_review',selected_target=None))
        self.blocked('DIMENSION_CURRENT_HEAD_MISMATCH')
        self.review=self.record();self.assertEqual(self.admin.post('/v1/dimensions/bindings',json=self.bp(previous_version=1,previous_sha256=self.b['sha256'])).status_code,200)
        self.b=self.detail().json()['binding'];self.blocked('DIMENSION_USE_APPROVAL_REQUIRED')
        self.approve();old=self.m;self.src['record_version']=2;self.src['measurements'][0]['value']='20.08';self.write_measurement()
        self.measurement(self.mp(previous_version=1,previous_sha256=old['sha256']));self.blocked('DIMENSION_CURRENT_HEAD_MISMATCH')

    def test_binding_current_version_and_event_chain_cas(self):
        self.setup_bound();old=self.b;event=self.approve();self.bind(self.bp(previous_version=1,previous_sha256=old['sha256'],rationale='再次显式登记'))
        self.blocked('DIMENSION_USE_APPROVAL_REQUIRED')
        olddetail=self.detail(1);self.assertEqual(olddetail.json()['current_result']['reason'],'DIMENSION_CURRENT_HEAD_MISMATCH')
        self.assertEqual(self.approver.post(self.au(),json=self.ap(previous_approval_id=event['payload']['approval_id'])).status_code,409)
        self.approve();self.assertEqual(self.admin.post('/v1/dimensions/bindings',json=self.bp()).status_code,409)

    def test_approval_and_comparison_retries_do_not_reactivate(self):
        self.setup_bound();p=self.ap();approved=self.approve(p)
        cp={'request_id':uuid.uuid4().hex,'binding_version':1,'binding_sha256':self.b['sha256']};saved=self.compare(cp)
        self.approve(self.ap(decision='revoked',previous_approval_id=approved['payload']['approval_id']))
        self.assertEqual(self.approve(p),approved);self.blocked('DIMENSION_USE_APPROVAL_REVOKED')
        retried=self.compare(cp);self.assertTrue(retried['replay']);self.assertEqual(retried['payload'],saved['payload'])
        self.assertEqual(self.approver.post(self.au(),json={**p,'reason':'changed'}).status_code,409)

    def test_racing_approval_writes_only_one_event(self):
        self.setup_bound();ps=[self.ap(),self.ap()]
        with ThreadPoolExecutor(max_workers=2) as pool:rs=list(pool.map(lambda p:self.approver.post(self.au(),json=p),ps))
        self.assertEqual(sorted(r.status_code for r in rs),[200,409]);self.assertEqual(len(self.detail().json()['approvals']),1)

    def test_stale_inputs_unknown_values_bad_target_and_invalid_interval_fail(self):
        self.start();self.review=self.record();self.measurement()
        for changes in [dict(source_sha256='0'*64),dict(measurement_sha256='0'*64),dict(review_sha256='0'*64),
                        dict(target_handle='FFFF'),dict(valid_to='2026-10-05T00:00:00+08:00'),dict(valid_to='2026-10-05T08:00:00+08:00')]:
            self.assertIn(self.admin.post('/v1/dimensions/bindings',json=self.bp(**changes)).status_code,[409,422])
        old=self.m;self.src['record_version']=2;self.src['measurements'][0]['unit']='unknown';self.write_measurement()
        self.measurement(self.mp(previous_version=1,previous_sha256=old['sha256']))
        r=self.admin.post('/v1/dimensions/bindings',json=self.bp());self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(r.json()['detail']['error_code'],'DIMENSION_MEASUREMENT_VALUE_OR_UNIT_UNKNOWN')

    def test_source_record_approval_and_comparison_tamper_fail_closed(self):
        self.setup_bound();event=self.approve();saved=self.compare()
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT source_bytes FROM dimension_measurement_versions WHERE measurement_id=%s',(self.mid,));original=bytes(cur.fetchone()[0])
            cur.execute('UPDATE dimension_measurement_versions SET source_bytes=%s WHERE measurement_id=%s',(b'{}',self.mid))
        self.assertEqual(self.detail().status_code,422)
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('UPDATE dimension_measurement_versions SET source_bytes=%s WHERE measurement_id=%s',(original,self.mid))
            cur.execute('UPDATE dimension_approval_events SET record_sha256=%s WHERE approval_id=%s',('0'*64,event['payload']['approval_id']))
        self.assertEqual(self.detail().status_code,409)
        cid=saved['payload']['comparison_id']
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:cur.execute('UPDATE dimension_comparisons SET record_sha256=%s WHERE comparison_id=%s',('0'*64,cid))
        self.assertEqual(self.viewer.get('/v1/dimensions/comparisons/'+cid).status_code,409)

    def test_transaction_identity_recheck_prevents_revoked_session_write(self):
        identity={'user_id':self.admin._fixture_users[0],'_session_hash':hashlib.sha256(self.admin.cookies.get('datamind_session').encode()).hexdigest()}
        self.admin.post('/v1/auth/logout')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:service.register_measurement(self.mp(),_write_dsn(),identity)
        self.assertEqual(error.exception.status_code,401)
        self.assertEqual(self.viewer.get('/v1/dimensions/measurements/'+self.mid).status_code,404)

    def test_comparison_history_pagination_preserves_pending_approved_and_revoked(self):
        self.setup_bound();pending=self.compare();event=self.approve();approved=self.compare()
        self.approve(self.ap(decision='revoked',previous_approval_id=event['payload']['approval_id']));revoked=self.compare()
        url='/v1/dimensions/bindings/'+self.bid+'/comparisons'
        page=self.viewer.get(url+'?limit=2').json();self.assertEqual(page['next_offset'],2)
        self.assertEqual([r['comparison_id'] for r in page['comparisons']],[revoked['payload']['comparison_id'],approved['payload']['comparison_id']])
        tail=self.viewer.get(url+'?limit=2&offset=2').json();self.assertIsNone(tail['next_offset'])
        self.assertEqual(tail['comparisons'][0]['comparison_id'],pending['payload']['comparison_id'])
        for query in ['?limit=51','?offset=-1','?offset=10001']:self.assertEqual(self.viewer.get(url+query).status_code,422)
        self.assertEqual(self.viewer.get('/v1/dimensions/bindings/missing/comparisons').status_code,404)

    def test_racing_measurement_and_binding_registration_use_compare_and_swap(self):
        self.start();self.review=self.record()
        with ThreadPoolExecutor(max_workers=2) as pool:rs=list(pool.map(lambda p:self.admin.post('/v1/dimensions/measurements',json=p),[self.mp(),self.mp()]))
        self.assertEqual(sorted(r.status_code for r in rs),[200,409])
        self.m=self.viewer.get('/v1/dimensions/measurements/'+self.mid).json()['measurement']
        with ThreadPoolExecutor(max_workers=2) as pool:rs=list(pool.map(lambda p:self.admin.post('/v1/dimensions/bindings',json=p),[self.bp(),self.bp()]))
        self.assertEqual(sorted(r.status_code for r in rs),[200,409])
        self.b=self.detail().json()['binding'];self.blocked('DIMENSION_USE_APPROVAL_REQUIRED')

    def test_declared_units_out_of_range_rejection_and_revoke_without_approval(self):
        self.src['measurements'][0]['value']='0.8';self.src['measurements'][0]['unit']='in';self.write_measurement()
        self.setup_bound();self.assertEqual(self.approver.post(self.au(),json=self.ap(decision='revoked')).status_code,409)
        reject=self.approve(self.ap(decision='rejected'));self.blocked('DIMENSION_USE_APPROVAL_REJECTED')
        self.approve(self.ap(previous_approval_id=reject['payload']['approval_id']))
        r=self.compare()['payload']['result'];self.assertEqual(r['comparison']['normalized_mm']['measurement'],'20.32')
        self.assertEqual(r['comparison']['status'],'outside_annotation_range')
        self.assertEqual(r['comparison']['measurement_original'],{'value':'0.8','unit':'in'})

    def test_geometry_and_original_units_cannot_be_approved_away(self):
        self.start(radius=9.975);self.review=self.record();self.measurement()
        r=self.admin.post('/v1/dimensions/bindings',json=self.bp());self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(r.json()['detail']['error_code'],'CAD_GEOMETRY_ANNOTATION_CONFLICT')
        self.assertEqual(self.viewer.get('/v1/dimensions/bindings/'+self.bid).status_code,404)


if __name__=='__main__':unittest.main()
