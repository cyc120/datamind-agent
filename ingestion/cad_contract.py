"""CAD adapter-output contract; validates snapshots, does not read DXF or DWG.

Source identity, business revision, parse version and native entity handles are
separate. A structurally valid snapshot is not a manufacturing approval.
"""
from decimal import Decimal, localcontext
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = 'cad-contract-v1'
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$', max_length=128)]
Handle = Annotated[str, Field(pattern=r'^[1-9A-F][0-9A-F]*$', max_length=32)]
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, Field(min_length=1, max_length=4096)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
DecimalText = Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$', max_length=64)]
Unit = Literal['mm', 'in', 'unknown']


class ContractModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Point(ContractModel):
    x: Number
    y: Number


class Box(ContractModel):
    xmin: Number
    ymin: Number
    xmax: Number
    ymax: Number

    @model_validator(mode='after')
    def ordered(self):
        if self.xmin >= self.xmax or self.ymin >= self.ymax:
            raise ValueError('CAD_BBOX_INVALID')
        return self

    def contains(self, other):
        return (self.xmin <= other[0] <= other[2] <= self.xmax
                and self.ymin <= other[1] <= other[3] <= self.ymax)


class Source(ContractModel):
    document_id: Identifier
    document_version: Annotated[int, Field(gt=0)]
    source_sha256: Sha256
    format: Literal['dxf']
    parser_version: Identifier
    provenance: Literal['synthetic_demo', 'authorized_external']
    # Raw DXF unit setting is retained, not inferred from geometry or a filename.
    insunits: Annotated[int, Field(strict=True)]
    unit: Unit
    coordinate_system: Literal['WCS_XY']
    layout: Literal['Model']

    @model_validator(mode='after')
    def units(self):
        if self.unit != {0: 'unknown', 1: 'in', 4: 'mm'}.get(self.insunits):
            raise ValueError('CAD_UNIT_SETTING_MISMATCH')
        return self


class View(ContractModel):
    view_id: Identifier
    bbox: Box


class Entity(ContractModel):
    handle: Handle
    layer: Text
    view_id: Identifier


class Line(Entity):
    entity_type: Literal['LINE']
    start: Point
    end: Point

    @model_validator(mode='after')
    def nonzero(self):
        if self.start == self.end:
            raise ValueError('CAD_ZERO_LENGTH_LINE')
        return self

    def bounds(self):
        return (min(self.start.x, self.end.x), min(self.start.y, self.end.y),
                max(self.start.x, self.end.x), max(self.start.y, self.end.y))


class Circle(Entity):
    entity_type: Literal['CIRCLE']
    center: Point
    radius: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]

    def bounds(self):
        return (self.center.x-self.radius, self.center.y-self.radius,
                self.center.x+self.radius, self.center.y+self.radius)


class NativeText(Entity):
    entity_type: Literal['TEXT']
    text: Text
    insertion: Point
    bbox: Box

    @model_validator(mode='after')
    def insertion_in_box(self):
        if not self.bbox.contains((self.insertion.x, self.insertion.y,
                                   self.insertion.x, self.insertion.y)):
            raise ValueError('CAD_TEXT_INSERTION_OUTSIDE_BBOX')
        return self

    def bounds(self):
        b = self.bbox
        return b.xmin, b.ymin, b.xmax, b.ymax


NativeEntity = Annotated[Union[Line, Circle, NativeText], Field(discriminator='entity_type')]


class Locator(ContractModel):
    locator_kind: Literal['cad_entity']
    source_sha256: Sha256
    handle: Handle
    layer: Text
    view_id: Identifier
    coordinate_system: Literal['WCS_XY']
    layout: Literal['Model']


class TitleField(ContractModel):
    value: Identifier
    locator: Locator


class BusinessIdentity(ContractModel):
    drawing_id: TitleField
    drawing_revision: TitleField
    part_id: TitleField
    # Drawing revisions are not process revisions; no implicit mapping to V2.


class AssociationEvidence(ContractModel):
    method: Literal['native_reference', 'human_label']
    reference_id: Identifier
    source_sha256: Sha256
    annotation_handle: Handle
    target_handle: Handle
    rationale: Text


class DrawingFeature(ContractModel):
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
            n, lo, hi = map(Decimal, values)
            with localcontext() as ctx:
                ctx.prec = 160
                valid_lower = n+lo > 0
                annotation = re.fullmatch(r'%%c([0-9]+(?:\.[0-9]+)?) \+/-([0-9]+(?:\.[0-9]+)?)',
                                          self.annotation_raw)
                if (annotation is None or len(annotation[1]) > 64 or len(annotation[2]) > 64
                        or n != Decimal(annotation[1]) or hi != Decimal(annotation[2])
                        or lo != -Decimal(annotation[2])):
                    raise ValueError('CAD_DIMENSION_NOT_IN_ANNOTATION')
            if self.unit == 'unknown' or n <= 0 or lo > hi or not valid_lower:
                raise ValueError('CAD_DIMENSION_RANGE_OR_UNIT_INVALID')
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


class DrawingSnapshot(ContractModel):
    contract_version: Literal['cad-contract-v1']
    source: Source
    identity: BusinessIdentity
    views: Annotated[list[View], Field(min_length=1, max_length=100)]
    entities: Annotated[list[NativeEntity], Field(min_length=1, max_length=10000)]
    features: Annotated[list[DrawingFeature], Field(max_length=10000)]

    @model_validator(mode='after')
    def linked_evidence(self):
        views = {v.view_id: v for v in self.views}
        entities = {e.handle: e for e in self.entities}
        if len(views) != len(self.views) or len(entities) != len(self.entities):
            raise ValueError('CAD_DUPLICATE_VIEW_OR_HANDLE')
        if len({f.feature_id for f in self.features}) != len(self.features):
            raise ValueError('CAD_DUPLICATE_FEATURE')
        for entity in self.entities:
            if entity.view_id not in views or not views[entity.view_id].bbox.contains(entity.bounds()):
                raise ValueError('CAD_ENTITY_OUTSIDE_VIEW')

        def located_text(locator):
            entity = entities.get(locator.handle)
            if (not isinstance(entity, NativeText) or locator.source_sha256 != self.source.source_sha256
                    or locator.layer != entity.layer or locator.view_id != entity.view_id):
                raise ValueError('CAD_LOCATOR_STALE_OR_INVALID')
            return entity

        for name, field in self.identity:
            if located_text(field.locator).text != field.value:
                raise ValueError('CAD_IDENTITY_NOT_IN_SOURCE')
        for feature in self.features:
            annotation = located_text(feature.locator)
            if annotation.text != feature.annotation_raw:
                raise ValueError('CAD_ANNOTATION_NOT_IN_SOURCE')
            for handle in feature.target_candidates:
                target = entities.get(handle)
                if not isinstance(target, Circle) or target.view_id != annotation.view_id:
                    raise ValueError('CAD_TARGET_MISSING_WRONG_TYPE_OR_VIEW')
            evidence = feature.association_evidence
            if evidence and (evidence.source_sha256 != self.source.source_sha256
                             or evidence.annotation_handle != annotation.handle
                             or evidence.target_handle != feature.selected_target):
                raise ValueError('CAD_ASSOCIATION_EVIDENCE_STALE')
        return self


class BindingContext(ContractModel):
    """Caller-supplied drawing registration, not inferred from a process revision."""
    part_id: Identifier
    drawing_id: Identifier
    drawing_revision: Identifier
    source_sha256: Sha256


def eligible_features(payload: dict, context: dict) -> dict:
    # Preserve all declared decimal digits, independent of the caller's context.
    with localcontext() as ctx:
        ctx.prec = 160
        return _eligible_features(payload, context)


def _eligible_features(payload: dict, context: dict) -> dict:
    """Revalidate and gate use; geometry/annotation conflicts stay unresolved.

    No production measurements are read, and no product pass/fail is emitted.
    The bounds below are annotation offsets, not geometry-based tolerances.
    """
    drawing = DrawingSnapshot.model_validate(payload)
    binding = BindingContext.model_validate(context)
    identity = drawing.identity
    if (binding.part_id != identity.part_id.value or binding.drawing_id != identity.drawing_id.value
            or binding.drawing_revision != identity.drawing_revision.value
            or binding.source_sha256 != drawing.source.source_sha256):
        return {'status': 'needs_review', 'reason': 'CAD_BUSINESS_BINDING_MISMATCH', 'features': []}
    entities = {e.handle: e for e in drawing.entities}
    usable, excluded = [], []
    for f in drawing.features:
        reason = None
        if f.association_status != 'confirmed':
            reason = 'CAD_ASSOCIATION_UNCERTAIN'
        elif f.nominal_value is None or drawing.source.unit == 'unknown':
            reason = 'CAD_VALUE_OR_UNIT_UNKNOWN'
        else:
            circle = entities[f.selected_target]
            factor = Decimal('25.4') if drawing.source.unit == 'in' else Decimal('1')
            annotation_factor = Decimal('25.4') if f.unit == 'in' else Decimal('1')
            geometry_mm = Decimal(str(circle.radius)) * 2 * factor
            annotation_mm = Decimal(f.nominal_value) * annotation_factor
            if geometry_mm != annotation_mm:
                reason = 'CAD_GEOMETRY_ANNOTATION_CONFLICT'
        if reason:
            excluded.append({'feature_id': f.feature_id, 'reason': reason})
        else:
            n = Decimal(f.nominal_value)
            usable.append({'feature_id': f.feature_id, 'nominal_value': f.nominal_value,
                           'lower': str(n+Decimal(f.tolerance_lower)),
                           'upper': str(n+Decimal(f.tolerance_upper)), 'unit': f.unit,
                           'annotation_handle': f.locator.handle, 'target_handle': f.selected_target})
    return {'status': 'ready' if usable and not excluded else 'needs_review',
            'features': usable, 'excluded': excluded}
