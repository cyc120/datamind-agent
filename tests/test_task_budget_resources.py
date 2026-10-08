"""Task budget resource limits (CPU / address space) and cross-host leases.

The CPU limit is enforced by the platform; the address-space limit is refused where the
platform will not honour it (macOS rejects ``RLIMIT_AS``), so the tests assert the
honest outcome per platform instead of assuming a cap took effect.
"""
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

import psycopg2

from datamind import task_budget
from datamind.agent import _write_dsn

TESTS = Path(__file__).resolve().parent
CHILD_ENV = {'PYTHONPATH': str(TESTS)}


class ResourceLimitTests(unittest.TestCase):
    def test_platform_support_is_probed_and_cached(self):
        self.assertIn(task_budget.rlimit_supported('cpu'), (True, False))
        self.assertIn(task_budget.rlimit_supported('memory'), (True, False))
        # Cached, so a second call cannot spawn another probe.
        self.assertEqual(task_budget.rlimit_supported('cpu'), task_budget.rlimit_supported('cpu'))
        with self.assertRaises(ValueError):
            task_budget.rlimit_supported('disk')

    def test_limit_arguments_are_validated(self):
        for kwargs in ({'memory_bytes': 1}, {'memory_bytes': True},
                       {'memory_bytes': task_budget.MAX_MEMORY_BYTES + 1},
                       {'cpu_seconds': 0}, {'cpu_seconds': True}, {'cpu_seconds': 99999}):
            with self.assertRaises(ValueError):
                task_budget.run_bounded('budget_targets', 'echo', [{}], deadline_s=30, **kwargs)

    def test_unbounded_run_reports_no_limits(self):
        result = task_budget.run_bounded('budget_targets', 'echo', [{'a': 1}], deadline_s=30, env=CHILD_ENV)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['limits'], {'memory_bytes': None, 'cpu_seconds': None})

    def test_cpu_limit_terminates_the_child_with_a_stable_code(self):
        if not task_budget.rlimit_supported('cpu'):
            self.skipTest('platform does not enforce RLIMIT_CPU')
        started = time.monotonic()
        result = task_budget.run_bounded('budget_targets', 'spin', [{'seconds': 300}],
                                         deadline_s=120, cpu_seconds=1, grace_s=1, env=CHILD_ENV)
        elapsed = time.monotonic() - started
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error_code'], task_budget.CPU_LIMIT_ERROR_CODE)
        self.assertEqual(result['limits']['cpu_seconds'], 1)
        self.assertLess(elapsed, 30, 'CPU limit was not enforced promptly')

    def test_memory_limit_is_refused_or_enforced_never_silently_ignored(self):
        requested = 512 * 1024 * 1024
        result = task_budget.run_bounded('budget_targets', 'allocate', [{'blocks': 32}],
                                         deadline_s=60, memory_bytes=requested, env=CHILD_ENV)
        if task_budget.rlimit_supported('memory'):
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(result['error_code'], task_budget.MEMORY_LIMIT_ERROR_CODE)
        else:
            # The platform refuses the cap, so the task must not have run at all.
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(result['error_code'], task_budget.RLIMIT_UNSUPPORTED_ERROR_CODE)
            self.assertIsNone(result['exit_code'])
            self.assertNotIn('value', result)

    def test_command_runner_accepts_the_same_limits(self):
        result = task_budget.run_bounded_command(['/bin/sh', '-c', 'printf ok'], deadline_s=30,
                                                 cpu_seconds=5)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['limits']['cpu_seconds'], 5)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.name = 'lease-test-' + uuid.uuid4().hex[:12]

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM task_leases WHERE name=%s', (self.name,))

    def expire(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("UPDATE task_leases SET expires_at=clock_timestamp()-interval '1 second' "
                        'WHERE name=%s', (self.name,))

    def test_lease_is_exclusive_and_fenced(self):
        first = task_budget.acquire_lease(self.dsn, self.name, 60, holder='host-a')
        self.assertIsNotNone(first)
        self.assertEqual(first['fence'], 1)
        self.assertEqual(first['holder'], 'host-a')
        self.assertIsNone(task_budget.acquire_lease(self.dsn, self.name, 60, holder='host-b'))

        self.expire()
        second = task_budget.acquire_lease(self.dsn, self.name, 60, holder='host-b')
        self.assertIsNotNone(second)
        self.assertEqual(second['fence'], 2, '接管必须递增 fence')
        self.assertNotEqual(second['token'], first['token'])

    def test_stale_holder_cannot_renew_or_release(self):
        first = task_budget.acquire_lease(self.dsn, self.name, 60, holder='host-a')
        self.expire()
        second = task_budget.acquire_lease(self.dsn, self.name, 60, holder='host-b')
        self.assertIsNone(task_budget.renew_lease(self.dsn, self.name, first['token'], 60))
        self.assertFalse(task_budget.release_lease(self.dsn, self.name, first['token']))
        self.assertIsNotNone(task_budget.renew_lease(self.dsn, self.name, second['token'], 60))
        self.assertTrue(task_budget.release_lease(self.dsn, self.name, second['token']))
        self.assertIsNone(task_budget.lease_holder(self.dsn, self.name))

    def test_holder_reports_expiry_and_sweep_removes_expired_rows(self):
        task_budget.acquire_lease(self.dsn, self.name, 60, holder='host-a')
        self.assertFalse(task_budget.lease_holder(self.dsn, self.name)['expired'])
        self.expire()
        self.assertTrue(task_budget.lease_holder(self.dsn, self.name)['expired'])
        self.assertIn(self.name, task_budget.sweep_leases(self.dsn))
        self.assertIsNone(task_budget.lease_holder(self.dsn, self.name))

    def test_lease_arguments_are_validated(self):
        for name in ('', '../etc', 'a' * 200, 3):
            with self.assertRaises(ValueError):
                task_budget.acquire_lease(self.dsn, name, 60, holder='host-a')
        for ttl in (0, -1, True, task_budget.LEASE_TTL_MAX + 1, 'x'):
            with self.assertRaises(ValueError):
                task_budget.acquire_lease(self.dsn, self.name, ttl, holder='host-a')
        for holder in ('', '   ', 3, 'x' * 201):
            with self.assertRaises(ValueError):
                task_budget.acquire_lease(self.dsn, self.name, 60, holder=holder)

    def test_an_independent_process_cannot_take_a_held_lease(self):
        task_budget.acquire_lease(self.dsn, self.name, 60, holder='host-a')
        script = ('import sys\n'
                  'from datamind import task_budget\n'
                  'result = task_budget.acquire_lease(sys.argv[1], sys.argv[2], 60, holder="host-b")\n'
                  'print("TAKEN" if result else "HELD")\n')
        # The isolated regression redirects metadata connections in-process, so the child
        # must be handed a DSN that already names the same database.
        completed = subprocess.run([sys.executable, '-c', script, task_budget.effective_dsn(self.dsn), self.name],
                                   cwd=str(Path(__file__).resolve().parents[1]),
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'HELD', completed.stdout)


if __name__ == '__main__':
    unittest.main()


class StdinDeadlineTests(unittest.TestCase):
    """F04: the wall-clock deadline must also cover the standard-input write."""

    def test_a_child_that_never_reads_stdin_cannot_bypass_the_deadline(self):
        payload = 'x' * (8 * 1024 * 1024)  # far beyond any pipe buffer
        started = time.monotonic()
        result = task_budget.run_bounded_command(
            [sys.executable, '-c', 'import time; time.sleep(300)'], payload,
            deadline_s=1, grace_s=1)
        elapsed = time.monotonic() - started
        self.assertEqual(result['status'], 'timeout', result)
        self.assertEqual(result['error_code'], 'TASK_DEADLINE_EXCEEDED')
        self.assertLess(elapsed, 6.0, f'写入 stdin 绕过了 deadline：{elapsed:.1f}s')

    def test_a_child_that_reads_stdin_normally_still_succeeds(self):
        result = task_budget.run_bounded_command(
            [sys.executable, '-c', 'import sys; print(len(sys.stdin.read()))'], 'abc',
            deadline_s=15)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['stdout'].strip(), '3')

    def test_a_large_payload_to_a_reading_child_is_delivered_completely(self):
        size = 4 * 1024 * 1024
        reader = 'import sys; data = sys.stdin.buffer.read(); print(len(data))'
        result = task_budget.run_bounded_command([sys.executable, '-c', reader], 'y' * size,
                                                 deadline_s=30)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['stdout'].strip(), str(size))
