"""Authenticated local dimension registry and immutable comparison evidence.

The frozen evaluator remains a trusted-input engine. This adapter obtains actors,
current heads and source bytes itself. No manufacturing or ERP authority is implied.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from typing import Annotated,Literal
import uuid

import psycopg2
from psycopg2.extras import Json,RealDictCursor
from pydantic import Field,ValidationError
from .auth import lock_identity
from .cad_store import ROOT,checked,fail
from . import cad_reviews
from ingestion.cad_contract import ContractModel,Identifier,Sha256,Text
from ingestion.cad_dimension_contract import (CONTRACT_VERSION,DimensionMeasurement,AssociationReview,
    DimensionBinding,ApprovalEvent,digest,evaluate,instant)

MAX_BYTES=1048576
STAGING_ROOTS={'synthetic_demo':ROOT/'data/dimension_demo/development',
               'authorized_external':ROOT/'data/dimension_demo/authorized'}
RequestId=Annotated[str,Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')]
Version=Annotated[int,Field(ge=1)]
Timestamp=Annotated[str,Field(min_length=20,max_length=40)]


class MeasurementRegistration(ContractModel):
    request_id: RequestId
    measurement_id: Identifier
    source_path: Annotated[str,Field(min_length=1,max_length=500)]
    source_sha256: Sha256
    provenance: Literal['synthetic_demo','authorized_external']
    previous_version: Annotated[int,Field(ge=0)]
    previous_sha256: Sha256 | None


class BindingRegistration(ContractModel):
    request_id: RequestId
    binding_id: Identifier
    previous_version: Annotated[int,Field(ge=0)]
    previous_sha256: Sha256 | None
    document_id: Annotated[str,Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    document_version: Version
    source_sha256: Sha256
    bundle_sha256: Sha256
    feature_id: Identifier
    feature_sha256: Sha256
    target_handle: Annotated[str,Field(pattern=r'^[1-9A-F][0-9A-F]*$',max_length=32)]
    measurement_id: Identifier
    measurement_record_version: Version
    measurement_sha256: Sha256
    review_id: Identifier
    review_sha256: Sha256
    valid_from: Timestamp
    valid_to: Timestamp
    rationale: Text


class ApprovalRequest(ContractModel):
    request_id: RequestId
    binding_version: Version
    binding_sha256: Sha256
    previous_approval_id: Identifier | None
    decision: Literal['approved','rejected','revoked']
    reason: Text


class ComparisonRequest(ContractModel):
    request_id: RequestId
    binding_version: Version
    binding_sha256: Sha256


def now():return datetime.now(timezone.utc).isoformat()
def wrap(payload):return {'payload':payload,'sha256':digest(payload)}


def locks(cur,*keys):
    for key in sorted(set(keys)):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))


def clean(p,key):
    p=dict(p);p[key]=p[key].strip()
    if not p[key]:fail('DIMENSION_REASON_REQUIRED',422)
    return p


def staged(relative,provenance):
    if Path(relative).is_absolute():fail('DIMENSION_STAGED_SOURCE_INVALID',422)
    path=(ROOT/relative).resolve();folder=STAGING_ROOTS[provenance].resolve()
    if (not path.is_relative_to(ROOT.resolve()) or not path.is_relative_to(folder)
            or path.suffix.lower()!='.json' or not path.is_file()):fail('DIMENSION_STAGED_SOURCE_INVALID',422)
    with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
    if not data or len(data)>MAX_BYTES:fail('DIMENSION_SOURCE_SIZE_INVALID',422)
    return data


def sources():
    result=[]
    for provenance,folder in STAGING_ROOTS.items():
        if not folder.is_dir():continue
        for path in sorted(folder.glob('*.json'))[:200]:
            if not path.resolve().is_relative_to(ROOT.resolve()) or not path.resolve().is_relative_to(folder.resolve()):continue
            data=staged(str(path.relative_to(ROOT)),provenance)
            result.append({'source_path':str(path.relative_to(ROOT)),'name':path.name,'provenance':provenance,
                           'source_sha256':hashlib.sha256(data).hexdigest()})
    return result


def unique_object(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('duplicate JSON key')
        result[k]=v
    return result


def measurement_from_bytes(data,measurement_id,version,provenance):
    try:
        if len(data)>MAX_BYTES:raise ValueError('size')
        src=json.loads(data.decode('utf-8'),object_pairs_hook=unique_object)
        if (not isinstance(src,dict) or set(src)!={'source_id','record_version','provenance','measurements'}
                or type(src['record_version']) is not int or src['record_version']!=version
                or src['provenance']!=provenance or not isinstance(src['measurements'],list)
                or len(src['measurements'])!=1):raise ValueError('single measurement/version required')
        raw=src['measurements'][0]
        if not isinstance(raw,dict) or 'source' in raw or raw.get('measurement_id')!=measurement_id:raise ValueError('identity')
        return DimensionMeasurement.model_validate({**raw,'source':{'source_id':src['source_id'],
            'record_version':version,'provenance':provenance,'locator':'/measurements/0',
            'source_sha256':hashlib.sha256(data).hexdigest()}}).model_dump()
    except (ValueError,TypeError,KeyError,RecursionError):fail('DIMENSION_SOURCE_INVALID',422)


def audit(payload,request,actor):
    return {'payload':payload,'request':request,'actor':{'user_id':actor['user_id'],
            'label':actor['display_name'],'roles':actor['roles']},'registered_at':now()}


def stored(row,model=None):
    if not row:fail('DIMENSION_RECORD_NOT_FOUND',404)
    r=row['record']
    try:
        if digest(r)!=row['record_sha256'] or r['request']['request_id']!=row['request_id']:
            fail('DIMENSION_RECORD_HASH_MISMATCH')
        instant(r['registered_at'])
        if model:
            p=model.model_validate(r['payload']).model_dump()
            key='registrar' if model is DimensionBinding else 'approver'
            if p[key+'_user_id']!=r['actor']['user_id'] or p[key+'_roles']!=r['actor']['roles']:
                fail('DIMENSION_ACTOR_MISMATCH')
        return r
    except (ValueError,KeyError,TypeError):fail('DIMENSION_RECORD_INVALID')


def measured(row):
    r=stored(row);p=r['payload'];req=r['request']
    m=measurement_from_bytes(bytes(row['source_bytes']),row['measurement_id'],row['record_version'],req['provenance'])
    if (m!=p or p['measurement_id']!=req['measurement_id'] or p['source']['source_sha256']!=req['source_sha256']
            or req['previous_version']!=row['record_version']-1 or 'admin' not in r['actor']['roles']):
        fail('DIMENSION_MEASUREMENT_SNAPSHOT_MISMATCH')
    return r


def get_measurement(cur,mid,version=None):
    if version is None:
        cur.execute('''SELECT v.* FROM dimension_measurement_versions v JOIN dimension_measurements a
            ON a.measurement_id=v.measurement_id AND a.current_version=v.record_version WHERE a.measurement_id=%s''',(mid,))
    else:cur.execute('SELECT * FROM dimension_measurement_versions WHERE measurement_id=%s AND record_version=%s',(mid,version))
    return measured(cur.fetchone())


def get_drawing(cur,did,version=None):
    if version is None:
        cur.execute('''SELECT v.* FROM cad_assets a JOIN cad_versions v ON v.document_id=a.document_id
            AND v.document_version=a.current_version WHERE a.document_id=%s''',(did,))
    else:cur.execute('SELECT * FROM cad_versions WHERE document_id=%s AND document_version=%s',(did,version))
    return checked(cur.fetchone())


def projection(review):
    return AssociationReview.model_validate({k:review[k] for k in AssociationReview.model_fields}).model_dump()


def get_binding(cur,bid,version=None):
    if version is None:
        cur.execute('''SELECT v.* FROM dimension_binding_versions v JOIN dimension_bindings a
            ON a.binding_id=v.binding_id AND a.current_version=v.binding_version WHERE a.binding_id=%s''',(bid,))
    else:cur.execute('SELECT * FROM dimension_binding_versions WHERE binding_id=%s AND binding_version=%s',(bid,version))
    row=cur.fetchone();r=stored(row,DimensionBinding);b=r['payload'];req=r['request']
    if (any(b[k]!=row[k] for k in ('binding_id','binding_version','document_id','document_version',
                                'measurement_id','measurement_record_version','review_id'))
            or b['binding_id']!=req['binding_id'] or b['binding_version']!=req['previous_version']+1
            or 'admin' not in b['registrar_roles']):fail('DIMENSION_BINDING_SNAPSHOT_MISMATCH')
    m=get_measurement(cur,b['measurement_id'],b['measurement_record_version'])['payload']
    drawing=get_drawing(cur,b['document_id'],b['document_version'])
    reviews=cad_reviews.read_all(cur,drawing).get(b['feature_id'],[])
    matches=[x for x in reviews if x['review_id']==b['review_id']]
    if not matches:fail('DIMENSION_REVIEW_NOT_FOUND')
    review=projection(matches[0])
    # Evaluate with historical heads and no use events to verify binding evidence.
    p=inputs(cur,r,drawing,m,review,[],historical=True)
    result=evaluate(drawing['bundle'],p)
    if result['reason']!='DIMENSION_USE_APPROVAL_REQUIRED':fail('DIMENSION_BINDING_EVIDENCE_INVALID')
    return r,drawing,m,review


def get_events(cur,b,review):
    cur.execute('''SELECT * FROM dimension_approval_events WHERE binding_id=%s AND binding_version=%s
        ORDER BY sequence_no LIMIT 1001''',(b['binding_id'],b['binding_version']))
    rows=cur.fetchall()
    if len(rows)>1000:fail('DIMENSION_APPROVAL_HISTORY_LIMIT')
    result=[];prev=None
    for index,row in enumerate(rows):
        r=stored(row,ApprovalEvent);event=r['payload']
        if (row['sequence_no']!=index+1 or event['approval_id']!=row['approval_id']
                or event['binding_id']!=b['binding_id'] or event['binding_version']!=b['binding_version']
                or event['binding_sha256']!=digest(b) or event['review_id']!=review['review_id']
                or event['review_sha256']!=digest(review) or event['measurement_sha256']!=b['measurement_sha256']
                or event['previous_approval_id']!=(prev['approval_id'] if prev else None)
                or (prev and instant(event['created_at'])<instant(prev['created_at']))
                or not {'approver','admin'}.intersection(event['approver_roles'])
                or event['approver_user_id'] in {b['registrar_user_id'],review['reviewer_user_id']}
                or (event['decision']=='revoked' and (not prev or prev['decision']!='approved'))):
            fail('DIMENSION_APPROVAL_CHAIN_INVALID')
        result.append(wrap(event));prev=event
    return result


def inputs(cur,r,drawing,m,review,events,historical=False):
    b=r['payload']
    if historical:
        current=drawing;cm=m;cr=review;cv=b['binding_version']
    else:
        current=get_drawing(cur,b['document_id']);cm=get_measurement(cur,b['measurement_id'])['payload']
        reviews=cad_reviews.read_all(cur,drawing).get(b['feature_id'],[])
        cr=projection(reviews[-1]) if reviews else review
        cur.execute('SELECT current_version FROM dimension_bindings WHERE binding_id=%s',(b['binding_id'],));cv=cur.fetchone()['current_version']
    return {'contract_version':CONTRACT_VERSION,'measurement':m,'binding':wrap(b),'review':wrap(review),
        'approvals':events,'context':{'current_document_id':current['document_id'],
        'current_document_version':current['document_version'],'current_source_sha256':current['source_sha256'],
        'current_measurement_id':cm['measurement_id'],'current_measurement_record_version':cm['source']['record_version'],
        'current_measurement_sha256':digest(cm),'current_review_id':cr['review_id'],'current_review_sha256':digest(cr),
        'current_binding_id':b['binding_id'],'current_binding_version':cv,'evaluated_at':now()}}


def public(r):
    return {'payload':r['payload'],'sha256':digest(r['payload']),'actor':r['actor'],'registered_at':r['registered_at']}


def prior(cur,table,request,actor):
    # Table names are constants supplied only by this module.
    cur.execute('SELECT * FROM '+table+' WHERE request_id=%s',(request['request_id'],));row=cur.fetchone()
    if row:
        r=stored(row)
        if r['request']!=request or r['actor']['user_id']!=actor['user_id']:fail('DIMENSION_REQUEST_ID_REUSED')
        return row
    return None


def save(cur,table,columns,values,r):
    fields=(*columns,'record','record_sha256','request_id')
    cur.execute('INSERT INTO '+table+' ('+','.join(fields)+') VALUES ('+','.join(['%s']*len(fields))+')',
                [*values,Json(r),digest(r),r['request']['request_id']])


def register_measurement(p,dsn,identity):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'admin')
        locks(cur,'dimension-measurement:'+p['measurement_id'],'dimension-request:'+p['request_id'])
        old=prior(cur,'dimension_measurement_versions',p,actor)
        if old:return public(measured(old))
        cur.execute('SELECT current_version FROM dimension_measurements WHERE measurement_id=%s',(p['measurement_id'],));head=cur.fetchone()
        previous=get_measurement(cur,p['measurement_id'])['payload'] if head else None
        if (p['previous_version']!=(head['current_version'] if head else 0)
                or p['previous_sha256']!=(digest(previous) if previous else None)):fail('DIMENSION_MEASUREMENT_STATE_CHANGED')
        data=staged(p['source_path'],p['provenance'])
        if hashlib.sha256(data).hexdigest()!=p['source_sha256']:fail('DIMENSION_SOURCE_CHANGED')
        version=p['previous_version']+1;m=measurement_from_bytes(data,p['measurement_id'],version,p['provenance'])
        if previous and (m['source']['source_id']!=previous['source']['source_id'] or m['part_id']!=previous['part_id']):
            fail('DIMENSION_MEASUREMENT_IDENTITY_CHANGED')
        r=audit(m,p,actor)
        if not head:cur.execute('INSERT INTO dimension_measurements VALUES (%s,%s)',(p['measurement_id'],version))
        save(cur,'dimension_measurement_versions',('measurement_id','record_version','source_bytes'),(p['measurement_id'],version,data),r)
        if head:cur.execute('UPDATE dimension_measurements SET current_version=%s WHERE measurement_id=%s',(version,p['measurement_id']))
        return public(r)


def register_binding(p,dsn,identity):
    p=clean(p,'rationale')
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'admin')
        locks(cur,'cad-document:'+p['document_id'],'dimension-measurement:'+p['measurement_id'],
              'dimension-binding:'+p['binding_id'],'dimension-request:'+p['request_id'])
        old=prior(cur,'dimension_binding_versions',p,actor)
        if old:return public(get_binding(cur,old['binding_id'],old['binding_version'])[0])
        cur.execute('SELECT current_version FROM dimension_bindings WHERE binding_id=%s',(p['binding_id'],));head=cur.fetchone()
        previous=get_binding(cur,p['binding_id'])[0]['payload'] if head else None
        if (p['previous_version']!=(head['current_version'] if head else 0)
                or p['previous_sha256']!=(digest(previous) if previous else None)):fail('DIMENSION_BINDING_STATE_CHANGED')
        drawing=get_drawing(cur,p['document_id']);m=get_measurement(cur,p['measurement_id'])['payload']
        if (any(p[k]!=drawing[k] for k in ('document_version','source_sha256','bundle_sha256'))
                or p['measurement_record_version']!=m['source']['record_version'] or p['measurement_sha256']!=digest(m)):
            fail('DIMENSION_INPUT_VERSION_CHANGED')
        f=cad_reviews.feature(drawing,p['feature_id']);reviews=cad_reviews.read_all(cur,drawing).get(p['feature_id'],[])
        if not reviews:fail('DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED')
        review=projection(reviews[-1])
        if (p['review_id']!=review['review_id'] or p['review_sha256']!=digest(review)
                or p['feature_sha256']!=digest(f)):fail('DIMENSION_REVIEW_STATE_CHANGED')
        if previous and (p['document_id']!=previous['document_id'] or p['measurement_id']!=previous['measurement_id']):
            fail('DIMENSION_BINDING_IDENTITY_CHANGED')
        b={k:review[k] for k in ('document_id','document_version','source_sha256','parser_version','bundle_sha256','feature_id','feature_sha256')}
        b.update({k:v['value'] for k,v in drawing['bundle']['snapshot']['identity'].items()})
        # Production scope comes from the explicitly selected measurement, never drawing revision inference.
        b.update({k:m[k] for k in ('measurement_id','batch_id','operation_no','process_revision','metric','feature_role','method')})
        b.update(binding_id=p['binding_id'],binding_version=p['previous_version']+1,target_handle=p['target_handle'],
            measurement_sha256=digest(m),measurement_source_sha256=m['source']['source_sha256'],
            measurement_record_version=m['source']['record_version'],review_id=review['review_id'],review_sha256=digest(review),
            valid_from=p['valid_from'],valid_to=p['valid_to'],rationale=p['rationale'],
            registrar_user_id=actor['user_id'],registrar_roles=actor['roles'])
        try:b=DimensionBinding.model_validate(b).model_dump()
        except ValidationError:fail('DIMENSION_BINDING_INVALID',422)
        r=audit(b,p,actor);ev=inputs(cur,r,drawing,m,review,[],historical=True);result=evaluate(drawing['bundle'],ev)
        if result['reason']!='DIMENSION_USE_APPROVAL_REQUIRED':fail(result['reason'] or 'DIMENSION_BINDING_INVALID')
        if not head:cur.execute('INSERT INTO dimension_bindings VALUES (%s,%s)',(b['binding_id'],b['binding_version']))
        columns=('binding_id','binding_version','document_id','document_version','measurement_id','measurement_record_version','review_id')
        save(cur,'dimension_binding_versions',columns,[b[k] for k in columns],r)
        if head:cur.execute('UPDATE dimension_bindings SET current_version=%s WHERE binding_id=%s',(b['binding_version'],b['binding_id']))
        return public(r)


def record_approval(bid,p,dsn,identity):
    p=clean(p,'reason');request={**p,'binding_id':bid}
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'approver')
        # Discover immutable scope before locks, then re-read under all shared mutation locks.
        r,drawing,m,review=get_binding(cur,bid,p['binding_version']);b=r['payload']
        locks(cur,'cad-document:'+b['document_id'],'dimension-measurement:'+b['measurement_id'],
              'dimension-binding:'+bid,'dimension-request:'+p['request_id'])
        old=prior(cur,'dimension_approval_events',request,actor)
        r,drawing,m,review=get_binding(cur,bid,p['binding_version']);b=r['payload'];events=get_events(cur,b,review)
        if old:return public(stored(old,ApprovalEvent))
        if p['binding_sha256']!=digest(b):fail('DIMENSION_BINDING_STATE_CHANGED')
        if actor['user_id'] in {b['registrar_user_id'],review['reviewer_user_id']}:fail('DIMENSION_INDEPENDENT_APPROVER_REQUIRED',403)
        last=events[-1]['payload'] if events else None
        if p['previous_approval_id']!=(last['approval_id'] if last else None):fail('DIMENSION_APPROVAL_STATE_CHANGED')
        if len(events)>=1000:fail('DIMENSION_APPROVAL_HISTORY_LIMIT')
        if p['decision']=='revoked':
            if not last or last['decision']!='approved':fail('DIMENSION_NO_ACTIVE_APPROVAL_TO_REVOKE')
        else:
            result=evaluate(drawing['bundle'],inputs(cur,r,drawing,m,review,events))
            if result['reason'] not in {None,'DIMENSION_USE_APPROVAL_REQUIRED','DIMENSION_USE_APPROVAL_REJECTED','DIMENSION_USE_APPROVAL_REVOKED'}:
                fail(result['reason'])
        event={k:b[k] for k in ('binding_id','binding_version','review_id','review_sha256','measurement_sha256')}
        event.update(approval_id='dimension-approval-'+uuid.uuid4().hex[:24],binding_sha256=digest(b),
            previous_approval_id=p['previous_approval_id'],decision=p['decision'],reason=p['reason'],
            approver_user_id=actor['user_id'],approver_roles=actor['roles'],created_at=now())
        r=audit(ApprovalEvent.model_validate(event).model_dump(),request,actor)
        save(cur,'dimension_approval_events',('approval_id','binding_id','binding_version','sequence_no'),
             (event['approval_id'],bid,b['binding_version'],len(events)+1),r)
        return public(r)


def read_measurement(mid,dsn,version=None):
    with psycopg2.connect(dsn) as conn:
        conn.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:return public(get_measurement(cur,mid,version))


def read_binding(bid,dsn,version=None):
    with psycopg2.connect(dsn) as conn:
        conn.set_session(isolation_level='REPEATABLE READ',readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            r,drawing,m,review=get_binding(cur,bid,version);events=get_events(cur,r['payload'],review)
            return {'binding':public(r),'approvals':events,'current_result':evaluate(drawing['bundle'],inputs(cur,r,drawing,m,review,events)),
                    'authentication':{'verified':True,'scope':'Local session and stored actor/role snapshots'}}


def listing(kind,dsn):
    table='dimension_measurements' if kind=='measurements' else 'dimension_bindings'
    key='measurement_id' if kind=='measurements' else 'binding_id'
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT '+key+',current_version FROM '+table+' ORDER BY '+key+' LIMIT 201');rows=cur.fetchall()
        return {kind:rows[:200],'truncated':len(rows)>200}


def comparison(bid,p,dsn,identity):
    request={**p,'binding_id':bid}
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'admin')
        r,drawing,m,review=get_binding(cur,bid,p['binding_version']);b=r['payload']
        locks(cur,'cad-document:'+b['document_id'],'dimension-measurement:'+b['measurement_id'],
              'dimension-binding:'+bid,'dimension-request:'+p['request_id'])
        old=prior(cur,'dimension_comparisons',request,actor)
        if old:return replay_record(old)
        r,drawing,m,review=get_binding(cur,bid,p['binding_version']);b=r['payload']
        if p['binding_sha256']!=digest(b):fail('DIMENSION_BINDING_STATE_CHANGED')
        events=get_events(cur,b,review);ev=inputs(cur,r,drawing,m,review,events);result=evaluate(drawing['bundle'],ev)
        payload={'comparison_id':'dimension-comparison-'+uuid.uuid4().hex[:24],'bundle':drawing['bundle'],
            'evaluation':ev,'result':result,'authentication':{'verified':True,
            'scope':'Local authenticated registration/review/use approval; no manufacturing authority'}}
        r=audit(payload,request,actor)
        save(cur,'dimension_comparisons',('comparison_id','binding_id','binding_version'),
             (payload['comparison_id'],bid,b['binding_version']),r)
        return {**public(r),'record_sha256':digest(r),'replay':False}


def replay_record(row):
    r=stored(row);p=r['payload']
    try:
        if (p['comparison_id']!=row['comparison_id'] or p['evaluation']['binding']['payload']['binding_id']!=row['binding_id']
                or p['evaluation']['binding']['payload']['binding_version']!=row['binding_version']
                or p['result']!=evaluate(p['bundle'],p['evaluation'])):fail('DIMENSION_COMPARISON_SNAPSHOT_MISMATCH')
    except (ValueError,KeyError,TypeError):fail('DIMENSION_COMPARISON_INVALID')
    return {**public(r),'record_sha256':row['record_sha256'],'replay':True}


def replay(cid,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT * FROM dimension_comparisons WHERE comparison_id=%s',(cid,));return replay_record(cur.fetchone())


def comparison_history(bid,dsn,offset=0,limit=20):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT binding_id FROM dimension_bindings WHERE binding_id=%s',(bid,))
        if not cur.fetchone():fail('DIMENSION_RECORD_NOT_FOUND',404)
        cur.execute('''SELECT * FROM dimension_comparisons WHERE binding_id=%s
            ORDER BY record->>'registered_at' DESC,comparison_id DESC LIMIT %s OFFSET %s''',(bid,limit+1,offset))
        rows=cur.fetchall();items=[]
        for row in rows[:limit]:
            item=replay_record(row);p=item['payload'];result=p['result']
            items.append({'comparison_id':p['comparison_id'],'binding_version':row['binding_version'],
                'status':result['status'],'reason':result['reason'],'comparison':result['comparison'],
                'registered_at':item['registered_at'],'record_sha256':item['record_sha256']})
        return {'comparisons':items,'next_offset':offset+limit if len(rows)>limit else None}
