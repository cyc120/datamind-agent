"""Prepare or verify a bounded native PDF snapshot; never publish to the KB."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.pdf_prepare import PdfPrepareError, prepare_pdf, verify_prepared


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--source', type=Path, help='Local PDF to prepare')
    modes.add_argument('--verify', action='store_true', help='Verify an existing sealed attempt')
    parser.add_argument('--storage-root', type=Path, default=ROOT / 'data/object_store/pdf_prepared')
    parser.add_argument('--document-id')
    parser.add_argument('--document-version', type=int)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--attempt-token', required=True)
    args = parser.parse_args(argv)
    if args.source and any(value is None for value in
                           (args.document_id, args.document_version, args.expected_sha256)):
        parser.error('--source requires --document-id, --document-version and --expected-sha256')
    if args.verify and any(value is not None for value in
                           (args.document_id, args.document_version, args.expected_sha256)):
        parser.error('--verify uses the saved request; omit document identity and source SHA flags')
    try:
        if args.verify:
            result = verify_prepared(args.storage_root, args.job_id, args.attempt_token)
        else:
            result = prepare_pdf(args.source, args.storage_root, {
                'document_id': args.document_id,
                'document_version': args.document_version,
                'source_sha256': args.expected_sha256,
                'job_id': args.job_id,
                'attempt_token': args.attempt_token,
            })
    except PdfPrepareError as error:
        print(json.dumps({'status': 'failed', 'error_code': error.code}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({'status': 'failed', 'error_code': 'PDF_STORAGE_FAILED'}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
