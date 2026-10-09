"""Agent/API integration: persisted execution, replay and scoped routing."""
import json
import unittest
from unittest.mock import patch

import psycopg2
from fastapi.testclient import TestClient

from datamind.agent import SQLAgent
from datamind.api import app
from datamind.sql_engine import ask as real_ask
import auth_fixtures


GOLD = "SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'"


def seeded_ask(question, source_id, *, generator=None, run_id=None):
    return real_ask(question, source_id, generator=lambda *_: {"sql": GOLD}, run_id=run_id)


class AgentAPITest(unittest.TestCase):
    def setUp(self):
        self.runs = []
        # /v1/chat, /v1/chat/stream and /v1/diagnostics/sql are mutating routes now covered by
        # the default-deny identity middleware (identity + CSRF + origin), so this client can no
        # longer rely on TestClient's host ("testclient", not loopback) being granted demo admin.
        # A disposable session carries the CSRF token every mutation must present.
        self.client = TestClient(app)
        auth_fixtures.login(self.client)

    def tearDown(self):
        try:
            if not self.runs:
                return
            with psycopg2.connect("host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev") as conn, conn.cursor() as cur:
                for run_id in self.runs:
                    cur.execute("DELETE FROM evidence_edges WHERE run_id=%s", (run_id,))
                    cur.execute("DELETE FROM evidence_nodes WHERE run_id=%s", (run_id,))
                    cur.execute("DELETE FROM claims WHERE run_id=%s", (run_id,))
                    cur.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        finally:
            # Remove the fixture account even when a test failed before registering a run.
            auth_fixtures.cleanup(self.client)
            self.client.close()

    def test_agent_persists_verified_sql_evidence(self):
        agent = SQLAgent()
        self.runs.append(agent.run_id)
        try:
            result = agent.run("2024年一共有多少笔订单？", database_sources=["demo_retail"],
                               generator=lambda *_: {"sql": GOLD})
        finally:
            agent.close()
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["sql_result"]["rows"], [[997]])
        run = self.client.get(f"/v1/runs/{agent.run_id}").json()
        self.assertEqual(run["status"], "COMPLETED")
        first = self.client.get(f"/v1/runs/{agent.run_id}/events").json()
        self.assertEqual(first["events"][-1]["event_type"], "answer.completed")
        after = first["events"][2]["event_id"]
        later = self.client.get(f"/v1/runs/{agent.run_id}/events", params={"after": after}).json()
        self.assertTrue(all(e["event_id"] > after for e in later["events"]))
        evidence = self.client.get(f"/v1/runs/{agent.run_id}/evidence").json()
        # 「有多少笔订单」不是注册指标问题，当前只做了时间范围规范化（`time_scope_only`），
        # 因此**不得**标为 verified：执行成功不等于语义已验证。答案照常返回（COMPLETED），
        # 但 claim 与 confidence 如实降级。强契约（销售额指标）另见下一个用例。
        self.assertEqual(evidence["claims"][0]["status"], "insufficient_evidence")
        self.assertEqual(evidence["claims"][0]["confidence_level"], "insufficient")
        verification = evidence["claims"][0]["verification"]
        self.assertFalse(verification["semantically_verified"])
        self.assertEqual(verification["verification_contract"], "time_scope_only")
        self.assertTrue(any(str(reason).startswith("SEMANTIC_CONTRACT_WEAK")
                            for reason in verification["verification_reasons"]))
        self.assertEqual(evidence["nodes"][0]["run_id"], agent.run_id)
        self.assertEqual(len(evidence["edges"]), 2)

    def test_a_registered_metric_question_is_verified_under_the_strong_contract(self):
        """正向对照：销售额问题走完整指标 AST 契约，仍必须标为 verified。

        没有这条，上面的降级就可能被"一律标 insufficient"伪修掉。
        """
        sales_sql = ("SELECT COALESCE(SUM(oi.quantity * oi.unit_price * (1 - oi.discount)), 0) AS amt "
                     "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
                     "WHERE o.created_at >= '2024-01-01' AND o.created_at < '2025-01-01' "
                     "AND o.order_status IN ('completed','shipped','refunded')")
        ask_result = real_ask("2024年销售额是多少？", "demo_retail",
                              generator=lambda *_: {"sql": sales_sql})
        self.assertEqual(ask_result["status"], "completed")
        self.assertTrue(ask_result["semantically_verified"])
        self.assertEqual(ask_result["verification_contract"], "retail_metric_ast_contract")
        self.assertFalse(ask_result["verification_reasons"])

    def test_stream_and_scope(self):
        with patch("datamind.agent.ask", side_effect=seeded_ask):
            response = self.client.post("/v1/chat/stream", json={"question": "2024年一共有多少笔订单？",
                                                                  "scope": {"database_sources": ["demo_retail"]}})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.runs.append(events[0]["run_id"])
        self.assertEqual(events[-1]["event_type"], "answer.completed")
        self.assertEqual(events[-1]["output_summary"]["sql_result"]["rows"], [[997]])
        response = self.client.post("/v1/chat", json={"question": "2024年一共有多少笔订单？",
                                                       "scope": {"database_sources": []}})
        self.runs.append(response.json()["run_id"])
        self.assertEqual(response.json()["error_code"], "SOURCE_NOT_ALLOWED")

    def test_cross_source_route_is_explicitly_unavailable(self):
        response = self.client.post("/v1/chat", json={"question": "查询销售额前三产品并结合市场分析报告说明原因"})
        self.runs.append(response.json()["run_id"])
        self.assertEqual(response.json()["error_code"], "ROUTE_UNSUPPORTED")

    def test_fixed_diagnostic_uses_real_database(self):
        response = self.client.post("/v1/diagnostics/sql")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.runs.append(body["run_id"])
        self.assertEqual(body["status"], "COMPLETED")
        self.assertEqual(body["sql_result"]["rows"], [[997]])


if __name__ == "__main__":
    unittest.main()
