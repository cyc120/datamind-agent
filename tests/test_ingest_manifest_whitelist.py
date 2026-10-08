"""The knowledge base is defined by its manifest, never by what happens to be on disk.

Regression: ingest enumerated `data/knowledge_base/*.pdf` by glob, so three PDFs written
into that directory by another tool were ingested into the served corpus (3 documents,
15 chunks, 18 facts, 15 vectors) without anyone reviewing them — and the counts were then
misread as the effect of an unrelated fix.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from ingestion import ingest

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / 'data' / 'knowledge_base'


class ManifestWhitelistTests(unittest.TestCase):
    def test_the_repository_knowledge_base_matches_its_manifest(self):
        jobs, missing, undeclared = ingest.knowledge_base_jobs()
        manifest = json.loads((KB / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(jobs), len(manifest['documents']))
        self.assertEqual(missing, [])
        # Any extra PDF in the directory must be reported, not silently ingested.
        self.assertEqual(undeclared, sorted(undeclared))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.kb = Path(self.directory.name) / 'knowledge_base'
        self.kb.mkdir()
        (self.kb / 'manifest.json').write_text(json.dumps({'documents': [
            {'file': 'declared.pdf', 'title': 'declared'},
            {'file': 'absent.pdf', 'title': 'declared but missing'},
        ]}, ensure_ascii=False), encoding='utf-8')
        (self.kb / 'declared.pdf').write_bytes(b'%PDF-1.4 declared')
        (self.kb / 'undeclared.pdf').write_bytes(b'%PDF-1.4 undeclared')

    def test_only_declared_pdfs_become_jobs(self):
        jobs, missing, undeclared = ingest.knowledge_base_jobs(self.kb)
        self.assertEqual([job['path'].name for job in jobs], ['declared.pdf'])
        self.assertEqual(missing, ['absent.pdf'])
        self.assertEqual(undeclared, ['undeclared.pdf'])

    def test_an_undeclared_file_is_never_ingested(self):
        jobs, _, undeclared = ingest.knowledge_base_jobs(self.kb)
        ingested = {job['path'].name for job in jobs}
        for name in undeclared:
            self.assertNotIn(name, ingested, f'{name} 不应被摄取')

    def test_a_declared_file_keeps_manifest_metadata(self):
        jobs, _, _ = ingest.knowledge_base_jobs(self.kb)
        self.assertEqual(jobs[0]['info']['title'], 'declared')
        self.assertEqual(jobs[0]['collection'], 'demo_kb')
        self.assertEqual(jobs[0]['origin'], 'curated')

    def test_a_scan_document_is_still_recognised_by_its_name(self):
        (self.kb / 'manifest.json').write_text(json.dumps({'documents': [
            {'file': '07_门店巡检记录_2025年12月_扫描版.pdf'}]}, ensure_ascii=False), encoding='utf-8')
        (self.kb / '07_门店巡检记录_2025年12月_扫描版.pdf').write_bytes(b'%PDF-1.4 scan')
        jobs, _, _ = ingest.knowledge_base_jobs(self.kb)
        self.assertEqual(jobs[0]['origin'], 'scan')

    def test_the_enumerator_does_not_read_the_production_directory_by_accident(self):
        # A guard on the guard: the temp directory must be what was read.
        before = set(p.name for p in KB.glob('*.pdf'))
        shutil.rmtree(self.kb)
        with self.assertRaises(FileNotFoundError):
            ingest.knowledge_base_jobs(self.kb)
        self.assertEqual(before, set(p.name for p in KB.glob('*.pdf')))


if __name__ == '__main__':
    unittest.main()
