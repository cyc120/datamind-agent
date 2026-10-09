"""The standalone upload worker and the read-only GET (review 5.1).

Before this change `GET /v1/uploads/{upload_id}` called `work_once`: the browser poll was
the queue's clock (closing the page stopped every parse) and N HTTP requests could start N
parses. These tests pin both halves of the fix:

* the detail GET is a **pure read** — it never claims, never parses and never writes, so a
  job stays `queued` until something else drives it;
* `python -m ingestion.upload_worker --once/--watch` is that something, it publishes a
  heartbeat, and `GET /v1/uploads` + `/health` report `worker.alive` honestly (a `queued`
  job with no worker must not be presented as "解析中").

The `--once` path runs the real bounded child parse. `--watch` is exercised as a real
subprocess; it is handed the database this process is actually connected to (the isolated
regression redirects connections in-process, so the child must be told the clone's DSN
explicitly, exactly as `task_budget.effective_dsn` documents). Staging is redirected to a
temporary root *inside the checkout* because the bounded child resolves the staged path
against its own repository root. All rows and staged files are removed in teardown.
"""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import psycopg2

from datamind import file_uploads, task_budget
from datamind.agent import _write_dsn
from datamind.api import app
from ingestion import upload_pipeline, upload_worker
import auth_fixtures

ROOT = Path(__file__).resolve().parents[1]
DXF = ROOT / 'data/cad_demo/development/dev-mm.dxf'
MEASUREMENT = ROOT / 'data/dimension_demo/development/demo-diameter.json'
#: `--watch` may need a couple of wave cycles; this is a bounded integration wait, not a
#: race between two workers.
WATCH_TIMEOUT_SECONDS = 60.0


class UploadWorkerTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        (ROOT / 'tmp').mkdir(exist_ok=True)
        # Inside the checkout: the bounded child process computes its own ROOT from
        # `__file__`, so a staged file outside the repository would fail containment.
        self.temp = tempfile.TemporaryDirectory(dir=str(ROOT / 'tmp'))
        self.root = Path(self.temp.name)
        self.patches = [patch.object(file_uploads, 'ROOT', self.root),
                        patch.object(upload_pipeline, 'ROOT', self.root),
                        # Namespace this test's heartbeat so a worker from another session
                        # can never make a liveness assertion pass (or fail) for us.
                        patch.object(upload_pipeline, 'HEARTBEAT_PREFIX',
                                     'upload_worker_test_' + self.root.name[-8:])]
        for item in self.patches:
            item.start()
        self.addCleanup(self._stop)
        self.admin = TestClient(app, client=('worker-admin-' + self.root.name, 50100))
        auth_fixtures.login(self.admin, roles=('admin',))
        self.docs = []
        self.jobs = []
        self.processes = []
        self.addCleanup(self._cleanup)

    # ------------------------------------------------------------------ harness
    def _stop(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        # Heartbeat leases are not business rows, but a killed test worker must not leave one
        # behind either: drop this test's namespace and any lease naming a killed pid.
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM task_leases WHERE name LIKE %s',
                        (upload_pipeline.HEARTBEAT_PREFIX + ':%',))
            for process in self.processes:
                cur.execute("DELETE FROM task_leases WHERE name LIKE 'upload_worker:%%' "
                            'AND holder LIKE %s', (f'%pid={process.pid}%',))
        auth_fixtures.cleanup(self.admin)
        self.admin.close()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def _cleanup(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            for table in ('document_contract_blocks', 'document_block_relations',
                          'document_block_locators', 'chunks', 'document_blocks'):
                cur.execute(f'DELETE FROM {table} WHERE document_id=ANY(%s)', (self.docs,))
            cur.execute('DELETE FROM ingest_jobs WHERE document_id=ANY(%s)', (self.docs,))
            cur.execute('DELETE FROM document_assets WHERE document_id=ANY(%s)', (self.docs,))
            cur.execute('SELECT count(*) FROM ingest_job_claims WHERE job_id=ANY(%s)',
                        (self.jobs,))
            self.assertEqual(cur.fetchone()[0], 0, 'claim rows must not outlive their job')

    def upload(self, name='dev-mm.dxf', data=None):
        data = (DXF if name.endswith('.dxf') else MEASUREMENT).read_bytes() if data is None else data
        response = self.admin.post(
            '/v1/uploads', params={'filename': name, 'provenance': 'synthetic_demo'},
            content=data, headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.jobs.append(body['job_id'])
        self.docs.append(upload_pipeline.identities(hashlib.sha256(data).hexdigest())['document_id'])
        return body

    def job_status(self, upload_id):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT status FROM ingest_jobs WHERE job_id=%s',
                        (upload_pipeline.job_id_for(upload_id),))
            row = cur.fetchone()
        return row[0] if row else None

    def claim_row(self, upload_id):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT claim_token,attempt FROM ingest_job_claims WHERE job_id=%s',
                        (upload_pipeline.job_id_for(upload_id),))
            return cur.fetchone()

    def contract_rows(self, document_id):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM document_contract_blocks WHERE document_id=%s',
                        (document_id,))
            return cur.fetchone()[0]

    def heartbeat_rows(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('SELECT name,holder FROM task_leases WHERE name LIKE %s '
                        'ORDER BY name', (upload_pipeline.HEARTBEAT_PREFIX + ':%',))
            return cur.fetchall()

    def detail(self, upload_id):
        response = self.admin.get('/v1/uploads/' + upload_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def listing_worker(self):
        response = self.admin.get('/v1/uploads')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['worker']

    def run_worker(self, arguments):
        """Run the CLI in-process and capture its progress lines."""
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = upload_worker.main(arguments)
        return code, captured.getvalue()

    # ------------------------------------------------- 1. the GET is only a read now
    def test_the_detail_get_never_claims_parses_or_writes(self):
        body = self.upload()
        upload_id = body['upload_id']
        document_id = self.docs[-1]
        self.assertEqual(body['status'], 'queued')

        for _ in range(3):
            detail = self.detail(upload_id)
            self.assertEqual(detail['status'], 'queued',
                             'a GET must not advance the job any more')
            self.assertEqual(detail['analysis']['blocks'], 0)
            self.assertEqual(detail['attempt'], 0)
            self.assertFalse(detail['worker']['alive'],
                             'the detail must let a polling client see that nothing runs')
        self.assertEqual(self.job_status(upload_id), 'queued')
        self.assertIsNone(self.claim_row(upload_id), 'a GET must not claim a generation')
        self.assertEqual(self.contract_rows(document_id), 0)

        # Only the worker advances it.
        code, output = self.run_worker(['--once'])
        self.assertEqual(code, upload_worker.EXIT_OK, output)
        self.assertEqual(self.job_status(upload_id), 'succeeded')
        self.assertEqual(self.detail(upload_id)['status'], 'succeeded')
        self.assertGreaterEqual(self.contract_rows(document_id), 1)
        self.assertEqual(self.claim_row(upload_id), (None, 1))

    def test_once_drains_every_claimable_job_and_reports_progress(self):
        first = self.upload()
        second = self.upload('demo-diameter.json')
        code, output = self.run_worker(['--once', '--workers', '2'])
        self.assertEqual(code, upload_worker.EXIT_OK, output)
        self.assertIn(upload_worker.MARKER, output)
        self.assertIn('heartbeat lease=', output)
        self.assertIn('claimed=2', output)
        self.assertIn('mode=once', output)
        self.assertIn(f"job_id={first['job_id']}", output)
        self.assertIn('outcome=succeeded', output)
        self.assertIn('outcome=partial_failure', output)
        self.assertEqual(self.job_status(first['upload_id']), 'succeeded')
        self.assertEqual(self.job_status(second['upload_id']), 'partial_failure')
        self.assertEqual(self.heartbeat_rows(), [],
                         'a finished worker releases its heartbeat lease')

    def test_once_on_an_empty_queue_is_a_clean_no_op(self):
        code, output = self.run_worker(['--once'])
        self.assertEqual(code, upload_worker.EXIT_OK, output)
        self.assertIn('claimed=0', output)

    def test_an_invalid_command_line_is_a_usage_exit_code(self):
        for arguments in ([], ['--watch', '--once'], ['--once', '--workers', '5'],
                          ['--once', '--deadline', '0'], ['--once', '--interval', '0']):
            with contextlib.redirect_stderr(io.StringIO()):  # argparse prints its usage there
                with self.assertRaises(SystemExit) as caught:
                    upload_worker.main(arguments)
            self.assertEqual(caught.exception.code, upload_worker.EXIT_USAGE, arguments)

    # ------------------------------------------- 2. liveness (heartbeat) is honest
    def test_worker_alive_flips_with_the_heartbeat_and_the_threshold_is_explicit(self):
        with patch.dict(os.environ, {upload_pipeline.WORKER_ALIVE_ENV: '3'}):
            self.assertEqual(upload_pipeline.worker_alive_seconds(), 3.0)
            self.assertFalse(self.listing_worker()['alive'])
            self.assertFalse(self.admin.get('/health').json()['worker']['alive'])

            # A 60s renewal interval keeps this deterministic: nothing renews mid-test.
            heartbeat = upload_pipeline.WorkerHeartbeat(self.dsn, interval=60)
            heartbeat.start()
            try:
                listing = self.listing_worker()
                self.assertTrue(listing['alive'])
                self.assertEqual(listing['alive_seconds'], 3.0)
                self.assertTrue(listing['last_seen_at'].endswith('+00:00'), listing)
                health = self.admin.get('/health').json()
                self.assertEqual(health['worker'], listing,
                                 '/health and the listing must report the same heartbeat')
                # A crashed worker stops being alive once the threshold has passed, without
                # anything having to clean the row up.
                with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
                    cur.execute("UPDATE task_leases SET renewed_at=now()-interval '10 seconds' "
                                'WHERE name LIKE %s',
                                (upload_pipeline.HEARTBEAT_PREFIX + ':%',))
                self.assertFalse(self.listing_worker()['alive'])
                self.assertIsNotNone(self.listing_worker()['last_seen_at'])
            finally:
                heartbeat.stop()
            self.assertFalse(self.listing_worker()['alive'],
                             'a graceful stop releases the lease immediately')
            self.assertEqual(self.heartbeat_rows(), [])

    def test_the_alive_threshold_falls_back_on_a_malformed_override(self):
        for value in ('', '   ', 'not-a-number', '0', '0.5', '99999'):
            with patch.dict(os.environ, {upload_pipeline.WORKER_ALIVE_ENV: value}):
                self.assertEqual(upload_pipeline.worker_alive_seconds(),
                                 upload_pipeline.WORKER_ALIVE_SECONDS, value)

    def test_the_worker_refuses_to_start_without_a_heartbeat(self):
        with patch.object(upload_pipeline.WorkerHeartbeat, 'start',
                          side_effect=RuntimeError('UPLOAD_WORKER_HEARTBEAT_UNAVAILABLE')):
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                code = upload_worker.main(['--once'])
        self.assertEqual(code, upload_worker.EXIT_ERROR)
        self.assertIn('refusing to start', captured.getvalue())

    # ------------------------------- 3. cross-process slots bound the total concurrency
    def test_a_busy_parse_slot_is_a_stable_exit_code_and_claims_nothing(self):
        body = self.upload()
        with patch.dict(os.environ, {'DATAMIND_TASK_SLOTS': '1'}):
            with task_budget.resource_slot(self.dsn, upload_pipeline.UPLOAD_SLOT_NAME, 1):
                code, output = self.run_worker(['--once'])
        self.assertEqual(code, upload_worker.EXIT_SLOTS_BUSY, output)
        self.assertIn('slots busy', output)
        self.assertEqual(self.job_status(body['upload_id']), 'queued',
                         'a wave that could not take a slot must not claim or parse')
        self.assertIsNone(self.claim_row(body['upload_id']))

    # -------------------------------------------- 4. the real CLI drives the queue
    def test_watch_advances_a_job_without_any_http_poll_and_stops_gracefully(self):
        body = self.upload()
        upload_id = body['upload_id']
        log = self.root / 'worker.log'
        env = dict(os.environ)
        # The isolated regression redirects metadata connections *inside* the test process,
        # so the child must be handed a DSN that already names the same database.
        env['DATAMIND_META_WRITE_DSN'] = task_budget.effective_dsn(self.dsn)
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        # A real CLI process cannot see this test's patched prefix, so watch the namespace
        # it actually uses for the duration of this test.
        with patch.object(upload_pipeline, 'HEARTBEAT_PREFIX', 'upload_worker'):
            with log.open('w') as stream:
                process = subprocess.Popen(
                    [sys.executable, '-m', 'ingestion.upload_worker', '--watch',
                     '--interval', '0.2'],
                    cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=stream,
                    stderr=subprocess.STDOUT)
            self.processes.append(process)

            deadline = time.monotonic() + WATCH_TIMEOUT_SECONDS
            while time.monotonic() < deadline and self.job_status(upload_id) != 'succeeded':
                if process.poll() is not None:
                    break
                time.sleep(0.2)
            self.assertEqual(self.job_status(upload_id), 'succeeded',
                             'the worker must advance a queued job with no HTTP request at all')
            self.assertGreaterEqual(self.contract_rows(self.docs[-1]), 1)
            # Liveness while it runs: the heartbeat is visible to both surfaces.
            self.assertTrue(self.listing_worker()['alive'])
            self.assertTrue(self.admin.get('/health').json()['worker']['alive'])
            self.assertTrue(any(upload_worker.MARKER in line
                                for line in log.read_text().splitlines()))

            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=WATCH_TIMEOUT_SECONDS), upload_worker.EXIT_OK)
            lines = log.read_text()
            self.assertIn('signal=SIGTERM', lines)
            self.assertIn('heartbeat released', lines)
            # The worker's own lease row is gone; a *different* worker (if any) is not ours
            # to judge, so the global `alive` value is only asserted when nothing else runs.
            holders = [row[1] for row in self.heartbeat_rows()
                       if row[0].startswith('upload_worker:')]
            self.assertFalse(any(f'pid={process.pid}' in holder for holder in holders))
            if not holders:
                self.assertFalse(upload_pipeline.worker_heartbeat(self.dsn)['alive'])


if __name__ == '__main__':
    unittest.main()
