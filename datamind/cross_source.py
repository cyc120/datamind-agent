"""Cross-source fusion: typed evidence graph -> binding -> verification -> bounded repair.

The public entry points are :func:`plan` and :func:`run`.

``plan(question, sources, document_collections, ...)`` runs the whole pipeline on *injected*
evidence containers. It never calls a model: SQL rows and document blocks are handed in (by a live
adapter or by a frozen fixture), and the answer is composed deterministically from the typed graph
and the claims bound to it.

``run(...)`` builds the live adapters (``sql_engine.ask`` for the SQL side, ``KnowledgeRetriever``
for the document side) and calls :func:`plan`.

Fail-closed contract — the pipeline never guesses:

* both a SQL side and a document side are required; a missing side is
  ``CROSS_SOURCE_EVIDENCE_INSUFFICIENT``;
* every numeric claim must carry a complete seven-field binding
  (``entity_id, entity_label, version, unit, time_scope, source_ref, source_sha256``);
  a document-side claim that cannot be bound (including "the document version does not apply to
  this time scope") is ``DOC_BINDING_MISSING``; an unsettleable SQL-side claim is
  ``CROSS_SOURCE_EVIDENCE_INSUFFICIENT``;
* two sources that disagree on the same ``entity + metric + version`` and have no *registered*
  authority rule produce ``CONFLICT_UNRESOLVED``, with **both original values** retained in the
  conflict list and the answer text;
* at most one repair is attempted, it may only fix a *binding* error (entity alias / version from
  the time scope / unit from a sibling claim), and every attempt is written to ``repair_ledger``.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import binding as binding_mod
from . import evidence_graph as graph_mod
from .evidence_graph import EvidenceGraph, GraphError, detect_conflicts, unresolved

CROSS_SOURCE_VERSION = 'cross-source-v1'

DOC_BINDING_MISSING = 'DOC_BINDING_MISSING'
CONFLICT_UNRESOLVED = graph_mod.CONFLICT_UNRESOLVED
CROSS_SOURCE_EVIDENCE_INSUFFICIENT = 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT'
#: A request scope that allows no data source at all. It is never degraded into
#: "evidence insufficient" and never turned into an automatic source pick (H1, round-1 review).
SOURCE_NOT_ALLOWED = 'SOURCE_NOT_ALLOWED'

#: Lexical hints for the live projection only. They never invent a value; an unknown metric word
#: leaves the unit unbound and the claim fails closed.
_METRIC_UNIT_HINTS = (
    ('温度', 'degC'), ('temperature', 'degC'), ('摄氏度', 'degC'),
    ('销售额', 'CNY'), ('金额', 'CNY'), ('营收', 'CNY'), ('退款额', 'CNY'),
    ('时长', 'day'), ('时限', 'day'), ('天数', 'day'), ('天', 'day'),
    ('数量', 'count'), ('笔数', 'count'), ('订单数', 'count'), ('人数', 'count'), ('件数', 'count'),
    # The live projection reads its metric word from the question; "会员数" was missing, so a real
    # membership-count question produced no metric at all (``NO_BOUNDABLE_SQL_CLAIM``).
    ('会员数', 'count'), ('客户数', 'count'), ('用户数', 'count'),
    ('比例', '%'), ('占比', '%'), ('增长率', '%'),
)
_STRUCTURE_RULES = (
    ('P4', ('条款', '说明材料', '多跳', '逐跳')),
    ('P3', ('冲突', '一致', '偏差', '核验', '超限', '异常', '不能裁定')),
    ('P2', ('找出', '筛选', '哪些', '超过', '超出', '未完成', '阈值', '时限', '不得超过')),
    ('P1', ()),
)
_CLAUSE_RE = re.compile(
    r'(?P<metric>[\u4e00-\u9fffA-Za-z_]{2,20}?)[^\d\-]{0,10}?'
    r'(?P<op><=|>=|<|>|=|不超过|不得低于|不低于|高于|低于|超过)\s*'
    r'(?P<limit>-?\d+(?:\.\d+)?)\s*'
    r'(?P<unit>degC|degF|K|℃|°C|mm|cm|km|kg|g|t|min|h|s|day|天|小时|分钟|秒|%|元|万元|件|个|条|笔|人)')
_OP_MAP = {'不超过': '>', '不得低于': '<', '不低于': '<', '高于': '>', '低于': '<', '超过': '>'}


class CrossSourceError(ValueError):
    def __init__(self, code, detail=''):
        super().__init__(code + (':' + detail if detail else ''))
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- small helpers

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _as_dict(row, columns):
    if isinstance(row, dict):
        return dict(row)
    return {str(column): row[index] if index < len(row) else None
            for index, column in enumerate(columns)}


def _first(mapping, *names):
    for name in names:
        value = mapping.get(name)
        if value not in (None, ''):
            return value
    return None


def _time_scope(mapping):
    scope = mapping.get('time_scope')
    if isinstance(scope, dict) and (scope.get('start') or scope.get('end')):
        return scope
    start = _first(mapping, 'period_start', 'window_start', 'valid_from', 'start')
    end = _first(mapping, 'period_end', 'window_end', 'valid_to', 'end')
    if start is None and end is None:
        return None
    return {'start': start, 'end': end}


def _instant(mapping, scope):
    for name in ('measured_at', 'produced_at', 'recorded_at', 'instant'):
        value = mapping.get(name)
        if value:
            return value
    if isinstance(scope, dict) and scope.get('start'):
        return scope['start']
    return None


def _version(mapping, fallback):
    """The applicable revision of a claim, then its own version, then the dataset snapshot.

    Conflict grouping uses this value, so a production row that names the process revision it was
    produced under (``recorded_revision``) groups with the document revision of the same name
    instead of with the dataset snapshot id.
    """
    return _first(mapping, 'applies_to_version', 'recorded_revision', 'revision', 'version',
                  'snapshot_version') or fallback


def _entity_label(mapping, columns=()):
    explicit = _first(mapping, 'entity_label', 'entity', 'entity_name', 'part_id', 'category',
                      'product', 'region', 'channel', 'customer_level', 'object', 'subject')
    if explicit:
        return str(explicit)
    for column in columns or ():
        lowered = str(column).lower()
        if any(token in lowered for token in ('name', 'label', 'category', 'product', 'category_name')):
            value = mapping.get(column)
            if value not in (None, ''):
                return str(value)
    return None


# ---------------------------------------------------------------- question parsing

def parse_question(question: str) -> dict:
    """Deterministic intent/structure parse. No model, no gold input."""
    text = str(question or '')
    structure = 'P1'
    for name, markers in _STRUCTURE_RULES:
        if markers and any(marker in text for marker in markers):
            structure = name
            break
    metrics = [word for word, _ in _METRIC_UNIT_HINTS if word in text]
    entities = re.findall(r'\b[A-Z]\d{2,4}\b', text.upper())
    entities += re.findall(r'《([^》]{2,40})》', text)
    return {'question': text, 'structure': structure, 'metrics': metrics,
            'entities': sorted(set(entities)),
            'needs_sql': True, 'needs_docs': True}


# ---------------------------------------------------------------- evidence containers

def sql_container(evidence) -> dict:
    """Normalise a SQL evidence container: ``{source_ref, snapshot_id, sql_hash, columns, rows}``."""
    if evidence is None:
        return {'source_ref': None, 'snapshot_id': None, 'columns': [], 'rows': [],
                'sql_hash': None, 'present': False, 'reason': 'SQL_EVIDENCE_ABSENT'}
    if callable(evidence):
        evidence = evidence()
    if not isinstance(evidence, dict):
        raise CrossSourceError('SQL_EVIDENCE_INVALID', type(evidence).__name__)
    columns = list(evidence.get('columns') or [])
    rows = [_as_dict(row, columns) for row in (evidence.get('rows') or [])]
    source_ref = _first(evidence, 'source_ref', 'source_id', 'source')
    digest = evidence.get('evidence_sha256') or _sha(graph_mod.canonical_json(
        {'source_ref': source_ref, 'snapshot_id': evidence.get('snapshot_id'),
         'sql_hash': evidence.get('sql_hash'), 'columns': columns,
         'rows': [[str(cell) for cell in row.values()] for row in rows]}))
    return {'source_ref': source_ref, 'snapshot_id': _first(evidence, 'snapshot_id', 'schema_snapshot'),
            'sql': evidence.get('sql'), 'sql_hash': evidence.get('sql_hash'),
            'columns': columns, 'rows': rows, 'evidence_sha256': digest,
            'present': bool(rows), 'reason': None if rows else 'SQL_EVIDENCE_EMPTY',
            'projection': evidence.get('projection')}


def doc_container(evidence) -> dict:
    """Normalise a document evidence container into ``{source_ref, blocks, present}``."""
    if evidence is None:
        return {'source_ref': None, 'blocks': [], 'present': False,
                'reason': 'DOC_EVIDENCE_ABSENT'}
    if callable(evidence):
        evidence = evidence()
    blocks = evidence.get('blocks') if isinstance(evidence, dict) else evidence
    if blocks is None:
        blocks = []
    normalised = []
    for position, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        item = dict(block)
        item.setdefault('chunk_id', item.get('block_id') or f'chunk-{position}')
        item.setdefault('source_ref', item.get('document_id') or f'doc-{position}')
        text = str(item.get('text') or item.get('exact_text') or '')
        item['text'] = text
        if not item.get('source_sha256'):
            item['source_sha256'] = item.get('document_sha256')
        if not item.get('document_version'):
            item['document_version'] = item.get('doc_version')
        normalised.append(item)
    return {'source_ref': (evidence.get('source_ref') if isinstance(evidence, dict) else None),
            'blocks': normalised, 'present': bool(normalised),
            'reason': None if normalised else 'DOC_EVIDENCE_EMPTY'}


def extract_clause(block: dict):
    """Conservative clause extraction from document text; returns ``None`` when unsure.

    Only an explicit ``clause`` object, or a single ``metric + operator + number + unit`` phrase
    in the text, becomes a ``spec_clause``. Anything else stays a plain ``doc_block``.

    A unit the converter cannot handle is marked ``unsupported_unit`` right here, at extraction
    time, so it is never later reported as a *missing binding field* (L2, round-1 security review).
    """
    clause = _mark_unsupported_unit(_extract_clause_raw(block))
    return clause


def _mark_unsupported_unit(clause):
    if clause is None:
        return None
    unit = clause.get('unit')
    clause['unsupported_unit'] = bool(unit) and binding_mod.unit_status(unit)['unit_unknown']
    return clause


def _extract_clause_raw(block: dict):
    clause = block.get('clause')
    if isinstance(clause, dict) and clause.get('limit') is not None:
        return {'clause_id': clause.get('clause_id') or block.get('chunk_id'),
                'metric': clause.get('metric') or block.get('metric'),
                'op': _OP_MAP.get(str(clause.get('op')), clause.get('op')) or '<=',
                'limit': clause.get('limit'), 'unit': clause.get('unit') or block.get('unit'),
                'entity_label': clause.get('entity_label') or _entity_label(block) or block.get('entity_label'),
                'valid_from': clause.get('valid_from') or block.get('valid_from'),
                'valid_to': clause.get('valid_to') or block.get('valid_to'),
                'quoted': clause.get('quoted') or block.get('text', '')[:200]}
    if block.get('limit') is not None and block.get('metric'):
        return {'clause_id': block.get('block_id') or block.get('chunk_id'),
                'metric': block.get('metric'), 'op': block.get('op') or '<=',
                'limit': block.get('limit'), 'unit': block.get('unit'),
                'entity_label': block.get('entity_label'), 'valid_from': block.get('valid_from'),
                'valid_to': block.get('valid_to'), 'quoted': block.get('text', '')[:200]}
    match = _CLAUSE_RE.search(block.get('text') or '')
    if not match:
        return None
    return {'clause_id': block.get('block_id') or block.get('chunk_id'),
            'metric': match.group('metric'), 'op': _OP_MAP.get(match.group('op'), match.group('op')),
            'limit': match.group('limit'), 'unit': match.group('unit'),
            'entity_label': _entity_label(block), 'valid_from': block.get('valid_from'),
            'valid_to': block.get('valid_to'), 'quoted': match.group(0)}


# ---------------------------------------------------------------- graph construction

def _bind(node_payload, *, entity, version, unit, scope, source_ref, sha, extra=None):
    return binding_mod.build_binding(entity_id=entity['entity_id'],
                                     entity_label=entity['entity_label'], version=version,
                                     unit=unit, time_scope=scope, source_ref=source_ref,
                                     source_sha256=sha, extra=extra)


def build_graph(question, intent, sql, docs, aliases=None, *, repair=None, run_ref=None) -> dict:
    """Build the typed graph for one pass. ``repair`` carries the single bounded fix, if any.

    ``run_ref`` scopes generated claim ids to one run while keeping them **stable inside that run**
    (the bounded repair targets a claim by id, so intra-run stability is load-bearing). Without it,
    two runs over identical evidence produced the same claim id and the second run's claim was
    silently dropped by ``ON CONFLICT (claim_id) DO NOTHING`` — the same cross-source question
    asked twice lost the second run's claims.
    """
    repair = repair or {}
    graph = EvidenceGraph(run_ref=run_ref)
    claims_meta = []
    doc_side_problems = []
    sql_side_problems = []
    for position, row in enumerate(sql['rows']):
        label = _entity_label(row, sql['columns'])
        bound = binding_mod.entity_bind(label, aliases)
        unit = _first(row, 'unit', 'normalized_unit')
        version = _version(row, sql['snapshot_id'])
        scope = _time_scope(row) or repair.get('time_scope')
        if repair.get('version'):
            version = repair['version']
        if repair.get('unit') and binding_mod.unit_status(unit)['unit_unknown']:
            unit = repair['unit']
        if repair.get('entity_id') and bound['entity_id'] is None:
            bound = {**bound, 'entity_id': repair['entity_id'],
                     'entity_label': repair.get('entity_label') or repair['entity_id'],
                     'match': 'repaired_alias'}
        source_ref = sql['source_ref'] or 'sql'
        ref = str(_first(row, 'measurement_id', 'row_id') or f'row-{position}')
        node_type = 'measurement' if _first(row, 'measurement_id', 'measured_at', 'produced_at',
                                            'batch_id') else 'sql_row'
        payload = {'source_kind': 'sql', 'source_ref': source_ref, 'metric': row.get('metric'),
                   'value': _first(row, 'value', 'measured_value', 'amount', 'count'),
                   'entity_id': bound['entity_id'], 'entity_label': bound['entity_label'],
                   # ``default=str``: a real database row carries Decimal/date/datetime, which the
                   # frozen fixtures (plain strings) never exercised. The projection contract is
                   # unchanged — the *typed* value stays in ``value``/``unit``/``time_scope``; this
                   # text is only the graph payload used for verbatim entity-label containment.
                   'entity_match_kind': bound['match'], 'text': json.dumps(row, ensure_ascii=False,
                                                                          sort_keys=True, default=str),
                   'unsupported_unit': bool(unit) and binding_mod.unit_status(unit)['unit_unknown'],
                   'instant': _instant(row, scope), 'row': position}
        node_binding = _bind(row, entity=bound, version=version, unit=unit, scope=scope,
                             source_ref=source_ref, sha=sql['evidence_sha256'],
                             extra={'sql_hash': sql['sql_hash'], 'snapshot_id': sql['snapshot_id']})
        node = graph.make_node(node_type, ref, source_sha256=sql['evidence_sha256'],
                               version=version, unit=unit, time_scope=scope,
                               binding=node_binding, payload=payload)
        if node.payload['value'] is not None and node.payload['metric']:
            claims_meta.append({'node_id': node.node_id, 'side': 'sql', 'position': position})
            if node.binding_problems:
                sql_side_problems.append({'node_id': node.node_id, 'problems': node.binding_problems})
        if not bound['entity_id']:
            sql_side_problems.append({'node_id': node.node_id, 'problems': ['entity_id'],
                                      'reason': bound.get('reason')})
    for position, block in enumerate(docs['blocks']):
        doc_id = block.get('document_id') or docs['source_ref'] or f'doc-{position}'
        # The binding version is the *applicable revision* of the claim: a document that states
        # the revision it applies to (``applies_to_version`` / ``recorded_revision``) is bound by
        # that, otherwise by its own document version.
        version = _version(block, block.get('document_version'))
        sha = block.get('source_sha256')
        scope = _time_scope(block)
        clause = extract_clause(block)
        label = _entity_label(block) or (clause or {}).get('entity_label')
        bound = binding_mod.entity_bind(label, aliases, allow_literal=True)
        if repair.get('entity_id') and label and bound['entity_id'] is None:
            bound = {**bound, 'entity_id': repair['entity_id'],
                     'entity_label': repair.get('entity_label') or repair['entity_id'],
                     'match': 'repaired_alias'}
        ref = str(block.get('chunk_id') or block.get('block_id') or f'block-{position}')
        doc_binding = _bind(block, entity=bound, version=version, unit=block.get('unit'),
                            scope=scope, source_ref=doc_id, sha=sha,
                            extra={'page_no': block.get('page_no'), 'title': block.get('title')})
        doc_payload = {'source_kind': 'doc', 'source_ref': doc_id,
                       'metric': block.get('metric'),
                       'value': block.get('value'),
                       'entity_id': bound['entity_id'],
                       'entity_label': bound['entity_label'],
                       'unsupported_unit': bool(block.get('unit')) and
                           binding_mod.unit_status(block['unit'])['unit_unknown'],
                       'document_id': doc_id, 'title': block.get('title'),
                       'collection': block.get('collection'),
                       'page_no': block.get('page_no'), 'text': block['text'],
                       'instant': _instant(block, scope),
                       'position': position}
        # Defect B (round 4): carry the record the document block transcribes, so the conflict
        # gate can tell two *different* measurements of one entity apart (evidence_graph
        # .record_scope). Only set when the block names one, so evidence without record ids
        # keeps its exact previous payload.
        if block.get('measurement_id') not in (None, ''):
            doc_payload['measurement_id'] = str(block['measurement_id'])
        doc_node = graph.make_node('doc_block', ref, source_sha256=sha, version=version,
                                   unit=block.get('unit'), time_scope=scope, binding=doc_binding,
                                   payload=doc_payload)
        if doc_node.binding_problems:
            doc_side_problems.append({'node_id': doc_node.node_id,
                                      'problems': doc_node.binding_problems,
                                      'document_id': doc_id})
        if clause:
            clause_scope = {'start': clause.get('valid_from'), 'end': clause.get('valid_to')}
            if not (clause_scope['start'] or clause_scope['end']):
                clause_scope = scope
            clause_binding = _bind(block, entity=bound, version=version, unit=clause.get('unit'),
                                   scope=clause_scope, source_ref=doc_id, sha=sha,
                                   extra={'clause_id': clause['clause_id']})
            clause_node = graph.make_node(
                'spec_clause', str(clause['clause_id']),
                source_sha256=sha, version=version, unit=clause.get('unit'),
                time_scope=clause_scope, binding=clause_binding,
                payload={'source_kind': 'doc', 'source_ref': doc_id, 'metric': clause['metric'],
                         'op': clause['op'], 'limit': clause['limit'],
                         'unit': clause.get('unit'), 'value': None,
                         'entity_id': bound['entity_id'], 'entity_label': bound['entity_label'],
                         'unsupported_unit': bool(clause.get('unsupported_unit')),
                         'document_id': doc_id, 'title': block.get('title'),
                         'quoted': clause.get('quoted'), 'text': block['text'],
                         'instant': _instant(block, scope), 'valid_from': clause.get('valid_from'),
                         'valid_to': clause.get('valid_to')})
            graph.add_edge(doc_node.node_id, clause_node.node_id, 'supports',
                           evidence=f'document block {ref} states clause {clause["clause_id"]}: '
                                    f'{clause["quoted"]!r}',
                           binding_type='clause_text')
            if clause_node.binding_problems:
                doc_side_problems.append({'node_id': clause_node.node_id,
                                          'problems': clause_node.binding_problems,
                                          'document_id': doc_id})
        if doc_node.payload.get('value') is not None and doc_node.payload.get('metric'):
            claims_meta.append({'node_id': doc_node.node_id, 'side': 'doc', 'position': position})
    return {'graph': graph, 'claims_meta': claims_meta, 'doc_problems': doc_side_problems,
            'sql_problems': sql_side_problems}


# ---------------------------------------------------------------- linking & verification

def _surface_forms(entity_label, entity_id, alias_index) -> list[str]:
    """Every approved surface form of one entity that may legitimately name it in a document.

    A retail row carries the *database* value (``online`` / ``app``), while the demo documents
    write the Chinese surface form (``线上商城``). Both name the same entity: the alias registry is
    this system's own approved vocabulary (``线上 -> online``), so an approved alias is entity-level
    evidence exactly like the canonical label. The literal label is always kept.
    """
    forms = []
    for value in (entity_label, entity_id):
        text = str(value or '').strip()
        if text and text not in forms:
            forms.append(text)
    for alias in sorted(alias_index.get(str(entity_id or '').strip(), ())):
        if alias and alias not in forms:
            forms.append(alias)
    return forms


def _alias_index(aliases) -> dict:
    index: dict[str, set] = {}
    for row in aliases or ():
        name = str(row.get('canonical_name') or '').strip()
        alias = str(row.get('alias') or '').strip()
        if name and alias:
            index.setdefault(name, set()).add(alias)
    return index


def link_graph(graph: EvidenceGraph, aliases=None) -> dict:
    """Add ``entity_match`` / ``unit_equivalent`` / ``time_overlap`` / ``version_scope`` edges."""
    counts = {'entity_match': 0, 'unit_equivalent': 0, 'time_overlap': 0, 'version_scope': 0}
    alias_index = _alias_index(aliases)
    nodes = graph.nodes()
    for index, left in enumerate(nodes):
        for right in nodes[index + 1:]:
            if left.source_kind == right.source_kind and left.node_type == right.node_type:
                continue
            evidence = None
            if left.entity_id and right.entity_id and left.entity_id == right.entity_id:
                evidence = f'entity_id {left.entity_id!r} on both sides'
            else:
                for holder, other in ((left, right), (right, left)):
                    text = str(other.payload.get('text') or '')
                    for surface in _surface_forms(holder.entity_label, holder.entity_id, alias_index):
                        if len(surface) >= 2 and surface in text:
                            approved = surface != str(holder.entity_label or '')
                            evidence = (f'approved alias {surface!r} of entity '
                                        f'{holder.entity_id!r} appears verbatim in {other.node_id}'
                                        if approved else
                                        f'entity label {surface!r} appears verbatim in {other.node_id}')
                            break
                    if evidence:
                        break
            if evidence:
                kind = ('entity_id' if evidence.startswith('entity_id')
                        else 'approved_alias' if evidence.startswith('approved alias')
                        else 'text_containment')
                graph.add_edge(left.node_id, right.node_id, 'entity_match', evidence=evidence,
                               binding_type=kind)
                counts['entity_match'] += 1
    for index, left in enumerate(nodes):
        for right in nodes[index + 1:]:
            if left.comparable_value is None or right.comparable_value is None:
                continue
            if binding_mod.units_equivalent(left.unit, right.unit):
                graph.add_edge(left.node_id, right.node_id, 'unit_equivalent',
                               evidence=f'units {left.unit!r} and {right.unit!r} share the '
                                        f'{binding_mod.unit_status(left.unit)["dimension"]} dimension',
                               binding_type='registered_unit_dimension')
                counts['unit_equivalent'] += 1
    for index, left in enumerate(nodes):
        for right in nodes[index + 1:]:
            overlap = binding_mod.time_scope_overlap(left.time_scope, right.time_scope)
            if overlap is True:
                graph.add_edge(left.node_id, right.node_id, 'time_overlap',
                               evidence=f'time scopes overlap: {graph_mod.canonical_json(left.time_scope)}'
                                        f' vs {graph_mod.canonical_json(right.time_scope)}',
                               binding_type='half_open_overlap')
                counts['time_overlap'] += 1
    clauses = graph.nodes_of_type('spec_clause')
    for clause in clauses:
        for node in nodes:
            if node.node_id == clause.node_id or node.payload.get('metric') != clause.payload.get('metric'):
                continue
            # A clause never gets a directional edge back to the document block that states it.
            if graph.edge_between(clause.node_id, node.node_id, 'supports') or \
                    graph.edge_between(node.node_id, clause.node_id, 'supports'):
                continue
            applicable, why = clause_applies_to(clause, node)
            if applicable:
                graph.add_edge(clause.node_id, node.node_id, 'version_scope', evidence=why,
                               binding_type='version_applies')
                counts['version_scope'] += 1
    return counts


def clause_applies_to(clause, node):
    """Return ``(bool, reason)`` for whether a clause's version/scope covers ``node``."""
    if clause.version and node.version and clause.version == node.version:
        return True, f'clause version {clause.version!r} equals claim version'
    instant = node.payload.get('instant')
    valid_from, valid_to = clause.payload.get('valid_from'), clause.payload.get('valid_to')
    if instant and (valid_from or valid_to):
        try:
            if binding_mod.version_applies(instant, valid_from, valid_to):
                return True, f'clause validity [{valid_from}, {valid_to}) covers {instant}'
        except (ValueError, TypeError):
            return False, 'VALIDITY_UNKNOWN'
    if clause.time_scope and node.time_scope:
        overlap = binding_mod.time_scope_overlap(clause.time_scope, node.time_scope)
        if overlap is True:
            return True, 'clause time scope overlaps the claim time scope'
        if overlap is None:
            return False, 'TIME_SCOPE_UNKNOWN'
    return False, 'VERSION_NOT_APPLICABLE'


def threshold_checks(graph: EvidenceGraph) -> list[dict]:
    """Compare each bound numeric claim against each applicable ``spec_clause``."""
    checks = []
    for clause in graph.nodes_of_type('spec_clause'):
        limit = clause.payload.get('limit')
        if limit is None:
            continue
        for node in graph.nodes():
            if node.node_id == clause.node_id or node.numeric_value is None:
                continue
            if node.payload.get('metric') != clause.payload.get('metric'):
                continue
            if not graph.edge_between(clause.node_id, node.node_id, 'version_scope') and \
                    not graph.edge_between(clause.node_id, node.node_id, 'entity_match'):
                continue
            value = node.normalized()[0]
            bound = binding_mod.normalize_unit(limit, clause.unit)
            if value is None or bound is None:
                checks.append({'clause_node_id': clause.node_id, 'claim_node_id': node.node_id,
                               'metric': node.payload.get('metric'), 'op': clause.payload.get('op'),
                               'limit': str(limit), 'unit': clause.unit, 'value': None,
                               'violation': None, 'status': 'needs_review',
                               'reason': 'UNIT_NOT_COMPARABLE'})
                continue
            op = clause.payload.get('op') or '<='
            violated = {'>': value > bound, '>=': value >= bound, '<': value < bound,
                        '<=': value <= bound, '=': value == bound}.get(op)
            if violated is None:
                checks.append({'clause_node_id': clause.node_id, 'claim_node_id': node.node_id,
                               'metric': node.payload.get('metric'), 'op': op,
                               'limit': str(limit), 'unit': clause.unit, 'value': format(value, 'f'),
                               'violation': None, 'status': 'needs_review',
                               'reason': 'OPERATOR_UNSUPPORTED'})
                continue
            checks.append({'clause_node_id': clause.node_id, 'claim_node_id': node.node_id,
                           'metric': node.payload.get('metric'), 'op': op, 'limit': str(limit),
                           'unit': clause.unit, 'value': format(value, 'f'),
                           'raw_value': None if node.numeric_value is None else str(node.numeric_value),
                           'raw_unit': node.unit,
                           'normalized_unit': node.normalized()[1],
                           'violation': bool(violated), 'status': 'checked',
                           'expression': f'{value} {op} {bound}',
                           'evidence': clause.payload.get('quoted')})
    return checks


# ---------------------------------------------------------------- claims & decision

def build_claims(graph: EvidenceGraph, claims_meta, intent) -> list[dict]:
    claims = []
    edges = graph.edges()
    for meta in claims_meta:
        node = graph.node(meta['node_id'])
        bound, problems = binding_mod.binding_complete(node.binding)
        value, unit = node.normalized()
        supporting = [node.node_id]
        for edge in edges:
            if edge.src == node.node_id and graph.node(edge.dst).source_kind == 'doc':
                supporting.append(edge.dst)
            elif edge.dst == node.node_id and graph.node(edge.src).source_kind == 'doc' and \
                    edge.edge_type in ('entity_match', 'version_scope', 'supports'):
                supporting.append(edge.src)
        payload = node.payload
        claims.append({
            'claim_id': graph_mod.stable_id('claim', {'node_id': node.node_id,
                                                        'run_ref': graph.run_ref or 'run-unspecified'}),
            'text': claim_text(node, value, supporting),
            'metric': payload.get('metric'),
            'value': None if node.numeric_value is None else str(node.numeric_value),
            'unit': node.unit,
            'normalized_value': None if value is None else format(value, 'f'),
            'normalized_unit': unit,
            'entity_id': node.entity_id,
            'entity_label': node.entity_label,
            'version': node.version,
            'time_scope': node.time_scope,
            'source_ref': node.source_ref,
            'source_kind': node.source_kind,
            'source_sha256': node.source_sha256,
            'binding': node.binding,
            'binding_status': 'bound' if bound else 'unbound',
            'binding_problems': problems,
            'entity_match_kind': payload.get('entity_match_kind'),
            # Kept next to ``binding_problems`` (which keep the raw ``unit:UNIT_UNKNOWN`` token so
            # the bounded sibling-unit repair still recognises it): the *reporting* layer turns it
            # into ``unsupported_unit``.
            'unsupported_unit': bool(payload.get('unsupported_unit')),
            'evidence_node_ids': sorted(set(supporting)),
            'node_id': node.node_id,
        })
    claims.sort(key=lambda item: item['claim_id'])
    return claims


def claim_text(node, value, supporting) -> str:
    label = node.entity_label or node.source_ref
    metric = node.payload.get('metric') or 'value'
    shown = node.payload.get('value')
    if value is not None and node.unit:
        return f'{label} 的 {metric} 为 {shown} {node.unit}'
    return f'{label} 的 {metric} 为 {shown}（单位未归一）'


#: A narrative document block only has to be bound by its *document identity*; it carries no
#: measurement, so unit and time scope are optional for it. A ``spec_clause`` (a rule) and any
#: numeric claim must carry the full seven-field binding.
_OPTIONAL_DOC_BLOCK_PROBLEMS = ('unit', 'unit:UNIT_UNKNOWN', 'time_scope')


def _doc_node_blocking_problems(node) -> list[str]:
    """Document-identity problems only: entity identity is handled by the linkage gate."""
    blocking = []
    for problem in node.binding_problems:
        if problem in ('entity_id', 'entity_label'):
            continue
        if node.node_type == 'doc_block' and problem in _OPTIONAL_DOC_BLOCK_PROBLEMS:
            continue
        # L2: report an unconvertible unit as exactly that, not as a missing binding field.
        if problem == 'unit:UNIT_UNKNOWN' and node.payload.get('unsupported_unit'):
            blocking.append('unsupported_unit')
            continue
        blocking.append(problem)
    return blocking


def _linked_to_sql(graph, node, depth: int = 3) -> bool:
    """True when the document node is bound to the data side through typed edges.

    A clause links to the data through ``version_scope``; the document block that states the
    clause links through ``supports`` to that clause. Traversal is bounded and cycle-safe, so a
    document node is only accepted when it reaches a SQL-side node over real edges.
    """
    seen = {node.node_id}
    frontier = [node.node_id]
    for _ in range(max(depth, 0)):
        next_frontier = []
        for current in frontier:
            for edge in graph.edges():
                if edge.edge_type not in ('entity_match', 'version_scope', 'supports'):
                    continue
                if edge.src == current:
                    other = edge.dst
                elif edge.dst == current:
                    other = edge.src
                else:
                    continue
                if other in seen:
                    continue
                seen.add(other)
                if graph.node(other).source_kind == 'sql':
                    return True
                next_frontier.append(other)
        frontier = next_frontier
        if not frontier:
            break
    return False


def version_mismatches(graph, sql_claims, doc_claims) -> list[str]:
    """A document claim that competes with a SQL claim on a *different* revision.

    Two sources may only be compared when they claim the same ``entity + metric + version`` —
    that is also the key the conflict gate groups by. A competing document value on another
    revision is refused as ``DOC_BINDING_MISSING``/``VERSION_MISMATCH`` instead of being answered:
    a specification clause binds a *rule* to a claim, it never makes two competing values of
    different revisions comparable.
    """
    problems = []
    for doc in doc_claims:
        for sql in sql_claims:
            if doc['metric'] != sql['metric'] or doc['version'] == sql['version']:
                continue
            if doc['entity_id'] and sql['entity_id'] and doc['entity_id'] != sql['entity_id']:
                continue
            problems.append(f'{doc["metric"]}:{sql["version"]}!={doc["version"]}')
    return sorted(set(problems))


def _entities_verified(claims, alias_registry_available: bool) -> bool:
    """False when an entity id rests on a surface literal *because* the registry was unusable.

    M4 (round-1 security review): with no approved-alias registry every label degrades to
    ``canonical_literal``, which is "same string" and not "same entity". Losing the registry is a
    registry-level fact, so the verdict is degraded for the whole run instead of only being noted
    in ``warnings``. A working registry that simply has no row for a label is a different fact and
    keeps the literal fallback.
    """
    if alias_registry_available:
        return True
    return not any(claim.get('entity_match_kind') == 'canonical_literal' for claim in claims)


def _problem_label(claim, problem: str) -> str:
    """Name an unregistered unit for what it is instead of hiding it behind ``UNIT_UNKNOWN``.

    L2 (round-1 security review): an adapter or clause may hand in a unit the table cannot convert.
    That is an *unsupported unit*, not a missing binding field, and the failure detail has to say so.
    """
    if problem == 'unit:UNIT_UNKNOWN' and claim.get('unsupported_unit'):
        return 'unsupported_unit'
    return problem


def _decide(graph, claims, conflicts, sql, docs, intent, repair_ledger, *, entity_verified: bool = True):
    doc_claims = [claim for claim in claims if claim['source_kind'] == 'doc']
    sql_claims = [claim for claim in claims if claim['source_kind'] == 'sql']
    doc_nodes = graph.nodes_of_type('doc_block') + graph.nodes_of_type('spec_clause')
    sql_nodes = graph.nodes_of_type('sql_row') + graph.nodes_of_type('measurement')
    if not sql['present']:
        return 'abstained', CROSS_SOURCE_EVIDENCE_INSUFFICIENT, 'SQL_EVIDENCE_EMPTY'
    if not docs['present']:
        return 'abstained', CROSS_SOURCE_EVIDENCE_INSUFFICIENT, 'DOC_EVIDENCE_EMPTY'
    if not sql_claims:
        return 'abstained', CROSS_SOURCE_EVIDENCE_INSUFFICIENT, 'NO_BOUNDABLE_SQL_CLAIM'
    unbound_sql = [claim for claim in sql_claims if claim['binding_status'] == 'unbound']
    if unbound_sql:
        return 'abstained', CROSS_SOURCE_EVIDENCE_INSUFFICIENT, \
            'SQL_CLAIM_BINDING_INCOMPLETE:' + ','.join(sorted(
                {_problem_label(claim, problem) for claim in unbound_sql
                 for problem in claim['binding_problems']}))
    if not entity_verified:
        # M4: identity is unproven, so nothing may be reported as verified — abstain, don't answer.
        return 'abstained', CROSS_SOURCE_EVIDENCE_INSUFFICIENT, \
            'ENTITY_UNVERIFIED:ALIAS_REGISTRY_UNAVAILABLE'
    unbound_doc = [claim for claim in doc_claims if claim['binding_status'] == 'unbound']
    if unbound_doc:
        problems = sorted({_problem_label(claim, problem) for claim in unbound_doc
                           for problem in claim['binding_problems']})
        return 'failed', DOC_BINDING_MISSING, 'DOC_CLAIM_BINDING_INCOMPLETE:' + ','.join(problems)
    mismatch = version_mismatches(graph, sql_claims, doc_claims)
    if mismatch:
        return 'failed', DOC_BINDING_MISSING, 'VERSION_MISMATCH:' + ';'.join(mismatch)
    # A document node whose *document identity* (version / sha256 / time scope) is incomplete is
    # a hard binding failure. An unbound entity is not: a specification clause applies to a class
    # of objects, and the linkage gate below decides whether it is actually bound to the data.
    #
    # Relevance gate (round 2, live): only a clause whose *metric* some data row carries can ever
    # take part in a version/threshold edge. A clause extracted from a retrieved block that names
    # no data metric cannot veto the run — live retrieval always returns such noise (the demo KB's
    # "超过 48 小时" sentence), and its missing validity window used to fail an otherwise complete
    # cross-source link. Relevant clauses keep the full seven-field discipline, unchanged.
    relevant_clause_ids = {node.node_id for node in graph.nodes_of_type('spec_clause')
                           if _clause_is_relevant(node, sql_nodes)}

    def _gates(node):
        return node.node_type != 'spec_clause' or node.node_id in relevant_clause_ids

    doc_problem_nodes = [{'node_id': node.node_id, 'problems': blocking}
                         for node in doc_nodes
                         if _gates(node) and (blocking := _doc_node_blocking_problems(node))]
    if doc_problem_nodes:
        problems = sorted({problem for item in doc_problem_nodes for problem in item['problems']})
        return 'failed', DOC_BINDING_MISSING, 'DOC_NODE_BINDING_INCOMPLETE:' + ','.join(problems)
    # Live linkage contract (round 2). A cross-source answer needs at least **one** document node
    # that reaches the data side over typed edges, and every *relevant* ``spec_clause`` must link: a
    # rule whose validity window/version does not apply to the data is a hard failure even when some
    # other document block does link (offline cs-p4-05 / cs-p2-05). Plain retrieved blocks that do
    # not link are *irrelevant context*, not a binding failure: real retrieval never returns only
    # the relevant chunk, so "every retrieved block must link" made the live path impossible
    # (8 chunks in, 7 unrelated -> hard DOC_BINDING_MISSING). They stay in the graph for audit,
    # carry no claim, and the answer may only cite the linked ones. With no linked document node at
    # all the run still fails closed with the same detail as before (offline cs-p1-06).
    linked_doc_nodes = [node for node in doc_nodes if _linked_to_sql(graph, node)]
    unlinked_clauses = [node for node in graph.nodes_of_type('spec_clause')
                        if node.node_id in relevant_clause_ids and not _linked_to_sql(graph, node)]
    if unlinked_clauses or not linked_doc_nodes:
        return 'failed', DOC_BINDING_MISSING, version_failure_detail(graph, sql_nodes, doc_nodes)
    if unresolved(conflicts):
        return 'abstained', CONFLICT_UNRESOLVED, ','.join(
            sorted({record['conflict_type'] for record in unresolved(conflicts)}))
    return 'completed', None, None


def _clause_is_relevant(clause, sql_nodes) -> bool:
    """True when some data node carries the clause's metric — only then can it bind to the data.

    A clause extracted from a *retrieved* document block (live retrieval always returns noise)
    whose metric no data row carries cannot take part in any ``version_scope``/threshold edge, so
    its own binding gaps must not veto an otherwise complete cross-source link.
    """
    metric = clause.payload.get('metric')
    return bool(metric) and any(node.payload.get('metric') == metric for node in sql_nodes)


def version_failure_detail(graph, sql_nodes, doc_nodes):
    """Explain *why* the document side could not be linked — never a generic message."""
    for clause in graph.nodes_of_type('spec_clause'):
        for node in sql_nodes or graph.nodes():
            if node.payload.get('metric') != clause.payload.get('metric'):
                continue
            applicable, why = clause_applies_to(clause, node)
            if not applicable:
                return f'VERSION_NOT_APPLICABLE:{why}'
    if any(_doc_node_blocking_problems(node) for node in doc_nodes):
        return 'DOC_BINDING_INCOMPLETE'
    return 'NO_DOC_EVIDENCE_LINKED'

def _repair_plan(graph, claims, sql, docs, aliases, detail=''):
    """At most one binding-only repair; returns ``(repair, ledger_entry)`` or ``(None, None)``.

    Only binding errors are repairable: an ambiguous/unbound entity (approved alias), a claim
    version that disagrees with the clause whose validity window covers it (adopt that version),
    or an unregistered unit that a sibling claim of the same metric already carries. A wrong
    document version whose validity window does **not** cover the claim yields no repair.
    """
    weak = [claim for claim in claims if claim['binding_status'] == 'unbound']
    for claim in weak:
        if 'entity_id' in claim['binding_problems']:
            for block in docs['blocks']:
                label = _entity_label(block)
                bound = binding_mod.entity_bind(label, aliases)
                if bound['entity_id']:
                    return ({'entity_id': bound['entity_id'], 'entity_label': bound['entity_label']},
                            {'attempt': 1, 'kind': 'entity_alias', 'target': claim['claim_id'],
                             'before': claim['entity_id'], 'after': bound['entity_id'],
                             'rule': 'approved_entity_alias', 'applied': True})
        if any(problem.startswith('unit:') for problem in claim['binding_problems']):
            for node in graph.nodes():
                if node.payload.get('metric') == claim['metric'] and node.unit and \
                        not binding_mod.unit_status(node.unit)['unit_unknown']:
                    return ({'unit': node.unit},
                            {'attempt': 1, 'kind': 'unit_from_sibling', 'target': claim['claim_id'],
                             'before': claim['unit'], 'after': node.unit,
                             'rule': 'same_metric_registered_unit', 'applied': True})
    # Version alignment is driven by the *link* failure, not by the claim's own binding: a claim
    # whose version disagrees with the applicable clause is repaired to that clause's version.
    for clause in graph.nodes_of_type('spec_clause'):
        for claim in claims:
            if claim['metric'] != clause.payload.get('metric'):
                continue
            node = graph.node(claim['node_id'])
            applicable, why = clause_applies_to(clause, node)
            if applicable and clause.version and clause.version != claim['version']:
                return ({'version': clause.version},
                        {'attempt': 1, 'kind': 'version_from_time_scope', 'target': claim['claim_id'],
                         'before': claim['version'], 'after': clause.version, 'rule': why,
                         'applied': True})
    target = weak[0]['claim_id'] if weak else None
    return (None, {'attempt': 1, 'kind': 'none_applicable', 'target': target,
                   'before': None, 'after': None,
                   'rule': 'NO_BINDING_REPAIR_AVAILABLE:' + (detail or 'UNKNOWN'),
                   'applied': False})


# ---------------------------------------------------------------- answer

def _answer_text(intent, claims, conflicts, checks, graph, status, error_code):
    if error_code == CONFLICT_UNRESOLVED:
        lines = [f'跨源核验发现 {len(unresolved(conflicts))} 处数值冲突，未登记权威规则，'
                 f'标记 {CONFLICT_UNRESOLVED}，保留双方原值：']
        for record in unresolved(conflicts):
            lines.append(f'- {record["entity_label"]} 的 {record["metric"]}（版本 {record["version"]}）：'
                         f'{record["left"]["value"]}{record["left"]["unit"] or ""}（{record["left"]["source_kind"]}）'
                         f' 与 {record["right"]["value"]}{record["right"]["unit"] or ""}（{record["right"]["source_kind"]}）'
                         f' 矛盾，冲突类型 {record["conflict_type"]}。')
        return '\n'.join(lines)
    if status != 'completed':
        return {'DOC_BINDING_MISSING': '文档证据无法绑定到数值声称（对象/版本/单位/时间口径不完整或不适用），'
                                       '已按 DOC_BINDING_MISSING 拒绝给出结论。',
                CROSS_SOURCE_EVIDENCE_INSUFFICIENT: '跨源证据不足（需要同时具备可绑定的数据库行与文档证据），'
                                                    '已按 CROSS_SOURCE_EVIDENCE_INSUFFICIENT 拒绝给出结论。'
                }.get(error_code, '跨源问题未能形成可核验的证据图，已拒绝给出结论。')
    structure = intent['structure']
    if structure == 'P2':
        violated = [check for check in checks if check.get('violation') is True]
        clause = graph.nodes_of_type('spec_clause')
        head = ''
        if clause:
            head = (f'按文档《{clause[0].payload.get("title") or clause[0].source_ref}》'
                    f'（版本 {clause[0].version}）的 {clause[0].payload.get("metric")} '
                    f'{clause[0].payload.get("op")} {clause[0].payload.get("limit")}'
                    f'{clause[0].unit or ""}，')
        return (f'{head}在数据库中筛出 {len(violated)} 条满足条件的记录，共 {len(claims)} 条可绑定声称；'
                f'逐条阈值判定见 threshold_checks。')
    if structure == 'P4':
        hops = []
        for node in graph.nodes():
            if node.node_type in ('sql_row', 'measurement'):
                hops.append(f'数据行 {node.node_id}（{node.entity_label} {node.metric}）')
            elif node.node_type == 'spec_clause':
                hops.append(f'规范条款 {node.node_id}（{node.metric} {node.payload.get("op")} '
                            f'{node.payload.get("limit")}{node.unit or ""}）')
        return (f'多跳跨源链路共 {len(graph.edges())} 条证据边：' + ' -> '.join(hops) +
                '。每一跳的证据边与绑定字段见 evidence_graph。')
    if structure == 'P3':
        return (f'跨源核验完成：{len(claims)} 条数值声称全部绑定；'
                f'{len(checks)} 条阈值判定，其中超限 {len([c for c in checks if c.get("violation") is True])} 条。')
    first = claims[0] if claims else None
    # Cite the document block that is actually *linked* into the verified graph; only fall back to
    # graph order when nothing links (that path does not reach a completed answer anyway). Quoting
    # the first retrieved chunk could cite an irrelevant one while the linked evidence stayed
    # invisible in the answer.
    doc = next((node for node in graph.nodes_of_type('doc_block') if _linked_to_sql(graph, node)),
               None) or next((node for node in graph.nodes_of_type('doc_block')), None)
    if first is None:
        return '跨源证据图已建立，但没有可绑定的数值声称。'
    quote = f'；文档《{doc.payload.get("title") or doc.source_ref}》说明：{doc.payload.get("text", "")[:120]}' \
        if doc else ''
    return (f'查询结果：{first["entity_label"]} 的 {first["metric"]} = {first["value"]} {first["unit"] or ""}'
            f'（来源 {first["source_ref"]}，版本 {first["version"]}）{quote}')


# ---------------------------------------------------------------- pipeline

def _scope_rejected_result(question, sources, document_collections) -> dict:
    """A well-formed fail-closed result for a request whose allow-list forbids everything.

    Returned before any adapter runs, so ``plan`` can never fall back to "pick a source" (H1).
    The shape mirrors the normal result so callers need no second code path.
    """
    return {'kind': 'cross_source', 'route': 'cross_source',
            'cross_source_version': CROSS_SOURCE_VERSION, 'question': question,
            'intent': parse_question(question), 'sources': list(sources or []),
            'document_collections': list(document_collections or []),
            'alias_registry_available': True,
            'sql_evidence': {'source_ref': None, 'snapshot_id': None, 'sql_hash': None,
                             'row_count': 0, 'present': False, 'reason': SOURCE_NOT_ALLOWED,
                             'evidence_sha256': None, 'projection': None},
            'document_evidence': {'block_count': 0, 'present': False,
                                  'reason': SOURCE_NOT_ALLOWED, 'documents': []},
            'authority_rule_count': 0, 'model_calls': 0,
            'status': 'failed', 'error_code': SOURCE_NOT_ALLOWED,
            'failure_detail': 'EMPTY_ALLOW_LIST',
            'answer': '当前请求范围不允许访问该数据源。',
            'claims': [], 'conflicts': [], 'unresolved_conflicts': [], 'threshold_checks': [],
            'repair_ledger': [], 'repair_attempts': 0, 'repair_applied': False,
            'link_counts': {}, 'graph_problems': [], 'graph_summary': {}, 'evidence_graph': {},
            'warnings': []}


def plan(question, sources=None, document_collections=None, *, sql_evidence=None,
         rag_evidence=None, aliases=None, authority_rules=None, source_label=None,
         repair_budget=1, on_graph=None, alias_registry_available: bool = True,
         run_ref=None) -> dict:
    """Run the full deterministic pipeline over injected evidence. Never calls a model.

    ``alias_registry_available=False`` means the approved-alias registry could not be read (as
    opposed to "this label has no approved alias"): see :func:`_entities_verified`. Refusing an
    *empty* allow-list is also done here, before any adapter seam can widen the scope (H1).
    """
    if sources is not None and not list(sources):
        return _scope_rejected_result(question, sources, document_collections)
    intent = parse_question(question)
    sql = sql_container(sql_evidence)
    docs = doc_container(rag_evidence)
    result = {'kind': 'cross_source', 'route': 'cross_source', 'cross_source_version': CROSS_SOURCE_VERSION,
              'question': question, 'intent': intent, 'sources': list(sources or []),
              'document_collections': list(document_collections or []),
              'alias_registry_available': alias_registry_available,
              'sql_evidence': {'source_ref': sql['source_ref'], 'snapshot_id': sql['snapshot_id'],
                               'sql_hash': sql['sql_hash'], 'row_count': len(sql['rows']),
                               'present': sql['present'], 'reason': sql['reason'],
                               'evidence_sha256': sql.get('evidence_sha256'),
                               'projection': sql.get('projection')},
              'document_evidence': {'block_count': len(docs['blocks']), 'present': docs['present'],
                                    'reason': docs['reason'],
                                    'documents': sorted({block.get('document_id') for block in
                                                         docs['blocks'] if block.get('document_id')})},
              'authority_rule_count': len(authority_rules or ()),
              'model_calls': 0}
    first = build_graph(question, intent, sql, docs, aliases, run_ref=run_ref)
    graph = first['graph']
    link_counts = link_graph(graph, aliases)
    checks = threshold_checks(graph)
    conflicts = detect_conflicts(graph, authority_rules)
    claims = build_claims(graph, first['claims_meta'], intent)
    entity_verified = _entities_verified(claims, alias_registry_available)
    status, error_code, detail = _decide(graph, claims, conflicts, sql, docs, intent, [],
                                         entity_verified=entity_verified)
    repair_ledger = []
    if repair_budget > 0 and error_code in (DOC_BINDING_MISSING, CROSS_SOURCE_EVIDENCE_INSUFFICIENT) \
            and (claims or graph.nodes()):
        repair, entry = _repair_plan(graph, claims, sql, docs, aliases, detail)
        if entry:
            repair_ledger.append(entry)
        if repair:
            second = build_graph(question, intent, sql, docs, aliases, repair=repair,
                                 run_ref=run_ref)
            graph, first = second['graph'], second
            link_counts = link_graph(graph, aliases)
            checks = threshold_checks(graph)
            conflicts = detect_conflicts(graph, authority_rules)
            claims = build_claims(graph, first['claims_meta'], intent)
            status, error_code, detail = _decide(graph, claims, conflicts, sql, docs, intent,
                                                 repair_ledger,
                                                 entity_verified=_entities_verified(
                                                     claims, alias_registry_available))
    answer = _answer_text(intent, claims, conflicts, checks, graph, status, error_code)
    problems = graph.validate()
    doc_nodes = graph.nodes_of_type('doc_block') + graph.nodes_of_type('spec_clause')
    linked_doc_ids = sorted(node.node_id for node in doc_nodes if _linked_to_sql(graph, node))
    ignored_doc_ids = sorted(node.node_id for node in doc_nodes
                             if node.node_id not in set(linked_doc_ids))
    result.update({'status': status, 'error_code': error_code, 'failure_detail': detail,
                   'answer': answer, 'claims': claims, 'conflicts': conflicts,
                   'unresolved_conflicts': unresolved(conflicts), 'threshold_checks': checks,
                   'repair_ledger': repair_ledger, 'repair_attempts': len(repair_ledger),
                   'repair_applied': bool([entry for entry in repair_ledger if entry.get('applied')]),
                   'link_counts': link_counts, 'graph_problems': problems,
                   # Honest accounting of the retrieval that did *not* become cross-source
                   # evidence: `linked` documents back the claims, `ignored` ones were retrieved
                   # but are not part of the verified link (they never enter the answer).
                   'doc_linkage': {'doc_nodes': len(doc_nodes), 'linked': len(linked_doc_ids),
                                   'ignored': len(ignored_doc_ids),
                                   'linked_node_ids': linked_doc_ids,
                                   'ignored_node_ids': ignored_doc_ids},
                   'graph_summary': graph.summary(),
                   'evidence_graph': graph.to_dict()})
    if on_graph is not None:
        # Persistence is a side channel: a store failure never rewrites the verification verdict,
        # it is reported honestly next to it.
        try:
            result['persistence'] = on_graph(graph)
        except Exception as exc:
            result['persistence_error'] = f'{type(exc).__name__}:{exc}'
    return result


def run(question, *, database_sources=None, document_collections=None, dsn=None, aliases=None,
        authority_rules=None, sql_adapter=None, doc_adapter=None, repair_budget=1,
        on_graph=None, run_ref=None) -> dict:
    """Live entry point: build real adapters, then call :func:`plan`."""
    warnings = []
    alias_registry_available = True
    if aliases is None:
        try:
            aliases = binding_mod.load_aliases(dsn)
        except Exception as exc:  # read-only registry unavailable -> no alias normalisation
            # M4 (round-1 security review): "the registry is unavailable" is not the same fact as
            # "this label has no approved alias". Continuing with ``aliases=[]`` let every entity
            # degrade to a surface-literal id and still be judged ``bound``. The flag travels into
            # ``plan`` so the verdict is degraded instead of silently trusting a string equality.
            aliases, alias_registry_available = [], False
            warnings.append(f'ALIAS_REGISTRY_UNAVAILABLE:{type(exc).__name__}')
    if authority_rules is None:
        try:
            authority_rules = load_authority_rules(dsn)
        except Exception as exc:
            authority_rules, warning = [], f'AUTHORITY_REGISTRY_UNAVAILABLE:{type(exc).__name__}'
            warnings.append(warning)
    sql_evidence = None
    if sql_adapter is not None:
        try:
            sql_evidence = sql_adapter()
        except Exception as exc:
            warnings.append(f'SQL_ADAPTER_FAILED:{type(exc).__name__}')
    else:
        try:
            sql_evidence = live_sql_evidence(question, database_sources)
        except CrossSourceError:
            # A refused scope is a verdict, not an adapter failure: it must reach the caller with
            # its own stable code (H1) instead of becoming "evidence insufficient".
            raise
        except Exception as exc:
            warnings.append(f'SQL_ADAPTER_FAILED:{type(exc).__name__}')
    doc_evidence = None
    if doc_adapter is not None:
        try:
            doc_evidence = doc_adapter()
        except Exception as exc:
            warnings.append(f'DOC_ADAPTER_FAILED:{type(exc).__name__}')
    else:
        try:
            # P1: the entity the data side actually returned anchors the document retrieval.
            doc_evidence = live_doc_evidence(question, document_collections,
                                             anchors=sql_entity_anchors(sql_evidence))
        except Exception as exc:
            warnings.append(f'DOC_ADAPTER_FAILED:{type(exc).__name__}')
    result = plan(question, database_sources, document_collections, sql_evidence=sql_evidence,
                  rag_evidence=doc_evidence, aliases=aliases, authority_rules=authority_rules,
                  alias_registry_available=alias_registry_available,
                  repair_budget=repair_budget, on_graph=on_graph, run_ref=run_ref)
    if isinstance(doc_evidence, dict) and doc_evidence.get('warnings'):
        warnings.extend(doc_evidence['warnings'])
    result['warnings'] = warnings
    return result


def load_authority_rules(dsn=None) -> list[dict]:
    """Read-only view of the registered authority rules (``source_authority_rules``)."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    if dsn is None:
        from .sql_engine import settings
        dsn = settings().get('DATAMIND_META_DSN') or (
            'host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev')
    with psycopg2.connect(dsn) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT rule_id, payload, payload_sha256 FROM source_authority_rules '
                        'ORDER BY created_at, rule_id LIMIT 1001')
            rows = [dict(row) for row in cur.fetchall()]
    return rows


#: Sentence head markers of a *data* request and tail markers of a *document* request.
_SQL_INTENT_MARKERS = ('最高', '最多', '最少', '最低', '哪个', '哪些', '多少', '排名', '前三', '前五',
                       '找出', '统计', '汇总', '趋势', '占比', 'TOP', 'top')
_DOC_TAIL_MARKERS = ('手册', '说明', '制度', '规范', '报告', '材料', '白皮书', '条款', '规定', '《')


def sql_scope(question: str) -> str:
    """The data part of a cross-source question — what the NL->SQL side may be asked.

    A cross-source question is two questions: a data request and a document request. Handing both
    to the SQL planner made it answer with ``clarification_required`` (the document half is not
    SQL-answerable), which the live path can only report as "no SQL evidence". Deterministically
    dropping a trailing *document-only* sentence — the head already carries the data intent and the
    tail only asks about a document — keeps the projection honest and does not change what the data
    side must compute. Anything that is not such a clean two-sentence shape is passed through
    unchanged (no guessing about the user's intent).
    """
    text = str(question or '').strip()
    parts = [part for part in re.split(r'(?<=[。？?！!])', text) if part.strip()]
    if len(parts) < 2:
        return text
    head, tail = parts[0], ''.join(parts[1:])
    if any(marker in head for marker in _SQL_INTENT_MARKERS) and \
            any(marker in tail for marker in _DOC_TAIL_MARKERS):
        return head.rstrip('。？?！! ').strip() or text
    return text


#: Question dimension word -> the catalog column names that carry it. The live projection uses this
#: to name the entity of a real row; the model aliases its projection freely (``channel`` one run,
#: ``渠道`` the next), so the column name alone is not a contract.
_ENTITY_COLUMN_HINTS = (
    ('品类', ('category',)), ('类别', ('category',)), ('类目', ('category',)),
    ('渠道', ('channel',)), ('商品', ('product_name',)), ('产品', ('product_name',)),
    ('区域', ('region_name', 'region')), ('大区', ('region_name', 'region')),
    ('地区', ('region_name', 'region')),
    ('等级', ('customer_level', 'level')), ('会员', ('customer_level', 'level')),
    ('品牌', ('brand',)), ('批次', ('batch_id',)), ('工序', ('operation_no',)),
    ('退款单', ('refund_id',)), ('订单', ('order_id',)),
)
#: Column-name tokens that never name the entity of a cross-source claim.
_ENTITY_COLUMN_EXCLUDE = ('time', 'date', '_at', '日期', '时间', 'remark', 'remark',
                          'description', '描述', '备注', 'count', 'amount', '金额', '数量')


def _is_number(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        float(str(value).strip().replace(',', ''))
        return True
    except (TypeError, ValueError):
        return False


def _entity_column(question, columns, value_column, rows):
    """Which returned column carries the entity the question asked about (or ``None``).

    Conservative on purpose: first the question's own dimension word must point at a column, and
    only when exactly **one** non-numeric, non-time column remains is that used. With two candidate
    dimensions and no question hint the projection refuses to guess — the claim then fails the
    entity binding and the run abstains, exactly as before.
    """
    lowered = [(str(column).lower(), column) for column in columns]
    text = str(question or '')
    for word, names in _ENTITY_COLUMN_HINTS:
        if word not in text:
            continue
        for name in names:
            for low, column in lowered:
                if column == value_column:
                    continue
                if low == name or name in low:
                    return column
    candidates = []
    for index, column in enumerate(columns):
        if column == value_column:
            continue
        low = str(column).lower()
        if any(token in low for token in _ENTITY_COLUMN_EXCLUDE):
            continue
        if any(row[index] is not None and not _is_number(row[index]) for row in rows):
            candidates.append(column)
    return candidates[0] if len(candidates) == 1 else None


def live_sql_evidence(question, sources) -> dict:
    """SQL side of the live path. The projection is inferred explicitly and recorded."""
    from .sql_engine import ask
    if sources is not None and not list(sources):
        # Second line of defence behind ``DataMindAgent._run_cross_source``: an explicitly empty
        # allow-list means "no source is allowed". ``(sources or [None])[0]`` used to turn ``[]``
        # into ``None`` and let ``source_for`` pick a source on its own (H1).
        raise CrossSourceError(SOURCE_NOT_ALLOWED, 'EMPTY_ALLOW_LIST')
    source = sources[0] if sources else None
    sql_question = sql_scope(question)
    result = ask(sql_question, source)
    if result.get('status') != 'completed':
        return {'source_ref': source, 'snapshot_id': result.get('source_snapshot_id'),
                'sql_hash': result.get('sql_hash'), 'columns': [], 'rows': [],
                'projection': {'status': result.get('status'),
                               'error_code': result.get('error_code'),
                               'sql_question': sql_question}}
    columns, rows = result['columns'], result['rows']
    numeric = [index for index, column in enumerate(columns)
               if any(str(row[index]).replace('.', '', 1).replace('-', '', 1).isdigit()
                      for row in rows if row[index] is not None)]
    value_column = columns[numeric[0]] if numeric else None
    projection = {'value_column': value_column,
                  'entity_column': _entity_column(question, columns, value_column, rows),
                  'metric': (parse_question(question)['metrics'] or [None])[0],
                  'unit': metric_unit_hint(question), 'time_scope': _frame_scope(result),
                  'sql_question': sql_question}
    mapped = []
    for row in rows:
        item = {column: row[index] for index, column in enumerate(columns)}
        item['metric'] = projection['metric']
        if projection['value_column']:
            item['value'] = item.get(projection['value_column'])
        if projection['entity_column']:
            # Explicit entity label of the real row: `build_graph` reads it before any column-name
            # heuristic, so a Chinese projection alias cannot lose the entity identity.
            item['entity_label'] = item.get(projection['entity_column'])
        if projection['unit']:
            item['unit'] = projection['unit']
        if projection['time_scope']:
            item['time_scope'] = projection['time_scope']
        mapped.append(item)
    return {'source_ref': source or result.get('source_id'), 'snapshot_id': result.get('source_snapshot_id'),
            'sql': result.get('sql'), 'sql_hash': result.get('sql_hash'), 'columns': list(columns),
            'rows': mapped, 'projection': projection}


def metric_unit_hint(question):
    for word, unit in _METRIC_UNIT_HINTS:
        if word in str(question or ''):
            return unit
    return None


def _frame_scope(result):
    """Time scope of the SQL result, timezone-aware whenever the frame carries a timezone.

    M1 (round-1 security review): ``sql_engine.query_frame`` emits bare dates (``from`` / ``to``)
    and keeps the timezone in a *sibling* key (``timezone``). Reading only ``from``/``to`` dropped
    it, so ``binding.timestamp`` refused the naive value (``TIMEZONE_REQUIRED``) and every live
    cross-source question with a time filter abstained. The discipline is unchanged: when the
    frame carries no timezone the value stays naive and is still refused downstream.
    """
    frame = result.get('query_frame') or {}
    filters = frame.get('time_filters') or []
    start = end = timezone_name = None
    for item in filters:
        if not isinstance(item, dict):
            continue
        start = start or item.get('start') or item.get('from') or item.get('value')
        end = end or item.get('end') or item.get('to')
        timezone_name = timezone_name or item.get('timezone')
    if start is None and end is None:
        return None
    return {'start': _aware_iso(start, timezone_name), 'end': _aware_iso(end, timezone_name)}


def _aware_iso(value, timezone_name):
    """Attach ``timezone_name`` to a bare date/datetime; leave an explicit offset untouched.

    An unusable timezone name is *not* guessed at: the value is passed through unchanged and the
    binding gate refuses it, so the failure stays a refusal rather than a silent default.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.isoformat()
        zone = _zone(timezone_name)
        return value.isoformat() if zone is None else value.replace(tzinfo=zone).isoformat()
    text = str(value)
    if not timezone_name:
        return text
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        return text
    zone = _zone(timezone_name)
    return text if zone is None else parsed.replace(tzinfo=zone).isoformat()


def _zone(timezone_name):
    if not timezone_name:
        return None
    try:
        return ZoneInfo(str(timezone_name))
    except Exception:
        return None


#: Upper bound on the document blocks handed to the graph. Real retrieval always returns more than
#: the one relevant chunk; the graph keeps them for audit and the linkage gate only accepts the
#: ones that actually reach the data side.
_MAX_LIVE_DOC_BLOCKS = 24


def sql_entity_anchors(sql_evidence) -> list:
    """Entity surface labels of the SQL projection — the anchors of the P1 document retrieval."""
    if not isinstance(sql_evidence, dict):
        return []
    columns = list(sql_evidence.get('columns') or [])
    anchors = []
    for row in sql_evidence.get('rows') or []:
        label = _entity_label(_as_dict(row, columns), columns)
        if label and str(label) not in anchors:
            anchors.append(str(label))
    return anchors[:3]


#: The frozen normative corpus shipped with the repository (``data/knowledge_sources``). It is
#: parsed from disk at call time; nothing is written to the knowledge base.
NORM_CORPUS_SUFFIXES = ('.pdf', '.docx')


def norm_corpus_evidence(paths=None, *, document_ids=None, must_tokens=(), any_tokens=(),
                         max_blocks=_MAX_LIVE_DOC_BLOCKS) -> dict:
    """Document side of the *frozen norm corpus* path — an additive adapter seam.

    Round 2 established that the public knowledge base cannot support P2/P3/P4 (of 169 chunks one
    is regex-extractable and it names no queryable metric). The corpus built by
    ``data/knowledge_sources/build_norm_corpus.py`` carries clauses with a threshold, a unit, an
    applicable revision/validity window and an applicable scope, and the transcription of the
    repository's own industrial PDF.

    This function only *projects* that corpus into the container shape :func:`live_doc_evidence`
    already returns, using the repository's PDF (``ingestion.ingest.parse_pdf``) and DOCX
    (``ingestion.office.parse_docx``) parsers through :mod:`ingestion.norm_facts`. The binding,
    linkage, threshold and verdict machinery is untouched: the blocks carry the same labelled
    fields the engine's ``extract_clause`` already understands, and the numbers are read from the
    files, never accepted as arguments.
    """
    import pathlib
    from ingestion import norm_facts
    if paths is None:
        directory = pathlib.Path(__file__).resolve().parents[1] / 'data' / 'knowledge_sources'
        paths = sorted(path for path in directory.iterdir()
                       if path.suffix.lower() in NORM_CORPUS_SUFFIXES)
    return norm_facts.evidence_blocks(paths, document_ids=document_ids,
                                      must_tokens=must_tokens, any_tokens=any_tokens,
                                      max_blocks=max_blocks)


def live_doc_evidence(question, document_collections, anchors=None) -> dict:
    """Document side of the live path: retrieval only, no model call.

    P1 is "SQL 结果驱动文档检索": the retrieval is anchored on the entity the data side actually
    returned (``anchors``), not only on the whole cross-source question (which mixes the data part
    and the document part). Each query is issued separately and the hits are unioned by
    ``chunk_id``; which query produced which block is recorded, so the provenance of the evidence
    stays inspectable instead of becoming one opaque pool.
    """
    from .knowledge import KnowledgeRetriever
    retriever = KnowledgeRetriever()
    queries = [str(question)]
    for anchor in anchors or ():
        text = str(anchor or '').strip()
        if text and text not in question and text not in queries:
            queries.append(text)
    blocks = []
    seen_chunk_ids = set()
    warnings = []
    for query in queries:
        try:
            retrieved = retriever.retrieve(query, collections=document_collections, limit=8)
        except Exception as exc:
            # One unusable anchor must not lose the whole document side; it is reported instead.
            warnings.append(f'DOC_ANCHOR_RETRIEVAL_FAILED:{query}:{type(exc).__name__}')
            continue
        for hit in retrieved.get('hits', []):
            chunk_id = hit.get('chunk_id')
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            blocks.append({'document_id': hit.get('document_id'), 'title': hit.get('title'),
                           'collection': hit.get('collection'),
                           'document_version': hit.get('document_version'),
                           'source_sha256': hit.get('document_sha256'),
                           'chunk_id': chunk_id, 'page_no': hit.get('page_start'),
                           'text': hit.get('exact_text') or '',
                           'retrieval_query': query,
                           'time_scope': None})
        if len(blocks) >= _MAX_LIVE_DOC_BLOCKS:
            break
    return {'source_ref': 'knowledge_base', 'blocks': blocks, 'queries': queries,
            'warnings': warnings}
