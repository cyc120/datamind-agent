"""构建期端口适配器 —— 只用于打包，**不会进入交付包**。

为什么需要它
------------
仓库里有几个「构建期」脚本把数据库端口 5432、Qdrant 地址 http://localhost:6333
直接写在模块常量里，没有环境变量开关：

    ingestion/ingest.py:35,40      PG = dict(host="localhost", port=5432, ...) / QDRANT = "http://localhost:6333"
    db/build_catalog.py:16         PG = dict(host="localhost", port=5432, user="postgres", ...)
    db/seed_demo_retail.py:81      psycopg2.connect(..., host="localhost", port=5432)
    db/seed_eval_cases.py:11       PG = dict(host="localhost", port=5432, ...)
    db/seed_industrial.py:15       DSN = 'host=127.0.0.1 port=5432 ...'

但交付包必须用 55432 / 56333，才能与老师机器上可能已存在的 PostgreSQL(5432) /
Qdrant(6333) 共存；本机这两个端口又被开发用的 Docker 容器占着。
按任务边界**不修改这些冻结文件**，改为在打包时用 PYTHONPATH 注入本模块：
CPython 启动时 site.py 会自动 ``import sitecustomize``，于是
``psycopg2.connect`` / ``qdrant_client.QdrantClient`` 被包上一层，
把「指向 5432 / localhost:6333 的连接」改写到便携实例。

安全约束（刻意的，不要放宽）
--------------------------
* 只改写到 ``DM_BUILD_PG_PORT`` / ``DM_BUILD_QDRANT_URL`` 指定的目标；两个环境变量
  缺失时**直接抛异常**，绝不静默回落，更不会去连 5432（那会是开发用的 Docker 库，
  等于把数据写错地方）。
* 只改写「本机 + 5432/5433」或「本机 + Qdrant 6333」这类本机连接；远端地址不动。
* 每次改写都会把命中计数写在 ``DM_BUILD_SHIM_LOG``（若设置）里，便于打包日志自证。
"""
from __future__ import annotations

import atexit
import os

_PG_PORT = os.environ.get("DM_BUILD_PG_PORT")
_QD_URL = os.environ.get("DM_BUILD_QDRANT_URL")
if not _PG_PORT or not _QD_URL:
    raise RuntimeError(
        "build_support/sitecustomize.py：必须设置 DM_BUILD_PG_PORT 与 DM_BUILD_QDRANT_URL，"
        "否则拒绝启动（避免构建脚本误连到开发用的 5432/6333）")

_PG_PORT = int(_PG_PORT)
_PG_HOST = os.environ.get("DM_BUILD_PG_HOST", "127.0.0.1")
_QD_URL = _QD_URL.rstrip("/")

_SRC_PORTS = {5432, 5433}
_SRC_HOSTS = {"localhost", "localhost.", "127.0.0.1", "::1", ""}
_LOCAL_HOSTS = {"localhost", "localhost.", "127.0.0.1", "::1"}

_stats = {"psycopg2": 0, "qdrant": 0}


def _note():
    path = os.environ.get("DM_BUILD_SHIM_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"psycopg2_rewritten={_stats['psycopg2']} qdrant_rewritten={_stats['qdrant']}\n")
    except OSError:
        pass


atexit.register(_note)


def _rewrite_port(port):
    """5432/5433/未指定 → 便携端口；其它端口原样返回（可能是别的库，不动）。"""
    if port in (None, "", 0):
        return _PG_PORT
    try:
        value = int(port)
    except (TypeError, ValueError):
        return port
    return _PG_PORT if value in _SRC_PORTS else value


def _rewrite_host(host):
    return _PG_HOST if str(host).strip().lower() in _SRC_HOSTS else host


def _install_psycopg2() -> None:
    import psycopg2
    from psycopg2.extensions import make_dsn, parse_dsn

    original = psycopg2.connect

    def connect(dsn=None, *args, **kwargs):
        if dsn is None and "dsn" in kwargs:
            dsn = kwargs.pop("dsn")
        params = {}
        if isinstance(dsn, str) and dsn.strip():
            try:
                params.update(parse_dsn(dsn))
            except Exception:            # 解析不了就原样交给 psycopg2 去报错
                return original(dsn, *args, **kwargs)
        elif isinstance(dsn, dict):      # 已经解析好的参数表
            params.update(dict(dsn))
        elif dsn is not None:
            return original(dsn, *args, **kwargs)
        params.update({k: v for k, v in kwargs.items() if k not in ("connection_factory", "cursor_factory")})
        passthrough = {k: v for k, v in kwargs.items() if k in ("connection_factory", "cursor_factory")}

        host = params.get("host")
        if host is None or str(host).strip().lower() in _SRC_HOSTS:
            params["host"] = _rewrite_host(host)
            params["port"] = _rewrite_port(params.get("port"))
        else:
            params.setdefault("port", 5432)
        params.pop("dsn", None)
        _stats["psycopg2"] += 1
        return original(make_dsn(**params), **passthrough)

    psycopg2.connect = connect


def _install_qdrant() -> None:
    import qdrant_client

    original = qdrant_client.QdrantClient
    if getattr(original, "_dm_build_patched", False):
        return

    class QdrantClient(original):        # type: ignore[misc, valid-type]
        _dm_build_patched = True

        def __init__(self, *args, **kwargs):
            url = kwargs.get("url")
            host = kwargs.get("host")
            if isinstance(url, str) and url:
                tail = url.split("://", 1)[-1]
                hostpart, _, portpart = tail.partition(":")
                port = portpart.split("/", 1)[0] or "6333"
                if hostpart.strip().lower() in _LOCAL_HOSTS and port == "6333":
                    kwargs["url"] = _QD_URL
                    _stats["qdrant"] += 1
            elif host is None or str(host).strip().lower() in _SRC_HOSTS:
                kwargs["host"] = _PG_HOST if host is None else host
                if kwargs.get("port") in (None, "", 6333):
                    kwargs["port"] = int(_QD_URL.rsplit(":", 1)[-1])
                    _stats["qdrant"] += 1
            super().__init__(*args, **kwargs)

    qdrant_client.QdrantClient = QdrantClient


_install_psycopg2()
try:
    _install_qdrant()
except ImportError:                      # 没有装 qdrant-client 的解释器不必失败
    pass
