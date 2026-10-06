"""Evaluate source-only extraction; labels never enter the reader or renderer."""
from collections import defaultdict
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.cad_dxf import CadReadError, parse_file
from ingestion.cad_contract import eligible_features
from ingestion.cad_render import render_svg


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(suite_path):
    suite=json.loads(suite_path.read_text())
    frozen=suite.get('frozen_reader')
    if suite['stage']=='acceptance':
        if not frozen:raise ValueError('CAD_FREEZE_REQUIRED')
        for name,sha in frozen['code_hashes'].items():
            if digest(ROOT/name)!=sha:raise ValueError('CAD_FROZEN_CODE_CHANGED: '+name)
        import ezdxf
        if ezdxf.__version__!=frozen['ezdxf_version']:raise ValueError('CAD_DEPENDENCY_VERSION_CHANGED')
    totals=defaultdict(lambda:{'passed':0,'total':0});rows=[]
    def check(row,metric,condition):
        totals[metric]['total']+=1;totals[metric]['passed']+=int(condition)
        row['checks'].append({'metric':metric,'passed':bool(condition)})
    for case in suite['cases']:
        path=(ROOT/case['source_file']).resolve()
        if not path.is_relative_to(ROOT/'data/cad_demo') or digest(path)!=case['source_sha256']:
            raise ValueError('CAD_SOURCE_CHANGED: '+case['case_id'])
        row={'case_id':case['case_id'],'source_sha256':case['source_sha256'],'checks':[]}
        # Only raw DXF and caller identity/version reach extraction.
        try:bundle=parse_file(path,document_id=case['document_id'],provenance='synthetic_demo')
        except CadReadError as error:
            row['error_code']=error.code
            if case['expected_error']:
                check(row,'unsupported_rejection',error.code==case['expected_error'])
            else:
                # Failed extraction still contributes all expected denominators.
                for _ in case['expected_identity']:check(row,'title_fields',False)
                for _ in case['expected_entities']:check(row,'native_entities_and_locations',False)
                check(row,'native_entity_count',False)
                for _ in range(4):
                    check(row,'dimension_fields' if case['expected_feature']['unit']!='unknown'
                          else 'unknown_value_preservation',False)
                for metric in ['annotation_location','object_association','use_gate','rendered_handle_coverage']:
                    check(row,metric,False)
        else:
            if case['expected_error']:
                check(row,'unsupported_rejection',False)
            else:
                s=bundle['snapshot'];row['snapshot']=s;row['metadata']=bundle['metadata']
                for key,expected in case['expected_identity'].items():
                    got=s['identity'][key]
                    check(row,'title_fields',got['value']==expected['value'] and got['locator']['handle']==expected['handle'])
                entities={e['handle']:e for e in s['entities']}
                for expected in case['expected_entities']:
                    got=entities.get(expected['handle'],{})
                    check(row,'native_entities_and_locations',all(got.get(k)==v for k,v in expected.items()))
                check(row,'native_entity_count',len(entities)==len(case['expected_entities']))
                f=s['features'][0] if len(s['features'])==1 else {}
                expected=case['expected_feature']
                for key in ['nominal_value','tolerance_lower','tolerance_upper','unit']:
                    check(row,'dimension_fields' if expected['unit']!='unknown' else 'unknown_value_preservation',f.get(key)==expected[key])
                check(row,'annotation_location',f.get('annotation_raw')==expected['annotation_raw']
                      and f.get('locator',{}).get('handle')==expected['annotation_handle'])
                check(row,'object_association',f.get('association_status')==expected['association_status']
                      and f.get('selected_target')==expected['selected_target'])
                context={key:value['value'] for key,value in case['expected_identity'].items()}
                context['source_sha256']=case['source_sha256']
                gate=eligible_features(s,context);row['gate']=gate
                reason=case['expected_gate_reason']
                check(row,'use_gate',gate['status']=='ready' if reason is None else
                      not gate['features'] and next(iter(gate.get('excluded',[])),{}).get('reason')==reason)
                svg=ET.fromstring(render_svg(bundle))
                rendered={g.attrib['data-handle'] for g in svg.findall('{http://www.w3.org/2000/svg}g')}
                check(row,'rendered_handle_coverage',rendered==set(entities))
        row['passed']=all(c['passed'] for c in row['checks']);rows.append(row)
    return dict(evaluated_at=datetime.now(timezone.utc).isoformat(),suite_version=suite['suite_version'],
                suite_sha256=digest(suite_path),stage=suite['stage'],provenance=suite['provenance'],
                limitation=suite['limitation'],metrics=dict(totals),cases=rows,
                cases_passed=sum(r['passed'] for r in rows),case_count=len(rows),models_called=False,database_accessed=False)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--suite',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    try:
        report=evaluate(args.suite)
        with args.output.open('x') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ['suite_version','metrics','cases_passed','case_count']},ensure_ascii=False))
        return 0 if report['cases_passed']==report['case_count'] else 1
    except (ValueError,OSError,KeyError) as error:
        print(str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
