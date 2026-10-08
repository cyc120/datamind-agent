"""Parse one Office file into the unified parse contract and print the summary.

Detection is by package content, not by file name: a ZIP that does not contain a known
OOXML part is reported as `application/zip` and refused, never guessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind import parse_contract
from ingestion import office


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--source-version', help='默认为源文件 SHA-256')
    parser.add_argument('--output', type=Path, help='可选：把契约结果写入该 JSON 文件')
    args = parser.parse_args(argv)
    source = args.source
    if not source.is_file():
        print(json.dumps({'status': 'failed', 'error_code': 'PARSE_MEDIA_UNREADABLE'}), file=sys.stderr)
        return 1
    version = args.source_version or hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        detected = parse_contract.detect_media_type(source)
        result = office.parse(source, document_id=args.document_id, source_version=version)
    except Exception as error:
        detail = getattr(error, 'detail', None)
        code = detail.get('error_code') if isinstance(detail, dict) else None
        print(json.dumps({'status': 'failed', 'error_code': code or 'OFFICE_PARSE_FAILED',
                          'detected_media_type': detected if 'detected' in dir() else None}),
              file=sys.stderr)
        return 1
    payload = result.payload
    summary = {'status': 'ok', 'detected_media_type': detected,
               'adapter': payload['manifest'].get('adapter'),
               'parse_status': payload['parse_status'], 'blocks': len(payload['blocks']),
               'locator_kinds': sorted({block['locator']['locator_kind'] for block in payload['blocks']}),
               'unsupported_features': payload['unsupported_features'],
               'contract_version': payload['contract_version']}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'契约结果：{args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
