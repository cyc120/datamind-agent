"""Pure trusted-input contract for explicit diameter binding and independent use approval.

No authentication, database, source acquisition or manufacturing authorization is
performed here. Production adapters must supply verified current heads and actors.
"""
import copy
from datetime import datetime
from decimal import Decimal,localcontext
import hashlib
import json
from typing import Annotated,Literal
from pydantic import Field,model_validator
from .cad_contract import (ContractModel,Identifier,Sha256,Handle,DecimalText,Unit,Text,
                           DrawingSnapshot,eligible_features)

CONTRACT_VERSION='cad-dimension-binding-v1'
Role=Literal['viewer','reviewer','approver','admin']
Roles=Annotated[list[Role],Field(min_length=1,max_length=4)]
Timestamp=Annotated[str,Field(min_length=20,max_length=40)]


def digest(payload):
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def instant(value):
    dt=datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:raise ValueError('DIMENSION_TIMEZONE_REQUIRED')
    return dt


class MeasurementSource(ContractModel):
    source_id: Identifier
    record_version: Annotated[int,Field(gt=0)]
    source_sha256: Sha256
    provenance: Literal['synthetic_demo','authorized_external']
    locator: Annotated[str,Field(pattern=r'^/measurements/(0|[1-9][0-9]*)$',max_length=64)]


class DimensionMeasurement(ContractModel):
    measurement_id: Identifier
    source: MeasurementSource
    part_id: Identifier
    batch_id: Identifier
    operation_no: Identifier
    process_revision: Identifier
    metric: Literal['diameter']
    feature_role: Identifier
    method: Identifier
    measured_at: Timestamp
    value: DecimalText | None
    unit: Unit

    @model_validator(mode='after')
    def valid_value_and_time(self):
        instant(self.measured_at)
        if self.value is not None and Decimal(self.value)<=0:raise ValueError('DIMENSION_MEASUREMENT_NOT_POSITIVE')
        return self


class AssociationReview(ContractModel):
    review_id: Identifier
    document_id: Identifier
    document_version: Annotated[int,Field(gt=0)]
    source_sha256: Sha256
    parser_version: Identifier
    bundle_sha256: Sha256
    feature_id: Identifier
    feature_sha256: Sha256
    decision: Literal['confirmed','rejected','needs_review']
    selected_target: Handle | None
    reviewer_user_id: Identifier
    reviewer_roles: Roles
    reason: Text


class DimensionBinding(ContractModel):
    binding_id: Identifier
    binding_version: Annotated[int,Field(gt=0)]
    document_id: Identifier
    document_version: Annotated[int,Field(gt=0)]
    source_sha256: Sha256
    parser_version: Identifier
    bundle_sha256: Sha256
    feature_id: Identifier
    feature_sha256: Sha256
    target_handle: Handle
    drawing_id: Identifier
    drawing_revision: Identifier
    part_id: Identifier
    measurement_id: Identifier
    measurement_sha256: Sha256
    measurement_source_sha256: Sha256
    measurement_record_version: Annotated[int,Field(gt=0)]
    batch_id: Identifier
    operation_no: Identifier
    process_revision: Identifier
    metric: Literal['diameter']
    feature_role: Identifier
    method: Identifier
    review_id: Identifier
    review_sha256: Sha256
    valid_from: Timestamp
    valid_to: Timestamp
    registrar_user_id: Identifier
    registrar_roles: Roles
    rationale: Text

    @model_validator(mode='after')
    def valid_interval(self):
        if instant(self.valid_from)>=instant(self.valid_to):raise ValueError('DIMENSION_VALIDITY_INTERVAL_INVALID')
        return self


class ApprovalEvent(ContractModel):
    approval_id: Identifier
    binding_id: Identifier
    binding_version: Annotated[int,Field(gt=0)]
    binding_sha256: Sha256
    review_id: Identifier
    review_sha256: Sha256
    measurement_sha256: Sha256
    previous_approval_id: Identifier | None
    decision: Literal['approved','rejected','revoked']
    approver_user_id: Identifier
    approver_roles: Roles
    reason: Text
    created_at: Timestamp

    @model_validator(mode='after')
    def valid_time(self):
        instant(self.created_at)
        return self


class EvaluationContext(ContractModel):
    # These are trusted current registration heads, not inferred from supplied files.
    current_document_id: Identifier
    current_document_version: Annotated[int,Field(gt=0)]
    current_source_sha256: Sha256
    current_measurement_id: Identifier
    current_measurement_record_version: Annotated[int,Field(gt=0)]
    current_measurement_sha256: Sha256
    current_review_id: Identifier
    current_review_sha256: Sha256
    current_binding_id: Identifier
    current_binding_version: Annotated[int,Field(gt=0)]
    evaluated_at: Timestamp

    @model_validator(mode='after')
    def valid_time(self):
        instant(self.evaluated_at)
        return self


class ReviewRecord(ContractModel):
    payload: AssociationReview
    sha256: Sha256


class BindingRecord(ContractModel):
    payload: DimensionBinding
    sha256: Sha256


class ApprovalRecord(ContractModel):
    payload: ApprovalEvent
    sha256: Sha256


class DimensionEvaluation(ContractModel):
    contract_version: Literal['cad-dimension-binding-v1']
    measurement: DimensionMeasurement
    review: ReviewRecord
    binding: BindingRecord
    approvals: Annotated[list[ApprovalRecord],Field(max_length=1000)]
    context: EvaluationContext


def evaluate(bundle,payload):
    drawing=DrawingSnapshot.model_validate(bundle['snapshot'])
    request=DimensionEvaluation.model_validate(payload)
    m=request.measurement;r=request.review.payload;b=request.binding.payload;c=request.context
    identity={k:v.value for k,v in drawing.identity};source=drawing.source
    evidence={'drawing':{'document_id':source.document_id,'document_version':source.document_version,
               'source_sha256':source.source_sha256,**identity},'measurement':m.model_dump(),
              'binding_id':b.binding_id,'binding_version':b.binding_version,'binding_sha256':request.binding.sha256,
              'review_id':r.review_id,'evaluated_at':c.evaluated_at}
    def blocked(reason):
        return {'contract_version':CONTRACT_VERSION,'status':'blocked','reason':reason,'comparison':None,'evidence':evidence,
                'authentication_verified':False,'formal_drawing_approval':False,'product_quality_conclusion':False}
    for record in [request.review,request.binding,*request.approvals]:
        if record.sha256!=digest(record.payload.model_dump()):return blocked('DIMENSION_RECORD_HASH_MISMATCH')
    measurement_sha=digest(m.model_dump());bundle_sha=digest(bundle)
    if bundle['metadata']['parser_version']!=source.parser_version:return blocked('DIMENSION_PARSER_MISMATCH')
    if (c.current_document_id!=source.document_id or c.current_document_version!=source.document_version
            or c.current_source_sha256!=source.source_sha256 or c.current_measurement_id!=m.measurement_id
            or c.current_measurement_record_version!=m.source.record_version or c.current_measurement_sha256!=measurement_sha
            or c.current_review_id!=r.review_id or c.current_review_sha256!=request.review.sha256
            or c.current_binding_id!=b.binding_id or c.current_binding_version!=b.binding_version):
        return blocked('DIMENSION_CURRENT_HEAD_MISMATCH')
    if (b.document_id!=source.document_id or b.document_version!=source.document_version
            or b.source_sha256!=source.source_sha256 or b.parser_version!=source.parser_version or b.bundle_sha256!=bundle_sha):
        return blocked('DIMENSION_DRAWING_VERSION_MISMATCH')
    if any(getattr(b,k)!=v for k,v in identity.items()):return blocked('DIMENSION_DRAWING_IDENTITY_MISMATCH')
    features={f.feature_id:f.model_dump() for f in drawing.features};f=features.get(b.feature_id)
    if not f or digest(f)!=b.feature_sha256:return blocked('DIMENSION_FEATURE_MISMATCH')
    if (b.measurement_id!=m.measurement_id or b.measurement_sha256!=measurement_sha
            or b.measurement_source_sha256!=m.source.source_sha256 or b.measurement_record_version!=m.source.record_version):
        return blocked('DIMENSION_MEASUREMENT_VERSION_MISMATCH')
    if any(getattr(b,k)!=getattr(m,k) for k in ('part_id','batch_id','operation_no','process_revision','metric','feature_role','method')):
        return blocked('DIMENSION_PRODUCTION_SCOPE_MISMATCH')
    if m.part_id!=identity['part_id']:return blocked('DIMENSION_PART_MISMATCH')
    if (not instant(b.valid_from)<=instant(m.measured_at)<instant(b.valid_to)):
        return blocked('DIMENSION_OUTSIDE_VALIDITY')
    if instant(m.measured_at)>instant(c.evaluated_at):return blocked('DIMENSION_MEASUREMENT_IN_FUTURE')
    if 'admin' not in b.registrar_roles:return blocked('DIMENSION_REGISTRAR_PERMISSION_REQUIRED')
    if (b.review_id!=r.review_id or b.review_sha256!=request.review.sha256
            or any(getattr(r,k)!=getattr(b,k) for k in ('document_id','document_version','source_sha256','parser_version','bundle_sha256','feature_id','feature_sha256'))):
        return blocked('DIMENSION_REVIEW_VERSION_MISMATCH')
    if (r.decision!='confirmed' or r.selected_target!=b.target_handle or b.target_handle not in f['target_candidates']):
        return blocked('DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED')
    if not {'reviewer','admin'}.intersection(r.reviewer_roles):return blocked('DIMENSION_REVIEW_PERMISSION_REQUIRED')
    snapshot=copy.deepcopy(bundle['snapshot'])
    chosen=next(x for x in snapshot['features'] if x['feature_id']==b.feature_id)
    chosen.update(association_status='confirmed',selected_target=b.target_handle,target_candidates=[b.target_handle],
        association_evidence={'method':'human_label','reference_id':r.review_id,'source_sha256':source.source_sha256,
            'annotation_handle':f['locator']['handle'],'target_handle':b.target_handle,'rationale':r.reason})
    snapshot['features']=[chosen]
    gate=eligible_features(snapshot,{**identity,'source_sha256':source.source_sha256})
    if gate['status']!='ready':return blocked(gate['excluded'][0]['reason'])
    if m.value is None or m.unit=='unknown':return blocked('DIMENSION_MEASUREMENT_VALUE_OR_UNIT_UNKNOWN')
    previous=None;seen=set();previous_time=None;active=None
    for record in request.approvals:
        event=record.payload
        if (event.approval_id in seen or event.previous_approval_id!=(previous.approval_id if previous else None)
                or (previous_time is not None and instant(event.created_at)<previous_time)):
            return blocked('DIMENSION_APPROVAL_CHAIN_INVALID')
        if (event.binding_id!=b.binding_id or event.binding_version!=b.binding_version
                or event.binding_sha256!=request.binding.sha256 or event.review_id!=r.review_id
                or event.review_sha256!=request.review.sha256 or event.measurement_sha256!=measurement_sha):
            return blocked('DIMENSION_APPROVAL_VERSION_MISMATCH')
        if not {'approver','admin'}.intersection(event.approver_roles):return blocked('DIMENSION_APPROVAL_PERMISSION_REQUIRED')
        if event.approver_user_id in {b.registrar_user_id,r.reviewer_user_id}:return blocked('DIMENSION_INDEPENDENT_APPROVER_REQUIRED')
        if event.decision=='revoked' and (previous is None or previous.decision!='approved'):
            return blocked('DIMENSION_NO_ACTIVE_APPROVAL_TO_REVOKE')
        seen.add(event.approval_id);previous=event;previous_time=instant(event.created_at)
        if previous_time<=instant(c.evaluated_at):active=event
    if active is None:return blocked('DIMENSION_USE_APPROVAL_REQUIRED')
    evidence['approval_id']=active.approval_id;evidence['approval_decision']=active.decision
    if active.decision!='approved':return blocked('DIMENSION_USE_APPROVAL_'+active.decision.upper())
    limits=gate['features'][0]
    with localcontext() as ctx:
        ctx.prec=160
        annotation_factor=Decimal('25.4') if limits['unit']=='in' else Decimal('1')
        measurement_factor=Decimal('25.4') if m.unit=='in' else Decimal('1')
        lower=Decimal(limits['lower'])*annotation_factor;upper=Decimal(limits['upper'])*annotation_factor
        actual=Decimal(m.value)*measurement_factor
        comparison={'status':'within_annotation_range' if lower<=actual<=upper else 'outside_annotation_range',
                    'measurement_original':{'value':m.value,'unit':m.unit},
                    'annotation_original':{'raw':f['annotation_raw'],'lower':limits['lower'],'upper':limits['upper'],'unit':limits['unit']},
                    'normalized_mm':{'measurement':str(actual),'lower':str(lower),'upper':str(upper)},
                    'annotation_handle':f['locator']['handle'],'target_handle':b.target_handle}
    return {'contract_version':CONTRACT_VERSION,'status':'compared','reason':None,'comparison':comparison,'evidence':evidence,
            'authentication_verified':False,'formal_drawing_approval':False,'product_quality_conclusion':False,
            'semantics':'Trusted-input local contract simulation; comparison is not manufacturing authorization or product quality certification.'}
