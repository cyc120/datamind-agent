"""Wiki 整理任务的常驻 worker：把 `wiki_build_jobs` 从 `queued` 推到终态。

`POST /v1/wiki/builds` 只落一行 `queued` 的任务并返回任务状态（`docs/WIKI_API.md` §5.9：
"整理由 `wiki_jobs` 的任务行承载，失败只记在任务行上"）。推进任务的**唯一**时钟是这个进程 ——
与 `ingestion/upload_worker.py` 头注释记录的同一个理由：HTTP 轮询一旦兼任时钟，关掉页面就
停摆、N 个并发请求就能起 N 个整理。

    .venv/bin/python scripts/wiki_build_worker.py --watch                 # 常驻
    .venv/bin/python scripts/wiki_build_worker.py --once                  # 跑一轮就退出
    .venv/bin/python scripts/wiki_build_worker.py --once --limit 5        # 一轮最多 5 个任务
    .venv/bin/python scripts/wiki_build_worker.py --check                 # 只体检，不整理

三个模式：

* `--once`：把当前可领取的任务都跑一遍（`--limit` 限制条数），然后退出。测试与 cron 用这个。
* `--watch`：轮询；一轮没有可领取的任务就睡 `--interval` 秒。SIGINT/SIGTERM 会让它在**当前
  任务跑完之后**退出，并且退出前把"已领取但没有落终态"的任务补写成失败
  （`WIKI_BUILD_INTERRUPTED`），不留 `running` 僵尸。
* `--check`：连一次库，打印 8 张 wiki 表的行数、连接角色、队列长度与本次身份判定，然后退出。
  只发 SELECT。

身份（本 worker 唯一需要解释的地方）
------------------------------------
`claim_build()` / `complete_build()` 里的 `_lock_actor()` 会拿身份去 `auth.lock_identity()`
复查会话与角色，所以 worker 必须**以任务 owner 的身份**开工，且该身份要对应一个真实会话：

* `--session-token`（或 `DATAMIND_WIKI_WORKER_SESSION`）：owner 的一个有效会话 token，
  worker 用它与 `auth_sessions.token_hash` 匹配，匹配不上就不开工；
* 或者本机回环上的无登录演示身份（`user-local-admin`），与 API 在同一台机器上给每个请求的
  身份**完全同一个**（`auth.py:59-69`）。

两者都不成立时，任务会被落成 `WIKI_WORKER_IDENTITY_UNAVAILABLE`，而不是永远停在 `queued`
让 §5.10 的读接口一直显示"排队中"。`--no-demo-identity` 可禁用第二条路径。

进度是一行一条 `[wiki_build_worker] <UTC 时间戳> ...`。退出码：0 正常、1 意外错误、2 参数错误。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datamind import wiki
from datamind import wiki_build
from datamind import wiki_jobs

MARKER = '[wiki_build_worker]'
DEFAULT_INTERVAL_SECONDS = 2.0
MIN_INTERVAL_SECONDS = 0.1
WIKI_TABLES = ('wiki_entries', 'wiki_revisions', 'wiki_assertions', 'wiki_evidence_refs',
               'wiki_provenance_links', 'wiki_relations', 'wiki_review_events',
               'wiki_build_jobs')


def emit(message, stream=None):
    """一行带时间戳的进度（与 `ingestion/upload_worker.py:emit()` 同款）。"""
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    print(f'{MARKER} {stamp} {message}', file=stream or sys.stdout, flush=True)


def build_parser():
    parser = argparse.ArgumentParser(
        prog='python scripts/wiki_build_worker.py',
        description='Drive the wiki build queue (wiki_build_jobs) without an HTTP poller.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once', action='store_true',
                      help='处理当前可领取的任务（最多 --limit 条），然后退出。')
    mode.add_argument('--watch', action='store_true',
                      help='常驻轮询：一轮没有可领取的任务就睡 --interval 秒。')
    mode.add_argument('--check', action='store_true',
                      help='只做体检（连库、表行数、身份判定），不整理任何任务。')
    parser.add_argument('--limit', type=int, metavar='N',
                        help='一轮最多领取几个任务（--once 默认不限；--watch 默认 1）。')
    parser.add_argument('--interval', type=float, default=DEFAULT_INTERVAL_SECONDS,
                        metavar='SECONDS',
                        help=f'--watch 空闲时的睡眠秒数（默认 {DEFAULT_INTERVAL_SECONDS:g}）。')
    parser.add_argument('--build-id', metavar='wjob-…',
                        help='只处理指定的整理任务（排障用）。')
    parser.add_argument('--session-token', metavar='TOKEN',
                        help='任务 owner 的一个有效会话 token；不传则用 '
                             'DATAMIND_WIKI_WORKER_SESSION。')
    parser.add_argument('--no-demo-identity', action='store_true',
                        help='禁用本机回环演示身份：此时每个任务都必须由 --session-token 匹配。')
    parser.add_argument('--demo-identity', action='store_true',
                        help='强制启用本机回环演示身份（跳过地址探测；只在确实同机时用）。')
    return parser


def dsn_role(dsn):
    """从 DSN 里取 `user=` 段（只用于打印，不参与任何判定）。"""
    for part in str(dsn).split():
        if part.startswith('user='):
            return part.split('=', 1)[1]
    return 'unknown'


def first_queued(dsn):
    """队列里的第一条任务（只读；`FOR UPDATE SKIP LOCKED` 不阻塞任何写入事务）。"""
    sql, params = wiki_build.queued_sql(single=True)
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None


def select_identity(dsn, *, session_token, allow_demo):
    """选任务用的身份（`select_queued()` 需要 `is_admin` / `user_id_of`）。

    有 owner 可参考时用 owner 的身份；owner 的身份解析不出来（例如没有有效会话）也不在这里
    退出 —— 每个任务在 `execute_job()` 里各自判定，判不出来就落账成失败。
    """
    job = first_queued(dsn)
    if job is not None:
        try:
            return resolve_identity_for(job, session_token=session_token, allow_demo=allow_demo)
        except wiki_build.IdentityUnavailable:
            return wiki_build.job_identity(job)
    if allow_demo and wiki_build.demo_identity_ok():
        from datamind.auth import demo_identity
        return {**demo_identity(), '_source': 'demo'}
    return {'user_id': wiki_build.DEMO_OWNER_ID, '_source': 'placeholder'}


def resolve_identity_for(job, *, session_token, allow_demo):
    """读一次库，判定该任务用哪个身份开工（判不出来抛 `IdentityUnavailable`）。"""
    with wiki.connect(wiki_build.app_dsn()) as conn, \
            conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        return wiki_build.resolve_identity(cur, job, session_token=session_token,
                                           allow_demo=allow_demo)


def execute_job(job, *, session_token, allow_demo, in_flight=None, request_id=None):
    """领取 → 整理（`wiki_build.build_draft`）→ 交付；返回 `(outcome, terminal)`。

    * `outcome` 是 `execute_build()` 的返回值（`{'build': …, 'delivered': bool}`）或 None；
    * `terminal` 表示任务是否已落终态（`succeeded` / `failed`），供退出清理使用。

    本函数**不**自己写任务状态：状态推进与两次权限复查都在
    `wiki_jobs.claim_build()` / `complete_build()` 里（`wiki_jobs.py` 模块头 WHY 4）。
    `in_flight` 是"已尝试领取、但还没确认落终态"的 build_id 集合，退出时由
    `recover_in_flight()` 兜底。
    """
    dsn = wiki_build.app_dsn()
    build_id = job['build_id']
    if in_flight is not None:
        in_flight.add(build_id)
    try:
        identity = resolve_identity_for(job, session_token=session_token, allow_demo=allow_demo)
    except wiki_build.IdentityUnavailable as error:
        # 没有身份就不能开工，但也不能把任务永远留在 queued：落账成失败（best-effort）。
        wiki_jobs.fail_build(build_id, error.code, error.detail or str(error), dsn)
        emit(f'build={build_id} failed code={error.code} 身份不可用，未开工', stream=sys.stderr)
        return None, True

    builder = wiki_build.builder_for(dsn, identity, request_id=request_id)
    try:
        outcome = wiki_jobs.execute_build(build_id, dsn, identity, builder)
    except Exception as error:  # 领取阶段的数据库/锁异常
        code, fatal = wiki_build.claim_error_verdict(error)
        detail = wiki_build.failure_detail(error)
        if fatal:
            wiki_jobs.fail_build(build_id, code, detail, dsn)
            emit(f'build={build_id} claim failed code={code}（不可重试，已落账）',
                 stream=sys.stderr)
            return None, True
        emit(f'build={build_id} claim error={type(error).__name__} 保持 queued', stream=sys.stderr)
        return None, False

    build = outcome.get('build') or {}
    emit(' '.join(part for part in (
        f'build={build_id}', f"status={build.get('status')}",
        f"delivered={bool(outcome.get('delivered'))}",
        f"entry={build.get('result_entry_id')}",
        f"revision={build.get('result_revision_id')}",
        f"error={build.get('error_code')}", f"attempts={build.get('attempts')}") if part))
    return outcome, wiki_build.is_terminal(build)


def run_once(*, session_token, allow_demo, limit=None, build_id=None, in_flight=None,
             request_id=None):
    """跑一轮：处理当前可领取的任务。返回 `{'claimed': n, 'terminal': n, 'pending': n}`。"""
    dsn = wiki_build.app_dsn()
    identity = select_identity(dsn, session_token=session_token, allow_demo=allow_demo)
    jobs = wiki_build.select_queued(dsn, identity, limit=limit, build_id=build_id)
    counts = {'claimed': 0, 'terminal': 0, 'pending': 0}
    for job in jobs:
        _, terminal = execute_job(job, session_token=session_token, allow_demo=allow_demo,
                                  in_flight=in_flight, request_id=request_id)
        counts['claimed'] += 1
        counts['terminal' if terminal else 'pending'] += 1
    return counts


def recover_in_flight(build_ids):
    """退出清理：把"本进程领取过、但没落终态"的任务补写成失败，不留 `running` 僵尸。

    只在退出路径调用，而且只碰**任务行**（`wiki_jobs.fail_build()`：无权限门、best-effort）：
    它不删版本、不改问答侧的账。中断时这一行是唯一能解释"任务为什么停在这里"的证据。
    """
    if not build_ids:
        return
    dsn = wiki_build.app_dsn()
    for build_id in sorted(build_ids):
        try:
            with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
                cur.execute('SELECT status FROM wiki_build_jobs WHERE build_id = %s', (build_id,))
                row = cur.fetchone()
            if row is None or wiki_build.is_terminal(row):
                continue
            wiki_jobs.fail_build(
                build_id, wiki_build.ERROR_INTERRUPTED,
                'worker %s stopped while the job was %s'
                % (wiki_build.worker_name(), row['status']), dsn)
            emit(f'build={build_id} status={row["status"]} → failed '
                 f'code={wiki_build.ERROR_INTERRUPTED}（退出清理）', stream=sys.stderr)
        except Exception as error:  # 清理失败也不能掩盖退出码
            emit(f'build={build_id} 退出清理失败 error={type(error).__name__}', stream=sys.stderr)


def check(*, session_token=None, allow_demo=True):
    """体检：连接角色、8 张表的行数、队列长度、首个任务的身份判定。只发 SELECT。"""
    dsn = wiki_build.app_dsn()
    emit(f'check dsn_role={dsn_role(dsn)} worker={wiki_build.worker_name()}')
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        cur.execute('SELECT current_user AS role, current_database() AS database')
        row = cur.fetchone()
        emit(f"connected role={row['role']} database={row['database']}")
        for table in WIKI_TABLES:
            cur.execute('SELECT count(*) AS counted FROM %s' % table)
            emit(f"table={table} rows={cur.fetchone()['counted']}")
        sql, params = wiki_build.queued_sql()
        cur.execute(sql, tuple(params))
        queued = [dict(item) for item in cur.fetchall()]
        emit('queued=%d %s' % (len(queued), ' '.join(job['build_id'] for job in queued)))
        if queued:
            try:
                identity = wiki_build.resolve_identity(cur, queued[0], session_token=session_token,
                                                       allow_demo=allow_demo)
                emit('identity owner=%s source=%s roles=%s'
                     % (identity['user_id'], identity.get('_source'),
                        ','.join(identity.get('roles') or [])))
            except wiki_build.IdentityUnavailable as error:
                emit('identity unavailable: %s' % error, stream=sys.stderr)
    return wiki_build.EXIT_OK


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interval < MIN_INTERVAL_SECONDS:
        parser.error(f'--interval must be >= {MIN_INTERVAL_SECONDS:g}')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be >= 1')
    if args.demo_identity and args.no_demo_identity:
        parser.error('--demo-identity and --no-demo-identity are mutually exclusive')
    session_token = wiki_build.current_session_token(args.session_token)
    allow_demo = args.demo_identity or (not args.no_demo_identity and wiki_build.demo_identity_ok())

    if args.check:
        return check(session_token=session_token, allow_demo=allow_demo)

    stopping = threading.Event()
    in_flight = set()

    def on_signal(signum, _frame):
        if not stopping.is_set():
            stopping.set()
            emit(f'signal={signal.Signals(signum).name} 不打断已领取的任务，当前任务结束后退出')

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    emit('start mode=%s worker=%s dsn_role=%s limit=%s session_token=%s demo_identity=%s'
         % ('once' if args.once else 'watch', wiki_build.worker_name(),
            dsn_role(wiki_build.app_dsn()),
            args.limit if args.limit is not None else 'all',
            'yes' if session_token else 'no', allow_demo))
    try:
        if args.once:
            counts = run_once(session_token=session_token, allow_demo=allow_demo,
                              limit=args.limit, build_id=args.build_id, in_flight=in_flight)
            emit('done mode=once claimed=%(claimed)d terminal=%(terminal)d '
                 'pending=%(pending)d' % counts)
            return wiki_build.EXIT_OK
        limit = 1 if args.limit is None else args.limit
        while not stopping.is_set():
            counts = run_once(session_token=session_token, allow_demo=allow_demo, limit=limit,
                              build_id=args.build_id, in_flight=in_flight)
            if counts['claimed'] == 0:
                stopping.wait(args.interval)
        emit('done mode=watch 已收到停止信号')
        return wiki_build.EXIT_OK
    except KeyboardInterrupt:  # pragma: no cover - 防御；信号处理器先赢
        return wiki_build.EXIT_OK
    except Exception as error:
        emit(f'fatal error={type(error).__name__} {wiki_build.failure_detail(error)}',
             stream=sys.stderr)
        return wiki_build.EXIT_ERROR
    finally:
        recover_in_flight(in_flight)


if __name__ == '__main__':
    sys.exit(main())
