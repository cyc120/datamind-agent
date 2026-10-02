"""Evaluate the frozen industrial development questions through real Agent/model calls."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import psycopg2
from datamind.agent import DataMindAgent,_read_dsn
from datamind.catalog import Catalog
from datamind.sql_engine import settings
from datamind.industrial_query import frame_for,findings


def canonical(value):
    if ((isinstance(value,(int,float,Decimal)) and not isinstance(value,bool)) or
        (isinstance(value,str) and re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?',value))):
        return str(Decimal(str(value)).normalize())
    return str(value)


def match(actual,gold,ordered):
    # Exact shape/value comparison: extra projected fields are not silently ignored.
    actual=[[canonical(v) for v in r] for r in actual];gold=[[canonical(v) for v in r] for r in gold]
    return actual==gold if ordered else sorted(actual)==sorted(gold)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    parser.add_argument('--rescore',type=Path,help='Recompare saved model results; makes no new model calls')
    args=parser.parse_args()
    args.output=args.output or ROOT/'reports'/('industrial_nl2sql_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    path=ROOT/'data/evaluation/industrial_nl2sql_v1.json';suite=json.loads(path.read_text())
    if args.rescore:
        if args.output.resolve()==args.rescore.resolve():raise ValueError('preserve the original report; use a different output')
        report=json.loads(args.rescore.read_text())
        assert report['suite_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
        assert [c['gold'] for c in report['cases']]==suite['cases']
        for record in report['cases']:
            gold=record['gold'];result=record.get('sql_result') or {}
            expected='COMPLETED' if gold['expected_status']=='completed' else 'NEEDS_CLARIFICATION'
            record['initial_passed']=record['passed']
            record['passed']=record['status']==expected
            if expected=='COMPLETED':record['passed']=record['passed'] and result.get('model_used') is True and match(result.get('rows',[]),gold['gold_rows'],gold['ordered'])
        report['passed']=sum(c['passed'] for c in report['cases'])
        report['model_execution']['passed']=sum(c['passed'] for c in report['cases'] if c['model_call_expected'])
        report['pre_model_clarification']['passed']=sum(c['passed'] for c in report['cases'] if not c['model_call_expected'])
        report.update(evaluator_version='industrial-sql-eval-v2',rescore={
            'created_at':datetime.now(timezone.utc).isoformat(),'source_report':str(args.rescore),
            'source_report_sha256':hashlib.sha256(args.rescore.read_bytes()).hexdigest(),
            'new_model_calls':0,'change':'Decimal numeric strings and JSON numbers compare by numeric value; exact row/column shape, units and IDs unchanged',
            'evaluator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        checks=[]
        for record in report['cases']:
            if record['model_call_expected'] and record.get('sql_result'):
                frame,_=frame_for(record['question'])
                errors=findings(record['sql_result']['model_sql'],frame)
                checks.append({'case_id':record['case_id'],'passed':not errors,'findings':errors})
        report['current_gate_recheck']={'scope':'Saved model SQL checked by current semantic gate without new execution or model calls',
            'cases':checks,'code_hashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in
            ['datamind/industrial_query.py','datamind/sql_engine.py','datamind/catalog.py','datamind/agent.py']}}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'model_execution':report['model_execution'],'pre_model_clarification':report['pre_model_clarification'],'new_model_calls':0}))
        return 0 if report['passed']==report['case_count'] else 1
    seed=ROOT/'data/industrial_demo/seed_v1.json'
    assert hashlib.sha256(seed.read_bytes()).hexdigest()==suite['seed_sha256']
    cfg=settings();catalog=Catalog(_read_dsn(),'demo_industrial')
    with psycopg2.connect(_read_dsn()) as conn,conn.cursor() as cur:
        cur.execute('SELECT measurement_id,batch_id,part_id,produced_at,operation_no,metric,value,unit,temperature_c FROM demo_industrial.measurement_observations ORDER BY measurement_id')
        rows=json.loads(json.dumps(cur.fetchall(),default=str))
    report={'created_at':datetime.now(timezone.utc).isoformat(),'suite_version':suite['suite_version'],'scope':suite['scope'],
        'suite_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'catalog_snapshot':catalog.snapshot_id,
        'production_rows_sha256':hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest(),
        'model':cfg.get('NEW_API_MODEL'),'protocol':cfg.get('NEW_API_PROTOCOL'),'profile':cfg.get('MODEL_PROFILE_NAME'),
        'prompt_version':'industrial-sql-v1','evaluator_version':'industrial-sql-eval-v2','case_count':len(suite['cases']),
        'cases':[],'code_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
        ['datamind/industrial_query.py','datamind/sql_engine.py','datamind/sql_guard.py','datamind/catalog.py','db/build_industrial_catalog.py','scripts/eval_industrial_sql.py']}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for case in suite['cases']:
        _,pre_clarification=frame_for(case['question'])
        agent=DataMindAgent()
        try:result=agent.run(case['question'],mode='sql',database_sources=['demo_industrial'])
        finally:agent.close()
        expected='COMPLETED' if case['expected_status']=='completed' else 'NEEDS_CLARIFICATION'
        sql=result.get('sql_result') or {}
        passed=result['status']==expected
        if expected=='COMPLETED':passed=passed and sql.get('model_used') is True and match(sql.get('rows',[]),case['gold_rows'],case['ordered'])
        record={'case_id':case['case_id'],'question':case['question'],'gold':case,'run_id':result['run_id'],
                'model_call_expected':not bool(pre_clarification),'status':result['status'],'error_code':result.get('error_code'),
                'answer':result['answer'],'sql_result':sql,'passed':passed}
        if not sql:
            with psycopg2.connect(_read_dsn()) as conn,conn.cursor() as cur:
                cur.execute('SELECT output_summary FROM trace_events WHERE run_id=%s ORDER BY event_id',(result['run_id'],))
                record['trace_outputs']=[r[0] for r in cur.fetchall()]
        report['cases'].append(record)
        report['passed']=sum(c['passed'] for c in report['cases'])
        executed=[c for c in report['cases'] if c['model_call_expected']]
        report['model_execution']={'passed':sum(c['passed'] for c in executed),'case_count':sum(c['expected_status']=='completed' for c in suite['cases'])}
        report['pre_model_clarification']={'passed':sum(c['passed'] for c in report['cases'] if not c['model_call_expected']),'case_count':sum(c['expected_status']!='completed' for c in suite['cases'])}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
        print(case['case_id'],result['status'],'pass='+str(passed),result['run_id'],flush=True)
    print(json.dumps({'model_execution':report['model_execution'],'pre_model_clarification':report['pre_model_clarification'],'output':str(args.output)}),flush=True)
    return 0 if report['passed']==report['case_count'] else 1


if __name__=='__main__':raise SystemExit(main())
