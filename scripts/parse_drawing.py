"""Parse a 2D drawing into the unified contract and print the summary.

DXF is read natively through the frozen CAD profile. DWG needs an external converter and
is refused with an explicit code instead of being guessed at.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind import parse_contract
from ingestion import cad_adapter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--source-version', help='默认为源文件 SHA-256')
    parser.add_argument('--provenance', choices=['synthetic_demo', 'authorized_external'],
                        default='synthetic_demo')
    parser.add_argument('--output', type=Path, help='可选：把契约结果写入该 JSON 文件')
    args = parser.parse_args(argv)
    source = args.source
    if not source.is_file():
        print(json.dumps({'status': 'failed', 'error_code': 'PARSE_MEDIA_UNREADABLE'}), file=sys.stderr)
        return 1
    version = args.source_version or hashlib.sha256(source.read_bytes()).hexdigest()
    detected = None
    try:
        detected = parse_contract.detect_media_type(source)
        result = cad_adapter.parse(source, document_id=args.document_id, source_version=version,
                                   provenance=args.provenance)
    except Exception as error:
        detail = getattr(error, 'detail', None)
        code = detail.get('error_code') if isinstance(detail, dict) else None
        print(json.dumps({'status': 'failed', 'error_code': code or 'CAD_PARSE_FAILED',
                          'detected_media_type': detected}), file=sys.stderr)
        return 1
    payload = result.payload
    print(json.dumps({'status': 'ok', 'detected_media_type': detected,
                      'adapter': payload['manifest'].get('profile'),
                      'parse_status': payload['parse_status'], 'blocks': len(payload['blocks']),
                      'relations': len(payload['relations']),
                      'block_types': sorted({b['block_type'] for b in payload['blocks']}),
                      'unsupported_features': payload['unsupported_features'],
                      'contract_version': payload['contract_version']},
                     ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'契约结果：{args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
