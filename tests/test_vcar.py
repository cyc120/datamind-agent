"""V-CAR dependency plans: ordering, concurrency, partial repair and shared budgets."""
import threading
import time
import unittest

from fastapi import HTTPException

from datamind import vcar


def node(node_id, tool='sql', depends_on=(), **extra):
    item = {'node_id': node_id, 'depends_on': list(depends_on), 'tool': tool,
            'input': {'ref': node_id}, 'expect': None, 'permission': 'admin'}
    item.update(extra)
    return item


class Clock:
    """Deterministic monotonic clock advancing a fixed amount per call."""

    def __init__(self, step=1.0):
        self.value = 0.0
        self.step = step

    def __call__(self):
        self.value += self.step
        return self.value


class VcarPlanTests(unittest.TestCase):
    def assert_code(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail['error_code'], code)

    def test_plan_orders_dependencies_before_dependents(self):
        built = vcar.plan([node('c', depends_on=['b']), node('a'), node('b', depends_on=['a'])])
        self.assertEqual([item.node_id for item in built.nodes], ['a', 'b', 'c'])
        self.assertEqual(built.subgraph(['b']), {'b', 'c'})
        self.assertEqual(built.dependents['a'], ['b'])

    def test_plan_rejects_invalid_shapes(self):
        self.assert_code('PLAN_NODE_LIMIT', lambda: vcar.plan([]))
        self.assert_code('PLAN_NODE_LIMIT', lambda: vcar.plan([node(f'n{i}') for i in range(vcar.MAX_NODES + 1)]))
        self.assert_code('PLAN_NODE_INVALID', lambda: vcar.plan([{'node_id': 'a'}]))
        self.assert_code('PLAN_NODE_INVALID', lambda: vcar.plan([node('a', extra_key=1)]))
        self.assert_code('PLAN_NODE_ID_INVALID', lambda: vcar.plan([node('a'), node('a')]))
        self.assert_code('PLAN_NODE_ID_INVALID', lambda: vcar.plan([node('../etc')]))
        self.assert_code('PLAN_TOOL_INVALID', lambda: vcar.plan([node('a', tool='unknown')]))
        self.assert_code('PLAN_PERMISSION_INVALID', lambda: vcar.plan([node('a', permission='root')]))
        self.assert_code('PLAN_DEPENDENCY_INVALID', lambda: vcar.plan([node('a', depends_on=['a'])]))
        self.assert_code('PLAN_DEPENDENCY_INVALID', lambda: vcar.plan([node('a', depends_on=['missing'])]))
        self.assert_code('PLAN_DEPENDENCY_INVALID', lambda: vcar.plan([node('a', depends_on=['b', 'b'])]))
        self.assert_code('PLAN_CYCLE', lambda: vcar.plan([node('a', depends_on=['b']), node('b', depends_on=['a'])]))

    def test_budget_is_validated_and_bounded(self):
        self.assert_code('PLAN_BUDGET_INVALID', lambda: vcar.budget({'unknown': 1}))
        self.assert_code('PLAN_MAX_REPAIR_INVALID', lambda: vcar.budget({'max_repair': 3}))
        self.assert_code('PLAN_MAX_STEPS_INVALID', lambda: vcar.budget({'max_steps': 0}))
        self.assert_code('PLAN_DEADLINE_INVALID', lambda: vcar.budget({'deadline_s': 99999}))
        self.assert_code('PLAN_MAX_PARALLEL_INVALID', lambda: vcar.budget({'max_parallel': 99}))
        merged = vcar.budget({'max_repair': 1})
        self.assertEqual(merged['max_repair'], 1)
        self.assertEqual(merged['deadline_s'], vcar.DEFAULT_BUDGET['deadline_s'])


class VcarExecuteTests(unittest.TestCase):
    def test_dependent_node_consumes_prior_results(self):
        seen = {}

        def child(node_, inputs):
            seen.update(inputs)
            return {'value': inputs['a']['value'] + 1}

        result = vcar.execute(vcar.plan([node('a'), node('b', depends_on=['a'], tool='rag')]),
                              {'sql': lambda n, i: {'value': 41}, 'rag': child})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(seen, {'a': {'value': 41}})
        self.assertEqual(result['nodes'][1]['result'], {'value': 42})
        self.assertEqual([n['status'] for n in result['nodes']], ['succeeded', 'succeeded'])

    def test_independent_nodes_run_concurrently(self):
        barrier = threading.Barrier(2, timeout=10)

        def blocking(node_, inputs):
            barrier.wait()
            return {'ok': True}

        started = time.monotonic()
        result = vcar.execute(vcar.plan([node('a'), node('b')]), {'sql': blocking},
                              budget={'max_parallel': 2, 'deadline_s': 20})
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(result['status'], 'completed')

    def test_failure_skips_dependents_but_not_independent_branches(self):
        def flaky(node_, inputs):
            if node_.node_id == 'a':
                raise HTTPException(409, detail={'error_code': 'SYNTHETIC_FAILURE'})
            return {'ok': True}

        result = vcar.execute(vcar.plan([node('a'), node('b'), node('c', depends_on=['a'])]),
                              {'sql': flaky}, budget={'max_repair': 0})
        statuses = {item['node_id']: item['status'] for item in result['nodes']}
        self.assertEqual(statuses, {'a': 'failed', 'b': 'succeeded', 'c': 'skipped'})
        gaps = {gap['node_id']: gap for gap in result['gaps']}
        self.assertEqual(gaps['a']['error_code'], 'SYNTHETIC_FAILURE')
        self.assertEqual(gaps['c']['error_code'], 'PLAN_DEPENDENCY_FAILED')
        self.assertEqual(result['status'], 'partial')

    def test_repair_reruns_only_the_affected_subgraph(self):
        calls = {'a': 0, 'b': 0, 'c': 0}

        def flaky(node_, inputs):
            calls[node_.node_id] += 1
            if node_.node_id == 'a' and calls['a'] == 1:
                raise HTTPException(409, detail={'error_code': 'SYNTHETIC_TRANSIENT'})
            return {'ok': True}

        result = vcar.execute(vcar.plan([node('a'), node('b'), node('c', depends_on=['a'])]),
                              {'sql': flaky}, budget={'max_repair': 2})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['repairs'], 1)
        # The independent node ran exactly once; the failed node and its dependent re-ran.
        self.assertEqual(calls, {'a': 2, 'b': 1, 'c': 1})
        node_a = next(item for item in result['nodes'] if item['node_id'] == 'a')
        self.assertEqual(node_a['attempts'], 2)
        # The original failure is preserved in the attempt history and events.
        self.assertIn('failed', [event['event'] for event in result['events'] if event['node_id'] == 'a'])
        self.assertIn('repair_scheduled', [event['event'] for event in result['events'] if event['node_id'] == 'a'])

    def test_repair_is_bounded_by_max_repair(self):
        calls = {'count': 0}

        def always_fails(node_, inputs):
            calls['count'] += 1
            raise HTTPException(409, detail={'error_code': 'SYNTHETIC_FAILURE'})

        result = vcar.execute(vcar.plan([node('a')]), {'sql': always_fails}, budget={'max_repair': 2})
        self.assertEqual(result['repairs'], 2)
        self.assertEqual(calls['count'], 3)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['nodes'][0]['attempts'], 3)

    def test_step_budget_marks_remaining_nodes_exhausted(self):
        result = vcar.execute(vcar.plan([node('a'), node('b'), node('c')]),
                              {'sql': lambda n, i: {'ok': True}}, budget={'max_steps': 1})
        statuses = {item['node_id']: item['status'] for item in result['nodes']}
        self.assertEqual(statuses, {'a': 'succeeded', 'b': 'budget_exhausted', 'c': 'budget_exhausted'})
        self.assertTrue(all(gap['error_code'] == 'PLAN_BUDGET_EXHAUSTED' for gap in result['gaps']))
        self.assertEqual(result['budget']['used_steps'], 1)

    def test_deadline_budget_is_enforced(self):
        result = vcar.execute(vcar.plan([node('a'), node('b'), node('c')]),
                              {'sql': lambda n, i: {'ok': True}},
                              budget={'deadline_s': 2, 'max_parallel': 1}, clock=Clock(1.0))
        self.assertIn('budget_exhausted', [item['status'] for item in result['nodes']])
        self.assertTrue(any(gap['error_code'] == 'PLAN_DEADLINE_EXCEEDED' for gap in result['gaps']))

    def test_row_and_token_budgets_accumulate_across_nodes(self):
        # Budgets are enforced at dispatch boundaries, so the check uses a dependent
        # node: with concurrency, already-dispatched nodes still complete.
        rows = vcar.execute(vcar.plan([node('a'), node('b', depends_on=['a'])]),
                            {'sql': lambda n, i: {'rows': 100, 'tokens': 10}}, budget={'max_rows': 50})
        self.assertEqual([item['status'] for item in rows['nodes']], ['succeeded', 'budget_exhausted'])
        self.assertEqual(rows['budget']['used_rows'], 100)
        tokens = vcar.execute(vcar.plan([node('a'), node('b', depends_on=['a'])]),
                              {'sql': lambda n, i: {'rows': 1, 'tokens': 1000}}, budget={'max_tokens': 500})
        self.assertEqual([item['status'] for item in tokens['nodes']], ['succeeded', 'budget_exhausted'])

    def test_unresolved_conflict_stops_auto_resolution_and_returns_both_sides(self):
        evidence = [{'source': 'sql', 'value': 870}, {'source': 'pdf', 'value': 850}]

        def conflicted(node_, inputs):
            if node_.node_id == 'a':
                return {'unresolved': True, 'evidence': evidence}
            return {'ok': True}

        result = vcar.execute(vcar.plan([node('a'), node('b', depends_on=['a'])]),
                              {'sql': conflicted, 'rag': conflicted}, budget={'max_repair': 2})
        statuses = {item['node_id']: item['status'] for item in result['nodes']}
        self.assertEqual(statuses, {'a': 'unresolved', 'b': 'skipped'})
        self.assertEqual(result['repairs'], 0)
        gap = next(gap for gap in result['gaps'] if gap['node_id'] == 'a')
        self.assertEqual(gap['evidence'], evidence)
        self.assertEqual(gap['error_code'], 'PLAN_UNRESOLVED_CONFLICT')

    def test_missing_runner_and_bad_results_are_stable_failures(self):
        unavailable = vcar.execute(vcar.plan([node('a', tool='cad')]), {'sql': lambda n, i: {}},
                                   budget={'max_repair': 0})
        self.assertEqual(unavailable['nodes'][0]['error_code'], 'PLAN_TOOL_UNAVAILABLE')
        bad = vcar.execute(vcar.plan([node('a')]), {'sql': lambda n, i: 'not-a-dict'}, budget={'max_repair': 0})
        self.assertEqual(bad['nodes'][0]['error_code'], 'PLAN_NODE_INVALID_RESULT')

    def test_state_can_resume_a_previous_run(self):
        def record(node_, inputs):
            raise HTTPException(409, detail={'error_code': 'SYNTHETIC_FAILURE'})

        first = vcar.execute(vcar.plan([node('a'), node('b')]),
                             {'sql': lambda n, i: {'ok': True} if n.node_id == 'a' else record(n, i)},
                             budget={'max_repair': 0})
        self.assertEqual({item['node_id']: item['status'] for item in first['nodes']},
                         {'a': 'succeeded', 'b': 'failed'})
        state = {item['node_id']: {'status': item['status'], 'result': item.get('result'),
                                   'attempts': item['attempts'], 'error_code': item['error_code']}
                 for item in first['nodes']}
        seen = []

        def fixed(node_, inputs):
            seen.append(node_.node_id)
            return {'ok': True}

        resumed = vcar.execute(vcar.plan([node('a'), node('b')]), {'sql': fixed},
                               budget={'max_repair': 0},
                               state=vcar.bound_state(vcar.plan([node('a'), node('b')]), state))
        self.assertTrue(resumed['resumed'])
        self.assertEqual(seen, ['b'])
        self.assertEqual(resumed['status'], 'completed')
        node_a = next(item for item in resumed['nodes'] if item['node_id'] == 'a')
        self.assertEqual(node_a['attempts'], 1)

    def test_invalid_resume_state_is_refused(self):
        built = vcar.plan([node('a')])
        for records in ({'missing': {'status': 'succeeded'}},
                        {'a': {'status': 'unknown'}}, {'a': 'succeeded'},
                        {'a': {'status': 'succeeded', 'result': object()}}):
            with self.assertRaises(HTTPException) as caught:
                vcar.execute(built, {'sql': lambda n, i: {}}, state=vcar.bound_state(built, records))
            self.assertEqual(caught.exception.detail['error_code'], 'PLAN_STATE_INVALID')
        with self.assertRaises(HTTPException) as caught:
            vcar.execute(built, {'sql': lambda n, i: {}}, state=[])
            self.assertEqual(caught.exception.detail['error_code'], 'PLAN_STATE_INVALID')

    def test_invalid_budget_arguments_are_refused(self):
        built = vcar.plan([node('a')])
        with self.assertRaises(HTTPException) as caught:
            vcar.execute(built, {'sql': lambda n, i: {}}, budget='nope')
        self.assertEqual(caught.exception.detail['error_code'], 'PLAN_BUDGET_INVALID')
        with self.assertRaises(HTTPException) as caught:
            vcar.execute(built, {'sql': lambda n, i: {}}, budget={'unknown_key': 1})
        self.assertEqual(caught.exception.detail['error_code'], 'PLAN_BUDGET_INVALID')


if __name__ == '__main__':
    unittest.main()


class ResumePlanBindingTests(unittest.TestCase):
    """F07: persisted node state is bound to the plan that produced it."""

    def build(self, broken=True):
        return vcar.plan([node('a'), node('b')])

    def test_resume_state_must_carry_a_plan_digest(self):
        built = self.build()
        with self.assertRaises(HTTPException) as caught:
            vcar.execute(built, {'sql': lambda n, i: {}}, state={'a': {'status': 'succeeded'}})
        self.assertEqual(caught.exception.detail['error_code'], 'PLAN_STATE_UNBOUND')

    def test_state_from_a_different_plan_is_refused(self):
        built = vcar.plan([node('a'), node('b')])
        other = vcar.plan([node('a'), node('b', depends_on=('a',))])
        self.assertNotEqual(built.digest, other.digest)
        with self.assertRaises(HTTPException) as caught:
            vcar.execute(built, {'sql': lambda n, i: {}},
                         state=vcar.bound_state(other, {'a': {'status': 'succeeded'}}))
        self.assertEqual(caught.exception.detail['error_code'], 'PLAN_STATE_PLAN_MISMATCH')

    def test_same_plan_content_produces_the_same_digest(self):
        first = vcar.plan([node('a'), node('b', depends_on=('a',))])
        second = vcar.plan([node('a'), node('b', depends_on=('a',))])
        self.assertEqual(first.digest, second.digest)
        result = vcar.execute(second, {'sql': lambda n, i: {'ok': True}},
                              state=vcar.bound_state(first, {'a': {'status': 'succeeded'}}))
        self.assertTrue(result['resumed'])
        self.assertEqual(result['plan_digest'], first.digest)

    def test_the_result_reports_the_digest_to_persist(self):
        built = self.build()
        result = vcar.execute(built, {'sql': lambda n, i: {'ok': True}})
        self.assertEqual(result['plan_digest'], built.digest)
        self.assertEqual(result['plan']['plan_digest'], built.digest)

    def test_a_record_for_an_unknown_node_is_still_refused_when_bound(self):
        built = vcar.plan([node('a')])
        with self.assertRaises(HTTPException) as caught:
            vcar.execute(built, {'sql': lambda n, i: {}},
                         state=vcar.bound_state(built, {'ghost': {'status': 'succeeded'}}))
        self.assertEqual(caught.exception.detail['error_code'], 'PLAN_STATE_INVALID')
