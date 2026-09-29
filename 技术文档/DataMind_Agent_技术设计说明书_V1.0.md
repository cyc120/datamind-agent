# DataMind Agent 技术设计说明书
## 面向赛题八：多模态数据驱动的可解释精准问数/问答智能体

版本：V1.0  
日期：2026年9月28日  
用途：竞赛方案设计、系统研发、测试验收与答辩演示

> **方案摘要**：本方案以“四大核心模块 + 两个基础设施层”组织系统，面向关系型数据库、PDF/Office 文档、扫描件、表格、公式和图片等异构数据，提供精准问数、知识问答、跨源多跳推理、证据校验与执行过程可视化。高级创新聚焦 **Verifiable Cross-source Agentic Reasoning（V-CAR，可验证跨源智能体推理）**，把任务规划、工具执行、答案校验与受控修复形成可审计闭环。

## 文档使用说明

本说明书把赛题能力要求映射为可实现的软件设计和可验收指标。赛题边界与阶段性分值按团队在前序阅读中整理的赛题八 PDF 要点归纳：初赛建立不少于 10 例、覆盖 NL2SQL 与 RAG 的评测集；初赛至少突破 1 项中级任务，决赛以至少 3 项中级任务为重点，5 项及以上对应中级任务覆盖广度满档；决赛复杂 NL2SQL 与多文档/多模态 RAG 以 90% 作为满分目标线；高级任务创新加分上限 20 分，总分口径最高 120 分。正式提交前，应将本说明书的任务编号、阈值及材料清单与赛事组委会发布的最新版原始评分表逐项核对。

本方案将“基础任务”定义为系统必须具备的端到端能力，将“中级任务”对应为赛题列出的专项难点，将“高级创新”定义为在基础能力上形成可量化、可验证、可演示的技术突破。文中所有项目验收数字均为研发目标或建议基线，不代表已取得的实验结果；没有实测前不得写成系统成绩。

## 目录

1. 项目定位与赛题映射  
2. 目标、范围与设计原则  
3. 总体架构与运行主链路  
4. 模块一：结构化数据与精准问数引擎  
5. 模块二：RAG 与多模态知识库  
6. 模块三：多 Agent 编排与跨源推理  
7. 模块四：全链路可解释与可视化  
8. Metadata / Knowledge Infrastructure  
9. Evaluation System  
10. 接口契约与关键数据结构  
11. Demo 设计与验收脚本  
12. 技术选型与替代方案  
13. 前后端与部署方案  
14. 仓库目录建议  
15. 六周研发路线图（每 3 天一个里程碑）  
16. 风险、治理与答辩证据  
17. 参考资料

# 1. 项目定位与赛题映射

## 1.1 项目定义

**项目名称：DataMind Agent**  
**项目定位：多模态数据驱动的可解释精准问数/问答智能体。**

系统统一连接结构化业务库与非结构化知识库，针对自然语言问题识别指标、实体、时间和约束，自动规划数据源与工具调用；经 SQL 查询、文档检索或二者联合后，形成带出处的答案，并用执行轨迹和证据关系说明答案如何产生。

### 关键能力

- 用业务术语、字段别名和表关系图完成 Schema Linking，生成可执行且只读的 SQL。
- 对 PDF、扫描件、表格、图片及含公式文档实行质量感知解析、结构恢复、混合检索与来源定位。
- 对单源、跨源和多跳问题形成显式任务 DAG，受限调用 SQL、RAG、公式计算等工具。
- 对关键数值、文档引用、跨源实体映射和逻辑一致性执行规则化验证。
- 在失败时按错误类别重规划、修复并重试；超过预算则澄清或说明无法验证。
- 展示真实工具调用、SQL、检索证据、校验结果、耗时与重试记录；不展示模型私有逐字思维链。

## 1.2 基础任务、中级任务、高级创新

| 级别 | 竞赛目标 | 系统实现范围 | 验收证据 |
|---|---|---|---|
| 基础任务 | 有可运行、可复现的问数与知识问答闭环 | 数据接入；单表/多表基础 NL2SQL；常见 PDF 解析和 RAG；单源路由；答案与 SQL/文档出处绑定；基础错误提示；可运行 Demo | 初赛至少 10 例、覆盖 NL2SQL 与 RAG 的基线集；启动说明；成功/失败用例；来源可点击 |
| 中级任务 A（前序摘要建议项 3）：语义校验与主动澄清 | 识别缺失、矛盾或含糊条件，避免伪确定回答 | 时间范围归一化；指标/实体消歧；澄清阈值；问题重写及确认状态 | 澄清必要性准确率、澄清后执行成功率、错误回答率 |
| 中级任务 B（前序摘要建议项 4）：Schema Linking | 让自然语言业务词对应正确表、字段、枚举值及关系 | 元数据检索、别名/样例值索引、Schema Graph、候选重排、Join Path 规划 | Top-k 表/列命中率、关系路径正确率、误链接率 |
| 中级任务 C（前序摘要建议项 5）：复杂多表 NL2SQL | 处理多表 JOIN、过滤、聚合、排序、分组与子查询 | SQL 计划、方言提示、AST 静态检查、只读执行、结果校验和有界修复 | Exact Match、Execution Accuracy、聚合/连接子集准确率；决赛目标线 90% |
| 中级任务 D（前序摘要建议项 7）：非标准文档结构恢复 | 恢复不规则目录、跨页表格、错序版面和页码定位 | 页面布局解析、标题树恢复、表格续页合并、版面块关系、原页截图定位 | 标题层级准确率、表格结构准确率、引用页码准确率 |
| 中级任务 E（前序摘要建议项 9）：文档质量与复杂度评估 | 发现低清、倾斜、模糊、缺页、OCR 低置信与公式风险 | 可解释质量分、路由阈值、降级路径、人工复核队列 | 质量分与人工分级相关性、低质量文档召回率、错误解析率 |
| 高级创新：V-CAR | 展示可复核的 Plan → Execute → Verify → Repair 跨源闭环 | 显式任务图、证据图、逐项验证、根因分类、受控修复、版本化 trace | 跨源多跳准确率、校验检出率、修复成功率、证据覆盖率、端到端耗时 |

赛题明确强调的其他中级挑战（如公式识别与计算、多模态信息提取）作为 A/B 类能力的扩展评测；团队按数据可获得性择优纳入。基线阶段优先完成 A、B、C、D、E 五项，形成“数据库问数 + 文档理解 + 质量评估 + 主动澄清”的覆盖面；高级创新只围绕 V-CAR 深挖，不另起一条与主系统脱节的算法线。

## 1.3 评分约束到工程证据的映射

| 评分关注点 | 工程设计对应项 | 可提交证据 |
|---|---|---|
| NL2SQL 准确率 | Schema Linking、Join Graph、SQL AST 校验、执行反馈修复 | 固定测试集、SQL/结果对照、错误分类和版本差异 |
| RAG 忠实度与多模态能力 | 结构解析、Hybrid Search、重排、引用校验 | 原文页图、解析 JSON、Recall@K、引用支持率、困难样例 |
| 跨源融合与多跳 | Planner DAG、实体映射、子任务依赖和 Evidence Graph | SQL 结果到文档检索的实体传递轨迹；反向路径测试 |
| 鲁棒性与效率 | 降级、缓存、索引、并发、超时预算 | 多规模压测、p50/p95、吞吐、缓存命中、失败恢复报告 |
| 可解释与产品完成度 | Trace API、流式事件、前端执行图和证据详情 | 现场可重放案例、导出 trace、来源定位及可读错误 |
| 高级创新 | V-CAR 闭环和可量化修复实验 | 消融对照、触发原因、重试前后结果、失败终止说明 |

# 2. 目标、范围与设计原则

## 2.1 MVP 与比赛增强范围

**MVP 必须完成**：一个可重置的演示数据库；至少一种扫描/原生 PDF 流程；问数、问答、数据库+文档联合问题三类路径；只读 SQL 安全执行；文档引用定位；基础 Trace 页面；可复现的最小评测。

**比赛增强**：Schema Graph、模糊问题澄清、Adaptive RAG、多表 SQL 修复、表格/公式专门解析、证据一致性校验、性能优化、多规模压测和 V-CAR 消融实验。

**明确不作为首版目标**：训练基础大模型；覆盖任意数据库方言；无人工审查的自动知识写回；展示隐藏思维链；以生成式评分器替代确定性评测；把所有 Agent 拆成独立微服务。

## 2.2 设计原则

1. **可验证优先**：每个事实断言至少映射到 SQL 行/聚合结果、文档片段、计算过程之一；证据不足就降低置信或拒答。
2. **确定性工具优先**：关系连接、数值计算、时间边界、权限和格式校验写成可测规则；LLM 做语义解释与候选生成。
3. **能力解耦、流程统一**：SQL 与 RAG 是工具能力；Orchestrator 管任务生命周期；Evidence/Trace 是统一产物。
4. **所有恢复都有上限**：修复最多 2 次；整个运行有时间、token、SQL 行数和检索上下文预算。
5. **只读与最小权限**：问数连接只授予只读账号；上传文档按租户隔离；日志中脱敏敏感内容。
6. **可重复评估**：模型、提示词、元数据、索引、语料版本和测试集版本共同写入 run manifest。

# 3. 总体架构与运行主链路

## 3.1 逻辑架构

| 交互层 | 核心能力层 | 基础设施层 | 运行支撑 |
|---|---|---|---|
| Vue 3 问答、对话历史、Trace 图、SQL/证据面板、知识库管理 | ① 精准问数；② Adaptive RAG；③ Agent Orchestrator / Fusion / Verifier；④ Trace / Evidence 可视化 | Metadata Registry、Schema Graph、对象存储、PostgreSQL、Qdrant、任务队列 | Evaluation System、OpenTelemetry、Prometheus、结构化日志、配置/密钥管理 |

## 3.2 主链路

```text
用户问题
  → Query Understanding（实体、指标、时间、范围、歧义）
  → Planner 产出受 JSON Schema 约束的 Task DAG
  → Router 选择 SQL / RAG / SQL→RAG / RAG→SQL / 公式计算
  → SQL Agent 和/或 RAG Agent 执行有界工具调用
  → Fusion Agent 合并结果并建立 Evidence Graph
  → Verifier 检查数值、引用、映射、逻辑和权限
  → PASS：生成带来源的答案；FAIL：修复/重规划，最多 2 次
  → Trace 事件实时推送，运行和证据持久化
```

## 3.3 示例路径

问题：“2025 年销售额最高的产品是什么？结合市场分析报告说明原因。”

1. Query Understanding 抽取 `metric=sales_amount`、`period=2025`、`rank=1`、`need_reason=true`。
2. Planner 建立 SQL Top-1 子任务；后继 RAG 子任务以 SQL 返回的产品规范 ID 为输入；Fusion 与 Verify 为终端任务。
3. SQL Agent 从 Schema Graph 检索订单、明细、产品和时间字段，生成只读查询并返回 Top-1 产品及金额。
4. RAG Agent 按规范产品 ID、别名和报告元数据混合检索，返回支持“原因”解释的片段与页码。
5. Verifier 确认名称/金额来自 SQL、原因句子被证据支持、文档未过期且没有冲突。
6. 前端按事件顺序呈现任务图、SQL、表格结果、原文证据卡片、验证项及最终答案。

# 4. 模块一：结构化数据与精准问数引擎

## 4.1 子组件与数据流

| 子组件 | 输入 | 处理 | 输出 |
|---|---|---|---|
| Connector / Introspector | 只读连接配置、数据库 | 读取表、列、类型、主外键、注释、样例值；按 allowlist 限定 schema | `SchemaSnapshot` |
| Semantic Metadata Service | `SchemaSnapshot`、业务词表 | 维护中文名、别名、单位、口径、枚举映射、敏感级别、有效时间 | `SemanticCatalog` |
| Schema Retriever | 问题解析结果、元数据 | 词法召回 + 向量召回 + 业务约束重排 | Top-k 表/列/关系候选及分数 |
| Schema Graph / Join Planner | 候选表、主外键图 | 受限最短路径/代价路径；排除禁用关系和多义连接 | `JoinPlan` |
| SQL Planner / Generator | 问题语义帧、候选 Schema、JoinPlan、数据库方言 | 先生成结构化查询计划，再生成 SQL；关键字段绑定元数据 ID | `SQLDraft` |
| SQL Guard / Validator | SQL AST、allowlist、执行策略 | 只允许 SELECT/CTE；检查表列权限、笛卡尔积、limit、聚合和时间范围 | 校验通过/阻断及原因码 |
| Read-only Executor | 通过验证 SQL、参数、预算 | 只读事务、statement timeout、行数限制、取消信号 | 列定义、结果行、耗时、行数 |
| Result Validator / Repair | SQL 错误或结果摘要 | 错误分类；基于 Schema 与数据库反馈局部修复；最多 2 次 | 有证据的结果或可解释失败 |

## 4.2 元数据设计与 Schema Linking

单个字段记录：`source_id`、`schema_name`、`table_name`、`column_name`、`data_type`、`comment`、`business_name`、`aliases[]`、`sample_values[]`、`unit`、`semantic_role`、`sensitivity`、`is_nullable`、`primary_key`、`foreign_keys[]`、`freshness`、`catalog_version`。

候选分数建议：

`Score = 0.30·lexical + 0.25·dense + 0.20·alias + 0.15·value_match + 0.10·graph_compatibility`

该权重仅为可调初值；以验证集的 Top-k 命中和误链接率网格搜索，不把相似度直接解释成概率。字段召回后必须结合表关系、指标口径和查询语境做二次排序。对“销售额”同时命中下单金额、退款金额、含税金额的场景，必须依据指标定义或向用户澄清。

Join Graph 节点是表，边是经过审核的外键/业务关系，边属性包括连接字段、基数、方向、有效状态、语义说明、更新时间。路径代价可定义为：`cost(edge)=1 + ambiguity_penalty + fanout_penalty + privacy_penalty`。仅允许 Catalog 中已批准的路径，不允许模型凭字段名自行拼接未经登记的 JOIN。

## 4.3 NL2SQL 算法步骤

1. 解析问题：意图、时间、指标、维度、实体、排序/排名、比较、输出粒度、限定条件。
2. 时间标准化：结合显式时区与赛事数据日期解析“去年/本季度”；未提供时间时不自行假设。
3. Schema 召回：表、列、枚举值与指标定义分别召回并显示候选理由。
4. 语义消歧：候选口径存在实质差异时提问；否则按业务 Catalog 中唯一活跃口径处理。
5. 查询计划：`select[] / metrics[] / filters[] / group_by[] / joins[] / order_by[] / limit / expected_grain`。
6. 生成 SQL：指定方言；常量采用参数绑定；标注每个 projection/filter 对应的业务语义 ID。
7. AST 与执行前检查：用 SQLGlot 解析 AST，拒绝 DDL/DML、系统目录、未授权 schema、无上限大结果、危险函数和无关系 JOIN；用 `EXPLAIN`/超时保护作为资源门禁。
8. 只读执行与结果校验：检查空值比例、重复粒度、单位、数值范围和预期聚合列；生成数据时间/查询参数摘要。
9. 失败修复：按错误码重取 schema、修复列名/方言/聚合歧义；任何修复都重新经过 Guard，禁止“直接改字符串后重跑”。
10. 输出数字证据：结果摘要、SQL 哈希、脱敏 SQL、执行时间、行数、数据源版本、可复现参数。

## 4.4 关键结构

```json
{
  "intent": "rank_entities",
  "metrics": [{"metric_id":"sales_amount","aggregation":"sum","unit":"CNY"}],
  "dimensions": [{"field_id":"product.name"}],
  "time_filter": {"field_id":"orders.created_at","from":"2025-01-01","to":"2026-01-01","timezone":"Asia/Shanghai"},
  "filters": [],
  "sort": [{"field":"sales_amount","direction":"desc"}],
  "limit": 1,
  "ambiguities": [],
  "needs_clarification": false
}
```

`SQLExecutionResult` 至少含 `run_id`、`query_id`、`sql_hash`、`redacted_sql`、`bound_params_redacted`、`columns[]`、`rows[]`、`row_count`、`elapsed_ms`、`source_snapshot_id`、`validator_findings[]`、`status`。

## 4.5 失败处理

- 元数据缺失：不给 SQL 生成器虚构列；返回“需要补充指标/字段口径”，并在 Catalog 中创建待维护项。
- 多义指标：展示候选口径、单位和计算定义，优先发起单轮澄清；未获回复时不输出单一确定数字。
- SQL AST 不通过：阻断并记录规则命中；提示安全原因，不尝试绕过 Guard。
- 查询超时/资源超限：取消语句，尝试增加过滤、限制结果或改用摘要查询；仍失败时解释范围与预算限制。
- 空结果/异常值：标为数据为空或结果异常，不把零值当作真实值；可建议缩小时间范围或确认筛选条件。
- 修复耗尽：保留错误类别、尝试次数、候选修复差异，终止并转人工确认。

## 4.6 模块验收指标

研发目标：表/列 Schema Linking Recall@5 ≥ 0.90；Join Path 正确率 ≥ 0.90；初赛测试集 NL2SQL Execution Accuracy ≥ 0.80 并逐周提升；决赛复杂多表子集目标 ≥ 0.90；只读策略违规执行数 = 0；SQL 修复成功率按错误类别统计而非混成单一总分。所有目标须由版本化测试集实测确认。

# 5. 模块二：RAG 与多模态知识库

## 5.1 摄取与质量感知解析

输入支持原生 PDF、扫描 PDF、Office、图片和结构化 CSV/表格。每个文件生成 `DocumentAsset`，记录 SHA-256、来源、租户、文件类型、页数、语言、上传时间、访问策略和版本。管线先做质量体检，再选择解析器：

- 原生文本层完整且阅读顺序稳定：PyMuPDF / Docling 快速解析。
- 扫描、倾斜或低清：图像纠偏、去噪、OCR；低置信页面转复核队列。
- 多栏、复杂版面、目录错序：布局检测与阅读顺序恢复，保留块坐标和页面截图。
- 表格密集/跨页表格：表格结构识别、单元格坐标、表头继承、跨页续表检测，保留原始二维结构和 Markdown 视图。
- 公式密集：保留原始页面图、公式区域、LaTeX/文本候选、识别置信；数学题进入确定性计算工具，不仅依赖自然语言生成结果。
- 质量不足：不静默丢弃；记录低清、缺页、文本覆盖、OCR 置信分布、旋转角、公式/表格密度等诊断信息。

质量分可以做可解释的规则分：`Q=0.25·text_coverage + 0.20·ocr_confidence + 0.15·layout_order + 0.15·table_integrity + 0.10·formula_confidence + 0.10·page_completeness + 0.05·language_consistency`。阈值在人工标注样本上校准；低于门限进入 OCR/版面增强，高风险仍不通过时标记需人工复核。

## 5.2 文档结构与 Chunk 数据结构

Parser 输出 `DocumentBlock`：`block_id`、`document_id`、`page_no`、`bbox`、`block_type`（title/paragraph/table/figure/caption/formula/header/footer）、`text`、`table_cells`、`formula_latex`、`reading_order`、`parent_heading_id`、`ocr_confidence`、`parser_version`。

`Chunk` 按语义和结构生成，字段包括 `chunk_id`、`document_id`、`document_version`、`section_path[]`、`page_start/end`、`block_ids[]`、`chunk_type`、`text_for_embedding`、`exact_text`、`table_json`、`image_ref`、`source_acl`、`quality_score`、`content_hash`、`embedding_model_version`。

切片策略：标题树优先、段落边界切分；普通文字目标 400-800 tokens、重叠 60-120 tokens（初始建议，需按模型上下文与 Recall@K 调参）；表格按表/行组切片，保留表名、完整表头、单位、脚注和页码；公式与相邻文字成对保留；图片以 caption、OCR 文本、页面坐标和原图引用联合检索。禁止跨文档、跨访问域切片。

## 5.3 检索与生成

检索流程：问题改写与实体扩展 → 权限/时间/文档类型过滤 → BM25/词法召回 + Dense 向量召回 → RRF 融合 → Cross-encoder 重排 → 多样性/来源去重 → 上下文预算裁剪 → 答案句与证据块绑定。

推荐初始参数：各路候选 Top-30，RRF 后 Top-20，重排 Top-5 至 Top-8；经离线基准搜索调参。每条答案事实输出 `[E1]` 等稳定证据 ID，对应文档、页码、标题路径、引用原文、检索分数、解析置信和文档版本。相似度只用于排序，不直接作为“答案置信度”。

## 5.4 表格、图片与公式问答

- 表格定位：问题先解析行列语义、单位和统计口径；检索表块后在结构化 `table_json` 中执行精确筛选/聚合；生成答案前回指原页截图。
- 图片定位：视觉模型可生成区域摘要，但最终结论绑定图片资产 ID、页码、bbox 与 OCR/区域描述；模型生成描述需标注为解析结果，不伪装成原文。
- 公式：识别公式表达式及符号定义；把公式 AST 输入安全的符号/数值计算器；明确单位、输入值来源、舍入规则；识别低置信或变量定义缺失时要求补充信息。
- 跨文档冲突：同时展示来源日期、版本和支持/反对证据，按权威级别和有效时间排序；不将冲突压成单一事实。

## 5.5 失败处理与验收

Parser 异常或 OCR 低置信：降级到 OCR/页面截图索引；版面顺序不确定则保留块顺序候选并降低质量分；检索空结果时先扩大同权限范围或改写一次，再询问是否扩大范围；引用不存在或无法回到页码时不生成基于该块的事实断言。

验收目标：文档召回 Recall@5 ≥ 0.85；证据 MRR@10 ≥ 0.80；人工标注回答忠实度 ≥ 0.90；引用正确页码率 ≥ 0.95；质量风险文档召回率 ≥ 0.90；决赛多文档/多模态目标准确率 ≥ 0.90。指标以样本级报告呈现，另列表格、公式、扫描件子集，避免总分掩盖薄弱模态。

# 6. 模块三：多 Agent 编排与跨源推理

## 6.1 Agent 角色与职责边界

| Agent / 服务 | 允许职责 | 不允许职责 | 典型输入/输出 |
|---|---|---|---|
| Query Understanding | 意图、实体、时间、歧义抽取；提出澄清 | 直接执行 SQL 或自行编造答案 | `UserQuery → QueryFrame` |
| Planner / Orchestrator | 建立任务 DAG、预算和数据源路由 | 越过工具权限直接访问数据库 | `QueryFrame → TaskPlan` |
| SQL Agent | Schema Linking、SQL 计划生成、只读执行请求、修复建议 | 绕过 Validator；执行写操作 | `SQLTask → SQLExecutionResult` |
| RAG Agent | 检索、重排、证据抽取、引用定位 | 将无证据内容当成已验证事实 | `RAGTask → EvidenceBundle` |
| Fusion Agent | 基于 ID/规范实体合并跨源结果，组织答案主张 | 隐去来源冲突或凭空推断因果 | `Subresults → ClaimSet` |
| Verification Agent | 数值、引用、实体、时间、逻辑、安全检查 | 用不可复现的“自信分”覆盖明确错误 | `ClaimSet + Evidence → VerificationReport` |

建议首版用 Python 显式状态机/有类型 DAG 实现，降低不可见控制流；若后续采用 LangGraph 等编排库，仍须保持任务状态、节点输入输出和工具边界可导出。Agent 是具有受限职责的流程节点，不以 Agent 数量作为创新指标。

## 6.2 TaskPlan 与执行状态

```json
{
  "plan_id":"p-...",
  "goal":"找出 2025 销售额第一的产品并用报告解释原因",
  "budget":{"max_steps":12,"max_repair":2,"deadline_ms":30000,"max_rows":2000},
  "tasks":[
    {"task_id":"t1","kind":"sql.rank","depends_on":[],"input_refs":["query_frame"],"status":"pending"},
    {"task_id":"t2","kind":"rag.search","depends_on":["t1"],"input_refs":["t1.product_id"],"status":"pending"},
    {"task_id":"t3","kind":"fusion","depends_on":["t1","t2"],"status":"pending"},
    {"task_id":"t4","kind":"verify","depends_on":["t3"],"status":"pending"}
  ]
}
```

状态枚举：`pending → ready → running → succeeded | failed | blocked | skipped`。每次变更写 `TraceEvent`；进程重启后从持久化状态恢复。依赖失败时只重跑受影响子图，且必须保留原失败事件。

## 6.3 V-CAR：Plan → Execute → Verify → Repair

**Plan**：把用户目标改写为可测试的 `ClaimSet` 和任务 DAG；每个子任务指定输入引用、工具、预期结果、超时与权限。  
**Execute**：按拓扑序执行；独立 SQL/RAG 任务允许并发，带依赖的任务等待前序结构化结果。所有工具调用有 idempotency key、run_id、超时和限额。  
**Verify**：校验 SQL 数值是否来自执行结果；文档事实是否由引用支持；实体 ID 与名称是否一致；时间、单位、币种和粒度是否匹配；不同来源是否冲突；查询/检索是否越权。  
**Repair**：按失败原因选择局部动作：重新取 Schema、替换错误字段、调整检索查询、扩展证据范围、重新匹配实体、重算公式或发起澄清。修复后重新执行受影响节点并重做全部依赖的验证。  
**Stop**：通过验证则回答；修复预算耗尽、权限被拒、来源冲突无法裁决或证据覆盖不足则终止并明确说明缺口。

状态转换：`PLANNED → EXECUTING → VERIFYING → COMPLETED`；验证失败时 `VERIFYING → REPAIRING → EXECUTING`；不可恢复时进入 `NEEDS_CLARIFICATION` 或 `FAILED_WITH_EVIDENCE`。每轮修复保存 `failure_code`、`root_cause`、`repair_action`、输入差异、节点版本和结果差异，便于做修复成功率消融。

## 6.4 跨源实体对齐与证据图

实体映射优先级：主键/外部编码精确匹配 > 经审核别名表 > 规范化名称 + 上下文候选 > 模型建议后人工确认。不得仅凭名称相似度自动把两个实体合并。每条映射存 `source_entity`、`canonical_entity`、`match_method`、`score`、`approved_by`、`valid_from/to`。

Evidence Graph 节点类型：`Question`、`PlanTask`、`SQLQuery`、`SQLRow/Aggregate`、`Document`、`Chunk`、`TableCell`、`Formula`、`Claim`、`VerificationCheck`。边类型：`derived_from`、`retrieved_from`、`maps_to`、`supports`、`contradicts`、`verified_by`、`repaired_from`。每个最终 Claim 至少有一个 `supports` 边或明确的 `insufficient_evidence` 状态。

## 6.5 校验规则与置信度

规则化门禁必须先于模型评审：

- 数值 Grounding：答案数值可在 SQL 结果或公式计算记录中定位；允许范围只用于格式舍入，不可容忍量级错位。
- 引用 Grounding：关键文本 claim 至少映射到一个证据块；使用句级支持/矛盾判断，模型审校作为二级信号并抽样人工复核。
- Entity Grounding：产品/客户/地区等规范 ID 与 SQL 行、文档实体映射同一。
- Temporal Grounding：问题期间、数据时间戳、文档版本有效期一致。
- Cross-source Consistency：冲突显式标出；无裁决依据时不自动选择。
- Safety Grounding：执行身份、允许工具、SQL AST 和检索 ACL 均通过。

展示置信等级而非未经校准的小数概率：`high / medium / low / insufficient`。如比赛需要数值分数，则用标注集做 isotonic/logistic calibration，另报 ECE/Brier Score；分数解释为已验证证据覆盖信号，不声称是事实绝对概率。

## 6.6 失败恢复

模型输出不符合 JSON Schema：一次结构化重试；仍失败转安全模板答复。工具超时：熔断该子任务、最多一次幂等重试；依赖关系允许时继续执行独立子图。RAG 无证据：不得用模型内知识补缺；询问是否扩大文档范围。SQL 与文档值冲突：展示双方时间与定义，进入人工澄清。Repair 超限：返回已验证的局部结论，并清楚列出未完成部分。

# 7. 模块四：全链路可解释与可视化

## 7.1 Trace 设计

每个 run 事件统一含：`event_id`、`run_id`、`parent_event_id`、`task_id`、`agent_name`、`event_type`、`status`、`started_at`、`ended_at`、`duration_ms`、`input_summary`、`output_summary`、`tool_name`、`artifact_refs[]`、`retry_index`、`error_code`、`model_id`、`prompt_version`、`token_usage`、`visibility`。原始文档全文、凭证和隐私字段不得放到可视化事件中；SQL 参数按敏感级别脱敏。

Trace 事件类型：`query.parsed`、`plan.created`、`tool.started`、`sql.validated`、`sql.executed`、`retrieval.completed`、`evidence.attached`、`verification.failed/passed`、`repair.started/completed`、`answer.completed`。

## 7.2 前端交互

- **问答区**：流式显示回答草稿状态、澄清请求和最终答案；未验证时显示“正在核验”，不把中间生成当成最终结论。
- **执行图**：Vue Flow / Cytoscape 展示 DAG 节点、依赖边、耗时、状态、失败/重试标识；可折叠低层细节。
- **SQL 面板**：展示脱敏 SQL、数据库方言、Validator 检查清单、执行时间、行数、表格结果和导出按钮。
- **证据面板**：展示来源名、版本、页码、标题路径、引用片段、置信和打开原页/高亮定位。
- **校验面板**：逐条显示 Claim、证据、规则名称、PASS/WARN/FAIL、修复结果。
- **性能面板**：总耗时、SQL、检索、解析、LLM 各阶段耗时；数据规模、并发数、缓存命中率和当前评测版本。

展示对象是可复核的决策依据、工具调用和证据关系，不提供隐式内部思维链文本。用户应能从最终答案点击到原始 SQL 行、文档页和验证规则。

## 7.3 流式协议

`POST /v1/chat/stream` 使用 SSE；事件载荷统一为 `event: trace` / `data: {TraceEvent}`。断线后通过 `GET /v1/runs/{run_id}/events?after={cursor}` 按序恢复。客户端用 `run_id + event_id` 去重；完成事件是 `answer.completed`，失败事件包括 `run.failed` 和可读原因。大文件与页面图走文件 API 或预签名对象引用，不内嵌在 SSE。

## 7.4 可解释性验收

- 运行步骤完整率 ≥ 0.99（持久化事件数/实际工具调用数）。
- 最终事实 Claim 证据覆盖率 ≥ 0.95；无法溯源的 Claim 必须被阻止或标记。
- 前端展示的 SQL 与实际执行 SQL 哈希一致；修复前后 SQL 可比较。
- 引用页码与原 PDF 页码映射正确率 ≥ 0.95。
- 一条完整 Demo 路径可从运行 ID 重放；PII 和凭证泄漏数 = 0。

# 8. Metadata / Knowledge Infrastructure

## 8.1 统一元数据对象

| 对象 | 主键 | 关键字段 | 主要用途 |
|---|---|---|---|
| DataSource | `source_id` | 类型、连接 secret 引用、schema allowlist、权限组、环境 | 连接与安全边界 |
| SchemaSnapshot | `snapshot_id` | source_id、catalog_version、表列、关系、更新时间 | 可复现 SQL 生成 |
| BusinessMetric | `metric_id` | 定义、表达式、单位、粒度、过滤口径、负责人 | 指标语义口径 |
| EntityAlias | `alias_id` | canonical_id、alias、来源、有效期、审批状态 | Schema 和跨源实体匹配 |
| DocumentAsset | `document_id` | 文件 hash、URI、页数、ACL、质量分、parser 版本 | 文档版本和访问控制 |
| DocumentBlock / Chunk | block_id / chunk_id | 页、坐标、标题路径、文本/表格/公式、内容 hash | 引用追溯与检索 |
| EvaluationCase | `case_id` | query、标签、gold SQL/answer/evidence、slice、版本 | 离线评测与回归 |
| Run / TraceEvent | run_id / event_id | plan、工具事件、时间、状态、版本 | 审计、演示、诊断 |
| Evidence / Claim | evidence_id / claim_id | 原始出处、支持关系、校验状态 | 端到端可解释性 |

## 8.2 存储建议

- PostgreSQL 保存 Catalog、权限、运行、评测集和结构化证据索引；JSONB 放可变解析属性，关系型列保存必须过滤和约束的字段。
- 对象存储保存源文件、页面渲染图和中间解析产物，按内容 hash 版本化；本地演示可用 MinIO 或文件卷。
- Qdrant 保存 chunk dense/sparse vectors 与过滤 payload；若语料规模小、系统组件需最少，可用 PostgreSQL + pgvector 作为替代。向量索引需与 `document_version`、ACL、parser/embedding model version 同步。
- Redis 可选：短时会话、任务协调、幂等锁和缓存。缓存键必须包含用户权限、查询规范化、数据/文档版本和模型/提示词版本，避免跨权限复用。
- Schema Graph 首版存 PostgreSQL edge table 并在内存构图；只有在多域关系查询、图遍历成本明显时再评估 Neo4j。

## 8.3 数据治理与版本

每次评测或 Demo 的 manifest 固定：应用 commit、SQL schema snapshot、业务词典版本、文档集合 hash、parser 版本、embedding/reranker 版本、模型 ID、提示词版本、温度/采样参数、评测集版本、缓存状态、部署规格。更新知识库采用 `ingest → validate → shadow index → atomic switch`，失败可回滚到上一个可用索引。

# 9. Evaluation System

## 9.1 测试集建设

初赛至少建立 10 道 NL2SQL 与 10 道 RAG 测试题；建议 6 周内扩充为 60-100 条可审计用例，并保持训练样例与盲测集隔离。样本按难度和失败类型分层：

- NL2SQL：单表过滤、时间范围、别名、枚举、JOIN 2/3/4 表、聚合、排序 Top-k、同比/环比、嵌套、空值和歧义澄清。
- RAG：原文段落、跨页标题、扫描件 OCR、复杂表格、表格计算、公式变量、跨文档冲突、过期版本、答案不可得。
- Cross-source：SQL→RAG，RAG→SQL，SQL/RAG 并行后融合，两步以上多跳，跨源同名异义与实体映射失败。
- Robustness：错别字、口语简称、时间缺失、超长问题、权限越界、空检索、坏文件和服务超时。

每条 Case 包含：`case_id`、类别/难度、用户问题、期望路由、gold query plan、gold SQL 或允许 SQL 集、结果断言、必需证据 ID、gold answer claims、澄清标签、失败类别、数据版本、人工审核者。

## 9.2 指标与计算

| 子系统 | 主要指标 | 说明 |
|---|---|---|
| Schema Linking | Recall@k、MRR、表/列/关系路径准确率 | 分别报告表、列、枚举值，使用人工标注 gold IDs |
| NL2SQL | Exact Match、Execution Accuracy、Clause Accuracy | Execution Accuracy 以只读参考库执行后结果比较；数值允许容差须逐题声明 |
| RAG Retriever | Recall@k、Precision@k、MRR、nDCG@k | 至少报告 k=5、10，按文档类型/质量分层 |
| RAG Answer | Correctness、Faithfulness、Citation Precision/Recall | 规则/标注为主，LLM Judge 只作辅助；随机抽样人工复核 |
| Agent / V-CAR | 路由准确率、计划可执行率、跨源答案准确率、Verify 检出率、Repair Success Rate | 分母分别报告触发校验数/修复数，不能只展示成功案例 |
| 可解释性 | Claim Evidence Coverage、Trace Completeness、Source Locator Accuracy | 输出 Claim 到 SQL/块/页码路径的可验证比例 |
| 性能 | p50/p95 latency、吞吐 QPS、错误率、token/查询、cache hit | 分单源/跨源；固定数据量、并发数和硬件 |
| 鲁棒性 | 澄清正确率、拒答正确率、权限违规数、低质文档风险召回率 | 对抗/异常用例单列；权限违规目标为 0 |

执行结果比较可先用排序无关的行集 canonicalization（列顺序归一、数值类型归一、明确小数容差），对排名/限量问题必须保留排序与 Top-k。不要仅用 SQL 字符串完全相同作为准确率；也不要把“可运行”当成语义正确。

## 9.3 测试流程和门禁

1. Freeze 数据、元数据和评测集版本，记录 manifest。
2. 运行组件级回归：Parser、Schema Retriever、SQL Guard、Retriever、Verifier。
3. 运行端到端基准：按场景、难度、模态、工具路径输出 confusion matrix 和错误簇。
4. 对每次提示词/索引/模型/路由变化做 A/B 与消融；一次只改变有限因素。
5. 对新增成绩人工抽查至少 10% 样本与全部高风险边界样本。
6. 发布门禁：SQL 安全违规为 0；关键路径通过率不下降；错误答案和低证据回答无静默增长；p95 不超预算；演示问题可重复运行。

## 9.4 多规模性能实验

建议准备小/中/大三档：结构化库 10 万/100 万/1000 万行（可用生成数据）；知识库 100/1000/10000 文档；并发 1/5/20。报告实际硬件、模型服务位置、预热与冷启动、网络、索引参数、缓存开关、批次大小。压测输出 p50/p95、RPS、失败率、数据库扫描行数、向量候选数量、LLM token 和成本估算。所有数字必须来自实测；没有 GPU 时可用外部模型 API 或 CPU 测试，并明确边界。

# 10. 接口契约与关键数据结构

## 10.1 外部 API

| 方法与路径 | 请求/用途 | 返回 |
|---|---|---|
| `POST /v1/chat/stream` | `{conversation_id?, question, scope, options}`；发起并流式呈现问答 | SSE TraceEvent 与 answer chunk |
| `POST /v1/documents` | 上传文件及 ACL/collection 元数据 | `document_id`、ingest job id、初始状态 |
| `GET /v1/documents/{id}` | 查询解析、质量和索引状态 | `DocumentAsset` 摘要、错误和质量报告 |
| `GET /v1/runs/{id}` | 查看运行状态、最终答案和版本 manifest | `RunSummary` |
| `GET /v1/runs/{id}/events` | 按游标拉取/续传 Trace | 有序事件列表 |
| `GET /v1/runs/{id}/evidence` | 获取 Claim 与证据图 | claim/evidence nodes and edges |
| `POST /v1/evaluations/{suite}/run` | 启动固定版本评测 | evaluation job id；管理员权限 |

鉴权采用 JWT/反向代理身份；`scope` 只能收窄用户权限，不能由请求体提升权限。文件上传限制类型、大小、页数与解析时长；API 返回稳定 `error_code`、`message`、`retryable` 和 trace id。

## 10.2 Chat 请求

```json
{
  "conversation_id": "conv-001",
  "question": "2025年销售额最高的产品是什么？结合报告解释原因。",
  "scope": {"database_sources": ["demo_sales"], "knowledge_collections": ["market_reports"]},
  "options": {"stream": true, "show_sql": true, "answer_locale": "zh-CN"}
}
```

## 10.3 最终答案响应

```json
{
  "run_id": "run-001",
  "status": "completed",
  "answer": "……[E1][E2]",
  "claims": [{"claim_id":"c1","text":"产品 X 的销售额最高","status":"verified","evidence_ids":["e1"]}],
  "confidence_level": "high",
  "trace_cursor": "evt-009",
  "versions": {"schema":"s-14","knowledge":"kb-22","prompt":"p-8","model":"model-config-3"}
}
```

## 10.4 错误契约

| 错误码 | 含义 | 行为 |
|---|---|---|
| `AMBIGUOUS_METRIC` | 指标口径不唯一 | 返回候选定义并请求澄清 |
| `SCHEMA_NOT_FOUND` | 未找到可信字段或关系 | 不生成 SQL；提示维护元数据 |
| `SQL_POLICY_BLOCKED` | SQL 触犯只读/权限/资源门禁 | 拒绝执行并记录规则 |
| `SQL_TIMEOUT` | 查询超时 | 取消语句，受预算限制重试或缩小范围 |
| `DOCUMENT_LOW_QUALITY` | 文档解析可信度不足 | 进入降级/人工复核，引用中标警告 |
| `EVIDENCE_NOT_FOUND` | 没有足够证据支持答案 | 澄清、扩大授权范围或返回未验证 |
| `CROSS_SOURCE_CONFLICT` | 数据库与文档矛盾 | 分列展示来源和有效期，不自动裁定 |
| `REPAIR_BUDGET_EXHAUSTED` | 修复预算用尽 | 终止并附失败轨迹，不循环调用 |

# 11. Demo 设计与验收脚本

## 11.1 演示数据

构建可重置的零售分析样例库：`customers`、`orders`、`order_items`、`products`、`regions`；指标定义 `sales_amount`、`units_sold`、`refund_amount`；知识库包含产品分析报告、渠道策略、扫描版表格报告和一份含公式的业务说明。样例数据明确标注为演示数据，不以模拟结果声称真实企业成绩。

## 11.2 三条现场演示路径

**路径 A：基础问数（约 1 分钟）**  
问：“2025 年华东地区销售额最高的前三个产品是什么？”展示时间归一化、Schema 命中、Join Path、SQL Guard、结果表和 SQL 证据。

**路径 B：跨源 V-CAR（约 2 分钟）**  
问：“找出 2025 年销售额最高的产品，并结合最新市场报告说明可能原因。”展示 Plan DAG、SQL→RAG 实体传递、报告页码/原文、Claim-Evidence、Verifier PASS 和耗时。

**路径 C：失败与自修复（约 1 分钟）**  
预置元数据别名不完整或故意给出低质量扫描件，让系统显示初次失败类别、受控修复/降级、修复后重新验证；若仍不能证实，展示澄清或安全终止。演示真实失败记录，不隐藏失败率。

## 11.3 现场演示验收清单

- 一键启动；演示前恢复数据库快照和知识库版本。
- 全流程在一场会话中展示，刷新页面可用 `run_id` 恢复 Trace。
- 现场能查看 SQL、结果、引用页、检索证据和校验状态。
- 至少展示 1 次主动澄清、1 次跨源成功、1 次 Verify/Repair 或失败终止。
- 避免依赖临时网络搜索；需要外部模型时提前检查服务并提供本地 fallback 答案说明。
- Demo 的每个量化指标显示测试版本和测量边界；不预填虚构的成功率或延迟。

# 12. 技术选型与替代方案

| 层 | 首选 | 用途与取舍 | 可替代项 |
|---|---|---|---|
| 语言/环境 | Python 3.11+、uv 锁依赖 | 生态适合 OCR、数据、Agent；用 lockfile 固定构建 | Poetry / Conda |
| API | FastAPI + Pydantic | 类型化契约、OpenAPI、SSE 接口；API 与服务层分离 | Django REST Framework、Litestar |
| Agent 编排 | 自建 typed DAG/state machine | 控制流透明、便于事件留痕与比赛讲解；首版运维简单 | LangGraph（需持久化状态/回放时）；Temporal（长任务、多服务规模时） |
| 主元数据库 | PostgreSQL | 目录、运行、评测、权限、JSONB 和事务 | MySQL；SQLite 仅本地原型 |
| 向量检索 | Qdrant | 独立向量服务，支持 dense/sparse/hybrid 查询 | pgvector（小型一体化）；Milvus（大规模、多租户部署） |
| SQL 语法与 AST | SQLGlot | 多方言 AST 解析、表列提取、规范化和安全规则前置 | SQLAlchemy Core 表达式（生成）；各数据库 EXPLAIN/权限仍须保留 |
| 文档解析 | Docling + PyMuPDF；OCR 分支 PaddleOCR | 版面结构/文本提取分开路由；保留原文坐标 | MinerU、Unstructured、Tesseract + 自研版面；按中文扫描件基准对比 |
| Embedding / Rerank | 可配置的中文/多语模型服务 | 版本可替换；以语料评测决定，不绑定单一供应商 | BGE 系列、Jina、云端 Embedding API；候选均须同集基准测试 |
| 对象存储 | MinIO / 兼容 S3 | 源文件和渲染页的不可变版本保存 | 本地文件卷（Demo）；云厂商对象存储 |
| 前端 | Vue 3 + TypeScript + Vite | 中小型竞赛前端开发效率高；模块化问答与 Trace 界面 | React + TypeScript |
| 图与图表 | Vue Flow + ECharts | Agent DAG、耗时和评测图表 | Cytoscape.js、AntV X6 / G6 |
| 可观测性 | OpenTelemetry + Prometheus + Grafana | 统一 Trace/指标；应用 run trace 仍单独持久化 | Jaeger/Tempo、Loki；比赛简化可先导出 JSONL |
| 部署 | Docker Compose | 单机复现、赛场搬运简单 | Kubernetes（多节点、弹性和生产隔离需要时） |

选型依据：先以端到端能力和可解释实验闭环为目标，不先引入多个分布式服务。数据库方言、文档类型、中文 OCR、向量过滤和部署硬件都要以真实赛题样例作小型 Benchmark。FastAPI、Qdrant Hybrid Search、SQLGlot 和 Vue 3 的官方文档说明了本方案所用 API/混合检索/AST/前端机制，链接见参考资料。实际版本以研发时锁定的 release、兼容性测试和赛事环境为准。

# 13. 前后端与部署方案

## 13.1 服务拆分

**单体模块化服务起步**：`api`、`orchestrator`、`sql`、`rag`、`verification`、`trace`、`evaluation` 分 package，共用类型和依赖注入；文档摄取/评测作为单独 worker 进程，必要时用 Redis Queue/Celery。不要在首版把每个 Agent 变成微服务。

前端 `Vue 3 + TypeScript + Vite`；状态管理 Pinia；请求/流式事件由统一 client 处理；Vue Flow 显示执行 DAG；ECharts 展示性能；PDF.js 或服务端页图支持证据定位。后端 `FastAPI + Pydantic + SQLAlchemy`；异步任务保存 PostgreSQL 状态，文件进 MinIO，向量写 Qdrant。

## 13.2 Docker Compose 部署拓扑

```text
reverse-proxy (TLS / static frontend)
   ├── frontend (Vue build)
   └── api (FastAPI, 1-2 replicas)
         ├── worker (OCR / ingestion / evaluation)
         ├── postgres (metadata + app state)
         ├── qdrant (dense/sparse index)
         ├── minio (source files / page images)
         └── redis (optional queue/cache)
```

数据库赛题样例作为独立 read-only source 连接，或使用容器化 PostgreSQL 初始化脚本；不要与 Metadata DB 共用高权限账号。模型服务支持 remote API 与本地兼容 OpenAI API 的 vLLM/Ollama adapter。模型不可用时，明确显示 degraded mode，不返回伪造的正常结果。

## 13.3 安全、权限与资源限制

- SQL: 独立只读角色、schema/table allowlist、AST allowlist、查询超时、结果行数和导出限制。
- 文件: MIME 与魔数双重检测、大小/页数限制、病毒扫描按条件启用、路径隔离、租户 ACL 贯穿索引过滤。
- 模型提示: 文档内容作为不可信数据，禁止其改变系统规则或工具权限；工具参数由 Pydantic/allowlist 校验。
- 运行: 最大任务数、工具并发、token、deadline、重复调用上限；cancel 传播至数据库/worker。
- 机密: `.env` 不入库；生产 secret manager；日志去除 key、原文 PII 和受限 SQL 参数。
- 评测: 生产数据和演示数据分环境；人工检查已授权数据，模型供应商的数据保留条款单独审核。

# 14. 仓库目录建议

```text
datamind-agent/
├── apps/
│   ├── api/                    # FastAPI entrypoint、routes、SSE
│   ├── worker/                 # 摄取、OCR、评测异步任务
│   └── web/                    # Vue 3 + TS 问答/trace/dashboard
├── backend/
│   ├── core/                   # 配置、日志、鉴权、错误码、依赖
│   ├── contracts/              # Pydantic schema、内部事件契约
│   ├── agents/                 # planner、router、sql、rag、fusion、verifier
│   ├── orchestration/          # typed DAG、state machine、budget、resume
│   ├── nl2sql/                 # introspection、catalog、retriever、graph、guard、repair
│   ├── rag/                    # ingestion、quality、parsers、chunking、retrieval、citation
│   ├── knowledge/              # entities、aliases、metrics、evidence graph
│   ├── storage/                # postgres、qdrant、object store、repositories
│   ├── explain/                # trace events、run replay、redaction
│   └── evaluation/             # metrics、runner、reports、regression gates
├── frontend/
│   ├── src/pages/              # chat、trace、knowledge、evaluation
│   ├── src/components/         # DAG、SQL panel、evidence viewer、charts
│   ├── src/api/                # OpenAPI client / SSE client
│   └── src/stores/             # conversation、run、knowledge state
├── configs/                    # parser、model、prompts、limits（不含密钥）
├── data/
│   ├── demo/                   # 小型可重建样例数据
│   └── samples/                # 已授权/脱敏样本
├── benchmark/
│   ├── cases/nl2sql.jsonl
│   ├── cases/rag.jsonl
│   ├── cases/cross_source.jsonl
│   ├── expected/               # gold SQL、claims、evidence labels
│   └── manifests/              # 数据、模型、索引、提示词版本
├── migrations/                 # PostgreSQL migration
├── deploy/
│   ├── compose.yaml
│   ├── Dockerfile.api
│   ├── Dockerfile.web
│   └── init/                   # demo seed and read-only role
├── docs/                       # 架构、API、评分映射、答辩材料
├── scripts/                    # ingest、reset-demo、eval、load-test
├── tests/                      # unit、integration、security、e2e
├── pyproject.toml
├── uv.lock
├── .env.example
└── README.md
```

# 15. 六周研发路线图（每 3 天一个里程碑）

## 里程碑 M1（第 1-3 天）：赛题冻结与工程骨架

**目标**：把评分点变成需求、数据契约和可启动骨架。  
**任务**：整理基础/中级/高级任务矩阵；冻结首批演示问题；建 Git、配置、FastAPI、Vue、PostgreSQL、Compose 骨架；定义 QueryFrame、TaskPlan、TraceEvent。  
**交付物**：需求追踪表、仓库骨架、OpenAPI 初稿、端到端空流程。  
**验收**：一键启动；`/health` 与前端连通；最小基准不少于 10 条，覆盖 NL2SQL 与 RAG 且有 gold label。
**风险**：赛题评分理解偏差；处理：逐项回看原表，保留评分条款到需求映射。

## M2（第 4-6 天）：演示数据与元数据登记

**目标**：有可控、可重置的数据源与术语表。  
**任务**：准备 customers/orders/order_items/products/regions；创建只读角色；录入 20 个字段描述、别名、单位、主外键和指标口径；实现 SchemaSnapshot。  
**交付物**：SQL seed、业务词表、元数据 schema、初始化/重置脚本。  
**验收**：初始化可重复；元数据覆盖核心表列 100%；业务指标定义可查询；只读账号无法写表。  
**风险**：演示数据过简；处理：覆盖 JOIN、空值、退款、不同时间口径等边界。

## M3（第 7-9 天）：Schema Linking 与 Join Graph

**目标**：实现“问题要查哪张表/列/关系”的独立能力。  
**任务**：实现词法别名、样例值、向量/词法混合召回；实现 Join Graph 路径代价；构建 30 条字段/关系标注题。  
**交付物**：Schema Retriever、JoinPlanner、Schema Linking 评测报告。  
**验收**：Top-5 表列 Recall@5 ≥ 0.85；核心 JOIN 路径正确率 ≥ 0.80；每个候选返回理由。  
**风险**：仅靠 embedding 误召回；处理：指标口径和关系图作为硬约束，低分场景澄清。

## M4（第 10-12 天）：结构化 Query Frame 与 SQL 生成/静态校验

**目标**：把问数意图约束为可审查计划，再生成 SQL。  
**任务**：实现时间/指标/维度抽取；定义计划 JSON Schema；接入 SQLGlot；实现 SELECT-only、权限表、行数、LIMIT、JOIN 检查。  
**交付物**：SQLPlan、SQLGenerator、SQLGuard、单元级安全样例。  
**验收**：基础问题可生成并通过 AST；DDL/DML/越权/无界查询拦截率 100%；歧义指标触发澄清。  
**风险**：静态规则误拦合法 SQL；处理：逐规则记录 reason code，建立正反例回归集。

## M5（第 13-15 天）：SQL 执行、语义校验与有界修复

**目标**：完成稳定问数闭环和错误恢复。  
**任务**：只读事务执行；单位/粒度/空值检查；分类语法、列名、方言、超时错误；最多 2 轮修复；补齐 30-40 条 NL2SQL 集。  
**交付物**：SQL Agent v1、错误字典、NL2SQL 回归报告。  
**验收**：初版 Execution Accuracy ≥ 0.75；安全违规 0；修复后正确率与修复次数分布可导出。  
**风险**：LLM 修复绕过安全边界；处理：所有候选修复重新过 Guard，修复只基于允许 Schema。

## M6（第 16-18 天）：文档解析、质量分与路由

**目标**：原生 PDF 和扫描 PDF 均能生成带页码的结构块。  
**任务**：接入 Docling/PyMuPDF；扫描件 OCR 分支；页面质量指标；标题、表格、公式与 bbox 输出；登记 parser version。  
**交付物**：Ingestion Worker、DocumentBlock schema、质量报告页。  
**验收**：10 份样本文档解析完成；页码映射率 ≥ 0.95；低质样例能被识别并降级。  
**风险**：复杂 PDF 解析成本高；处理：先覆盖评分样例，异步解析并保留原页图复核路径。

## M7（第 19-21 天）：结构化切片、向量索引与混合检索

**目标**：建成可追溯、受 ACL 控制的 RAG 检索基础。  
**任务**：实现标题树切片、表格/公式独立块、内容 hash；Qdrant dense/sparse 索引；ACL/日期 filter；RRF 融合。  
**交付物**：Chunker、索引重建脚本、检索调试页。  
**验收**：初始 RAG Recall@5 ≥ 0.80；每个命中可回到文档版本、页码、块坐标；越权检索 0。  
**风险**：切片粒度影响证据完整度；处理：对照多种 chunk 长度和 overlap 做离线比较。

## M8（第 22-24 天）：重排、引用与 RAG 答案校验

**目标**：从“检索到内容”走到“答案 claim 有据可查”。  
**任务**：接入 reranker；上下文去重/预算；句级 Claim-Evidence；引用定位与冲突标记；增补扫描件、表格题。  
**交付物**：RAG Agent v1、引用组件、RAG 基准报告。  
**验收**：引用定位率 ≥ 0.90；人工抽查忠实度 ≥ 0.85；无证据题能正确 abstain 或澄清。  
**风险**：模型引用看似合理但支持不足；处理：对每条关键断言执行支持性复核和人工抽样。

## M9（第 25-27 天）：Planner、Router 与持久化任务 DAG

**目标**：统一编排 SQL、RAG 和澄清路径。  
**任务**：实现状态机、TaskPlan Schema、路由策略、并行子任务、预算、事件落库、断线续传。  
**交付物**：Orchestrator v1、SSE Event API、运行回放端点。  
**验收**：SQL-only/RAG-only/DB→RAG 三类路由测试通过；事件顺序完整率 ≥ 0.98；超时后可终止。  
**风险**：过早拆 Agent 造成复杂度；处理：保持模块内函数式工具，单进程可观测执行。

## M10（第 28-30 天）：跨源融合、实体对齐与 Evidence Graph

**目标**：完成真实的跨源数据依赖和多跳问答。  
**任务**：实现规范产品 ID/别名映射；SQL→RAG 传参；反向 RAG→SQL 的实体过滤；Claim/Evidence 节点与边；冲突处理。  
**交付物**：Fusion Agent、Evidence Graph API、20 条跨源案例。  
**验收**：跨源实体对齐准确率 ≥ 0.90；至少 10 条多跳用例有逐步证据；无映射时不自动合并。  
**风险**：同名实体串错；处理：规范 ID 优先、映射阈值和人工确认状态进入证据图。

## M11（第 31-33 天）：V-CAR 校验/修复闭环

**目标**：形成高级创新的核心可量化实验。  
**任务**：实现数值、引用、实体、时间、逻辑和安全校验；定义 failure code；局部 repair；记录修复前后图；做无 verifier/有 verifier 消融。  
**交付物**：Verifier v1、Repair Policy、消融报告初版。  
**验收**：严重答案错误检出率 ≥ 0.90；错误触发修复成功率 ≥ 0.60；最大修复 2 次；修复后所有结果重新验收。  
**风险**：Verifier 与生成器共享同一模型产生相关偏差；处理：关键数值/权限使用确定性规则，模型评审仅作辅助信号。

## M12（第 34-36 天）：全链路前端与证据可视化

**目标**：现场能看懂、复核和重放系统执行。  
**任务**：完成聊天、DAG、SQL、证据页图、校验卡片、指标面板；流式更新与刷新恢复；脱敏。  
**交付物**：Vue Demo v1、预置运行记录和现场脚本。  
**验收**：三条 Demo 路径可完成；Trace 展示真实 SQL/命中块/校验；可从答案跳到出处；页面 PII 泄漏 0。  
**风险**：视觉效果抢占核心验证时间；处理：优先主链路和证据定位，再做动画/配色。

## M13（第 37-39 天）：性能、安全与鲁棒性

**目标**：证明系统不是只对单条样例有效。  
**任务**：小/中/大数据规模和 1/5/20 并发压测；为常用查询、schema 和解析结果加版本化缓存；检查资源上限、ACL 和 Prompt Injection；失败恢复演练。  
**交付物**：压测报告、安全检查清单、瓶颈与优化记录。  
**验收**：p50/p95/QPS/错误率均有原始记录；权限违规 0；系统资源受预算限制；缓存开关有对照数据。  
**风险**：外部模型服务波动；处理：固定模型版本或记录 API 版本，准备本地 fallback 与离线案例。

## M14（第 40-42 天）：冻结、验收与比赛交付

**目标**：完成能现场复现的版本、报告和答辩证据。  
**任务**：冻结代码/数据/模型/评测版本；跑完整 benchmark；清理数据与配置；演练失败/澄清/跨源 Demo；制作架构图、结果表和技术说明。  
**交付物**：release tag、技术文档、部署包、评测结果、Demo 视频/备份、答辩材料。  
**验收**：赛场离线或受限网络环境可启动；至少 10 条覆盖 NL2SQL 与 RAG 的测试结果逐条可复核；性能和失败率如实报告；所有展示结果有 run_id 与版本。
**风险**：提交前改动引入回归；处理：冻结窗口内只接受阻断性修复，并重新跑关键回归。

## 15.1 关键路径与每周检查点

| 周次 | 关键里程碑 | Gate |
|---|---|---|
| 第 1 周（D1-D9） | 骨架、演示数据、Schema Linking | 可复现初始化；Top-5 Schema 命中达到第一目标 |
| 第 2 周（D10-D15） | NL2SQL 与安全闭环 | 只读 Guard 通过；基础 SQL 可执行并可修复 |
| 第 3 周（D16-D24） | 多模态解析、质量评估、Hybrid RAG | 页码/引用可追溯；基准题可以回归 |
| 第 4 周（D25-D30） | Planner、跨源依赖与 Evidence Graph | DB→RAG 路径完整，有多跳证据 |
| 第 5 周（D31-D36） | V-CAR 闭环和可视化 | Verify/Repair 可量化；前端可复核运行 |
| 第 6 周（D37-D42） | 性能、安全、冻结与交付 | 端到端可复现；证据与报告对得上 |

# 16. 风险、治理与答辩证据

## 16.1 重点风险登记

| 风险 | 影响 | 预防/应对 | 监测指标 |
|---|---|---|---|
| 赛题原始评分表版本变化 | 做了非评分重点 | M1 对照最新版 PDF；冻结评分追踪表 | requirement-to-evidence 覆盖率 |
| Schema 元数据不全 | NL2SQL 错连/错口径 | 维护负责人、别名审批、低置信澄清 | Linking Recall、误链接率 |
| OCR/版面恢复不稳定 | RAG 命中错误页/错序表 | 按文档质量路由；原页图和人工复核 | 页码准确率、OCR 低置信比例 |
| 模型输出不稳定 | 复测波动 | 固定模型配置、提示词版本和温度；保存原始事件 | 重复运行方差 |
| 跨源实体错误对齐 | 看似合理但结论错 | canonical ID 优先、实体置信门限、冲突展示 | Entity Match Accuracy |
| 无限自修复循环 | 高成本/不可控 | max_repair=2、deadline、token 与工具预算 | 平均修复数、预算耗尽率 |
| 越权 SQL 或文档访问 | 安全/合规事故 | 只读账号、ACL filter、AST guard、审计 | 越权次数目标 0 |
| 外部服务不可用 | Demo 中断 | 提前预热、降级提示、本地预置 trace 与模型 adapter | 服务可用率、降级成功率 |
| 指标被演示样例误导 | 成绩不可复核 | 固定 benchmark、分层报告、记录分母与失败例 | 报告复现率 |

## 16.2 答辩展示证据链

每个技术亮点按“问题 → 设计 → 对照 → 结果 → 限制”讲述：

- **Schema Linking**：别名/图结构引入前后 Top-k 和 SQL Execution Accuracy 差异。
- **Adaptive RAG**：统一解析器与质量感知路由的页码、表格、召回对照。
- **V-CAR**：无校验 vs 校验 vs 修复闭环；报检出率、修复成功率、额外耗时和拒答变化。
- **性能**：不只给最好延迟；说明数据规模、硬件、并发、冷/热缓存及 p95。
- **可解释**：从最终答案点击到 SQL 行、原始页、证据块和校验规则，展示失败案例的真实轨迹。

对尚未完成的能力标注为研发计划；对完成项必须展示运行 ID、代码/模型版本、数据版本和可复现输入。不可用准确率目标替代实测成绩。

# 17. 参考资料

## 17.1 赛题资料

- 《赛题八：多模态数据驱动的可解释精准问数/问答智能体》赛题 PDF，命题单位：中电云计算技术有限公司；任务分类和分值以上文“文档使用说明”所述的团队赛题阅读记录为设计约束。最终答辩/提交时以组委会公布的最新版原文及评分表为准。

## 17.2 技术文档（官方/项目文档）

1. FastAPI 官方教程与 API 文档：https://fastapi.tiangolo.com/tutorial/  
2. PostgreSQL JSONB 与索引：https://www.postgresql.org/docs/current/datatype-json.html  
3. PostgreSQL 全文检索：https://www.postgresql.org/docs/current/textsearch.html  
4. Qdrant Hybrid and Multi-Stage Queries：https://qdrant.tech/documentation/search/hybrid-queries/  
5. SQLGlot 文档：https://sqlglot.com/  
6. Vue 3 Composition API 指南：https://vuejs.org/guide/extras/composition-api-faq.html  
7. OpenTelemetry 概念与 Trace：https://opentelemetry.io/docs/concepts/  
8. Docling 项目文档：https://docling-project.github.io/docling/  
9. PaddleOCR 项目文档：https://github.com/PaddlePaddle/PaddleOCR  
10. pgvector 项目文档：https://github.com/pgvector/pgvector

> 依赖版本、模型服务和赛事部署要求应在正式实现时通过兼容性测试锁定；本说明书不假定任何组件版本在赛期始终不变。
