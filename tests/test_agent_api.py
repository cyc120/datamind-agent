"""Agent/API integration: persisted execution, replay and scoped routing."""
import json
import unittest
from unittest.mock import patch

import psycopg2
from fastapi.testclient import TestClient

from datamind.agent import SQLAgent
from datamind.api import app
from datamind.sql_engine import ask as real_ask


GOLD = "SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'"


def seeded_ask(question, source_id, *, generator=None, run_id=None):
    return real_ask(question, source_id, generator=lambda *_: {"sql": GOLD}, run_id=run_id)


class AgentAPITest(unittest.TestCase):
    def setUp(self):
        self.runs = []
        self.client = TestClient(app)

    def tearDown(self):
        if not self.runs:
            return
        with psycopg2.connect("host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev") as conn, conn.cursor() as cur:
            for run_id in self.runs:
                cur.execute("DELETE FROM evidence_edges WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM evidence_nodes WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM claims WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
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
        self.assertEqual(evidence["claims"][0]["status"], "verified")
        self.assertEqual(evidence["nodes"][0]["run_id"], agent.run_id)
        self.assertEqual(len(evidence["edges"]), 2)

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

    def test_document_route_is_explicitly_unavailable(self):
        response = self.client.post("/v1/chat", json={"question": "结合市场分析报告说明原因"})
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
