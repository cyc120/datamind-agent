"""R04: a terminal/result write is bound to the *claim generation*, not to the job row.

The audit's interleaving was: upload -> worker A claims -> the same bytes are re-uploaded
(which used to reset the job to `queued`) -> worker B claims -> A finishes. Because the
terminal write only checked `status='running'`, A's completion *succeeded* while B was still
parsing: a false success, and A's contract rows/`quality_detail` were committed before
`_finish` had a chance to notice.

These tests reproduce that interleaving deterministically — no `sleep`, no racing threads:
the internal claim/parse functions are called in a fixed order and the "late" generation is
made late by ageing its `started_at` (exactly what the wave deadline does). Every rejection
is asserted as a **row-by-row** snapshot equality of `ingest_jobs`, `document_assets`,
`ingest_job_claims`, `document_contract_blocks`, `document_block_relations`,
`document_block_locators`, `chunks` and `document_blocks`.

Non-vacuity: each parse is given a per-generation marker in `manifest.parser_version`
(persisted to `document_assets.parser_version`), so "the surviving result is B's" is a real
assertion and not "both parses produced identical bytes".

Every fixture is real: bytes go through `POST /v1/uploads`, the DXF is parsed by the real
adapter, and the rows are removed in teardown.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import RealDictCursor

from datamind import file_uploads
from datamind.agent import _write_dsn
from datamind.api import app
from ingestion import upload_pipeline
import auth_fixtures

ROOT = Path(__file__).resolve().parents[1]
DXF = ROOT / 'data/cad_demo/development/dev-mm.dxf'
SNAPSHOT_TABLES = ('ingest_jobs', 'document_assets', 'document_contract_blocks',
                   'document_block_relations', 'document_block_locators', 'chunks',
                   'document_blocks', 'ingest_job_claims')


class _Parsed:
    """The `(result, extra)` shape `run_parse` expects, without touching the adapter."""

    def __init__(self, payload):
        self.payload = payload


def marker_parser(marker):
    """A real DXF parse whose `manifest.parser_version` names the claim generation."""
    real = upload_pipeline.PARSERS['.dxf']

    def parse(path, claim):
        result, extra = real(path, claim)
        payload = copy.deepcopy(result.payload)
        payload['manifest'] = dict(payload['manifest'])
        payload['manifest']['parser_version'] = marker
        return _Parsed(payload), extra

    return parse


class ClaimGenerationTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(file_uploads, 'ROOT', self.root),
                        patch.object(upload_pipeline, 'ROOT', self.root)]
        for item in self.patches:
            item.start()
        self.addCleanup(self._stop)
        self.admin = TestClient(app, client=('r04-admin-' + self.root.name, 50000))
        self.admin_two = TestClient(app, client=('r04-admin2-' + self.root.name, 50001))
        auth_fixtures.login(self.admin, roles=('admin',))
        auth_fixtures.login(self.admin_two, roles=('admin',))
        self.docs = []
        self.jobs = []
        self.addCleanup(self._cleanup)

    # ------------------------------------------------------------------ harness
    def _stop(self):
        for client in (self.admin, self.admin_two):
            auth_fixtures.cleanup(client)
            client.close()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def _cleanup(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            for table in ('document_contract_blocks', 'document_block_relations',
                          'document_block_locators', 'chunks', 'document_blocks'):
                cur.execute(f'DELETE FROM {table} WHERE document_id=ANY(%s)', (self.docs,))
            # `ingest_job_claims` cascades from `ingest_jobs` (migration 018).
            cur.execute('DELETE FROM ingest_jobs WHERE document_id=ANY(%s)', (self.docs,))
            cur.execute('DELETE FROM document_assets WHERE document_id=ANY(%s)', (self.docs,))
            cur.execute('SELECT count(*) FROM ingest_job_claims WHERE job_id=ANY(%s)',
                        (self.jobs,))
            self.assertEqual(cur.fetchone()[0], 0, 'claim rows must not outlive their job')

    def upload(self, data=None, filename='dev-mm.dxf', client=None):
        data = DXF.read_bytes() if data is None else data
        response = (client or self.admin).post(
            '/v1/uploads', params={'filename': filename, 'provenance': 'synthetic_demo'},
            content=data, headers={'Content-Type': 'application/octet-stream'})
        if response.status_code == 201:
            body = response.json()
            self.jobs.append(body['job_id'])
            self.docs.append(upload_pipeline.identities(
                hashlib.sha256(data).hexdigest())['document_id'])
        return response

    def claim(self, upload_id):
        """Claim one job in its own transaction, exactly like `work_once` does."""
        with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            return upload_pipeline._claim(cur, upload_id)

    def make_stale(self, upload_id):
        """Age the `running` row past `stale_running_seconds()` (what a wave timeout does)."""
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("UPDATE ingest_jobs SET started_at=now()-interval '10 minutes' "
                        'WHERE job_id=%s', (upload_pipeline.job_id_for(upload_id),))

    def job_row(self, upload_id):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT status,error FROM ingest_jobs WHERE job_id=%s',
                        (upload_pipeline.job_id_for(upload_id),))
            return cur.fetchone()

    def claim_row(self, upload_id):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT claim_token,attempt FROM ingest_job_claims WHERE job_id=%s',
                        (upload_pipeline.job_id_for(upload_id),))
            return cur.fetchone()

    def job_snapshot(self, upload_id):
        """The *queue* identity of one upload: the job row plus its claim generation row."""
        job_id = upload_pipeline.job_id_for(upload_id)
        with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM ingest_jobs WHERE job_id=%s', (job_id,))
            job = cur.fetchone()
            cur.execute('SELECT * FROM ingest_job_claims WHERE job_id=%s', (job_id,))
            claim = cur.fetchone()
        return json.loads(json.dumps({'job': job, 'claim': claim}, default=str, sort_keys=True))

    def asset_row(self, document_id):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT status,needs_review,parser_version,quality_detail '
                        'FROM document_assets WHERE document_id=%s', (document_id,))
            return cur.fetchone()

    def contract_rows(self, document_id):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM document_contract_blocks WHERE document_id=%s',
                        (document_id,))
            return cur.fetchone()[0]

    def snapshot(self, document_id):
        """Every row these tables hold for the document, normalised for comparison."""
        data = {}
        with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            for table in SNAPSHOT_TABLES:
                if table == 'ingest_jobs':
                    cur.execute('SELECT * FROM ingest_jobs WHERE document_id=%s ORDER BY job_id',
                                (document_id,))
                elif table == 'ingest_job_claims':
                    cur.execute('SELECT c.* FROM ingest_job_claims c JOIN ingest_jobs j '
                                'ON j.job_id=c.job_id WHERE j.document_id=%s ORDER BY c.job_id',
                                (document_id,))
                else:
                    cur.execute(f'SELECT * FROM {table} WHERE document_id=%s ORDER BY 1',
                                (document_id,))
                data[table] = cur.fetchall()
        return json.loads(json.dumps(data, default=str, sort_keys=True))

    def detail(self, upload_id):
        response = self.admin.get('/v1/uploads/' + upload_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------- 1. the re-upload rule (R04.3)
    def test_a_duplicate_upload_does_not_disown_a_running_claim(self):
        """The exact rule, asserted: `running` is never reset by a re-upload."""
        created = self.upload()
        self.assertEqual(created.status_code, 201, created.text)
        upload_id = created.json()['upload_id']
        claim_a = self.claim(upload_id)
        self.assertEqual(claim_a['attempt'], 1)
        before = self.job_snapshot(upload_id)

        repeat = self.upload()
        self.assertEqual(repeat.status_code, 201, repeat.text)
        self.assertEqual(repeat.json()['status'], 'running',
                         'a re-upload of a running job reports its current status')
        self.assertEqual(self.job_snapshot(upload_id), before,
                         'a re-upload of a running job changes nothing about the claim')
        self.assertEqual(self.job_row(upload_id), ('running', None))
        token, attempt = self.claim_row(upload_id)
        self.assertEqual((token, attempt), (claim_a['claim_token'], 1))

        # And a second worker cannot claim it while the first claim is fresh.
        self.assertIsNone(self.claim(upload_id))
        self.assertEqual(self.claim_row(upload_id), (claim_a['claim_token'], 1))

    def test_a_terminal_job_is_re_armed_into_a_new_generation(self):
        created = self.upload()
        upload_id = created.json()['upload_id']
        document_id = self.docs[-1]
        claim_a = self.claim(upload_id)
        self.assertTrue(upload_pipeline.run_parse(claim_a, self.dsn)['finalized'])
        self.assertEqual(self.job_row(upload_id), ('succeeded', None))
        self.assertIsNone(self.claim_row(upload_id)[0], 'a terminal job holds no live token')

        # A terminal job re-armed by a re-upload goes back to `queued`; the *next* claim
        # opens generation 2 (attempt increments, token is new and never reused).
        repeat = self.upload()
        self.assertEqual(repeat.json()['status'], 'queued')
        self.assertEqual(self.job_row(upload_id), ('queued', None))
        self.assertEqual(self.claim_row(upload_id), (None, 1))
        claim_b = self.claim(upload_id)
        self.assertEqual(claim_b['attempt'], 2)
        self.assertNotEqual(claim_b['claim_token'], claim_a['claim_token'])

    def test_two_admins_uploading_the_same_content_keep_one_generation(self):
        """Concurrent duplicate uploads share the identity without a second generation."""
        first = self.upload()
        upload_id = first.json()['upload_id']
        claim_a = self.claim(upload_id)
        second = self.upload(client=self.admin_two)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()['upload_id'], upload_id)
        self.assertEqual(second.json()['status'], 'running')
        self.assertEqual(self.claim_row(upload_id), (claim_a['claim_token'], 1))
        self.assertEqual(self.contract_rows(self.docs[-1]), 0, 'nothing parsed yet')

    # ------------------------------------- 2. the audit interleaving, step by step
    def test_a_late_generation_cannot_write_its_result_or_a_terminal_state(self):
        """A claims -> the same bytes are re-uploaded -> B claims -> A finishes late."""
        created = self.upload()
        upload_id = created.json()['upload_id']
        document_id = self.docs[-1]
        claim_a = self.claim(upload_id)

        repeat = self.upload()
        self.assertEqual(repeat.json()['status'], 'running')
        self.make_stale(upload_id)
        claim_b = self.claim(upload_id)
        self.assertIsNotNone(claim_b, 'a stale running row is reclaimable')
        self.assertEqual(claim_b['attempt'], 2)
        self.assertNotEqual(claim_b['claim_token'], claim_a['claim_token'])

        before = self.snapshot(document_id)
        # A's parse completes and tries to commit its contract result and terminal status.
        with patch.dict(upload_pipeline.PARSERS, {'.dxf': marker_parser('late-A')}):
            value_a = upload_pipeline.run_parse(claim_a, self.dsn)
        self.assertTrue(value_a['claim_lost'])
        self.assertFalse(value_a['finalized'])
        self.assertEqual(self.snapshot(document_id), before,
                         'A must not change status, blocks, locators or quality detail')
        # The terminal path of the very same generation is fenced too.
        self.assertFalse(upload_pipeline._finish(self.dsn, claim_a, 'succeeded'))
        self.assertFalse(upload_pipeline._finish(self.dsn, claim_a, 'failed',
                                                 'UPLOAD_PARSE_FAILED'))
        self.assertEqual(self.snapshot(document_id), before)
        self.assertEqual(self.job_row(upload_id), ('running', None))

        # B finishes: its result is the one that survives, and the job is B's terminal state.
        with patch.dict(upload_pipeline.PARSERS, {'.dxf': marker_parser('gen-B')}):
            value_b = upload_pipeline.run_parse(claim_b, self.dsn)
        self.assertTrue(value_b['finalized'])
        self.assertFalse(value_b['claim_lost'])
        self.assertGreaterEqual(value_b['blocks'], 1)
        self.assertEqual(self.job_row(upload_id), ('succeeded', None))
        self.assertEqual(self.asset_row(document_id)[2], 'gen-B',
                         "the persisted parser version must be B's, not A's")
        self.assertEqual(self.contract_rows(document_id), value_b['blocks'])
        self.assertEqual(self.claim_row(upload_id), (None, 2))
        detail = self.detail(upload_id)
        self.assertEqual(detail['status'], 'succeeded')
        self.assertEqual(detail['attempt'], 2)
        self.assertEqual(detail['analysis']['blocks'], value_b['blocks'])
        self.assertNotIn('late-A', json.dumps(detail))

    def test_a_late_wave_timeout_record_does_not_touch_the_newer_generation(self):
        created = self.upload()
        upload_id = created.json()['upload_id']
        document_id = self.docs[-1]
        claim_a = self.claim(upload_id)
        self.make_stale(upload_id)
        claim_b = self.claim(upload_id)
        before = self.snapshot(document_id)

        self.assertIsNone(upload_pipeline._record_wave_timeout(self.dsn, claim_a))
        self.assertEqual(self.snapshot(document_id), before,
                         'a stale generation must not bump the wave-timeout counter')
        timeout = {'status': 'timeout', 'error_code': 'TASK_DEADLINE_EXCEEDED',
                   'deadline_seconds': 45.0, 'elapsed_ms': 45000.0}
        with patch.object(upload_pipeline.task_budget, 'run_bounded', return_value=timeout):
            outcome = upload_pipeline._execute(self.dsn, claim_a)
        self.assertEqual(outcome['outcome'], 'claim_lost')
        self.assertEqual(outcome['error_code'], 'UPLOAD_CLAIM_LOST')
        self.assertEqual(self.snapshot(document_id), before)
        # The live generation still owns the row and can be finished by B.
        self.assertEqual(self.detail(upload_id)['wave_timeouts'], 0)
        self.assertTrue(upload_pipeline.run_parse(claim_b, self.dsn)['finalized'])

    # -------------------------------------- 3. cancel, retry and re-upload ordering
    def test_cancel_fences_the_running_claim_and_a_re_upload_opens_generation_two(self):
        created = self.upload()
        upload_id = created.json()['upload_id']
        document_id = self.docs[-1]
        claim_a = self.claim(upload_id)
        cancelled = upload_pipeline.cancel(upload_id, self.dsn, self.demo_identity())
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(self.job_row(upload_id), ('cancelled', None))
        self.assertEqual(self.claim_row(upload_id), (None, 1))
        # `claim_token` triple: no claim row token, attempt history kept.

        before = self.snapshot(document_id)
        with patch.dict(upload_pipeline.PARSERS, {'.dxf': marker_parser('after-cancel-A')}):
            value_a = upload_pipeline.run_parse(claim_a, self.dsn)
        self.assertTrue(value_a['claim_lost'])
        self.assertEqual(self.snapshot(document_id), before,
                         'a cancelled generation cannot write results or terminal state')

        # A re-upload after cancellation re-arms the job (terminal -> new generation).
        repeat = self.upload()
        self.assertEqual(repeat.json()['status'], 'queued')
        self.assertEqual(self.claim_row(upload_id), (None, 1))
        claim_b = self.claim(upload_id)
        self.assertEqual(claim_b['attempt'], 2)
        with patch.dict(upload_pipeline.PARSERS, {'.dxf': marker_parser('gen-B')}):
            self.assertTrue(upload_pipeline.run_parse(claim_b, self.dsn)['finalized'])
        self.assertEqual(self.asset_row(document_id)[2], 'gen-B')

    def test_retry_deads_the_previous_generation_token(self):
        created = self.upload()
        upload_id = created.json()['upload_id']
        document_id = self.docs[-1]
        claim_a = self.claim(upload_id)
        self.assertTrue(upload_pipeline._finish(self.dsn, claim_a, 'failed', 'PARSE_ADAPTER_UNKNOWN'))
        self.assertEqual(self.job_row(upload_id), ('failed', 'PARSE_ADAPTER_UNKNOWN'))
        self.assertEqual(self.claim_row(upload_id), (None, 1))

        retried = upload_pipeline.retry(upload_id, self.dsn, self.demo_identity())
        self.assertEqual(retried['status'], 'queued')
        before = self.snapshot(document_id)
        self.assertFalse(upload_pipeline._finish(self.dsn, claim_a, 'succeeded'))
        self.assertTrue(upload_pipeline.run_parse(claim_a, self.dsn)['claim_lost'])
        self.assertEqual(self.snapshot(document_id), before)
        claim_b = self.claim(upload_id)
        self.assertEqual(claim_b['attempt'], 2)
        self.assertTrue(upload_pipeline.run_parse(claim_b, self.dsn)['finalized'])

    # ------------------------------------------ 4. interruption before the commit
    def test_an_interrupted_commit_leaves_no_half_result_and_no_wrong_terminal_state(self):
        """The parse finished; the transaction dies just before it commits."""
        created = self.upload()
        upload_id = created.json()['upload_id']
        document_id = self.docs[-1]
        claim_a = self.claim(upload_id)
        before = self.snapshot(document_id)

        with patch.object(upload_pipeline, '_clear_claim_token',
                          side_effect=RuntimeError('interrupted before commit')):
            with self.assertRaises(RuntimeError):
                upload_pipeline.run_parse(claim_a, self.dsn)

        # Contract rows, locators, quality detail and the terminal status were all written
        # *before* the failure inside the same transaction, so all of them must be gone.
        self.assertEqual(self.snapshot(document_id), before)
        self.assertEqual(self.contract_rows(document_id), 0)
        self.assertEqual(self.job_row(upload_id), ('running', None),
                         'an interrupted commit must not leave a terminal state')
        self.assertEqual(self.claim_row(upload_id)[0], claim_a['claim_token'],
                         'the claim is still live, so the job stays resumable')
        quality = self.asset_row(document_id)[3]
        self.assertNotIn('analysis', quality or {})

        # And the same claim can be finished afterwards — recovery, not residue.
        value = upload_pipeline.run_parse(claim_a, self.dsn)
        self.assertTrue(value['finalized'])
        self.assertEqual(self.job_row(upload_id), ('succeeded', None))
        self.assertEqual(self.contract_rows(document_id), value['blocks'])

    def test_a_finished_generation_survives_a_parent_that_reports_failure(self):
        """The child committed its result and then died before the parent read stdout.

        `run_bounded` can only report a non-zero exit; the parent must not downgrade a job
        whose generation already committed and closed its token.
        """
        created = self.upload()
        upload_id = created.json()['upload_id']
        document_id = self.docs[-1]
        claim_a = self.claim(upload_id)
        value = upload_pipeline.run_parse(claim_a, self.dsn)      # the child's commit
        self.assertTrue(value['finalized'])
        committed = self.snapshot(document_id)

        crashed = {'status': 'failed', 'error_code': 'TASK_RESULT_INVALID', 'elapsed_ms': 1.0}
        with patch.object(upload_pipeline.task_budget, 'run_bounded', return_value=crashed):
            outcome = upload_pipeline._execute(self.dsn, claim_a)
        self.assertEqual(outcome['outcome'], 'claim_lost')
        self.assertEqual(self.snapshot(document_id), committed,
                         'a committed generation must not be rewritten by a late parent')
        self.assertEqual(self.job_row(upload_id), ('succeeded', None))
        self.assertEqual(self.detail(upload_id)['status'], 'succeeded')

    # --------------------------------------------------- 5. the generation is explicit
    def test_every_claim_returns_a_unique_token_and_a_monotone_attempt(self):
        created = self.upload()
        upload_id = created.json()['upload_id']
        tokens, attempts = [], []
        for expected in (1, 2, 3):
            claim = self.claim(upload_id)
            self.assertIsNotNone(claim, expected)
            tokens.append(claim['claim_token'])
            attempts.append(claim['attempt'])
            # A failed generation is re-armed explicitly; the next claim is the next one.
            self.assertTrue(upload_pipeline._finish(self.dsn, claim, 'failed',
                                                    'UPLOAD_PARSE_FAILED'))
            self.assertEqual(upload_pipeline.retry(upload_id, self.dsn,
                                                   self.demo_identity())['status'], 'queued')
        self.assertEqual(attempts, [1, 2, 3])
        self.assertEqual(len(set(tokens)), 3, 'tokens must never be reused')
        self.assertTrue(all(len(token) >= 32 for token in tokens))

    def demo_identity(self):
        """The demo/admin identity `lock_identity` accepts without a session row."""
        return {'_demo': True, 'user_id': self.admin._fixture_users[0],
                'display_name': '隔离测试', 'roles': ['admin']}


if __name__ == '__main__':
    unittest.main()
