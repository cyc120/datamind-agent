"""Frozen synthetic industrial rule checks through the actual Agent/API and PDF evidence."""
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from datamind.api import app


def main():
    suite_path=ROOT/'data/evaluation/industrial_v1.json';suite=json.loads(suite_path.read_text())
    manifest=json.loads((ROOT/'data/industrial_demo/manifest.json').read_text())
    pdf=ROOT/'data/industrial_demo'/manifest['file'];assert hashlib.sha256(pdf.read_bytes()).hexdigest()==suite['source_sha256']==manifest['sha256']
    groups={};details=[]
    with TestClient(app) as client:
        for gold in suite['cases']:
            key=(gold['part_id'],gold['operation_no'],gold['month'])
            if key not in groups:
                year,month=gold['month'].split('-');question=f'{year}年{int(month)}月{gold["part_id"]}工序{gold["operation_no"]}温度核验，生产记录和质检报告是否一致？'
                response=client.post('/v1/chat',json={'question':question,'mode':'industrial',
                    'scope':{'database_sources':['demo_industrial'],'document_collections':['industrial_demo']}});assert response.status_code==200
                groups[key]=response.json()
            run=groups[key];result=run.get('industrial_result') or {};matches=[r for r in result.get('rows',[]) if r['measurement_id']==gold['measurement_id']]
            row=matches[0] if len(matches)==1 else {};values=(row.get('compliance')==gold['expected_compliance'] and row.get('agreement')==gold['expected_agreement'] and row.get('applicable_revision')==gold['expected_revision'])
            facts={f['fact_id']:f for f in result.get('facts',[])}
            locators=True
            for fid in [row.get('requirement_fact_id'),row.get('report_fact_id')]:
                if fid:
                    f=facts[fid];locators=locators and f['source_sha256']==manifest['sha256'] and client.get(f['page_url']).status_code==200 and client.get(f['source_url']).status_code==200
            details.append({'case_id':gold['case_id'],'gold':gold,'run_id':run['run_id'],'actual':row,
                            'locators_resolved':locators,'passed':run['status']=='COMPLETED' and values and locators})
            print(gold['case_id'],details[-1]['passed'],flush=True)
        sample=groups[('A102','30','2026-09')];result=sample['industrial_result'];inv=suite['invariants']
        invariants={'batch_count':result['summary']['batch_count']==inv['september_A102_batches'],
                    'measurement_count':len(result['rows'])==inv['september_A102_measurements'],
                    'unique_measurement_grain':len({r['measurement_id'] for r in result['rows']})==len(result['rows']),
                    'conflict_set':result['summary']['conflicts']==inv['conflict_measurements'],
                    'out_of_range_set':result['summary']['out_of_range']==inv['out_of_range_measurements'],
                    'no_model_used':all(not r['industrial_result']['model_used'] for r in groups.values())}
        conflicts=client.get('/v1/runs/'+sample['run_id']+'/conflicts').json()['conflicts']
        actual_conflicts=[c for c in conflicts if c['conflict_type']=='value_conflict']
        invariants['no_automatic_authority']=len(actual_conflicts)==1 and actual_conflicts[0]['selected_fact_ref'] is None and actual_conflicts[0]['resolution_status']=='unresolved'
    report={'created_at':datetime.now(timezone.utc).isoformat(),'suite_version':suite['suite_version'],'scope':suite['scope'],
        'suite_sha256':hashlib.sha256(suite_path.read_bytes()).hexdigest(),'source_manifest':manifest,'evaluator_version':'industrial-eval-v1',
        'passed':sum(d['passed'] for d in details),'case_count':len(details),'invariants':invariants,'cases':details,
        'runs':[{'run_id':r['run_id'],'summary':r['industrial_result']['summary'],'versions':{'rules':r['industrial_result']['rule_version'],'model_used':False}} for r in groups.values()],
        'initial_implementation_failure_run':'run-49ac9fc9f84949f6 (final serialization bug; retained, not a model result)',
        'code_hashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['datamind/industrial.py','datamind/industrial_rules.py','ingestion/industrial_facts.py','datamind/field_store.py','datamind/agent.py','scripts/eval_industrial.py']}}
    path=ROOT/'reports/industrial_rules.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f'cases={report["passed"]}/{report["case_count"]}; invariants={all(invariants.values())}; {path}')
    return 0 if all(d['passed'] for d in details) and all(invariants.values()) else 1


if __name__=='__main__':raise SystemExit(main())
