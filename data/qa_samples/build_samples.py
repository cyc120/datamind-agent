#!/usr/bin/env python3
"""Deterministic builder for the WP9 question-answering sample set (`data/qa_samples/`).

What this file is
-----------------
The audit (§7.3) says the project has no fixed multi-format, end-to-end sample set for the
"upload a new file → parse → review → index → ask → locate the citation" story. This script
generates that set offline, so the evaluation in `scripts/eval_qa_samples.py` can freeze the
samples first and judge them afterwards instead of judging whatever happens to be in the
database on the day of the demo.

Discipline
----------
* **Byte-deterministic.** Every writer is pinned: ZIP container timestamps and OOXML core
  properties are normalised, PDFs are written with ``no_new_id``/``reproducible`` metadata,
  and the scanned pages come from a fixed rasteriser + font. Regenerating the same content
  therefore reproduces the same SHA-256, which is what makes "先冻结 manifest 再评测" mean
  something.
* **Frozen once.** The manifest is written with exclusive-create semantics: a second run
  refuses to overwrite it unless ``--force`` is passed, mirroring the append-only rule used
  by the evaluation registry. ``--check`` re-verifies size + SHA-256 + manifest digest and
  never writes.
* **No database, no network, no model.** This script only writes the files under
  ``data/qa_samples/`` and reads ``git rev-parse`` for the commit stamp.

Answer assertions are machine-checkable and are re-verified by `tests/test_qa_samples.py`
from the generated files themselves (text extraction per format, exact cells for XLSX, table
cells for DOCX) or, for the two image-only scans, from the frozen ground truth that the
deterministic rasteriser rendered.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent
ROOT = SAMPLES_DIR.parents[1]
MANIFEST_PATH = SAMPLES_DIR / 'manifest.json'

#: One fixed instant for every embedded timestamp (OOXML core properties, PDF metadata,
#: DXF header). 2026-10-10T00:00:00Z frozen as text in several formats.
FIXED_ISO = '2026-10-10T00:00:00+00:00'
FIXED_PDF_DATE = 'D:20261010000000Z'
FIXED_ZIP_DT = (1980, 1, 1, 0, 0, 0)
FIXED_DXF_COMMENT = 'ezdxf deterministic build @ 2026-10-10T00:00:00+00:00'
FIXED_DXF_JULIAN = 2461323.5  # 2026-10-10T00:00:00Z, re-derived by tests via ezdxf.

SUITE = 'qa_samples'
SUITE_VERSION = 'qa-samples-v1'

#: The CJK font the scanned pages are rasterised with. Recorded (path + sha256) in the
#: manifest so "the scan was rendered with this font" is checkable rather than assumed.
SCAN_FONT_CANDIDATES = (
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
)

#: Expected citation-locator type per format, copied from `datamind.parse_contract`
#: (LOCATOR_KINDS / LOCATOR_REQUIRED_FIELDS). The evaluation asserts the stored locators
#: actually carry these fields; the expectation is declared here so a contract change shows
#: up as a failure instead of being silently re-labelled.
LOCATOR_REQUIRED_FIELDS = {
    'pdf_region': ['page_no', 'page_width', 'page_height', 'bbox', 'unit', 'coordinate_system'],
    'office_object': ['part'],
    'sheet_range': ['sheet'],
    'cad_entity': ['layout', 'layer', 'model_xy', 'unit'],
}


# --------------------------------------------------------------------------------------
# Deterministic writers
# --------------------------------------------------------------------------------------

_CORE_CREATED = re.compile(r'(<dcterms:created[^>]*>)[^<]*(</dcterms:created>)')
_CORE_MODIFIED = re.compile(r'(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)')


def _normalize_ooxml(data: bytes) -> bytes:
    """Re-zip an OOXML package with fixed entry timestamps and fixed core properties.

    python-docx / openpyxl / python-pptx each stamp the ZIP container and
    ``docProps/core.xml`` with the wall clock, so two identical documents written a second
    apart have different bytes. Rewriting the package with one fixed timestamp and one fixed
    ``dcterms:created``/``dcterms:modified`` makes the output reproducible without touching
    any part the readers care about (verified by re-opening every normalised package).
    """
    source = zipfile.ZipFile(io.BytesIO(data))
    output = io.BytesIO()
    names = sorted(source.namelist(), key=lambda name: (name != '[Content_Types].xml', name))
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for name in names:
            payload = source.read(name)
            if name == 'docProps/core.xml':
                text = payload.decode('utf-8')
                text = _CORE_CREATED.sub(lambda m: m.group(1) + FIXED_ISO + m.group(2), text)
                text = _CORE_MODIFIED.sub(lambda m: m.group(1) + FIXED_ISO + m.group(2), text)
                payload = text.encode('utf-8')
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_DT)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, payload)
    source.close()
    return output.getvalue()


def _save_ooxml(document, writer='save') -> bytes:
    buffer = io.BytesIO()
    getattr(document, writer)(buffer)
    return _normalize_ooxml(buffer.getvalue())


def _save_pdf(document) -> bytes:
    """Serialise a frozen-metadata PDF with PyMuPDF's reproducible writers."""
    document.set_metadata({
        'creationDate': FIXED_PDF_DATE, 'modDate': FIXED_PDF_DATE, 'producer': '',
        'creator': '', 'title': '', 'author': '', 'subject': '', 'keywords': ''})
    data = document.tobytes(no_new_id=True, reproducible=True)
    document.close()
    return data


def _canonicalize_dxf_section(text: str, section: str, *, keep_first: bool,
                             key_codes: tuple[str, ...]) -> str:
    """Re-emit one DXF section's records in a canonical, hash-seed-independent order.

    ``ezdxf`` writes the ``CLASSES`` and ``OBJECTS`` records in an order that comes from
    iterating Python sets, so the same drawing produced by two interpreter processes
    (different ``PYTHONHASHSEED``) differs in that order alone — a frozen SHA-256 would then
    describe one process rather than the drawing. Records are therefore re-emitted sorted by
    the values of ``key_codes``. ``keep_first`` protects the ``OBJECTS`` root dictionary,
    which ezdxf (and DXF readers generally) require to be the first object in the section.
    Handles travel inside their own record, so nothing is renumbered. A section that cannot
    be read as clean code/value pairs is returned untouched rather than corrupted.
    """
    lines = text.splitlines()
    start = None
    for index in range(len(lines) - 3):
        if (lines[index].strip() == '0' and lines[index + 1].strip() == 'SECTION'
                and lines[index + 2].strip() == '2' and lines[index + 3].strip() == section):
            start = index + 4
            break
    if start is None:
        return text
    end = start
    while end < len(lines) - 1 and not (lines[end].strip() == '0'
                                        and lines[end + 1].strip() == 'ENDSEC'):
        end += 1
    if end >= len(lines) - 1 or (end - start) % 2:
        return text
    records: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for position in range(start, end, 2):
        pair = (lines[position], lines[position + 1])
        if pair[0].strip() == '0' and current:
            records.append(current)
            current = []
        current.append(pair)
    if current:
        records.append(current)

    def key(record):
        lookup = {}
        for code, value in record:
            lookup.setdefault(code.strip(), value.strip())
        return tuple(lookup.get(code, '') for code in key_codes)

    head = records[:1] if keep_first else []
    records = records[1:] if keep_first else records
    records.sort(key=key)
    rebuilt: list[str] = []
    for record in head + records:
        for code, value in record:
            rebuilt.extend([code, value])
    tail = lines[end:]
    return '\n'.join(lines[:start] + rebuilt + tail) + ('\n' if text.endswith('\n') else '')


def _save_dxf(document) -> bytes:
    """Serialise a DXF as normalised ASCII text.

    ``ezdxf`` re-stamps ``$TDCREATE``/``$TDUPDATE``/``$TDUCREATE``/``$TDUUPDATE`` with the
    wall clock at write time (a header assignment is overwritten), regenerates
    ``$VERSIONGUID``, and emits the ``CLASSES``/``OBJECTS`` records in hash-seed-dependent
    order. Rather than pretending those are stable, the writer text is normalised: both
    sections are canonicalised (the OBJECTS root dictionary stays first), every julian
    timestamp is replaced by one fixed value, every GUID by one fixed GUID per position, and
    the library's own build comment by a fixed string. Handles, entity order and geometry are
    untouched, so the drawing stays a valid DXF (verified by re-reading it).
    """
    stream = io.StringIO()
    document.write(stream)
    text = _canonicalize_dxf_section(stream.getvalue(), 'CLASSES', keep_first=False,
                                     key_codes=('1',))
    text = _canonicalize_dxf_section(text, 'OBJECTS', keep_first=True,
                                     key_codes=('0', '5'))
    text = re.sub(r'\b24\d{5}\.\d+\b', str(FIXED_DXF_JULIAN), text)
    text = re.sub(r'\b245\d{4}\.\d+\b', str(FIXED_DXF_JULIAN), text)
    seen: dict[str, str] = {}

    def _fixed_guid(match: re.Match) -> str:
        value = match.group(0)
        if value not in seen:
            index = len(seen) + 1
            seen[value] = '{%08d-0000-0000-0000-%012d}' % (index, index)
        return seen[value]

    text = re.sub(r'\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-'
                  r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}', _fixed_guid, text)
    text = re.sub(r'1\.\d+\.\d+ @ [0-9T:+\-.]+', FIXED_DXF_COMMENT, text)
    return text.encode('ascii')


# --------------------------------------------------------------------------------------
# Sample content (the single source of truth for both the files and the assertions)
# --------------------------------------------------------------------------------------

def _questions(*items):
    return list(items)


def _sample(**kwargs):
    """Fill the fields every sample shares, so the spec below stays declarative."""
    kwargs.setdefault('text_layer', True)
    kwargs.setdefault('pages', 1)
    assert len(kwargs['questions']) >= 2, kwargs['file']
    return kwargs


SAMPLES = [
    _sample(
        file='01_data_service_policy_native.pdf', format='pdf-native', suffix='.pdf',
        pages=2, expected_locator_kinds=['pdf_region'],
        description='原生文本 PDF：编号条款 + 跨页数值表',
        ground_truth={'refund_sla_hours': '48 小时', 'escalate_hours': '24 小时',
                      'deposit_ratio': '15%', 'q1_approved': '120', 'q2_approved': '90'},
        questions=_questions(
            {'id': 'qa-01-q1', 'question': '退款审批时限是多少小时？',
             'answer': '48 小时', 'checkable_from': 'file_text',
             'assertion': {'kind': 'exact_text', 'expected': '48 小时',
                           'ground_truth_key': 'refund_sla_hours'}},
            {'id': 'qa-01-q2', 'question': '第一、二季度审批量合计是多少笔？',
             'answer': '210 笔', 'checkable_from': 'file_text',
             'assertion': {'kind': 'arith_sum', 'operands': [120, 90], 'expected': 210,
                           'operand_sources': [
                               {'pointer': 'page:2', 'value': 120},
                               {'pointer': 'page:2', 'value': 90}]}}),
    ),
    _sample(
        file='02_regional_sales_native.pdf', format='pdf-native', suffix='.pdf',
        pages=1, expected_locator_kinds=['pdf_region'],
        description='原生文本 PDF：区域销售额表 + 口径说明',
        ground_truth={'caliber_note': '不含税金额', 'east': '320', 'south': '180',
                      'north': '150', 'total': '650'},
        questions=_questions(
            {'id': 'qa-02-q1', 'question': '三个区域销售额合计是多少万元？',
             'answer': '650 万元', 'checkable_from': 'file_text',
             'assertion': {'kind': 'arith_sum', 'operands': [320, 180, 150], 'expected': 650,
                           'operand_sources': [
                               {'pointer': 'page:1', 'value': 320},
                               {'pointer': 'page:1', 'value': 180},
                               {'pointer': 'page:1', 'value': 150}]}},
            {'id': 'qa-02-q2', 'question': '华东销售额占三区域合计的百分比（保留整数）？',
             'answer': '49%', 'checkable_from': 'file_text',
             'assertion': {'kind': 'arith_ratio_percent', 'operands': [320], 'denominator': 650,
                           'expected': 49, 'operand_sources': [{'pointer': 'page:1', 'value': 320}]}}),
    ),
    _sample(
        file='03_equipment_inspection_scanned.pdf', format='pdf-scanned', suffix='.pdf',
        pages=1, text_layer=False, expected_locator_kinds=['pdf_region'],
        description='扫描版 PDF：仅图像、无文本层（OCR 路径样本）',
        ground_truth={'equipment_id': 'EQ-2049', 'temperature': '88.5 C', 'verdict': '超限',
                      'items_total': '3 项', 'items_failed': '2 项', 'items_passed': '1 项'},
        questions=_questions(
            {'id': 'qa-03-q1', 'question': '设备 EQ-2049 的巡检温度是多少？',
             'answer': '88.5 C', 'checkable_from': 'ground_truth_only',
             'assertion': {'kind': 'exact_text', 'expected': '88.5 C',
                           'ground_truth_key': 'temperature'}},
            {'id': 'qa-03-q2', 'question': '不合格项数与合格项数之差是多少项？',
             'answer': '1 项', 'checkable_from': 'ground_truth_only',
             'assertion': {'kind': 'arith_diff', 'operands': [2, 1], 'expected': 1,
                           'operand_sources': [
                               {'pointer': 'ground_truth:items_failed', 'value': 2},
                               {'pointer': 'ground_truth:items_passed', 'value': 1}]}}),
    ),
    _sample(
        file='04_quality_notice_scanned.pdf', format='pdf-scanned', suffix='.pdf',
        pages=2, text_layer=False, expected_locator_kinds=['pdf_region'],
        description='扫描版 PDF：两页、仅图像、含抽检结论',
        ground_truth={'batch_no': 'B-2026-0715', 'sampled': '200 件', 'failed': '6 件',
                      'passed': '194 件', 'pass_rate': '97%'},
        questions=_questions(
            {'id': 'qa-04-q1', 'question': '抽检批次号是多少？',
             'answer': 'B-2026-0715', 'checkable_from': 'ground_truth_only',
             'assertion': {'kind': 'exact_text', 'expected': 'B-2026-0715',
                           'ground_truth_key': 'batch_no'}},
            {'id': 'qa-04-q2', 'question': '合格件数是多少件？',
             'answer': '194 件', 'checkable_from': 'ground_truth_only',
             'assertion': {'kind': 'arith_diff', 'operands': [200, 6], 'expected': 194,
                           'operand_sources': [
                               {'pointer': 'ground_truth:sampled', 'value': 200},
                               {'pointer': 'ground_truth:failed', 'value': 6}]}}),
    ),
    _sample(
        file='05_service_sla_manual.docx', format='docx-native', suffix='.docx',
        expected_locator_kinds=['office_object'],
        description='DOCX：标题层级 + 三行表格（真实 body 顺序）',
        ground_truth={'first_line_hours': '2 小时', 'second': '8 小时', 'third': '24 小时',
                      'sum': '34 小时'},
        questions=_questions(
            {'id': 'qa-05-q1', 'question': '一线响应时限是多少小时？',
             'answer': '2 小时', 'checkable_from': 'file_text',
             'assertion': {'kind': 'exact_text', 'expected': '2 小时',
                           'ground_truth_key': 'first_line_hours'}},
            {'id': 'qa-05-q2', 'question': '三级响应时限合计是多少小时？',
             'answer': '34 小时', 'checkable_from': 'file_cell',
             'assertion': {'kind': 'arith_sum', 'operands': [2, 8, 24], 'expected': 34,
                           'operand_sources': [
                               {'pointer': 'docx_table:0:1:1', 'value': 2},
                               {'pointer': 'docx_table:0:2:1', 'value': 8},
                               {'pointer': 'docx_table:0:3:1', 'value': 24}]}}),
    ),
    _sample(
        file='06_downtime_incident.docx', format='docx-native', suffix='.docx',
        expected_locator_kinds=['office_object'],
        description='DOCX：事件报告段落 + 时间构成数字',
        ground_truth={'incident_id': 'INC-7781', 'planned': '120 分钟',
                      'unplanned': '45 分钟', 'total': '165 分钟'},
        questions=_questions(
            {'id': 'qa-06-q1', 'question': '事件编号是什么？',
             'answer': 'INC-7781', 'checkable_from': 'file_text',
             'assertion': {'kind': 'exact_text', 'expected': 'INC-7781',
                           'ground_truth_key': 'incident_id'}},
            {'id': 'qa-06-q2', 'question': '非计划停机占总停机时间的百分比（保留整数）？',
             'answer': '27%', 'checkable_from': 'file_text',
             'assertion': {'kind': 'arith_ratio_percent', 'operands': [45], 'denominator': 165,
                           'expected': 27, 'operand_sources': [{'pointer': 'paragraph:5', 'value': 45}]}}),
    ),
    _sample(
        file='07_sales_ledger.xlsx', format='xlsx-native', suffix='.xlsx',
        expected_locator_kinds=['sheet_range'],
        description='XLSX：两个工作表，销售明细逐行成块（sheet_range 定位）',
        ground_truth={'east_sum': '250', 'grand_total': '570', 'east_rows': '2'},
        questions=_questions(
            {'id': 'qa-07-q1', 'question': '华东区域两个月销售额合计是多少万元？',
             'answer': '250 万元', 'checkable_from': 'file_cell',
             'assertion': {'kind': 'arith_sum', 'operands': [120, 130], 'expected': 250,
                           'operand_sources': [
                               {'pointer': 'cell:销售明细:C2', 'value': 120},
                               {'pointer': 'cell:销售明细:C3', 'value': 130}]}},
            {'id': 'qa-07-q2', 'question': '全部六条记录的销售额合计是多少万元？',
             'answer': '570 万元', 'checkable_from': 'file_cell',
             'assertion': {'kind': 'arith_sum', 'operands': [120, 130, 90, 95, 70, 65],
                           'expected': 570,
                           'operand_sources': [
                               {'pointer': 'cell:销售明细:C2', 'value': 120},
                               {'pointer': 'cell:销售明细:C3', 'value': 130},
                               {'pointer': 'cell:销售明细:C4', 'value': 90},
                               {'pointer': 'cell:销售明细:C5', 'value': 95},
                               {'pointer': 'cell:销售明细:C6', 'value': 70},
                               {'pointer': 'cell:销售明细:C7', 'value': 65}]}},
            {'id': 'qa-07-q3', 'question': '华东区域共有几条销售记录？',
             'answer': '2 条', 'checkable_from': 'file_cell',
             'assertion': {'kind': 'count_matching', 'expected': 2,
                           'column': '销售明细!A', 'equals': '华东',
                           'operand_sources': [
                               {'pointer': 'cell:销售明细:A2', 'value': '华东'},
                               {'pointer': 'cell:销售明细:A3', 'value': '华东'}]}}),
    ),
    _sample(
        file='08_inventory_levels.xlsx', format='xlsx-native', suffix='.xlsx',
        expected_locator_kinds=['sheet_range'],
        description='XLSX：库存与安全库存对照（低于安全库存计数）',
        ground_truth={'below_safety': '2', 'stock_total': '245'},
        questions=_questions(
            {'id': 'qa-08-q1', 'question': '低于安全库存的物料有几项？',
             'answer': '2 项', 'checkable_from': 'file_cell',
             'assertion': {'kind': 'count_less_than', 'expected': 2,
                           'columns': ['库存!B', '库存!C'],
                           'operand_sources': [
                               {'pointer': 'cell:库存:B2', 'value': 40},
                               {'pointer': 'cell:库存:C2', 'value': 50},
                               {'pointer': 'cell:库存:B4', 'value': 25},
                               {'pointer': 'cell:库存:C4', 'value': 30}]}},
            {'id': 'qa-08-q2', 'question': '四项物料的库存总量是多少？',
             'answer': '245', 'checkable_from': 'file_cell',
             'assertion': {'kind': 'arith_sum', 'operands': [40, 80, 25, 100], 'expected': 245,
                           'operand_sources': [
                               {'pointer': 'cell:库存:B2', 'value': 40},
                               {'pointer': 'cell:库存:B3', 'value': 80},
                               {'pointer': 'cell:库存:B4', 'value': 25},
                               {'pointer': 'cell:库存:B5', 'value': 100}]}}),
    ),
    _sample(
        file='09_product_review.pptx', format='pptx-native', suffix='.pptx',
        expected_locator_kinds=['office_object'],
        description='PPTX：4 页复盘（标题 + 正文文本框，slide/shape 定位）',
        ground_truth={'target': '500 万元', 'actual': '430 万元', 'delay_times': '3 次'},
        questions=_questions(
            {'id': 'qa-09-q1', 'question': '本季度目标销售额是多少万元？',
             'answer': '500 万元', 'checkable_from': 'file_text',
             'assertion': {'kind': 'exact_text', 'expected': '500 万元',
                           'ground_truth_key': 'target'}},
            {'id': 'qa-09-q2', 'question': '目标与实际销售额的差额是多少万元？',
             'answer': '70 万元', 'checkable_from': 'file_text',
             'assertion': {'kind': 'arith_diff', 'operands': [500, 430], 'expected': 70,
                           'operand_sources': [
                               {'pointer': 'slide:2', 'value': 500},
                               {'pointer': 'slide:3', 'value': 430}]}}),
    ),
    _sample(
        file='10_safety_training.pptx', format='pptx-native', suffix='.pptx',
        expected_locator_kinds=['office_object'],
        description='PPTX：3 页安全培训课件（阈值 + 演练频次）',
        ground_truth={'threshold': '40 摄氏度', 'drill_per_quarter': '1 次', 'per_year': '4'},
        questions=_questions(
            {'id': 'qa-10-q1', 'question': '高温作业阈值是多少摄氏度？',
             'answer': '40 摄氏度', 'checkable_from': 'file_text',
             'assertion': {'kind': 'exact_text', 'expected': '40 摄氏度',
                           'ground_truth_key': 'threshold'}},
            {'id': 'qa-10-q2', 'question': '按每季度 1 次计算，全年应急演练共几次？',
             'answer': '4 次', 'checkable_from': 'file_text',
             'assertion': {'kind': 'arith_product', 'operands': [1, 4], 'expected': 4,
                           'operand_sources': [{'pointer': 'slide:3', 'value': 1}]}}),
    ),
    _sample(
        file='11_bracket_spec_layers.dxf', format='dxf-native', suffix='.dxf',
        expected_locator_kinds=['cad_entity'],
        description='DXF（工业特色）：两图层 + 尺寸线 + 文本标注（cad_entity 定位）',
        ground_truth={'model': 'BR-220', 'temp_limit': '88.5C', 'layers': '2'},
        questions=_questions(
            {'id': 'qa-11-q1', 'question': '图纸标注的型号是什么？',
             'answer': 'BR-220', 'checkable_from': 'file_text',
             'assertion': {'kind': 'exact_text', 'expected': 'BR-220',
                           'ground_truth_key': 'model'}},
            {'id': 'qa-11-q2', 'question': '图纸中标注的温度上限值是多少？',
             'answer': '88.5C', 'checkable_from': 'file_text',
             'assertion': {'kind': 'exact_text', 'expected': '88.5C',
                           'ground_truth_key': 'temp_limit'}}),
    ),
]


# --------------------------------------------------------------------------------------
# Per-format writers
# --------------------------------------------------------------------------------------

def _new_pdf():
    import pymupdf
    document = pymupdf.open()
    document.set_metadata({'title': 'DataMind QA sample', 'author': '',
                           'subject': 'synthetic deterministic demo document',
                           'keywords': 'qa_samples', 'creator': '', 'producer': '',
                           'creationDate': FIXED_PDF_DATE, 'modDate': FIXED_PDF_DATE})
    return document


def _pdf_page(document):
    # `china-s` is PyMuPDF's built-in simplified-Chinese font: no external font file is
    # needed for a native PDF with CJK text (unlike the scans, which need a rasteriser font).
    return document.new_page(width=595, height=842)


def _pdf_text(page, x, y, text, size=13):
    page.insert_text((x, y), text, fontname='china-s', fontsize=size)


def _write_policy_pdf():
    document = _new_pdf()
    page = _pdf_page(document)
    _pdf_text(page, 60, 70, '数据服务与退款管理制度（演示）', size=17)
    _pdf_text(page, 60, 110, '第 3 章 退款处理时限')
    _pdf_text(page, 60, 140, '3.2 退款审批时限为 48 小时。')
    _pdf_text(page, 60, 170, '3.4 超时未完成的工单须在 24 小时内升级。')
    _pdf_text(page, 60, 210, '第 5 章 资金与保证金')
    _pdf_text(page, 60, 240, '5.1 保证金比例为 15%。')
    page2 = _pdf_page(document)
    _pdf_text(page2, 60, 70, '附表：季度审批量（演示数据）', size=15)
    _pdf_text(page2, 60, 110, '季度      审批量（笔）')
    _pdf_text(page2, 60, 140, '第一季度  120')
    _pdf_text(page2, 60, 170, '第二季度  90')
    return _save_pdf(document)


def _write_sales_pdf():
    document = _new_pdf()
    page = _pdf_page(document)
    _pdf_text(page, 60, 70, '2025 年区域销售简报（演示）', size=17)
    _pdf_text(page, 60, 110, '口径说明：销售额为不含税金额。')
    _pdf_text(page, 60, 150, '区域    销售额（万元）')
    _pdf_text(page, 60, 180, '华东    320')
    _pdf_text(page, 60, 210, '华南    180')
    _pdf_text(page, 60, 240, '华北    150')
    return _save_pdf(document)


def _scan_font():
    import hashlib as _hashlib
    from PIL import ImageFont
    for candidate in SCAN_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path, _hashlib.sha256(path.read_bytes()).hexdigest(), ImageFont.truetype(str(path), 30), \
                ImageFont.truetype(str(path), 38)
    raise SystemExit('SCAN_FONT_UNavailable: no CJK font found in %s' % (SCAN_FONT_CANDIDATES,))


def _render_scan(lines):
    """Rasterise one page of text as a grayscale JPEG (deterministic for a fixed font).

    Grayscale + a fixed JPEG quality keeps the two scans small enough to ship while staying
    byte-reproducible (Pillow's encoder is deterministic for a fixed input and quality).
    """
    from PIL import Image, ImageDraw
    _, _, body, title = _scan_font()
    width, height = 1000, 140 + 52 * len(lines)
    image = Image.new('L', (width, height), 255)
    draw = ImageDraw.Draw(image)
    y = 40
    for index, line in enumerate(lines):
        draw.text((60, y), line, fill=0, font=title if index == 0 else body)
        y += 52
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=75, optimize=True, subsampling=0)
    return buffer.getvalue()


def _write_scanned_pdf(pages):
    import pymupdf
    document = _new_pdf()
    for lines in pages:
        png = _render_scan(lines)
        from PIL import Image
        size = Image.open(io.BytesIO(png)).size
        page = document.new_page(width=size[0] * 72 / 150, height=size[1] * 72 / 150)
        page.insert_image(page.rect, stream=png)
    return _save_pdf(document)


def _write_inspection_scan():
    return _write_scanned_pdf([[
        '设备巡检记录（扫描件）',
        '设备编号：EQ-2049',
        '巡检温度：88.5 C',
        '判定：超限',
        '检查项：3 项',
        '其中不合格：2 项',
        '其中合格：1 项',
    ]])


def _write_quality_scan():
    return _write_scanned_pdf([
        ['产品质量通告（扫描件）',
         '批次号：B-2026-0715',
         '抽检总数：200 件',
         '不合格数：6 件',
         '合格数：194 件',
         '合格率：97%'],
        ['处理意见（第二页）',
         '复检结论：批次隔离',
         '责任工序：A102',
         '复检日期：2026-07-18'],
    ])


def _write_sla_docx():
    from docx import Document
    document = Document()
    document.add_heading('客户服务响应时限规范', level=1)
    document.add_heading('1. 响应时限', level=2)
    document.add_paragraph('1.1 一线响应时限为 2 小时。')
    document.add_paragraph('1.2 二线响应时限为 8 小时。')
    document.add_paragraph('1.3 三线响应时限为 24 小时。')
    table = document.add_table(rows=4, cols=2)
    header = ['级别', '时限（小时）']
    for column, text in enumerate(header):
        table.cell(0, column).text = text
    for row, (level, hours) in enumerate([('一线', '2'), ('二线', '8'), ('三线', '24')], start=1):
        table.cell(row, 0).text = level
        table.cell(row, 1).text = hours
    return _save_ooxml(document)


def _write_incident_docx():
    from docx import Document
    document = Document()
    document.add_heading('设备停机事件报告', level=1)
    document.add_paragraph('事件编号：INC-7781')
    document.add_paragraph('发生工序：A102 装配线')
    document.add_paragraph('计划停机：120 分钟')
    document.add_paragraph('非计划停机：45 分钟')
    document.add_paragraph('总停机时间：165 分钟')
    document.add_paragraph('初步原因：主轴温度超限，已按工艺规程降速处理。')
    return _save_ooxml(document)


def _write_sales_xlsx():
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '销售明细'
    sheet.append(['区域', '月份', '销售额（万元）'])
    for row in [('华东', '1月', 120), ('华东', '2月', 130), ('华南', '1月', 90),
                ('华南', '2月', 95), ('华北', '1月', 70), ('华北', '2月', 65)]:
        sheet.append(list(row))
    caliber = workbook.create_sheet('口径')
    caliber.append(['指标', '说明'])
    caliber.append(['销售额', '不含税金额，单位万元'])
    return _save_ooxml(workbook)


def _write_inventory_xlsx():
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '库存'
    sheet.append(['物料', '库存', '安全库存'])
    for row in [('M-01', 40, 50), ('M-02', 80, 60), ('M-03', 25, 30), ('M-04', 100, 90)]:
        sheet.append(list(row))
    return _save_ooxml(workbook)


def _write_review_pptx():
    from pptx import Presentation
    presentation = Presentation()
    layout = presentation.slide_layouts[1]
    slides = [('2026 Q1 产品复盘', ['复盘范围：华东与华南']),
              ('目标销售额：500 万元', ['目标口径：不含税']),
              ('实际销售额：430 万元', ['差异原因：渠道延迟']),
              ('主要问题：交付延迟 3 次', ['改进项：提前备货'])]
    for title, lines in slides:
        slide = presentation.slides.add_slide(layout)
        slide.shapes.title.text = title
        slide.placeholders[1].text = '\n'.join(lines)
    return _save_ooxml(presentation)


def _write_safety_pptx():
    from pptx import Presentation
    presentation = Presentation()
    layout = presentation.slide_layouts[1]
    slides = [('安全培训课件', ['培训对象：一线作业人员']),
              ('高温作业阈值：40 摄氏度', ['超过阈值须停止作业']),
              ('应急演练：每季度 1 次，参与 12 人', ['全年共 4 次演练'])]
    for title, lines in slides:
        slide = presentation.slides.add_slide(layout)
        slide.shapes.title.text = title
        slide.placeholders[1].text = '\n'.join(lines)
    return _save_ooxml(presentation)


def _write_bracket_dxf():
    import ezdxf
    document = ezdxf.new('R2010', setup=False)
    for name, color in (('EQUIPMENT', 3), ('ANNOTATION', 1), ('DIMENSION', 5)):
        document.layers.add(name, color=color)
    space = document.modelspace()
    space.add_line((0, 0), (120, 0), dxfattribs={'layer': 'EQUIPMENT'})
    space.add_line((0, 0), (0, 60), dxfattribs={'layer': 'EQUIPMENT'})
    space.add_line((120, 0), (120, 60), dxfattribs={'layer': 'EQUIPMENT'})
    space.add_circle((60, 30), 12, dxfattribs={'layer': 'EQUIPMENT'})
    space.add_text('MODEL: BR-220', dxfattribs={'layer': 'ANNOTATION', 'height': 4.0}
                   ).set_placement((5, 70))
    space.add_text('TEMP_LIMIT=88.5C', dxfattribs={'layer': 'ANNOTATION', 'height': 4.0}
                   ).set_placement((5, 80))
    space.add_linear_dim(base=(0, -15), p1=(0, 0), p2=(120, 0),
                         dxfattribs={'layer': 'DIMENSION'}).render()
    return _save_dxf(document)


WRITERS = {
    '01_data_service_policy_native.pdf': _write_policy_pdf,
    '02_regional_sales_native.pdf': _write_sales_pdf,
    '03_equipment_inspection_scanned.pdf': _write_inspection_scan,
    '04_quality_notice_scanned.pdf': _write_quality_scan,
    '05_service_sla_manual.docx': _write_sla_docx,
    '06_downtime_incident.docx': _write_incident_docx,
    '07_sales_ledger.xlsx': _write_sales_xlsx,
    '08_inventory_levels.xlsx': _write_inventory_xlsx,
    '09_product_review.pptx': _write_review_pptx,
    '10_safety_training.pptx': _write_safety_pptx,
    '11_bracket_spec_layers.dxf': _write_bracket_dxf,
}


# --------------------------------------------------------------------------------------
# Shared read-side helpers (reused by scripts/eval_qa_samples.py and tests)
# --------------------------------------------------------------------------------------

def extract_text(path: Path) -> str:
    """Extract the text a reader would see, per format, without any model.

    PYPDF/PDF → PyMuPDF text layer (empty for the image-only scans); DOCX → paragraphs +
    table cells; XLSX → every cell; PPTX → every shape's text; DXF → every TEXT/MTEXT entity.
    """
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        import pymupdf
        with pymupdf.open(str(path)) as document:
            return '\n'.join(page.get_text() for page in document)
    if suffix == '.docx':
        from docx import Document
        document = Document(str(path))
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.extend(cell.text for cell in row.cells)
        return '\n'.join(parts)
    if suffix == '.xlsx':
        import openpyxl
        workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        try:
            parts = []
            for sheet in workbook.worksheets:
                parts.append(sheet.title)
                for row in sheet.iter_rows(values_only=True):
                    parts.extend('' if value is None else str(value) for value in row)
            return '\n'.join(parts)
        finally:
            workbook.close()
    if suffix == '.pptx':
        from pptx import Presentation
        presentation = Presentation(str(path))
        parts = []
        for slide in presentation.slides:
            for shape in slide.shapes:
                if getattr(shape, 'has_text_frame', False):
                    parts.append(shape.text_frame.text)
        return '\n'.join(parts)
    if suffix == '.dxf':
        import ezdxf
        document = ezdxf.readfile(str(path))
        parts = []
        for entity in document.modelspace():
            if entity.dxftype() == 'TEXT':
                parts.append(entity.dxf.text)
            elif entity.dxftype() == 'MTEXT':
                parts.append(entity.text)
        return '\n'.join(parts)
    raise ValueError(f'unsupported sample suffix: {suffix}')


def read_cell(path: Path, pointer: str):
    """Resolve one ``cell:<sheet>:<A1>`` pointer to its exact value."""
    _, sheet_name, coordinate = pointer.split(':', 2)
    import openpyxl
    workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        sheet = workbook[sheet_name]
        return sheet[coordinate].value
    finally:
        workbook.close()


def read_docx_table_cell(path: Path, pointer: str):
    """Resolve one ``docx_table:<table>:<row>:<column>`` pointer to its exact text."""
    _, table_no, row_no, column_no = pointer.split(':')
    from docx import Document
    document = Document(str(path))
    return document.tables[int(table_no)].rows[int(row_no)].cells[int(column_no)].text


def page_text(path: Path, page_no: int) -> str:
    import pymupdf
    with pymupdf.open(str(path)) as document:
        return document[page_no - 1].get_text()


def resolve_operand(path: Path, sample: dict, pointer: str, value):
    """Verify one declared operand against the file (or the frozen ground truth).

    Returns ``(ok, detail)``. ``ground_truth:<key>`` is the only pointer kind that cannot be
    read back from the file itself: the two scans carry no text layer, so their evidence is
    the frozen ground truth the deterministic rasteriser rendered, plus the file hash.
    """
    if pointer.startswith('cell:'):
        actual = read_cell(path, pointer)
        return _same_number(actual, value), {'pointer': pointer, 'actual': _plain(actual)}
    if pointer.startswith('docx_table:'):
        actual = read_docx_table_cell(path, pointer)
        return _same_number(actual, value), {'pointer': pointer, 'actual': actual}
    if pointer.startswith('page:'):
        text = page_text(path, int(pointer.split(':', 1)[1]))
        return _contains_number(text, value), {'pointer': pointer, 'needle': str(value)}
    if pointer.startswith('slide:'):
        text = extract_text(path)
        return _contains_number(text, value), {'pointer': pointer, 'needle': str(value)}
    if pointer.startswith('paragraph:'):
        text = extract_text(path)
        return _contains_number(text, value), {'pointer': pointer, 'needle': str(value)}
    if pointer.startswith('ground_truth:'):
        actual = sample['ground_truth'][pointer.split(':', 1)[1]]
        return _same_number(actual, value), {'pointer': pointer, 'actual': actual}
    raise ValueError(f'unknown pointer: {pointer}')


def _plain(value):
    return value if isinstance(value, (int, float, str)) else str(value)


def _same_number(actual, expected) -> bool:
    """Compare an expected number with a value that may carry a unit suffix ('2 项', '200 件')."""
    if isinstance(expected, str):
        return str(actual).strip() == expected.strip()
    try:
        return abs(float(str(actual).replace(',', '').strip()) - float(expected)) < 1e-9
    except (TypeError, ValueError):
        match = re.search(r'-?\d+(?:\.\d+)?', str(actual))
        if not match:
            return False
        return abs(float(match.group(0)) - float(expected)) < 1e-9


def _contains_number(text: str, value) -> bool:
    if isinstance(value, str):
        return value in text
    return re.search(r'(?<![\d.])%s(?![\d.])' % re.escape(str(value)), text) is not None


def check_assertion(path: Path, sample: dict, entry: dict) -> dict:
    """Recompute one question's assertion from the file / ground truth, without a model.

    Kinds: ``exact_text`` (expected string is present where it can be read or, for a scan,
    in the frozen ground truth), ``arith_sum``/``arith_diff``/``arith_product``,
    ``arith_ratio_percent``, ``count_matching`` and ``count_less_than`` (XLSX columns).
    """
    assertion = entry['assertion']
    kind = assertion['kind']
    result = {'kind': kind, 'expected': assertion.get('expected')}
    if kind == 'exact_text':
        expected = assertion['expected']
        if entry['checkable_from'] == 'file_text':
            text = extract_text(path)
            ok = expected in text
            result['observed'] = f'text contains {expected!r}: {ok}'
        elif entry['checkable_from'] == 'ground_truth_only':
            ok = sample['ground_truth'][assertion['ground_truth_key']] == expected
            result['observed'] = 'ground_truth[%s]=%r' % (
                assertion['ground_truth_key'], sample['ground_truth'][assertion['ground_truth_key']])
        else:
            text = extract_text(path)
            ok = expected in text
            result['observed'] = f'text contains {expected!r}: {ok}'
        result['ok'] = bool(ok)
        return result
    operands = list(assertion.get('operands') or [])
    if kind == 'arith_sum':
        computed = sum(operands)
    elif kind == 'arith_diff':
        computed = operands[0] - sum(operands[1:])
    elif kind == 'arith_product':
        computed = 1
        for value in operands:
            computed *= value
    elif kind == 'arith_ratio_percent':
        computed = round(operands[0] / assertion['denominator'] * 100)
    elif kind == 'count_matching':
        computed = _count_cells(path, assertion['column'], assertion['equals'])
    elif kind == 'count_less_than':
        computed = _count_less(path, assertion['columns'])
    else:
        raise ValueError(f'unknown assertion kind: {kind}')
    result['computed'] = computed
    result['ok'] = computed == assertion['expected']
    sources = [resolve_operand(path, sample, source['pointer'], source['value'])
               for source in assertion.get('operand_sources') or []]
    result['operand_sources'] = [detail for _, detail in sources]
    result['operand_sources_ok'] = all(ok for ok, _ in sources)
    return result


def _count_cells(path: Path, column_pointer: str, equals) -> int:
    """Count XLSX rows whose first column equals ``equals`` (header row excluded by value)."""
    sheet_name, column = column_pointer.split('!', 1)
    index = 0
    for char in column:
        index = index * 26 + (ord(char.upper()) - 64)
    import openpyxl
    workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        sheet = workbook[sheet_name]
        count = 0
        for row in sheet.iter_rows(min_col=index, max_col=index, values_only=True):
            if row and row[0] is not None and str(row[0]).strip() == str(equals):
                count += 1
        return count
    finally:
        workbook.close()


def _count_less(path: Path, columns) -> int:
    """Count XLSX rows where the left column is strictly below its safety column."""
    first, second = columns
    sheet_name, left = first.split('!', 1)
    _, right = second.split('!', 1)
    left_index = sum((ord(char.upper()) - 64) * 26 ** (len(left) - 1 - position)
                     for position, char in enumerate(left))
    right_index = sum((ord(char.upper()) - 64) * 26 ** (len(right) - 1 - position)
                      for position, char in enumerate(right))
    import openpyxl
    workbook = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        sheet = workbook[sheet_name]
        count = 0
        for row in sheet.iter_rows(min_col=min(left_index, right_index),
                                   max_col=max(left_index, right_index), values_only=True):
            offset = min(left_index, right_index) - 1
            value = row[left_index - 1 - offset] if len(row) > left_index - 1 - offset else None
            safety = row[right_index - 1 - offset] if len(row) > right_index - 1 - offset else None
            if value is None or safety is None or isinstance(value, str) or isinstance(safety, str):
                continue
            if float(value) < float(safety):
                count += 1
        return count
    finally:
        workbook.close()


# --------------------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------------------

def _git_commit() -> str | None:
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=str(ROOT),
                              check=True, capture_output=True, text=True).stdout.strip() or None
    except Exception:
        return None


def manifest_digest(samples) -> str:
    """Digest over the sample list only, so regenerating identical content is idempotent."""
    material = json.dumps(samples, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def _scan_font_record() -> dict | None:
    for candidate in SCAN_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return {'path': str(path),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return None


def build_manifest() -> dict:
    samples, missing = [], []
    for spec in SAMPLES:
        writer = WRITERS.get(spec['file'])
        if writer is None:
            missing.append(spec['file'])
            continue
        data = writer()
        entry = {
            'file': spec['file'],
            'format': spec['format'],
            'suffix': spec['suffix'],
            'description': spec['description'],
            'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'pages': spec.get('pages'),
            'text_layer': spec.get('text_layer', True),
            'expected_locator_kinds': spec['expected_locator_kinds'],
            'expected_locator_fields': {kind: LOCATOR_REQUIRED_FIELDS[kind]
                                        for kind in spec['expected_locator_kinds']},
            'ground_truth': spec['ground_truth'],
            'questions': spec['questions'],
        }
        samples.append(entry)
    if missing:
        raise SystemExit('no writer for: ' + ', '.join(missing))
    return {
        'suite': SUITE,
        'suite_version': SUITE_VERSION,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'commit': _git_commit(),
        'generator': 'data/qa_samples/build_samples.py',
        'frozen': True,
        'determinism': {
            'zip_timestamp': list(FIXED_ZIP_DT),
            'ooxml_core_properties': FIXED_ISO,
            'pdf_metadata': FIXED_PDF_DATE,
            'pdf_writer': 'pymupdf tobytes(no_new_id=True, reproducible=True)',
            'dxf_normalisation': 'fixed julian timestamps + fixed GUIDs + fixed build comment',
            'scan_renderer': 'Pillow RGB + fixed CJK font, 150 dpi page placement',
            'scan_font': _scan_font_record(),
        },
        'counts': {
            'samples': len(samples),
            'formats': sorted({entry['format'] for entry in samples}),
            'questions': sum(len(entry['questions']) for entry in samples),
        },
        'note': ('问答样本集；答案断言可由 tests/test_qa_samples.py 从文件本身或冻结的 '
                 'ground_truth 复核。两个扫描件无文本层，其证据是确定性渲染用的 ground_truth。'),
        'samples': samples,
        'digest': manifest_digest(samples),
    }


def check_manifest(path: Path = MANIFEST_PATH) -> tuple[bool, list[str]]:
    """Verify every frozen sample's size + SHA-256 and the manifest digest itself.

    Sample paths resolve against ``path.parent`` so a copy of the manifest can be verified
    (and deliberately tampered with) inside a temporary directory without touching the
    frozen originals.
    """
    problems = []
    if not path.is_file():
        return False, [f'manifest missing: {path}']
    manifest = json.loads(path.read_text(encoding='utf-8'))
    for entry in manifest.get('samples', []):
        target = path.parent / entry['file']
        if not target.is_file():
            problems.append(f"missing file: {entry['file']}")
            continue
        data = target.read_bytes()
        if len(data) != entry['size']:
            problems.append(f"size mismatch: {entry['file']} frozen={entry['size']} actual={len(data)}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry['sha256']:
            problems.append(f"sha256 mismatch: {entry['file']} frozen={entry['sha256']} actual={digest}")
    recomputed = manifest_digest(manifest.get('samples', []))
    if recomputed != manifest.get('digest'):
        problems.append(f'manifest digest mismatch: frozen={manifest.get("digest")} recomputed={recomputed}')
    return (not problems), problems


def write_samples(force: bool = False) -> dict:
    if MANIFEST_PATH.exists() and not force:
        raise SystemExit('manifest already frozen (append-only): %s\n'
                         'use --check to verify or --force to regenerate' % MANIFEST_PATH)
    manifest = build_manifest()
    for entry in manifest['samples']:
        spec = next(item for item in SAMPLES if item['file'] == entry['file'])
        data = WRITERS[spec['file']]()
        (SAMPLES_DIR / entry['file']).write_bytes(data)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true',
                        help='verify the frozen samples and manifest, write nothing')
    parser.add_argument('--force', action='store_true',
                        help='regenerate and overwrite the frozen manifest')
    parser.add_argument('--print', action='store_true', help='print the manifest to stdout')
    args = parser.parse_args(argv)

    if args.check:
        ok, problems = check_manifest()
        print(json.dumps({'checked': str(MANIFEST_PATH), 'ok': ok, 'problems': problems},
                         ensure_ascii=False))
        return 0 if ok else 1

    manifest = write_samples(force=args.force)
    if args.print:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print('已生成 %d 份样本（%d 个问题），格式：%s' % (
        manifest['counts']['samples'], manifest['counts']['questions'],
        ','.join(manifest['counts']['formats'])))
    print('manifest digest：%s' % manifest['digest'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
