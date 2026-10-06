"""Authenticated, version-bound association review overlays; never edit CAD source."""
import copy
import uuid
import psycopg2
from psycopg2.extras import Json,RealDictCursor
from .auth import lock_identity
from .cad_store import checked,digest,fail
from ingestion.cad_contract import eligible_features

AUDIT_FIELDS=('review_id','request_id','document_id','document_version','feature_id','source_sha256',
              'parser_version','bundle_sha256','feature_sha256','previous_review_id','decision',
              'selected_target','reason','reviewer_user_id','reviewer_label','reviewer_roles',
              'feature_snapshot','request_payload')
REQUEST_FIELDS=('request_id','document_version','source_sha256','parser_version','bundle_sha256',
                'feature_sha256','previous_review_id','decision','selected_target','reason')


def feature(row,feature_id):
    for f in row['bundle']['snapshot']['features']:
        if f['feature_id']==feature_id:return f
    fail('CAD_FEATURE_NOT_FOUND',404)


def selection(original,decision,target):
    if decision not in {'confirmed','rejected','needs_review'}:fail('CAD_REVIEW_INVALID',422)
    if decision=='confirmed':
        if target not in original['target_candidates']:fail('CAD_REVIEW_TARGET_INVALID',422)
    elif target is not None:fail('CAD_REVIEW_TARGET_UNEXPECTED',422)


def public(row):
    return {k:row[k] for k in AUDIT_FIELDS if k not in {'feature_snapshot','request_payload'}} | {'created_at':row['created_at']}


def validated(review,row):
    review=dict(review)
    try:
        if digest({k:review[k] for k in AUDIT_FIELDS})!=review['record_sha256']:fail('CAD_REVIEW_HASH_MISMATCH')
        original=feature(row,review['feature_id'])
        if (any(review[k]!=row[k] for k in ('document_id','document_version','source_sha256','parser_version','bundle_sha256'))
                or review['feature_snapshot']!=original or digest(original)!=review['feature_sha256']
                or review['request_payload']!={k:review[k] for k in REQUEST_FIELDS}):
            fail('CAD_REVIEW_SNAPSHOT_MISMATCH')
        selection(original,review['decision'],review['selected_target'])
    except (KeyError,ValueError,TypeError):fail('CAD_REVIEW_INVALID')
    return review


def read_all(cur,row):
    cur.execute('''SELECT * FROM cad_association_reviews WHERE document_id=%s AND document_version=%s
        ORDER BY created_at,review_id LIMIT 1001''',(row['document_id'],row['document_version']))
    rows=cur.fetchall()
    if len(rows)>1000:fail('CAD_REVIEW_HISTORY_LIMIT')
    results={};previous={}
    for stored in rows:
        review=validated(stored,row);key=review['feature_id']
        if review['previous_review_id']!=previous.get(key):fail('CAD_REVIEW_CHAIN_INVALID')
        previous[key]=review['review_id'];results.setdefault(key,[]).append(review)
    return results


def inspect(row,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        reviews=read_all(cur,row)
    snapshot=copy.deepcopy(row['bundle']['snapshot']);states=[];exclusions={}
    for f in snapshot['features']:
        history=reviews.get(f['feature_id'],[]);latest=history[-1] if history else None
        states.append({'feature_id':f['feature_id'],'feature_sha256':digest(f),
                       'latest_review':public(latest) if latest else None,'history_count':len(history)})
        if latest and latest['decision']=='confirmed':
            target=latest['selected_target'];f.update(association_status='confirmed',target_candidates=[target],selected_target=target,
                association_evidence={'method':'human_label','reference_id':latest['review_id'],'source_sha256':row['source_sha256'],
                    'annotation_handle':f['locator']['handle'],'target_handle':target,'rationale':latest['reason']})
        else:
            exclusions[f['feature_id']]=('CAD_ASSOCIATION_NOT_REVIEWED' if not latest else
                'CAD_ASSOCIATION_REJECTED' if latest['decision']=='rejected' else 'CAD_ASSOCIATION_REVIEW_PENDING')
            f.update(association_status='ambiguous' if len(f['target_candidates'])>1 else 'unresolved',
                     selected_target=None,association_evidence=None)
    context={k:v['value'] for k,v in snapshot['identity'].items()};context['source_sha256']=row['source_sha256']
    gate=eligible_features(snapshot,context)
    for item in gate['excluded']:
        item['reason']=exclusions.get(item['feature_id'],item['reason'])
    return {'association_reviews':states,'review_gate':gate,
            'review_semantics':'Authenticated association review only; no formal drawing approval, production binding or quality conclusion'}


def history(row,feature_id,dsn):
    feature(row,feature_id)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        records=read_all(cur,row).get(feature_id,[])
    return {'document_version':row['document_version'],'feature_id':feature_id,
            'reviews':[public(r) for r in records],'latest_review':public(records[-1]) if records else None}


def record(document_id,feature_id,payload,dsn,identity):
    payload=dict(payload);payload['reason']=payload['reason'].strip()
    if not payload['reason']:fail('CAD_REVIEW_REASON_REQUIRED',422)
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'reviewer')
        for key in sorted(['cad-document:'+document_id,'cad-review-request:'+payload['request_id']]):
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
        cur.execute('SELECT * FROM cad_association_reviews WHERE request_id=%s',(payload['request_id'],));prior=cur.fetchone()
        if prior:
            if (prior['document_id']!=document_id or prior['feature_id']!=feature_id
                    or prior['request_payload']!=payload or prior['reviewer_user_id']!=actor['user_id']):
                fail('CAD_REVIEW_REQUEST_ID_REUSED')
            cur.execute('SELECT * FROM cad_versions WHERE document_id=%s AND document_version=%s',
                        (document_id,prior['document_version']))
            row=checked(cur.fetchone());read_all(cur,row)
            return public(validated(prior,row))
        cur.execute('''SELECT v.* FROM cad_assets a JOIN cad_versions v
            ON v.document_id=a.document_id AND v.document_version=a.current_version
            WHERE a.document_id=%s FOR UPDATE OF a''',(document_id,))
        row=checked(cur.fetchone());original=feature(row,feature_id)
        if (any(payload[k]!=row[k] for k in ('document_version','source_sha256','parser_version','bundle_sha256'))
                or payload['feature_sha256']!=digest(original)):fail('CAD_REVIEW_VERSION_CHANGED')
        selection(original,payload['decision'],payload['selected_target'])
        records=read_all(cur,row).get(feature_id,[]);previous=records[-1]['review_id'] if records else None
        if payload['previous_review_id']!=previous:fail('CAD_REVIEW_STATE_CHANGED')
        review={'review_id':'cad-review-'+uuid.uuid4().hex[:24],'document_id':document_id,'feature_id':feature_id,
                **payload,'reviewer_user_id':actor['user_id'],'reviewer_label':actor['display_name'],
                'reviewer_roles':actor['roles'],'feature_snapshot':original,'request_payload':payload}
        review['record_sha256']=digest({k:review[k] for k in AUDIT_FIELDS})
        fields=(*AUDIT_FIELDS,'record_sha256');values=[Json(review[k]) if k in {'feature_snapshot','request_payload'} else review[k] for k in fields]
        cur.execute('INSERT INTO cad_association_reviews ('+','.join(fields)+') VALUES ('+','.join(['%s']*len(fields))+') RETURNING *',values)
        return public(dict(cur.fetchone()))
