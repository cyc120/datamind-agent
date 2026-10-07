"""Separate local field approvals bound to a confirmed authenticated review."""
import hashlib
import uuid
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from fastapi import HTTPException
from ingestion.facts import payload_hash
from .auth import lock_identity


def history(document_id,fact_id,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT approval_id,request_id,document_id,fact_id,document_version,source_sha256,parser_version,
            payload_sha256,review_id,previous_approval_id,decision,approver_user_id,approver_label,approver_roles,
            reason,created_at FROM document_fact_approvals WHERE document_id=%s AND fact_id=%s
            ORDER BY created_at,approval_id LIMIT 500''',(document_id,fact_id))
        return [dict(r) for r in cur.fetchall()]


def record(document,fact,source,request,dsn,*,identity):
    def reject(code):raise HTTPException(409,detail={'error_code':code})
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor=lock_identity(cur,identity,'approver')
        request=dict(request,approver_user_id=actor['user_id'],approver_label=actor['display_name'],approver_roles=actor['roles'])
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('fact-approval-request:'+request['request_id'],))
        # Shared with review writes so review/approval races cannot cross versions.
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('fact-review:'+document['document_id']+':'+fact['fact_id'],))
        cur.execute('SELECT * FROM document_assets WHERE document_id=%s FOR UPDATE',(document['document_id'],))
        current=cur.fetchone()
        if not current or current['status']!='indexed' or 'public' not in current['acl'] or current['needs_review']:
            raise HTTPException(404,detail={'error_code':'DOCUMENT_NOT_FOUND'})
        if any(current[k]!=document[k] for k in ('doc_version','sha256','parser_version','object_uri')) or hashlib.sha256(source.read_bytes()).hexdigest()!=document['sha256']:
            reject('SOURCE_VERSION_MISMATCH')
        expected={'document_version':document['doc_version'],'source_sha256':document['sha256'],
                  'parser_version':document['parser_version'],'payload_sha256':fact['payload_sha256']}
        if any(request[k]!=v for k,v in expected.items()):reject('FACT_VERSION_MISMATCH')
        cur.execute('SELECT payload,payload_sha256 FROM document_facts WHERE document_id=%s AND fact_id=%s FOR UPDATE',
                    (document['document_id'],fact['fact_id']))
        original=cur.fetchone()
        if not original or original['payload_sha256']!=fact['payload_sha256'] or payload_hash(original['payload'])!=fact['payload_sha256']:reject('FACT_VERSION_MISMATCH')
        cur.execute('SELECT * FROM document_fact_approvals WHERE request_id=%s',(request['request_id'],))
        existing=cur.fetchone()
        if existing:
            if existing['request_payload']!=request or existing['document_id']!=document['document_id'] or existing['fact_id']!=fact['fact_id']:reject('APPROVAL_REQUEST_ID_REUSED')
            return dict(existing)
        version=(document['document_id'],fact['fact_id'],document['doc_version'],document['sha256'],document['parser_version'],fact['payload_sha256'])
        cur.execute('''SELECT * FROM document_fact_reviews WHERE document_id=%s AND fact_id=%s AND document_version=%s
            AND source_sha256=%s AND parser_version=%s AND payload_sha256=%s ORDER BY created_at DESC,review_id DESC LIMIT 1''',version)
        review=cur.fetchone()
        if not review or review['review_id']!=request['review_id'] or review['decision']!='confirmed' or not review['reviewer_user_id']:
            reject('CONFIRMED_AUTHENTICATED_REVIEW_REQUIRED')
        if review['reviewer_user_id']==actor['user_id']:reject('REVIEWER_CANNOT_APPROVE_OWN_REVIEW')
        cur.execute('''SELECT * FROM document_fact_approvals WHERE document_id=%s AND fact_id=%s AND document_version=%s
            AND source_sha256=%s AND parser_version=%s AND payload_sha256=%s ORDER BY created_at DESC,approval_id DESC LIMIT 1''',version)
        previous=cur.fetchone()
        if request['previous_approval_id']!=(previous['approval_id'] if previous else None):reject('APPROVAL_STATE_CHANGED')
        if request['decision']=='revoked' and (not previous or previous['decision']!='approved'):reject('NO_ACTIVE_APPROVAL_TO_REVOKE')
        approval_id='approval-'+uuid.uuid4().hex[:24]
        keys=('request_id','document_version','source_sha256','parser_version','payload_sha256','review_id',
              'previous_approval_id','decision','approver_user_id','approver_label','approver_roles','reason')
        cur.execute('''INSERT INTO document_fact_approvals(approval_id,document_id,fact_id,request_id,document_version,
            source_sha256,parser_version,payload_sha256,review_id,previous_approval_id,decision,approver_user_id,
            approver_label,approver_roles,reason,fact_snapshot,request_payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (approval_id,document['document_id'],fact['fact_id'],*(request[k] for k in keys),Json(original['payload']),Json(request)))
        return dict(cur.fetchone())
