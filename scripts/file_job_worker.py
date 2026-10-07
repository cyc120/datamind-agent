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
    args = ap.parse_args()
    if args.watch:
        try:
            while True:
                result = work_once(_write_dsn(), job_id=args.job_id, workers=args.workers)
                if result['claimed']:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                else:
                    time.sleep(2)
        except KeyboardInterrupt:
            return
    action = work_once if args.once else drain
    print(json.dumps(action(_write_dsn(), job_id=args.job_id, workers=args.workers), ensure_ascii=False))


if __name__ == '__main__':
    main()
