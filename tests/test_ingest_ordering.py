# -*- coding: utf-8 -*-
"""F10 摄取跨存储一致性（outbox-lite 顺序协议）单元测试。

只驱动 `ingestion/ingest.py` 抽出的 `publish_document`：Qdrant 客户端、DB 连接/
游标全部是内存假件（unittest.mock / 普通假类），**不连真实 Qdrant，也不连真实
PostgreSQL**，因此可以安全地在任何环境跑。

被断言的协议（复审建议的顺序）：
1. 把 `document_assets.status` 置为不可检索状态（NON_SEARCHABLE_STATUS）并
   **立即提交**，这次提交必须发生在**第一次 Qdrant 调用之前**；
2. 之后才 delete + upsert Qdrant；
3. 最后写 PG 元数据并把状态置 'indexed'（无切片则 'failed'）后提交，这次提交
   必须发生在 Qdrant 调用**之后**；
4. 第 2/3 步任何异常向上传播，全程不出现把状态置成 'indexed' 的提交。
"""
import inspect
import unittest
from unittest import mock

from ingestion import ingest


class Order:
    """按发生顺序记录 execute / commit / Qdrant / 元数据写入。"""

    def __init__(self):
        self.entries = []          # [(seq, kind, payload)]
        self._seq = 0

    def add(self, kind, payload=None):
        self._seq += 1
        self.entries.append((self._seq, kind, payload))
        return self._seq

    def seqs(self, kind):
        return [seq for seq, entry_kind, _ in self.entries if entry_kind == kind]

    def first(self, kind):
        found = self.seqs(kind)
        return found[0] if found else None

    def last(self, kind):
        found = self.seqs(kind)
        return found[-1] if found else None

    def first_qdrant(self):
        found = [seq for seq, entry_kind, _ in self.entries if entry_kind.startswith("qdrant_")]
        return min(found) if found else None

    def last_qdrant(self):
        found = [seq for seq, entry_kind, _ in self.entries if entry_kind.startswith("qdrant_")]
        return max(found) if found else None

    def document_asset_updates(self):
        """[(seq, status, params)]，只取改 document_assets 状态的语句。"""
        out = []
        for seq, kind, payload in self.entries:
            if kind != "pg_execute":
                continue
            sql, params = payload
            if "UPDATE document_assets" in sql and params:
                out.append((seq, params[0], params))
        return out

    def committed_statuses(self):
        """每次 commit 时，该事务里最近一次 document_assets 状态更新的 status。"""
        out, pending = [], None
        for seq, kind, payload in self.entries:
            if kind == "pg_execute":
                sql, params = payload
                if "UPDATE document_assets" in sql and params:
                    pending = params[0]
            elif kind == "commit":
                out.append(pending)
        return out

    def commit_seq_for_status(self, status):
        """第一次"提交时可见 status == status"的 commit 序号。"""
        pending = None
        for seq, kind, payload in self.entries:
            if kind == "pg_execute":
                sql, params = payload
                if "UPDATE document_assets" in sql and params:
                    pending = params[0]
            elif kind == "commit" and pending == status:
                return seq
        return None


class FakeCursor:
    def __init__(self, order, *, fail_on=None):
        self.order = order
        self.fail_on = fail_on or (lambda sql: False)
        self.executed = []          # [(sql, params)]

    def execute(self, sql, params=None):
        self.order.add("pg_execute", (sql, params))
        self.executed.append((sql, params))
        if self.fail_on(sql):
            raise RuntimeError("PG_EXECUTE_FAILED")


class FakeConnection:
    """假连接：可选地让第 N 次 commit 失败。

    失败的 commit 什么也没提交，因此记成 'commit_failed'，不计入已提交状态。
    """

    def __init__(self, order, *, fail_at=None):
        self.order = order
        self.fail_at = fail_at
        self.commits = 0

    def commit(self):
        self.commits += 1
        if self.fail_at == self.commits:
            self.order.add("commit_failed")
            raise RuntimeError("PG_COMMIT_FAILED")
        self.order.add("commit")


class FakeQdrant:
    """记录调用顺序的假向量库；可选地在 delete / upsert 上抛错。"""

    def __init__(self, order, *, fail=None):
        self.order = order
        self.fail = fail
        self.deleted = []
        self.upserted = []

    def delete(self, collection, points_selector=None, wait=None):
        self.order.add("qdrant_delete", collection)
        if self.fail == "delete":
            raise RuntimeError("QDRANT_DELETE_FAILED")
        self.deleted.append((collection, points_selector, wait))

    def upsert(self, collection, points, **kwargs):
        self.order.add("qdrant_upsert", list(points))
        if self.fail == "upsert":
            raise RuntimeError("QDRANT_UPSERT_FAILED")
        self.upserted.append((collection, list(points)))


class Harness:
    """把 publish_document 的全部外部依赖换成内存假件。"""

    def __init__(self, *, qdrant_fail=None, metadata_error=None, chunk_count=3,
                 commit_fail_at=None):
        self.order = Order()
        self.cur = FakeCursor(self.order)
        self.conn = FakeConnection(self.order, fail_at=commit_fail_at)
        self.qc = FakeQdrant(self.order, fail=qdrant_fail)
        self.chunk_count = chunk_count
        self.metadata_error = metadata_error
        self.metadata_calls = 0
        self.points = ["point-a", "point-b"]

    def write_metadata(self):
        self.metadata_calls += 1
        self.order.add("pg_metadata")
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.chunk_count

    def publish(self):
        return ingest.publish_document(
            self.cur, self.qc, "doc-1", ingest.COLLECTION, list(self.points),
            self.write_metadata, commit=self.conn.commit, page_count=7,
            quality_score=None, quality_detail={"k": "v"})


class IngestOrderingTests(unittest.TestCase):
    def test_non_searchable_commit_happens_before_first_qdrant_call(self):
        h = Harness()
        h.publish()

        mark_seq = h.order.commit_seq_for_status(ingest.NON_SEARCHABLE_STATUS)
        self.assertIsNotNone(mark_seq, "必须先把状态置为不可检索并提交")
        first_qdrant = h.order.first_qdrant()
        self.assertIsNotNone(first_qdrant)
        self.assertLess(mark_seq, first_qdrant,
                        "非检索状态的提交必须发生在第一次 Qdrant 调用之前")

        indexed_seq = h.order.commit_seq_for_status(ingest.SEARCHABLE_STATUS)
        self.assertIsNotNone(indexed_seq, "正常路径最终必须提交 'indexed'")
        self.assertGreater(indexed_seq, h.order.last_qdrant(),
                           "'indexed' 的提交必须发生在 Qdrant 调用之后")
        self.assertGreater(indexed_seq, h.order.first("pg_metadata"),
                           "'indexed' 的提交必须发生在 PG 元数据写入之后")

    def test_normal_path_final_committed_status_is_indexed(self):
        h = Harness(chunk_count=3)
        returned = h.publish()

        self.assertEqual(returned, 3)
        self.assertEqual(h.order.committed_statuses(),
                         [ingest.NON_SEARCHABLE_STATUS, ingest.SEARCHABLE_STATUS])
        _, status, params = h.order.document_asset_updates()[-1]
        self.assertEqual(status, ingest.SEARCHABLE_STATUS)
        self.assertFalse(params[1], "有切片时 needs_review 应为 False")
        self.assertEqual(h.metadata_calls, 1)
        self.assertEqual(len(h.qc.deleted), 1, "应有一次 Qdrant delete")
        self.assertEqual(h.qc.upserted, [(ingest.COLLECTION, h.points)])

    def test_qdrant_delete_failure_never_commits_indexed(self):
        h = Harness(qdrant_fail="delete")
        with self.assertRaises(RuntimeError):
            h.publish()

        self.assertEqual(h.order.committed_statuses(), [ingest.NON_SEARCHABLE_STATUS])
        self.assertEqual(h.metadata_calls, 0, "Qdrant 失败时不应写 PG 元数据")
        executed_statuses = [status for _, status, _ in h.order.document_asset_updates()]
        self.assertNotIn(ingest.SEARCHABLE_STATUS, executed_statuses)

    def test_qdrant_upsert_failure_never_commits_indexed(self):
        h = Harness(qdrant_fail="upsert")
        with self.assertRaises(RuntimeError):
            h.publish()

        self.assertEqual(h.order.committed_statuses(), [ingest.NON_SEARCHABLE_STATUS])
        self.assertEqual(h.metadata_calls, 0)
        self.assertNotIn(ingest.SEARCHABLE_STATUS,
                         [status for _, status, _ in h.order.document_asset_updates()])

    def test_metadata_write_failure_never_commits_indexed(self):
        h = Harness(metadata_error=RuntimeError("PG_WRITE_FAILED"))
        with self.assertRaises(RuntimeError):
            h.publish()

        self.assertEqual(h.metadata_calls, 1)
        self.assertEqual(h.order.committed_statuses(), [ingest.NON_SEARCHABLE_STATUS])
        self.assertNotIn(ingest.SEARCHABLE_STATUS,
                         [status for _, status, _ in h.order.document_asset_updates()])

    def test_terminal_commit_failure_leaves_document_non_searchable(self):
        # 正是 F10 描述的场景：Qdrant 写成功，随后 PG 提交失败。
        # 此时 Qdrant 里是新向量（孤儿），但 PG 收敛在不可检索状态，
        # 不会出现"检索拿新向量配旧文本"。
        h = Harness(commit_fail_at=2)
        with self.assertRaises(RuntimeError):
            h.publish()

        self.assertEqual(h.metadata_calls, 1, "终态提交前元数据确实写过")
        self.assertEqual(h.order.committed_statuses(), [ingest.NON_SEARCHABLE_STATUS])
        self.assertEqual(h.order.entries[-1][1], "commit_failed")
        self.assertEqual(len(h.qc.deleted), 1)
        self.assertEqual(len(h.qc.upserted), 1)

    def test_non_searchable_commit_failure_never_touches_qdrant(self):
        h = Harness(commit_fail_at=1)
        with self.assertRaises(RuntimeError):
            h.publish()

        self.assertEqual(h.order.committed_statuses(), [])
        self.assertIsNone(h.order.first_qdrant(), "非检索状态没提交成功就不该动 Qdrant")
        self.assertEqual(h.metadata_calls, 0)

    def test_no_chunks_commits_failed_not_indexed(self):
        h = Harness(chunk_count=0)
        returned = h.publish()

        self.assertEqual(returned, 0)
        self.assertEqual(h.order.committed_statuses(),
                         [ingest.NON_SEARCHABLE_STATUS, ingest.FAILED_STATUS])
        _, status, params = h.order.document_asset_updates()[-1]
        self.assertEqual(status, ingest.FAILED_STATUS)
        self.assertTrue(params[1], "无切片时 needs_review 应为 True")

    def test_protocol_never_constructs_a_real_qdrant_client(self):
        # publish_document 从参数取客户端，本测试把类本身换成会炸的桩来证明这一点。
        with mock.patch.object(ingest, "QdrantClient",
                               side_effect=AssertionError("must not construct a real Qdrant client")):
            h = Harness()
            self.assertEqual(h.publish(), 3)

    def test_extracted_status_values_are_reexisting_non_indexed(self):
        # 依赖既有取值范围，而不是臆造新状态：'parsing' 是本文件原 INSERT 已写的值。
        self.assertEqual(ingest.NON_SEARCHABLE_STATUS, "parsing")
        self.assertNotEqual(ingest.NON_SEARCHABLE_STATUS, ingest.SEARCHABLE_STATUS)
        source = inspect.getsource(ingest)
        self.assertIn("'parsing'", source)

    def test_skip_indexed_branch_still_precedes_the_f10_mark(self):
        # main() 不易离线驱动（要连 PG/Qdrant/嵌入模型），这里用结构守卫锁住
        # "先跳过已 indexed 文档、再写不可检索状态" 的顺序不被 F10 改动打乱。
        source = inspect.getsource(ingest.main)
        self.assertLess(source.index("skip (indexed)"), source.index("F10 第 1 步"),
                        "跳过已 indexed 文档的分支必须仍先于 F10 的非检索状态提交")


if __name__ == "__main__":
    unittest.main()
