"""Build the frozen CAD capability acceptance suite (``cad-capability-v1``).

这是**作者自建的合成验收集**，不是独立工厂数据。它存在的理由只有一个：审查
``reports/project_audit_20261009_561316c.md`` 5.5 指出 CAD v2 的能力证据目前只来自
"开发中用来修 bug 的两份真实图纸"，那两份图纸因此**不能同时充当独立泛化测试集**。
本套件把能力**逐层**（单位 / 版本 / 句柄 / 实体 / 布局 / 声明式拒绝）拆成可复核的
材料 + 冻结标签，两份真实图纸另在 ``data/cad_demo/DEVELOPMENT_SAMPLES.md`` 登记为
**开发样本**。

诚实边界（报告里也重复一次，防止数字被挪用）：

* 材料由 ``ezdxf`` 与手写 ASCII 标签流生成，是**作者生成的合成样本**，不代表工厂图纸
  分布；本套件的通过率**不是**"识别准确率"，**不是**泛化准确率。
* 标签在材料冻结时一并冻结（``manifest.json`` 记 ``labels_sha256``）：材料被改动而
  标签不变时，评测脚本**拒绝评测**而不是给出分数。
* 几何测量值（``geometry_measurement``）与文字声明的尺寸（``declared_text``）分开
  断言，绝不把几何值当制造尺寸。

确定性：``ezdxf`` 写出的 DXF 含时间戳与随机 GUID，本脚本在写盘前把它们钉成常量，
所以同一 ``ezdxf`` 版本重复生成得到**逐字节相同**的材料。

用法::

    .venv/bin/python data/evaluation/cad_capability_v1/build_suite.py            # 生成/重建
    .venv/bin/python data/evaluation/cad_capability_v1/build_suite.py --check     # 只校验冻结值
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import ezdxf

SUITE_ID = 'cad-capability-v1'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MATERIALS_DIR = HERE / 'materials'
LABELS_PATH = HERE / 'labels.json'
MANIFEST_PATH = HERE / 'manifest.json'

#: 被评代码（生产路径）——报告的 ``integrity.code_files`` 逐一记 sha256。
EVALUATED_CODE = ('ingestion/cad_adapter.py', 'ingestion/cad_dxf_v2.py',
                  'ingestion/cad_contract_v2.py', 'datamind/parse_contract.py')
PINNED_V2_MANIFEST = 'data/evaluation/frozen/cad_dxf_v2/manifest.json'
DEVELOPMENT_SAMPLES = 'data/cad_demo/DEVELOPMENT_SAMPLES.md'

#: 任何一例都不该出现的码（除非该例显式把它当作期望）。用 ``CODE`` 前缀匹配。
GLOBAL_ABSENT_CODES = (
    'CAD_ENTITY_LOSS_OR_LAYOUT_UNSUPPORTED',
    'CAD_BLOCK_ENTITY_LOSS_UNSUPPORTED',
    'CAD_OUTPUT_CONTRACT_INVALID',
    'CAD_DXF_INVALID',
    'CAD_DXF_TAGS_INVALID',
    'CAD_BINARY_DXF_UNSUPPORTED',
    'CAD_ENTITY_COUNT_INVALID',
    'CAD_SOURCE_SIZE_INVALID',
    'CAD_DXF_AUDIT_ERRORS',
    'CAD_NO_SUPPORTED_ENTITIES',
    'PARSE_ADAPTER_UNSUPPORTED',
    'PARSE_RESULT_INVALID',
    'block_limit_reached',
    'CAD_ENTITY_LIMIT_REACHED',
)

LAYERS = ('unit', 'version', 'handle', 'entity', 'layout', 'rejected')

UNCOVERED = [
    {'capability': 'DWG', 'reason': '专有格式，需外部转换器；读取器以 CAD_DWG_CONVERTER_UNAVAILABLE 明确拒绝，需真实转换器环境才能评测。'},
    {'capability': 'DXF AC1012/AC1014 (R13/R14)', 'reason': "ezdxf 1.4.4 只能写 AC1009 与 AC1015+；R13/R14 无法在本环境生成材料。"},
    {'capability': 'AC1009 (R12) 的 LWPOLYLINE/MTEXT/DIMENSION', 'reason': 'ezdxf 以 DXFVersionError 拒绝在 R12 写这些实体；R12 材料只含 R12 合法实体。'},
    {'capability': '二进制 DXF', 'reason': '未构造材料；读取器以 CAD_BINARY_DXF_UNSUPPORTED 拒绝。'},
    {'capability': '非 UTF-8 / $DWGCODEPAGE 回退编码', 'reason': '材料均为 UTF-8；CAD_ENCODING_FALLBACK 与文本保真度未覆盖。'},
    {'capability': '$INSUNITS 的其他取值（2=ft,3=mi,5 之外的非建模码等）', 'reason': '只覆盖 0/1/4 与一个非建模码 5(cm)；其余取值同为 CAD_UNIT_CODE_NOT_MODELLED 分支，但未逐一枚举。'},
    {'capability': '非法格式句柄 / $HANDLING 显式与标签矛盾', 'reason': '只覆盖重复句柄与"部分带句柄"；CAD_ENTITY_HANDLE_INVALID 的非法格式分支与 CAD_HANDLING_FLAG_INCONSISTENT 未覆盖。'},
    {'capability': '折线宽度 / 三维或网格折线', 'reason': '只覆盖 bulge；CAD_POLYLINE_WIDTH_UNSUPPORTED、CAD_POLYLINE_3D_OR_MESH_UNSUPPORTED 未覆盖。'},
    {'capability': '文字变形（oblique/width/halign/valign/generation flag）与超长 MTEXT 截断', 'reason': 'CAD_TEXT_TRANSFORM_UNSUPPORTED、CAD_MTEXT_TRUNCATED、CAD_TEXT_CONTROL_CHARACTER_UNSUPPORTED 未覆盖。'},
    {'capability': '块内 DIMENSION、非均匀/零缩放 INSERT、MINSERT 阵列、块深度上限、展开实体上限、声明条数截断', 'reason': 'CAD_DIMENSION_IN_BLOCK_UNSUPPORTED、CAD_INSERT_*、CAD_MINSERT_ARRAY_UNSUPPORTED、CAD_BLOCK_DEPTH_LIMIT_REACHED、CAD_UNSUPPORTED_ITEMS_TRUNCATED 未覆盖。'},
    {'capability': 'CAD_BLOCK_RECURSION_CYCLE 的读取器防御分支', 'reason': '本轮实测循环块引用被 ezdxf 审计先行以 CAD_DXF_AUDIT_ERRORS 整图拒绝，读取器的 CAD_BLOCK_RECURSION_CYCLE 分支未能在材料上触达（防御性代码路径）。'},
    {'capability': '直径特征（DATAMIND XDATA 关联）与 LLM 抽屉合同层 declared_sizes 取值纪律', 'reason': '本套件只评 adapter/reader 路径；直径 XDATA 与 cad_contract_v2.declared_sizes 的排除码属合同层，未纳入。'},
    {'capability': '纸空间实体读取本身', 'reason': '读取器按设计声明跳过纸空间（CAD_PAPERSPACE_ENTITIES_SKIPPED），从不读取；套件只断言"声明跳过"，不声称支持纸空间几何。'},
    {'capability': 'positional 模式下的块展开', 'reason': '读取器按设计不展开（CAD_POSITIONAL_BLOCK_UNSUPPORTED），套件只断言"逐项声明 + skipped"。'},
    {'capability': '预览/渲染（ingestion/cad_render.py）', 'reason': 'cad_render 只校验 v1 的 DrawingSnapshot，v2 实体不可渲染；本套件不评渲染。'},
    {'capability': '真实工厂图纸分布与泛化准确率', 'reason': '本套件是作者合成样本；真实图纸只作开发样本，见 data/cad_demo/DEVELOPMENT_SAMPLES.md。任何"准确率"结论都超出本套件证据范围。'},
]

NOT_MEASURED = [
    '本套件是作者自建合成验收集，不是独立工厂数据，也不是盲测。',
    '通过率是"逐项断言通过比例"，不是"识别准确率"或"泛化准确率"；报告不给出 accuracy 指标。',
    'geometry_measurement（几何测量值）不得当作制造尺寸；只有 declared_text/declared_value 才是文字声明的尺寸。',
    '验收集不含任何真实图纸；两份真实图纸是开发样本，其缺陷已被用于修改读取器。',
    '评测不调用任何模型、数据库或网络；不写入真实业务库。',
]

# --------------------------------------------------------------------------------------
# 确定性：钉住 ezdxf 写出的时间戳与随机 GUID
# --------------------------------------------------------------------------------------
_FIXED_JULIAN = '2461323.5'
_FIXED_GUID = '{00000000-0000-0000-0000-00000000CADE}'
_FIXED_SAVEDBY = 'cad-capability-suite-v1'


def _pin_header(text, name, value):
    pattern = re.compile(r'([ \t]*9\r?\n' + re.escape(name) + r'\r?\n[ \t]*\d+\r?\n)[^\r\n]*')
    return pattern.sub(lambda match: match.group(1) + value, text, count=1)


def _pin_dictionary_variables(text):
    pattern = re.compile(r'(DictionaryVariables\r?\n[ \t]*280\r?\n[ \t]*0\r?\n[ \t]*1\r?\n)'
                         r'([^\r\n]*@[^\r\n]*)')
    text = pattern.sub(lambda match: match.group(1) + 'ezdxf-pinned @ 2026-01-01T00:00:00+00:00',
                       text)
    # R12 keeps the same stamp as XDATA (`1000` values of the EZDXF dictionary), so the
    # DictionaryVariables rewrite above does not reach it.
    stamp = re.compile(r'(\d+\.\d+\.\d+ @ )\d{4}-\d{2}-\d{2}T[0-9:.+\-]+')
    return stamp.sub(lambda match: match.group(1) + '2026-01-01T00:00:00+00:00', text)


def _strip_header_var(text, name):
    pattern = re.compile(r'[ \t]*9\r?\n' + re.escape(name) + r'\r?\n[ \t]*\d+\r?\n[^\r\n]*\r?\n')
    return pattern.sub('', text, count=1)


def _normalize_class_records(text):
    """按类名排定 ``CLASSES`` 段内的 ``CLASS`` 记录。

    ``ezdxf`` 1.4.4 在同一份图纸上会把 ``LAYOUT`` 与 ``ACDBPLACEHOLDER`` 两条 CLASS 记录
    以**依赖进程哈希种子**的顺序写出（``set``/``dict`` 迭代顺序），于是同一版本重复生成的
    字节在不同进程里不同。CLASS 记录不带句柄、彼此独立，按类名排序不改变语义，却让材料
    逐字节可复现——冻结套件必须做到这一点，否则 sha256 守护会随机失败。
    """
    lines = text.split('\n')
    start = next((i for i, line in enumerate(lines)
                  if line.strip() == 'CLASSES' and i >= 1 and lines[i - 1].strip() == '2'), None)
    if start is None:
        return text
    end = next((j - 1 for j in range(start + 1, len(lines))
                if lines[j].strip() == 'ENDSEC' and lines[j - 1].strip() == '0'), None)
    if end is None:
        return text
    body = lines[start + 1:end]
    if len(body) % 2:
        return text
    pairs = [(body[index], body[index + 1]) for index in range(0, len(body), 2)]
    records, current = [], []
    for pair in pairs:
        # DXF tags alternate code/value, so only a tag in *code* position can start a record;
        # a value line that happens to read "0" (group 90/280/281) must not split it.
        if pair[0].strip() == '0' and current:
            records.append(current)
            current = []
        current.append(pair)
    if current:
        records.append(current)
    if not records or not all(record and record[0][0].strip() == '0'
                              and record[0][1].strip() == 'CLASS' for record in records):
        return text

    def key(record):
        return next((value for code, value in record if code.strip() == '1'), '')

    records.sort(key=key)
    lines[start + 1:end] = [line for record in records for pair in record for line in pair]
    return '\n'.join(lines)


def normalize_dxf(text):
    """把 ezdxf 的易变字段钉成常量，使同一版本重复生成的字节完全一致。"""
    for name in ('$TDCREATE', '$TDUPDATE'):
        text = _pin_header(text, name, _FIXED_JULIAN)
    for name in ('$FINGERPRINTGUID', '$VERSIONGUID'):
        text = _pin_header(text, name, _FIXED_GUID)
    text = _pin_header(text, '$LASTSAVEDBY', _FIXED_SAVEDBY)
    text = _pin_dictionary_variables(text)
    return _normalize_class_records(text)


def _write_doc(doc, *, crlf=False, strip_insunits=False):
    stream = io.StringIO()
    doc.write(stream)
    text = normalize_dxf(stream.getvalue())
    if strip_insunits:
        text = _strip_header_var(text, '$INSUNITS')
    if crlf:
        text = text.replace('\n', '\r\n')
    return text


# --------------------------------------------------------------------------------------
# 材料构造
# --------------------------------------------------------------------------------------
def _new(version, unit=None):
    doc = ezdxf.new(version, setup=True)
    if unit is not None and version != 'AC1009':
        # R12 不导出 $INSUNITS；对 R12 传 unit 会得到一个源里并不存在的声明。
        doc.header['$INSUNITS'] = unit
    return doc


def _primitives(doc):
    """LINE + CIRCLE + ARC + 旋转 TEXT + MTEXT（v2 支持集的核心五类）。"""
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_circle((5, 5), 2)
    model.add_arc((0, 0), 3, 0, 90)
    model.add_text('HELLO', dxfattribs={'rotation': 30, 'height': 2.5}).set_placement((1, 1))
    model.add_mtext('M1\\PM2').set_location((2, 2))
    return doc


def _two_entities(doc):
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.modelspace().add_circle((5, 5), 2)
    return doc


def _r12_entities(doc):
    """R12 合法子集：LINE/CIRCLE/ARC/POLYLINE2d/TEXT（无 LWPOLYLINE/MTEXT/DIMENSION）。"""
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_circle((5, 5), 2)
    model.add_arc((0, 0), 3, 0, 90)
    model.add_polyline2d([(1, 1), (2, 2), (3, 1)], close=True)
    model.add_text('HELLO', dxfattribs={'rotation': 30, 'height': 2.5}).set_placement((1, 1))
    return doc


def _version_entities(doc):
    """R2000+ 合法集：上面五类 + LWPOLYLINE + MTEXT。"""
    _primitives(doc)
    doc.modelspace().add_lwpolyline([(0, 0), (5, 0), (5, 5)], close=True)
    return doc


def _polyline_with_bulge(doc):
    """两条 POLYLINE：一条平直闭合，一条带 bulge（应逐项声明且不产出几何）。"""
    model = doc.modelspace()
    model.add_polyline2d([(0, 0), (5, 0), (5, 5)], close=True)
    bulged = model.add_polyline2d([(10, 0), (15, 0), (15, 5)], close=False)
    bulged.vertices[0].dxf.bulge = 0.5
    return doc


def _lwpolyline_closed_and_open(doc):
    model = doc.modelspace()
    model.add_lwpolyline([(0, 0), (5, 0), (5, 5)], close=True)
    model.add_lwpolyline([(10, 0), (15, 0)], close=False)
    return doc


def _dimensions(doc):
    """一个未覆盖文字的 DIMENSION（<> 由几何生成）与一个文字声明 25.4 的 DIMENSION。"""
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_linear_dim(base=(0, -3), p1=(0, 0), p2=(10, 0),
                         override={'dimtxt': 2.5}).render()
    model.add_linear_dim(base=(0, -6), p1=(0, 0), p2=(10, 0), text='25.4',
                         override={'dimtxt': 2.5}).render()
    return doc


def _nested_blocks(doc):
    inner = doc.blocks.new('CAP_INNER')
    inner.add_line((0, 0), (1, 0))
    outer = doc.blocks.new('CAP_OUTER')
    outer.add_line((0, 0), (2, 0))
    outer.add_blockref('CAP_INNER', (1, 1))
    model = doc.modelspace()
    model.add_blockref('CAP_OUTER', (5, 5))
    model.add_blockref('CAP_INNER', (20, 20))
    return doc


def _block_cycle(doc):
    first = doc.blocks.new('CAP_A')
    second = doc.blocks.new('CAP_B')
    first.add_line((0, 0), (1, 0))
    second.add_line((0, 0), (1, 0))
    first.add_blockref('CAP_B', (0, 0))
    second.add_blockref('CAP_A', (0, 0))
    doc.modelspace().add_blockref('CAP_A', (0, 0))
    return doc


def _block_missing(doc):
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.modelspace().add_blockref('CAP_NOPE', (5, 5))
    return doc


def _spline_only(doc):
    doc.modelspace().add_spline([(0, 0), (5, 5), (10, 0)])
    return doc


def _mixed_unsupported(doc):
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_spline([(10, 0), (15, 5), (20, 0)])
    model.add_hatch(color=2).paths.add_polyline_path([(0, 0), (5, 0), (5, 5)], is_closed=True)
    model.add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    return doc


def _paperspace(doc):
    doc.modelspace().add_line((0, 0), (10, 0))
    layout = doc.layout('Layout1')
    layout.add_line((0, 0), (5, 5))
    layout.add_text('PAPERSPACE', dxfattribs={'height': 2}).set_placement((0, 10))
    return doc


def _three_dimensional(doc):
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_line((0, 0, 5), (10, 0, 5))
    return doc


def _ocs_extrusion(doc):
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_circle((5, 5), 2, dxfattribs={'extrusion': (0, 1, 0)})
    return doc


def _hidden_layer(doc):
    doc.layers.add('CAP_HIDDEN', color=1).off()
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_line((0, 1), (10, 1), dxfattribs={'layer': 'CAP_HIDDEN'})
    return doc


def _model_only(doc):
    doc.modelspace().add_line((0, 0), (10, 0))
    return doc


# --------------------------------------------------------------------------------------
# 手写标签流：句柄"一个都没有"、重复句柄、"部分带句柄"这三种形态 ezdxf 写不出来
# --------------------------------------------------------------------------------------
def _pairs(items):
    return ''.join(f'{code}\n{value}\n' for code, value in items)


def _handleless_source(*, with_block=True, crlf=False):
    """真正不含 ``code 5`` 的 AC1009 图纸：2 条 POLYLINE + 1 个 INSERT。

    ezdxf 的写入器总会输出句柄，所以这个源形态只能手写。``with_block=True`` 时块
    ``CAP_PART`` 内含一条同样无句柄的 LINE，用于断言 positional 模式**不展开块**。
    """
    header = [('0', 'SECTION'), ('2', 'HEADER'), ('9', '$ACADVER'), ('1', 'AC1009'),
              ('9', '$EXTMIN'), ('10', '0.0'), ('20', '0.0'), ('30', '0.0'),
              ('9', '$EXTMAX'), ('10', '20.0'), ('20', '20.0'), ('30', '0.0'),
              ('0', 'ENDSEC')]
    tables = [('0', 'SECTION'), ('2', 'TABLES'),
              ('0', 'TABLE'), ('2', 'LAYER'), ('70', '1'),
              ('0', 'LAYER'), ('2', '0'), ('70', '0'), ('62', '7'), ('6', 'CONTINUOUS'),
              ('0', 'ENDTAB'), ('0', 'ENDSEC')]
    blocks = [('0', 'SECTION'), ('2', 'BLOCKS')]
    if with_block:
        blocks += [('0', 'BLOCK'), ('8', '0'), ('2', 'CAP_PART'), ('70', '0'),
                   ('10', '0.0'), ('20', '0.0'), ('30', '0.0'),
                   ('0', 'LINE'), ('8', '0'), ('10', '0.0'), ('20', '0.0'), ('30', '0.0'),
                   ('10', '1.0'), ('20', '0.0'), ('30', '0.0'),
                   ('0', 'ENDBLK'), ('8', '0')]
    blocks += [('0', 'ENDSEC')]
    entities = [('0', 'SECTION'), ('2', 'ENTITIES')]
    for vertices in (((0, 0), (5, 0), (5, 5)), ((10, 10), (15, 10))):
        entities += [('0', 'POLYLINE'), ('8', '0'), ('66', '1'),
                     ('10', '0.0'), ('20', '0.0'), ('30', '0.0')]
        for x, y in vertices:
            entities += [('0', 'VERTEX'), ('8', '0'), ('10', str(float(x))),
                         ('20', str(float(y))), ('30', '0.0')]
        entities += [('0', 'SEQEND'), ('8', '0')]
    if with_block:
        entities += [('0', 'INSERT'), ('8', '0'), ('2', 'CAP_PART'),
                     ('10', '2.0'), ('20', '2.0'), ('30', '0.0')]
    entities += [('0', 'ENDSEC'), ('0', 'EOF')]
    text = _pairs(header + tables + blocks + entities)
    return text.replace('\n', '\r\n') if crlf else text


def _handles_source(*, duplicate=False, partial=False):
    """带 ``code 5`` 的最小 AC1009 源：两条 LINE。

    ``duplicate=True`` 两条记录共用 ``5/AA`` → 重复句柄整图拒绝。
    ``partial=True`` 只有第一条带 ``5/AA`` → "部分记录带句柄"整图拒绝。
    """
    header = [('0', 'SECTION'), ('2', 'HEADER'), ('9', '$ACADVER'), ('1', 'AC1009'),
              ('9', '$HANDLING'), ('70', '1'), ('0', 'ENDSEC')]
    tables = [('0', 'SECTION'), ('2', 'TABLES'),
              ('0', 'TABLE'), ('2', 'LAYER'), ('70', '1'),
              ('0', 'LAYER'), ('2', '0'), ('70', '0'), ('62', '7'), ('6', 'CONTINUOUS'),
              ('0', 'ENDTAB'), ('0', 'ENDSEC')]
    entities = [('0', 'SECTION'), ('2', 'ENTITIES')]
    for index, (start, end) in enumerate((((0, 0), (10, 0)), ((0, 5), (10, 5)))):
        record = [('0', 'LINE'), ('8', '0')]
        if duplicate or (partial and index == 0):
            record += [('5', 'AA')]
        record += [('10', str(float(start[0]))), ('20', str(float(start[1]))), ('30', '0.0'),
                   ('11', str(float(end[0]))), ('21', str(float(end[1]))), ('31', '0.0')]
        entities += record
    entities += [('0', 'ENDSEC'), ('0', 'EOF')]
    return _pairs(header + tables + entities)


# --------------------------------------------------------------------------------------
# 用例清单：材料 + 冻结标签
# --------------------------------------------------------------------------------------
def _absent(case_expected):
    """全局禁用码去掉该例自己的期望码后的清单。"""
    expected = set(case_expected.get('unsupported_codes') or [])
    expected |= set(case_expected.get('error_codes_any') or [])
    return [code for code in GLOBAL_ABSENT_CODES if code not in expected]


def _case(case_id, *, file, layers, build, unit, identity_mode, parse_status='parsed',
          error_codes_any=None, unsupported_codes=None, entity_counts=None,
          block_counts=None, feature_counts=None, entity_total=None, reader=None,
          extra_absent=None):
    expected = {
        'parse_status': parse_status,
        'unit': unit,
        'identity_mode': identity_mode,
        'locator_layout': 'Model' if unit is not None else None,
        'entity_counts': entity_counts or {},
        'block_counts': block_counts or {},
        'feature_counts': feature_counts or {},
        'entity_total': entity_total,
        'unsupported_codes': list(unsupported_codes or []),
        'error_codes_any': list(error_codes_any or []),
        'reader': reader or {},
    }
    absent = _absent(expected)
    if extra_absent:
        absent = sorted(set(absent) | set(extra_absent))
    expected['absent_codes'] = absent
    return {'case_id': case_id, 'file': file, 'layers': list(layers),
            'document_id': 'doc-capsuite-' + case_id.replace('_', '-'),
            'build': build, 'expected': expected}


def case_specs():
    """The frozen suite: one entry per material, labels written before the first run."""
    specs = [
        _case('unit_in', file='unit_in.dxf', layers=['unit', 'layout'],
              build=lambda: _write_doc(_two_entities(_new('AC1027', 1))),
              unit='in', identity_mode='source', entity_counts={'LINE': 1, 'CIRCLE': 1},
              entity_total={'min': 2, 'max': 2},
              reader={'entities': [{'match': {'entity_type': 'LINE'}, 'expect': {'start': {'x': 0.0, 'y': 0.0}, 'end': {'x': 10.0, 'y': 0.0}}}]}),
        _case('unit_mm', file='unit_mm.dxf', layers=['unit', 'layout'],
              build=lambda: _write_doc(_two_entities(_new('AC1027', 4))),
              unit='mm', identity_mode='source', entity_counts={'LINE': 1, 'CIRCLE': 1},
              entity_total={'min': 2, 'max': 2}),
        _case('unit_missing', file='unit_missing.dxf', layers=['unit', 'version', 'layout'],
              build=lambda: _write_doc(_two_entities(_new('AC1027', 4)), strip_insunits=True),
              unit='unknown', identity_mode='source', entity_counts={'LINE': 1, 'CIRCLE': 1},
              extra_absent=['CAD_UNIT_CODE_NOT_MODELLED'],
              reader={'entities': [{'match': {'entity_type': 'CIRCLE'}, 'expect': {'radius': 2.0}}]}),
        _case('unit_cm_not_modelled', file='unit_cm_not_modelled.dxf', layers=['unit'],
              build=_cm_not_modelled_doc,
              unit='unknown', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_UNIT_CODE_NOT_MODELLED:5'],
              entity_counts={'LINE': 1}),

        _case('version_r12', file='version_r12.dxf', layers=['version', 'entity', 'handle'],
              build=_r12_doc,
              unit='unknown', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'POLYLINE': 1, 'TEXT': 1},
              entity_total={'min': 5, 'max': 5},
              reader={'entities': [{'match': {'entity_type': 'POLYLINE'}, 'expect': {'closed': True}},
                                   {'match': {'entity_type': 'TEXT'}, 'expect': {'rotation': 30.0}},
                                   {'match': {'entity_type': 'ARC'}, 'expect': {'start_angle': 0.0, 'end_angle': 90.0}}]}),
        _case('version_r2000', file='version_r2000.dxf', layers=['version', 'entity'],
              build=lambda: _version_doc('AC1015', 4),
              unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'LWPOLYLINE': 1, 'TEXT': 1, 'MTEXT': 1},
              entity_total={'min': 6, 'max': 6}),
        _case('version_r2004', file='version_r2004.dxf', layers=['version'],
              build=lambda: _version_doc('AC1018', 4), unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'LWPOLYLINE': 1, 'TEXT': 1, 'MTEXT': 1}),
        _case('version_r2007', file='version_r2007.dxf', layers=['version'],
              build=lambda: _version_doc('AC1021', 4), unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'LWPOLYLINE': 1, 'TEXT': 1, 'MTEXT': 1}),
        _case('version_r2010', file='version_r2010.dxf', layers=['version'],
              build=lambda: _version_doc('AC1024', 4), unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'LWPOLYLINE': 1, 'TEXT': 1, 'MTEXT': 1}),
        _case('version_r2013', file='version_r2013.dxf', layers=['version'],
              build=lambda: _version_doc('AC1027', 4), unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'LWPOLYLINE': 1, 'TEXT': 1, 'MTEXT': 1}),
        _case('version_r2018', file='version_r2018.dxf', layers=['version'],
              build=lambda: _version_doc('AC1032', 4), unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'LWPOLYLINE': 1, 'TEXT': 1, 'MTEXT': 1}),

        _case('handle_source_lf', file='handle_source_lf.dxf', layers=['handle', 'layout'],
              build=lambda: _handle_source_doc(4, crlf=False), unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'TEXT': 1},
              reader={'require_handle_present': True}),
        _case('handle_source_crlf', file='handle_source_crlf.dxf', layers=['handle'],
              build=lambda: _handle_source_doc(4, crlf=True), unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'TEXT': 1},
              reader={'require_handle_present': True}),
        _case('handle_positional_lf', file='handle_positional_lf.dxf',
              layers=['handle', 'entity', 'layout'],
              build=lambda: _handleless_source(with_block=True, crlf=False),
              unit='unknown', identity_mode='positional', parse_status='partial',
              unsupported_codes=['CAD_SOURCE_HANDLE_UNAVAILABLE', 'CAD_POSITIONAL_BLOCK_UNSUPPORTED'],
              entity_counts={'POLYLINE': 2, 'INSERT': 1},
              entity_total={'min': 3, 'max': 3},
              reader={'entity_indices': [1, 2, 3], 'require_no_handle': True, 'require_handle_present': False,
                      'entities': [{'match': {'entity_type': 'POLYLINE'}, 'expect': {'closed': False}},
                                   {'match': {'entity_type': 'INSERT'}, 'expect': {'expansion_status': 'skipped'}}]}),
        _case('handle_positional_crlf', file='handle_positional_crlf.dxf',
              layers=['handle', 'entity', 'layout'],
              build=lambda: _handleless_source(with_block=True, crlf=True),
              unit='unknown', identity_mode='positional', parse_status='partial',
              unsupported_codes=['CAD_SOURCE_HANDLE_UNAVAILABLE', 'CAD_POSITIONAL_BLOCK_UNSUPPORTED'],
              entity_counts={'POLYLINE': 2, 'INSERT': 1},
              reader={'entity_indices': [1, 2, 3], 'require_no_handle': True}),
        _case('handle_duplicate', file='handle_duplicate.dxf', layers=['handle', 'rejected'],
              build=lambda: _handles_source(duplicate=True), unit=None, identity_mode=None,
              parse_status='rejected', error_codes_any=['CAD_DUPLICATE_SOURCE_HANDLE']),
        _case('handle_partial', file='handle_partial.dxf', layers=['handle', 'rejected'],
              build=lambda: _handles_source(partial=True), unit=None, identity_mode=None,
              parse_status='rejected', error_codes_any=['CAD_ENTITY_HANDLE_INVALID']),

        _case('entity_primitives', file='entity_primitives.dxf', layers=['entity'],
              build=lambda: _write_doc(_primitives(_new('AC1027', 4))),
              unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'CIRCLE': 1, 'ARC': 1, 'TEXT': 1, 'MTEXT': 1},
              entity_total={'min': 5, 'max': 5},
              reader={'entities': [{'match': {'entity_type': 'TEXT'}, 'expect': {'text': 'HELLO', 'rotation': 30.0, 'height': 2.5}},
                                   {'match': {'entity_type': 'MTEXT'}, 'expect': {'text': 'M1\nM2', 'raw_text': 'M1\\PM2'}}]}),
        _case('entity_lwpolyline_closed', file='entity_lwpolyline_closed.dxf', layers=['entity'],
              build=lambda: _write_doc(_lwpolyline_closed_and_open(_new('AC1027', 4))),
              unit='mm', identity_mode='source', entity_counts={'LWPOLYLINE': 2},
              reader={'entities': [{'match': {'entity_type': 'LWPOLYLINE', 'occurrence': 0}, 'expect': {'closed': True}},
                                   {'match': {'entity_type': 'LWPOLYLINE', 'occurrence': 1}, 'expect': {'closed': False}}]}),
        _case('entity_polyline_bulge', file='entity_polyline_bulge.dxf', layers=['entity'],
              build=lambda: _write_doc(_polyline_with_bulge(_new('AC1027', 4))),
              unit='mm', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_POLYLINE_BULGE_UNSUPPORTED'],
              entity_counts={'POLYLINE': 1}, entity_total={'min': 1, 'max': 1},
              reader={'entities': [{'match': {'entity_type': 'POLYLINE'}, 'expect': {'closed': True}}]}),
        _case('entity_native_dimension', file='entity_native_dimension.dxf', layers=['entity'],
              build=lambda: _write_doc(_dimensions(_new('AC1027', 4))),
              unit='mm', identity_mode='source',
              entity_counts={'LINE': 1, 'DIMENSION': 2},
              block_counts={'figure': 1, 'paragraph': 2, 'dimension': 2},
              feature_counts={'native_dimension': 2},
              entity_total={'min': 3, 'max': 3},
              reader={'feature_count': 2,
                      'features': [{'index': 0, 'expect': {'value_authority': 'dimension_measurement',
                                                           'declared_text': None,
                                                           'geometry_measurement': '10.0'}},
                                   {'index': 1, 'expect': {'value_authority': 'declared_text',
                                                           'declared_text': '25.4',
                                                           'declared_value': '25.4',
                                                           'geometry_measurement': '10.0'}}]}),
        _case('entity_insert_nested', file='entity_insert_nested.dxf', layers=['entity'],
              build=lambda: _write_doc(_nested_blocks(_new('AC1027', 4))),
              unit='mm', identity_mode='source',
              entity_counts={'INSERT': 3, 'LINE': 3}, block_counts={'figure': 6},
              entity_total={'min': 6, 'max': 6},
              reader={'max_block_path_depth': 2, 'any': [{'match': {'entity_type': 'LINE', 'block_path_len': 2}}]}),
        _case('entity_block_cycle', file='entity_block_cycle.dxf', layers=['entity', 'rejected'],
              build=lambda: _write_doc(_block_cycle(_new('AC1027', 4))),
              unit=None, identity_mode=None, parse_status='rejected',
              error_codes_any=['CAD_DXF_AUDIT_ERRORS', 'CAD_BLOCK_RECURSION_CYCLE']),
        _case('entity_block_missing', file='entity_block_missing.dxf', layers=['entity', 'rejected'],
              build=lambda: _write_doc(_block_missing(_new('AC1027', 4))),
              unit='mm', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_BLOCK_MISSING', 'CAD_DXF_AUDIT_ENTITY_REMOVED'],
              entity_counts={'LINE': 1}, entity_total={'min': 1, 'max': 1}),

        _case('reject_spline_only', file='reject_spline_only.dxf', layers=['rejected', 'entity'],
              build=lambda: _write_doc(_spline_only(_new('AC1027', 4))),
              unit=None, identity_mode='source', parse_status='unsupported',
              unsupported_codes=['CAD_ENTITY_TYPE_UNSUPPORTED:SPLINE'],
              entity_total={'min': 0, 'max': 0}),
        _case('reject_mixed_unsupported', file='reject_mixed_unsupported.dxf', layers=['rejected', 'entity'],
              build=lambda: _write_doc(_mixed_unsupported(_new('AC1027', 4))),
              unit='mm', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_ENTITY_TYPE_UNSUPPORTED:SPLINE',
                                 'CAD_ENTITY_TYPE_UNSUPPORTED:HATCH',
                                 'CAD_ENTITY_TYPE_UNSUPPORTED:3DFACE'],
              entity_counts={'LINE': 1}, entity_total={'min': 1, 'max': 1}),
        _case('layout_paperspace', file='layout_paperspace.dxf', layers=['layout', 'rejected'],
              build=lambda: _write_doc(_paperspace(_new('AC1027', 4))),
              unit='mm', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_PAPERSPACE_ENTITIES_SKIPPED'],
              entity_counts={'LINE': 1}, entity_total={'min': 1, 'max': 1}),
        _case('reject_3d_z', file='reject_3d_z.dxf', layers=['rejected', 'entity'],
              build=lambda: _write_doc(_three_dimensional(_new('AC1027', 4))),
              unit='mm', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_3D_OR_NONFINITE_UNSUPPORTED'],
              entity_counts={'LINE': 1}, entity_total={'min': 1, 'max': 1}),
        _case('reject_ocs_extrusion', file='reject_ocs_extrusion.dxf', layers=['rejected', 'entity'],
              build=lambda: _write_doc(_ocs_extrusion(_new('AC1027', 4))),
              unit='mm', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_OCS_OR_THICKNESS_UNSUPPORTED'],
              entity_counts={'LINE': 1}, entity_total={'min': 1, 'max': 1}),
        _case('reject_hidden_layer', file='reject_hidden_layer.dxf', layers=['rejected', 'entity'],
              build=lambda: _write_doc(_hidden_layer(_new('AC1027', 4))),
              unit='mm', identity_mode='source', parse_status='partial',
              unsupported_codes=['CAD_HIDDEN_ENTITY_UNSUPPORTED'],
              entity_counts={'LINE': 1}, entity_total={'min': 1, 'max': 1}),
        _case('layout_model_only', file='layout_model_only.dxf', layers=['layout'],
              build=lambda: _write_doc(_model_only(_new('AC1027', 4))),
              unit='mm', identity_mode='source', entity_counts={'LINE': 1},
              entity_total={'min': 1, 'max': 1}),
    ]
    return specs


def _cm_not_modelled_doc():
    doc = _new('AC1027', 5)
    doc.modelspace().add_line((0, 0), (10, 0))
    return _write_doc(doc)


def _r12_doc():
    return _write_doc(_r12_entities(_new('AC1009')))


def _version_doc(version, unit):
    return _write_doc(_version_entities(_new(version, unit)))


def _handle_source_doc(unit, *, crlf):
    doc = _new('AC1027', unit)
    model = doc.modelspace()
    model.add_line((0, 0), (10, 0))
    model.add_circle((5, 5), 2)
    model.add_text('HELLO', dxfattribs={'rotation': 30, 'height': 2.5}).set_placement((1, 1))
    return _write_doc(doc, crlf=crlf)


# --------------------------------------------------------------------------------------
# 材料元数据（供覆盖度守护使用）
# --------------------------------------------------------------------------------------
_HEADER_VAR = r'9\r?\n{name}\r?\n[ \t]*\d+\r?\n([^\r\n]*)'


def _header_value(text, name):
    match = re.search(_HEADER_VAR.format(name=re.escape(name)), text)
    return match.group(1).strip() if match else None


def material_metadata(path):
    raw = path.read_bytes()
    text = raw.decode('utf-8', errors='replace')
    handle_tags = len(re.findall(r'(?m)^[ \t]*5\r?\n', text))
    crlf = text.count('\r\n')
    return {'dxf_version': _header_value(text, '$ACADVER'),
            'declared_insunits': _header_value(text, '$INSUNITS'),
            'declared_handling': _header_value(text, '$HANDLING'),
            'code_5_tags': handle_tags,
            'crlf_newlines': crlf,
            'lf_newlines': text.count('\n') - crlf,
            'bytes': len(raw)}


# --------------------------------------------------------------------------------------
# 生成 / 校验
# --------------------------------------------------------------------------------------
def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def labels_payload(specs):
    cases = []
    for spec in specs:
        cases.append({'case_id': spec['case_id'], 'material': 'materials/' + spec['file'],
                      'document_id': spec['document_id'], 'layers': spec['layers'],
                      'expected': spec['expected']})
    return {'suite_id': SUITE_ID,
            'provenance': 'author-built synthetic acceptance suite (not independent factory data)',
            'limitation': ('作者自建合成材料；两份真实图纸仅作开发样本 '
                           '(data/cad_demo/DEVELOPMENT_SAMPLES.md)。通过率不是识别准确率。'),
            'cases': cases}


def build(write=True):
    specs = case_specs()
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    materials = []
    for spec in specs:
        path = MATERIALS_DIR / spec['file']
        text = spec['build']()
        if not isinstance(text, str):  # pragma: no cover - a builder bug, not a data case
            raise TypeError(f'{spec["case_id"]}: builder must return str')
        if write:
            path.write_bytes(text.encode('utf-8'))
        materials.append((spec, path))
    labels = labels_payload(specs)
    labels_text = json.dumps(labels, ensure_ascii=False, indent=2) + '\n'
    if write:
        LABELS_PATH.write_text(labels_text, encoding='utf-8')
    manifest = {
        'suite_id': SUITE_ID,
        'suite_version': SUITE_ID,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'generator': 'data/evaluation/cad_capability_v1/build_suite.py',
        'generator_sha256': _sha256(Path(__file__).resolve()),
        'ezdxf_version': ezdxf.__version__,
        'python_version': sys.version,
        'profile': 'datamind-2d-engineering-dxf-v2',
        'parser_version': 'dxf-native-v2',
        'provenance': {
            'kind': 'author-built synthetic acceptance suite',
            'is_independent_factory_data': False,
            'real_drawings_used_for_grading': False,
            'development_samples': [DEVELOPMENT_SAMPLES],
            'measurement_path': 'ingestion.cad_adapter.parse (production v2 path)',
        },
        'evaluated_code': list(EVALUATED_CODE),
        'pinned_v2_manifest': PINNED_V2_MANIFEST,
        'labels_file': 'labels.json',
        'labels_sha256': hashlib.sha256(labels_text.encode('utf-8')).hexdigest(),
        'cases_count': len(specs),
        'layers': {layer: sum(1 for spec in specs if layer in spec['layers']) for layer in LAYERS},
        'materials': [
            {'case_id': spec['case_id'],
             'file': 'materials/' + spec['file'],
             'sha256': _sha256(path),
             'layers': spec['layers'],
             **material_metadata(path)}
            for spec, path in materials],
        'uncovered': UNCOVERED,
        'not_measured': NOT_MEASURED,
        'notes': [
            'Materials are generated by build_suite.py with pinned timestamps/GUIDs, so a rebuild with the same ezdxf version is byte-identical.',
            'labels.json is frozen together with the materials; manifest.labels_sha256 detects a labels-only edit.',
            'No real drawing is graded here; the two real drawings are development samples (data/cad_demo/DEVELOPMENT_SAMPLES.md).',
        ],
    }
    if write:
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
    return manifest, labels


def check():
    """Re-generate in memory and compare with the frozen bytes; report every mismatch."""
    specs = case_specs()
    problems = []
    for spec in specs:
        path = MATERIALS_DIR / spec['file']
        if not path.exists():
            problems.append(f'missing material: {spec["file"]}')
            continue
        expected = spec['build']().encode('utf-8')
        if path.read_bytes() != expected:
            problems.append(f'byte mismatch: {spec["file"]}')
    labels_text = json.dumps(labels_payload(specs), ensure_ascii=False, indent=2) + '\n'
    if not LABELS_PATH.exists():
        problems.append('missing labels.json')
    elif LABELS_PATH.read_text(encoding='utf-8') != labels_text:
        problems.append('labels.json differs from the frozen expectation')
    if not MANIFEST_PATH.exists():
        problems.append('missing manifest.json')
    else:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
        if manifest['labels_sha256'] != hashlib.sha256(labels_text.encode('utf-8')).hexdigest():
            problems.append('manifest.labels_sha256 does not match a rebuild')
        for record in manifest['materials']:
            path = HERE / record['file']
            if not path.exists():
                problems.append(f'missing material: {record["file"]}')
            elif _sha256(path) != record['sha256']:
                problems.append(f'sha256 mismatch: {record["file"]}')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='校验已冻结材料/标签能否被本脚本逐字节重建，不写盘')
    args = parser.parse_args(argv)
    if args.check:
        problems = check()
        for problem in problems:
            print(problem, file=sys.stderr)
        print(json.dumps({'suite_id': SUITE_ID, 'problems': len(problems)}, ensure_ascii=False))
        return 1 if problems else 0
    manifest, _labels = build(write=True)
    print(json.dumps({'suite_id': SUITE_ID, 'cases': manifest['cases_count'],
                      'layers': manifest['layers'],
                      'labels_sha256': manifest['labels_sha256']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
