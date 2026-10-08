"""Retrieval-side consumption of validated locators: citations carry stored regions only."""
import hashlib
from pathlib import Path
import unittest

import psycopg2

from datamind.agent import _write_dsn
from datamind import rag_engine
from ingestion import contract_store

ROOT = Path(__file__).resolve().parents[1]
TEXT = '退款需在七个工作日内提出。'


def hit(block_ids=('blk-1', 'blk-2')):
    return {'chunk_id': 'chunk-1', 'document_id': 'doc-1', 'title': '制度', 'collection': 'demo_kb',
            'origin': 'curated', 'document_version': 1, 'document_sha256': 'a' * 64,
            'content_hash': 'b' * 64, 'parser_version': 'pdf-ocr-0.4', 'quality_detail': {},
            'page_start': 1, 'page_end': 1, 'block_ids': list(block_ids), 'source_blocks': [],
            'section_path': [], 'retrieval_score': 0.9, 'retrieval_scores': {}, 'file_type': 'pdf',
            'exact_text': TEXT}


class FakeRetriever:
    def __init__(self, hits):
        self._hits = hits
        self.requested = None

    def retrieve(self, question, **kwargs):
        return {'hits': [dict(item) for item in self._hits], 'eligible_chunks': len(self._hits),
                'coverage': {'all_covered': True, 'documents': []}, 'snapshot_id': 'snap-1',
                'retriever_version': 'test', 'mode': 'lexical', 'collections': ['demo_kb']}


def generator(question, hits, feedback):
    return {'abstain': False, 'claims': [{'text': TEXT,
                                          'citations': [{'chunk_id': hits[0]['chunk_id'], 'quote': TEXT}]}]}


LOCATOR = {'locator': {'locator_kind': 'pdf_region', 'document_id': 'doc-1', 'source_version': 'v1',
                       'page_no': 1, 'page_width': 595.0, 'page_height': 842.0,
                       'bbox': [10.0, 20.0, 100.0, 120.0], 'unit': 'point',
                       'coordinate_system': 'top-left', 'rotation_degrees': 0},
           'contract_version': 'parse-contract-v1'}


class CitationLocatorTests(unittest.TestCase):
    def test_only_stored_locators_are_attached(self):
        citation = rag_engine.citation_from_hit(hit(), locators={'blk-1': LOCATOR})
        self.assertEqual([entry['block_id'] for entry in citation['locators']], ['blk-1'])
        self.assertEqual(citation['locators'][0]['locator']['page_no'], 1)
        self.assertEqual(citation['locators'][0]['contract_version'], 'parse-contract-v1')

    def test_a_block_without_a_stored_locator_contributes_nothing(self):
        citation = rag_engine.citation_from_hit(hit(), locators={})
        self.assertEqual(citation['locators'], [])

    def test_no_lookup_means_no_locator_key_is_invented(self):
        citation = rag_engine.citation_from_hit(hit())
        self.assertEqual(citation['locators'], [])


class AskDocumentsLocatorTests(unittest.TestCase):
    def test_lookup_receives_exactly_the_cited_block_ids_and_fills_citations(self):
        seen = {}

        def lookup(block_ids):
            seen['ids'] = list(block_ids)
            return {'blk-2': LOCATOR}

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator, locator_lookup=lookup)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(seen['ids'], ['blk-1', 'blk-2'])
        evidence = result['retrieved_evidence'][0]
        self.assertEqual([entry['block_id'] for entry in evidence['locators']], ['blk-2'])
        self.assertEqual([entry['block_id'] for entry in result['citations'][0]['locators']], ['blk-2'])

    def test_without_a_lookup_citations_stay_locator_free(self):
        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]), generator=generator)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['retrieved_evidence'][0]['locators'], [])
        self.assertEqual(result['citations'][0]['locators'], [])

    def test_a_failing_lookup_is_recorded_and_never_blocks_the_answer(self):
        def broken(block_ids):
            raise RuntimeError('synthetic database outage')

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator, locator_lookup=broken)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['locator_lookup_error'], 'RuntimeError')
        self.assertEqual(result['citations'][0]['locators'], [])

    def test_a_lookup_error_code_is_reported_when_the_error_carries_one(self):
        class Coded(Exception):
            code = 'CONTRACT_LOCATOR_QUERY_TOO_LARGE'

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator,
                                          locator_lookup=lambda ids: (_ for _ in ()).throw(Coded()))
        self.assertEqual(result['locator_lookup_error'], 'CONTRACT_LOCATOR_QUERY_TOO_LARGE')


class ReadLocatorsTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.block_id = 'blk-locator-read-test'
        self.document_id = 'doc-locator-read-test'
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO document_block_locators(block_id, document_id,
                contract_version, locator) VALUES (%s, %s, %s, %s)
                ON CONFLICT (block_id) DO UPDATE SET locator=EXCLUDED.locator''',
                        (self.block_id, self.document_id, 'parse-contract-v1',
                         __import__('json').dumps(LOCATOR['locator'])))

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM document_block_locators WHERE block_id=%s', (self.block_id,))

    def test_stored_rows_are_returned_and_unknown_ids_are_omitted(self):
        found = contract_store.read_locators([self.block_id, 'blk-absent'], self.dsn)
        self.assertEqual(list(found), [self.block_id])
        self.assertEqual(found[self.block_id]['locator']['locator_kind'], 'pdf_region')
        self.assertEqual(found[self.block_id]['contract_version'], 'parse-contract-v1')

    def test_empty_input_queries_nothing_and_an_oversized_query_is_refused(self):
        self.assertEqual(contract_store.read_locators([], self.dsn), {})
        self.assertEqual(contract_store.read_locators([None, ''], self.dsn), {})
        with self.assertRaises(ValueError) as caught:
            contract_store.read_locators([f'blk-{index}' for index in range(contract_store.MAX_LOCATOR_QUERY + 1)],
                                         self.dsn)
        self.assertEqual(str(caught.exception), 'CONTRACT_LOCATOR_QUERY_TOO_LARGE')


if __name__ == '__main__':
    unittest.main()
