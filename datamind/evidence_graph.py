"""Typed evidence graph for cross-source answering.

Nodes are *evidence*, never conclusions: ``sql_row`` / ``doc_block`` / ``measurement`` /
``spec_clause``. Edges are typed relations between them: ``entity_match`` / ``version_scope`` /
``unit_equivalent`` / ``time_overlap`` / ``supports`` / ``conflicts``. Claims are carried by the
caller (``datamind.cross_source``) and reference node ids, so the node vocabulary stays closed.

The module is deliberately deterministic: node ids are content hashes, serialisation sorts every
collection with ``sort_keys=True`` and no whitespace, and :meth:`EvidenceGraph.digest` is a
sha256 over that serialisation. Two runs over the same evidence produce the same graph id.

Conflicts are decided here and only here. Two numeric claims that belong to the same
``entity_id + metric + version`` and the same *named record* but come from different sources and
disagree produce a ``conflicts`` edge that keeps **both original values**. If no *registered*
authority rule (``source_authority_rules`` semantics, read-only) covers the pair, the record is
``CONFLICT_UNRESOLVED`` and no side is selected. A registered rule may select a side, but the
conflict edge and both values are still retained.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from . import binding as _binding

GRAPH_VERSION = 'evidence-graph-v1'

NODE_TYPES = ('sql_row', 'doc_block', 'measurement', 'spec_clause')
EDGE_TYPES = ('entity_match', 'version_scope', 'unit_equivalent', 'time_overlap', 'supports',
              'conflicts')

#: Which side of an industrial authority rule each source kind is. ``production`` is the SQL
#: side, ``report`` the document side; this mirrors ``datamind.authority`` exactly.
AUTHORITY_SIDES = {'sql': 'production', 'doc': 'report'}
SIDE_SOURCE_KIND = {'production': 'sql', 'report': 'doc'}

#: Relations that are semantically symmetric. They are stored in one canonical orientation
#: (smaller node id first) so the same evidence always serialises to the same graph, and they do
#: not count as directed edges for cycle detection.
SYMMETRIC_EDGE_TYPES = ('entity_match', 'unit_equivalent', 'time_overlap', 'conflicts')
DIRECTIONAL_EDGE_TYPES = ('version_scope', 'supports')

CONFLICT_UNRESOLVED = 'CONFLICT_UNRESOLVED'


class GraphError(ValueError):
    """A structurally invalid graph operation; ``code`` is stable."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code + (':' + detail if detail else ''))
        self.code = code
        self.detail = detail


def stable_id(prefix: str, payload) -> str:
    """Deterministic content-addressed id (``prefix`` + first 20 hex of the canonical sha256)."""
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=False, default=str).encode()).hexdigest()
    return f'{prefix}-{digest[:20]}'


def canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      default=str)


class EvidenceNode:
    """One piece of evidence. ``binding`` is the seven-field record from ``datamind.binding``."""

    __slots__ = ('node_id', 'node_type', 'ref', 'source_sha256', 'version', 'unit', 'time_scope',
                 'binding', 'payload', 'binding_problems')

    def __init__(self, node_id, node_type, ref, *, source_sha256=None, version=None, unit=None,
                 time_scope=None, binding=None, payload=None):
        if node_type not in NODE_TYPES:
            raise GraphError('NODE_TYPE_NOT_ALLOWED', str(node_type))
        if not str(ref or '').strip():
            raise GraphError('NODE_REF_EMPTY', str(ref))
        self.node_id = node_id
        self.node_type = node_type
        self.ref = str(ref)
        self.source_sha256 = source_sha256
        self.version = version
        self.unit = unit
        self.time_scope = time_scope
        self.binding = binding
        self.payload = payload or {}
        self.binding_problems = _binding.binding_problems(binding) if binding else ['binding']

    @property
    def source_kind(self) -> str:
        return str(self.payload.get('source_kind') or ('doc' if self.node_type in
                                                       ('doc_block', 'spec_clause') else 'sql'))

    @property
    def source_ref(self):
        return self.payload.get('source_ref') or self.ref

    @property
    def metric(self):
        return self.payload.get('metric')

    @property
    def entity_id(self):
        return (self.binding or {}).get('entity_id') or self.payload.get('entity_id')

    @property
    def entity_label(self):
        return (self.binding or {}).get('entity_label') or self.payload.get('entity_label')

    @property
    def numeric_value(self):
        raw = self.payload.get('value')
        if raw is None:
            return None
        try:
            number = Decimal(str(raw))
        except Exception:
            return None
        return number if number.is_finite() else None

    @property
    def comparable_value(self):
        """The number this node contributes to a unit comparison: its value, else its limit."""
        if self.numeric_value is not None:
            return self.numeric_value
        limit = self.payload.get('limit')
        if limit is None:
            return None
        try:
            number = Decimal(str(limit))
        except Exception:
            return None
        return number if number.is_finite() else None

    def normalized(self):
        """``(Decimal, canonical unit)`` for this node's own comparable value, else ``(None, None)``."""
        value = self.comparable_value
        if value is None:
            return (None, None)
        target = _binding.unit_status(self.unit)
        if target['unit_unknown']:
            return (None, None)
        base = _binding.base_unit(self.unit)
        if base is None:
            return (None, None)
        return (_binding.normalize_unit(self.comparable_value, self.unit, base), base)

    def to_dict(self) -> dict:
        return {'node_id': self.node_id, 'node_type': self.node_type, 'ref': self.ref,
                'source_kind': self.source_kind, 'source_sha256': self.source_sha256,
                'version': self.version, 'unit': self.unit, 'time_scope': self.time_scope,
                'binding': self.binding, 'binding_problems': self.binding_problems,
                'payload': self.payload}


class EvidenceEdge:
    __slots__ = ('edge_id', 'src', 'dst', 'edge_type', 'binding_type', 'confidence', 'evidence')

    def __init__(self, src, dst, edge_type, *, evidence, binding_type=None, confidence=None):
        if edge_type not in EDGE_TYPES:
            raise GraphError('EDGE_TYPE_NOT_ALLOWED', str(edge_type))
        if not str(evidence or '').strip():
            raise GraphError('EDGE_EVIDENCE_EMPTY', f'{src}->{dst}:{edge_type}')
        if src == dst:
            raise GraphError('EDGE_SELF_REFERENCE', f'{src}->{dst}:{edge_type}')
        self.src = src
        self.dst = dst
        self.edge_type = edge_type
        self.binding_type = binding_type
        self.confidence = confidence
        self.evidence = str(evidence)
        self.edge_id = stable_id('e', {'src': src, 'dst': dst, 'edge_type': edge_type})

    def to_dict(self) -> dict:
        return {'edge_id': self.edge_id, 'src': self.src, 'dst': self.dst,
                'edge_type': self.edge_type, 'binding_type': self.binding_type,
                'confidence': None if self.confidence is None else str(self.confidence),
                'evidence': self.evidence}


class EvidenceGraph:
    """Mutable builder + deterministic serialiser. ``validate()`` never mutates."""

    def __init__(self, run_ref: str | None = None):
        self.run_ref = run_ref
        self._nodes: dict[str, EvidenceNode] = {}
        self._edges: dict[tuple, EvidenceEdge] = {}

    # ---------------------------------------------------------------- building
    def add_node(self, node: EvidenceNode) -> EvidenceNode:
        existing = self._nodes.get(node.node_id)
        if existing is not None:
            if canonical_json(existing.to_dict()) != canonical_json(node.to_dict()):
                raise GraphError('NODE_ID_COLLISION', node.node_id)
            return existing
        self._nodes[node.node_id] = node
        return node

    def make_node(self, node_type, ref, **kwargs) -> EvidenceNode:
        node_id = kwargs.pop('node_id', None) or stable_id(
            'n', {'node_type': node_type, 'ref': ref,
                  'source_ref': kwargs.get('payload', {}).get('source_ref')})
        return self.add_node(EvidenceNode(node_id, node_type, ref, **kwargs))

    def add_edge(self, src, dst, edge_type, *, evidence, binding_type=None, confidence=None):
        if src not in self._nodes:
            raise GraphError('EDGE_SRC_UNKNOWN', src)
        if dst not in self._nodes:
            raise GraphError('EDGE_DST_UNKNOWN', dst)
        if edge_type in SYMMETRIC_EDGE_TYPES and src > dst:
            src, dst = dst, src
        edge = EvidenceEdge(src, dst, edge_type, evidence=evidence, binding_type=binding_type,
                            confidence=confidence)
        self._edges.setdefault((src, dst, edge_type), edge)
        return self._edges[(src, dst, edge_type)]

    # ---------------------------------------------------------------- reading
    def node(self, node_id) -> EvidenceNode:
        return self._nodes[node_id]

    def nodes(self) -> list[EvidenceNode]:
        return [self._nodes[key] for key in sorted(self._nodes)]

    def edges(self) -> list[EvidenceEdge]:
        return [self._edges[key] for key in sorted(self._edges)]

    def nodes_of_type(self, node_type) -> list[EvidenceNode]:
        return [node for node in self.nodes() if node.node_type == node_type]

    def edges_of_type(self, edge_type) -> list[EvidenceEdge]:
        return [edge for edge in self.edges() if edge.edge_type == edge_type]

    def edge_between(self, src, dst, edge_type):
        """The edge joining ``src`` and ``dst`` with this ``edge_type``, or ``None``.

        ``add_edge`` stores every :data:`SYMMETRIC_EDGE_TYPES` relation in one canonical
        orientation (smaller node id first), so a symmetric relation must be looked up in *both*
        orientations: ``edge_between(a, b, 'entity_match')`` and
        ``edge_between(b, a, 'entity_match')`` name the same edge. Directional relations
        (:data:`DIRECTIONAL_EDGE_TYPES`) keep their exact orientation, so a backwards lookup of
        ``supports`` / ``version_scope`` still returns ``None``.
        """
        edge = self._edges.get((src, dst, edge_type))
        if edge is None and edge_type in SYMMETRIC_EDGE_TYPES:
            edge = self._edges.get((dst, src, edge_type))
        return edge

    def neighbors(self, node_id) -> list[str]:
        out = []
        for edge in self.edges():
            if edge.src == node_id:
                out.append(edge.dst)
            elif edge.dst == node_id:
                out.append(edge.src)
        return sorted(set(out))

    # ---------------------------------------------------------------- validation
    def validate(self) -> list[dict]:
        """Topology + vocabulary check. Returns problems (empty list == valid)."""
        problems: list[dict] = []
        node_ids = set(self._nodes)
        for node in self.nodes():
            if node.node_type not in NODE_TYPES:
                problems.append({'code': 'NODE_TYPE_NOT_ALLOWED', 'node_id': node.node_id,
                                 'value': node.node_type})
            if not node.ref.strip():
                problems.append({'code': 'NODE_REF_EMPTY', 'node_id': node.node_id})
            if not isinstance(node.payload, dict):
                problems.append({'code': 'NODE_PAYLOAD_NOT_OBJECT', 'node_id': node.node_id})
        for edge in self.edges():
            if edge.edge_type not in EDGE_TYPES:
                problems.append({'code': 'EDGE_TYPE_NOT_ALLOWED', 'edge_id': edge.edge_id,
                                 'value': edge.edge_type})
            if edge.src not in node_ids:
                problems.append({'code': 'EDGE_DANGLING_SRC', 'edge_id': edge.edge_id})
            if edge.dst not in node_ids:
                problems.append({'code': 'EDGE_DANGLING_DST', 'edge_id': edge.edge_id})
            if edge.src == edge.dst:
                problems.append({'code': 'EDGE_SELF_REFERENCE', 'edge_id': edge.edge_id})
            if not edge.evidence.strip():
                problems.append({'code': 'EDGE_EVIDENCE_EMPTY', 'edge_id': edge.edge_id})
        problems.extend(self._cycle_problems())
        return problems

    def _cycle_problems(self) -> list[dict]:
        """Cycles over the *directional* relations only (``supports`` / ``version_scope``)."""
        adjacency: dict[str, list[str]] = {}
        for edge in self.edges():
            if edge.edge_type not in DIRECTIONAL_EDGE_TYPES:
                continue
            adjacency.setdefault(edge.src, []).append(edge.dst)
        state: dict[str, int] = {}
        problems = []

        def visit(node_id, stack):
            state[node_id] = 1
            for target in sorted(adjacency.get(node_id, [])):
                if state.get(target) == 1:
                    path = stack[stack.index(target):] + [target] if target in stack else [node_id, target]
                    problems.append({'code': 'EDGE_CYCLE', 'path': path})
                    continue
                if state.get(target) is None:
                    visit(target, stack + [target])
            state[node_id] = 2

        for node_id in sorted(adjacency):
            if state.get(node_id) is None:
                visit(node_id, [node_id])
        return problems

    def assert_valid(self) -> None:
        problems = self.validate()
        if problems:
            raise GraphError('GRAPH_INVALID', canonical_json(problems))

    # ---------------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        return {'graph_version': GRAPH_VERSION, 'run_ref': self.run_ref,
                'node_types': list(NODE_TYPES), 'edge_types': list(EDGE_TYPES),
                'nodes': [node.to_dict() for node in self.nodes()],
                'edges': [edge.to_dict() for edge in self.edges()]}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def summary(self) -> dict:
        return {'graph_version': GRAPH_VERSION, 'graph_digest': self.digest(),
                'node_count': len(self._nodes), 'edge_count': len(self._edges),
                'node_types': sorted({node.node_type for node in self.nodes()}),
                'edge_types': sorted({edge.edge_type for edge in self.edges()})}


# ---------------------------------------------------------------- conflicts

def record_scope(node: EvidenceNode):
    """The single record a node speaks about, when it names one; else ``None``.

    A document-side claim carries the measurement it transcribes (``measurement_id`` from the
    labelled parser); a data-side node's ``ref`` *is* its row identity (``measurement_id`` /
    ``row_id`` in ``datamind.cross_source.build_graph``). ``None`` means "no record named", which
    stays comparable so evidence without record ids keeps its previous behaviour.
    """
    for key in ('record_id', 'measurement_id'):
        value = node.payload.get(key)
        if value not in (None, ''):
            return str(value)
    if node.source_kind == 'sql':
        return str(node.ref) if str(node.ref or '').strip() else None
    return None


def _named_records_differ(left: EvidenceNode, right: EvidenceNode) -> bool:
    """True when both sides name a record and the named records are different.

    Two different records are two different claims: the value a report states for measurement
    ``M006-2`` must never be paired with the database row of ``M011-1``. This is the round-4
    defect-B fix; only pairs where *both* sides name a record are separated, so a claim that
    carries no record id stays comparable exactly as before.
    """
    left_scope, right_scope = record_scope(left), record_scope(right)
    return bool(left_scope and right_scope and left_scope != right_scope)


def _value_side(node: EvidenceNode, target: str) -> dict:
    value, unit = node.normalized()
    return {'node_id': node.node_id, 'node_type': node.node_type,
            'source_kind': node.source_kind, 'source_ref': node.source_ref,
            'value': None if node.numeric_value is None else str(node.numeric_value),
            'unit': node.unit, 'normalized_value': None if value is None else format(value, 'f'),
            'normalized_unit': unit, 'source_sha256': node.source_sha256,
            'version': node.version, 'time_scope': node.time_scope, 'target': target,
            'record_scope': record_scope(node)}


def _authority_match(rule: dict, entity_id, metric) -> bool:
    payload = rule.get('payload') if isinstance(rule.get('payload'), dict) else rule
    if not isinstance(payload, dict):
        return False
    context = payload.get('context') or {}
    scope = context.get('scope') or {}
    if metric is not None and scope.get('metric') not in (None, metric):
        return False
    identifiers = {scope.get('part_id'), context.get('entity_id'), payload.get('entity_id')}
    identifiers.discard(None)
    if identifiers and entity_id is not None and entity_id not in identifiers:
        return False
    return payload.get('selected_source') in SIDE_SOURCE_KIND


def detect_conflicts(graph: EvidenceGraph, authority_rules=None, *, annotate: bool = True) -> list[dict]:
    """Find contradictory numeric claims and (optionally) record ``conflicts`` edges.

    A pair conflicts when both nodes carry a finite numeric value, belong to the same
    ``(entity_id, metric, version)`` key, come from different sources, **name the same record
    (or one of them names none)**, and either their units are not comparable or their normalised
    values differ. Both original values are always retained.
    """
    records: list[dict] = []
    seen: set[tuple] = set()
    buckets: dict[tuple, list[EvidenceNode]] = {}
    for node in graph.nodes():
        if node.numeric_value is None or node.metric is None or node.version is None:
            continue
        key = (node.entity_id, str(node.metric), str(node.version))
        buckets.setdefault(key, []).append(node)
    for key in sorted(buckets, key=lambda item: tuple(str(part) for part in item)):
        candidates = buckets[key]
        for index, left in enumerate(candidates):
            for right in candidates[index + 1:]:
                if left.source_ref == right.source_ref and left.source_kind == right.source_kind:
                    continue
                # Defect B (round 4): two *different* named records are two different claims, so
                # they are never paired. Without this, the report value of M006-2 was compared
                # with the database row M011-1 (same entity/metric/version) and produced false
                # ``value_conflict`` records that turned an agreeing verification into an
                # unjustified ``CONFLICT_UNRESOLVED`` abstention.
                if _named_records_differ(left, right):
                    continue
                pair = (left.node_id, right.node_id)
                if pair in seen:
                    continue
                seen.add(pair)
                # Display order is stable and meaningful: the data side first, then by node id.
                ordered = sorted((left, right), key=lambda item: (
                    0 if item.source_kind == 'sql' else 1, item.node_id))
                left, right = ordered[0], ordered[1]
                comparable = _binding.units_equivalent(left.unit, right.unit)
                if comparable:
                    left_value, left_unit = left.normalized()
                    right_value, right_unit = right.normalized()
                    if left_value is None or right_value is None:
                        comparable = False
                if not comparable:
                    conflict_type = 'unit_incomparable'
                    reason = 'UNITS_NOT_COMPARABLE'
                else:
                    if left_value == right_value:
                        continue
                    conflict_type = 'value_conflict'
                    reason = 'NO_REGISTERED_AUTHORITY_RULE'
                record = {'conflict_id': stable_id('conf', {'entity_id': key[0], 'metric': key[1],
                                                            'version': key[2],
                                                            'nodes': sorted(pair)}),
                          'entity_id': key[0], 'entity_label': left.entity_label or right.entity_label,
                          'metric': key[1], 'version': key[2], 'conflict_type': conflict_type,
                          'resolution_status': CONFLICT_UNRESOLVED, 'rule_id': reason,
                          'authority_rule_id': None, 'selected_node_id': None,
                          'left': _value_side(left, left.unit), 'right': _value_side(right, right.unit)}
                for rule in authority_rules or ():
                    if not _authority_match(rule, key[0], key[1]):
                        continue
                    payload = rule.get('payload') if isinstance(rule.get('payload'), dict) else rule
                    side = payload.get('selected_source')
                    wanted = SIDE_SOURCE_KIND.get(side)
                    chosen = left if left.source_kind == wanted else right if right.source_kind == wanted else None
                    if chosen is None:
                        continue
                    record['resolution_status'] = 'resolved_by_registered_authority'
                    record['rule_id'] = 'REGISTERED_AUTHORITY_RULE'
                    record['authority_rule_id'] = rule.get('rule_id') or payload.get('rule_id')
                    record['selected_node_id'] = chosen.node_id
                    break
                records.append(record)
                if annotate:
                    evidence = (f'entity={key[0]} metric={key[1]} version={key[2]} '
                                f'{record["conflict_type"]}: '
                                f'{record["left"]["value"]}{record["left"]["unit"] or ""} vs '
                                f'{record["right"]["value"]}{record["right"]["unit"] or ""} '
                                f'-> {record["resolution_status"]}')
                    graph.add_edge(left.node_id, right.node_id, 'conflicts', evidence=evidence,
                                   binding_type=record['conflict_type'])
    return records


def unresolved(records) -> list[dict]:
    return [record for record in (records or []) if record.get('resolution_status') == CONFLICT_UNRESOLVED]


# ---------------------------------------------------------------- persistence

def persist(conn, run_id: str, graph: EvidenceGraph) -> dict:
    """Best-effort write of the graph into ``evidence_nodes`` / ``evidence_edges``.

    Only the columns that exist in *both* the ``deploy/init`` shape and migration 019 are
    written (``evidence_id`` / ``run_id`` / ``node_type`` / ``ref_key`` / ``payload`` and
    ``src_evidence_id`` / ``dst_evidence_id`` / ``edge_type``); the richer binding fields live in
    the node payload so no column has to exist first. The caller owns the transaction.
    """
    nodes = edges = 0
    with conn.cursor() as cur:
        for node in graph.nodes():
            payload = {'graph_version': GRAPH_VERSION, **node.payload,
                       'binding': node.binding, 'binding_problems': node.binding_problems,
                       'version': node.version, 'unit': node.unit, 'time_scope': node.time_scope,
                       'source_sha256': node.source_sha256, 'ref': node.ref}
            cur.execute("""INSERT INTO evidence_nodes(evidence_id, run_id, node_type, ref_key, payload)
                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT (evidence_id) DO NOTHING""",
                        (node.node_id, run_id, node.node_type, node.ref[:256],
                         _jsonb(payload)))
            nodes += 1
        for edge in graph.edges():
            cur.execute("""INSERT INTO evidence_edges(run_id, src_evidence_id, dst_evidence_id, edge_type)
                           VALUES (%s,%s,%s,%s)""",
                        (run_id, edge.src, edge.dst, edge.edge_type))
            edges += 1
    return {'node_count': nodes, 'edge_count': edges}


def _jsonb(payload):
    from psycopg2.extras import Json
    return Json(payload)
