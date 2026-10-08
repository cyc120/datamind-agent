"""Parse a file through the contract router and persist its blocks, locators and relations.

Formats without a page concept (Office, DXF) land in `document_contract_blocks`; the
page-shaped `document_blocks` table is left to the PDF ingest path. A successful import also
registers the `document_assets` row that owns the contract blocks: without it the document
has no public identity, and `GET /v1/documents/{id}/contract` answers 404 because
`_public_document()` looks the document up in `document_assets` first.

`--dry-run` validates and reports without writing anything.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2

from datamind import parse_contract
from datamind.knowledge import COLLECTIONS
from ingestion import cad_adapter, contract_store, office

DEFAULT_DSN = 'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'
DEFAULT_COLLECTION = 'demo_kb'
# `_public_document()` requires exactly this status; a document whose contract blocks exist
# but whose asset says anything else is stored yet unreadable on HTTP.
DEFAULT_STATUS = 'indexed'

# Media type to the `file_type` vocabulary of `document_assets` (`ingest.py` writes 'pdf'
# and 'json'). Every adapter this script can route to maps to its real container format.
FILE_TYPES = {
    parse_contract.DXF_MEDIA_TYPE: 'dxf',
    'application/pdf': 'pdf',
    office.DOCX: 'docx',
    office.PPTX: 'pptx',
    office.XLSX: 'xlsx',
}

ASSET_UPSERT = '''
    INSERT INTO document_assets(document_id, title, collection, file_type, origin, sha256,
        object_uri, page_count, language, acl, parser_version, doc_version, status,
        needs_review)
    VALUES (%s,%s,%s,%s,'import',%s,%s,NULL,NULL,%s,%s,1,%s,FALSE)
    ON CONFLICT (document_id) DO UPDATE SET
        title=EXCLUDED.title, collection=EXCLUDED.collection, file_type=EXCLUDED.file_type,
        sha256=EXCLUDED.sha256, object_uri=EXCLUDED.object_uri, acl=EXCLUDED.acl,
        parser_version=EXCLUDED.parser_version, status=EXCLUDED.status, needs_review=FALSE
    RETURNING document_id, status'''


def build_route(path):
    """Register every available adapter so detection can route this file."""
    cad_adapter.register_cad_adapters()
    office.register_office_adapters()


def parse_any(path, *, document_id, source_version):
    """Route the file and return `(detected_media_type, validated result)`.

    The media type is returned rather than sniffed again by the caller so the asset's
    `file_type` and the router cannot drift apart.
    """
    build_route(path)
    detected = parse_contract.detect_media_type(path)
    result = parse_contract.dispatch(detected, path=path, document_id=document_id,
                                     source_version=source_version)
    return detected, result


def register_asset(cur, *, document_id, path, sha256, detected_media_type, collection,
                   status, title):
    """Register (or refresh) the asset row that makes the document publicly readable.

    Idempotent on `document_id`: a repeated import updates the same row instead of adding a
    second one. `document_assets` also has a `(collection, sha256)` unique key — a different
    id for the same bytes in the same collection is refused with a named error rather than
    surfaced as a raw constraint violation.

    The defaults follow the rest of the repository: `collection` must be one the API will
    serve, `status` must be `indexed` for `_public_document()` to find the document, and
    `needs_review` stays false because these blocks are a parse artifact, not a fact
    awaiting human review.
    """
    file_type = FILE_TYPES.get(detected_media_type)
    if file_type is None:
        raise ValueError(f'CONTRACT_ASSET_FILE_TYPE_UNKNOWN:{detected_media_type}')
    if collection not in COLLECTIONS:
        raise ValueError(f'CONTRACT_ASSET_COLLECTION_NOT_ALLOWED:{collection}')
    try:
        cur.execute(ASSET_UPSERT, (document_id, title, collection, file_type, sha256,
                                   str(path.resolve()), ['public'],
                                   parse_contract.CONTRACT_VERSION, status))
        row = cur.fetchone()
    except psycopg2.errors.UniqueViolation:
        cur.execute('SELECT document_id FROM document_assets WHERE collection=%s AND sha256=%s',
                    (collection, sha256))
        existing = cur.fetchone()
        code = 'CONTRACT_ASSET_ALREADY_REGISTERED_ELSEWHERE'
        detail = {'collection': collection, 'sha256': sha256,
                  'existing_document_id': existing[0] if existing else None,
                  'requested_document_id': document_id}
        raise ValueError(f'{code}:{json.dumps(detail, ensure_ascii=False)}') from None
    return {'document_id': row[0], 'status': row[1], 'file_type': file_type,
            'collection': collection}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--source-version', help='默认为源文件 SHA-256')
    parser.add_argument('--collection', default=os.environ.get('DATAMIND_IMPORT_COLLECTION') or DEFAULT_COLLECTION,
                        help=f'契约文档所属知识库，必须是 {sorted(COLLECTIONS)} 之一（默认 {DEFAULT_COLLECTION}）')
    parser.add_argument('--title', help='文档标题；默认取源文件名')
    parser.add_argument('--status', default=DEFAULT_STATUS,
                        help=f"资产状态；契约读接口要求 {DEFAULT_STATUS!r}（默认 {DEFAULT_STATUS}）")
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
        detected, result = parse_any(args.source, document_id=args.document_id,
                                     source_version=version)
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
    dsn = args.dsn or os.environ.get('DATAMIND_META_WRITE_DSN') or os.environ.get('DATAMIND_WRITE_DSN') or DEFAULT_DSN
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            asset = register_asset(cur, document_id=args.document_id, path=args.source,
                                   sha256=version, detected_media_type=detected,
                                   collection=args.collection, status=args.status,
                                   title=args.title or args.source.stem)
            stored = contract_store.persist_result(cur, document_id=args.document_id,
                                                   payload=payload, dsn=dsn)
    except ValueError as error:
        print(json.dumps({'status': 'failed', 'error_code': str(error) or 'CONTRACT_ASSET_INVALID',
                          'detected_media_type': detected}), file=sys.stderr)
        return 1
    summary.update({'persisted': True, 'asset': asset,
                    **{key: stored[key] for key in ('locators', 'relations', 'omitted_columns')}})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
