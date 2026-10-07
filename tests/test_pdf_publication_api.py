"""Admin-only HTTP boundaries for sealed native PDF snapshot registration.

Run through scripts/test_isolated.py: database rows, identities, and prepared
files are disposable. Native preparation happens in the test's main thread;
the HTTP worker verifies sealed artifacts and never renders or calls a model.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import psycopg2
import pymupdf

from datamind.agent import _write_dsn
from datamind.api import app
from datamind import pdf_publications as publications
from datamind.knowledge import KnowledgeRetriever
from ingestion.pdf_prepare import prepare_pdf
from auth_fixtures import cleanup, login


class PdfSnapshotAPITest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdf-snapshot-api-")
        self.base = Path(self.temporary.name).resolve()
        self.store = self.base / "prepared"
        self.document_id = "pdf-api-" + uuid.uuid4().hex[:20]
        self.ids = [self.document_id]
        self.clients = []
        self.addCleanup(self.clean_fixtures)
        self.store_patch = patch.object(publications, "PREPARED_STORE", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.admin = self.client(("admin",))
        self.prepared, self.source = self.prepare_version(1)
        self.payload = self.registration(self.prepared)

    def clean_fixtures(self):
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
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
            login(client, roles, name="PDF 快照隔离测试")
        return client

    def prepare_version(self, version, *, document_id=None, text=None):
        document_id = document_id or self.document_id
        if document_id not in self.ids:
            self.ids.append(document_id)
        source = self.base / (uuid.uuid4().hex + ".pdf")
        with pymupdf.open() as pdf:
            for number in range(2):
                page = pdf.new_page(width=400, height=240)
                page.insert_text((15, 30), text or
                                 f"Synthetic native API fixture version {version}, page {number + 1}, measured value {20 + version}.04 mm.",
                                 fontsize=8)
            source.write_bytes(pdf.tobytes(garbage=4, deflate=True))
        request = {"document_id": document_id, "document_version": version,
                   "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                   "job_id": "api-job-" + uuid.uuid4().hex[:20], "attempt_token": "attempt-1"}
        return prepare_pdf(source, self.store, request), source

    def registration(self, prepared, *, previous_version=0, previous_source_sha256=None, **changes):
        request = prepared["request"]
        payload = {"request_id": "api-request-" + uuid.uuid4().hex[:20],
                   "document_id": request["document_id"], "title": "原生 PDF 快照测试",
                   "job_id": request["job_id"], "attempt_token": request["attempt_token"],
                   "manifest_sha256": prepared["manifest_sha256"],
                   "previous_version": previous_version,
                   "previous_source_sha256": previous_source_sha256}
        payload.update(changes)
        return payload

    def path(self, suffix="", *, document_id=None):
        return "/v1/pdf-snapshots/" + (document_id or self.document_id) + suffix

    def artifact_params(self, prepared=None):
        prepared = prepared or self.prepared
        return {"version": prepared["request"]["document_version"],
                "expected_manifest_sha256": prepared["manifest_sha256"]}

    def publish(self, payload=None, client=None):
        response = (client or self.admin).post("/v1/pdf-snapshots", json=payload or self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["version"]

    def assert_error(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.text)
        if code is not None:
            self.assertEqual(response.json()["detail"]["error_code"], code)
        self.assertNotIn(str(self.base), response.text)
        self.assertNotIn(str(publications.ROOT), response.text)

    def assert_private_response(self, response):
        self.assertEqual(response.headers["cache-control"], "no-store")
        if response.headers.get("content-type", "").startswith("application/json"):
            self.assertNotIn(str(self.base), response.text)
            self.assertNotIn(str(publications.ROOT), response.text)
            self.assertNotIn("_session_hash", response.text)
            self.assertNotIn("csrf_token", response.text)
            self.assertNotIn(self.admin.cookies.get("datamind_session"), response.text)
            self.assertNotIn(hashlib.sha256(self.admin.cookies.get("datamind_session").encode()).hexdigest(), response.text)

    def reads(self):
        params = self.artifact_params()
        return [("/v1/pdf-snapshots", None), (self.path(), None),
                (self.path("/versions"), None), (self.path("/file"), params),
                (self.path("/pages/1"), params), (self.path("/bundle"), params)]

    def test_every_route_requires_admin_before_any_artifact_read(self):
        for client, status in [(self.client(), 401),
                               (self.client(("viewer",)), 403),
                               (self.client(("reviewer",)), 403),
                               (self.client(("approver",)), 403)]:
            for path, params in self.reads():
                with self.subTest(path=path, expected=status):
                    self.assert_error(client.get(path, params=params), status)
            self.assert_error(client.post("/v1/pdf-snapshots", json=self.payload), status)

    def test_write_requires_csrf_and_same_origin(self):
        csrf = self.admin.headers.pop("X-CSRF-Token")
        self.assert_error(self.admin.post("/v1/pdf-snapshots", json=self.payload), 403, "CSRF_REQUIRED")
        self.admin.headers["X-CSRF-Token"] = csrf
        for headers in ({"Origin": "https://untrusted.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assert_error(self.admin.post("/v1/pdf-snapshots", json=self.payload, headers=headers),
                                  403, "ORIGIN_NOT_ALLOWED")
        response = self.admin.post("/v1/pdf-snapshots", json=self.payload,
                                   headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private_response(response)

    def test_strict_request_rejects_authority_paths_flags_and_non_integer_versions(self):
        changes = [{"previous_version": True}, {"previous_version": "0"}, {"previous_version": -1},
                   {"title": 123}, {"title": ""}, {"request_id": True},
                   {"document_id": "../escape"}, {"attempt_token": "attempt/name"},
                   {"manifest_sha256": "A" * 64}, {"manifest_sha256": "short"},
                   {"actor": {"user_id": "spoof"}}, {"roles": ["admin"]},
                   {"provenance": "authorized_external"}, {"source_path": "/outside/source.pdf"},
                   {"storage_root": "/outside/store"}, {"source_sha256": "0" * 64},
                   {"parser_version": "spoof-parser"}, {"document_version": 1},
                   {"indexed": True}, {"knowledge_visible": True}, {"model_calls": 7}]
        for change in changes:
            with self.subTest(change=change):
                response = self.admin.post("/v1/pdf-snapshots", json={**self.payload, **change})
                self.assertEqual(response.status_code, 422, response.text)
        missing_previous_sha = dict(self.payload)
        missing_previous_sha.pop("previous_source_sha256")
        self.assertEqual(self.admin.post("/v1/pdf-snapshots", json=missing_previous_sha).status_code, 422)
        for change in ({"previous_version": 0, "previous_source_sha256": "0" * 64},
                       {"previous_version": 1, "previous_source_sha256": None}):
            response = self.admin.post("/v1/pdf-snapshots", json={**self.payload, **change})
            self.assertEqual(response.status_code, 422, response.text)

    def test_registered_metadata_is_server_owned_and_registration_only(self):
        response = self.admin.post("/v1/pdf-snapshots", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private_response(response)
        body = response.json()
        self.assertIn("semantics", body)
        meta = body["version"]
        for key, value in (("document_id", self.document_id), ("document_version", 1),
                           ("source_sha256", self.prepared["request"]["source_sha256"]),
                           ("parser_version", self.prepared["parser_version"]),
                           ("manifest_sha256", self.prepared["manifest_sha256"]),
                           ("artifact_integrity_checked", True), ("registration_only", True),
                           ("indexed", False), ("knowledge_visible", False),
                           ("source_authority_verified", False),
                           ("registrar_authentication_verified", True)):
            self.assertEqual(meta[key], value)
        self.assertEqual(meta["registrar_user_id"], self.admin._fixture_users[0])
        self.assertEqual(meta["registrar_label"], "PDF 快照隔离测试")
        self.assertEqual(meta["registrar_roles"], ["admin"])
        self.assertNotIn("source_path", meta)
        self.assertNotIn("bundle_path", meta)
        self.assertNotIn("artifacts", meta)

    def test_same_request_replay_preserves_original_result_and_registers_once(self):
        first = self.admin.post("/v1/pdf-snapshots", json=self.payload)
        second = self.admin.post("/v1/pdf-snapshots", json=self.payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pdf_snapshot_versions WHERE document_id=%s", (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 1)
        changed = {**self.payload, "title": "Changed request payload"}
        self.assert_error(self.admin.post("/v1/pdf-snapshots", json=changed), 409, "PDF_REQUEST_ID_REUSED")

    def test_changed_manifest_or_mismatched_prepared_identity_is_not_registered(self):
        self.assert_error(self.admin.post("/v1/pdf-snapshots", json={**self.payload, "manifest_sha256": "0" * 64}),
                          409, "PDF_PREPARED_IDENTITY_MISMATCH")
        wrong_document, _ = self.prepare_version(1, document_id="pdf-api-" + uuid.uuid4().hex[:20])
        wrong_version, _ = self.prepare_version(2)
        for prepared in (wrong_document, wrong_version):
            payload = self.registration(prepared, document_id=self.document_id)
            self.assert_error(self.admin.post("/v1/pdf-snapshots", json=payload),
                              409, "PDF_PREPARED_IDENTITY_MISMATCH")
        response = self.admin.get("/v1/pdf-snapshots")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(self.document_id, response.text)

    def test_v1_artifacts_remain_identical_after_v2_becomes_current(self):
        self.publish()
        original = {}
        for suffix in ("/file", "/pages/1", "/bundle"):
            response = self.admin.get(self.path(suffix), params=self.artifact_params())
            self.assertEqual(response.status_code, 200, response.text)
            original[suffix] = response.content
            self.assert_private_response(response)
            if suffix != "/bundle":
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(original["/file"], self.source.read_bytes())
        self.assertEqual(original["/pages/1"], (self.store / self.prepared["artifacts"][2]["path"]).read_bytes())
        second, source = self.prepare_version(2)
        payload = self.registration(second, previous_version=1,
                                    previous_source_sha256=self.prepared["request"]["source_sha256"])
        self.publish(payload)
        current = self.admin.get(self.path())
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["version"]["document_version"], 2)
        self.assertTrue(current.json()["version"]["artifact_integrity_checked"])
        for suffix in original:
            response = self.admin.get(self.path(suffix), params=self.artifact_params())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, original[suffix])
        latest_file = self.admin.get(self.path("/file"), params=self.artifact_params(second))
        self.assertEqual(latest_file.status_code, 200, latest_file.text)
        self.assertEqual(latest_file.content, source.read_bytes())
        self.assertNotEqual(latest_file.content, original["/file"])
        historic = self.admin.get(self.path(), params={"version": 1})
        self.assertEqual(historic.json()["version"]["document_version"], 1)

    def test_file_page_and_bundle_require_exact_version_and_manifest(self):
        self.publish()
        for suffix in ("/file", "/pages/1", "/bundle"):
            for params in ({}, {"version": 1}, {"expected_manifest_sha256": self.prepared["manifest_sha256"]},
                           {"version": 0, "expected_manifest_sha256": self.prepared["manifest_sha256"]},
                           {"version": 1, "expected_manifest_sha256": "A" * 64}):
                with self.subTest(suffix=suffix, params=params):
                    self.assert_error(self.admin.get(self.path(suffix), params=params), 422)
            self.assert_error(self.admin.get(self.path(suffix), params={**self.artifact_params(),
                                                                       "expected_manifest_sha256": "0" * 64}),
                              409, "PDF_SNAPSHOT_SELECTION_CHANGED")

    def test_attachment_headers_and_typed_bundle_are_safe_and_version_bound(self):
        self.publish()
        response = self.admin.get(self.path("/file"), params=self.artifact_params())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.headers["content-disposition"].startswith("attachment;"))
        self.assertNotIn(str(self.base), response.headers["content-disposition"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assert_private_response(response)
        image = self.admin.get(self.path("/pages/2"), params=self.artifact_params())
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertTrue(image.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(image.headers["x-content-type-options"], "nosniff")
        bundle = self.admin.get(self.path("/bundle"), params=self.artifact_params())
        self.assertEqual(bundle.status_code, 200, bundle.text)
        self.assert_private_response(bundle)
        self.assertTrue(bundle.json()["version"]["artifact_integrity_checked"])
        expected = json.loads((self.store / self.prepared["bundle_path"]).read_bytes())
        self.assertEqual(bundle.json()["bundle"], expected)
        self.assertEqual(len(bundle.json()["bundle"]["pages"]), 2)

    def test_unknown_document_version_and_page_are_not_found(self):
        self.publish()
        unknown = "pdf-api-absent-" + uuid.uuid4().hex[:12]
        for suffix in ("", "/versions", "/file", "/pages/1", "/bundle"):
            params = self.artifact_params() if suffix in {"/file", "/pages/1", "/bundle"} else None
            code = "PDF_ASSET_NOT_FOUND" if suffix == "/versions" else "PDF_VERSION_NOT_FOUND"
            self.assert_error(self.admin.get(self.path(suffix, document_id=unknown), params=params), 404, code)
        self.assert_error(self.admin.get(self.path(), params={"version": 99}), 404, "PDF_VERSION_NOT_FOUND")
        for suffix in ("/file", "/pages/1", "/bundle"):
            self.assert_error(self.admin.get(self.path(suffix), params={**self.artifact_params(), "version": 99}),
                              404, "PDF_VERSION_NOT_FOUND")
        for page in (0, 3):
            self.assert_error(self.admin.get(self.path(f"/pages/{page}"), params=self.artifact_params()),
                              404, "PDF_PAGE_NOT_FOUND")

    def test_metadata_pages_are_bounded_and_do_not_claim_artifact_verification(self):
        self.publish()
        second, _ = self.prepare_version(2)
        self.publish(self.registration(second, previous_version=1,
                                       previous_source_sha256=self.prepared["request"]["source_sha256"]))
        other = "pdf-api-" + uuid.uuid4().hex[:20]
        other_prepared, _ = self.prepare_version(1, document_id=other)
        self.publish(self.registration(other_prepared))
        listing = self.admin.get("/v1/pdf-snapshots", params={"limit": 1, "offset": 0})
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assert_private_response(listing)
        self.assertEqual(listing.json()["limit"], 1)
        self.assertEqual(listing.json()["offset"], 0)
        self.assertEqual(len(listing.json()["snapshots"]), 1)
        self.assertTrue(listing.json()["has_more"])
        self.assertIs(listing.json()["snapshots"][0]["artifact_integrity_checked"], False)
        defaults = self.admin.get("/v1/pdf-snapshots")
        self.assertEqual(defaults.status_code, 200, defaults.text)
        self.assertEqual(defaults.json()["limit"], 20)
        self.assertEqual(defaults.json()["offset"], 0)
        history = self.admin.get(self.path("/versions"), params={"limit": 1, "offset": 0})
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["current_version"], 2)
        self.assertEqual(len(history.json()["versions"]), 1)
        self.assertTrue(history.json()["has_more"])
        self.assertIs(history.json()["versions"][0]["artifact_integrity_checked"], False)
        later = self.admin.get(self.path("/versions"), params={"limit": 1, "offset": 1})
        self.assertEqual(later.status_code, 200, later.text)
        self.assertFalse(later.json()["has_more"])
        self.assertNotEqual(history.json()["versions"][0]["document_version"],
                            later.json()["versions"][0]["document_version"])
        empty = self.admin.get(self.path("/versions"), params={"limit": 20, "offset": 10000})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["versions"], [])
        self.assertFalse(empty.json()["has_more"])
        for path in ("/v1/pdf-snapshots", self.path("/versions")):
            for params in ({"limit": 0}, {"limit": 51}, {"offset": -1}, {"offset": 10001}):
                with self.subTest(path=path, params=params):
                    self.assert_error(self.admin.get(path, params=params), 422)

    def test_missing_incomplete_and_changed_prepared_files_fail_closed(self):
        missing = {**self.payload, "job_id": "not-prepared-" + uuid.uuid4().hex[:12]}
        self.assert_error(self.admin.post("/v1/pdf-snapshots", json=missing), 409, "PDF_ATTEMPT_INCOMPLETE")
        attempt = self.store / "attempts" / self.payload["job_id"] / self.payload["attempt_token"]
        complete = attempt / ".complete.json"
        saved = complete.read_bytes()
        complete.unlink()
        self.assert_error(self.admin.post("/v1/pdf-snapshots", json=self.payload), 409, "PDF_ATTEMPT_INCOMPLETE")
        complete.write_bytes(saved)
        page = next(item for item in self.prepared["artifacts"] if item["kind"] == "page_image")
        image_path = self.store / page["path"]
        image_path.chmod(0o600)
        image_path.write_bytes(b"changed page image")
        self.assert_error(self.admin.post("/v1/pdf-snapshots", json=self.payload), 409, "PDF_ARTIFACT_TAMPERED")
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pdf_snapshot_assets WHERE document_id=%s", (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 0)

    def test_tamper_after_registration_blocks_full_reads_but_metadata_is_explicit(self):
        self.publish()
        source_path = self.store / self.prepared["source_path"]
        source_path.chmod(0o600)
        source_path.write_bytes(b"source changed after registration")
        for suffix in ("", "/file", "/pages/1", "/bundle"):
            params = self.artifact_params() if suffix else None
            self.assert_error(self.admin.get(self.path(suffix), params=params), 409, "PDF_ARTIFACT_TAMPERED")
        for path, key in (("/v1/pdf-snapshots", "snapshots"), (self.path("/versions"), "versions")):
            response = self.admin.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            rows = [row for row in response.json()[key] if row["document_id"] == self.document_id]
            self.assertEqual(len(rows), 1)
            self.assertIs(rows[0]["artifact_integrity_checked"], False)

    def test_read_permissions_follow_current_account_roles(self):
        self.publish()
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles=ARRAY['viewer'] WHERE user_id=%s", (self.admin._fixture_users[0],))
        for path, params in self.reads():
            self.assert_error(self.admin.get(path, params=params), 403, "PERMISSION_DENIED")
        self.assert_error(self.admin.post("/v1/pdf-snapshots", json=self.payload), 403, "PERMISSION_DENIED")

    def revoke_session(self, client):
        token_hash = hashlib.sha256(client.cookies.get("datamind_session").encode()).hexdigest()
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=%s", (token_hash,))

    def change_roles(self, client, roles):
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles=%s WHERE user_id=%s", (roles, client._fixture_users[0]))

    def test_artifact_reads_recheck_admin_after_capturing_private_bytes(self):
        self.publish()
        original = publications.read_artifact
        for suffix in ("/file", "/pages/1", "/bundle"):
            client = self.client(("admin",))
            captured = []

            def read_then_demote(*args, **kwargs):
                data = original(*args, **kwargs)
                captured.append(data)
                self.change_roles(client, ["viewer"])
                return data

            with self.subTest(suffix=suffix), patch.object(publications, "read_artifact", side_effect=read_then_demote):
                response = client.get(self.path(suffix), params=self.artifact_params())
                self.assert_error(response, 403, "PERMISSION_DENIED")
                self.assertEqual(len(captured), 1)
                self.assertGreater(len(captured[0]), 100)
                self.assertEqual(response.json(), {"detail": {"error_code": "PERMISSION_DENIED"}})
                self.assertNotIn("attachment", response.headers.get("content-disposition", ""))
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_detail_rechecks_live_session_after_full_integrity_verification(self):
        self.publish()
        original = publications.read_version
        captured = []

        def read_then_revoke(*args, **kwargs):
            row = original(*args, **kwargs)
            captured.append(row)
            self.revoke_session(self.admin)
            return row

        with patch.object(publications, "read_version", side_effect=read_then_revoke):
            response = self.admin.get(self.path())
        self.assert_error(response, 401, "SESSION_INVALID")
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0]["_verified_result"])
        self.assertEqual(response.json(), {"detail": {"error_code": "SESSION_INVALID"}})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_metadata_reads_recheck_live_session_after_database_read(self):
        self.publish()
        for name, path in (("list_assets", "/v1/pdf-snapshots"), ("history", self.path("/versions"))):
            client = self.client(("admin",))
            original = getattr(publications, name)
            captured = []

            def read_then_revoke(*args, **kwargs):
                result = original(*args, **kwargs)
                captured.append(result)
                self.revoke_session(client)
                return result

            with self.subTest(route=path), patch.object(publications, name, side_effect=read_then_revoke):
                response = client.get(path)
                self.assert_error(response, 401, "SESSION_INVALID")
                self.assertEqual(len(captured), 1)
                self.assertIn(self.document_id, json.dumps(captured[0], default=str))
                self.assertEqual(response.json(), {"detail": {"error_code": "SESSION_INVALID"}})
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_committed_post_does_not_leak_result_after_admin_is_removed(self):
        original = publications.register
        captured = []

        def register_then_demote(*args, **kwargs):
            meta = original(*args, **kwargs)
            captured.append(meta)
            self.change_roles(self.admin, ["viewer"])
            return meta

        with patch.object(publications, "register", side_effect=register_then_demote):
            response = self.admin.post("/v1/pdf-snapshots", json=self.payload)
        self.assert_error(response, 403, "PERMISSION_DENIED")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["document_id"], self.document_id)
        self.assertEqual(response.json(), {"detail": {"error_code": "PERMISSION_DENIED"}})
        self.change_roles(self.admin, ["admin"])
        recovered = self.publish()
        self.assertEqual(recovered["manifest_sha256"], captured[0]["manifest_sha256"])
        self.assertEqual(recovered["created_at"], captured[0]["created_at"].isoformat())
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pdf_snapshot_versions WHERE document_id=%s", (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_registration_is_isolated_from_public_documents_and_retrieval_tables(self):
        self.publish()
        response = self.admin.get("/v1/documents")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(self.document_id, response.text)
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            for table in ("document_assets", "document_blocks", "chunks", "document_facts"):
                cur.execute(f"SELECT count(*) FROM {table} WHERE document_id=%s", (self.document_id,))
                self.assertEqual(cur.fetchone()[0], 0, table)
        ref = {"document_id": self.document_id, "document_version": 1,
               "source_sha256": self.prepared["request"]["source_sha256"],
               "parser_version": self.prepared["parser_version"]}
        retriever = KnowledgeRetriever(_write_dsn(), mode="lexical")
        with self.assertRaisesRegex(ValueError, "^DOCUMENT_SELECTION_UNAVAILABLE$"):
            retriever.retrieve("此登记 PDF 是否成为公共知识？", document_refs=[ref])
