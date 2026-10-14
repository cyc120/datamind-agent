"""Detach the local API (and the queue workers) from an agent/terminal session.

`GET /v1/uploads/{upload_id}` is a pure read since review 5.1: it reports the queue but no
longer drives it. A deployment that starts only the API therefore leaves every upload at
`queued` forever, so the background start launches the standalone worker too and records its
pid in `.cache/api/upload_worker.pid`. Logs stay in the workspace.

`POST /v1/wiki/builds` has the same shape for a different queue: §5.9 of `docs/WIKI_API.md`
says the request only records a `queued` task, so a deployment that starts no wiki worker
leaves every build at `queued` and `GET /v1/wiki/builds/{id}` never reaches a terminal state.
The wiki worker is started the same way and recorded in `.cache/api/wiki_build_worker.pid`.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_WORKER_ARGV = ('-m', 'ingestion.upload_worker', '--watch')
WIKI_WORKER_ARGV = ('scripts/wiki_build_worker.py', '--watch')


def ready():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=1) as response:
            return json.load(response).get('agent')=='sql+rag'
    except (OSError,ValueError):return False


def live_pid(path):
    """The pid in `path` when that process still exists, else None (stale pid file)."""
    try:
        pid=int(path.read_text().strip())
        os.kill(pid,0)
    except (OSError,ValueError):return None
    return pid


def start_upload_worker(directory):
    """Start the detached queue worker unless a live one already owns the pid file."""
    pid_file=directory/'upload_worker.pid'
    pid=live_pid(pid_file)
    if pid:
        print(f'上传队列 worker 已在运行，PID {pid}')
        return pid
    with (directory/'upload_worker.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,*UPLOAD_WORKER_ARGV],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    pid_file.write_text(str(process.pid)+'\n')
    for _ in range(20):
        if process.poll() is not None:
            raise RuntimeError('上传队列 worker 启动失败；查看 .cache/api/upload_worker.log')
        time.sleep(.05)
    print(f'上传队列 worker 后台运行，PID {process.pid}（日志 .cache/api/upload_worker.log）')
    return process.pid


def start_wiki_build_worker(directory):
    """Start the detached wiki build worker unless a live one already owns the pid file.

    Same contract as `start_upload_worker`: pid file in `.cache/api/`, log next to it, and a
    liveness check so a worker that dies immediately is reported instead of silently recorded.
    """
    pid_file=directory/'wiki_build_worker.pid'
    pid=live_pid(pid_file)
    if pid:
        print(f'Wiki 整理 worker 已在运行，PID {pid}')
        return pid
    with (directory/'wiki_build_worker.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,*WIKI_WORKER_ARGV],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    pid_file.write_text(str(process.pid)+'\n')
    for _ in range(20):
        if process.poll() is not None:
            raise RuntimeError('Wiki 整理 worker 启动失败；查看 .cache/api/wiki_build_worker.log')
        time.sleep(.05)
    print(f'Wiki 整理 worker 后台运行，PID {process.pid}'
          '（日志 .cache/api/wiki_build_worker.log）')
    return process.pid


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-upload-worker',action='store_true',
                        help='不启动上传队列 worker（SQL-only 部署没有上传管道）')
    parser.add_argument('--no-wiki-worker',action='store_true',
                        help='不启动 Wiki 整理 worker（没有 Wiki 表/证据的部署）')
    parser.add_argument('--host',default='127.0.0.1',
                        help='绑定地址；默认仅回环。分享给他人访问时传 0.0.0.0')
    args=parser.parse_args(argv)
    directory=ROOT/'.cache/api';directory.mkdir(parents=True,exist_ok=True)
    if not args.no_upload_worker:
        # First, so an already-running API is healed too: without a worker the queue stalls.
        start_upload_worker(directory)
    if not args.no_wiki_worker:
        # Same reasoning for wiki builds: `POST /v1/wiki/builds` only enqueues (WIKI_API.md 5.9),
        # so without this worker every task stays `queued` forever.
        start_wiki_build_worker(directory)
    if ready():
        print('DataMind Agent 已运行：http://127.0.0.1:8000/');return
    with (directory/'server.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','datamind.api:app','--host',args.host,'--port','8000','--no-proxy-headers'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (directory/'server.pid').write_text(str(process.pid)+'\n')
    for _ in range(20):
        if process.poll() is not None:raise RuntimeError('API 启动失败；查看 .cache/api/server.log')
        if ready():
            print(f'DataMind Agent 后台运行，PID {process.pid}：http://127.0.0.1:8000/');return
        time.sleep(.25)
    raise RuntimeError('API 未在 5 秒内就绪；查看 .cache/api/server.log')


if __name__=='__main__':main()
