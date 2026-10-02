"""Row binding, missing labels, provenance and persisted field access boundaries."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
import uuid

import cv2
import numpy as np
import psycopg2
import pymupdf
from fastapi.testclient import TestClient
from types import SimpleNamespace

from datamind.api import app
from ingestion import facts, tables, ocr

ROOT = Path(__file__).resolve().parents[1]
WRITE_DSN = 'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'


def block():
    rows = [['产品', '抽检数', '外观瑕疵', '功能不良', '不良率'], ['产品A', '45', '3', '1', '8.9%'], ['产品B', '40', '2', '1', '7.5%']]
    cells = [[{'text': text, 'confidence': .95, 'status': 'recognized', 'bbox': [10+col*20, 10+row*20, 25+col*20, 25+row*20]}
              for col, text in enumerate(values)] for row, values in enumerate(rows)]
    return {'block_id': 'test-block', 'page_no': 1, 'text': '\n'.join(' | '.join(row) for row in rows),
            'table_cells': {'cell_details': cells, 'page_size': [200, 200], 'coordinate_system': 'pdf_points'}}


def extract(b):
    return facts.extract([b], document_id='test-doc', source_sha256='a'*64, parser_version='test-v1')


class FieldExtractionTest(unittest.TestCase):
    def test_row_binding_percent_units_and_unknowns(self):
        result, quality = extract(block())
        self.assertEqual(len(result), 8)
        rate = next(f for f in result if f['entity_name'] == '产品A' and f['metric'] == 'defect_rate')
        self.assertEqual((rate['raw_value'], rate['normalized_value'], rate['normalized_unit']), ('8.9%', '0.089', '1'))
        self.assertEqual([r['role'] for r in rate['evidence_refs']], ['entity', 'header', 'value'])
        self.assertEqual([r['row'] for r in rate['evidence_refs']], [1, 0, 1])
        self.assertTrue(all(f[k] is None for f in result for k in ('entity_id', 'batch', 'operation', 'valid_from', 'valid_to')))
        self.assertTrue(all(f['normalized_unit'] is None for f in result if f['metric'] != 'defect_rate'))
        self.assertTrue(all(c['status'] == 'passed' for c in quality['arithmetic_checks']))

    def test_missing_header_does_not_get_a_metric_from_values(self):
        b = block(); b['table_cells']['cell_details'][0][-1].update(text=None, raw_candidate='不良率', status='missing_header')
        result, quality = extract(b)
        self.assertEqual(len(result), 6)
        self.assertFalse(any(f['metric'] == 'defect_rate' for f in result))
        self.assertTrue(quality['unresolved'])
        b['table_cells']['cell_details'][0][0].update(text=None, status='missing_header')
        self.assertEqual(extract(b)[0], [])

    def test_arithmetic_mismatch_is_reported_without_correcting_source(self):
        b = block(); b['table_cells']['cell_details'][1][-1]['text'] = '9.9%'
        result, quality = extract(b)
        self.assertEqual(quality['arithmetic_checks'][0]['status'], 'mismatch')
        self.assertEqual(next(f['normalized_value'] for f in result if f['entity_name']=='产品A' and f['metric']=='defect_rate'), '0.099')

    def test_ambiguous_or_unlocated_cells_cannot_become_facts(self):
        b = block(); b['table_cells']['cell_details'][1][1]['status'] = 'ambiguous_cell'
        b['table_cells']['cell_details'][2][1]['bbox'] = None
        result, _ = extract(b)
        self.assertFalse(any(f['metric']=='sample_count' for f in result))
        with self.assertRaises(ValueError): facts.normalize_value('8.9', 'defect_rate')
        with self.assertRaises(ValueError): facts.normalize_value('120%', 'defect_rate')

    def test_dark_band_crop_recovery_preserves_low_confidence_candidates(self):
        image = np.full((650, 1000, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (100, 150), (900, 190), (100, 100, 100), -1)
        centers = [200, 450, 700]
        for x in centers: cv2.rectangle(image, (x-10, 160), (x+10, 180), (30, 30, 30), -1)
        lines = []
        for row, values in enumerate([['产品A', '45', '8.9%'], ['产品B', '40', '7.5%']]):
            for x, text in zip(centers, values):
                y = 210 + row*42
                lines.append({'box': [[x-30,y-10],[x+30,y-10],[x+30,y+10],[x-30,y+10]], 'text': text, 'confidence': .95})
        engine = lambda _: SimpleNamespace(txts=['产品', '抽检数', '不良率'], scores=[.99,.99,.5])
        blocks, detail = tables.recover(image, lines, inverse=np.array([[1.,0,0],[0,1.,0]]), page_size=[300,195], dpi=240,
                                       page_no=1, group_rows=ocr.group_rows, recognizer=engine)
        self.assertEqual(len(blocks), 1)
        grid = blocks[0]['table_cells']['cells']
        self.assertEqual(grid[0], ['产品', '抽检数', None])
        self.assertEqual(grid[1:], [['产品A','45','8.9%'],['产品B','40','7.5%']])
        self.assertEqual(detail[0]['headers'][2]['raw_candidate'], '不良率')
        self.assertEqual(blocks[0]['table_cells']['structure_status'], 'partial')


class FieldAPIIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.doc_id = 'field-test-' + uuid.uuid4().hex[:12]
        self.source = ROOT / 'data/knowledge_base/07_门店巡检记录_2025年12月_扫描版.pdf'
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        # collection+sha unique: use an ephemeral byte-identical PDF in the allowed source directory,
        # then append a PDF comment to give it a fixture-only digest.
        self.path = self.source.parent / (self.doc_id + '.pdf')
        self.path.write_bytes(self.source.read_bytes() + b'\n% ' + self.doc_id.encode())
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.b = block(); self.b['block_id'] = self.doc_id + '-block'
        with pymupdf.open(self.path) as pdf:
            self.b['table_cells']['page_size'] = [float(pdf[0].rect.width), float(pdf[0].rect.height)]
        result, _ = facts.extract([self.b], document_id=self.doc_id, source_sha256=self.sha, parser_version='field-test-v1')
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO document_assets(document_id,title,collection,file_type,origin,sha256,object_uri,
                           doc_version,acl,status,needs_review,page_count,parser_version)
                           VALUES (%s,'字段测试','demo_kb','pdf','scan',%s,%s,1,'{public}','indexed',false,1,'field-test-v1')""",
                        (self.doc_id,self.sha,str(self.path)))
            cur.execute("""INSERT INTO document_blocks(block_id,document_id,page_no,block_type,text,content_hash,parser_version)
                           VALUES (%s,%s,1,'table',%s,%s,'field-test-v1')""", (self.b['block_id'],self.doc_id,self.b['text'],hashlib.sha256(self.b['text'].encode()).hexdigest()))
            facts.persist(cur,self.doc_id,result)

    def tearDown(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM document_assets WHERE document_id=%s',(self.doc_id,))
        self.path.unlink(missing_ok=True)
        self.client.close()

    def get(self):
        return self.client.get('/v1/documents/' + self.doc_id + '/facts')

    def update(self, sql):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur: cur.execute(sql,(self.doc_id,))

    def test_persisted_locators_and_private_review_boundaries(self):
        response=self.get(); self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['fact_count'],8)
        self.assertEqual(response.json()['facts'][0]['evidence_refs'][0]['block_id'],self.b['block_id'])
        self.update("UPDATE document_assets SET acl='{private}' WHERE document_id=%s")
        self.assertEqual(self.get().status_code,404)
        self.update("UPDATE document_assets SET acl='{public}',needs_review=true WHERE document_id=%s")
        self.assertEqual(self.get().status_code,404)

    def test_stale_parser_or_version_fields_are_filtered(self):
        self.update("UPDATE document_facts SET document_version=2 WHERE document_id=%s")
        self.assertEqual(self.get().json()['fact_count'],0)
        self.update("UPDATE document_facts SET document_version=1,parser_version='old' WHERE document_id=%s")
        self.assertEqual(self.get().json()['fact_count'],0)

    def test_changed_payload_or_block_is_filtered(self):
        self.update("UPDATE document_facts SET payload=jsonb_set(payload,'{raw_value}','\"999\"'::jsonb) WHERE document_id=%s")
        self.assertEqual(self.get().json()['fact_count'],0)
        result, _ = facts.extract([self.b],document_id=self.doc_id,source_sha256=self.sha,parser_version='field-test-v1')
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur: facts.persist(cur,self.doc_id,result)
        self.update("UPDATE document_blocks SET text='changed' WHERE document_id=%s")
        self.assertEqual(self.get().json()['fact_count'],0)

    def test_source_digest_change_blocks_fields(self):
        self.path.write_bytes(self.path.read_bytes()+b'\n% changed')
        self.assertEqual(self.get().status_code,409)

    def test_wrong_page_dimensions_cannot_scale_locators(self):
        self.b['table_cells']['page_size'] = [999,999]
        result, _ = facts.extract([self.b],document_id=self.doc_id,source_sha256=self.sha,parser_version='field-test-v1')
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur: facts.persist(cur,self.doc_id,result)
        self.assertEqual(self.get().json()['fact_count'],0)
