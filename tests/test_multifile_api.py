"""HTTP/SSE scope plumbing and fail-closed versioned source links.

Use scripts/test_isolated.py. Stub agent calls never reach a model provider;
source fixtures and database rows are created and removed by each test.
"""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import psycopg2
import pymupdf

from datamind.api import app
from datamind.knowledge import ROOT
import auth_fixtures


WRITE_DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev"
REFS = [{"document_id": f"doc-api-{label}", "document_version": i,
         "source_sha256": str(i) * 64, "parser_version": "api-scope-v1"}
        for i, label in enumerate(("a", "b"), 1)]


class MultiFileChatAPITest(unittest.TestCase):
    def setUp(self):
        # /v1/chat and /v1/chat/stream are mutating routes now gated by the default-deny
        # identity middleware (identity + CSRF + same-origin), so this client needs a session
        # instead of the loopback demo shortcut TestClient's "testclient" host cannot claim.
        self.client = TestClient(app)
        auth_fixtures.login(self.client)

    def tearDown(self):
        auth_fixtures.cleanup(self.client)
        self.client.close()

    def body(self, scope=None):
        return {"question": "比较所选两份工艺文档", "mode": "rag",
                "scope": scope if scope is not None else {"document_collections": ["demo_kb"], "document_refs": REFS}}

    def test_chat_passes_exact_selected_refs_to_agent_and_closes_it(self):
        agent = Mock()
        agent.run.return_value = {"status": "COMPLETED", "answer": "测试回答"}
        with patch("datamind.api.DataMindAgent", return_value=agent):
            response = self.client.post("/v1/chat", json=self.body())
        self.assertEqual(response.status_code, 200)
        agent.run.assert_called_once_with("比较所选两份工艺文档", mode="rag", database_sources=None,
                                         document_collections=["demo_kb"], document_refs=REFS)
        agent.close.assert_called_once_with()

    def test_sse_passes_refs_and_streams_the_corresponding_event(self):
        agents = []

        def factory(*, sink):
            agent = Mock()

            def run(question, **scope):
                event = {"event_type": "answer.completed", "output_summary": {
                    "status": "COMPLETED", "selected_documents": scope["document_refs"]}}
                sink(event)
                return event["output_summary"]

            agent.run.side_effect = run
            agents.append(agent)
            return agent

        with patch("datamind.api.DataMindAgent", side_effect=factory):
            response = self.client.post("/v1/chat/stream", json=self.body())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[0]["output_summary"]["selected_documents"], REFS)
        agents[0].run.assert_called_once_with("比较所选两份工艺文档", mode="rag", database_sources=None,
                                             document_collections=["demo_kb"], document_refs=REFS)
        agents[0].close.assert_called_once_with()

    def test_scope_and_ref_invalid_inputs_stop_before_agent_construction(self):
        invalid = [{"unknown_scope": True}, {"document_refs": []},
                   {"document_refs": [dict(REFS[0], document_id=f"doc-api-{i}") for i in range(9)]},
                   {"document_refs": [dict(REFS[0], document_version="1")]},
                   {"document_refs": [dict(REFS[0], document_version=True)]},
                   {"document_refs": [dict(REFS[0], source_sha256="A" * 64)]},
                   {"document_refs": [dict(REFS[0], parser_version=None)]},
                   {"document_refs": [dict(REFS[0], reviewer_roles=["admin"])]}]
        for path in ("/v1/chat", "/v1/chat/stream"):
            for scope in invalid:
                with self.subTest(path=path, scope=scope), patch("datamind.api.DataMindAgent") as factory:
                    response = self.client.post(path, json=self.body(scope))
                    self.assertEqual(response.status_code, 422)
                    factory.assert_not_called()

    def test_legacy_collection_scope_passes_no_explicit_refs(self):
        agent = Mock()
        agent.run.return_value = {"status": "ABSTAINED"}
        with patch("datamind.api.DataMindAgent", return_value=agent):
            response = self.client.post("/v1/chat", json=self.body({"document_collections": ["demo_kb"]}))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(agent.run.call_args.kwargs["document_refs"])


class MultiFileSourceAPITest(unittest.TestCase):
    def setUp(self):
        # Deliberately anonymous: every request below is a GET on /v1/documents/..., which the
        # middleware keeps in its public read prefix, so no session is needed here.
        self.client = TestClient(app)
        self.document_id = "test-multifile-api-" + uuid.uuid4().hex[:16]
        self.directory = tempfile.TemporaryDirectory(prefix="multifile-api-", dir=ROOT / "data/knowledge_base")
        self.source = Path(self.directory.name) / "fixture.pdf"
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "Multifile source fixture " + self.document_id)
            pdf.save(self.source)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.image_dir = ROOT / "data/object_store/page_images" / self.document_id
        self.image_dir.mkdir(parents=True)
        self.image = self.image_dir / "page_001.png"
        with pymupdf.open(self.source) as pdf:
            self.image.write_bytes(pdf[0].get_pixmap(dpi=72).tobytes("png"))
        self.params = {"expected_version": 3, "expected_sha256": self.sha}
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO document_assets(document_id,title,collection,file_type,origin,
                sha256,object_uri,doc_version,acl,status,needs_review,page_count,parser_version)
                VALUES (%s,'多文件源链接测试','demo_kb','pdf','curated',%s,%s,3,'{public}','indexed',FALSE,1,'api-source-v1')""",
                (self.document_id, self.sha, str(self.source)))

    def tearDown(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM document_assets WHERE document_id=%s", (self.document_id,))
        self.client.close()
        self.directory.cleanup()
        shutil.rmtree(self.image_dir)

    def path(self, suffix):
        return f"/v1/documents/{self.document_id}/{suffix}"

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["detail"]["error_code"], code)

    def test_paired_current_tokens_return_original_file_and_page_bytes(self):
        file_response = self.client.get(self.path("file"), params=self.params)
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(file_response.content, self.source.read_bytes())
        self.assertEqual(file_response.headers["content-type"], "application/pdf")
        page_response = self.client.get(self.path("pages/1"), params=self.params)
        self.assertEqual(page_response.status_code, 200)
        self.assertEqual(page_response.content, self.image.read_bytes())
        self.assertEqual(page_response.headers["content-type"], "image/png")

    def test_paired_tokens_are_optional_for_legacy_live_links(self):
        for suffix in ("file", "pages/1"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get(self.path(suffix)).status_code, 200)

    def test_partial_identity_tokens_are_rejected_as_invalid_input(self):
        for suffix in ("file", "pages/1"):
            for params in ({"expected_version": 3}, {"expected_sha256": self.sha}):
                with self.subTest(suffix=suffix, params=params):
                    self.assert_error(self.client.get(self.path(suffix), params=params), 422, "DOCUMENT_SELECTION_INVALID")

    def test_mismatched_version_or_sha_returns_conflict(self):
        for suffix in ("file", "pages/1"):
            for params in ({**self.params, "expected_version": 2}, {**self.params, "expected_sha256": "0" * 64}):
                with self.subTest(suffix=suffix, params=params):
                    self.assert_error(self.client.get(self.path(suffix), params=params), 409, "DOCUMENT_SELECTION_CHANGED")

    def test_old_citation_links_fail_after_head_version_changes(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("UPDATE document_assets SET doc_version=4 WHERE document_id=%s", (self.document_id,))
        for suffix in ("file", "pages/1"):
            with self.subTest(suffix=suffix):
                self.assert_error(self.client.get(self.path(suffix), params=self.params), 409, "DOCUMENT_SELECTION_CHANGED")
                self.assertEqual(self.client.get(self.path(suffix), params={**self.params, "expected_version": 4}).status_code, 200)

    def test_source_bytes_drift_is_detected_even_with_matching_head_tokens(self):
        self.source.write_bytes(b"changed original source")
        for suffix in ("file", "pages/1"):
            with self.subTest(suffix=suffix):
                self.assert_error(self.client.get(self.path(suffix), params=self.params), 409, "SOURCE_VERSION_MISMATCH")

    def test_deleted_or_private_source_cannot_be_reopened_by_citation_tokens(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("UPDATE document_assets SET acl='{private}' WHERE document_id=%s", (self.document_id,))
        for suffix in ("file", "pages/1"):
            with self.subTest(suffix=suffix):
                self.assert_error(self.client.get(self.path(suffix), params=self.params), 404, "DOCUMENT_NOT_FOUND")
