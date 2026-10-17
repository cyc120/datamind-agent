"""Wiki knowledge graph P2: the projection / adaptation layer.

This module turns the *existing* evidence tables into the shapes the eight ``wiki_*`` tables of
``db/migrations/021_wiki_knowledge.sql`` demand, without writing anything and without inventing
anything. It is pure: every function takes rows (or payloads) and returns a dict or a dataclass,
so the whole layer is testable without a database.

The three contracts it implements are, in priority order, ``docs/WIKI_DOMAIN.md`` (identity rules
of section 4, relation vocabulary of sections 3.2-3.5, the two conflict paths of 3.5),
``docs/WIKI_API.md`` (the ``provenance_incomplete`` semantics of section 6, the unified conflict
object of 7.4, the field names actually rendered) and the migration above (the real CHECK
constraints: id formats, closed vocabularies, ``run_scoped = (source_sha256 IS NULL)``,
``supported = (relation_type IS NOT NULL)``, ``supported = (unsupported_reason IS NULL)``).

WHAT IS PROJECTED, AND FROM WHERE
---------------------------------
``evidence_nodes`` has six columns in the deployed database (``evidence_id``, ``run_id``,
``node_type``, ``ref_key``, ``payload``, ``created_at``) -- the migration's ``source_sha256`` /
``version`` / ``unit`` / ``time_scope`` / ``binding`` columns do **not** exist there. Everything
therefore has to come out of ``payload``, whose real key names are taken from
``frontend/src/components/stargraph/NodeDetail.vue`` (the one place that already handles all nine
node types). Two writers with different payload shapes exist for the same node types:

* the industrial writer (``datamind/agent.py::_record_industrial_evidence``) -- ``SQLRow`` is a
  *flat* row (``part_id`` / ``batch_id`` / ``measurement_id`` / ``operation_no`` / ``metric`` /
  ``value`` / ``unit`` / ``measured_at`` / ``method`` / ``quality_id`` / ``report_id``);
* the SQL writer (``datamind/agent.py::_record_evidence``) -- ``SQLRow`` is ``{columns, rows,
  row_count}``. Both are live (350 flat rows, 94 nested ones), so both are projected.

``evidence_edges`` has six columns too (``edge_id``, ``run_id``, ``src_evidence_id``,
``dst_evidence_id``, ``edge_type``, ``weight``): the justification, the binding type and the
confidence of an edge were **never persisted**. That is exactly what
:data:`MISSING_EDGE_FIELDS` records, and why every projected relation carries
``provenance_incomplete`` with ``applies: true`` -- guessing them back is forbidden
(``docs/WIKI_API.md`` section 6.3 rule 2).

THE TWO CONFLICT PATHS
----------------------
``docs/WIKI_DOMAIN.md`` section 3.5: the industrial path writes the ``conflict_records`` table
(175 rows live) and the cross-source path writes **nothing at all** -- its conflicts only reach
``trace_events.output_summary.cross_source_result`` (``datamind/agent.py:195-202``). This database
has zero cross-source runs, so :func:`conflict_from_cross_source` is covered by synthetic tests
only. :func:`conflict_from_industrial_row` reads both the ``payload`` snapshot and the table's own
columns and never prefers one silently.

THE JSONB JSON-``null`` TRAP
----------------------------
``conflict_records.selected_fact_ref`` stores the JSON literal ``null`` in all 175 rows, so
``WHERE selected_fact_ref IS NOT NULL`` matches every row. :func:`jsonb_absent` treats SQL NULL,
the JSON literal ``null`` and an absent key as the same "unsupplied", and
:func:`conflict_from_industrial_row` applies it to ``selected_fact_ref``, ``left_fact_refs``,
``right_fact_refs`` and ``normalized_values`` alike. On the way *out* the module never emits a
JSON ``null`` literal for an unsupplied value: SQL NULL is the only representation the wiki_*
schema accepts.

CONNECTION CONVENTION (same as ``datamind/settings_store.py:31-33`` and
``datamind/sql_engine.py:353-357``): writes go through ``datamind_app``, reads through
``readonly_user``, both DSNs default to the local development cluster and are overridable by
environment variable. This module holds no connection of its own -- the callables are here so the
build layer and the tests agree on one spelling, and so a missing ``psycopg2`` degrades to a
clear error instead of an import crash.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import unicodedata
from decimal import Decimal, InvalidOperation

from .display_labels import CLAIM_TEXT_ZH, localize_enum_tokens

PROJECTION_VERSION = 'wiki-projection-v1'
DERIVATION_RULE_VERSION = 'wiki-derive-v1'

#: The default write/read DSNs, spelled out in the module so both the build layer and the tests
#: use one source (``DATAMIND_WIKI_WRITE_DSN`` / ``DATAMIND_WIKI_READ_DSN`` override them).
DEFAULT_WRITE_DSN = (
    "host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev")
DEFAULT_READ_DSN = (
    "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev")


def write_dsn() -> str:
    """DSN for the Wiki tables (owner ``datamind_app``, the role ``db/migrate.py`` runs as)."""
    return os.environ.get('DATAMIND_WIKI_WRITE_DSN') or DEFAULT_WRITE_DSN


def read_dsn() -> str:
    """DSN for reading the evidence tables (``readonly_user``, which may SELECT 43 tables)."""
    return os.environ.get('DATAMIND_WIKI_READ_DSN') or DEFAULT_READ_DSN


# ---------------------------------------------------------------- id formats (WIKI_API.md 1)
#
# The migration constrains every id with a regular expression, so these are not decoration:
# ``wiki_relations.src_id ~ '^(wiki|wassert|wev)-[0-9a-f]{24}$'`` alone refuses a single wrong
# character. One table of patterns, used by both :func:`allocate_id` and :func:`validate_id`.
ID_PREFIXES = {
    'entry': 'wiki', 'revision': 'wrev', 'evidence_ref': 'wev', 'provenance_link': 'wprov',
    'relation': 'wrel', 'review_event': 'wevt', 'build_job': 'wjob', 'space': 'wsp',
    'assertion': 'wassert',
}
ID_PATTERNS = {kind: re.compile('^%s-[0-9a-f]{24}$' % prefix) for kind, prefix in ID_PREFIXES.items()}
HEX64 = re.compile('^[a-f0-9]{64}$')
RELATION_ENDPOINT = re.compile('^(wiki|wassert|wev)-[0-9a-f]{24}$')
#: ``wiki_relations.raw_type`` is ``VARCHAR(32)``; a longer raw value has to be clipped or the
#: insert fails. The clip is recorded in ``basis`` so nothing is lost silently.
RAW_TYPE_MAX = 32

# ---------------------------------------------------------------- vocabularies (WIKI_DOMAIN.md 3)
#
# The union of ``evidence_graph.py:30-31`` (new) and ``db/migrations/019_evidence_graph.sql:86-89``
# (legacy, which is where ``derived_from`` / ``retrieved_from`` come from). ``supports`` is in both;
# it is one word with one meaning, not two.
NEW_EDGE_TYPES = ('entity_match', 'version_scope', 'unit_equivalent', 'time_overlap', 'supports',
                  'conflicts')
LEGACY_EDGE_TYPES = ('derived_from', 'retrieved_from', 'maps_to', 'contradicts', 'verified_by',
                     'repaired_from', 'resolved_by')
EDGE_TYPE_VOCABULARY = NEW_EDGE_TYPES + LEGACY_EDGE_TYPES

#: WIKI_DOMAIN.md 3.2 #1-#13: the 13 relation types that come from an existing edge. They keep
#: ``origin='projected'`` and the original word in ``raw_type``. ``contradicts`` is projected *as*
#: ``conflicts`` (3.3) with ``contradicts`` preserved in ``raw_type``.
PROJECTED_RELATION_TYPES = frozenset((
    'entity_match', 'version_scope', 'unit_equivalent', 'time_overlap', 'supports', 'conflicts',
    'derived_from', 'retrieved_from', 'maps_to', 'contradicts', 'verified_by', 'repaired_from',
    'resolved_by'))

#: WIKI_DOMAIN.md 3.2 #14-#21: the eight derived relation types. They come from no edge at all, so
#: ``raw_type`` must be NULL and they must be ``origin='wiki_derived'`` with a derivation record.
DERIVED_RELATION_TYPES = frozenset((
    'measures', 'applies_to', 'scoped_by', 'threshold_of', 'about', 'justified_by', 'supersedes',
    'restates'))

ALL_RELATION_TYPES = PROJECTED_RELATION_TYPES | DERIVED_RELATION_TYPES

#: WIKI_DOMAIN.md 3.2: undirected types. ``wiki_relations`` stores these in canonical endpoint
#: order and the migration's CHECK spells the same five out.
SYMMETRIC_RELATION_TYPES = frozenset(
    ('entity_match', 'unit_equivalent', 'time_overlap', 'conflicts', 'contradicts'))

#: WIKI_DOMAIN.md 3.3: the two words with one meaning. The projection always emits ``conflicts``.
RELATION_RAW_ALIASES = {'contradicts': 'conflicts'}

UNSUPPORTED_TYPE_REASON = 'TYPE_NOT_IN_WIKI_VOCABULARY'

#: WIKI_API.md 6.4: the reason codes for a provenance gap. ``recoverable`` is false for all of
#: them -- a field that was never persisted cannot be recovered by inference.
REASON_EDGE_COLUMNS_ABSENT = 'EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA'
REASON_NODE_BINDING_NOT_PERSISTED = 'NODE_BINDING_NOT_PERSISTED'
REASON_SOURCE_SNAPSHOT_ABSENT = 'SOURCE_SNAPSHOT_ABSENT'
REASON_TRACE_EVENT_PRUNED = 'TRACE_EVENT_PRUNED'
REASON_REVISION_PREDATES_PROJECTION = 'REVISION_PREDATES_PROJECTION'

#: The three edge columns migration 019 declares and the deployed table does not have. Read from
#: ``information_schema`` by :func:`missing_edge_columns` when a caller wants to prove it live;
#: hard-coded here only as the documented expectation.
MISSING_EDGE_FIELDS = ('edge.evidence', 'edge.binding_type', 'edge.confidence')

#: WIKI_API.md 7.4: how a raw resolution value is normalised. ``CONFLICT_UNRESOLVED`` is the
#: in-memory spelling of ``evidence_graph.detect_conflicts``.
RESOLUTION_NORMALIZATION = {
    'CONFLICT_UNRESOLVED': 'unresolved',
    'unresolved': 'unresolved',
    'needs_review': 'needs_review',
    'resolved_equivalent': 'resolved_equivalent',
    'resolved_by_registered_authority': 'resolved_by_authority',
    'resolved_by_authority': 'resolved_by_authority',
}
#: Only ``unresolved`` means "undecided conflict". ``needs_review`` and ``resolved_equivalent``
#: both mean "there is a difference record but it is not an adjudicated contradiction" -- reading
#: them as unresolved is the mistake WIKI_DOMAIN.md 1.4 warns about by name.
UNRESOLVED_RESOLUTION = 'unresolved'

CONFLICT_PATH_INDUSTRIAL = 'industrial_table'
CONFLICT_PATH_CROSS_SOURCE = 'cross_source_trace'

#: The four entry types of WIKI_DOMAIN.md 2, matching ``wiki_entries.entry_type``'s CHECK.
ENTRY_TYPES = ('business_entity', 'metric', 'norm', 'conclusion')

# ---------------------------------------------------------------- node type adaptation (3.4)
#
# Requirement document section 41 / WIKI_DOMAIN.md 3.4 rules 1-5: an unknown type keeps its raw
# value, gets ``supported=false`` and the reason ``TYPE_NOT_IN_WIKI_VOCABULARY``, is still shown,
# and never takes part in the derivation of the eight Wiki-derived relations (3.4 rule 4).
#
# ``node_type_norm`` is the Wiki-recognised form of a raw node type. For the four closed-set
# vocabulary words of ``evidence_graph.py:29`` it IS the raw word, because
# ``datamind/cross_source.py:308-309`` really does write ``measurement`` / ``sql_row`` into
# ``evidence_nodes.node_type`` when a cross-source run persists its graph.
NODE_TYPE_NORMALIZATION = {
    # closed vocabulary (evidence_graph.py:29) -- already the Wiki spelling
    'sql_row': 'sql_row',
    'doc_block': 'doc_block',
    'measurement': 'measurement',
    'spec_clause': 'spec_clause',
    # the nine node types that actually exist in this database (2760 rows, zero closed-set rows)
    'Claim': 'claim',
    'SQLQuery': 'sql_query',
    'SQLRow': 'sql_row',
    'Chunk': 'doc_block',
    'Document': 'document',
    'DocumentFact': 'document_fact',
    'VerificationCheck': 'verification_check',
    'ConflictRecord': 'conflict_record',
    'AuthorityRule': 'authority_rule',
}

#: Which entry type (WIKI_DOMAIN.md 2) a node type feeds, when it feeds one at all. A node type
#: absent from this table produces an evidence reference but no entry -- ``document`` and
#: ``doc_block`` are evidence carriers, ``sql_query`` / ``verification_check`` are provenance, and
#: ``conflict_record`` is already the unified conflict object. Inventing an entry for them would
#: mean inventing a business key.
NODE_TYPE_ENTRY_TYPES = {
    'measurement': 'metric',
    'document_fact': 'metric',   # provisional: 'requirement' fact_kind is a norm, see below
    'claim': 'conclusion',
}
#: ``DocumentFact.payload.fact_kind`` splits the industrial facts in two (315 ``measurement``,
#: 70 ``requirement``) and WIKI_DOMAIN.md 2.3 says the requirement half is a ``norm``.
FACT_KIND_ENTRY_TYPES = {'measurement': 'metric', 'requirement': 'norm'}

#: WIKI_DOMAIN.md 4.2: which payload field carries the source snapshot for each node type, in
#: ``COALESCE`` order. The document half is mandatory because ``document_sha256`` and
#: ``source_sha256`` are mutually exclusive in this database (186 vs 35 rows, never both).
SOURCE_SHA_FIELDS = {
    'document': ('document_sha256', 'source_sha256'),
    'doc_block': ('document_sha256', 'source_sha256'),
    'document_fact': ('source_sha256', 'document_sha256'),
    'sql_row': ('source_sha256', 'evidence_sha256', 'source_snapshot_id'),
    'measurement': ('source_sha256', 'evidence_sha256', 'source_snapshot_id'),
    'sql_query': ('source_sha256', 'evidence_sha256', 'source_snapshot_id'),
    'spec_clause': ('source_sha256', 'document_sha256'),
}

#: WIKI_DOMAIN.md 4.2 ``source_kind``: 'doc' for the document half, 'sql' for the data half.
SQL_NODE_TYPES = frozenset(('sql_row', 'measurement', 'sql_query'))
DOC_NODE_TYPES = frozenset(('doc_block', 'document', 'document_fact', 'spec_clause'))

#: The locator kinds the migration allows.
LOCATOR_KINDS = ('sql_record', 'sql_aggregate', 'pdf_region', 'text_span')

#: Which payload field each node type names its record with, in order. ``measurement_id`` first
#: because it is the named record WIKI_DOMAIN.md 4.3 compares on.
RECORD_ID_FIELDS = ('measurement_id', 'row_id', 'record_id', 'fact_id', 'block_id', 'chunk_id')


class ProjectionError(ValueError):
    """A projection input that cannot be projected without inventing a value.

    ``code`` is stable so the build layer can map it onto a ``WIKI_*`` error code instead of
    turning it into a 500.
    """

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code + (':' + detail if detail else ''))
        self.code = code
        self.detail = detail


# ================================================================ primitives

def canonical_json(payload) -> str:
    """The one serialisation used for every fingerprint and for ``payload_sha256``.

    Same recipe as ``evidence_graph.py:63-65`` (sorted keys, no whitespace, non-ASCII kept
    verbatim, ``default=str`` so a live ``Decimal`` / ``datetime`` still serialises), so a
    fingerprint computed here over the same object equals one computed there.
    """
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      default=str)


def digest64(payload) -> str:
    """Bare lowercase sha256 hex -- no ``sha256:`` prefix; the API layer adds that (WIKI_API.md 1)."""
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def allocate_id(kind: str, *, seed=None) -> str:
    """A contract-shaped id: ``<prefix>-`` + 24 hex.

    ``seed`` makes the id reproducible (tests, and any caller that wants a deterministic
    projection of a frozen fixture). Without a seed the id is freshly allocated, which is what
    WIKI_DOMAIN.md 4.1 means by "allocated, not derivable" for ``entry_id``: identity is carried
    by ``business_key``, never by the id.
    """
    if kind not in ID_PREFIXES:
        raise ProjectionError('WIKI_ID_KIND_UNKNOWN', str(kind))
    hex24 = (hashlib.sha256(str(seed).encode('utf-8')).hexdigest() if seed is not None
             else uuid.uuid4().hex)[:24]
    return '%s-%s' % (ID_PREFIXES[kind], hex24)


def validate_id(kind: str, value) -> bool:
    """True when ``value`` satisfies the migration's CHECK for this id kind."""
    pattern = ID_PATTERNS.get(kind)
    return bool(pattern and isinstance(value, str) and pattern.match(value))


def jsonb_absent(value) -> bool:
    """True when a JSONB value was never supplied -- SQL NULL, JSON ``null``, or an empty string.

    This is the ``selected_fact_ref`` trap of ``db/migrations/021_wiki_knowledge.sql:69-77``: 175
    of 175 rows store the JSON literal ``null``, so ``IS NOT NULL`` matches every one of them. The
    JSON ``null`` arrives from psycopg2 either as Python ``None`` or as the *string* ``'null'``
    depending on how the column was read, so both spellings are refused here. An empty collection
    counts as unsupplied too: an empty conflict side is not a side.
    """
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in ('', 'null'):
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return True
    return False


def jsonb_present(value) -> bool:
    """The exact negation of :func:`jsonb_absent`. Read every JSONB column through one of these."""
    return not jsonb_absent(value)


def unsupported_node_type(raw_type) -> dict:
    """The verdict object for a node type Wiki does not recognise (WIKI_DOMAIN.md 3.4 rules 3/5)."""
    return {'raw_type': raw_type, 'node_type': None, 'supported': False,
            'unsupported_reason': UNSUPPORTED_TYPE_REASON}


def normalize_node_type(raw_type) -> dict:
    """Map a raw ``evidence_nodes.node_type`` onto the Wiki spelling, or refuse to.

    Returns ``{'raw_type', 'node_type', 'supported', 'unsupported_reason'}``. ``raw_type`` is
    always the original value (3.4 rule 1); ``node_type`` is ``None`` exactly when
    ``supported`` is false, mirroring the migration's ``supported = (relation_type IS NOT NULL)``
    discipline.
    """
    raw = '' if raw_type is None else str(raw_type)
    normalized = NODE_TYPE_NORMALIZATION.get(raw)
    if normalized is None:
        return unsupported_node_type(raw)
    return {'raw_type': raw, 'node_type': normalized, 'supported': True,
            'unsupported_reason': None}


def raw_node_types_for(raw_types) -> list:
    """``wiki_evidence_refs.raw_node_types``.

    The migration requires a non-empty array with no blank element whenever ``supported`` is
    false, so a blank raw type becomes the explicit ``'<unknown>'`` marker rather than an empty
    string (an empty element would fail ``CHECK (NOT ('' = ANY (raw_node_types)))``).
    """
    values = [raw_types] if isinstance(raw_types, str) else list(raw_types or [])
    cleaned = [str(value) for value in values if value is not None and str(value).strip()]
    return cleaned or ['<unknown>']


# ================================================================ payload reading helpers
#
# The real field names, from frontend/src/components/stargraph/NodeDetail.vue:22-46. Nothing here
# guesses a key: a missing key yields None and the caller records the gap.

def node_payload(node) -> dict:
    """The node's ``payload`` as a plain dict, tolerating a raw ``evidence_nodes`` row."""
    if node is None:
        return {}
    if isinstance(node, dict):
        payload = node.get('payload')
        return payload if isinstance(payload, dict) else {}
    payload = getattr(node, 'payload', None)
    return payload if isinstance(payload, dict) else {}


def payload_binding(payload: dict) -> dict | None:
    """``payload.binding`` when the cross-source writer stored one.

    Measured: **zero** of 2760 nodes carry this key (WIKI_API.md 12.2), so a projection that finds
    None must say ``NODE_BINDING_NOT_PERSISTED`` rather than fall back to a guess.
    """
    binding = payload.get('binding') if isinstance(payload, dict) else None
    return binding if isinstance(binding, dict) else None


def _first_present(mapping: dict, keys) -> tuple:
    """``(key, value)`` of the first key present with a non-empty value, else ``(None, None)``."""
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != '':
            return key, value
    return None, None


def source_snapshot(payload: dict, node_type: str | None) -> tuple:
    """``(source_sha256, field_used)`` for a node, ``(None, None)`` when no snapshot is named.

    WIKI_DOMAIN.md 4.2 rule 1 fixes the field order and requires the document half to be
    ``COALESCE(document_sha256, source_sha256)``: the two are mutually exclusive in this database,
    so reading only one would silently degrade 186 or 35 nodes to run-scoped identity.
    """
    payload = payload if isinstance(payload, dict) else {}
    for key in SOURCE_SHA_FIELDS.get(node_type or '', ()):  # type: ignore[arg-type]
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value), key
    # The cross-source writer puts ``source_sha256`` on the node and ALSO in its binding.
    binding = payload_binding(payload)
    if binding and str(binding.get('source_sha256') or '').strip():
        return str(binding['source_sha256']), 'binding.source_sha256'
    return None, None


def source_ref_for(payload: dict, node_type: str | None = None, ref_key=None) -> str | None:
    """``source_ref`` of WIKI_DOMAIN.md 4.2: the ``source_id`` (SQL) or the ``document_id`` (doc).

    Returns ``None`` -- never a fabricated value -- for the node types that genuinely do not name
    a source of their own (``claim``, ``verification_check``): a claim's source is the run, and
    that is what ``wiki_provenance_links.run_id`` is for.
    """
    payload = payload if isinstance(payload, dict) else {}
    _, value = _first_present(payload, ('source_ref', 'document_id', 'source_id'))
    if value is not None:
        return str(value)
    binding = payload_binding(payload)
    if binding and str(binding.get('source_ref') or '').strip():
        return str(binding['source_ref'])
    return None


def source_kind_for(payload: dict, node_type: str | None) -> str | None:
    """``'sql'`` / ``'doc'``, or ``None`` when the node type does not say.

    A payload that declares ``source_kind`` wins (the cross-source writer always sets it); then
    the node type decides (WIKI_DOMAIN.md 4.2's table). A node that names neither gets ``None``
    and the caller has to report ``SOURCE_SNAPSHOT_ABSENT`` instead of picking a side.
    """
    payload = payload if isinstance(payload, dict) else {}
    declared = str(payload.get('source_kind') or '').strip().lower()
    if declared in ('sql', 'doc'):
        return declared
    if node_type in SQL_NODE_TYPES:
        return 'sql'
    if node_type in DOC_NODE_TYPES:
        return 'doc'
    return None


def record_id_for(payload: dict, node_type: str | None, ref_key=None) -> str | None:
    """The named record this node speaks about (``evidence_graph.record_scope`` semantics).

    ``measurement_id`` first, then the other real ids; failing all of them a SQL-side node falls
    back to its ``ref_key`` (which *is* its row identity, ``evidence_graph.py:360-361``), and a
    document-side node stays ``None`` rather than borrowing the document id.
    """
    payload = payload if isinstance(payload, dict) else {}
    key, value = _first_present(payload, RECORD_ID_FIELDS)
    if value is not None:
        return str(value)
    if node_type in SQL_NODE_TYPES and ref_key is not None and str(ref_key).strip():
        return str(ref_key)
    return None


def value_of(payload: dict) -> str | None:
    """The comparable value of a node as a decimal string, or ``None``.

    Reads ``value`` then ``normalized_value``; a live SQL row carries ``Decimal`` and the JP
    convention is a stringified decimal, so everything is stringified here and never floated.
    """
    payload = payload if isinstance(payload, dict) else {}
    _, raw = _first_present(payload, ('value', 'normalized_value'))
    if raw is None:
        return None
    try:
        number = Decimal(str(raw))
    except (InvalidOperation, ArithmeticError, ValueError):
        return None
    return format(number, 'f') if number.is_finite() else None


def unit_of(payload: dict) -> str | None:
    """The node's unit, preferring the raw one (``raw_unit`` is what the document wrote)."""
    payload = payload if isinstance(payload, dict) else {}
    _, raw = _first_present(payload, ('unit', 'raw_unit', 'normalized_unit'))
    return None if raw is None else str(raw)


def excerpt_of(payload: dict, node_type: str | None) -> str:
    """The quotable source text, or ``''``.

    WIKI_API.md 11.4: 5 retrieved chunks in ``run-28ebd0921f8f43a5`` have an empty ``quote``. Empty
    means empty -- neighbouring text or a summary must never be substituted, so this returns the
    empty string and the caller keeps it visible.
    """
    payload = payload if isinstance(payload, dict) else {}
    if node_type == 'doc_block':
        _, quote = _first_present(payload, ('quote', 'text', 'excerpt'))
        return '' if quote is None else str(quote)
    if node_type == 'document_fact':
        raw = payload.get('raw_value')
        unit = payload.get('raw_unit')
        if raw in (None, ''):
            return ''
        return ('%s %s' % (raw, unit)).strip() if unit else str(raw)
    if node_type == 'measurement':
        value, unit = payload.get('value'), payload.get('unit')
        if value in (None, ''):
            return ''
        return ('%s %s' % (value, unit)).strip() if unit else str(value)
    if node_type == 'document':
        _, title = _first_present(payload, ('title',))
        return '' if title is None else str(title)
    _, text = _first_present(payload, ('text',))
    return '' if text is None else str(text)


# ================================================================ locator (WIKI_DOMAIN.md 4.2)

def _bbox_of(payload: dict) -> list | None:
    """The first usable bbox: ``locators[0].locator.bbox``, ``source_blocks[0].bbox``, ``bbox``."""
    for entry in payload.get('locators') or []:
        if isinstance(entry, dict):
            locator = entry.get('locator')
            if isinstance(locator, dict) and isinstance(locator.get('bbox'), list):
                return locator['bbox']
    for block in payload.get('source_blocks') or []:
        if isinstance(block, dict) and isinstance(block.get('bbox'), list):
            return block['bbox']
    for ref in payload.get('evidence_refs') or []:
        if isinstance(ref, dict) and isinstance(ref.get('bbox'), list):
            return ref['bbox']
    bbox = payload.get('bbox')
    return bbox if isinstance(bbox, list) else None


def _page_of(payload: dict) -> int | None:
    for entry in payload.get('locators') or []:
        if isinstance(entry, dict):
            locator = entry.get('locator')
            if isinstance(locator, dict) and locator.get('page_no') is not None:
                return locator['page_no']
    for source in (payload, ) + tuple(
            block for block in (payload.get('source_blocks') or []) if isinstance(block, dict)):
        for key in ('page_no', 'page_start'):
            if source.get(key) is not None:
                return source[key]
    for ref in payload.get('evidence_refs') or []:
        if isinstance(ref, dict) and ref.get('page_no') is not None:
            return ref['page_no']
    return None


def _block_of(payload: dict) -> str | None:
    for entry in payload.get('locators') or []:
        if isinstance(entry, dict) and str(entry.get('block_id') or '').strip():
            return str(entry['block_id'])
    for block in payload.get('source_blocks') or []:
        if isinstance(block, dict) and str(block.get('block_id') or '').strip():
            return str(block['block_id'])
    for ref in payload.get('evidence_refs') or []:
        if isinstance(ref, dict) and str(ref.get('block_id') or '').strip():
            return str(ref['block_id'])
    _, value = _first_present(payload, ('block_id',))
    return None if value is None else str(value)


def locator_for(payload: dict, node_type: str | None, source_ref=None,
                ref_key=None) -> tuple:
    """``(locator_kind, locator)`` for a node, or ``(None, None)`` when it cannot be built.

    The migration's per-kind CHECKs are the reason this is not a free-form dict:

    * ``pdf_region`` requires ``page_no`` **and** ``bbox`` **and** ``block_id``;
    * ``text_span`` requires ``block_id``;
    * ``sql_record`` / ``sql_aggregate`` require ``sql_hash``.

    A node that names only part of a PDF region therefore projects as ``text_span`` -- the truth
    about what is known -- instead of a ``pdf_region`` that would fail the insert. Returning
    ``(None, None)`` is the honest outcome when not even one of those keys exists, and the caller
    reports ``WIKI_LOCATOR_UNAVAILABLE``.
    """
    payload = payload if isinstance(payload, dict) else {}
    kind = str(payload.get('locator_kind') or '').strip()
    sql_hash = str(payload.get('sql_hash') or '').strip() or (
        '' if ref_key is None else str(ref_key).strip())
    if node_type in SQL_NODE_TYPES:
        if sql_hash:
            nested = isinstance(payload.get('rows'), list)
            _, row_count = _first_present(payload, ('row_count',))
            named_record = payload.get('measurement_id') not in (None, '')
            # A nested {columns, rows} payload is an aggregate result; a flat named row is a
            # record. WIKI_DOMAIN.md 4.2's locator_kind table draws exactly this line.
            locator_kind = 'sql_record' if named_record else (
                'sql_aggregate' if (nested or row_count is not None) else 'sql_record')
            locator = {'sql_hash': sql_hash}
            for key in ('row_count', 'columns', 'source_snapshot_id', 'source_id'):
                value = payload.get(key)
                if value not in (None, '', []):
                    locator[key] = value
            if payload.get('measurement_id') not in (None, ''):
                locator['measurement_id'] = payload['measurement_id']
            elif payload.get('ref') not in (None, ''):
                locator['ref'] = payload['ref']
            return locator_kind, locator
        return None, None
    if node_type in ('document_fact', ) or kind == 'pdf_region' or node_type == 'document':
        page, bbox, block = _page_of(payload), _bbox_of(payload), _block_of(payload)
        if page is not None and bbox is not None and block is not None:
            locator = {'page_no': page, 'bbox': bbox, 'block_id': block}
            for key in ('document_id', 'source_revision', 'contract_version', 'coordinate_system',
                        'unit'):
                value = payload.get(key)
                if value not in (None, '', []):
                    locator[key] = value
            return 'pdf_region', locator
        if block is not None:
            locator = {'block_id': block}
            if page is not None:
                locator['page_no'] = page
            if bbox is not None:
                locator['bbox'] = bbox
            if source_ref is not None:
                locator['document_id'] = source_ref
            return 'text_span', locator
        return None, None
    if node_type == 'doc_block':
        page, bbox, block = _page_of(payload), _bbox_of(payload), _block_of(payload)
        if page is not None and bbox is not None and block is not None:
            locator = {'page_no': page, 'bbox': bbox, 'block_id': block}
            for key in ('document_id', 'page_start', 'page_end', 'section_path', 'contract_version',
                        'content_hash', 'parser_version'):
                value = payload.get(key)
                if value not in (None, '', []):
                    locator[key] = value
            return 'pdf_region', locator
        if block is not None:
            locator = {'block_id': block}
            if page is not None:
                locator['page_no'] = page
            if source_ref is not None:
                locator['document_id'] = source_ref
            return 'text_span', locator
        return None, None
    return None, None


def record_sha256_for(payload: dict, node_type: str | None, excerpt: str = '') -> str:
    """``record_sha256``: the content digest of the record itself.

    Preference order is the source's own digest (``content_hash`` for a parsed block,
    ``payload_sha256`` for an industrial fact) and only then a digest computed over the payload.
    A computed digest is a digest of *what was persisted*, not a claim that it is the source's
    own -- and it is stable, which is what the fingerprint needs.
    """
    payload = payload if isinstance(payload, dict) else {}
    for key in ('content_hash', 'payload_sha256', 'document_sha256', 'source_sha256'):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip().lower()
        if HEX64.match(text):
            return text
    if excerpt:
        return digest64({'excerpt': excerpt})
    return digest64({'payload': payload})


# ================================================================ identity 1: entry (4.1)

def entity_key_part(entity_type, canonical_key, *, confirmation=None, title=None) -> tuple:
    """``key_part`` + ``confirmation`` for a ``business_entity`` (WIKI_DOMAIN.md 4.1).

    ``entity_type + US + canonical_entity_key`` when an approved alias or a human confirmation
    supplied the key; otherwise ``'pending:' + lower(btrim(title))`` **and**
    ``confirmation='pending'``. The two halves cannot be separated: the migration's
    ``CHECK (key_part NOT LIKE 'pending:%' OR confirmation = 'pending')`` refuses a pending key
    promoted without a human, and WIKI_DOMAIN.md 4.1 rule 3 forbids that promotion.
    """
    key = '' if canonical_key is None else str(canonical_key).strip()
    kind = '' if entity_type is None else str(entity_type).strip()
    if key:
        part = (kind + '\x1f' + key) if kind else key
        return part, (confirmation or 'confirmed')
    fallback = '' if title is None else str(title).strip().lower()
    if not fallback:
        raise ProjectionError('WIKI_ENTITY_KEY_UNRESOLVED',
                              'no canonical entity key and no title to build a pending key from')
    return 'pending:' + fallback, 'pending'


def metric_key_part(metric_id) -> str:
    """``metric_id``: the canonical name *inside the source*, never the Chinese label."""
    text = '' if metric_id is None else str(metric_id).strip()
    if not text:
        raise ProjectionError('WIKI_METRIC_ID_UNRESOLVED', 'metric_id is empty')
    return text


def norm_key_part(source_document_id, source_revision, clause_key=None) -> str:
    """``source_document_id + US + source_revision + US + clause_key``.

    ``source_revision`` is mandatory (WIKI_DOMAIN.md 2.3): A102-V2 and A102-V3 are two norms, not
    two versions of one norm. Merging them would make historical conclusions unexplainable.
    """
    document = '' if source_document_id is None else str(source_document_id).strip()
    revision = '' if source_revision is None else str(source_revision).strip()
    if not document or not revision:
        raise ProjectionError('WIKI_NORM_KEY_UNRESOLVED',
                              'source_document_id and source_revision are both required')
    return '\x1f'.join((document, revision, '' if clause_key is None else str(clause_key).strip()))


def conclusion_key_part(run_id, claim_id) -> str:
    """``run_id + US + claim_id``. Conclusions never merge across runs (WIKI_DOMAIN.md 2.4)."""
    run = '' if run_id is None else str(run_id).strip()
    claim = '' if claim_id is None else str(claim_id).strip()
    if not run or not claim:
        raise ProjectionError('WIKI_CONCLUSION_KEY_UNRESOLVED',
                              'run_id and claim_id are both required')
    return run + '\x1f' + claim


def entry_identity(space_id, namespace, entry_type, key_part, *, confirmation=None,
                   entry_id=None) -> dict:
    """The entry identity of WIKI_DOMAIN.md 4.1.

    ``business_key`` is ``space_id | namespace | entry_type | key_part`` -- the four segments the
    migration's ``UNIQUE (space_id, namespace, entry_type, key_part)`` makes unique inside a space,
    with ``space_id`` first so two users never share an identity (4.1 rule 5).

    The returned ``business_key`` dict is the ``entry.business_key`` object of WIKI_API.md 5.1,
    which additionally carries ``confirmation`` (the field the publication gate reads).
    ``entry_id`` is allocated when not supplied: identity travels in the business key, so
    re-projecting the same object with a new id can never create a second identity -- the unique
    constraint refuses it.
    """
    if entry_type not in ENTRY_TYPES:
        raise ProjectionError('WIKI_ENTRY_TYPE_UNKNOWN', str(entry_type))
    if not str(space_id or '').strip():
        raise ProjectionError('WIKI_SPACE_ID_UNRESOLVED', 'space_id is empty')
    if not str(namespace or '').strip():
        raise ProjectionError('WIKI_NAMESPACE_UNRESOLVED', 'namespace is empty')
    part = '' if key_part is None else str(key_part)
    if not part.strip():
        raise ProjectionError('WIKI_KEY_PART_EMPTY', 'key_part is empty')
    if part.startswith('pending:') and confirmation != 'pending':
        raise ProjectionError(
            'WIKI_ENTITY_KEY_PENDING_MISMATCH',
            'a pending: key_part may only ever be stored with confirmation=pending')
    resolved_confirmation = confirmation or 'pending'
    return {
        'entry_id': entry_id or allocate_id('entry', seed=canonical_json(
            [str(space_id), str(namespace), entry_type, part])),
        'entry_type': entry_type,
        'namespace': str(namespace),
        'key_part': part,
        'confirmation': resolved_confirmation,
        'business_key': {'space_id': str(space_id), 'namespace': str(namespace),
                         'entry_type': entry_type, 'key_part': part,
                         'confirmation': resolved_confirmation},
    }


def entry_type_for_node(node_type: str | None, payload: dict) -> str | None:
    """Which of the four entry types a node type feeds, or ``None`` for an evidence-only node."""
    payload = payload if isinstance(payload, dict) else {}
    if node_type == 'document_fact':
        fact_kind = str(payload.get('fact_kind') or '').strip()
        return FACT_KIND_ENTRY_TYPES.get(fact_kind)
    return NODE_TYPE_ENTRY_TYPES.get(node_type or '')


def _clean_title(text, limit: int = 120) -> str:
    """A one-line title: citation markers dropped, whitespace collapsed, clipped on a word edge."""
    value = '' if text is None else str(text)
    value = value.replace('**', '').replace('[', ' ').replace(']', ' ')
    value = unicodedata.normalize('NFKC', value)
    value = ' '.join(value.split())
    if len(value) <= limit:
        return value
    clipped = value[:limit]
    if ' ' in clipped[limit // 2:]:
        clipped = clipped[:clipped.rindex(' ')]
    return clipped.rstrip() + '…'


def entry_key_part_for_node(node_type, payload, *, run_id=None, ref_key=None) -> tuple:
    """``(entry_type, key_part, confirmation)`` for a node, or ``(None, None, None)``.

    ``key_part`` follows WIKI_DOMAIN.md 4.1 verbatim per entry type. Every branch raises rather
    than falling back to a title-derived key it has no right to invent.
    """
    payload = payload if isinstance(payload, dict) else {}
    entry_type = entry_type_for_node(node_type, payload)
    if entry_type is None:
        return None, None, None
    if entry_type == 'metric':
        _, metric_id = _first_present(payload, ('metric',))
        return entry_type, metric_key_part(metric_id), 'confirmed'
    if entry_type == 'norm':
        _, clause_key = _first_present(payload, ('clause_key', 'clause_id', 'measurement_id'))
        return entry_type, norm_key_part(payload.get('document_id'),
                                         payload.get('source_revision'), clause_key), 'confirmed'
    if entry_type == 'conclusion':
        _, claim_id = _first_present(payload, ('claim_id',))
        claim_id = claim_id or ref_key
        return entry_type, conclusion_key_part(run_id, claim_id), 'confirmed'
    # business_entity: no node type currently produces one directly, but the branch is the
    # contract's own rule and the entity payload fields are real (DocumentFact / SQLRow).
    part, confirmation = entity_key_part(payload.get('entity_type'), None,
                                         confirmation='pending',
                                         title=payload.get('entity_name') or payload.get('title'))
    return entry_type, part, confirmation


def entry_title_for_node(node_type, payload) -> str:
    """A title drawn only from fields that exist (NodeDetail.vue's per-type lists)."""
    payload = payload if isinstance(payload, dict) else {}
    if node_type == 'claim':
        # 标题直接取主张文本。文本里的枚举词在这里换成中文：**已写进库的旧主张一个字节都不动**，
        # 只有本次投影出来的标题是中文（未登记的取值原样保留，见 display_labels 纪律 1）。
        text = localize_enum_tokens(_clean_title(payload.get('text')), CLAIM_TEXT_ZH)
        if text:
            return text
    for key in ('metric', 'title', 'entity_name', 'fact_id', 'conflict_id'):
        value = payload.get(key)
        if value is not None and str(value).strip():
            # str(value), not the key name: the title is the value that field holds.
            return _clean_title(value)
    name, value = _first_present(payload, ('measurement_id', 'block_id', 'chunk_id'))
    if value is not None:
        return '%s %s' % (name, value)
    return ''


def entry_summary_for_node(node_type, payload) -> str:
    """A summary assembled from named real fields; empty when there is nothing true to say."""
    payload = payload if isinstance(payload, dict) else {}
    parts = []
    if node_type == 'document_fact':
        fact_kind = payload.get('fact_kind')
        if fact_kind:
            parts.append('fact_kind=%s' % fact_kind)
        if payload.get('normalized_range'):
            parts.append('normalized_range=%s' % canonical_json(payload['normalized_range']))
        elif payload.get('normalized_value') is not None:
            parts.append('normalized_value=%s %s' % (payload['normalized_value'],
                                                     payload.get('normalized_unit') or ''))
        if payload.get('valid_from') or payload.get('valid_to'):
            parts.append('valid %s~%s' % (payload.get('valid_from'), payload.get('valid_to')))
    elif node_type == 'measurement':
        value = value_of(payload)
        if value is not None:
            parts.append('value=%s %s' % (value, payload.get('unit') or ''))
        if payload.get('metric'):
            parts.append('metric=%s' % payload['metric'])
    elif node_type == 'claim':
        status = payload.get('verification_contract')
        if status:
            parts.append('verification_contract=%s' % status)
    return '; '.join(part.strip() for part in parts if part and part.strip())


def project_entry(space_id, namespace, node_type, payload, *, run_id=None, ref_key=None,
                  entry_id=None, title=None, summary=None) -> dict | None:
    """The entry projection of one node, or ``None`` when the node feeds no entry.

    ``confirmation`` is ``pending`` for anything whose business key was not established by an
    approved alias or a human confirmation -- which today is every entity, because
    ``entity_aliases`` is populated but never resolved in a running query (WIKI_DOMAIN.md 2.1).
    """
    payload = payload if isinstance(payload, dict) else {}
    entry_type, key_part, confirmation = entry_key_part_for_node(
        node_type, payload, run_id=run_id, ref_key=ref_key)
    if entry_type is None:
        return None
    project_title = title if title is not None else entry_title_for_node(node_type, payload)
    if not str(project_title or '').strip():
        raise ProjectionError('WIKI_ENTRY_TITLE_EMPTY', '%s/%s' % (node_type, key_part))
    identity = entry_identity(space_id, namespace, entry_type, key_part,
                             confirmation=confirmation, entry_id=entry_id)
    project_summary = entry_summary_for_node(node_type, payload) if summary is None else summary
    return {**identity, 'title': project_title, 'aliases': [], 'category': None,
            'summary': project_summary or '', 'demo_data': True,
            'source_node_type': node_type}


# ================================================================ identity 2: evidence (4.2)

def evidence_identity(space_id, source_kind, source_ref, source_sha256, locator_kind, locator,
                      record_sha256, *, run_id=None, run_scoped=None) -> dict:
    """The evidence identity of WIKI_DOMAIN.md 4.2, including the run-scoped degradation.

    ``evidence_fingerprint = sha256(canonical_json({space_id, source_kind, source_ref,
    source_sha256, locator_kind, locator, record_sha256}))`` -- bare hex, no ``sha256:`` prefix.
    Identity deliberately excludes ``run_id``, ``node_id``, ``confidence`` and any retrieval
    score: ranking and model confidence do not decide authority (4.2 rule 3).

    Rule 1 (the degradation): when the source snapshot is absent, ``run_id`` is **appended to the
    fingerprint** and the run is pinned in ``run_scoped_run_id``. The migration states this as a
    two-way invariant, ``CHECK (run_scoped = (source_sha256 IS NULL))``, so an empty snapshot
    string is normalised to ``None`` here rather than passed through as ``''``.

    The reference id is derived from the fingerprint, not allocated: the tables' own
    ``UNIQUE (space_id, evidence_fingerprint)`` already says two runs over one source version and
    one span share a single row, so a derived id is what makes that row reachable twice.
    """
    if not str(space_id or '').strip():
        raise ProjectionError('WIKI_SPACE_ID_UNRESOLVED', 'space_id is empty')
    if source_kind not in ('sql', 'doc'):
        raise ProjectionError('WIKI_SOURCE_KIND_UNRESOLVED', str(source_kind))
    if not str(source_ref or '').strip():
        raise ProjectionError('WIKI_SOURCE_REF_UNRESOLVED', 'source_ref is empty')
    if locator_kind not in LOCATOR_KINDS:
        raise ProjectionError('WIKI_LOCATOR_KIND_UNKNOWN', str(locator_kind))
    if not isinstance(locator, dict) or not locator:
        raise ProjectionError('WIKI_LOCATOR_UNRESOLVED', 'locator is empty')
    record_digest = str(record_sha256 or '').strip().lower()
    if not HEX64.match(record_digest):
        raise ProjectionError('WIKI_RECORD_SHA256_INVALID', str(record_sha256))

    snapshot = None if source_sha256 is None or not str(source_sha256).strip() else str(
        source_sha256).strip()
    is_run_scoped = snapshot is None if run_scoped is None else bool(run_scoped)
    if is_run_scoped and snapshot is not None:
        raise ProjectionError(
            'WIKI_RUN_SCOPED_WITH_SNAPSHOT',
            'run_scoped is TRUE but a snapshot is present; the snapshot must stay out of the '
            'identity exactly when it exists (WIKI_DOMAIN.md 4.2 rule 3)')
    if not is_run_scoped and snapshot is None:
        raise ProjectionError(
            'WIKI_SOURCE_SNAPSHOT_ABSENT',
            'no snapshot: the fingerprint must be run-scoped and the run pinned (4.2 rule 1)')
    pinned_run = None
    if is_run_scoped:
        pinned_run = '' if run_id is None else str(run_id).strip()
        if not pinned_run:
            raise ProjectionError(
                'WIKI_RUN_ID_REQUIRED',
                'a run-scoped evidence identity without run_id could be reused across runs')

    material = {'space_id': str(space_id), 'source_kind': source_kind, 'source_ref': str(source_ref),
                'source_sha256': snapshot, 'locator_kind': locator_kind, 'locator': locator,
                'record_sha256': record_digest}
    if is_run_scoped:
        material['run_id'] = pinned_run
    fingerprint = digest64(material)
    return {
        'evidence_ref_id': allocate_id('evidence_ref', seed=str(space_id) + '\x1f' + fingerprint),
        'evidence_fingerprint': fingerprint,
        'fingerprint_material': material,
        'source_kind': source_kind,
        'source_ref': str(source_ref),
        'source_sha256': snapshot,
        'locator_kind': locator_kind,
        'locator': locator,
        'record_sha256': record_digest,
        'run_scoped': is_run_scoped,
        'run_scoped_run_id': pinned_run,
    }


# ================================================================ identity 3: comparison (4.3)

def comparison_key(space_id, entity_key, predicate, applicability=None, time_scope=None,
                   record_scope=None, statistic=None) -> str:
    """``comparison_key`` of WIKI_DOMAIN.md 4.3 -- bare sha256 hex.

    The migration stores it without the ``sha256:`` prefix that WIKI_API.md 5.3 shows in responses
    (``CHECK (comparison_key ~ '^[a-f0-9]{64}$')``), and the service adds the prefix. Only
    assertions sharing this key may ever be weighed against each other, so a metric asserted for a
    different time or scope is not a conflict at all -- it is a different group (4.3 rule 2).

    ``None`` is the only "unsupplied" value for the three optional groups; a JSON ``null`` literal
    would fail the migration's CHECKs, so an empty dict is normalised to ``None`` here.
    """
    if not str(space_id or '').strip():
        raise ProjectionError('WIKI_SPACE_ID_UNRESOLVED', 'space_id is empty')
    if not str(predicate or '').strip():
        raise ProjectionError('WIKI_PREDICATE_UNRESOLVED', 'predicate (metric / fact_kind) is empty')
    if not isinstance(entity_key, dict) or not entity_key:
        raise ProjectionError('WIKI_ENTITY_KEY_UNRESOLVED', 'entity_key must be a non-empty object')

    def optional(value):
        return value if isinstance(value, dict) and value else None

    return digest64({'space_id': str(space_id), 'entity_key': entity_key,
                     'predicate': str(predicate), 'applicability': optional(applicability),
                     'time_scope': optional(time_scope), 'record_scope': optional(record_scope),
                     'statistic': None if statistic is None or not str(statistic).strip()
                     else str(statistic)})


def _time_scope_of(payload: dict) -> dict | None:
    scope = payload.get('time_scope')
    if isinstance(scope, dict) and scope:
        return scope
    from_value, to_value = payload.get('valid_from'), payload.get('valid_to')
    if from_value is None and to_value is None:
        return None
    scope = {'from': from_value, 'to': to_value}
    if payload.get('timezone'):
        scope['timezone'] = payload['timezone']
    return scope


def _applicability_of(payload: dict) -> dict | None:
    applicability = payload.get('applicability')
    if isinstance(applicability, dict) and applicability:
        return applicability
    scope = {}
    for key in ('source_revision', 'valid_from', 'valid_to'):
        if payload.get(key) is not None:
            scope[key] = payload[key]
    return scope or None


def _record_scope_of(payload: dict, node_type: str | None) -> dict | None:
    scope = payload.get('record_scope')
    if isinstance(scope, dict) and scope:
        return scope
    scope = {}
    for key in RECORD_ID_FIELDS + ('batch_id', 'part_id', 'operation_no', 'quality_id', 'report_id',
                                   'row_id'):
        value = payload.get(key)
        if value is not None and str(value).strip():
            scope[key] = value
    if scope:
        return scope
    record_id = record_id_for(payload, node_type)
    return {'record_id': record_id} if record_id else None


def _statistic_of(payload: dict) -> str | None:
    _, value = _first_present(payload, ('statistic', 'method', 'quality_method'))
    return None if value is None else str(value)


def assertion_identity(space_id, payload, *, node_type=None, entity_key=None) -> dict:
    """The assertion (comparison) identity of a measurement-like node.

    ``entity_key`` prefers an explicit argument, then ``payload.entity_key`` (industrial conflict
    records carry exactly that object), then the four named-record fields, and finally the entity
    id alone -- each step is a real field, and the ``confirmation`` marker is carried along as
    WIKI_DOMAIN.md 4.3 / the migration's ``wiki_assertions.entity_key`` comment require.
    """
    payload = payload if isinstance(payload, dict) else {}
    resolved_entity = entity_key if isinstance(entity_key, dict) and entity_key else None
    if resolved_entity is None and isinstance(payload.get('entity_key'), dict):
        resolved_entity = payload['entity_key']
    if resolved_entity is None:
        resolved_entity = {}
        for key in ('part_id', 'batch_id', 'operation_no', 'measurement_id', 'entity_id',
                    'entity_name'):
            if payload.get(key) not in (None, ''):
                resolved_entity[key] = payload[key]
        if payload.get('entity_type'):
            resolved_entity['entity_type'] = payload['entity_type']
    if not resolved_entity:
        raise ProjectionError('WIKI_ENTITY_KEY_UNRESOLVED',
                              'the node names no entity; a comparison group cannot be formed')
    if 'confirmation' not in resolved_entity:
        resolved_entity = {**resolved_entity, 'confirmation': 'pending'}
    predicate, predicate_value = _first_present(payload, ('metric', 'fact_kind'))
    if predicate_value is None:
        raise ProjectionError('WIKI_PREDICATE_UNRESOLVED',
                              'the node names no metric / fact_kind')
    return {
        'comparison_key': comparison_key(
            space_id, resolved_entity, predicate_value, applicability=_applicability_of(payload),
            time_scope=_time_scope_of(payload), record_scope=_record_scope_of(payload, node_type),
            statistic=_statistic_of(payload)),
        'entity_key': resolved_entity,
        'predicate': str(predicate_value),
        'predicate_field': predicate,
        'raw_value': None if payload.get('raw_value') is None else str(payload['raw_value']),
        'raw_unit': payload.get('raw_unit') or payload.get('report_unit') or payload.get('unit'),
        'normalized_value': (None if payload.get('normalized_value') is None
                             else str(payload['normalized_value'])),
        'normalized_unit': payload.get('normalized_unit'),
        'time_scope': _time_scope_of(payload),
        'applicability': _applicability_of(payload),
        'record_scope': _record_scope_of(payload, node_type),
        'statistic': _statistic_of(payload),
    }


# ================================================================ verification accounting (7.2)

#: The literal ``datamind/agent.py:238`` and ``:629`` hard-code. Wiki must never rewrite it.
FULL_SEMANTIC_ENTAILMENT_NOT_VERIFIED = 'not_independently_verified'


def build_verification_accounting(*, source_integrity=None, binding=None, binding_problems=None,
                                  verification_contract=None, semantically_verified=None,
                                  validator_findings=None, authority_rule_id=None,
                                  full_semantic_entailment=None) -> dict:
    """The five separately recorded items of WIKI_API.md 7.2.

    Each item is recorded on its own evidence and never substitutes for another. Two of them are
    deliberately not "checked" from data this database has:

    * ``semantic_support`` is **always** ``checked: false`` with ``full_semantic_entailment``
      ``not_independently_verified`` -- that value is a hard-coded literal in the agent, so Wiki
      may not upgrade it;
    * ``source_authority`` is ``checked: false`` unless a caller passes a real registered rule id
      (WIKI_DOMAIN.md 5.2: Wiki must never auto-activate an authority rule).

    ``entity_binding`` is left ``checked: false`` and its missing fields are visible when the node
    carries no binding at all -- which is every node in this database (0 of 2760 carry
    ``payload.binding``).
    """
    problems = list(binding_problems or [])
    has_binding = isinstance(binding, dict) and bool(binding)
    semantic = full_semantic_entailment or FULL_SEMANTIC_ENTAILMENT_NOT_VERIFIED
    if not has_binding:
        entity_binding = {'checked': False}
    else:
        entity_binding = {'checked': True,
                          'binding_status': (binding.get('binding_status')
                                             or binding.get('status') or 'bound'),
                          'binding_problems': problems}
    return {
        'source_integrity': ({'checked': True, 'value': source_integrity}
                             if source_integrity else {'checked': False}),
        'entity_binding': entity_binding,
        'rule_verification': {'checked': True, 'verification_contract': verification_contract,
                              'semantically_verified': bool(semantically_verified),
                              'validator_findings': list(validator_findings or [])},
        'semantic_support': {'checked': False, 'full_semantic_entailment': semantic},
        'source_authority': {'checked': bool(authority_rule_id),
                             'authority_rule_id': authority_rule_id or None},
    }


def verification_for_claim(claim: dict | None, *, run_status=None) -> dict:
    """Map a ``claims`` row onto the assertion-verification record, without upgrading anything.

    The ``verified`` predicate is copied from ``datamind/agent.py:618-623``
    (``bound = binding_status == 'bound' and not binding_problems``; plus ``status ==
    'completed'``) and applied as an **input**, never re-derived from ``runs.status`` alone
    (``docs/WIKI_API.md`` 7.3 says so explicitly).

    ``claims.status='verified'`` is not "the fact is confirmed": with
    ``verification_contract='time_scope_only'`` the record proves only that a time range was
    normalised (``semantically_verified=false``). The contract name and the semantic flag are
    therefore carried through verbatim, and ``full_semantic_entailment`` keeps its literal.

    ``warned`` maps to ``unverified`` and never to ``verified``: in this database that status is
    used both for "demo data" and for "verification warning".
    """
    row = claim if isinstance(claim, dict) else {}
    verification = row.get('verification') if isinstance(row.get('verification'), dict) else {}
    status = str(row.get('status') or '').strip()
    external_status = str(row.get('run_status') or run_status or '').strip()
    binding = verification.get('binding') if isinstance(verification.get('binding'), dict) else None
    binding_problems = verification.get('binding_problems')
    bound = (binding is not None and binding.get('binding_status', 'bound') == 'bound'
             and not binding_problems)
    completed = external_status.upper() in ('COMPLETED', 'completed', '')

    if status == 'verified' and (bound or binding is None) and completed:
        assertion_status = 'verified'
    elif status == 'insufficient_evidence':
        assertion_status = 'insufficient'
    else:
        assertion_status = 'unverified'

    semantic_literal = verification.get('full_semantic_entailment')
    if semantic_literal is None and verification.get('semantic_entailment') is not None:
        semantic_literal = verification.get('semantic_entailment')
    authority_rule_id = verification.get('authority_rule_id')
    if authority_rule_id is None and binding is not None:
        authority_rule_id = binding.get('authority_rule_id')
    return {
        'status': assertion_status,
        'source_claim_status': status or None,
        'source_run_status': external_status or None,
        'checked_at': row.get('measured_at') or row.get('produced_at'),
        'checked_by': {'user_id': None, 'method': 'rule_engine'},
        'coverage': {'rule_version': verification.get('rule_version')},
        'rule_version': verification.get('rule_version'),
        'accounting': build_verification_accounting(
            source_integrity=verification.get('sql_hash_matches') and 'sql_hash 一致' or None,
            binding=binding, binding_problems=binding_problems,
            verification_contract=verification.get('verification_contract'),
            semantically_verified=verification.get('semantically_verified'),
            validator_findings=verification.get('validator_findings'),
            authority_rule_id=authority_rule_id,
            full_semantic_entailment=semantic_literal),
        'verification_contract': verification.get('verification_contract'),
        'semantically_verified': verification.get('semantically_verified'),
        'full_semantic_entailment': semantic_literal or FULL_SEMANTIC_ENTAILMENT_NOT_VERIFIED,
        'binding_persisted': binding is not None,
    }


def verification_for_check(payload: dict, *, run_status=None) -> dict:
    """Map an industrial ``VerificationCheck`` payload onto the same shape.

    ``agreement`` is the source-agreement verdict of the industrial rules, ``compliance`` the
    in-range verdict; both exist for every one of the 350 live rows. ``unresolved`` agreement is
    the only one that maps to ``conflicted`` -- ``needs_review`` is "a difference was recorded, a
    human has to look", which WIKI_DOMAIN.md 1.4 forbids reading as an undecided conflict.
    """
    payload = payload if isinstance(payload, dict) else {}
    agreement = str(payload.get('agreement') or '').strip()
    if agreement == 'unresolved':
        assertion_status = 'conflicted'
    elif agreement in ('consistent', 'resolved_equivalent'):
        assertion_status = 'verified'
    elif agreement == 'insufficient_evidence' or str(payload.get('compliance') or '') == \
            'insufficient_evidence':
        assertion_status = 'insufficient'
    else:
        assertion_status = 'unverified'
    rules = [rule for rule in (payload.get('rules') or []) if isinstance(rule, dict)]
    rule_ids = [str(rule.get('rule_id')) for rule in rules if rule.get('rule_id')]
    rule_versions = {str(rule.get('rule_version')) for rule in rules if rule.get('rule_version')}
    binding = payload_binding(payload)
    return {
        'status': assertion_status,
        'source_agreement': agreement or None,
        'source_compliance': payload.get('compliance'),
        'checked_at': payload.get('measured_at') or payload.get('produced_at'),
        'checked_by': {'user_id': None, 'method': 'rule_engine'},
        'coverage': {'entity_key': {key: payload.get(key) for key in
                                    ('part_id', 'batch_id', 'operation_no', 'measurement_id')
                                    if payload.get(key) not in (None, '')},
                     'metric': payload.get('metric')},
        'rule_version': sorted(rule_versions)[0] if len(rule_versions) == 1 else None,
        'accounting': build_verification_accounting(
            source_integrity=payload.get('report_value') is not None and 'report_value 已记录'
            or None,
            binding=binding,
            verification_contract=None,
            semantically_verified=False,
            validator_findings=rule_ids),
        'verification_contract': None,
        'semantically_verified': False,
        'full_semantic_entailment': FULL_SEMANTIC_ENTAILMENT_NOT_VERIFIED,
        'binding_persisted': binding is not None,
        'issues': [issue for issue in (payload.get('issues') or []) if isinstance(issue, dict)],
        'rules': rules,
    }


# ================================================================ provenance_incomplete (API 6)

def provenance_incomplete(*, applies: bool, missing_fields=(), reason_code=None,
                          detected_at=None) -> dict:
    """The ``provenance_incomplete`` object of WIKI_API.md 6.4.

    ``applies=false`` is ``{"applies": false}`` and nothing else: the migration makes this column
    ``NOT NULL`` and requires an object carrying ``applies``, and it explicitly contrasts it with
    ``conflict_records.selected_fact_ref`` by forbidding the JSON ``null`` literal here. So
    "nothing is missing" is a *stated* false, never an absent value.

    ``recoverable`` is false for every reason code in 6.4: a field that was never persisted cannot
    be inferred back, and the whole point of the marker is that guessing is forbidden.
    """
    if not applies:
        return {'applies': False}
    payload = {'applies': True, 'missing_fields': sorted(set(missing_fields)),
               'reason_code': reason_code, 'recoverable': False}
    if detected_at is not None:
        payload['detected_at'] = detected_at
    return payload


def edge_provenance_incomplete(*, detected_at=None) -> dict:
    """The marker every projected relation carries: the deployed ``evidence_edges`` has 6 columns.

    Measured: ``edge_id | run_id | src_evidence_id | dst_evidence_id | edge_type | weight``. The
    justification, the binding type and the confidence that migration 019 declares and
    ``evidence_graph.py:215-225`` accepts in memory are **not there**, so any field that depends
    on them is missing and stays missing.
    """
    return provenance_incomplete(applies=True, missing_fields=MISSING_EDGE_FIELDS,
                                 reason_code=REASON_EDGE_COLUMNS_ABSENT, detected_at=detected_at)


def node_provenance_incomplete(payload: dict, *, node_type=None, detected_at=None) -> dict:
    """The marker for one evidence node: an absent binding, an absent snapshot, or both."""
    payload = payload if isinstance(payload, dict) else {}
    missing, reasons = [], []
    if payload_binding(payload) is None:
        missing.append('node.binding')
        reasons.append(REASON_NODE_BINDING_NOT_PERSISTED)
    snapshot, _ = source_snapshot(payload, node_type)
    if snapshot is None:
        missing.append('node.source_sha256')
        reasons.append(REASON_SOURCE_SNAPSHOT_ABSENT)
    if not missing:
        return provenance_incomplete(applies=False)
    # One reason code per marker (the response shape is a single object), so the first gap in
    # contract order wins and every gap is still listed in missing_fields.
    return provenance_incomplete(applies=True, missing_fields=missing, reason_code=reasons[0],
                                 detected_at=detected_at)


def missing_edge_columns(column_names) -> list:
    """Which of migration 019's three edge metadata columns a live ``evidence_edges`` lacks.

    The projection must work on the narrow deployed shape (WIKI_API.md 10 constraint 6), so this
    is how a caller proves *at runtime* that ``provenance_incomplete`` applies instead of trusting
    the module's constant.
    """
    present = {str(name) for name in column_names or ()}
    return [field.split('.', 1)[1] for field in MISSING_EDGE_FIELDS
            if field.split('.', 1)[1] not in present]


# ================================================================ relations (3.2-3.4)

def relation_type_for(raw_edge_type) -> dict:
    """Map a raw ``edge_type`` onto the Wiki relation vocabulary, or refuse to.

    WIKI_DOMAIN.md 3.1: the vocabulary is the **union** of the two existing vocabularies, never a
    third one. An unrecognised type keeps ``raw_type``, gets ``relation_type=None``,
    ``supported=false`` and ``unsupported_reason='TYPE_NOT_IN_WIKI_VOCABULARY'`` -- it is still
    displayed (3.4 rule 4), just not mapped to the nearest known type and not allowed to take part
    in derivation or in a "verified" verdict.

    ``contradicts`` is the one legal rename: one meaning, two words since migration 019, and Wiki
    projects it as ``conflicts`` while keeping ``contradicts`` in ``raw_type`` (3.3).
    """
    raw = '' if raw_edge_type is None else str(raw_edge_type)
    if raw in PROJECTED_RELATION_TYPES:
        return {'raw_type': raw, 'relation_type': RELATION_RAW_ALIASES.get(raw, raw),
                'origin': 'projected', 'supported': True, 'unsupported_reason': None}
    if raw in DERIVED_RELATION_TYPES:
        # Not a raw edge word at all: a caller using a derived type as a raw type is asking for a
        # projection from an edge that cannot carry it.
        return {'raw_type': raw, 'relation_type': None, 'origin': 'projected', 'supported': False,
                'unsupported_reason': UNSUPPORTED_TYPE_REASON}
    return {'raw_type': raw, 'relation_type': None, 'origin': 'projected', 'supported': False,
            'unsupported_reason': UNSUPPORTED_TYPE_REASON}


def is_symmetric(relation_type) -> bool:
    return relation_type in SYMMETRIC_RELATION_TYPES


def canonical_endpoints(src_kind, src_id, dst_kind, dst_id, symmetric) -> tuple:
    """Put a symmetric relation in canonical endpoint order.

    ``wiki_relations`` stores the five undirected types with ``(src_kind, src_id) <= (dst_kind,
    dst_id)`` (a CHECK), so an unnormalised pair is an insert failure -- and, worse, two rows for
    one relation. A directed relation keeps its exact orientation: ``Claim --supports--> Chunk``
    is not the same statement as ``Chunk --supports--> Claim``.
    """
    if not symmetric:
        return (src_kind, src_id, dst_kind, dst_id)
    if (str(src_kind), str(src_id)) <= (str(dst_kind), str(dst_id)):
        return (src_kind, src_id, dst_kind, dst_id)
    return (dst_kind, dst_id, src_kind, src_id)


def endpoint_kind(resource_id) -> str | None:
    """``'entry'`` / ``'assertion'`` / ``'evidence_ref'`` from an id prefix, else ``None``."""
    if not isinstance(resource_id, str):
        return None
    prefix = resource_id.split('-', 1)[0]
    return {'wiki': 'entry', 'wassert': 'assertion', 'wev': 'evidence_ref'}.get(prefix)


def project_relation(raw_edge_type, src_id, dst_id, *, edge_id=None, run_id=None, weight=None,
                     src_kind=None, dst_kind=None, detected_at=None, basis_extra=None,
                     src_raw_id=None, dst_raw_id=None) -> dict:
    """One projected relation, shaped for ``wiki_relations``.

    ``src_id`` / ``dst_id`` must be ids the table admits: the migration's CHECK is literally
    ``^(wiki|wassert|wev)-[0-9a-f]{24}$``, so an ``evidence_edges`` endpoint (``ev-`` + hex16, or
    the content id ``n-`` + 20 hex, ``WIKI_DOMAIN.md`` 4.2 rule 2) **cannot** be stored as-is. Pass
    the ids that :func:`project_evidence_ref` produced -- the reusable ``wev-`` reference of each
    endpoint -- and put the raw per-run occurrence id in ``src_raw_id`` / ``dst_raw_id``, which
    lands in ``basis`` so the original record stays traceable. That split is the whole point of
    4.2 rule 2: one run = one occurrence record, one fingerprint = one reusable reference.

    ``entry_id`` / ``revision_id`` are **not** part of this object: the migration's composite
    foreign key binds a relation to the version that owns it, and versions are minted by the build
    step. What the projection layer owns is the part that can be decided from the evidence alone --
    the type, the raw type, the origin, the direction, the canonical order and the honest record
    of what is missing.
    """
    verdict = relation_type_for(raw_edge_type)
    relation_type = verdict['relation_type']
    resolved_src_kind = src_kind or endpoint_kind(src_id)
    resolved_dst_kind = dst_kind or endpoint_kind(dst_id)
    # The id FORMAT comes first: an ``ev-``/``n-`` endpoint (the per-run occurrence ids of
    # WIKI_DOMAIN.md 4.2 rule 2) is a format problem that the caller must fix by passing the
    # resolved ``wev-`` reference -- not an unknown kind.
    if not RELATION_ENDPOINT.match(str(src_id)) or not RELATION_ENDPOINT.match(str(dst_id)):
        raise ProjectionError('WIKI_RELATION_ENDPOINT_FORMAT',
                              '%s -> %s' % (src_id, dst_id))
    if resolved_src_kind is None or resolved_dst_kind is None:
        raise ProjectionError('WIKI_RELATION_ENDPOINT_UNKNOWN',
                              '%s -> %s' % (src_id, dst_id))
    if src_id == dst_id:
        raise ProjectionError('WIKI_RELATION_SELF_LOOP', str(src_id))
    symmetric = is_symmetric(relation_type)
    src_kind_out, src_id_out, dst_kind_out, dst_id_out = canonical_endpoints(
        resolved_src_kind, src_id, resolved_dst_kind, dst_id, symmetric)

    raw_type = verdict['raw_type']
    basis = {'source_table': 'evidence_edges', 'edge_id': edge_id, 'run_id': run_id,
             'raw_edge_type': raw_type, 'weight': None if weight is None else str(weight),
             'src_evidence_id': src_raw_id, 'dst_evidence_id': dst_raw_id}
    if len(raw_type) > RAW_TYPE_MAX:
        basis['raw_type_clipped_from'] = raw_type
    if basis_extra:
        basis.update(basis_extra)
    return {
        'relation_type': relation_type,
        'raw_type': raw_type[:RAW_TYPE_MAX] if raw_type else None,
        'origin': verdict['origin'],
        'is_symmetric': symmetric,
        'supported': verdict['supported'],
        'unsupported_reason': verdict['unsupported_reason'],
        'src_kind': src_kind_out, 'src_id': src_id_out,
        'dst_kind': dst_kind_out, 'dst_id': dst_id_out,
        'rule_version': DERIVATION_RULE_VERSION if verdict['origin'] == 'wiki_derived' else None,
        'derivation': None,
        'basis': basis,
        'basis_note': _relation_basis_note(verdict, run_id),
        'provenance_incomplete': edge_provenance_incomplete(detected_at=detected_at),
        'projection_version': PROJECTION_VERSION,
    }


def _relation_basis_note(verdict, run_id) -> str:
    parts = ['projected from evidence_edges.edge_type=%s' % verdict['raw_type']]
    if verdict['raw_type'] != (verdict['relation_type'] or ''):
        parts.append('projected as %s' % (verdict['relation_type'] or 'unsupported'))
    if run_id:
        parts.append('run %s' % run_id)
    return '; '.join(parts)


def project_run_relations(nodes, edges, space_id, *, run_id=None, detected_at=None) -> dict:
    """Project a whole run's edges, resolving each ``ev-`` endpoint to its reusable ``wev-`` id.

    This is the one place that wires the two halves together: :func:`project_evidence_ref` turns
    every node of the run into an evidence reference, and the resulting
    ``{evidence_id: evidence_ref_id}`` map is what :func:`project_relation` needs, because
    ``wiki_relations.src_id`` can only hold ``wiki-`` / ``wassert-`` / ``wev-`` ids.

    An edge whose endpoint has no projected reference is **dropped with a reason**, never emitted
    with a raw id: a relation pointing at an id the schema refuses is worse than a reported gap.
    """
    refs = [project_evidence_ref(space_id, node, run_id=run_id, detected_at=detected_at)
            for node in nodes or ()]
    id_map = {ref['evidence_id']: ref['evidence_ref_id'] for ref in refs
              if ref.get('evidence_id')}
    relations, dropped = [], []
    for edge in edges or ():
        raw_src = edge.get('src_evidence_id') if isinstance(edge, dict) else \
            getattr(edge, 'src_evidence_id', None)
        raw_dst = edge.get('dst_evidence_id') if isinstance(edge, dict) else \
            getattr(edge, 'dst_evidence_id', None)
        raw_type = edge.get('edge_type') if isinstance(edge, dict) else \
            getattr(edge, 'edge_type', None)
        edge_id = edge.get('edge_id') if isinstance(edge, dict) else getattr(edge, 'edge_id', None)
        weight = edge.get('weight') if isinstance(edge, dict) else getattr(edge, 'weight', None)
        src = id_map.get(raw_src)
        dst = id_map.get(raw_dst)
        if src is None or dst is None:
            dropped.append({'edge_id': edge_id, 'edge_type': raw_type,
                            'src_evidence_id': raw_src, 'dst_evidence_id': raw_dst,
                            'missing_endpoints': [value for value, resolved in
                                                  ((raw_src, src), (raw_dst, dst))
                                                  if resolved is None],
                            'reason_code': 'RELATION_ENDPOINT_NOT_PROJECTED'})
            continue
        relation = project_relation(raw_type, src, dst, edge_id=edge_id, run_id=run_id,
                                    weight=weight, detected_at=detected_at,
                                    src_raw_id=raw_src, dst_raw_id=raw_dst)
        relation['src_evidence_id'] = raw_src
        relation['dst_evidence_id'] = raw_dst
        relations.append(relation)
    return {'evidence_refs': refs, 'relations': relations, 'dropped_edges': dropped,
            'counts': {'evidence_refs': len(refs), 'relations': len(relations),
                       'dropped_edges': len(dropped),
                       'unsupported_relations': sum(1 for rel in relations
                                                    if not rel['supported'])}}


def project_derived_relation(relation_type, src_id, dst_id, *, inputs, src_kind=None,
                             dst_kind=None, basis_note=None) -> dict:
    """One of the eight Wiki-derived relations (3.2 #14-#21).

    These come from **no edge**, so ``raw_type`` must be NULL (the migration forbids it otherwise),
    ``origin`` must be ``wiki_derived``, and a ``rule_version`` plus a ``derivation`` record
    carrying the same ``rule_version`` are mandatory so a historical version can reproduce the
    derivation (WIKI_DOMAIN.md 3.3).
    """
    if relation_type not in DERIVED_RELATION_TYPES:
        raise ProjectionError('WIKI_DERIVED_TYPE_UNKNOWN', str(relation_type))
    resolved_src_kind = src_kind or endpoint_kind(src_id)
    resolved_dst_kind = dst_kind or endpoint_kind(dst_id)
    if resolved_src_kind is None or resolved_dst_kind is None:
        raise ProjectionError('WIKI_RELATION_ENDPOINT_UNKNOWN', '%s -> %s' % (src_id, dst_id))
    if not isinstance(inputs, (list, tuple)) or not inputs:
        raise ProjectionError('WIKI_DERIVATION_INPUTS_EMPTY', str(relation_type))
    symmetric = is_symmetric(relation_type)
    src_kind_out, src_id_out, dst_kind_out, dst_id_out = canonical_endpoints(
        resolved_src_kind, src_id, resolved_dst_kind, dst_id, symmetric)
    return {
        'relation_type': relation_type,
        'raw_type': None,
        'origin': 'wiki_derived',
        'is_symmetric': symmetric,
        'supported': True,
        'unsupported_reason': None,
        'src_kind': src_kind_out, 'src_id': src_id_out,
        'dst_kind': dst_kind_out, 'dst_id': dst_id_out,
        'rule_version': DERIVATION_RULE_VERSION,
        'derivation': {'rule_version': DERIVATION_RULE_VERSION, 'inputs': list(inputs)},
        'basis': {'source_table': None, 'edge_id': None, 'run_id': None, 'raw_edge_type': None},
        'basis_note': basis_note,
        'provenance_incomplete': provenance_incomplete(applies=False),
        'projection_version': PROJECTION_VERSION,
    }


# ================================================================ conflicts (3.5 / API 7.4)

def normalize_resolution(raw_status) -> str | None:
    """The resolution normalisation table of WIKI_API.md 7.4, applied and nothing more.

    ``needs_review`` and ``resolved_equivalent`` survive as themselves on purpose: both mean
    "there is a difference record", not "there is an undecided conflict". Only ``unresolved``
    (including the in-memory ``CONFLICT_UNRESOLVED``) is an undecided conflict.
    """
    if raw_status is None:
        return None
    return RESOLUTION_NORMALIZATION.get(str(raw_status).strip(), str(raw_status).strip())


def is_unresolved_conflict(conflict: dict) -> bool:
    """True only for an undecided conflict -- the single predicate the ``conflicted`` state uses."""
    return (conflict or {}).get('resolution') == UNRESOLVED_RESOLUTION


def _normalized_values_side(normalized_values: dict, side: str) -> tuple:
    """``(value, unit)`` for one side of an industrial record's ``normalized_values``.

    The real key names are ``production`` (the SQL side), ``report_raw`` and ``report_unit``
    (the document side) -- reading them as ``left`` / ``right`` would silently produce nothing,
    which is how a conflict ends up displayed without its values.
    """
    if not isinstance(normalized_values, dict):
        return None, None
    if side == 'production':
        return normalized_values.get('production'), normalized_values.get('production_unit')
    return normalized_values.get('report_raw'), normalized_values.get('report_unit')


def conflict_side(*, side, value, unit, normalized_value, normalized_unit, refs, node_id=None,
                  source_sha256=None, version=None, record_scope=None, source_kind=None,
                  source_ref=None, node_type=None) -> dict:
    """One side of a conflict: **both** the original value and the normalised one are kept.

    WIKI_DOMAIN.md 3.5 and requirement section 3.3 line 65 require every conflict to show both
    sides' values, sources and adjudication basis, and the industrial side genuinely has them
    (``left_fact_refs`` + ``right_fact_refs`` + ``normalized_values``), as does the cross-source
    side (``left`` / ``right``, ``evidence_graph.py:377-385``). Nothing is recomputed here: the
    unit conversions already happened in the rules engine.
    """
    return {'side': side, 'node_id': node_id, 'source_kind': source_kind, 'source_ref': source_ref,
            'node_type': node_type,
            'value': None if value is None else str(value),
            'unit': unit,
            'normalized_value': None if normalized_value is None else str(normalized_value),
            'normalized_unit': normalized_unit,
            'refs': [ref for ref in (refs or []) if jsonb_present(ref)],
            'source_sha256': source_sha256, 'version': version, 'record_scope': record_scope}


def _selected_ref(selected) -> dict | None:
    """The selected side's reference, or ``None``. Never picks a side.

    ``conflict_records.selected_fact_ref`` is a JSON ``null`` in all 175 rows, so this is the one
    place where the JSON-``null`` trap decides between "no side was selected" and "this row names
    a winner". Reading it with ``IS NOT NULL`` would claim every conflict was adjudicated.
    """
    if jsonb_absent(selected):
        return None
    if isinstance(selected, dict):
        return dict(selected)
    if isinstance(selected, list):
        first = selected[0]
        return dict(first) if isinstance(first, dict) else {'ref': selected}
    return {'ref': selected}


def conflict_from_industrial_row(row: dict) -> dict:
    """Normalise one ``conflict_records`` row (industrial path) onto the unified shape.

    Both representations are read: the table's own columns (``entity_key``, ``metric``, ``rule_id``,
    ``resolution_status``, ``left_fact_refs``, ``right_fact_refs``, ``normalized_values``,
    ``comparison_context``, ``selected_fact_ref``) **and** the ``payload`` snapshot, which carries
    the same record plus ``reason`` / ``rule_version`` / ``repair_attempt_refs``. The payload wins
    only where the column is absent -- neither source is preferred silently, so a row whose column
    and payload disagree still shows the column value that was actually indexed.
    """
    row = row if isinstance(row, dict) else {}
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    conflict_id = row.get('conflict_id') or payload.get('conflict_id')
    if not conflict_id:
        raise ProjectionError('WIKI_CONFLICT_ID_MISSING', 'neither the row nor its payload has one')
    entity_key = row.get('entity_key') if jsonb_present(row.get('entity_key')) else \
        payload.get('entity_key')
    metric = row.get('metric') or payload.get('metric')
    left_refs = row.get('left_fact_refs') if jsonb_present(row.get('left_fact_refs')) else \
        payload.get('left_fact_refs')
    right_refs = row.get('right_fact_refs') if jsonb_present(row.get('right_fact_refs')) else \
        payload.get('right_fact_refs')
    # Both sides' original values, from whichever representation actually carries them.
    if jsonb_absent(row.get('normalized_values')) and jsonb_absent(payload.get('normalized_values')):
        normalized_values = {}
    else:
        normalized_values = (row.get('normalized_values')
                             if jsonb_present(row.get('normalized_values'))
                             else payload.get('normalized_values')) or {}
    context = row.get('comparison_context') if jsonb_present(row.get('comparison_context')) else \
        payload.get('comparison_context')
    context = context if isinstance(context, dict) else {}
    left_value, left_unit = _normalized_values_side(normalized_values, 'production')
    right_value, right_unit = _normalized_values_side(normalized_values, 'report')
    # `normalized_values` names a unit for the report side only (`report_unit`); the production
    # side's unit is the comparison unit, because the industrial rules compare the two sides in
    # that one unit (`comparison_context.unit`). Reading it from anywhere else would be a guess.
    comparison_unit = context.get('unit')
    if left_unit is None:
        left_unit = comparison_unit
    if right_unit is None:
        right_unit = comparison_unit
    rule_id = row.get('rule_id') or payload.get('rule_id')
    resolution = normalize_resolution(row.get('resolution_status')
                                      or payload.get('resolution_status'))

    # The industrial record's three-state vocabulary maps onto the five conflict types of
    # WIKI_DOMAIN.md 3.5 unchanged: `unit_equivalence` is "converted equal, not a contradiction",
    # `measurement_context` / `version_overlap` are "recorded, a human has to look".
    conflict_type = row.get('conflict_type') or payload.get('conflict_type')
    context_type = context.get('kind') if isinstance(context.get('kind'), str) else None
    if conflict_type is None and resolution == 'resolved_equivalent':
        conflict_type = 'unit_equivalence'
    if conflict_type is None:
        conflict_type = context_type

    comparison_material = {
        'entity_key': entity_key if isinstance(entity_key, dict) else {},
        'metric': metric,
        'conflict_type': conflict_type,
        'rule_version': payload.get('rule_version'),
        'comparison_context': context,
    }
    authority = payload.get('authority') if isinstance(payload.get('authority'), dict) else None
    return {
        'conflict_id': str(conflict_id),
        'source_path': CONFLICT_PATH_INDUSTRIAL,
        'source_locator': {'table': 'conflict_records', 'run_id': row.get('run_id'),
                           'columns_read': sorted(key for key in row
                                                  if key != 'payload')},
        'comparison_key': digest64(comparison_material),
        'entity_key': entity_key if isinstance(entity_key, dict) else {},
        'metric': metric,
        'conflict_type': conflict_type,
        'resolution': resolution,
        'raw_resolution_status': row.get('resolution_status') or payload.get('resolution_status'),
        'rule_id': rule_id,
        'authority_rule_id': ((authority or {}).get('rule_id')
                              or payload.get('authority_rule_id')
                              or (rule_id if payload.get('authority') else None)),
        'authority_rule_ids': payload.get('authority_rule_ids') or [],
        'rule_version': payload.get('rule_version'),
        'reason': row.get('reason') or payload.get('reason'),
        'left': conflict_side(side='production', value=left_value, unit=left_unit,
                              normalized_value=left_value, normalized_unit=left_unit,
                              refs=left_refs, source_kind='sql',
                              source_ref=context.get('source_ref'),
                              record_scope={'measurement_id': (entity_key or {}).get(
                                  'measurement_id')} if isinstance(entity_key, dict) and
                              (entity_key or {}).get('measurement_id') else None),
        'right': conflict_side(side='report', value=right_value, unit=right_unit,
                               normalized_value=right_value, normalized_unit=right_unit,
                               refs=right_refs, source_kind='doc', source_ref=None,
                               record_scope=None),
        'selected': _selected_ref(row.get('selected_fact_ref')
                                  if 'selected_fact_ref' in row
                                  else payload.get('selected_fact_ref')),
        'comparison_context': context,
        'normalized_values': normalized_values if isinstance(normalized_values, dict) else {},
        'left_fact_refs': left_refs or [], 'right_fact_refs': right_refs or [],
        'repair_attempt_refs': payload.get('repair_attempt_refs') or [],
        'provenance_incomplete': provenance_incomplete(
            applies=jsonb_absent(row.get('comparison_context')) or jsonb_absent(
                row.get('normalized_values')),
            missing_fields=[field for field, value in (
                ('conflict.left_fact_refs', left_refs), ('conflict.right_fact_refs', right_refs),
                ('conflict.normalized_values', normalized_values),
                ('conflict.comparison_context', context)) if jsonb_absent(value)],
            reason_code=REASON_EDGE_COLUMNS_ABSENT),
        'projection_version': PROJECTION_VERSION,
    }


def conflict_from_cross_source(record: dict, *, run_id=None, event_id=None,
                               event_type=None) -> dict:
    """Normalise one ``cross_source_result.conflicts[i]`` record (cross-source path).

    This path writes **no table at all** (WIKI_DOMAIN.md 3.5): the record only exists inside
    ``trace_events.output_summary.cross_source_result``, so the locator points at that event and
    the caller must read the terminal event. The record's own shape is
    ``evidence_graph.detect_conflicts`` (``evidence_graph.py:454-476``): each side already carries
    ``node_id`` / ``value`` / ``unit`` / ``normalized_value`` / ``normalized_unit`` /
    ``source_sha256`` / ``version`` / ``record_scope``.

    This database has **zero** cross-source runs, so this function is covered by synthetic data
    only -- it must not be claimed as validated against real data.
    """
    record = record if isinstance(record, dict) else {}
    conflict_id = record.get('conflict_id')
    if not conflict_id:
        raise ProjectionError('WIKI_CONFLICT_ID_MISSING', 'cross-source record has no conflict_id')

    def side_of(raw, fallback_side):
        raw = raw if isinstance(raw, dict) else {}
        unit = raw.get('unit')
        return conflict_side(side=str(raw.get('side') or raw.get('target') or fallback_side),
                             value=raw.get('value'), unit=unit,
                             normalized_value=raw.get('normalized_value'),
                             normalized_unit=raw.get('normalized_unit'),
                             refs=[{'node_id': raw.get('node_id')}] if raw.get('node_id') else [],
                             node_id=raw.get('node_id'), source_kind=raw.get('source_kind'),
                             source_ref=raw.get('source_ref'),
                             node_type=raw.get('node_type'),
                             source_sha256=raw.get('source_sha256'),
                             version=raw.get('version'),
                             record_scope=raw.get('record_scope'))

    entity_id = record.get('entity_id')
    entity_label = record.get('entity_label')
    entity_key = {'entity_id': entity_id} if entity_id not in (None, '') else {}
    if entity_label:
        entity_key['entity_label'] = entity_label
    if entity_key:
        entity_key['confirmation'] = 'pending'
    left_raw, right_raw = record.get('left'), record.get('right')
    left_scope = (left_raw or {}).get('record_scope') if isinstance(left_raw, dict) else None
    right_scope = (right_raw or {}).get('record_scope') if isinstance(right_raw, dict) else None
    selected_node_id = record.get('selected_node_id')
    comparison_material = {
        'entity_id': entity_id, 'metric': record.get('metric'), 'version': record.get('version'),
        'record_scope': {'left': left_scope, 'right': right_scope},
    }
    conflict = {
        'conflict_id': str(conflict_id),
        'source_path': CONFLICT_PATH_CROSS_SOURCE,
        'source_locator': {'table': None, 'event_type': event_type, 'event_id': event_id,
                           'run_id': run_id,
                           'output_summary_key': 'cross_source_result.conflicts'},
        'comparison_key': digest64(comparison_material),
        'entity_key': entity_key,
        'metric': record.get('metric'),
        'conflict_type': record.get('conflict_type'),
        'resolution': normalize_resolution(record.get('resolution_status')),
        'raw_resolution_status': record.get('resolution_status'),
        'rule_id': record.get('rule_id'),
        'authority_rule_id': record.get('authority_rule_id'),
        'authority_rule_ids': [],
        'rule_version': record.get('rule_version') or record.get('version'),
        'reason': record.get('reason') or record.get('rule_id'),
        'left': side_of(left_raw, 'production'),
        'right': side_of(right_raw, 'report'),
        'selected': ({'node_id': selected_node_id} if selected_node_id else None),
        'comparison_context': {'metric': record.get('metric'), 'version': record.get('version'),
                               'entity_label': entity_label},
        'normalized_values': {},
        'left_fact_refs': [], 'right_fact_refs': [],
        'repair_attempt_refs': [],
        # The cross-source record is complete in memory (both sides, both normalised values); what
        # is missing is that it was never persisted anywhere but the trace event.
        'provenance_incomplete': {'applies': False},
        'projection_version': PROJECTION_VERSION,
    }
    # `graph_digest` is a cross-source-only field, and the object shape must stay identical
    # between the two paths, so it is added only when the record actually carries one.
    if record.get('graph_digest'):
        conflict['graph_digest'] = record['graph_digest']
    return conflict


def cross_source_result_conflicts(output_summary: dict) -> dict:
    """Split a terminal event's ``cross_source_result`` into conflicts and unresolved conflicts.

    ``agent.py:195-202`` puts the whole cross-source result under ``cross_source_result``, and
    ``cross_source.py:1026-1027`` fills ``conflicts`` and ``unresolved_conflicts`` from the same
    list. ``unresolved_conflicts`` is recomputed here from the normalised ``resolution`` rather
    than trusted as a pre-filtered list, because a list that was filtered elsewhere is exactly the
    kind of value that silently changes meaning.

    A payload without the key returns ``present: False`` -- and an empty result is *not* the same
    statement as a missing one, so the two are distinguished.
    """
    summary = output_summary if isinstance(output_summary, dict) else {}
    raw = summary.get('cross_source_result')
    if not isinstance(raw, dict):
        return {'present': False, 'conflicts': [], 'unresolved_conflicts': [], 'declared_count': None,
                'declared_unresolved_count': None}
    records = [record for record in (raw.get('conflicts') or []) if isinstance(record, dict)]
    conflicts = [conflict_from_cross_source(record) for record in records]
    return {
        'present': True,
        'conflicts': conflicts,
        'unresolved_conflicts': [c for c in conflicts if is_unresolved_conflict(c)],
        'declared_count': len(records),
        'declared_unresolved_count': len(raw.get('unresolved_conflicts') or []),
        'status': raw.get('status'),
        'error_code': raw.get('error_code'),
        'graph_digest': (raw.get('graph_summary') or {}).get('graph_digest')
        if isinstance(raw.get('graph_summary'), dict) else None,
    }


def industrial_conflicts_unresolved(row: dict) -> bool:
    """The single predicate for "this run has an undecided conflict" (WIKI_API.md 7.3/7.4).

    ``conflict_records`` has 175 rows and only 35 of them are ``unresolved``; the other 140 are
    ``needs_review`` (105) or ``resolved_equivalent`` (35). Reading "there is a conflict_records
    row" as "there is an undecided conflict" would mark 5x too many claims conflicted.
    """
    row = row if isinstance(row, dict) else {}
    return normalize_resolution(row.get('resolution_status')) == UNRESOLVED_RESOLUTION


def conflicts_for_run(industrial_rows=(), output_summaries=(), *, run_id=None) -> dict:
    """Read both conflict paths for one run and return them in one structure.

    The return value keeps the two paths *labelled* rather than merged into one undifferentiated
    list: ``industrial`` rows come from ``conflict_records``, ``cross_source`` from the terminal
    trace events. Merging them is the caller's presentation decision, and the ``source_path``
    field on every conflict keeps it answerable afterwards.
    """
    industrial = [conflict_from_industrial_row(row) for row in industrial_rows or ()]
    cross_source = []
    events = []
    for summary in output_summaries or ():
        split = cross_source_result_conflicts(summary)
        events.append(split)
        cross_source.extend(split['conflicts'])
    conflicts = industrial + cross_source
    return {
        'run_id': run_id,
        'industrial': industrial,
        'cross_source': cross_source,
        'cross_source_events': events,
        'conflicts': conflicts,
        'unresolved_conflicts': [c for c in conflicts if is_unresolved_conflict(c)],
        'has_unresolved_conflict': any(is_unresolved_conflict(c) for c in conflicts),
        'counts': {'industrial': len(industrial), 'cross_source': len(cross_source),
                   'unresolved': sum(1 for c in conflicts if is_unresolved_conflict(c)),
                   'needs_review': sum(1 for c in conflicts
                                       if c['resolution'] == 'needs_review'),
                   'resolved_equivalent': sum(1 for c in conflicts
                                              if c['resolution'] == 'resolved_equivalent')},
    }


# ================================================================ evidence reference projection

def _version_of(payload: dict) -> str | None:
    _, value = _first_present(payload, ('version', 'recorded_revision', 'applicable_revision',
                                        'source_revision', 'document_version'))
    return None if value is None else str(value)


def project_evidence_ref(space_id, node, *, run_id=None, binding=None, detected_at=None) -> dict:
    """Project one ``evidence_nodes`` row onto a ``wiki_evidence_refs`` row (plus a render block).

    The identity rules are the point of this function, so the three ways they can fail are all
    explicit and none of them is papered over:

    * **no snapshot** -> the fingerprint is run-scoped and the run is pinned
      (``run_scoped = (source_sha256 IS NULL)``, WIKI_DOMAIN.md 4.2 rule 1);
    * **no trustworthy source kind** -> the reference is ``run_scoped`` with
      ``SOURCE_SNAPSHOT_ABSENT`` rather than a guessed ``'doc'``/``'sql'`` (``source_kind`` is a
      closed vocabulary in the migration, so a guess would also be a lie with a schema behind it);
    * **no buildable locator** -> ``locator_kind='text_span'`` with the record id as its anchor so
      the NOT NULL locator column still holds something true, and ``source_validity_hint`` says the
      locator cannot be resolved (WIKI_API.md's ``WIKI_LOCATOR_UNAVAILABLE``).

    ``render`` carries the field names ``NodeDetail.vue`` actually reads, so the nine node types
    keep rendering after projection instead of collapsing into a generic JSON blob.
    """
    payload = node_payload(node)
    raw_type = (node.get('node_type') if isinstance(node, dict) else
                getattr(node, 'node_type', None))
    ref_key = (node.get('ref_key') if isinstance(node, dict) else getattr(node, 'ref_key', None))
    evidence_id = (node.get('evidence_id') if isinstance(node, dict) else
                   getattr(node, 'evidence_id', None))
    node_run_id = run_id or (node.get('run_id') if isinstance(node, dict) else
                             getattr(node, 'run_id', None))
    verdict = normalize_node_type(raw_type)
    node_type = verdict['node_type']
    resolved_binding = binding if isinstance(binding, dict) else payload_binding(payload)

    source_ref = source_ref_for(payload, node_type, ref_key)
    source_kind = source_kind_for(payload, node_type)
    snapshot, snapshot_field = source_snapshot(payload, node_type)
    excerpt = excerpt_of(payload, node_type)
    record_sha256 = record_sha256_for(payload, node_type, excerpt)
    record_id = record_id_for(payload, node_type, ref_key)
    locator_kind, locator = locator_for(payload, node_type, source_ref, ref_key)
    unresolved_locator = locator is None
    if unresolved_locator:
        # Keep the column's NOT NULL honest: a text_span anchored on the record id is a true
        # statement ("this is a span of this record"), unlike a fabricated pdf_region.
        locator_kind = 'text_span'
        locator = {'block_id': str(record_id or ref_key or evidence_id or 'unknown')}

    # `source_kind` and `source_ref` are mandatory columns. A node that names neither (a Claim, a
    # VerificationCheck) is carried by its run, and the run is pinned so the reference cannot be
    # reused by another run and silently acquire that run's source.
    if source_kind is None or not str(source_ref or '').strip():
        source_kind = source_kind or 'doc'
        source_ref = str(source_ref or (node_run_id or evidence_id or 'unresolved'))
        force_run_scoped = True
    else:
        force_run_scoped = snapshot is None

    if force_run_scoped:
        identity = evidence_identity(space_id, source_kind, source_ref, None, locator_kind, locator,
                                     record_sha256, run_id=node_run_id, run_scoped=True)
    else:
        identity = evidence_identity(space_id, source_kind, source_ref, snapshot, locator_kind,
                                     locator, record_sha256, run_scoped=False)

    provenance = node_provenance_incomplete(payload, node_type=node_type, detected_at=detected_at)
    if unresolved_locator and provenance['applies'] is False:
        provenance = provenance_incomplete(applies=True, missing_fields=['ref.locator'],
                                           reason_code=REASON_SOURCE_SNAPSHOT_ABSENT,
                                           detected_at=detected_at)
    elif unresolved_locator:
        provenance = {**provenance,
                      'missing_fields': sorted(set(provenance['missing_fields'])
                                               | {'ref.locator'})}

    return {
        **identity,
        'space_id': str(space_id),
        'evidence_id': evidence_id,
        'run_id': node_run_id,
        'raw_node_type': verdict['raw_type'],
        'node_type': node_type,
        'supported': verdict['supported'],
        'unsupported_reason': verdict['unsupported_reason'],
        'raw_node_types': raw_node_types_for(verdict['raw_type']),
        'excerpt': excerpt,
        'record_id': record_id,
        'source_snapshot_field': snapshot_field,
        'version': _version_of(payload),
        'unit': unit_of(payload),
        'record_scope': _record_scope_of(payload, node_type),
        'time_scope': _time_scope_of(payload),
        'binding': resolved_binding,
        'binding_persisted': resolved_binding is not None,
        'provenance_incomplete': provenance,
        'locator_resolved': not unresolved_locator,
        'source_validity_hint': 'WIKI_LOCATOR_UNAVAILABLE' if unresolved_locator else None,
        'projection_version': PROJECTION_VERSION,
        'render': render_for_node(raw_type, payload, ref_key=ref_key, node_type=node_type),
    }


def render_for_node(raw_node_type, payload, *, ref_key=None, node_type=None) -> dict:
    """The render block: exactly the fields ``NodeDetail.vue`` reads for this node type.

    Every key is a key the frontend already looks up (the file's own header comment at
    ``NodeDetail.vue:22-46`` lists them type by type), so a projected node renders like a live
    one. Keys that the payload does not have are omitted rather than filled with a placeholder:
    ``NodeDetail.vue`` renders a missing field as "—", and an invented empty string would be a
    claim that the source said nothing.
    """
    payload = payload if isinstance(payload, dict) else {}
    resolved = node_type or normalize_node_type(raw_node_type)['node_type']
    render = {'node_type': raw_node_type, 'ref_key': ref_key}
    fields = RENDER_FIELDS.get(resolved or '', ())
    for key in fields:
        if key in payload:
            render[key] = payload[key]
    render['payload'] = payload
    render['fallback_json'] = resolved is None
    return render


#: ``NodeDetail.vue``'s real reads per node type (lines 22-46 and the template branches).
RENDER_FIELDS = {
    'claim': ('text',),
    'sql_query': ('sql', 'bound_params', 'bound_params_redacted', 'sql_hash', 'source_snapshot_id'),
    'sql_row': ('batch_id', 'part_id', 'produced_at', 'recorded_revision', 'measurement_id',
                'operation_no', 'metric', 'value', 'unit', 'measured_at', 'method', 'quality_id',
                'report_id', 'quality_method', 'columns', 'rows', 'row_count'),
    'measurement': ('batch_id', 'part_id', 'produced_at', 'recorded_revision', 'measurement_id',
                    'operation_no', 'metric', 'value', 'unit', 'measured_at', 'method', 'quality_id',
                    'report_id', 'quality_method'),
    'document': ('title', 'document_id', 'document_version', 'origin', 'source_sha256',
                 'document_sha256', 'source_url'),
    'doc_block': ('quote', 'excerpt', 'text', 'title', 'page_start', 'page_end', 'page_no',
                  'section_path', 'retrieval_score', 'used_in_answer', 'page_url', 'source_url'),
    'document_fact': ('fact_kind', 'entity_name', 'entity_type', 'entity_id', 'operation_no',
                      'batch_id', 'raw_value', 'raw_unit', 'raw_header', 'normalized_value',
                      'normalized_unit', 'normalized_range', 'metric', 'method', 'measured_at',
                      'report_id', 'valid_from', 'valid_to', 'source_revision', 'approval_status',
                      'provenance', 'source_url', 'evidence_refs'),
    'verification_check': ('compliance', 'agreement', 'batch_id', 'part_id', 'operation_no',
                           'measurement_id', 'value', 'unit', 'normalized_value',
                           'normalized_unit', 'report_value', 'report_unit', 'recorded_revision',
                           'applicable_revision', 'measured_at', 'produced_at', 'method',
                           'report_id', 'report_fact_id', 'requirement_fact_id', 'issues', 'rules'),
    'conflict_record': ('conflict_id', 'conflict_type', 'metric', 'resolution_status', 'rule_id',
                        'rule_version', 'entity_key', 'comparison_context', 'normalized_values',
                        'left_fact_refs', 'right_fact_refs', 'selected_fact_ref', 'reason'),
    'authority_rule': ('rule_id', 'rule_version', 'registration', 'creator_label', 'approver_label',
                       'approval_reason', 'event_id'),
    'spec_clause': ('clause_id', 'text', 'metric', 'value', 'unit', 'valid_from', 'valid_to',
                    'entity_id', 'page_no', 'bbox', 'block_id'),
}


# ================================================================ derived-relation plans (3.3)

def _entry_key_for_entity(payload: dict) -> tuple:
    """``(key_part, confirmation)`` of the entity a measurement-like node names."""
    _, name = _first_present(payload, ('entity_id', 'part_id', 'entity_name'))
    if name is None:
        return None, None
    return entity_key_part(payload.get('entity_type'), payload.get('entity_id') or name,
                           confirmation='pending')


def _metric_key_for(payload: dict) -> str | None:
    _, metric = _first_present(payload, ('metric',))
    if metric is None:
        return None
    try:
        return metric_key_part(metric)
    except ProjectionError:
        return None


def plan_wiki_derived_relations(nodes, *, space_id=None, namespace=None, run_ids=None) -> dict:
    """Plan the eight Wiki-derived relations (3.2 #14-#21) from real payload fields.

    This returns **plans**, not stored relations, and it says so: every plan carries
    ``deferred: true`` and ``deferred_reason='ENTRY_AND_ASSERTION_IDS_ASSIGNED_AT_BUILD_TIME'``.
    The reason is structural rather than cautious -- a Wiki-derived relation's endpoints are
    ``wiki-``/``wassert-`` ids (the migration's ``src_id`` CHECK admits nothing else), and those
    are minted by the build step. What the projection layer can do -- and what it does here -- is
    name the derivation from the actual fields, so the build step has nothing left to invent.

    ``run_ids`` limits ``restates`` to the runs being projected. Pass it whenever the node list is
    wider than one build: ``restates`` is a *comparison* between two claims, so running it over the
    whole database pairs every measurement node with every other one that happens to share a
    comparison key, which is a quadratic list that means nothing (measured: 11 305 pairs over the
    full 2 760-node corpus, versus the handful a real two-run comparison produces). When ``run_ids``
    is ``None`` every run in ``nodes`` participates and that is reported in
    ``restates_scope``.

    The eight, with the field each one actually reads (WIKI_DOMAIN.md 3.3's table):

    ==================  ==========================================================
    ``measures``        ``DocumentFact.entity_id`` + ``.metric``; ``SQLRow.part_id`` + ``.metric``
    ``applies_to``      a fact's ``valid_from``/``valid_to`` against an entity's time scope
    ``scoped_by``       the same pair, opposite direction
    ``threshold_of``    ``DocumentFact.normalized_range`` on a ``fact_kind='requirement'`` row
    ``about``           a claim's ``text`` mapped through the entity / metric it names
    ``justified_by``    ``VerificationCheck.requirement_fact_id`` / ``.report_fact_id``
    ``supersedes``      two norms, same business key, later ``valid_from`` -- never a revision
                        string comparison
    ``restates``        two claims with the same ``comparison_key`` under different ``run_id``
    ==================  ==========================================================
    """
    plans = []
    seen = set()
    scope = None if run_ids is None else {str(value) for value in run_ids}
    measurements, requirements, checks, claims = [], [], [], []
    for node in nodes or ():
        payload = node_payload(node)
        verdict = normalize_node_type(node.get('node_type') if isinstance(node, dict) else
                                      getattr(node, 'node_type', None))
        if not verdict['supported']:
            # 3.4 rule 4: an unsupported node type takes no part in derivation.
            continue
        node_type = verdict['node_type']
        run_id = node.get('run_id') if isinstance(node, dict) else getattr(node, 'run_id', None)
        evidence_id = node.get('evidence_id') if isinstance(node, dict) else \
            getattr(node, 'evidence_id', None)
        if scope is not None and str(run_id) not in scope:
            continue
        record = (node_type, evidence_id, payload, run_id)
        if node_type == 'document_fact':
            if str(payload.get('fact_kind') or '') == 'requirement':
                requirements.append(record)
            else:
                measurements.append(record)
        elif node_type in ('measurement', 'sql_row'):
            measurements.append(record)
        elif node_type == 'verification_check':
            checks.append(record)
        elif node_type == 'claim':
            claims.append(record)

    def add(relation_type, src_key, dst_key, inputs, basis_note):
        marker = (relation_type, src_key, dst_key)
        if marker in seen:
            return
        seen.add(marker)
        plans.append({'relation_type': relation_type, 'src_key': src_key, 'dst_key': dst_key,
                      'origin': 'wiki_derived', 'rule_version': DERIVATION_RULE_VERSION,
                      'derivation': {'rule_version': DERIVATION_RULE_VERSION,
                                     'inputs': list(inputs)},
                      'basis_note': basis_note, 'deferred': True,
                      'deferred_reason': 'ENTRY_AND_ASSERTION_IDS_ASSIGNED_AT_BUILD_TIME'})

    # measures: entity <-> metric, from the pairs a measurement-like fact actually states.
    for node_type, evidence_id, payload, run_id in measurements:
        entity_part, confirmation = _entry_key_for_entity(payload)
        metric_part = _metric_key_for(payload)
        if not entity_part or not metric_part:
            continue
        add('measures', ('business_entity', entity_part, confirmation),
            ('metric', metric_part, 'confirmed'),
            [{'node_type': node_type, 'evidence_id': evidence_id,
              'fields': ['entity_id', 'metric']}],
            'entity and metric co-occur on one %s node' % node_type)

    # threshold_of / applies_to / scoped_by: a requirement states a limit for a metric and is
    # applicable to an entity over a validity window.
    for node_type, evidence_id, payload, run_id in requirements:
        metric_part = _metric_key_for(payload)
        entity_part, confirmation = _entry_key_for_entity(payload)
        window = {'valid_from': payload.get('valid_from'), 'valid_to': payload.get('valid_to')}
        if metric_part and jsonb_present(payload.get('normalized_range')):
            add('threshold_of', ('norm', payload.get('fact_id') or evidence_id),
                ('metric', metric_part, 'confirmed'),
                [{'node_type': node_type, 'evidence_id': evidence_id,
                  'fields': ['normalized_range', 'fact_kind', 'metric']}],
                'a requirement states normalized_range for this metric')
        if metric_part and entity_part and (window['valid_from'] or window['valid_to']):
            add('applies_to', ('norm', payload.get('fact_id') or evidence_id),
                ('business_entity', entity_part, confirmation),
                [{'node_type': node_type, 'evidence_id': evidence_id,
                  'fields': ['valid_from', 'valid_to', 'entity_id']}],
                'the norm validity window covers this entity')
            add('scoped_by', ('business_entity', entity_part, confirmation),
                ('norm', payload.get('fact_id') or evidence_id),
                [{'node_type': node_type, 'evidence_id': evidence_id,
                  'fields': ['valid_from', 'valid_to', 'entity_id']}],
                'the same pair, opposite direction')

    # supersedes: two norms with the same key part (document + revision slot) where the later
    # valid_from wins. WIKI_DOMAIN.md 3.3 is explicit: never a revision-string comparison.
    by_document = {}
    for node_type, evidence_id, payload, run_id in requirements:
        document = payload.get('document_id')
        if document:
            by_document.setdefault(str(document), []).append((evidence_id, payload))
    for document, entries in sorted(by_document.items()):
        ordered = sorted(entries, key=lambda item: str(item[1].get('valid_from') or ''))
        for index, (older_id, older) in enumerate(ordered):
            for newer_id, newer in ordered[index + 1:]:
                if str(newer.get('valid_from') or '') <= str(older.get('valid_from') or ''):
                    continue
                add('supersedes', ('norm', newer.get('fact_id') or newer_id),
                    ('norm', older.get('fact_id') or older_id),
                    [{'document_id': document,
                      'later': {'fact_id': newer.get('fact_id'), 'valid_from':
                                newer.get('valid_from')},
                      'earlier': {'fact_id': older.get('fact_id'), 'valid_from':
                                  older.get('valid_from')}}],
                    'later valid_from under the same document; revision strings are not compared')

    # justified_by: the check names the requirement / report fact it actually used.
    for node_type, evidence_id, payload, run_id in checks:
        for key in ('requirement_fact_id', 'report_fact_id'):
            fact_id = payload.get(key)
            if fact_id in (None, ''):
                continue
            add('justified_by', ('conclusion', payload.get('measurement_id') or evidence_id),
                ('norm' if key == 'requirement_fact_id' else 'document', fact_id),
                [{'node_type': node_type, 'evidence_id': evidence_id, 'fields': [key]}],
                'the verification check used this fact (%s)' % key)

    # about: a claim points at the entity / metric its own comparison group names.
    for node_type, evidence_id, payload, run_id in claims:
        entity_part, confirmation = _entry_key_for_entity(payload)
        metric_part = _metric_key_for(payload)
        if entity_part:
            add('about', ('conclusion', payload.get('claim_id') or evidence_id),
                ('business_entity', entity_part, confirmation),
                [{'node_type': node_type, 'evidence_id': evidence_id, 'fields': ['entity_id']}],
                'the claim names this entity')
        if metric_part:
            add('about', ('conclusion', payload.get('claim_id') or evidence_id),
                ('metric', metric_part, 'confirmed'),
                [{'node_type': node_type, 'evidence_id': evidence_id, 'fields': ['metric']}],
                'the claim names this metric')

    # restates: the same comparison_key under two different runs. Comparison identity is
    # space-scoped (4.3), so without a space the hash would describe a group that exists in no
    # space -- the derivation is then skipped *and reported as skipped*, not silently empty.
    by_comparison = {}
    restates_skipped = None if space_id else 'SPACE_ID_REQUIRED_FOR_COMPARISON_KEY'
    for node_type, evidence_id, payload, run_id in (measurements if space_id else ()):
        try:
            identity = assertion_identity(str(space_id), payload, node_type=node_type)
        except ProjectionError:
            continue
        by_comparison.setdefault(identity['comparison_key'], []).append(
            (run_id, evidence_id, identity['comparison_key']))
    for comparison, entries in sorted(by_comparison.items()):
        runs = {str(run) for run, _, _ in entries}
        if len(runs) < 2:
            continue
        ordered = sorted(entries, key=lambda item: (str(item[0]), str(item[1])))
        for index, (run_a, id_a, _) in enumerate(ordered):
            for run_b, id_b, _ in ordered[index + 1:]:
                if str(run_a) == str(run_b):
                    continue
                add('restates', ('assertion', id_a), ('assertion', id_b),
                    [{'comparison_key': comparison, 'runs': [run_a, run_b]}],
                    'the same comparison_key under two different runs')
    return {'rule_version': DERIVATION_RULE_VERSION, 'namespace': namespace,
            'plans': plans, 'counts': _plan_counts(plans),
            'restates_skipped_reason': restates_skipped,
            'restates_scope': ('run_ids' if scope is not None else 'all_nodes_supplied'),
            'runs_considered': sorted({str(record[3]) for record in
                                       measurements + requirements + checks + claims})}


def _plan_counts(plans) -> dict:
    counts = {}
    for plan in plans:
        counts[plan['relation_type']] = counts.get(plan['relation_type'], 0) + 1
    return counts


# ================================================================ run-level projection summary

def projection_summary(nodes, edges, *, space_id=None, namespace=None) -> dict:
    """Projection health for one run: what mapped, what did not, and what is incomplete.

    WIKI_API.md 8.2's ``WIKI_NO_EVIDENCE`` (read succeeded, nothing to project) and
    ``WIKI_LOAD_FAILED`` (the read failed) must stay distinguishable, so "zero nodes" is reported
    as a fact of the data here and never as an error.
    """
    node_rows = list(nodes or ())
    edge_rows = list(edges or ())
    unsupported_nodes, supported_nodes = [], []
    entry_types, evidence_refs = {}, []
    for node in node_rows:
        payload = node_payload(node)
        raw_type = node.get('node_type') if isinstance(node, dict) else getattr(node, 'node_type',
                                                                             None)
        verdict = normalize_node_type(raw_type)
        if verdict['supported']:
            supported_nodes.append(raw_type)
        else:
            unsupported_nodes.append(raw_type)
        entry_type = entry_type_for_node(verdict['node_type'], payload)
        if entry_type:
            entry_types[entry_type] = entry_types.get(entry_type, 0) + 1
        if space_id:
            evidence_refs.append(project_evidence_ref(space_id, node))
    relation_types, unsupported_edges = {}, []
    for edge in edge_rows:
        raw = edge.get('edge_type') if isinstance(edge, dict) else getattr(edge, 'edge_type', None)
        verdict = relation_type_for(raw)
        if verdict['supported']:
            relation_types[verdict['relation_type']] = \
                relation_types.get(verdict['relation_type'], 0) + 1
        else:
            unsupported_edges.append(raw)
    return {
        'projection_version': PROJECTION_VERSION,
        'node_count': len(node_rows),
        'edge_count': len(edge_rows),
        'no_evidence': not node_rows,
        'supported_node_count': len(supported_nodes),
        'unsupported_node_count': len(unsupported_nodes),
        'unsupported_node_types': sorted({str(value) for value in unsupported_nodes}),
        'entry_type_counts': entry_types,
        'evidence_ref_count': len(evidence_refs),
        'run_scoped_evidence_ref_count': sum(1 for ref in evidence_refs if ref['run_scoped']),
        'relation_type_counts': relation_types,
        'unsupported_edge_types': sorted({str(value) for value in unsupported_edges}),
        'type_not_in_wiki_vocabulary': len(unsupported_nodes) + len(unsupported_edges),
    }


def deferred_derivation_summary(plans: dict) -> dict:
    """How many of the eight derived relations a node set can support, and which are absent.

    WIKI_DOMAIN.md 3.4 rule 6 allows a vocabulary to grow and a re-read to re-judge ``supported``
    without touching a historical version, so a projection that cannot derive a relation should
    say which one is missing rather than produce an empty list that looks like "there are none".
    """
    counts = (plans or {}).get('counts') or {}
    return {'planned': counts,
            'absent': sorted(DERIVED_RELATION_TYPES - set(counts)),
            'deferred': sum(counts.values()),
            'deferred_reason': 'ENTRY_AND_ASSERTION_IDS_ASSIGNED_AT_BUILD_TIME'}
