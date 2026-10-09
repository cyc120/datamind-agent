"""Bounded native-document -> readonly SQL -> per-measurement rule comparison tool."""
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import time

import psycopg2
from psycopg2.extras import RealDictCursor
from .field_store import read_verified_facts
from .industrial_rules import RULE_VERSION, evaluate
from .authority import load as load_authority, basis_valid, resolve as resolve_authority
from .visibility import published_document_sql

ROOT=Path(__file__).resolve().parents[1]
SQL='''SELECT b.batch_id,b.part_id,b.produced_at,b.recorded_revision,m.measurement_id,m.operation_no,
              m.metric,m.value,m.unit,m.measured_at,m.method,q.quality_id,q.report_id,q.method AS quality_method
       FROM demo_industrial.production_batches b JOIN demo_industrial.process_measurements m USING(batch_id)
       LEFT JOIN demo_industrial.quality_records q USING(measurement_id)
       WHERE b.part_id=%s AND m.operation_no=%s AND b.produced_at>=%s AND b.produced_at<%s
         AND m.metric='holding_temperature' ORDER BY b.batch_id,m.measurement_id LIMIT 1001'''


def plain(value):
    return json.loads(json.dumps(value,default=lambda v:str(v) if isinstance(v,Decimal) else v.isoformat()))


def parse_question(question):
    parts=set(re.findall(r'(?<![A-Z0-9])A\d{3}(?![A-Z0-9])',question.upper()))
    operations=set(re.findall(r'工序\s*(\d+)',question))
    dates=set(re.findall(r'(\d{4})\s*年\s*(\d{1,2})\s*月',question))
    if len(parts)!=1 or len(operations)!=1 or len(dates)!=1 or not any(w in question for w in ('温度','temperature')):
        raise ValueError('INDUSTRIAL_SCOPE_REQUIRED')
    year,month=map(int,next(iter(dates)))
    start=datetime(year,month,1).isoformat()+'+08:00'
    end=datetime(year+1,1,1).isoformat()+'+08:00' if month==12 else datetime(year,month+1,1).isoformat()+'+08:00'
    return {'part_id':next(iter(parts)),'operation_no':next(iter(operations)),'start':start,'end':end,
            'month':f'{year:04}-{month:02}','metric':'holding_temperature'}


def run(question, *, dsn, on_step=None, apply_authority=True):
    started=time.perf_counter();params=parse_question(question);step=on_step or (lambda *_:None)
    # Evidence and production data are scoped to the synthetic industrial source only.
    with psycopg2.connect(dsn) as conn:
        conn.set_session(readonly=True,isolation_level='REPEATABLE READ')
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout='5s'")
            cur.execute("SELECT dataset_version,seed_sha256,pdf_sha256,provenance FROM demo_industrial.dataset_manifest WHERE dataset_version='industrial-demo-v1'")
            manifest=cur.fetchone()
            if not manifest: raise ValueError('INDUSTRIAL_DATA_NOT_READY')
            # The publish gate itself, restricted to the synthetic industrial collection by the
            # caller. It used to be spelled out here as a fourth copy of the condition; the
            # policy layer now owns that spelling (see docs/DEPLOYMENT_BOUNDARIES.md §8).
            gate = published_document_sql('document_assets', ('industrial_demo',))
            cur.execute(f"SELECT * FROM document_assets WHERE {gate.text} ORDER BY document_id",
                        gate.params)
            docs=[dict(d) for d in cur.fetchall()]
            facts=[]
            for doc in docs:
                path=Path(doc.get('object_uri') or '').resolve()
                if not path.is_relative_to(ROOT/'data/industrial_demo') or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=doc['sha256']:
                    raise ValueError('SOURCE_VERSION_MISMATCH')
                for fact in read_verified_facts(doc,path,dsn,usable_only=True):
                    if fact['metric']=='holding_temperature':
                        facts.append({**fact,'title':doc['title'],'collection':doc['collection'],
                                      'source_url':f'/v1/documents/{doc["document_id"]}/file',
                                      'page_url':f'/v1/documents/{doc["document_id"]}/pages/{fact["evidence_refs"][0]["page_no"]}'})
            requirements=[f for f in facts if f['fact_kind']=='requirement']
            reports=[f for f in facts if f['fact_kind']=='measurement']
            step('requirements.loaded',{'fact_count':len(requirements),'source_sha256':manifest['pdf_sha256'],'scope':params})
            cur.execute(SQL,(params['part_id'],params['operation_no'],params['start'],params['end']))
            raw_rows=plain([dict(r) for r in cur.fetchall()])
            authority_rules=load_authority(cur) if apply_authority else []
            for rule in authority_rules:
                rule['basis_valid']=rule['payload_valid'] and basis_valid(cur,rule['payload']['basis'])
    if len(raw_rows)>1000: raise ValueError('INDUSTRIAL_ROW_LIMIT')
    snapshot=hashlib.sha256(json.dumps(raw_rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    step('sql.executed',{'row_count':len(raw_rows),'sql_hash':hashlib.sha256(SQL.encode()).hexdigest(),'snapshot':snapshot,
                         'readonly':True,'grain':'one measurement_id per row'})
    evaluated=[]
    for row in raw_rows:
        checked=evaluate(row,requirements,reports)
        if checked['report']:
            resolve_authority(checked,row,checked['report'],dict(manifest),authority_rules)
        evaluated.append(checked)
    results=[]
    for row in evaluated:
        req,report=row.pop('requirement'),row.pop('report')
        row.update(requirement_fact_id=req['fact_id'] if req else None,report_fact_id=report['fact_id'] if report else None,
                   report_value=report['raw_value'] if report else None,report_unit=report['raw_unit'] if report else None)
        results.append(row)
    summary={'batch_count':len({r['batch_id'] for r in results}),'measurement_count':len(results),
             'out_of_range':[r['measurement_id'] for r in results if r['compliance']=='out_of_range'],
             'conflicts':[r['measurement_id'] for r in results if r['agreement']=='unresolved'],
             'authority_resolved':[r['measurement_id'] for r in results if r['agreement']=='resolved_by_authority'],
             'needs_review':[r['measurement_id'] for r in results if r['compliance'] in {'needs_review','insufficient_evidence'} or r['agreement'] in {'needs_review','insufficient_evidence'}]}
    step('verification.completed',{'summary':summary,'rule_version':RULE_VERSION,'overall_pass_not_asserted':True})
    if not results:
        answer='指定零件、工序和月份没有生产测量记录，无法进行跨源核验。'
    else:
        bad=sorted({r['batch_id'] for r in results if r['compliance']=='out_of_range'})
        conflicts=sorted({r['batch_id'] for r in results if r['agreement']=='unresolved'})
        answer=(f'工业合成演示：查到 {summary["batch_count"]} 个批次、{len(results)} 条测量。'
                f'数据库温度超限批次：{"、".join(bad) or "无"}。'
                f'同测量来源冲突批次：{"、".join(conflicts) or "无"}。'
                f'按已登记权威规则裁决 {len(summary["authority_resolved"])} 条测量；裁决与来源一致分别记录。'
                f'有 {len(summary["needs_review"])} 条测量存在版本、单位或报告证据缺口，详见核验表。'
                '未消解冲突保留双方原值，不能据此给出统一的合格结论。')
    return {'kind':'industrial','status':'completed' if results else 'abstained','answer':answer,'scope':params,
            'rule_version':RULE_VERSION,'authority_version':'source-authority-v1','dataset':dict(manifest),'rows':results,'facts':facts,'summary':summary,
            'sql':SQL,'bound_params':[params[k] for k in ('part_id','operation_no','start','end')],
            'sql_hash':hashlib.sha256(SQL.encode()).hexdigest(),'source_snapshot_id':snapshot,
            'sql_rows':raw_rows,'elapsed_ms':round((time.perf_counter()-started)*1000,2),'model_used':False,
            'partial':bool(summary['needs_review'] or summary['conflicts'])}
