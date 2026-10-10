"""Citation payloads preserve locator coordinates and page-preview identity fields."""
import unittest

from datamind import rag_engine


def sample_hit():
    return {'chunk_id': 'chunk-1', 'document_id': 'doc-1', 'title': '制度', 'collection': 'demo_kb',
            'origin': 'curated', 'document_version': 1, 'document_sha256': 'a' * 64,
            'content_hash': 'b' * 64, 'parser_version': 'pdf-ocr-0.4', 'quality_detail': {},
            'page_start': 1, 'page_end': 1, 'block_ids': ['blk-1'], 'source_blocks': [],
            'section_path': [], 'retrieval_score': 0.9, 'retrieval_scores': {}, 'file_type': 'pdf',
            'exact_text': '文本'}


def sample_locator():
    return {'locator': {'locator_kind': 'pdf_region', 'document_id': 'doc-1', 'source_version': 'v1',
                        'page_no': 1, 'page_width': 595.0, 'page_height': 842.0,
                        'bbox': [10.0, 20.0, 100.0, 120.0], 'unit': 'point',
                        'coordinate_system': 'top-left', 'rotation_degrees': 0},
            'contract_version': 'parse-contract-v1'}


class CitationLocatorContractTests(unittest.TestCase):
    def test_the_contract_emits_locator_and_page_preview_fields(self):
        citation = rag_engine.citation_from_hit(sample_hit(), locators={'blk-1': sample_locator()})
        self.assertEqual(len(citation['locators']), 1)
        locator = citation['locators'][0]['locator']
        for field in ('locator_kind', 'page_no', 'bbox', 'page_width', 'page_height', 'rotation_degrees'):
            self.assertIn(field, locator, field)
        # A page preview must be scoped to the cited document and version.
        for field in ('document_version', 'document_sha256', 'page_url', 'document_id'):
            self.assertIn(field, citation, field)


if __name__ == '__main__':
    unittest.main()
