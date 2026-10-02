"""First-run fixed-template field/locator checks; labels never enter the parser."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pymupdf
from ingestion.ingest import parse_pdf
from ingestion.industrial_facts import extract,PARSER_VERSION


def compact(value):return re.sub(r'\s+','',str(value or ''))


def iou(a,b):
    width=max(0,min(a[2],b[2])-max(a[0],b[0]));height=max(0,min(a[3],b[3])-max(a[1],b[1]))
    inter=width*height;union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union>0 else 0


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=ROOT/'reports/industrial_forms_initial.json');args=ap.parse_args()
    if args.output.exists():
        raise SystemExit('Report already exists; preserve the initial run and use --output with a new path')
    path=ROOT/'data/evaluation/industrial_forms_holdout_v1.json';suite=json.loads(path.read_text());source=ROOT/suite['source_file']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==suite['source_sha256']
    for name,sha in suite['frozen_parser_code_hashes'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,'frozen parser changed: '+name
    assert suite['visual_qa']=='all four pages visually checked'
    started=time.perf_counter();doc_id='forms-holdout-'+suite['source_sha256'][:12]
    # Only original PDF and document identifier reach parse/extract; no fixture, labels or counterparts.
    blocks,count,quality=parse_pdf(source,doc_id,include_quality=True)
    for index,b in enumerate(blocks):b['block_id']='holdout-block-'+str(index)
    with pymupdf.open(source) as pdf:sizes={i+1:[float(p.rect.width),float(p.rect.height)] for i,p in enumerate(pdf)}
    facts,field_quality=extract(blocks,document_id=doc_id,source_sha256=suite['source_sha256'],page_sizes=sizes)
    cases=[]
    for case in suite['cases']:
        page=case['page'];matches=[f for f in facts if f['evidence_refs'][0]['page_no']==page]
        fact=matches[0] if len(matches)==1 else None;refs={r['field']:r for r in fact['evidence_refs']} if fact else {}
        expected=case['expected_fact'];entity_bound=bool(fact and all(compact(fact.get(k))==compact(v) for k,v in expected.items() if k in ('entity_id','operation_no','measurement_id')))
        diagnostics={}
        for b in blocks:
            if b['page_no']!=page:continue
            raw=re.split(r'[：:]',b['text'],maxsplit=1)
            if len(raw)==2:diagnostics.setdefault(compact(raw[0]),[]).append((raw[1],b))
        checks=[]
        for gold in case['fields']:
            ref=refs.get(gold['label']);text=ref['text'].split('：',1)[1] if ref and '：' in ref['text'] else None
            position=iou(ref['bbox'],gold['bbox']) if ref else 0
            text_match=compact(text)==compact(gold['value']) if text is not None else False
            candidates=diagnostics.get(gold['label'],[])
            raw_ok=len(candidates)==1 and compact(candidates[0][0])==compact(gold['value'])
            checks.append({'field':gold['label'],'gold_value':gold['value'],'actual_value':text,
                          'text_matches':text_match,'locator_iou':round(position,4),'entity_bound':entity_bound,
                          'passed':text_match and position>=.5 and entity_bound,
                          'raw_block_diagnostic':{'matched':raw_ok,'candidates':[{'text':v,'bbox':b['bbox'],'confidence':b.get('ocr_confidence')} for v,b in candidates]}})
        normalized=bool(fact and all(compact(fact.get(k))==compact(v) for k,v in expected.items()))
        cases.append({'case_id':case['case_id'],'carrier':case['carrier'],'template':case['template'],'page':page,
            'field_count':len(checks),'fields_passed':sum(c['passed'] for c in checks),'fields':checks,
            'fact_exists':fact is not None,'fact_normalization_passed':normalized,
            'parser_errors':[e for e in field_quality['errors'] if e['page_no']==page]})
    strata={carrier:{'passed':sum(c['fields_passed'] for c in cases if c['carrier']==carrier),
                     'field_count':sum(c['field_count'] for c in cases if c['carrier']==carrier)} for carrier in ('native','scan')}
    report={'created_at':datetime.now(timezone.utc).isoformat(),'suite_version':suite['suite_version'],
        'scope':suite['provenance'],'source_file':suite['source_file'],'source_sha256':suite['source_sha256'],
        'suite_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'parser_version':PARSER_VERSION,
        'parser_code_hashes':suite['frozen_parser_code_hashes'],'evaluator_version':'industrial-forms-eval-v1',
        'evaluator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'field_count':sum(c['field_count'] for c in cases),
        'fields_passed':sum(c['fields_passed'] for c in cases),'strata':strata,'facts_passed':sum(c['fact_normalization_passed'] for c in cases),
        'fact_case_count':len(cases),'cases':cases,'quality':quality,'field_quality':field_quality,
        'elapsed_ms':round((time.perf_counter()-started)*1000,2),'parser_input':'Original PDF only; no gold/reference text',
        'runtime_indexed':False,'model_used':False,'limitations':['Synthetic authored follow-on templates, not a blind factory benchmark',
        'Raw-block diagnostics are not accepted fields or fact extraction scores','Missing fields remain in the denominator; no parser tuning in this first report']}
    args.output.parent.mkdir(parents=True,exist_ok=True);
    with args.output.open('x',encoding='utf-8') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'fields':f"{report['fields_passed']}/{report['field_count']}",'strata':strata,'facts':f"{report['facts_passed']}/{report['fact_case_count']}",'output':str(args.output)},ensure_ascii=False))
    return 0 if report['fields_passed']==report['field_count'] else 1


if __name__=='__main__':raise SystemExit(main())
