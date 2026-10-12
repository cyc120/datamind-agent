#!/usr/bin/env python
"""把**会话功能上线之前**的历史 run 归属到会话里（按时间邻近聚类）。

背景（为什么需要这个脚本）
--------------------------
`runs.conversation_id` 这一列在 020 迁移之前从未被写入过，所以本机 700+ 条历史问答
全部是 `conversation_id IS NULL`。会话列表只显示真的属于某个会话的 run，于是历史
一条都不会出现——用户看到的是「还没有会话」。

这个脚本按**时间邻近**把历史 run 聚成会话：相邻两条 run 间隔超过 ``--gap-minutes``
就断开。必须诚实声明：**分组的边界是推断出来的，不是原始数据里就有的**。真实数据
只告诉我们"谁在什么时间问了什么"，没有告诉我们"哪几句属于同一次对话"。因此：

* 只在 ``conversation_id IS NULL`` 的 run 上工作——已经有归属的 run 绝不被重写；
* 可重复执行：第二次运行会发现没有可归属的 run，什么也不做（幂等）；
* 默认先 ``--dry-run`` 看形状，确认后再真跑。

局限性（如实记录，不粉饰）
--------------------------
自动化测试会以秒级间隔连续发问，因此无论阈值多小，都会聚出「几百轮」的巨型会话。
本机实测：2 分钟阈值下最大仍有 219 轮。这不是阈值调参能解决的，是数据本身的性质——
那些 run 是人/代理脚本刷出来的，不是一次真实对话。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind.sessions import _auto_title, _session_id  # noqa: E402  复用同一套 id/标题规则

DEFAULT_OWNER = 'user-local-admin'
DEFAULT_GAP_MINUTES = 5
FALLBACK_TITLE = '历史会话'


def _write_dsn() -> str:
    return (os.environ.get('DATAMIND_META_WRITE_DSN') or os.environ.get('DATAMIND_WRITE_DSN')
            or 'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev')


def cluster_runs(rows, gap_seconds):
    """把按时间升序的 run 行聚成会话（纯函数，便于单测）。

    ``rows`` 每项需含 ``started_at``（timezone-aware datetime）。相邻两条间隔
    **严格大于** ``gap_seconds`` 时开启新会话。返回 ``list[list[dict]]``。
    """
    if gap_seconds <= 0:
        raise ValueError('gap_seconds must be > 0')
    clusters, current, previous = [], [], None
    for row in rows:
        started = row['started_at']
        if previous is not None and (started - previous).total_seconds() > gap_seconds:
            clusters.append(current)
            current = []
        current.append(row)
        previous = started
    if current:
        clusters.append(current)
    return clusters


def session_times(cluster):
    """会话的 created_at/updated_at = 该簇首/末一条 run 的时间（不编造"现在"）。"""
    return cluster[0]['started_at'], cluster[-1]['started_at']


def session_title(cluster):
    """用簇内第一条问题命名；问题为空时回落到固定文案（不编造标题）。"""
    return _auto_title(cluster[0].get('question')) or FALLBACK_TITLE


def load_orphan_runs(cur, since=None):
    sql = ('SELECT run_id, question, started_at FROM runs '
           'WHERE conversation_id IS NULL')
    params = []
    if since is not None:
        sql += ' AND started_at >= %s'
        params.append(since)
    sql += ' ORDER BY started_at ASC, run_id ASC'
    cur.execute(sql, tuple(params))
    return [dict(r) for r in cur.fetchall()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gap-minutes', type=float, default=DEFAULT_GAP_MINUTES,
                        help=f'相邻 run 间隔超过该值即断开为新会话（默认 {DEFAULT_GAP_MINUTES}）')
    parser.add_argument('--owner', default=DEFAULT_OWNER,
                        help=f'会话归属的 user_id（默认 {DEFAULT_OWNER}）')
    parser.add_argument('--since', default=None,
                        help='只回填该时间之后的历史 run，ISO 格式如 2026-10-09T00:00:00+08:00')
    parser.add_argument('--dry-run', action='store_true', help='只报告将要做什么，不写库')
    args = parser.parse_args(argv)

    since = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since)
        except ValueError:
            raise SystemExit(f'--since 不是合法的 ISO 时间：{args.since}')
        if since.tzinfo is None:
            raise SystemExit('--since 必须带时区（例如 2026-10-09T00:00:00+08:00），'
                             '否则与 TIMESTAMPTZ 的比较含义不明')

    gap_seconds = args.gap_minutes * 60
    dsn = _write_dsn()

    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT user_id FROM auth_users WHERE user_id=%s', (args.owner,))
        if not cur.fetchone():
            raise SystemExit(f'归属用户不存在：{args.owner}（chat_sessions.user_id 有外键约束）')

        rows = load_orphan_runs(cur, since=since)
        if not rows:
            print('没有待归属的 run（conversation_id 全部已有值）。幂等：本次不做任何改动。')
            return 0

        clusters = cluster_runs(rows, gap_seconds)
        sizes = sorted((len(c) for c in clusters), reverse=True)
        print(f'待归属 run：{len(rows)} 条')
        print(f'时间邻近聚类（>{args.gap_minutes:g} 分钟断开）：{len(clusters)} 个会话')
        print(f'每会话轮次：最大 {sizes[0]}、中位 {sizes[len(sizes)//2]}、'
              f'单轮会话 {sum(1 for s in sizes if s == 1)} 个')
        print(f'归属用户：{args.owner}')
        print(f'时间范围：{rows[0]["started_at"].isoformat()} ~ {rows[-1]["started_at"].isoformat()}')
        print()
        print('前 5 个会话：')
        for cluster in clusters[:5]:
            created, updated = session_times(cluster)
            print(f'  {len(cluster):>4} 轮  {created:%m-%d %H:%M}~{updated:%H:%M}  '
                  f'{session_title(cluster)}')

        if args.dry_run:
            print('\n--dry-run：未写入任何内容。')
            return 0

        for cluster in clusters:
            session_id = _session_id()
            created, updated = session_times(cluster)
            cur.execute('''INSERT INTO chat_sessions(session_id,user_id,title,created_at,updated_at)
                           VALUES (%s,%s,%s,%s,%s)''',
                        (session_id, args.owner, session_title(cluster), created, updated))
            cur.execute('UPDATE runs SET conversation_id=%s WHERE run_id = ANY(%s)',
                        (session_id, [r['run_id'] for r in cluster]))

    print(f'\n完成：新建 {len(clusters)} 个会话，归属 {len(rows)} 条 run。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
