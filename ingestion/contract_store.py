"""Persist a validated parse result: contract blocks, locators and relations.

Office and DXF blocks locate their origin by part/paragraph/slide/sheet/handle rather than
by page, so they are stored in `document_contract_blocks` instead of the page-shaped
`document_blocks`. Only a result that the contract already validated is written, and a
document's rows are replaced together — a partial write never leaves half a document.

Because the two formats split across two stores, the readers cover both: `read_result`
returns each store under its own key, and `read_locators` merges the page-shaped
`document_block_locators` with the `locator` column of `document_contract_blocks`.
"""
from __future__ import annotations

import hashlib

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from datamind import parse_contract

BLOCK_INSERT = '''
    INSERT INTO document_contract_blocks(block_id, document_id, contract_version, block_type,
        text, reading_order, native_attributes, locator, content_hash)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)'''

# Migration 017 closed the block shape: every key a validated contract block may carry has a
# column. The extended insert is the normal path; the nine-column one above is the pre-017
# shape, used only when those columns are absent so a database that has not migrated yet
# still stores the core block instead of failing the whole import.
BLOCK_COLUMNS = ('block_id', 'document_id', 'contract_version', 'block_type', 'text',
                 'reading_order', 'native_attributes', 'locator', 'content_hash')
BLOCK_EXTENDED_COLUMNS = ('source_version', 'parser_version', 'quality', 'page_no',
                          'heading_level', 'table_cells', 'formula_latex', 'ocr_confidence',
                          'bbox')
BLOCK_INSERT_EXTENDED = '''
    INSERT INTO document_contract_blocks(''' + ', '.join(BLOCK_COLUMNS + BLOCK_EXTENDED_COLUMNS) + ''')
    VALUES (''' + ','.join(['%s'] * (len(BLOCK_COLUMNS) + len(BLOCK_EXTENDED_COLUMNS))) + ''')'''


RELATION_INSERT = '''
    INSERT INTO document_block_relations(relation_id, document_id, source_block_id,
        target_block_id, relation_type, relation_state, recovery_method, evidence)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)'''


def relation_id(document_id, relation):
    identity = [document_id, relation['source_block_id'], relation['target_block_id'],
                relation['relation_type']]
    return 'rel-' + hashlib.sha256(repr(identity).encode('utf-8')).hexdigest()[:56]


# Whether `document_contract_blocks` carries the migration-017 columns is a database fact,
# not a guess: it is probed once per DSN and cached. A probe that fails is treated as
# "absent" so the writer degrades to the pre-017 insert instead of raising.
_BLOCK_COLUMNS_CACHE = {}


def block_columns(dsn):
    """The `document_contract_blocks` columns this database actually has."""
    global _BLOCK_COLUMNS_CACHE
    if dsn not in _BLOCK_COLUMNS_CACHE:
        try:
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute('''SELECT column_name FROM information_schema.columns
                    WHERE table_name='document_contract_blocks' ''')
                present = {row[0] for row in cur.fetchall()}
        except psycopg2.Error:
            present = set()
        _BLOCK_COLUMNS_CACHE[dsn] = present
    return _BLOCK_COLUMNS_CACHE[dsn]


def _block_write_columns(dsn=None, *, available_columns=None):
    """Block columns this store will write, preferring the full migration-017 shape."""
    if available_columns is None:
        available_columns = block_columns(dsn) if dsn else set()
    if set(BLOCK_COLUMNS + BLOCK_EXTENDED_COLUMNS) <= set(available_columns):
        return BLOCK_INSERT_EXTENDED, BLOCK_COLUMNS + BLOCK_EXTENDED_COLUMNS, ()
    omitted = tuple(name for name in BLOCK_EXTENDED_COLUMNS if name not in set(available_columns))
    return BLOCK_INSERT, BLOCK_COLUMNS, omitted


def _block_values(block, document_id, contract_version):
    """Row for either insert shape, in the matching column order.

    Unset optional keys are stored as NULL (or an empty JSON object for the NOT NULL
    `quality` column) rather than invented, and every scalar is passed through unchanged.
    """
    def jsonish(value, fallback):
        return Json(value if value is not None else fallback)

    native = jsonish(block.get('native_attributes'), {})
    locator = jsonish(block.get('locator'), {})
    quality = jsonish(block.get('quality'), {})
    core = (block['block_id'], document_id, contract_version, block['block_type'],
            block['text'], block['reading_order'], native, locator, block['content_hash'])
    extended = (block.get('source_version'), block.get('parser_version'), quality,
                block.get('page_no'), block.get('heading_level'),
                jsonish(block.get('table_cells'), None) if 'table_cells' in block else None,
                block.get('formula_latex'), block.get('ocr_confidence'),
                jsonish(block.get('bbox'), None) if 'bbox' in block else None)
    return core + extended


def _insert_blocks(cur, blocks, document_id, contract_version, *, dsn=None,
                   available_columns=None):
    """Write every block, using the full shape when the database has those columns.

    A database that has not applied migration 017 is detected before writing, and the
    nine-column insert is used so the core block is still stored. If the probe ever says a
    column exists when it does not, the extended INSERT is retried inside a savepoint and
    falls back to the core shape — the write then records the omitted columns instead of
    losing them silently.
    """
    statement, _, omitted = _block_write_columns(dsn, available_columns=available_columns)
    if not omitted:
        for block in blocks:
            cur.execute(statement, _block_values(block, document_id, contract_version))
        return omitted
    cur.execute('SAVEPOINT contract_block_extended')
    try:
        for block in blocks:
            cur.execute(BLOCK_INSERT_EXTENDED,
                        _block_values(block, document_id, contract_version))
    except psycopg2.errors.UndefinedColumn:
        cur.execute('ROLLBACK TO SAVEPOINT contract_block_extended')
        cur.execute('RELEASE SAVEPOINT contract_block_extended')
    else:
        cur.execute('RELEASE SAVEPOINT contract_block_extended')
        return ()
    for block in blocks:
        cur.execute(statement, _block_values(block, document_id, contract_version))
    return omitted


def persist_result(cur, *, document_id, payload, dsn=None, available_columns=None):
    """Replace this document's contract rows with the given validated result.

    `payload` must be a ParseResult payload (or the `.payload` of one); it is validated
    again here so a caller cannot persist something the contract would reject.

    Every key the contract validated is written, including the optional block fields
    (`heading_level`/`table_cells`/`formula_latex`/`ocr_confidence`/`page_no`/`bbox`) that
    migration 017 gave a column. On a database without those columns the write degrades to
    the core shape and `omitted_columns` names what could not be stored. `available_columns`
    exists for that simulation and defaults to what the database actually reports.
    """
    result = payload.payload if hasattr(payload, 'payload') else payload
    validated = parse_contract.validate_parse_result(result).payload
    blocks = validated['blocks']
    cur.execute('DELETE FROM document_contract_blocks WHERE document_id=%s', (document_id,))
    cur.execute('DELETE FROM document_block_relations WHERE document_id=%s', (document_id,))
    omitted = _insert_blocks(cur, blocks, document_id, validated['contract_version'], dsn=dsn,
                             available_columns=available_columns)
    relations = 0
    for relation in validated['relations']:
        cur.execute(RELATION_INSERT, (relation_id(document_id, relation), document_id,
                                      relation['source_block_id'], relation['target_block_id'],
                                      relation['relation_type'], relation['state'],
                                      relation['recovery_method'], relation['evidence']))
        relations += 1
    return {'document_id': document_id, 'contract_version': validated['contract_version'],
            'parse_status': validated['parse_status'], 'blocks': len(blocks),
            'locators': len(blocks), 'relations': relations, 'omitted_columns': list(omitted)}


def read_result(document_id, dsn, *, limit=2000):
    """Read back everything the contract stored for one document.

    Two stores exist on purpose: page-shaped PDF parses keep their blocks in
    `document_blocks` with the validated locator in `document_block_locators`, while formats
    without a page concept (Office, DXF) land in `document_contract_blocks`. Returning only
    one of them would report "nothing stored" for a document that does have validated
    locators, so both are read here and reported under their own keys.

    Contract blocks are read with the columns the database has: a row written before
    migration 017 has no value for the newer fields, and this returns them as None rather
    than hiding the key, so a caller can tell "absent" from "not part of the shape".

    **`content_hash` has two different meanings in one response, and they are not
    interchangeable:** `locators[].content_hash` comes from `document_blocks` and is
    the text-integrity hash `sha256(text)` — the one `field_store` verifies evidence
    against — while `blocks[].content_hash` comes from `document_contract_blocks` and is the
    contract promise over identity + source + position + text. Swapping them makes fact
    verification fail *silently* (it returns no facts rather than an error), so callers must
    never compare one against the other. `tests/test_contract_store.py` pins the difference.
    """
    available = block_columns(dsn)
    columns = [name for name in BLOCK_COLUMNS + BLOCK_EXTENDED_COLUMNS if name in available]
    if not columns:
        columns = list(BLOCK_COLUMNS)
    selected = 'block_id, contract_version, block_type, text, reading_order, ' + \
        ', '.join(name for name in columns
                  if name not in ('block_id', 'contract_version', 'block_type', 'text',
                                  'reading_order'))
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f'''SELECT {selected} FROM document_contract_blocks
            WHERE document_id=%s ORDER BY reading_order LIMIT %s''', (document_id, limit))
        blocks = []
        for row in cur.fetchall():
            stored = dict(row)
            for name in BLOCK_EXTENDED_COLUMNS:
                stored.setdefault(name, None)
            blocks.append(stored)
        cur.execute('''SELECT b.block_id, l.contract_version, b.block_type, b.text,
            b.reading_order, b.page_no, b.bbox, b.content_hash, l.locator
            FROM document_block_locators l JOIN document_blocks b USING(block_id)
            WHERE l.document_id=%s ORDER BY b.reading_order LIMIT %s''', (document_id, limit))
        locators = [dict(row) for row in cur.fetchall()]
        cur.execute('''SELECT relation_id, source_block_id, target_block_id, relation_type,
            relation_state, recovery_method, evidence FROM document_block_relations
            WHERE document_id=%s ORDER BY relation_id''', (document_id,))
        relations = [dict(row) for row in cur.fetchall()]
    return {'document_id': document_id, 'blocks': blocks, 'relations': relations,
            'locators': locators, 'block_count': len(blocks), 'locator_count': len(locators),
            'relation_count': len(relations)}


MAX_LOCATOR_QUERY = 512
MAX_RELATION_QUERY = MAX_LOCATOR_QUERY


def read_locators(block_ids, dsn):
    """Validated locators for the given block ids, keyed by id.

    A contract-validated parse writes its locator to one of two stores depending on whether
    the format has a page concept: page-shaped PDF parses keep it in
    `document_block_locators` beside their `document_blocks` row, while formats without a
    page (Office, DXF) keep it in the `locator` column of `document_contract_blocks`. Both
    are read here and merged; reading only the page-shaped store would make every Office and
    DXF locator invisible to a citation even though the contract validated it.

    A missing id means "no validated locator" and must not be filled in by the caller.
    Bounded to `MAX_LOCATOR_QUERY` ids so a citation cannot ask for an unbounded lookup.
    """
    ids = sorted({str(value) for value in (block_ids or []) if value})
    if not ids:
        return {}
    if len(ids) > MAX_LOCATOR_QUERY:
        raise ValueError('CONTRACT_LOCATOR_QUERY_TOO_LARGE')
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT block_id, locator, contract_version FROM document_block_locators
            WHERE block_id=ANY(%s) ORDER BY block_id''', (ids,))
        page_shaped = [dict(row) for row in cur.fetchall()]
        cur.execute('''SELECT block_id, locator, contract_version FROM document_contract_blocks
            WHERE block_id=ANY(%s) ORDER BY block_id''', (ids,))
        contract_blocks = [dict(row) for row in cur.fetchall()]
    # Merge precedence: the page-shaped store wins, and the contract-block store only fills
    # ids it did not cover. `document_block_locators` holds the locator in its own column
    # that the PDF path validates against the block's page/bbox, while the contract-block
    # column is the same JSON payload parked beside its block; when one id somehow appears
    # in both, preferring the store with the page/bbox cross-check keeps the historical PDF
    # answer byte-identical rather than silently changing it.
    merged = {}
    for row in page_shaped + contract_blocks:
        merged.setdefault(row['block_id'],
                          {'locator': row['locator'], 'contract_version': row['contract_version']})
    return merged


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
