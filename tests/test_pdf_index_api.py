"""Private PDF outbox HTTP contract against disposable DB/native/vector data.

The synthetic 512-dimensional embedder is an injection for local Qdrant tests,
not a model-quality score. Preparation stays in the main thread; submitting an
HTTP publication only queues work and never invokes an embedding provider.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import psycopg2
import pymupdf
from qdrant_client import QdrantClient

from datamind import pdf_index as index, pdf_publications as registry, pdf_vectors as vectors
from datamind.agent import _write_dsn
from datamind.api import app
from datamind.knowledge import KnowledgeRetriever
from ingestion.pdf_prepare import prepare_pdf
from auth_fixtures import cleanup, login


BASE = "/v1/pdf-knowledge/publications"


def synthetic_embedder(texts):
    """Deterministic local test embeddings; no remote service or model loading."""
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        yield [(digest[i % len(digest)] + 1) / 256 for i in range(vectors.VECTOR_DIM)]


class PdfIndexAPITest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdf-index-api-")
        self.base = Path(self.temporary.name).resolve()
        self.store = self.base / "prepared"
        self.document_id = "pdf-index-api-" + uuid.uuid4().hex[:16]
        self.ids = [self.document_id]
        self.clients = []
        self.addCleanup(self.clean_fixtures)
        self.store_patch = patch.object(registry, "PREPARED_STORE", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.vector_client = QdrantClient(":memory:")
        self.close_vectors = self.vector_client.close
        self.addCleanup(self.close_vectors)
        # HTTP checked reads obtain an owned factory client. Keep this shared
        # in-memory fixture alive, then close it explicitly after each test.
        self.close_patch = patch.object(self.vector_client, "close")
        self.close_patch.start()
        self.addCleanup(self.close_patch.stop)
        self.factory_patch = patch.object(vectors, "client_factory", return_value=self.vector_client)
        self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.admin = self.client(("admin",))
        self.prepared = self.prepare_and_register(1)
        self.payload = self.publication_payload(self.prepared)

    def clean_fixtures(self):
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM pdf_knowledge_heads WHERE document_id=ANY(%s)", (self.ids,))
            cur.execute("DELETE FROM pdf_knowledge_requests WHERE document_id=ANY(%s)", (self.ids,))
            cur.execute("DELETE FROM pdf_snapshot_versions WHERE document_id=ANY(%s)", (self.ids,))
            cur.execute("DELETE FROM pdf_snapshot_assets WHERE document_id=ANY(%s)", (self.ids,))
        for client in self.clients:
            cleanup(client)
            client.close()
        self.temporary.cleanup()

    def client(self, roles=None):
        client = TestClient(app)
        self.clients.append(client)
        if roles is not None:
            login(client, roles, name="私有 PDF 发布隔离测试")
        return client

    def prepare_and_register(self, version, *, document_id=None, previous_sha=None):
        document_id = document_id or self.document_id
        if document_id not in self.ids:
            self.ids.append(document_id)
        source = self.base / (uuid.uuid4().hex + ".pdf")
        with pymupdf.open() as pdf:
            for number in range(2):
                page = pdf.new_page(width=400, height=240)
                page.insert_text((15, 30),
                                 f"Synthetic private native PDF fixture version {version}, page {number + 1}, value {20 + version}.04 mm.",
                                 fontsize=8)
            source.write_bytes(pdf.tobytes(garbage=4, deflate=True))
        request = {"document_id": document_id, "document_version": version,
                   "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                   "job_id": "index-job-" + uuid.uuid4().hex[:20], "attempt_token": "attempt-1"}
        prepared = prepare_pdf(source, self.store, request)
        registration = {"request_id": "index-register-" + uuid.uuid4().hex[:20],
                        "document_id": document_id, "title": "私有 PDF 发布测试",
                        "job_id": request["job_id"], "attempt_token": request["attempt_token"],
                        "manifest_sha256": prepared["manifest_sha256"],
                        "previous_version": version - 1, "previous_source_sha256": previous_sha}
        response = self.admin.post("/v1/pdf-snapshots", json=registration)
        self.assertEqual(response.status_code, 200, response.text)
        return prepared

    def publication_payload(self, prepared, *, previous_publication_id=None, **changes):
        result = {"request_id": "index-request-" + uuid.uuid4().hex[:20],
                  "document_id": prepared["request"]["document_id"],
                  "document_version": prepared["request"]["document_version"],
                  "expected_manifest_sha256": prepared["manifest_sha256"],
                  "previous_publication_id": previous_publication_id}
        result.update(changes)
        return result

    def submit(self, payload=None, client=None):
        response = (client or self.admin).post(BASE, json=payload or self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["publication"]

    def drain(self, publication):
        index.drain(_write_dsn(), publication["publication_id"], workers=1,
                    embedder=synthetic_embedder, client=self.vector_client)
        response = self.admin.get(BASE + "/" + publication["publication_id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["publication"]["status"], "published", response.text)
        return response.json()["publication"]

    def successful_publication(self):
        return self.drain(self.submit())

    def publication_path(self, publication, suffix=""):
        return BASE + "/" + publication["publication_id"] + suffix

    def current_path(self):
        return "/v1/pdf-knowledge/documents/" + self.document_id

    def chunks_params(self, publication):
        return {"expected_plan_sha256": publication["plan_sha256"] or "0" * 64}

    def assert_safe(self, response):
        self.assertEqual(response.headers["cache-control"], "no-store")
        for value in (str(self.base), str(registry.ROOT), "_session_hash", "csrf_token", '"vectors"', '"vector":'):
            self.assertNotIn(value, response.text)
        session = self.admin.cookies.get("datamind_session")
        self.assertNotIn(session, response.text)
        self.assertNotIn(hashlib.sha256(session.encode()).hexdigest(), response.text)

    def assert_error(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.text)
        if code is not None:
            self.assertEqual(response.json()["detail"]["error_code"], code)
        self.assert_safe(response)

    def change_roles(self, client, roles):
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles=%s WHERE user_id=%s", (roles, client._fixture_users[0]))

    def revoke_session(self, client):
        session_hash = hashlib.sha256(client.cookies.get("datamind_session").encode()).hexdigest()
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE token_hash=%s", (session_hash,))

    def test_queue_submission_is_safe_idempotent_and_does_not_embed(self):
        with patch.object(index, "work_once") as worker, patch.object(vectors, "client_factory") as factory, \
                patch.object(vectors, "build_plan", side_effect=AssertionError("queue submission must not embed")) as builder:
            first = self.admin.post(BASE, json=self.payload)
            second = self.admin.post(BASE, json=self.payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        worker.assert_not_called()
        factory.assert_not_called()
        builder.assert_not_called()
        self.assert_safe(first)
        meta = first.json()["publication"]
        self.assertEqual(meta["status"], "queued")
        self.assertIs(meta["private_indexed"], False)
        self.assertIs(meta["public_knowledge_visible"], False)
        self.assertIs(meta["source_authority_verified"], False)
        self.assertIs(meta["vector_integrity_checked"], False)
        self.assertIsNone(meta["plan_sha256"])
        self.assertEqual(meta["owner"], {"user_id": self.admin._fixture_users[0],
                                         "display_name": "私有 PDF 发布隔离测试", "roles": ["admin"]})
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pdf_knowledge_requests WHERE document_id=%s", (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_all_routes_require_admin_and_no_store(self):
        publication = self.successful_publication()
        paths = [(BASE, None), (self.publication_path(publication), None),
                 (self.publication_path(publication, "/chunks"), self.chunks_params(publication)),
                 (self.current_path(), None)]
        for client, status in [(self.client(), 401), (self.client(("viewer",)), 403),
                               (self.client(("reviewer",)), 403), (self.client(("approver",)), 403)]:
            for path, params in paths:
                with self.subTest(path=path, status=status):
                    self.assert_error(client.get(path, params=params), status)
            self.assert_error(client.post(BASE, json=self.payload), status)
            self.assert_error(client.post(self.publication_path(publication, "/retry")), status)

    def test_post_requires_csrf_and_same_origin(self):
        csrf = self.admin.headers.pop("X-CSRF-Token")
        self.assert_error(self.admin.post(BASE, json=self.payload), 403, "CSRF_REQUIRED")
        self.admin.headers["X-CSRF-Token"] = csrf
        # 同源判定已改为**固定白名单**，不再从 Host 推导：`http://testserver` 是 TestClient 的
        # Host，正好是"自证同源"的攻击面，必须被拒；正例改用白名单内的来源。
        for headers in ({"Origin": "https://untrusted.invalid"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Origin": "http://testserver"}):
            self.assert_error(self.admin.post(BASE, json=self.payload, headers=headers), 403, "ORIGIN_NOT_ALLOWED")
        response = self.admin.post(BASE, json=self.payload, headers={"Origin": "http://127.0.0.1:8000"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_strict_submission_rejects_spoofed_context_and_version_types(self):
        changes = [{"document_version": True}, {"document_version": "1"}, {"document_version": 0},
                   {"request_id": True}, {"document_id": "../escape"}, {"expected_manifest_sha256": "A" * 64},
                   {"expected_manifest_sha256": "short"}, {"previous_publication_id": 1},
                   {"actor": {"user_id": "spoof"}}, {"roles": ["admin"]}, {"profile": {"model": "spoof"}},
                   {"source_path": "/outside/private.pdf"}, {"storage_root": "/outside/store"},
                   {"indexed": True}, {"public_knowledge_visible": True}, {"vector_plan_sha256": "0" * 64}]
        for change in changes:
            with self.subTest(change=change):
                response = self.admin.post(BASE, json={**self.payload, **change})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
        missing = dict(self.payload)
        missing.pop("previous_publication_id")
        self.assert_error(self.admin.post(BASE, json=missing), 422)

    def test_manifest_and_request_reuse_conflicts_do_not_add_queue_rows(self):
        self.assert_error(self.admin.post(BASE, json={**self.payload, "expected_manifest_sha256": "0" * 64}),
                          409, "PDF_PUBLICATION_SNAPSHOT_SELECTION_CHANGED")
        publication = self.submit()
        changed = {**self.payload, "previous_publication_id": "pdf-pub-" + uuid.uuid4().hex}
        self.assert_error(self.admin.post(BASE, json=changed), 409, "PDF_PUBLICATION_REQUEST_ID_REUSED")
        other = self.client(("admin",))
        self.assert_error(other.post(BASE, json=self.payload), 409, "PDF_PUBLICATION_REQUEST_ID_REUSED")
        self.assertEqual(self.admin.get(self.publication_path(publication)).status_code, 200)
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pdf_knowledge_requests WHERE document_id=%s", (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_published_chunks_are_checked_and_current_metadata_stays_private(self):
        publication = self.successful_publication()
        chunks = self.admin.get(self.publication_path(publication, "/chunks"), params=self.chunks_params(publication))
        self.assertEqual(chunks.status_code, 200, chunks.text)
        self.assert_safe(chunks)
        self.assertIs(chunks.json()["vector_integrity_checked"], True)
        self.assertEqual(chunks.json()["publication"]["publication_id"], publication["publication_id"])
        self.assertIs(chunks.json()["publication"]["private_indexed"], True)
        self.assertIs(chunks.json()["publication"]["public_knowledge_visible"], False)
        self.assertIs(chunks.json()["publication"]["source_authority_verified"], False)
        self.assertGreater(len(chunks.json()["chunks"]), 0)
        for chunk in chunks.json()["chunks"]:
            self.assertEqual(chunk["document_id"], self.document_id)
            self.assertEqual(chunk["document_version"], 1)
            self.assertEqual(chunk["source_sha256"], self.prepared["request"]["source_sha256"])
            self.assertEqual(chunk["content_sha256"], hashlib.sha256(chunk["text"].encode()).hexdigest())
        current = self.admin.get(self.current_path())
        self.assertEqual(current.status_code, 200, current.text)
        self.assert_safe(current)
        self.assertEqual(current.json()["publication"]["publication_id"], publication["publication_id"])
        self.assertIs(current.json()["publication"]["vector_integrity_checked"], False)

    def test_fixed_chunks_require_exact_plan_and_a_published_status(self):
        queued = self.submit()
        self.assert_error(self.admin.get(self.publication_path(queued, "/chunks"), params=self.chunks_params(queued)),
                          409, "PDF_PUBLICATION_NOT_PUBLISHED")
        publication = self.drain(queued)
        for params in ({}, {"expected_plan_sha256": "A" * 64}, {"expected_plan_sha256": "short"}):
            self.assert_error(self.admin.get(self.publication_path(publication, "/chunks"), params=params), 422)
        self.assert_error(self.admin.get(self.publication_path(publication, "/chunks"),
                                        params={"expected_plan_sha256": "0" * 64}), 409, "PDF_INDEX_SELECTION_CHANGED")

    def test_unknown_publication_and_current_document_are_not_found(self):
        unknown = "pdf-pub-" + uuid.uuid4().hex
        for suffix in ("", "/chunks"):
            params = {"expected_plan_sha256": "0" * 64} if suffix else None
            self.assert_error(self.admin.get(BASE + "/" + unknown + suffix, params=params),
                              404, "PDF_PUBLICATION_NOT_FOUND")
        self.assert_error(self.admin.post(BASE + "/" + unknown + "/retry"), 404, "PDF_PUBLICATION_NOT_FOUND")
        self.assert_error(self.admin.get(self.current_path()), 404, "PDF_PUBLICATION_HEAD_NOT_FOUND")

    def test_listing_is_bounded_and_metadata_contains_no_raw_vector_plan(self):
        self.submit()
        other = self.prepare_and_register(1, document_id="pdf-index-api-" + uuid.uuid4().hex[:16])
        self.submit(self.publication_payload(other))
        response = self.admin.get(BASE, params={"limit": 1, "offset": 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response)
        body = response.json()
        self.assertEqual(body["limit"], 1)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(len(body["publications"]), 1)
        self.assertTrue(body["has_more"])
        self.assertNotIn('"plan":', response.text)
        defaults = self.admin.get(BASE)
        self.assertEqual(defaults.status_code, 200, defaults.text)
        self.assertEqual(defaults.json()["limit"], 20)
        self.assertEqual(defaults.json()["offset"], 0)
        empty = self.admin.get(BASE, params={"offset": 10000})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["publications"], [])
        self.assertFalse(empty.json()["has_more"])
        for params in ({"limit": 0}, {"limit": 51}, {"offset": -1}, {"offset": 10001}):
            self.assert_error(self.admin.get(BASE, params=params), 422)

    def test_only_original_owner_can_retry_failed_work_and_published_work_cannot_retry(self):
        publication = self.submit()
        failed_embedder = Mock(side_effect=RuntimeError("synthetic embedding failure"))
        index.work_once(_write_dsn(), publication["publication_id"], workers=1,
                        embedder=failed_embedder, client=self.vector_client)
        failed = self.admin.get(self.publication_path(publication))
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["publication"]["status"], "failed", failed.text)
        other = self.client(("admin",))
        self.assert_error(other.post(self.publication_path(publication, "/retry")), 403, "PDF_PUBLICATION_OWNER_REQUIRED")
        csrf = self.admin.headers.pop("X-CSRF-Token")
        self.assert_error(self.admin.post(self.publication_path(publication, "/retry")), 403, "CSRF_REQUIRED")
        self.admin.headers["X-CSRF-Token"] = csrf
        retried = self.admin.post(self.publication_path(publication, "/retry"))
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assert_safe(retried)
        self.assertEqual(retried.json()["publication"]["publication_id"], publication["publication_id"])
        self.assertEqual(retried.json()["publication"]["status"], "queued")
        published = self.drain(publication)
        self.assert_error(self.admin.post(self.publication_path(published, "/retry")), 409, "PDF_PUBLICATION_NOT_FAILED")

    def test_new_snapshot_blocks_current_private_head_until_new_publication(self):
        first = self.successful_publication()
        original = self.admin.get(self.publication_path(first, "/chunks"), params=self.chunks_params(first))
        self.assertEqual(original.status_code, 200, original.text)
        second = self.prepare_and_register(2, previous_sha=self.prepared["request"]["source_sha256"])
        self.assert_error(self.admin.get(self.current_path()), 409, "PDF_PUBLICATION_SNAPSHOT_CHANGED")
        historic = self.admin.get(self.publication_path(first, "/chunks"), params=self.chunks_params(first))
        self.assertEqual(historic.status_code, 200, historic.text)
        self.assertEqual(historic.json(), original.json())
        newer = self.submit(self.publication_payload(second, previous_publication_id=first["publication_id"]))
        published = self.drain(newer)
        current = self.admin.get(self.current_path())
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["publication"]["publication_id"], published["publication_id"])
        self.assertEqual(current.json()["publication"]["document_version"], 2)
        historic = self.admin.get(self.publication_path(first, "/chunks"), params=self.chunks_params(first))
        self.assertEqual(historic.status_code, 200, historic.text)
        self.assertEqual(historic.json(), original.json())

    def test_missing_real_vector_point_prevents_checked_chunk_read(self):
        publication = self.successful_publication()
        collections = self.vector_client.get_collections().collections
        self.assertEqual(len(collections), 1)
        self.assertTrue(collections[0].name.startswith(vectors.COLLECTION_PREFIX))
        points, _ = self.vector_client.scroll(collections[0].name, limit=10)
        self.assertGreater(len(points), 0)
        self.vector_client.delete(collection_name=collections[0].name, points_selector=[points[0].id], wait=True)
        self.assert_error(self.admin.get(self.publication_path(publication, "/chunks"), params=self.chunks_params(publication)),
                          409, "PDF_INDEX_VECTOR_MISSING")

    def test_changed_sealed_source_prevents_checked_chunk_read(self):
        publication = self.successful_publication()
        path = self.store / self.prepared["source_path"]
        path.chmod(0o600)
        path.write_bytes(b"tampered native PDF source")
        self.assert_error(self.admin.get(self.publication_path(publication, "/chunks"), params=self.chunks_params(publication)),
                          409, "PDF_ARTIFACT_TAMPERED")

    def test_private_publication_does_not_enter_legacy_documents_or_anonymous_rag(self):
        self.successful_publication()
        public = self.client()
        response = public.get("/v1/documents")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(self.document_id, response.text)
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            for table in ("document_assets", "document_blocks", "chunks", "document_facts"):
                cur.execute(f"SELECT count(*) FROM {table} WHERE document_id=%s", (self.document_id,))
                self.assertEqual(cur.fetchone()[0], 0, table)
        ref = {"document_id": self.document_id, "document_version": 1,
               "source_sha256": self.prepared["request"]["source_sha256"], "parser_version": self.prepared["parser_version"]}
        with self.assertRaisesRegex(ValueError, "^DOCUMENT_SELECTION_UNAVAILABLE$"):
            KnowledgeRetriever(_write_dsn(), mode="lexical").retrieve("此私有 PDF 可被公开检索吗？", document_refs=[ref])

    def test_every_read_rechecks_live_authorization_after_result_is_captured(self):
        publication = self.successful_publication()
        cases = [("listing", BASE, None), ("poll", self.publication_path(publication), None),
                 ("current", self.current_path(), None),
                 ("read_chunks", self.publication_path(publication, "/chunks"), self.chunks_params(publication))]
        for name, path, params in cases:
            client = self.client(("admin",))
            original = getattr(index, name)
            captured = []

            def read_then_demote(*args, **kwargs):
                result = original(*args, **kwargs)
                captured.append(result)
                self.change_roles(client, ["viewer"])
                return result

            with self.subTest(route=path), patch.object(index, name, side_effect=read_then_demote):
                response = client.get(path, params=params)
                self.assert_error(response, 403, "PERMISSION_DENIED")
                self.assertEqual(len(captured), 1)
                self.assertEqual(response.json(), {"detail": {"error_code": "PERMISSION_DENIED"}})

    def test_publication_poll_rechecks_revoked_session_after_metadata_read(self):
        publication = self.submit()
        original = index.poll

        def read_then_revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            self.revoke_session(self.admin)
            return result

        with patch.object(index, "poll", side_effect=read_then_revoke):
            response = self.admin.get(self.publication_path(publication))
        self.assert_error(response, 401, "SESSION_INVALID")
        self.assertEqual(response.json(), {"detail": {"error_code": "SESSION_INVALID"}})

    def test_committed_queue_post_with_lost_admin_returns_no_result_and_replays_once(self):
        original = index.submit
        captured = []

        def submit_then_demote(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append(result)
            self.change_roles(self.admin, ["viewer"])
            return result

        with patch.object(index, "submit", side_effect=submit_then_demote):
            response = self.admin.post(BASE, json=self.payload)
        self.assert_error(response, 403, "PERMISSION_DENIED")
        self.assertEqual(response.json(), {"detail": {"error_code": "PERMISSION_DENIED"}})
        self.assertEqual(len(captured), 1)
        self.change_roles(self.admin, ["admin"])
        recovered = self.submit()
        self.assertEqual(recovered["publication_id"], captured[0]["publication_id"])
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pdf_knowledge_requests WHERE document_id=%s", (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_committed_retry_with_lost_admin_returns_no_result_but_preserves_requeue(self):
        publication = self.submit()
        index.work_once(_write_dsn(), publication["publication_id"], workers=1,
                        embedder=Mock(side_effect=RuntimeError("synthetic embedding failure")),
                        client=self.vector_client)
        failed = self.admin.get(self.publication_path(publication))
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["publication"]["status"], "failed")
        original = index.retry
        captured = []

        def retry_then_demote(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append(result)
            self.change_roles(self.admin, ["viewer"])
            return result

        with patch.object(index, "retry", side_effect=retry_then_demote):
            response = self.admin.post(self.publication_path(publication, "/retry"))
        self.assert_error(response, 403, "PERMISSION_DENIED")
        self.assertEqual(response.json(), {"detail": {"error_code": "PERMISSION_DENIED"}})
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["status"], "queued")
        self.change_roles(self.admin, ["admin"])
        recovered = self.admin.get(self.publication_path(publication))
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["publication"]["status"], "queued")
        self.assertEqual(recovered.json()["publication"]["publication_id"], publication["publication_id"])
        self.assert_error(self.admin.post(self.publication_path(publication, "/retry")),
                          409, "PDF_PUBLICATION_NOT_FAILED")
