"""Bounded V-CAR: Plan -> Execute -> Verify -> Repair -> Reverify.

Implements the general dependency plan documented in the design (section 6.1/6.4):

* every node carries its id, dependencies, tool, structured input reference, expected
  output, permission, budget, status and events;
* independent nodes run concurrently and dependent nodes consume prior results;
* a failure skips only its dependents while independent branches keep running;
* repair re-runs **only the affected subgraph** and the original failure is preserved;
* one shared budget bounds ``deadline/max_steps/max_rows/max_tokens/max_repair`` with at
  most two repair rounds, and any repair re-runs the dependent conclusions instead of
  trusting them;
* a genuine source conflict without an authority rule stops automatic resolution and
  returns both sides' evidence;
* a run can be resumed from persisted node state.

This module is the orchestration primitive; it does not call tools by itself.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
import re
import threading
import time

from fastapi import HTTPException

from . import task_budget

MAX_NODES = 64
NODE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
TOOLS = frozenset({'sql', 'rag', 'industrial_rules', 'fields', 'cad', 'ocr', 'source_authority'})
PERMISSIONS = frozenset({'viewer', 'reviewer', 'approver', 'admin'})
NODE_KEYS = frozenset({'node_id', 'depends_on', 'tool', 'input', 'expect', 'permission'})
BUDGET_KEYS = frozenset({'deadline_s', 'max_steps', 'max_rows', 'max_tokens', 'max_repair', 'max_parallel'})
DEFAULT_BUDGET = {'deadline_s': 120.0, 'max_steps': 64, 'max_rows': 10000,
                  'max_tokens': 200000, 'max_repair': 2, 'max_parallel': 4}
EVENT_LIMIT = 512
DEFAULT_NODE_DEADLINE_SECONDS = 120.0
DEFAULT_NODE_SLOTS = 2
_TARGET = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]{0,120}$')
STATUSES = ('pending', 'running', 'succeeded', 'failed', 'skipped', 'unresolved', 'budget_exhausted')
TERMINAL = frozenset({'succeeded', 'failed', 'skipped', 'unresolved', 'budget_exhausted'})


def fail(code, status=422):
    raise HTTPException(status, detail={'error_code': code})


class Node:
    """One immutable planned task."""

    __slots__ = ('node_id', 'depends_on', 'tool', 'input', 'expect', 'permission', 'index')

    def __init__(self, node_id, depends_on, tool, input, expect, permission, index):
        self.node_id = node_id
        self.depends_on = tuple(depends_on)
        self.tool = tool
        self.input = input
        self.expect = expect
        self.permission = permission
        self.index = index

    def public(self):
        return {'node_id': self.node_id, 'depends_on': list(self.depends_on), 'tool': self.tool,
                'input': copy.deepcopy(self.input), 'expect': self.expect, 'permission': self.permission}


class Plan:
    """A validated, topologically ordered dependency plan."""

    def __init__(self, nodes):
        self.nodes = tuple(nodes)
        self.by_id = {node.node_id: node for node in nodes}
        self.dependents = {node.node_id: [] for node in nodes}
        for node in nodes:
            for parent in node.depends_on:
                self.dependents[parent].append(node.node_id)

    def subgraph(self, roots):
        """The given nodes plus everything that transitively depends on them."""
        seen, queue = set(), list(roots)
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self.dependents[current])
        return {node.node_id for node in self.nodes if node.node_id in seen}

    def public(self):
        return {'nodes': [node.public() for node in self.nodes]}


def _bounded_int(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        fail(f'PLAN_{name.upper()}_INVALID')
    return value


def _bounded_number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
        fail(f'PLAN_{name.upper()}_INVALID')
    return float(value)


def budget(value=None):
    """Merge and validate an explicit budget against the documented shared defaults."""
    merged = dict(DEFAULT_BUDGET)
    if value is not None:
        if not isinstance(value, dict) or not set(value) <= BUDGET_KEYS:
            fail('PLAN_BUDGET_INVALID')
        merged.update(value)
    merged['deadline_s'] = _bounded_number(merged['deadline_s'], 'deadline', 0.5, 3600)
    merged['max_steps'] = _bounded_int(merged['max_steps'], 'max_steps', 1, 4096)
    merged['max_rows'] = _bounded_int(merged['max_rows'], 'max_rows', 0, 10_000_000)
    merged['max_tokens'] = _bounded_int(merged['max_tokens'], 'max_tokens', 0, 100_000_000)
    merged['max_repair'] = _bounded_int(merged['max_repair'], 'max_repair', 0, 2)
    merged['max_parallel'] = _bounded_int(merged['max_parallel'], 'max_parallel', 1, 16)
    return merged


def plan(nodes):
    """Validate nodes and return a topologically ordered `Plan`."""
    if not isinstance(nodes, (list, tuple)) or not 1 <= len(nodes) <= MAX_NODES:
        fail('PLAN_NODE_LIMIT')
    raw, seen = [], set()
    for item in nodes:
        if not isinstance(item, dict) or set(item) != NODE_KEYS:
            fail('PLAN_NODE_INVALID')
        node_id = item['node_id']
        if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id) or node_id in seen:
            fail('PLAN_NODE_ID_INVALID')
        seen.add(node_id)
        depends = item['depends_on']
        if not isinstance(depends, list) or len(depends) != len(set(depends)):
            fail('PLAN_DEPENDENCY_INVALID')
        if item['tool'] not in TOOLS:
            fail('PLAN_TOOL_INVALID')
        if item['permission'] not in PERMISSIONS:
            fail('PLAN_PERMISSION_INVALID')
        if not isinstance(item['input'], dict) or not isinstance(item['expect'], (str, type(None))):
            fail('PLAN_NODE_INVALID')
        try:
            json.dumps(item['input'], ensure_ascii=False)
        except (TypeError, ValueError):
            fail('PLAN_NODE_INVALID')
        raw.append(item)
    ids = {item['node_id'] for item in raw}
    for item in raw:
        if item['node_id'] in item['depends_on'] or any(parent not in ids for parent in item['depends_on']):
            fail('PLAN_DEPENDENCY_INVALID')
    order, remaining = [], {item['node_id']: set(item['depends_on']) for item in raw}
    while remaining:
        ready = sorted(node_id for node_id, parents in remaining.items() if not parents - set(order))
        if not ready:
            fail('PLAN_CYCLE')
        for node_id in ready:
            order.append(node_id)
            del remaining[node_id]
    built = [Node(item['node_id'], item['depends_on'], item['tool'], copy.deepcopy(item['input']),
                  item['expect'], item['permission'], order.index(item['node_id'])) for item in raw]
    # `Plan.nodes` is topologically ordered so output, reports and index are stable.
    return Plan(sorted(built, key=lambda item: item.index))


def validate_state(plan_, state):
    """Accept only a previous run's node state for the same plan; never invent results."""
    if state is None:
        return {}
    if not isinstance(state, dict):
        fail('PLAN_STATE_INVALID')
    restored = {}
    for node_id, record in state.items():
        if node_id not in plan_.by_id or not isinstance(record, dict):
            fail('PLAN_STATE_INVALID')
        if record.get('status') not in STATUSES:
            fail('PLAN_STATE_INVALID')
        if 'result' in record:
            try:
                json.dumps(record['result'], ensure_ascii=False)
            except (TypeError, ValueError):
                fail('PLAN_STATE_INVALID')
        restored[node_id] = {'status': record['status'], 'result': record.get('result'),
                             'attempts': record.get('attempts', 0), 'error_code': record.get('error_code')}
    return restored


class _Run:
    def __init__(self, plan_, budget_, clock):
        self.plan = plan_
        self.budget = budget_
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + budget_['deadline_s']
        self.status = {node.node_id: 'pending' for node in plan_.nodes}
        self.results = {}
        self.error_codes = {}
        self.attempts = {node.node_id: 0 for node in plan_.nodes}
        self.history = {node.node_id: [] for node in plan_.nodes}
        self.events = []
        self.resumed = False
        self.rows = self.tokens = self.steps = self.repairs = 0

    def event(self, node_id, kind, **extra):
        if len(self.events) < EVENT_LIMIT:
            self.events.append({'node_id': node_id, 'event': kind, 'at_ms': round((self.clock() - self.started) * 1000, 1), **extra})

    def expired(self):
        return self.clock() >= self.deadline

    def exhausted(self):
        return (self.steps >= self.budget['max_steps'] or self.expired()
                or self.rows > self.budget['max_rows'] or self.tokens > self.budget['max_tokens'])

    def settle(self, node, status, *, result=None, error_code=None):
        self.status[node.node_id] = status
        if result is not None:
            self.results[node.node_id] = result
        if error_code:
            self.error_codes[node.node_id] = error_code
        self.event(node.node_id, status, error_code=error_code)

    def run_node(self, node, runner):
        self.steps += 1
        self.attempts[node.node_id] += 1
        self.history[node.node_id].append({'attempt': self.attempts[node.node_id], 'round': self.repairs})
        self.event(node.node_id, 'started', tool=node.tool, attempt=self.attempts[node.node_id])
        inputs = {parent: self.results.get(parent) for parent in node.depends_on}
        started = self.clock()
        try:
            result = runner(node, inputs)
        except HTTPException as error:
            code = error.detail.get('error_code') if isinstance(error.detail, dict) else None
            return self._node_failure(node, started, code or 'PLAN_NODE_FAILED')
        except Exception:
            return self._node_failure(node, started, 'PLAN_NODE_FAILED')
        if not isinstance(result, dict):
            return self._node_failure(node, started, 'PLAN_NODE_INVALID_RESULT')
        self.rows += int(result.get('rows') or 0) if isinstance(result.get('rows'), int) else 0
        self.tokens += int(result.get('tokens') or 0) if isinstance(result.get('tokens'), int) else 0
        if result.get('unresolved') is True:
            # A real conflict without an authority rule stops automatic resolution.
            self.settle(node, 'unresolved', result=result, error_code='PLAN_UNRESOLVED_CONFLICT')
            return True
        self.settle(node, 'succeeded', result=result)
        return True

    def _node_failure(self, node, started, code):
        self.settle(node, 'failed', error_code=code)
        self.event(node.node_id, 'failed_detail', error_code=code,
                   elapsed_ms=round((self.clock() - started) * 1000, 1))
        return False

    def skip_dependents(self, roots, code):
        for node_id in sorted(self.plan.subgraph(roots)):
            if self.status[node_id] == 'pending':
                self.settle(self.plan.by_id[node_id], 'skipped', error_code=code)

    def mark_budget_exhausted(self, code):
        for node in self.plan.nodes:
            if self.status[node.node_id] == 'pending':
                self.settle(node, 'budget_exhausted', error_code=code)


def execute(plan_, runners, *, budget=None, state=None, clock=time.monotonic,
            context=None, cancel_event=None):
    """Run a validated plan under one shared budget.

    `runners` maps a tool name to `callable(node, inputs) -> dict`. Node failures are
    returned in the result instead of raised. `state` resumes a previous run. Passing a
    shared `ExecutionContext` (or a `cancel_event`) lets bounded runners see the remaining
    budget and lets a cancellation stop the run and its in-flight process groups.
    """
    if not isinstance(plan_, Plan):
        fail('PLAN_INVALID')
    if budget is not None and not isinstance(budget, dict):
        fail('PLAN_BUDGET_INVALID')
    limits = _merged_budget(budget or {})
    attached = {getattr(runner, 'vcar_context', None) for runner in runners.values()} if isinstance(runners, dict) else set()
    attached.discard(None)
    if context is None and len(attached) == 1:
        context = next(iter(attached))
    if context is None:
        context = ExecutionContext()
    if not isinstance(context, ExecutionContext):
        fail('PLAN_CONTEXT_INVALID')
    if cancel_event is not None:
        context.cancel_event = cancel_event
    restored = validate_state(plan_, state)
    run = _Run(plan_, limits, clock)
    run.resumed = bool(restored)
    for node_id, record in restored.items():
        status = record['status']
        run.attempts[node_id] = record['attempts']
        if record.get('error_code'):
            run.error_codes[node_id] = record['error_code']
        if status == 'succeeded':
            # Completed work is reused and never re-executed.
            run.status[node_id] = 'succeeded'
            if record.get('result') is not None:
                run.results[node_id] = record['result']
        elif status == 'unresolved':
            # A genuine unresolved conflict is not auto-resolved by resuming.
            run.status[node_id] = 'unresolved'
            if record.get('result') is not None:
                run.results[node_id] = record['result']
        else:
            # pending/failed/skipped/budget_exhausted resume as pending work.
            run.status[node_id] = 'pending'
    run.event('*', 'resumed', restored=len(restored))
    for item in attached | {context}:
        item._bind(limits, run.started)

    with ThreadPoolExecutor(max_workers=limits['max_parallel']) as pool:
        while True:
            if context.cancelled:
                # Cancellation stops the run; in-flight groups are killed by the runners.
                for node in plan_.nodes:
                    if run.status[node.node_id] == 'pending':
                        run.settle(node, 'skipped', error_code='PLAN_CANCELLED')
                break
            if run.exhausted():
                run.mark_budget_exhausted('PLAN_DEADLINE_EXCEEDED' if run.expired() else 'PLAN_BUDGET_EXHAUSTED')
                break
            ready = [node for node in plan_.nodes if run.status[node.node_id] == 'pending'
                     and all(run.status[parent] == 'succeeded' for parent in node.depends_on)]
            if not ready:
                break
            # A batch may never exceed the remaining step budget, so a small max_steps is
            # respected even when several independent nodes are ready at once.
            room = limits['max_steps'] - run.steps
            if room <= 0:
                run.mark_budget_exhausted('PLAN_BUDGET_EXHAUSTED')
                break
            ready = ready[:min(limits['max_parallel'], room)]
            outcomes = list(pool.map(lambda node: _dispatch(runners, node, run), ready))
            failed = [node.node_id for node, ok in zip(ready, outcomes) if not ok]
            unresolved = [node.node_id for node in ready if run.status[node.node_id] == 'unresolved']
            if unresolved:
                run.skip_dependents(unresolved, 'PLAN_DEPENDENCY_UNRESOLVED')
            if failed:
                run.skip_dependents(failed, 'PLAN_DEPENDENCY_FAILED')
                repairable = [node_id for node_id in failed if run.status[node_id] == 'failed']
                if repairable and run.repairs < limits['max_repair']:
                    run.repairs += 1
                    # Repair only the affected subgraph; dependents are re-run so their
                    # conclusions are re-verified rather than trusted.
                    for node_id in sorted(plan_.subgraph(repairable)):
                        if run.status[node_id] in {'failed', 'skipped'}:
                            run.status[node_id] = 'pending'
                            run.error_codes.pop(node_id, None)
                            run.event(node_id, 'repair_scheduled', round=run.repairs)
    return _report(plan_, run, limits)


def _merged_budget(limits):
    merged = dict(DEFAULT_BUDGET)
    if not set(limits) <= BUDGET_KEYS:
        fail('PLAN_BUDGET_INVALID')
    merged.update(limits)
    return budget(merged)


def _dispatch(runners, node, run):
    runner = runners.get(node.tool) if isinstance(runners, dict) else None
    if runner is None:
        run.steps += 1
        run.settle(node, 'failed', error_code='PLAN_TOOL_UNAVAILABLE')
        return False
    return run.run_node(node, runner)


def _report(plan_, run, limits):
    nodes = []
    gaps = []
    for node in sorted(plan_.nodes, key=lambda item: item.index):
        status = run.status[node.node_id]
        record = {'node_id': node.node_id, 'tool': node.tool, 'status': status,
                  'attempts': run.attempts[node.node_id], 'depends_on': list(node.depends_on),
                  'expect': node.expect, 'history': run.history[node.node_id],
                  'error_code': run.error_codes.get(node.node_id)}
        if node.node_id in run.results:
            record['result'] = run.results[node.node_id]
        nodes.append(record)
        if status in {'failed', 'skipped', 'unresolved', 'budget_exhausted'}:
            gap = {'node_id': node.node_id, 'status': status, 'error_code': run.error_codes.get(node.node_id)}
            if status == 'unresolved':
                gap['evidence'] = run.results.get(node.node_id, {}).get('evidence', [])
            gaps.append(gap)
    succeeded = any(node['status'] == 'succeeded' for node in nodes)
    status = 'completed' if not gaps else ('partial' if succeeded else 'failed')
    return {'status': status, 'plan': plan_.public(), 'nodes': nodes, 'gaps': gaps,
            'repairs': run.repairs, 'resumed': run.resumed,
            'budget': {'deadline_s': limits['deadline_s'], 'max_steps': limits['max_steps'],
                       'max_rows': limits['max_rows'], 'max_tokens': limits['max_tokens'],
                       'max_repair': limits['max_repair'],
                       'used_steps': run.steps, 'used_rows': run.rows, 'used_tokens': run.tokens},
            'events': run.events,
            'elapsed_ms': round((run.clock() - run.started) * 1000, 1)}


class ExecutionContext:
    """Shared, thread-safe view of one run's remaining budget and cancellation state.

    A caller creates it (optionally with a `cancel_event`), hands it to both
    `bounded_runners` and `execute`, and can then cancel an in-flight run.
    """

    def __init__(self, cancel_event=None):
        self._lock = threading.Lock()
        self._deadline = None
        self._budget = None
        self.cancel_event = cancel_event

    def _bind(self, limits, started, clock=time.monotonic):
        with self._lock:
            self._budget = dict(limits)
            self._deadline = started + limits['deadline_s']

    def remaining(self):
        """Seconds left in the shared wall-clock budget, or None before a run starts."""
        with self._lock:
            deadline = self._deadline
        return None if deadline is None else max(0.0, deadline - time.monotonic())

    @property
    def cancelled(self):
        return bool(self.cancel_event is not None and self.cancel_event.is_set())

    @property
    def budget(self):
        with self._lock:
            return copy.deepcopy(self._budget)


def bounded_runners(targets, *, dsn=None, slots=None, context=None, grace_s=None,
                    deadline_s=None, output_limit=None, env=None):
    """Build runners that execute each node in its own process group under `task_budget`.

    `targets` maps an allowed tool to `(module, function)` and is resolved server-side, so
    a plan (or an HTTP caller) can never choose the executable. The child receives exactly
    one JSON payload with the node id, tool, structured input and dependency results, and
    must return a JSON object. A node never outlives the remaining shared budget, and a
    cancellation terminates its process group.
    """
    if not isinstance(targets, dict):
        raise ValueError('VCAR_TARGETS_INVALID')
    context = context if context is not None else ExecutionContext()
    runners = {}
    for tool, target in targets.items():
        if tool not in TOOLS:
            raise ValueError('VCAR_TARGET_INVALID')
        if (not isinstance(target, (list, tuple)) or len(target) != 2
                or not all(isinstance(part, str) and _TARGET.fullmatch(part) for part in target)):
            raise ValueError('VCAR_TARGET_INVALID')
        runners[tool] = _bounded_runner(tool, target[0], target[1], dsn=dsn, slots=slots,
                                        context=context, grace_s=grace_s, deadline_s=deadline_s,
                                        output_limit=output_limit, env=env)
    return runners


def _bounded_runner(tool, module, function, *, dsn, slots, context, grace_s, deadline_s,
                    output_limit, env):
    slot_name = 'vcar-' + tool

    def runner(node, inputs):
        if context.cancelled:
            fail('PLAN_CANCELLED')
        remaining = context.remaining()
        if remaining is not None:
            if remaining <= 0:
                fail('PLAN_DEADLINE_EXCEEDED')
            limit = min(deadline_s, remaining) if deadline_s else remaining
        else:
            limit = deadline_s or DEFAULT_NODE_DEADLINE_SECONDS
        if limit < task_budget.MIN_DEADLINE_SECONDS:
            fail('PLAN_DEADLINE_EXCEEDED')
        payload = {'node_id': node.node_id, 'tool': tool, 'input': copy.deepcopy(node.input),
                   'expect': node.expect, 'inputs': copy.deepcopy(inputs)}

        def once():
            return task_budget.run_bounded(module, function, [payload], deadline_s=limit,
                                           output_limit=output_limit, grace_s=grace_s, env=env,
                                           cancel_event=context.cancel_event,
                                           fallback_error_code='PLAN_NODE_FAILED')

        if dsn:
            with task_budget.resource_slot(dsn, slot_name,
                                           slots or task_budget.configured_slots(DEFAULT_NODE_SLOTS)):
                outcome = once()
        else:
            outcome = once()
        if outcome['status'] == 'timeout':
            fail('PLAN_DEADLINE_EXCEEDED')
        if outcome['status'] == 'cancelled':
            fail('PLAN_CANCELLED')
        if outcome['status'] != 'ok':
            fail(outcome.get('error_code') or 'PLAN_NODE_FAILED')
        return outcome.get('value')

    # Lets `execute` bind the run's budget to the context these runners actually use,
    # instead of silently falling back to a per-node default deadline.
    runner.vcar_context = context
    return runner


class PlanRequest:
    """Validated HTTP payload: a node list plus an optional shared budget.

    The payload never selects an executable; tools are resolved from the server-side
    registry, so a caller can only choose among already allowed tools.
    """

    KEYS = frozenset({'nodes', 'budget'})

    @staticmethod
    def parse(value):
        if not isinstance(value, dict) or not set(value) <= PlanRequest.KEYS or 'nodes' not in value:
            fail('PLAN_REQUEST_INVALID')
        budget_value = value.get('budget')
        if budget_value is not None:
            if not isinstance(budget_value, dict) or set(budget_value) - BUDGET_KEYS:
                fail('PLAN_REQUEST_INVALID')
        return {'nodes': value['nodes'], 'budget': budget_value}


def targets_from_env(value=None):
    """Resolve the allowlisted tool -> (module, function) registry; fail closed."""
    raw = value if value is not None else os.environ.get('DATAMIND_VCAR_TARGETS')
    if not raw or not str(raw).strip():
        fail('VCAR_TARGETS_NOT_CONFIGURED', 503)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        fail('VCAR_TARGETS_INVALID', 503)
    if not isinstance(data, dict) or not data:
        fail('VCAR_TARGETS_INVALID', 503)
    return data


def run_request(payload, dsn=None, *, context=None, cancel_event=None, child_env=None,
                targets=None, clock=time.monotonic):
    """Validate an HTTP plan payload, resolve server-side targets and execute it."""
    request = PlanRequest.parse(payload)
    built = plan(request['nodes'])
    registry = targets_from_env(targets)
    context = context if context is not None else ExecutionContext()
    if cancel_event is not None:
        context.cancel_event = cancel_event
    try:
        runners = bounded_runners(registry, dsn=dsn, context=context, env=child_env)
    except ValueError as error:
        fail(str(error), 503)
    return execute(built, runners, budget=request['budget'], context=context, clock=clock)
