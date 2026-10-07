"""Query-profile and cross-connection admission tests, without model downloads.

DB concurrency checks run through scripts/test_isolated.py. Vector fixtures here
are deliberately synthetic, not an embedding/retrieval accuracy evaluation.
"""
import copy
import math
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn

from datamind.agent import _write_dsn
from datamind import pdf_search as search, pdf_vectors as vectors


def embed(texts):
    return [[1.0] + [0.0] * 511 for _ in texts]


class PdfQueryProfileTests(unittest.TestCase):
    def error(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail['error_code'], code)

    def test_synthetic_profile_requires_explicit_internal_injection(self):
        profile = vectors._profile(synthetic=True)
        with patch.object(vectors, '_production_embedding') as production:
            self.error('PDF_SEARCH_PRODUCTION_PROFILE_REQUIRED',
                       lambda: vectors.query_embedding('What is the value?', profile))
        production.assert_not_called()

    def test_matching_synthetic_injection_is_normalized_and_exactly_one_query(self):
        embedding = Mock(side_effect=embed)
        query = vectors.query_embedding('Query', vectors._profile(synthetic=True), embedding)
        embedding.assert_called_once_with(['Query'])
        self.assertEqual(query, embed(['Query'])[0])
        self.assertEqual(len(vectors._vector_bytes(query)), 512 * 4)
        self.assertEqual(math.hypot(*query), 1)

    def test_query_rejects_invalid_text_before_embedding(self):
        model = Mock(side_effect=AssertionError('no embedding'))
        for text in (' ', '', 'x' * 2001, '\ud800', True, 3):
            self.error('PDF_SEARCH_QUESTION_INVALID', lambda: vectors.query_embedding(
                text, vectors._profile(synthetic=True), model))
        model.assert_not_called()

    def test_query_embedding_count_and_invalid_vector(self):
        profile = vectors._profile(synthetic=True)
        for result in ([], embed(['one', 'two'])):
            self.error('PDF_INDEX_VECTOR_COUNT_INVALID',
                       lambda: vectors.query_embedding('Query', profile, lambda texts: result))
        for vector in ([0.0] * 512, [float('nan')] * 512, [True] + [0.0] * 511, [1.0] * 513):
            self.error('PDF_INDEX_VECTOR_INVALID',
                       lambda: vectors.query_embedding('Query', profile, lambda texts: [vector]))

    def test_query_embedding_failure_hides_internal_error(self):
        model = Mock(side_effect=RuntimeError('/private/cache/key-secret'))
        self.error('PDF_INDEX_EMBEDDING_FAILED',
                   lambda: vectors.query_embedding('Query', vectors._profile(synthetic=True), model))

    def test_production_profile_must_match_all_model_and_runtime_fields(self):
        profile = vectors._profile(synthetic=False)
        for field in ('model_file_sha256', 'sdk_versions', 'normalization'):
            actual = copy.deepcopy(profile)
            if field == 'sdk_versions':
                actual[field]['fastembed'] = 'another-runtime'
            else:
                actual[field] = 'changed'
            with patch.object(vectors, '_production_embedding', return_value=(actual, embed(['Query']))):
                self.error('PDF_SEARCH_PROFILE_MISMATCH', lambda: vectors.query_embedding('Query', profile))

    def test_production_cannot_use_synthetic_injection(self):
        self.error('PDF_SEARCH_PROFILE_MISMATCH', lambda: vectors.query_embedding(
            'Query', vectors._profile(synthetic=False), embed))

    def test_matching_production_profile_contract(self):
        profile = vectors._profile(synthetic=False)
        with patch.object(vectors, '_production_embedding', return_value=(profile, embed(['Query']))) as production:
            self.assertEqual(vectors.query_embedding('Query', profile), embed(['Query'])[0])
        production.assert_called_once_with(['Query'])


class PdfSearchAdmissionTests(unittest.TestCase):
    def dsn(self):
        # The helper redirects metadata DB connections. Obtain the actual clone
        # DSN for independent connections/subprocesses, never the original DSN.
        conn = psycopg2.connect(_write_dsn())
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT current_database()')
                database = cur.fetchone()[0]
            self.assertTrue(database.startswith('datamind_test_'))
            options = parse_dsn(_write_dsn())
            options['dbname'] = database
            return make_dsn(**options)
        finally:
            conn.close()

    def test_cross_connection_two_slots_and_release_after_busy(self):
        dsn = self.dsn()
        with search._search_slot(dsn), search._search_slot(dsn):
            with self.assertRaises(HTTPException) as caught:
                with search._search_slot(dsn):
                    self.fail('third search entered')
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(caught.exception.detail['error_code'], 'PDF_SEARCH_BUSY')
        with search._search_slot(dsn):
            pass

    def test_slots_apply_to_independent_process(self):
        dsn = self.dsn()
        script = '''import psycopg2, sys
conn=psycopg2.connect(sys.stdin.read());conn.autocommit=True
with conn.cursor() as cur:
    for slot in range(2):
        cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',('pdf-private-search-slot:'+str(slot),))
        if cur.fetchone()[0]:
            raise SystemExit(1)
conn.close()
'''
        with search._search_slot(dsn), search._search_slot(dsn):
            result = subprocess.run([sys.executable, '-c', script], input=dsn, text=True,
                                    capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_slot_connection_stays_idle_without_a_transaction(self):
        dsn = self.dsn()
        with search._search_slot(dsn), psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute('''SELECT state,xact_start FROM pg_stat_activity
                WHERE datname=current_database() AND pid<>pg_backend_pid()
                AND query LIKE 'SELECT pg_try_advisory_lock%%' ''')
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0], ('idle', None))

    def test_exception_releases_both_slots(self):
        dsn = self.dsn()
        with self.assertRaisesRegex(RuntimeError, 'synthetic crash'):
            with search._search_slot(dsn), search._search_slot(dsn):
                raise RuntimeError('synthetic crash')
        with search._search_slot(dsn), search._search_slot(dsn):
            pass


    def test_lost_slot_session_is_detected_and_cannot_return_success(self):
        dsn = self.dsn()
        with self.assertRaises(HTTPException) as caught:
            with search._search_slot(dsn) as live:
                with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                    cur.execute('''SELECT pid FROM pg_stat_activity
                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                        AND query LIKE 'SELECT pg_try_advisory_lock%%' ''')
                    pids = cur.fetchall()
                    self.assertEqual(len(pids), 1)
                    cur.execute('SELECT pg_terminate_backend(%s)', (pids[0][0],))
                    self.assertTrue(cur.fetchone()[0])
                live()
        self.assertEqual(caught.exception.detail['error_code'], 'PDF_SEARCH_DATABASE_UNAVAILABLE')
        with search._search_slot(dsn), search._search_slot(dsn):
            pass


    def test_context_exit_rechecks_liveness_even_without_manual_probe(self):
        dsn = self.dsn()
        with self.assertRaises(HTTPException) as caught:
            with search._search_slot(dsn):
                with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                    cur.execute('''SELECT pid FROM pg_stat_activity
                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                        AND query LIKE 'SELECT pg_try_advisory_lock%%' ''')
                    pids = cur.fetchall()
                    self.assertEqual(len(pids), 1)
                    cur.execute('SELECT pg_terminate_backend(%s)', (pids[0][0],))
                    self.assertTrue(cur.fetchone()[0])
        self.assertEqual(caught.exception.detail['error_code'], 'PDF_SEARCH_DATABASE_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
