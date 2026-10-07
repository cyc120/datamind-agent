"""Unified task budget: hard process deadline, bounded output and cross-process slots.

Deadline tests use stdlib targets only, so they need no embedding model, vector store or
upstream provider. Slot tests use the metadata database the current process connected to.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import psycopg2

from datamind import task_budget
from datamind.agent import _write_dsn


class TaskBudgetConfigTests(unittest.TestCase):
    def test_env_overrides_are_validated(self):
        with patch.dict(os.environ, {'DATAMIND_TASK_DEADLINE_SECONDS': '12.5',
                                     'DATAMIND_TASK_OUTPUT_BYTES': '4096',
                                     'DATAMIND_TASK_SLOTS': '3',
                                     'DATAMIND_TASK_GRACE_SECONDS': '2'}, clear=False):
            self.assertEqual(task_budget.configured_deadline(), 12.5)
            self.assertEqual(task_budget.configured_output_limit(), 4096)
            self.assertEqual(task_budget.configured_slots(), 3)
            self.assertEqual(task_budget.configured_grace(), 2)

    def test_invalid_env_values_are_rejected(self):
        cases = [('DATAMIND_TASK_DEADLINE_SECONDS', '0'), ('DATAMIND_TASK_DEADLINE_SECONDS', 'nope'),
                 ('DATAMIND_TASK_DEADLINE_SECONDS', '99999'), ('DATAMIND_TASK_OUTPUT_BYTES', '1'),
                 ('DATAMIND_TASK_SLOTS', '0'), ('DATAMIND_TASK_SLOTS', '2.5'), ('DATAMIND_TASK_SLOTS', '99')]
        for name, value in cases:
            with patch.dict(os.environ, {name: value}, clear=False):
                with self.assertRaises(ValueError):
                    (task_budget.configured_slots() if name == 'DATAMIND_TASK_SLOTS' else
                     task_budget.configured_output_limit() if name == 'DATAMIND_TASK_OUTPUT_BYTES' else
                     task_budget.configured_deadline())

    def test_explicit_bounds_override_the_environment(self):
        with patch.dict(os.environ, {'DATAMIND_TASK_DEADLINE_SECONDS': '1'}, clear=False):
            self.assertEqual(task_budget.run_bounded('time', 'sleep', [0], deadline_s=30)['status'], 'ok')


class TaskBudgetRunnerTests(unittest.TestCase):
    def test_returns_a_value_from_a_child_process(self):
        result = task_budget.run_bounded('os', 'getpid', [], deadline_s=30)
        self.assertEqual(result['status'], 'ok', result)
        self.assertIsInstance(result['value'], int)
        self.assertNotEqual(result['value'], os.getpid())
        self.assertEqual(result['exit_code'], 0)
        self.assertFalse(result['output_truncated'])

    def test_hard_deadline_terminates_the_process_group(self):
        started = time.monotonic()
        result = task_budget.run_bounded('time', 'sleep', [60], deadline_s=1, grace_s=1)
        elapsed = time.monotonic() - started
        self.assertEqual(result['status'], 'timeout', result)
        self.assertEqual(result['error_code'], 'TASK_DEADLINE_EXCEEDED')
        self.assertLess(elapsed, 10, 'deadline was not enforced promptly')
        self.assertGreaterEqual(result['elapsed_ms'], 900)

    def test_deadline_also_kills_the_grandchild(self):
        directory = Path(tempfile.mkdtemp())
        marker = directory / 'pid'
        result = task_budget.run_bounded(
            'subprocess', 'run', [['sh', '-c', 'echo $$ > %s; sleep 60' % marker]],
            deadline_s=1, grace_s=2)
        self.assertEqual(result['status'], 'timeout', result)
        deadline = time.monotonic() + 5
        pid = None
        while time.monotonic() < deadline:
            if marker.exists() and marker.read_text().strip():
                pid = int(marker.read_text().strip())
                break
            time.sleep(0.05)
        self.assertIsNotNone(pid, 'grandchild never started')
        alive = True
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.1)
        self.assertFalse(alive, 'grandchild survived the deadline')

    def test_flooding_output_is_capped_and_cannot_produce_a_result(self):
        # A child that floods stdout past the cap can never smuggle a parsed result
        # back: its JSON is truncated, so the outcome is an explicit failure.
        result = task_budget.run_bounded('builtins', 'print', ['x' * 100000], deadline_s=30, output_limit=4096)
        self.assertEqual(result['status'], 'failed', result['status'])
        self.assertEqual(result['error_code'], 'TASK_RESULT_INVALID')
        self.assertTrue(result['output_truncated'])
        self.assertGreater(result['stdout_bytes'], 4096)
        self.assertLessEqual(len(result['stdout']), 4096)

    def test_child_error_code_and_status_are_propagated(self):
        result = task_budget.run_bounded('datamind.task_budget', 'fail', ['SYNTHETIC_DENIED'], deadline_s=30)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error_code'], 'SYNTHETIC_DENIED')
        self.assertEqual(result['status_code'], 409)
        self.assertNotIn('SYNTHETIC_DENIED', result['stderr'])

    def test_uncaught_target_error_uses_the_fallback_code(self):
        result = task_budget.run_bounded('os', 'getcwd', ['too', 'many'], deadline_s=30,
                                         fallback_error_code='SYNTHETIC_FALLBACK')
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error_code'], 'SYNTHETIC_FALLBACK')
        self.assertEqual(result['exit_code'], 0)
        # The runner converts the exception into a stable code; no traceback is forwarded.
        self.assertNotIn('TypeError', result['stderr'])

    def test_child_exiting_without_a_result_uses_the_fallback_code(self):
        result = task_budget.run_bounded('sys', 'exit', [3], deadline_s=30,
                                         fallback_error_code='SYNTHETIC_FALLBACK')
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error_code'], 'SYNTHETIC_FALLBACK')
        self.assertEqual(result['exit_code'], 3)

    def test_application_error_codes_propagate_from_a_bounded_task(self):
        # ingestion.pdf_prepare raises PdfPrepareError(.code); the runner must forward
        # the stable code rather than the generic fallback.
        request = {'document_id': 'doc-x', 'document_version': 1, 'source_sha256': 'a' * 64,
                   'job_id': 'job-x', 'attempt_token': 'attempt-x'}
        result = task_budget.run_bounded('ingestion.pdf_prepare', 'prepare_json',
                                         ['/nonexistent/task-budget-source.pdf', '/tmp/task-budget-none', request],
                                         deadline_s=30, fallback_error_code='SYNTHETIC_FALLBACK')
        self.assertEqual(result['status'], 'failed', result)
        self.assertTrue(str(result['error_code']).startswith('PDF_'), result['error_code'])

    def test_invalid_deadline_and_output_bounds_are_rejected(self):
        for kwargs in ({'deadline_s': 0}, {'deadline_s': 99999}, {'deadline_s': 'x'},
                       {'deadline_s': True}, {'output_limit': 1}, {'grace_s': -1}):
            with self.assertRaises(ValueError):
                task_budget.run_bounded('time', 'sleep', [0], **kwargs)

    def test_effective_dsn_names_the_database_this_process_uses(self):
        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            expected = cur.fetchone()[0]
        effective = task_budget.effective_dsn(_write_dsn())
        with psycopg2.connect(effective) as conn, conn.cursor() as cur:
            cur.execute('SELECT current_database()')
            self.assertEqual(cur.fetchone()[0], expected)


class TaskBudgetSlotTests(unittest.TestCase):
    def test_slots_are_bounded_and_released(self):
        name = 'task-budget-test-' + str(os.getpid())
        with task_budget.resource_slot(_write_dsn(), name, 2):
            with task_budget.resource_slot(_write_dsn(), name, 2):
                with self.assertRaises(HTTPException) as caught:
                    with task_budget.resource_slot(_write_dsn(), name, 2):
                        self.fail('a third slot was granted')
                self.assertEqual(caught.exception.status_code, 503)
                self.assertEqual(caught.exception.detail['error_code'], 'TASK_BUDGET_BUSY')
        with task_budget.resource_slot(_write_dsn(), name, 2):
            pass

    def test_slot_is_released_after_an_exception(self):
        name = 'task-budget-release-' + str(os.getpid())
        with self.assertRaisesRegex(RuntimeError, 'synthetic crash'):
            with task_budget.resource_slot(_write_dsn(), name, 1):
                raise RuntimeError('synthetic crash')
        with task_budget.resource_slot(_write_dsn(), name, 1):
            pass

    def test_slot_concurrency_must_be_valid(self):
        for value in (0, 99, True, 2.5):
            with self.assertRaises(ValueError):
                with task_budget.resource_slot(_write_dsn(), 'task-budget-invalid', value):
                    self.fail('invalid concurrency was accepted')


if __name__ == '__main__':
    unittest.main()
