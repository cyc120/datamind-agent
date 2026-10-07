"""Repair lost published private vectors and reclaim orphans, without touching heads."""
import unittest
import uuid

from qdrant_client.models import PointStruct

from fastapi import HTTPException

from datamind.agent import _write_dsn
from datamind import pdf_maintenance, pdf_vectors as vectors
import test_pdf_index_api as fixtures


REPAIR = '/v1/pdf-knowledge/publications/{}/repair'
ORPHANS = '/v1/pdf-knowledge/orphans'
RECLAIM = '/v1/pdf-knowledge/orphans/reclaim'


class PdfMaintenanceTest(unittest.TestCase):
    clean_fixtures = fixtures.PdfIndexAPITest.clean_fixtures
    client = fixtures.PdfIndexAPITest.client
    prepare_and_register = fixtures.PdfIndexAPITest.prepare_and_register
    publication_payload = fixtures.PdfIndexAPITest.publication_payload
    submit = fixtures.PdfIndexAPITest.submit
    drain = fixtures.PdfIndexAPITest.drain
    successful_publication = fixtures.PdfIndexAPITest.successful_publication
    assert_safe = fixtures.PdfIndexAPITest.assert_safe
    assert_error = fixtures.PdfIndexAPITest.assert_error

    def setUp(self):
        fixtures.PdfIndexAPITest.setUp(self)
        self.dsn = _write_dsn()

    def assert_code(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail['error_code'], code)

    def collection(self):
        names = [item.name for item in self.vector_client.get_collections().collections]
        self.assertEqual(len(names), 1, names)
        return names[0]

    def point_ids(self, name):
        ids, offset = [], None
        while True:
            batch, offset = self.vector_client.scroll(name, limit=100, offset=offset,
                                                      with_payload=False, with_vectors=False)
            ids.extend(str(point.id) for point in batch)
            if offset is None:
                return ids

    def add_orphan(self, name, value=0.5):
        orphan_id = str(uuid.uuid4())
        self.vector_client.upsert(name, [PointStruct(id=orphan_id, vector=[value] + [0.0] * 511,
                                                     payload={'schema': 'orphan-synthetic'})], wait=True)
        return orphan_id

    def publication_row(self, publication_id):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM pdf_knowledge_requests WHERE publication_id=%s', (publication_id,))
            return cur.fetchone()

    def test_repair_restores_a_deleted_point_and_leaves_state_unchanged(self):
        publication = self.successful_publication()
        name = self.collection()
        before = self.point_ids(name)
        self.assertGreater(len(before), 1)
        self.vector_client.delete(name, points_selector=[before[0]], wait=True)
        self.assertEqual(len(self.point_ids(name)), len(before) - 1)
        row_before = self.publication_row(publication['publication_id'])

        result = pdf_maintenance.repair_publication(publication['publication_id'], self.dsn)

        self.assertEqual(result['missing_before'], 1, result)
        self.assertEqual(result['repaired'], 1)
        self.assertEqual(result['expected'], len(before))
        self.assertTrue(result['verified'])
        self.assertTrue(result['head_unchanged'])
        self.assertEqual(sorted(self.point_ids(name)), sorted(before))
        row_after = self.publication_row(publication['publication_id'])
        self.assertEqual(row_after['status'], row_before['status'])
        self.assertEqual(row_after['vector_plan_sha256'], row_before['vector_plan_sha256'])
        self.assertEqual(row_after['result_sha256'], row_before['result_sha256'])

    def test_repair_reports_nothing_missing_when_the_store_is_intact(self):
        publication = self.successful_publication()
        result = pdf_maintenance.repair_publication(publication['publication_id'], self.dsn)
        self.assertEqual(result['missing_before'], 0, result)
        self.assertEqual(result['repaired'], 0)
        self.assertTrue(result['verified'])

    def test_repair_refuses_unknown_and_invalid_publications(self):
        self.assert_code('PDF_PUBLICATION_NOT_FOUND',
                         lambda: pdf_maintenance.repair_publication('pdf-pub-' + '0' * 32, self.dsn))
        self.assert_code('PDF_PUBLICATION_ID_INVALID',
                         lambda: pdf_maintenance.repair_publication('../etc/passwd', self.dsn))

    def test_orphan_scan_counts_only_unreferenced_points(self):
        publication = self.successful_publication()
        name = self.collection()
        referenced = len(self.point_ids(name))
        orphan = self.add_orphan(name)

        scan = pdf_maintenance.orphan_scan(self.dsn)

        self.assertTrue(scan['dry_run'])
        self.assertEqual(scan['orphans'], 1, scan)
        entry = next(item for item in scan['collections'] if item['collection'] == name)
        self.assertEqual(entry['orphans'], 1)
        self.assertEqual(entry['referenced_here'], referenced)
        self.assertEqual(entry['orphan_sample'], [orphan])

    def test_dry_run_reclaim_deletes_nothing_and_confirm_deletes_the_orphan(self):
        self.successful_publication()
        name = self.collection()
        referenced = sorted(self.point_ids(name))
        orphan = self.add_orphan(name)

        dry = pdf_maintenance.reclaim_orphans(name, self.dsn, confirm=False)
        self.assertTrue(dry['dry_run'])
        self.assertEqual(dry['deleted'], 0)
        self.assertEqual(dry['orphans'], 1)
        self.assertIn(orphan, self.point_ids(name))

        done = pdf_maintenance.reclaim_orphans(name, self.dsn, confirm=True)
        self.assertFalse(done['dry_run'])
        self.assertEqual(done['deleted'], 1, done)
        self.assertEqual(done['remaining_orphans'], 0)
        self.assertNotIn(orphan, self.point_ids(name))
        # Referenced points survive reclamation untouched.
        self.assertEqual(sorted(self.point_ids(name)), referenced)

    def test_reclaim_never_treats_a_referenced_point_as_orphan(self):
        publication = self.successful_publication()
        name = self.collection()
        self.add_orphan(name)
        pdf_maintenance.reclaim_orphans(name, self.dsn, confirm=True)
        result = pdf_maintenance.repair_publication(publication['publication_id'], self.dsn)
        self.assertEqual(result['missing_before'], 0, result)

    def test_reclaim_refuses_non_private_collections_and_bad_batches(self):
        self.assert_code('PDF_MAINTENANCE_COLLECTION_FORBIDDEN',
                         lambda: pdf_maintenance.reclaim_orphans('datamind_knowledge', self.dsn))
        self.assert_code('PDF_MAINTENANCE_COLLECTION_FORBIDDEN',
                         lambda: pdf_maintenance.reclaim_orphans('pdf_private', self.dsn, confirm=True))
        self.successful_publication()
        name = self.collection()
        self.assert_code('PDF_MAINTENANCE_BATCH_INVALID',
                         lambda: pdf_maintenance.reclaim_orphans(name, self.dsn, confirm=True, batch=0))

    def test_reclaim_request_payload_is_strict(self):
        for bad in ({}, {'collection': ''}, {'collection': 'x', 'confirm': 'yes'},
                    {'collection': 'x', 'extra': 1}, {'collection': 3}):
            self.assert_code('PDF_MAINTENANCE_REQUEST_INVALID',
                             lambda bad=bad: pdf_maintenance.reclaim_payload(bad))

    def test_http_endpoints_require_admin_and_report_repair(self):
        publication = self.successful_publication()
        name = self.collection()
        self.vector_client.delete(name, points_selector=[self.point_ids(name)[0]], wait=True)

        anonymous = self.client().post(REPAIR.format(publication['publication_id']))
        self.assertIn(anonymous.status_code, (401, 403), anonymous.text)

        response = self.admin.post(REPAIR.format(publication['publication_id']))
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response)
        self.assertEqual(response.json()['missing_before'], 1, response.json())

        scan = self.admin.get(ORPHANS)
        self.assertEqual(scan.status_code, 200, scan.text)
        self.assert_safe(scan)
        self.assertEqual(scan.json()['orphans'], 0, scan.json())

        orphan = self.add_orphan(name)
        body = {'collection': name, 'confirm': False}
        dry = self.admin.post(RECLAIM, json=body)
        self.assertEqual(dry.status_code, 200, dry.text)
        self.assertEqual(dry.json()['deleted'], 0)
        self.assertIn(orphan, self.point_ids(name))

        body['confirm'] = True
        done = self.admin.post(RECLAIM, json=body)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()['deleted'], 1)
        self.assertNotIn(orphan, self.point_ids(name))

    def test_http_reclaim_rejects_a_non_private_collection(self):
        response = self.admin.post(RECLAIM, json={'collection': 'datamind_knowledge', 'confirm': True})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()['detail']['error_code'], 'PDF_MAINTENANCE_COLLECTION_FORBIDDEN')


if __name__ == '__main__':
    unittest.main()
