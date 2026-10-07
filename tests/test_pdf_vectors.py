"""Synthetic embeddings and actual in-memory Qdrant; no shared DB/index writes."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from fastapi import HTTPException
import pymupdf
from qdrant_client import QdrantClient, models

from datamind import pdf_publications as registry, pdf_vectors as vectors
from ingestion import pdf_prepare as prepared


def synthetic_embed(texts):
    for text in texts:
        seed = hashlib.sha256(text.encode()).digest()
        yield [(seed[i % len(seed)] - 127) / 127 for i in range(vectors.VECTOR_DIM)]


def registered_row(snapshot):
    """A structurally valid registration fixture, without any DB connection."""
    native = snapshot['request']
    request = {'request_id': 'registered-' + uuid.uuid4().hex, 'document_id': native['document_id'],
               'title': 'Synthetic PDF vector fixture', 'job_id': native['job_id'],
               'attempt_token': native['attempt_token'], 'manifest_sha256': snapshot['manifest_sha256'],
               'previous_version': native['document_version'] - 1,
               'previous_source_sha256': None if native['document_version'] == 1 else 'a' * 64}
    row = {**request, 'document_version': native['document_version'], 'source_sha256': native['source_sha256'],
           'parser_version': snapshot['parser_version'], 'snapshot': snapshot,
           'snapshot_sha256': registry.digest(snapshot), 'request_payload': request,
           'request_sha256': registry.digest(request), 'registrar_user_id': 'synthetic-admin',
           'registrar_label': 'Synthetic fixture account', 'registrar_roles': ['admin'],
           'created_at': datetime.now(timezone.utc)}
    row['registration_sha256'] = registry.digest(registry._registration_evidence(row))
    return row


class PdfVectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-vectors-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.row, self.bundle = self.fixture()
        self.publication_id = 'publication-test-' + uuid.uuid4().hex
        self.plan = vectors.build_plan(self.row, self.bundle, self.publication_id, synthetic_embed)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)

    def fixture(self, *, document_id='pdf-vector-test', version=1, block_count=1, text=None):
        source = self.base / (uuid.uuid4().hex + '.pdf')
        with pymupdf.open() as doc:
            for start in range(0, block_count, 50):
                page = doc.new_page(width=300, height=220)
                for number in range(start, min(start + 50, block_count)):
                    page.insert_text((10, 10 + (number - start) * 3.5),
                                     text or f'Synthetic native independent source number {number:04d}: value 20.04 mm.',
                                     fontsize=1)
            source.write_bytes(doc.tobytes(garbage=4, deflate=True))
        snapshot = prepared.prepare_pdf(source, self.base / 'prepared', {
            'document_id': document_id, 'document_version': version,
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'job_id': 'job-' + uuid.uuid4().hex, 'attempt_token': 'attempt-' + uuid.uuid4().hex})
        bundle = json.loads((self.base / 'prepared' / snapshot['bundle_path']).read_bytes())
        return registered_row(snapshot), bundle

    def error(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail, {'error_code': code})
        self.assertNotIn(str(self.base), str(caught.exception.detail))
        return caught.exception

    def rehash(self, plan):
        plan['plan_sha256'] = vectors._digest({k: v for k, v in plan.items() if k != 'plan_sha256'})
        return plan

    def test_plan_binds_native_evidence_and_explicit_synthetic_profile(self):
        vectors.validate_plan(self.plan, self.row, self.bundle, self.publication_id)
        self.assertEqual(self.plan['schema'], vectors.SCHEMA)
        self.assertTrue(self.plan['profile']['synthetic'])
        self.assertEqual(self.plan['profile']['model'], vectors.SYNTHETIC_MODEL)
        self.assertNotEqual(self.plan['profile']['model'], vectors.MODEL)
        self.assertEqual(self.plan['profile']['distance'], 'Dot')
        self.assertEqual(self.plan['collection'], vectors.COLLECTION_PREFIX + self.plan['profile_sha256'])
        chunk = self.plan['chunks'][0]
        self.assertEqual({k: chunk[k] for k in self.bundle['chunks'][0]}, self.bundle['chunks'][0])
        self.assertEqual(chunk['content_sha256'], hashlib.sha256(chunk['text'].encode()).hexdigest())
        point = self.plan['points'][0]
        self.assertEqual(point['payload']['acl'], ['admin_private'])
        self.assertEqual(point['payload']['bbox_f64_hex'], [float(value).hex() for value in chunk['bbox']])
        self.assertNotIn('bbox', point['payload'])
        self.assertFalse({'lease', 'claim_token', 'status', 'current', 'published'} & set(point['payload']))
        self.assertAlmostEqual(math.hypot(*point['vector']), 1.0, places=6)

    def test_deterministic_replay_and_publication_version_source_scoped_ids(self):
        replay = vectors.build_plan(self.row, self.bundle, self.publication_id, synthetic_embed)
        self.assertEqual(replay, self.plan)
        different = vectors.build_plan(self.row, self.bundle, 'other-publication', synthetic_embed)
        self.assertNotEqual(different['points'][0]['id'], self.plan['points'][0]['id'])
        for changes in ({'version': 2}, {'document_id': 'another-document'}, {'text': 'Synthetic changed native source with value 20.08 mm.'}):
            row, bundle = self.fixture(**changes)
            changed = vectors.build_plan(row, bundle, self.publication_id, synthetic_embed)
            self.assertNotEqual(changed['points'][0]['id'], self.plan['points'][0]['id'])

    def test_valid_uuid_ids_and_deepcopy_safe_chunks(self):
        self.assertEqual(str(uuid.UUID(self.plan['points'][0]['id'])), self.plan['points'][0]['id'])
        chunks = vectors.safe_chunks(self.plan)
        self.assertNotIn('vector', chunks[0])
        chunks[0]['source_block_ids'].clear()
        self.assertTrue(self.plan['chunks'][0]['source_block_ids'])

    def test_changed_snapshot_or_bundle_never_becomes_a_plan(self):
        row = copy.deepcopy(self.row); row['source_sha256'] = '0' * 64
        self.error('PDF_INDEX_SNAPSHOT_INVALID', lambda: vectors.build_plan(row, self.bundle, self.publication_id, synthetic_embed))
        for field, value in (('text', 'invented'), ('bbox', [0.0, 0.0, 1.0, 1.0]), ('page_no', True), ('source_block_char_end', 100)):
            bundle = copy.deepcopy(self.bundle); bundle['chunks'][0][field] = value
            self.error('PDF_INDEX_SNAPSHOT_INVALID', lambda: vectors.build_plan(self.row, bundle, self.publication_id, synthetic_embed))

    def test_plan_digest_publication_and_registration_binding(self):
        plan = copy.deepcopy(self.plan); plan['points'][0]['vector'][0] += 0.1
        self.error('PDF_INDEX_PLAN_INVALID', lambda: vectors.validate_plan(plan, self.row, self.bundle, self.publication_id))
        self.error('PDF_INDEX_SNAPSHOT_CHANGED', lambda: vectors.validate_plan(self.plan, self.row, self.bundle, 'wrong-publication'))
        row, bundle = self.fixture(version=2)
        self.error('PDF_INDEX_SNAPSHOT_CHANGED', lambda: vectors.validate_plan(self.plan, row, bundle, self.publication_id))
        for field in ('source_sha256', 'manifest_sha256', 'registration_sha256'):
            plan = copy.deepcopy(self.plan); plan[field] = 'a' * 64; self.rehash(plan)
            self.error('PDF_INDEX_PLAN_INVALID',
                       lambda: vectors.validate_plan(plan, self.row, self.bundle, self.publication_id))

    def test_unknown_fields_and_legacy_collection_rejected_before_client(self):
        for changes in ({'collection': 'datamind_knowledge'}, {'status': 'published'}, {'document_version': True}):
            plan = copy.deepcopy(self.plan); plan.update(changes); self.rehash(plan)
            client = Mock()
            self.error('PDF_INDEX_PLAN_INVALID', lambda: vectors.write_and_verify(plan, client))
            client.assert_not_called(); self.assertEqual(client.method_calls, [])

    def test_forged_chunk_and_payload_rejected_even_when_plan_hash_recomputed(self):
        for mutate in (lambda p: p['chunks'][0].update(content_sha256='0' * 64),
                       lambda p: p['points'][0]['payload'].update(acl=['public']),
                       lambda p: p['points'][0]['payload'].update(claim_token='mutable'),
                       lambda p: p['points'][0].update(id=str(uuid.uuid4()))):
            plan = copy.deepcopy(self.plan); mutate(plan); self.rehash(plan)
            self.error('PDF_INDEX_PLAN_INVALID', lambda: vectors.safe_chunks(plan))

    def test_vectors_reject_bool_nan_inf_wrong_dimension_and_zero_norm(self):
        invalid = [[True] + [1.0] * 511, [float('nan')] + [1.0] * 511,
                   [float('inf')] + [1.0] * 511, [0.0] * 512, [1.0] * 511, [1.0] * 513]
        for vector in invalid:
            with self.subTest(first=repr(vector[0]), dimension=len(vector)):
                self.error('PDF_INDEX_VECTOR_INVALID', lambda: vectors.build_plan(
                    self.row, self.bundle, self.publication_id, lambda texts: [vector] * len(texts)))

    def test_embedding_count_and_failure_are_stable(self):
        for result in ([], [[1.0] * 512] * 2):
            self.error('PDF_INDEX_VECTOR_COUNT_INVALID', lambda: vectors.build_plan(
                self.row, self.bundle, self.publication_id, lambda texts: result))
        def broken(texts): raise RuntimeError(str(self.base))
        self.error('PDF_INDEX_EMBEDDING_FAILED', lambda: vectors.build_plan(self.row, self.bundle, self.publication_id, broken))

    def test_chunk_admission_happens_before_embedding(self):
        row, bundle = self.fixture(block_count=550)
        self.assertGreater(len(bundle['chunks']), 512)
        embedder = Mock(side_effect=AssertionError('must not embed'))
        self.error('PDF_INDEX_CHUNK_LIMIT', lambda: vectors.build_plan(row, bundle, self.publication_id, embedder))
        embedder.assert_not_called()

    def test_plan_size_limit(self):
        plan = copy.deepcopy(self.plan)
        plan['profile']['sdk_versions']['fastembed'] = 'x' * vectors.MAX_PLAN_BYTES
        self.rehash(plan)
        self.error('PDF_INDEX_PLAN_TOO_LARGE', lambda: vectors.safe_chunks(plan))

    def test_default_missing_model_cache_never_downloads(self):
        with patch.dict('os.environ', {'DATAMIND_PDF_EMBED_CACHE': str(self.base / 'absent')}), \
             patch('fastembed.TextEmbedding', side_effect=AssertionError('must not load or download')) as model:
            self.error('PDF_INDEX_MODEL_UNAVAILABLE', lambda: vectors.build_plan(self.row, self.bundle, self.publication_id))
        model.assert_not_called(); self.assertFalse((self.base / 'absent').exists())

    def test_local_model_loading_uses_exact_path_offline_and_actual_file_hashes(self):
        cache = self.base / 'local-model'; cache.mkdir()
        for name in vectors._MODEL_FILES: (cache / name).write_bytes(('explicit-test-model-file:' + name).encode())
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in cache.iterdir()}
        model = Mock(); model.embed.side_effect = lambda texts, **kwargs: synthetic_embed(texts)
        with patch.dict('os.environ', {'DATAMIND_PDF_EMBED_CACHE': str(cache)}), patch('fastembed.TextEmbedding', return_value=model) as loader:
            plan = vectors.build_plan(self.row, self.bundle, self.publication_id)
        kwargs = loader.call_args.kwargs
        self.assertTrue(kwargs['local_files_only']); self.assertEqual(kwargs['specific_model_path'], str(cache))
        self.assertFalse(plan['profile']['synthetic'])
        for name in vectors._MODEL_FILES:
            self.assertEqual(plan['profile']['model_files_sha256'][name], hashlib.sha256(before[name][0]).hexdigest())
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in cache.iterdir()})

    def test_in_memory_write_readback_and_idempotent_replay_do_not_close_injected_client(self):
        vectors.write_and_verify(self.plan, self.client)
        vectors.verify_plan(self.plan, self.client)
        with patch.object(self.client, 'upsert', wraps=self.client.upsert) as upsert:
            vectors.write_and_verify(self.plan, self.client)
            upsert.assert_not_called()
        actual = self.client.retrieve(self.plan['collection'], [self.plan['points'][0]['id']], with_vectors=True)[0]
        self.assertEqual(actual.payload, self.plan['points'][0]['payload'])
        self.assertEqual(vectors._vector_bytes(actual.vector), vectors._vector_bytes(self.plan['points'][0]['vector']))

    def test_max_batch_size_and_wait_and_returned_vector_flags(self):
        row, bundle = self.fixture(block_count=70)
        plan = vectors.build_plan(row, bundle, self.publication_id, synthetic_embed)
        with patch.object(self.client, 'upsert', wraps=self.client.upsert) as upsert, \
             patch.object(self.client, 'retrieve', wraps=self.client.retrieve) as retrieve:
            vectors.write_and_verify(plan, self.client)
        self.assertGreater(upsert.call_count, 1)
        for call in upsert.call_args_list:
            self.assertLessEqual(len(call.args[1]), 32); self.assertIs(call.kwargs['wait'], True)
        for call in retrieve.call_args_list:
            self.assertLessEqual(len(call.kwargs['ids']), 32)
            self.assertIs(call.kwargs['with_vectors'], True); self.assertIs(call.kwargs['with_payload'], True)

    def test_wrong_collection_configuration_is_rejected_without_upsert(self):
        for dimension, distance, datatype in ((511, models.Distance.DOT, models.Datatype.FLOAT32),
                                               (512, models.Distance.COSINE, models.Datatype.FLOAT32),
                                               (512, models.Distance.DOT, models.Datatype.FLOAT16)):
            client = QdrantClient(':memory:')
            try:
                client.create_collection(self.plan['collection'], vectors_config=models.VectorParams(size=dimension, distance=distance, datatype=datatype))
                with patch.object(client, 'upsert', wraps=client.upsert) as upsert:
                    self.error('PDF_INDEX_COLLECTION_CONFIG_MISMATCH', lambda: vectors.write_and_verify(self.plan, client))
                    upsert.assert_not_called()
            finally: client.close()

    def test_quantization_is_rejected(self):
        vectors.write_and_verify(self.plan, self.client)
        info = self.client.get_collection(self.plan['collection'])
        info.config.quantization_config = models.ScalarQuantization(scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8))
        with patch.object(self.client, 'get_collection', return_value=info):
            self.error('PDF_INDEX_COLLECTION_CONFIG_MISMATCH', lambda: vectors.verify_plan(self.plan, self.client))

    def test_missing_and_tampered_actual_vectors_and_payload_rejected(self):
        vectors.write_and_verify(self.plan, self.client)
        record = self.client.retrieve(self.plan['collection'], [self.plan['points'][0]['id']], with_vectors=True)[0]
        for altered in ([], [SimpleNamespace(id=record.id, payload=record.payload, vector=[0.0] * 512)],
                        [SimpleNamespace(id=record.id, payload={**record.payload, 'acl': ['public']}, vector=record.vector)],
                        [SimpleNamespace(id=record.id, payload=record.payload, vector=[True] + record.vector[1:])],
                        [record, record]):
            with patch.object(self.client, 'retrieve', return_value=altered):
                self.error('PDF_INDEX_VECTOR_MISSING' if not altered else 'PDF_INDEX_VECTOR_MISMATCH',
                           lambda: vectors.verify_plan(self.plan, self.client))

    def test_existing_bad_point_is_not_overwritten(self):
        vectors.write_and_verify(self.plan, self.client)
        self.client.overwrite_payload(self.plan['collection'], {'acl': ['public']}, [self.plan['points'][0]['id']])
        with patch.object(self.client, 'upsert', wraps=self.client.upsert) as upsert:
            self.error('PDF_INDEX_VECTOR_MISMATCH', lambda: vectors.write_and_verify(self.plan, self.client))
            upsert.assert_not_called()

    def test_ack_or_count_is_not_used_as_confirmation(self):
        with patch.object(self.client, 'upsert', return_value=SimpleNamespace(status='completed')):
            self.error('PDF_INDEX_VECTOR_MISSING', lambda: vectors.write_and_verify(self.plan, self.client))

    def test_network_timeout_after_commit_recovers_original_points(self):
        original = self.client.upsert
        def committed_then_timeout(*args, **kwargs):
            original(*args, **kwargs); raise TimeoutError('unknown response')
        with patch.object(self.client, 'upsert', side_effect=committed_then_timeout):
            self.error('PDF_INDEX_QDRANT_UNAVAILABLE', lambda: vectors.write_and_verify(self.plan, self.client))
        vectors.write_and_verify(self.plan, self.client); vectors.verify_plan(self.plan, self.client)

    def test_only_actual_collection_creation_race_is_recovered(self):
        vectors.write_and_verify(self.plan, self.client)
        with patch.object(self.client, 'collection_exists', return_value=False):
            vectors.write_and_verify(self.plan, self.client)
        with patch.object(self.client, 'collection_exists', return_value=False), \
             patch.object(self.client, 'create_collection', side_effect=ValueError('unrelated storage failure')):
            self.error('PDF_INDEX_QDRANT_UNAVAILABLE', lambda: vectors.write_and_verify(self.plan, self.client))

    def test_client_factory_rejects_missing_legacy_and_non_loopback_configuration(self):
        valid_key = 'synthetic-private-key'
        invalid = ['', 'http://example.com:6335', 'http://user:pass@127.0.0.1:6335',
                   'http://127.0.0.1:6335/path', 'http://127.0.0.1:6335?key=secret',
                   'http://127.0.0.1:6335#fragment', 'file:///private/path', 'http://127.0.0.1']
        for url in invalid:
            with patch.dict('os.environ', {'DATAMIND_PDF_QDRANT_URL': url, 'DATAMIND_PDF_QDRANT_API_KEY': valid_key}):
                self.error('PDF_INDEX_QDRANT_CONFIG_INVALID', vectors.client_factory)
        with patch.dict('os.environ', {'DATAMIND_PDF_QDRANT_URL': 'http://127.0.0.1:6335', 'DATAMIND_PDF_QDRANT_API_KEY': ''}):
            self.error('PDF_INDEX_QDRANT_CONFIG_INVALID', vectors.client_factory)
        probe = Mock(); probe.get.return_value.status_code = 200
        with patch.dict('os.environ', {'DATAMIND_PDF_QDRANT_URL': 'http://127.0.0.1:6333', 'DATAMIND_PDF_QDRANT_API_KEY': valid_key}), \
             patch('httpx.Client') as constructor, patch('qdrant_client.QdrantClient') as qdrant:
            constructor.return_value.__enter__.return_value = probe
            self.error('PDF_INDEX_PRIVATE_QDRANT_REQUIRED', vectors.client_factory)
            qdrant.assert_not_called()

    def test_client_factory_auth_probe_and_owned_lifetime(self):
        probe = Mock(); probe.get.return_value.status_code = 403
        sdk = Mock()
        with patch.dict('os.environ', {'DATAMIND_PDF_QDRANT_URL': 'http://localhost:6335', 'DATAMIND_PDF_QDRANT_API_KEY': 'synthetic-private-key'}), \
             patch('httpx.Client') as http, patch('qdrant_client.QdrantClient', return_value=sdk) as qdrant:
            http.return_value.__enter__.return_value = probe
            self.assertIs(vectors.client_factory(), sdk)
            self.assertFalse(http.call_args.kwargs['trust_env']); self.assertEqual(http.call_args.kwargs['timeout'], 5)
            self.assertFalse(qdrant.call_args.kwargs['trust_env']); self.assertEqual(qdrant.call_args.kwargs['timeout'], 5)
            self.assertNotIn('headers', probe.get.call_args.kwargs)
            sdk.get_collections.assert_called_once()
        with patch.object(vectors, 'client_factory', return_value=sdk):
            with vectors._client(None): pass
        sdk.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
