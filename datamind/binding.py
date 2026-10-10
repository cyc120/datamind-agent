"""Version-bound evidence binding: units, versions, time scopes and entity identity.

This module is the single place where the four deterministic semantics that used to live
inside :mod:`datamind.industrial_rules` are defined:

* **unit verification** — :func:`normalize_unit` / :func:`unit_status` only perform
  *registered, deterministic* equivalences (an exact ``Decimal`` affine transform). Anything
  that is not a terminated decimal conversion (for example ``s -> min``) is refused and the
  caller marks the claim ``unit_unknown`` instead of inventing a rounded value.
* **version applicability** — :func:`version_applies` keeps the industrial half-open rule
  ``valid_from <= recorded_at < valid_to`` and raises on a timestamp without a timezone, so a
  caller can record ``VALIDITY_UNKNOWN`` rather than guess.
* **time scope overlap** — :func:`time_scope_overlap` returns ``True`` / ``False`` / ``None``
  (unknown) and never treats two open-ended scopes as overlapping evidence.
* **entity identity** — :func:`entity_bind` normalises a surface label through the
  ``entity_aliases`` table's *approved* rows (read-only) and refuses to pick a canonical id when
  one alias maps to several of them.

The module has no model, network or gold input. ``build_binding`` assembles the seven-field
binding record ``{entity_id, entity_label, version, unit, time_scope, source_ref, source_sha256}``
that every numeric cross-source claim must carry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import json
import re

BINDING_VERSION = 'binding-v1'
RULE_VERSION = 'binding-rules-v1'

#: Every claim binding must carry all seven fields; a missing one is a binding failure, never a
#: silently defaulted value.
BINDING_FIELDS = ('entity_id', 'entity_label', 'version', 'unit', 'time_scope', 'source_ref',
                  'source_sha256')

# unit -> (dimension, factor to the dimension's base unit, offset after scaling)
# Conversions are applied as ``base = value * factor + offset`` and back as
# ``result = (base - target.offset) / target.factor``; back-division is only accepted when it is
# an exact terminated decimal (denominator built from 2s and 5s).
_UNITS = {
    'degC': ('temperature', Decimal('1'), Decimal('0')),
    'K': ('temperature', Decimal('1'), Decimal('-273.15')),
    'mm': ('length', Decimal('1'), Decimal('0')),
    'cm': ('length', Decimal('10'), Decimal('0')),
    'm': ('length', Decimal('1000'), Decimal('0')),
    'km': ('length', Decimal('1000000'), Decimal('0')),
    'g': ('mass', Decimal('1'), Decimal('0')),
    'kg': ('mass', Decimal('1000'), Decimal('0')),
    't': ('mass', Decimal('1000000'), Decimal('0')),
    's': ('time', Decimal('1'), Decimal('0')),
    'min': ('time', Decimal('60'), Decimal('0')),
    'h': ('time', Decimal('3600'), Decimal('0')),
    'day': ('time', Decimal('86400'), Decimal('0')),
    'kPa': ('pressure', Decimal('1'), Decimal('0')),
    'MPa': ('pressure', Decimal('1000'), Decimal('0')),
    'count': ('count', Decimal('1'), Decimal('0')),
    '%': ('ratio', Decimal('1'), Decimal('0')),
    'CNY': ('currency', Decimal('1'), Decimal('0')),
    # 万元 (ten-thousand CNY) is a legal unit the clause regex already accepted; it is an ordinary
    # terminating scale (factor 10000), so it belongs in this table (L2, round-1 security review).
    '万元': ('currency', Decimal('10000'), Decimal('0')),
}

#: Units whose scale/offset are rational but *not* terminating decimals. They are kept exact as
#: :class:`~fractions.Fraction` and converted through :func:`_normalize_exact`: the result is only
#: returned when its decimal expansion terminates. That is the same discipline as ``s -> min``
#: (refused, never rounded) applied to ``degF -> degC``, where ``5/9`` cannot be a decimal.
_EXACT_UNITS = {
    'degF': ('temperature', Fraction(5, 9), Fraction(-160, 9)),
}

_UNIT_ALIASES = {
    'degc': 'degC', '°c': 'degC', '℃': 'degC', '摄氏度': 'degC', 'celsius': 'degC',
    'degf': 'degF', '°f': 'degF', '华氏度': 'degF', 'fahrenheit': 'degF',
    'k': 'K', '开尔文': 'K', 'kelvin': 'K',
    '毫米': 'mm', '厘米': 'cm', '米': 'm', '千米': 'km', '公里': 'km',
    '克': 'g', '千克': 'kg', '公斤': 'kg', '吨': 't',
    '秒': 's', '分钟': 'min', '分': 'min', '小时': 'h', '时': 'h', '天': 'day', '日': 'day',
    '件': 'count', '个': 'count', '台': 'count', '人': 'count', '笔': 'count', '条': 'count',
    '单': 'count', '次': 'count', '张': 'count',
    '元': 'CNY', '¥': 'CNY', 'rmb': 'CNY', '人民币': 'CNY',
    '%': '%', '百分比': '%',
}

_ENTITY_SAFE = re.compile(r'^[0-9A-Za-z_\u4e00-\u9fff][0-9A-Za-z_\-.\u4e00-\u9fff ]{1,63}$')
_HEX_SHA256 = re.compile(r'^[0-9a-f]{64}$')

#: The canonical comparison unit of each dimension: two values of the same dimension are
#: compared after both are normalised to this unit.
_BASE = {'temperature': 'degC', 'length': 'mm', 'mass': 'g', 'time': 's', 'pressure': 'kPa',
         'count': 'count', 'ratio': '%', 'currency': 'CNY'}


class BindingError(ValueError):
    """A binding could not be established; ``code`` is the stable fail-closed reason."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code + (':' + detail if detail else ''))
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- numeric coercion

def decimal_value(value) -> Decimal:
    """Coerce to ``Decimal`` without float noise; raises ``ValueError('INVALID_NUMBER')``."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('INVALID_NUMBER') from None


# ---------------------------------------------------------------- units

def _canonical_unit(unit):
    if unit is None:
        return None
    text = str(unit).strip()
    if not text:
        return None
    if text in _UNITS or text in _EXACT_UNITS:
        return text
    return _UNIT_ALIASES.get(text.lower()) or _UNIT_ALIASES.get(text)


def _dimension(canonical: str) -> str:
    return _UNITS[canonical][0] if canonical in _UNITS else _EXACT_UNITS[canonical][0]


def _rational_definition(canonical: str):
    """``(dimension, Fraction factor, Fraction offset)`` for a canonical unit."""
    if canonical in _EXACT_UNITS:
        return _EXACT_UNITS[canonical]
    dimension, factor, offset = _UNITS[canonical]
    return dimension, Fraction(factor), Fraction(offset)


def _terminating_decimal(value: Fraction):
    """Exact ``Decimal`` when the rational's expansion terminates, else ``None``."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        return None
    scale = max(twos, fives)
    # ``value.denominator`` divides ``10 ** scale``, so this division is exact.
    scaled = value.numerator * (10 ** scale) // value.denominator
    return Decimal(scaled).scaleb(-scale)


def _terminating(factor: Decimal) -> bool:
    """True when ``1 / factor`` is an exact terminated decimal (denominator only 2s and 5s)."""
    if factor == 0:
        return False
    scaled = factor.normalize()
    exponent = scaled.as_tuple().exponent
    if exponent > 0:
        # 60 -> 6E+1; dividing by 60 is not terminated, but 1/60 is the case we must refuse.
        value = int(scaled)
    else:
        value = int(scaled.scaleb(-exponent))
    denominator = value
    while denominator % 2 == 0 and denominator > 1:
        denominator //= 2
    while denominator % 5 == 0 and denominator > 1:
        denominator //= 5
    return denominator == 1


def unit_status(unit) -> dict:
    """Deterministic status of one unit string; never guesses a dimension.

    ``unit_unknown`` is True only for an unregistered/bogus unit. A registered unit whose
    *target* conversion would not terminate (``s -> min``) is still a known unit; that refusal
    happens inside :func:`normalize_unit`, which returns ``None``.
    """
    canonical = _canonical_unit(unit)
    if canonical is None:
        return {'unit': None, 'unit_unknown': True, 'dimension': None, 'reason': 'UNIT_UNKNOWN'}
    return {'unit': canonical, 'unit_unknown': False, 'dimension': _dimension(canonical),
            'reason': 'OK'}


def normalize_unit(value, unit, target=None):
    """Deterministic unit conversion of ``value`` from ``unit`` to ``target``.

    Returns a ``Decimal`` when — and only when — the conversion is a registered, exact,
    terminated decimal equivalence. Returns ``None`` and the caller must mark the claim
    ``unit_unknown`` (see :func:`unit_status`) when the unit is unknown, when dimensions differ,
    or when the inverse division would not terminate (``s -> min``). ``target=None`` converts to
    the canonical comparison unit of the value's own dimension (``K -> degC``, ``cm -> mm``).
    """
    source = _canonical_unit(unit)
    if source is None:
        return None
    number = decimal_value(value)
    if not number.is_finite():
        return None
    if source in _EXACT_UNITS:
        return _normalize_exact(number, source, target)
    dimension, factor, offset = _UNITS[source]
    destination = _canonical_unit(target) if target is not None else _BASE[dimension]
    if destination is None or _dimension(destination) != dimension:
        return None
    if destination in _EXACT_UNITS:
        return _normalize_exact(number, source, target)
    target_dimension, target_factor, target_offset = _UNITS[destination]
    if not _terminating(target_factor):
        return None
    with localcontext() as context:
        context.prec = 50
        base = number * factor + offset
        result = (base - target_offset) / target_factor
    if not result.is_finite():
        return None
    # Refuse anything the fixed-precision division may have rounded: the round trip has to hold.
    with localcontext() as context:
        context.prec = 50
        if result * target_factor + target_offset != base:
            return None
    return result


def _normalize_exact(number: Decimal, source: str, target):
    """Rational conversion for the ``_EXACT_UNITS`` (``degF``); refused when it does not terminate.

    ``degF -> degC`` is ``(F - 32) * 5 / 9``: the factor is not a terminating decimal, so the
    arithmetic is done on :class:`~fractions.Fraction` and the result is only converted back to a
    ``Decimal`` when its expansion terminates. A value such as ``70 degF`` (``190/9 degC``) is
    therefore refused, exactly like ``1 s -> min``, instead of being reported as a rounded number.
    """
    dimension, factor, offset = _rational_definition(source)
    destination = _canonical_unit(target) if target is not None else _BASE[dimension]
    if destination is None:
        return None
    target_dimension, target_factor, target_offset = _rational_definition(destination)
    if target_dimension != dimension:
        return None
    base = Fraction(number) * factor + offset
    return _terminating_decimal((base - target_offset) / target_factor)


def base_unit(unit):
    """Canonical comparison unit of ``unit``'s dimension, or ``None`` when the unit is unknown."""
    status = unit_status(unit)
    if status['unit_unknown']:
        return None
    return _BASE[status['dimension']]


def units_equivalent(left_unit, right_unit) -> bool:
    """True when both units are registered and share one dimension.

    Comparing two values then goes through :func:`normalize_unit` to the dimension's base unit,
    which is an exact transform for every registered unit; an individual reverse conversion may
    still be refused there (``s -> min``).
    """
    left = unit_status(left_unit)
    right = unit_status(right_unit)
    if left['unit_unknown'] or right['unit_unknown']:
        return False
    return left['dimension'] == right['dimension']


def values_agree(left_value, left_unit, right_value, right_unit) -> bool:
    """Exact numeric agreement after normalising both sides to one canonical unit."""
    left_status = unit_status(left_unit)
    right_status = unit_status(right_unit)
    if left_status['unit_unknown'] or right_status['unit_unknown'] or \
            left_status['dimension'] != right_status['dimension']:
        return False
    target = _BASE[left_status['dimension']]
    left = normalize_unit(left_value, left_unit, target)
    right = normalize_unit(right_value, right_unit, target)
    return left is not None and right is not None and left == right


def source_agreement(left_value, left_unit, right_value, right_unit) -> dict:
    """Core '来源一致' verdict for two raw values.

    ``status`` is one of ``consistent`` / ``resolved_equivalent`` / ``unresolved`` /
    ``needs_review``. Conflicting values are reported with both normalised values and the
    industrial rule id ``NO_REGISTERED_AUTHORITY_RULE``; this function never picks a side.
    """
    left_status = unit_status(left_unit)
    right_status = unit_status(right_unit)
    if left_status['unit_unknown'] or right_status['unit_unknown'] or \
            left_status['dimension'] != right_status['dimension']:
        return {'status': 'needs_review', 'reason': 'VALUES_NOT_COMPARABLE',
                'left_value': None, 'right_value': None}
    target = _BASE[left_status['dimension']]
    left = normalize_unit(left_value, left_unit, target)
    right = normalize_unit(right_value, right_unit, target)
    if left is None or right is None:
        return {'status': 'needs_review', 'reason': 'VALUES_NOT_COMPARABLE',
                'left_value': None if left is None else format(left, 'f'),
                'right_value': None if right is None else format(right, 'f')}
    if left != right:
        return {'status': 'unresolved', 'reason': 'NO_REGISTERED_AUTHORITY_RULE',
                'left_value': format(left, 'f'), 'right_value': format(right, 'f')}
    return {'status': 'resolved_equivalent' if left_unit != right_unit else 'consistent',
            'reason': 'DECIMAL_UNIT_CONVERSION_EQUAL' if left_unit != right_unit else 'EXACT_EQUALITY',
            'left_value': format(left, 'f'), 'right_value': format(right, 'f')}


# ---------------------------------------------------------------- compliance

def inclusive_range_status(value, low, high) -> str:
    """Core '合规判定': inclusive range membership; values must already be comparable."""
    return 'within_range' if Decimal(low) <= value <= Decimal(high) else 'out_of_range'


def range_expression(low, value, high) -> str:
    return f'{low} <= {value} <= {high}'


# ---------------------------------------------------------------- time

def timestamp(text) -> datetime:
    """ISO-8601 with an explicit timezone; a naive timestamp is refused (industrial rule).

    An already-parsed ``datetime`` is accepted when it carries a timezone: database rows and the
    cross-source adapters hand datetimes, not strings.
    """
    value = text if isinstance(text, datetime) else datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return value


def version_applies(recorded_at, valid_from, valid_to) -> bool:
    """Half-open applicability ``valid_from <= recorded_at < valid_to``.

    Propagates ``ValueError``/``TypeError`` for an unusable timestamp so the caller records
    ``VALIDITY_UNKNOWN`` instead of treating the record as inapplicable.
    """
    recorded = timestamp(recorded_at)
    start = timestamp(valid_from)
    if valid_to is None:
        return start <= recorded
    return start <= recorded < timestamp(valid_to)


def _scope_bounds(scope):
    if not isinstance(scope, dict):
        return None
    start, end = scope.get('start'), scope.get('end')
    if start is None and end is None:
        return None
    try:
        return (timestamp(start) if start is not None else None,
                timestamp(end) if end is not None else None)
    except (ValueError, TypeError):
        return None


def time_scope_overlap(left_scope, right_scope):
    """``True`` / ``False`` / ``None`` (unknown) overlap for two half-open time scopes."""
    left = _scope_bounds(left_scope)
    right = _scope_bounds(right_scope)
    if left is None or right is None:
        return None
    left_start, left_end = left
    right_start, right_end = right
    if left_end is not None and right_start is not None and left_end <= right_start:
        return False
    if right_end is not None and left_start is not None and right_end <= left_start:
        return False
    return True


def scope_contains(scope, instant) -> bool:
    """Half-open membership of ``instant`` in ``scope``; ``False`` when unknown."""
    bounds = _scope_bounds(scope)
    if bounds is None:
        return False
    start, end = bounds
    try:
        recorded = timestamp(instant)
    except (ValueError, TypeError):
        return False
    if start is not None and recorded < start:
        return False
    if end is not None and recorded >= end:
        return False
    return True


# ---------------------------------------------------------------- entity identity

def normalize_label(label) -> str:
    return re.sub(r'\s+', '', str(label or '').strip()).lower()


def load_aliases(dsn: str | None = None) -> list[dict]:
    """Read the *approved* alias rows from ``entity_aliases`` (read-only).

    Returns the same shape ``Catalog`` reads: ``{alias, canonical_name, entity_type}``. A
    database failure is not hidden: it raises, and the caller decides to fail closed.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    if dsn is None:
        from .sql_engine import settings
        dsn = settings().get('DATAMIND_META_DSN') or (
            'host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev')
    with psycopg2.connect(dsn) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT alias, canonical_name, entity_type FROM entity_aliases WHERE approved=TRUE ORDER BY alias")
            return [dict(row) for row in cur.fetchall()]


def entity_bind(label, aliases=None, *, allow_literal: bool = True) -> dict:
    """Bind a surface label to one canonical entity id, or refuse.

    ``aliases`` is an iterable of approved ``entity_aliases`` rows (``alias`` /
    ``canonical_name``). One alias mapping to more than one canonical name is an ambiguity and
    yields ``unbound`` — the caller must fail closed rather than pick one.
    """
    text = str(label or '').strip()
    key = normalize_label(text)
    if not key:
        return {'entity_id': None, 'entity_label': None, 'matched_alias': None,
                'match': 'unbound', 'ambiguous': False, 'reason': 'EMPTY_LABEL'}
    canonical = set()
    matched_alias = None
    for row in aliases or ():
        alias = normalize_label(row.get('alias'))
        name = str(row.get('canonical_name') or '').strip()
        if alias and alias == key and name:
            canonical.add(name)
            matched_alias = str(row.get('alias'))
    if len(canonical) > 1:
        return {'entity_id': None, 'entity_label': None, 'matched_alias': matched_alias,
                'match': 'unbound', 'ambiguous': True, 'reason': 'AMBIGUOUS_ALIAS',
                'candidates': sorted(canonical)}
    if canonical:
        name = canonical.pop()
        return {'entity_id': name, 'entity_label': name, 'matched_alias': matched_alias,
                'match': 'approved_entity_alias', 'ambiguous': False, 'reason': 'ALIAS_APPROVED',
                'surface': text}
    if not allow_literal:
        return {'entity_id': None, 'entity_label': None, 'matched_alias': None,
                'match': 'unbound', 'ambiguous': False, 'reason': 'NO_APPROVED_ALIAS', 'surface': text}
    if not _ENTITY_SAFE.match(text):
        return {'entity_id': None, 'entity_label': None, 'matched_alias': None,
                'match': 'unbound', 'ambiguous': False, 'reason': 'UNSAFE_LITERAL', 'surface': text}
    return {'entity_id': text, 'entity_label': text, 'matched_alias': None,
            'match': 'canonical_literal', 'ambiguous': False, 'reason': 'LITERAL_ENTITY', 'surface': text}


# ---------------------------------------------------------------- binding record

def canonical_digest(payload) -> str:
    """Stable sha256 of a JSON payload (sorted keys, no whitespace)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def build_binding(*, entity_id, entity_label, version, unit, time_scope, source_ref,
                  source_sha256, extra=None) -> dict:
    binding = {'binding_version': BINDING_VERSION, 'entity_id': entity_id,
               'entity_label': entity_label, 'version': version, 'unit': unit,
               'time_scope': time_scope, 'source_ref': source_ref,
               'source_sha256': source_sha256}
    if extra:
        binding['extra'] = extra
    return binding


def binding_problems(binding) -> list[str]:
    """List every missing/unusable binding field; an empty list means the binding is complete."""
    if not isinstance(binding, dict):
        return ['BINDING_NOT_A_DICT']
    problems = []
    for field in BINDING_FIELDS:
        value = binding.get(field)
        if field == 'time_scope':
            if _scope_bounds(value) is None:
                problems.append('time_scope')
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(field)
    if isinstance(binding.get('source_sha256'), str) and \
            not _HEX_SHA256.match(binding['source_sha256']):
        problems.append('source_sha256:NOT_SHA256')
    status = unit_status(binding.get('unit'))
    if status['unit_unknown']:
        problems.append('unit:' + status['reason'])
    return problems


def binding_complete(binding) -> tuple[bool, list[str]]:
    problems = binding_problems(binding)
    return (not problems), problems


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
