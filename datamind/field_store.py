"""Shared version/hash/page validation for publicly scoped document field readers."""
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from ingestion.facts import payload_hash


def read_verified_facts(document, source, dsn, *, usable_only=False):
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT payload,payload_sha256 FROM document_facts
                       WHERE document_id=%s AND document_version=%s AND source_sha256=%s AND parser_version=%s
                       ORDER BY fact_id LIMIT 5000''', (document['document_id'],document['doc_version'],document['sha256'],document['parser_version']))
        rows=cur.fetchall()
        cur.execute('SELECT block_id,content_hash,text,parser_version,page_no,bbox FROM document_blocks WHERE document_id=%s',(document['document_id'],))
        blocks={b['block_id']:b for b in cur.fetchall()}
        cur.execute('''SELECT DISTINCT ON (fact_id,payload_sha256) review_id,fact_id,payload_sha256,
                       decision,reviewer_label,reviewer_user_id,reviewer_roles,reason,created_at FROM document_fact_reviews
                       WHERE document_id=%s AND document_version=%s AND source_sha256=%s AND parser_version=%s
                       ORDER BY fact_id,payload_sha256,created_at DESC,review_id DESC''',
                    (document['document_id'],document['doc_version'],document['sha256'],document['parser_version']))
        reviews={(r['fact_id'],r['payload_sha256']):dict(r) for r in cur.fetchall()}
        cur.execute('''SELECT DISTINCT ON (fact_id,payload_sha256) approval_id,fact_id,payload_sha256,review_id,
                       decision,approver_user_id,approver_label,reason,created_at FROM document_fact_approvals
                       WHERE document_id=%s AND document_version=%s AND source_sha256=%s AND parser_version=%s
                       ORDER BY fact_id,payload_sha256,created_at DESC,approval_id DESC''',
                    (document['document_id'],document['doc_version'],document['sha256'],document['parser_version']))
        approvals={(r['fact_id'],r['payload_sha256']):dict(r) for r in cur.fetchall()}
    sizes={}
    if rows and document['file_type']=='pdf':
        import pymupdf
        with pymupdf.open(source) as pdf: sizes={i+1:[float(p.rect.width),float(p.rect.height)] for i,p in enumerate(pdf)}
    result=[]
    for row in rows:
        fact=row['payload']
        if payload_hash(fact)!=row['payload_sha256'] or any(fact.get(k)!=v for k,v in (
            ('document_id',document['document_id']),('document_version',document['doc_version']),
            ('source_sha256',document['sha256']),('parser_version',document['parser_version']))): continue
        refs=fact.get('evidence_refs') or [];roles={r.get('role') for r in refs}
        if fact.get('extraction_version')=='document-facts-v1': valid=len(refs)==3 and roles=={'entity','header','value'}
        elif fact.get('extraction_version') in {'industrial-native-v1','industrial-labelled-v2'}:
            valid=bool(refs) and {'entity','header'}<=roles and ('value' in roles or {'lower','upper'}<=roles)
        else: valid=False
        if fact.get('extraction_version') == 'industrial-labelled-v2':
            from ingestion.industrial_facts import COMMON, REQUIREMENT, MEASUREMENT
            required = COMMON + (REQUIREMENT if fact.get('fact_kind') == 'requirement' else MEASUREMENT)
            valid = (valid and len(refs) == len(required) and {r.get('field') for r in refs} == set(required)
                     and len({r.get('page_no') for r in refs}) == 1
                     and len({r.get('block_id') for r in refs}) == len(required))
        for ref in refs:
            b=blocks.get(ref.get('block_id'));box=ref.get('bbox') or [];size=ref.get('page_size') or []
            valid=valid and bool(b) and len(box)==4 and len(size)==2
            if not valid: break
            valid=(b['parser_version']==document['parser_version'] and b['page_no']==ref.get('page_no') and
                ref.get('source_sha256')==document['sha256'] and ref.get('block_content_hash')==b['content_hash'] and
                hashlib.sha256(b['text'].encode()).hexdigest()==b['content_hash'] and ref.get('coordinate_system')=='pdf_points' and
                ref.get('page_no') in sizes and all(abs(a-b)<.01 for a,b in zip(size,sizes.get(ref.get('page_no'),[]))) and
                0<=box[0]<box[2]<=size[0] and 0<=box[1]<box[3]<=size[1])
            if valid and fact.get('extraction_version') == 'industrial-labelled-v2':
                from ingestion.industrial_facts import labelled_value
                valid = (labelled_value(b['text']) == (ref.get('field'), ref.get('field_value'))
                         and b.get('bbox') is not None and len(b['bbox']) == 4
                         and all(abs(a-c) < .01 for a,c in zip(box,b['bbox'])))
        if valid:
            review=reviews.get((fact['fact_id'],row['payload_sha256']))
            approval=approvals.get((fact['fact_id'],row['payload_sha256']))
            approval_current=bool(approval and review and approval['review_id']==review['review_id']
                                  and review['decision']=='confirmed' and review['reviewer_user_id'])
            if usable_only and review and review['decision'] in {'rejected','needs_review'}:continue
            # Once this version entered approval, a new review cannot bypass a
            # rejection/revocation. Stale approvals require independent approval.
            if usable_only and approval and not (approval_current and approval['decision']=='approved'):continue
            fact['payload_sha256']=row['payload_sha256']
            fact['latest_review']=review
            fact['latest_approval']=approval
            fact['formal_approval_status']=approval['decision'] if approval_current else 'stale' if approval else 'not_approved'
            if review:review['created_at']=review['created_at'].isoformat()
            if approval:approval['created_at']=approval['created_at'].isoformat()
            result.append(fact)
    return result
