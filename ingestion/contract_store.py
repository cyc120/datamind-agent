"""Persist a validated parse result: contract blocks, locators and relations.

Office and DXF blocks locate their origin by part/paragraph/slide/sheet/handle rather than
by page, so they are stored in `document_contract_blocks` instead of the page-shaped
`document_blocks`. Only a result that the contract already validated is written, and a
document's rows are replaced together — a partial write never leaves half a document.
"""
from __future__ import annotations

import hashlib
import json

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from datamind import parse_contract

BLOCK_INSERT = '''
    INSERT INTO document_contract_blocks(block_id, document_id, contract_version, block_type,
        text, reading_order, native_attributes, locator, content_hash)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)'''

RELATION_INSERT = '''
    INSERT INTO document_block_relations(relation_id, document_id, source_block_id,
        target_block_id, relation_type, relation_state, recovery_method, evidence)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)'''


def relation_id(document_id, relation):
    identity = [document_id, relation['source_block_id'], relation['target_block_id'],
                relation['relation_type']]
    return 'rel-' + hashlib.sha256(repr(identity).encode('utf-8')).hexdigest()[:56]


def persist_result(cur, *, document_id, payload):
    """Replace this document's contract rows with the given validated result.

    `payload` must be a ParseResult payload (or the `.payload` of one); it is validated
    again here so a caller cannot persist something the contract would reject.
    """
    result = payload.payload if hasattr(payload, 'payload') else payload
    validated = parse_contract.validate_parse_result(result).payload
    blocks = validated['blocks']
    cur.execute('DELETE FROM document_contract_blocks WHERE document_id=%s', (document_id,))
    cur.execute('DELETE FROM document_block_relations WHERE document_id=%s', (document_id,))
    locators = 0
    for block in blocks:
        cur.execute(BLOCK_INSERT, (block['block_id'], document_id, validated['contract_version'],
                                   block['block_type'], block['text'], block['reading_order'],
                                   Json(block.get('native_attributes') or {}),
                                   Json(block['locator']), block['content_hash']))
        locators += 1
    relations = 0
    for relation in validated['relations']:
        cur.execute(RELATION_INSERT, (relation_id(document_id, relation), document_id,
                                      relation['source_block_id'], relation['target_block_id'],
                                      relation['relation_type'], relation['state'],
                                      relation['recovery_method'], relation['evidence']))
        relations += 1
    return {'document_id': document_id, 'contract_version': validated['contract_version'],
            'parse_status': validated['parse_status'], 'blocks': len(blocks),
            'locators': locators, 'relations': relations}


def read_result(document_id, dsn, *, limit=2000):
    """Read back the stored contract blocks and relations for one document."""
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT block_id, contract_version, block_type, text, reading_order,
            native_attributes, locator, content_hash FROM document_contract_blocks
            WHERE document_id=%s ORDER BY reading_order LIMIT %s''', (document_id, limit))
        blocks = [dict(row) for row in cur.fetchall()]
        cur.execute('''SELECT relation_id, source_block_id, target_block_id, relation_type,
            relation_state, recovery_method, evidence FROM document_block_relations
            WHERE document_id=%s ORDER BY relation_id''', (document_id,))
        relations = [dict(row) for row in cur.fetchall()]
    return {'document_id': document_id, 'blocks': blocks, 'relations': relations,
            'block_count': len(blocks), 'relation_count': len(relations)}


MAX_LOCATOR_QUERY = 512
MAX_RELATION_QUERY = MAX_LOCATOR_QUERY


def read_locators(block_ids, dsn):
    """Validated locators for the given block ids, keyed by id.

    Only rows written by a contract-validated parse exist here, so a missing id means
    "no validated locator" and must not be filled in by the caller. Bounded to
    `MAX_LOCATOR_QUERY` ids so a citation cannot ask for an unbounded lookup.
    """
    ids = sorted({str(value) for value in (block_ids or []) if value})
    if not ids:
        return {}
    if len(ids) > MAX_LOCATOR_QUERY:
        raise ValueError('CONTRACT_LOCATOR_QUERY_TOO_LARGE')
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT block_id, locator, contract_version FROM document_block_locators
            WHERE block_id=ANY(%s)''', (ids,))
        return {row['block_id']: {'locator': row['locator'], 'contract_version': row['contract_version']}
                for row in cur.fetchall()}


def read_relations(block_ids, dsn):
    """Validated relations touching the given block ids, grouped by block id.

    A relation is stored only when the parse contract validated it (evidence is required and
    a self-reference is refused), so only stored rows are returned and a block without rows
    simply has no key here — nothing is derived to fill a gap. An id matches whichever end of
    the relation it is, and `direction` records that role ('source' when the queried block is
    the source, 'target' when it is the target) while `peer_block_id` is the other end. The
    name differs from the locator payload on purpose: there `block_id` is the cited block,
    here it is the far end of the relation, and conflating the two would mislabel evidence. Bounded to
    `MAX_RELATION_QUERY` ids so a citation cannot ask for an unbounded lookup.
    """
    ids = sorted({str(value) for value in (block_ids or []) if value})
    if not ids:
        return {}
    if len(ids) > MAX_RELATION_QUERY:
        raise ValueError('CONTRACT_RELATION_QUERY_TOO_LARGE')
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT relation_id, document_id, source_block_id, target_block_id,
            relation_type, relation_state, recovery_method, evidence
            FROM document_block_relations
            WHERE source_block_id=ANY(%s) OR target_block_id=ANY(%s)
            ORDER BY relation_id''', (ids, ids))
        rows = cur.fetchall()
    grouped = {}
    wanted = set(ids)
    for row in rows:
        stored = {key: row[key] for key in ('relation_id', 'document_id', 'relation_type',
                                            'relation_state', 'recovery_method', 'evidence')}
        if row['source_block_id'] in wanted:
            grouped.setdefault(row['source_block_id'], []).append(
                {**stored, 'direction': 'source', 'peer_block_id': row['target_block_id']})
        if row['target_block_id'] in wanted:
            grouped.setdefault(row['target_block_id'], []).append(
                {**stored, 'direction': 'target', 'peer_block_id': row['source_block_id']})
    return grouped
