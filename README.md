# DataMind Agent — 基础数据库搭建（赛题八）

多模态数据驱动的可解释精准问数/问答智能体的**数据底座**：结构化业务库 + 非结构化知识库 + 元数据基础设施 + 评测集，全部可重置、可复现。

## 一、总体架构

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
├── deploy/
│   ├── compose.yaml            # PostgreSQL + Qdrant（MinIO 注释保留）
│   ├── .env / .env.example
│   └── init/                   # 首次启动自动执行
│       ├── 01_roles.sql        #   datamind_app / readonly_user 角色
│       ├── 02_metadata.sql     #   元数据底座 17 张表（§8.1 全部对象）
│       └── 03_demo_retail.sql  #   零售演示库 DDL + 只读授权
├── db/
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
└── OHR-Bench/ chinook-database/   # 赛题推荐数据集（原始仓库）
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
git clone https://github.com/opendatalab/OHR-Bench  # 或
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
- `py -3.13 -m pip install psycopg2-binary pymupdf fastembed qdrant-client pillow reportlab matplotlib img2pdf pyarrow numpy`（见 requirements.txt）
- Embedding 模型 BAAI/bge-small-zh-v1.5 经 hf-mirror.com 自动下载（约 100MB，本地缓存后离线可用）

## 八、已知边界与后续工作

1. **扫描件 OCR 为演示代理**：以原生对照版文本层代替真实 OCR（置信度标 0.85 并记录于 quality_detail）；正式 OCR 接入 PaddleOCR 是后续里程碑（M6 完整版）。
2. **MinIO 缺位**：官方镜像已下架；如需 S3 语义可自备镜像恢复 compose 中注释段。
3. **公式块**：公式以图片存在，LaTeX 结构化识别（中级任务 6b）待接入公式识别模型。
4. **知识库写回**、trace 事件生产者、V-CAR 编排均属应用层，不在"基础数据库"范围。
