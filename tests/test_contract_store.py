"""Contract store: persisting validated blocks, locators and relations, and reading back."""
import json
from pathlib import Path
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient
import psycopg2

from datamind.agent import _write_dsn
from datamind.api import app
from datamind import parse_contract as contract
from ingestion import cad_adapter, contract_store

ROOT = Path(__file__).resolve().parents[1]
DXF = ROOT / 'data/cad_demo/development/dev-mm.dxf'


def dxf_payload(document_id='doc-store-check'):
    cad_adapter.register_cad_adapters()
    return cad_adapter.parse(DXF, document_id=document_id).payload


class ContractStoreTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.document_id = 'doc-contract-store-test'
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO document_assets(document_id, title, file_type, sha256)
                VALUES (%s, %s, 'dxf', %s) ON CONFLICT (document_id) DO NOTHING''',
                        (self.document_id, 'contract store test', 'c' * 64))
            cur.execute('DELETE FROM document_contract_blocks WHERE document_id=%s', (self.document_id,))
            cur.execute('DELETE FROM document_block_relations WHERE document_id=%s', (self.document_id,))

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM document_contract_blocks WHERE document_id=%s', (self.document_id,))
            cur.execute('DELETE FROM document_block_relations WHERE document_id=%s', (self.document_id,))
            cur.execute('DELETE FROM document_assets WHERE document_id=%s', (self.document_id,))

    def persist(self, payload=None):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            return contract_store.persist_result(cur, document_id=self.document_id,
                                                 payload=payload or dxf_payload(self.document_id))

    def test_blocks_locators_and_relations_round_trip(self):
        stored = self.persist()
        self.assertEqual(stored['blocks'], 8)
        self.assertEqual(stored['locators'], 8)
        self.assertEqual(stored['relations'], 1)
        self.assertEqual(stored['parse_status'], 'parsed')

        read = contract_store.read_result(self.document_id, self.dsn)
        self.assertEqual(read['block_count'], 8)
        self.assertEqual(read['relation_count'], 1)
        first = read['blocks'][0]
        self.assertEqual(first['locator']['locator_kind'], 'cad_entity')
        self.assertEqual(first['locator']['unit'], 'mm')
        self.assertEqual(first['contract_version'], contract.CONTRACT_VERSION)
        self.assertTrue(first['content_hash'])
        relation = read['relations'][0]
        self.assertEqual(relation['relation_type'], 'references')
        self.assertEqual(relation['relation_state'], 'verified')
        self.assertEqual(relation['recovery_method'], 'native_reference')
        self.assertTrue(relation['evidence'])
        self.assertNotEqual(relation['source_block_id'], relation['target_block_id'])

    def test_persisting_again_replaces_rather_than_duplicates(self):
        self.persist()
        self.persist()
        read = contract_store.read_result(self.document_id, self.dsn)
        self.assertEqual(read['block_count'], 8)
        self.assertEqual(read['relation_count'], 1)

    def test_relation_ids_are_deterministic(self):
        payload = dxf_payload(self.document_id)
        relation = payload['relations'][0]
        first = contract_store.relation_id(self.document_id, relation)
        second = contract_store.relation_id(self.document_id, relation)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('rel-'))
        self.assertNotEqual(first, contract_store.relation_id('doc-other', relation))

    def test_an_invalid_result_is_refused_before_writing(self):
        payload = dxf_payload(self.document_id)
        payload['blocks'][0]['locator']['bbox'] = [1, 2, 3, 4]  # cad_entity has no bbox
        with self.assertRaises(HTTPException) as caught:
            self.persist(payload)
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_LOCATOR_INVALID')
        self.assertEqual(contract_store.read_result(self.document_id, self.dsn)['block_count'], 0)

    def test_missing_evidence_is_refused_by_the_database_constraint(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO document_contract_blocks(block_id, document_id,
                contract_version, block_type, text, reading_order, locator, content_hash)
                VALUES ('blk-a', %s, 'parse-contract-v1', 'paragraph', 'x', 0, '{}', %s)''',
                        (self.document_id, 'd' * 64))
            with self.assertRaises(psycopg2.Error):
                cur.execute('''INSERT INTO document_block_relations(relation_id, document_id,
                    source_block_id, target_block_id, relation_type, relation_state,
                    recovery_method, evidence) VALUES ('rel-x', %s, 'blk-a', 'blk-a',
                    'references', 'verified', 'native_reference', '')''', (self.document_id,))
            conn.rollback()


class ContractEndpointTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.document_id = 'doc-contract-api-test'
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO document_assets(document_id, title, file_type, sha256,
                status, acl, needs_review, collection)
                VALUES (%s, 'contract api test', 'dxf', %s, 'indexed', '{public}', FALSE, 'demo_kb')
                ON CONFLICT (document_id) DO UPDATE SET status='indexed', acl='{public}',
                needs_review=FALSE''', (self.document_id, 'e' * 64))
            contract_store.persist_result(cur, document_id=self.document_id,
                                          payload=dxf_payload(self.document_id))
        self.client = TestClient(app)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM document_contract_blocks WHERE document_id=%s', (self.document_id,))
            cur.execute('DELETE FROM document_block_relations WHERE document_id=%s', (self.document_id,))
            cur.execute('DELETE FROM document_assets WHERE document_id=%s', (self.document_id,))

    def test_endpoint_returns_locators_and_relations(self):
        response = self.client.get(f'/v1/documents/{self.document_id}/contract')
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['block_count'], 8)
        self.assertEqual(body['relation_count'], 1)
        self.assertEqual(body['blocks'][0]['locator']['locator_kind'], 'cad_entity')
        self.assertEqual(body['relations'][0]['relation_type'], 'references')
        # The payload must be JSON-serializable as-is.
        self.assertIn('cad_entity', json.dumps(body))

    def test_endpoint_is_404_for_an_unknown_document(self):
        response = self.client.get('/v1/documents/doc-does-not-exist/contract')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['detail']['error_code'], 'DOCUMENT_NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
