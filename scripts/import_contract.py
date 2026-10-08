"""Parse a file through the contract router and persist its blocks, locators and relations.

Formats without a page concept (Office, DXF) land in `document_contract_blocks`; the
page-shaped `document_blocks` table is left to the PDF ingest path. `--dry-run` validates
and reports without writing anything.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2

from datamind import parse_contract
from ingestion import cad_adapter, contract_store, office


def build_route(path):
    """Register every available adapter so detection can route this file."""
    cad_adapter.register_cad_adapters()
    office.register_office_adapters()


def parse_any(path, *, document_id, source_version):
    build_route(path)
    detected = parse_contract.detect_media_type(path)
    return parse_contract.dispatch(detected, path=path, document_id=document_id,
                                  source_version=source_version)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--source-version', help='默认为源文件 SHA-256')
    parser.add_argument('--dry-run', action='store_true', help='只校验与统计，不写库')
    parser.add_argument('--dsn', help='写入 DSN；默认 DATAMIND_META_WRITE_DSN 或本机默认')
    parser.add_argument('--output', type=Path, help='可选：把契约结果写入该 JSON 文件')
    args = parser.parse_args(argv)
    if not args.source.is_file():
        print(json.dumps({'status': 'failed', 'error_code': 'PARSE_MEDIA_UNREADABLE'}), file=sys.stderr)
        return 1
    version = args.source_version or hashlib.sha256(args.source.read_bytes()).hexdigest()
    detected = None
    try:
        detected = parse_contract.detect_media_type(args.source)
        result = parse_any(args.source, document_id=args.document_id, source_version=version)
    except Exception as error:
        detail = getattr(error, 'detail', None)
        code = detail.get('error_code') if isinstance(detail, dict) else None
        print(json.dumps({'status': 'failed', 'error_code': code or 'CONTRACT_IMPORT_FAILED',
                          'detected_media_type': detected}), file=sys.stderr)
        return 1
    payload = result.payload
    summary = {'status': 'validated', 'detected_media_type': detected,
               'parse_status': payload['parse_status'], 'blocks': len(payload['blocks']),
               'relations': len(payload['relations']),
               'unsupported_features': payload['unsupported_features']}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.dry_run:
        summary['persisted'] = False
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    import os
    dsn = args.dsn or os.environ.get('DATAMIND_META_WRITE_DSN') or os.environ.get('DATAMIND_WRITE_DSN') \
        or 'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        stored = contract_store.persist_result(cur, document_id=args.document_id, payload=payload)
    summary.update({'persisted': True, **{key: stored[key] for key in ('locators', 'relations')}})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
