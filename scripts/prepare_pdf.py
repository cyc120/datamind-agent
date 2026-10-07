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
    parser.add_argument('--deadline', type=float, metavar='SECONDS',
                        help='Run the preparation in its own process group under a hard deadline '
                             '(0.5–3600) instead of in-process.')
    parser.add_argument('--grace', type=float, metavar='SECONDS',
                        help='Seconds between SIGTERM and SIGKILL when the deadline expires.')
    args = parser.parse_args(argv)
    if args.source and any(value is None for value in
                           (args.document_id, args.document_version, args.expected_sha256)):
        parser.error('--source requires --document-id, --document-version and --expected-sha256')
    if args.verify and any(value is not None for value in
                           (args.document_id, args.document_version, args.expected_sha256)):
        parser.error('--verify uses the saved request; omit document identity and source SHA flags')
    if args.deadline is not None:
        from datamind.task_budget import run_bounded, validate_deadline
        try:
            deadline = validate_deadline(args.deadline)
        except ValueError as error:
            parser.error(str(error))
        if args.verify:
            parser.error('--deadline currently applies to --source only')
        request = {'document_id': args.document_id, 'document_version': args.document_version,
                   'source_sha256': args.expected_sha256, 'job_id': args.job_id,
                   'attempt_token': args.attempt_token}
        outcome = run_bounded('ingestion.pdf_prepare', 'prepare_json',
                              [str(args.source), str(args.storage_root), request],
                              deadline_s=deadline, grace_s=args.grace, fallback_error_code='PDF_PREPARE_FAILED')
        if outcome['status'] == 'ok':
            print(json.dumps(outcome['value'], ensure_ascii=False, indent=2))
            return 0
        print(json.dumps({'status': 'failed', 'error_code': outcome['error_code'],
                          'elapsed_ms': outcome['elapsed_ms'],
                          'deadline_seconds': outcome['deadline_seconds']}), file=sys.stderr)
        return 1
    if args.grace is not None:
        parser.error('--grace requires --deadline')
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
