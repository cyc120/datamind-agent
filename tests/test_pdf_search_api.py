"""Private PDF retrieval HTTP contract against disposable native/vector data.

Fixture composition deliberately imports no TestCase class into this module and
inherits no publication tests. Synthetic query vectors are injected only inside
the test process; the HTTP request has no provider, model, or embedding override.
"""
import hashlib
import importlib
import math
import unittest
import uuid
from unittest.mock import patch

import psycopg2
from qdrant_client.models import PointStruct

from datamind import api, pdf_index as index, pdf_publications as registry, pdf_vectors as vectors
from datamind.agent import _write_dsn
from datamind.knowledge import KnowledgeRetriever
import test_pdf_index_api as fixtures


SEARCH = "/v1/pdf-knowledge/search"


def query_vector(question, profile, embedder=None):
    return vectors._normalize(next(fixtures.synthetic_embedder([question])))


class PdfSearchAPITest(unittest.TestCase):
    # Compose the established disposable DB/native-PDF/vector setup and its
    # cleanup, without rediscovering any of PdfIndexAPITest's test methods.
    clean_fixtures = fixtures.PdfIndexAPITest.clean_fixtures
    client = fixtures.PdfIndexAPITest.client
    prepare_and_register = fixtures.PdfIndexAPITest.prepare_and_register
    publication_payload = fixtures.PdfIndexAPITest.publication_payload
    submit = fixtures.PdfIndexAPITest.submit
    drain = fixtures.PdfIndexAPITest.drain
    successful_publication = fixtures.PdfIndexAPITest.successful_publication
    publication_path = fixtures.PdfIndexAPITest.publication_path
    current_path = fixtures.PdfIndexAPITest.current_path
    chunks_params = fixtures.PdfIndexAPITest.chunks_params
    assert_safe = fixtures.PdfIndexAPITest.assert_safe
    assert_error = fixtures.PdfIndexAPITest.assert_error
    change_roles = fixtures.PdfIndexAPITest.change_roles
    revoke_session = fixtures.PdfIndexAPITest.revoke_session

    def setUp(self):
        fixtures.PdfIndexAPITest.setUp(self)
        self.search_service = importlib.import_module("datamind.pdf_search")
        self.query_patch = patch.object(vectors, "query_embedding", side_effect=query_vector)
        self.query_mock = self.query_patch.start()
        self.addCleanup(self.query_patch.stop)

    def search_payload(self, *publications, **changes):
        result = {"question": "What dimensions does this private native PDF describe?",
                  "publications": [{"publication_id": p["publication_id"],
                                    "expected_plan_sha256": p["plan_sha256"] or "0" * 64}
                                   for p in publications]}
        result.update(changes)
        return result

    def search(self, *publications, client=None, **changes):
        return (client or self.admin).post(SEARCH, json=self.search_payload(*publications, **changes))

    def new_document(self):
        prepared = self.prepare_and_register(1, document_id="pdf-search-api-" + uuid.uuid4().hex[:16])
        return self.drain(self.submit(self.publication_payload(prepared)))

    def vector_points(self):
        collections = self.vector_client.get_collections().collections
        self.assertEqual(len(collections), 1)
        collection = collections[0].name
        points, _ = self.vector_client.scroll(collection, limit=20, with_vectors=True)
        self.assertGreater(len(points), 0)
        return collection, points

    def test_returns_bound_safe_evidence_and_never_calls_public_rag_or_generation(self):
        publication = self.successful_publication()
        with patch.object(api, "DataMindAgent", side_effect=AssertionError("private retrieval must not generate")), \
                patch.object(KnowledgeRetriever, "retrieve", side_effect=AssertionError("public retrieval must not run")), \
                patch("datamind.providers.test_chat", side_effect=AssertionError("remote provider must not run")):
            response = self.search(publication)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response)
        body = response.json()
        self.assertEqual(body["status"], "retrieved")
        self.assertEqual(body["question"], self.search_payload(publication)["question"])
        self.assertEqual(len(body["selected_publications"]), 1)
        selected = body["selected_publications"][0]
        self.assertEqual(selected["publication_id"], publication["publication_id"])
        self.assertEqual(selected["plan_sha256"], publication["plan_sha256"])
        self.assertIs(body["vector_integrity_checked"], True)
        self.assertIs(body["public_knowledge_visible"], False)
        self.assertIs(body["source_authority_verified"], False)
        self.assertIs(body["answer_generated"], False)
        self.assertNotIn("answer", body)
        self.assertGreater(len(body["hits"]), 0)
        self.assertEqual(body["candidate_chunks"], publication["chunk_count"])
        checked = self.admin.get(self.publication_path(publication, "/chunks"),
                                 params=self.chunks_params(publication)).json()["chunks"]
        chunks = {chunk["chunk_id"]: chunk for chunk in checked}
        scores = []
        for hit in body["hits"]:
            chunk = chunks[hit["chunk_id"]]
            for key, value in chunk.items():
                self.assertEqual(hit[key], value)
            self.assertEqual(hit["publication_id"], publication["publication_id"])
            self.assertEqual(hit["plan_sha256"], publication["plan_sha256"])
            self.assertRegex(hit["profile_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(hit["title"], "私有 PDF 发布测试")
            self.assertTrue(math.isfinite(hit["retrieval_score"]))
            self.assertGreaterEqual(hit["retrieval_score"], self.search_service.MIN_SCORE)
            scores.append(hit["retrieval_score"])
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(body["coverage"]["all_covered"])
        self.assertEqual(body["coverage"]["documents"], [{"publication_id": publication["publication_id"],
                                                         "document_id": self.document_id,
                                                         "hit_count": len(body["hits"])}])
        self.query_mock.assert_called_once()

    def test_evidence_links_return_exact_selected_native_source_and_page(self):
        publication = self.successful_publication()
        response = self.search(publication)
        self.assertEqual(response.status_code, 200, response.text)
        hit = response.json()["hits"][0]
        for key in ("source_url", "page_url"):
            self.assertTrue(hit[key].startswith("/v1/pdf-snapshots/"), hit[key])
            self.assertIn("version=1", hit[key])
            self.assertIn(self.prepared["manifest_sha256"], hit[key])
        source = self.admin.get(hit["source_url"])
        self.assertEqual(source.status_code, 200, source.text)
        self.assertEqual(hashlib.sha256(source.content).hexdigest(), self.prepared["request"]["source_sha256"])
        self.assertEqual(source.headers["cache-control"], "no-store")
        page = self.admin.get(hit["page_url"])
        self.assertEqual(page.status_code, 200, page.text)
        self.assertTrue(page.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(page.headers["cache-control"], "no-store")
        public = self.client()
        self.assert_error(public.get(hit["source_url"]), 401)
        self.assert_error(public.get(hit["page_url"]), 401)

    def test_anonymous_and_all_non_admin_roles_are_denied_before_embedding(self):
        publication = self.successful_publication()
        for client, status in ((self.client(), 401), (self.client(("viewer",)), 403),
                               (self.client(("reviewer",)), 403), (self.client(("approver",)), 403)):
            with self.subTest(status=status, roles=getattr(client, "_fixture_users", [])):
                self.assert_error(self.search(publication, client=client), status)
        self.query_mock.assert_not_called()

    def test_search_requires_csrf_and_same_origin(self):
        publication = self.successful_publication()
        csrf = self.admin.headers.pop("X-CSRF-Token")
        self.assert_error(self.search(publication), 403, "CSRF_REQUIRED")
        self.admin.headers["X-CSRF-Token"] = csrf
        for headers in ({"Origin": "https://untrusted.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assert_error(self.admin.post(SEARCH, json=self.search_payload(publication), headers=headers),
                              403, "ORIGIN_NOT_ALLOWED")
        self.query_mock.assert_not_called()
        response = self.admin.post(SEARCH, json=self.search_payload(publication), headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_strict_question_validation_rejects_whitespace_types_and_unicode_limits(self):
        publication = self.successful_publication()
        for question in (None, True, 1, [], {}, "", " \n\t", "a" * 2001, "\U0001f600" * 2001):
            with self.subTest(question_type=type(question).__name__, length=len(question) if isinstance(question, str) else None):
                self.assert_error(self.search(publication, question=question), 422)
        self.query_mock.assert_not_called()
        for question in ("a", "a" * 2000, "\U0001f600" * 2000):
            response = self.search(publication, question=question)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["question"], question)

    def test_strict_limit_rejects_bool_string_float_and_out_of_range(self):
        publication = self.successful_publication()
        for limit in (None, True, False, "8", 8.0, 0, 3, 13):
            with self.subTest(limit=limit):
                self.assert_error(self.search(publication, limit=limit), 422)
        self.query_mock.assert_not_called()
        for limit in (4, 12):
            response = self.search(publication, limit=limit)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertLessEqual(len(response.json()["hits"]), limit)

    def test_rejects_unknown_fields_and_all_injection_overrides(self):
        publication = self.successful_publication()
        for key, value in (("actor", {"user_id": "spoof"}), ("roles", ["admin"]), ("embedder", "synthetic"),
                           ("model", "external"), ("base_url", "https://outside.invalid"),
                           ("source_path", "/outside/private.pdf"), ("min_score", 0), ("public_knowledge_visible", True)):
            with self.subTest(key=key):
                self.assert_error(self.search(publication, **{key: value}), 422)
        selected = self.search_payload(publication)
        selected["publications"][0]["profile"] = {"synthetic": True}
        self.assert_error(self.admin.post(SEARCH, json=selected), 422)
        for key in ("question", "publications"):
            missing = self.search_payload(publication)
            missing.pop(key)
            self.assert_error(self.admin.post(SEARCH, json=missing), 422)
        self.query_mock.assert_not_called()

    def test_strict_selection_rejects_missing_malformed_and_duplicate_identifiers(self):
        publication = self.successful_publication()
        selected = self.search_payload(publication)["publications"][0]
        choices = (None, {}, [], "not-a-list", [selected] * 2, [selected] * 5,
                   [{"publication_id": publication["publication_id"]}],
                   [{"expected_plan_sha256": publication["plan_sha256"]}],
                   [{**selected, "publication_id": True}], [{**selected, "publication_id": "../escape"}],
                   [{**selected, "publication_id": "PDF-PUB-" + "a" * 32}],
                   [{**selected, "expected_plan_sha256": True}], [{**selected, "expected_plan_sha256": "A" * 64}],
                   [{**selected, "expected_plan_sha256": "short"}])
        for publications in choices:
            with self.subTest(publications=publications):
                self.assert_error(self.search(publication, publications=publications), 422)
        self.query_mock.assert_not_called()

    def test_same_document_multiple_versions_rejected_before_query_embedding(self):
        first = self.successful_publication()
        next_pdf = self.prepare_and_register(2, previous_sha=self.prepared["request"]["source_sha256"])
        second = self.drain(self.submit(self.publication_payload(next_pdf, previous_publication_id=first["publication_id"])))
        self.assert_error(self.search(first, second), 422)
        self.query_mock.assert_not_called()

    def test_multifile_retrieval_reserves_one_above_threshold_hit_per_document(self):
        publications = [self.successful_publication()] + [self.new_document() for _ in range(3)]
        response = self.search(*publications, limit=4)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response)
        body = response.json()
        self.assertEqual(len(body["hits"]), 4)
        self.assertEqual({hit["publication_id"] for hit in body["hits"]}, {p["publication_id"] for p in publications})
        self.assertTrue(body["coverage"]["all_covered"])
        self.assertEqual({d["hit_count"] for d in body["coverage"]["documents"]}, {1})
        self.assertEqual(body["candidate_chunks"], sum(p["chunk_count"] for p in publications))
        self.assertEqual(self.query_mock.call_count, 1, "one matching embedding profile requires one query vector")

    def test_third_database_search_slot_is_busy_and_release_restores_search(self):
        publication = self.successful_publication()
        with self.search_service._search_slot(_write_dsn()), self.search_service._search_slot(_write_dsn()):
            self.assert_error(self.search(publication), 503, "PDF_SEARCH_BUSY")
            self.query_mock.assert_not_called()
        response = self.search(publication)
        self.assertEqual(response.status_code, 200, response.text)
        self.query_mock.assert_called_once()

    def test_unselected_publication_and_exact_query_orphan_never_enter_results(self):
        publication = self.successful_publication()
        unselected = self.new_document()
        collection, _ = self.vector_points()
        orphan_id = str(uuid.uuid4())
        orphan_document = "pdf-search-orphan-" + uuid.uuid4().hex[:16]
        question = self.search_payload(publication)["question"]
        profile = vectors._profile(synthetic=True)
        self.vector_client.upsert(collection, points=[PointStruct(
            id=orphan_id, vector=query_vector(question, profile),
            payload={"document_id": orphan_document, "publication_id": "pdf-pub-" + uuid.uuid4().hex,
                     "chunk_id": "orphan-chunk", "text": "exact-query-vector orphan must remain private"})], wait=True)
        with patch.object(self.vector_client, "search", side_effect=AssertionError("unfiltered collection search forbidden")), \
                patch.object(self.vector_client, "upsert", side_effect=AssertionError("retrieval must not write points")), \
                patch.object(self.vector_client, "delete", side_effect=AssertionError("retrieval must not delete points")), \
                patch.object(self.vector_client, "set_payload", side_effect=AssertionError("retrieval must not mutate payload")), \
                patch.object(self.vector_client, "create_collection", side_effect=AssertionError("retrieval must not create collections")):
            response = self.search(publication)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response)
        self.assertEqual({hit["publication_id"] for hit in response.json()["hits"]}, {publication["publication_id"]})
        self.assertNotIn(unselected["document_id"], response.text)
        self.assertNotIn(unselected["publication_id"], response.text)
        self.assertNotIn(orphan_document, response.text)
        self.assertNotIn("orphan-chunk", response.text)
        self.assertEqual(response.json()["candidate_chunks"], publication["chunk_count"])
        self.assertEqual(self.vector_client.count(collection, exact=True).count,
                         publication["chunk_count"] + unselected["chunk_count"] + 1)

    def test_aggregate_admission_and_actual_plan_budgets_reject_before_embedding(self):
        first, second = self.successful_publication(), self.new_document()
        self.assertEqual((first["chunk_count"], second["chunk_count"]), (2, 2))
        for name, maximum in (("MAX_CANDIDATES", 3), ("MAX_TEXT_BYTES", 1), ("MAX_PLAN_BYTES", 1)):
            with self.subTest(budget=name), patch.object(self.search_service, name, maximum):
                self.assert_error(self.search(first, second), 422, "PDF_SEARCH_SELECTION_TOO_LARGE")
        # Admission metadata is not the sole resource guard: independently
        # validated actual frozen plans must also fit the aggregate budgets.
        with patch.object(self.search_service, "_admit", return_value=None), \
                patch.object(self.search_service, "MAX_CANDIDATES", 1):
            self.assert_error(self.search(first), 422, "PDF_SEARCH_SELECTION_TOO_LARGE")
        self.query_mock.assert_not_called()

    def test_different_complete_embedding_profiles_are_rejected_before_embedding(self):
        first = self.successful_publication()
        prepared = self.prepare_and_register(1, document_id="pdf-search-api-" + uuid.uuid4().hex[:16])
        second = self.submit(self.publication_payload(prepared))
        # This tests profile selection, not real production model inference.
        # Freeze a production-marked profile through the private internal model
        # adapter seam, using deterministic vectors and no HTTP model override.
        def production_fixture(texts):
            return vectors._profile(synthetic=False), list(fixtures.synthetic_embedder(texts))

        with patch.object(vectors, "_production_embedding", side_effect=production_fixture):
            index.drain(_write_dsn(), second["publication_id"], workers=1,
                        embedder=None, client=self.vector_client)
        response = self.admin.get(self.publication_path(second))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["publication"]["status"], "published", response.text)
        second = response.json()["publication"]
        self.assertEqual(len(self.vector_client.get_collections().collections), 2)
        self.assert_error(self.search(first, second), 409, "PDF_SEARCH_PROFILE_MISMATCH")
        self.query_mock.assert_not_called()

    def test_explicit_historical_selection_survives_current_snapshot_and_publication_advance(self):
        first = self.successful_publication()
        initial = self.search(first)
        self.assertEqual(initial.status_code, 200, initial.text)
        next_pdf = self.prepare_and_register(2, previous_sha=self.prepared["request"]["source_sha256"])
        self.assert_error(self.admin.get(self.current_path()), 409, "PDF_PUBLICATION_SNAPSHOT_CHANGED")
        historic = self.search(first)
        self.assertEqual(historic.status_code, 200, historic.text)
        self.assertEqual(historic.json(), initial.json())
        second = self.drain(self.submit(self.publication_payload(next_pdf, previous_publication_id=first["publication_id"])))
        current = self.search(second)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual({hit["document_version"] for hit in current.json()["hits"]}, {2})
        historic = self.search(first)
        self.assertEqual(historic.status_code, 200, historic.text)
        self.assertEqual(historic.json(), initial.json())

    def test_private_head_advancing_during_query_keeps_explicit_historic_result(self):
        first = self.successful_publication()
        next_pdf = self.prepare_and_register(2, previous_sha=self.prepared["request"]["source_sha256"])
        second = self.submit(self.publication_payload(next_pdf, previous_publication_id=first["publication_id"]))
        initial = self.search(first)
        self.assertEqual(initial.status_code, 200, initial.text)

        def embed_then_publish_new_head(question, profile, embedder=None):
            result = query_vector(question, profile, embedder)
            index.drain(_write_dsn(), second["publication_id"], workers=1,
                        embedder=fixtures.synthetic_embedder, client=self.vector_client)
            return result

        self.query_mock.side_effect = embed_then_publish_new_head
        historic = self.search(first)
        self.assertEqual(historic.status_code, 200, historic.text)
        self.assertEqual(historic.json(), initial.json())
        current = self.admin.get(self.current_path())
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["publication"]["publication_id"], second["publication_id"])

    def test_unknown_unpublished_and_wrong_frozen_plan_are_denied(self):
        queued = self.submit()
        self.assert_error(self.search(queued), 409, "PDF_PUBLICATION_NOT_PUBLISHED")
        unknown = {"publication_id": "pdf-pub-" + uuid.uuid4().hex, "plan_sha256": "0" * 64}
        self.assert_error(self.search(unknown), 404, "PDF_PUBLICATION_NOT_FOUND")
        publication = self.drain(queued)
        wrong = {**publication, "plan_sha256": "0" * 64}
        self.assert_error(self.search(wrong), 409, "PDF_INDEX_SELECTION_CHANGED")
        self.query_mock.assert_not_called()

    def test_missing_vector_point_fails_closed_even_when_other_chunks_exist(self):
        publication = self.successful_publication()
        collection, points = self.vector_points()
        self.vector_client.delete(collection_name=collection, points_selector=[points[0].id], wait=True)
        self.assert_error(self.search(publication), 409, "PDF_INDEX_VECTOR_MISSING")

    def test_tampered_actual_vector_payload_fails_closed_without_repair(self):
        publication = self.successful_publication()
        collection, points = self.vector_points()
        self.vector_client.set_payload(collection_name=collection, payload={"acl": "public"}, points=[points[0].id], wait=True)
        self.assert_error(self.search(publication), 409)
        after = self.vector_client.retrieve(collection, ids=[points[0].id], with_payload=True)[0]
        self.assertEqual(after.payload["acl"], "public", "read-only retrieval must not repair vector records")

    def test_tampered_sealed_source_fails_closed(self):
        publication = self.successful_publication()
        source = self.store / self.prepared["source_path"]
        source.chmod(0o600)
        source.write_bytes(b"tampered native source")
        self.assert_error(self.search(publication), 409, "PDF_ARTIFACT_TAMPERED")
        self.query_mock.assert_not_called()

    def test_publication_changed_during_query_embedding_is_rechecked_before_return(self):
        publication = self.successful_publication()

        def embed_then_corrupt(question, profile, embedder=None):
            result = query_vector(question, profile, embedder)
            with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                cur.execute("UPDATE pdf_knowledge_requests SET vector_plan_sha256=%s WHERE publication_id=%s",
                            ("0" * 64, publication["publication_id"]))
            return result

        self.query_mock.side_effect = embed_then_corrupt
        self.assert_error(self.search(publication), 409)
        self.query_mock.assert_called_once()

    def test_native_source_changed_during_query_embedding_is_rechecked_before_return(self):
        publication = self.successful_publication()

        def embed_then_corrupt(question, profile, embedder=None):
            result = query_vector(question, profile, embedder)
            source = self.store / self.prepared["source_path"]
            source.chmod(0o600)
            source.write_bytes(b"native source changed during embedding")
            return result

        self.query_mock.side_effect = embed_then_corrupt
        self.assert_error(self.search(publication), 409, "PDF_ARTIFACT_TAMPERED")
        self.query_mock.assert_called_once()

    def test_final_joint_vector_capture_detects_first_document_deletion_after_last_file_check(self):
        first, second = self.successful_publication(), self.new_document()
        collection, points = self.vector_points()
        first_point = next(point for point in points if point.payload["publication_id"] == first["publication_id"])
        original = self.search_service._selected
        captured = []

        def select_then_delete(ref, *args, **kwargs):
            result = original(ref, *args, **kwargs)
            captured.append(ref["publication_id"])
            if len(captured) == 4:
                self.assertEqual(ref["publication_id"], second["publication_id"])
                self.vector_client.delete(collection_name=collection, points_selector=[first_point.id], wait=True)
            return result

        with patch.object(self.search_service, "_selected", side_effect=select_then_delete), \
                patch.object(self.vector_client, "retrieve", wraps=self.vector_client.retrieve) as retrieves:
            response = self.search(first, second)
        self.assert_error(response, 409, "PDF_INDEX_VECTOR_MISSING")
        self.assertEqual(captured, [first["publication_id"], second["publication_id"]] * 2)
        self.assertEqual(retrieves.call_count, 2)
        expected_points = {str(point.id) for point in points}
        for call in retrieves.call_args_list:
            self.assertEqual({str(point_id) for point_id in call.kwargs["ids"]}, expected_points)
            self.assertEqual(len(call.kwargs["ids"]), first["chunk_count"] + second["chunk_count"])
        self.query_mock.assert_called_once()

    def test_plan_growing_after_admission_is_rejected_before_full_body_validation(self):
        publication = self.successful_publication()
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT octet_length(convert_to(vector_plan::text,'UTF8')) FROM pdf_knowledge_requests WHERE publication_id=%s",
                        (publication["publication_id"],))
            plan_bytes = cur.fetchone()[0]
        original = self.search_service._admit
        admitted = []

        def admit_then_expand(refs, dsn):
            result = original(refs, dsn)
            admitted.append(publication["publication_id"])
            with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                cur.execute("""UPDATE pdf_knowledge_requests
                    SET vector_plan=jsonb_set(vector_plan,'{oversized}',to_jsonb(%s::text),true)
                    WHERE publication_id=%s""", ("oversized-body-" + "a" * 4096, publication["publication_id"]))
            return result

        with patch.object(self.search_service, "MAX_PLAN_BYTES", plan_bytes + 128), \
                patch.object(self.search_service, "_admit", side_effect=admit_then_expand), \
                patch.object(index, "_checked", side_effect=AssertionError("oversized row body must not be decoded/validated")) as checked:
            response = self.search(publication)
        self.assert_error(response, 422, "PDF_SEARCH_SELECTION_TOO_LARGE")
        self.assertEqual(admitted, [publication["publication_id"]])
        checked.assert_not_called()
        self.query_mock.assert_not_called()

    def test_final_lightweight_db_capture_detects_changed_result_evidence(self):
        publication = self.successful_publication()
        original = self.search_service._recheck_metadata
        captures = []

        def corrupt_then_recheck(rows, dsn):
            captures.append([row["publication_id"] for row in rows])
            with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                cur.execute("UPDATE pdf_knowledge_requests SET result_sha256=%s WHERE publication_id=%s",
                            ("0" * 64, publication["publication_id"]))
            return original(rows, dsn)

        with patch.object(self.search_service, "_recheck_metadata", side_effect=corrupt_then_recheck), \
                patch.object(vectors, "verify_selection", wraps=vectors.verify_selection) as captures_vectors:
            response = self.search(publication)
        self.assert_error(response, 409, "PDF_PUBLICATION_EVIDENCE_CHANGED")
        self.assertEqual(captures, [[publication["publication_id"]]])
        self.assertEqual(captures_vectors.call_count, 2)
        self.query_mock.assert_called_once()

    def test_final_db_capture_detects_raw_plan_mutation_with_unchanged_hash_labels(self):
        publication = self.successful_publication()
        original = self.search_service._recheck_metadata
        captures = []

        def change_raw_plan_then_recheck(rows, dsn):
            captures.append([row["publication_id"] for row in rows])
            before = rows[0]
            with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                cur.execute("""UPDATE pdf_knowledge_requests
                    SET vector_plan=jsonb_set(vector_plan,'{chunks,0,text}',to_jsonb(%s::text),true)
                    WHERE publication_id=%s RETURNING vector_plan_sha256,result_sha256""",
                            ("raw plan text changed after all file/vector checks", publication["publication_id"]))
                self.assertEqual(cur.fetchone(), (before["vector_plan_sha256"], before["result_sha256"]))
            return original(rows, dsn)

        with patch.object(self.search_service, "_recheck_metadata", side_effect=change_raw_plan_then_recheck), \
                patch.object(vectors, "verify_selection", wraps=vectors.verify_selection) as captures_vectors:
            response = self.search(publication)
        self.assert_error(response, 409, "PDF_PUBLICATION_EVIDENCE_CHANGED")
        self.assertEqual(captures, [[publication["publication_id"]]])
        self.assertEqual(captures_vectors.call_count, 2)
        self.query_mock.assert_called_once()

    def test_final_db_capture_detects_raw_snapshot_mutation_with_unchanged_hash_labels(self):
        publication = self.successful_publication()
        original = self.search_service._recheck_metadata
        captures = []

        def change_raw_snapshot_then_recheck(rows, dsn):
            captures.append([row["publication_id"] for row in rows])
            before = rows[0]
            with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
                cur.execute("""UPDATE pdf_snapshot_versions
                    SET snapshot=jsonb_set(snapshot,'{counts,text_utf8_bytes}',to_jsonb(%s::int),true)
                    WHERE document_id=%s AND document_version=%s RETURNING snapshot_sha256,registration_sha256""",
                            (1, publication["document_id"], publication["document_version"]))
                self.assertEqual(cur.fetchone(), (before["target_snapshot_sha256"], before["target_registration_sha256"]))
            return original(rows, dsn)

        with patch.object(self.search_service, "_recheck_metadata", side_effect=change_raw_snapshot_then_recheck), \
                patch.object(vectors, "verify_selection", wraps=vectors.verify_selection) as captures_vectors:
            response = self.search(publication)
        self.assert_error(response, 409, "PDF_PUBLICATION_EVIDENCE_CHANGED")
        self.assertEqual(captures, [[publication["publication_id"]]])
        self.assertEqual(captures_vectors.call_count, 2)
        self.query_mock.assert_called_once()

    def test_low_similarity_is_empty_with_explicit_uncovered_document(self):
        publication = self.successful_publication()
        self.query_mock.side_effect = lambda question, profile, embedder=None: [-v for v in query_vector(question, profile)]
        response = self.search(publication)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response)
        body = response.json()
        self.assertEqual(body["status"], "empty")
        self.assertEqual(body["hits"], [])
        self.assertFalse(body["coverage"]["all_covered"])
        self.assertEqual(body["coverage"]["documents"], [{"publication_id": publication["publication_id"],
                                                         "document_id": self.document_id, "hit_count": 0}])
        self.assertGreater(body["candidate_chunks"], 0)
        self.assertIs(body["vector_integrity_checked"], True)
        self.assertIs(body["answer_generated"], False)

    def test_role_demotion_after_service_captures_result_returns_no_private_data(self):
        publication = self.successful_publication()
        original = self.search_service.search
        captured = []

        def search_then_demote(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append(result)
            self.change_roles(self.admin, ["viewer"])
            return result

        with patch.object(self.search_service, "search", side_effect=search_then_demote):
            response = self.search(publication)
        self.assert_error(response, 403, "PERMISSION_DENIED")
        self.assertEqual(response.json(), {"detail": {"error_code": "PERMISSION_DENIED"}})
        self.assertEqual(len(captured), 1)

    def test_revoked_session_after_service_captures_result_returns_no_private_data(self):
        publication = self.successful_publication()
        original = self.search_service.search

        def search_then_revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            self.revoke_session(self.admin)
            return result

        with patch.object(self.search_service, "search", side_effect=search_then_revoke):
            response = self.search(publication)
        self.assert_error(response, 401, "SESSION_INVALID")
        self.assertEqual(response.json(), {"detail": {"error_code": "SESSION_INVALID"}})

    def test_final_authorization_occurs_after_search_response_encoding(self):
        publication = self.successful_publication()
        original = api.jsonable_encoder
        encoded = []

        def encode_then_demote(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            if isinstance(value, dict) and value.get("status") in {"retrieved", "empty"} and "selected_publications" in value:
                encoded.append(result)
                self.change_roles(self.admin, ["viewer"])
            return result

        with patch.object(api, "jsonable_encoder", side_effect=encode_then_demote):
            response = self.search(publication)
        self.assert_error(response, 403, "PERMISSION_DENIED")
        self.assertEqual(response.json(), {"detail": {"error_code": "PERMISSION_DENIED"}})
        self.assertEqual(len(encoded), 1)

    def test_search_changes_no_public_metadata_and_private_document_is_unavailable_to_legacy_rag(self):
        publication = self.successful_publication()
        response = self.search(publication)
        self.assertEqual(response.status_code, 200, response.text)
        public = self.client()
        documents = public.get("/v1/documents")
        self.assertEqual(documents.status_code, 200, documents.text)
        self.assertNotIn(self.document_id, documents.text)
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            for table in ("document_assets", "document_blocks", "chunks", "document_facts"):
                cur.execute(f"SELECT count(*) FROM {table} WHERE document_id=%s", (self.document_id,))
                self.assertEqual(cur.fetchone()[0], 0, table)
            cur.execute("SELECT count(*) FROM pdf_knowledge_requests WHERE document_id=%s", (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 1)
        selected = {"document_id": self.document_id, "document_version": 1,
                    "source_sha256": self.prepared["request"]["source_sha256"], "parser_version": self.prepared["parser_version"]}
        with self.assertRaisesRegex(ValueError, "^DOCUMENT_SELECTION_UNAVAILABLE$"):
            KnowledgeRetriever(_write_dsn(), mode="lexical").retrieve("private PDF", document_refs=[selected])


if __name__ == "__main__":
    unittest.main()
