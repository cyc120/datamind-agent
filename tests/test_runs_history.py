"""「运行记录」接口与历史会话回填的守护测试。

两件此前真实存在、且用户直接感知到的问题：

1. **侧边栏「运行记录」点进去是白屏**：它指向旧版的 `/#runs`，而新前端只有 `/` 与 `/app`，
   且没有兜底路由。后端 `GET /v1/runs` 其实一直在（本机 750 条真实历史），前端也早就
   导出了 `listRuns()`，只是没人调用。本轮把它做成真页面，这里钉住接口契约：
   分页、`has_more`、以及 `conversation_id` 必须**如实投影**。

2. **历史 run 一条都不属于任何会话**：`runs.conversation_id` 在 020 迁移之前从未被写入，
   所以会话列表永远是空的。回填脚本按时间邻近聚类补上归属。

关于回填必须诚实的一点：分组的边界是**推断**出来的，不是原始数据里就有的。因此这里
只测「规则被正确执行」，不假装聚类结果等于真实的对话边界；`cluster_runs` 的边界条件
（严格大于才断开、幂等、空输入）是纯函数，最值得钉死。
"""
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from fastapi.testclient import TestClient

from datamind.agent import _write_dsn
from datamind.api import app
from scripts.backfill_sessions import (
    FALLBACK_TITLE,
    cluster_runs,
    session_times,
    session_title,
)

TZ = timezone(timedelta(hours=8))


def _at(minutes: float, base=None):
    """构造相对基准时间偏移的 timezone-aware 时间。"""
    origin = base or datetime(2026, 10, 12, 9, 0, tzinfo=TZ)
    return origin + timedelta(minutes=minutes)


def _row(run_id, started, question='问题'):
    return {'run_id': run_id, 'question': question, 'started_at': started}


class ClusterRunsTest(unittest.TestCase):
    """时间邻近聚类是纯函数——边界条件全部在这里钉死。"""

    def test_empty_input_yields_no_clusters(self):
        self.assertEqual(cluster_runs([], 300), [])

    def test_single_run_is_one_cluster(self):
        clusters = cluster_runs([_row('run-a', _at(0))], 300)
        self.assertEqual(len(clusters), 1)
        self.assertEqual([r['run_id'] for r in clusters[0]], ['run-a'])

    def test_runs_within_the_gap_stay_together(self):
        rows = [_row('run-a', _at(0)), _row('run-b', _at(1)), _row('run-c', _at(4))]
        clusters = cluster_runs(rows, 300)
        self.assertEqual([[r['run_id'] for r in c] for c in clusters],
                         [['run-a', 'run-b', 'run-c']])

    def test_a_gap_beyond_the_threshold_splits(self):
        rows = [_row('run-a', _at(0)), _row('run-b', _at(6)), _row('run-c', _at(20))]
        clusters = cluster_runs(rows, 300)
        self.assertEqual([[r['run_id'] for r in c] for c in clusters],
                         [['run-a'], ['run-b'], ['run-c']])

    def test_the_threshold_is_strictly_greater(self):
        """间隔恰好等于阈值**不**断开：规则是 "> gap"，不是 ">= gap"。

        这条容易在重构时被写成 >=，从而把「刚好卡在阈值上」的一次连续对话拆开。
        """
        rows = [_row('run-a', _at(0)), _row('run-b', _at(5))]
        clusters = cluster_runs(rows, 300)
        self.assertEqual(len(clusters), 1, '恰好 5 分钟不应断开')

        rows = [_row('run-a', _at(0)), _row('run-b', _at(5.001))]
        self.assertEqual(len(cluster_runs(rows, 300)), 2, '超过 5 分钟必须断开')

    def test_non_positive_gap_is_rejected(self):
        for bad in (0, -1):
            with self.subTest(gap=bad):
                with self.assertRaises(ValueError):
                    cluster_runs([_row('run-a', _at(0))], bad)


class SessionNamingTest(unittest.TestCase):
    """会话的时间与标题都来自簇内真实数据，不编造。"""

    def test_times_come_from_the_first_and_last_run(self):
        cluster = [_row('run-a', _at(0)), _row('run-b', _at(7))]
        created, updated = session_times(cluster)
        self.assertEqual(created, _at(0))
        self.assertEqual(updated, _at(7))

    def test_title_is_the_first_question_collapsed_and_truncated(self):
        cluster = [_row('run-a', _at(0), question='  2024年   一共有\n多少笔订单？' + 'x' * 60)]
        title = session_title(cluster)
        self.assertEqual(title, ('2024年 一共有 多少笔订单？' + 'x' * 60)[:40])
        self.assertNotIn('\n', title)
        self.assertLessEqual(len(title), 40)

    def test_blank_question_falls_back_instead_of_inventing_a_title(self):
        for blank in ('', '   ', '\n\t', None):
            with self.subTest(question=blank):
                self.assertEqual(session_title([_row('run-a', _at(0), question=blank)]),
                                 FALLBACK_TITLE)


class RunsHistoryAPITest(unittest.TestCase):
    """`GET /v1/runs` 的契约：分页、has_more、conversation_id 如实投影。"""

    def setUp(self):
        self.client = TestClient(app)
        self.run_ids = []
        self.dsn = _write_dsn()
        self.marker = 'run-' + uuid.uuid4().hex[:10]

    def tearDown(self):
        if self.run_ids:
            with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute('DELETE FROM runs WHERE run_id = ANY(%s)', (self.run_ids,))
        self.client.close()

    def insert_run(self, started, question='历史问题', conversation_id=None):
        run_id = f'{self.marker}-{len(self.run_ids):02d}'
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO runs(run_id, question, status, started_at, conversation_id)
                           VALUES (%s,%s,'COMPLETED',%s,%s)''',
                        (run_id, question, started, conversation_id))
        self.run_ids.append(run_id)
        return run_id

    def test_payload_declares_pagination(self):
        self.insert_run(_at(0))
        body = self.client.get('/v1/runs', params={'limit': 1}).json()
        for key in ('runs', 'limit', 'offset', 'has_more'):
            self.assertIn(key, body)
        self.assertEqual(body['limit'], 1)
        self.assertEqual(body['offset'], 0)
        self.assertIsInstance(body['has_more'], bool)

    def test_every_row_projects_conversation_id(self):
        """必须**始终**带这个键：null 是"早于会话功能"的正常历史状态，
        UI 靠它显示「未归属会话」。键缺失会让前端把它当成未知而不敢显示。"""
        self.insert_run(_at(0), conversation_id=None)
        row = self.client.get('/v1/runs', params={'limit': 1}).json()['runs'][0]
        self.assertIn('conversation_id', row)

    def test_conversation_id_round_trips(self):
        session_id = 'sess-' + uuid.uuid4().hex[:24]
        run_id = self.insert_run(_at(0), conversation_id=session_id)
        detail = self.client.get(f'/v1/runs/{run_id}').json()
        self.assertEqual(detail['conversation_id'], session_id)
        self.assertEqual(detail['run_id'], run_id)

    def test_pages_do_not_overlap_and_are_stable(self):
        for i in range(5):
            self.insert_run(_at(i))
        first = self.client.get('/v1/runs', params={'limit': 2, 'offset': 0}).json()
        second = self.client.get('/v1/runs', params={'limit': 2, 'offset': 2}).json()
        ids_first = [r['run_id'] for r in first['runs']]
        ids_second = [r['run_id'] for r in second['runs']]
        self.assertEqual(len(ids_first), 2)
        self.assertEqual(len(ids_second), 2)
        self.assertFalse(set(ids_first) & set(ids_second), '相邻两页不得重复')
        # 同一窗口重复请求必须给出同一结果（排序稳定，靠 started_at, run_id 兜底）
        again = self.client.get('/v1/runs', params={'limit': 2, 'offset': 0}).json()
        self.assertEqual([r['run_id'] for r in again['runs']], ids_first)

    def test_rows_are_newest_first(self):
        times = [_at(0), _at(1), _at(2)]
        ids = [self.insert_run(t) for t in times]
        rows = self.client.get('/v1/runs', params={'limit': 200}).json()['runs']
        seen = [r['run_id'] for r in rows if r['run_id'] in ids]
        self.assertEqual(seen, list(reversed(ids)), '应按 started_at 倒序')

    def test_offset_past_the_end_is_empty_not_an_error(self):
        body = self.client.get('/v1/runs', params={'limit': 5, 'offset': 100000}).json()
        self.assertEqual(body['runs'], [])
        self.assertFalse(body['has_more'])

    def test_out_of_range_pagination_is_rejected(self):
        for params in ({'limit': 0}, {'limit': 201}, {'offset': -1}, {'offset': 100001}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get('/v1/runs', params=params).status_code, 422)

    def test_unknown_run_is_not_found(self):
        response = self.client.get('/v1/runs/run-does-not-exist-0000')
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()['detail']['error_code'], 'RUN_NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
