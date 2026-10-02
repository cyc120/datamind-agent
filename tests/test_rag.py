"""Citation integrity, public/version boundaries and persisted document answers."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import psycopg2
from fastapi.testclient import TestClient

from datamind.agent import DataMindAgent, route_for
from datamind.api import app
from datamind.knowledge import KnowledgeRetriever, select_documents
from datamind.rag_engine import ask_documents, verify_claims
from ingestion.ingest import build_chunks


WRITE_DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev"
TEXT = "蜃楼测试工艺要求温度保持在 42 度。"


class CitationTest(unittest.TestCase):
    def setUp(self):
        self.hit = {"chunk_id": "c1", "title": "工艺规范", "exact_text": TEXT}
        self.draft = {"claims": [{"text": "蜃楼测试工艺要求温度保持在42度。",
                       "citations": [{"chunk_id": "c1", "quote": TEXT}]}]}

    def test_quote_and_numeric_support(self):
        result = verify_claims(self.draft, [self.hit])
        self.assertTrue(result[0]["verification"]["excerpt_matches"])
        self.assertEqual(result[0]["verification"]["semantic_entailment"], "not_independently_verified")
        self.draft["claims"][0]["text"] = "要求温度保持在43度。"
        with self.assertRaisesRegex(ValueError, "RAG_UNSUPPORTED_NUMBER"):
            verify_claims(self.draft, [self.hit])

    def test_fabricated_excerpt_and_unknown_source_are_blocked(self):
        self.draft["claims"][0]["citations"][0]["quote"] = "原文要求温度保持在42度。"
        with self.assertRaisesRegex(ValueError, "RAG_EXCERPT_MISMATCH"):
            verify_claims(self.draft, [self.hit])
        self.draft["claims"][0]["citations"][0]["chunk_id"] = "private-chunk"
        with self.assertRaisesRegex(ValueError, "RAG_UNKNOWN_CITATION"):
            verify_claims(self.draft, [self.hit])

    def test_source_pages_and_block_refs_survive_long_text_splitting(self):
        blocks = [{"block_id": "b1", "page_no": 2, "block_type": "paragraph", "text": "甲乙丙丁" * 500},
                  {"block_id": "b2", "page_no": 3, "block_type": "paragraph", "text": "第二页独立的证据正文"}]
        chunks = build_chunks(blocks, "test-doc")
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(chunk["page_start"] == chunk["page_end"] for chunk in chunks))
        self.assertTrue(all(chunk["block_ids"] for chunk in chunks))
        self.assertEqual(chunks[-1]["block_ids"], ["b2"])
        self.assertEqual(chunks[-1]["page_start"], 3)

    def test_auto_route_selects_document_intents_and_blocks_cross_source(self):
        for question in ("退款多久到账？", "门店巡检哪家得分最低？", "渠道年度预算是多少？"):
            self.assertEqual(route_for(question), "rag")
        self.assertEqual(route_for("2024年多少笔订单？"), "sql")
        self.assertEqual(route_for("查询销售额前三产品并结合市场报告说明原因"), "cross_source")

    def test_explicit_document_names_limit_retrieval(self):
        rows = [{"title": "[ohr-005/OHR-Bench/finance] finance sample"},
                {"title": "[ohr-006/OHR-Bench/finance] another sample"},
                {"title": "03_2025年分区域经营数据简报"}]
        self.assertEqual(select_documents("According to ohr-005", rows), rows[:1])
        self.assertEqual(select_documents("According to ohr-099", rows), [])
        self.assertEqual(select_documents("根据《2025年分区域经营数据简报》", rows), rows[2:])


class RAGIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:12]
        self.docs, self.runs = [], []
        self.client = TestClient(app)
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            for kind in ("public", "private", "stale", "tampered", "review"):
                doc = f"test-doc-{self.tag}-{kind}"
                self.docs.append(doc)
                cur.execute("""INSERT INTO document_assets(document_id,title,collection,file_type,origin,
                               sha256,object_uri,doc_version,acl,status,needs_review,page_count)
                               VALUES (%s,'蜃楼测试工艺','demo_kb','pdf','curated',%s,'/etc/passwd',%s,%s,'indexed',%s,1)""",
                            (doc, hashlib.sha256(doc.encode()).hexdigest(), 2 if kind == "stale" else 1,
                             ["private"] if kind == "private" else ["public"], kind == "review"))
                cur.execute("""INSERT INTO chunks(chunk_id,document_id,chunk_index,text_for_embedding,exact_text,
                               embedding_model,content_hash,page_start,page_end,acl)
                               VALUES (%s,%s,0,%s,%s,'test',%s,1,1,'{public}')""",
                            (doc + "-chunk", doc, TEXT, TEXT,
                             "0" * 64 if kind == "tampered" else hashlib.sha256(TEXT.encode()).hexdigest()))

    def tearDown(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            for run_id in self.runs:
                cur.execute("DELETE FROM evidence_edges WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM evidence_nodes WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM claims WHERE run_id=%s", (run_id,))
                cur.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
            cur.execute("DELETE FROM document_assets WHERE document_id=ANY(%s)", (self.docs,))
        self.client.close()

    def retrieve(self):
        return KnowledgeRetriever(mode="lexical")

    def generator(self, question, hits, feedback):
        return {"abstain": False, "claims": [{"text": TEXT, "citations": [{
                "chunk_id": next(hit["chunk_id"] for hit in hits if hit["exact_text"] == TEXT), "quote": TEXT}]}]}

    def test_private_stale_tampered_and_review_chunks_cannot_enter_context(self):
        retriever = KnowledgeRetriever(mode="hybrid")
        with patch.object(retriever, "_dense", return_value=[(doc + "-chunk", 0.99) for doc in self.docs]):
            result = retriever.retrieve("蜃楼测试工艺", collections=["demo_kb"])
        ids = {hit["chunk_id"] for hit in result["hits"]}
        self.assertIn(self.docs[0] + "-chunk", ids)
        self.assertTrue(all(doc + "-chunk" not in ids for doc in self.docs[1:]))

    def test_empty_collection_scope_is_denied_and_private_file_is_hidden(self):
        response = self.client.post("/v1/chat", json={"question": "蜃楼测试工艺", "mode": "rag",
                                                       "scope": {"document_collections": []}})
        self.runs.append(response.json()["run_id"])
        self.assertEqual(response.json()["error_code"], "COLLECTION_NOT_ALLOWED")
        self.assertEqual(self.client.get(f"/v1/documents/{self.docs[1]}/file").status_code, 404)
        self.assertEqual(self.client.get(f"/v1/documents/{self.docs[0]}/file").status_code, 404)

    def test_source_change_blocks_original_file_and_page_preview(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.NamedTemporaryFile(dir=root / "data/knowledge_base", suffix=".tmp") as source:
            source.write(b"updated source bytes")
            source.flush()
            with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
                cur.execute("UPDATE document_assets SET object_uri=%s WHERE document_id=%s",
                            (source.name, self.docs[0]))
            for suffix in ("file", "pages/1"):
                response = self.client.get(f"/v1/documents/{self.docs[0]}/{suffix}")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"]["error_code"], "SOURCE_VERSION_MISMATCH")

    def test_document_answer_persists_claims_sources_and_replays_over_sse(self):
        def ask_with_sources(question, **kwargs):
            kwargs["retriever"], kwargs["generator"] = self.retrieve(), self.generator
            return ask_documents(question, **kwargs)
        with patch("datamind.agent.ask_documents", side_effect=ask_with_sources):
            response = self.client.post("/v1/chat/stream", json={"question": "蜃楼测试工艺", "mode": "rag",
                                                                  "scope": {"document_collections": ["demo_kb"]}})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        run_id = events[0]["run_id"]
        self.runs.append(run_id)
        final = events[-1]["output_summary"]
        self.assertEqual(final["status"], "COMPLETED")
        self.assertEqual(final["confidence_level"], "medium")
        self.assertEqual(final["rag_result"]["citations"][0]["quote"], TEXT)
        evidence = self.client.get(f"/v1/runs/{run_id}/evidence").json()
        self.assertEqual(evidence["claims"][0]["status"], "warned")
        self.assertTrue(any(node["node_type"] == "Chunk" for node in evidence["nodes"]))
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}").json()["versions"]["prompt"], "rag-v2")

    def test_missing_evidence_abstains_without_model_and_repair_budget_is_bounded(self):
        retriever = self.retrieve()
        with patch.object(retriever, "retrieve", return_value={"hits": [], "snapshot_id": "kb-empty",
             "mode": "lexical", "eligible_chunks": 0, "warnings": [], "retriever_version": "test", "collections": ["demo_kb"]}):
            result = ask_documents("蜃楼测试工艺", retriever=retriever,
                                   generator=lambda *_: self.fail("model should not run"))
        self.assertEqual(result["status"], "abstained")
        calls = []
        def fabricated(*args):
            calls.append(1)
            return {"claims": [{"text": "错误温度99度", "citations": [{"chunk_id": self.docs[0] + "-chunk", "quote": TEXT}]}]}
        result = ask_documents("蜃楼测试工艺", retriever=self.retrieve(), generator=fabricated)
        self.assertEqual(result["error_code"], "RAG_VERIFICATION_FAILED")
        self.assertEqual(len(calls), 2)

    def test_model_abstention_is_persisted_with_retrieval_trace(self):
        agent = DataMindAgent()
        self.runs.append(agent.run_id)
        try:
            result = agent.run("蜃楼测试工艺", mode="rag", retriever=self.retrieve(),
                               rag_generator=lambda *_: {"abstain": True, "claims": []})
        finally:
            agent.close()
        self.assertEqual(result["status"], "ABSTAINED")
        events = self.client.get(f"/v1/runs/{agent.run_id}/events").json()["events"]
        self.assertEqual(events[-1]["event_type"], "answer.abstained")
        self.assertTrue(any(event["event_type"] == "retrieval.completed" for event in events))


if __name__ == "__main__":
    unittest.main()
