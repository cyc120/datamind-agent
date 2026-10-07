"""Explicit synthetic declarations for contract tests, never real identities or ERP."""
from ingestion.cad_dimension_contract import CONTRACT_VERSION,digest
from ingestion.cad_dxf import parse_bytes
import test_cad_dxf as native


def wrap(payload):return {'payload':payload,'sha256':digest(payload)}


def make_fixture(value='20.04',unit='mm',drawing_unit=4,radius=10,reference=True):
    doc,annotation,circle=native.drawing(unit=drawing_unit,reference=reference);circle.dxf.radius=radius
    bundle=parse_bytes(native.source(doc),document_id='binding-demo',document_version=1,provenance='synthetic_demo')
    return from_bundle(bundle,value=value,unit=unit)


def from_bundle(bundle,value='20.04',unit='mm',measurement=None):
    s=bundle['snapshot'];source=s['source'];f=s['features'][0]
    m=measurement or {'measurement_id':'dimension-demo-1','source':{'source_id':'dimension-fixture-v1','record_version':1,
       'source_sha256':'a'*64,'provenance':'synthetic_demo','locator':'/measurements/0'},
       'part_id':s['identity']['part_id']['value'],'batch_id':'DIM-BATCH-1','operation_no':'30',
       'process_revision':'PROCESS-R5','metric':'diameter','feature_role':'outer_diameter','method':'declared_caliper',
       'measured_at':'2026-10-05T08:00:00+08:00','value':value,'unit':unit}
    r={'review_id':'synthetic-review-1','document_id':source['document_id'],'document_version':source['document_version'],
       'source_sha256':source['source_sha256'],'parser_version':source['parser_version'],'bundle_sha256':digest(bundle),
       'feature_id':f['feature_id'],'feature_sha256':digest(f),'decision':'confirmed','selected_target':f['target_candidates'][0],
       'reviewer_user_id':'synthetic-reviewer-1','reviewer_roles':['reviewer'],'reason':'Synthetic declared association only; not authenticated by this fixture.'}
    b={'binding_id':'synthetic-binding-1','binding_version':1,**{k:r[k] for k in ['document_id','document_version','source_sha256','parser_version','bundle_sha256','feature_id','feature_sha256']},
       'target_handle':r['selected_target'],**{k:v['value'] for k,v in s['identity'].items()},
       **{k:m[k] for k in ['part_id','measurement_id','batch_id','operation_no','process_revision','metric','feature_role','method']},
       'measurement_sha256':digest(m),'measurement_source_sha256':m['source']['source_sha256'],'measurement_record_version':m['source']['record_version'],
       'review_id':r['review_id'],'review_sha256':digest(r),'valid_from':'2026-10-05T00:00:00+08:00','valid_to':'2026-10-06T00:00:00+08:00',
       'registrar_user_id':'synthetic-registrar-1','registrar_roles':['admin'],'rationale':'Synthetic explicit mapping; drawing revision is not process revision.'}
    event={'approval_id':'synthetic-approval-1','binding_id':b['binding_id'],'binding_version':b['binding_version'],'binding_sha256':digest(b),
       'review_id':r['review_id'],'review_sha256':digest(r),'measurement_sha256':digest(m),'previous_approval_id':None,
       'decision':'approved','approver_user_id':'synthetic-approver-1','approver_roles':['approver'],
       'reason':'Synthetic independent use declaration only; no verified session or manufacturing authority.',
       'created_at':'2026-10-05T09:00:00+08:00'}
    context={'current_document_id':source['document_id'],'current_document_version':source['document_version'],'current_source_sha256':source['source_sha256'],
       'current_measurement_id':m['measurement_id'],'current_measurement_record_version':m['source']['record_version'],'current_measurement_sha256':digest(m),
       'current_review_id':r['review_id'],'current_review_sha256':digest(r),'current_binding_id':b['binding_id'],'current_binding_version':b['binding_version'],
       'evaluated_at':'2026-10-05T10:00:00+08:00'}
    return bundle,{'contract_version':CONTRACT_VERSION,'measurement':m,'review':wrap(r),'binding':wrap(b),'approvals':[wrap(event)],'context':context}


def rehash_binding(payload):
    payload['binding']['sha256']=digest(payload['binding']['payload'])
    for event in payload['approvals']:
        event['payload']['binding_sha256']=payload['binding']['sha256'];event['sha256']=digest(event['payload'])


def event_after(payload,decision='revoked'):
    old=payload['approvals'][-1]['payload'];new={**old,'approval_id':'synthetic-approval-2',
       'previous_approval_id':old['approval_id'],'decision':decision,'created_at':'2026-10-05T11:00:00+08:00'}
    payload['approvals'].append(wrap(new));payload['context']['evaluated_at']='2026-10-05T12:00:00+08:00'
