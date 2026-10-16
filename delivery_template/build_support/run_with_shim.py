"""构建期启动器：让包内嵌入式 Python 也能加载 build_support/sitecustomize.py。

为什么需要它
------------
便携运行时用的是 CPython **嵌入式发行版**，它靠 ``python3xx._pth`` 决定 sys.path；
存在 ``._pth`` 时 CPython 会忽略 ``PYTHONPATH``，``site`` 也不会自动
``import sitecustomize``。而 build_delivery.ps1 用包内这套 Python 去跑仓库的
构建脚本（migrate / seed / ingest），这些脚本里硬编码了 5432、6333，
需要 sitecustomize 把连接改写到便携实例（55432 / 56333）。

所以构建期统一用本文件作为入口：

    runtime\\python\\python.exe <本文件> <仓库脚本> [参数...]

它做三件事：
  1. 把本目录放到 sys.path 最前，并显式 import sitecustomize（安装端口改写）；
  2. 先探一下便携 PostgreSQL / Qdrant 是否在监听，不在就给出中文错误（而不是
     让 psycopg2 抛一串看不懂的 OperationalError）；
  3. 用 ``__main__`` 语义执行目标脚本，等价于直接 ``python <脚本>``。
"""
from __future__ import annotations

import os
import runpy
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import sitecustomize  # noqa: E402,F401  —— 导入即安装改写

if len(sys.argv) < 2:
    sys.stderr.write('用法：python run_with_shim.py <脚本路径> [参数...]\n')
    raise SystemExit(2)


def _probe(host: str, port: int, what: str) -> None:
    with socket.socket() as sock:
        sock.settimeout(2.0)
        try:
            sock.connect((host, port))
        except OSError as error:
            raise SystemExit(
                f'[构建期] {what} 未在 {host}:{port} 监听（{error}）。'
                f'请先启动便携实例，或检查 DM_BUILD_* 环境变量。')


_probe('127.0.0.1', sitecustomize._PG_PORT, '便携 PostgreSQL')
if os.environ.get('DM_BUILD_REQUIRE_QDRANT'):
    # 只有确实要写向量库的那一步（ingestion/ingest.py）才检查 Qdrant；
    # 种子与 Catalog 步骤只连数据库，此时 Qdrant 还没起。
    _probe('127.0.0.1', int(sitecustomize._QD_URL.rsplit(':', 1)[-1]), '便携 Qdrant')

target = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(target, run_name='__main__')
