# DataMind Agent — 多模态数据驱动的可解释精准问数/问答智能体

赛题八（"中国电子杯"高校 ICT 产教融合创新大赛 · 中电云计算技术有限公司命题）项目仓库。

本仓库包含三大部分：

1. **`gateway/` — 大模型接入基座（可选）**：基于 New API 的模型网关，可继续用于渠道池、令牌配额与调用审计。应用默认直连上游 OpenAI 兼容接口——在 8000 页面登录管理员后于左侧「模型接入」填入接口地址、模型名和 API 密钥即可，无需启动该网关；若要经过网关，把接口地址填成 `http://localhost:4000/v1` 即可。详见 [gateway/README.md](gateway/README.md)。
2. **`deploy/` `db/` `ingestion/` `scripts/` `data/` — 基础数据库底座**（本 README 主体）：结构化业务库 + 非结构化知识库 + 元数据基础设施 + 评测集，全部可重置、可复现。
3. **`datamind/` `web/` — 应用层**：结构化问数与文档问答 Agent、HTTP/SSE 接口、引用定位和运行回放。

`赛题/` 和 `技术文档/` 为项目资料，不参与网关运行。

技术设计主入口已整理为 **[V1.3 整合版说明书](技术文档/DataMind_Agent_技术设计说明书_V1.3.md)**（0.28.0 基线）：依据[复杂文件分类与解析参考 MD](<技术文档/复杂非结构化文件分类与解析技术文档.md>)统一文件分类、解析/结构/定位契约、工业跨源核验、真实接口与分层验收。当前 SQL 问数、文档问答、真实扫描 OCR、公式图片解析和字段候选/定位已接入；限定工业温度规则核验、受控二维 DXF、认证关联复核与单条尺寸核验服务可运行。尺寸页面、受控文件队列与最多 8 份文件的显式 RAG 已完成；原生 PDF 已有离线准备和认证快照登记，已有私有知识发布与冻结计划恢复。私有 PDF 可显式多文件检索，并能在本次检索证据上做受控模型回答（原文连续片段/数字支持校验、生成后重查身份与证据）。私有 PDF 已可按冻结计划受控修复丢失点并回收孤立点；通用 V-CAR 依赖计划（并发、受影响子图局部修复、统一预算、断点恢复）已实现，节点可在独立进程组内按统一预算执行并经 `POST /v1/plans/execute` 受控调用（目标由服务端白名单决定）。PDF/OCR 解析队列、视觉载荷与机制消融、完整表格结构、通用 CAD/工业查询与广泛来源裁决仍待扩展。能力与历史分母见 [V1.3 §18](技术文档/DataMind_Agent_技术设计说明书_V1.3.md#18-实施状态历史成绩与修订记录)；历史文档/OCR/工业评测与各阶段边界见 [V1.2 历史说明书](技术文档/DataMind_Agent_技术设计说明书_V1.2.md#18-实施进度与下一步验收2026-年-10-月-2-日) §18.12–18.33。

## 当前进度（2026-10-07，代码 API 0.28.0）

[进度总览与下一阶段验收](PROGRESS.md) 汇总当前状态；详细实现、历史错误与分母保留在 [V1.2 历史说明书](技术文档/DataMind_Agent_技术设计说明书_V1.2.md#18-实施进度与下一步验收2026-年-10-月-2-日) §18.12–18.33 与 [V1.3 §18](技术文档/DataMind_Agent_技术设计说明书_V1.3.md#18-实施状态历史成绩与修订记录)。本轮私有 PDF 检索的 profile/并发专项 14/14、联合向量 capture 8/8、HTTP 32/32，完整隔离回归 **465/465**；独立认证 Qdrant 与本地真实 BGE 的两文件/历史版本场景通过，原库计数与旧模块未改。上一知识发布 411/411、认证登记 338/338、准备 292/292 保留；私有知识发布提交 [`6913ccc`](https://github.com/cyc120/datamind-agent/commit/6913ccc9a459b7bd91a72f72ec642d56e92d8a24) 已随 PR #1/#2 并入 `main`。受控私有模型回答新增 15 项组件/契约测试（**480/480**），本轮统一任务预算与进程时限新增 22 项、完整隔离回归 **502/502**：挂钟 deadline 终止进程组（含孙进程）、跨进程资源槽、取消传播、输出上限与稳定超时码。扫描页 OCR 与公式会话已接入同一预算：受控路径与进程内路径的 OCR 结果一致，真实 1 秒时限对约 1.8 秒的扫描页返回 `OCR_TIMEOUT`。

| 阶段 | 已验证结果 | 当前缺口 |
|---|---|---|
| 工业生产模型问数 | 8/8 开发题、2/2 模型前澄清 | 限定单层查询；复杂 SQL、独立泛化未验收 |
| 工业跨源规则 | 13/13 开发规则；单位/版本/超限/冲突可解释 | 显式来源权威规则已限定实现；真实工厂依据、广泛规则与 CAD 待扩展 |
| 新模板字段历史首轮 | 28/56 字段、2/4 完整事实；原生/扫描各 14/28 | 原报告与旧解析器保留，分母不与 v2 混合 |
| 紧凑格式 v2 新样本首轮 | 104/104 字段、8/8 完整事实；原生/扫描各 52/52 | 同一作者的合成材料，四份表单及扫描变体，真实工厂泛化未验收 |
| 字段复核/独立审批 | 登录身份、角色权限、原页定位、版本审计、另一账户批准/不批准/撤销、历史与幂等 | 本地账户；授权真实模板、缺失字段补录与广泛来源裁决未实现 |
| 来源权威规则 | 精确测量/方法/版本/有效期、依据页、独立启用/撤销、冲突与运行快照 | 只做合成温度机制验证，依据授权语义由人核对 |
| CAD 资产与查看 | 认证登记、固定源副本、不可覆盖历史、实体/特征/预览；新版后旧版响应哈希不变 | 限定 DXF；正式图纸审批与通用 CAD 待扩展 |
| CAD 认证关联复核 | 确认/存疑/退回、候选圆、版本/哈希/前序、历史和换版不继承 | 独立人工记录；真实关联正确性与业务授权需人核对 |
| 尺寸契约、认证服务与页面 | 冻结契约、版本化测量、显式绑定、独立批准/撤销、当前门禁、核验快照与分页回放；`/#dimensions` | 本地单条尺寸比较；不接真实 ERP，不构成整批质量结论 |
| 多文件登记队列 | DXF/单测量 JSON 上传，逐文件状态、租约、并发限额、取消、失败项重试；`/#files` | 需独立 CLI worker；PDF/OCR/Office 不在当前队列 |
| 显式多文档问答 | 最多 8 份已索引公开文件，版本/源/解析器绑定、逐文件检索覆盖和实际引用覆盖 | 相关片段覆盖不等于全文审阅；语义蕴含未独立验证 |
| 原生 PDF 离线准备 | 固定源 SHA、独立 attempt、版本化块/切片、旋转/裁剪定位、资源门禁与封存验证；[使用说明](docs/PDF_PREPARATION.md) | 独立进程准备；离线输出不可直接用于问答，OCR/队列未接入 |
| PDF 私有快照登记 | admin 认证、当前版本 CAS、原请求恢复、固定版本原文件/页图/bundle、读取完成后权限重验；[使用说明](docs/PDF_SNAPSHOT_REGISTRY.md) | `registration_only=true`、不索引/不进入知识库；无上传或操作页面，不裁定来源权威 |
| PDF 私有知识版本 | 持久 outbox、冻结向量计划、独立认证向量存储、逐点确认、CAS/租约恢复、历史切片对账；[使用说明](docs/PDF_KNOWLEDGE_PUBLICATION.md) | 私有切片 API；无已发布点修复/孤立回收或硬资源隔离 |
| PDF 私有模型回答 | 只用本次选定私有片段生成；原文连续片段与数字支持校验、最多一次修复、文件未覆盖即拒答、生成后重查身份与证据；[说明](docs/PDF_PRIVATE_SEARCH.md) | 15 项组件/契约测试；真实模型忠实度未评测 |
| 统一任务预算与进程时限 | 跨进程资源槽、挂钟 deadline、进程组终止（含孙进程）、取消传播、输出上限与稳定超时码；登记 worker / PDF 准备 CLI / 扫描页 OCR / 公式会话可选接入；[说明](docs/TASK_BUDGET.md) | 34 项组件/契约测试；非 CPU/内存 cgroup 硬隔离；跨主机租约待接入 |
| PDF 私有多文件检索 | 1–4 份显式发布/完整计划 SHA、匹配本地 embedding profile、完整点核对、逐文件覆盖、两个跨进程检索槽；[使用说明](docs/PDF_PRIVATE_SEARCH.md) | 返回证据；未生成答案；实验阈值未标定，槽不等于统一 CPU/内存硬隔离 |
| 组件/集成回归 | 本轮隔离回归 465/465（411 + 14 + 8 + 32）；真实本地 BGE/认证存储场景另列 | 不代表模型或真实工厂识别准确率 |

尺寸认证服务已经接入操作页面；受控文件登记已有持久任务队列，多文档问答可显式选择与校验。不同文件独立成功或失败，同一资产仍按预期前版提交，队列不会自动配对测量/图纸或代为批准。原生 PDF 可准备、认证登记并发布独立私有知识版本；向量确认前不推进头，重试复用冻结计划。已发布 PDF 可以显式多文件检索和受控模型回答，查询期间源/计划/实际点变化拒绝返回，历史选择保持固定。统一任务预算已提供挂钟 deadline、进程组终止与跨进程资源槽，登记 worker、PDF 准备 CLI、扫描页 OCR 与公式会话均已接入；通用 V-CAR 依赖计划（并发、受影响子图局部修复、统一预算、断点恢复）已实现，节点可在独立进程组内按统一预算执行并经 `POST /v1/plans/execute` 受控调用（目标由服务端白名单决定）。**现阶段以技术实现为主**，按 [V1.3 §15](技术文档/DataMind_Agent_技术设计说明书_V1.3.md) 顺序推进：统一解析契约（§5.2/§5.4）已接入生产解析出口、具备媒体类型探测与 Adapter 路由，并把校验过的 locator 写入 `document_block_locators`（`parse_pdf` 经 `dispatch` 产出并校验契约），CPU 限额/跨主机租约（§13）已完成原语层；先做按 media type 的 Adapter 路由与契约存储列、Office 适配，再做真实视觉载荷与模型规模对比、统一 CPU/内存配额与跨主机租约；**赛题交付包等前置技术任务完成后再做**。历史扫描字段 3/15、RAG 10/12 及单题复测 1/1 保持各自分母。

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
.venv/bin/python scripts/test_isolated.py
.venv/bin/python scripts/eval_sql.py --output reports/nl2sql.json
.venv/bin/python scripts/eval_sql.py --suite robustness --output reports/robustness.json
.venv/bin/python scripts/eval_schema.py --output reports/schema_linking.json
.venv/bin/uvicorn datamind.api:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

登录管理员后，模型接入在页面左侧「模型接入」中配置并即时生效，支持**多套接入配置**（ccswitch 风格）：每套配置可独立选择协议、地址、模型、密钥、鉴权方式，一键切换当前启用，支持导出/导入（默认不含密钥）。**协议支持 OpenAI 兼容（`/chat/completions`）与 Anthropic Messages（`/v1/messages`）**两套；Base URL 会自动补版本段（`/v1`、`/api/paas/v4`、`/api/v3` 都能直接填）。预设 DeepSeek / Anthropic 官方 / Claude 中转 / 阿里云百炼 / 智谱 / 月之暗面 / 硅基流动 / 火山方舟 / 本地网关 / 自定义，填入 API 密钥后可「拉取模型列表」验证密钥、选模型、「测试连接」验证可对话。密钥存放于 `datamind_meta.app_settings`，接口只回显掩码，`readonly_user` 对该表无任何权限；不再读取任何本地密令文件。同名环境变量 `NEW_API_BASE_URL`、`NEW_API_MODEL`、`NEW_API_KEY` 仍可作为兜底（优先级低于页面配置）。**本机地址（localhost/127.0.0.1）始终直连**——httpx 会经 urllib 读取 macOS 系统代理，若不绕开会把本地网关请求塞进代理导致挂起或 502；远端地址继续尊重系统/环境代理，也可在配置里用「代理」字段显式指定。问数数据库只用 `readonly_user`；可通过 `DATAMIND_META_DSN`、`DATAMIND_META_WRITE_DSN`、`DATAMIND_DB_HOST`、`DATAMIND_DB_PORT`、`DATAMIND_QUERY_USER`、`DATAMIND_QUERY_PASSWORD`、`DATAMIND_SQL_TIMEOUT` 配置。失败时返回可读错误码：`MODEL_KEY_MISSING`（未配置密钥）、`MODEL_AUTH_FAILED`（密钥被拒）、`MODEL_BALANCE_EMPTY`（上游额度不足）、`MODEL_UNREACHABLE`、`MODEL_TIMEOUT` 等；评测脚本逐例记录结果，不把失败计为正确。

本地 Agent 提供 `POST /v1/chat`、`POST /v1/chat/stream`（SSE）、`GET /v1/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events?after=...`、`GET /v1/runs/{id}/evidence`、`GET|PUT|DELETE /v1/model/config`（兼容单配置）、`GET|POST /v1/model/profiles`、`PUT|DELETE /v1/model/profiles/{id}`、`POST /v1/model/profiles/{id}/activate`、`GET /v1/model/profiles/export`、`POST /v1/model/profiles/import`、`POST /v1/model/config/models`、`POST /v1/model/config/test` 和 `/health`。页面入口包括问数与问答、模型接入、运行记录、字段复核、来源权威规则、图纸与实体、尺寸核验和多文件任务，可直接深链 `/#model`、`/#runs`、`/#fields`、`/#authority`、`/#cad`、`/#dimensions`、`/#files`。运行、事件、SQL/文档证据和 Claim 写入 `datamind_meta`，可按 run ID 回放。服务面向本机演示，默认绑定 `127.0.0.1`；复核、审批与模型管理已接入本地账户权限，公共问数/文档读取仍面向本机演示。工业温度问题可进入限定跨源规则工具；零售等通用数据库与文档联合推理仍返回 `ROUTE_UNSUPPORTED`。

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
| DocumentFactReview / Approval | `document_fact_reviews` / `document_fact_approvals` | 版本绑定的认证提取复核、另一账户的独立审批、原始快照与请求审计 |
| SourceAuthorityRule / Event | `source_authority_rules` / `source_authority_events` | 精确来源快照/依据页的不可变登记与独立启用/撤销事件 |
| LocalIdentity / Session | `auth_users` / `auth_sessions` / `auth_login_limits` | 本地账户、密码哈希、限时会话/CSRF、登录限流；问数只读角色无权限 |

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

`knowledge.py` 使用词法排序与 BGE/Qdrant 向量检索，经 RRF 合并后默认返回最多 5 个切片，显式多文件模式最多 8 个并为每份选中文件保留相关片段。未显式选择时，OHR 样本编号与已知完整文档名可以限制检索范围；显式选择优先以完整版本引用为准。仅允许 `demo_kb`、`ohrbench_kb`、`industrial_demo` 中已索引、公开、无需复核的文档，并核对文档版本与切片哈希。向量服务不可用时降级为词法检索并显示告警。

`rag_engine.py` 调用真实模型生成带引用的事实，检查引用来自候选证据、原文连续片段匹配，以及答案数字是否出现于引用或源标题；最多一次格式/引用修复。语义蕴含尚无独立验证，因此 Claim 标为 `warned`、可信级别为 `medium`。无足够证据返回 `ABSTAINED`，保存检索轨迹和候选来源。

请求支持 `mode: auto|sql|rag|industrial`；文档范围通过 `scope.document_collections` 指定。API 0.14.0 增加 `scope.document_refs`，每份引用必须包含 `document_id/document_version/source_sha256/parser_version`，最多 8 份无重复公开文档。所选版本或访问状态不符、任一文件缺相关证据均阻断；生成完成前再次检查文件头与有效切片集合，不回退全库。`retrieval.coverage` 与 `answer_coverage` 分别报告检索片段覆盖、实际引用/未引用文件。文档接口为 `GET /v1/documents`、`GET /v1/documents/{id}`、`GET /v1/documents/{id}/file`、`GET /v1/documents/{id}/pages/{page_no}` 和 `GET /v1/documents/{id}/facts`。引用带文件哈希、版本、页码、块 ID/坐标和原文；源文件发生改变时，文件、页面预览与字段返回 `SOURCE_VERSION_MISMATCH`。前端显示引用卡片、原文件/原页链接及候选字段定位，并支持 SSE 和运行回放。

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

## 工业新模板字段首轮与人工复核（2026-10-02，历史阶段）

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
.venv/bin/python scripts/eval_industrial_forms.py --suite data/evaluation/industrial_forms_acceptance_v2.json --output reports/industrial_forms_followup.json
.venv/bin/python db/migrate.py
.venv/bin/python scripts/test_isolated.py
```

下一阶段：在单独的开发样本中支持紧凑格式，新增未参与修复的字段/定位测试；补授权真实模板、身份认证与来源权威规则，再推进 CAD。M6 泛化、正式审批和完整 P1 尚未验收。

## 紧凑工业格式与新样本验收（2026-10-04，历史阶段）

`industrial-labelled-v2` 支持单页单表单的全角/半角冒号及标签内空格，不修改原值或时间中的冒号。每个必需字段都绑定原文块、页坐标、内容哈希；重复/缺失字段、多个实体混排、跨页补上下文、缺定位均拒绝，未知单位或无效温度/日期保留为存疑候选。旧 `industrial-native-v1` 的字段读取与复核仍兼容，运行版本来自实际证据。

| 材料 | 原生字段 / 完整事实 | 扫描字段 / 完整事实 | 总计 |
|---|---|---|---|
| 单独开发材料（4 页） | 26/26、2/2 | 26/26、2/2 | 52/52、4/4 |
| 冻结后新样本首轮（8 页） | 52/52、4/4 | 52/52、4/4 | 104/104、8/8 |

新样本包括工艺要求/质检报告、degC/K、未批准声明、不同字段顺序/页边距/字号和扫描旋转，解析器只读取 PDF 原文或扫描像素。四份表单与其扫描变体相关，作者也参与实现，部分数值与开发材料相同；这些不是独立工厂盲测。字段通过要求文本一致、IoU≥0.5、实体/工序/测量绑定及有效单位；完整事实要求全部字段通过，并核对标准化值/范围、批次/报告、版本和有效期。新样本运行后没有修复解析器或改标签。

材料为 `output/pdf/industrial_forms_development_v2.pdf` 与 `output/pdf/industrial_forms_acceptance_v2.pdf`，对应冻结标签位于 `data/evaluation/`。首轮报告 `reports/industrial_forms_acceptance_v2_initial.json`、开发报告 `reports/industrial_forms_development_v2.json` 与验证 manifest 分开保存；旧 v1 的 PDF、标签和 28/56 报告保持不变，旧解析器快照及 Git 基线见 `data/evaluation/frozen/industrial_native_v1/`。

全部 **99/99** 回归通过。`scripts/test_isolated.py` 复制元数据库、排除模型设置、改写副本路径、重新提取旧工业 PDF 为 v2，再测试字段读取/复核和既有流程；结束时删除自建测试库。浏览器验证了新版实体/工序/审批声明、原页定位与窄窗口表格滚动。原 8000 服务及原库仍为上一阶段状态；本次代码 API 为 0.8.0。合并升级时需在目标检出重新提取工业原文并重启，旧版复核决定不会自动继承。

```bash
.venv/bin/python scripts/test_isolated.py
# 历史验收复现：先在独立目录按 manifest 恢复 runtime_snapshots；当前字段读取代码已升级
.venv/bin/python scripts/eval_industrial_forms.py --suite data/evaluation/industrial_forms_acceptance_v2.json --output reports/industrial_forms_v2_another_run.json
# 仅开发材料允许显式使用当前解析器；验收材料始终核对冻结哈希
.venv/bin/python scripts/eval_industrial_forms.py --suite data/evaluation/industrial_forms_development_v2.json --current-parser --output reports/industrial_forms_v2_development_another_run.json
```

下一阶段优先实现复核身份认证/权限、独立的正式审批及有依据的来源权威规则；同时扩充授权真实工业模板。多字段同一文本块、完整工业表格结构、CAD 和通用跨源计划仍待实现。

## 本地账户与独立字段审批（2026-10-05，API 0.9 阶段）

代码 API **0.9.0**。无匿名注册、默认账户或默认密码；账户由有数据库管理权限的本地操作者交互创建。先应用 `db/migrations/005_identity_and_approvals.sql`（启动脚本会调用迁移），再创建账户：

> **本机免登录（默认）**：服务只绑定 `127.0.0.1`，来自本机的请求会自动以已登记的 `local-admin`（管理员）身份进入，页面与接口都不再要求密码，`/#cad`、`/#dimensions`、`/#files`、`/#authority` 可直接打开。CSRF/同源检查与角色门禁代码仍在；设 `DATAMIND_LOCAL_NO_LOGIN=0` 可恢复强制登录，`=1` 可对非本机客户端也启用。只有需要区分身份（例如“另一账户审批”）时，才用下面的命令创建真实账户并关闭该开关。

```bash
.venv/bin/python db/migrate.py
# 每条 create 会交互输入两次密码（12–256 字符）；不要把密码写入命令行或仓库
.venv/bin/python scripts/manage_users.py create reviewer1 --name 复核员 --role reviewer
.venv/bin/python scripts/manage_users.py create approver1 --name 审批员 --role approver
.venv/bin/python scripts/manage_users.py create admin1 --name 管理员 --role admin
# 停用账户并撤销其全部会话
.venv/bin/python scripts/manage_users.py disable reviewer1
```

迁移和账户管理支持 `DATAMIND_META_WRITE_DSN`；迁移另兼容旧 `DATAMIND_WRITE_DSN`。页面左侧登录后，reviewer/admin 可确认提取，approver/admin 可批准、不批准或撤销；**同一账户不能审批自己的复核，admin 也不例外**。所有 `/v1/model/*` 接口只对 admin 开放，写操作还需 CSRF。viewer 可登录浏览；公共问数/文档仍是本机演示，不等于完整多租户 ACL。

认证使用带随机盐的 scrypt 密码哈希、8 小时 HttpOnly/SameSite Strict cookie 会话（HTTPS 时 Secure）、服务端会话凭据哈希和 CSRF/同源检查。前端不把会话或 CSRF 写入 localStorage。账户停用、会话过期/退出、角色变化在后续请求重新核查；复核/审批写事务内再次锁定并检查身份。连续失败按账户与客户端在 15 分钟窗口内限流；这些机制尚未替代 SSO/MFA 或外部实名核验。

`GET /v1/auth/me`、`POST /v1/auth/login|logout` 管理会话；`GET/POST /v1/documents/{document_id}/facts/{fact_id}/approvals` 保存独立审批历史。审批必须绑定最新的已认证“确认原文”记录、文件/解析器/字段版本及哈希，保存另一审批账户和原因。旧未认证记录不能被补认身份或直接批准。接口只追加记录，数据库所有者仍有管理权限，不能声称防篡改。

新复核使旧审批失效；已进入审批的当前字段版本若未批准、撤销或审批失效，就不能进入新的工业字段核验。重新确认原文不能绕过这个门禁，需要另一账户再次批准。尚无审批记录的版本保留原有演示资格，不可把全部演示事实称为已审批。这个过滤只作用于工业字段核验，不作用于生产 SQL、文档 RAG 或历史运行快照；本地批准也不覆盖源文件审批声明、不使单位/证据校验失效、不自动解决来源冲突。

完整组件/集成回归 **112/112**，见 `reports/identity_regression.txt`。新增 13 项覆盖身份伪造/权限、CSRF/同源、会话/停用/角色变化、密码与只读权限、限流、独立审批、版本/幂等/并发、审批失效和重新复核绕过。真实页面在隔离副本验证确认→独立批准→历史；批准后 B001 冲突仍为未消解，见 `reports/identity_approval_verification.json`、`reports/identity_approval_browser.png`。测试不复制模型设置或账户/会话；副本、进程与临时凭据均已清理，原 8000 服务和原库没有升级，本阶段无模型调用。

旧紧凑格式 104/104、8/8 和首轮失败报告原样保留。审批功能改变了冻结范围内的 `field_store.py`；历史 v2 复现须在独立目录依据 `data/evaluation/frozen/industrial_labelled_v2/manifest.json` 的 `runtime_snapshots` 恢复文件后运行，输出到新报告路径。直接使用当前检出会触发哈希保护；不绕过验收冻结或覆盖旧报告。下一步登记来源权威规则与依据，保持无规则冲突未消解，再扩充授权真实模板和 CAD。

## 显式来源权威规则（2026-10-05，API 0.10.0）

入口为 `/#authority`。仅针对现有工业温度核验中的可比较数值冲突：管理员读取冲突测量，选择采用生产记录或质检报告，填写覆盖生产时间的半开有效期、公开 PDF 的版本/依据页与裁定理由；另一 approver/admin 账户批准后生效。登记人不能审批自己的规则，字段报告须先通过认证提取复核和独立字段审批。没有预置权威、来源名优先级或默认选中来源。

迁移 `006_source_authority.sql` 由启动/迁移脚本应用；无修改或删除登记记录的 API。`GET /v1/authority/candidates?question=...` 和 `POST /v1/authority/rules` 仅管理员，`POST /v1/authority/rules/{id}/events` 仅 approver/admin；所有变更需登录/CSRF，同一登记/审批账户被拒绝。已登录账户可读 `GET /v1/authority/rules` 及事件历史。每次登记绑定具体零件/批次/测量/工序/指标/方法、数据集哈希、生产行哈希、报告文件/解析器/字段载荷/复核/审批 ID 和依据文件版本/哈希/页码。有效期应用于生产时间，后续查询可以核验历史测量。

运行时再次检查精确范围、版本与哈希、字段审批、依据公开状态和实际文件、有效期、登记载荷及独立批准。待审批、不批准、撤销、无有效规则保持冲突未消解；多条适用规则重叠时拒绝自动选优先级，证据变化后规则失效；已批准登记的完整性校验失败时阻止自动数值裁决。字段失效仍是证据不足，规则不能越过单位、版本、测量对齐、原文审批或工艺证据门禁。

成功裁决状态为 `resolved_by_authority`，保留两方原值，另记录 `selected_fact_ref`、标准化采用值和规则/批准事件/依据快照；原有工艺范围核验仍针对生产原值。例：合成 B001 的生产记录 **870 degC** 与报告 **850 degC** 冲突，模拟采用报告后仍显示生产记录“超限”，来源列显示“按登记规则裁决”，不能称两方一致或整体合格。`AuthorityRule` 证据节点关联核验和冲突，历史运行保存当时快照；后续撤销不改旧答案、规则依据或冲突记录。

**126/126** 完整组件/集成回归，其中新增 14 项；报告为 `reports/source_authority_regression.txt`。首轮新增测试 1 失败/2 错误涉及夹具原值恢复、ACL 响应及已排除字段的候选预期，保留于 `reports/source_authority_targeted_initial.txt`，修正夹具后 14/14。真实页面在隔离副本完成登记→另一账户启用→查询→撤销→历史核对，见 `reports/source_authority_verification.json` 及页面截图。测试库不复制模型设置、账户/会话/登录限流或权威规则，临时库和凭据随后清理；原库未升级，本次无模型调用。

这是**本地登记与审批机制**，没有自动证明所引用文本在业务或法律上授予权威。测试引用合成页并明确声明模拟授权；真实依据需人核对和授权提供，不能将成绩称为工厂裁决准确率。仅覆盖精确温度测量，没有通用优先级、跨实体复用、CAD 或多租户访问控制；数据库所有者仍可管理记录，并非防篡改系统。下一阶段先冻结 CAD 实体/几何/版本及业务关联契约，另设可复现基准；真实模板和权威依据仍需扩充。

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
.venv/bin/python scripts/test_isolated.py

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
4. **应用层状态**：SQL 与文档 Trace/HTTP/SSE/引用/回放、显式多文件 RAG、限定工业温度核验、受控 DXF/认证尺寸服务与多文件登记队列已实现；通用 PDF/OCR 并发、完整 OCR 结构、通用 CAD、跨源 V-CAR 与广泛来源裁决待实现。知识库自动写回不列入首版目标。


### CAD 契约与业务关联门禁（2026-10-05）

`ingestion/cad_contract.py` 实现严格输出契约 `cad-contract-v1`，见 [CAD 契约](data/cad_demo/CONTRACT.md)。源哈希、文档版本、解析版本、图号/图纸版次/零件分开；限定二维 Model/WCS_XY、LINE/CIRCLE/TEXT、源单位 mm/in/unknown 与选定直径/对称公差。错版、失效定位、未知单位、多对象及标注/几何矛盾阻止使用；没有明确对象关联依据不输出制造范围。

```bash
.venv/bin/python -m unittest discover -s tests -p test_cad_contract.py -v
.venv/bin/python scripts/verify_cad_contract.py
```

本轮 **12 项 CAD 契约测试，完整隔离回归 138/138**；冻结首轮证据 `reports/cad_contract_initial.json`、回归 `reports/cad_contract_regression.txt`。材料为一张人工合成 DXF 和手工适配输出/作者关联标签，CLI 没有读取 DXF，也未证明提取忠实度；成绩仅属于契约/门禁。API 保持 0.10.0，未接图纸 API/摄取、DWG 转换、图纸渲染或生产尺寸核验。下一步完成真实 DXF 读取/定位渲染，再冻结分离的开发与新图纸验收；真实授权材料、通用计划和完整 M6/P1 仍待验证。


### 原生 DXF 读取与离线实体预览（2026-10-05）

新增 `ingestion/cad_dxf.py`、`cad_render.py` 和 `scripts/parse_cad.py`，实际读取源文件的标题文字、几何/句柄、单位与明确对象引用，生成兼容冻结 CAD 契约的 JSON、SVG、可点击原文/关联的离线 HTML。范围与复现见 [DXF profile](data/cad_demo/DXF_PROFILE.md)。

```bash
.venv/bin/python -m pip install -r requirements-cad.txt
.venv/bin/python scripts/parse_cad.py data/cad_demo/development/dev-mm.dxf --document-id doc-dev-mm --provenance synthetic_demo --output-dir tmp/cad-new-preview
.venv/bin/python scripts/eval_cad_dxf.py --suite data/evaluation/cad_dxf_acceptance_v1.json --output reports/cad_dxf_reproduction.json
```

本轮 **15 项专项/153 项隔离回归通过**。先完成两份开发图，冻结读取器/渲染/评测器后生成六份新图：2 可用、3 多候选/几何矛盾/未知单位保留或排除、1 不支持图元拒绝，首轮 6/6 场景符合预期。标题 15/15、已知尺寸字段 16/16、原生实体/句柄/坐标 35/35；作者自建同 profile 材料，不是工厂独立 CAD 准确率。

仅支持明确 DM_* 标题/直径图层及 DATAMIND XDATA 引用的二维 LINE/CIRCLE/TEXT；不按位置猜标题或最近对象。文字字体/边界近似；旋转/三维/OCS/块/原生 DIMENSION/ARC、纸空间、未知字体样式、DWG/图纸扫描等明确拒绝。未接图纸摄取/持久化/API/生产尺寸问数，API 保持 0.10.0。下一步接图纸源版本与查看接口，通用图纸理解与真实授权材料另验收。


### 图纸资产、源版本与认证查看（2026-10-05，API 0.11.0）

新增 `007_cad_assets.sql`、`datamind/cad_store.py` 及 `/#cad` 页面，工作流见 [资产登记与查看](data/cad_demo/ASSET_WORKFLOW.md)。所有图纸读取需登录；仅 admin 可以查看本地素材列表及登记。管理员将合成材料放到 `data/cad_demo/development/`，经授权的外部材料放到 `data/cad_demo/authorized/`，页面选择文件、填写资产 ID/显示名并保存。需要可选 CAD 依赖、既有账户及增量迁移，没有默认密码；当前尚未升级原检出或原业务库。

登记核对选定源 SHA256、当前文档版本和源 SHA；保存原始字节副本与解析快照，追加版本并事务更新当前指针。新版本必须保持图号/零件身份；图纸版次可变。请求 ID 与认证登记人绑定，同请求重试返回原版本；过期前序/源变化/不支持类型拒绝。历史读取再次核对源/快照哈希和版本；业务登记不等同图纸审批。

| 接口 | 用途 |
|---|---|
| `GET /v1/drawings/sources` | admin 查看本地素材与哈希 |
| `GET /v1/drawings` / `POST /v1/drawings` | 已登记资产 / admin 追加源版本 |
| `GET /v1/drawings/{id}/versions` | 当前指针与全部版本 |
| `GET /v1/drawings/{id}` | 图纸身份/源/解析元数据 |
| `GET /v1/drawings/{id}/entities` / `features` | 实体定位与尺寸候选/提取一致性门禁 |
| `GET /v1/drawings/{id}/file` / `preview` / `viewer` | 原 DXF / SVG / 沙箱交互 HTML |

除历史列表外，单图读取可指定 `?version=1`；省略版本读取当前。页面源下载和 iframe 均固定选中版本。新增 14 项测试、完整隔离回归 **167/167**；页面验证 v1（R2、20±0.1）→v2（R3、22±0.2）→v1，旧源/预览/字段响应哈希不变。既有 11 文件契约及 8 文件读取器冻结范围保持不变，六份合成 DXF 复现通过。

源副本为忽略的运行数据，需要与数据库一起备份；登记的 provenance 是本地材料声明，不能自动证明外部授权。接口追加历史，但文件系统/数据库管理员仍能管理数据；不是防篡改存储。SQL 事务失败可能留下未引用的内容副本，当前没有回收器。CAD 资产未进入 RAG、生产事实或质量问数，没有认证关联复核、正式审批或生产尺寸判定。


### CAD 认证关联复核（2026-10-05，API 0.12.0）

新增 `008_cad_association_reviews.sql`、`datamind/cad_reviews.py` 和尺寸复核卡片，完整流程见 [认证关联复核](data/cad_demo/ASSOCIATION_REVIEW_WORKFLOW.md)。reviewer/admin 可对当前版本确认、保留存疑或退回关联，确认需明确选择已列出的候选圆并填写理由；viewer/单独 approver 角色只读。原始尺寸表、实体与预览继续显示提取结果，认证决定独立追加，换版不自动继承。

特征响应保留 `features/inspection_gate`，新增 `association_reviews/review_gate`；人工确认的候选仍检查值、单位和几何，不能覆盖矛盾或未知值。`GET/POST /v1/drawings/{id}/features/{feature_id}/reviews` 提供认证记录与历史。写入绑定当前文档/源/解析/bundle/feature 哈希及最新前序，CSRF/同源、事务内身份、幂等/并发均校验；历史版本不新增决定。

新增 **12/12 首轮专项**，完整隔离回归 **179/179**；页面对合成无引用图先存疑，再模拟指定圆 38 确认。原文件/预览/实体哈希不变；登记 v2 后原 v1 特征与复核响应不变，v2 为未复核，旧版可只读回放两条历史。隔离库和凭据清理、原库计数一致；冻结契约/读取器及六份旧合成图兼容复现通过。这是本地认证/关联机制验证，人工理由不自动证明真实图纸关系、业务权威或制造授权。该 API 0.12 阶段当时未提交/推送或升级原检出；其后 API 0.13 接入显式尺寸绑定与独立使用批准服务，API 0.14 完成操作页面。


### 尺寸测量、显式绑定和独立使用批准契约（2026-10-06，API 保持 0.12.0）

新增 `ingestion/cad_dimension_contract.py`、Schema、冻结合成尺寸源和 `scripts/verify_cad_dimension_contract.py`，说明见 [尺寸绑定契约](data/cad_demo/DIMENSION_BINDING_CONTRACT.md)。现有生产演示为温度测量，本阶段没有把它解释成直径，也没有写入生产库。图纸版次与工艺版次分开，绑定必须明确测量/批次/工序/方法/尺寸角色及源/载荷哈希，并绑定当前图纸、特征、目标、复核和独立使用批准声明。

契约检查当前登记头、完整版本/源、半开测量有效期、带时区时刻、角色声明及批准人独立于登记/复核；无批准、不批准、撤销、过期或未来批准不比较，保留历史时刻回放。确认关联不能绕过单位未知或几何矛盾。只有所有门禁通过，才按精确 Decimal/25.4 比较标注上下界和单条测量，保留原值/单位；不产生整体产品质量结论。

**纯函数不执行认证或查真实当前状态，输入身份/角色/批准均为合成声明。** 每个输出明确 `authentication_verified=false`、`formal_drawing_approval=false`、`product_quality_conclusion=false`；不能把任意客户端 JSON 当生产批准。后续服务须从事务内锁定的认证记录和不可变源记录投影输入。该 API 0.12 纯契约阶段当时未接入 HTTP/页面；API 0.13 服务认证与 API 0.14 操作页面分别记录于后续章节。

专项首轮 **16/16**，完整隔离回归 **195/195**；13 文件冻结及源码快照，实际 DXF/合成测量源字节核对，首轮报告拒绝覆盖。五个生命周期状态与旧 CAD 六图复现分别报告；不是独立工厂准确率。代码、源改动拒绝证据和 Docker 起初未运行的回归失败日志均保留；恢复现有服务后完成隔离回归，原库未迁移，无模型调用。


### 认证尺寸登记与独立使用批准服务（2026-10-06，API 0.13.0）

新增 `009_cad_dimensions.sql`、`datamind/cad_dimensions.py` 和 11 个 `/v1/dimensions/` 接口，工作流、请求字段、角色和边界见 [认证尺寸服务](data/cad_demo/DIMENSION_SERVICE_WORKFLOW.md)。admin 从限定本地 JSON 素材登记单条版本化测量，显式绑定当前图纸/特征/目标/认证复核；另一 approver/admin 账户批准使用后才能进行单条标注比较。源字节及认证审计保存于数据库事务，不接入温度生产库、RAG 或真实 ERP。

换图纸、测量、复核或绑定版本阻断新的比较，新绑定不继承批准；已有批准可在换版后撤销。新比较重新读可信当前头，历史比较保存输入快照/哈希并可固定回放。冻结纯函数的认证标志不变，服务认证状态在外层另列；不输出整体产品质量或正式图纸批准。

专项最终 **17/17**，初轮测试零件不匹配的失败日志保留；完整隔离回归 **212/212**（195 + 17），见 `reports/cad_dimension_service_regression.txt`。该 API 0.13 阶段完成服务，页面与三角色验证由 API 0.14 后续完成；功能提交 `a79836a` 已随 PR #1/#2 并入 `main`；原检出与原服务尚未升级。

### 尺寸操作页面与多文件并发（2026-10-06，API 0.14.0）

`/#dimensions` 接入认证尺寸服务：管理员登记固定测量版本，读取当前基线，再明确选择测量、当前图纸、已确认标注与对象，填写带时区的有效期及依据。图纸版次和工艺版次分别显示；对象默认空选，不按距离、文件名或相同尺寸自动配对。独立 approver/admin 账户可以批准、退回或撤销；所有已登录账户可查看当前核验和分页历史回放，管理员可以保存本次核验快照。图纸、测量、复核或绑定改版会阻断旧绑定的当前使用，撤销不改写历史快照。页面加载期间禁用选择/写入，同账户初始化合并，旧异步响应不能跨选择或退出后回填。

`/#files` 提供管理员受控上传与登记队列。每批最多 20 份 DXF/JSON，DXF 上限 2 MiB，JSON 上限 1 MiB 且每份只有一条测量；上传成功表示源已暂存，解析与登记结果另按文件显示。任务持久保存逐项输入、版本基线、成功/失败/取消状态及重试记录。批次默认并发 2、可设 1–4，所有 worker 共享最多 4 个 running 项；不同资产并发，同资产仍使用前版/SHA 比较提交。失败项重试不重复成功项，取消保留已提交历史，不自动升级旧版请求或代替人工复核/批准。

HTTP 服务和页面轮询不会自动执行队列。完成依赖、账户和迁移初始化后，在另一个终端启动独立 worker，并确保与 HTTP 服务使用相同元数据库写入 DSN：

```bash
.venv/bin/python scripts/file_job_worker.py --watch --workers 2
# 单轮/排空当前可领取任务：--once 或 --drain；可用 --job-id 限定一个批次
```

「问数与问答」的「限定问答文件」可明确选择最多 8 份已经索引的公开文档，允许跨支持的知识库。服务保留每文件相关片段名额，显示检索覆盖及最终引用覆盖；缺证或版本变化时明确停止。DXF/测量登记不会自动进入文档知识库。当前相关片段检索与原文/数字检查不能证明已阅读完整文件或自动裁定所有语义冲突；单次尺寸比较也不能扩称为整批质量结论。

队列协议、资源限制、恢复与后续 PDF/OCR 方案见 [多文件并发说明](docs/MULTIFILE_CONCURRENCY.md)，实现记录见技术说明书 §18.29。本阶段新增 49 项专项与完整隔离回归 261/261 均通过；前端竞态检查 5/5，页面验证独立批准/撤销/历史回放、混合文件上传及重复提交、多文件检索和引用覆盖。报告见 `reports/next_stage_regression.txt`、`reports/next_stage_browser_verification.json`、`reports/multifile_ui_races.json`；两个一次性预览副本、临时凭据和上传素材已清理，原库计数一致。问答使用确定性生成器，历史模型准确率分母不变。PDF/OCR、Office、图像和一般 CAD 文件尚未接入此并发队列。
