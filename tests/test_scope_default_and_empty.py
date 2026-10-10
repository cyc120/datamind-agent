"""R4 scope contract: "no scope" (``None``) is the default scope, only an explicit ``[]`` denies.

Round 2 fixed H1 (an empty allow-list on the cross-source route must be refused, never turned
into "pick a source") by reading the **raw** request scope at the cross-source entry point. The
guard ``if not sources`` then refused ``None`` exactly like ``[]``, so a request that simply
carried no ``scope`` was rejected with ``SOURCE_NOT_ALLOWED`` before any evidence adapter ran.
That is the semantic error this file pins down:

* ``None``      -> the request did not narrow anything; it runs under the *default* scope
                   (``DATABASE_SOURCES`` / ``knowledge.COLLECTIONS``), and the source is inferred
                   from the question exactly like the SQL path.
* ``[]``        -> "no source / no collection is allowed": refused (H1). The refusal must stay.
* illegal value -> refused (H1/M2).

Scope problems and evidence problems keep distinct codes and must not be mixed:
``SOURCE_NOT_ALLOWED`` / ``COLLECTION_NOT_ALLOWED`` are *scope* verdicts;
``CROSS_SOURCE_EVIDENCE_INSUFFICIENT`` / ``DOC_BINDING_MISSING`` / ``CONFLICT_UNRESOLVED`` are
*evidence* verdicts produced by the graph engine after the scope was accepted.

The API-level cases run the shipped HTTP path; the agent-level case uses the same injected-evidence
seam the SQL/RAG routes already expose, so the default-scope case is provable without a model.
"""
from __future__ import annotations

import hashlib
import json
import unittest

import psycopg2
from fastapi.testclient import TestClient

from datamind import cross_source as cross_source_mod
from datamind.agent import DATABASE_SOURCES, DataMindAgent
from datamind.evidence_graph import canonical_json
from datamind.api import app
import auth_fixtures

WRITE = 'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'
CROSS_QUESTION = '查询销售额前三产品并结合市场分析报告说明原因'
SCOPE_2025 = {'start': '2025-01-01T00:00:00+08:00', 'end': '2026-01-01T00:00:00+08:00'}
SHA_A = 'a' * 64
SCOPE_ERROR_CODES = {'SOURCE_NOT_ALLOWED', 'COLLECTION_NOT_ALLOWED'}
EVIDENCE_ERROR_CODES = {cross_source_mod.CROSS_SOURCE_EVIDENCE_INSUFFICIENT,
                        cross_source_mod.DOC_BINDING_MISSING,
                        cross_source_mod.CONFLICT_UNRESOLVED}


def cross_evidence():
    """The completing fixture ``tests/test_security_round2.py`` uses, kept local and explicit."""
    rows = [{'category': '家用电器', 'metric': 'sales_amount', 'value': '128000.50',
             'unit': 'CNY', 'time_scope': SCOPE_2025}]
    blocks = [{'document_id': 'doc-channel', 'document_version': '2025-04',
               'source_sha256': SHA_A, 'chunk_id': 'doc-channel-c1',
               'title': 'doc-channel', 'entity_label': '家用电器',
               'text': '家用电器品类 2025 年渠道经验：线下门店转化率最高。'}]
    sql_evidence = {'source_ref': 'demo_retail', 'snapshot_id': 'retail-snapshot-v3', 'columns': [],
                    'sql_hash': hashlib.sha256(canonical_json(rows).encode()).hexdigest(),
                    'rows': rows}
    return {'sql': sql_evidence, 'docs': blocks}


class _RunCleanup(unittest.TestCase):
    """Every run this file creates is deleted again, so the clone is left as it was found."""

    def _delete_runs(self, run_ids):
        if not run_ids:
            return
        with psycopg2.connect(WRITE) as conn, conn.cursor() as cur:
            for run_id in run_ids:
                for table in ('trace_events', 'evidence_edges', 'evidence_nodes', 'claims', 'runs'):
                    cur.execute(f'DELETE FROM {table} WHERE run_id=%s', (run_id,))


class ScopeContractAPITest(_RunCleanup):
    """The four scope shapes at the shipped ``/v1/chat`` boundary (cross-source route)."""

    def setUp(self):
        self.runs = []
        self.client = TestClient(app)
        auth_fixtures.login(self.client)

    def tearDown(self):
        try:
            self._delete_runs(self.runs)
        finally:
            auth_fixtures.cleanup(self.client)
            self.client.close()

    def _chat(self, payload):
        response = self.client.post("/v1/chat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.runs.append(body["run_id"])
        print("scope_probe " + json.dumps(
            {"scope": payload.get("scope"), "status": body["status"],
             "error_code": body["error_code"]}, ensure_ascii=False))
        return body

    def test_no_scope_uses_the_default_scope(self):
        """Regression guard (R4): no scope must reach the evidence stage, not stop at the gate."""
        body = self._chat({"question": CROSS_QUESTION})
        self.assertNotIn(body["error_code"], SCOPE_ERROR_CODES,
                         "未显式给 scope 必须按默认范围处理，不得当成空 allow-list 拒绝")
        self.assertIn(body["error_code"], EVIDENCE_ERROR_CODES,
                      "范围放行后由证据图引擎给证据码（不猜答案）")
        self.assertIn(body["status"], {"FAILED_WITH_EVIDENCE", "ABSTAINED"})

    def test_explicit_empty_source_list_still_denies(self):
        body = self._chat({"question": CROSS_QUESTION, "scope": {"database_sources": []}})
        self.assertEqual(body["error_code"], "SOURCE_NOT_ALLOWED", "H1：显式空 allow-list 必须拒绝")
        self.assertEqual(body["status"], "FAILED_WITH_EVIDENCE")

    def test_an_unregistered_source_is_denied(self):
        body = self._chat({"question": CROSS_QUESTION, "scope": {"database_sources": ["nope"]}})
        self.assertEqual(body["error_code"], "SOURCE_NOT_ALLOWED")

    def test_a_registered_single_source_passes_the_gate(self):
        body = self._chat({"question": CROSS_QUESTION, "scope": {"database_sources": ["demo_retail"]}})
        self.assertNotIn(body["error_code"], SCOPE_ERROR_CODES,
                         "合法单源不得被范围门拒绝")

    def test_an_unregistered_collection_is_denied(self):
        body = self._chat({"question": CROSS_QUESTION,
                           "scope": {"database_sources": ["demo_retail"],
                                     "document_collections": ["public_kb"]}})
        self.assertEqual(body["error_code"], "COLLECTION_NOT_ALLOWED")

    def test_explicit_empty_collection_list_still_denies(self):
        body = self._chat({"question": CROSS_QUESTION,
                           "scope": {"database_sources": ["demo_retail"], "document_collections": []}})
        self.assertEqual(body["error_code"], "COLLECTION_NOT_ALLOWED",
                         "显式空集合 allow-list 与空数据源一样必须拒绝")


class ScopeContractAgentTest(_RunCleanup):
    """The same contract one level down, with injected evidence so the default case can complete."""

    def setUp(self):
        self.run_ids = []

    def tearDown(self):
        self._delete_runs(self.run_ids)

    def run_agent(self, **kwargs):
        agent = DataMindAgent()
        self.run_ids.append(agent.run_id)
        try:
            return agent.run(**kwargs)
        finally:
            agent.close()

    def test_no_scope_defaults_to_the_full_allow_list_and_completes(self):
        result = self.run_agent(question=CROSS_QUESTION, database_sources=None,
                                document_collections=None,
                                cross_source_evidence=cross_evidence())
        self.assertNotIn(result.get("error_code"), SCOPE_ERROR_CODES)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["cross_source_result"]["status"], "completed")
        self.assertEqual(list(DATABASE_SOURCES),
                         ["demo_retail", "chinook", "demo_industrial"],
                         "默认范围就是这三个数据源，顺序即单源推断的优先级")

    def test_no_scope_with_history_still_passes_the_frame_scope_check(self):
        """The ``frame.scope_check`` half of the same bug.

        A history-bearing request consults ``resolve_turn`` first; with no ``scope`` the frame's
        allow-lists are the defaults, so an inherited in-scope proposal must pass and the turn must
        reach the cross-source engine instead of stopping at a scope gate.
        """
        history = [{"role": "user", "content": "demo_retail 2025年家用电器销售额是多少？"},
                   {"role": "assistant", "content": "查询结果：128000.50。"}]
        result = self.run_agent(question=CROSS_QUESTION, database_sources=None,
                                document_collections=None, history=history,
                                cross_source_evidence=cross_evidence())
        self.assertNotIn(result.get("error_code"), SCOPE_ERROR_CODES,
                         "无 scope 时 frame 的范围门必须用默认范围，不得当成空 allow-list")
        self.assertEqual(result["status"], "COMPLETED", result.get("error_code"))

    def test_empty_allow_list_is_still_refused_with_evidence_injected(self):
        refused = self.run_agent(question=CROSS_QUESTION, database_sources=[],
                                 document_collections=['demo_kb'],
                                 cross_source_evidence=cross_evidence())
        self.assertEqual((refused["status"], refused["error_code"]),
                         ("FAILED_WITH_EVIDENCE", "SOURCE_NOT_ALLOWED"))
        self.assertNotIn("cross_source_result", refused,
                         "范围被拒时不得留下任何证据结果（不猜答案）")


if __name__ == "__main__":
    unittest.main()
