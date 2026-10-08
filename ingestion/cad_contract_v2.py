"""`cad-contract-v2`: the source contract of the wide 2D engineering-DXF profile.

`cad-contract-v1` (`ingestion/cad_contract.py`) is hash-frozen in
`data/evaluation/frozen/cad_dxf_v1` and stays exactly as it was: it is a *labelled
interchange* contract (three DM_* title layers, LINE/CIRCLE/TEXT, one `%%c.. +/-..`
annotation form). Real CAD exports carry polylines, arcs, block references, MTEXT and
native DIMENSION entities, so v2 is a **new** contract instead of a widened v1.

What v2 changes, and why each change is safe to make in a new version:

* New entity types (`LWPOLYLINE`/`POLYLINE`, `ARC`, `INSERT` references, `MTEXT`,
  native `DIMENSION`) keep every v1 discipline: raw source handle, layer, one logical
  `model` view, WCS XY only, and **no invented geometry**.
* `block_path` on the locator/entity names the INSERT chain an expanded entity came
  from. A block-internal handle is only unique together with that chain, so a locator
  that omitted it could point at the wrong copy of the same block.
* `identity` is now *optional*: a real drawing is not required to use the DM_* layers,
  and a missing title is reported as "not declared", never fabricated.
* `unit` still comes from `$INSUNITS` alone. Codes other than 0/1/4 are reported as
  `unknown` (`insunits_modelled=False`) and never guessed into `mm`.
* **Two explicit identity modes.** A source whose entity/block records really carry
  `code 5` is read in `handle_source='source'` mode and the raw handle is the identity,
  exactly as before. A source whose entity records carry **no** `code 5` at all (an old
  R2.x/R12 drawing exported with `$HANDLING` off) is read in `handle_source='positional'`
  mode: the entity keeps `handle=None` -- the library's synthetic handle is never adopted
  as if it came from the source -- and gets a deterministic, 1-based `entity_index`
  instead. `handle_source`/`entity_index` are only serialised in positional mode, so a
  source-handle snapshot keeps its previous shape.
* Native dimensions separate the **geometric measurement** of the dimension from the
  **text the drafter declared**. `value_authority` records which one a value is, so
  `declared_sizes()` can refuse to promote a measurement into a manufacturing size.

The module is validation only: it does not read DXF files, call models or touch the
database. `ingestion/cad_dxf_v2.py` produces these payloads and
`ingestion/cad_adapter.py` projects them into the unified parse contract.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import math
import re
from typing import Annotated, Literal, Union

from pydantic import Field, model_serializer, model_validator

from .cad_contract import (AssociationEvidence, Box, ContractModel, DecimalText, Handle,
                           Identifier, Number, Point, Sha256, Text, Unit, View)

CONTRACT_VERSION = 'cad-contract-v2'
# The two declared identity modes. `source` means every entity is located by the raw
# `code 5` handle the source itself carries; `positional` means the source carries no
# handle at all and the entity is located by its `entity_index`. They are mutually
# exclusive per drawing and are never mixed.
IDENTITY_MODES = ('source', 'positional')
# Declared gaps: the source has no handles at all (`CAD_SOURCE_HANDLE_UNAVAILABLE`,
# counted, never silent), and a block reference inside such a source cannot be expanded
# because a block-internal entity has no handle to disambiguate repeated INSERTs
# (`CAD_POSITIONAL_BLOCK_UNSUPPORTED`).
SOURCE_HANDLE_UNAVAILABLE = 'CAD_SOURCE_HANDLE_UNAVAILABLE'
POSITIONAL_BLOCK_UNSUPPORTED = 'CAD_POSITIONAL_BLOCK_UNSUPPORTED'
# Ceilings the contract itself enforces. The reader's tunable limits
# (`DATAMIND_CAD_MAX_ENTITIES` and friends) may not exceed them, so a "validated"
# snapshot always fits the model that validated it.
MAX_ENTITIES = 200_000
MAX_FEATURES = 100_000
MAX_BLOCK_PATH = 32
MAX_ANNOTATION = 4096
LongText = Annotated[str, Field(min_length=1, max_length=16384)]
DimensionType = Literal['linear', 'aligned', 'angular', 'diameter', 'radius',
                        'angular_3p', 'ordinate', 'unknown']
# The only annotation form whose numeric fields v2 claims to read is the same one v1
# reads; the text is kept verbatim either way.
DIAMETER_ANNOTATION = re.compile(r'%%c([0-9]+(?:\.[0-9]+)?) \+/-([0-9]+(?:\.[0-9]+)?)')
PLAIN_NUMBER = re.compile(r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')
_UNIT_BY_INSUNITS = {0: 'unknown', 1: 'in', 4: 'mm'}
# A positional identity is a 1-based ordinal of the entity's own primary record inside
# the source's ENTITIES section (see `ingestion/cad_dxf_v2.py` for the exact counting
# rule). It is an int, never the library's synthetic handle string.
EntityIndex = Annotated[int, Field(strict=True, ge=1)]
HandleSource = Literal['source', 'positional']


def identity_mode(handle, entity_index, handle_source):
    """Return the declared identity mode, refusing a half-declared or forged one.

    * `source` (the default when a handle is present): the raw `code 5` handle is the
      identity and it is required; a positional index is not part of this mode.
    * `positional`: an `entity_index` is required and a `handle` is **refused**, because
      a handle in a handle-less source could only be a value the library invented.
    """
    if handle_source is not None and handle_source not in IDENTITY_MODES:
        raise ValueError('CAD_HANDLE_SOURCE_INVALID')
    if handle is None and entity_index is None:
        raise ValueError('CAD_ENTITY_IDENTITY_MISSING')
    mode = handle_source or ('source' if handle is not None else 'positional')
    if mode == 'source':
        if handle is None:
            raise ValueError('CAD_SOURCE_HANDLE_REQUIRED')
    elif handle is not None or entity_index is None:
        # A handle may not appear in positional mode: see `CAD_ENTITY_HANDLE_INVALID`.
        raise ValueError('CAD_POSITIONAL_IDENTITY_INVALID')
    return mode


class Source(ContractModel):
    document_id: Identifier
    document_version: Annotated[int, Field(gt=0)]
    source_sha256: Sha256
    format: Literal['dxf']
    parser_version: Identifier
    provenance: Literal['synthetic_demo', 'authorized_external']
    # The raw DXF setting is retained; `insunits_modelled` says whether this contract
    # has a name for it. An unmodelled code is `unknown`, never a guessed millimetre.
    insunits: Annotated[int, Field(strict=True)]
    insunits_modelled: Annotated[bool, Field(strict=True)]
    unit: Unit
    # The text codec the source bytes were read with (`utf-8` when it decoded as UTF-8).
    encoding: Identifier
    coordinate_system: Literal['WCS_XY']
    layout: Literal['Model']

    @model_validator(mode='after')
    def units(self):
        expected = _UNIT_BY_INSUNITS.get(self.insunits)
        if self.insunits_modelled != (expected is not None):
            raise ValueError('CAD_UNIT_MODELLED_FLAG_MISMATCH')
        if expected is None:
            if self.unit != 'unknown':
                raise ValueError('CAD_UNIT_SETTING_MISMATCH')
        elif self.unit != expected:
            raise ValueError('CAD_UNIT_SETTING_MISMATCH')
        return self


class _Identified(ContractModel):
    """Base of the two records that carry an identity, in one of the two declared modes.

    Only the identity fields a record actually uses are serialised. A `source`-mode record
    therefore dumps exactly the keys it dumped before positional identity existed (no
    `entity_index`/`handle_source: null` noise), and a `positional`-mode record dumps
    `entity_index`/`handle_source` with **no** `handle` key at all -- so a snapshot can
    never be read as if a handle were present.
    """

    @model_serializer(mode='wrap')
    def _identity_only_when_declared(self, handler):
        data = handler(self)
        if isinstance(data, dict):
            for key in ('handle', 'entity_index', 'handle_source'):
                if data.get(key) is None:
                    data.pop(key, None)
        return data


class Locator(_Identified):
    """A v2 source locator: a v1 locator plus the INSERT chain of an expanded entity.

    ``block_path`` is the ordered list of INSERT handles from the Model-space reference
    down to the block that contains the entity; an empty list means the entity lives
    directly in Model space. In ``source`` mode ``(handle, block_path)`` is unique inside
    one drawing; in ``positional`` mode the identity is the ``entity_index`` and
    ``handle`` is absent (a positional locator never carries a handle, and a non-empty
    ``block_path`` is impossible because block expansion is refused in that mode).
    """
    locator_kind: Literal['cad_entity']
    source_sha256: Sha256
    handle: Handle | None = None
    entity_index: EntityIndex | None = None
    handle_source: HandleSource | None = None
    layer: Text
    view_id: Identifier
    coordinate_system: Literal['WCS_XY']
    layout: Literal['Model']
    block_path: Annotated[list[Handle], Field(max_length=MAX_BLOCK_PATH)]

    @model_validator(mode='after')
    def identity(self):
        mode = identity_mode(self.handle, self.entity_index, self.handle_source)
        if mode == 'positional' and self.block_path:
            # `block_path` can only name INSERT handles, so a positional locator may not
            # carry one: it would smuggle a handle into a handle-less identity.
            raise ValueError('CAD_POSITIONAL_BLOCK_PATH_FORBIDDEN')
        return self

    def key(self):
        return (self.handle, self.entity_index, tuple(self.block_path))


class Entity(_Identified):
    handle: Handle | None = None
    entity_index: EntityIndex | None = None
    handle_source: HandleSource | None = None
    layer: Text
    view_id: Identifier
    block_path: Annotated[list[Handle], Field(max_length=MAX_BLOCK_PATH)]
    # A real point of the entity in WCS (insertion point, centre, first vertex, ...).
    # `cad_adapter` projects it into the unified locator's `model_xy`; it is never a
    # computed centre of an invented bounding box.
    anchor: Point

    @model_validator(mode='after')
    def identity(self):
        mode = identity_mode(self.handle, self.entity_index, self.handle_source)
        if mode == 'positional' and self.block_path:
            raise ValueError('CAD_POSITIONAL_BLOCK_PATH_FORBIDDEN')
        return self

    def bounds(self):
        raise NotImplementedError

    def key(self):
        return (self.handle, self.entity_index, tuple(self.block_path))


def _ordered(values):
    xs = [float(p[0]) for p in values]
    ys = [float(p[1]) for p in values]
    return (min(xs), min(ys), max(xs), max(ys))


def _angle_in_sweep(angle, start, end):
    angle, start, end = angle % 360.0, start % 360.0, end % 360.0
    if start <= end:
        return start <= angle <= end
    return angle >= start or angle <= end


def arc_bounds(center, radius, start_angle, end_angle):
    """Exact bounding box of a CCW arc from ``start_angle`` to ``end_angle`` (degrees)."""
    points = [(center[0] + radius * math.cos(math.radians(a)),
               center[1] + radius * math.sin(math.radians(a)))
              for a in (start_angle, end_angle)]
    for quadrant in (0, 90, 180, 270):
        if _angle_in_sweep(quadrant, start_angle, end_angle):
            points.append((center[0] + radius * math.cos(math.radians(quadrant)),
                           center[1] + radius * math.sin(math.radians(quadrant))))
    return _ordered(points)


class Line(Entity):
    entity_type: Literal['LINE']
    start: Point
    end: Point

    @model_validator(mode='after')
    def anchored_and_nonzero(self):
        if self.start == self.end:
            raise ValueError('CAD_ZERO_LENGTH_LINE')
        if self.anchor != self.start:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        return self

    def bounds(self):
        return _ordered([(self.start.x, self.start.y), (self.end.x, self.end.y)])


class Circle(Entity):
    entity_type: Literal['CIRCLE']
    center: Point
    radius: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]

    @model_validator(mode='after')
    def anchored(self):
        if self.anchor != self.center:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        return self

    def bounds(self):
        return (self.center.x - self.radius, self.center.y - self.radius,
                self.center.x + self.radius, self.center.y + self.radius)


class Arc(Entity):
    entity_type: Literal['ARC']
    center: Point
    radius: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    start_angle: Number
    end_angle: Number

    @model_validator(mode='after')
    def anchored_and_swept(self):
        if self.anchor != self.center:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        if self.start_angle % 360.0 == self.end_angle % 360.0:
            raise ValueError('CAD_ZERO_SWEEP_ARC')
        return self

    def bounds(self):
        return arc_bounds((self.center.x, self.center.y), self.radius,
                          self.start_angle, self.end_angle)


class Polyline(Entity):
    """`LWPOLYLINE`/`POLYLINE` with straight segments only.

    A non-zero bulge would make a segment an arc; the reader refuses that entity and
    declares it rather than reporting a straight line where the source has a curve.
    """
    entity_type: Literal['LWPOLYLINE', 'POLYLINE']
    vertices: Annotated[list[Point], Field(min_length=2, max_length=MAX_ENTITIES)]
    closed: Annotated[bool, Field(strict=True)]

    @model_validator(mode='after')
    def anchored(self):
        if self.anchor != self.vertices[0]:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        return self

    def bounds(self):
        return _ordered([(v.x, v.y) for v in self.vertices])


class NativeText(Entity):
    entity_type: Literal['TEXT']
    text: Text
    insertion: Point
    height: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    # Rotation in WCS degrees, after the INSERT transform when the entity came from one.
    rotation: Number
    # Present only when ezdxf could compute extents; `None` means "not measured", which
    # the rendering and scoring paths must not read as a pixel-accurate outline.
    bbox: Box | None

    @model_validator(mode='after')
    def anchored(self):
        if self.anchor != self.insertion:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        if self.bbox is not None and not self.bbox.contains(
                (self.insertion.x, self.insertion.y, self.insertion.x, self.insertion.y)):
            raise ValueError('CAD_TEXT_INSERTION_OUTSIDE_BBOX')
        return self

    def bounds(self):
        if self.bbox is None:
            return (self.insertion.x, self.insertion.y, self.insertion.x, self.insertion.y)
        return self.bbox.xmin, self.bbox.ymin, self.bbox.xmax, self.bbox.ymax


class MText(Entity):
    """`MTEXT`: `text` is the plain reading, `raw_text` keeps the DXF format codes."""
    entity_type: Literal['MTEXT']
    text: LongText
    raw_text: LongText
    insertion: Point
    char_height: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    rotation: Number
    attachment_point: Annotated[int, Field(ge=0, le=9)]
    bbox: Box | None

    @model_validator(mode='after')
    def anchored(self):
        if self.anchor != self.insertion:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        if self.bbox is not None and not self.bbox.contains(
                (self.insertion.x, self.insertion.y, self.insertion.x, self.insertion.y)):
            raise ValueError('CAD_TEXT_INSERTION_OUTSIDE_BBOX')
        return self

    def bounds(self):
        if self.bbox is None:
            return (self.insertion.x, self.insertion.y, self.insertion.x, self.insertion.y)
        return self.bbox.xmin, self.bbox.ymin, self.bbox.xmax, self.bbox.ymax


class Insert(Entity):
    """A block reference. Its content is expanded into sibling entities carrying this
    handle in their `block_path`; this record keeps the reference itself locatable."""
    entity_type: Literal['INSERT']
    block_name: Text
    insertion: Point
    rotation: Number
    scale_x: Number
    scale_y: Number
    expansion_status: Literal['expanded', 'expanded_with_gaps', 'skipped']
    expansion_code: Text | None
    expanded_entity_count: Annotated[int, Field(ge=0)]

    @model_validator(mode='after')
    def anchored_and_consistent(self):
        if self.anchor != self.insertion:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        if (self.expansion_status == 'skipped') != (self.expansion_code is not None):
            raise ValueError('CAD_INSERT_EXPANSION_STATUS_INCONSISTENT')
        return self

    def bounds(self):
        return (self.insertion.x, self.insertion.y, self.insertion.x, self.insertion.y)


class Dimension(Entity):
    """A native `DIMENSION` entity.

    `geometry_measurement` is what the dimension geometry measures.
    `declared_text` is a drafter's explicit text override (`None` when the source says
    `<>`/empty, i.e. the value is generated from the geometry). The two are never
    conflated here; `value_authority` on the feature says which one a value is.
    """
    entity_type: Literal['DIMENSION']
    dimension_type: DimensionType
    geometry_measurement: Number | None
    declared_text: Text | None
    defpoints: Annotated[list[Point], Field(min_length=1, max_length=4)]
    dimstyle: Text | None

    @model_validator(mode='after')
    def anchored(self):
        if self.anchor != self.defpoints[0]:
            raise ValueError('CAD_ANCHOR_NOT_ON_ENTITY')
        return self

    def bounds(self):
        return _ordered([(p.x, p.y) for p in self.defpoints])


NativeEntity = Annotated[Union[Line, Circle, Arc, Polyline, NativeText, MText, Insert, Dimension],
                         Field(discriminator='entity_type')]


class TitleField(ContractModel):
    value: Identifier
    locator: Locator


class BusinessIdentity(ContractModel):
    """Optional on purpose: a real drawing need not use the labelled DM_* layers, and a
    title that is not declared is reported as absent rather than fabricated."""
    drawing_id: TitleField | None = None
    drawing_revision: TitleField | None = None
    part_id: TitleField | None = None


class DiameterFeature(ContractModel):
    """v1's labelled diameter annotation, carried into v2 unchanged in meaning."""
    feature_id: Identifier
    feature_type: Literal['diameter']
    annotation_raw: Text
    locator: Locator
    nominal_value: DecimalText | None
    tolerance_lower: DecimalText | None
    tolerance_upper: DecimalText | None
    unit: Unit
    association_status: Literal['confirmed', 'ambiguous', 'unresolved']
    target_candidates: Annotated[list[Handle], Field(max_length=100)]
    selected_target: Handle | None
    association_evidence: AssociationEvidence | None

    @model_validator(mode='after')
    def safe_values_and_association(self):
        values = (self.nominal_value, self.tolerance_lower, self.tolerance_upper)
        if any(v is None for v in values) and not all(v is None for v in values):
            raise ValueError('CAD_PARTIAL_DIMENSION')
        if self.nominal_value is not None:
            n, lo, hi = (Decimal(v) for v in values)
            with localcontext() as ctx:
                ctx.prec = 160
                match = DIAMETER_ANNOTATION.fullmatch(self.annotation_raw)
                if (match is None or len(match[1]) > 64 or len(match[2]) > 64
                        or n != Decimal(match[1]) or hi != Decimal(match[2])
                        or lo != -Decimal(match[2])
                        or self.unit == 'unknown' or n <= 0 or lo > hi or n + lo <= 0):
                    raise ValueError('CAD_DIMENSION_NOT_IN_ANNOTATION')
        if len(set(self.target_candidates)) != len(self.target_candidates):
            raise ValueError('CAD_DUPLICATE_TARGET')
        if self.association_status == 'confirmed':
            if (len(self.target_candidates) != 1 or self.selected_target != self.target_candidates[0]
                    or self.association_evidence is None):
                raise ValueError('CAD_ASSOCIATION_EVIDENCE_REQUIRED')
        else:
            if self.selected_target is not None or self.association_evidence is not None:
                raise ValueError('CAD_UNCERTAIN_ASSOCIATION_SELECTED')
            if (self.association_status == 'ambiguous') != (len(self.target_candidates) > 1):
                raise ValueError('CAD_ASSOCIATION_STATE_MISMATCH')
        return self


class NativeDimensionFeature(ContractModel):
    """A native dimension's values, with the source of each value made explicit."""
    feature_id: Identifier
    feature_type: Literal['native_dimension']
    locator: Locator
    dimension_type: DimensionType
    unit: Unit
    # The drafter's explicit text, verbatim; None when the source did not override it.
    declared_text: Text | None
    # `declared_text` parsed as a plain decimal; None when the text is not a bare number.
    declared_value: DecimalText | None
    # What the dimension geometry measures, in drawing units. Never a declared size.
    geometry_measurement: DecimalText | None
    value_authority: Literal['declared_text', 'dimension_measurement', 'unavailable']

    @model_validator(mode='after')
    def authority_matches_values(self):
        if self.declared_text is None:
            if self.declared_value is not None:
                raise ValueError('CAD_DECLARED_VALUE_WITHOUT_TEXT')
            expected = 'dimension_measurement' if self.geometry_measurement is not None else 'unavailable'
        else:
            if self.declared_value is not None and not PLAIN_NUMBER.fullmatch(self.declared_value):
                raise ValueError('CAD_DECLARED_VALUE_NOT_PLAIN')
            expected = 'declared_text'
        if self.value_authority != expected:
            raise ValueError('CAD_VALUE_AUTHORITY_MISMATCH')
        return self


Feature = Annotated[Union[DiameterFeature, NativeDimensionFeature],
                    Field(discriminator='feature_type')]


class DrawingSnapshot(ContractModel):
    contract_version: Literal['cad-contract-v2']
    source: Source
    identity: BusinessIdentity
    views: Annotated[list[View], Field(min_length=1, max_length=100)]
    entities: Annotated[list[NativeEntity], Field(min_length=1, max_length=MAX_ENTITIES)]
    features: Annotated[list[Feature], Field(max_length=MAX_FEATURES)]

    @model_validator(mode='after')
    def linked_evidence(self):
        views = {v.view_id: v for v in self.views}
        if len(views) != len(self.views):
            raise ValueError('CAD_DUPLICATE_VIEW')
        located = {}
        for entity in self.entities:
            if entity.key() in located:
                raise ValueError('CAD_DUPLICATE_HANDLE_OR_BLOCK_PATH')
            located[entity.key()] = entity
        if len({f.feature_id for f in self.features}) != len(self.features):
            raise ValueError('CAD_DUPLICATE_FEATURE')
        for entity in self.entities:
            if entity.view_id not in views or not views[entity.view_id].bbox.contains(entity.bounds()):
                raise ValueError('CAD_ENTITY_OUTSIDE_VIEW')

        def located_entity(locator):
            entity = located.get(locator.key())
            if (entity is None or locator.source_sha256 != self.source.source_sha256
                    or locator.layer != entity.layer or locator.view_id != entity.view_id):
                raise ValueError('CAD_LOCATOR_STALE_OR_INVALID')
            return entity

        for field in (self.identity.drawing_id, self.identity.drawing_revision, self.identity.part_id):
            if field is None:
                continue
            annotation = located_entity(field.locator)
            if not isinstance(annotation, NativeText) or annotation.text != field.value:
                raise ValueError('CAD_IDENTITY_NOT_IN_SOURCE')
        for feature in self.features:
            entity = located_entity(feature.locator)
            if isinstance(feature, DiameterFeature):
                if not isinstance(entity, NativeText) or entity.text != feature.annotation_raw:
                    raise ValueError('CAD_ANNOTATION_NOT_IN_SOURCE')
                for handle in feature.target_candidates:
                    # Explicit references name a Model-space circle; a handle inside a
                    # block names every copy of it, so it is not a usable target here.
                    # A positional source has no handles, so no candidate can exist.
                    target = located.get((handle, None, ()))
                    if not isinstance(target, Circle) or target.view_id != entity.view_id:
                        raise ValueError('CAD_TARGET_MISSING_WRONG_TYPE_OR_VIEW')
                evidence = feature.association_evidence
                if evidence and (evidence.source_sha256 != self.source.source_sha256
                                 or evidence.annotation_handle != entity.handle
                                 or evidence.target_handle != feature.selected_target):
                    raise ValueError('CAD_ASSOCIATION_EVIDENCE_STALE')
            else:
                if not isinstance(entity, Dimension):
                    raise ValueError('CAD_NATIVE_DIMENSION_LOCATOR_NOT_A_DIMENSION')
                if (entity.dimension_type != feature.dimension_type
                        or entity.declared_text != feature.declared_text):
                    raise ValueError('CAD_NATIVE_DIMENSION_MISMATCH')
                measurement = None if entity.geometry_measurement is None else _decimal_text(
                    entity.geometry_measurement)
                if measurement != feature.geometry_measurement:
                    raise ValueError('CAD_NATIVE_DIMENSION_MEASUREMENT_MISMATCH')
        return self


def _decimal_text(value):
    with localcontext() as ctx:
        ctx.prec = 160
        text = format(Decimal(repr(float(value))), 'f')
    return None if len(text) > 64 else text


def padded_box(bounds):
    """Union of `(xmin, ymin, xmax, ymax)` tuples, padded by one drawing unit (as v1 does).

    A v2 entity's bounds may be a degenerate point (an INSERT, a single-vertex-free
    dimension); the padding keeps the contract's strict `xmin < xmax` box valid without
    inventing an extent for the entity itself.
    """
    boxes = [tuple(float(value) for value in box) for box in bounds]
    if not boxes:
        raise ValueError('CAD_VIEW_BOUNDS_UNAVAILABLE')
    return {'xmin': min(b[0] for b in boxes) - 1.0, 'ymin': min(b[1] for b in boxes) - 1.0,
            'xmax': max(b[2] for b in boxes) + 1.0, 'ymax': max(b[3] for b in boxes) + 1.0}


def view_bbox(entities):
    """The padded union of every validated entity's `bounds()`."""
    return padded_box([entity.bounds() for entity in entities])


def declared_sizes(payload: dict) -> dict:
    """Values a drafter declared, and why every other value is not usable.

    Keep the v1 rule that a **geometric** measurement never masquerades as a
    manufacturing size: a labelled diameter is usable only when its text was declared
    and its explicit target circle agrees, and a native dimension is usable only when
    the drafter overrode its text (`value_authority == 'declared_text'`). A dimension
    whose display value is generated from its own geometry is reported as excluded with
    `CAD_VALUE_IS_GEOMETRY_MEASUREMENT` instead of being promoted.
    """
    drawing = DrawingSnapshot.model_validate(payload)
    located = {entity.key(): entity for entity in drawing.entities}
    usable, excluded = [], []
    for feature in drawing.features:
        reason = None
        if isinstance(feature, DiameterFeature):
            if feature.association_status != 'confirmed':
                reason = 'CAD_ASSOCIATION_UNCERTAIN'
            elif feature.nominal_value is None or drawing.source.unit == 'unknown':
                reason = 'CAD_VALUE_OR_UNIT_UNKNOWN'
            else:
                circle = located[(feature.selected_target, None, ())]
                factor = Decimal('25.4') if drawing.source.unit == 'in' else Decimal('1')
                with localcontext() as ctx:
                    ctx.prec = 160
                    geometry = Decimal(str(circle.radius)) * 2 * factor
                    if geometry != Decimal(feature.nominal_value) * factor:
                        reason = 'CAD_GEOMETRY_ANNOTATION_CONFLICT'
            if reason is None:
                n = Decimal(feature.nominal_value)
                usable.append({'feature_id': feature.feature_id, 'feature_type': 'diameter',
                               'value': feature.nominal_value, 'lower': str(n + Decimal(feature.tolerance_lower)),
                               'upper': str(n + Decimal(feature.tolerance_upper)), 'unit': feature.unit,
                               'value_authority': 'declared_text',
                               'annotation_handle': feature.locator.handle,
                               'target_handle': feature.selected_target})
        else:
            if feature.value_authority == 'dimension_measurement':
                reason = 'CAD_VALUE_IS_GEOMETRY_MEASUREMENT'
            elif feature.value_authority == 'unavailable':
                reason = 'CAD_VALUE_OR_UNIT_UNKNOWN'
            elif feature.declared_value is None:
                reason = 'CAD_DECLARED_TEXT_NOT_A_NUMBER'
            elif drawing.source.unit == 'unknown':
                reason = 'CAD_VALUE_OR_UNIT_UNKNOWN'
            else:
                usable.append({'feature_id': feature.feature_id, 'feature_type': 'native_dimension',
                               'value': feature.declared_value, 'lower': None, 'upper': None,
                               'unit': feature.unit, 'value_authority': 'declared_text',
                               'annotation_handle': feature.locator.handle, 'target_handle': None})
        if reason is not None:
            excluded.append({'feature_id': feature.feature_id, 'reason': reason,
                             'feature_type': feature.feature_type})
    return {'status': 'ready' if usable and not excluded else 'needs_review',
            'features': usable, 'excluded': excluded}
