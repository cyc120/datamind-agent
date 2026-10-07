"""Run private PDF publication waves; only queued registered snapshots are eligible."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from datamind.agent import _write_dsn
from datamind.pdf_index import work_once,drain


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication-id')
    parser.add_argument('--workers',type=int,choices=(1,2),default=1)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--once',action='store_true',help='run one wave (default)')
    mode.add_argument('--drain',action='store_true',help='drain currently claimable work')
    mode.add_argument('--watch',action='store_true')
    parser.add_argument('--poll-interval',type=float,default=5)
    args=parser.parse_args()
    if not 1<=args.poll_interval<=60: parser.error('--poll-interval must be between 1 and 60 seconds')
    try:
        while True:
            execute=drain if args.drain else work_once
            result=execute(_write_dsn(),args.publication_id,args.workers)
            if result['claimed'] or not args.watch: print(json.dumps(result,ensure_ascii=False),flush=True)
            if any(outcome['outcome']=='failed' for outcome in result['outcomes']): return 1
            if not args.watch: return 0
            time.sleep(args.poll_interval)
    except KeyboardInterrupt: return 0
    except Exception:
        print(json.dumps({'error_code':'PDF_PUBLICATION_WORKER_FAILED'}),file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
