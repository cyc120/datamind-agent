"""Bounded retrieval of explicitly selected, authenticated private PDF versions.

No public knowledge search, generation provider, current-head substitution or
vector writes. PostgreSQL session locks bound simultaneous searches across API
processes, without holding a transaction during file, model or vector I/O.
"""
from contextlib import contextmanager
import copy
import math
import re
from typing import Annotated
from urllib.parse import urlencode

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import pdf_index as publications, pdf_vectors as vectors

MAX_PUBLICATIONS = 4
MAX_CANDIDATES = 512
MAX_TEXT_BYTES = 200_000
MAX_PLAN_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024
SEARCH_CONCURRENCY = 2
# An experimental retrieval floor, not a calibrated relevance/answer guarantee.
MIN_SCORE = 0.35


class PublicationReference(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    publication_id: Annotated[str, Field(pattern=r'^pdf-pub-[a-f0-9]{32}$')]
    expected_plan_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    publications: Annotated[list[PublicationReference], Field(min_length=1, max_length=MAX_PUBLICATIONS)]
    limit: Annotated[int, Field(ge=4, le=12)] = 8

    @field_validator('question')
    @classmethod
    def question_required(cls, value):
        value = value.strip()
        try:
            size = len(value.encode('utf-8'))
        except UnicodeError:
            raise ValueError('PDF_SEARCH_QUESTION_INVALID')
        if not value or size > 8000:
            raise ValueError('PDF_SEARCH_QUESTION_INVALID')
        return value

    @model_validator(mode='after')
    def unique_publications(self):
        ids = [ref.publication_id for ref in self.publications]
        if len(ids) != len(set(ids)):
            raise ValueError('PDF_SEARCH_DUPLICATE_PUBLICATION')
        return self


def fail(code, status=409):
    raise HTTPException(status, detail={'error_code': code})


def _payload(payload):
    try:
        return SearchRequest.model_validate(payload).model_dump()
    except (ValidationError, ValueError, TypeError):
        fail('PDF_SEARCH_REQUEST_INVALID', 422)


@contextmanager
def _search_slot(dsn):
    connection = None
    try:
        connection = psycopg2.connect(dsn, connect_timeout=5)
        connection.autocommit = True
        with connection.cursor() as cur:
            cur.execute("SET statement_timeout='5s'")
            acquired = False
            for slot in range(SEARCH_CONCURRENCY):
                cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',
                            ('pdf-private-search-slot:' + str(slot),))
                if cur.fetchone()[0]:
                    acquired = True
                    break
        if not acquired:
            fail('PDF_SEARCH_BUSY', 503)

        def live():
            with connection.cursor() as cur:
                cur.execute('SELECT 1')
                if cur.fetchone() != (1,):
                    fail('PDF_SEARCH_DATABASE_UNAVAILABLE', 503)

        yield live
        live()
    except psycopg2.Error:
        fail('PDF_SEARCH_DATABASE_UNAVAILABLE', 503)
    finally:
        # Session closure releases its single lock even after an exception or a
        # broken connection. No pool is used and no transaction remains open.
        if connection is not None:
            connection.close()


def _admit(refs, dsn):
    ids = [ref['publication_id'] for ref in refs]
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT publication_id,document_id,status,vector_plan_sha256,
            octet_length(convert_to(vector_plan::text,'UTF8')) AS plan_bytes,
            target_snapshot #>> '{counts,chunk_count}' AS chunk_count,
            target_snapshot #>> '{counts,text_utf8_bytes}' AS text_bytes
            FROM pdf_knowledge_requests WHERE publication_id=ANY(%s)''', (ids,))
        records = {row['publication_id']: row for row in cur.fetchall()}
    seen_documents = set()
    totals = {'chunk_count': 0, 'text_bytes': 0, 'plan_bytes': 0}
    for ref in refs:
        row = records.get(ref['publication_id'])
        if row is None:
            fail('PDF_PUBLICATION_NOT_FOUND', 404)
        if row['status'] != 'published':
            fail('PDF_PUBLICATION_NOT_PUBLISHED')
        if row['vector_plan_sha256'] != ref['expected_plan_sha256']:
            fail('PDF_INDEX_SELECTION_CHANGED')
        if row['document_id'] in seen_documents:
            fail('PDF_SEARCH_DUPLICATE_DOCUMENT', 422)
        seen_documents.add(row['document_id'])
        for key in ('chunk_count', 'text_bytes'):
            value = row[key]
            if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,8}', value):
                fail('PDF_PUBLICATION_EVIDENCE_INVALID')
            totals[key] += int(value)
        if type(row['plan_bytes']) is not int or row['plan_bytes'] <= 0:
            fail('PDF_PUBLICATION_EVIDENCE_INVALID')
        totals['plan_bytes'] += row['plan_bytes']
    if (totals['chunk_count'] > MAX_CANDIDATES or totals['text_bytes'] > MAX_TEXT_BYTES
            or totals['plan_bytes'] > MAX_PLAN_BYTES):
        fail('PDF_SEARCH_SELECTION_TOO_LARGE', 422)


def _selected(ref, dsn, remaining_plan_bytes=MAX_PLAN_BYTES):
    # Guard and body share one SQL snapshot. An oversized replacement between
    # lightweight admission and this load is never decoded by the JSON adapter.
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT r.*,octet_length(convert_to(r.vector_plan::text,'UTF8')) AS _stored_plan_bytes,
            encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') AS _stored_row_digest,
            encode(sha256(convert_to(to_jsonb(s)::text,'UTF8')),'hex') AS _stored_snapshot_digest
            FROM pdf_knowledge_requests r LEFT JOIN pdf_snapshot_versions s
            ON s.document_id=r.document_id AND s.document_version=r.document_version
            WHERE r.publication_id=%s AND r.status='published' AND r.vector_plan_sha256=%s
            AND octet_length(convert_to(r.vector_plan::text,'UTF8'))<=%s
            AND octet_length(convert_to(to_jsonb(r)::text,'UTF8'))
                -octet_length(convert_to(r.vector_plan::text,'UTF8'))<=%s''',
                    (ref['publication_id'], ref['expected_plan_sha256'], remaining_plan_bytes, MAX_METADATA_BYTES))
        row = cur.fetchone()
        if row is None:
            cur.execute('''SELECT status,vector_plan_sha256,
                octet_length(convert_to(vector_plan::text,'UTF8')) AS plan_bytes,
                octet_length(convert_to(to_jsonb(pdf_knowledge_requests)::text,'UTF8'))
                    -octet_length(convert_to(vector_plan::text,'UTF8')) AS metadata_bytes
                FROM pdf_knowledge_requests WHERE publication_id=%s''', (ref['publication_id'],))
            metadata = cur.fetchone()
            if metadata is None:
                fail('PDF_PUBLICATION_NOT_FOUND', 404)
            if metadata['status'] != 'published':
                fail('PDF_PUBLICATION_NOT_PUBLISHED')
            if metadata['vector_plan_sha256'] != ref['expected_plan_sha256']:
                fail('PDF_INDEX_SELECTION_CHANGED')
            if type(metadata['plan_bytes']) is int and metadata['plan_bytes'] > remaining_plan_bytes:
                fail('PDF_SEARCH_SELECTION_TOO_LARGE', 422)
            if type(metadata['metadata_bytes']) is int and metadata['metadata_bytes'] > MAX_METADATA_BYTES:
                fail('PDF_SEARCH_SELECTION_TOO_LARGE', 422)
            fail('PDF_INDEX_SELECTION_CHANGED')
    row = publications._checked(row)
    if row['status'] != 'published':
        fail('PDF_PUBLICATION_NOT_PUBLISHED')
    if row['vector_plan_sha256'] != ref['expected_plan_sha256']:
        fail('PDF_INDEX_SELECTION_CHANGED')
    snapshot, bundle = publications._snapshot_bundle(row, dsn)
    vectors.validate_plan(row['vector_plan'], snapshot, bundle, row['publication_id'])
    return row, snapshot


def _recheck_metadata(rows, dsn):
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT r.publication_id,r.status,r.evidence_sha256,r.vector_plan_sha256,r.result_sha256,
            encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') AS _stored_row_digest,
            encode(sha256(convert_to(to_jsonb(s)::text,'UTF8')),'hex') AS _stored_snapshot_digest
            FROM pdf_knowledge_requests r LEFT JOIN pdf_snapshot_versions s
            ON s.document_id=r.document_id AND s.document_version=r.document_version
            WHERE r.publication_id=ANY(%s)''',
                    ([row['publication_id'] for row in rows],))
        captured = {row['publication_id']: row for row in cur.fetchall()}
    for before in rows:
        after = captured.get(before['publication_id'])
        if (after is None or after['status'] != 'published' or any(after[key] != before[key] for key in
                ('evidence_sha256', 'vector_plan_sha256', 'result_sha256',
                 '_stored_row_digest', '_stored_snapshot_digest'))):
            fail('PDF_PUBLICATION_EVIDENCE_CHANGED')


def _rank(rows, query, limit):
    by_document = []
    for row, snapshot in rows:
        plan = row['vector_plan']
        hits = []
        for chunk, point in zip(plan['chunks'], plan['points']):
            score = math.fsum(a * b for a, b in zip(query, point['vector']))
            if score < MIN_SCORE:
                continue
            selection = urlencode({'version': row['document_version'],
                                   'expected_manifest_sha256': row['manifest_sha256']})
            hits.append({**copy.deepcopy(chunk), 'publication_id': row['publication_id'],
                         'plan_sha256': row['vector_plan_sha256'], 'profile_sha256': plan['profile_sha256'],
                         'title': snapshot['title'], 'retrieval_score': min(1.0, max(-1.0, score)),
                         'source_url': f"/v1/pdf-snapshots/{row['document_id']}/file?{selection}",
                         'page_url': f"/v1/pdf-snapshots/{row['document_id']}/pages/{chunk['page_no']}?{selection}"})
        hits.sort(key=_order)
        by_document.append(hits)
    # Reserve one eligible result per selected document, then fill globally.
    # No empty/weak document is silently removed from coverage metadata.
    chosen = [hits[0] for hits in by_document if hits]
    rest = sorted([hit for hits in by_document for hit in hits[1:]], key=_order)
    return sorted(chosen + rest[:limit - len(chosen)], key=_order)


def _order(hit):
    return (-hit['retrieval_score'], hit['publication_id'], hit['chunk_id'])


def search(payload, dsn, *, embedder=None, client=None):
    request = _payload(payload)
    refs = request['publications']
    with _search_slot(dsn) as live:
        _admit(refs, dsn)
        selected = []
        chunks = text_bytes = plan_bytes = 0
        documents = set()
        for ref in refs:
            row, snapshot = _selected(ref, dsn, MAX_PLAN_BYTES - plan_bytes)
            plan = row['vector_plan']
            chunks += len(plan['chunks'])
            text_bytes += sum(len(chunk['text'].encode('utf-8')) for chunk in plan['chunks'])
            plan_bytes += row['_stored_plan_bytes']
            if (chunks > MAX_CANDIDATES or text_bytes > MAX_TEXT_BYTES or plan_bytes > MAX_PLAN_BYTES):
                fail('PDF_SEARCH_SELECTION_TOO_LARGE', 422)
            if row['document_id'] in documents:
                fail('PDF_SEARCH_DUPLICATE_DOCUMENT', 422)
            documents.add(row['document_id'])
            selected.append((row, snapshot))
        profile = selected[0][0]['vector_plan']['profile']
        if any(row['vector_plan']['profile'] != profile for row, _ in selected):
            fail('PDF_SEARCH_PROFILE_MISMATCH')
        live()
        query = vectors.query_embedding(request['question'], profile, embedder=embedder)
        live()
        vectors._vector_bytes(query)
        with vectors._client(client) as connection:
            vectors.verify_selection([row['vector_plan'] for row, _ in selected], client=connection)
            live()
            hits = _rank(selected, query, request['limit'])
            # Query/vector operations can outlive initial DB/file verification.
            # Explicit versions remain fixed even if current heads advance.
            final_rows = []
            final_bytes = 0
            for ref, (before, _) in zip(refs, selected):
                after, _ = _selected(ref, dsn, MAX_PLAN_BYTES - final_bytes)
                final_bytes += after['_stored_plan_bytes']
                if any(after[key] != before[key] for key in
                       ('evidence_sha256', 'vector_plan_sha256', 'result_sha256',
                        '_stored_row_digest', '_stored_snapshot_digest')):
                    fail('PDF_PUBLICATION_EVIDENCE_CHANGED')
                final_rows.append(after)
            # Check all selected points together after every document's file
            # verification, then capture DB identities together. This bounds
            # the read window but does not invent an atomic cross-store read.
            live()
            vectors.verify_selection([row['vector_plan'] for row in final_rows], client=connection)
            _recheck_metadata(final_rows, dsn)
            live()
        final = []
        for after in final_rows:
            after['_vectors_verified'] = True
            final.append(publications.public(after))
        documents = [{'publication_id': row['publication_id'], 'document_id': row['document_id'],
                      'hit_count': sum(hit['publication_id'] == row['publication_id'] for hit in hits)}
                     for row in final]
        return {'status': 'retrieved' if hits else 'empty', 'question': request['question'],
                'selected_publications': final, 'hits': hits,
                'coverage': {'all_covered': all(item['hit_count'] > 0 for item in documents),
                             'documents': documents},
                'candidate_chunks': sum(len(row['vector_plan']['chunks']) for row, _ in selected),
                'score_floor': MIN_SCORE, 'ranking': 'per-document-best-then-global-dot-v1',
                'vector_integrity_checked': True, 'public_knowledge_visible': False,
                'source_authority_verified': False, 'answer_generated': False}
