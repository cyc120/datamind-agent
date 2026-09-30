# DataMind Agent — 多模态数据驱动的可解释精准问数/问答智能体

赛题八（"中国电子杯"高校 ICT 产教融合创新大赛 · 中电云计算技术有限公司命题）项目仓库。

本仓库包含三大部分：

1. **`gateway/` — 大模型接入基座（可选）**：基于 New API 的模型网关，可继续用于渠道池、令牌配额与调用审计。应用默认直连上游 OpenAI 兼容接口——在 8000 页面左侧「模型接入」填入接口地址、模型名和 API 密钥即可，无需启动该网关；若要经过网关，把接口地址填成 `http://localhost:4000/v1` 即可。详见 [gateway/README.md](gateway/README.md)。
2. **`deploy/` `db/` `ingestion/` `scripts/` `data/` — 基础数据库底座**（本 README 主体）：结构化业务库 + 非结构化知识库 + 元数据基础设施 + 评测集，全部可重置、可复现。
3. **`datamind/` `web/` — 应用层首个模块**：结构化数据问数引擎、SQL Agent、HTTP/SSE 接口和本地测试页面。

`赛题/` 和 `技术文档/` 为项目资料，不参与网关运行。

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
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/eval_sql.py --output reports/nl2sql.json
.venv/bin/python scripts/eval_sql.py --suite robustness --output reports/robustness.json
.venv/bin/uvicorn datamind.api:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

模型接入在页面左侧「模型接入」中配置并即时生效，支持**多套接入配置**（ccswitch 风格）：每套配置可独立选择协议、地址、模型、密钥、鉴权方式，一键切换当前启用，支持导出/导入（默认不含密钥）。**协议支持 OpenAI 兼容（`/chat/completions`）与 Anthropic Messages（`/v1/messages`）**两套；Base URL 会自动补版本段（`/v1`、`/api/paas/v4`、`/api/v3` 都能直接填）。预设 DeepSeek / Anthropic 官方 / Claude 中转 / 阿里云百炼 / 智谱 / 月之暗面 / 硅基流动 / 火山方舟 / 本地网关 / 自定义，填入 API 密钥后可「拉取模型列表」验证密钥、选模型、「测试连接」验证可对话。密钥存放于 `datamind_meta.app_settings`，接口只回显掩码，`readonly_user` 对该表无任何权限；不再读取任何本地密令文件。同名环境变量 `NEW_API_BASE_URL`、`NEW_API_MODEL`、`NEW_API_KEY` 仍可作为兜底（优先级低于页面配置）。**本机地址（localhost/127.0.0.1）始终直连**——httpx 会经 urllib 读取 macOS 系统代理，若不绕开会把本地网关请求塞进代理导致挂起或 502；远端地址继续尊重系统/环境代理，也可在配置里用「代理」字段显式指定。问数数据库只用 `readonly_user`；可通过 `DATAMIND_META_DSN`、`DATAMIND_META_WRITE_DSN`、`DATAMIND_DB_HOST`、`DATAMIND_DB_PORT`、`DATAMIND_QUERY_USER`、`DATAMIND_QUERY_PASSWORD`、`DATAMIND_SQL_TIMEOUT` 配置。失败时返回可读错误码：`MODEL_KEY_MISSING`（未配置密钥）、`MODEL_AUTH_FAILED`（密钥被拒）、`MODEL_BALANCE_EMPTY`（上游额度不足）、`MODEL_UNREACHABLE`、`MODEL_TIMEOUT` 等；评测脚本逐例记录结果，不把失败计为正确。

本地 SQL Agent 提供 `POST /v1/chat`、`POST /v1/chat/stream`（SSE）、`GET /v1/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events?after=...`、`GET /v1/runs/{id}/evidence`、`GET|PUT|DELETE /v1/model/config`（兼容单配置）、`GET|POST /v1/model/profiles`、`PUT|DELETE /v1/model/profiles/{id}`、`POST /v1/model/profiles/{id}/activate`、`GET /v1/model/profiles/export`、`POST /v1/model/profiles/import`、`POST /v1/model/config/models`、`POST /v1/model/config/test` 和 `/health`。页面为左侧侧边栏三视图（问数 / 模型接入 / 运行记录），可直接深链 `/#model`、`/#runs`。运行与事件、SQL 证据和 Claim 写入 `datamind_meta`；刷新页面后可按 run ID 回放。服务目前只面向本机演示，默认启动命令绑定 `127.0.0.1`；尚未接入身份认证。文档、RAG 和跨源问题会返回 `ROUTE_UNSUPPORTED`，不会伪造答案。

测试页另有“测试数据库链路（不调用模型）”按钮：它只运行一条固定的 2024 年订单数查询，经过同一 Agent、Guard 和只读数据库连接，可在模型令牌不可用时验证前端、数据库、Trace 与回放。此按钮的结果不计入 NL2SQL 模型准确率。

当前 Schema 召回采用词法、业务别名、枚举值和已审核 Join Graph；向量召回及其权重调参尚未接入。

**当前实测基线（2026-09-30，模型 `deepseek-flash` 经本地网关，快照 `snap-demo_retail-e9495c660cd1`，评测集 `retail-v2` + 提示词 `sql-v2`）**：NL2SQL 执行一致率 **14/14，连续三轮**；鲁棒性用例 **4/4**（错别字、口语简称、越权拒答、空结果）。逐例报告含生成 SQL、gold SQL、修复尝试与结果预览，见 `reports/nl2sql.json`、`reports/robustness.json`。样本量小，不能外推为通用准确率；技术说明书 §4.6 的 Recall@5 仍需在扩样后的固定评测集上实测。

## 一、数据库总体架构

```
┌────────────────────── Docker Compose (deploy/compose.yaml) ──────────────────────┐
│  PostgreSQL :5432                    Qdrant :6333/:6334                          │
│  ├─ datamind_meta   元数据底座        └─ datamind_knowledge  137 向量点           │
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
│       └── 03_demo_retail.sql  #   零售演示库 DDL + 只读授权
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

## 四、结构化数据

**demo_retail（自建零售演示库）**: regions(6) / products(20) / customers(500) / orders(3000) / order_items(7587) / refunds(531)。覆盖边界：退款、折扣、NULL（region_id/cost_price/phone）、6 种订单状态、季节性销量（11-12 月高峰）。2024 年销售额口径校验参考值 **¥2,123,352.61**。

**chinook（赛题推荐）**: 完整加载 11 表（275 艺术家/3503 曲目/412 账单），中文业务词表已登记。

**指标口径（business_metrics）**: `sales_amount = Σ quantity × unit_price × (1 - discount)`，仅统计 completed/shipped/refunded；另有 units_sold / refund_amount / net_sales / order_count / customer_count 及 Chinook 的 revenue / tracks_sold。

## 五、非结构化知识库（26 份文档 / 137 切片）

| 类型 | 文档 |
|---|---|
| 原生文本（多级目录/跨页表） | 01 重点产品分析报告（销量冠军与 DB 联动）、02 渠道白皮书 |
| 表格密集 | 03 分区域经营简报（**数值=数据库实时聚合，跨源一致可校验**） |
| 公式 | 05 指标核算公式（3 个 LaTeX 公式图） |
| 长文档 | 06 年度经营分析（24 行跨页续表） |
| **扫描件**（旋转+噪声+模糊） | 07 门店巡检、08 质检报告（附 native_counterparts 原生对照） |
| 规章/FAQ/手册/数据字典 | 04、11、09、10 |
| OHR-Bench 真实语料 | 14 份（academic/administration/finance/law/manual/news/textbook ×2，gt_text 摄取、noise_text 留作 OCR 鲁棒性评测） |

切片策略：标题树优先、正文 500-800 字符、表格独立切片保留 table_json、嵌入文本带标题路径前缀。

## 六、快速开始

**数据集获取**（原始大文件不入库，Chinook SQL 已随仓库附带于 `db/chinook/`）：

```bash
# OHR-Bench（约 200MB，含 parquet；非必需，仅影响 OHR-Bench 抽样语料）
curl -L -O https://hf-mirror.com/datasets/opendatalab/OHR-Bench/resolve/main/OHR-Bench_v2.parquet
mkdir -p OHR-Bench && mv OHR-Bench_v2.parquet OHR-Bench/
```

```bash
# 1. 启动 Docker Desktop 后，一键重置/重建全部数据
bash scripts/reset_demo.sh

# 2. 端到端验证（19 项检查）
py -3.13 scripts/verify.py

# 3. 单独重建知识库索引
py -3.13 ingestion/ingest.py --reset

# 4. 只读账号连接问数（禁止任何写操作）
#    host=localhost port=5432 db=demo_retail|chinook user=readonly_user pwd=readonly_dev
```

## 七、环境依赖

- Docker Desktop；Python 3.13
- `py -3.13 -m pip install -r requirements.txt`
- Embedding 模型 BAAI/bge-small-zh-v1.5 经 hf-mirror.com 自动下载（约 100MB，本地缓存后离线可用）

## 八、已知边界与后续工作

1. **扫描件 OCR 为演示代理**：以原生对照版文本层代替真实 OCR（置信度标 0.85 并记录于 quality_detail）；正式 OCR 接入 PaddleOCR 是后续里程碑（M6 完整版）。
2. **MinIO 缺位**：官方镜像已下架；如需 S3 语义可自备镜像恢复 compose 中注释段。
3. **公式块**：公式以图片存在，LaTeX 结构化识别（中级任务 6b）待接入公式识别模型。
4. **知识库写回**、trace 事件生产者、V-CAR 编排均属应用层，不在"基础数据库"范围。
