"""Opt-in smoke: disposable DB/Qdrant, real cached BGE, authenticated retrieval.

Requires existing datamind-postgres and qdrant/qdrant:v1.12.4 Docker image. No
downloads, deployed service upgrades or writes to the original metadata DB.
Usage: python scripts/verify_pdf_search_local.py --model-cache /absolute/cache
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


class RealPrivateSearchSmoke(unittest.TestCase):
    def test_real_private_multifile_search_and_historical_selection(self):
        import httpx
        import psycopg2
        import pymupdf
        from fastapi.testclient import TestClient
        from datamind.agent import _write_dsn
        from datamind.api import app
        from datamind import pdf_index as index, pdf_publications as registry, pdf_vectors as vectors
        from ingestion.pdf_prepare import prepare_pdf
        from auth_fixtures import login, cleanup

        result = {'date': '2026-10-07', 'server_image': 'qdrant/qdrant:v1.12.4',
                  'external_model_calls': 0, 'model_downloads': 0,
                  'scope': 'isolated metadata DB, temporary authenticated Qdrant and real local BGE'}
        container = 'pdf-search-smoke-' + uuid.uuid4().hex[:12]
        key = secrets.token_urlsafe(32)
        clients = []
        documents = []
        with tempfile.TemporaryDirectory(prefix='pdf-search-real-') as temporary:
            base = Path(temporary).resolve()
            try:
                with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                    cur.execute('SELECT current_database()')
                    self.assertTrue(cur.fetchone()[0].startswith('datamind_test_'))
                subprocess.run(['docker', 'run', '--pull=never', '--detach', '--rm', '--name', container,
                                '--publish', '127.0.0.1::6333', '--env', 'QDRANT__SERVICE__API_KEY',
                                'qdrant/qdrant:v1.12.4'],
                               env=dict(os.environ, QDRANT__SERVICE__API_KEY=key),
                               check=True, capture_output=True)
                binding = json.loads(subprocess.check_output(['docker', 'inspect', '--format',
                    '{{json .NetworkSettings.Ports}}', container], text=True))['6333/tcp'][0]
                self.assertEqual(binding['HostIp'], '127.0.0.1')
                url = 'http://127.0.0.1:' + binding['HostPort']
                for _ in range(50):
                    try:
                        response = httpx.get(url + '/collections', timeout=1, trust_env=False)
                        if response.status_code in (401, 403):
                            result['unauthenticated_server_status'] = response.status_code
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.2)
                else:
                    self.fail('Temporary server did not require authentication')
                admin = TestClient(app)
                clients.append(admin)
                login(admin, ('admin',), name='Private retrieval real local smoke')
                env = {'DATAMIND_PDF_QDRANT_URL': url, 'DATAMIND_PDF_QDRANT_API_KEY': key,
                       'DATAMIND_PDF_EMBED_CACHE': str(self.model_cache)}

                def publish(document_id, version=1, previous_source=None, previous_publication=None):
                    if document_id not in documents:
                        documents.append(document_id)
                    source = base / (uuid.uuid4().hex + '.pdf')
                    with pymupdf.open() as pdf:
                        page = pdf.new_page(width=400, height=240)
                        page.insert_text((15, 30), f'Native source version {version}: temperature limit is {64 + version}.0 C.', fontsize=9)
                        page.insert_text((15, 80), f'Pipeline diameter is {31 + version}.0 mm; tolerance is 0.02 mm.', fontsize=9)
                        source.write_bytes(pdf.tobytes(garbage=4, deflate=True))
                    native = prepare_pdf(source, registry.PREPARED_STORE, {
                        'document_id': document_id, 'document_version': version,
                        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'job_id': 'search-job-' + uuid.uuid4().hex[:20], 'attempt_token': 'attempt-1'})
                    registration = admin.post('/v1/pdf-snapshots', json={
                        'request_id': 'search-register-' + uuid.uuid4().hex[:16],
                        'document_id': document_id, 'title': 'Real local private retrieval fixture',
                        'job_id': native['request']['job_id'], 'attempt_token': 'attempt-1',
                        'manifest_sha256': native['manifest_sha256'], 'previous_version': version - 1,
                        'previous_source_sha256': previous_source})
                    self.assertEqual(registration.status_code, 200, registration.text)
                    queued = admin.post('/v1/pdf-knowledge/publications', json={
                        'request_id': 'search-publication-' + uuid.uuid4().hex[:16],
                        'document_id': document_id, 'document_version': version,
                        'expected_manifest_sha256': native['manifest_sha256'],
                        'previous_publication_id': previous_publication})
                    self.assertEqual(queued.status_code, 200, queued.text)
                    pub_id = queued.json()['publication']['publication_id']
                    outcome = index.drain(_write_dsn(), pub_id, workers=1)
                    self.assertEqual(outcome['outcomes'][0]['outcome'], 'published', outcome)
                    metadata = admin.get('/v1/pdf-knowledge/publications/' + pub_id).json()['publication']
                    with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                        cur.execute('SELECT vector_plan FROM pdf_knowledge_requests WHERE publication_id=%s', (pub_id,))
                        plan = cur.fetchone()[0]
                    self.assertFalse(plan['profile']['synthetic'])
                    return metadata, native, plan

                def payload(*publications):
                    return {'question': 'What are the temperature limit and pipeline diameter?', 'limit': 4,
                            'publications': [{'publication_id': p['publication_id'],
                                              'expected_plan_sha256': p['plan_sha256']} for p in publications]}

                with patch.object(registry, 'PREPARED_STORE', base / 'prepared'), patch.dict(os.environ, env):
                    doc_a = 'pdf-real-search-' + uuid.uuid4().hex[:16]
                    doc_b = 'pdf-real-search-' + uuid.uuid4().hex[:16]
                    a1, native_a1, plan_a1 = publish(doc_a)
                    b1, _, _ = publish(doc_b)
                    initial = admin.post('/v1/pdf-knowledge/search', json=payload(a1))
                    self.assertEqual(initial.status_code, 200, initial.text)
                    multifile = admin.post('/v1/pdf-knowledge/search', json=payload(a1, b1))
                    self.assertEqual(multifile.status_code, 200, multifile.text)
                    body = multifile.json()
                    self.assertTrue(body['coverage']['all_covered'])
                    self.assertEqual({hit['document_id'] for hit in body['hits']}, {doc_a, doc_b})
                    self.assertTrue(body['vector_integrity_checked'])
                    self.assertFalse(body['answer_generated'])
                    for hit in body['hits']:
                        source = admin.get(hit['source_url'])
                        self.assertEqual(source.status_code, 200, source.text)
                        self.assertEqual(hashlib.sha256(source.content).hexdigest(), hit['source_sha256'])
                        page = admin.get(hit['page_url'])
                        self.assertEqual(page.status_code, 200, page.text)
                        self.assertTrue(page.content.startswith(b'\x89PNG\r\n\x1a\n'))
                    a2, _, _ = publish(doc_a, 2, native_a1['request']['source_sha256'], a1['publication_id'])
                    historic = admin.post('/v1/pdf-knowledge/search', json=payload(a1))
                    self.assertEqual(historic.status_code, 200, historic.text)
                    self.assertEqual(historic.content, initial.content)
                    current = admin.post('/v1/pdf-knowledge/search', json=payload(a2, b1))
                    self.assertEqual(current.status_code, 200, current.text)
                    self.assertEqual({h['document_version'] for h in current.json()['hits'] if h['document_id'] == doc_a}, {2})
                    wrong = payload(a1)
                    wrong['publications'][0]['expected_plan_sha256'] = plan_a1['plan_sha256']
                    rejected = admin.post('/v1/pdf-knowledge/search', json=wrong)
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    with patch.dict(os.environ, {'DATAMIND_PDF_EMBED_CACHE': str(base / 'missing-cache')}):
                        missing = admin.post('/v1/pdf-knowledge/search', json=payload(a1))
                    self.assertEqual(missing.status_code, 503, missing.text)
                    self.assertEqual(missing.json()['detail']['error_code'], 'PDF_INDEX_MODEL_UNAVAILABLE')
                    with patch.dict(os.environ, {'DATAMIND_PDF_QDRANT_API_KEY': secrets.token_urlsafe(32)}):
                        denied = admin.post('/v1/pdf-knowledge/search', json=payload(a1))
                    self.assertEqual(denied.status_code, 503, denied.text)
                    result.update(scene_passed=True, documents=2, publications=3,
                                  actual_local_bge_query=True, model_profile=plan_a1['profile'],
                                  multifile_coverage_checked=True, exact_source_and_page_links_checked=True,
                                  historic_response_bytes_unchanged=True, wrong_internal_plan_hash_rejected=True,
                                  missing_cache_rejected_without_download=True, wrong_qdrant_key_rejected=True,
                                  answer_generated=False, public_knowledge_visible=False,
                                  source_authority_verified=False)
            finally:
                with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                    cur.execute('DELETE FROM pdf_knowledge_heads WHERE document_id=ANY(%s)', (documents,))
                    cur.execute('DELETE FROM pdf_knowledge_requests WHERE document_id=ANY(%s)', (documents,))
                    cur.execute('DELETE FROM pdf_snapshot_versions WHERE document_id=ANY(%s)', (documents,))
                    cur.execute('DELETE FROM pdf_snapshot_assets WHERE document_id=ANY(%s)', (documents,))
                for client in clients:
                    cleanup(client)
                    client.close()
                subprocess.run(['docker', 'rm', '--force', container], capture_output=True)
        result.update(temporary_server_removed=True, owned_products_removed=True,
                      temporary_accounts_and_rows_removed=True, production_service_upgraded=False)
        self.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-cache', required=True, type=Path)
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/pdf_search_real_local_verification.json')
    args = parser.parse_args()
    RealPrivateSearchSmoke.model_cache = args.model_cache.resolve(strict=True)
    if not RealPrivateSearchSmoke.model_cache.is_dir():
        parser.error('--model-cache must be an existing local directory')
    RealPrivateSearchSmoke.report = args.report
    unittest.defaultTestLoader.discover = lambda *a, **kw: unittest.defaultTestLoader.loadTestsFromTestCase(RealPrivateSearchSmoke)
    sys.argv = ['scripts/test_isolated.py']
    runpy.run_path(str(ROOT / 'scripts/test_isolated.py'), run_name='__main__')
