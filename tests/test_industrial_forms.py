"""Explicit binding and invalid context cannot turn into usable industrial facts."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
import uuid

import psycopg2
from psycopg2.extras import Json
from fastapi.testclient import TestClient
from datamind.api import app
from ingestion.industrial_facts import extract, PARSER_VERSION
from ingestion.facts import persist, payload_hash

ROOT = Path(__file__).resolve().parents[1]
WRITE = 'host=127.0.0.1 dbname=datamind_meta user=datamind_app password=datamind_app_dev'
FIELDS = [('文档类型','质检报告'), ('零件编号','P320'), ('工序编号','15'),
          ('指标','holding_temperature'), ('版次','P320-V2'), ('审批状态','未批准'),
          ('单位','K'), ('测量方法','core'), ('批次编号','LOT-320'),
          ('测量编号','MEAS-320-7'), ('报告编号','REP-320-7'),
          ('测量时点','2026-11-01T10:35:20+08:00'), ('实测值','1097.75'), ('报告结论','原文待核验')]


def blocks(separator=':', spaced=False):
    return [dict(block_id=f'form-block-{i}', page_no=1,
                 text=(' '.join(k) if spaced else k)+separator+v,
                 bbox=[62,124+i*34,350,140+i*34], ocr_confidence=None)
            for i,(k,v) in enumerate(FIELDS)]


def parse(items):
    return extract(items, document_id='form-unit', source_sha256='a'*64, page_sizes={1:[595,842]})


class IndustrialFormExtractionTest(unittest.TestCase):
    def test_separators_label_spacing_timestamp_and_unapproved_source(self):
        for separator, spaced in [(':',False),('：',True)]:
            facts, quality = parse(blocks(separator,spaced))
            self.assertEqual(quality['errors'], [])
            fact = facts[0]
            self.assertEqual((fact['normalized_value'],fact['normalized_unit']),('824.60','degC'))
            self.assertEqual(fact['measured_at'],'2026-11-01T10:35:20+08:00')
            self.assertEqual(fact['approval_status'],'unapproved')
            self.assertEqual(fact['verification_status'],'candidate')
            for ref, block in zip(fact['evidence_refs'], [next(b for b in blocks(separator,spaced)
                if b['block_id']==r['block_id']) for r in fact['evidence_refs']]):
                self.assertEqual(ref['text'],block['text'])
                self.assertEqual(ref['bbox'],block['bbox'])
                self.assertEqual(ref['block_content_hash'],hashlib.sha256(block['text'].encode()).hexdigest())

    def test_missing_empty_duplicate_and_multi_entity_pages_are_rejected(self):
        for label in ('单位','批次编号','测量编号','工序编号'):
            items=blocks(); items=[b for b in items if not b['text'].startswith(label+':')]
            self.assertEqual(parse(items)[0],[])
        for tail in ('单位:', '零件编号:P999', '零 件 编 号：P999'):
            items=blocks(); items.append(dict(items[0],block_id='duplicate',text=tail))
            self.assertEqual(parse(items)[0],[])
        items=blocks();items[1]['text']+=' | 工序编号:15'
        self.assertEqual(parse(items)[0],[])

    def test_blank_id_and_value_whitespace_are_not_guessed(self):
        for raw in ('零件编号:P 320','工序编号:十五','批次编号:','测量编号:MEAS 320 7'):
            label=raw.split(':')[0];items=blocks()
            next(b for b in items if b['text'].startswith(label+':'))['text']=raw
            self.assertEqual(parse(items)[0],[])

    def test_page_boundary_cannot_supply_missing_context(self):
        items=blocks();items[-2]['page_no']=2
        self.assertEqual(parse(items)[0],[])

    def test_invalid_locators_do_not_become_facts(self):
        for box in ([0,0,0,10], [-1,5,10,10], [0,0,596,10], [0,float('nan'),10,20], None):
            items=blocks();items[1]['bbox']=box
            facts,quality=parse(items)
            self.assertEqual(facts,[]);self.assertEqual(quality['errors'][0]['reason'],'FIELD_LOCATOR_UNAVAILABLE')

    def test_unknown_unit_and_invalid_date_stay_needs_review(self):
        for label, value in [('单位','C'),('实测值','NaN'),('测量时点','2026-11-01')]:
            items=blocks();next(b for b in items if b['text'].startswith(label+':'))['text']=label+':'+value
            fact=parse(items)[0][0]
            self.assertEqual(fact['verification_status'],'needs_review')
            self.assertIsNone(fact['normalized_value'])
            self.assertTrue(fact['extraction_error'])

    def test_scan_origin_and_confidence_are_preserved(self):
        items=blocks()
        for b in items:b['ocr_confidence']=.91
        fact=parse(items)[0][0]
        self.assertEqual(fact['extraction_method'],'pixel_ocr_labelled_form')
        self.assertEqual(fact['ocr_confidence'],.91)

    def test_requirement_range_validity_and_measurement_mixing(self):
        items=[b for b in blocks() if b['text'].split(':')[0] not in
               {'批次编号','测量编号','报告编号','测量时点','实测值','报告结论'}]
        items[0]['text']='文档类型:工艺要求'
        for i,text in enumerate(['生效时间:2026-10-01T00:00:00+08:00','失效时间:无','温度下限:1068.15','温度上限:1118.15']):
            items.append(dict(items[0],block_id=f'range-{i}',text=text,bbox=[62,400+i*34,350,416+i*34]))
        fact=parse(items)[0][0]
        self.assertEqual(fact['normalized_range']['min'],'795.00')
        self.assertEqual(fact['normalized_range']['max'],'845.00')
        self.assertIsNone(fact['valid_to'])
        bad=copy.deepcopy(items);bad[-1]['text']='温度上限:1000'
        self.assertEqual(parse(bad)[0][0]['extraction_error'],'INVALID_TEMPERATURE_RANGE')
        bad=copy.deepcopy(items);bad[-3]['text']='失效时间:2026-09-01T00:00:00+08:00'
        self.assertEqual(parse(bad)[0][0]['extraction_error'],'INVALID_VALIDITY_RANGE')
        items.append(dict(items[0],block_id='stray',text='测量编号:OTHER-1'))
        self.assertEqual(parse(items)[0],[])


class IndustrialFormAPIIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ingestion.ingest import parse_pdf
        cls.source=ROOT/'output/pdf/industrial_forms_development_v2.pdf'
        cls.source_blocks=[b for b in parse_pdf(cls.source,'compact-integration-fixture')[0] if b['page_no']==1]

    def setUp(self):
        self.client=TestClient(app);self.doc_id='compact-test-'+uuid.uuid4().hex[:12]
        from auth_fixtures import login
        login(self.client)
        self.path=ROOT/'data/industrial_demo'/f'{self.doc_id}.pdf'
        self.path.write_bytes(self.source.read_bytes())
        self.sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.items=copy.deepcopy(self.source_blocks)
        for i,b in enumerate(self.items):b['block_id']=self.doc_id+f'-block-{i}'
        self.fact=extract(self.items,document_id=self.doc_id,source_sha256=self.sha,page_sizes={1:[595,842]})[0][0]
        with psycopg2.connect(WRITE) as conn,conn.cursor() as cur:
            cur.execute('''INSERT INTO document_assets(document_id,title,collection,file_type,origin,sha256,
                object_uri,page_count,parser_version,status) VALUES (%s,'隔离紧凑字段测试','industrial_demo',
                'pdf','curated',%s,%s,4,%s,'indexed')''',(self.doc_id,self.sha,str(self.path),PARSER_VERSION))
            for i,b in enumerate(self.items):
                cur.execute('''INSERT INTO document_blocks(block_id,document_id,page_no,bbox,block_type,text,
                    reading_order,parser_version,content_hash) VALUES (%s,%s,1,%s,'paragraph',%s,%s,%s,%s)''',
                    (b['block_id'],self.doc_id,Json(b['bbox']),b['text'],i,PARSER_VERSION,hashlib.sha256(b['text'].encode()).hexdigest()))
            persist(cur,self.doc_id,[self.fact])

    def tearDown(self):
        with psycopg2.connect(WRITE) as conn,conn.cursor() as cur:
            cur.execute('DELETE FROM document_fact_reviews WHERE document_id=%s',(self.doc_id,))
            cur.execute('DELETE FROM document_facts WHERE document_id=%s',(self.doc_id,))
            cur.execute('DELETE FROM document_blocks WHERE document_id=%s',(self.doc_id,))
            cur.execute('DELETE FROM document_assets WHERE document_id=%s',(self.doc_id,))
        from auth_fixtures import cleanup
        cleanup(self.client)
        self.client.close()
        self.path.unlink(missing_ok=True)

    def get(self):
        response=self.client.get('/v1/documents/'+self.doc_id+'/facts')
        self.assertEqual(response.status_code,200)
        return response.json()['facts']

    def test_v2_fields_locator_and_review_keep_source_approval(self):
        fact=self.get()[0];self.assertEqual(fact['parser_version'],PARSER_VERSION)
        self.assertEqual(self.client.get(f'/v1/documents/{self.doc_id}/file').status_code,200)
        response=self.client.post(f'/v1/documents/{self.doc_id}/facts/{fact["fact_id"]}/reviews',json=dict(
            request_id=uuid.uuid4().hex,document_version=1,source_sha256=self.sha,parser_version=PARSER_VERSION,
            payload_sha256=fact['payload_sha256'],previous_review_id=None,decision='confirmed',
            reviewer_label='隔离测试',reason='只核对提取字段'))
        self.assertEqual(response.status_code,200)
        updated=self.get()[0]
        self.assertEqual(updated['approval_status'],fact['approval_status'])
        self.assertEqual(updated['latest_review']['decision'],'confirmed')

    def test_tampered_field_value_bbox_and_missing_context_are_hidden(self):
        for change in ('value','bbox','missing'):
            fact=copy.deepcopy(self.fact)
            if change=='value':fact['evidence_refs'][0]['field_value']='伪造'
            if change=='bbox':fact['evidence_refs'][0]['bbox']=[1,1,20,20]
            if change=='missing':fact['evidence_refs']=[r for r in fact['evidence_refs'] if r['field']!='批次编号']
            with psycopg2.connect(WRITE) as conn,conn.cursor() as cur:
                cur.execute('UPDATE document_facts SET payload=%s,payload_sha256=%s WHERE fact_id=%s',
                            (Json(fact),payload_hash(fact),fact['fact_id']))
            self.assertEqual(self.get(),[])


if __name__=='__main__':unittest.main()
