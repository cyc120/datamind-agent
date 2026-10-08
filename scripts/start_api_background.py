"""Detach the local API from an agent/terminal session; logs stay in the workspace."""
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import json

ROOT=Path(__file__).resolve().parents[1]


def ready():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=1) as response:
            return json.load(response).get('agent')=='sql+rag'
    except (OSError,ValueError):return False


def main():
    if ready():
        print('DataMind Agent 已运行：http://127.0.0.1:8000/');return
    directory=ROOT/'.cache/api';directory.mkdir(parents=True,exist_ok=True)
    with (directory/'server.log').open('ab') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','datamind.api:app','--host','127.0.0.1','--port','8000','--no-proxy-headers'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (directory/'server.pid').write_text(str(process.pid)+'\n')
    for _ in range(20):
        if process.poll() is not None:raise RuntimeError('API 启动失败；查看 .cache/api/server.log')
        if ready():
            print(f'DataMind Agent 后台运行，PID {process.pid}：http://127.0.0.1:8000/');return
        time.sleep(.25)
    raise RuntimeError('API 未在 5 秒内就绪；查看 .cache/api/server.log')


if __name__=='__main__':main()
