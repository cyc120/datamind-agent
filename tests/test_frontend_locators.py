"""The citation UI must read exactly the locator fields the contract actually emits.

There is no JavaScript test runner in this repository, so this guards the seam that can
silently drift: the field names the front end depends on versus the ones a real citation
carries. It also syntax-checks the script when Node is available.
"""
from pathlib import Path
import shutil
import subprocess
import unittest

from datamind import rag_engine

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / 'web' / 'app.js'
FIELDS_READ_BY_THE_UI = ('locator_kind', 'page_no', 'bbox', 'page_width', 'page_height',
                         'rotation_degrees', 'handle', 'sheet')


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


class FrontendLocatorTests(unittest.TestCase):
    def setUp(self):
        self.source = APP_JS.read_text(encoding='utf-8')

    def test_the_ui_reads_locators_and_draws_a_region_overlay(self):
        for needle in ('source.locators', 'citationLocators', 'citationRegionFrame',
                       'locatorLabel', 'field-page'):
            self.assertIn(needle, self.source, needle)
        for field in FIELDS_READ_BY_THE_UI:
            self.assertIn(field, self.source, field)

    def test_the_ui_refuses_to_draw_a_rotated_region(self):
        # A rotated page cannot be overlaid on the un-rotated page image this client
        # fetches, so the guard must be present rather than assumed.
        self.assertIn('rotation_degrees', self.source)
        self.assertIn('!locator.rotation_degrees', self.source)

    def test_the_contract_emits_every_field_the_ui_reads(self):
        citation = rag_engine.citation_from_hit(sample_hit(), locators={'blk-1': sample_locator()})
        self.assertEqual(len(citation['locators']), 1)
        locator = citation['locators'][0]['locator']
        for field in ('locator_kind', 'page_no', 'bbox', 'page_width', 'page_height', 'rotation_degrees'):
            self.assertIn(field, locator, field)
        # The UI uses these for the page preview query string.
        for field in ('document_version', 'document_sha256', 'page_url', 'document_id'):
            self.assertIn(field, citation, field)

    def test_the_ui_shows_non_page_locators_as_a_label_only(self):
        # cad_entity/sheet_range have no page image; they must fall through to a label.
        for kind, field in (('cad_entity', 'handle'), ('sheet_range', 'sheet')):
            self.assertIn(kind, self.source)
            self.assertIn(field, self.source)

    @unittest.skipUnless(shutil.which('node'), 'node 未安装')
    def test_app_js_is_syntactically_valid(self):
        completed = subprocess.run([shutil.which('node'), '--check', str(APP_JS)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
