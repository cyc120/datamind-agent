"""Version-bound append-only local extraction reviews, independent of source authority."""
import hashlib
import uuid

import psycopg2
from psycopg2.extras import Json,RealDictCursor
from fastapi import HTTPException
from ingestion.facts import payload_hash


def history(document_id,fact_id,dsn):
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT review_id,document_id,fact_id,document_version,source_sha256,parser_version,
                       payload_sha256,previous_review_id,decision,reviewer_label,reviewer_user_id,reviewer_roles,reason,created_at
                       FROM document_fact_reviews WHERE document_id=%s AND fact_id=%s
                       ORDER BY created_at,review_id LIMIT 500''',(document_id,fact_id))
        return [dict(row) for row in cur.fetchall()]


def record(document,fact,source,request,dsn,*,identity):
    """API verified source/block locators before calling; recheck locked source/fact below."""
    def reject(code,status=409):raise HTTPException(status,detail={'error_code':code})
    with psycopg2.connect(dsn) as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
        from .auth import lock_identity
        actor=lock_identity(cur,identity,'reviewer')
        request=dict(request,reviewer_user_id=actor['user_id'],reviewer_label=actor['display_name'],reviewer_roles=actor['roles'])
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('fact-review-request:'+request['request_id'],))
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('fact-review:'+document['document_id']+':'+fact['fact_id'],))
        cur.execute('SELECT * FROM document_assets WHERE document_id=%s FOR UPDATE',(document['document_id'],))
        current=cur.fetchone()
        if not current or current['status']!='indexed' or 'public' not in current['acl'] or current['needs_review']:reject('DOCUMENT_NOT_FOUND',404)
        if any(current[k]!=document[k] for k in ('doc_version','sha256','parser_version','object_uri')):reject('SOURCE_VERSION_MISMATCH')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=document['sha256']:reject('SOURCE_VERSION_MISMATCH')
        expected={'document_version':document['doc_version'],'source_sha256':document['sha256'],
                  'parser_version':document['parser_version'],'payload_sha256':fact['payload_sha256']}
        if any(request[k]!=v for k,v in expected.items()):reject('FACT_VERSION_MISMATCH')
        cur.execute('SELECT payload,payload_sha256 FROM document_facts WHERE document_id=%s AND fact_id=%s FOR UPDATE',
                    (document['document_id'],fact['fact_id']))
        original=cur.fetchone()
        if not original or payload_hash(original['payload'])!=original['payload_sha256'] or original['payload_sha256']!=fact['payload_sha256']:reject('FACT_VERSION_MISMATCH')
        cur.execute('SELECT * FROM document_fact_reviews WHERE request_id=%s',(request['request_id'],))
        existing=cur.fetchone()
        if existing:
            if existing['document_id']!=document['document_id'] or existing['fact_id']!=fact['fact_id'] or existing['request_payload']!=request:reject('REVIEW_REQUEST_ID_REUSED')
            return dict(existing)
        cur.execute('''SELECT review_id FROM document_fact_reviews WHERE document_id=%s AND fact_id=%s
                       AND document_version=%s AND source_sha256=%s AND parser_version=%s AND payload_sha256=%s
                       ORDER BY created_at DESC,review_id DESC LIMIT 1''',
                    (document['document_id'],fact['fact_id'],document['doc_version'],document['sha256'],document['parser_version'],fact['payload_sha256']))
        latest=cur.fetchone();previous=latest['review_id'] if latest else None
        if request.get('previous_review_id')!=previous:reject('REVIEW_STATE_CHANGED')
        review_id='review-'+uuid.uuid4().hex[:24]
        cur.execute('''INSERT INTO document_fact_reviews(review_id,request_id,document_id,fact_id,document_version,
                       source_sha256,parser_version,payload_sha256,previous_review_id,decision,reviewer_label,
                       reason,fact_snapshot,request_payload,reviewer_user_id,reviewer_roles) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING *''',
                    (review_id,request['request_id'],document['document_id'],fact['fact_id'],document['doc_version'],document['sha256'],
                     document['parser_version'],fact['payload_sha256'],previous,request['decision'],request['reviewer_label'],
                     request['reason'],Json(original['payload']),Json(request),actor['user_id'],actor['roles']))
        return dict(cur.fetchone())
