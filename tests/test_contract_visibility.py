"""R03: an unreviewed upload's contract text is admin evidence, not anonymous public read.

`GET /v1/documents/{id}/contract` lives inside the anonymous public-read prefix, and the
upload pipeline keeps `needs_review=TRUE` with the default `public` ACL until an explicit
approve. The route therefore has to make the review gate itself. The acceptance matrix is
unparsed / parsed-pending-review / approved / cancelled / failed, each crossed with an
anonymous, a viewer, a reviewer and an admin identity:

* unparsed, pending review, cancelled and failed are 404 for anonymous *and* every
  non-admin role — the same answer the detail route gives, so no caller learns the id exists;
* approved (indexed, review cleared) stays a public read for anonymous, exactly as before;
* an authenticated administrator — including the loopback no-login demo admin — can still
  read the evidence before approval, and that response carries `Cache-Control: no-store`.

The fixtures are synthetic rows in the disposable metadata clone and are deleted in
teardown; the two real upload assets are never read or modified here.
"""
import hashlib
import os
import uuid
import unittest

from fastapi.testclient import TestClient
import psycopg2

from datamind.agent import _write_dsn
from datamind.api import app
from ingestion import contract_store
import auth_fixtures

ANONYMOUS_CLIENT = ('203.0.113.7', 41821)   # documented non-loopback TEST-NET-3 address
LOOPBACK_CLIENT = ('127.0.0.1', 54321)      # the local no-login demo admin path


def contract_payload(document_id, count=1, text='待复核图纸正文'):
    """A minimal validated ParseResult payload: `count` located contract blocks."""
    suffix = document_id[-12:]
    blocks = []
    for index in range(count):
        blocks.append({
            'block_id': f'blk-{suffix}-{index:04d}', 'document_id': document_id,
            'source_version': 'v1', 'parser_version': 'dxf-adapter-v1',
            'block_type': 'figure', 'text': f'{text} {index}', 'reading_order': index,
            'content_hash': f'{index:064d}', 'native_attributes': {}, 'quality': {},
            'locator': {'locator_kind': 'cad_entity', 'document_id': document_id,
                        'source_version': 'v1', 'layout': 'Model',
                        'handle': str(38 + index), 'layer': 'GEOMETRY',
                        'model_xy': [1.0, 2.0], 'unit': 'mm'}})
    return {'classification': {'detected_media_type': 'image/vnd.dxf', 'source_name': 'd.dxf'},
            'adapter_capabilities': ['entities'], 'relations': [], 'parsed_scope': [],
            'failed_scope': [], 'quality_findings': [], 'unsupported_features': [],
            'artifact_refs': [], 'manifest': {'parser_version': 'dxf-native-v1'},
            'parse_status': 'parsed', 'blocks': blocks}


class ContractVisibilityTests(unittest.TestCase):
    #: state -> (status, needs_review, contract blocks written); the five states the review
    #: asked for. `approved` is the only published one.
    STATES = {
        'unparsed': ('parsing', True, 0),
        'pending_review': ('parsed', True, 3),
        'approved': ('indexed', False, 3),
        'cancelled': ('pending', True, 2),
        'failed': ('failed', True, 0),
    }
    UNREVIEWED = ('unparsed', 'pending_review', 'cancelled', 'failed')
    BLOCK_TEXT = '待复核图纸正文'

    def setUp(self):
        # Unset the forced-demo override so "anonymous" is decided by the peer address alone,
        # exactly as it is in production.
        self._previous = os.environ.pop('DATAMIND_LOCAL_NO_LOGIN', None)
        self.addCleanup(self._restore_env)
        self.dsn = _write_dsn()
        self.token = uuid.uuid4().hex[:10]
        self.documents = []
        self.states = {state: self._create(state, *row) for state, row in self.STATES.items()}
        self.anonymous = TestClient(app, client=ANONYMOUS_CLIENT)
        self.viewer = TestClient(app, client=ANONYMOUS_CLIENT)
        self.reviewer = TestClient(app, client=ANONYMOUS_CLIENT)
        self.admin = TestClient(app, client=ANONYMOUS_CLIENT)
        auth_fixtures.login(self.viewer, roles=('viewer',))
        auth_fixtures.login(self.reviewer, roles=('reviewer',))
        auth_fixtures.login(self.admin, roles=('admin',))
        self.loopback = TestClient(app, client=LOOPBACK_CLIENT)
        self.addCleanup(self._cleanup)

    def _restore_env(self):
        if self._previous is not None:
            os.environ['DATAMIND_LOCAL_NO_LOGIN'] = self._previous

    def _create(self, state, status, needs_review, block_count):
        document_id = f'doc-vis-{self.token}-{state}'
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO document_assets(document_id, title, collection, file_type,
                origin, sha256, doc_version, acl, status, needs_review)
                VALUES (%s, %s, 'demo_kb', 'dxf', 'upload', %s, 1, '{public}', %s, %s)""",
                        (document_id, f'visibility {state}',
                         hashlib.sha256(document_id.encode()).hexdigest(), status, needs_review))
            if block_count:
                contract_store.persist_result(
                    cur, document_id=document_id,
                    payload=contract_payload(document_id, block_count, self.BLOCK_TEXT))
        self.documents.append(document_id)
        return document_id

    def _cleanup(self):
        for client in (self.viewer, self.reviewer, self.admin):
            auth_fixtures.cleanup(client)
            client.close()
        for client in (self.anonymous, self.loopback):
            client.close()
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            for table in ('document_contract_blocks', 'document_block_relations',
                          'document_block_locators', 'document_blocks'):
                cur.execute(f'DELETE FROM {table} WHERE document_id=ANY(%s)', (self.documents,))
            cur.execute('DELETE FROM document_assets WHERE document_id=ANY(%s)', (self.documents,))

    # ------------------------------------------------------------------ the review matrix
    def test_unreviewed_states_are_404_for_anonymous_and_non_admins_but_not_admin(self):
        for state in self.UNREVIEWED:
            document_id = self.states[state]
            for label, client in (('anonymous', self.anonymous), ('viewer', self.viewer),
                                  ('reviewer', self.reviewer)):
                with self.subTest(state=state, identity=label):
                    response = client.get(f'/v1/documents/{document_id}/contract')
                    self.assertEqual(response.status_code, 404, response.text)
                    self.assertEqual(response.json()['detail']['error_code'],
                                     'DOCUMENT_NOT_FOUND')
            with self.subTest(state=state, identity='admin'):
                response = self.admin.get(f'/v1/documents/{document_id}/contract')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()['document_id'] == document_id)

    def test_an_approved_document_stays_an_anonymous_public_read(self):
        document_id = self.states['approved']
        for label, client in (('anonymous', self.anonymous), ('viewer', self.viewer),
                              ('reviewer', self.reviewer), ('admin', self.admin)):
            with self.subTest(identity=label):
                response = client.get(f'/v1/documents/{document_id}/contract')
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body['block_count'], 3)
                self.assertEqual(body['totals']['blocks'], 3)
                self.assertIn(self.BLOCK_TEXT, body['blocks'][0]['text'])
                # Public read behaviour is not degraded: this branch is not the evidence one.
                self.assertNotEqual(response.headers.get('cache-control'), 'no-store')

    def test_the_anonymous_404_is_not_distinguishable_from_an_unknown_document(self):
        """The 404 must be an authorization answer, not an existence oracle.

        The same id returns 200 with the seeded text for an admin, which proves the fixture
        really exists and the assertion above is not vacuous; the anonymous/viewer bodies
        are byte-identical to the unknown-id body, so nothing about the row leaks.
        """
        document_id = self.states['pending_review']
        unknown = f'doc-vis-{self.token}-does-not-exist'
        admin = self.admin.get(f'/v1/documents/{document_id}/contract')
        self.assertEqual(admin.status_code, 200, admin.text)
        self.assertEqual(admin.json()['totals']['blocks'], 3)
        self.assertIn(self.BLOCK_TEXT, admin.json()['blocks'][0]['text'])
        self.assertEqual(admin.headers.get('cache-control'), 'no-store')
        known_anonymous = self.anonymous.get(f'/v1/documents/{document_id}/contract')
        unknown_anonymous = self.anonymous.get(f'/v1/documents/{unknown}/contract')
        self.assertEqual(known_anonymous.status_code, 404)
        self.assertEqual(known_anonymous.status_code, unknown_anonymous.status_code)
        self.assertEqual(known_anonymous.text, unknown_anonymous.text)
        viewer = self.viewer.get(f'/v1/documents/{document_id}/contract')
        self.assertEqual(viewer.status_code, 404)
        self.assertEqual(viewer.text, known_anonymous.text)
        reviewer = self.reviewer.get(f'/v1/documents/{document_id}/contract')
        self.assertEqual(reviewer.status_code, 404)
        self.assertEqual(reviewer.text, known_anonymous.text)

    def test_the_detail_route_uses_the_same_publish_predicate(self):
        """Reading and publishing share one judgement: for ordinary callers the two routes
        must agree on every state, and only the authenticated admin gets the wider
        pre-approval evidence read."""
        for state, document_id in self.states.items():
            published = state == 'approved'
            for label, client in (('anonymous', self.anonymous), ('viewer', self.viewer),
                                  ('reviewer', self.reviewer)):
                with self.subTest(state=state, identity=label):
                    detail = client.get(f'/v1/documents/{document_id}')
                    contract = client.get(f'/v1/documents/{document_id}/contract')
                    self.assertEqual(detail.status_code == 200, published)
                    self.assertEqual(contract.status_code, detail.status_code)

    def test_the_loopback_demo_admin_can_still_review_before_approval(self):
        document_id = self.states['pending_review']
        response = self.loopback.get(f'/v1/documents/{document_id}/contract')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        body = response.json()
        self.assertEqual(body['totals']['blocks'], 3)
        self.assertEqual(len(body['blocks']), 3)
        self.assertIn(self.BLOCK_TEXT, body['blocks'][0]['text'])

    def test_only_the_evidence_read_is_marked_no_store(self):
        pending = self.loopback.get(f'/v1/documents/{self.states["pending_review"]}/contract')
        approved = self.loopback.get(f'/v1/documents/{self.states["approved"]}/contract')
        self.assertEqual(pending.headers.get('cache-control'), 'no-store')
        self.assertIsNone(approved.headers.get('cache-control'))

    # ------------------------------------------------------------------------ pagination
    def test_the_contract_route_exposes_the_page_and_the_store_total(self):
        document_id = self.states['pending_review']
        first_response = self.loopback.get(f'/v1/documents/{document_id}/contract',
                                           params={'limit': '2', 'offset': '0'})
        second_response = self.loopback.get(f'/v1/documents/{document_id}/contract',
                                            params={'limit': '2', 'offset': '2'})
        self.assertEqual(first_response.status_code, 200, first_response.text)
        self.assertEqual(second_response.status_code, 200, second_response.text)
        first = first_response.json()
        second = second_response.json()
        self.assertEqual(first['page']['limit'], 2)
        self.assertEqual(first['page']['offset'], 0)
        self.assertEqual(first['page']['total'], 3)
        self.assertEqual(first['page']['returned'], 2)
        self.assertTrue(first['page']['truncated'])
        self.assertTrue(first['page']['has_more'])
        self.assertEqual(first['totals']['blocks'], 3)
        self.assertEqual(first['counts']['blocks_returned'], 2)
        self.assertEqual(second['page']['returned'], 1)
        self.assertFalse(second['page']['has_more'])
        block_ids = [block['block_id'] for block in first['blocks'] + second['blocks']]
        self.assertEqual(len(block_ids), 3)
        self.assertEqual(len(set(block_ids)), 3)

    def test_an_invalid_page_window_is_a_stable_error_not_a_clamp(self):
        document_id = self.states['approved']
        cases = ({'limit': '0'}, {'limit': '5001'}, {'limit': 'abc'}, {'limit': '1.5'},
                 {'offset': '-1'}, {'offset': '1000001'}, {'offset': 'x'})
        for params in cases:
            with self.subTest(params=params):
                response = self.loopback.get(f'/v1/documents/{document_id}/contract',
                                             params=params)
                self.assertEqual(response.status_code, 400, response.text)
                code = response.json()['detail']['error_code']
                self.assertIn(code, ('CONTRACT_PAGE_LIMIT_INVALID',
                                     'CONTRACT_PAGE_OFFSET_INVALID'))
                self.assertEqual(code, 'CONTRACT_PAGE_LIMIT_INVALID' if 'limit' in params
                                 else 'CONTRACT_PAGE_OFFSET_INVALID')


if __name__ == '__main__':
    unittest.main()
