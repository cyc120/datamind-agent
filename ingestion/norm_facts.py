"""Labelled normative-clause extraction from the frozen cross-source corpus.

This module is a **read-only parser**, not a generator: it reads the blocks already produced by the
repository's own ingestion entry points — :func:`ingestion.ingest.parse_pdf` for native PDFs and
:func:`ingestion.office.parse_docx` for DOCX — and turns the labelled lines of one clause into one
structured record. A field that is absent stays absent; the original text and the source locator
are kept next to every record, so each extracted value can be re-checked by hand.

The label discipline mirrors :mod:`ingestion.industrial_facts` (one labelled line per field, the
first colon wins, an embedded label inside a value is reported as *ambiguous* instead of being
split). The difference is vocabulary and granularity: one document holds many clauses, so a clause
record is a maximal run of labelled lines that contains a ``条款编号``.

Why a separate vocabulary: the *normative* corpus states a rule (指标 / 比较方向 / 阈值 / 单位 /
版次 / 生效日期 / 失效日期 / 适用对象) while the industrial demo states one工艺要求 per page. Both
are the same "one labelled line per field" shape, so the same parser pattern applies.

Nothing here writes to the database and nothing here invents a number.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

PARSER_VERSION = 'norm-labelled-v1'

#: label -> the record field it fills. Only the first separator is consumed, so an ISO timestamp
#: such as ``2026-09-01T00:00:00+08:00`` keeps its own colons.
FIELD_LABELS = {
    '文档编号': 'document_code',
    '文档名称': 'document_title',
    '版本': 'document_version',
    '适用范围': 'scope',
    '生效日期': 'valid_from',
    '失效日期': 'valid_to',
    '条款编号': 'clause_id',
    '条款标题': 'clause_title',
    '指标': 'metric',
    '比较方向': 'op',
    '阈值': 'limit',
    '单位': 'unit',
    '版次': 'applies_to_version',
    '适用对象': 'entity_label',
    '条款原文': 'clause_text',
    # A 质检核验记录 states a measured value instead of a threshold; the same label discipline
    # carries it, so a document-side numeric claim needs no second parser.
    '实测值': 'value',
    '测量编号': 'measurement_id',
    '批次编号': 'batch_id',
    '报告编号': 'report_id',
    '测量时点': 'instant',
    '报告结论': 'verdict',
    '审批状态': 'approval_status',
    '来源文件': 'source_file',
}

LABELS = set(FIELD_LABELS)
#: A new clause record starts at this label when the current run already holds one.
START_LABELS = {'条款编号'}
#: Labels that never become part of a clause record on their own.
DOCUMENT_LABELS = {'文档编号', '文档名称', '版本', '适用范围', '生效日期', '失效日期'}

_LABEL_PATTERN = '|'.join('\\s*'.join(re.escape(char) for char in label)
                          for label in sorted(LABELS, key=len, reverse=True))
EMBEDDED_FIELD = re.compile(r'(?:' + _LABEL_PATTERN + r')\s*[：:]')
_ISO = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?')
_NUMBER = re.compile(r'^[+-]?\d+(?:\.\d+)?$')


class NormFactsError(ValueError):
    """A corpus document could not be parsed; ``code`` is the stable reason."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code + (':' + detail if detail else ''))
        self.code = code
        self.detail = detail


def labelled_value(text: str):
    """``(label, value)`` for one labelled line, else ``None``.

    The split happens at the *first* separator only; a label is matched after whitespace inside the
    label is removed (PDF text extraction inserts stray spaces inside CJK labels).
    """
    parts = re.split(r'[：:]', str(text or ''), maxsplit=1)
    if len(parts) != 2:
        return None
    label = re.sub(r'\s+', '', parts[0])
    if label not in FIELD_LABELS:
        return None
    return label, parts[1].strip()


def normalize_field(field: str, value: str):
    """Deterministic per-field normalization; never guesses a missing value."""
    text = str(value).strip()
    if field == 'valid_to' and text in ('无', 'None', '-', ''):
        return None
    if field == 'limit' or field == 'value':
        if not _NUMBER.match(text):
            raise NormFactsError('FIELD_NOT_NUMERIC', f'{field}={text!r}')
        return text
    return text or None


def extract_document(blocks, *, document_id, source_sha256):
    """Parse one document's blocks into ``(metadata, clauses, errors)``.

    ``blocks`` are ``{text, page_no, block_id}`` (the shape both ingestion entries already yield).
    A clause record is a maximal run of consecutive labelled blocks containing a ``条款编号``;
    blocks that are not labelled terminate the run and are ignored as prose.
    """
    metadata, clauses, errors = {}, [], []
    current: list[dict] = []

    def flush():
        nonlocal current
        run = current
        current = []
        if not run:
            return
        fields, duplicates, ambiguous = {}, [], []
        for item in run:
            pair = labelled_value(item['text'])
            if pair is None:
                continue
            label, raw = pair
            field = FIELD_LABELS[label]
            if EMBEDDED_FIELD.search(raw):
                ambiguous.append(label)
            if field in fields:
                duplicates.append(label)
            fields[field] = raw
        if 'clause_id' not in fields:
            target = metadata
        else:
            target = None
        if target is not None:
            for field, raw in fields.items():
                try:
                    target[field] = normalize_field(field, raw)
                except NormFactsError as exc:
                    errors.append({'document_id': document_id, 'field': field, 'error': exc.code})
            return
        record = {'document_id': document_id, 'source_sha256': source_sha256}
        for field, raw in fields.items():
            try:
                record[field] = normalize_field(field, raw)
            except NormFactsError as exc:
                errors.append({'document_id': document_id, 'clause_id': fields.get('clause_id'),
                               'field': field, 'error': exc.code})
                record[field] = None
        record['duplicates'] = duplicates
        record['ambiguous'] = ambiguous
        record['page_no'] = run[0].get('page_no')
        record['block_id'] = run[0].get('block_id')
        record['text'] = '\n'.join(item['text'] for item in run)
        record['parser_version'] = PARSER_VERSION
        clauses.append(record)

    for block in blocks:
        text = str(block.get('text') or '')
        pair = labelled_value(text)
        if pair is None:
            flush()
            continue
        label = pair[0]
        if label in START_LABELS and current:
            flush()
        current.append(block)
    flush()
    return metadata, clauses, errors


# ---------------------------------------------------------------- document -> blocks

def pdf_blocks(path: Path, document_id: str):
    """Native-PDF blocks via the repository's own PDF parser (page images go to a temp dir).

    ``ingest.parse_pdf`` also rasterises pages for the UI evidence locator. That side effect is
    redirected to a throw-away directory here: a read-only evaluation must not drop new artifacts
    into ``data/object_store``.
    """
    import shutil
    import tempfile
    from ingestion import ingest
    scratch = Path(tempfile.mkdtemp(prefix='norm-pdf-'))
    previous = ingest.PAGE_IMG_DIR
    try:
        ingest.PAGE_IMG_DIR = scratch
        blocks, page_count = ingest.parse_pdf(Path(path), document_id)
    finally:
        ingest.PAGE_IMG_DIR = previous
        shutil.rmtree(scratch, ignore_errors=True)
    return [{'block_id': block.get('block_id'), 'page_no': block.get('page_no'),
             'text': block.get('text') or '', 'block_type': block.get('block_type')}
            for block in blocks], page_count


def docx_blocks(path: Path, document_id: str):
    """DOCX blocks via the repository's own office parser (paragraphs and tables)."""
    from ingestion import office
    result = office.parse_docx(Path(path), document_id=document_id, source_version=1)
    return [{'block_id': block.get('block_id'), 'page_no': None,
             'text': block.get('text') or '', 'block_type': block.get('block_type')}
            for block in result.get('blocks') or []], None


def document_blocks(path, document_id=None):
    """``(document_id, blocks, page_count, sha256)`` for one corpus file, by suffix."""
    path = Path(path)
    if not path.is_file():
        raise NormFactsError('CORPUS_FILE_MISSING', str(path))
    doc_id = document_id or path.stem
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        blocks, pages = pdf_blocks(path, doc_id)
    elif suffix == '.docx':
        blocks, pages = docx_blocks(path, doc_id)
    else:
        raise NormFactsError('CORPUS_FORMAT_UNSUPPORTED', suffix)
    return doc_id, blocks, pages, digest


# ---------------------------------------------------------------- corpus -> engine blocks

def corpus_records(paths):
    """Parse every corpus file into ``(documents, clauses, errors)`` (read-only)."""
    documents, clauses, errors = [], [], []
    for path in paths:
        doc_id, blocks, pages, digest = document_blocks(path)
        metadata, found, problems = extract_document(blocks, document_id=doc_id,
                                                     source_sha256=digest)
        documents.append({'document_id': doc_id, 'path': str(path), 'source_sha256': digest,
                          'page_count': pages, 'block_count': len(blocks),
                          'title': metadata.get('document_title') or doc_id,
                          'document_code': metadata.get('document_code'),
                          'document_version': metadata.get('document_version'),
                          'scope': metadata.get('scope'), 'valid_from': metadata.get('valid_from'),
                          'valid_to': metadata.get('valid_to'), 'metadata': metadata,
                          'clauses': len(found)})
        for record in found:
            record['title'] = metadata.get('document_title') or doc_id
            record['document_code'] = metadata.get('document_code')
            record['document_level_version'] = metadata.get('document_version')
            clauses.append(record)
        errors.extend(problems)
    return documents, clauses, errors


def _block_for(record, document):
    """One engine block for one clause / one document-side numeric claim.

    The engine's ``extract_clause`` reads the structured labels when ``metric`` and ``limit`` are
    present and falls back to its own clause regex otherwise; both readings are measured by the
    evaluation harness, so a label that stopped parsing would be visible.
    """
    block = {'document_id': record['document_id'],
             'title': record.get('title') or record['document_id'],
             'collection': 'knowledge_sources',
             'document_version': record.get('document_version') or record.get('document_level_version'),
             'source_sha256': record.get('source_sha256'),
             'chunk_id': record['clause_id'],
             'block_id': record.get('block_id'),
             'page_no': record.get('page_no'),
             'text': record.get('text') or '',
             'clause_id': record['clause_id'],
             'metric': record.get('metric'),
             'applies_to_version': record.get('applies_to_version'),
             'valid_from': record.get('valid_from') or document.get('valid_from'),
             'valid_to': record.get('valid_to') if record.get('valid_to') is not None
             else document.get('valid_to'),
             'entity_label': record.get('entity_label') or document.get('scope')}
    if record.get('limit') is not None:
        block.update({'limit': record['limit'], 'op': record.get('op'), 'unit': record.get('unit')})
        # The explicit clause object is the engine's documented "already parsed" form: it maps the
        # document's own comparison wording (``超过`` / ``不得低于``) onto the violation operator
        # through the engine's own ``_OP_MAP``, and it carries the quoted clause sentence as the
        # evidence text. The raw wording stays in ``text``/``op`` above for auditability.
        block['clause'] = {'clause_id': record['clause_id'], 'metric': record.get('metric'),
                           'op': record.get('op'), 'limit': record['limit'],
                           'unit': record.get('unit'),
                           'entity_label': record.get('entity_label'),
                           'valid_from': record.get('valid_from'),
                           'valid_to': record.get('valid_to'),
                           'quoted': record.get('clause_text') or record.get('text') or ''}
    if record.get('value') is not None:
        # A 质检核验记录 states a measured value: it becomes a document-side *numeric claim*
        # (doc_block with value+metric), which is exactly what the conflict gate compares.
        block.update({'value': record['value'], 'unit': record.get('unit')})
        block['measurement_id'] = record.get('measurement_id')
        block['recorded_at'] = record.get('instant')
    return block


def select(records, documents, *, document_ids=None, must_tokens=(), any_tokens=(), max_blocks=24):
    """Deterministic corpus selection: document ids plus required/alternative verbatim tokens.

    This is the corpus analogue of the retrieval anchor in ``cross_source.live_doc_evidence``: it
    is deliberately *not* a similarity search, so a case's evidence set is reproducible and every
    dropped record is counted by the caller. ``must_tokens`` must all appear verbatim in the
    record; ``any_tokens`` requires at least one of them.
    """
    wanted_docs = set(document_ids or ())
    required = [str(token) for token in (must_tokens or ()) if str(token)]
    alternatives = [str(token) for token in (any_tokens or ()) if str(token)]
    by_id = {document['document_id']: document for document in documents}
    kept, dropped = [], []
    for record in records:
        document = by_id.get(record['document_id'], {})
        text = record.get('text') or ''
        if wanted_docs and record['document_id'] not in wanted_docs:
            dropped.append(record)
            continue
        if any(token not in text for token in required):
            dropped.append(record)
            continue
        if alternatives and not any(token in text for token in alternatives):
            dropped.append(record)
            continue
        kept.append(_block_for(record, document))
    kept = kept[:max_blocks]
    return kept, dropped


def evidence_blocks(paths, *, document_ids=None, must_tokens=(), any_tokens=(), max_blocks=24):
    """Engine-ready document evidence container consumed by :func:`datamind.cross_source.plan`."""
    documents, clauses, errors = corpus_records(paths)
    blocks, dropped = select(clauses, documents, document_ids=document_ids,
                             must_tokens=must_tokens, any_tokens=any_tokens,
                             max_blocks=max_blocks)
    return {'source_ref': 'knowledge_sources',
            'blocks': blocks, 'present': bool(blocks),
            'reason': None if blocks else 'DOC_EVIDENCE_EMPTY',
            'documents': sorted({document['document_id'] for document in documents}),
            'selected_document_ids': sorted({block['document_id'] for block in blocks}),
            'dropped_count': len(dropped), 'clause_count': len(clauses),
            'parse_errors': errors, 'parser_version': PARSER_VERSION}
