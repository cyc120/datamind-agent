"""引用关系载荷契约：保留完整关系字段，并区分对端块与被引块。"""
import unittest

from datamind import rag_engine


# 引用关联载荷应保留的关系字段。
CITATION_RELATION_FIELDS = ('relation_type', 'relation_state', 'recovery_method',
                            'evidence', 'direction', 'peer_block_id', 'cited_block_id')
# 存储层关系行的完整形状（与 ingestion.contract_store.read_relations 返回的一致）。
STORED_RELATION_FIELDS = ('relation_id', 'document_id', 'relation_type', 'relation_state',
                          'recovery_method', 'evidence', 'direction', 'peer_block_id')


def sample_hit(block_ids=('blk-dim-1',)):
    return {'chunk_id': 'chunk-1', 'document_id': 'doc-1', 'title': '图纸说明', 'collection': 'demo_kb',
            'origin': 'curated', 'document_version': 1, 'document_sha256': 'a' * 64,
            'content_hash': 'b' * 64, 'parser_version': 'cad-0.1', 'quality_detail': {},
            'page_start': 1, 'page_end': 1, 'block_ids': list(block_ids), 'source_blocks': [],
            'section_path': [], 'retrieval_score': 0.9, 'retrieval_scores': {}, 'file_type': 'pdf',
            'exact_text': 'DIMENSION 标注 ⌀40 位于 (120,80)'}


def stored_relation(direction='source', peer_block_id='blk-circle-1'):
    """A relation row shaped exactly as storage returns it; `peer_block_id` is the other end."""
    return {'relation_id': 'rel-1', 'document_id': 'doc-1', 'relation_type': 'references',
            'relation_state': 'verified', 'recovery_method': 'native_reference',
            'evidence': 'DIMENSION 标注 ⌀40 位于 (120,80)，引用目标圆 handle=2F',
            'direction': direction, 'peer_block_id': peer_block_id}


class CitationRelationContractTests(unittest.TestCase):
    """citation_from_hit 保留存储关系字段，并标明当前引用块。"""

    def setUp(self):
        self.entry = rag_engine.citation_from_hit(
            sample_hit(), relations={'blk-dim-1': [stored_relation()]})['relations'][0]

    def test_the_contract_emits_all_citation_relation_fields(self):
        for field in CITATION_RELATION_FIELDS:
            self.assertIn(field, self.entry, field)

    def test_the_contract_emits_the_whole_stored_relation_row(self):
        for field in STORED_RELATION_FIELDS:
            self.assertIn(field, self.entry, field)
        self.assertEqual(self.entry['cited_block_id'], 'blk-dim-1')

    def test_peer_and_cited_block_are_the_opposite_ends(self):
        incoming = rag_engine.citation_from_hit(
            sample_hit(), relations={'blk-dim-1': [stored_relation(direction='target',
                                                                   peer_block_id='blk-circle-9')]})['relations'][0]
        self.assertEqual(incoming['peer_block_id'], 'blk-circle-9')
        self.assertEqual(incoming['cited_block_id'], 'blk-dim-1')
        self.assertEqual(incoming['direction'], 'target')
        self.assertNotEqual(incoming['peer_block_id'], incoming['cited_block_id'])

    def test_a_citation_without_relations_emits_none(self):
        # 无关联时，载荷必须是空列表而不是缺键或占位对象。
        citation = rag_engine.citation_from_hit(sample_hit())
        self.assertEqual(citation['relations'], [])


if __name__ == '__main__':
    unittest.main()
