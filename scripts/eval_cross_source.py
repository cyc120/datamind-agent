#!/usr/bin/env python
"""Evaluate the cross-source suite (``data/evaluation/cross_source_v1.json``).

``--offline`` runs :func:`datamind.cross_source.plan` on the frozen per-case fixtures. It never
calls the upstream model: every provider entry point is replaced by a counting stub and the run
fails loudly if anything tries to reach it. ``--live`` runs :func:`datamind.cross_source.run`
with the real SQL/retrieval adapters (and therefore the real model for NL->SQL).

Both modes write a report with the numerator/denominator per structure, the failing samples and
the exact command. Numbers are never massaged: refusals, upstream errors and timeouts stay in the
denominator.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind import binding as binding_mod  # noqa: E402
from datamind import cross_source  # noqa: E402

SUITE_PATH = ROOT / 'data/evaluation/cross_source_v1.json'
OFFLINE_REPORT = ROOT / 'reports/cross_source_offline.json'
LIVE_REPORT = ROOT / 'reports/cross_source_live.json'

MODEL_CALLS = {'count': 0}


def _forbid_model():
    def stub(*args, **kwargs):
        MODEL_CALLS['count'] += 1
        raise RuntimeError('MODEL_CALL_FORBIDDEN_IN_OFFLINE_MODE')
    return stub


def _install_model_guard():
    import datamind.providers as providers
    providers.chat = _forbid_model()
    for module_name in ('datamind.sql_engine', 'datamind.rag_engine'):
        module = sys.modules.get(module_name)
        if module is not None:
            module.provider_chat = providers.chat


def _resolve(path, result):
    """Resolve ``a.b[0].c`` against the result dict; returns ``(found, value)``."""
    current = result
    for part in str(path).split('.'):
        name = part
        index = None
        if '[' in part:
            name, _, rest = part.partition('[')
            index = int(rest.rstrip(']'))
        if name:
            if not isinstance(current, dict) or name not in current:
                return (False, None)
            current = current[name]
        if index is not None:
            if not isinstance(current, (list, tuple)) or index >= len(current):
                return (False, None)
            current = current[index]
    return (True, current)


def _node_ids(result):
    return {node['node_id'] for node in result.get('evidence_graph', {}).get('nodes', [])}


def check_one(check, result):
    """Return ``(passed, detail)`` for one frozen check."""
    op = check['op']
    path = check.get('path')
    wanted = check.get('value')
    found, value = _resolve(path, result)
    if op == 'eq':
        return (found and value == wanted, f'{path}={value!r} wanted {wanted!r}')
    if op == 'ne':
        return (found and value != wanted, f'{path}={value!r} wanted != {wanted!r}')
    if op == 'contains':
        return (found and wanted in (value or ''),
                f'{path}={str(value)[:80]!r} wanted to contain {wanted!r}')
    if op == 'nonempty':
        return (found and bool(value), f'{path} empty')
    if op == 'len_eq':
        return (found and len(value) == wanted, f'len({path})={len(value) if found else None} wanted {wanted}')
    if op == 'len_gte':
        return (found and len(value) >= wanted, f'len({path})={len(value) if found else None} wanted >= {wanted}')
    if op == 'min_len':
        return (found and len(str(value)) >= wanted, f'{path} too short')
    if op == 'graph_valid':
        return (found and not value, f'graph_problems={value}')
    if op == 'graph_kinds_include':
        kinds = set((value or {}).get('node_types') or [])
        missing = sorted(set(wanted) - kinds)
        return (found and not missing, f'node_types={sorted(kinds)} missing {missing}')
    if op == 'graph_edges_include':
        kinds = set((value or {}).get('edge_types') or [])
        missing = sorted(set(wanted) - kinds)
        return (found and not missing, f'edge_types={sorted(kinds)} missing {missing}')
    if op == 'edge_kinds_min':
        kinds = {edge['edge_type'] for edge in (value or {}).get('edges', [])}
        return (found and len(kinds) >= wanted, f'{len(kinds)} distinct edge kinds wanted >= {wanted}')
    if op == 'all_bindings_complete':
        claims = value or []
        bad = [claim['claim_id'] for claim in claims
               if claim.get('binding_status') != 'bound' or claim.get('binding_problems')]
        return (found and claims and not bad, f'incomplete bindings: {bad}')
    if op == 'all_claims_have_evidence':
        ids = _node_ids(result)
        bad = []
        for claim in value or []:
            evidence = claim.get('evidence_node_ids') or []
            if not evidence or any(item not in ids for item in evidence):
                bad.append(claim.get('claim_id'))
        return (found and value and not bad, f'claims without resolving evidence: {bad}')
    if op == 'claim_fields_nonempty':
        bad = []
        for claim in value or []:
            missing = [field for field in wanted
                       if claim.get(field) in (None, '', [], {})]
            if missing:
                bad.append({'claim_id': claim.get('claim_id'), 'missing': missing})
        return (found and value and not bad, f'claims with empty fields: {bad}')
    if op == 'binding_fields_present':
        bad = []
        for claim in value or []:
            record = claim.get('binding') or {}
            missing = [field for field in wanted if record.get(field) is None]
            if missing:
                bad.append({'claim_id': claim.get('claim_id'), 'missing': missing})
        return (found and value and not bad, f'bindings missing fields: {bad}')
    if op == 'threshold_violations_eq':
        count = len([item for item in (value or []) if item.get('violation') is True])
        return (found and count == wanted, f'violations={count} wanted {wanted}')
    if op == 'conflicts_retain_both':
        bad = []
        for record in value or []:
            left, right = record.get('left') or {}, record.get('right') or {}
            if left.get('value') is None or right.get('value') is None or not left.get('node_id') \
                    or not right.get('node_id'):
                bad.append(record.get('conflict_id'))
        return (found and value and not bad, f'conflicts losing a side: {bad}')
    if op == 'conflicts_resolved_by_authority':
        bad = [record.get('conflict_id') for record in value or []
               if record.get('resolution_status') != 'resolved_by_registered_authority'
               or not record.get('selected_node_id')]
        return (found and value and not bad, f'not resolved by authority: {bad}')
    raise SystemExit('UNKNOWN_CHECK_OP:' + str(op))


def evaluate_case(case, result):
    failures = []
    for check in case.get('expected_answer_checks', []):
        ok, detail = check_one(check, result)
        if not ok:
            failures.append({'check': check, 'observed': detail})
    expected_mode = case.get('expected_failure_mode')
    observed = result.get('error_code')
    if expected_mode != observed:
        failures.append({'check': {'op': 'expected_failure_mode', 'value': expected_mode},
                         'observed': f'error_code={observed!r}'})
    return failures


def load_suite(path=SUITE_PATH):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def build_evidence(fixture):
    return {'sql': fixture.get('sql'), 'docs': {'blocks': fixture.get('docs', [])},
            'aliases': fixture.get('aliases'), 'authority_rules': fixture.get('authority_rules')}


def _mutate(fixture, variant):
    """Perturb one binding field of a frozen fixture; the pipeline must notice."""
    mutated = json.loads(json.dumps(fixture, ensure_ascii=False))
    if variant == 'sql_unit_removed':
        for row in (mutated.get('sql') or {}).get('rows') or []:
            row.pop('unit', None)
    elif variant == 'doc_sha_removed':
        for block in mutated.get('docs') or []:
            block.pop('source_sha256', None)
    elif variant == 'doc_version_retargeted':
        for block in mutated.get('docs') or []:
            if block.get('document_version'):
                block['document_version'] = 'WRONG-VERSION-9999'
            block.pop('applies_to_version', None)
            if isinstance(block.get('clause'), dict):
                block['clause'].pop('valid_from', None)
                block['clause'].pop('valid_to', None)
            block.pop('valid_from', None)
            block.pop('valid_to', None)
    elif variant == 'sql_rows_emptied':
        if isinstance(mutated.get('sql'), dict):
            mutated['sql']['rows'] = []
    else:
        raise SystemExit('UNKNOWN_MUTATION:' + str(variant))
    return mutated


MUTATIONS = ('sql_unit_removed', 'doc_sha_removed', 'doc_version_retargeted', 'sql_rows_emptied')


def mutation_probe(suite, baseline_cases):
    probe = OrderedDict()
    baseline = {case['case_id']: (case['status'], case['error_code']) for case in baseline_cases}
    for variant in MUTATIONS:
        changed = completed_to_refused = 0
        for case in suite['cases']:
            fixture = _mutate(suite['fixtures'][case['fixtures'][0]], variant)
            evidence = build_evidence(fixture)
            sql = evidence['sql']
            sql = dict(sql) if isinstance(sql, dict) else sql
            result = cross_source.plan(case['question'],
                                       [sql.get('source_ref') if isinstance(sql, dict) else None],
                                       ['frozen_fixture'], sql_evidence=sql,
                                       rag_evidence=evidence['docs'],
                                       aliases=evidence['aliases'],
                                       authority_rules=evidence['authority_rules'])
            observed = (result.get('status'), result.get('error_code'))
            if observed != baseline.get(case['case_id']):
                changed += 1
                if baseline.get(case['case_id']) == ('completed', None) and \
                        observed[0] != 'completed':
                    completed_to_refused += 1
        probe[variant] = {'cases': len(suite['cases']), 'verdict_changed': changed,
                          'completed_to_refused': completed_to_refused,
                          'unchanged': len(suite['cases']) - changed}
    return probe


def run_offline(suite):
    _install_model_guard()
    cases = []
    for case in suite['cases']:
        fixture = suite['fixtures'][case['fixtures'][0]]
        evidence = build_evidence(fixture)
        try:
            result = cross_source.plan(case['question'], [evidence['sql'].get('source_ref')],
                                       [fixture.get('collection', 'frozen_fixture')],
                                       sql_evidence=evidence['sql'],
                                       rag_evidence=evidence['docs'],
                                       aliases=evidence['aliases'],
                                       authority_rules=evidence['authority_rules'])
        except Exception as exc:  # a crash is a failed case, never a hidden pass
            result = {'status': 'failed', 'error_code': f'EVAL_EXCEPTION:{type(exc).__name__}',
                      'claims': [], 'conflicts': [], 'evidence_graph': {'nodes': [], 'edges': []},
                      'graph_summary': {}, 'threshold_checks': [], 'failure_detail': str(exc)}
        failures = evaluate_case(case, result)
        cases.append({'case_id': case['case_id'], 'structure': case['structure'],
                      'question': case['question'], 'status': result.get('status'),
                      'error_code': result.get('error_code'),
                      'failure_detail': result.get('failure_detail'),
                      'passed': not failures, 'failures': failures,
                      'claims': len(result.get('claims') or []),
                      'conflicts': len(result.get('conflicts') or []),
                      'threshold_checks': len(result.get('threshold_checks') or []),
                      'repair_attempts': result.get('repair_attempts', 0),
                      'graph_nodes': result.get('graph_summary', {}).get('node_count', 0),
                      'graph_edges': result.get('graph_summary', {}).get('edge_count', 0),
                      'graph_digest': result.get('graph_summary', {}).get('graph_digest')})
    return cases


def _install_model_counter():
    """Count real upstream calls for ``--live`` so the report never has to guess."""
    import datamind.providers as providers
    original = providers.chat

    def counting(*args, **kwargs):
        MODEL_CALLS['count'] += 1
        return original(*args, **kwargs)

    providers.chat = counting
    for module_name in ('datamind.sql_engine', 'datamind.rag_engine'):
        module = sys.modules.get(module_name)
        if module is not None:
            module.provider_chat = counting


def _edge_type_counts(result):
    counts = Counter(edge.get('edge_type') for edge
                     in (result.get('evidence_graph') or {}).get('edges') or [])
    return dict(sorted(counts.items()))


def _linked_document_titles(result):
    """Titles/texts of the document nodes that are actually linked into the verified graph."""
    linkage = result.get('doc_linkage') or {}
    linked = set(linkage.get('linked_node_ids') or [])
    titles = []
    for node in (result.get('evidence_graph') or {}).get('nodes') or []:
        if node.get('node_id') in linked:
            payload = node.get('payload') or {}
            titles.append({'node_id': node.get('node_id'), 'node_type': node.get('node_type'),
                           'title': payload.get('title'), 'source_ref': payload.get('source_ref'),
                           'text_head': str(payload.get('text') or '')[:60]})
    return titles


def _binding_contract(result):
    """Hit iff every claim carries the complete seven-field binding."""
    claims = result.get('claims') or []
    bad = [{'claim_id': claim.get('claim_id'), 'problems': claim.get('binding_problems')}
           for claim in claims
           if claim.get('binding_status') != 'bound' or claim.get('binding_problems')]
    missing_fields = []
    for claim in claims:
        record = claim.get('binding') or {}
        missing = [field for field in binding_mod.BINDING_FIELDS if record.get(field) is None]
        if missing:
            missing_fields.append({'claim_id': claim.get('claim_id'), 'missing': missing})
    return {'hit': bool(claims) and not bad and not missing_fields,
            'claims': len(claims), 'incomplete_claims': bad, 'claims_missing_fields': missing_fields}


def _answer_evidence(result):
    """Does the delivered answer actually carry the value, the unit and the linked document?"""
    answer = str(result.get('answer') or '')
    claims = result.get('claims') or []
    titles = [item for item in _linked_document_titles(result)
              if item.get('node_type') == 'doc_block' and item.get('title')]
    carries_value = bool(claims) and all(
        str(claim.get('value')) in answer or
        (claim.get('normalized_value') and str(claim['normalized_value']) in answer)
        for claim in claims[:1])
    carries_unit = bool(claims) and bool(claims[0].get('unit')) and str(claims[0]['unit']) in answer
    carries_doc = bool(titles) and any(str(item['title']) in answer for item in titles)
    return {'carries_value': carries_value, 'carries_unit': carries_unit,
            'carries_linked_document': carries_doc,
            'carries_evidence_and_binding': bool(carries_value and carries_unit and carries_doc)}


def classify_failure(result):
    """One deterministic label per failed/refused live case — never a generic bucket."""
    code = result.get('error_code')
    detail = str(result.get('failure_detail') or '')
    projection = (result.get('sql_evidence') or {}).get('projection') or {}
    upstream = projection.get('status')
    if result.get('status') == 'completed':
        return 'completed'
    if code and str(code).startswith('EVAL_EXCEPTION'):
        return 'eval_exception'
    if isinstance(upstream, str) and upstream != 'completed':
        if str(projection.get('error_code') or '').startswith('MODEL_'):
            return 'upstream_model_error'
        return 'upstream_' + str(projection.get('error_code') or upstream)
    if detail == 'SQL_EVIDENCE_EMPTY':
        return 'sql_evidence_empty'
    if detail == 'DOC_EVIDENCE_EMPTY':
        return 'doc_evidence_empty'
    if detail == 'NO_BOUNDABLE_SQL_CLAIM':
        return 'no_boundable_sql_claim'
    if detail.startswith('SQL_CLAIM_BINDING_INCOMPLETE'):
        return 'sql_binding_incomplete'
    if detail.startswith('DOC_CLAIM_BINDING_INCOMPLETE'):
        return 'doc_claim_binding_incomplete'
    if detail.startswith('DOC_NODE_BINDING_INCOMPLETE'):
        return 'doc_node_binding_incomplete'
    if detail.startswith('ENTITY_UNVERIFIED'):
        return 'entity_unverified'
    if detail.startswith('VERSION_MISMATCH'):
        return 'version_mismatch'
    if detail.startswith('VERSION_NOT_APPLICABLE'):
        return 'doc_version_not_applicable'
    if detail == 'NO_DOC_EVIDENCE_LINKED':
        return 'no_doc_evidence_linked'
    if code == 'CONFLICT_UNRESOLVED':
        return 'conflict_unresolved'
    if code == 'SOURCE_NOT_ALLOWED':
        return 'source_not_allowed'
    return 'other:' + str(code) + ':' + detail


def live_failures(case, result):
    """The live contract of one case: a real answer, or a fail-closed refusal *declared* as such."""
    expected = case.get('live_expected') or {}
    contract = expected.get('contract', 'answer')
    failures = []
    observed_status = result.get('status')
    if contract == 'answer':
        if observed_status != 'completed':
            failures.append({'check': 'live_status', 'observed': f'{observed_status}/{result.get("error_code")}',
                             'wanted': 'completed'})
        binding = _binding_contract(result)
        if not binding['hit']:
            failures.append({'check': 'live_binding_contract', 'observed': binding})
        linkage = result.get('doc_linkage') or {}
        if not linkage.get('linked'):
            failures.append({'check': 'live_doc_linked', 'observed': linkage})
        evidence = _answer_evidence(result)
        if not evidence['carries_evidence_and_binding']:
            failures.append({'check': 'live_answer_evidence', 'observed': evidence})
        if not _edge_type_counts(result):
            failures.append({'check': 'live_typed_edges', 'observed': {}})
    else:
        # The case is declared *not answerable* on the current data. It only passes when the
        # engine really failed closed with a stable code — a refusal is never counted as an answer.
        if observed_status == 'completed':
            failures.append({'check': 'live_expected_fail_closed',
                             'observed': 'completed (a refusal was required)'})
        if not result.get('error_code'):
            failures.append({'check': 'live_refusal_code', 'observed': result.get('error_code')})
    return failures


def run_live(suite, max_attempts=3):
    cases = []
    for case in suite['cases']:
        fixture = suite['fixtures'][case['fixtures'][0]]
        source_ref = (fixture.get('sql') or {}).get('source_ref')
        question = (case.get('live_expected') or {}).get('question') or case['question']
        attempts = []
        result = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = cross_source.run(question, database_sources=[source_ref] if source_ref else None)
            except Exception as exc:
                result = {'status': 'failed', 'error_code': f'EVAL_EXCEPTION:{type(exc).__name__}',
                          'claims': [], 'conflicts': [], 'evidence_graph': {'nodes': [], 'edges': []},
                          'graph_summary': {}, 'threshold_checks': [], 'failure_detail': str(exc),
                          'warnings': []}
            code = str(result.get('error_code') or '')
            projection_code = str(((result.get('sql_evidence') or {}).get('projection') or {}
                                   ).get('error_code') or '')
            attempts.append({'attempt': attempt, 'status': result.get('status'), 'error_code': code,
                             'upstream_error_code': projection_code or None,
                             'failure_detail': result.get('failure_detail')})
            # Only an upstream MODEL_* failure is retried (bounded, and every attempt is recorded).
            # The model error is nested in the SQL projection when the adapter turned it into
            # "no SQL evidence", so both places are inspected.
            retryable = any(value.startswith('MODEL_') or value == 'MODEL_UNAVAILABLE'
                            for value in (code, projection_code))
            if not retryable or attempt == max_attempts:
                break
        binding = _binding_contract(result)
        failures = live_failures(case, result)
        cases.append({
            'case_id': case['case_id'], 'structure': case['structure'], 'question': question,
            'status': result.get('status'), 'error_code': result.get('error_code'),
            'failure_detail': result.get('failure_detail'), 'warnings': result.get('warnings'),
            'attempts': attempts, 'model_calls': MODEL_CALLS['count'],
            'projection': (result.get('sql_evidence') or {}).get('projection'),
            'sql_present': (result.get('sql_evidence') or {}).get('present'),
            'sql_row_count': (result.get('sql_evidence') or {}).get('row_count'),
            'doc_block_count': (result.get('document_evidence') or {}).get('block_count'),
            'doc_linkage': result.get('doc_linkage'),
            'binding_contract_hit': binding['hit'], 'binding_detail': binding,
            'graph_nodes': result.get('graph_summary', {}).get('node_count', 0),
            'graph_edges': result.get('graph_summary', {}).get('edge_count', 0),
            'edge_type_counts': _edge_type_counts(result),
            'conflicts': len(result.get('conflicts') or []),
            'unresolved_conflicts': len(result.get('unresolved_conflicts') or []),
            'answer_evidence': _answer_evidence(result),
            'answer': result.get('answer'),
            'failure_reason_class': classify_failure(result),
            'live_expected': case.get('live_expected') or {},
            'passed': not failures, 'failures': failures})
    return cases


def live_summary(cases):
    """Two honest numbers: really-answered cases and refusals that were designed refusals."""
    answerable = [case for case in cases if (case.get('live_expected') or {}).get('contract') == 'answer']
    declared_refusal = [case for case in cases
                        if (case.get('live_expected') or {}).get('contract') == 'fail_closed']
    answered = [case for case in answerable if case['passed']]
    return {
        'answerable_cases': len(answerable),
        'answered_and_verified': len(answered),
        'answered_rate': f'{len(answered)}/{len(answerable)}',
        'declared_refusal_cases': len(declared_refusal),
        'declared_refusal_fail_closed_ok': sum(1 for case in declared_refusal if case['passed']),
        'declared_refusal_rate': f"{sum(1 for case in declared_refusal if case['passed'])}/{len(declared_refusal)}",
        'not_counted_as_answers': ('fail-closed refusals are never counted in answered_and_verified'),
    }


def summarise(suite, cases, mode, command):
    by_structure = OrderedDict()
    for name in suite['structures']:
        subset = [case for case in cases if case['structure'] == name]
        entry = {'cases': len(subset), 'passed': sum(1 for c in subset if c['passed']),
                 'failed': sum(1 for c in subset if not c['passed']),
                 'pass_rate': f"{sum(1 for c in subset if c['passed'])}/{len(subset)}"}
        if mode == 'live':
            answerable = [c for c in subset if (c.get('live_expected') or {}).get('contract') == 'answer']
            entry['answerable_cases'] = len(answerable)
            entry['answered_and_verified'] = sum(1 for c in answerable if c['passed'])
            entry['answered_rate'] = f"{sum(1 for c in answerable if c['passed'])}/{len(answerable)}"
        by_structure[name] = entry
    failures = [case for case in cases if not case['passed']]
    modes = Counter(case['error_code'] or 'NONE' for case in cases)
    if mode == 'live':
        checks_total = sum(5 if (case.get('live_expected') or {}).get('contract') == 'answer' else 2
                           for case in cases)
    else:
        checks_total = sum(len(case_cfg['expected_answer_checks']) + 1
                           for case_cfg in suite['cases'] for case in cases
                           if case['case_id'] == case_cfg['case_id'])
    checks_failed = sum(len(case['failures']) for case in cases)
    passed = sum(1 for case in cases if case['passed'])
    report = {
        'suite': suite['suite'], 'suite_version': suite['suite_version'], 'mode': mode,
        'ran_at': datetime.now(timezone.utc).isoformat(),
        'commit': subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=str(ROOT),
                                 capture_output=True, text=True).stdout.strip() or None,
        'command': command,
        'studio': ('offline fixtures; no upstream model, no database write' if mode == 'offline' else
                   'live adapters: real upstream model for NL->SQL, real retrieval, read-only queries'),
        'model_calls': MODEL_CALLS['count'],
        'totals': {'cases': len(cases), 'passed': passed, 'failed': len(cases) - passed,
                   'pass_rate': f'{passed}/{len(cases)}'},
        'checks': {'total': checks_total, 'failed': checks_failed,
                   'passed': checks_total - checks_failed},
        'by_structure': by_structure,
        'error_code_counts': dict(sorted(modes.items())),
        'failure_samples': [{'case_id': case['case_id'], 'structure': case['structure'],
                             'question': case['question'], 'status': case['status'],
                             'error_code': case['error_code'],
                             'failure_detail': case.get('failure_detail'),
                             'failure_reason_class': case.get('failure_reason_class'),
                             'live_expected': case.get('live_expected'),
                             'failures': case['failures']} for case in failures],
        'cases': cases,
    }
    if mode == 'offline':
        report['binding_sensitivity_probe'] = mutation_probe(suite, cases)
    else:
        report['live_summary'] = live_summary(cases)
        report['failure_reason_counts'] = dict(sorted(Counter(
            case['failure_reason_class'] for case in cases).items()))
    return report


def filter_cases(suite, structures=None, case_ids=None):
    """Restrict a suite to some structures/cases without touching the frozen checks."""
    if not structures and not case_ids:
        return suite
    selected = [case for case in suite['cases']
                if (not structures or case['structure'] in structures)
                and (not case_ids or case['case_id'] in case_ids)]
    if not selected:
        raise SystemExit('NO_CASE_MATCHED:' + ','.join(sorted(structures or []) + sorted(case_ids or [])))
    filtered = dict(suite)
    filtered['cases'] = selected
    used = {name for case in selected for name in case['fixtures']}
    filtered['fixtures'] = {name: fixture for name, fixture in suite['fixtures'].items()
                            if name in used}
    return filtered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--suite', default=str(SUITE_PATH))
    parser.add_argument('--out', default=None)
    parser.add_argument('--structure', action='append', default=None,
                        help='restrict to one structure (P1..P4); repeatable')
    parser.add_argument('--case', action='append', default=None,
                        help='restrict to one case_id; repeatable')
    args = parser.parse_args()
    if args.offline == args.live:
        raise SystemExit('choose exactly one of --offline / --live')
    suite = filter_cases(load_suite(args.suite), args.structure, args.case)
    mode = 'offline' if args.offline else 'live'
    if args.offline:
        cases = run_offline(suite)
    else:
        _install_model_counter()
        cases = run_live(suite)
    command = ' '.join(sys.argv)
    report = summarise(suite, cases, mode, command)
    out = Path(args.out) if args.out else (OFFLINE_REPORT if args.offline else LIVE_REPORT)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {'mode': mode, 'totals': report['totals'],
               'by_structure': report['by_structure'],
               'model_calls': report['model_calls'],
               'error_code_counts': report['error_code_counts'],
               'report': str(out)}
    if mode == 'live':
        summary['live_summary'] = report['live_summary']
        summary['failure_reason_counts'] = report['failure_reason_counts']
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report['totals']['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
