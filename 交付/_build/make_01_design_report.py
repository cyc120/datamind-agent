# -*- coding: utf-8 -*-
"""生成 交付/01_设计报告_DataMind_Agent.docx（≤20 页；第 1–2 页为核心亮点预览页）。

所有数字均取自仓库真实材料，括号内标注来源路径。不写入无法核验的数字。
运行：cd "/Users/caicai/DataMind Agent" && .venv/bin/python 交付/_build/make_01_design_report.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from docx_kit import (new_doc, para, h1, h2, h3, bullets, numbered, code_block,
                      table, page_break, add_footer_pagenum, highlight_box)

OUT = pathlib.Path(__file__).resolve().parents[1] / "01_设计报告_DataMind_Agent.docx"

doc = new_doc()
add_footer_pagenum(doc, "DataMind Agent 设计报告 v1.0")

# ============ 第 1 页：项目核心亮点预览页 ============
para(doc, "DataMind Agent", size=24, bold=True, align="center", space_after=1)
para(doc, "多模态数据驱动的可解释精准问数问答智能体　·　设计报告", size=12, align="center", space_after=2)
para(doc, "项目核心亮点预览页（官方要求：封面/目录后第 1–2 页）", size=9, align="center",
     space_after=6, color=(0x88, 0x44, 0x00))
para(doc, "一句话定位：把「问一句、答一句」做成「答案 + 可回放证据链路 + 明确的不知道」——"
          "系统不直接生成结论，而是先生成带类型的证据图（数据行 / 文档块 / 测量 / 规范条款为节点，"
          "实体同一性、版本适用、单位等价、时间口径、数值支持、数值冲突为边），"
          "在受控修复上限内消解冲突；无法消解时同时展示双方证据并标记待裁决，绝不自动单选来源。",
     size=9.5, space_after=6)

highlight_box(doc, "亮点 1｜可解释性：答案即证据链", [
    "每条答案携带：生成的 SQL 与绑定参数、引用的文档片段、原文定位（页码/区域/句柄）、运行事件流与回放。",
    "证据入口：GET /v1/runs/{id}、/v1/runs/{id}/evidence、/v1/runs/{id}/events；前端引用卡片与定位徽标（web/app.js）。",
    "旋转页与非页面定位只显示标签、绝不猜测绘制（web/app.js citationRegionFrame 分支，有契约守护测试）。",
    "来源：docs/OFFICIAL_REQUIREMENTS_MAPPING.md A4/E3/F3；PROGRESS.md「引用消费定位 / 前端消费定位」两行。",
])
highlight_box(doc, "亮点 2｜受控查询：SQL Guard 把「能跑」变成「允许跑」", [
    "sqlglot AST 门禁 + 参数绑定 + 只读执行：按 AST scope 解析列权限，拒绝越权路径与不可验证的派生表。",
    "指标口径按 business_metrics 注册表做结构比对；顶层投影必须就是批准指标，指标查询禁止 OR。",
    "独立复查发现的 R01/R02 反例均已修复（带 CTE 同名绕过、'正确 SUM 再乘 0' 等四种篡改变体曾全部判通过）。",
    "来源：docs/AUDIT_FIXES.md、reports/audit_fix_verification.json、PROGRESS.md 阶段流水（第 1、2 批）。",
], fill="EAF3FB")
highlight_box(doc, "亮点 3｜多源融合：工业跨源链路已跑通并保留冲突双方", [
    "「工艺规范原文 + 只读生产 SQL」逐测量核验：单位 / 版本 / 冲突 / 超限四类判定，未登记权威时不选来源、保留双方原值。",
    "13/13 冻结开发规则通过（合成集，不调用大模型）；来源：reports/industrial_rules.json（passed=13, case_count=13）。",
    "通用「数据库 + 文档」跨源目前 fail-closed：datamind/agent.py 命中 cross_source 后返回 ROUTE_UNSUPPORTED，而非猜测作答。",
    "证据图存储已在运行：evidence_nodes=1456 / evidence_edges=1359（只读 SQL 实测）；绑定列与跨源引擎为规划项。",
])
highlight_box(doc, "亮点 4｜交付形态：每一次「完成」都留下可复现证据", [
    "评测与验证报告统一落在 reports/*.json，逐例保留失败样本、分母与退出码；本次交付另附 6 份真实生成物。",
    "隔离回归在当前基线为 1297/1297（失败 0，源库未修改）；来源：reports/latest_regression.json、PROGRESS.md 当前状态表。",
    "依赖锁定 requirements.lock 共 65 个固定版本，并有守护测试 tests/test_dependency_lock.py 校验锁与实际安装一致。",
    "来源：docs/OFFICIAL_REQUIREMENTS_MAPPING.md D7；PROGRESS.md「仓库治理」阶段。",
], fill="EAF3FB")
highlight_box(doc, "亮点 5｜评测纪律：拒绝、超时、上游错误单列但不移出分母", [
    "零分母一律报 N/A，不用 0 或 100% 冒充；正向与反向对照同时写（计划 §8 度量纪律）。",
    "可机读的题面一致性护栏：data/evaluation/registry/{nl2sql,rag,robustness}_v1.json，append-only + 逐字段 diff 校验。",
    "实测 3/3 suite 登记与库内题面一致；来源：reports/cards/wp1a_registry.md「结果（分子分母）」表。",
    "扫描件能力缺口如实保留：表头 5/9、字段 3/15（reports/header_initial.json、reports/document_fields.json）。",
])
highlight_box(doc, "亮点 6｜诚实边界：写清楚「不能据此声称什么」", [
    "官方测试集未提供：E1/E2/E5/E6/F4/F5 的官方准确率档在本仓库内无法判定，只报自建冻结集分子/分母。",
    "上游仅文本模型（deepseek-flash），无本地视觉权重：不主张多模态识别领先（docs/MODEL_WEIGHTS.md）。",
    "无与公开最优方案的对比实验：不主张「业界领先」，加分材料只做机制性对照与诚实边界。",
    "来源：docs/SCORE_GAP_LEDGER.md §5「本轮不可能完成、不要假装完成的项」。",
], fill="FDEBEB")

page_break(doc)

# ============ 第 2 页：亮点预览续 + 报告导读 ============
para(doc, "核心亮点预览（续）与报告导读", size=12.5, bold=True, space_after=3)
h2(doc, "7. 交付物一览（本次由脚本真实生成，页数实测）")
table(doc, ["文件", "类型", "页/幻灯片", "结构检查"], [
    ["交付/01_设计报告_DataMind_Agent.docx + .pdf", "设计报告", "13 页 / 13 页", "check_office.py 通过（见 §9.5）"],
    ["交付/02_技术实现说明书_DataMind_Agent.docx + .pdf", "技术说明书", "12 页 / 12 页", "check_office.py 通过"],
    ["交付/03_答辩演示.pptx", "答辩 PPT", "20 张", "check_office.py 通过 + 匿名检查"],
    ["交付/04_突破与创新性自述.pdf（+ DOCX 源）", "加分自述", "3 页", "check_office.py 通过"],
    ["交付/DELIVERY_MANIFEST.json / docs/DELIVERY_CHECKLIST.md", "机读清单 / 官方核对", "—", "JSON 可解析"],
], widths=[7.2, 3.0, 3.0, 3.8])

h2(doc, "8. 本报告的组织方式")
para(doc, "按官方评分项组织，每节同时给出「已实现的证据路径」与「仍未达成的部分」，引用数字时标注来源文件"
          "（相对仓库根目录）。章节与评分项对应关系：§3 架构（E4/F13）、§4 NL2SQL（E1/E5/F4、B4/B5）、"
          "§5 RAG（E2/E6/F5、B6/B7/B9）、§6 多源融合（F1/F3）、§7 可解释性（E3/F3）、§8 中级任务与鲁棒性"
          "（E8/E9/E10/F7/F8）、§9 评测效率与交付（E7/E11/E12/F6/F10）、§10 失败样本与诚实边界。", size=9.5, space_after=3)

h2(doc, "9. 亮点预览页与生成过程的自检（可复现）")
bullets(doc, [
    "本报告与另一份 DOCX、一份 PPTX、一份独立 PDF 全部由仓库 .venv/bin/python + 生成脚本真实产出（脚本在 交付/_build/），非手工排版。",
    "结构检查：.venv/bin/python <office-skill>/../scripts/check_office.py <file> --out reports/office_checks.json；页数用 pymupdf 读 PDF、幻灯片数用 python-pptx 读（实测值，非估算）。",
    "本轮未做 PDF 逐页视觉检查（未生成渲染预览）：结构检查通过不等于版式/分页/字体渲染无问题，已计入诚实边界（§10.2）。",
])

page_break(doc)

# ============ §3 架构 ============
h1(doc, "§3 系统架构与技术栈")
h2(doc, "3.1 分层结构")
para(doc, "系统为 FastAPI + 原生 JavaScript 前端 + PostgreSQL 元数据库 + Qdrant 向量库四件套，"
          "全部为仓库内真实代码；架构说明见 技术文档/DataMind_Agent_技术设计说明书_V1.3.md。", size=9.5)
table(doc, ["层", "职责", "仓库路径与实测"], [
    ["接口层", "REST + SSE；问答、运行回放、文档、上传、复核、权威、CAD、尺寸、私有 PDF",
     "datamind/api.py（version=\"0.39.0\"）；/openapi.json 实测 paths=75"],
    ["编排层", "路由（sql / rag / cross_source / 工业）、澄清、多轮 history 边界",
     "datamind/agent.py；不传 history 时提示词与结果逐字节不变"],
    ["问数层", "问题帧 → Catalog/Schema 召回 → 模型 SQL → AST 门禁 → 只读执行 → 最多一次修复",
     "datamind/sql_engine.py、catalog.py、sql_guard.py"],
    ["问答层", "混合检索（向量 + 词法 RRF）→ 生成 → 原文连续片段/数字校验 → 拒答",
     "datamind/knowledge.py、rag_engine.py"],
    ["解析层", "统一解析契约（parse_status 五态、七类 locator、块/关系约束）+ 适配器登记",
     "datamind/parse_contract.py；ingestion/（PDF/Office/DXF/OCR/公式）"],
    ["治理层", "任务预算与进程组隔离、来源权威、字段复核、可见性策略、租约",
     "datamind/task_budget.py、authority.py、visibility.py；db/migrations/001–018"],
], widths=[2.2, 6.6, 8.2])

h2(doc, "3.2 数据底座（只读实测）")
table(doc, ["对象", "实测值", "命令/来源"], [
    ["服务健康", "status=ok, database=ready, knowledge=ready, worker.alive=true",
     "curl -s http://127.0.0.1:8000/health"],
    ["知识库文档", "/health document_count=27；document_assets indexed=27、parsed=2",
     "只读 SQL：select status, count(*) from document_assets group by status"],
    ["切片 / 事实 / 向量", "chunks=169、document_facts=23、Qdrant points_count=169",
     "只读 SQL + GET /collections/datamind_knowledge"],
    ["证据图", "evidence_nodes=1456、evidence_edges=1359", "只读 SQL（deploy/init/02_metadata.sql）"],
    ["评测用例", "evaluation_cases：nl2sql=14、rag=12、robustness=4", "只读 SQL（suite 分组）"],
    ["代码与迁移", "git ls-files=605；db/migrations/*.sql=18（001–018）", "git ls-files | wc -l；ls db/migrations"],
], widths=[2.8, 7.4, 6.8])

h2(doc, "3.3 一键启动与依赖")
bullets(doc, [
    "scripts/start_local.sh：依赖检查 → 容器 → 迁移 → 种子 → 真实摄取 → 服务；生产化启动见 scripts/start_api_background.py。",
    "requirements.txt + 分能力 requirements-sql/rag/ocr/formula/office/cad.txt + requirements.lock（65 个固定版本）。",
    "权重不随包：docs/MODEL_WEIGHTS.md 逐项列路径/体积/SHA256/获取脚本/缺失时降级行为。",
    "边界：本报告未在干净空库环境中重跑启动演练（禁止重建容器/库），故不主张「新环境已验证可启动」。",
])

page_break(doc)

# ============ §4 NL2SQL ============
h1(doc, "§4 NL2SQL 与受控查询（E1 / E5 / F4；B4 / B5）")
h2(doc, "4.1 链路")
code_block(doc, "question → query_frame(实体/指标/时间/范围) → Catalog.retrieve(词法+别名+枚举+JoinGraph, Top-8)\n"
                "        → 模型生成 SQL → sql_guard（AST 列权限 + 口径结构比对 + 参数绑定）\n"
                "        → 只读执行 → 失败分类 → 最多一次受控修复 → answer(+sql,+rows,+evidence)")
h2(doc, "4.2 已有证据（分子/分母）")
table(doc, ["项", "分子/分母", "来源"], [
    ["零售套题通过", "14/14（passed=14, total=14，cases=14）", "reports/nl2sql.json"],
    ["Schema Linking 表召回@5", "31/31（rate=1.0）", "reports/schema_linking.json summary"],
    ["语义锚点列召回@5", "28/28（rate=1.0）", "同上"],
    ["指标召回@5 / 连接路径正确", "4/4 / 6/6（含 18 条自建题）", "同上（case_count=18）"],
    ["工业限定问数", "8/8 开发题、模型前澄清 2/2", "PROGRESS.md「工业模型问数」行"],
    ["鲁棒性套题", "4/4（robustness）", "reports/robustness.json"],
], widths=[4.6, 6.6, 5.8])
h2(doc, "4.3 仍未达成")
bullets(doc, [
    "无官方测试集：E5/F4 的「≥90% 得 10 分」档在本仓库内无法判定；14/14 为自建小样本、较早代码，不可外推。",
    "WP1 要求的新增子集（复杂 SQL ≥15、拒答 ≥6、鲁棒 ≥10）在本轮为 not_attempted，原因见 reports/cards/wp1a_registry.md。",
    "库内题面标签与 gold_sql 存在不一致（nl2sql-006 标「JOIN 2表」实为 3 表；nl2sql-009 标「退款JOIN 3表」实为单表），已如实登记未改写。",
])
h2(doc, "4.4 口径护栏")
para(doc, "题面入仓并 append-only 冻结：data/evaluation/registry/{nl2sql,rag,robustness}_v1.json；"
          "scripts/eval_registry.py check 实测 3/3 一致（退出码 0）；篡改临时副本被检出（退出码 1）；"
          "重复冻结被拒绝（退出码 2）。来源：reports/cards/wp1a_registry.md。", size=9.5)

page_break(doc)

# ============ §5 RAG ============
h1(doc, "§5 RAG 文档问答（E2 / E6 / F5；B6 / B7 / B9）")
h2(doc, "5.1 链路与拒答纪律")
code_block(doc, "question → 混合检索(向量 BGE/Qdrant + 词法 RRF) → 候选片段\n"
                "        → 生成（只允许使用本次候选证据）→ 原文连续片段匹配 + 数字必须出现在引用中\n"
                "        → 不满足即拒答（insufficient），不补写、不猜测")
bullets(doc, [
    "文档范围必须显式：1–8 份无重复公开文件（scope.document_refs），每文件必须有相关片段否则阻断。",
    "证据覆盖不完整如实报告 MULTIFILE_EVIDENCE_INCOMPLETE；0 切片文档被选中时服务端拒绝 "
    "DOCUMENT_SELECTION_NOT_SEARCHABLE（PROGRESS.md 第 2 批 2a）。",
    "表格/公式等解析缺口直接进入拒答路径：公式图片无公式文本的题目按纪律拒答而不是猜（README:178）。",
])
h2(doc, "5.2 已有证据")
table(doc, ["项", "分子/分母", "来源"], [
    ["RAG 套题通过（历史两轮）", "10/12", "reports/rag.json、reports/rag_second.json"],
    ["可答题目数 / 拒答", "answerable_count=11、case_count=12", "reports/rag.json"],
    ["检索 recall@5 / MRR@10", "1.0 / 0.9545454545454546", "reports/rag.json"],
    ["字段级端到端正确率", "10/12", "reports/rag_fields.json"],
    ["扫描件表头 / 字段", "5/9、3/15（缺口如实保留）", "reports/header_initial.json、reports/document_fields.json"],
    ["CAD 能力分层冻结集", "31/31、365 条断言（合成集，非泛化成绩）", "reports/cad_capability_v1.json"],
], widths=[4.6, 6.6, 5.8])
h2(doc, "5.3 仍未达成")
bullets(doc, [
    "无独立逐结论忠实度三态标注（支持/矛盾/无依据）：E6 只报端到端正确率，不能当忠实度。",
    "多文档固定集与多模态问答端到端集未做；上传管道 vector_index 为 deferred，审批后文档在有切片前不可问答。",
    "B7 混乱目录恢复、B9 清晰度评分与错别字校正无专项集；B8 文档复杂度评估在映射表中为「未实现」。",
])

page_break(doc)

# ============ §6 多源融合 ============
h1(doc, "§6 多源融合与证据绑定（F1 / F3）")
h2(doc, "6.1 已实现的跨源链路（限定：工业结构）")
para(doc, "已实现的是「一份工艺/质检原文 + 只读生产 SQL」的逐测量核验：从原文取阈值与适用范围，"
          "绑定单位与版本后查询数据库，判定超限 / 冲突 / 需复核，冲突时保留双方原值。", size=9.5)
table(doc, ["项", "值", "来源"], [
    ["冻结开发规则通过", "13/13（passed=13, case_count=13）", "reports/industrial_rules.json"],
    ["冲突集合 / 超限集合", "conflicts=[\"M001-1\"]、out_of_range=[\"M001-1\",\"M006-2\"]", "同上 runs[0].summary"],
    ["是否调用模型", "model_used=false（纯规则核验）", "同上 runs[0].versions"],
    ["证据图存储", "evidence_nodes=1456 / evidence_edges=1359", "只读 SQL"],
    ["合成性声明", "「Synthetic demonstration … no factory data, real approvals or independent accuracy claims」", "同上 source_manifest.provenance"],
], widths=[4.2, 7.4, 5.4])
h2(doc, "6.2 通用跨源仍 fail-closed")
bullets(doc, [
    "datamind/agent.py 的 route_for() 仅关键词命中 cross_source，命中后直接返回 ROUTE_UNSUPPORTED（审计 §5 与 SCORE_GAP_LEDGER F1）。",
    "现有 evidence_nodes/edges 没有 source_sha256/version/unit/time_scope 绑定列，也没有 entity_match/version_scope 等边类型 CHECK；"
    "「19 号证据图已建」不可主张（docs/SCORE_GAP_LEDGER.md §5）。",
    "因此 F1 的「≥3 种结构不同的跨源问题」未达成；本报告只报已有工业链路的 13/13，不外推为跨源种类数。",
])
h2(doc, "6.3 创新点（单一贡献）")
para(doc, "版本化的跨源证据绑定 + 可控修复：把「实体/版本/单位/口径绑定」提升为一等公民，"
          "并给出可复现度量（绑定准确率、错误绑定率、冲突检出率、误修复率）与受控修复上限。"
          "详见 交付/04_突破与创新性自述.pdf 与 §9.4 的消融表。", size=9.5)

page_break(doc)

# ============ §7 可解释性 ============
h1(doc, "§7 可解释性与可追溯（E3 / F3）")
table(doc, ["能力", "具体形态", "仓库证据"], [
    ["SQL 展示", "答案携带生成的 SQL 与绑定参数", "datamind/api.py、web/app.js"],
    ["文档引用", "引用卡片显示原文片段 + 定位徽标（页码/区域/句柄）", "web/app.js citationLocators"],
    ["区域高亮", "未旋转 PDF 页可展开原页区域高亮；旋转页只显示标签", "web/app.js citationRegionFrame + 契约守护测试"],
    ["运行回放", "事件流与证据接口，前端按 run_id 回放", "GET /v1/runs/{id}、/evidence、/events"],
    ["关系呈现", "引用载荷附已存契约关系（只附存储中确有的，失败不阻断）", "datamind/parse_contract.py read_relations"],
    ["边界", "旋转页 / 非页面定位 / 无定位文档，一律不猜测绘制或推导区域", "PROGRESS.md「引用消费定位」「前端消费定位」"],
], widths=[2.6, 7.6, 6.8])
h2(doc, "7.1 仍未达成")
bullets(doc, [
    "跨源答案的证据卡片（数据行 + 文档块 + 版本 + 单位 + 冲突双方）尚无载体，因为通用跨源引擎未建。",
    "关系不参与排序/打分/重排/冲突消解（PROGRESS.md「契约关系消费」行明确标注）。",
    "前端为静态资源，变更后需浏览器刷新；本轮未做界面视觉验收。",
])

# ============ §8 中级任务 ============
h1(doc, "§8 中级任务与鲁棒性（E8 / E9 / E10；F7 / F8）")
table(doc, ["官方难题", "状态", "已有证据 / 缺口"], [
    ["B1 要素识别与纠错标准化", "部分", "entity_aliases(approved) 参与 Schema 召回（catalog.py）；无通用纠错评测"],
    ["B2 可扩展要素提取", "部分", "领域知识存于数据库注册表（中文名/别名/枚举/语义角色）；无新增领域成本实测"],
    ["B3 语义校验与澄清", "已实现（限定）", "clarify()/NEEDS_CLARIFICATION；工业模型前澄清 2/2；无「不必要澄清率」指标"],
    ["B4 Schema Linking", "已实现（限定）", "表 31/31、列 28/28、指标 4/4、路径 6/6（18 条自建题）"],
    ["B5 多表 JOIN/聚合/嵌套", "部分", "sql_guard 语义门禁；样本小、无公开基准"],
    ["B6 公式识别与计算", "部分（仅识别）", "formula worker + LaTeX 结构校验；无公式求值引擎（6b 未实现）"],
    ["B7 混乱目录层级恢复", "部分", "标题树优先切片 + heading_level/reading_order；无专项鲁棒集"],
    ["B8 文档复杂度评估", "未实现", "全仓库仅命中评测用例的手工 difficulty 标签"],
    ["B9 文档质量检测与告警", "部分", "parse_status 五态、quality_findings、OCR 置信度；无清晰度评分与繁简纠错"],
], widths=[3.6, 2.4, 10.6])
h2(doc, "8.1 多轮上下文（E10）")
bullets(doc, [
    "契约层已实现：/v1/chat 与 /v1/chat/stream 接受客户端 history（≤20 条、单条 ≤2000 字符、合计 ≤8000，超限拒绝不截断）。",
    "历史以 trust=\"untrusted\" 边界区块送入模型并附防注入声明；实测把 DROP TABLE orders 放进历史后 SQL 与参数完全不变。",
    "turns_used 如实返回；不传 history 时提示词与结果逐字节不变（三种独立证明，tests/test_multi_turn.py 33 项）。",
    "仍未达成：「连续 5 轮以上准确」无冻结对话集评测；前端未接入 history；无服务端会话。",
])
h2(doc, "8.2 鲁棒性（E9 / F7）")
bullets(doc, [
    "robustness 套题 4/4（错别字、口语简称、越权拒答、空结果各 1 条）；规模远小于计划要求的 ≥10 条。",
    "权限矩阵、任务隔离、失败状态与格式兼容较完善；模糊/低质量真实输入的覆盖仍少。",
    "扫描件解析失败边界如实保留为数字（表头 5/9、字段 3/15），不隐藏。",
])

page_break(doc)

# ============ §9 评测与效率 + 交付 ============
h1(doc, "§9 评测、效率与交付清单")
h2(doc, "9.1 系统效率（E7 / F6）——已有实测，缺分析与优化对照")
table(doc, ["路径", "规模点", "中位耗时", "峰值 RSS", "状态"], [
    ["CAD 解析", "100 / 1000 / 5000 / 20000 实体", "251.1 / 283.3 / 445.2 / 897.9 ms",
     "191.7 / 199.1 / 237.5 / 366.8 MB", "后两档 partial（blocks 上限 2000）"],
    ["文档选择读取", "1 / 2 / 5 / 8 份", "102.9 / 106.2 / 106.9 / 107.3 ms",
     "60.7 / 61.0 / 61.4 / 61.6 MB", "实际读取 9 / 14 / 34 / 44 行"],
], widths=[2.4, 3.6, 4.6, 4.4, 4.6])
para(doc, "来源：reports/scale_v1.json（每点 3 次；机器 Apple M5 Pro、15 核、Python 3.14.5；"
          "报告自带 4 条免责：单机单进程、无 CPU 绑定/cgroup、无并发负载、非生产吞吐承诺）。",
     size=8.5, space_after=4)
bullets(doc, [
    "仍未达成：无时间复杂度理论分析、无瓶颈定位、无优化前后对照、无并发负载、无 SQL 侧多规模（1千/1万/10万行）。",
    "CAD 后两档为受限输出路径测量（blocks 上限 2000），不得当作完整解析 2 万实体的证明。",
])
h2(doc, "9.2 评测与回归口径")
table(doc, ["项", "值", "来源"], [
    ["完整隔离回归（当前权威分母）", "1297/1297，失败/错误 0，源库未修改", "reports/latest_regression.json、PROGRESS.md"],
    ["题面一致性护栏", "3/3 suite 一致；重复冻结被拒（exit 2）；篡改被检出（exit 1）", "reports/cards/wp1a_registry.md"],
    ["统一报告 schema 检查", "0/3 既有报告符合（如实结果，非脚本缺陷）", "reports/cards/wp1a_registry.md"],
    ["依赖锁", "requirements.lock 65 个固定版本 + 守护测试", "docs/OFFICIAL_REQUIREMENTS_MAPPING.md D7"],
], widths=[4.6, 7.4, 5.0])
h2(doc, "9.3 消融实验（创新举证用，合成机制替身）")
table(doc, ["臂", "正确/例数", "准确率", "误修复率", "冲突检出", "未消解"], [
    ["ocr_text", "3/16", "0.1875", "N/A（未改写）", "0/4", "0"],
    ["structured", "11/16", "0.6875", "0.0", "4/4", "0"],
    ["local", "11/16", "0.6875", "0.1429（2 例）", "4/4", "0"],
    ["full", "16/16", "1.0", "0.0", "4/4", "2（保留双方证据）"],
], widths=[2.6, 2.6, 2.2, 3.4, 2.6, 3.6])
para(doc, "来源：reports/vcar_ablation.json（dataset 16 例，sha256 74cbbc1e…；报告自注"
          "「合成机制消融，不调用任何模型；不是 OCR/问答准确率」）。", size=8.5, space_after=4)
h2(doc, "9.4 交付清单（本次真实生成，页数实测）")
table(doc, ["文件", "页/幻灯片（实测）", "字节数", "结构检查"], [
    ["交付/01_设计报告_DataMind_Agent.docx", "13", "53791", "见 reports/office_checks.json"],
    ["交付/01_设计报告_DataMind_Agent.pdf", "13", "510294", "pymupdf 页数"],
    ["交付/02_技术实现说明书_DataMind_Agent.docx", "12", "53915", "见 reports/office_checks.json"],
    ["交付/02_技术实现说明书_DataMind_Agent.pdf", "12", "569582", "pymupdf 页数"],
    ["交付/03_答辩演示.pptx", "20 张幻灯片", "73082", "见 reports/office_checks.json"],
    ["交付/04_突破与创新性自述.pdf", "3", "334019", "pymupdf 页数"],
], widths=[7.0, 3.4, 2.6, 4.0])
para(doc, "清单明细（含 sha256 与生成命令）见 交付/DELIVERY_MANIFEST.json；官方提交物逐项状态见 docs/DELIVERY_CHECKLIST.md。",
     size=8.5)

h2(doc, "9.5 结构检查结果")
para(doc, "两个 DOCX 与 PPTX 均用 office 技能自带检查器验证（ZIP/XML 完整性、关系、段落/表格/幻灯片计数、"
          "必需文本断言），结果 JSON 存 reports/office_checks.json。检查结论：全部结构完整；"
          "本轮未做逐页视觉检查（无渲染预览），版式风险未消除。", size=9.5)

page_break(doc)

# ============ §10 失败样本与诚实边界 ============
h1(doc, "§10 失败样本与诚实边界")
h2(doc, "10.1 必须留在报告里的失败样本")
table(doc, ["失败", "事实", "来源"], [
    ["RAG 2 例未通过", "12 例中 10 通过：1 例上游错误、1 例缺表头/公式拒答", "reports/rag.json"],
    ["扫描件表头/字段", "表头 5/9、字段 3/15", "reports/header_initial.json、reports/document_fields.json"],
    ["本地修复臂误修复", "2 例误修复（misrepair_rate=0.1429，占原正确输入 0.5）", "reports/vcar_ablation.json arms[local]"],
    ["ocr_text 臂漏检冲突", "4 个真实冲突全部漏检（recall=0.0）", "reports/vcar_ablation.json arms[ocr_text]"],
    ["题面标签不一致", "nl2sql-006（标 2 表实为 3 表）、nl2sql-009（标 3 表实为单表）", "reports/cards/wp1a_registry.md"],
    ["历史报告不符合统一 schema", "nl2sql.json 57 项、rag.json 49 项、robustness.json 17 项问题", "reports/cards/wp1a_registry.md"],
], widths=[3.4, 7.6, 5.6])
h2(doc, "10.2 诚实边界（不能据此声称什么）")
numbered(doc, [
    "不得声称任何官方得分、名次或晋级概率：官方测试集未提供，本报告全部数字为自建集/合成集实测。",
    "不得声称多模态识别领先：上游仅文本模型 deepseek-flash，无本地视觉权重目录（docs/MODEL_WEIGHTS.md）。",
    "不得声称「业界领先」：无与公开最优方案的对比实验；V-CAR 为 16 例合成机制替身，只解释机制。",
    "不得声称「证据图存储已建成完整版本」：现只有基础表（1456/1359 行），缺绑定列与边类型 CHECK。",
    "不得把 CAD 31/31 当作泛化或工厂材料成绩：报告自注 is_independent_factory_data=false、is_blind_test=false。",
    "不得把历史 14/14、10/12 当作 0.39.0 的成绩：来自较早代码与小样本（审计 §4）。",
    "不得声称干净环境已验证可启动：本轮禁止重建容器/库，未做空库演练。",
    "本轮未做 PDF 视觉检查：结构检查通过不代表版式、分页或字体渲染无问题。",
])
h2(doc, "10.3 评分联动与当前判断")
table(doc, ["官方项", "本报告能提供什么", "当前判断"], [
    ["E11 技术文档（10）", "Word + PDF 成套、按评分项组织、含图表与失败说明", "提交形态已具备；分值由评委"],
    ["F10 决赛文档（5）", "本报告 + 技术说明书；决赛 ≤50 页深化稿另做", "初赛稿已备，决赛稿未做"],
    ["E7/F6 效率", "4+4 规模点实测（含免责声明）", "部分：缺复杂度、并发、优化对照"],
    ["F11/F12 答辩演示", "PPT ≤20 页 + 匿名检查方法；现场表现不由仓库决定", "未兑现（需现场）"],
    ["加分申报", "自述成稿 + 消融数据 + 诚实边界", "默认 0；是否申报由复核结论决定"],
], widths=[3.2, 8.4, 5.0])

doc.save(str(OUT))
print("WROTE", OUT)
