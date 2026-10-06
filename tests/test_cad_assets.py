"""CAD registration/version history, authenticated source inspection and guards."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import ezdxf
import psycopg2
from psycopg2.extras import Json
from fastapi.testclient import TestClient
from datamind.api import app
from datamind.agent import _write_dsn
from datamind import cad_store
import auth_fixtures
from test_cad_dxf import drawing,source


class CadAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad-assets-test-');self.root=Path(self.temp.name)
        self.stage=self.root/'data/cad_demo/development';self.stage.mkdir(parents=True)
        self.storage=self.root/'data/object_store/cad_sources'
        self.patches=[patch.object(cad_store,'ROOT',self.root),patch.object(cad_store,'SOURCE_STORE',self.storage),
                      patch.object(cad_store,'STAGING_ROOTS',{'synthetic_demo':self.stage,'authorized_external':self.root/'data/cad_demo/authorized'})]
        for p in self.patches:p.start()
        self.id='cad-test-'+uuid.uuid4().hex[:20];self.ids=[self.id];self.clients=[]
        for role in ['admin','viewer','reviewer']:
            c=TestClient(app,client=('cad-'+uuid.uuid4().hex[:12],50000));auth_fixtures.login(c,roles=(role,),name='隔离'+role)
            self.clients.append(c);setattr(self,role,c)
        self.doc,self.annotation,self.circle=drawing();self.path=self.stage/'v1.dxf';self.path.write_bytes(source(self.doc))

    def tearDown(self):
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT binding_id FROM dimension_binding_versions WHERE document_id=ANY(%s)',(self.ids,))
            dimension_ids=[r[0] for r in cur.fetchall()]
            cur.execute('DELETE FROM dimension_comparisons WHERE binding_id IN (SELECT binding_id FROM dimension_binding_versions WHERE document_id=ANY(%s))',(self.ids,))
            cur.execute('DELETE FROM dimension_approval_events WHERE binding_id IN (SELECT binding_id FROM dimension_binding_versions WHERE document_id=ANY(%s))',(self.ids,))
            cur.execute('DELETE FROM dimension_binding_versions WHERE document_id=ANY(%s)',(self.ids,))
            cur.execute('DELETE FROM dimension_bindings WHERE binding_id=ANY(%s)',(dimension_ids,))
            cur.execute('DELETE FROM cad_association_reviews WHERE document_id=ANY(%s)',(self.ids,))
            cur.execute('DELETE FROM cad_versions WHERE document_id=ANY(%s)',(self.ids,))
            cur.execute('DELETE FROM cad_assets WHERE document_id=ANY(%s)',(self.ids,))
        for c in self.clients:auth_fixtures.cleanup(c);c.close()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def payload(self,**changes):
        data=dict(request_id=uuid.uuid4().hex,document_id=self.id,title='CAD 隔离测试',
                  source_path=str(self.path.relative_to(self.root)),source_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                  provenance='synthetic_demo',previous_version=0,previous_source_sha256=None)
        data.update(changes);return data

    def register(self,payload=None):
        response=self.admin.post('/v1/drawings',json=payload or self.payload())
        self.assertEqual(response.status_code,200,response.text);return response.json()['version']

    def new_source(self):
        title=next(e for e in self.doc.modelspace().query('TEXT') if e.dxf.layer=='DM_REVISION');title.dxf.text='R5'
        self.annotation.dxf.text='%%c22 +/-0.2';self.circle.dxf.radius=11
        self.path=self.stage/'v2.dxf';self.path.write_bytes(source(self.doc))

    def test_anonymous_and_roles_cannot_register_or_read_staging(self):
        with TestClient(app) as anonymous:
            for path in ['/v1/drawings','/v1/drawings/sources',f'/v1/drawings/{self.id}/viewer']:
                self.assertEqual(anonymous.get(path).status_code,401)
            self.assertEqual(anonymous.post('/v1/drawings',json=self.payload()).status_code,401)
        for c in [self.viewer,self.reviewer]:
            self.assertEqual(c.post('/v1/drawings',json=self.payload()).status_code,403)
            self.assertEqual(c.get('/v1/drawings/sources').status_code,403)
        self.assertEqual(self.admin.get('/v1/drawings/sources').json()['sources'][0]['source_path'],str(self.path.relative_to(self.root)))

    def test_csrf_origin_and_server_owned_registrar(self):
        token=self.admin.headers.pop('X-CSRF-Token');self.assertEqual(self.admin.post('/v1/drawings',json=self.payload()).status_code,403)
        self.admin.headers['X-CSRF-Token']=token
        self.assertEqual(self.admin.post('/v1/drawings',json=self.payload(),headers={'Origin':'https://untrusted.invalid'}).status_code,403)
        self.assertEqual(self.admin.post('/v1/drawings',json=self.payload(registrar_label='spoof')).status_code,422)
        v=self.register();self.assertEqual(v['registrar_label'],'隔离admin')

    def test_registered_source_is_a_fixed_copy_not_staging_file(self):
        original=self.path.read_bytes();v=self.register();self.path.write_bytes(b'changed staged file')
        url=f'/v1/drawings/{self.id}/file?version=1';response=self.viewer.get(url)
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,original)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(hashlib.sha256(response.content).hexdigest(),v['source_sha256'])

    def test_entities_features_svg_and_sandboxed_interactive_viewer(self):
        self.register();base=f'/v1/drawings/{self.id}'
        entities=self.viewer.get(base+'/entities').json();features=self.viewer.get(base+'/features').json()
        self.assertEqual(len(entities['entities']),7);self.assertEqual(features['inspection_gate']['status'],'ready')
        svg=self.viewer.get(base+'/preview');html=self.viewer.get(base+'/viewer')
        self.assertEqual(svg.status_code,200);self.assertIn('image/svg+xml',svg.headers['content-type'])
        self.assertIn('entity-'+self.circle.dxf.handle,svg.text)
        self.assertIn('sandbox allow-scripts',html.headers['content-security-policy']);self.assertNotIn('allow-same-origin',html.headers['content-security-policy'])
        self.assertIn(self.annotation.dxf.text,html.text);self.assertNotIn('object_uri',self.viewer.get(base).text)

    def test_version_history_old_source_and_preview_remain_unchanged(self):
        v1=self.register();oldfile=self.viewer.get(f'/v1/drawings/{self.id}/file?version=1').content
        oldsvg=self.viewer.get(f'/v1/drawings/{self.id}/preview?version=1').content
        self.new_source();v2=self.register(self.payload(previous_version=1,previous_source_sha256=v1['source_sha256']))
        self.assertEqual(v2['document_version'],2)
        history=self.viewer.get(f'/v1/drawings/{self.id}/versions').json()
        self.assertEqual(history['current_version'],2);self.assertEqual(len(history['versions']),2)
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/file?version=1').content,oldfile)
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/preview?version=1').content,oldsvg)
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/features').json()['features'][0]['nominal_value'],'22')

    def test_idempotent_retry_does_not_add_version_or_require_original_staging(self):
        payload=self.payload();v1=self.register(payload);self.path.unlink()
        self.assertEqual(self.register(payload),v1)
        self.assertEqual(len(self.viewer.get(f'/v1/drawings/{self.id}/versions').json()['versions']),1)
        response=self.admin.post('/v1/drawings',json={**payload,'title':'other'})
        self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail']['error_code'],'CAD_REQUEST_ID_REUSED')

    def test_stale_version_and_hash_do_not_replace_current_pointer(self):
        v=self.register();self.new_source()
        for changes in [dict(previous_version=0),dict(previous_version=1,previous_source_sha256='0'*64)]:
            response=self.admin.post('/v1/drawings',json=self.payload(**changes));self.assertEqual(response.status_code,409)
            self.assertEqual(response.json()['detail']['error_code'],'CAD_VERSION_CHANGED')
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}').json()['version']['source_sha256'],v['source_sha256'])

    def test_same_source_or_changed_business_identity_is_not_new_version(self):
        v=self.register();body=self.payload(previous_version=1,previous_source_sha256=v['source_sha256'])
        self.assertEqual(self.admin.post('/v1/drawings',json=body).json()['detail']['error_code'],'CAD_SOURCE_ALREADY_CURRENT')
        next(e for e in self.doc.modelspace().query('TEXT') if e.dxf.layer=='DM_PART_ID').dxf.text='A999'
        self.path.write_bytes(source(self.doc));body=self.payload(previous_version=1,previous_source_sha256=v['source_sha256'])
        self.assertEqual(self.admin.post('/v1/drawings',json=body).json()['detail']['error_code'],'CAD_ASSET_IDENTITY_CHANGED')

    def test_staging_hash_traversal_absolute_or_symlink_escape_are_rejected(self):
        for value in ['../secret.dxf',str(self.path.resolve()),'data/cad_demo/acceptance/test.dxf']:
            response=self.admin.post('/v1/drawings',json=self.payload(source_path=value));self.assertEqual(response.status_code,422)
        external=self.root/'outside.dxf';external.write_bytes(self.path.read_bytes());link=self.stage/'escape.dxf';link.symlink_to(external)
        response=self.admin.post('/v1/drawings',json=self.payload(source_path=str(link.relative_to(self.root))))
        self.assertEqual(response.status_code,422)
        response=self.admin.post('/v1/drawings',json=self.payload(source_sha256='0'*64));self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['error_code'],'CAD_STAGED_SOURCE_CHANGED')
        # A symlinked staging directory must not expose files outside ROOT.
        with tempfile.TemporaryDirectory(prefix='cad-outside-root-') as outside:
            external_folder=Path(outside);(external_folder/'v1.dxf').write_bytes(self.path.read_bytes())
            redirected=self.root/'data/cad_demo/authorized';redirected.symlink_to(external_folder,target_is_directory=True)
            response=self.admin.post('/v1/drawings',json=self.payload(
                source_path='data/cad_demo/authorized/v1.dxf',provenance='authorized_external'))
            self.assertEqual(response.status_code,422)
            self.assertEqual(self.admin.get('/v1/drawings/sources').json()['sources'][0]['provenance'],'synthetic_demo')

    def test_unsupported_dxf_and_bad_metadata_leave_no_asset(self):
        self.doc.modelspace().add_arc((0,0),1,0,90);self.path.write_bytes(source(self.doc))
        response=self.admin.post('/v1/drawings',json=self.payload());self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['detail']['error_code'],'CAD_ENTITY_TYPE_UNSUPPORTED')
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}').status_code,404)
        self.assertEqual(self.admin.post('/v1/drawings',json=self.payload(title=' ')).status_code,422)

    def test_source_copy_corruption_missing_or_symlinked_source_blocks_every_view(self):
        v=self.register();path=self.storage/(v['source_sha256']+'.dxf');original=path.read_bytes();path.write_bytes(b'corrupt')
        base=f'/v1/drawings/{self.id}'
        for suffix in ['', '/entities','/features','/preview','/viewer','/file','/versions']:
            r=self.viewer.get(base+suffix);self.assertEqual(r.status_code,409,r.text)
        path.unlink();self.assertEqual(self.viewer.get(base+'/file').status_code,404)
        external=self.root/'outside.dxf';external.write_bytes(original);path.symlink_to(external)
        self.assertEqual(self.viewer.get(base+'/file').status_code,404)

    def test_snapshot_digest_and_bound_identity_tampering_are_rejected(self):
        self.register()
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('SELECT bundle FROM cad_versions WHERE document_id=%s',(self.id,));bundle=cur.fetchone()[0]
            bundle['snapshot']['source']['document_version']=99
            cur.execute('UPDATE cad_versions SET bundle=%s WHERE document_id=%s',(Json(bundle),self.id))
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/entities').json()['detail']['error_code'],'CAD_SNAPSHOT_HASH_MISMATCH')
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute('UPDATE cad_versions SET bundle_sha256=%s WHERE document_id=%s',(cad_store.digest(bundle),self.id))
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/entities').json()['detail']['error_code'],'CAD_SNAPSHOT_IDENTITY_MISMATCH')

    def test_concurrent_same_request_retries_and_competing_versions(self):
        payload=self.payload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(lambda _:self.admin.post('/v1/drawings',json=payload),range(2)))
        self.assertEqual([r.status_code for r in replies],[200,200]);v=replies[0].json()['version']
        self.new_source();payloads=[self.payload(previous_version=1,previous_source_sha256=v['source_sha256']) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(lambda p:self.admin.post('/v1/drawings',json=p),payloads))
        self.assertEqual(sorted(r.status_code for r in replies),[200,409])
        self.assertEqual(len(self.viewer.get(f'/v1/drawings/{self.id}/versions').json()['versions']),2)

    def test_expired_session_changed_role_and_history_bounds(self):
        self.register();self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/features?version=0').status_code,422)
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/file?version=999').status_code,404)
        uid=self.admin._fixture_users[0]
        with psycopg2.connect(_write_dsn()) as conn,conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles=ARRAY['viewer'] WHERE user_id=%s",(uid,))
        self.assertEqual(self.admin.post('/v1/drawings',json=self.payload()).status_code,403)
        self.viewer.post('/v1/auth/logout')
        self.assertEqual(self.viewer.get(f'/v1/drawings/{self.id}/viewer').status_code,401)


if __name__=='__main__':unittest.main()
