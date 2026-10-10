# -*- coding: utf-8 -*-
"""生成 交付/04_突破与创新性自述_DataMind_Agent.docx（源文件；转 PDF ≤5 页）。

单一贡献：「版本化的跨源证据绑定 + 可控修复」。

第二轮（修复轮）改动要点（对齐 reports/review_round1_evidence.md §6.1 的两条 refuted：
C130「两份文件均不存在」与 C131「待本轮复查确认」）：
  1. 不再写「两份文件均不存在」这种会立刻过期的表述；改为**逐份点名文件与其真实状态**
     （存在/不存在、字节数、记录数），并写明状态核对时刻。
  2. 只引用真实存在、且可被第一轮复查报告 supporting 的数字：
       - reports/cross_source_offline.json（存在，13 798 B）→ 复查 C01–C10 confirmed、R1 复跑逐位吻合；
       - reports/vcar_ablation.json（存在）→ 复查 C132 confirmed（机制替身，非跨源成绩）。
     reports/binding_ablation.json 在本自述撰写时**已存在**（23:36 落盘），但它是在第一轮复查结论
     冻结（23:14）之后由同一轮工作包自报产出的，**尚无独立复查者复核**；因此本自述逐位引用其数字时
     同时标注「同轮自报、未经独立复查」，并照录其自带边界与 claim_boundary。
  3. 未产出的证据（reports/cross_source_live.json）明确写「本轮未产出，不作主张」。

数字只来自仓库真实文件；本脚本不产生数字。
运行：cd "/Users/caicai/DataMind Agent" && .venv/bin/python 交付/_build/make_04_innovation.py
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from docx_kit import (new_doc, para, h1, h2, bullets, numbered, code_block,
                      table, add_footer_pagenum, _style_run)


def _render_bold_markers(p):
    """把段落文本里的 **粗体** 标记渲染成真正的加粗 run（不留下字面星号）。"""
    txt = p.text
    if "**" not in txt:
        return
    size = 9.0
    if p.runs and p.runs[0].font.size is not None:
        size = p.runs[0].font.size.pt
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    for part in re.split(r"(\*\*.+?\*\*)", txt):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            _style_run(p.add_run(part[2:-2]), size=size, bold=True)
        else:
            _style_run(p.add_run(part), size=size)


def apply_bold_markers(document):
    """对所有正文段落与表格单元格执行 **粗体** 渲染（表格里也常用）。"""
    for p in document.paragraphs:
        _render_bold_markers(p)
    for t in document.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _render_bold_markers(p)

OUT = pathlib.Path(__file__).resolve().parents[1] / "04_突破与创新性自述_DataMind_Agent.docx"

# ---- 证据状态常量（只读实测值；核对时刻硬编码，避免不同轮次口径漂移）----
AS_OF = "2026-10-09T23:45+08:00"
BA_BYTES = "219 605 B"
BA_SHA16 = "d086dd2e24014965"
BA_GEN = "2026-10-09T15:36:52+00:00"
CS_BYTES = "13 798 B"

doc = new_doc(margin_cm=1.6)
add_footer_pagenum(doc, "《突破与创新性自述》")

para(doc, "《突破与创新性自述》", size=19, bold=True, align="center", space_after=2)
para(doc, "版本化的跨源证据绑定 + 可控修复", size=12, bold=True, align="center", space_after=2)
para(doc, "DataMind Agent　·　基线提交 e4e2ebf　·　API 0.39.0　·　篇幅 ≤5 页　·　第二轮（修复轮）同步版",
     size=8.5, align="center", space_after=5)

# ---------- 1 定位声明 ----------
h1(doc, "1. 定位声明", size=13)
para(doc, "跨源问数最危险的失败不是「查不到」，而是把证据绑错了对象、版本、单位或时间口径之后，"
          "仍然给出高置信度的答案。本项目不新增一个大模型，也不追求把模块数量堆满；"
          "我们把「绑定」从一个隐式假设提升为一等公民，并把它变成可度量、可复核、可在有限次数内受控修复的机制。",
     size=9)
para(doc, "因此本项申报的方向是「实用创新与转化价值」而非「技术突破领先性」："
          "我们没有与公开最优方案的对比实验，没有公开基准成绩，也没有真实视觉模型参与对比，"
          "不做任何「业界领先」的主张。若后续对照实验证明本机制相对普通流水线无差异，"
          "本项应降级为纯粹的工程化与可信性叙述。", size=9)
para(doc, "对应官方加分方向（赛题 PDF 第 14 页原文要点）：「实用创新与转化价值 0~10 分 —— "
          "提出了出奇简单、易于部署的解决方案；在工程化设计、系统架构、用户体验方面有突出贡献」。", size=8.5)

# ---------- 2 核心贡献 ----------
h1(doc, "2. 核心贡献（单一贡献，一句话可陈述）", size=13)
para(doc, "系统不直接生成跨源结论，而是先生成一张带类型的证据图：数据行 / 文档块 / 测量 / 规范条款是节点；"
          "实体同一性、版本适用、单位等价、时间口径、数值支持、数值冲突是边。"
          "每条结论必须携带绑定字段（对象 + 版本 + 单位 + 时间口径 + 来源 sha256）；"
          "冲突在有限次数的受控修复内消解；无法消解时同时展示双方证据并标记待裁决，绝不自动单选一个来源。", size=9)
h2(doc, "2.1 机制要点与现有资产的对应（不是另起炉灶）", size=10)
table(doc, ["要素", "已有资产（仓库路径）", "本贡献的增量"], [
    ["实体同一性", "entity_aliases（approved）参与 Schema 召回（datamind/catalog.py）", "跨 SQL 行与文档块共用规范实体 id，绑定必须回指原值"],
    ["连接路径", "Catalog.join_path()（datamind/catalog.py）", "多跳路径的跳数与证据边一起进入解释"],
    ["版本与单位", "图纸/工艺版本、industrial_rules.py 的单位与版本核验、来源权威登记（迁移 006）", "泛化为 binding{entity_id, version, unit, time_scope, source_sha256} 并作为答案必填字段"],
    ["冲突处理", "工业规则「未登记权威时不选来源，保留双方原值」", "升格为跨源通用裁决门 + 稳定错误码语义"],
    ["受控修复", "V-CAR 修复上限、ablation.py 的修复成功率/误修复率指标", "只修绑定错误，每次修复记账，设误修复率硬门"],
    ["可解释", "事件流、回放、引用卡片、契约关系", "证据图可视化：节点类型、边类型、绑定字段、冲突双方"],
], widths=[2.4, 6.4, 6.6], size=8)
h2(doc, "2.2 可复现度量（本贡献真正缺过的东西）", size=10)
numbered(doc, [
    "绑定准确率 = 正确绑定到（对象 + 版本 + 单位 + 时间口径）的声称 / 全部答案声称。",
    "错误绑定率（标志性指标）= 绑定错误却仍以高置信度输出的声称比例；零分母报 N/A，不用 0 冒充。",
    "冲突检出率 / 误报率：同一来源相反值、跨版本不一致、单位不可归一化、时间范围不重叠，四类分别报 P/R。",
    "修复账本：尝试次数、成功率、误修复率、每次修复带来的延迟与 token 成本。",
    "对照消融：plain（直接拼接 SQL 结果与文档片段让模型作答）/ bind_only（绑定但不核验）/ full（绑定 + 核验 + 受控修复）三臂在同一冻结集同一环境比较。",
], size=9)

# ---------- 3 举证 ----------
h1(doc, "3. 举证（只用仓库内真实存在、且可被复查报告 supporting 的数字）", size=13)
h2(doc, "3.1 证据状态总览（逐份点名，只读实测；核对时刻 %s）" % AS_OF, size=10)
table(doc, ["被引用文件", "只读实测状态", "记录/规模", "第一轮复查判定"], [
    ["reports/cross_source_offline.json", "**存在**（%s）" % CS_BYTES, "21 用例 / 161 断言 / model_calls=0", "confirmed（C01–C10；R1 复跑逐位吻合）"],
    ["reports/binding_ablation.json", "**存在**（%s，sha256 前 16 位 %s，generated_at %s）" % (BA_BYTES, BA_SHA16, BA_GEN),
     "40 例陷阱 / 3 臂 / 68 条 arm-case 失败 / model_calls=0", "**同轮自报，未经独立复查**（复查冻结于 23:14，该文件 23:36 落盘）"],
    ["reports/vcar_ablation.json", "**存在**", "16 例合成机制消融", "confirmed（C132：dataset/sha256/cases/generated_at 逐字段核对一致）"],
    ["reports/cross_source_live.json", "**本轮未产出**（`ls` 退出码 1）", "—", "不适用（无该文件）"],
    ["data/evaluation/binding_traps_v1.json", "**存在**", "40 例（四类各 10）", "未见独立复查（本轮新增）"],
], widths=[5.6, 4.6, 4.2, 5.0], size=7.5)
para(doc, "说明：上表「存在 / 未产出」均为撰写时的只读 `ls` 与字节实测，并已写入核对时刻；"
          "本自述不再以一句整句笼统否定两份专项报告，而是逐份点名文件及其真实状态与记录数。", size=8)

h2(doc, "3.2 跨源离线证据（reports/cross_source_offline.json，已由第一轮复查 supporting）", size=10)
para(doc, "该报告为**注入 fixture + 期望先冻结**的内部一致性评测（`model_calls = 0`），"
          "不是泛化准确率，也不衡量真实模型：21/21 用例、161/161 断言通过；"
          "其中 6 条为设计内 fail-closed（4 条 `DOC_BINDING_MISSING` + 2 条 `CONFLICT_UNRESOLVED`），"
          "报告 `error_code_counts` 为 NONE=15。第一轮复查者独立复跑（R1）得到同一结果，逐位吻合。", size=9)
table(doc, ["指标", "分子/分母", "值", "来源"], [
    ["用例通过率（注入 fixture）", "21/21", "1.0", "cross_source_offline.json totals"],
    ["断言通过率（内部一致性）", "161/161", "1.0", "cross_source_offline.json checks"],
    ["设计内 fail-closed 保留", "6/21", "0.2857", "error_code_counts + cases[]"],
    ["模型调用次数", "0", "0", "model_calls"],
], widths=[5.4, 2.6, 2.2, 5.6], size=8)
para(doc, "结构性瑕疵（如实保留）：该报告的机器可读 `failure_samples` 为空数组，"
          "6 条预期拒答分布在 `cases[]` 与 `error_code_counts` 中，无法一键取到失败清单"
          "（review_round1_evidence §5 明确记录）。", size=8)

h2(doc, "3.3 绑定三臂消融（reports/binding_ablation.json，同轮新增；硬判定满足申报门槛）", size=10)
para(doc, "该报告在 40 例冻结绑定陷阱集（同对象跨版本 / 单位不可归一 / 时间口径不同 / 文档与库数值相反，"
          "各 10 例）上跑 `plain` / `bind_only` / `full` 三臂，作答器为确定性桩（`answerer = deterministic-stub`，"
          "`model_calls = 0`）。**硬判定**原文：`criterion = full 臂错误绑定率相对 plain 臂下降 ≥50% 且绝对值 ≤5%`，"
          "`plain_misbinding_rate = 0.8`、`full_misbinding_rate = 0.0`、`relative_drop = 1.0`、`meets = true`。", size=9)
table(doc, ["臂", "绑定准确率", "错误绑定率（high 声称口径）", "错误绑定率（全例口径）", "冲突 P/R", "误修复率"], [
    ["plain", "8/40 = 0.200", "32/40 = 0.800", "32/40 = 0.800", "N/A / 0/25", "N/A（无改写）"],
    ["bind_only", "8/40 = 0.200", "27/35 = 0.7714", "27/40 = 0.675", "N/A / 0/25", "N/A（无改写）"],
    ["full", "40/40 = 1.000", "**0/17 = 0.000**", "**0/40 = 0.000**", "**25/25 / 25/25**", "**0/9 = 0.000**"],
], widths=[2.2, 3.0, 4.6, 4.2, 3.4, 3.0], size=8)
para(doc, "full 臂修复账本：attempts 15 / changed 9 / rejected 6 / succeeded 9 / misrepair 0，"
          "成功率 9/9 = 1.0，误修复率 0/9 = 0.0（分母 = 发生改写的修复数）；full 臂 high 断言 17/40，"
          "另 23 例按设计拒答。字段级绑定（分母 = 17 例可作答）：plain `version` 9/17、`time_scope` 16/17，"
          "full 四项各 17/17。报告 `failures[]` 保留 **68 条** arm-case 失败（plain 34、bind_only 34、full 0）。"
          "敏感性探针：拆掉版本核验/冲突门/修复后，错误绑定率分别回到 0.500（9/18）/ 0.541（20/37）与绑定准确率 0.800。", size=8.5)
para(doc, "**未经独立复查的边界（必读）**：`reports/binding_ablation.json` 由本工作包于 "
          "2026-10-09 23:36（CST）落盘，晚于第一轮三份复查报告的结论冻结时刻（23:14）："
          "`review_round1_evidence.md` §6.2 当时记录的仍是「该文件完全缺失」。"
          "因此本节的全部数字属**同一轮自报、尚无独立复查者复核**；"
          "在下一轮复查复跑 `scripts/eval_binding.py --check` 之前，不得把这些数字当作已验证事实引用。", size=8.5)

h2(doc, "3.4 仅作合成机制替身引用的 reports/vcar_ablation.json（16 例）", size=10)
table(doc, ["臂", "正确/例数", "准确率", "误修复率", "冲突检出（真冲突 4）", "未消解"], [
    ["ocr_text", "3/16", "0.1875", "N/A（未改写）", "0/4（recall=0.0）", "0"],
    ["structured", "11/16", "0.6875", "0.0", "4/4（precision=1.0）", "0"],
    ["local", "11/16", "0.6875", "0.1429（2 例）", "4/4", "0"],
    ["full", "16/16", "1.0", "0.0", "4/4", "2（保留双方证据）"],
], widths=[2.2, 2.2, 2.0, 4.2, 4.4, 4.0], size=8)
para(doc, "来源：reports/vcar_ablation.json（dataset vcar-ablation-v1，sha256 前 16 位 74cbbc1ef760f530，cases=16，"
          "generated_at 2026-10-07T15:21:10+00:00；复查 C132 confirmed）。"
          "**该数字属于视觉载荷替身的合成机制消融，不调用任何模型，不是 OCR/问答准确率，"
          "不得改写成跨源绑定成绩。**", size=8)

h2(doc, "3.5 本轮未产出、因此不作主张的证据", size=10)
bullets(doc, [
    "`reports/cross_source_live.json`：**本轮未产出**（核对时刻 `ls` 退出码 1）→ 跨源 `--live` 端到端"
    "（真实演示库 + 真实模型）**没有成功记录**，本自述不给出任何 live 跨源数字。",
    "公开基准对比（Spider/BIRD 等）与真实视觉模型对比：**本轮未产出**，不作主张。",
    "逐页视觉检查（渲染预览）与干净环境演练：**本轮未做**，不作主张。",
], size=8.5)

h2(doc, "3.6 上述报告自带的边界（原文照录，必须与数字同时引用）", size=10)
code_block(doc, "binding_ablation.json boundaries（原文照录）：\n"
                "  · stub 作答器：不调用任何上游模型（model_calls=0），因此本报告不是真实模型准确率，\n"
                "    也不能外推为业务准确率；\n"
                "  · 自建冻结陷阱集：40 例由本仓库自行编写并冻结，plain 臂的失败来自陷阱的设计，\n"
                "    「相对下降」是机制对照结果，不是泛化能力的证明；\n"
                "  · 零分母一律 N/A（None），不用 0 冒充；分母分子报告在同一条目内；\n"
                "  · failures[] 保留每一条失败样本（含 plain/bind_only/full 三臂），未做任何剔除；\n"
                "  · 本报告不衡量真实模型、真实演示库查询或前端表现。\n"
                "claim_boundary（原文照录）：full 臂通过门槛只能支持「在冻结定义下机制有效」的实用创新举证；\n"
                "  若要主张技术突破领先性，还需要真实模型 + 独立外部数据的证据。", size=7.5)

# ---------- 4 诚实边界 ----------
h1(doc, "4. 诚实边界（不主张什么）", size=13)
numbered(doc, [
    "**无真实视觉模型对比**：上游仅文本模型 deepseek-flash，仓库无本地视觉权重目录（docs/MODEL_WEIGHTS.md）；"
    "`vcar_ablation.json` 的 local 臂是机制替身，不得当作真实视觉模型成绩。",
    "**不得主张业界领先**：无与公开最优方案的对比实验，无公开基准成绩；"
    "绑定三臂消融是**自建冻结集 + 确定性 stub** 上的机制对照，不是泛化能力，也不是业界领先。",
    "**离线 stub 不等于模型准确率**：`cross_source_offline.json`（model_calls=0，注入 fixture）与 "
    "`binding_ablation.json`（model_calls=0，deterministic-stub）都不是模型准确率，"
    "不得外推为业务准确率。",
    "**绑定消融数字尚未通过独立复查**（见 §3.3 末段）：同轮自报、结果写于复查冻结之后，"
    "引用时须带此限定；下一轮需复跑 `scripts/eval_binding.py --check`。",
    "**不主张通用跨源已实现**：`datamind/agent.py` 的空 allow-list 与关键词门问题已由安全复查列名为高危/中危"
    "（review_round1_security §1.4 / M1 / M2），本轮未修；跨源 live 无成功记录。",
    "**不主张任何官方得分**：官方测试集未提供；本项目全部数字来自自建或合成冻结集。",
    "**不主张「plain 臂一定更差」的普适结论**：本冻结集上 plain 错误绑定率 0.800，"
    "但该值由陷阱设计决定；若在独立外部数据上 plain 接近满分，本项只能申报转化与工程价值。",
    "**不主张「版本化证据绑定已全库落地」**：既有 `evidence_nodes`/`evidence_edges` 表结构见"
    "docs/SCORE_GAP_LEDGER.md §3.3，绑定字段的库级落地范围以该文件为准，本自述不扩大。",
], size=9)

# ---------- 5 评审价值 ----------
h1(doc, "5. 评审价值与可复核入口", size=13)
bullets(doc, [
    "对评委：本贡献把「可解释」从界面展示推进到可度量的绑定正确性；评委可用同一冻结集复跑三臂对照，"
    "并逐条阅读 68 条失败样本与敏感性探针。",
    "对工程：绑定字段与证据图是通用抽象，不依赖某个领域提示词；新增领域只需要登记元数据（已有 schema 注册表机制）。",
    "对产品：冲突「同时展示双方 + 待裁决」比自动选一个来源更贴近真实业务责任边界（谁有权裁定由人决定）。",
    "可复核入口：reports/binding_ablation.json、reports/cross_source_offline.json、reports/vcar_ablation.json、"
    "reports/cards/wp3_binding.md、reports/cards/wp2_cross_source.md、reports/review_round1_evidence.md §6、"
    "docs/SCORE_GAP_LEDGER.md §5。",
])
h2(doc, "5.1 本自述的复现命令（全部只读 / 不写演示库）", size=10)
code_block(doc, 'cd "/Users/caicai/DataMind Agent"\n'
                'git rev-parse --short HEAD                                    # e4e2ebf（只读）\n'
                'ls -l reports/binding_ablation.json reports/cross_source_offline.json reports/cross_source_live.json\n'
                '#   → 前两份存在；cross_source_live.json: No such file or directory（exit 1）\n'
                '.venv/bin/python scripts/eval_binding.py --check; echo "exit=$?"   # 0 → problems: []，summary 钉住\n'
                '.venv/bin/python -c "import json;d=json.load(open(\'reports/binding_ablation.json\'));'
                'print(d[\'verdict\'])"\n'
                '#   → {\'meets\': True, \'relative_drop\': 1.0, \'plain_misbinding_rate\': 0.8, \'full_misbinding_rate\': 0.0}\n'
                '.venv/bin/python -c "import json;d=json.load(open(\'reports/cross_source_offline.json\'));'
                'print(d[\'totals\'], d[\'model_calls\'])"\n'
                '#   → 21/21 passed；model_calls 0', size=7.5)
para(doc, "本自述与设计报告、技术说明书、答辩演示同步交付；所有数字均可由上述命令与报告文件复核。", size=8.5)

apply_bold_markers(doc)
doc.save(str(OUT))
print("WROTE", OUT)
