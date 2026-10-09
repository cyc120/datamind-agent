"""Wide 2D engineering-DXF reader (`dxf-native-v2`).

Reads the DXF entity families a real CAD export actually contains -- `LWPOLYLINE`/
`POLYLINE`, `ARC`, `INSERT` (recursively expanded), `MTEXT` and native `DIMENSION` on top
of v1's `LINE`/`CIRCLE`/`TEXT` -- into `cad-contract-v2`
(`ingestion/cad_contract_v2.py`). `ingestion/cad_dxf.py` (v1) is untouched and remains
selectable, so every old evidence file still reproduces.

Two decisions shape this reader:

1. **Partial success with item-by-item declarations.** A single unmodelled entity does
   not throw a whole engineering drawing away. Every entity (or block subtree) that is
   not read is declared with a stable code, its entity type and its source handle, and
   the adapter degrades `parse_status` to `partial`. Whole-drawing rejections are
   reserved for reading failures that make the *source* untrustworthy: binary/oversize/
   undecodable bytes, invalid tag pairs, missing/duplicate handles, entity loss against
   the raw tags, audit errors, a source beyond the configured entity bound, or a drawing
   with no supported entity at all.
2. **No invented values.** Units come from `$INSUNITS` alone (an unmodelled code becomes
   `unknown`, never `mm`); a native dimension keeps its geometric measurement and its
   drafter's text apart; a block-internal handle is only unique together with its
   `block_path` INSERT chain; and a block content that would need geometry we do not
   model (a mirrored text, a bulged segment, a non-uniform block scale) is declared
   instead of being approximated.

All limits are parameterised (`DATAMIND_CAD_MAX_BYTES`, `..._MAX_ENTITIES`,
`..._MAX_EXPANDED_ENTITIES`, `..._MAX_BLOCK_DEPTH`) and bounded by the contract's own
ceilings, so a validated snapshot always fits the model that validated it.

3. **Two explicit identity modes; no handle is ever invented.** Whether the *source*
   carries handles is decided from the raw tags, never from the value `ezdxf` reports:
   a source whose entity/block records really contain a `code 5` tag is read in
   `handle_source='source'` mode, and a source in which **no** such record carries a
   `code 5` tag is read in `handle_source='positional'` mode. `ezdxf` silently
   synthesises handles for a handle-less file (`'2'`, `'31E'`, ...); those values are
   never adopted as if they came from the source. In positional mode each entity is
   located by a deterministic `entity_index` -- the 1-based ordinal of the entity's own
   **primary record inside the source ENTITIES section**, counted in file order over
   records that are not inline sub-records (`VERTEX`/`SEQEND`/`ATTRIB`); a three-vertex
   `POLYLINE` therefore occupies exactly one number -- and the missing handle evidence is
   declared as `CAD_SOURCE_HANDLE_UNAVAILABLE:<n>` with `parse_status='partial'`. Block
   references cannot be expanded in positional mode (a block-internal entity has no
   handle to disambiguate repeated INSERTs), so they are declared item by item as
   `CAD_POSITIONAL_BLOCK_UNSUPPORTED` instead of being guessed at.

The `$INSUNITS`/`$HANDLING` values are read from the **source header tags**, not from
`ezdxf`'s defaulted header: `ezdxf` answers `6` (metres) for a file that declares no
`$INSUNITS` at all, and adopting that default would turn "undeclared" into a unit the
source never stated. An absent `$INSUNITS` is the documented DXF default of 0, which is
`unit='unknown'` and never a guessed millimetre.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext
import hashlib
import io
import math
import os
from pathlib import Path
import re

from pydantic import ValidationError

from .cad_contract_v2 import (CONTRACT_VERSION, DIAMETER_ANNOTATION, IDENTITY_MODES,
                              MAX_ANNOTATION, PLAIN_NUMBER, POSITIONAL_BLOCK_UNSUPPORTED,
                              SOURCE_HANDLE_UNAVAILABLE, DrawingSnapshot, arc_bounds, padded_box)
from .cad_dxf import TITLE_LAYERS, CadReadError

PARSER_VERSION = 'dxf-native-v2'
PROFILE = 'datamind-2d-engineering-dxf-v2'
VIEW_ID = 'model'

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_ENTITIES = 200_000
DEFAULT_MAX_EXPANDED_ENTITIES = 200_000
DEFAULT_MAX_BLOCK_DEPTH = 8
DEFAULT_MAX_DECLARED_ITEMS = 500
LIMIT_ENV = {'max_bytes': 'DATAMIND_CAD_MAX_BYTES',
             'max_entities': 'DATAMIND_CAD_MAX_ENTITIES',
             'max_expanded_entities': 'DATAMIND_CAD_MAX_EXPANDED_ENTITIES',
             'max_block_depth': 'DATAMIND_CAD_MAX_BLOCK_DEPTH'}
# The contract refuses a snapshot outside these, so a limit above them could only
# produce a read that fails validation afterwards. `resolve_limits` refuses it up front.
LIMIT_CEILING = {'max_bytes': 512 * 1024 * 1024, 'max_entities': 200_000,
                 'max_expanded_entities': 200_000, 'max_block_depth': 64,
                 'max_declared_items': 5000}

_HANDLE = re.compile(r'[1-9A-F][0-9A-F]*')
_COORDINATE_LIMIT = 1e9
_DEGENERATE_Z = 1e-9
_SUPPORTED_ENTITIES = ('LINE', 'CIRCLE', 'ARC', 'LWPOLYLINE', 'POLYLINE', 'TEXT', 'MTEXT',
                       'DIMENSION', 'INSERT')
# Handles DXF stores inline, absorbed into their owner by ezdxf: a POLYLINE owns its
# VERTEX/SEQEND records, an INSERT owns its ATTRIB/SEQEND records.
_SUB_ENTITY_TYPES = ('VERTEX', 'SEQEND', 'ATTRIB')
_DIMENSION_TYPES = {0: 'linear', 1: 'aligned', 2: 'angular', 3: 'diameter', 4: 'radius',
                    5: 'angular_3p', 6: 'ordinate'}
_CODEPAGES = {'ANSI_874': 'cp874', 'ANSI_932': 'shift_jis', 'ANSI_936': 'gbk',
              'ANSI_949': 'euc_kr', 'ANSI_950': 'big5', 'ANSI_1250': 'cp1250',
              'ANSI_1251': 'cp1251', 'ANSI_1252': 'cp1252', 'ANSI_1253': 'cp1253',
              'ANSI_1254': 'cp1254', 'ANSI_1255': 'cp1255', 'ANSI_1256': 'cp1256',
              'ANSI_1257': 'cp1257', 'ANSI_1258': 'cp1258', 'UTF-8': 'utf-8', 'UTF8': 'utf-8'}


@dataclass(frozen=True)
class CadLimits:
    max_bytes: int = DEFAULT_MAX_BYTES
    max_entities: int = DEFAULT_MAX_ENTITIES
    max_expanded_entities: int = DEFAULT_MAX_EXPANDED_ENTITIES
    max_block_depth: int = DEFAULT_MAX_BLOCK_DEPTH
    max_declared_items: int = DEFAULT_MAX_DECLARED_ITEMS


def _limit_value(field, raw):
    name = LIMIT_ENV.get(field, field)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as error:
        raise CadReadError('CAD_LIMIT_INVALID', {'name': name, 'value': repr(raw)}) from error
    if not 0 < value <= LIMIT_CEILING[field]:
        raise CadReadError('CAD_LIMIT_INVALID',
                           {'name': name, 'value': value, 'max': LIMIT_CEILING[field]})
    return value


def resolve_limits(overrides=None, env=None):
    """Read the tunable limits from explicit overrides, then the environment, then the
    documented defaults. An unusable value is refused with `CAD_LIMIT_INVALID` rather
    than clamped, so a caller never believes a bound it did not get."""
    overrides = overrides or {}
    environment = os.environ if env is None else env
    values = {}
    for field in ('max_bytes', 'max_entities', 'max_expanded_entities', 'max_block_depth',
                  'max_declared_items'):
        raw = overrides.get(field)
        if raw is None:
            raw = environment.get(LIMIT_ENV.get(field, ''))
        values[field] = getattr(CadLimits, field) if raw is None else _limit_value(field, raw)
    return CadLimits(**values)


def _library():
    try:
        import ezdxf
        from ezdxf import bbox
    except ImportError as error:  # pragma: no cover - the dependency is pinned
        raise CadReadError('CAD_DEPENDENCY_MISSING', {'requirements': 'requirements-cad.txt'}) from error
    return ezdxf, bbox


def _declared_codec(data):
    head = data[:8192].decode('latin-1')
    match = re.search(r'\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n\s*([^\s\r\n]+)', head)
    return _CODEPAGES.get(match[1].strip().upper()) if match else None


def _decode(data):
    """Decode the source bytes, preferring UTF-8 and declaring any fallback codec.

    DXF is nominally ASCII, but a drawing exported by a non-English CAD stores its text
    in the code page named by `$DWGCODEPAGE`. The structural tags stay intact under any
    single-byte or GBK-family decode, so the codec is recorded on the source instead of
    rejecting the file; text fidelity under a fallback codec is not verified.
    """
    try:
        return data.decode('utf-8-sig'), 'utf-8'
    except UnicodeDecodeError:
        pass
    tried = []
    for codec in (_declared_codec(data), 'gb18030', 'cp1252'):
        if not codec or codec in tried:
            continue
        tried.append(codec)
        try:
            return data.decode(codec), codec
        except (UnicodeDecodeError, LookupError):
            continue
    raise CadReadError('CAD_ENCODING_UNSUPPORTED', {'tried': tried})


def _tags(text):
    lines = text.splitlines()
    if len(lines) % 2:
        raise CadReadError('CAD_DXF_TAGS_INVALID')
    pairs = []
    for index in range(0, len(lines), 2):
        try:
            code = int(lines[index].strip())
        except ValueError as error:
            raise CadReadError('CAD_DXF_TAGS_INVALID') from error
        pairs.append((code, lines[index + 1].strip()))
    return pairs


def _record_handle(pairs, start):
    index = start + 1
    while index < len(pairs) and pairs[index][0] != 0:
        if pairs[index][0] == 5:
            return pairs[index][1]
        index += 1
    return None


def _scan_source_records(pairs):
    """`(type, handle)` records straight from the raw tags, before ezdxf can repair them.

    `ENTITIES` holds Model-space and paper-space entities; `BLOCKS` holds the entities
    inside block definitions (the `BLOCK`/`ENDBLK` records themselves are skipped).
    """
    entities, blocks, section, in_block, index = [], [], None, 0, 0
    while index < len(pairs):
        code, value = pairs[index]
        if code == 0 and value == 'SECTION':
            if index + 1 >= len(pairs) or pairs[index + 1][0] != 2:
                raise CadReadError('CAD_DXF_TAGS_INVALID')
            section = pairs[index + 1][1]
            index += 2
            continue
        if code == 0 and value == 'ENDSEC':
            section = None
        elif section == 'ENTITIES' and code == 0:
            entities.append((value, _record_handle(pairs, index)))
        elif section == 'BLOCKS' and code == 0:
            if value == 'BLOCK':
                in_block += 1
            elif value == 'ENDBLK':
                in_block = max(0, in_block - 1)
            elif in_block > 0:
                blocks.append((value, _record_handle(pairs, index)))
        index += 1
    return entities, blocks


def _header_values(pairs):
    """The `HEADER` section's `$NAME -> value` pairs, straight from the raw tags.

    `ezdxf` fills a missing header variable with its own default (`$INSUNITS` becomes 6,
    `$HANDLING` becomes 1), so the only honest source of "what did the file declare" is
    the file's own tags. Only the `HEADER` section is inspected: a `code 9` string also
    occurs in other sections (a `DIMSTYLE`'s variables), and those are not header vars.
    """
    values, section, index = {}, None, 0
    while index < len(pairs):
        code, value = pairs[index]
        if code == 0 and value == 'SECTION':
            section = pairs[index + 1][1] if (index + 1 < len(pairs)
                                              and pairs[index + 1][0] == 2) else None
            index += 2
            continue
        if code == 0 and value == 'ENDSEC':
            section = None
        elif section == 'HEADER' and code == 9 and index + 1 < len(pairs):
            values[value] = pairs[index + 1][1]
        index += 1
    return values


def _declared_flag(value):
    """A raw header flag: absent or `0` is off, anything else is on."""
    if value is None:
        return False
    return str(value).strip() not in ('', '0')


def _validate_source_records(entities, blocks, limits):
    """Check the raw records and return the source identity mode.

    `'source'` means every record the drawing would locate carries a real `code 5`
    handle; `'positional'` means **no** record carries one, so the source has no handle
    evidence to trust and the reader will use a declared positional identity instead.
    A source that mixes both (some records with a handle, some without) is internally
    inconsistent and is refused: positional identity could only cover part of the
    drawing, and the handle-bearing part would be silently reinterpreted.
    """
    if not entities:
        raise CadReadError('CAD_ENTITY_COUNT_INVALID', {'entities': 0})
    total = len(entities) + len(blocks)
    if total > limits.max_entities:
        raise CadReadError('CAD_ENTITY_COUNT_INVALID',
                           {'records': total, 'max': limits.max_entities})
    records = entities + blocks
    if not any(handle is not None for _kind, handle in records):
        return 'positional'
    handles = []
    for kind, handle in records:
        if handle is None or not _HANDLE.fullmatch(handle):
            raise CadReadError('CAD_ENTITY_HANDLE_INVALID', {'type': kind, 'handle': handle})
        handles.append(handle)
    duplicates = sorted(handle for handle, count in Counter(handles).items() if count > 1)
    if duplicates:
        raise CadReadError('CAD_DUPLICATE_SOURCE_HANDLE',
                           {'handles': duplicates[:10], 'count': len(duplicates)})
    return 'source'


def _positional_indices(read_records, source_records, doc):
    """`{id(entity): entity_index}` for a handle-less source, or refuse the drawing.

    `entity_index` is the 1-based ordinal of the entity's own primary record inside the
    source `ENTITIES` section (see the module docstring). The mapping is built from the
    *type multiset* of the records the library read versus the records the raw tags
    declare: if the two disagree, an entity was lost or reordered in a way that would
    make a positional identity point at the wrong record, so the drawing is refused
    rather than given a plausible-but-wrong index.
    """
    read_types = Counter(kind for kind, _handle in read_records)
    source_types = Counter(record[0] for record in source_records)
    if read_types != source_types:
        raise CadReadError('CAD_ENTITY_LOSS_OR_LAYOUT_UNSUPPORTED',
                           {'reason': 'source_entity_types_not_reproducible',
                            'raw': sum(source_types.values()), 'read': sum(read_types.values())})
    positions = {}
    for position, (kind, _handle) in enumerate(source_records, start=1):
        positions.setdefault(kind, []).append(position)
    seen, indices = Counter(), {}
    for layout in doc.layouts:
        for entity in layout:
            kind = entity.dxftype()
            indices[id(entity)] = positions[kind][seen[kind]]
            seen[kind] += 1
    return indices


def _dxf(entity, name, default=None):
    """`entity.dxf.get` that honours the default.

    ezdxf raises `DXFAttributeError` instead of returning a default when the *entity
    type* does not define the attribute at all (an INSERT has no `thickness`), so the
    plain accessor cannot be used to probe a shared attribute across entity families.
    """
    try:
        return entity.dxf.get(name, default)
    except Exception:
        return default


def _xyz(vector):
    if hasattr(vector, 'x'):
        return float(vector.x), float(vector.y), float(vector.z)
    return float(vector[0]), float(vector[1]), float(vector[2] if len(vector) > 2 else 0.0)


def _finite2(point):
    x, y, z = _xyz(point)
    if not all(math.isfinite(value) and abs(value) <= _COORDINATE_LIMIT for value in (x, y, z)):
        return None
    if abs(z) > _DEGENERATE_Z:
        return None
    return {'x': float(x), 'y': float(y)}


def _box_tuple(box):
    return (box['xmin'], box['ymin'], box['xmax'], box['ymax'])


def _record_key(record):
    """A stable in-memory key for one emitted record, in either identity mode.

    Source mode returns the raw handle unchanged, so every existing key (`model_circles`,
    `diameter_texts`, feature ids) is byte-for-byte what it was. Positional mode returns
    `i<entity_index>`, which is a declared identity and never a synthesised handle.
    """
    handle = record.get('handle')
    if handle:
        return handle
    index = record.get('entity_index')
    if index is None:
        # Every emitted record has exactly one of the two identities; reaching here would
        # mean a record with no identity at all, which must never be keyed silently.
        raise CadReadError('CAD_ENTITY_IDENTITY_MISSING')
    return 'i' + str(index)


def _sub_entities(entity, name):
    """A safe iteration over an owner's inline records (`vertices`, `attribs`).

    `getattr` is not enough: some entities expose a *method* under the same name, and a
    property may raise on an entity that does not own the records. Anything that is not
    an iterable of entities is treated as "owns none".
    """
    try:
        value = getattr(entity, name, None)
    except Exception:
        return ()
    if value is None or callable(value):
        return ()
    try:
        return tuple(value)
    except TypeError:
        return ()


def _visited(entity):
    """The handle of one entity plus the handles it owns inline.

    `_sub_entities` is name-driven, and the same attribute name does not always hold
    entities: `LEADER.vertices`, for instance, is a list of plain coordinate **tuples**
    while `POLYLINE.vertices` holds entity objects. Only members that really are entities
    carry a handle, so each one is checked before it is read — a real drawing containing a
    LEADER used to crash the whole parse here (`AttributeError: 'tuple' object has no
    attribute 'dxf'`) instead of being declared unsupported.
    """
    handles = [entity.dxf.get('handle')]
    for vertex in _sub_entities(entity, 'vertices'):
        if hasattr(vertex, 'dxf'):
            handles.append(vertex.dxf.get('handle'))
    try:
        seqend = getattr(entity, 'seqend', None)
    except Exception:
        seqend = None
    if seqend is not None and not callable(seqend) and hasattr(seqend, 'dxf'):
        handles.append(seqend.dxf.get('handle'))
    for attrib in _sub_entities(entity, 'attribs'):
        if hasattr(attrib, 'dxf'):
            handles.append(attrib.dxf.get('handle'))
    return handles


class _Extractor:
    """One pass over one drawing; every gap is recorded, never guessed."""

    def __init__(self, doc, *, limits, sha, unit, bbox_module, identity_mode='source',
                 index_by_id=None):
        self.doc, self.limits, self.sha, self.unit = doc, limits, sha, unit
        if identity_mode not in IDENTITY_MODES:
            raise CadReadError('CAD_HANDLE_SOURCE_INVALID', {'mode': repr(identity_mode)})
        self.identity_mode = identity_mode
        self.positional = identity_mode == 'positional'
        # `{id(entity): entity_index}` for a handle-less source; empty in source mode.
        self.index_by_id = index_by_id or {}
        self.bbox = bbox_module
        self.entities, self.bounds, self.features, self.identity = [], [], [], {}
        self.unsupported, self.counts, self.warnings = [], Counter(), ['CAD_SINGLE_MODEL_VIEW_ONLY']
        self.model_circles, self.diameter_texts = {}, {}
        self.emitted = 0
        self.limit_declared = False

    def entity_index(self, entity):
        """The declared positional ordinal of one source entity, or `None` in source mode."""
        if not self.positional or entity is None:
            return None
        return self.index_by_id.get(id(entity))

    def entity_label(self, entity):
        """A non-fabricated label for a warning or detail string.

        Source mode keeps the raw handle; positional mode uses `#<entity_index>` and
        never the handle `ezdxf` synthesised, so no warning can leak an invented handle.
        """
        if self.positional:
            return '#' + str(self.entity_index(entity))
        return str(entity.dxf.handle)

    # -- declarations ----------------------------------------------------------------
    def declare(self, code, *, entity=None, handle=None, entity_type=None, block_path=(),
                detail=None, entity_index=None):
        """Record one declared gap.

        In positional mode the library's synthetic handle is **never** emitted: the
        declaration carries `handle=None` and the entity's `entity_index` instead, so a
        gap cannot reintroduce a value the source does not contain.
        """
        if entity is not None:
            entity_type = entity_type or entity.dxftype()
            if entity_index is None:
                entity_index = self.entity_index(entity)
            if not self.positional and handle is None:
                handle = entity.dxf.get('handle')
        if self.positional:
            handle = None
        self.counts[code] += 1
        if len(self.unsupported) < self.limits.max_declared_items:
            item = {'code': code, 'entity_type': entity_type, 'handle': handle,
                    'block_path': list(block_path), 'detail': detail}
            if self.positional:
                item['entity_index'] = entity_index
            self.unsupported.append(item)

    def locator(self, record):
        locator = {'locator_kind': 'cad_entity', 'source_sha256': self.sha,
                   'layer': record['layer'], 'view_id': VIEW_ID,
                   'coordinate_system': 'WCS_XY', 'layout': 'Model',
                   'block_path': list(record['block_path'])}
        if self.positional:
            locator['entity_index'] = record['entity_index']
            locator['handle_source'] = 'positional'
        else:
            locator['handle'] = record['handle']
        return locator

    # -- geometry helpers ------------------------------------------------------------
    def _linear(self, chain):
        """The composed block-to-WCS affine map, as `(origin, x_axis, y_axis)`."""
        if not chain:
            return ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
        origin = self._apply(chain, 0.0, 0.0)
        x_axis = self._apply(chain, 1.0, 0.0)
        y_axis = self._apply(chain, 0.0, 1.0)
        return (origin, (x_axis[0] - origin[0], x_axis[1] - origin[1]),
                (y_axis[0] - origin[0], y_axis[1] - origin[1]))

    def _apply(self, chain, x, y):
        point = (float(x), float(y))
        for matrix in reversed(chain):
            vector = matrix.transform((point[0], point[1], 0.0))
            point = (float(vector.x), float(vector.y))
        return point

    @staticmethod
    def _map(linear, x, y):
        origin, x_axis, y_axis = linear
        return (origin[0] + x_axis[0] * x + y_axis[0] * y,
                origin[1] + x_axis[1] * x + y_axis[1] * y)

    @staticmethod
    def _shape(linear):
        """`(scale, rotation_degrees, mirrored)` of a similarity map, `None` otherwise."""
        _origin, x_axis, y_axis = linear
        scale_x, scale_y = math.hypot(*x_axis), math.hypot(*y_axis)
        if scale_x <= 0 or scale_y <= 0:
            return None
        if abs(scale_x - scale_y) > 1e-9 * max(1.0, scale_x, scale_y):
            return None
        determinant = x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0]
        return scale_x, math.degrees(math.atan2(x_axis[1], x_axis[0])), determinant < 0

    def _source_xy(self, vector, handle, block_path,
                   code='CAD_3D_OR_NONFINITE_UNSUPPORTED'):
        """The source point, refused unless it is finite, bounded and in the Z=0 plane.

        The block transform only maps X/Y, so the source Z has to be checked here as well
        or a genuinely three-dimensional entity would be flattened silently.
        """
        x, y, z = _xyz(vector)
        if (not all(math.isfinite(value) and abs(value) <= _COORDINATE_LIMIT
                    for value in (x, y, z)) or abs(z) > _DEGENERATE_Z):
            self.declare(code, handle=handle, block_path=block_path)
            return None
        return (x, y)

    def _place(self, linear, vector, handle, block_path,
               code='CAD_3D_OR_NONFINITE_UNSUPPORTED'):
        """A source point placed into WCS, or `None` with the refusal declared."""
        xy = self._source_xy(vector, handle, block_path, code)
        if xy is None:
            return None
        return _finite2(self._map(linear, *xy))

    # -- entity dispatch -------------------------------------------------------------
    def extract(self, entity, chain, block_path, block_names):
        if self.emitted >= self.limits.max_expanded_entities:
            if not self.limit_declared:
                self.limit_declared = True
                self.declare('CAD_ENTITY_LIMIT_REACHED',
                             detail=f'expanded limit {self.limits.max_expanded_entities}')
            return []
        kind = entity.dxftype()
        if not self._usable(entity, block_path):
            return []
        if kind == 'INSERT':
            if self.positional:
                # A block-internal entity has no source handle, so repeated INSERTs of the
                # same block would share one positional identity. Declare the reference as
                # skipped instead of reporting an ambiguous expansion.
                return self._skipped_insert(entity, block_path, POSITIONAL_BLOCK_UNSUPPORTED,
                                            detail='source has no entity handles')
            return self._insert(entity, chain, block_path, block_names)
        handler = {'LINE': self._line, 'CIRCLE': self._circle, 'ARC': self._arc,
                   'LWPOLYLINE': self._lwpolyline, 'POLYLINE': self._polyline,
                   'TEXT': self._text, 'MTEXT': self._mtext,
                   'DIMENSION': self._dimension}.get(kind)
        if handler is None:
            self.declare('CAD_ENTITY_TYPE_UNSUPPORTED', entity=entity, block_path=block_path,
                         detail='supported: ' + ','.join(_SUPPORTED_ENTITIES))
            return []
        if kind == 'DIMENSION' and block_path:
            # A dimension's definition points are block coordinates and its measured value
            # belongs to the block, so promoting it to drawing coordinates would change the
            # number. Refuse it explicitly instead.
            self.declare('CAD_DIMENSION_IN_BLOCK_UNSUPPORTED', entity=entity, block_path=block_path)
            return []
        return handler(entity, chain, block_path)

    def _usable(self, entity, block_path):
        if tuple(_dxf(entity, 'extrusion', (0, 0, 1))) != (0.0, 0.0, 1.0) \
                or _dxf(entity, 'thickness', 0) != 0:
            self.declare('CAD_OCS_OR_THICKNESS_UNSUPPORTED', entity=entity, block_path=block_path)
            return False
        layer_name = _dxf(entity, 'layer')
        if not isinstance(layer_name, str) or not layer_name:
            self.declare('CAD_LAYER_UNKNOWN', entity=entity, block_path=block_path)
            return False
        try:
            layer = self.doc.layers.get(layer_name)
        except (KeyError, TypeError):
            self.declare('CAD_LAYER_UNKNOWN', entity=entity, block_path=block_path)
            return False
        if (_dxf(entity, 'invisible', 0) or layer.is_off() or layer.is_frozen()):
            self.declare('CAD_HIDDEN_ENTITY_UNSUPPORTED', entity=entity, block_path=block_path)
            return False
        return True

    def _emit(self, record, bounds, *, count=True):
        record['anchor'] = record.pop('_anchor')
        self.entities.append(record)
        self.bounds.append(tuple(float(value) for value in bounds))
        if count:
            self.emitted += 1
        return record

    def _base(self, entity, block_path, anchor):
        """The identity common to every emitted entity record.

        Source mode keeps the raw `code 5` handle exactly as before. Positional mode
        drops `entity.dxf.handle` (a value `ezdxf` synthesised for a handle-less file)
        and carries the declared `entity_index`/`handle_source` instead.
        """
        record = {'layer': entity.dxf.layer, 'view_id': VIEW_ID,
                  'block_path': list(block_path), '_anchor': anchor}
        if self.positional:
            record['handle'] = None
            record['entity_index'] = self.entity_index(entity)
            record['handle_source'] = 'positional'
        else:
            record['handle'] = entity.dxf.handle
        return record

    @staticmethod
    def _point_box(points):
        box = padded_box([[point['x'], point['y'], point['x'], point['y']] for point in points])
        return _box_tuple(box)

    # -- primitives ------------------------------------------------------------------
    def _line(self, entity, chain, block_path):
        linear = self._linear(chain)
        start = self._place(linear, entity.dxf.start, entity.dxf.handle, block_path)
        end = self._place(linear, entity.dxf.end, entity.dxf.handle, block_path)
        if start is None or end is None:
            return []
        if start == end:
            self.declare('CAD_ZERO_LENGTH_LINE', entity=entity, block_path=block_path)
            return []
        record = dict(self._base(entity, block_path, start), entity_type='LINE',
                      start=start, end=end)
        return [self._emit(record, (min(start['x'], end['x']), min(start['y'], end['y']),
                                    max(start['x'], end['x']), max(start['y'], end['y'])))]

    def _circle(self, entity, chain, block_path):
        linear = self._linear(chain)
        shape = self._shape(linear)
        if shape is None:
            self.declare('CAD_INSERT_NON_UNIFORM_SCALE_UNSUPPORTED', entity=entity,
                         block_path=block_path)
            return []
        radius = float(entity.dxf.radius) * shape[0]
        center = self._place(linear, entity.dxf.center, entity.dxf.handle, block_path)
        if center is None:
            return []
        if not math.isfinite(radius) or not 0 < radius <= _COORDINATE_LIMIT:
            self.declare('CAD_RADIUS_INVALID', entity=entity, block_path=block_path)
            return []
        record = dict(self._base(entity, block_path, center), entity_type='CIRCLE',
                      center=center, radius=float(radius))
        if not block_path:
            self.model_circles[_record_key(record)] = record
        return [self._emit(record, (center['x'] - radius, center['y'] - radius,
                                    center['x'] + radius, center['y'] + radius))]

    def _arc(self, entity, chain, block_path):
        linear = self._linear(chain)
        shape = self._shape(linear)
        if shape is None:
            self.declare('CAD_INSERT_NON_UNIFORM_SCALE_UNSUPPORTED', entity=entity,
                         block_path=block_path)
            return []
        scale, _rotation, mirrored = shape
        radius = float(entity.dxf.radius) * scale
        center = self._place(linear, entity.dxf.center, entity.dxf.handle, block_path)
        if center is None:
            return []
        if not math.isfinite(radius) or not 0 < radius <= _COORDINATE_LIMIT:
            self.declare('CAD_RADIUS_INVALID', entity=entity, block_path=block_path)
            return []
        start_angle, end_angle = float(entity.dxf.start_angle), float(entity.dxf.end_angle)
        if start_angle % 360.0 == end_angle % 360.0:
            self.declare('CAD_ZERO_SWEEP_ARC', entity=entity, block_path=block_path)
            return []
        if not chain:
            # A Model-space arc keeps the source angles exactly; no transform is applied.
            angles = (start_angle % 360.0, end_angle % 360.0)
        elif not mirrored:
            rotation = shape[1]
            angles = ((start_angle + rotation) % 360.0, (end_angle + rotation) % 360.0)
        else:
            # Transform both end points: reflection reverses the sweep, and a DXF arc is
            # always stored counter-clockwise, so a mirrored arc's angles swap.
            local = self._source_xy(entity.dxf.center, entity.dxf.handle, block_path) or (0.0, 0.0)
            local_radius = radius / scale
            ends = [self._map(linear,
                              local[0] + local_radius * math.cos(math.radians(angle)),
                              local[1] + local_radius * math.sin(math.radians(angle)))
                    for angle in (start_angle, end_angle)]
            angles = tuple(math.degrees(math.atan2(point[1] - center['y'], point[0] - center['x']))
                           % 360.0 for point in ends)
            angles = (angles[1], angles[0])
        if angles[0] == angles[1]:
            self.declare('CAD_ZERO_SWEEP_ARC', entity=entity, block_path=block_path)
            return []
        record = dict(self._base(entity, block_path, center), entity_type='ARC', center=center,
                      radius=float(radius), start_angle=float(angles[0]), end_angle=float(angles[1]))
        return [self._emit(record, arc_bounds((center['x'], center['y']), radius, *angles))]

    def _lwpolyline(self, entity, chain, block_path):
        points = list(entity.get_points('xyseb'))
        if float(_dxf(entity, 'elevation', 0) or 0) != 0:
            self.declare('CAD_3D_OR_NONFINITE_UNSUPPORTED', entity=entity, block_path=block_path)
            return []
        if any(abs(float(point[2])) > 0 or abs(float(point[3])) > 0 for point in points):
            self.declare('CAD_POLYLINE_WIDTH_UNSUPPORTED', entity=entity, block_path=block_path)
            return []
        if any(abs(float(point[4])) > 0 for point in points):
            self.declare('CAD_POLYLINE_BULGE_UNSUPPORTED', entity=entity, block_path=block_path)
            return []
        return self._polyline_record(entity, chain, block_path,
                                     [(float(point[0]), float(point[1]), 0.0) for point in points],
                                     bool(entity.closed))

    def _polyline(self, entity, chain, block_path):
        if entity.get_mode() != 'AcDb2dPolyline':
            self.declare('CAD_POLYLINE_3D_OR_MESH_UNSUPPORTED', entity=entity,
                         block_path=block_path, detail=entity.get_mode())
            return []
        if any(abs(float(vertex.dxf.get('bulge', 0))) > 0 for vertex in entity.vertices):
            self.declare('CAD_POLYLINE_BULGE_UNSUPPORTED', entity=entity, block_path=block_path)
            return []
        return self._polyline_record(entity, chain, block_path,
                                     [vertex.dxf.location for vertex in entity.vertices],
                                     bool(entity.is_closed))

    def _polyline_record(self, entity, chain, block_path, vertices, closed):
        if len(vertices) < 2:
            self.declare('CAD_POLYLINE_DEGENERATE', entity=entity, block_path=block_path,
                         detail=f'{len(vertices)} vertices')
            return []
        linear = self._linear(chain)
        mapped = [self._place(linear, vertex, entity.dxf.handle, block_path)
                  for vertex in vertices]
        if any(point is None for point in mapped):
            return []
        record = dict(self._base(entity, block_path, mapped[0]), entity_type=entity.dxftype(),
                      vertices=mapped, closed=bool(closed))
        return [self._emit(record, self._point_box(mapped))]

    def _text_bbox(self, entity, linear, anchor):
        points = [(anchor['x'], anchor['y'])]
        try:
            extent = self.bbox.extents([entity])
        except Exception:  # ezdxf raises a variety of font/geometry errors here
            extent = None
        if extent is not None and getattr(extent, 'has_data', False):
            for x, y in ((extent.extmin.x, extent.extmin.y), (extent.extmax.x, extent.extmax.y),
                         (extent.extmin.x, extent.extmax.y), (extent.extmax.x, extent.extmin.y)):
                points.append(self._map(linear, float(x), float(y)))
            self.warnings.append('CAD_TEXT_BOUNDS_APPROXIMATE')
        else:
            self.warnings.append('CAD_TEXT_BOUNDS_UNAVAILABLE:' + self.entity_label(entity))
        return padded_box([[x, y, x, y] for x, y in points])

    def _text(self, entity, chain, block_path):
        if (float(entity.dxf.get('oblique', 0)) != 0 or float(entity.dxf.get('width', 1)) != 1
                or int(entity.dxf.get('halign', 0)) != 0 or int(entity.dxf.get('valign', 0)) != 0
                or int(_dxf(entity, 'text_generation_flag', 0) or 0) != 0):
            self.declare('CAD_TEXT_TRANSFORM_UNSUPPORTED', entity=entity, block_path=block_path)
            return []
        text = entity.dxf.text
        height = float(entity.dxf.height)
        if (not text.strip() or not math.isfinite(height) or not 0 < height <= 1e6
                or any(ord(character) < 32 for character in text)):
            self.declare('CAD_TEXT_INVALID', entity=entity, block_path=block_path)
            return []
        linear = self._linear(chain)
        shape = self._shape(linear)
        if shape is None or shape[2]:
            self.declare('CAD_TEXT_IN_BLOCK_TRANSFORM_UNSUPPORTED', entity=entity,
                         block_path=block_path)
            return []
        insertion = self._place(linear, entity.dxf.insert, entity.dxf.handle, block_path)
        if insertion is None:
            return []
        record = dict(self._base(entity, block_path, insertion), entity_type='TEXT', text=text,
                      insertion=insertion, height=float(height * shape[0]),
                      rotation=float((float(entity.dxf.get('rotation', 0)) + shape[1]) % 360.0),
                      bbox=self._text_bbox(entity, linear, insertion))
        if record['layer'] == 'DM_DIAMETER':
            record['_entity'] = entity
            self.diameter_texts[_record_key(record) + ':' + '/'.join(record['block_path'])] = record
        return [self._emit(record, _box_tuple(record['bbox']))]

    def _mtext(self, entity, chain, block_path):
        raw, plain = entity.dxf.text, entity.plain_text()
        if not plain.strip():
            self.declare('CAD_TEXT_INVALID', entity=entity, block_path=block_path)
            return []
        if len(raw) > MAX_ANNOTATION or len(plain) > MAX_ANNOTATION:
            self.declare('CAD_MTEXT_TRUNCATED', entity=entity, block_path=block_path,
                         detail=f'{max(len(raw), len(plain))}>{MAX_ANNOTATION}')
            raw, plain = raw[:MAX_ANNOTATION], plain[:MAX_ANNOTATION]
        if any(ord(character) < 32 and character != '\n' for character in plain):
            self.declare('CAD_TEXT_CONTROL_CHARACTER_UNSUPPORTED', entity=entity,
                         block_path=block_path)
            return []
        height = float(entity.dxf.char_height)
        if not math.isfinite(height) or not 0 < height <= 1e6:
            self.declare('CAD_TEXT_INVALID', entity=entity, block_path=block_path)
            return []
        linear = self._linear(chain)
        shape = self._shape(linear)
        if shape is None or shape[2]:
            self.declare('CAD_TEXT_IN_BLOCK_TRANSFORM_UNSUPPORTED', entity=entity,
                         block_path=block_path)
            return []
        insertion = self._place(linear, entity.dxf.insert, entity.dxf.handle, block_path)
        if insertion is None:
            return []
        record = dict(self._base(entity, block_path, insertion), entity_type='MTEXT',
                      text=plain, raw_text=raw, insertion=insertion,
                      char_height=float(height * shape[0]),
                      rotation=float((float(entity.dxf.get('rotation', 0)) + shape[1]) % 360.0),
                      attachment_point=int(entity.dxf.get('attachment_point', 1)),
                      bbox=self._text_bbox(entity, linear, insertion))
        return [self._emit(record, _box_tuple(record['bbox']))]

    def _dimension(self, entity, chain, block_path):
        linear = self._linear(chain)
        defpoints = []
        for name in ('defpoint', 'defpoint2', 'defpoint3', 'text_midpoint'):
            value = _dxf(entity, name)
            if value is None:
                continue
            point = self._place(linear, value, entity.dxf.handle, block_path)
            if point is None:
                return []
            if point not in defpoints:
                defpoints.append(point)
        if not defpoints:
            self.declare('CAD_DIMENSION_DEFPOINTS_UNAVAILABLE', entity=entity, block_path=block_path)
            return []
        dimension_type = _DIMENSION_TYPES.get(int(_dxf(entity, 'dimtype', 0) or 0) & 7, 'unknown')
        measurement = None
        try:
            value = entity.get_measurement()
        except Exception:
            value = None
        if value is not None and math.isfinite(float(value)):
            measurement = float(value)
        declared = _dxf(entity, 'text')
        declared = declared.strip() if isinstance(declared, str) else ''
        declared_text = declared if declared not in ('', '<>') else None
        if declared_text is not None and len(declared_text) > MAX_ANNOTATION:
            self.declare('CAD_DIMENSION_TEXT_TRUNCATED', entity=entity, block_path=block_path)
            declared_text = declared_text[:MAX_ANNOTATION]
        record = dict(self._base(entity, block_path, defpoints[0]), entity_type='DIMENSION',
                      dimension_type=dimension_type, geometry_measurement=measurement,
                      declared_text=declared_text, defpoints=defpoints[:4],
                      dimstyle=(_dxf(entity, 'dimstyle') or None))
        self.features.append(self._native_dimension_feature(record))
        return [self._emit(record, self._point_box(defpoints))]

    @staticmethod
    def _decimal_text(value):
        if value is None:
            return None
        text = format(Decimal(repr(float(value))), 'f')
        return text if len(text) <= 64 else None

    def _native_dimension_feature(self, record):
        declared_text = record['declared_text']
        declared_value = None
        if declared_text is not None:
            candidate = declared_text[3:] if (record['dimension_type'] == 'diameter'
                                              and declared_text.startswith('%%c')) else declared_text
            if PLAIN_NUMBER.fullmatch(candidate):
                declared_value = candidate
        measurement = self._decimal_text(record['geometry_measurement'])
        authority = ('declared_text' if declared_text is not None
                     else ('dimension_measurement' if measurement is not None else 'unavailable'))
        return {'feature_id': 'dimension-' + _record_key(record), 'feature_type': 'native_dimension',
                'locator': self.locator(record), 'dimension_type': record['dimension_type'],
                'unit': self.unit, 'declared_text': declared_text, 'declared_value': declared_value,
                'geometry_measurement': measurement, 'value_authority': authority}

    # -- block references ------------------------------------------------------------
    def _insert(self, entity, chain, block_path, block_names):
        name = _dxf(entity, 'name') or '?'
        handle = entity.dxf.get('handle')
        if len(name) > MAX_ANNOTATION:
            return self._skipped_insert(entity, block_path, 'CAD_BLOCK_NAME_INVALID',
                                        detail=f'{len(name)} characters')
        if int(_dxf(entity, 'row_count', 1) or 1) > 1 \
                or int(_dxf(entity, 'column_count', 1) or 1) > 1:
            return self._skipped_insert(entity, block_path, 'CAD_MINSERT_ARRAY_UNSUPPORTED')
        scales = (float(_dxf(entity, 'xscale', 1)), float(_dxf(entity, 'yscale', 1)),
                  float(_dxf(entity, 'zscale', 1)))
        if any(scale == 0 for scale in scales):
            return self._skipped_insert(entity, block_path, 'CAD_INSERT_DEGENERATE_SCALE_UNSUPPORTED')
        if self._shape(self._linear(chain + [entity.matrix44()])) is None:
            return self._skipped_insert(entity, block_path, 'CAD_INSERT_NON_UNIFORM_SCALE_UNSUPPORTED')
        try:
            block = self.doc.blocks.get(name)
        except KeyError:
            return self._skipped_insert(entity, block_path, 'CAD_BLOCK_MISSING',
                                        detail=f'block {name!r} is not defined')
        if name in block_names:
            return self._skipped_insert(entity, block_path, 'CAD_BLOCK_RECURSION_CYCLE',
                                        detail=' -> '.join(list(block_names) + [name]))
        if len(block_path) >= self.limits.max_block_depth:
            return self._skipped_insert(entity, block_path, 'CAD_BLOCK_DEPTH_LIMIT_REACHED',
                                        detail=f'depth {len(block_path)} >= {self.limits.max_block_depth}')
        insertion = self._place(self._linear(chain), entity.dxf.insert, handle, block_path)
        if insertion is None:
            return []
        declared_before = sum(self.counts.values())
        # Reserve this reference's own slot before expanding, so the bound cannot be
        # exceeded by the references whose records are emitted after their content.
        self.emitted += 1
        child_chain = chain + [entity.matrix44()]
        # Expand the block into a side buffer so the reference record can be emitted
        # *before* the content it reaches, keeping document order in the snapshot.
        outer_entities, outer_bounds = self.entities, self.bounds
        self.entities, self.bounds = [], []
        try:
            for child in block:
                self.extract(child, child_chain, block_path + [handle], block_names + (name,))
            children, child_bounds = self.entities, self.bounds
        finally:
            self.entities, self.bounds = outer_entities, outer_bounds
        status = ('expanded_with_gaps' if sum(self.counts.values()) > declared_before
                  else 'expanded')
        record = dict(self._base(entity, block_path, insertion), entity_type='INSERT',
                      block_name=name, insertion=insertion,
                      rotation=float(entity.dxf.get('rotation', 0)),
                      scale_x=scales[0], scale_y=scales[1], expansion_status=status,
                      expansion_code=None, expanded_entity_count=len(children))
        emitted = self._emit(record, (insertion['x'], insertion['y'],
                                      insertion['x'], insertion['y']), count=False)
        self.entities.extend(children)
        self.bounds.extend(child_bounds)
        return [emitted]

    def _skipped_insert(self, entity, block_path, code, detail=None):
        self.declare(code, entity=entity, block_path=block_path, detail=detail)
        insertion = _finite2(_dxf(entity, 'insert', (0, 0, 0))) or {'x': 0.0, 'y': 0.0}
        record = dict(self._base(entity, block_path, insertion), entity_type='INSERT',
                      block_name=(_dxf(entity, 'name') or '?'),
                      insertion=insertion, rotation=float(entity.dxf.get('rotation', 0)),
                      scale_x=float(_dxf(entity, 'xscale', 1)),
                      scale_y=float(_dxf(entity, 'yscale', 1)), expansion_status='skipped',
                      expansion_code=code, expanded_entity_count=0)
        return [self._emit(record, (insertion['x'], insertion['y'],
                                    insertion['x'], insertion['y']))]

    # -- labelled diameters (v1 semantics, v2 locators) ------------------------------
    def diameter_features(self):
        return [self._diameter_feature(record) for record in self.diameter_texts.values()]

    def _diameter_feature(self, record):
        entity, annotation = record['_entity'], record['text']
        candidates, evidence, selected = sorted(self.model_circles), None, None
        if self.positional:
            # A target candidate is an explicit **source handle**; a handle-less source
            # has none, so no association can be confirmed from XDATA here. The
            # annotation text itself is still read (it is text, not an identity).
            candidates = []
            if entity.has_xdata('DATAMIND'):
                self.declare('CAD_REFERENCE_TARGET_INVALID', entity=entity,
                             detail='source has no entity handles')
        elif entity.has_xdata('DATAMIND'):
            tags = list(entity.get_xdata('DATAMIND'))
            if not tags or tags[0] != (1000, 'diameter_target') \
                    or any(tag.code != 1005 for tag in tags[1:]):
                self.declare('CAD_REFERENCE_FORMAT_UNSUPPORTED', entity=entity)
                candidates = []
            else:
                references = [tag.value for tag in tags[1:]]
                if (not references or len(set(references)) != len(references)
                        or any(reference not in self.model_circles for reference in references)):
                    self.declare('CAD_REFERENCE_TARGET_INVALID', entity=entity,
                                 detail=','.join(references) or 'no target')
                    candidates = []
                else:
                    candidates = sorted(references)
                    if len(candidates) == 1:
                        selected = candidates[0]
                        evidence = dict(
                            method='native_reference', reference_id='xdata-' + entity.dxf.handle,
                            source_sha256=self.sha, annotation_handle=entity.dxf.handle,
                            target_handle=selected,
                            rationale='Explicit DATAMIND XDATA diameter_target handle in the source DXF; no business approval inferred.')
        if len(candidates) > 100:
            self.declare('CAD_TOO_MANY_ASSOCIATION_CANDIDATES', entity=entity)
            candidates = []
        state = 'confirmed' if evidence else ('ambiguous' if len(candidates) > 1 else 'unresolved')
        nominal = lower = upper = None
        match = DIAMETER_ANNOTATION.fullmatch(annotation)
        if self.unit != 'unknown' and match and len(match[1]) <= 64 and len(match[2]) <= 64:
            with localcontext() as context:
                context.prec = 160
                size, tolerance = Decimal(match[1]), Decimal(match[2])
                if size > 0 and size - tolerance > 0:
                    nominal, lower, upper = (format(size, 'f'), format(-tolerance, 'f'),
                                             format(tolerance, 'f'))
        if nominal is None:
            self.warnings.append('CAD_DIMENSION_VALUE_UNKNOWN:' + _record_key(record))
        return {'feature_id': 'diameter-' + _record_key(record), 'feature_type': 'diameter',
                'annotation_raw': annotation, 'locator': self.locator(record),
                'nominal_value': nominal, 'tolerance_lower': lower, 'tolerance_upper': upper,
                'unit': self.unit, 'association_status': state, 'target_candidates': candidates,
                'selected_target': selected, 'association_evidence': evidence}

    # -- identity --------------------------------------------------------------------
    def capture_identity(self):
        conflicted = set()
        for record in self.entities:
            if record['entity_type'] != 'TEXT' or record['layer'] not in TITLE_LAYERS:
                continue
            key, value = TITLE_LAYERS[record['layer']], record['text']
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
                self.declare('CAD_TITLE_VALUE_INVALID', handle=record['handle'], entity_type='TEXT',
                             block_path=record['block_path'], detail=record['layer'])
                conflicted.add(key)
                self.identity.pop(key, None)
                continue
            if key in self.identity or key in conflicted:
                self.declare('CAD_TITLE_DUPLICATE', handle=record['handle'], entity_type='TEXT',
                             block_path=record['block_path'], detail=record['layer'])
                conflicted.add(key)
                self.identity.pop(key, None)
                continue
            self.identity[key] = {'value': value, 'locator': self.locator(record)}
        return self.identity


def unsupported_features(unsupported, counts):
    """Item-by-item declarations, capped at the documented limit plus a total."""
    items = [_feature_string(item) for item in unsupported]
    declared = sum(counts.values())
    if declared > len(items):
        items.append(f'CAD_UNSUPPORTED_ITEMS_TRUNCATED:{declared}')
    return items


def _feature_string(item):
    parts = [item['code']]
    if item.get('entity_type'):
        parts.append(str(item['entity_type']))
    if item.get('handle'):
        parts.append(str(item['handle']))
    elif item.get('entity_index') is not None:
        # Positional mode names the entity by its declared ordinal; the string is
        # `CODE:TYPE:#<index>[:DETAIL]` and never carries a synthesised handle.
        parts.append('#' + str(item['entity_index']))
    text = ':'.join(parts)
    if item.get('detail') is not None:
        text += ':' + str(item['detail'])
    return text


def parse_bytes(data, *, document_id, document_version=1, provenance='synthetic_demo', limits=None,
                max_bytes=None, max_entities=None, max_expanded_entities=None, max_block_depth=None):
    overrides = {'max_bytes': max_bytes, 'max_entities': max_entities,
                 'max_expanded_entities': max_expanded_entities, 'max_block_depth': max_block_depth}
    limits = limits or resolve_limits({key: value for key, value in overrides.items()
                                       if value is not None})
    if not data or len(data) > limits.max_bytes:
        raise CadReadError('CAD_SOURCE_SIZE_INVALID', {'bytes': len(data), 'max': limits.max_bytes})
    if data.startswith(b'AutoCAD Binary DXF') or b'\x00' in data:
        raise CadReadError('CAD_BINARY_DXF_UNSUPPORTED')
    text, encoding = _decode(data)
    pairs = _tags(text)
    entities, blocks = _scan_source_records(pairs)
    identity_mode = _validate_source_records(entities, blocks, limits)
    # `ezdxf.read(io.StringIO(...))` performs no newline translation and its
    # `ascii_tags_loader` only strips a trailing '\n', so a CRLF source would hand every
    # value a trailing '\r' (the section name becomes 'ENTITIES\r' and the whole ENTITIES
    # section is ignored). Windows CAD exports are routinely CRLF, so the text handed to
    # the library is newline-normalised; the raw tags, the digest and the decoded text
    # are untouched.
    header = _header_values(pairs)
    # Tri-state on purpose: `None` means the source does not declare `$HANDLING` at all,
    # which is not a contradiction with anything (ezdxf's own writer omits the variable).
    # Only an *explicit* flag that disagrees with the record tags is worth reporting.
    declared_handling = header.get('$HANDLING')
    handling_declared = None if declared_handling is None else _declared_flag(declared_handling)
    ezdxf, bbox_module = _library()
    try:
        doc = ezdxf.read(io.StringIO(text.replace('\r\n', '\n').replace('\r', '\n')))
    except (ezdxf.DXFError, ValueError, TypeError) as error:
        raise CadReadError('CAD_DXF_INVALID') from error
    # Integrity is checked against what `ezdxf.read` produced, *before* `audit()` is given
    # the chance to repair anything: a repair must never be presented as a clean read.
    # `(dxftype, handle)` order, the same as `_scan_source_records` returns.
    read_records = [(entity.dxftype(), entity.dxf.get('handle'))
                    for layout in doc.layouts for entity in layout]
    read_blocks = [(entity.dxftype(), entity.dxf.get('handle'))
                   for block in doc.blocks
                   if not block.name.startswith(('*Model_Space', '*Paper_Space'))
                   for entity in block]
    source_records = [record for record in entities if record[0] not in _SUB_ENTITY_TYPES]
    source_blocks = [record for record in blocks if record[0] not in _SUB_ENTITY_TYPES]
    index_by_id = {}
    if identity_mode == 'positional':
        # A handle-less source is compared by entity *type*: the handles `ezdxf` reports
        # here are synthesised, so they are evidence of nothing.
        index_by_id = _positional_indices(read_records, source_records, doc)
        if Counter(kind for kind, _handle in read_blocks) \
                != Counter(record[0] for record in source_blocks):
            raise CadReadError('CAD_BLOCK_ENTITY_LOSS_UNSUPPORTED',
                               {'raw': len(source_blocks), 'read': len(read_blocks)})
    else:
        if set(read_records) != set(source_records):
            raise CadReadError('CAD_ENTITY_LOSS_OR_LAYOUT_UNSUPPORTED',
                               {'raw': len(entities), 'read': len(read_records)})
        if set(read_blocks) != set(source_blocks):
            raise CadReadError('CAD_BLOCK_ENTITY_LOSS_UNSUPPORTED',
                               {'raw': len(blocks), 'read': len(read_blocks)})
    if identity_mode == 'source':
        # Only a handle-bearing source can be checked for entity loss by handle; for a
        # handle-less one `_positional_indices` already proved the type multiset matches,
        # and `_visited` would read back the very synthetic handles this mode rejects.
        reachable = {handle for layout in doc.layouts for entity in layout
                     for handle in _visited(entity)}
        reachable |= {handle for block in doc.blocks for entity in block
                      for handle in _visited(entity)}
        lost = sorted({handle for _kind, handle in entities + blocks} - reachable)
        if lost:
            raise CadReadError('CAD_ENTITY_LOSS_OR_LAYOUT_UNSUPPORTED',
                               {'lost_handles': lost[:10], 'count': len(lost)})
    before_handles = {handle for _kind, handle in read_records + read_blocks}
    insert_names = {entity.dxf.get('handle'): entity.dxf.get('name')
                    for layout in doc.layouts for entity in layout
                    if entity.dxftype() == 'INSERT'}
    audit = doc.audit()
    if audit.errors:
        raise CadReadError('CAD_DXF_AUDIT_ERRORS',
                           {'errors': len(audit.errors), 'fixes': len(audit.fixes)})

    sha = hashlib.sha256(data).hexdigest()
    # The unit comes from the *source* header, never from `ezdxf`'s header: ezdxf answers
    # 6 (metres) for a file that declares no `$INSUNITS` at all. An absent variable is the
    # documented DXF default 0, which stays `unknown` and is never guessed into millimetres.
    declared_unit = header.get('$INSUNITS')
    unit_code = 0
    if declared_unit is not None:
        try:
            unit_code = int(str(declared_unit).strip())
        except (TypeError, ValueError):
            unit_code = 0
    insunits_modelled = unit_code in (0, 1, 4)
    unit = {0: 'unknown', 1: 'in', 4: 'mm'}.get(unit_code, 'unknown')
    extractor = _Extractor(doc, limits=limits, sha=sha, unit=unit, bbox_module=bbox_module,
                           identity_mode=identity_mode, index_by_id=index_by_id)
    if declared_unit is not None:
        try:
            int(str(declared_unit).strip())
        except (TypeError, ValueError):
            extractor.declare('CAD_INSUNITS_INVALID', detail=repr(declared_unit))
    if not insunits_modelled:
        extractor.declare('CAD_UNIT_CODE_NOT_MODELLED', detail=str(unit_code))
    if unit == 'unknown':
        extractor.warnings.append('CAD_UNIT_UNKNOWN')
    if handling_declared is not None and handling_declared != (identity_mode == 'source'):
        # The `$HANDLING` flag and the record tags disagree; the tags decide, and the
        # disagreement is reported instead of being silently resolved either way. An
        # absent flag is *not* a disagreement and is not reported.
        extractor.warnings.append('CAD_HANDLING_FLAG_INCONSISTENT')
    if identity_mode == 'positional':
        # The declared gap: no entity record in the source carries a handle at all, so the
        # drawing is located by `entity_index`. The count is the number of records that
        # would have carried a handle.
        extractor.declare(SOURCE_HANDLE_UNAVAILABLE,
                          detail=str(len(source_records) + len(source_blocks)))
        extractor.warnings.append('CAD_IDENTITY_MODE_POSITIONAL')
    if encoding != 'utf-8':
        extractor.warnings.append('CAD_ENCODING_FALLBACK:' + encoding)
    if audit.fixes:
        extractor.warnings.append(f'CAD_DXF_AUDIT_FIXES:{len(audit.fixes)}')
        after_handles = {entity.dxf.get('handle') for layout in doc.layouts for entity in layout}
        after_handles |= {entity.dxf.get('handle') for block in doc.blocks for entity in block}
        for handle in sorted(before_handles - after_handles):
            name = insert_names.get(handle)
            # In positional mode the library's synthesised handle is not a source value,
            # so it is not carried into the declaration; the removal is still counted.
            reported = None if extractor.positional else handle
            extractor.declare('CAD_DXF_AUDIT_ENTITY_REMOVED', handle=reported,
                              entity_type='INSERT' if name else None,
                              detail=f'block {name!r}' if name else None)
            if name and name not in {block.name for block in doc.blocks}:
                # The repair deleted a reference to a block the file never defines.
                extractor.declare('CAD_BLOCK_MISSING', handle=reported, entity_type='INSERT',
                                  detail=f'block {name!r} is not defined')
        if after_handles - before_handles:
            extractor.declare('CAD_DXF_AUDIT_ENTITY_ADDED',
                              detail=str(len(after_handles - before_handles)))

    paper = [layout for layout in doc.layouts if layout.name != 'Model']
    paper_count = sum(len(layout) for layout in paper)
    if paper_count:
        first = next(entity for layout in paper for entity in layout)
        extractor.declare('CAD_PAPERSPACE_ENTITIES_SKIPPED', entity=first,
                          detail=f'{paper_count} entities in '
                                 + ','.join(layout.name for layout in paper))
    for entity in doc.modelspace():
        extractor.extract(entity, [], [], ())
    features = extractor.diameter_features() + extractor.features
    identity = extractor.capture_identity()
    for record in extractor.entities:
        record.pop('_entity', None)
    declarations = unsupported_features(extractor.unsupported, extractor.counts)
    warnings = list(dict.fromkeys(extractor.warnings))
    if not extractor.entities:
        raise CadReadError('CAD_NO_SUPPORTED_ENTITIES',
                           {'unsupported': extractor.unsupported,
                            'unsupported_features': declarations,
                            'identity': identity_mode})
    payload = {'contract_version': CONTRACT_VERSION,
               'source': {'document_id': document_id, 'document_version': document_version,
                          'source_sha256': sha, 'format': 'dxf', 'parser_version': PARSER_VERSION,
                          'provenance': provenance, 'insunits': unit_code,
                          'insunits_modelled': insunits_modelled, 'unit': unit,
                          'encoding': encoding, 'coordinate_system': 'WCS_XY', 'layout': 'Model'},
               'identity': identity,
               'views': [{'view_id': VIEW_ID, 'bbox': padded_box(extractor.bounds)}],
               'entities': extractor.entities, 'features': features}
    try:
        snapshot = DrawingSnapshot.model_validate(payload).model_dump()
    except ValidationError as error:
        raise CadReadError('CAD_OUTPUT_CONTRACT_INVALID', {'validation': str(error)}) from error
    return {'snapshot': snapshot, 'metadata': {
        'profile': PROFILE, 'parser_version': PARSER_VERSION, 'ezdxf_version': ezdxf.__version__,
        'dxf_version': doc.dxfversion, 'encoding': encoding, 'warnings': warnings,
        'unsupported': extractor.unsupported, 'unsupported_features': declarations,
        'unsupported_counts': dict(sorted(extractor.counts.items())),
        'first_unsupported': extractor.unsupported[0] if extractor.unsupported else None,
        # The identity mode and *why* it was chosen. `handling_declared` is the raw
        # `$HANDLING` header flag; `source_handle_records` is how many raw records really
        # carry a `code 5` tag. The tags decide the mode, the flag is only recorded.
        'identity': {'mode': identity_mode, 'handling_declared': handling_declared,
                     'source_handle_records': sum(1 for _kind, handle in entities + blocks
                                                  if handle is not None),
                     'positional_records': (len(source_records) + len(source_blocks)
                                            if identity_mode == 'positional' else 0)},
        'limits': {'max_bytes': limits.max_bytes, 'max_entities': limits.max_entities,
                   'max_expanded_entities': limits.max_expanded_entities,
                   'max_block_depth': limits.max_block_depth,
                   'max_declared_items': limits.max_declared_items},
        'counts': {'source_entities': len(entities), 'source_block_entities': len(blocks),
                   'entities': len(snapshot['entities']), 'features': len(snapshot['features'])}}}


def parse_file(path, **context):
    path = Path(path)
    if path.suffix.lower() != '.dxf':
        raise CadReadError('CAD_FORMAT_UNSUPPORTED')
    limits = context.get('limits') or resolve_limits(
        {key: value for key, value in context.items() if key in LIMIT_ENV and value is not None})
    with path.open('rb') as stream:
        data = stream.read(limits.max_bytes + 1)
    return parse_bytes(data, **context)
