"""Industrial NL2SQL grain, unit, scope and persisted contract checks."""
import unittest
from unittest.mock import patch

import psycopg2
from fastapi.testclient import TestClient
from datamind.api import app
from datamind.agent import route_for
from datamind.catalog import Catalog
from datamind.industrial_query import findings, frame_for
from datamind.sql_engine import ask
from datamind.sql_guard import guard_sql, PolicyError

META='host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev'
QUESTION='2026年9月A102工序30有多少个已测量批次？'
WHERE="part_id='A102' AND operation_no='30' AND metric='holding_temperature' AND produced_at>='2026-09-01' AND produced_at<'2026-10-01'"
SQL='SELECT count(DISTINCT batch_id) AS batch_count FROM measurement_observations WHERE '+WHERE


class IndustrialQueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.catalog=Catalog(META,'demo_industrial');cls.client=TestClient(app)

    def test_catalog_boundary_and_search_path(self):
        self.assertEqual(set(self.catalog.tables),{'parts','measurement_observations'})
        self.assertEqual(self.catalog.schemas,{'demo_industrial'})
        for sql in ('SELECT count(*) FROM app_settings','SELECT count(*) FROM process_revisions',
                    'SELECT count(*) FROM public.parts'):
            with self.assertRaises(PolicyError):guard_sql(sql,self.catalog)
        result=ask(QUESTION,'demo_industrial',generator=lambda *_:{'sql':SQL})
        self.assertEqual(result['rows'],[[9]]);self.assertFalse(result['model_used'])

    def test_scope_filters_must_be_conjunctive_and_on_production_time(self):
        frame,_=frame_for(QUESTION)
        for sql in (SQL.replace(' AND ',' OR ',1),SQL.replace("part_id='A102'","part_id='A103'"),
                    SQL.replace('produced_at','measured_at'),SQL.replace(" AND operation_no='30'",''),
                    SQL+" AND method='core'",SQL.replace("produced_at<'2026-10-01'","produced_at<='2026-10-01'")):
            self.assertTrue(findings(sql,frame),sql)

    def test_aggregations_preserve_batch_and_temperature_grain(self):
        frame,_=frame_for(QUESTION)
        self.assertEqual(findings(SQL,frame),[])
        self.assertTrue(findings(SQL.replace('count(DISTINCT batch_id)','count(*)'),frame))
        self.assertTrue(findings(SQL.replace('count(DISTINCT batch_id)','count(DISTINCT batch_id)+1'),frame))
        self.assertTrue(findings(SQL.replace('count(DISTINCT batch_id)',"count(DISTINCT batch_id) FILTER (WHERE unit='K')"),frame))
        avg,_=frame_for('2026年9月A102工序30的平均温度是多少摄氏度？')
        self.assertTrue(findings('SELECT avg(value) FROM measurement_observations WHERE '+WHERE,avg))
        self.assertTrue(findings('SELECT avg(CAST(temperature_c AS integer)) FROM measurement_observations WHERE '+WHERE,avg))
        result=ask('2026年9月A102工序30的平均温度是多少摄氏度？','demo_industrial',
                   generator=lambda *_:{'sql':'SELECT round(avg(temperature_c),2) FROM measurement_observations WHERE '+WHERE})
        self.assertEqual(str(result['rows'][0][0]),'855.56')

    def test_view_normalizes_units_without_changing_sources(self):
        with psycopg2.connect(META) as conn,conn.cursor() as cur:
            cur.execute("SELECT count(*),count(DISTINCT measurement_id) FROM demo_industrial.measurement_observations")
            self.assertEqual(cur.fetchone(),(13,13))
            cur.execute("SELECT value,unit,temperature_c FROM demo_industrial.measurement_observations WHERE measurement_id IN ('M002-1','M005-1') ORDER BY measurement_id")
            rows=cur.fetchall();self.assertEqual(str(rows[0][0]),'1123.15');self.assertEqual(str(rows[0][2]),'850.00');self.assertIsNone(rows[1][2])
            cur.execute("SELECT has_table_privilege(current_user,'demo_industrial.measurement_observations','INSERT,UPDATE,DELETE')")
            self.assertFalse(cur.fetchone()[0])

    def test_clarification_precedes_model_and_auto_routes(self):
        def unavailable(*_):raise AssertionError('must not call model')
        for q in ('A102工序30有多少条测量记录？','2026年13月A102工序30有多少条测量？',
                  '2026年9月A102工序30哪些批次温度超限？','2026年9月A102和A103工序30测量数？'):
            self.assertEqual(ask(q,'demo_industrial',generator=unavailable)['status'],'clarification_required')
        self.assertEqual(route_for(QUESTION),'sql')
        self.assertEqual(route_for('2026年9月A102工序30哪些批次温度超限？'),'industrial')
        self.assertEqual(route_for('2026年9月A102工序30的平均温度是多少？'),'sql')

    def test_api_persists_contract_model_sql_and_replay(self):
        with patch('datamind.sql_engine._call_model',return_value={'sql':SQL}):
            response=self.client.post('/v1/chat',json={'question':QUESTION,'mode':'sql','scope':{'database_sources':['demo_industrial']}})
        final=response.json();self.assertEqual(final['status'],'COMPLETED');self.assertEqual(final['sql_result']['rows'],[[9]])
        events=self.client.get('/v1/runs/'+final['run_id']+'/events').json()['events']
        result=events[-1]['output_summary']['sql_result']
        self.assertEqual(result['source_id'],'demo_industrial');self.assertEqual(result['model_sql'],SQL)
        self.assertIn('2026-09-01',result['bound_params']);self.assertIn('未知单位',result['semantic_contract'])
        claims=self.client.get('/v1/runs/'+final['run_id']+'/evidence').json()['claims']
        self.assertEqual(claims[0]['status'],'warned');self.assertTrue(claims[0]['verification']['synthetic_demo'])

    def test_wrong_model_metric_has_finite_repair_budget(self):
        result=ask(QUESTION,'demo_industrial',generator=lambda *_:{'sql':SQL.replace('count(DISTINCT batch_id)','count(*)')})
        self.assertEqual(result['error_code'],'REPAIR_BUDGET_EXHAUSTED');self.assertEqual(len(result['attempts']),3)
        self.assertEqual(result['attempts'][0]['error_code'],'INDUSTRIAL_BATCH_DISTINCT_REQUIRED')

    def test_evaluation_handles_decimal_serialization_but_requires_exact_shape(self):
        from scripts.eval_industrial_sql import match
        self.assertTrue(match([['M006-1','850','degC']],[['M006-1',850,'degC']],True))
        self.assertFalse(match([['M006-1','850','K']],[['M006-1',850,'degC']],True))
        self.assertFalse(match([[9,10]],[[9]],False))
        self.assertFalse(match([[9],[9]],[[9]],False))


if __name__=='__main__':unittest.main()
