#!/usr/bin/env python
"""Evaluate the frozen normative cross-source suite (``data/evaluation/cross_source_norm_v1.json``).

What this measures, and what it does not
----------------------------------------
Round 2 proved that P2/P3/P4 could not be *answered* on the real data because the knowledge base
had no bindable clause. This harness tests exactly that gap after it was closed with a frozen
corpus (``data/knowledge_sources``):

* the SQL side is compiled from the **clause** of the corpus (metric / threshold direction / unit /
  time scope / entity), executed **read-only** against the real ``demo_retail`` /
  ``demo_industrial`` databases, and the returned rows are the real rows;
* the document side is the **real parsing** of the corpus PDF/DOCX files by the repository's own
  ingestion parsers;
* the verdict, the seven-field binding, the typed evidence graph, the threshold checks and the
  conflict gate are the engine's unchanged code path (``datamind.cross_source.plan``).

``--model`` is deliberately *not* the default: the structure under test is "document rule drives
the SQL filter", so the SQL is compiled from the clause instead of being guessed by NL->SQL. No
upstream model is contacted, and a guard makes any accidental call fail loudly
(``model_calls`` stays 0). The NL->SQL live path is measured by ``scripts/eval_cross_source.py``
and is not re-measured here.

``--check`` validates the frozen labels *before* any verdict is produced: it re-derives each case's
clause from the corpus, re-runs its read-only SQL, recomputes the expected violation count and the
expected conflict, and fails if the frozen label disagrees. Loosening a label therefore breaks
``--check``.

Denominators are never massaged: a declared fail-closed case counts as a refusal, not as an answer,
and "answerable" is defined per structure with both numbers reported.

The report says the same thing at the top level. ``failure_samples`` lists only cases whose
*frozen assertions failed*; the declared fail-closed cases (``expected_status != completed``) pass
their assertions by refusing, so they would be invisible in a top-level read that stopped at
``failure_samples: []`` and ``totals.failed: 0``. ``declared_fail_closed`` therefore carries its own
count, per-structure split and samples, so "28/28 passed" can never be read as "28/28 answered".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind import binding as binding_mod  # noqa: E402
from datamind import cross_source  # noqa: E402
from ingestion import norm_facts  # noqa: E402

SUITE_PATH = ROOT / 'data/evaluation/cross_source_norm_v1.json'
CORPUS_DIR = ROOT / 'data/knowledge_sources'
LIVE_REPORT = ROOT / 'reports/cross_source_norm_live.json'
RETAIL_DSN = 'host=127.0.0.1 port=5432 dbname=demo_retail user=readonly_user password=readonly_dev'
META_DSN = 'host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev'

MODEL_CALLS = {'count': 0}
HOP_EDGE_TYPES = ('entity_match', 'version_scope', 'supports')


def _forbid_model():
    def stub(*args, **kwargs):
        MODEL_CALLS['count'] += 1
        raise RuntimeError('MODEL_CALL_FORBIDDEN_IN_NORM_SUITE')
    return stub


def _install_model_guard():
    import datamind.providers as providers
    providers.chat = _forbid_model()
    for module_name in ('datamind.sql_engine', 'datamind.rag_engine'):
        module = sys.modules.get(module_name)
        if module is not None:
            module.provider_chat = providers.chat


# ---------------------------------------------------------------- read-only access

def _snapshot_ids():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    with psycopg2.connect(META_DSN) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT source_id, snapshot_id FROM schema_snapshots '
                        'ORDER BY source_id, catalog_version')
            latest = {}
            for row in cur.fetchall():
                latest[row['source_id']] = row['snapshot_id']
    return latest


def _readonly_query(dsn, sql, params=()):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    with psycopg2.connect(dsn) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout = '15s'")
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]


def _sql_digest(sql, params):
    return hashlib.sha256(json.dumps([sql, [str(p) for p in params]],
                                     ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _iso(value):
    return None if value is None else value.isoformat()


# ---------------------------------------------------------------- corpus

def corpus_records(paths=None):
    paths = paths or (sorted(CORPUS_DIR.glob('*.pdf')) + sorted(CORPUS_DIR.glob('*.docx')))
    return norm_facts.corpus_records(paths)


def clause_index():
    _documents, clauses, _errors = corpus_records()
    return {record['clause_id']: record for record in clauses}


def corpus_cases(case):
    """The clause records a case's ``doc_selection`` actually reaches (declared selection)."""
    selection = case.get('doc_selection') or {}
    documents, clauses, _errors = corpus_records()
    kept, dropped = norm_facts.select(clauses, documents,
                                      document_ids=selection.get('document_ids'),
                                      must_tokens=selection.get('must_tokens'),
                                      any_tokens=selection.get('any_tokens'),
                                      max_blocks=selection.get('max_blocks', 24))
    return kept, dropped


# ---------------------------------------------------------------- SQL templates
#
# One template per rule family. A template receives the case's parameters and the *parsed clause*;
# the clause decides which column is read (metric/unit) and the time scope, while the threshold
# comparison itself is left to the engine's ``threshold_checks`` unless the case declares
# ``pushdown: true`` — a pushed-down threshold makes a zero-hit run report "no SQL evidence",
# which would turn a compliant case into a false refusal.

def _row(entity, value, unit, metric, *, row_id, recorded_at, scope, version=None, extra=None):
    row = {'row_id': row_id, 'entity_label': entity, 'metric': metric, 'value': value, 'unit': unit,
           'recorded_at': recorded_at, 'time_scope': scope}
    if version:
        row['recorded_revision'] = version
    row.update(extra or {})
    return row


def _t_retail_refund_delay(params, clause, metric):
    sql = ('SELECT r.refund_id::text AS row_id, o.channel AS entity_label, '
           'EXTRACT(EPOCH FROM (r.refunded_at - o.paid_at)) / 86400.0 AS value, '
           'r.refunded_at AS recorded_at '
           'FROM refunds r JOIN order_items oi ON oi.order_item_id = r.order_item_id '
           'JOIN orders o ON o.order_id = oi.order_id '
           'WHERE o.paid_at IS NOT NULL AND r.refunded_at > o.paid_at '
           'AND r.refunded_at >= %s AND r.refunded_at < %s '
           'ORDER BY r.refund_id LIMIT 500')
    args = (params['window_start'], params['window_end'])
    scope = {'start': params['window_start'], 'end': params['window_end']}
    rows = [_row(item['entity_label'], item['value'], '天', metric, row_id=item['row_id'],
                 recorded_at=_iso(item['recorded_at']), scope=scope)
            for item in _readonly_query(RETAIL_DSN, sql, args)]
    return {'source_ref': 'demo_retail', 'sql': sql, 'params': args, 'rows': rows}


def _t_retail_channel_sales(params, clause, metric):
    sql = ('SELECT o.channel AS entity_label, '
           'SUM(oi.quantity * oi.unit_price * (1 - oi.discount)) AS value '
           'FROM orders o JOIN order_items oi ON oi.order_id = o.order_id '
           "WHERE o.order_status = 'completed' AND o.created_at >= %s AND o.created_at < %s "
           'GROUP BY o.channel ORDER BY o.channel')
    args = (params['window_start'], params['window_end'])
    scope = {'start': params['window_start'], 'end': params['window_end']}
    rows = [_row(item['entity_label'], item['value'], '元', metric,
                 row_id='channel:' + str(item['entity_label']),
                 recorded_at=params['window_end'], scope=scope)
            for item in _readonly_query(RETAIL_DSN, sql, args)]
    return {'source_ref': 'demo_retail', 'sql': sql, 'params': args, 'rows': rows}


def _t_retail_member_spend(params, clause, metric):
    sql = ('SELECT c.customer_level AS entity_label, '
           'SUM(oi.quantity * oi.unit_price * (1 - oi.discount)) AS value '
           'FROM customers c JOIN orders o ON o.customer_id = c.customer_id '
           'JOIN order_items oi ON oi.order_id = o.order_id '
           "WHERE o.order_status = 'completed' AND o.created_at >= %s AND o.created_at < %s "
           'GROUP BY c.customer_level ORDER BY c.customer_level')
    args = (params['window_start'], params['window_end'])
    scope = {'start': params['window_start'], 'end': params['window_end']}
    rows = [_row(item['entity_label'], item['value'], '元', metric,
                 row_id='level:' + str(item['entity_label']),
                 recorded_at=params['window_end'], scope=scope)
            for item in _readonly_query(RETAIL_DSN, sql, args)]
    return {'source_ref': 'demo_retail', 'sql': sql, 'params': args, 'rows': rows}


def _t_retail_category_margin(params, clause, metric):
    sql = ('SELECT p.category AS entity_label, '
           'MIN((p.list_price - p.cost_price) / NULLIF(p.list_price, 0)) * 100 AS value '
           'FROM products p GROUP BY p.category ORDER BY p.category')
    scope = {'start': params['window_start'], 'end': None}
    rows = [_row(item['entity_label'], item['value'], '%', metric,
                 row_id='category:' + str(item['entity_label']),
                 recorded_at=params['window_start'], scope=scope)
            for item in _readonly_query(RETAIL_DSN, sql)]
    return {'source_ref': 'demo_retail', 'sql': sql, 'params': (), 'rows': rows}


def _t_retail_product_price(params, clause, metric):
    sql = ('SELECT p.product_id::text AS row_id, p.product_name AS entity_label, '
           'p.list_price AS value FROM products p ORDER BY p.product_id')
    scope = {'start': params['window_start'], 'end': None}
    rows = [_row(item['entity_label'], item['value'], '元', metric, row_id=item['row_id'],
                 recorded_at=params['window_start'], scope=scope)
            for item in _readonly_query(RETAIL_DSN, sql)]
    return {'source_ref': 'demo_retail', 'sql': sql, 'params': (), 'rows': rows}


def _t_retail_order_quantity(params, clause, metric):
    sql = ('SELECT o.order_id::text AS row_id, o.channel AS entity_label, '
           'SUM(oi.quantity) AS value '
           'FROM orders o JOIN order_items oi ON oi.order_id = o.order_id '
           'WHERE o.channel = %s AND o.created_at >= %s AND o.created_at < %s '
           'GROUP BY o.order_id, o.channel ORDER BY o.order_id LIMIT 400')
    args = (params['channel'], params['window_start'], params['window_end'])
    scope = {'start': params['window_start'], 'end': params['window_end']}
    rows = [_row(item['entity_label'], item['value'], '件', metric, row_id=item['row_id'],
                 recorded_at=params['window_end'], scope=scope)
            for item in _readonly_query(RETAIL_DSN, sql, args)]
    return {'source_ref': 'demo_retail', 'sql': sql, 'params': args, 'rows': rows}


def _t_industrial_temp(params, clause, metric):
    """Industrial holding-temperature rows, read-only, with their real process revision.

    ``normalized`` reads the database's own ``temperature_c`` column (``unit='degC'``) and drops
    the measurements the database itself could not normalise — those rows are kept as the
    unit-incomparable sample of the P3 suite instead of being silently averaged in.

    ``pushdown`` compiles the clause's comparison straight into the ``WHERE`` clause (the literal
    "document rule drives the SQL filter" reading). It is off by default because a pushed-down
    threshold turns a compliant case into an empty result set, which the engine reports as
    "no SQL evidence" rather than as "no violation".
    """
    normalized = bool(params.get('normalized', True))
    op = str(clause.get('op') or '')
    pushdown = bool(params.get('pushdown'))
    condition, args = '', ()
    if pushdown:
        if op not in ('>', '>=', '<', '<=', '='):
            raise SystemExit('PUSHDOWN_OPERATOR_UNSUPPORTED:' + op)
        condition = f' AND m.temperature_c {op} %s'
        args = args + (str(clause.get('limit')),)
    sql = ('SELECT m.measurement_id AS row_id, m.part_id AS entity_label, m.value AS raw_value, '
           'm.unit AS raw_unit, m.temperature_c, m.measured_at, b.recorded_revision, '
           'r.valid_from AS revision_valid_from, r.valid_to AS revision_valid_to '
           'FROM demo_industrial.measurement_observations m '
           'JOIN demo_industrial.production_batches b ON b.batch_id = m.batch_id '
           'LEFT JOIN demo_industrial.process_revisions r ON r.revision_id = b.recorded_revision '
           "WHERE m.part_id = %s AND m.metric = 'holding_temperature' "
           'AND m.measured_at >= %s AND m.measured_at < %s' + condition +
           ' ORDER BY m.measured_at, m.measurement_id LIMIT 200')
    args = (params['part_id'], params['window_start'], params['window_end']) + args
    scope = {'start': params['window_start'], 'end': params['window_end']}
    rows, excluded = [], []
    for item in _readonly_query(META_DSN, sql, args):
        if normalized:
            if item['temperature_c'] is None:
                excluded.append({'row_id': item['row_id'], 'reason': 'TEMPERATURE_NOT_NORMALIZED',
                                 'raw_value': str(item['raw_value']), 'raw_unit': item['raw_unit']})
                continue
            value, unit = item['temperature_c'], 'degC'
        else:
            value, unit = item['raw_value'], item['raw_unit']
        rows.append(_row(item['entity_label'], value, unit, metric, row_id=item['row_id'],
                         recorded_at=_iso(item['measured_at']), scope=scope,
                         version=item['recorded_revision']))
    return {'source_ref': 'demo_industrial', 'sql': sql, 'params': args, 'rows': rows,
            'excluded': excluded}


def _t_industrial_by_ids(params, clause, metric):
    """The exact measurement rows a case names (P3/P4 focused samples)."""
    identifiers = list(params['measurement_ids'])
    placeholders = ','.join(['%s'] * len(identifiers))
    sql = ('SELECT m.measurement_id AS row_id, m.part_id AS entity_label, m.value AS raw_value, '
           'm.unit AS raw_unit, m.temperature_c, m.measured_at, b.recorded_revision '
           'FROM demo_industrial.measurement_observations m '
           'JOIN demo_industrial.production_batches b ON b.batch_id = m.batch_id '
           f'WHERE m.measurement_id IN ({placeholders}) ORDER BY m.measurement_id')
    scope = {'start': params['window_start'], 'end': params['window_end']}
    rows = [_row(item['entity_label'], item['raw_value'], item['raw_unit'], metric,
                 row_id=item['row_id'], recorded_at=_iso(item['measured_at']), scope=scope,
                 version=item['recorded_revision'])
            for item in _readonly_query(META_DSN, sql, identifiers)]
    return {'source_ref': 'demo_industrial', 'sql': sql, 'params': identifiers, 'rows': rows}


TEMPLATES = {
    'retail_refund_delay': _t_retail_refund_delay,
    'retail_channel_sales': _t_retail_channel_sales,
    'retail_member_spend': _t_retail_member_spend,
    'retail_category_margin': _t_retail_category_margin,
    'retail_product_price': _t_retail_product_price,
    'retail_order_quantity': _t_retail_order_quantity,
    'industrial_temp': _t_industrial_temp,
    'industrial_by_ids': _t_industrial_by_ids,
}


def engine_clause(record):
    """The clause as the *engine* reads it from the corpus block (operator already mapped).

    The corpus stores the document's own wording (``超过`` / ``不得低于``); ``extract_clause`` maps
    it onto the violation operator (``>`` / ``<``). The SQL templates and the independent
    recomputation both consume this reading, so a change in the mapping shows up in both.
    """
    if not record:
        return {}
    if record.get('limit') is None:
        return {'metric': record.get('metric'), 'limit': None, 'op': None,
                'unit': record.get('unit')}
    return cross_source.extract_clause(norm_facts._block_for(record, {})) or {}


def build_sql_evidence(case, clause, snapshots):
    template = TEMPLATES[case['sql_template']]
    metric = (cross_source.parse_question(case['question'])['metrics'] or [None])[0]
    built = template(case.get('sql_params') or {}, clause, metric)
    source_ref = built['source_ref']
    return {'source_ref': source_ref, 'snapshot_id': snapshots.get(source_ref),
            'sql': built['sql'], 'sql_hash': _sql_digest(built['sql'], built.get('params') or ()),
            'columns': [], 'rows': built['rows'],
            'projection': {'sql_template': case['sql_template'],
                           'pushdown': bool((case.get('sql_params') or {}).get('pushdown')),
                           'metric': metric,
                           'excluded_rows': built.get('excluded') or []}}, built, metric


# ---------------------------------------------------------------- engine run

def run_case(case, clause_map, snapshots, aliases, authority_rules):
    clause_ids = case.get('expected_clause_ids') or []
    record = clause_map.get(clause_ids[0]) if clause_ids else None
    evidence, built, metric = build_sql_evidence(case, engine_clause(record), snapshots)
    selection = case.get('doc_selection') or {}
    doc_evidence = cross_source.norm_corpus_evidence(
        document_ids=selection.get('document_ids'), must_tokens=selection.get('must_tokens'),
        any_tokens=selection.get('any_tokens'), max_blocks=selection.get('max_blocks', 24))
    result = cross_source.plan(case['question'], [evidence['source_ref']],
                               selection.get('document_ids'),
                               sql_evidence=evidence, rag_evidence=doc_evidence,
                               aliases=aliases, authority_rules=authority_rules)
    context = {'clause_map': clause_map, 'clause': record, 'sql_rows': evidence['rows'],
               'doc_evidence': doc_evidence, 'metric': metric, 'snapshots': snapshots}
    return result, context


# ---------------------------------------------------------------- checks

def _graph(result):
    graph = result.get('evidence_graph') or {}
    nodes = {node['node_id']: node for node in graph.get('nodes') or []}
    return nodes, list(graph.get('edges') or [])


def _clause_node(nodes, clause_id):
    for node in nodes.values():
        if node['node_type'] == 'spec_clause' and node.get('ref') == clause_id:
            return node
    return None


def _node_ids(result):
    nodes, _edges = _graph(result)
    return set(nodes)


def _ref_to_id(result, clause_id):
    nodes, _edges = _graph(result)
    node = _clause_node(nodes, clause_id)
    return None if node is None else node['node_id']


def longest_chain(result):
    """Longest simple chain over the allowed hop edges with **no repeated edge type**.

    A plain longest path degenerates into an ``entity_match`` chain (every A102 row shares the
    part id with every document block), which says nothing about the structure. Requiring each edge
    *type* to appear at most once forces a meaningful composition — data row ``--version_scope-->``
    clause ``--supports-->`` clause text → ... — and the chain is still read off the real graph.
    """
    nodes, edges = _graph(result)
    adjacency = {}
    for edge in edges:
        if edge['edge_type'] not in HOP_EDGE_TYPES:
            continue
        adjacency.setdefault(edge['src'], []).append((edge['dst'], edge['edge_type']))
        adjacency.setdefault(edge['dst'], []).append((edge['src'], edge['edge_type']))
    best = {'edges': 0, 'node_types': [], 'edge_types': [], 'start': None}
    budget = 20000
    for start in adjacency:
        stack = [(start, frozenset([start]), [nodes[start]['node_type']], [], frozenset())]
        while stack and budget > 0:
            current, seen, node_types, edge_types, used = stack.pop()
            budget -= 1
            if len(edge_types) > best['edges']:
                best = {'edges': len(edge_types), 'node_types': list(node_types),
                        'edge_types': list(edge_types), 'start': start}
            for neighbour, edge_type in adjacency.get(current, ()):
                if neighbour in seen or edge_type in used:
                    continue
                stack.append((neighbour, seen | {neighbour}, node_types + [nodes[neighbour]['node_type']],
                              edge_types + [edge_type], used | {edge_type}))
    return best


def run_check(check, result, context):
    kind = check['check']
    if kind == 'status':
        return result.get('status') == check['equals'], result.get('status')
    if kind == 'error_code':
        return result.get('error_code') == check.get('equals'), result.get('error_code')
    if kind == 'failure_detail_contains':
        detail = str(result.get('failure_detail') or '')
        return check['value'] in detail, detail
    if kind == 'clause_bound':
        nodes, _edges = _graph(result)
        node = _clause_node(nodes, check['clause_id'])
        if node is None:
            return False, None
        binding = node.get('binding') or {}
        missing = [field for field in check.get('fields') or [] if not binding.get(field)]
        return (not missing), {'missing': missing, 'binding': binding}
    if kind in ('threshold_checks', 'threshold_violations', 'threshold_needs_review'):
        wanted_id = _ref_to_id(result, check['clause_id'])
        subset = [item for item in result.get('threshold_checks') or []
                  if item.get('clause_node_id') == wanted_id]
        if kind == 'threshold_checks':
            observed = len(subset)
            return observed == check['count'], observed
        if kind == 'threshold_violations':
            observed = sorted(item['claim_node_id'] for item in subset
                              if item.get('violation') is True)
            return len(observed) == check['count'], {'count': len(observed), 'node_ids': observed}
        observed = [item for item in subset if item.get('reason') == check['reason']]
        return len(observed) >= check.get('min', 1), {'count': len(observed),
                                                      'statuses': [i.get('status') for i in observed]}
    if kind == 'sql_rows':
        observed = (result.get('sql_evidence') or {}).get('row_count')
        return observed == check['count'], observed
    if kind == 'doc_linked':
        linkage = result.get('doc_linkage') or {}
        return (linkage.get('linked') or 0) >= check['min'], linkage
    if kind == 'answer_contains':
        answer = result.get('answer')
        text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        missing = [item for item in check['all_of'] if item not in text]
        return (not missing), {'missing': missing, 'answer': text[:400]}
    if kind == 'edges_present':
        _nodes, edges = _graph(result)
        counts = Counter(edge['edge_type'] for edge in edges)
        missing = [edge_type for edge_type in check['edge_types']
                   if counts.get(edge_type, 0) < check.get('min', 1)]
        return (not missing), {'counts': dict(counts), 'missing': missing}
    if kind == 'edges_between_absent':
        nodes, edges = _graph(result)
        clause = _clause_node(nodes, check['clause_id'])
        if clause is None:
            return False, None
        wanted_kind = check.get('to_source_kind')
        offenders = []
        for edge in edges:
            if edge['edge_type'] not in set(check.get('edge_types') or ()):
                continue
            other = edge['dst'] if edge['src'] == clause['node_id'] else (
                edge['src'] if edge['dst'] == clause['node_id'] else None)
            if other is None:
                continue
            if wanted_kind and nodes[other]['source_kind'] != wanted_kind:
                continue
            offenders.append({'edge_type': edge['edge_type'], 'other': other})
        return (not offenders), {'offenders': offenders}
    if kind == 'hops':
        chain = longest_chain(result)
        ok = chain['edges'] >= check.get('min_edges', 1)
        wanted_types = check.get('node_types') or []
        if wanted_types:
            ok = ok and all(node_type in chain['node_types'] for node_type in wanted_types)
        # Edge types are required to be *present* on the chain, not to appear in one fixed order:
        # the chain is undirected, so its printed order depends on the chosen start node.
        wanted_edges = list(check.get('edge_types') or check.get('edge_sequence') or [])
        if wanted_edges:
            ok = ok and all(edge_type in chain['edge_types'] for edge_type in wanted_edges)
        return ok, chain
    if kind == 'conflict':
        conflicts = result.get('conflicts') or []
        unresolved = result.get('unresolved_conflicts') or []
        if not check.get('present'):
            return (not conflicts), {'conflicts': len(conflicts),
                                     'unresolved': len(unresolved)}
        matched = None
        for record in unresolved:
            if check.get('entity_id') and record.get('entity_id') != check['entity_id']:
                continue
            if check.get('version') and str(record.get('version')) != str(check['version']):
                continue
            if check.get('metric') and str(record.get('metric')) != str(check['metric']):
                continue
            if check.get('conflict_type') and record.get('conflict_type') != check['conflict_type']:
                continue
            values = {str(record['left'].get('value')), str(record['right'].get('value'))}
            if check.get('values') and values != set(str(v) for v in check['values']):
                continue
            matched = record
            break
        return matched is not None, {'matched': matched,
                                     'unresolved': [{'conflict_type': record['conflict_type'],
                                                     'entity_id': record['entity_id'],
                                                     'metric': record['metric'],
                                                     'version': record['version'],
                                                     'left': record['left'].get('value'),
                                                     'right': record['right'].get('value')}
                                                    for record in unresolved]}
    if kind == 'binding_contract':
        claims = [claim for claim in result.get('claims') or []
                  if claim['source_kind'] == check.get('source_kind')]
        incomplete = [claim['claim_id'] for claim in claims
                      if claim.get('binding_status') != 'bound']
        return (bool(claims) and not incomplete), {'claims': len(claims), 'incomplete': incomplete}
    raise SystemExit('UNKNOWN_CHECK:' + kind)


def evaluate(case, result, context):
    observed, failures = [], []
    for check in case['expected_answer_checks']:
        passed, value = run_check(check, result, context)
        observed.append({'check': check['check'], 'wanted': check, 'observed': value,
                         'passed': passed})
        if not passed:
            failures.append({'check': check['check'], 'wanted': check, 'observed': value})
    return observed, failures


# ---------------------------------------------------------------- label check (--check)

#: Where each seven-field binding field comes from on a parsed corpus record. ``entity_id`` is
#: produced by ``binding.entity_bind`` at graph time (an approved alias or a canonical literal);
#: on the record the surface form lives in ``entity_label``, so that is what is checked here and
#: the resolved id is checked by the ``clause_bound`` runtime check.
BINDING_FIELD_SOURCE = {
    'entity_id': 'entity_label', 'entity_label': 'entity_label', 'version': 'applies_to_version',
    'unit': 'unit', 'time_scope': 'valid_from', 'source_ref': 'document_id',
    'source_sha256': 'source_sha256'}


def check_labels(suite):
    """Re-derive every frozen label from the corpus and the real databases."""
    clause_map = clause_index()
    snapshots = _snapshot_ids()
    problems, per_case = [], []
    for case in suite['cases']:
        issues = []
        clause_ids = case.get('expected_clause_ids') or []
        kept, dropped = corpus_cases(case)
        selected_ids = [block['chunk_id'] for block in kept]
        missing = [clause_id for clause_id in clause_ids if clause_id not in selected_ids]
        if missing:
            issues.append({'check': 'clause_selected', 'missing': missing,
                           'selected': selected_ids})
        undeclared = [block['chunk_id'] for block in kept
                      if block.get('limit') is not None and block['chunk_id'] not in clause_ids]
        if undeclared:
            issues.append({'check': 'undeclared_threshold_clause', 'clause_ids': undeclared})
        for clause_id in clause_ids:
            record = clause_map.get(clause_id)
            if record is None:
                issues.append({'check': 'clause_exists', 'clause_id': clause_id})
                continue
            for field in case.get('expected_binding_fields') or []:
                source = BINDING_FIELD_SOURCE.get(field, field)
                if record.get(source) in (None, ''):
                    issues.append({'check': 'binding_field', 'clause_id': clause_id,
                                   'field': field, 'record_field': source})
        metrics = cross_source.parse_question(case['question'])['metrics']
        if (metrics or [None])[:1] != (case.get('expected_question_metrics') or [None])[:1]:
            issues.append({'check': 'question_metrics', 'observed': metrics,
                           'wanted': case.get('expected_question_metrics')})
        record = clause_map.get(clause_ids[0]) if clause_ids else {}
        clause = engine_clause(record)
        evidence, built, metric = build_sql_evidence(case, clause, snapshots)
        if case.get('expected_sql_filter'):
            wanted = case['expected_sql_filter']
            observed = {'metric': record.get('metric'), 'unit': record.get('unit'),
                        'op': record.get('op'), 'limit': record.get('limit')}
            for key in ('metric', 'unit', 'limit'):
                if str(wanted.get(key)) != str(observed.get(key)):
                    issues.append({'check': 'sql_filter_' + key, 'observed': observed.get(key),
                                   'wanted': wanted.get(key)})
            if str(wanted.get('op')) != str(observed.get('op')):
                issues.append({'check': 'sql_filter_op', 'observed': observed.get('op'),
                               'wanted': wanted.get('op')})
        recomputed = recompute(case, clause, evidence['rows'])
        divergence = case.get('expected_recompute_divergence') or {}
        for check in case['expected_answer_checks']:
            if check['check'] in ('threshold_violations', 'threshold_checks') and 'count' in check:
                key = ('violations' if check['check'] == 'threshold_violations' else 'checks')
                declared = divergence.get(key)
                if declared:
                    # The label encodes the *observed* engine behaviour; the independent
                    # recomputation must reproduce the declared divergence, so neither the
                    # engine number nor the independent number can drift unnoticed.
                    if recomputed[key] != declared['independent']:
                        issues.append({'check': check['check'] + '_independent_recomputed',
                                       'observed': recomputed[key],
                                       'wanted': declared['independent']})
                    if check['count'] != declared['engine']:
                        issues.append({'check': check['check'] + '_engine_divergence',
                                       'observed': check['count'], 'wanted': declared['engine']})
                    if declared['independent'] == declared['engine']:
                        issues.append({'check': check['check'] + '_divergence_expected',
                                       'observed': declared})
                    continue
                if recomputed[key] != check['count']:
                    issues.append({'check': check['check'] + '_recomputed',
                                   'observed': recomputed[key], 'wanted': check['count']})
            if check['check'] == 'threshold_needs_review':
                if recomputed['needs_review'] < check.get('min', 1):
                    issues.append({'check': 'threshold_needs_review_recomputed',
                                   'observed': recomputed['needs_review'],
                                   'wanted': check.get('min', 1)})
            if check['check'] == 'sql_rows' and 'count' in check:
                if len(evidence['rows']) != check['count']:
                    issues.append({'check': 'sql_rows_recomputed',
                                   'observed': len(evidence['rows']), 'wanted': check['count']})
            if check['check'] == 'conflict':
                expected = bool(check.get('present'))
                if recomputed['conflict_present'] != expected:
                    issues.append({'check': 'conflict_recomputed',
                                   'observed': recomputed['conflict_present'], 'wanted': expected})
            if check['check'] == 'hops':
                if check.get('min_edges', 0) < 3:
                    issues.append({'check': 'hops_min_edges_too_loose',
                                   'wanted': check.get('min_edges')})
        per_case.append({'case_id': case['case_id'], 'structure': case['structure'],
                         'selected_blocks': selected_ids, 'dropped': len(dropped),
                         'sql_rows': len(evidence['rows']),
                         'sql_excluded': (evidence['projection'].get('excluded_rows') or []),
                         'recomputed': recomputed, 'issues': issues})
        problems.extend([dict(issue, case_id=case['case_id']) for issue in issues])
    return {'ok': not problems, 'problems': problems, 'cases': per_case,
            'documents': len(clause_map)}


def recompute(case, clause, rows):
    """Recompute the frozen expectations straight from the real rows (no engine involved).

    The violation recomputation uses :mod:`datamind.binding` directly (same unit discipline, same
    comparison direction); the conflict recomputation applies the engine's documented conflict key
    ``entity + metric + version`` with a literal entity comparison, which is enough for the A102 /
    A103 samples and is stated as such in the card.

    The conflict key additionally carries the *record scope* (``measurement_id``): two claims that
    name different measurements are not a conflict about the same record. This mirrors
    ``datamind.evidence_graph.record_scope`` after the round-4 defect fix (norm-p3-09 used to be
    reported as a false conflict because two distinct records shared entity+metric+version).
    Claims and rows that do not name a record fall back to the pre-fix behaviour, i.e. they are
    still compared, so the looser key is never used to hide a real conflict.
    """
    op = {'>': lambda a, b: a > b, '>=': lambda a, b: a >= b, '<': lambda a, b: a < b,
          '<=': lambda a, b: a <= b, '=': lambda a, b: a == b}.get(str(clause.get('op')))
    claims = _doc_claims(case)
    violations = checks = needs_review = 0
    comparable = rows if clause.get('limit') is not None else ()
    for row in comparable:
        unit = _effective_unit(row, claims)
        value = binding_mod.normalize_unit(row['value'], unit)
        limit = binding_mod.normalize_unit(clause.get('limit'), clause.get('unit'))
        if value is None or limit is None:
            needs_review += 1
            continue
        checks += 1
        if op is not None and op(value, limit):
            violations += 1
    conflict_present = False
    for block_value, block_unit, block_metric, block_version, block_entity, block_record in claims:
        for row in rows:
            if str(block_metric) != str(row['metric']):
                continue
            if str(block_entity) != str(row['entity_label']):
                continue
            if str(block_version) != str(row.get('recorded_revision') or ''):
                continue
            if not _record_scopes_agree(block_record, row.get('row_id')):
                continue
            unit = _effective_unit(row, claims)
            if not binding_mod.units_equivalent(block_unit, unit):
                conflict_present = True
                continue
            if not binding_mod.values_agree(block_value, block_unit, row['value'], unit):
                conflict_present = True
    return {'violations': violations, 'checks': checks, 'needs_review': needs_review,
            'conflict_present': conflict_present}


def _effective_unit(row, claims):
    """Mirror of the engine's single bounded unit repair: a sibling claim of the same metric.

    ``cross_source._repair_plan`` rewrites an unregistered unit from the first node that shares the
    metric and carries a *registered* unit. The independent recomputation applies the same rule so
    that a repaired case (norm-p3-05) is still recomputable instead of being exempted.
    """
    status = binding_mod.unit_status(row['unit'])
    if not status['unit_unknown']:
        return row['unit']
    for _value, unit, metric, _version, _entity, _record in claims:
        if str(metric) != str(row['metric']):
            continue
        if not binding_mod.unit_status(unit)['unit_unknown']:
            return unit
    return row['unit']


def _record_scopes_agree(left, right):
    """Mirror of ``datamind.evidence_graph._named_records_differ`` (round-4 defect-B fix).

    Two claims that both name a record are comparable only when they name the *same* record; a
    claim that names no record stays comparable, exactly as before the fix. Kept here (rather than
    imported) so the independent recomputation does not depend on the engine it is checking.
    """
    left_scope = None if left in (None, '') else str(left)
    right_scope = None if right in (None, '') else str(right)
    return not (left_scope and right_scope and left_scope != right_scope)


def _doc_claims(case):
    """The document-side numeric claims a case's selection reaches.

    One tuple per claim: ``(value, unit, metric, version, entity, record_scope)``. ``record_scope``
    is the measurement the claim transcribes when the labelled parser recorded one, otherwise
    ``None`` (which keeps the pre-fix comparison behaviour).
    """
    kept, _dropped = corpus_cases(case)
    claims = []
    for block in kept:
        if block.get('value') is not None and block.get('metric'):
            claims.append((block['value'], block.get('unit'), block['metric'],
                           block.get('applies_to_version') or block.get('document_version'),
                           block.get('entity_label'), block.get('measurement_id') or None))
    return claims


# ---------------------------------------------------------------- report

def classify(result):
    if result.get('status') == 'completed':
        return 'answered'
    return str(result.get('error_code') or 'UNKNOWN') + ':' + str(result.get('failure_detail') or '')


def summarise(suite, cases, command):
    by_structure = OrderedDict()
    for name in suite['structures']:
        subset = [case for case in cases if case['structure'] == name]
        answerable = [case for case in subset if case['expected_status'] == 'completed']
        answered = [case for case in answerable if case['passed']]
        entry = {'cases': len(subset), 'passed': sum(1 for c in subset if c['passed']),
                 'failed': sum(1 for c in subset if not c['passed']),
                 'pass_rate': f"{sum(1 for c in subset if c['passed'])}/{len(subset)}",
                 'answerable_cases': len(answerable),
                 'answered_and_verified': len(answered),
                 'answered_rate': f'{len(answered)}/{len(answerable)}',
                 'declared_fail_closed': len(subset) - len(answerable),
                 'meets_minimum_3': len(answered) >= 3}
        by_structure[name] = entry
    passed = sum(1 for case in cases if case['passed'])
    #: A declared fail-closed case refuses *by design* (``expected_status != completed``) and its
    #: assertions pass, so it is absent from ``failure_samples`` even though it is not an answer.
    #: Recording it separately keeps the top level honest without changing what ``failure_samples``
    #: means (assertion failures) or any existing field.
    refusals = [case for case in cases if case['expected_status'] != 'completed']
    report = {
        'suite': suite['suite'], 'suite_version': suite['suite_version'], 'mode': 'live',
        'ran_at': datetime.now(timezone.utc).isoformat(),
        'commit': subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=str(ROOT),
                                 capture_output=True, text=True).stdout.strip() or None,
        'command': command,
        'studio': ('frozen norm corpus parsed by the repository ingestion parsers + read-only '
                   'real SQL compiled from the document clause; no upstream model call'),
        'model_calls': MODEL_CALLS['count'],
        'totals': {'cases': len(cases), 'passed': passed, 'failed': len(cases) - passed,
                   'pass_rate': f'{passed}/{len(cases)}'},
        'by_structure': by_structure,
        'answerable_structures': {name: entry['meets_minimum_3']
                                  for name, entry in by_structure.items()},
        'structures_with_3_answers': sum(1 for entry in by_structure.values()
                                         if entry['meets_minimum_3']),
        'error_code_counts': dict(sorted(Counter(case['error_code'] or 'NONE'
                                                 for case in cases).items())),
        'failure_reason_counts': dict(sorted(Counter(case['failure_reason_class']
                                                     for case in cases).items())),
        'failure_samples': [{'case_id': case['case_id'], 'structure': case['structure'],
                             'question': case['question'], 'status': case['status'],
                             'error_code': case['error_code'],
                             'failure_detail': case['failure_detail'],
                             'failures': case['failures']}
                            for case in cases if not case['passed']],
        'failure_samples_note': ('只记录**断言失败**的样例（`passed == false`）。声明内失败关闭的样例'
                                 '断言全部通过，因此不在这里，见 `declared_fail_closed`。'),
        'declared_fail_closed': {
            'cases': len(refusals),
            'by_structure': dict(sorted(Counter(case['structure'] for case in refusals).items())),
            'note': ('`expected_status != completed` 的声明内失败关闭样例：计入 refused，不计入 '
                     'answered。本字段为 0 条时 `totals.passed == totals.cases` 才可以读作「全部作答」。'),
            'samples': [{'case_id': case['case_id'], 'structure': case['structure'],
                         'question': case['question'], 'expected_status': case['expected_status'],
                         'status': case['status'], 'error_code': case['error_code'],
                         'failure_detail': case['failure_detail'],
                         'failure_reason_class': case['failure_reason_class']}
                        for case in refusals],
        },
        'cases': cases,
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--suite', default=str(SUITE_PATH))
    parser.add_argument('--out', default=None)
    parser.add_argument('--structure', action='append', default=None)
    parser.add_argument('--case', action='append', default=None)
    parser.add_argument('--check-out', default=None)
    args = parser.parse_args()
    if args.check == args.live:
        raise SystemExit('choose exactly one of --check / --live')
    suite = json.loads(Path(args.suite).read_text(encoding='utf-8'))
    if args.structure or args.case:
        suite = filter_suite(suite, args.structure, args.case)
    _install_model_guard()
    if args.check:
        result = check_labels(suite)
        summary = {'ok': result['ok'], 'problems': result['problems'],
                   'cases': [{key: value for key, value in case.items() if key != 'issues'}
                             for case in result['cases']],
                   'case_issues': {case['case_id']: case['issues'] for case in result['cases']
                                   if case['issues']}}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.check_out:
            Path(args.check_out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                            encoding='utf-8')
        return 0 if result['ok'] else 1
    aliases = binding_mod.load_aliases(META_DSN)
    authority_rules = cross_source.load_authority_rules(META_DSN)
    snapshots = _snapshot_ids()
    clause_map = clause_index()
    cases = []
    for case in suite['cases']:
        result, context = run_case(case, clause_map, snapshots, aliases, authority_rules)
        observed, failures = evaluate(case, result, context)
        nodes, edges = _graph(result)
        cases.append({
            'case_id': case['case_id'], 'structure': case['structure'],
            'question': case['question'],
            'intent_structure': (result.get('intent') or {}).get('structure'),
            'question_metrics': (result.get('intent') or {}).get('metrics'),
            'expected_status': case['expected_status'],
            'status': result.get('status'), 'error_code': result.get('error_code'),
            'failure_detail': result.get('failure_detail'),
            'failure_reason_class': classify(result),
            'sql_template': case['sql_template'], 'sql_row_count': len(context['sql_rows']),
            'sql_excluded_rows': (result.get('sql_evidence') or {}).get('projection', {}).get(
                'excluded_rows') or [],
            'doc_blocks_selected': len(context['doc_evidence']['blocks']),
            'doc_block_ids': [block['chunk_id'] for block in context['doc_evidence']['blocks']],
            'doc_blocks_dropped': context['doc_evidence']['dropped_count'],
            'expected_clause_ids': case.get('expected_clause_ids') or [],
            'clause_bindings': _clause_bindings(result, case),
            'doc_linkage': result.get('doc_linkage'),
            'graph_nodes': len(nodes), 'graph_edges': len(edges),
            'edge_type_counts': dict(sorted(Counter(edge['edge_type'] for edge in edges).items())),
            'threshold_checks': _threshold_summary(result),
            'conflicts': [{'conflict_type': record['conflict_type'], 'entity_id': record['entity_id'],
                           'metric': record['metric'], 'version': record['version'],
                           'left': record['left'].get('value'), 'right': record['right'].get('value')}
                          for record in result.get('unresolved_conflicts') or []],
            'hop_chain': longest_chain(result),
            'answer': result.get('answer'),
            'repair_ledger': result.get('repair_ledger'),
            'warnings': result.get('warnings'),
            'checks': observed, 'passed': not failures, 'failures': failures})
    command = ' '.join(sys.argv)
    report = summarise(suite, cases, command)
    out = Path(args.out) if args.out else LIVE_REPORT
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {'mode': 'live', 'totals': report['totals'], 'by_structure': report['by_structure'],
               'structures_with_3_answers': report['structures_with_3_answers'],
               'model_calls': report['model_calls'],
               'error_code_counts': report['error_code_counts'],
               'failure_reason_counts': report['failure_reason_counts'],
               'failure_samples': len(report['failure_samples']),
               'declared_fail_closed': report['declared_fail_closed']['cases'],
               'declared_fail_closed_by_structure': report['declared_fail_closed']['by_structure'],
               'report': str(out)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report['totals']['failed'] == 0 else 1


def _clause_bindings(result, case):
    nodes, _edges = _graph(result)
    bindings = {}
    for clause_id in case.get('expected_clause_ids') or []:
        node = _clause_node(nodes, clause_id)
        if node is None:
            bindings[clause_id] = {'present': False}
            continue
        binding = node.get('binding') or {}
        bindings[clause_id] = {
            'present': True,
            'fields': {field: binding.get(field) for field in binding_mod.BINDING_FIELDS},
            'complete': not node.get('binding_problems'),
            'problems': node.get('binding_problems')}
    return bindings


def _threshold_summary(result):
    return [{'clause_node_id': item.get('clause_node_id'), 'claim_node_id': item.get('claim_node_id'),
             'metric': item.get('metric'), 'op': item.get('op'), 'limit': item.get('limit'),
             'unit': item.get('unit'), 'value': item.get('value'), 'status': item.get('status'),
             'violation': item.get('violation'), 'reason': item.get('reason'),
             'expression': item.get('expression')}
            for item in result.get('threshold_checks') or []]


def filter_suite(suite, structures=None, case_ids=None):
    selected = [case for case in suite['cases']
                if (not structures or case['structure'] in structures)
                and (not case_ids or case['case_id'] in case_ids)]
    if not selected:
        raise SystemExit('NO_CASE_MATCHED')
    filtered = dict(suite)
    filtered['cases'] = selected
    return filtered


if __name__ == '__main__':
    raise SystemExit(main())
