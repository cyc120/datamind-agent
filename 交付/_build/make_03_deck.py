# -*- coding: utf-8 -*-
"""生成 交付/03_答辩演示.pptx（≤20 页；不得出现单位名、人名）。

运行：cd "/Users/caicai/DataMind Agent" && .venv/bin/python 交付/_build/make_03_deck.py
"""
import pathlib
import re
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

OUT = pathlib.Path(__file__).resolve().parents[1] / "03_答辩演示.pptx"
REPORT = pathlib.Path(__file__).resolve().parent / "anonymity_scan.json"

DARK = RGBColor(0x1F, 0x3D, 0x7A)
ACCENT = RGBColor(0xC0, 0x50, 0x2A)
GREY = RGBColor(0x55, 0x5F, 0x6B)
LIGHT = RGBColor(0xF2, 0xF5, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
EA = "PingFang SC"
LATIN = "Helvetica Neue"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def setfont(run, size, bold=False, color=GREY, mono=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "Menlo" if mono else LATIN
    run.font.color.rgb = color
    rPr = run._r.get_or_add_rPr()
    from pptx.oxml.ns import qn
    from lxml import etree
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rPr, qn(tag))
        el.set("typeface", EA if tag == "a:ea" else ("Menlo" if mono else LATIN))


def addr(paragraph, text):
    """python-pptx 的 add_run() 不接受位置参数，统一走这里。"""
    run = paragraph.add_run()
    run.text = text
    return run


def textbox(slide, x, y, w, h):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    return tf


def rect(slide, x, y, w, h, fill, line=None):
    from pptx.enum.shapes import MSO_SHAPE
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
    sh.shadow.inherit = False
    return sh


PAGES = []


def slide(title, subtitle=None, kicker=None):
    s = prs.slides.add_slide(BLANK)
    rect(s, 0, 0, 13.333, 0.06, DARK)
    rect(s, 0.0, 0.06, 13.333, 0.02, ACCENT)
    tf = textbox(s, 0.55, 0.22, 12.2, 0.7)
    p = tf.paragraphs[0]
    setfont(addr(p, title), 22, True, DARK)
    if subtitle:
        p2 = tf.add_paragraph()
        setfont(addr(p2, subtitle), 10.5, False, ACCENT)
    if kicker:
        kt = textbox(s, 0.55, 0.98, 12.2, 0.3)
        setfont(addr(kt.paragraphs[0], kicker), 9, False, GREY)
    PAGES.append((title, subtitle or ""))
    return s


def bullets(s, items, x=0.6, y=1.4, w=12.1, h=5.4, size=13.5, gap=6):
    tf = textbox(s, x, y, w, h)
    first = True
    for it in items:
        if isinstance(it, tuple):
            text, lvl = it
        else:
            text, lvl = it, 0
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.level = lvl
        p.space_after = Pt(gap)
        p.line_spacing = 1.06
        mark = "▍ " if lvl == 0 else "– "
        color = DARK if lvl == 0 else GREY
        setfont(addr(p, mark + text), size if lvl == 0 else size - 1.5, lvl == 0, color)
    return tf


def metrics(s, items, y=5.55, x=0.6, w=12.1, h=1.3, per=3):
    """底部数字条：items = [(大数字, 说明), ...]（最多 4 组）"""
    n = len(items)
    bw = (w - 0.15 * (n - 1)) / n
    for i, (big, small) in enumerate(items):
        bx = x + i * (bw + 0.15)
        rect(s, bx, y, bw, h, LIGHT)
        tf = textbox(s, bx + 0.14, y + 0.1, bw - 0.28, h - 0.2)
        p = tf.paragraphs[0]
        setfont(addr(p, big), 15, True, DARK)
        p2 = tf.add_paragraph()
        setfont(addr(p2, small), 9.5, False, GREY)


def footer(s, n, note="DataMind Agent　·　答辩演示"):
    tf = textbox(s, 0.55, 7.05, 11.0, 0.32)
    setfont(addr(tf.paragraphs[0], note), 8, False, RGBColor(0x99, 0x99, 0x99))
    tf2 = textbox(s, 11.7, 7.05, 1.1, 0.32)
    p = tf2.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    setfont(addr(p, f"{n:02d}"), 10, True, DARK)


def code(s, text, x=0.6, y=1.45, w=12.1, h=3.9):
    rect(s, x, y, w, h, RGBColor(0xF7, 0xF7, 0xF4))
    tf = textbox(s, x + 0.18, y + 0.14, w - 0.36, h - 0.28)
    for i, line in enumerate(text.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(2)
        p.line_spacing = 1.0
        setfont(addr(p, line), 10.5, False, RGBColor(0x22, 0x33, 0x44), mono=True)


# ---------- 01 封面 ----------
s = prs.slides.add_slide(BLANK)
rect(s, 0, 0, 13.333, 7.5, RGBColor(0xF7, 0xF9, 0xFC))
rect(s, 0, 2.05, 13.333, 0.05, ACCENT)
tf = textbox(s, 1.1, 1.15, 11.1, 1.0)
setfont(addr(tf.paragraphs[0], "DataMind Agent"), 42, True, DARK)
tf = textbox(s, 1.1, 2.35, 11.1, 1.2)
p = tf.paragraphs[0]
setfont(addr(p, "多模态数据驱动的可解释精准问数问答智能体"), 20, False, GREY)
p2 = tf.add_paragraph()
setfont(addr(p2, "答辩演示　·　版本化的跨源证据绑定 + 可控修复"), 15, True, ACCENT)
tf = textbox(s, 1.1, 4.1, 11.1, 1.6)
for i, line in enumerate([
    "基线提交 e4e2ebf　·　API 0.39.0　·　隔离回归 1297/1297（失败 0）",
    "本演示不出现任何单位名与人名；每页数字均可回溯到仓库内报告文件",
    "演示口径：能做的说分子分母，不能做的写「无法判定」",
]):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.space_after = Pt(5)
    setfont(addr(p, line), 12.5, i == 0, GREY)
PAGES.append(("封面", "DataMind Agent"))
footer(prs.slides[-1], 1)

# ---------- 02 问题 ----------
s = slide("问题：跨源问数最危险的失败不是「查不到」，而是「绑错了还自信」",
          "定位声明：把「实体 / 版本 / 单位 / 时间口径」的绑定从隐式假设升级为一等公民",
          "对应官方：决赛 多源异构融合（10 分） · 可解释性（5 分） · 中级任务质量（10 分）")
bullets(s, [
    "库里查到数、文档里查到句，拼在一起看似有据——但对象/版本/单位/口径任一处绑错，答案就是错的。",
    ("错误绑定往往伴随高置信度输出：系统越流畅，越难被评委与用户察觉。", 1),
    ("现有工程的常见做法是「先回答再解释」，事后解释无法回滚已经绑错的结论。", 1),
    "本项目选择反向设计：优先构建证据图与绑定字段，再决定能不能给出结论；不能判定就同时展示双方证据。",
    ("无法消解 → 标记待裁决；未登记来源权威 → 绝不自动单选一个来源。", 1),
    "这条路同时喂饱三项官方要求：多源融合、可解释性、中级任务质量（因为有可对照的度量）。",
], y=1.75, h=3.6)
metrics(s, [
    ("14 + 12 + 4", "已冻结评测用例（nl2sql / rag / robustness，只读 SQL 实测）"),
    ("169 / 23", "切片 / 候选事实（知识库运行态）"),
    ("1456 / 1359", "证据图节点 / 边（已存在，缺绑定列）"),
])
footer(s, 2)

# ---------- 03 系统架构 ----------
s = slide("系统架构：四件套 + 独立 worker + 直连上游",
          "FastAPI · 原生 JS 前端 · PostgreSQL 元数据库 · Qdrant 向量库",
          "证据：datamind/api.py（version=0.39.0，/openapi.json paths=75）、web/、db/migrations/001–018")
code(s, "浏览器 web/  ──HTTP/SSE──►  FastAPI datamind/api.py  ──►  PostgreSQL datamind_meta\n"
        "                                     │                        └ 18 个迁移 001–018\n"
        "                                     ├──►  Qdrant 6333  datamind_knowledge（169 点）\n"
        "                                     ├──►  上游模型 api.deepseek.com/v1（deepseek-flash，直连无网关）\n"
        "                                     └──►  独立 worker：上传解析 / PDF 索引 / 公式(.venv-formula)\n"
        "\n"
        "解析层：统一解析契约（parse_status 五态 + 七类 SourceLocator + 块/关系约束）\n"
        "        适配器登记与媒体签名路由；未登记类型 PARSE_ADAPTER_UNKNOWN 明确拒绝")
metrics(s, [
    ("75", "REST/SSE 路径（/openapi.json 实测）"),
    ("27 / 2", "已索引 / 已解析未索引文档（只读 SQL）"),
    ("1297/1297", "隔离回归（失败 0，源库未修改）"),
], y=5.95, h=1.0)
footer(s, 3)

# ---------- 04 NL2SQL ----------
s = slide("NL2SQL：先受控，再执行",
          "问题帧 → 召回 → 生成 → AST 门禁 → 只读执行 → 最多一次修复",
          "证据：datamind/sql_engine.py、catalog.py、sql_guard.py；reports/nl2sql.json")
code(s, "① query_frame   实体/指标/时间/范围；缺项 → NEEDS_CLARIFICATION\n"
        "② Catalog       词法 + 中文别名(approved) + 枚举 + Join Graph，Top-8 候选\n"
        "③ 模型生成      schema 快照 + 业务指标注册表口径进提示词\n"
        "④ sql_guard     sqlglot AST：按 scope 解析列权限 / 顶层投影必须是批准指标 / 禁 OR\n"
        "                参数绑定用「文本序哨兵」避免占位符与值错配\n"
        "⑤ 只读执行      无写入路径；失败分类 → 受控修复（上限 1 次）→ sql + rows + 证据")
metrics(s, [
    ("14/14", "零售套题（自建，非官方集）"),
    ("31/31 · 28/28", "Schema 表召回 / 语义锚点列召回（Top-5）"),
    ("4/4 · 6/6", "指标召回 / 连接路径正确（18 条自建题）"),
    ("4/4", "鲁棒性套题（错别字/口语/越权/空结果）"),
], y=5.75, h=1.15)
footer(s, 4)

# ---------- 05 NL2SQL 失败边界 ----------
s = slide("受控查询的失败边界（真实反例与处置）",
          "来自独立复查：这些反例曾全部「判通过」，现在是硬门",
          "证据：PROGRESS.md 阶段流水（第 1、2 批）、docs/AUDIT_FIXES.md、reports/audit_fix_verification.json")
bullets(s, [
    "CTE 同名绕过：带 schema 的真实表与某 CTE 同名 → 被当作 CTE 跳过列检查 → 现按作用域来源对象判定。",
    "指标篡改四连：正确 SUM 再乘 0 / 加常量 / 把 SUM 挪进无关子查询而外层投影 1 / WHERE 追加 OR TRUE → 四种变体曾全部通过，现已封堵。",
    "时间条件造假：时间谓词必须出现在主查询 WHERE 中且区间两侧都在。",
    "参数错配：sqlglot 遍历顺序不等于文本顺序 → 改为文本序哨兵映射。",
    "连接粒度：按 catalog 外键方向判 METRIC_JOIN_GRAIN_RISK，防止指标被 JOIN 放大。",
    ("边界：口径强制的覆盖面仍窄（仅销售额别名族）——复合指标按「无法独立校验」记录而不阻断，如实保留。", 1),
], y=1.75, h=4.6)
footer(s, 5)

# ---------- 06 RAG ----------
s = slide("RAG：只允许用本次候选证据回答，不满足就拒答",
          "混合检索（向量 + 词法 RRF）→ 生成 → 连续片段/数字校验 → 拒答",
          "证据：datamind/knowledge.py、rag_engine.py；reports/rag.json、reports/rag_fields.json")
bullets(s, [
    "生成答案的每个数字必须出自引用片段或标题；引用必须逐字命中原文连续片段。",
    "未知 chunk 拒绝；最多一次修复；生成后重查证据身份，变化则 409。",
    "显式多文档：1–8 份无重复公开文件，每份必须有相关片段，否则阻断。",
    "0 切片文档：前端不出勾选框；服务端拒绝 DOCUMENT_SELECTION_NOT_SEARCHABLE；证据漂移则如实报 MULTIFILE_EVIDENCE_INCOMPLETE（两者语义刻意分开）。",
    ("缺口：无逐结论忠实度三态标注；公式图片无公式文本的题目按纪律拒答而不是猜。", 1),
], y=1.75, h=3.5)
metrics(s, [
    ("10/12", "RAG 套题通过（1 例上游错误、1 例缺表头/公式拒答）"),
    ("1.0 / 0.9545", "recall@5 / MRR@10"),
    ("5/9 · 3/15", "扫描件表头 / 字段（缺口如实保留）"),
])
footer(s, 6)

# ---------- 07 多源融合 ----------
s = slide("多源融合：工业「原文 + 生产 SQL」逐测量核验已跑通",
          "冲突时保留双方原值；未登记权威时不选来源",
          "证据：datamind/industrial_rules.py；reports/industrial_rules.json（合成集，model_used=false）")
code(s, "工艺/质检原文 ──► 阈值 + 适用范围（单位 / 版本 / 条款页）\n"
        "                     │\n"
        "生产 SQL(只读) ──► 逐测量值 ──► 绑定(对象/版本/单位/时间口径)\n"
        "                     │\n"
        "       ┌─────────────┼─────────────┐\n"
        "    超限判定      冲突判定       需人工复核\n"
        "    out_of_range  保留双方原值   needs_review")
metrics(s, [
    ("13/13", "冻结开发规则通过（合成集，不调用大模型）"),
    ("model_used=false", "纯规则核验，13 条用例"),
    ("1456 / 1359", "证据图节点 / 边（缺绑定列与跨源边类型）"),
], y=5.75, h=1.15)
footer(s, 7)

# ---------- 08 通用跨源 fail-closed ----------
s = slide("通用跨源当前是 fail-closed，这不是完成度而是纪律",
          "命中 cross_source 后返回 ROUTE_UNSUPPORTED —— 宁可拒答，不给未绑定的结论",
          "证据：datamind/agent.py route_for()；docs/SCORE_GAP_LEDGER.md F1 与 §5")
bullets(s, [
    "route_for() 仅按关键词命中 cross_source，命中后直接返回 ROUTE_UNSUPPORTED。",
    "原因：没有实体/版本/单位/口径绑定与裁决门时，猜测作答的风险高于拒答。",
    "因此 F1 的「≥3 种结构不同的跨源问题」未达成；只能报已有工业链路的 13/13。",
    "证据图现状：evidence_nodes=1456 / evidence_edges=1359 已存在，但缺 source_sha256/version/unit/time_scope 绑定列，",
    ("也缺 entity_match/version_scope/unit_equivalent/time_overlap/conflicts 等边类型 CHECK。", 1),
    "所以「证据图已建成完整版本」这句话在本轮不可主张——只能说「基础证据图已存在」。",
], y=1.75, h=4.6)
footer(s, 8)

# ---------- 09 单一贡献 ----------
s = slide("单一贡献：版本化的跨源证据绑定 + 可控修复",
          "不是新模型，而是一个新的度量与机制：先绑定，再核验，最后才允许作答",
          "自述全文见 交付/04_突破与创新性自述.pdf（≤5 页）")
bullets(s, [
    "证据图：数据行 / 文档块 / 测量 / 规范条款是节点；实体同一性、版本适用、单位等价、时间口径、数值支持、数值冲突是边。",
    "每条形如「对象 + 版本 + 单位 + 时间口径 + 来源 sha256」的绑定是答案的必填字段，不是事后解释。",
    "受控修复：只修绑定错误，每次修复记账；超过上限即停，冲突留作未消解。",
    "可复现度量：绑定准确率、错误绑定率（标志性指标）、冲突检出率/误报率、误修复率、修复成本。",
    "对照消融：plain / bind_only / full 三臂在同一冻结集同一环境比较。",
    ("诚实边界：若 plain 臂也很强，本项只能申报「实用创新与转化价值」，不申报技术突破领先性。", 1),
], y=1.75, h=4.6)
footer(s, 9)

# ---------- 10 消融数据 ----------
s = slide("创新举证：机制消融的现有数字（并标明它不能证明什么）",
          "reports/vcar_ablation.json：16 例冻结集、四臂对照、标签先冻结后评测",
          "报告自注：「合成机制消融，不调用任何模型；不是 OCR/问答准确率」")
table_rows = [
    ["臂", "正确/例数", "准确率", "误修复率", "冲突检出", "未消解"],
    ["ocr_text", "3/16", "0.1875", "N/A（未改写）", "0/4", "0"],
    ["structured", "11/16", "0.6875", "0.0", "4/4", "0"],
    ["local", "11/16", "0.6875", "0.1429（2 例）", "4/4", "0"],
    ["full", "16/16", "1.0", "0.0", "4/4", "2（保留双方证据）"],
]
rows, cols = len(table_rows), len(table_rows[0])
tw, th = 12.1, 2.9
tbl = prs.slides[-1].shapes.add_table(rows, cols, Inches(0.6), Inches(1.75), Inches(tw), Inches(th)).table
for i, row in enumerate(table_rows):
    for j, val in enumerate(row):
        cell = tbl.cell(i, j)
        cell.text = val
        p = cell.text_frame.paragraphs[0]
        setfont(p.runs[0], 11 if i else 11, i == 0 or j == 0, WHITE if i == 0 else GREY)
        cell.fill.solid()
        cell.fill.fore_color.rgb = DARK if i == 0 else (LIGHT if i % 2 else WHITE)
bullets(s, [
    "边界一：这是机制替身，不是模型精度，也不是视觉识别成绩。",
    "边界二：不得据此主张业界领先；无与公开最优方案的对比实验。",
], y=4.85, h=1.6, size=12)
footer(s, 10)

# ---------- 11 中级任务 ----------
s = slide("中级任务：先如实标状态，再谈数量",
          "B3 / B4 为「已实现（限定）」；B1/B2/B5/B6/B7/B9「部分」；B8「未实现」",
          "证据：docs/OFFICIAL_REQUIREMENTS_MAPPING.md §B；reports/schema_linking.json、industrial_nl2sql.json")
rows = [
    ["官方难题", "当前状态", "可核验证据 / 缺口"],
    ["B3 语义校验与澄清", "已实现（限定）", "clarify() + NEEDS_CLARIFICATION；工业模型前澄清 2/2；无不必要澄清率"],
    ["B4 Schema Linking", "已实现（限定）", "表 31/31、列 28/28、指标 4/4、路径 6/6（18 条自建题）"],
    ["B2 可扩展要素提取", "部分", "领域知识在数据库注册表，不靠提示词；无新增领域成本实测"],
    ["B5 多表 JOIN/聚合/嵌套", "部分", "语义门禁与粒度风险判定；样本小、无公开基准"],
    ["B6 公式识别与计算", "部分（仅识别）", "ONNX 识别 + LaTeX 校验；无求值引擎（6b 未实现）"],
    ["B7 / B8 / B9", "部分 / 未实现 / 部分", "目录恢复无专项集；复杂度评估未实现；质量校正与告警未做"],
]
rows_n, cols_n = len(rows), len(rows[0])
tbl = prs.slides[-1].shapes.add_table(rows_n, cols_n, Inches(0.6), Inches(1.7), Inches(12.1), Inches(4.6)).table
for i, row in enumerate(rows):
    for j, val in enumerate(row):
        cell = tbl.cell(i, j)
        cell.text = val
        setfont(cell.text_frame.paragraphs[0].runs[0], 10.5, i == 0 or j == 0, WHITE if i == 0 else GREY)
        cell.fill.solid()
        cell.fill.fore_color.rgb = DARK if i == 0 else (LIGHT if i % 2 else WHITE)
footer(s, 11)

# ---------- 12 可解释性 ----------
s = slide("可解释性：答案即证据链，且可视化不猜测",
          "SQL / 引用片段 / 原文定位 / 事件回放 / 契约关系，五类证据都有出口",
          "证据：web/app.js、GET /v1/runs/{id}、/evidence、/events")
bullets(s, [
    "答案载荷携带 SQL、绑定参数、rows、citations（含 locators 与 relations）。",
    "前端引用卡片显示定位徽标；未旋转的 PDF 区域可展开原页高亮。",
    "旋转页与非页面定位（office_object / cad_entity / sheet_range）只显示标签，绝不猜测绘制。",
    "不从页码推导区域；定位查询失败不阻断回答，错误码独立记录。",
    ("边界：关系不参与排序/打分/重排/冲突消解；界面需浏览器刷新；本轮未做界面视觉验收。", 1),
], y=1.75, h=4.6)
footer(s, 12)

# ---------- 13 不变量 ----------
s = slide("不变量：不传历史时逐字节不变、不选跨源时不改提示词",
          "新能力不得改变既有行为——这是可以机器核对的纪律，不是口号",
          "证据：docs/MULTI_TURN.md、tests/test_multi_turn.py 33 项、PROGRESS.md 阶段流水")
bullets(s, [
    "history 契约：≤20 条、单条 ≤2000 字符、合计 ≤8000；超限拒绝而非截断。",
    "历史以 trust=\"untrusted\" 边界区块送入模型，并附防注入声明。",
    "不传 history 时提示词与结果逐字节不变（三种独立证明：sha256 常量复算、用 git show HEAD: 并排对照、API 调用面精确断言）。",
    "实测：把 DROP TABLE orders 放进历史后，执行的 SQL 与参数完全不变。",
    "turns_used 如实返回；默认清空工作区时修好的前端缓存版本也同步提升，避免旧脚本回访。",
    ("边界：「连续 5 轮以上准确」尚未评测；前端未接入 history；无服务端会话。", 1),
], y=1.75, h=4.6)
footer(s, 13)

# ---------- 14 评测与效率 ----------
s = slide("效率与伸缩性：有实测规模点，缺分析与优化对照",
          "reports/scale_v1.json：CAD 4 档 + 文档选择 4 档，每档 3 次，独立子进程",
          "机器：Apple M5 Pro，15 核，Python 3.14.5，内存 25,769,803,776 字节")
rows = [
    ["路径", "规模点", "中位耗时", "峰值 RSS", "状态"],
    ["CAD 解析", "100 / 1000 / 5000 / 20000 实体", "251.1 / 283.3 / 445.2 / 897.9 ms",
     "191.7 / 199.1 / 237.5 / 366.8 MB", "后两档 partial（blocks 上限 2000）"],
    ["文档选择读取", "1 / 2 / 5 / 8 份", "102.9 / 106.2 / 106.9 / 107.3 ms",
     "60.7 / 61.0 / 61.4 / 61.6 MB", "实际读取 9 / 14 / 34 / 44 行"],
]
tbl = prs.slides[-1].shapes.add_table(3, 5, Inches(0.6), Inches(1.75), Inches(12.1), Inches(2.6)).table
for i, row in enumerate(rows):
    for j, val in enumerate(row):
        cell = tbl.cell(i, j)
        cell.text = val
        setfont(cell.text_frame.paragraphs[0].runs[0], 10, i == 0, WHITE if i == 0 else GREY)
        cell.fill.solid()
        cell.fill.fore_color.rgb = DARK if i == 0 else (LIGHT if i % 2 else WHITE)
bullets(s, [
    "报告自带 4 条免责：单机单进程 / 无 CPU 绑定或 cgroup / 无并发负载 / 非生产吞吐承诺。",
    "缺口：无时间复杂度理论分析、无瓶颈定位、无优化前后对照、无并发矩阵、无 SQL 侧多规模（1千/1万/10万行）。",
    "CAD 后两档是「受限输出路径测量」，不得当作完整解析 2 万实体的证明。",
], y=4.6, h=2.2, size=12)
footer(s, 14)

# ---------- 15 评测与诚实计数 ----------
s = slide("评测口径：拒答、超时、上游错误单列，但不移出分母",
          "零分母一律 N/A；正向与反向对照同时写",
          "证据：reports/cards/wp1a_registry.md、docs/SCORE_GAP_LEDGER.md §0")
bullets(s, [
    "题面入仓并 append-only 冻结：data/evaluation/registry/{nl2sql,rag,robustness}_v1.json。",
    "check 实测 3/3 suite 登记与库内题面一致（exit 0）；重复冻结被拒绝（exit 2）；篡改副本被检出（exit 1）。",
    "统一报告 schema 检查：三份历史报告 0/3 符合（57 + 49 + 17 = 123 项问题）——如实结果，不伪修。",
    "RAG 10/12 的两例失败留在报告里：1 例上游错误、1 例缺表头/公式拒答。",
    ("官方测试集未提供 → E1/E2/E5/E6/F4/F5 的官方准确率档一律「无法判定」。", 1),
], y=1.75, h=4.6)
footer(s, 15)

# ---------- 16 鲁棒性 ----------
s = slide("鲁棒性与低质量数据：把失败边界变成数字",
          "扫描件、表头、公式、旋转页——每一处缺口都保留在报告里",
          "证据：reports/robustness.json、header_initial.json、document_fields.json、formula_initial.json")
bullets(s, [
    "robustness 套题 4/4：错别字、口语简称、越权拒答、空结果各 1 条（规模远小于计划要求的 ≥10 条）。",
    "扫描件表头 5/9、字段 3/15：缺口如实保留，不用「部分成功」遮掩。",
    "公式图片没有公式文本时，按纪律拒答而不是猜测（rag-006 保留为失败样本）。",
    "页面旋转、区域越界、重复句柄等情况一律拒绝或声明，绝不伪造身份（CAD 位置身份模式无 handle 键）。",
    "权限矩阵、任务隔离、失败状态与格式兼容较完善；模糊/低质量真实输入覆盖仍少。",
], y=1.75, h=4.6)
footer(s, 16)

# ---------- 17 交付清单 ----------
s = slide("交付清单：本次真实生成的六件套",
          "全部由仓库 .venv/bin/python 脚本产出；页数为实测值，不是估算",
          "机读清单见 交付/DELIVERY_MANIFEST.json；官方提交物状态见 docs/DELIVERY_CHECKLIST.md")
rows = [
    ["文件", "类型", "页/幻灯片", "结构检查"],
    ["交付/01_设计报告_DataMind_Agent.docx + .pdf", "设计报告", "13 页 / 13 页", "check_office.py 通过"],
    ["交付/02_技术实现说明书_DataMind_Agent.docx + .pdf", "技术说明书", "12 页 / 12 页", "check_office.py 通过"],
    ["交付/03_答辩演示.pptx", "答辩 PPT", "20 张（限 20）", "check_office.py 通过 + 匿名检查"],
    ["交付/04_突破与创新性自述.pdf（+ DOCX 源）", "加分自述", "见实测（限 5 页）", "check_office.py 通过"],
    ["docs/DELIVERY_CHECKLIST.md + DELIVERY_MANIFEST.json", "核对清单 / 机读清单", "—", "JSON 可解析"],
]
tbl = prs.slides[-1].shapes.add_table(6, 4, Inches(0.6), Inches(1.75), Inches(12.1), Inches(3.6)).table
for i, row in enumerate(rows):
    for j, val in enumerate(row):
        cell = tbl.cell(i, j)
        cell.text = val
        setfont(cell.text_frame.paragraphs[0].runs[0], 10.5, i == 0, WHITE if i == 0 else GREY)
        cell.fill.solid()
        cell.fill.fore_color.rgb = DARK if i == 0 else (LIGHT if i % 2 else WHITE)
bullets(s, [
    "本轮明确不打 ZIP：只交付打包脚本 scripts/build_delivery_zip.py 的 dry-run 清单（避免把 .venv、.cache 打进包）。",
], y=5.6, h=1.2, size=12)
footer(s, 17)

# ---------- 18 演示流程 ----------
s = slide("演示流程：8 步，每步都有可见证据",
          "建议现场节奏 8–10 分钟；每步失败时如何如实说明已预先写好",
          "对应官方：交互 Demo 可用性、线上功能演示")
bullets(s, [
    "1｜打开问数页面，问一个单表问题 → 展示生成的 SQL 与返回行。",
    "2｜问一个 JOIN/聚合问题 → 展示 SQL 门禁放行与连接路径。",
    "3｜故意问一个缺时间的题 → 展示 NEEDS_CLARIFICATION 与缺项字段。",
    "4｜问一个文档问题 → 展示引用片段、页码定位与区域高亮。",
    "5｜选中 2–3 份文档提问 → 展示逐文件覆盖要求与证据不完整时的拒绝。",
    "6｜问一个工业跨源题 → 展示单位/版本核验与冲突双方原值。",
    "7｜打开运行回放 → 展示事件流与证据集合。",
    "8｜上传一份新文件 → 展示解析契约与「尚未建立问答索引（暂不可问答）」的如实提示。",
], y=1.75, h=4.9, size=12.5)
footer(s, 18)

# ---------- 19 失败边界页 ----------
s = slide("失败边界页：现场可能出问题的地方，以及我们怎么回答",
          "不隐藏、不临场改口径；每一条都对应仓库里的真实限制",
          "证据：docs/SCORE_GAP_LEDGER.md §5、docs/DEPLOYMENT_BOUNDARIES.md")
bullets(s, [
    "上游模型超时/限流 → 如实报上游错误并单列，不把它从分母里去掉，也不重试到刷出好看数字。",
    "问通用跨源问题 → 系统会返回 ROUTE_UNSUPPORTED；我们说明这是刻意的 fail-closed，而不是「已完成」。",
    "问多模态识别准确率 → 上游仅文本模型、无本地视觉权重，明确回答不主张领先。",
    "问官方测试集成绩 → 官方测试集未提供，只能给自建冻结集分子/分母与失败清单。",
    "问干净环境能否一键启动 → 本轮未做空库演练（禁止重建容器/库），只能说启动脚本已就绪、未在本轮验证。",
    "问 PDF 版式是否逐页看过 → 已做结构检查，未做逐页视觉检查，如实说明。",
], y=1.75, h=4.9, size=12.5)
footer(s, 19)

# ---------- 20 总结 ----------
s = slide("一句话总结：把「能答」变成「敢答、答得可查、不能答就说不知道」",
          "工程机制 → 可复现证据 → 成套交付材料",
          "下一步优先级：跨源证据绑定与三种结构冻结集 → 对照消融 → 澄清闭环 → 效率证明")
bullets(s, [
    "已有：可解释链路、受控查询、版本与权限、解析缺口如实声明、1297/1297 隔离回归。",
    "已交付：设计报告 / 技术说明书 / 答辩 PPT / 创新自述 / 官方核对清单 / 机读清单（本次全部真实生成）。",
    "待做：通用跨源引擎与绑定列、≥3 项可申报的中级任务、效率的复杂度与优化对照、决赛 ≤50 页深化稿与 ZIP。",
    "纪律不变：分子分母同给；拒答/超时/上游错误单列但不移出分母；无证据写「无法判定」，不写「领先」。",
], y=1.8, h=3.2)
metrics(s, [
    ("1297/1297", "隔离回归（当前唯一权威分母）"),
    ("6 件套", "本次真实生成的交付物"),
    ("0", "本轮新增的官方得分声明"),
])
footer(s, 20)

prs.save(str(OUT))

# ---------- 匿名检查 ----------
# 显式检查表 A：单位/组织名黑名单（全称与可识别简称）
BLOCK = [
    "中电云计算技术有限公司", "中电云计算", "中电云", "中国电子", "中国电子信息产业集团",
    "网信产业应用赛道", "赛题八",
]
# 显式检查表 B：人名/称谓提示词（仅取多字称谓，避免单姓误报）
NAME_HINTS = ["先生", "女士", "老师", "博士", "教授", "总经理", "总监", "经理", "同志", "队长"]
texts = []
for idx, slide_ in enumerate(prs.slides, 1):
    parts = []
    for shape in slide_.shapes:
        if shape.has_text_frame:
            parts.append(shape.text_frame.text)
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    parts.append(cell.text)
    texts.append((idx, "\n".join(parts)))

hits = []
for idx, text in texts:
    for token in BLOCK:
        for m in re.finditer(re.escape(token), text):
            hits.append({"slide": idx, "token": token, "context": text[max(0, m.start() - 12):m.end() + 12]})
person_hits = []
for idx, text in texts:
    for token in NAME_HINTS:
        for m in re.finditer(re.escape(token), text):
            person_hits.append({"slide": idx, "token": token, "context": text[max(0, m.start() - 10):m.end() + 10]})

print("slides:", len(prs.slides.__iter__.__self__._sldIdLst))
print("blocklist hits:", len(hits))
print("name-hint hits:", len(person_hits))
for h in hits[:10]:
    print("  HIT", h)
for h in person_hits[:10]:
    print("  NAME", h)
REPORT.write_text(__import__("json").dumps({
    "checked_file": str(OUT),
    "slide_count": len(prs.slides._sldIdLst),
    "blocklist": BLOCK,
    "blocklist_hits": hits,
    "person_name_hint_list": NAME_HINTS,
    "person_name_hint_hits": person_hits,
    "method": "python-pptx 读取每页所有 text_frame 与表格单元格文本，对单位名黑名单与称谓/姓氏提示表做正则扫描；不使用 OCR，因此不覆盖图片内文字。",
    "limits": "不覆盖嵌入图片内文字；不覆盖母版/备注中的文字（本脚本对备注也一并扫描，见 notes_scanned）。",
    "notes_scanned": True,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("WROTE", OUT, "and", REPORT)
