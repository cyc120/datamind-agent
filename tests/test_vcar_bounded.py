"""V-CAR nodes executed in their own process group under the shared task budget."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from datamind import task_budget, vcar

TESTS = Path(__file__).resolve().parent
CHILD_ENV = {'PYTHONPATH': str(TESTS)}


def node(node_id, tool='sql', depends_on=(), **extra):
    item = {'node_id': node_id, 'depends_on': list(depends_on), 'tool': tool,
            'input': {}, 'expect': None, 'permission': 'admin'}
    item.update(extra)
    return item


def target(function):
    return ('vcar_targets', function)


def fixtures_anonymous():
    from fastapi.testclient import TestClient

    from datamind.api import app
    return TestClient(app)


def fixtures_client():
    """Authenticated admin TestClient with a disposable provisioned account."""
    from fastapi.testclient import TestClient

    from datamind.api import app
    import auth_fixtures
    client = TestClient(app)
    auth_fixtures.login(client, ('admin',), name='V-CAR 隔离测试')
    return client


class BoundedRunnerTests(unittest.TestCase):
    def reason(self, code, action):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail['error_code'], code)

    def test_node_runs_in_its_own_process_and_receives_the_payload(self):
        plan = vcar.plan([node('a'), node('b', tool='rag', depends_on=['a'])])
        runners = vcar.bounded_runners({'sql': target('echo'), 'rag': target('echo')},
                                       env=CHILD_ENV, deadline_s=30)
        result = vcar.execute(plan, runners, budget={'deadline_s': 60})
        self.assertEqual(result['status'], 'completed', result['gaps'])
        first, second = result['nodes']
        self.assertNotEqual(first['result']['pid'], os.getpid())
        self.assertEqual(first['result']['node_id'], 'a')
        self.assertEqual(second['result']['inputs'], {'a': first['result']})
        self.assertNotEqual(second['result']['pid'], os.getpid())

    def test_targets_are_allowlisted_and_validated(self):
        for targets in ({'unknown_tool': target('echo')},
                        {'sql': ('vcar_targets',)},
                        {'sql': ('../evil', 'echo')},
                        {'sql': ('vcar_targets', 'echo; rm -rf /')},
                        {'sql': 'not-a-tuple'},
                        'not-a-dict'):
            with self.assertRaises(ValueError) as caught:
                vcar.bounded_runners(targets, env=CHILD_ENV)
            self.assertIn(str(caught.exception), ('VCAR_TARGETS_INVALID', 'VCAR_TARGET_INVALID'))

    def test_node_is_terminated_when_the_shared_budget_runs_out(self):
        runners = vcar.bounded_runners({'sql': target('sleep_forever')}, env=CHILD_ENV)
        started = time.monotonic()
        result = vcar.execute(vcar.plan([node('a', input={'seconds': 300})]), runners,
                              budget={'deadline_s': 2, 'max_repair': 0})
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 20, 'node outlived the shared deadline')
        codes = {item['node_id']: item['error_code'] for item in result['nodes']}
        self.assertIn(codes['a'], ('PLAN_DEADLINE_EXCEEDED', 'PLAN_BUDGET_EXHAUSTED'), result)

    def test_cancellation_kills_the_node_process_group_and_skips_the_rest(self):
        directory = Path(tempfile.mkdtemp())
        marker = directory / 'pid'
        cancel = threading.Event()
        context = vcar.ExecutionContext(cancel_event=cancel)
        runners = vcar.bounded_runners({'sql': target('mark_and_sleep')}, env=CHILD_ENV, context=context)
        # `b` depends on `a`, so it is still pending when the cancellation arrives.
        plan = vcar.plan([node('a', input={'marker': str(marker), 'seconds': 300}),
                          node('b', depends_on=['a'])])
        outcome = {}

        def run():
            outcome.update(vcar.execute(plan, runners, budget={'deadline_s': 120, 'max_repair': 0},
                                        context=context, cancel_event=cancel))

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (marker.exists() and marker.read_text().strip()):
            time.sleep(0.05)
        self.assertTrue(marker.exists() and marker.read_text().strip(), 'child never started')
        child = int(marker.read_text().strip())
        cancel.set()
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive(), 'run did not stop after cancellation')
        statuses = {item['node_id']: item['status'] for item in outcome['nodes']}
        self.assertEqual(statuses['a'], 'failed', outcome)
        self.assertEqual(statuses['b'], 'skipped', outcome)
        codes = {item['node_id']: item['error_code'] for item in outcome['nodes']}
        self.assertEqual(codes['a'], 'PLAN_CANCELLED', outcome)
        # A pending dependent is cancelled; when the failure is recorded first it is
        # skipped as a failed dependency instead.
        self.assertIn(codes['b'], ('PLAN_CANCELLED', 'PLAN_DEPENDENCY_FAILED'), outcome)
        alive = True
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.1)
        self.assertFalse(alive, 'cancelled node process survived')

    def test_node_result_budgets_are_accumulated(self):
        runners = vcar.bounded_runners({'sql': target('rows')}, env=CHILD_ENV, deadline_s=30)
        # Budgets are enforced at dispatch boundaries, so the second node depends on
        # the first: with concurrency, already-dispatched nodes still complete.
        result = vcar.execute(vcar.plan([node('a', input={'rows': 500, 'tokens': 20}),
                                         node('b', input={'rows': 1}, depends_on=['a'])]), runners,
                              budget={'deadline_s': 60, 'max_rows': 100})
        self.assertEqual(result['nodes'][0]['status'], 'succeeded')
        self.assertEqual(result['nodes'][1]['status'], 'budget_exhausted')
        self.assertEqual(result['budget']['used_rows'], 500)

    def test_busy_resource_slot_is_reported_as_a_node_failure(self):
        dsn = 'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'
        runners = vcar.bounded_runners({'sql': target('echo')}, env=CHILD_ENV, dsn=dsn, slots=1,
                                       deadline_s=30)
        with task_budget.resource_slot(dsn, 'vcar-sql', 1):
            result = vcar.execute(vcar.plan([node('a')]), runners,
                                  budget={'deadline_s': 60, 'max_repair': 0})
        self.assertEqual(result['nodes'][0]['status'], 'failed')
        self.assertEqual(result['nodes'][0]['error_code'], 'TASK_BUDGET_BUSY')

    def test_execution_context_exposes_remaining_budget(self):
        context = vcar.ExecutionContext()
        self.assertIsNone(context.remaining())
        self.assertFalse(context.cancelled)
        vcar.execute(vcar.plan([node('a')]),
                     vcar.bounded_runners({'sql': target('echo')}, env=CHILD_ENV, context=context),
                     budget={'deadline_s': 1}, context=context)
        self.assertEqual(context.budget['deadline_s'], 1)
        self.assertLess(context.remaining(), 5)


if __name__ == '__main__':
    unittest.main()


class PlanEndpointTests(unittest.TestCase):
    """The HTTP entry resolves tools server-side and fails closed when unconfigured."""

    def setUp(self):
        self.client = fixtures_client()

    def post(self, payload):
        return self.client.post('/v1/plans/execute', json=payload)

    def test_endpoint_requires_admin_and_rejects_bad_requests(self):
        unauthenticated = fixtures_anonymous()
        self.assertIn(unauthenticated.post('/v1/plans/execute', json={'nodes': [node('a')]}).status_code,
                      (401, 403))
        for bad in ({}, {'nodes': []}, {'nodes': 'x'}, {'unknown': 1}, {'nodes': [], 'budget': {'x': 1}}):
            response = self.post(bad)
            self.assertEqual(response.status_code, 422, (bad, response.text))

    def test_endpoint_fails_closed_without_a_server_side_registry(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'DATAMIND_VCAR_TARGETS': ''}, clear=False):
            response = self.post({'nodes': [node('a')]})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()['detail']['error_code'], 'VCAR_TARGETS_NOT_CONFIGURED')

    def test_endpoint_runs_a_registered_target_in_a_child_process(self):
        import json as _json
        import os
        from unittest.mock import patch
        env = {'PYTHONPATH': str(TESTS),
               'DATAMIND_VCAR_TARGETS': _json.dumps({'sql': ['vcar_targets', 'echo']})}
        with patch.dict(os.environ, env, clear=False):
            response = self.post({'nodes': [node('a', input={'k': 'v'})], 'budget': {'deadline_s': 60}})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'completed', body.get('gaps'))
        self.assertNotEqual(body['nodes'][0]['result']['pid'], os.getpid())
        self.assertEqual(body['nodes'][0]['result']['input'], {'k': 'v'})
