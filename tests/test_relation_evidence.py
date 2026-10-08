"""Retrieval-side consumption of stored contract relations: citations carry stored rows only.

Relations are persisted only when the parse contract validated them. Like locators they are
attached to a citation from storage alone (a missing row contributes nothing), they are fetched
once per answer, and a lookup failure is recorded without blocking the answer. They do not take
part in retrieval ranking or conflict resolution; these tests only pin the traceability payload.
"""
import unittest
import uuid

import psycopg2

from datamind.agent import _write_dsn
from datamind import rag_engine
from ingestion import contract_store

TEXT = '退款需在七个工作日内提出。'
RELATION_FIELDS = ('relation_id', 'relation_type', 'relation_state', 'recovery_method',
                   'evidence', 'peer_block_id', 'direction')
LOCATOR = {'locator': {'locator_kind': 'pdf_region', 'document_id': 'doc-1', 'source_version': 'v1',
                       'page_no': 1, 'page_width': 595.0, 'page_height': 842.0,
                       'bbox': [10.0, 20.0, 100.0, 120.0], 'unit': 'point',
                       'coordinate_system': 'top-left', 'rotation_degrees': 0},
           'contract_version': 'parse-contract-v1'}


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

    def retrieve(self, question, **kwargs):
        return {'hits': [dict(item) for item in self._hits], 'eligible_chunks': len(self._hits),
                'coverage': {'all_covered': True, 'documents': []}, 'snapshot_id': 'snap-1',
                'retriever_version': 'test', 'mode': 'lexical', 'collections': ['demo_kb']}


def generator(question, hits, feedback):
    return {'abstain': False, 'claims': [{'text': TEXT,
                                          'citations': [{'chunk_id': hits[0]['chunk_id'], 'quote': TEXT}]}]}


def stored_relation(direction='source', peer_block_id='blk-2'):
    """A relation row shaped exactly as `contract_store.read_relations` returns it.

    The peer key is `peer_block_id`, deliberately unlike the locator payload where
    `block_id` means the cited block.
    """
    return {'relation_id': 'rel-stored-1', 'document_id': 'doc-1', 'relation_type': 'references',
            'relation_state': 'verified', 'recovery_method': 'native_reference',
            'evidence': '标注 ⌀40 引用目标圆 CN-7', 'direction': direction,
            'peer_block_id': peer_block_id}


class CitationRelationTests(unittest.TestCase):
    def test_a_stored_relation_is_attached_to_the_cited_block(self):
        citation = rag_engine.citation_from_hit(hit(('blk-1',)), relations={'blk-1': [stored_relation()]})
        self.assertEqual(len(citation['relations']), 1)
        entry = citation['relations'][0]
        for field in RELATION_FIELDS:
            self.assertIn(field, entry, field)
        self.assertEqual(entry['relation_id'], 'rel-stored-1')
        self.assertEqual(entry['relation_type'], 'references')
        self.assertEqual(entry['relation_state'], 'verified')
        self.assertEqual(entry['recovery_method'], 'native_reference')
        self.assertEqual(entry['evidence'], '标注 ⌀40 引用目标圆 CN-7')
        self.assertEqual(entry['cited_block_id'], 'blk-1')

    def test_the_direction_records_which_end_the_cited_block_is(self):
        outgoing = stored_relation(direction='source', peer_block_id='blk-2')
        incoming = stored_relation(direction='target', peer_block_id='blk-3')
        citation = rag_engine.citation_from_hit(hit(('blk-1',)),
                                                relations={'blk-1': [outgoing, incoming]})
        self.assertEqual([(entry['direction'], entry['peer_block_id']) for entry in citation['relations']],
                         [('source', 'blk-2'), ('target', 'blk-3')])
        self.assertEqual({entry['cited_block_id'] for entry in citation['relations']}, {'blk-1'})

    def test_a_block_without_stored_relations_gets_none_invented(self):
        citation = rag_engine.citation_from_hit(hit(('blk-1', 'blk-2')),
                                                relations={'blk-2': [stored_relation()]})
        self.assertEqual([entry['cited_block_id'] for entry in citation['relations']], ['blk-2'])

    def test_without_stored_rows_or_without_a_lookup_the_list_is_empty(self):
        self.assertEqual(rag_engine.citation_from_hit(hit(), relations={})['relations'], [])
        self.assertEqual(rag_engine.citation_from_hit(hit(), relations={'blk-absent': []})['relations'], [])
        self.assertEqual(rag_engine.citation_from_hit(hit())['relations'], [])
        citation = rag_engine.citation_from_hit(hit(('blk-9',)), relations={'blk-1': [stored_relation()]})
        self.assertEqual(citation['relations'], [])  # blk-1 is not cited by this hit

    def test_attaching_does_not_mutate_the_stored_rows(self):
        stored = stored_relation()
        rag_engine.citation_from_hit(hit(('blk-1',)), relations={'blk-1': [stored]})
        self.assertNotIn('cited_block_id', stored)


class AskDocumentsRelationTests(unittest.TestCase):
    def test_one_deduplicated_lookup_feeds_evidence_and_citations(self):
        seen = {}

        def lookup(block_ids):
            seen['ids'] = list(block_ids)
            return {'blk-2': [stored_relation()]}

        result = rag_engine.ask_documents('退款条件',
                                          retriever=FakeRetriever([hit(('blk-2', 'blk-1', 'blk-2'))]),
                                          generator=generator, relation_lookup=lookup)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(seen['ids'], ['blk-1', 'blk-2'])
        evidence = result['retrieved_evidence'][0]
        self.assertEqual([entry['cited_block_id'] for entry in evidence['relations']], ['blk-2'])
        self.assertEqual(evidence['relations'][0]['relation_id'], 'rel-stored-1')
        self.assertEqual(result['citations'][0]['relations'][0]['relation_id'], 'rel-stored-1')
        self.assertNotIn('relation_lookup_error', result)

    def test_without_a_lookup_the_answer_carries_no_relations(self):
        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]), generator=generator)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['retrieved_evidence'][0]['relations'], [])
        self.assertEqual(result['citations'][0]['relations'], [])
        self.assertNotIn('relation_lookup_error', result)

    def test_a_failing_lookup_is_recorded_and_never_blocks_the_answer(self):
        def broken(block_ids):
            raise RuntimeError('synthetic database outage')

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator, relation_lookup=broken)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['relation_lookup_error'], 'RuntimeError')
        self.assertEqual(result['retrieved_evidence'][0]['relations'], [])
        self.assertEqual(result['citations'][0]['relations'], [])

    def test_a_lookup_error_code_is_reported_when_the_error_carries_one(self):
        class Coded(Exception):
            code = 'CONTRACT_RELATION_QUERY_TOO_LARGE'

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator,
                                          relation_lookup=lambda ids: (_ for _ in ()).throw(Coded()))
        self.assertEqual(result['relation_lookup_error'], 'CONTRACT_RELATION_QUERY_TOO_LARGE')
        self.assertEqual(result['citations'][0]['relations'], [])

    def test_a_broken_relation_lookup_does_not_disturb_locators_and_vice_versa(self):
        def broken(block_ids):
            raise RuntimeError('synthetic database outage')

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator, locator_lookup=broken,
                                          relation_lookup=lambda ids: {'blk-2': [stored_relation()]})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['locator_lookup_error'], 'RuntimeError')
        self.assertEqual(result['citations'][0]['locators'], [])
        self.assertEqual(result['citations'][0]['relations'][0]['relation_id'], 'rel-stored-1')

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator,
                                          locator_lookup=lambda ids: {'blk-2': LOCATOR},
                                          relation_lookup=broken)
        self.assertEqual(result['relation_lookup_error'], 'RuntimeError')
        self.assertEqual(result['citations'][0]['relations'], [])
        self.assertEqual([entry['block_id'] for entry in result['citations'][0]['locators']], ['blk-2'])

    def test_both_lookups_are_asked_for_the_same_cited_blocks(self):
        seen = {}

        def locator_lookup(block_ids):
            seen['locators'] = list(block_ids)
            return {'blk-1': LOCATOR}

        def relation_lookup(block_ids):
            seen['relations'] = list(block_ids)
            return {}

        result = rag_engine.ask_documents('退款条件', retriever=FakeRetriever([hit()]),
                                          generator=generator, locator_lookup=locator_lookup,
                                          relation_lookup=relation_lookup)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(seen['locators'], ['blk-1', 'blk-2'])
        self.assertEqual(seen['relations'], ['blk-1', 'blk-2'])
        self.assertEqual([entry['block_id'] for entry in result['citations'][0]['locators']], ['blk-1'])
        self.assertEqual(result['citations'][0]['relations'], [])


class ReadRelationsTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.tag = uuid.uuid4().hex[:12]
        self.document_id = f'doc-rel-test-{self.tag}'
        self.source = f'blk-rel-src-{self.tag}'
        self.target = f'blk-rel-dst-{self.tag}'
        self.relation_id = f'rel-test-{self.tag}'
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO document_block_relations(relation_id, document_id,
                source_block_id, target_block_id, relation_type, relation_state, recovery_method,
                evidence) VALUES (%s,%s,%s,%s,'references','verified','native_reference',%s)''',
                        (self.relation_id, self.document_id, self.source, self.target,
                         '标注 ⌀40 引用目标圆 CN-7'))

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM document_block_relations WHERE document_id=%s', (self.document_id,))

    def test_a_query_by_source_returns_the_relation_with_its_direction(self):
        found = contract_store.read_relations([self.source], self.dsn)
        self.assertEqual(list(found), [self.source])
        entry = found[self.source][0]
        for field in RELATION_FIELDS:
            self.assertIn(field, entry, field)
        self.assertEqual(entry['relation_id'], self.relation_id)
        self.assertEqual(entry['document_id'], self.document_id)
        self.assertEqual(entry['direction'], 'source')
        self.assertEqual(entry['peer_block_id'], self.target)
        self.assertEqual(entry['relation_state'], 'verified')
        self.assertEqual(entry['recovery_method'], 'native_reference')
        self.assertEqual(entry['evidence'], '标注 ⌀40 引用目标圆 CN-7')

    def test_a_query_by_target_returns_the_same_row_the_other_way_round(self):
        found = contract_store.read_relations([self.target], self.dsn)
        self.assertEqual(list(found), [self.target])
        entry = found[self.target][0]
        self.assertEqual(entry['relation_id'], self.relation_id)
        self.assertEqual(entry['direction'], 'target')
        self.assertEqual(entry['peer_block_id'], self.source)

    def test_both_ends_cited_at_once_are_grouped_with_opposite_directions(self):
        found = contract_store.read_relations([self.target, self.source, self.source], self.dsn)
        self.assertEqual(sorted(found), sorted([self.source, self.target]))
        self.assertEqual([(entry['direction'], entry['peer_block_id']) for entry in found[self.source]],
                         [('source', self.target)])
        self.assertEqual([(entry['direction'], entry['peer_block_id']) for entry in found[self.target]],
                         [('target', self.source)])

    def test_unknown_ids_are_omitted_and_nothing_is_fabricated(self):
        found = contract_store.read_relations([self.source, f'blk-absent-{self.tag}'], self.dsn)
        self.assertEqual(list(found), [self.source])
        self.assertEqual(contract_store.read_relations([f'blk-absent-{self.tag}'], self.dsn), {})

    def test_empty_input_queries_nothing(self):
        # A bogus DSN would raise if the function tried to connect, so this also proves the
        # empty case never opens a connection.
        self.assertEqual(contract_store.read_relations([], 'not-a-valid-dsn'), {})
        self.assertEqual(contract_store.read_relations([None, ''], 'not-a-valid-dsn'), {})

    def test_an_oversized_query_is_refused(self):
        oversized = [f'blk-{index}' for index in range(contract_store.MAX_RELATION_QUERY + 1)]
        with self.assertRaises(ValueError) as caught:
            contract_store.read_relations(oversized, self.dsn)
        self.assertEqual(str(caught.exception), 'CONTRACT_RELATION_QUERY_TOO_LARGE')
        self.assertEqual(contract_store.MAX_RELATION_QUERY, contract_store.MAX_LOCATOR_QUERY)


if __name__ == '__main__':
    unittest.main()
