"""Explicit local authority decisions; never infer authority from source names."""
from datetime import timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from .auth import lock_identity
from .industrial_rules import timestamp
from .visibility import is_published

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'source-authority-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def reject(code,status=409):
    raise HTTPException(status,detail={'error_code':code})


def report_binding(fact):
    return {**{k:fact[k] for k in ('document_id','document_version','source_sha256','parser_version','fact_id','payload_sha256')},
            'review_id':(fact.get('latest_review') or {}).get('review_id'),
            'approval_id':(fact.get('latest_approval') or {}).get('approval_id')}


def payload_valid(rule):
    try:
        p=rule['payload'];context=p['context'];basis=p['basis']
        return (digest(p)==rule['payload_sha256'] and p['version']==VERSION
                and p['selected_source'] in {'production','report'}
                and set(context['scope'])=={'part_id','operation_no','batch_id','measurement_id','metric','method'}
                and set(context['dataset'])=={'dataset_version','seed_sha256','pdf_sha256'}
                and set(context['report'])=={'document_id','document_version','source_sha256','parser_version','fact_id','payload_sha256','review_id','approval_id'}
                and set(basis)=={'document_id','document_version','source_sha256','page_no'}
                and timestamp(p['valid_from'])<timestamp(p['valid_to']) and bool(p['rationale'].strip()))
    except (KeyError,TypeError,ValueError,AttributeError):return False


def candidate_context(dataset,row,fact):
    return {'dataset':{k:dataset[k] for k in ('dataset_version','seed_sha256','pdf_sha256')},
            'scope':{k:row[k] for k in ('part_id','operation_no','batch_id','measurement_id','metric','method')},
            'production_row_sha256':digest(row),'report':report_binding(fact)}


def candidates(question,dsn):
    from .industrial import run
    result=run(question,dsn=dsn,apply_authority=False)
    facts={f['fact_id']:f for f in result['facts']};raw={r['measurement_id']:r for r in result['sql_rows']}
    items=[]
    for row in result['rows']:
        if row['agreement']!='unresolved':continue
        fact=facts[row['report_fact_id']];context=candidate_context(result['dataset'],raw[row['measurement_id']],fact)
        items.append({'context':context,'candidate_sha256':digest(context),'production_value':row['value'],
                      'production_unit':row['unit'],'report_value':fact['raw_value'],'report_unit':fact['raw_unit'],
                      'produced_at':row['produced_at'],'basis_suggestion':{'document_id':fact['document_id'],
                          'document_version':fact['document_version'],'source_sha256':fact['source_sha256'],
                          'page_no':fact['evidence_refs'][0]['page_no']},
                      'valid_from':result['scope']['start'],'valid_to':result['scope']['end'],
                      'eligible':fact.get('formal_approval_status')=='approved'})
    return items


def basis_valid(cur,basis):
    cur.execute('SELECT * FROM document_assets WHERE document_id=%s',(basis['document_id'],))
    doc=cur.fetchone()
    # The document publish gate (indexed, public ACL, review cleared, allowed collection)
    # is the shared one; everything below is this module's own version/source binding.
    if not is_published(doc):return False
    if doc['doc_version']!=basis['document_version'] or doc['sha256']!=basis['source_sha256']:return False
    if doc['file_type']!='pdf' or not 1<=basis['page_no']<=doc['page_count']:return False
    path=Path(doc.get('object_uri') or '').resolve()
    return (any(path.is_relative_to(ROOT/name) for name in ('data/knowledge_base','data/ohr_bench_samples','data/industrial_demo')) and path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest()==basis['source_sha256'])


def load(cur):
    cur.execute('''SELECT r.*,e.event_id,e.decision,e.actor_user_id,e.actor_label,e.reason AS activation_reason
        FROM source_authority_rules r LEFT JOIN LATERAL
        (SELECT * FROM source_authority_events WHERE rule_id=r.rule_id ORDER BY created_at DESC,event_id DESC LIMIT 1) e ON true
        ORDER BY r.created_at,r.rule_id LIMIT 1001''')
    rows=cur.fetchall()
    if len(rows)>1000:reject('AUTHORITY_REGISTRY_LIMIT',503)
    result=[dict(r) for r in rows]
    for rule in result:rule['payload_valid']=payload_valid(rule)
    return result


def list_rules(dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        return load(cur)


def register(request,identity,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'admin')
        if not request['rationale'].strip():reject('AUTHORITY_RATIONALE_REQUIRED',422)
        cur.execute('SELECT pg_advisory_xact_lock(714202611)')
        stored_request={**request,'actor_user_id':actor['user_id']}
        cur.execute('SELECT * FROM source_authority_rules WHERE request_id=%s',(request['request_id'],))
        existing=cur.fetchone()
        if existing:
            if existing['request_payload']!=stored_request:reject('AUTHORITY_REQUEST_ID_REUSED')
            return dict(existing)
        items=candidates(request['question'],dsn)
        item=next((c for c in items if c['context']['scope']['measurement_id']==request['measurement_id']),None)
        if not item or item['candidate_sha256']!=request['candidate_sha256']:reject('AUTHORITY_CONTEXT_CHANGED')
        if not item['eligible']:reject('APPROVED_FIELD_REQUIRED')
        try:
            start=timestamp(request['valid_from']);end=timestamp(request['valid_to'])
            valid=start<end and start<=timestamp(item['produced_at'])<end
        except (ValueError,TypeError):valid=False
        if not valid:reject('AUTHORITY_VALIDITY_INVALID',422)
        basis={k:request['basis'][k] for k in ('document_id','document_version','source_sha256','page_no')}
        if not basis_valid(cur,basis):reject('AUTHORITY_BASIS_INVALID')
        payload={'version':VERSION,'context':item['context'],'selected_source':request['selected_source'],
                 'valid_from':request['valid_from'],'valid_to':request['valid_to'],'basis':basis,'rationale':request['rationale']}
        rule_id='authority-'+uuid.uuid4().hex[:24]
        cur.execute('''INSERT INTO source_authority_rules(rule_id,request_id,creator_user_id,creator_label,creator_roles,
            payload,payload_sha256,request_payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (rule_id,request['request_id'],actor['user_id'],actor['display_name'],actor['roles'],Json(payload),digest(payload),Json(stored_request)))
        return dict(cur.fetchone())


def activation(rule_id,request,identity,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'approver')
        if not request['reason'].strip():reject('AUTHORITY_REASON_REQUIRED',422)
        cur.execute('SELECT pg_advisory_xact_lock(714202611)')
        cur.execute('SELECT * FROM source_authority_rules WHERE rule_id=%s FOR UPDATE',(rule_id,));rule=cur.fetchone()
        if not rule:reject('AUTHORITY_RULE_NOT_FOUND',404)
        if rule['creator_user_id']==actor['user_id']:reject('AUTHORITY_CREATOR_CANNOT_APPROVE')
        stored_request={**request,'actor_user_id':actor['user_id'],'rule_id':rule_id}
        cur.execute('SELECT * FROM source_authority_events WHERE request_id=%s',(request['request_id'],));existing=cur.fetchone()
        if existing:
            if existing['request_payload']!=stored_request:reject('AUTHORITY_REQUEST_ID_REUSED')
            return dict(existing)
        cur.execute('SELECT * FROM source_authority_events WHERE rule_id=%s ORDER BY created_at DESC,event_id DESC LIMIT 1',(rule_id,))
        previous=cur.fetchone()
        if request['previous_event_id']!=(previous['event_id'] if previous else None):reject('AUTHORITY_STATE_CHANGED')
        if request['decision']=='revoked' and (not previous or previous['decision']!='approved'):reject('NO_AUTHORITY_APPROVAL_TO_REVOKE')
        payload=rule['payload']
        if request['decision']=='approved':
            if not payload_valid(rule):reject('AUTHORITY_PAYLOAD_CHANGED')
            scope=payload['context']['scope']
            # Context validation must use the measurement's own produced month,
            # independent of the rule interval's beginning.
            cur.execute('SELECT produced_at FROM demo_industrial.production_batches WHERE batch_id=%s',(scope['batch_id'],))
            batch=cur.fetchone()
            if not batch:reject('AUTHORITY_CONTEXT_CHANGED')
            month=batch['produced_at'].astimezone(timezone(timedelta(hours=8)))
            question=f'{month.year}年{month.month}月{scope["part_id"]}工序{scope["operation_no"]}温度核验'
            item=next((c for c in candidates(question,dsn) if c['context']['scope']==scope),None)
            if not item or item['context']!=payload['context'] or not item['eligible']:reject('AUTHORITY_CONTEXT_CHANGED')
            if not basis_valid(cur,payload['basis']):reject('AUTHORITY_BASIS_INVALID')
        event_id='authority-event-'+uuid.uuid4().hex[:24]
        cur.execute('''INSERT INTO source_authority_events(event_id,request_id,rule_id,previous_event_id,decision,
            actor_user_id,actor_label,actor_roles,reason,request_payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (event_id,request['request_id'],rule_id,request['previous_event_id'],request['decision'],actor['user_id'],
             actor['display_name'],actor['roles'],request['reason'],Json(stored_request)))
        return dict(cur.fetchone())


def history(rule_id,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT * FROM source_authority_events WHERE rule_id=%s ORDER BY created_at,event_id',(rule_id,))
        return [dict(r) for r in cur.fetchall()]


def resolve(result,row,fact,dataset,rules):
    """Only comparable value conflicts can be resolved; all other gates survive."""
    if result['agreement']!='unresolved':return result
    issue=next(i for i in result['issues'] if i['kind']=='value_conflict')
    invalid=[r['rule_id'] for r in rules if r.get('decision')=='approved' and not r.get('payload_valid')]
    if invalid:
        issue['reason']='AUTHORITY_REGISTRY_INVALID';issue['authority_rule_ids']=invalid
        return result
    context=candidate_context(dataset,row,fact)
    active=[r for r in rules if r.get('decision')=='approved' and r['payload']['context']['scope']==context['scope']]
    applicable=[]
    for rule in active:
        p=rule['payload']
        try:valid=timestamp(p['valid_from'])<=timestamp(row['produced_at'])<timestamp(p['valid_to'])
        except (ValueError,TypeError):valid=False
        if valid:applicable.append(rule)
    if not applicable:return result
    if len(applicable)!=1:
        issue['reason']='AUTHORITY_RULE_OVERLAP';issue['authority_rule_ids']=[r['rule_id'] for r in applicable]
        return result
    rule=applicable[0];p=rule['payload']
    if (digest(p)!=rule['payload_sha256'] or p['context']!=context or not rule.get('basis_valid')
        or fact.get('formal_approval_status')!='approved' or p['version']!=VERSION
        or rule['creator_user_id']==rule['actor_user_id']):
        issue['reason']='AUTHORITY_RULE_STALE';issue['authority_rule_ids']=[rule['rule_id']]
        return result
    selection=({'kind':'sql_measurement','measurement_id':row['measurement_id'],'row_sha256':context['production_row_sha256']}
               if p['selected_source']=='production' else {'kind':'document_fact',**context['report']})
    selected=result['normalized_value'] if p['selected_source']=='production' else issue['right_value']
    proof={'rule_id':rule['rule_id'],'rule_sha256':rule['payload_sha256'],'event_id':rule['event_id'],
           'rule_version':VERSION,'registration':p,'creator_label':rule['creator_label'],
           'approver_label':rule['actor_label'],'approval_reason':rule['activation_reason']}
    result.update(agreement='resolved_by_authority',authority=proof,selected_fact_ref=selection,
                  selected_value=selected,selected_unit='degC')
    issue.update(status='resolved_by_authority',reason='REGISTERED_AUTHORITY_RULE',authority=proof,selected_fact_ref=selection)
    result['rules'].append({'rule_id':rule['rule_id'],'rule_version':VERSION,'event_id':rule['event_id']})
    return result
