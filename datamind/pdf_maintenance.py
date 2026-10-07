"""Repair lost published private PDF vectors and reclaim orphaned points.

Repair only re-writes points that the frozen, immutable plan already defines with exact
payload and vector bytes; it never changes the plan, the publication row or the current
head. Reclamation is dry-run by default, is restricted to private collections and never
deletes an id referenced by any frozen plan.

Cross-store reads are not atomic: a publication frozen after the reference set is read is
outside this operation's guarantee, so the reference set is re-read immediately before
deleting and the caller must treat the result as a maintenance window, not a transaction.
"""
from __future__ import annotations

from typing import Annotated

import psycopg2
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import pdf_index as registry, pdf_vectors as vectors

SCAN_BATCH = 256
DELETE_BATCH = 256
MAX_SAMPLE = 20


class ReclaimRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    collection: Annotated[str, Field(min_length=1, max_length=128)]
    confirm: bool = False


def reclaim_payload(value):
    try:
        return ReclaimRequest.model_validate(value).model_dump()
    except (ValidationError, ValueError, TypeError):
        fail('PDF_MAINTENANCE_REQUEST_INVALID', 422)


def fail(code, status=409):
    from fastapi import HTTPException
    raise HTTPException(status, detail={'error_code': code})


def _publication_row(publication_id, dsn):
    if not isinstance(publication_id, str) or not vectors._ID.fullmatch(publication_id):
        fail('PDF_PUBLICATION_ID_INVALID', 422)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT * FROM pdf_knowledge_requests WHERE publication_id=%s', (publication_id,))
        row = cur.fetchone()
    if row is None:
        fail('PDF_PUBLICATION_NOT_FOUND', 404)
    if row['status'] != 'published':
        fail('PDF_PUBLICATION_NOT_PUBLISHED')
    if row['vector_plan'] is None:
        fail('PDF_VECTOR_PLAN_REQUIRED')
    return row


def repair_publication(publication_id, dsn, *, client=None):
    """Restore points missing from a published private version, without touching heads."""
    row = _publication_row(publication_id, dsn)
    plan = row['vector_plan']
    snapshot, bundle = registry._snapshot_bundle(row, dsn)
    # Re-validate the frozen plan against the fixed snapshot/bundle before writing bytes.
    vectors.validate_plan(plan, snapshot, bundle, publication_id)
    outcome = vectors.repair_plan(plan, client=client)
    return {'publication_id': publication_id, 'document_id': row['document_id'],
            'document_version': row['document_version'], 'plan_sha256': row['vector_plan_sha256'],
            'verified': True, 'head_unchanged': True, **outcome}


def _referenced(dsn):
    """Union of referenced point ids per collection over every frozen plan."""
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT publication_id, vector_plan FROM pdf_knowledge_requests WHERE vector_plan IS NOT NULL')
        rows = cur.fetchall()
    by_collection = {}
    for row in rows:
        plan = row['vector_plan']
        # Fail closed instead of trusting a tampered plan to define what is referenced.
        vectors._checked_plan(plan)
        referenced = by_collection.setdefault(plan['collection'], set())
        referenced.update(point['id'] for point in plan['points'])
    return by_collection


def _collection_ids(client, name):
    ids, offset = [], None
    while True:
        points, offset = client.scroll(name, limit=SCAN_BATCH, offset=offset,
                                       with_payload=False, with_vectors=False)
        ids.extend(str(point.id) for point in points)
        if offset is None:
            break
    return ids


def _private_collections(client):
    try:
        names = [item.name for item in client.get_collections().collections]
    except Exception:
        fail('PDF_INDEX_QDRANT_UNAVAILABLE', 503)
    return sorted(name for name in names if name.startswith(vectors.COLLECTION_PREFIX))


def orphan_scan(dsn, *, client=None):
    """Report private collections and the point ids no frozen plan references."""
    referenced = _referenced(dsn)
    report = []
    with vectors._client(client) as connection:
        for name in _private_collections(connection):
            actual = _collection_ids(connection, name)
            known = referenced.get(name, set())
            orphans = sorted(point_id for point_id in actual if point_id not in known)
            report.append({'collection': name, 'points': len(actual), 'referenced_here': len(set(actual) & known),
                           'orphans': len(orphans), 'orphan_sample': orphans[:MAX_SAMPLE]})
    return {'dry_run': True, 'collections': report,
            'orphans': sum(item['orphans'] for item in report),
            'referenced_publications': sum(len(items) for items in referenced.values())}


def reclaim_orphans(collection, dsn, *, confirm=False, client=None, batch=DELETE_BATCH):
    """Delete points of one private collection that no frozen plan references.

    Dry-run unless `confirm` is true. The reference set is re-read immediately before
    deletion; referenced points are never removed.
    """
    if not isinstance(collection, str) or not collection.startswith(vectors.COLLECTION_PREFIX):
        fail('PDF_MAINTENANCE_COLLECTION_FORBIDDEN', 422)
    if isinstance(batch, bool) or not isinstance(batch, int) or not 1 <= batch <= 1024:
        fail('PDF_MAINTENANCE_BATCH_INVALID', 422)
    from qdrant_client import models
    with vectors._client(client) as connection:
        vectors._collection(connection, collection)
        referenced = _referenced(dsn).get(collection, set())
        actual = _collection_ids(connection, collection)
        orphans = sorted(point_id for point_id in actual if point_id not in referenced)
        base = {'collection': collection, 'points': len(actual), 'referenced_here': len(set(actual) & referenced),
                'orphans': len(orphans), 'orphan_sample': orphans[:MAX_SAMPLE]}
        if not confirm:
            return {'dry_run': True, 'deleted': 0, **base}
        # Narrow the race window: re-read and re-subtract right before deleting.
        referenced = _referenced(dsn).get(collection, set())
        orphans = [point_id for point_id in orphans if point_id not in referenced]
        deleted = 0
        for start in range(0, len(orphans), batch):
            chunk = orphans[start:start + batch]
            try:
                connection.delete(collection, points_selector=models.PointIdsList(points=chunk), wait=True)
            except Exception:
                fail('PDF_INDEX_QDRANT_UNAVAILABLE', 503)
            deleted += len(chunk)
        referenced = _referenced(dsn).get(collection, set())
        remaining = [point_id for point_id in _collection_ids(connection, collection) if point_id not in referenced]
        if remaining:
            fail('PDF_MAINTENANCE_ORPHANS_REMAIN')
        return {'dry_run': False, 'deleted': deleted, 'remaining_orphans': 0, **base}
