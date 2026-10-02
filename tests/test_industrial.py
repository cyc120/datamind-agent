"""Deterministic industrial comparisons, cardinality, access and persisted evidence."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg2
from fastapi.testclient import TestClient
from datamind.api import app
from datamind.agent import route_for
from datamind.industrial import run,parse_question,SQL
from datamind.industrial_rules import evaluate,temperature,timestamp

READ='host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev'
WRITE='host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'
QUESTION='2026年9月A102工序30哪些批次温度超限？生产记录和质检报告是否一致？'


class IndustrialRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result=run(QUESTION,dsn=READ)
        cls.facts=result['facts'];cls.rows=result['sql_rows'];cls.result=result

    def compare(self,row):
        return evaluate(row,[f for f in self.facts if f['fact_kind']=='requirement'],[f for f in self.facts if f['fact_kind']=='measurement'])

    def test_unit_offset_and_unknown_units(self):
        self.assertEqual(temperature('1123.15','K'),Decimal('850.00'))
        for v,u in [('850','sensorX'),('NaN','degC'),('inf','K')]:
            with self.assertRaises(ValueError):temperature(v,u)
        with self.assertRaises(ValueError):timestamp('2026-09-01')

    def test_scope_requires_unique_ids_and_month(self):
        self.assertEqual(parse_question(QUESTION)['part_id'],'A102')
        for q in ['A102工序30温度核验','2026年9月A102与A103工序30温度核验','2026年13月A102工序30温度核验']:
            with self.assertRaises(ValueError):parse_question(q)
        self.assertEqual(route_for(QUESTION),'industrial')
        self.assertEqual(parse_question('2026年12月A102工序30温度核验')['end'],'2027-01-01T00:00:00+08:00')

    def test_exact_comparison_grain_no_many_to_many_fanout(self):
        self.assertEqual(self.result['summary']['batch_count'],9)
        self.assertEqual(len(self.rows),10)
        self.assertEqual(len({r['measurement_id'] for r in self.rows}),10)
        self.assertEqual([r['measurement_id'] for r in self.rows if r['batch_id']=='B006'],['M006-1','M006-2'])

    def test_review_exclusion_becomes_missing_evidence_not_agreement(self):
        from datamind.field_store import read_verified_facts
        def reviewed(document, source, dsn, *, usable_only=False):
            self.assertTrue(usable_only)
            return [f for f in read_verified_facts(document,source,dsn,usable_only=usable_only)
                    if f.get('measurement_id') != 'M001-1']
        with patch('datamind.industrial.read_verified_facts', side_effect=reviewed):
            result = run(QUESTION,dsn=READ)
        row = next(r for r in result['rows'] if r['measurement_id']=='M001-1')
        self.assertIsNone(row['report_fact_id'])
        self.assertEqual(row['agreement'],'insufficient_evidence')
        self.assertIn('M001-1', result['summary']['needs_review'])

    def test_out_of_range_is_not_a_source_conflict(self):
        row=next(r for r in self.rows if r['measurement_id']=='M006-2');result=self.compare(row)
        self.assertEqual((result['compliance'],result['agreement']),('out_of_range','consistent'))
        row=next(r for r in self.rows if r['measurement_id']=='M001-1');original=copy.deepcopy(row);result=self.compare(row)
        self.assertEqual((result['compliance'],result['agreement']),('out_of_range','unresolved'))
        self.assertEqual(row,original)
        self.assertEqual(result['report']['raw_value'],'850')

    def test_approved_half_open_versions_and_overlap(self):
        r=run('2026年8月A102工序30温度核验',dsn=READ)['rows'][0]
        self.assertEqual(r['applicable_revision'],'A102-V1')
        self.assertTrue(any(i['status']=='resolved_by_rule' for i in r['issues']))
        r=next(r for r in self.result['rows'] if r['measurement_id']=='M004-1')
        self.assertEqual(r['compliance'],'needs_review');self.assertIsNone(r['applicable_revision'])

    def test_missing_time_or_different_method_cannot_be_compared(self):
        row=copy.deepcopy(next(r for r in self.rows if r['measurement_id']=='M001-1'))
        row['measured_at']='2026-09-05T11:00:00+08:00'
        self.assertEqual(self.compare(row)['agreement'],'needs_review')
        row['method']='surface';self.assertEqual(self.compare(row)['compliance'],'needs_review')

    def test_runtime_connection_has_no_source_write_privileges(self):
        with psycopg2.connect(READ) as conn, conn.cursor() as cur:
            cur.execute("SELECT has_table_privilege(current_user,'demo_industrial.process_measurements','INSERT,UPDATE,DELETE')")
            self.assertFalse(cur.fetchone()[0])
        self.assertIn('WHERE b.part_id=%s',SQL)


class IndustrialAPIIntegrationTest(unittest.TestCase):
    def setUp(self):self.client=TestClient(app);self.runs=[]
    def tearDown(self):
        with psycopg2.connect(WRITE) as conn,conn.cursor() as cur:
            for rid in self.runs:
                cur.execute('DELETE FROM evidence_edges WHERE run_id=%s',(rid,));cur.execute('DELETE FROM evidence_nodes WHERE run_id=%s',(rid,))
                cur.execute('DELETE FROM claims WHERE run_id=%s',(rid,));cur.execute('DELETE FROM runs WHERE run_id=%s',(rid,))
        self.client.close()
    def ask(self,body):
        response=self.client.post('/v1/chat',json=body);self.assertEqual(response.status_code,200)
        result=response.json();self.runs.append(result['run_id']);return result

    def test_autoroute_persistence_conflicts_and_no_model_call(self):
        with patch('datamind.agent.ask',side_effect=AssertionError('No NL2SQL call')),patch('datamind.agent.ask_documents',side_effect=AssertionError('No RAG call')):
            r=self.ask({'question':QUESTION})
        self.assertEqual(r['status'],'COMPLETED');self.assertFalse(r['industrial_result']['model_used'])
        evidence=self.client.get('/v1/runs/'+r['run_id']+'/evidence').json()
        self.assertTrue({'SQLQuery','SQLRow','Document','DocumentFact','VerificationCheck','ConflictRecord','Claim'}<={n['node_type'] for n in evidence['nodes']})
        conflicts=self.client.get('/v1/runs/'+r['run_id']+'/conflicts').json()['conflicts']
        unresolved=[c for c in conflicts if c['conflict_type']=='value_conflict']
        self.assertEqual(len(unresolved),1);self.assertEqual(unresolved[0]['resolution_status'],'unresolved')
        self.assertIsNone(unresolved[0]['selected_fact_ref'])
        replay=self.client.get('/v1/runs/'+r['run_id']+'/events').json()['events'][-1]['output_summary']
        self.assertEqual(replay['industrial_result']['summary']['conflicts'],['M001-1'])

    def test_scope_denial_and_missing_parameters(self):
        r=self.ask({'question':QUESTION,'scope':{'database_sources':[]}});self.assertEqual(r['error_code'],'SOURCE_NOT_ALLOWED')
        r=self.ask({'question':QUESTION,'scope':{'document_collections':['demo_kb']}});self.assertEqual(r['error_code'],'COLLECTION_NOT_ALLOWED')
        r=self.ask({'question':'A102工序30温度核验','mode':'industrial'});self.assertEqual(r['status'],'NEEDS_CLARIFICATION')
        r=self.ask({'question':'2027年9月A102工序30温度核验','mode':'industrial'});self.assertEqual(r['status'],'ABSTAINED')

    def test_sse_completed_and_source_locations(self):
        response=self.client.post('/v1/chat/stream',json={'question':QUESTION,'mode':'industrial'})
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.runs.append(events[0]['run_id']);self.assertEqual(events[-1]['event_type'],'answer.completed')
        result=events[-1]['output_summary']['industrial_result'];fact=result['facts'][0]
        fields=self.client.get('/v1/documents/'+fact['document_id']+'/facts')
        self.assertEqual(fields.status_code,200);self.assertEqual(fields.json()['fact_count'],17)
        self.assertEqual(self.client.get(fact['page_url']).status_code,200)
        self.assertEqual(self.client.get(fact['source_url']).status_code,200)


if __name__=='__main__':unittest.main()
