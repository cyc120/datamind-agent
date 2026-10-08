"""The visibility gate F10 relies on, asserted against the real retrieval code.

The ingest protocol leaves a document at a non-`indexed` status whenever a cross-store step
fails. That only protects the reader if retrieval really refuses such a document, so this
checks the actual `KnowledgeRetriever` query instead of trusting the design note.
"""
import hashlib
import unittest
import uuid

import psycopg2

from datamind.agent import _write_dsn
from datamind.knowledge import KnowledgeRetriever

TOKEN = '相位一致性校验样本词'


class IngestVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.document_id = f'test-vis-{uuid.uuid4().hex[:12]}'
        self.chunk_id = self.document_id + '-chunk'
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO document_assets(document_id, title, collection, file_type,
                origin, sha256, doc_version, acl, status, needs_review, page_count)
                VALUES (%s, '可见性测试', 'demo_kb', 'pdf', 'curated', %s, 1, '{public}',
                        'parsing', FALSE, 1)""",
                        (self.document_id, hashlib.sha256(self.document_id.encode()).hexdigest()))
            cur.execute("""INSERT INTO chunks(chunk_id, document_id, chunk_index, text_for_embedding,
                exact_text, embedding_model, content_hash, page_start, page_end, acl)
                VALUES (%s, %s, 0, %s, %s, 'test', %s, 1, 1, '{public}')""",
                        (self.chunk_id, self.document_id, TOKEN, TOKEN,
                         hashlib.sha256(TOKEN.encode()).hexdigest()))

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM chunks WHERE document_id=%s', (self.document_id,))
            cur.execute('DELETE FROM document_assets WHERE document_id=%s', (self.document_id,))

    def set_status(self, status):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('UPDATE document_assets SET status=%s WHERE document_id=%s',
                        (status, self.document_id))

    def retrieved_ids(self):
        result = KnowledgeRetriever(mode='lexical').retrieve(TOKEN, collections=['demo_kb'])
        return {hit['document_id'] for hit in result['hits']}

    def test_a_document_that_is_not_indexed_is_not_retrievable(self):
        # This is the state a failed cross-store step leaves behind.
        self.assertNotIn(self.document_id, self.retrieved_ids())

    def test_the_same_document_becomes_retrievable_once_indexed(self):
        # The mirror image: the gate is about status, not about the fixture being wrong.
        self.set_status('indexed')
        self.assertIn(self.document_id, self.retrieved_ids())

    def test_every_non_indexed_status_hides_the_document(self):
        for status in ('parsing', 'pending', 'failed'):
            self.set_status(status)
            self.assertNotIn(self.document_id, self.retrieved_ids(), status)

    def test_the_chunk_row_itself_still_exists_while_hidden(self):
        # Hiding is a read-side decision; the rows are present, so a retry can finish them
        # and nothing has to be reconstructed from Qdrant.
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM chunks WHERE document_id=%s', (self.document_id,))
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
