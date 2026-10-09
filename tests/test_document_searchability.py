"""A selected document must be able to contribute evidence.

Audit R05 (`reports/project_audit_20261009_561316c.md`): `approve` marked an upload
`indexed` as soon as it had contract blocks, but this pipeline never builds chunks, so the
document appeared in the Q&A file list while `eligible_chunks` was 0 and `all_covered`
false. The Q&A selector filtering on `searchable` is only the client-side half — a
selection can also go stale between the list read and the submit, so the server refuses a
selected document that has no eligible chunk instead of answering from nothing.
"""
import hashlib
import unittest
import uuid

import psycopg2

from datamind.agent import _read_dsn, _write_dsn
from datamind.knowledge import KnowledgeRetriever

COLLECTION = "demo_kb"


class SelectedDocumentSearchabilityTest(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.document_id = f"doc-srch-{uuid.uuid4().hex[:12]}"
        self.chunk_id = f"chk-srch-{uuid.uuid4().hex[:12]}"
        self.text = "退款在验收后三个工作日内原路退回。"
        self.addCleanup(self.cleanup)

    def _asset(self, *, status="indexed", needs_review=False, collection=COLLECTION):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO document_assets(document_id, title, file_type, sha256, status,
                acl, needs_review, collection, doc_version, parser_version)
                VALUES (%s, 'searchability', 'pdf', %s, %s, '{public}', %s, %s, 1, 'test-v1')""",
                        (self.document_id, "a" * 64, status, needs_review, collection))

    def _chunk(self):
        digest = hashlib.sha256(self.text.encode()).hexdigest()
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO chunks(chunk_id, document_id, document_version, chunk_index,
                chunk_type, page_start, page_end, block_ids, text_for_embedding, exact_text,
                acl, embedding_model, content_hash)
                VALUES (%s,%s,1,0,'prose',1,1,'{}',%s,%s,'{public}','test-embedding',%s)""",
                        (self.chunk_id, self.document_id, self.text, self.text, digest))

    def cleanup(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE document_id=%s", (self.document_id,))
            cur.execute("DELETE FROM document_assets WHERE document_id=%s", (self.document_id,))

    @staticmethod
    def _ref(document_id):
        return {"document_id": document_id, "document_version": 1, "source_sha256": "a" * 64,
                "parser_version": "test-v1", "title": "searchability"}

    def test_an_indexed_document_without_chunks_is_refused(self):
        """The audit's exact path: visible, `indexed`, but never chunked."""
        self._asset()
        retriever = KnowledgeRetriever(_read_dsn())
        with self.assertRaises(ValueError) as caught:
            retriever._selected_rows([COLLECTION], [self._ref(self.document_id)])
        self.assertEqual(str(caught.exception), "DOCUMENT_SELECTION_NOT_SEARCHABLE")

    def test_a_digest_mismatch_is_reported_as_incomplete_not_as_unsearchable(self):
        """Distinct from "never chunked": the chunks exist, their text drifted.

        The existing contract reports that as `eligible_chunks=0` plus incomplete coverage so
        the other selected files still answer; refusing the whole selection here would break
        `tests/test_multifile_rag.py::...tampered_file_evidence_is_not_counted_as_covered`,
        which asserts exactly that behaviour. Collapsing the two facts into one refusal would
        lose the distinction the caller needs.
        """
        self._asset()
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO chunks(chunk_id, document_id, document_version, chunk_index,
                chunk_type, page_start, page_end, block_ids, text_for_embedding, exact_text,
                acl, embedding_model, content_hash)
                VALUES (%s,%s,1,0,'prose',1,1,'{}',%s,%s,'{public}','test-embedding',%s)""",
                        (self.chunk_id, self.document_id, self.text, self.text, "b" * 64))
        retriever = KnowledgeRetriever(_read_dsn())
        rows, snapshots = retriever._selected_rows([COLLECTION], [self._ref(self.document_id)])
        self.assertEqual(rows, [], "失效的证据不得进入回答")
        self.assertEqual([snapshot["eligible_chunks"] for snapshot in snapshots], [0],
                         "必须以「证据不完整」呈现，而不是抛错拒绝整次选择")

    def test_the_same_document_is_accepted_once_it_has_a_matching_chunk(self):
        """Reverse control: the refusal must be about missing evidence, not about refusing."""
        self._asset()
        self._chunk()
        retriever = KnowledgeRetriever(_read_dsn())
        rows, snapshots = retriever._selected_rows([COLLECTION], [self._ref(self.document_id)])
        self.assertEqual([snapshot["eligible_chunks"] for snapshot in snapshots], [1])
        self.assertEqual([row["chunk_id"] for row in rows], [self.chunk_id])

    def test_an_unapproved_document_is_still_rejected_as_unavailable(self):
        """The pre-existing visibility gate keeps its own, distinct error code."""
        self._asset(needs_review=True)
        self._chunk()
        retriever = KnowledgeRetriever(_read_dsn())
        with self.assertRaises(ValueError) as caught:
            retriever._selected_rows([COLLECTION], [self._ref(self.document_id)])
        self.assertEqual(str(caught.exception), "DOCUMENT_SELECTION_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
