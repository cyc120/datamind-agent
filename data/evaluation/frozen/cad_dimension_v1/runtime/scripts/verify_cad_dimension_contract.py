"""Verify frozen synthetic dimension-binding fixture; no DB/auth/model calls."""
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ingestion.cad_dimension_contract import CONTRACT_VERSION,DimensionEvaluation,evaluate,digest
from ingestion.cad_dxf import parse_file


def verify_fixture(root=ROOT):
    directory=root/'data/cad_demo/dimension_binding_v1'
    manifest=json.loads((directory/'manifest.json').read_text())
    if manifest['contract_version']!=CONTRACT_VERSION:raise ValueError('DIMENSION_CONTRACT_VERSION_MISMATCH')
    for relative,expected in manifest['frozen_files'].items():
        path=(root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('DIMENSION_FROZEN_FILE_CHANGED: '+relative)
    import ezdxf
    if ezdxf.__version__!=manifest['ezdxf_version']:raise ValueError('DIMENSION_DXF_LIBRARY_VERSION_CHANGED')
    if json.loads((directory/'contract.schema.json').read_text())!=DimensionEvaluation.model_json_schema():raise ValueError('DIMENSION_SCHEMA_CHANGED')
    request=json.loads((directory/'evaluation.json').read_text())
    measurement_source=directory/'measurement_source.json';raw=measurement_source.read_bytes();body=json.loads(raw)
    index=int(request['measurement']['source']['locator'].split('/')[-1]);source=request['measurement']['source']
    if (source['source_sha256']!=hashlib.sha256(raw).hexdigest() or source['record_version']!=body['record_version']
            or source['source_id']!=body['source_id'] or source['provenance']!=body['provenance']):
        raise ValueError('DIMENSION_MEASUREMENT_SOURCE_MISMATCH')
    actual={**body['measurements'][index],'source':source}
    if request['measurement']!=actual:raise ValueError('DIMENSION_MEASUREMENT_NOT_IN_SOURCE')
    bundle=parse_file(root/manifest['drawing_source'],document_id=request['binding']['payload']['document_id'],
                      document_version=request['binding']['payload']['document_version'],provenance='synthetic_demo')
    # No gold is supplied to parsing or comparison. Expectations are checked after.
    before=copy.deepcopy((bundle,request));result=evaluate(bundle,request)
    if before!=(bundle,request):raise ValueError('DIMENSION_INPUT_WAS_MODIFIED')
    expected=json.loads((directory/'expected.json').read_text())
    actual={k:result[k] for k in ['status','reason','comparison','authentication_verified','formal_drawing_approval','product_quality_conclusion']}
    if actual!=expected:raise ValueError('DIMENSION_EXPECTATION_MISMATCH')
    return {'contract_version':CONTRACT_VERSION,'frozen_files_verified':len(manifest['frozen_files']),
            'measurement_source_sha256':source['source_sha256'],'drawing_source_sha256':bundle['snapshot']['source']['source_sha256'],
            'result':result,'source_bytes_checked':True,'dxf_parser_executed':True,'models_called':False,'database_accessed':False,
            'authentication_performed':False,'provenance':'synthetic_demo',
            'limitation':'One author-authored source/identity/approval declaration fixture; trusted-input contract only, not real authenticated approval, ERP integration or factory accuracy.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path);args=ap.parse_args()
    try:
        result=verify_fixture();result['verified_at']=datetime.now(timezone.utc).isoformat()
        text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
        if args.output:
            with args.output.open('x',encoding='utf-8') as stream:stream.write(text)
        print(text,end='');return 0
    except (ValueError,OSError,KeyError,IndexError) as error:
        print(str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
