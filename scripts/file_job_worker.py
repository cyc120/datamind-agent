"""Bounded durable worker for authenticated staged DXF/measurement registrations."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind.agent import _write_dsn
from datamind.file_jobs import work_once, drain


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once', action='store_true')
    mode.add_argument('--drain', action='store_true')
    mode.add_argument('--watch', action='store_true', help='Poll until interrupted; recovers expired leases.')
    ap.add_argument('--job-id')
    ap.add_argument('--workers', type=int, choices=range(1, 5), default=2)
    ap.add_argument('--deadline', type=float, metavar='SECONDS',
                    help='Run each registration in its own process group under a hard deadline '
                         '(0.5–3600); default keeps the in-process path.')
    ap.add_argument('--grace', type=float, metavar='SECONDS',
                    help='Seconds between SIGTERM and SIGKILL when a deadline expires.')
    args = ap.parse_args()
    if args.deadline is not None:
        from datamind.task_budget import validate_deadline
        try:
            args.deadline = validate_deadline(args.deadline)
        except ValueError as error:
            ap.error(str(error))
    if args.grace is not None and args.deadline is None:
        ap.error('--grace requires --deadline')
    if args.watch:
        try:
            while True:
                result = work_once(_write_dsn(), job_id=args.job_id, workers=args.workers,
                                   deadline_s=args.deadline)
                if result['claimed']:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                else:
                    time.sleep(2)
        except KeyboardInterrupt:
            return
    action = work_once if args.once else drain
    print(json.dumps(action(_write_dsn(), job_id=args.job_id, workers=args.workers,
                            deadline_s=args.deadline), ensure_ascii=False))


if __name__ == '__main__':
    main()
