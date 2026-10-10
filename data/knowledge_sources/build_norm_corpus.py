#!/usr/bin/env python
"""Deterministic builder for the frozen normative cross-source corpus.

The round-2 live evaluation showed the real knowledge base cannot support P2/P3/P4: of the 169
public chunks exactly one is even regex-extractable as ``metric + operator + number + unit``, and
it names no queryable metric. This script builds the missing artifact — a *specification corpus*
whose clauses carry a threshold, a unit, an applicable revision (or validity window) and an
applicable scope, and whose thresholds are chosen so that they can be cross-checked against the
real demo databases.

Two rules are never broken here:

* **nothing is invented for the data side** — every threshold maps to a real column of
  ``demo_retail`` / ``demo_industrial`` and the evaluation harness compares it against real rows;
* **the 质检核验记录 is transcribed, not authored** — its measured values are parsed out of the
  repository's existing ``data/industrial_demo/industrial_demo_v1.pdf`` with the repository's own
  labelled-field parser, and the applicable process revision of each record is read from
  ``demo_industrial.production_batches`` (read-only). The corpus therefore *carries* the
  discrepancy that already exists between that PDF and the database instead of creating one.

Usage::

    .venv/bin/python data/knowledge_sources/build_norm_corpus.py            # write corpus+manifest
    .venv/bin/python data/knowledge_sources/build_norm_corpus.py --check    # determinism + parsing
    .venv/bin/python data/knowledge_sources/build_norm_corpus.py --report tmp/norm_corpus.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CORPUS_DIR = Path(__file__).resolve().parent
MANIFEST = CORPUS_DIR / 'manifest.json'
REAL_INDUSTRIAL_PDF = ROOT / 'data' / 'industrial_demo' / 'industrial_demo_v1.pdf'
PDF_CREATED = 'D:20260101000000Z'
META_DSN = ('host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user '
            'password=readonly_dev')

#: Fixed document-level dates. They are the *rule* validity windows, so every one of them covers
#: the real rows the corresponding case queries.
RETAIL_FROM = '2023-01-01T00:00:00+08:00'
A102_V1_FROM, A102_V1_TO = '2026-08-01T00:00:00+08:00', '2026-09-01T00:00:00+08:00'
A102_V2_FROM = '2026-09-01T00:00:00+08:00'
A102_V3_FROM, A102_V3_TO = '2026-09-15T00:00:00+08:00', '2026-09-20T00:00:00+08:00'
A103_V1_FROM = '2026-08-01T00:00:00+08:00'
DRAFT_FROM = '2027-01-01T00:00:00+08:00'


def clause(clause_id, title, metric, op, limit, unit, version, valid_from, valid_to, scope,
           text, extra=None):
    return {'clause_id': clause_id, 'clause_title': title, 'metric': metric, 'op': op,
            'limit': limit, 'unit': unit, 'applies_to_version': version,
            'valid_from': valid_from, 'valid_to': valid_to, 'entity_label': scope,
            'clause_text': text, 'extra': dict(extra or {})}


def plain_clause(clause_id, title, version, valid_from, valid_to, scope, text):
    """An explanatory material: a clause with no threshold (it must stay a plain doc block)."""
    return {'clause_id': clause_id, 'clause_title': title, 'metric': None, 'op': None,
            'limit': None, 'unit': None, 'applies_to_version': version, 'valid_from': valid_from,
            'valid_to': valid_to, 'entity_label': scope, 'clause_text': text, 'extra': {}}


DOCUMENTS = [
    {'name': 'DM-KF-2026-001_售后时效规范', 'code': 'DM-KF-2026-001',
     'title': '售后服务时效规范', 'version': '2026.01', 'scope': '全渠道销售订单退款处理',
     'valid_from': RETAIL_FROM, 'valid_to': None, 'format': 'pdf', 'clauses': [
         clause('DM-KF-2026-001-6.1', '一般退款处理时长上限', '时长', '超过', '7', '天',
                'DM-KF-2026-001', RETAIL_FROM, None, '全渠道',
                '时长：退款处理时长不得超过 7 天。'),
         clause('DM-KF-2026-001-6.2', '大件家电退款处理时长上限', '时长', '超过', '5', '天',
                'DM-KF-2026-001', RETAIL_FROM, None, '大件家电',
                '时长：大件家电退款处理时长不得超过 5 天。')]},
    {'name': 'DM-KF-2026-011_售后复核说明材料', 'code': 'DM-KF-2026-011',
     'title': '售后时效判定说明材料', 'version': '2026.01', 'scope': '全渠道销售订单退款处理',
     'valid_from': RETAIL_FROM, 'valid_to': None, 'format': 'docx', 'clauses': [
         plain_clause('DM-KF-2026-011-1.1', '退款时效判定流程说明', 'DM-KF-2026-011',
                      RETAIL_FROM, None, '全渠道',
                      '条款原文：本说明只解释退款时效的判定流程，不改变任何阈值；判定结果须与'
                      '《DM-KF-2026-001》第六条一致，适用于线上、门店与 APP 渠道。')]},
    {'name': 'DM-CH-2026-004_渠道业绩考核办法', 'code': 'DM-CH-2026-004',
     'title': '渠道业绩考核办法', 'version': '2026.01', 'scope': '全渠道销售业绩考核',
     'valid_from': RETAIL_FROM, 'valid_to': None, 'format': 'docx', 'clauses': [
         clause('DM-CH-2026-004-4.1', '渠道销售金额下限', '金额', '不得低于', '2000000', '元',
                'DM-CH-2026-004', RETAIL_FROM, None, '渠道',
                '金额：各渠道销售金额不得低于 2000000 元。'),
         clause('DM-CH-2026-004-4.2', '线上直销渠道销售金额下限', '金额', '不得低于', '2500000',
                '元', 'DM-CH-2026-004', RETAIL_FROM, None, '线上商城',
                '金额：线上商城渠道销售金额不得低于 2500000 元。')]},
    {'name': 'DM-VIP-2026-002_会员等级权益标准', 'code': 'DM-VIP-2026-002',
     'title': '会员等级权益标准', 'version': '2026.01', 'scope': '会员等级累计消费额门槛',
     'valid_from': RETAIL_FROM, 'valid_to': None, 'format': 'docx', 'clauses': [
         clause('DM-VIP-2026-002-3.1', '金卡会员累计消费额门槛', '金额', '不得低于', '900000',
                '元', 'DM-VIP-2026-002', RETAIL_FROM, None, '金卡',
                '金额：金卡会员累计消费金额不得低于 900000 元。'),
         clause('DM-VIP-2026-002-3.2', '钻石会员累计消费额门槛', '金额', '不得低于', '800000',
                '元', 'DM-VIP-2026-002', RETAIL_FROM, None, '钻石',
                '金额：钻石会员累计消费金额不得低于 800000 元。')]},
    {'name': 'DM-PR-2026-007_商品定价与毛利规范', 'code': 'DM-PR-2026-007',
     'title': '商品定价与毛利规范', 'version': '2026.01', 'scope': '在售商品定价与品类毛利',
     'valid_from': RETAIL_FROM, 'valid_to': None, 'format': 'pdf', 'clauses': [
         clause('DM-PR-2026-007-5.1', '品类最低毛利比例下限', '比例', '低于', '30', '%',
                'DM-PR-2026-007', RETAIL_FROM, None, '商品品类',
                '比例：品类毛利比例低于 30 % 时须复核定价。'),
         clause('DM-PR-2026-007-5.2', '单品定价下限', '金额', '低于', '100', '元',
                'DM-PR-2026-007', RETAIL_FROM, None, '商品定价',
                '金额：单品定价低于 100 元时须报备。')]},
    {'name': 'DM-WH-2026-009_订单数量管控规范', 'code': 'DM-WH-2026-009',
     'title': '订单数量管控规范', 'version': '2026.01', 'scope': '订单明细商品件数管控',
     'valid_from': RETAIL_FROM, 'valid_to': None, 'format': 'docx', 'clauses': [
         clause('DM-WH-2026-009-2.1', '单笔订单商品件数上限', '数量', '超过', '2', '件',
                'DM-WH-2026-009', RETAIL_FROM, None, '订单明细',
                '数量：单笔订单商品件数不得超过 2 件。')]},
    {'name': 'GY-A102-V1_工艺温度规范', 'code': 'GY-A102-V1',
     'title': 'A102 工艺温度规范（V1）', 'version': 'A102-V1', 'scope': 'A102 零件工序 30 保温温度',
     'valid_from': A102_V1_FROM, 'valid_to': A102_V1_TO, 'format': 'pdf', 'clauses': [
         clause('GY-A102-V1-4.1', 'V1 保温温度上限', '温度', '超过', '860', 'degC',
                'A102-V1', A102_V1_FROM, A102_V1_TO, 'A102',
                '温度：保温温度上限不得超过 860 degC。')]},
    {'name': 'GY-A102-V2_工艺温度规范', 'code': 'GY-A102-V2',
     'title': 'A102 工艺温度规范（V2）', 'version': 'A102-V2', 'scope': 'A102 零件工序 30 保温温度',
     'valid_from': A102_V2_FROM, 'valid_to': None, 'format': 'pdf', 'clauses': [
         clause('GY-A102-V2-4.1', 'V2 保温温度上限', '温度', '超过', '850', 'degC',
                'A102-V2', A102_V2_FROM, None, 'A102',
                '温度：保温温度上限不得超过 850 degC。')]},
    {'name': 'GY-A102-V3_工艺温度规范', 'code': 'GY-A102-V3',
     'title': 'A102 工艺温度规范（V3）', 'version': 'A102-V3', 'scope': 'A102 零件工序 30 保温温度',
     'valid_from': A102_V3_FROM, 'valid_to': A102_V3_TO, 'format': 'docx', 'clauses': [
         clause('GY-A102-V3-4.1', 'V3 保温温度上限', '温度', '超过', '855', 'degC',
                'A102-V3', A102_V3_FROM, A102_V3_TO, 'A102',
                '温度：保温温度上限不得超过 855 degC。')]},
    {'name': 'GY-A102-DRAFT_工艺温度规范', 'code': 'GY-A102-DRAFT',
     'title': 'A102 工艺温度规范（草稿）', 'version': 'A102-DRAFT',
     'scope': 'A102 零件工序 30 保温温度', 'valid_from': DRAFT_FROM, 'valid_to': None,
     'format': 'docx', 'approval_status': '未批准', 'clauses': [
         clause('GY-A102-DRAFT-4.1', '草稿保温温度上限', '温度', '超过', '900', 'degC',
                'A102-DRAFT', DRAFT_FROM, None, 'A102',
                '温度：草稿版保温温度上限不得超过 900 degC。')]},
    {'name': 'GY-A103_工艺温度规范_华氏版', 'code': 'GY-A103-F',
     'title': 'A103 工艺温度规范（华氏标注）', 'version': 'A103-V1',
     'scope': 'A103 零件工序 30 保温温度', 'valid_from': A103_V1_FROM, 'valid_to': None,
     'format': 'docx', 'clauses': [
         clause('GY-A103-F-4.1', 'A103 保温温度上限（华氏）', '温度', '超过', '1544', 'degF',
                'A103-V1', A103_V1_FROM, None, 'A103',
                '温度：保温温度上限不得超过 1544 degF。'),
         clause('GY-A103-F-4.2', 'A103 保温温度预警线（华氏）', '温度', '超过', '1545', 'degF',
                'A103-V1', A103_V1_FROM, None, 'A103',
                '温度：保温温度预警线不得超过 1545 degF。')]},
    {'name': 'GY-A103-V2_工艺温度规范_未生效', 'code': 'GY-A103-V2',
     'title': 'A103 工艺温度规范（未生效）', 'version': 'A103-V2',
     'scope': 'A103 零件工序 30 保温温度', 'valid_from': '2030-01-01T00:00:00+08:00',
     'valid_to': None, 'format': 'docx', 'approval_status': '已批准但尚未生效（2030-01-01 起）',
     'clauses': [
         clause('GY-A103-V2-4.1', 'V2 保温温度上限（未生效）', '温度', '超过', '840', 'degC',
                'A103-V2', '2030-01-01T00:00:00+08:00', None, 'A103',
                '温度：保温温度上限不得超过 840 degC。')]},
    {'name': 'SM-GY-2026-001_工艺温度判定说明材料', 'code': 'SM-GY-2026-001',
     'title': '工艺温度判定说明材料', 'version': '2026.09',
     'scope': 'A102/A103 零件工序 30 保温温度判定', 'valid_from': A102_V1_FROM,
     'valid_to': None, 'format': 'docx', 'clauses': [
         plain_clause('SM-GY-2026-001-1.1', '保温温度判定流程说明', 'SM-GY-2026-001',
                      A102_V1_FROM, None, 'A102',
                      '条款原文：本说明用于解释 GY-A102-V2-4.1 与 GY-A103-F-4.1 的判定流程；'
                      'A102 与 A103 零件的实测值与规范值必须分别留痕，不得以单一来源默认裁定。')]},
]


# ---------------------------------------------------------------- rendering

def document_lines(document):
    """The exact labelled lines of one corpus file (one clause run per clause)."""
    lines = [f'DataMind 规范语料 · {document["title"]}',
             f'文档编号：{document["code"]}',
             f'文档名称：{document["title"]}',
             f'版本：{document["version"]}',
             f'适用范围：{document["scope"]}',
             f'生效日期：{document["valid_from"]}',
             f'失效日期：{document["valid_to"] or "无"}']
    if document.get('approval_status'):
        lines.append(f'审批状态：{document["approval_status"]}')
    for position, item in enumerate(document['clauses'], start=1):
        lines.append('')
        lines.append(f'第 {position} 条')
        lines.append(f'条款编号：{item["clause_id"]}')
        lines.append(f'条款标题：{item["clause_title"]}')
        if item.get('metric'):
            lines.append(f'指标：{item["metric"]}')
        if item.get('op'):
            lines.append(f'比较方向：{item["op"]}')
        if item.get('limit') is not None:
            lines.append(f'阈值：{item["limit"]}')
        if item.get('unit'):
            lines.append(f'单位：{item["unit"]}')
        if item.get('applies_to_version'):
            lines.append(f'版次：{item["applies_to_version"]}')
        lines.append(f'生效日期：{item["valid_from"]}')
        lines.append(f'失效日期：{item["valid_to"] or "无"}')
        if item.get('entity_label'):
            lines.append(f'适用对象：{item["entity_label"]}')
        for label, value in (item.get('extra') or {}).items():
            lines.append(f'{label}：{value}')
        lines.append(f'条款原文：{item["clause_text"]}')
    return lines


_PDF_WHITESPACE = b' \t\r\n\x0c\x00'


def _pdf_literal_end(data: bytes, start: int):
    """Index just past the balanced ``( ... )`` PDF string starting at ``start`` (or ``None``).

    PDF literal strings nest on unescaped parentheses and use ``\\`` to escape the next byte, so
    the closing ``)`` cannot be found by a naive scan.
    """
    depth, position = 0, start
    while position < len(data):
        char = data[position:position + 1]
        if char == b'\\':
            position += 2
            continue
        if char == b'(':
            depth += 1
        elif char == b')':
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def _pdf_id_span(data: bytes):
    """Byte span of the *trailer* ``/ID`` array, covering every string form PyMuPDF writes.

    This is the fix for the round-3 falsification. PyMuPDF draws a fresh random file id on every
    save and writes each element of the ``/ID`` array either as a hex string (``<...>``) or as a
    literal string (``(...)``, with ``\\``-escapes and a length that differs from the hex form).
    Measured on the 售后时效规范 document: 1 save in 30 used the literal form. The previous
    hex-only regex simply did not match that form and returned the bytes untouched, so a quarter
    of ``--check`` runs reported ``deterministic: false`` with a file length that differed by a
    byte or two. The search runs from the end of the file so it can only pick the trailer array.
    """
    index = data.rfind(b'/ID')
    while index != -1:
        position = index + 3
        while position < len(data) and data[position] in _PDF_WHITESPACE:
            position += 1
        if data[position:position + 1] == b'[':
            depth, cursor = 0, position
            while cursor < len(data):
                char = data[cursor:cursor + 1]
                if char == b'(':
                    cursor = _pdf_literal_end(data, cursor)
                    if cursor is None:
                        break
                    continue
                if char == b'[':
                    depth += 1
                elif char == b']':
                    depth -= 1
                    if depth == 0:
                        return index, cursor + 1
                cursor += 1
        index = data.rfind(b'/ID', 0, index)
    return None


def _normalize_pdf_id(data: bytes) -> bytes:
    """Write a content-derived trailer ``/ID`` into freshly generated PDF bytes.

    The id is a pure function of the bytes *excluding* the ``/ID`` array — so it cannot depend on
    which string form PyMuPDF happened to pick, nor on the byte length of that form. Because the
    trailer sits after the cross-reference table, replacing it keeps every recorded object offset
    valid. A missing array is a hard error: silently returning the random bytes is exactly the
    defect that made the frozen corpus non-reproducible.
    """
    span = _pdf_id_span(data)
    if span is None:
        raise RuntimeError('generated PDF has no trailer /ID array; refusing to emit a '
                           'non-reproducible corpus file')
    start, end = span
    stripped = data[:start] + data[end:]
    digest = hashlib.sha256(stripped).hexdigest().upper().encode('ascii')
    return data[:start] + b'/ID[<' + digest + b'><' + digest + b'>]' + data[end:]


def write_pdf(path: Path, lines):
    import pymupdf
    doc = pymupdf.open()
    doc.set_metadata({'title': path.stem, 'author': 'DataMind Agent',
                      'subject': '规范语料（自建，与演示库可交叉验证）',
                      'creator': 'data/knowledge_sources/build_norm_corpus.py',
                      'producer': 'PyMuPDF', 'creationDate': PDF_CREATED,
                      'modDate': PDF_CREATED})
    page = doc.new_page(width=595, height=842)
    y = 64
    for line in lines:
        if y > 792:
            page = doc.new_page(width=595, height=842)
            y = 64
        if line:
            page.insert_text((56, y), line, fontsize=11, fontname='china-s')
        y += 20
    data = _normalize_pdf_id(doc.tobytes(garbage=0, deflate=True))
    doc.close()
    path.write_bytes(data)


def _normalize_docx(data: bytes) -> bytes:
    """Rewrite the DOCX package with fixed ZIP entry timestamps.

    ``zipfile`` stamps every entry with the *current local time* when python-docx writes a
    paragraph, so two runs of the builder produced byte-different packages with identical
    contents. Pinning the entry timestamps makes the corpus reproducible; nothing inside the
    document is touched.
    """
    import io
    import zipfile
    source = zipfile.ZipFile(io.BytesIO(data))
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            item = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = info.compress_type
            item.external_attr = info.external_attr
            item.internal_attr = info.internal_attr
            item.create_system = info.create_system
            archive.writestr(item, source.read(info.filename))
    return target.getvalue()


def write_docx(path: Path, lines):
    import docx
    import io
    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    path.write_bytes(_normalize_docx(buffer.getvalue()))


def write_document(path: Path, document):
    lines = document_lines(document)
    if document['format'] == 'pdf':
        write_pdf(path, lines)
    else:
        write_docx(path, lines)
    return path


# ---------------------------------------------------------------- quality transcript

def _real_pdf_records():
    """Parse the repository's existing industrial PDF (labelled fields) — read-only."""
    from ingestion import ingest
    from ingestion.industrial_facts import labelled_value
    scratch = Path(tempfile.mkdtemp(prefix='norm-transcript-'))
    previous = ingest.PAGE_IMG_DIR
    try:
        ingest.PAGE_IMG_DIR = scratch
        blocks, _ = ingest.parse_pdf(REAL_INDUSTRIAL_PDF, 'industrial_demo_v1_transcript')
    finally:
        ingest.PAGE_IMG_DIR = previous
        shutil.rmtree(scratch, ignore_errors=True)
    records, current = [], []
    for block in blocks:
        text = block.get('text') or ''
        pair = labelled_value(text)
        if text.strip().startswith('质检记录'):
            if current:
                records.append(current)
            current = [text.strip()]
            continue
        if pair is None:
            continue
        if current:
            current.append(text)
    if current:
        records.append(current)
    parsed = []
    for lines in records:
        fields = {}
        for line in lines:
            pair = labelled_value(line)
            if pair:
                fields[pair[0]] = pair[1]
        if '测量编号' in fields:
            parsed.append(fields)
    return parsed


def _applicable_revisions(measurement_ids):
    """Applicable process revision of each measurement, read-only from ``demo_industrial``."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    placeholders = ','.join(['%s'] * len(measurement_ids))
    sql = ('SELECT m.measurement_id, b.batch_id, b.recorded_revision, r.valid_from, r.valid_to '
           'FROM demo_industrial.measurement_observations m '
           'JOIN demo_industrial.production_batches b ON b.batch_id = m.batch_id '
           'LEFT JOIN demo_industrial.process_revisions r ON r.revision_id = b.recorded_revision '
           f'WHERE m.measurement_id IN ({placeholders})')
    with psycopg2.connect(META_DSN) as conn:
        conn.set_session(readonly=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, list(measurement_ids))
            return {row['measurement_id']: dict(row) for row in cur.fetchall()}


def quality_transcript():
    """The frozen transcription of the existing industrial PDF, with its real DB attribution."""
    records = _real_pdf_records()
    revisions = _applicable_revisions([record['测量编号'] for record in records
                                       if record.get('测量编号')])
    transcript = []
    for record in records:
        measurement_id = record['测量编号']
        attribution = revisions.get(measurement_id) or {}
        revision = attribution.get('recorded_revision')
        valid_from = attribution.get('valid_from')
        transcript.append({
            '报告编号': record.get('报告编号'), '测量编号': measurement_id,
            '批次编号': record.get('批次编号'), '零件编号': record.get('零件编号'),
            '测量时点': record.get('测量时点'), '实测值': record.get('实测值'),
            '单位': record.get('单位'), '报告结论': record.get('报告结论'),
            '库记录版次': revision,
            '版次生效日期': valid_from.isoformat() if valid_from is not None else None,
            '版次失效日期': (attribution.get('valid_to').isoformat()
                          if attribution.get('valid_to') is not None else None)})
    return transcript


def transcript_document(transcript):
    """Wrap the frozen transcript into one corpus document (one clause record per record)."""
    clauses = []
    for item in transcript:
        clauses.append(clause(
            f'ZJ-{item["报告编号"]}', f'质检核验记录 {item["报告编号"]}', '温度', None, None,
            item['单位'], item.get('库记录版次') or 'ZJ-A102-2026.09',
            item.get('版次生效日期') or '2026-09-01T00:00:00+08:00',
            item.get('版次失效日期'), item['零件编号'],
            f'条款原文：质检报告 {item["报告编号"]} 记录实测值 {item["实测值"]} {item["单位"]}。',
            extra={'测量编号': item['测量编号'], '批次编号': item['批次编号'],
                   '报告编号': item['报告编号'], '测量时点': item['测量时点'],
                   '实测值': item['实测值'], '报告结论': item.get('报告结论') or ''}))
    return {'name': 'ZJ-A102_质检核验记录', 'code': 'ZJ-A102-2026.09',
            'title': 'A102 质检核验记录（转录自 industrial_demo_v1.pdf）', 'version': '2026.09',
            'scope': 'A102 零件工序 30 保温温度质检核验',
            'valid_from': '2026-09-01T00:00:00+08:00', 'valid_to': None, 'format': 'pdf',
            'clauses': clauses}


def build(target: Path, transcript):
    target.mkdir(parents=True, exist_ok=True)
    files = []
    for document in DOCUMENTS + [transcript_document(transcript)]:
        path = target / f'{document["name"]}.{document["format"]}'
        write_document(path, document)
        files.append({'name': path.name, 'format': document['format'],
                      'document_code': document['code'], 'title': document['title'],
                      'clauses': len(document['clauses']),
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return files


# ---------------------------------------------------------------- verification

def _canonical_op(value):
    """Map a document's comparison word onto the engine's *violation* operator.

    ``超过`` / ``不超过`` both mean "violated when the value is greater than the limit"; the
    engine's ``_OP_MAP`` already encodes that, and this mirror lets the report compare a document
    word with a parsed operator without importing a private name.
    """
    return {'不超过': '>', '超过': '>', '高于': '>', '低于': '<', '不低于': '<',
            '不得低于': '<'}.get(str(value), str(value))


def extraction_report(target: Path):
    """Measure, for the frozen corpus: label parse, clause text regex, field completeness.

    Three numbers are kept apart on purpose, because they mean different things:

    ``threshold_clauses.labelled_extracted``
        a threshold clause whose structured labels parsed into metric/op/limit/unit — this is the
        path the cross-source engine consumes;
    ``threshold_clauses.text_regex_extracted``
        the same clause read by the engine's *own* ``metric + operator + number + unit`` regex with
        no labels at all — the honest "could this sentence be parsed from raw prose" number;
    ``value_claims.extracted``
        a 质检核验记录 whose measured value became a document-side numeric claim.
    """
    from datamind import cross_source
    from ingestion import norm_facts
    paths = sorted(target.glob('*.pdf')) + sorted(target.glob('*.docx'))
    documents, clauses, errors = norm_facts.corpus_records(paths)
    per_document, threshold, claims, incomplete, failures = [], [], [], [], []
    for document in documents:
        subset = [record for record in clauses if record['document_id'] == document['document_id']]
        per_document.append({'document_id': document['document_id'], 'title': document['title'],
                             'block_count': document['block_count'], 'clauses': len(subset),
                             'clause_ids': [record['clause_id'] for record in subset]})
    for record in clauses:
        required = recording_requirements(record)
        missing = [field for field in required if not record.get(field)]
        if missing:
            incomplete.append({'clause_id': record['clause_id'], 'missing': missing})
        block = norm_facts._block_for(record, {})
        labelled = cross_source.extract_clause(block)
        if record.get('limit') is not None:
            # Path 2: the engine's own clause regex over the whole labelled record text.
            text_only = cross_source.extract_clause({'text': record['text'],
                                                     'chunk_id': record['clause_id']})
            # Path 3: the same regex over the bare prose sentence (no labels at all).
            prose = cross_source.extract_clause({'text': record.get('clause_text') or '',
                                                 'chunk_id': record['clause_id'] + ':prose'})
            def reading(clause):
                if not clause:
                    return None
                return {key: clause.get(key) for key in ('metric', 'op', 'limit', 'unit')}

            def agrees(clause, keys):
                return bool(clause) and all(
                    (_canonical_op(clause.get(key)) if key == 'op' else str(clause.get(key)))
                    == (_canonical_op(record.get(key)) if key == 'op' else str(record.get(key)))
                    for key in keys)

            entry = {'clause_id': record['clause_id'], 'metric': record.get('metric'),
                     'op': record.get('op'), 'limit': record.get('limit'),
                     'unit': record.get('unit'), 'applies_to_version': record.get('applies_to_version'),
                     'valid_from': record.get('valid_from'), 'valid_to': record.get('valid_to'),
                     'entity_label': record.get('entity_label'),
                     'labelled_extracted': bool(labelled),
                     'text_regex_extracted': bool(text_only),
                     'text_regex_value_agrees': agrees(text_only, ('op', 'limit', 'unit')),
                     'text_regex_metric_agrees': agrees(text_only, ('metric',)),
                     'text_regex_reading': reading(text_only),
                     'prose_regex_extracted': bool(prose),
                     'prose_regex_value_agrees': agrees(prose, ('op', 'limit', 'unit')),
                     'prose_regex_metric_agrees': agrees(prose, ('metric',)),
                     'prose_regex_reading': reading(prose)}
            threshold.append(entry)
            if not labelled:
                failures.append({'clause_id': record['clause_id'], 'reason': 'CLAUSE_NOT_EXTRACTED'})
        elif record.get('value') is not None:
            claims.append({'clause_id': record['clause_id'], 'metric': record.get('metric'),
                           'value': record.get('value'), 'unit': record.get('unit'),
                           'applies_to_version': record.get('applies_to_version'),
                           'instant': record.get('instant'),
                           'entity_label': record.get('entity_label'),
                           'claim_block': bool(block.get('value') is not None
                                               and block.get('metric') and block.get('unit'))})
    return {'corpus_dir': str(target), 'documents': len(documents), 'records': len(clauses),
            'parse_errors': errors, 'per_document': per_document,
            'documents_detail': documents,
            'threshold_clauses': {
                'total': len(threshold),
                'labelled_extracted': sum(1 for item in threshold if item['labelled_extracted']),
                'text_regex_extracted': sum(1 for item in threshold if item['text_regex_extracted']),
                'text_regex_value_agrees': sum(1 for item in threshold
                                               if item['text_regex_value_agrees']),
                'text_regex_metric_agrees': sum(1 for item in threshold
                                                if item['text_regex_metric_agrees']),
                'prose_regex_extracted': sum(1 for item in threshold if item['prose_regex_extracted']),
                'prose_regex_value_agrees': sum(1 for item in threshold
                                                if item['prose_regex_value_agrees']),
                'prose_regex_metric_agrees': sum(1 for item in threshold
                                                 if item['prose_regex_metric_agrees']),
                'detail': threshold},
            'value_claims': {
                'total': len(claims),
                'extracted': sum(1 for item in claims if item['claim_block']),
                'detail': claims},
            'binding_fields': {
                'required': len(clauses), 'complete': len(clauses) - len(incomplete),
                'incomplete': incomplete},
            'clause_extraction_failures': failures}


def recording_requirements(record):
    """Fields a record must carry to be bindable by the engine (measured per record)."""
    required = ['clause_id']
    if record.get('limit') is not None:
        required += ['metric', 'op', 'limit', 'unit']
    if record.get('value') is not None:
        required += ['metric', 'value', 'unit']
    required += ['applies_to_version', 'valid_from', 'entity_label']
    return required


def check(target: Path = CORPUS_DIR):
    """Determinism + parsing gate: regenerate into a temp dir and compare byte for byte."""
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    scratch = Path(tempfile.mkdtemp(prefix='norm-corpus-check-'))
    problems = []
    try:
        files = build(scratch, manifest['transcript'])
        written = {item['name']: item['sha256'] for item in files}
        frozen = {item['name']: item['sha256'] for item in manifest['files']}
        if written != frozen:
            for name in sorted(set(written) | set(frozen)):
                if written.get(name) != frozen.get(name):
                    problems.append({'file': name, 'regenerated': written.get(name),
                                     'frozen': frozen.get(name)})
        # The 质检核验记录 must still be a faithful parse of the real industrial PDF.
        live = _real_pdf_records()
        frozen_records = manifest['transcript']
        if len(live) != len(frozen_records):
            problems.append({'check': 'transcript_count', 'live': len(live),
                             'frozen': len(frozen_records)})
        else:
            for found, wanted in zip(live, frozen_records):
                for label in ('报告编号', '测量编号', '实测值', '单位', '零件编号'):
                    if found.get(label) != wanted.get(label):
                        problems.append({'check': 'transcript_field', 'label': label,
                                         'live': found.get(label), 'frozen': wanted.get(label)})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    report = extraction_report(target)
    if report['threshold_clauses']['labelled_extracted'] != report['threshold_clauses']['total']:
        problems.append({'check': 'threshold_clauses_extracted',
                         'observed': report['threshold_clauses']['labelled_extracted'],
                         'wanted': report['threshold_clauses']['total']})
    if report['value_claims']['extracted'] != report['value_claims']['total']:
        problems.append({'check': 'value_claims_extracted',
                         'observed': report['value_claims']['extracted'],
                         'wanted': report['value_claims']['total']})
    if report['binding_fields']['incomplete']:
        problems.append({'check': 'binding_fields_incomplete',
                         'observed': report['binding_fields']['incomplete']})
    return {'deterministic': not problems, 'problems': problems, 'extraction': report}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--report', default=None)
    parser.add_argument('--transcript-only', action='store_true')
    args = parser.parse_args()
    if args.transcript_only:
        print(json.dumps(quality_transcript(), ensure_ascii=False, indent=2))
        return 0
    if args.check:
        result = check()
        extraction = result['extraction']
        print(json.dumps({'deterministic': result['deterministic'],
                          'problems': result['problems'],
                          'documents': extraction['documents'],
                          'records': extraction['records'],
                          'threshold_clauses': {key: value for key, value
                                                in extraction['threshold_clauses'].items()
                                                if key != 'detail'},
                          'value_claims': {key: value for key, value
                                           in extraction['value_claims'].items() if key != 'detail'},
                          'binding_fields': extraction['binding_fields']},
                         ensure_ascii=False, indent=2))
        if args.report:
            Path(args.report).write_text(
                json.dumps(extraction, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return 0 if result['deterministic'] else 1
    transcript = quality_transcript()
    files = build(CORPUS_DIR, transcript)
    manifest = {'corpus_version': 'norm-corpus-v1',
                'builder': 'data/knowledge_sources/build_norm_corpus.py',
                'note': ('自建规范语料：阈值与演示库真实列可交叉验证；质检核验记录为 '
                         'data/industrial_demo/industrial_demo_v1.pdf 的原文转录'),
                'real_source_pdf': str(REAL_INDUSTRIAL_PDF.relative_to(ROOT)),
                'real_source_pdf_sha256': hashlib.sha256(
                    REAL_INDUSTRIAL_PDF.read_bytes()).hexdigest(),
                'files': files, 'transcript': transcript}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')
    print(json.dumps({'written': len(files), 'manifest': str(MANIFEST),
                      'files': files}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
