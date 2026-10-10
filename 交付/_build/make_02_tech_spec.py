# -*- coding: utf-8 -*-
"""生成 交付/02_技术实现说明书_DataMind_Agent.docx（≤30 页）。

内容取自仓库真实材料；Mermaid 以「文字说明 + 代码块」形式给出（Word 内不渲染）。
运行：cd "/Users/caicai/DataMind Agent" && .venv/bin/python 交付/_build/make_02_tech_spec.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from docx_kit import (new_doc, para, h1, h2, h3, bullets, numbered, code_block,
                      table, page_break, add_footer_pagenum)

OUT = pathlib.Path(__file__).resolve().parents[1] / "02_技术实现说明书_DataMind_Agent.docx"

doc = new_doc()
add_footer_pagenum(doc, "DataMind Agent 技术实现说明书 v1.0")

para(doc, "DataMind Agent 技术实现说明书", size=22, bold=True, align="center", space_after=2)
para(doc, "多模态数据驱动的可解释精准问数问答智能体", size=12, align="center", space_after=2)
para(doc, "基线提交 e4e2ebf　·　API 0.39.0　·　覆盖范围：架构 / NL2SQL / RAG / 多源融合 / 可解释性 / 中级任务 / 测试与评估",
     size=9, align="center", space_after=6)
para(doc, "阅读约定：文中每个数字后标注来源文件（相对仓库根目录）；数字分三类——"
          "①只读实测（/health、/openapi.json、只读 SQL）；②既有报告原值（reports/*.json）；"
          "③官方评分表原文（赛题 PDF 第 10–14 页）。三类不混用，需现场判定的一律写「无法判定」。",
     size=9, space_after=4)

# ---------------- 1 架构 ----------------
h1(doc, "1. 系统架构")
h2(doc, "1.1 部署形态与进程")
code_block(doc, "┌────────────┐   HTTP/SSE   ┌──────────────────────────────┐\n"
                "│ 浏览器前端 │ ───────────► │ FastAPI 服务 datamind/api.py │\n"
                "│ web/       │ ◄─────────── │ version=0.39.0（paths=75）   │\n"
                "└────────────┘              └───────┬──────────┬───────────┘\n"
                "                                   │          │\n"
                "                     ┌─────────────▼──┐   ┌───▼─────────────┐\n"
                "                     │ PostgreSQL     │   │ Qdrant 6333     │\n"
                "                     │ datamind_meta  │   │ datamind_knowledge\n"
                "                     └────────────────┘   └─────────────────┘\n"
                "         独立进程：上传 worker / PDF 索引 worker / 公式 worker(.venv-formula)\n"
                "         外部依赖：https://api.deepseek.com/v1 模型 deepseek-flash（直连，无本地网关）")
para(doc, "Mermaid（用代码块描述，Word 内不渲染）：", size=9)
code_block(doc, "flowchart LR\n  U[User] --> API[FastAPI api.py]\n"
                "  API --> AG[agent.py route_for]\n  AG -->|sql| SQ[sql_engine+sql_guard]\n"
                "  AG -->|rag| RG[rag_engine+knowledge]\n  AG -->|cross_source| UN[ROUTE_UNSUPPORTED]\n"
                "  SQ --> PG[(PostgreSQL datamind_meta)]\n  RG --> QD[(Qdrant)]\n"
                "  SQ --> EV[(runs/events/evidence)]\n  RG --> EV\n  EV --> API")
h2(doc, "1.2 分层职责（与代码路径一一对应）")
table(doc, ["层", "职责", "路径"], [
    ["接口", "REST + SSE；问答/回放/文档/上传/复核/权威/CAD/尺寸/私有 PDF", "datamind/api.py"],
    ["编排", "路由 sql|rag|cross_source|工业；澄清；多轮 history 边界", "datamind/agent.py"],
    ["问数", "问题帧 → Catalog 召回 → 模型 SQL → AST 门禁 → 只读执行 → 一次修复", "sql_engine.py, catalog.py, sql_guard.py"],
    ["问答", "混合检索（向量+词法 RRF）→ 生成 → 片段/数字校验 → 拒答", "knowledge.py, rag_engine.py"],
    ["解析", "统一解析契约（parse_status 五态、七类 locator、块/关系约束）", "parse_contract.py, ingestion/*"],
    ["治理", "任务预算与进程组隔离、来源权威、字段复核、可见性、租约", "task_budget.py, authority.py, visibility.py"],
    ["存储", "18 个迁移 001–018；元数据库 + 契约存储 + 证据图 + 任务/租约表", "db/migrations/, deploy/init/"],
], widths=[1.8, 8.0, 7.0])
h2(doc, "1.3 运行实例（只读实测，2026-10-09）")
table(doc, ["指标", "值", "命令"], [
    ["/health", "status=ok, database=ready, knowledge=ready, vector_store=ready", "curl http://127.0.0.1:8000/health"],
    ["document_count", "27", "同上（另有 parsed 未索引 2 份，仅 SQL 可见）"],
    ["chunks / facts / vectors", "169 / 23 / 169", "只读 SQL + GET /collections/datamind_knowledge"],
    ["evidence_nodes / edges", "1456 / 1359", "只读 SQL"],
    ["evaluation_cases", "nl2sql=14、rag=12、robustness=4", "只读 SQL（按 suite 分组）"],
    ["API 路径数 / 版本", "75 / 0.39.0", "GET /openapi.json"],
], widths=[4.0, 8.0, 6.0])

page_break(doc)

# ---------------- 2 统一解析契约 ----------------
h1(doc, "2. 统一解析契约与多模态接入")
para(doc, "整个系统的地基是「解析结果必须自证」：任何适配器产出的块与定位都要通过契约校验才能入库，"
          "缺口必须显式声明而不是留空。", size=9.5)
h2(doc, "2.1 契约要素")
table(doc, ["要素", "内容", "代码/证据"], [
    ["parse_status 五态", "parsed / partial / failed / skipped / unsupported 语义化状态机", "datamind/parse_contract.py"],
    ["七类 SourceLocator", "pdf_region、office_object、cad_entity、sheet_range、formula_region 等", "同上（各自有必填字段与校验）"],
    ["块与关系约束", "block_id 定长摘要；relation 需 evidence 非空、禁止自引用；写库前再次校验", "db/migrations/014–017"],
    ["适配器登记与路由", "按媒体签名探测 → dispatch 到已登记 handler；未登记报 PARSE_ADAPTER_UNKNOWN", "ingestion/（PDF/Office/DXF/OHR JSON）"],
    ["定位写入", "document_block_locators 存 11 条真实 PDF 定位（实机验证）", "PROGRESS.md 0.37.0「运行验收」"],
], widths=[3.0, 8.4, 5.4])
h2(doc, "2.2 各模态的接入现状与缺口")
table(doc, ["模态", "现状", "缺口（如实）"], [
    ["原生 PDF", "按 SHA 固定源、版本化块/切片、旋转/裁剪坐标保守处理", "不发布到知识库；非 cgroup 硬隔离"],
    ["扫描 PDF", "原像素 OCR；块与 detail 与进程内路径关键字段一致（16 块/27 行/1.79s 实测）", "表头 5/9、字段 3/15；清晰度评分与繁简纠错未做"],
    ["DOCX/PPTX/XLSX", "DOCX 标题/段落/整表、PPTX 文本框、XLSX 按行（真实行号）映射进契约", "未解析样式/批注/修订/图表/备注/公式/合并单元格；Office 块未接入检索"],
    ["DXF", "dxf-native-v2：ARC/LWPOLYLINE/POLYLINE/INSERT 块展开/MTEXT/DIMENSION；部分成功 + 逐项声明", "DWG/DGN 需外部转换器（明确拒绝）；无真实工厂泛化成绩"],
    ["公式", "隔离 Python 3.12 worker + ONNX 模型 + SHA256 校验 + LaTeX 结构校验", "无公式求值引擎（官方 6b 未实现）"],
    ["JSON 语料", "OHR-Bench 走同一契约路径并声明 locator_unavailable", "无页面定位"],
], widths=[2.4, 8.4, 6.0])

h2(doc, "2.3 解析契约的 Mermaid 状态图（代码块描述）")
code_block(doc, "stateDiagram-v2\n  [*] --> parsed: 全部块通过 locator 校验\n"
                "  parsed --> partial: 超上限/部分类型不支持\n  parsed --> failed: 校验失败(PARSE_LOCATOR_INVALID)\n"
                "  parsed --> skipped: 无块\n  [*] --> unsupported: 适配器缺失/媒体不支持\n"
                "  partial --> indexed: 摄取成功且 chunks>0\n  failed --> [*]: 不中断摄取，如实记录")

page_break(doc)

# ---------------- 3 NL2SQL ----------------
h1(doc, "3. NL2SQL 实现")
h2(doc, "3.1 处理链路（含每步的失败码）")
code_block(doc, "① query_frame：实体/指标/时间/数据库范围 → 缺项则 NEEDS_CLARIFICATION\n"
                "② Catalog.retrieve(question, limit=8)：词法 + 中文别名 + 枚举 + Join Graph\n"
                "③ 模型生成 SQL（提示词含 schema 快照与注册表口径）\n"
                "④ sql_guard：sqlglot AST 解析 → 按 scope 解析列权限（RESTRICTED_COLUMN / SCHEMA_NOT_ALLOWED）\n"
                "             → 指标口径结构比对（顶层投影必须是批准指标；禁止 OR；时间谓词必须在主 WHERE）\n"
                "             → 参数绑定（文本序哨兵，保证占位符↔值映射正确）\n"
                "⑤ 只读执行（DATAMIND 只读角色；无写入路径）\n"
                "⑥ 失败分类 → 最多一次受控修复 → 返回 sql/rows/evidence")
h2(doc, "3.2 关键防线（来自独立复查后的修复）")
table(doc, ["缺陷类", "曾出现的反例", "当前处置", "证据"], [
    ["CTE 同名绕过", "public.customers 与某 CTE 同名 → 当作 CTE 跳过列检查", "按作用域来源对象判定 is_cte 且无 schema 限定", "PROGRESS.md 第 1 批 R01"],
    ["指标篡改", "正确 SUM 再乘 0 / 加常量 / 挪进无关子查询 / WHERE 追加 OR TRUE", "顶层投影必须是批准指标，仅允许 Alias/ROUND/COALESCE/ABS/CAST 包装；禁 OR", "PROGRESS.md 第 1 批 R02"],
    ["连接粒度", "多对多 JOIN 导致指标口径放大", "按 catalog 外键方向判 METRIC_JOIN_GRAIN_RISK", "同上"],
    ["参数错配", "sqlglot find_all(Placeholder) 顺序 ≠ 文本顺序", "改为文本序哨兵映射", "同上"],
    ["派生表越权", "不可验证的派生表携带受限列", "sql_guard 按 scope 拒绝", "docs/AUDIT_FIXES.md F01"],
], widths=[2.6, 5.4, 5.6, 3.2])
h2(doc, "3.3 评测结果（分子/分母，均为自建集）")
table(doc, ["套件", "通过/总数", "其他数字", "来源"], [
    ["nl2sql（零售，14 条）", "14/14", "cases=14", "reports/nl2sql.json"],
    ["robustness（4 条）", "4/4", "错别字/口语/越权/空结果各 1", "reports/robustness.json"],
    ["schema linking（18 条）", "表 31/31、列 28/28、指标 4/4、路径 6/6", "Top-5 微平均召回", "reports/schema_linking.json"],
    ["工业问数", "8/8 开发题", "模型前澄清 2/2", "PROGRESS.md「工业模型问数」"],
    ["工业跨源规则（合成）", "13/13", "model_used=false", "reports/industrial_rules.json"],
], widths=[4.0, 4.4, 4.4, 4.0])
para(doc, "边界：以上全部为自建/合成集，非官方测试集；E5/F4 的官方准确率档「无法判定」。"
          "题面一致性与 append-only 护栏见 reports/cards/wp1a_registry.md。", size=9)

page_break(doc)

# ---------------- 4 RAG ----------------
h1(doc, "4. RAG 实现")
h2(doc, "4.1 检索与生成")
code_block(doc, "检索：BGE 向量（Qdrant） + 词法检索 → RRF 融合 → 候选片段（受任务预算约束）\n"
                "生成：只允许使用本次候选证据；提示词含防注入边界\n"
                "校验：①原文连续片段必须逐字出现 ②答案中的数字必须出自引用片段或标题\n"
                "      ③未知 chunk 拒绝 ④最多一次修复 ⑤生成后重查证据身份（变化→409）\n"
                "拒答：不满足即 insufficient，不补写、不猜测")
h2(doc, "4.2 显式多文档问答的约束")
bullets(doc, [
    "范围必须显式给出 1–8 份无重复公开文件（scope.document_refs），版本/源/解析器精确绑定。",
    "每份文件必须有相关片段，否则阻断；检索覆盖与实际引用覆盖在生成结束后重验。",
    "0 切片文档：前端不出勾选框；服务端在选中时拒绝 DOCUMENT_SELECTION_NOT_SEARCHABLE；"
    "证据漂移（切片哈希失效）则如实报告证据不完整（MULTIFILE_EVIDENCE_INCOMPLETE）——两者是不同语义，刻意不合并。",
    "线上实测：/v1/documents 27 项中 26 可检索、1 不可检索（doc-industrial-0ac6c55838c3，no_chunks）。",
], )
h2(doc, "4.3 结果与缺口")
table(doc, ["指标", "值", "来源"], [
    ["通过 / 用例数", "10/12", "reports/rag.json"],
    ["可答题目数", "answerable_count=11", "同上"],
    ["recall@5 / MRR@10", "1.0 / 0.9545454545454546", "同上"],
    ["未通过原因（2 例）", "1 例上游错误；1 例缺表头/公式拒答", "审计 §4、docs/OFFICIAL_REQUIREMENTS_MAPPING.md A3"],
    ["忠实度三态标注", "未做（E6 无法判定）", "docs/SCORE_GAP_LEDGER.md E6"],
], widths=[4.0, 6.4, 6.4])
h2(doc, "4.4 私有 PDF 链路（另一条独立链路）")
bullets(doc, [
    "私有快照登记：admin/CSRF/同源、当前头 CAS、固定版本原文件/页图/bundle；服务 27/27、HTTP 19/19（PROGRESS.md）。",
    "私有知识发布：冻结向量计划、独立认证 Qdrant、实际点确认、token/lease 栅栏；全 worker 最多 2 个 running。",
    "私有多文件检索：固定 publication/计划 SHA、1–4 份、同 embedding profile、逐文件覆盖；两个跨进程检索槽、总候选 512。",
    "私有模型回答：只用本次选定片段、连续片段与数字校验、最多一次修复、文件未覆盖即拒答。",
    "边界：不接公开知识、不写向量；真实模型忠实度与准确率未评测。",
])

page_break(doc)

# ---------------- 5 多源融合 ----------------
h1(doc, "5. 多源融合与证据绑定")
h2(doc, "5.1 已实现：工业「原文 + 生产 SQL」逐测量核验")
code_block(doc, "工艺/质检原文 ──► 阈值与适用范围（单位、版本、条款页）\n"
                "                        │\n"
                "生产 SQL（只读）──► 逐测量值 ──► 绑定（对象/版本/单位/时间口径）\n"
                "                        │\n"
                "        ┌───────────────┼───────────────┐\n"
                "     超限判定        冲突判定        需人工复核\n"
                "   (out_of_range)  (保留双方原值)   (needs_review)\n"
                "        未登记权威 → 不选来源，CONFLICT_UNRESOLVED 语义")
table(doc, ["项", "值", "来源"], [
    ["规则冻结集通过", "13/13", "reports/industrial_rules.json"],
    ["冲突 / 超限 / 待复核", "conflicts=[\"M001-1\"]；out_of_range=[\"M001-1\",\"M006-2\"]；needs_review 5 项", "同上 runs[0].summary"],
    ["是否调用模型", "model_used=false", "同上 runs[0].versions"],
    ["保留的失败运行", "run-49ac9fc9f84949f6（首轮序列化缺陷，保留不删）", "同上 initial_implementation_failure_run"],
], widths=[4.0, 7.4, 5.4])
h2(doc, "5.2 证据图存储现状")
bullets(doc, [
    "已有 evidence_nodes=1456 / evidence_edges=1359（只读 SQL），DDL 位于 deploy/init/02_metadata.sql:252-269。",
    "现有边类型为 derived_from/retrieved_from/maps_to/supports/contradicts/verified_by/repaired_from；"
    "对照检查（entity_match/version_scope/unit_equivalent/time_overlap/conflicts）尚未建立。",
    "绑定列 source_sha256/version/unit/time_scope 未建立；因此不能声称「版本化证据绑定已经落地」。",
    "计划中的 019 迁移真实工作是「加绑定列 + 边类型 CHECK」，不是从零建表（docs/SCORE_GAP_LEDGER.md §3.3 P6/P7）。",
])
h2(doc, "5.3 通用跨源为何仍是 fail-closed")
para(doc, "route_for() 只按关键词命中 cross_source，命中后直接返回 ROUTE_UNSUPPORTED。"
          "这是刻意的：在没有实体/版本/单位绑定与裁决门之前，猜测作答比拒答更危险。"
          "因此 F1 的「≥3 种结构不同的跨源问题」未达成，只能报已有工业链路的 13/13。", size=9.5)

page_break(doc)

# ---------------- 6 可解释性 ----------------
h1(doc, "6. 可解释性与可追溯")
h2(doc, "6.1 证据出口清单")
table(doc, ["出口", "内容", "代码"], [
    ["答案载荷", "sql、绑定参数、rows、citations（含 locators/relations）", "datamind/agent.py、api.py"],
    ["GET /v1/runs/{id}", "运行摘要与状态", "datamind/api.py"],
    ["GET /v1/runs/{id}/evidence", "证据集合（数据行 + 文档块 + 定位）", "datamind/api.py"],
    ["GET /v1/runs/{id}/events", "事件流（可回放）", "datamind/api.py"],
    ["前端引用卡片", "定位徽标/区域高亮；旋转页与非页面定位只显示标签", "web/app.js"],
    ["契约关系", "read_relations 两向命中；只附存储中确有的", "datamind/parse_contract.py"],
], widths=[4.4, 8.0, 4.4])
h2(doc, "6.2 可视化纪律（防「猜测绘制」）")
bullets(doc, [
    "仅对未旋转的 pdf_region 提供区域高亮；rotation_degrees ≠ 0 时只显示标签。",
    "非页面定位（office_object/cad_entity/sheet_range）不走区域绘制分支。",
    "不从页码推导区域；查询定位失败不阻断回答，错误码独立记录。",
    "有前端契约守护测试（node --check + 字段一致性 + 旋转拒绝分支存在性）。",
    "边界：关系不参与排序/打分/重排/冲突消解；本轮未做界面视觉验收。",
])
h2(doc, "6.3 Mermaid（代码块描述）：一次跨源问答的时序")
code_block(doc, "sequenceDiagram\n  participant U as 用户\n  participant A as agent\n"
                "  participant S as sql_engine\n  participant R as rag_engine\n  participant E as 证据图\n"
                "  U->>A: 跨源问题\n  A->>S: 结构化子问题\n  S-->>A: rows + sql\n"
                "  A->>R: 文档子问题（绑定实体/版本）\n  R-->>A: 片段 + 定位\n"
                "  A->>E: 写入 nodes/edges\n  E-->>A: 冲突？\n"
                "  alt 冲突且无权威登记\n    A-->>U: 双方证据 + 待裁决\n  else 可判定\n    A-->>U: 结论 + 完整证据链\n  end")

page_break(doc)

# ---------------- 7 中级任务 ----------------
h1(doc, "7. 中级任务实现细节")
h2(doc, "7.1 B3 语义校验与澄清")
bullets(doc, [
    "clarify() 为规则式缺项检测（时间范围、口径、对象），产出 clarification 字段并按 NEEDS_CLARIFICATION 路由。",
    "已实现的证据：工业套件「模型前澄清」2/2（reports/industrial_nl2sql.json）。",
    "未达成：无完整「提出缺项 → 用户补充 → 消歧补槽 → 完成查询」闭环评测；无「不必要澄清率」指标；"
    "前端补槽交互未接入（docs/SCORE_GAP_LEDGER.md F2）。",
])
h2(doc, "7.2 B4 Schema Linking")
code_block(doc, "Catalog.retrieve(question, limit=8)\n"
                "  ├── 词法匹配（问题词 ↔ 表/列中文名与别名）\n"
                "  ├── 别名映射（entity_aliases WHERE approved=TRUE，如 VIP→钻石）\n"
                "  ├── 枚举值命中（列枚举与问题中的字面值）\n"
                "  └── Join Graph 路径（Catalog.join_path，外键方向）\n"
                "评测（reports/schema_linking.json）：表 31/31、语义锚点列 28/28、指标 4/4、路径 6/6")
bullets(doc, [
    "规模：18 条自建题、2 个 schema 快照（demo_retail、chinook），Top-5 微平均召回。",
    "未达成：表数量未扩到 ≥40、未加入近义/命名相似干扰表、无未见问题集、错误路径拒绝率未单列。",
])
h2(doc, "7.3 B2 可扩展要素提取（机制就位，成本未测）")
bullets(doc, [
    "领域知识存于数据库注册表：schema_snapshots/_tables/_columns（中文名/别名/枚举/语义角色）、entity_aliases、business_metrics。",
    "db/build_catalog.py 由 introspection 生成，datamind/catalog.py 读取渲染 → 引擎不需要领域提示词。",
    "未达成：未做「新增一个未参与开发的领域」的成本实验（配置行数、标注样本数、代码改动行数、旧领域回归影响），"
    "因此不主张「低成本扩展已被证明」。",
])
h2(doc, "7.4 B5 / B6 / B7 / B8 / B9 现状")
table(doc, ["难题", "已实现部分", "明确缺口"], [
    ["B5 多表 JOIN/聚合/嵌套", "受控 JOIN + 语义门禁 + 粒度风险判定", "样本小、无公开基准；嵌套与合法派生表覆盖不足"],
    ["B6 公式识别与计算", "ONNX 识别 + LaTeX 结构校验 + 块级 formula_latex 进引用", "无求值引擎；6b 计算结果无实现"],
    ["B7 混乱目录恢复", "标题树优先切片、heading_level/reading_order 字段", "无缺标题/错编号/伪标题/跨页层级专项集"],
    ["B8 文档复杂度评估", "—（映射表判「未实现」）", "无复杂度评分模型或规则；仅有手工 difficulty 标签"],
    ["B9 质量检测与告警", "parse_status 五态、quality_findings、OCR 置信度、旋转校验", "无清晰度评分、繁简/错别字校正、统一告警报告"],
], widths=[3.4, 7.0, 6.4])

page_break(doc)

# ---------------- 8 评测与质量保证 ----------------
h1(doc, "8. 测试与评估体系")
h2(doc, "8.1 三层验证")
table(doc, ["层", "规模与口径", "入口"], [
    ["完整隔离回归", "1297/1297，失败/错误 0，源库未修改（metadata_isolated=true）", "scripts/test_isolated.py；reports/latest_regression.json"],
    ["专项验证报告", "按工作包分文件（契约/预算/PDF/OCR/消融/CAD 等）", "reports/*_verification.json"],
    ["冻结验收集", "CAD 能力 31 例 365 断言；工业规则 13 例；schema 18 例", "data/evaluation/、reports/cad_capability_v1.json"],
], widths=[3.4, 8.4, 6.0])
h2(doc, "8.2 隔离回归的机制")
bullets(doc, [
    "克隆元数据库后执行；不复制模型设置、账户、会话、登录限流、权威规则、CAD 资产、私有 PDF 行。",
    "报告 JSON 记录 metadata_isolated、source_database_modified 等布尔断言，便于机器核对。",
    "纪律：新增测试会改变分母，每次同步 PROGRESS.md 的当前状态表（本轮未新增测试文件，分母未变）。",
])
h2(doc, "8.3 评测登记（append-only）")
table(doc, ["suite", "登记文件", "题数", "digest（原文摘录，前 16 hex）"], [
    ["nl2sql", "data/evaluation/registry/nl2sql_v1.json", "14", "a73bf740e4ad4eb9"],
    ["rag", "data/evaluation/registry/rag_v1.json", "12", "f5d42555a5831048"],
    ["robustness", "data/evaluation/registry/robustness_v1.json", "4", "ec105871014d2931"],
], widths=[2.4, 7.6, 1.6, 5.4])
para(doc, "来源：reports/cards/wp1a_registry.md。check 实测 3/3 一致（exit 0）；重复冻结拒绝（exit 2）；"
          "篡改副本被检出（exit 1）；统一 schema 对三份历史报告报 0/3 符合（exit 1，如实结果）。", size=9)
h2(doc, "8.4 效率测量的口径（限制条件必须随数字一起引用）")
bullets(doc, [
    "机器：macOS-27.0.1-arm64，Apple M5 Pro，15 核，Python 3.14.5，内存 25,769,803,776 字节；每规模 3 次。",
    "报告自带 4 条免责：单机单进程；无 CPU 绑定或 cgroup 隔离；无并发负载；非生产吞吐或容量承诺。",
    "CAD 5000/20000 实体档 parse_status=partial（blocks 上限 2000）→ 受限输出路径测量。",
    "文档选择 1/2/5/8 份实际读取 9/14/34/44 行、耗时 102.9–107.3 ms，不可外推为「整套 RAG 只需 100 ms」。",
])

page_break(doc)

# ---------------- 9 安全与治理 ----------------
h1(doc, "9. 安全边界与工程治理")
table(doc, ["议题", "实现", "证据"], [
    ["跨源密钥不继承", "跨源路径不带已存密钥；本机 sink 阳性对照实测 0 次外送", "PROGRESS.md 0.38.0"],
    ["网络暴露", "仅绑定 127.0.0.1；免登录只读 socket 校验对端；同源固定白名单；中间件默认拒绝", "docs/AUDIT_FIXES.md F03/F04"],
    ["只读执行", "SQL 侧无写入路径；评测登记连接 set_session(readonly=True)", "datamind/sql_guard.py、data/evaluation/registry/registry.py"],
    ["任务预算", "跨进程资源槽、挂钟 deadline、进程组终止（含孙进程）、输出上限与稳定超时码", "datamind/task_budget.py（34 项测试）"],
    ["可见性统一判定", "searchability(status,needs_review,chunks) 唯一入口，四个端点复用", "PROGRESS.md 第 2 批 2a"],
    ["依赖锁", "requirements.lock 65 个固定版本 + 守护测试校验锁与实际安装一致", "tests/test_dependency_lock.py"],
    ["未知项不猜", "未登记权威不选来源；未识别跨源问法返回稳定码；无切片的文档不可问答", "industrial_rules.py、agent.py、upload_pipeline.searchability"],
], widths=[3.0, 8.4, 6.4])
h2(doc, "9.1 已知未收敛项（原文照录，不修饰）")
bullets(doc, [
    "F02 口径强制面仍窄（仅销售额别名族），复合指标按「无法独立校验」记录而不阻断。",
    "profile 解析仍有两套实现（行为已一致，实现未合并）。",
    "F10 摄取跨存储一致性仍有孤儿向量窗口（发布顺序协议已修，窗口未彻底关闭）。",
    "CPU/内存限额：RLIMIT_CPU 实测生效；RLIMIT_AS 在本机 macOS 被拒 → 内存上限失败关闭而非假装生效。",
    "租约未接入执行路径；非 cgroup 硬隔离、非分布式共识。",
])
h2(doc, "9.2 仓库与交付治理")
bullets(doc, [
    "git ls-files=605 个跟踪文件；db/migrations/*.sql=18；tests/ 目录 84 项（test_*.py 78 项，含 1 个 pending 目录项）。",
    "交付物生成脚本独立放在 交付/_build/，不修改任何既有代码或文档。",
    "交付清单（含 sha256 与生成命令）见 交付/DELIVERY_MANIFEST.json；官方提交物逐项状态见 docs/DELIVERY_CHECKLIST.md。",
])

page_break(doc)

# ---------------- 10 局限与路线 ----------------
h1(doc, "10. 已知局限与后续路线")
h2(doc, "10.1 局限（按官方评分项影响排序）")
numbered(doc, [
    "通用跨源融合未建（F1）：当前仅工业结构跨源 13/13；无 ≥3 种结构差异的冻结集与绑定列。",
    "中级任务广度（F8/E8）：B3/B4 为「已实现（限定）」，B1/B2/B5/B6/B7/B9 为「部分」，B8 未实现。",
    "效率证明（E7/F6）：有 4+4 规模点实测，缺复杂度分析、并发吞吐与优化前后对照。",
    "多轮质量（E10）：契约与边界已实现并有 33 项测试，缺 ≥6 轮连续对话的准确率证据与前端接入。",
    "忠实度（E6）：只有组件级校验与端到端正确率，无逐结论三态标注。",
    "交付形态：决赛 ≤50 页深化稿与 ZIP 未打包（本轮明确不打 ZIP，只出清单与 dry-run 脚本）。",
])
h2(doc, "10.2 后续路线（与提升计划 WP 对应）")
table(doc, ["优先级", "工作", "验收判据"], [
    ["P0", "跨源证据绑定 + 三种结构冻结集（WP2）", "P1/P2/P3 ≥80%、错误绑定率 ≤5%、冲突 100% 展示双方"],
    ["P0", "同类绑定的对照消融（WP3）", "三臂同集同环境；full 臂错误绑定率相对 plain 下降 ≥50% 且绝对 ≤5%"],
    ["P1", "交互式澄清闭环 + 不必要澄清率（WP4）", "不必要澄清率 ≤10%；一次补充后完成查询"],
    ["P1", "效率证明补齐（WP8）", "≥3 档 × 并发矩阵 + 复杂度 + 一处优化前后对照"],
    ["P2", "上传→索引→问答闭环（WP9）", "≥10 份 ≥4 种格式；缺索引仍被拒绝"],
], widths=[1.8, 7.6, 8.4])
para(doc, "以上均为「完成并通过验收」的条件目标，不是已获分数，也不是官方成绩。", size=9, space_after=2)
h2(doc, "10.3 本说明书的诚实边界")
bullets(doc, [
    "本说明书未在干净空库环境重跑启动演练（本轮禁止重建容器/库），不主张「新环境已验证可启动」。",
    "文中所有准确率/召回数字来自自建或合成集，官方测试集未提供；官方分档结论一律「无法判定」。",
    "V-CAR 消融为 16 例合成机制替身，不调用任何模型，不衡量 OCR/问答准确率，不能作为领先性证据。",
    "本说明书不含任何单位名、合作方名或人名。",
])

doc.save(str(OUT))
print("WROTE", OUT)
