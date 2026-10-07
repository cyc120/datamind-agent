"""Bounded raw staging guards; no metadata migration on original database."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from datamind.api import app
from datamind import file_uploads
import auth_fixtures


class FileUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch.object(file_uploads,'ROOT',self.root);self.patch.start()
        self.admin=TestClient(app,client=('upload-admin-'+self.root.name,50000));self.viewer=TestClient(app)
        auth_fixtures.login(self.admin,roles=('admin',));auth_fixtures.login(self.viewer,roles=('viewer',))
    def tearDown(self):
        for c in [self.admin,self.viewer]:auth_fixtures.cleanup(c);c.close()
        self.patch.stop();self.temp.cleanup()
    def upload(self,data=b'synthetic exact bytes',**changes):
        p={'filename':'demo.dxf','provenance':'synthetic_demo'};p.update(changes)
        return self.admin.post('/v1/files/uploads',params=p,content=data,headers={'Content-Type':'application/octet-stream'})
    def test_auth_csrf_role_filename_and_size_limits(self):
        with TestClient(app) as c:self.assertEqual(c.post('/v1/files/uploads',params={'filename':'x.dxf','provenance':'synthetic_demo'},content=b'x').status_code,401)
        self.assertEqual(self.viewer.post('/v1/files/uploads',params={'filename':'x.dxf','provenance':'synthetic_demo'},content=b'x').status_code,403)
        token=self.admin.headers.pop('X-CSRF-Token');self.assertEqual(self.upload().status_code,403);self.admin.headers['X-CSRF-Token']=token
        for name in ['../x.dxf','x.pdf','/tmp/x.json','x\\x.dxf']:
            self.assertEqual(self.upload(filename=name).status_code,422)
        self.assertEqual(self.upload(b'').status_code,413)
        self.assertEqual(self.upload(b'x'*(1024*1024+1),filename='demo.json').status_code,413)
        self.assertEqual(self.upload(b'x'*(2*1024*1024+1)).status_code,413)
    def test_content_addressed_bytes_and_no_registration_side_effect(self):
        body=b'only raw source fixture';first=self.upload(body);self.assertEqual(first.status_code,200,first.text)
        source=first.json()['source'];self.assertEqual(source['source_sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual((self.root/source['source_path']).read_bytes(),body)
        repeat=self.upload(body,filename='other.dxf');self.assertEqual(repeat.json()['source']['source_path'],source['source_path'])
        self.assertEqual(first.headers['cache-control'],'no-store')
    def test_outside_staging_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            directory=self.root/'data/cad_demo';directory.mkdir(parents=True)
            (directory/'authorized').symlink_to(outside,target_is_directory=True)
            self.assertEqual(self.upload(provenance='authorized_external').status_code,409)
            self.assertEqual(list(Path(outside).iterdir()),[])


if __name__=='__main__':unittest.main()
