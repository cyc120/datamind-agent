"""Isolated API fixtures: review concurrency, immutable evidence and candidate exclusion."""
import copy
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from psycopg2.extras import RealDictCursor
import test_document_fields as fixtures
WRITE_DSN = fixtures.WRITE_DSN
from datamind.api import _public_document
from datamind.agent import _read_dsn
from datamind.field_store import read_verified_facts


class FactReviewIntegrationTest(unittest.TestCase):
    setUp = fixtures.FieldAPIIntegrationTest.setUp
    get = fixtures.FieldAPIIntegrationTest.get
    update = fixtures.FieldAPIIntegrationTest.update

    def tearDown(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            # Delete only ephemeral test audit records; no production review is touched.
            cur.execute('DELETE FROM document_fact_reviews WHERE document_id=%s', (self.doc_id,))
        fixtures.FieldAPIIntegrationTest.tearDown(self)

    def request(self, fact=None, **changes):
        fact = fact or self.get().json()['facts'][0]
        request = dict(request_id='test-'+uuid.uuid4().hex, document_version=1, source_sha256=self.sha,
                       parser_version='field-test-v1', payload_sha256=fact['payload_sha256'],
                       previous_review_id=(fact['latest_review'] or {}).get('review_id'),
                       decision='confirmed', reviewer_label='隔离测试', reason='测试夹具字段核对')
        request.update(changes)
        return fact, request

    def post(self, fact, request):
        return self.client.post(f'/v1/documents/{self.doc_id}/facts/{fact["fact_id"]}/reviews', json=request)

    def history(self, fact):
        return self.client.get(f'/v1/documents/{self.doc_id}/facts/{fact["fact_id"]}/reviews').json()['reviews']

    def original(self):
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT payload,payload_sha256,approval_status,verification_status FROM document_facts WHERE document_id=%s ORDER BY fact_id', (self.doc_id,))
            return [dict(row) for row in cur.fetchall()]

    def usable(self):
        return read_verified_facts(_public_document(self.doc_id), self.path, _read_dsn(), usable_only=True)

    def test_append_only_decisions_preserve_source_and_replay_payload(self):
        original = copy.deepcopy(self.original()); source = self.path.read_bytes()
        fact, request = self.request()
        decisions = ['confirmed', 'needs_review', 'rejected', 'confirmed']
        expected_counts = [8, 7, 7, 8]
        previous = None
        for decision, expected in zip(decisions, expected_counts):
            fact, request = self.request(decision=decision, previous_review_id=previous)
            response = self.post(fact, request); self.assertEqual(response.status_code, 200, response.text)
            previous = response.json()['review']['review_id']
            self.assertEqual(len(self.usable()), expected)
            self.assertEqual(self.get().json()['fact_count'], 8)
            self.assertEqual(self.original(), original)
        self.assertEqual([r['decision'] for r in self.history(fact)], decisions)
        self.assertEqual(self.path.read_bytes(), source)
        self.assertTrue(all(row['approval_status']=='unreviewed' for row in self.original()))

    def test_idempotent_retry_and_stale_previous_are_not_extra_reviews(self):
        fact, request = self.request()
        first = self.post(fact, request); self.assertEqual(first.status_code, 200)
        retry = self.post(fact, request); self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.json()['review']['review_id'], retry.json()['review']['review_id'])
        self.assertEqual(self.post(fact, dict(request, decision='rejected')).status_code, 409)
        _, stale = self.request(previous_review_id=None)
        self.assertEqual(self.post(fact, stale).status_code, 409)
        self.assertEqual(len(self.history(fact)), 1)

    def test_request_id_is_unique_even_across_concurrent_different_facts(self):
        facts = self.get().json()['facts'][:2]; request_id = 'test-'+uuid.uuid4().hex
        jobs = [self.request(fact=fact, request_id=request_id) for fact in facts]
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda job:self.post(*job), jobs))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        self.assertEqual(sum(len(self.history(f)) for f in facts), 1)

    def test_version_payload_and_blank_labels_cannot_be_recorded(self):
        fact, request = self.request()
        for changes in ({'document_version':2}, {'source_sha256':'f'*64}, {'parser_version':'old'}, {'payload_sha256':'f'*64}):
            self.assertEqual(self.post(fact, dict(request, **changes)).status_code, 409)
        for changes in ({'reviewer_label':'  '}, {'reason':'  '}):
            self.assertEqual(self.post(fact, dict(request, **changes)).status_code, 422)
        self.assertEqual(self.history(fact), [])
        self.path.write_bytes(self.path.read_bytes()+b'\n% changed')
        self.assertEqual(self.post(fact, request).status_code, 409)

    def test_changed_fact_does_not_inherit_old_decision(self):
        from ingestion import facts as extractor
        fact, request = self.request(decision='rejected'); self.assertEqual(self.post(fact, request).status_code, 200)
        self.assertEqual(len(self.usable()), 7)
        for row in self.b['table_cells']['cell_details'][1:]:
            row[1]['text'] = '46'
        self.b['text'] = '\n'.join(' | '.join(c['text'] for c in row) for row in self.b['table_cells']['cell_details'])
        import hashlib
        fresh, _ = extractor.extract([self.b], document_id=self.doc_id,source_sha256=self.sha,parser_version='field-test-v1')
        with psycopg2.connect(WRITE_DSN) as conn, conn.cursor() as cur:
            cur.execute('UPDATE document_blocks SET text=%s,content_hash=%s WHERE document_id=%s', (self.b['text'],hashlib.sha256(self.b['text'].encode()).hexdigest(),self.doc_id))
            extractor.persist(cur,self.doc_id,fresh)
        self.assertEqual(len(self.usable()), 8)
        self.assertTrue(all(f['latest_review'] is None for f in self.get().json()['facts']))
        self.assertEqual(len(self.history(fact)), 1)
        self.assertEqual(self.post(fact, request).status_code, 409)

    def test_private_asset_and_readonly_role_boundaries(self):
        fact, request = self.request()
        self.update("UPDATE document_assets SET acl='{private}' WHERE document_id=%s")
        self.assertEqual(self.post(fact,request).status_code,404)
        with psycopg2.connect(_read_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT has_table_privilege(current_user,'document_fact_reviews','SELECT'), has_table_privilege(current_user,'document_fact_reviews','INSERT,UPDATE,DELETE')")
            self.assertEqual(cur.fetchone(), (True,False))


if __name__ == '__main__': unittest.main()
