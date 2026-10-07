"""Explicit file scopes, balanced evidence and generation-time version guards.

Run via scripts/test_isolated.py; the generators never call a model provider.
"""
import hashlib
import unittest
import uuid
from unittest.mock import patch

import psycopg2

from datamind.knowledge import KnowledgeRetriever, document_selection
from datamind.rag_engine import ask_documents


WRITE_DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev"
PARSER = "multifile-test-v1"


class ExplicitSelectionValidationTest(unittest.TestCase):
    def test_empty_duplicate_oversized_and_forged_refs_are_rejected(self):
        valid = {"document_id": "doc-scope", "document_version": 1,
                 "source_sha256": "a" * 64, "parser_version": PARSER}
        cases = [[], [valid, valid], [dict(valid, document_id=f"doc-{i}") for i in range(9)],
                 [dict(valid, document_version=True)], [dict(valid, source_sha256="../source")],
                 [dict(valid, parser_version="")], [dict(valid, acl=["public"])]]
        for refs in cases:
            with self.subTest(refs=refs), self.assertRaisesRegex(ValueError, "DOCUMENT_SELECTION_INVALID"):
                document_selection(refs)


class MultiFileRAGIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:12]
        self.documents = []
        self.a = self.add_document("a", "并发工艺温度规范", ["并发工艺温度规范明确要求温度保持在 42 度。"] * 40)
        self.b = self.add_document("b", "检验报告", ["并发工艺的检验记录温度为 43 度。"])
        self.outside = self.add_document("outside", "未选择的并发工艺", ["并发工艺温度是 99 度。"])
        self.refs = [self.a, self.b]
        self.retriever = KnowledgeRetriever(mode="lexical")

    def add_document(self, label, title, texts, *, acl=None, needs_review=False):
        doc_id = f"test-multifile-{self.tag}-{label}"
        sha = hashlib.sha256(doc_id.encode()).hexdigest()
        self.documents.append(doc_id)
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO document_assets(document_id,title,collection,file_type,origin,
                sha256,object_uri,doc_version,acl,status,needs_review,page_count,parser_version)
                VALUES (%s,%s,'demo_kb','pdf','curated',%s,'unavailable',1,%s,'indexed',%s,1,%s)""",
                (doc_id, title, sha, acl or ["public"], needs_review, PARSER))
            for index, text in enumerate(texts):
                block_id = f"{doc_id}-b{index}"
                cur.execute("""INSERT INTO document_blocks(block_id,document_id,page_no,bbox,block_type,text,
                    reading_order,parser_version,content_hash) VALUES (%s,%s,1,'[0,0,100,20]','paragraph',%s,%s,%s,%s)""",
                    (block_id, doc_id, text, index, PARSER, hashlib.sha256(text.encode()).hexdigest()))
                cur.execute("""INSERT INTO chunks(chunk_id,document_id,document_version,chunk_index,
                    text_for_embedding,exact_text,embedding_model,content_hash,page_start,page_end,block_ids,acl)
                    VALUES (%s,%s,1,%s,%s,%s,'test',%s,1,1,%s,'{public}')""",
                    (f"{doc_id}-c{index}", doc_id, index, text, text,
                     hashlib.sha256(text.encode()).hexdigest(), [block_id]))
        return {"document_id": doc_id, "document_version": 1, "source_sha256": sha, "parser_version": PARSER}

    def tearDown(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM document_assets WHERE document_id=ANY(%s)", (self.documents,))

    def query(self, refs=None, question="并发工艺温度规范", **kwargs):
        return self.retriever.retrieve(question, collections=["demo_kb"],
                                       document_refs=self.refs if refs is None else refs, **kwargs)

    def generator(self, question, hits, feedback):
        first = {}
        for hit in hits:
            first.setdefault(hit["document_id"], hit)
        return {"abstain": False, "claims": [{"text": hit["exact_text"], "citations": [
            {"chunk_id": hit["chunk_id"], "quote": hit["exact_text"]}]} for hit in first.values()]}

    def ask(self, generator=None, refs=None):
        return ask_documents("并发工艺温度规范", collections=["demo_kb"], retriever=self.retriever,
                             document_refs=self.refs if refs is None else refs,
                             generator=generator or self.generator)

    def test_selected_ids_override_question_titles_and_exclude_other_files(self):
        result = self.query(question="根据《未选择的并发工艺》说明并发工艺温度")
        self.assertEqual({hit["document_id"] for hit in result["hits"]}, {ref["document_id"] for ref in self.refs})
        self.assertTrue(result["coverage"]["all_covered"])
        self.assertEqual([item["source_sha256"] for item in result["selected_documents"]], [ref["source_sha256"] for ref in self.refs])

    def test_missing_private_and_review_pending_files_fail_closed(self):
        private = self.add_document("private", "私有并发工艺", ["并发工艺温度 99 度。"], acl=["private"])
        review = self.add_document("review", "复核中并发工艺", ["并发工艺温度 99 度。"], needs_review=True)
        missing = dict(self.a, document_id=f"test-missing-{self.tag}")
        for ref in (private, review, missing):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "DOCUMENT_SELECTION_UNAVAILABLE"):
                self.query([self.a, ref])

    def test_version_source_and_parser_mismatches_are_rejected(self):
        for change in ({"document_version": 2}, {"source_sha256": "0" * 64}, {"parser_version": "old-parser"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "DOCUMENT_SELECTION_CHANGED"):
                self.query([dict(self.a, **change), self.b])

    def test_each_file_keeps_relevant_evidence_under_top_k_competition(self):
        result = self.query()
        self.assertEqual(len(result["hits"]), 5)
        self.assertIn(self.b["document_id"], {hit["document_id"] for hit in result["hits"]})
        self.assertTrue(result["coverage"]["all_covered"])
        self.assertEqual(result["coverage"]["documents"][0]["eligible_chunks"], 40)

    def test_eight_selected_files_fit_the_bounded_context(self):
        refs = [*self.refs, *[self.add_document(f"extra{i}", "检验报告", [f"并发工艺温度记录为 {50+i} 度。"])
                              for i in range(6)]]
        result = self.query(refs)
        self.assertEqual(len(result["hits"]), 8)
        self.assertEqual({hit["document_id"] for hit in result["hits"]}, {ref["document_id"] for ref in refs})
        self.assertTrue(result["coverage"]["all_covered"])

    def test_missing_relevant_evidence_abstains_before_model(self):
        unrelated = self.add_document("unrelated", "杂项", ["苹果香蕉春天夏天。"])
        result = self.ask(refs=[self.a, unrelated], generator=lambda *_: self.fail("must not call model"))
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["error_code"], "MULTIFILE_EVIDENCE_INCOMPLETE")
        self.assertEqual(result["retrieval"]["coverage"]["missing_document_ids"], [unrelated["document_id"]])

    def test_tampered_file_evidence_is_not_counted_as_covered(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute("UPDATE chunks SET content_hash=%s WHERE document_id=%s", ("0" * 64, self.b["document_id"]))
        result = self.ask(generator=lambda *_: self.fail("must not call model"))
        self.assertEqual(result["error_code"], "MULTIFILE_EVIDENCE_INCOMPLETE")
        self.assertEqual(result["retrieval"]["coverage"]["documents"][1]["eligible_chunks"], 0)

    def test_citations_preserve_separate_source_versions_pages_and_blocks(self):
        result = self.ask()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer_coverage"]["uncited_document_ids"], [])
        citations = {item["document_id"]: item for item in result["citations"]}
        for ref in self.refs:
            citation = citations[ref["document_id"]]
            self.assertEqual(citation["document_version"], 1)
            self.assertEqual(citation["document_sha256"], ref["source_sha256"])
            self.assertEqual(citation["parser_version"], PARSER)
            self.assertEqual(citation["page_start"], 1)
            self.assertTrue(citation["block_ids"])
            self.assertTrue(citation["source_blocks"])
            self.assertIn("expected_version=1", citation["source_url"])
            self.assertIn("expected_sha256=" + ref["source_sha256"], citation["page_url"])
        self.assertEqual({claim["verification"]["semantic_entailment"] for claim in result["claims"]}, {"not_independently_verified"})

    def test_excluded_file_citation_is_refused_even_if_generator_requests_it(self):
        def excluded(question, hits, feedback):
            return {"claims": [{"text": "并发工艺温度是 99 度。", "citations": [
                {"chunk_id": self.outside["document_id"] + "-c0", "quote": "并发工艺温度是 99 度。"}]}]}
        result = self.ask(excluded)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "RAG_VERIFICATION_FAILED")
        self.assertEqual([item["error_code"] for item in result["attempts"]], ["RAG_UNKNOWN_CITATION"] * 2)

    def test_dense_hits_outside_selected_files_cannot_enter_context(self):
        self.retriever.mode = "hybrid"
        with patch.object(self.retriever, "_dense", return_value=[(self.outside["document_id"] + "-c0", .99)]):
            result = self.query()
        self.assertNotIn(self.outside["document_id"], {hit["document_id"] for hit in result["hits"]})

    def test_source_version_change_during_generation_refuses_completed_answer(self):
        def changed(question, hits, feedback):
            draft = self.generator(question, hits, feedback)
            with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
                cur.execute("UPDATE document_assets SET doc_version=2 WHERE document_id=%s", (self.b["document_id"],))
            return draft
        result = self.ask(changed)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "DOCUMENT_SELECTION_CHANGED")
        self.assertEqual(result["citations"], [])

    def test_same_head_chunk_refresh_during_generation_refuses_old_content(self):
        def changed(question, hits, feedback):
            draft = self.generator(question, hits, feedback)
            text = "并发工艺的检验记录温度为 44 度。"
            with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
                cur.execute("UPDATE chunks SET exact_text=%s,content_hash=%s WHERE document_id=%s",
                            (text, hashlib.sha256(text.encode()).hexdigest(), self.b["document_id"]))
            return draft
        result = self.ask(changed)
        self.assertEqual(result["error_code"], "DOCUMENT_SELECTION_CHANGED")

    def test_access_revocation_during_generation_refuses_old_evidence(self):
        def changed(question, hits, feedback):
            draft = self.generator(question, hits, feedback)
            with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
                cur.execute("UPDATE document_assets SET acl='{private}' WHERE document_id=%s", (self.b["document_id"],))
            return draft
        result = self.ask(changed)
        self.assertEqual(result["error_code"], "DOCUMENT_SELECTION_CHANGED")

    def test_source_locator_change_during_generation_refuses_old_evidence(self):
        def changed(question, hits, feedback):
            draft = self.generator(question, hits, feedback)
            with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
                cur.execute("UPDATE document_blocks SET bbox='[0,0,110,20]' WHERE document_id=%s", (self.b["document_id"],))
            return draft
        result = self.ask(changed)
        self.assertEqual(result["error_code"], "DOCUMENT_SELECTION_CHANGED")
