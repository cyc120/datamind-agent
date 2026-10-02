# DataMind Agent — 多模态数据驱动的可解释精准问数/问答智能体

赛题八（"中国电子杯"高校 ICT 产教融合创新大赛 · 中电云计算技术有限公司命题）项目仓库。

本仓库包含三大部分：

1. **`gateway/` — 大模型接入基座（可选）**：基于 New API 的模型网关，可继续用于渠道池、令牌配额与调用审计。应用默认直连上游 OpenAI 兼容接口——在 8000 页面左侧「模型接入」填入接口地址、模型名和 API 密钥即可，无需启动该网关；若要经过网关，把接口地址填成 `http://localhost:4000/v1` 即可。详见 [gateway/README.md](gateway/README.md)。
2. **`deploy/` `db/` `ingestion/` `scripts/` `data/` — 基础数据库底座**（本 README 主体）：结构化业务库 + 非结构化知识库 + 元数据基础设施 + 评测集，全部可重置、可复现。
3. **`datamind/` `web/` — 应用层**：结构化问数与文档问答 Agent、HTTP/SSE 接口、引用定位和运行回放。

`赛题/` 和 `技术文档/` 为项目资料，不参与网关运行。

技术设计说明书已于 **2026 年 10 月 1 日修订为 V1.2**：依据[复杂文件分类与解析参考 MD](<技术文档/复杂非结构化文件分类与解析技术文档.md>)完善工业增强设计。当前 SQL 单源及文档单源问答均可运行；真实扫描 OCR、公式图片解析和字段候选/定位已接入；已增加限定工业温度的“工艺原文→只读 SQL→质检报告”规则核验和工业生产模型问数；质检扫描表头、完整表格结构、CAD、通用工业查询与冲突裁决仍待扩展。状态见[技术说明书 §18](技术文档/DataMind_Agent_技术设计说明书_V1.2.md#18-实施进度与下一步验收2026-年-10-月-2-日)，文档问答历史基线见 §18.12，OCR/公式阶段记录见 §18.13，扫描字段进度见 §18.14，工业规则阶段见 §18.15，最新工业模型问数见 §18.16。

## 当前进度（2026-10-02，API 0.7.0）

[进度总览与下一阶段验收](PROGRESS.md) 汇总当前状态；详细实现、历史错误与分母保留在技术说明书 §18.12–18.19。

| 阶段 | 已验证结果 | 当前缺口 |
|---|---|---|
| 工业生产模型问数 | 8/8 开发题、2/2 模型前澄清 | 限定单层查询；复杂 SQL、独立泛化未验收 |
| 工业跨源规则 | 13/13 开发规则；单位/版本/超限/冲突可解释 | 正式审批、来源权威规则与 CAD 待实现 |
| 新模板字段首轮 | 28/56 字段、2/4 完整事实；原生/扫描各 14/28 | 紧凑半角冒号模板未形成接受事实 |
| 字段复核 | 原页定位、确认/存疑/退回、版本审计、历史与幂等 | 本机未认证演示；缺失字段补录与正式审批未实现 |
| 组件/集成回归 | 89/89，最新同步核查见 `reports/repository_regression.txt` | 不代表 89 道模型问答准确率 |

下一阶段优先在独立开发材料中支持紧凑格式，再用未参与修复的样本验收。新模板是实现后编写的合成测试材料，不能作为工厂盲测成绩；历史扫描字段 3/15、RAG 10/12 及单题复测 1/1 保持各自分母。

## 模块一：结构化数据与精准问数

`datamind/` 已实现技术说明书 §4 的首个可运行闭环：版本化 Catalog 读取、中文别名/枚举召回、审核关系的 Join Path、结构化问题帧、模型生成 SQL、SQLGlot AST Guard、参数绑定、只读事务执行、指标口径检查和最多两次数据库错误修复。命令行入口：

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-sql.txt
docker compose -f deploy/compose.yaml up -d postgres
# 全新数据库首次启动后，加载演示数据、Catalog 和评测用例：
docker cp db/chinook/Chinook_PostgreSql.sql datamind-postgres:/tmp/chinook.sql
docker exec datamind-postgres psql -U postgres -q -f /tmp/chinook.sql
docker exec datamind-postgres psql -U postgres -d chinook -q -c 'GRANT CONNECT ON DATABASE chinook TO readonly_user, datamind_app; GRANT USAGE ON SCHEMA public TO readonly_user, datamind_app; GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user, datamind_app;'
.venv/bin/python db/seed_demo_retail.py
.venv/bin/python db/build_catalog.py
.venv/bin/python db/seed_eval_cases.py
.venv/bin/python scripts/ask_sql.py '2025年销售额最高的前三个产品是什么？'
.venv/bin/python scripts/ask_sql.py 'Chinook库里哪个国家客户最多？' --source chinook
# 全套回归还依赖文档/工业底座，先安装依赖并完成非破坏性初始化：
.venv/bin/pip install -r requirements-ocr.txt
bash scripts/start_local.sh --background
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/eval_sql.py --output reports/nl2sql.json
.venv/bin/python scripts/eval_sql.py --suite robustness --output reports/robustness.json
.venv/bin/python scripts/eval_schema.py --output reports/schema_linking.json
.venv/bin/uvicorn datamind.api:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

模型接入在页面左侧「模型接入」中配置并即时生效，支持**多套接入配置**（ccswitch 风格）：每套配置可独立选择协议、地址、模型、密钥、鉴权方式，一键切换当前启用，支持导出/导入（默认不含密钥）。**协议支持 OpenAI 兼容（`/chat/completions`）与 Anthropic Messages（`/v1/messages`）**两套；Base URL 会自动补版本段（`/v1`、`/api/paas/v4`、`/api/v3` 都能直接填）。预设 DeepSeek / Anthropic 官方 / Claude 中转 / 阿里云百炼 / 智谱 / 月之暗面 / 硅基流动 / 火山方舟 / 本地网关 / 自定义，填入 API 密钥后可「拉取模型列表」验证密钥、选模型、「测试连接」验证可对话。密钥存放于 `datamind_meta.app_settings`，接口只回显掩码，`readonly_user` 对该表无任何权限；不再读取任何本地密令文件。同名环境变量 `NEW_API_BASE_URL`、`NEW_API_MODEL`、`NEW_API_KEY` 仍可作为兜底（优先级低于页面配置）。**本机地址（localhost/127.0.0.1）始终直连**——httpx 会经 urllib 读取 macOS 系统代理，若不绕开会把本地网关请求塞进代理导致挂起或 502；远端地址继续尊重系统/环境代理，也可在配置里用「代理」字段显式指定。问数数据库只用 `readonly_user`；可通过 `DATAMIND_META_DSN`、`DATAMIND_META_WRITE_DSN`、`DATAMIND_DB_HOST`、`DATAMIND_DB_PORT`、`DATAMIND_QUERY_USER`、`DATAMIND_QUERY_PASSWORD`、`DATAMIND_SQL_TIMEOUT` 配置。失败时返回可读错误码：`MODEL_KEY_MISSING`（未配置密钥）、`MODEL_AUTH_FAILED`（密钥被拒）、`MODEL_BALANCE_EMPTY`（上游额度不足）、`MODEL_UNREACHABLE`、`MODEL_TIMEOUT` 等；评测脚本逐例记录结果，不把失败计为正确。

本地 Agent 提供 `POST /v1/chat`、`POST /v1/chat/stream`（SSE）、`GET /v1/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events?after=...`、`GET /v1/runs/{id}/evidence`、`GET|PUT|DELETE /v1/model/config`（兼容单配置）、`GET|POST /v1/model/profiles`、`PUT|DELETE /v1/model/profiles/{id}`、`POST /v1/model/profiles/{id}/activate`、`GET /v1/model/profiles/export`、`POST /v1/model/profiles/import`、`POST /v1/model/config/models`、`POST /v1/model/config/test` 和 `/health`。页面有问数与问答、模型接入、运行记录、字段复核四个视图，可直接深链 `/#model`、`/#runs`、`/#fields`。运行、事件、SQL/文档证据和 Claim 写入 `datamind_meta`，可按 run ID 回放。服务面向本机演示，默认绑定 `127.0.0.1`，尚未接入身份认证。工业温度问题可进入限定跨源规则工具；零售等通用数据库与文档联合推理仍返回 `ROUTE_UNSUPPORTED`。

测试页另有“测试数据库链路（不调用模型）”按钮：它只运行一条固定的 2024 年订单数查询，经过同一 Agent、Guard 和只读数据库连接，可在模型令牌不可用时验证前端、数据库、Trace 与回放。此按钮的结果不计入 NL2SQL 模型准确率。

当前 Schema 召回采用词法、业务别名、枚举值和已审核 Join Graph；向量召回及其权重调参尚未接入。

Schema Linking 的独立验收入口是 `.venv/bin/python scripts/eval_schema.py`。`data/evaluation/schema_linking_v1.json` 固定了 12 条 NL2SQL 问题及 6 条扩展问题的人工核对 gold 表、语义锚点列、指标和连接路径。脚本使用 SQL Agent 实际的 8 候选预算，分别计算表/列/指标的 Top-5 微平均召回与 Join Graph 路径准确率；报告记录逐题漏召回、Catalog 快照及检索代码哈希。当前结果见 `reports/schema_linking.json`：表 **31/31**、列 **28/28**、指标 **4/4**、路径 **6/6**。这是小规模自建样本上的元数据召回成绩，不等同于复杂问题的端到端 SQL 准确率；向量召回尚未接入。

**当前实测基线（2026-09-30，模型 `deepseek-flash` 经本地网关，快照 `snap-demo_retail-e9495c660cd1`，评测集 `retail-v2` + 提示词 `sql-v2`）**：NL2SQL 执行一致率 **14/14，连续三轮**；鲁棒性用例 **4/4**（错别字、口语简称、越权拒答、空结果）。逐例报告含生成 SQL、gold SQL、修复尝试与结果预览，见 `reports/nl2sql.json`、`reports/robustness.json`。样本量小，不能外推为通用准确率；技术说明书 §4.6 的 Recall@5 仍需在扩样后的固定评测集上实测。

## 一、数据库总体架构

```
┌────────────────────── Docker Compose (deploy/compose.yaml) ──────────────────────┐
│  PostgreSQL :5432                    Qdrant :6333/:6334                          │
│  ├─ datamind_meta   元数据底座        └─ datamind_knowledge  169 向量点           │
│  ├─ demo_retail     自建零售演示库      (BAAI/bge-small-zh-v1.5, 512维, cosine)   │
│  └─ chinook         赛题推荐样例库                                                │
│  角色: postgres(管理) / datamind_app(应用读写) / readonly_user(问数只读)           │
└──────────────────────────────────────────────────────────────────────────────────┘
对象存储: MinIO 官方镜像已于 2025 年从 Docker Hub 下架 → 按 §8.2 用本地文件卷
  data/object_store/page_images/<doc_id>/page_NNN.png  （页面截图，供证据定位）
```

## 二、目录结构

```
├── gateway/                    # 大模型接入基座（New API，可选，见 gateway/README.md）
├── deploy/
│   ├── compose.yaml            # PostgreSQL + Qdrant（MinIO 注释保留）
│   ├── .env / .env.example
│   └── init/                   # 首次启动自动执行
│       ├── 01_roles.sql        #   datamind_app / readonly_user 角色
│       ├── 02_metadata.sql     #   元数据底座 17 张表（§8.1 全部对象）
│       ├── 03_demo_retail.sql  #   零售演示库 DDL + 只读授权
│       └── 04_industrial_schema.sql # 工业 schema，由 postgres 创建并交给应用所有者
├── db/
│   ├── chinook/                # Chinook 加载 SQL（随仓库附带，上游 MIT 协议）
│   ├── seed_demo_retail.py     # 零售库种子数据（固定随机种子，可重复）
│   ├── build_catalog.py        # Introspection → SchemaSnapshot/词表/指标/别名
│   └── seed_eval_cases.py      # 评测集: 14 NL2SQL + 12 RAG + 4 鲁棒 = 30 条
├── ingestion/
│   ├── generate_demo_docs.py   # 生成 12 份中文演示 PDF（数据与 DB 联动）
│   ├── sample_ohrbench.py      # OHR-Bench 分层抽样 14 份（7 领域×2）
│   └── ingest.py               # 摄取: 解析→切片→BGE→Qdrant+PG
├── scripts/
│   ├── reset_demo.sh           # 一键重置全部数据库
│   └── verify.py               # 端到端验证（19 项检查）
├── data/
│   ├── knowledge_base/         # 12 份演示 PDF + manifest + native_counterparts
│   ├── ohr_bench_samples/      # OHR-Bench 样张(gt_text + noise_text)
│   └── object_store/           # 页面截图（证据定位用）
├── 赛题/ 技术文档/              # 项目资料
└── OHR-Bench/ chinook-database/   # 原始数据集（本地下载，不入库）
```

## 三、元数据底座（datamind_meta，对应技术说明书 §8.1）

| 对象 | 表 | 说明 |
|---|---|---|
| DataSource | `data_sources` | 连接、schema allowlist、只读模式 |
| SchemaSnapshot | `schema_snapshots/_tables/_columns/_relations` | 表列快照 + 中文名/别名/枚举/语义角色 + Join Graph 边（审核制） |
| BusinessMetric | `business_metrics` | sales_amount 等 8 个指标口径 |
| EntityAlias | `entity_aliases` | VIP→钻石 等别名映射 |
| DocumentAsset | `document_assets` | sha256/质量分/解析器版本/状态 |
| DocumentBlock/Chunk | `document_blocks` / `chunks` | 块坐标/标题路径/table_json + 向量绑定 |
| EvaluationCase | `evaluation_cases` | 30 条带 gold 标签用例 |
| Run/TraceEvent | `runs` / `trace_events` | 运行与全链路事件持久化 |
| Evidence/Claim | `evidence_nodes/_edges` / `claims` | 证据图与答案主张 |
| IngestJob | `ingest_jobs` | 摄取任务 |
| DocumentFact / ConflictRecord | `document_facts` / `conflict_records` | 候选字段、源审批声明、核验冲突与规则处置 |
| DocumentFactReview | `document_fact_reviews` | 文件/字段版本绑定的提取复核、原始快照与请求审计 |

## 四、结构化数据

**demo_retail（自建零售演示库）**: regions(6) / products(20) / customers(500) / orders(3000) / order_items(7587) / refunds(531)。覆盖边界：退款、折扣、NULL（region_id/cost_price/phone）、6 种订单状态、季节性销量（11-12 月高峰）。2024 年销售额口径校验参考值 **¥2,123,352.61**。

**chinook（赛题推荐）**: 完整加载 11 表（275 艺术家/3503 曲目/412 账单），中文业务词表已登记。

**指标口径（business_metrics）**: `sales_amount = Σ quantity × unit_price × (1 - discount)`，仅统计 completed/shipped/refunded；另有 units_sold / refund_amount / net_sales / order_count / customer_count 及 Chinook 的 revenue / tracks_sold。

## 五、非结构化知识库（26 份文档 / 169 切片）

| 类型 | 文档 |
|---|---|
| 原生文本（多级目录/跨页表） | 01 重点产品分析报告（销量冠军与 DB 联动）、02 渠道白皮书 |
| 表格密集 | 03 分区域经营简报（以已冻结 PDF 表格为文档问答依据；不预设与当前数据库一致） |
| 公式 | 05 指标核算公式（3 个 LaTeX 公式图） |
| 长文档 | 06 年度经营分析（24 行跨页续表） |
| **扫描件**（旋转+噪声+模糊） | 07 门店巡检、08 质检报告（运行时仅读取原扫描像素；附带对照不参与解析） |
| 规章/FAQ/手册/数据字典 | 04、11、09、10 |
| OHR-Bench 真实语料 | 14 份（academic/administration/finance/law/manual/news/textbook ×2，gt_text 摄取、noise_text 留作 OCR 鲁棒性评测） |

切片策略：标题树优先、正文 500-800 字符、表格独立切片保留 table_json、嵌入文本带标题路径前缀。

### 模块二：文档检索、引用与拒答

`knowledge.py` 使用词法排序与 BGE/Qdrant 向量检索，经 RRF 合并后返回最多 5 个切片。显式 OHR 样本编号与已知完整文档名限制检索范围；仅允许 `demo_kb`、`ohrbench_kb` 中已索引、公开、无需复核的文档，并核对文档版本与切片哈希。向量服务不可用时降级为词法检索并显示告警。

`rag_engine.py` 调用真实模型生成带引用的事实，检查引用来自候选证据、原文连续片段匹配，以及答案数字是否出现于引用或源标题；最多一次格式/引用修复。语义蕴含尚无独立验证，因此 Claim 标为 `warned`、可信级别为 `medium`。无足够证据返回 `ABSTAINED`，保存检索轨迹和候选来源。

请求支持 `mode: auto|sql|rag|industrial`；文档范围通过 `scope.document_collections` 指定。文档接口为 `GET /v1/documents`、`GET /v1/documents/{id}`、`GET /v1/documents/{id}/file`、`GET /v1/documents/{id}/pages/{page_no}` 和 `GET /v1/documents/{id}/facts`。引用带文件哈希、版本、页码、块 ID/坐标和原文；源文件发生改变时，文件、页面预览与字段返回 `SOURCE_VERSION_MISMATCH`。前端显示引用卡片、原文件/原页链接及候选字段定位，并支持 SSE 和运行回放。

**2026-10-01 实测**：26 文档、169 切片/向量；12 条固定题、模型 `deepseek-flash`、提示词 `rag-v1`、检索器 `hybrid-rrf-v2`，两轮严格端到端验收均为 **10/12**，可回答题 Recall@5 **11/11**、MRR@10 **0.955**。`rag-002` 数值/条件正确且引用可打开，但引用客服手册而非 gold 指定制度；`rag-006` 公式仍是图片，因缺少公式文本而拒答，计为失败。`rag-012` 缺少 2026 年预算证据，按 gold 正确拒答。报告见 `reports/rag.json`、`reports/rag_second.json`；首轮上游模型错误及检索失败保留于 `reports/rag_initial.json`，不从历史统计中删除。46 项 SQL/API/引用完整性回归通过；这些小样本成绩不代表复杂文档的通用准确率。

```bash
bash scripts/start_local.sh --background   # SQL + RAG + 工业演示，后台 API，保留现有数据
bash scripts/start_local.sh --sql-only     # 只初始化 SQL 所需服务
.venv/bin/python ingestion/ingest.py --refresh  # 解析器更新后刷新，不清空资产
.venv/bin/python db/seed_eval_cases.py --suite rag
.venv/bin/python scripts/eval_rag.py --retrieval-only
.venv/bin/python scripts/eval_rag.py        # 有失败时退出码为 1，仍保存逐题报告
```

打开 http://127.0.0.1:8000/，选择「演示文档知识库」后点击「退款条件」或「耳机续航」。若代码更新前已有 API 进程，请先停止原进程再启动；前端静态资源已加版本参数，刷新后可加载新版。

### 真实扫描 OCR 与公式图片解析（2026-10-02）

`ingestion/ocr.py` 使用固定 RapidOCR 3.9.2（PaddleOCR 的 ONNX 模型），对每页按原生文本量选择文本层或 240 DPI 原始像素 OCR；保留去倾斜变换及其逆变换、页坐标、低置信度候选与模型哈希。运行时禁止原生对照输入。扫描页按空间行聚合，并对深色表头加局部单元格恢复；未识别表头与结构告警一并进入引用；不再使用固定 0.85/0.95 作为 PDF 质量分。

`ingestion/formula_worker.py` 在独立 Python 环境加载 PP-FormulaNet_plus-M ONNX，输入仅为 PDF 原始嵌入图片。模型权重按固定 SHA256 校验；超时、不可用或不合法 LaTeX 留作解析缺口。`rapid-doc==0.9.10` 只使用公式 adapter，安装时 `--no-deps` 配合 `requirements-formula.txt`，不加载其 Office/LLM 根模块。主应用 Python 3.14 与 worker 的 NumPy/Python 要求相互隔离。

```bash
.venv/bin/python -m pip install -r requirements-ocr.txt
# 需要已有 Python 3.10–3.12；也可通过 --python 提供其完整可执行路径
.venv/bin/python scripts/setup_formula.py --python python3.12
.venv/bin/python scripts/eval_ocr.py --output reports/pdf_ocr.json
.venv/bin/python ingestion/ingest.py --refresh
.venv/bin/python scripts/eval_rag.py --output reports/rag_ocr.json
```

**OCR/公式阶段结果（上一轮）**：原图识别 5/5（6 行数字、3 个公式）；真实 RAG 11/12，Recall@5 11/11、MRR@10 0.955，详见 `reports/pdf_ocr.json`、`reports/rag_ocr.json`。退款制度来源与同比公式已通过；质检题 `rag-010` 因“不良率”表头未识别而拒答，仍计失败。54 项回归通过。OCR 行评测初轮的产品名空格匹配问题保留于 `reports/pdf_ocr_initial.json`，冻结标签不变；当前只忽略标签排版空白并要求数字同处空间行。

评测标签固定在 `data/evaluation/pdf_ocr_v1.json`：2 张演示扫描页的 6 行数字、3 个公式图。标签仅供评测比较，不传给识别器；原文件 SHA256 变化即停止评测。此评测不代表通用中文字符准确率或工业文件泛化。RAG 冻结事实/来源标签不变，`rag-eval-v2` 仅增加 LaTeX 百分号转义及单字母下标括号的展示等价匹配，报告记录评测器版本与代码哈希。初始小公式模型的乱码保留在 `reports/formula_initial.json`，未入库。

### 表格字段与原页定位（当前进度）

当前解析器 `pdf-ocr-0.4`。`ingestion/tables.py` 用深色表头区域与对齐的 OCR 行限定单页表格，局部裁剪后使用固定 PP-OCRv5 server 识别表头；不使用标签字典补写表头。仅支持此类结构，尚未覆盖续表、合并单元格或通用版式。低于 0.75 的候选保留，空白和不可读格仍标记未知。

`ingestion/facts.py` 将有明确实体/字段/值且可定位的单元格登记为候选字段：保留原文值、比例归一化、实体/表头/值三个坐标、文档版本、原文件/块/字段哈希。单位、实体编号、批次、有效期不从文件名或数字推测，未人工审批；当前已保存 **6 条门店得分候选**，质检页实体表头缺失，未生成产品字段。

数据库迁移 `db/migrations/001_document_facts.sql` 会应用到已有库；启动与摄取均调用幂等迁移。`GET /v1/documents/{id}/facts` 检查公开范围、当前版本、原文件/块/字段摘要和实际 PDF 页尺寸。引用卡片增加「查看识别字段」→「定位原页」，用三个框标出实体、表头和值，运行回放可恢复入口。

```bash
.venv/bin/python scripts/setup_ocr_models.py  # 固定权重 SHA 校验；约 81 MiB
.venv/bin/python db/migrate.py
.venv/bin/python ingestion/ingest.py --refresh
.venv/bin/python scripts/eval_document_fields.py  # 未通过会退出 1，仍保留完整报告
.venv/bin/python scripts/eval_ocr.py --output reports/pdf_tables_ocr.json
.venv/bin/python scripts/eval_rag.py --output reports/rag_fields.json
```

当前开发集结果：**表头 5/9、字段 3/15**（2 张演示扫描页；3 个冻结门店得分通过，12 个产品字段因表头不足缺失）；原图数字行/公式仍 **5/5**，回归 **64/64**。报告见 `reports/document_fields.json`、`reports/pdf_tables_ocr.json`、`reports/fields_regression.txt`。数据集参与研发，不能代表独立工业准确率。未恢复的产品字段保留在失败分母；另 3 条未冻结门店得分不计模型成绩。

本轮完整真实 RAG **10/12**，Recall@5 11/11、MRR@10 0.955：`rag-001` 上游报错，`rag-010` 表头缺失拒答；首轮见 `reports/rag_fields.json`。仅复测 `rag-001` 为 **1/1**（`reports/rag_fields_retry.json`），不合并为完整套件成绩。表头初试与未采用的 medium 模型输出见 `reports/header_initial.json`、`reports/header_medium_trial.json`。原页定位和回放验收见 `reports/fields_verification.json`、`reports/document_fields_locator.png`。

上述扫描识别缺口仍保留。后续工业契约与限定规则核验已落地，见下节；CAD、通用工业抽取与来源权威裁决尚未实现。

### 工业温度跨源核验（2026-10-02）

已完成限定的 **工艺原文→只读生产查询→质检原文→逐测量核验→证据与回放**。这是自建合成演示，文件中的审批只是模拟源声明，字段仍是候选；不代表真实工厂数据或人工审核。

- 数据契约：`data/industrial_demo/CONTRACT.md`；3 零件、12 批次、13 测量、13 条唯一测量对应的质检登记；17 页原生 PDF 提取 5 工艺范围和 12 报告值，保留实体、工序、批次、测量、方法、时间、单位及版本坐标。
- 规则：`industrial-rules-v1`，Decimal 摄氏/K 换算、审批与半开有效期、逐测量键/方法/时间核对。多版本重叠、未知单位、缺报告留待复核；真实值差异保留双方原值，没有权威规则时不选定来源。
- 入口：打开本机页面，点击「工业温度核验」，或选择「工业合成演示核验」并输入 **2026年9月A102工序30哪些批次温度超限？生产记录和质检报告是否一致？**。得到 9 批次/10 测量，B001、B006 超限，B001 报告数值冲突，5 条测量有复核/证据缺口。支持原页定位、双方重叠工艺版本和 Trace 回放。
- 实测：冻结规则开发集 **13/13**、完整回归 **74/74**，见 `reports/industrial_rules.json`、`reports/industrial_regression.txt`、`reports/industrial_verification.json`。工业查询及答案使用固定参数化 SQL/规则，不调用大模型，这些成绩不能记作 NL2SQL、OCR 或独立泛化准确率。
- 当前共 27 文档资产、23 条候选字段；原知识库仍为 169 切片/向量。工业 PDF 使用 `typed_facts_only`，没有新增向量/RAG 切片；旧扫描字段 3/15、表头 5/9 的失败边界保持不变。

```bash
bash scripts/start_local.sh --background
.venv/bin/python scripts/start_api_background.py  # 已运行则复用；日志 .cache/api/server.log
.venv/bin/python db/seed_industrial.py             # 幂等装载工业 SQL 与实际 PDF 字段
.venv/bin/python scripts/eval_industrial.py
```

启动脚本以 postgres 创建 `demo_industrial` schema 后运行增量迁移 `002_industrial_demo.sql`；全新容器由 `deploy/init/04_industrial_schema.sql` 创建。应用不拥有数据库 CREATE 权限；独立执行迁移/seed 前须先完成这一步初始化。工业数据源在 `datamind_meta` 内，查询使用只读账号，不影响原零售/Chinook 数据。

API 增加 `mode: industrial`，scope 为 `database_sources: ["demo_industrial"]`、`document_collections: ["industrial_demo"]`；`GET /v1/runs/{id}/conflicts` 返回持久化冲突，最终事件携带 `industrial_result`，每次运行保存 SQL 行快照和规则/源版本。

仓库已附嵌入字体的 PDF，运行解析无需 reportlab 或本机字体。重新编写 PDF 可运行 `scripts/build_industrial_docs.py`，需 reportlab 和可嵌入的 TrueType 字体；以 `DATAMIND_PDF_FONT` 指定字体路径。工业 Catalog/NL2SQL 已按下节接入；下一阶段优先独立工业字段样本和人工复核/权威规则，再推进 CAD。

### 工业生产模型问数（2026-10-02，限定 v1）

新增「工业生产问数（模型）」范围和「工业批次数」示例。选择后点「运行 Agent」，实际调用当前启用的模型生成 SQL，再经过 Catalog、AST/语义门禁、参数绑定、只读执行及持久化 Trace。与前节固定查询/规则核验分别展示。

- `industrial-catalog-v1` 仅允许 `demo_industrial.parts` 与 `measurement_observations`，实际位于 `datamind_meta`；2 个对象、14 列、1 条无扇出的维度关联、3 个指标。视图一行一条测量，不写回原值；批次数去重，均值按测量粒度，K 归一化为摄氏度，未知单位为 NULL。
- 支持明确生产年月/年份、单批次、单零件/工序、按零件分组、计数、温度均值/最大/最小、明细、未知单位和零件目录。当前限单层 SELECT；复杂 SQL、多实体、相对时间、阈值过滤及按批次均值另行扩展。批次数仅代表已有测量的批次。
- 时间、实体、工序、指标必须出现在 AND 条件中；阻止错误时间字段、OR 放宽、附加筛选、混单位聚合、批次重复计数、事实行重复 JOIN 和聚合改写。工艺超限/报告真值请求应选择跨源规则模式，强制 SQL 时在模型前澄清。契约见 `data/industrial_demo/QUERY_CONTRACT.md`。
- 当前小规模开发集真实模型执行 **8/8**（`deepseek-flash`、`industrial-sql-v1`），模型前澄清 **2/2**，完整回归 **82/82**。8 条查询包括 9 个已测量批次、10 条测量、按零件分组、均值 855.56°C、B006 双样本、未知单位和目录。样本参与开发，不能代表独立泛化或真实工厂准确率。
- 初始报告 `reports/industrial_nl2sql_initial.json` 为 7/8：B006 返回正确的 850/870，但 API Decimal 字符串与 JSON gold 数值比较不同。`industrial-sql-eval-v2` 保持标签、行列形状和单位不变，按数值比较；同一批输出复算为 8/8，保留全部 run ID 和初始失败，不做新模型调用或拼接重试成绩。正式报告 `reports/industrial_nl2sql.json` 记录两版哈希与当前门禁复核。

```bash
bash scripts/start_local.sh --background
.venv/bin/python db/build_industrial_catalog.py  # 幂等迁移/登记工业问数 Catalog
.venv/bin/python scripts/eval_industrial_sql.py # 真实模型评测，默认保存带时间的新报告
# 仅复算已有输出，不调用模型，必须使用不同的输出文件：
.venv/bin/python scripts/eval_industrial_sql.py --rescore reports/industrial_nl2sql_initial.json --output reports/industrial_nl2sql.json
```

API 使用 `mode: sql`、`scope.database_sources: ["demo_industrial"]`；符合范围的工业统计也可自动路由。`sql_result` 携带模型 SQL、绑定参数、Catalog 快照、问题帧和合成统计口径；回放恢复问数范围和口径。Claim 标为 warned/medium，完整答案语义尚未独立验证。页面/API/回放与后台服务记录见 `reports/industrial_query_verification.json`，完整回归见 `reports/industrial_query_regression.txt`。历史零售 NL2SQL、RAG、扫描字段和固定工业规则分母保留，本阶段没有重跑这些模型套件。

## 工业新模板字段首轮与人工复核（2026-10-02）

本轮完成两个阶段：冻结新模板解析测试，随后接入字段复核页面/API。当前 API **0.7.0**，后台服务仍在 `http://127.0.0.1:8000/`。

- 新材料：`output/pdf/industrial_forms_holdout_v1.pdf`，4 页、2 种自建质检模板，各含原生/真实扫描页；扫描页没有文本层。所有页面已渲染检查，56 个字段及区域标签在首轮解析前冻结。材料由实现者在 v1 解析器完成后编写，是后续合成模板测试，**不是独立工厂数据或盲测**。
- 首轮：字段 **28/56**，原生 **14/28**，扫描 **14/28**，完整事实 **2/4**。纵向全角冒号模板通过，紧凑半角冒号模板两种载体均未形成接受事实。原文诊断不计接受字段成绩；缺失项全部保留在分母，解析器未针对这批页调参，报告 `reports/industrial_forms_initial.json` 保留原样。
- 入口：左侧「字段复核」→「打开字段」→「定位原页」→填写复核人和依据→确认原文、标记存疑或退回字段。工业核验表也有「查看字段 / 人工复核」按钮；每个字段可看复核历史。
- 记录：迁移 `004_document_fact_reviews.sql`；只追加记录，绑定文件哈希、文档/解析器版本、字段载荷哈希及前一条记录。请求 ID 去重；版本变化或并发旧状态返回 409。没有 API 修改/删除原始字段或补齐缺失字段。
- 语义：复核针对提取忠实度；确认不改变原文审批声明、不自动裁定 SQL/PDF 真值冲突。最新存疑/退回字段从**后续工业字段规则核验**中排除，仍可在字段页查看；证据缺口不能视作来源一致。旧运行回放、生产 SQL、文档 RAG 保持原有语义。
- 当前为本机未认证演示，复核人是自行填写的标签，不代表核实身份或正式审批。写接口仅由本地应用使用；查询角色只有复核表 SELECT 权限。正式使用仍需身份认证、审批与权威规则。
- 完整回归 **89/89**，覆盖追加历史、原始字段/审批不变、重复/并发、旧版本隔离、退回后证据不足及既有功能。浏览器用隔离副本验证提交/历史/定位，随后移除副本；正式文档没有被代为复核。见 `reports/field_review_verification.json`、`reports/field_review_regression.txt`、`reports/field_review_browser.png`。

测试材料未进入运行知识库；当前仍为 27 文档、23 候选事实、169 切片/向量。工业模型 8/8 与模型前澄清 2/2 保留为上轮成绩；本轮没有新增真实模型评测或修改令牌额度。

```bash
# 初始报告禁止覆盖；后续复测必须指定新的路径
.venv/bin/python scripts/eval_industrial_forms.py --output reports/industrial_forms_followup.json
.venv/bin/python db/migrate.py
.venv/bin/python -m unittest discover -s tests -v
```

下一阶段：在单独的开发样本中支持紧凑格式，新增未参与修复的字段/定位测试；补授权真实模板、身份认证与来源权威规则，再推进 CAD。M6 泛化、正式审批和完整 P1 尚未验收。

## 六、快速开始

**数据集获取**（原始大文件不入库，Chinook SQL 已随仓库附带于 `db/chinook/`）：

```bash
# OHR-Bench（约 200MB，含 parquet；非必需，仅影响 OHR-Bench 抽样语料）
curl -L -O https://hf-mirror.com/datasets/opendatalab/OHR-Bench/resolve/main/OHR-Bench_v2.parquet
mkdir -p OHR-Bench && mv OHR-Bench_v2.parquet OHR-Bench/
```

```bash
# 1. 启动 Docker Desktop 后，初始化并运行应用（保留已有数据）
bash scripts/start_local.sh --background

# 2. 基础设施验证及应用回归
.venv/bin/python scripts/verify.py
.venv/bin/python -m unittest discover -s tests -v

# 3. 单独刷新知识库索引
.venv/bin/python ingestion/ingest.py --refresh
# scripts/reset_demo.sh 和 ingestion/ingest.py --reset 会清空相应数据，只用于显式重建

# 4. 只读账号连接问数（禁止任何写操作）
#    host=localhost port=5432 db=demo_retail|chinook user=readonly_user pwd=readonly_dev
```

## 七、环境依赖

- Docker Desktop；本次应用验收使用 Python 3.14
- SQL + RAG + 扫描 OCR：`.venv/bin/python -m pip install -r requirements-ocr.txt`；完整数据生成工具另见 `requirements.txt`
- 公式 ONNX worker：独立 Python 3.10–3.12 环境；安装入口 `scripts/setup_formula.py`，约 594 MB 权重缓存后离线运行
- Embedding 模型 BAAI/bge-small-zh-v1.5 经 hf-mirror.com 自动下载（约 100MB，本地缓存后离线可用）

## 八、已知边界与后续工作

1. **扫描件结构仍不完整**：已用真实 OCR 替代原生对照代理，保留旋转矩阵、低置信度候选和原页坐标；深色表头缺失，行聚合不等于完整表格恢复。模型置信度未校准，不能作为文档准确率。
2. **MinIO 缺位**：官方镜像已下架；如需 S3 语义可自备镜像恢复 compose 中注释段。
3. **公式块**：已有本地图片→LaTeX worker，结构检查失败或模型缺失时保留解析缺口；合格输出的数学语义仍未独立验证。公式检测只覆盖原生 PDF 中有限数量的横向嵌入图，复杂版面/扫描公式待扩展。
4. **应用层状态**：SQL 与文档单源 Trace/HTTP/SSE/引用/回放已实现；限定工业温度事实与规则核验已实现；完整 OCR 结构、CAD、通用跨源 V-CAR 与权威裁决待实现。知识库自动写回不列入首版目标。
