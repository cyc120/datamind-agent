# WP8 效率与伸缩性证明（SQL 三档 / 并发矩阵 / 分段计时 / 优化前后对照）

| 字段 | 值 |
|---|---|
| **WP** | WP8「效率与伸缩性证明」（对应《提升计划》§WP8；官方 E7 系统效率 5 分、F6 效率与伸缩性 5 分） |
| **日期** | 2026-10-09 22:39（CST，北京时间；`TZ=Asia/Shanghai date` → `2026-10-09 22:39 (CST)`） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`；`reports/scale_v2.json.environment.commit` 同为 `e4e2ebf`） |
| **API 版本** | 0.39.0（`GET http://127.0.0.1:8000/openapi.json` 的 `info.version` = `0.39.0`；与 `datamind/api.py` `version="0.39.0"` 一致） |
| **机读件** | [reports/scale_v2.json](../scale_v2.json)（sha256 `f6e8e3f22a969918fe184ffc9ab9381104490dacbd8ece9e978b059bce277194`，六个 section 齐备） |
| **说明文档** | [docs/PERFORMANCE.md](../../docs/PERFORMANCE.md)（复杂度分析 + 瓶颈 + 优化对照） |
| **状态** | **partial**：SQL / 并发 / 分段（工具链）/ 文档 / 优化对照均已实测；**模型段（首字、完整问答时延）为 `not_attempted`，留空 `null`**；隔离回归 22:39:30 通过（31/31），22:40 后因并发工作包的新迁移外部阻断（失败样本 10） |

---

## 问题

《提升计划》§WP8 原文：「`scripts/eval_scale.py` 的 retrieval 分支只跑 `_selected_rows`，不含召回+重排+生成；计时含导入与连接；无并发、无 SQL 侧规模。」
《审计报告》§7.2 进一步指出：`reports/scale_v1.json` 里 CAD 后两档（5000/20000 实体）都是 `partial`、块数触顶上限 2000 → **受限输出路径测量**；文档选择 1/2/5/8 份只读到 9/14/34/44 个切片，「不能外推为百万切片检索或整套 RAG 只需 100 ms」；每点 3 次、峰值 RSS 是子进程高水位、无并发、无 SQL 侧规模。并明确要求：补 SQL 1千/1万/10万行、文档 10/100/500 页、分段计时（路由/召回/检索/模型/首字/完整响应，冷启动与预热分开、报 p50/p95）、并发 1/2/4/8、复杂度分析与至少一次优化前后对照；**模型不可用时只报工具链测量，完整问答留空，不得用 mock 延迟充数**。

本轮开工前的实测缺口（真实存在，非估算）：

1. 没有任何 SQL 侧规模点（`reports/scale_v1.json` 的 `retrieval` 只动 `document_assets`/`chunks`）。
2. 没有任何并发负载；`scale_v1.json` 的 4 条 caveat 自己写着「no concurrent load」。
3. 没有分段计时，也没有冷/热分离：`retrieval` 分支的 103–107 ms 里同时包含模块导入与数据库连接。
4. 没有复杂度分析与优化前后对照（审计对 E7 的第 ①③ 项判「未实现」）。

## 做法

**新增（只写新文件 / 只扩展被授权的脚本，未改任何业务代码，未改 `db/migrations/`）**

1. **[scripts/build_scale_data.py](../../scripts/build_scale_data.py)**：确定性生成 SQL 侧三档数据（`order_items` = 1 千 / 1 万 / 10 万行，同 schema、与 `deploy/init/03_demo_retail.sql` 的**最小字段集**一致：`products/customers/orders/order_items`）。
   - 随机数用自实现 splitmix64（不依赖 `random` 模块实现细节），每行内容只由行号决定；每个档位**独立生成**（不外推切片，否则 1 千档订单会引用 1 万档才存在的 `customer_id`——这个坑已在测试里回归）。
   - **只写 side 目录**（默认 `tmp/scale_v2_data`），不写 `datamind_meta`、不碰演示库；`--check` 重新生成到临时目录并逐表比对 sha256 + 行数 + 「取值域稳定的表小档是大档前缀」；`--clean` 带 marker 守卫只删自己生成的目录。
   - 同文件内给出 `PRODUCT_INDEX_DDL`，**逐字**取自 `deploy/init/03_demo_retail.sql:73-76`，作为索引对照的「优化后」组。
2. **[scripts/eval_scale.py](../../scripts/eval_scale.py)**：新增 `--kind`（默认 `v1`）。
   - `--kind v1`（默认）= **既有扫描原样**：同路径 `reports/scale_v1.json`、同字段（`generated_at/machine/caveats/repeats_per_scale/cad_parse/retrieval`）、子进程 `cad`/`retrieval` 分派文本未动；本轮**没有重跑它**（那会覆盖既有 v1 报告），改由守护测试断言。
   - `--kind sql`：三档 × 固定查询集（单表过滤 / 两表 JOIN 聚合 / 三表 JOIN / 客户维度明细 Top-20），加**索引优化前后对照**（只有主键 → 演示库索引集 → `DROP INDEX` 回退复测）。
   - `--kind locator_ab`：块定位查询「逐行关联子查询（`knowledge.py:117-130` 形状）vs 一次 JOIN + 聚合」，两组输出做 JSON 规范化比对。
   - `--kind retrieval_scan`：用**产品真实函数** `datamind.knowledge.lexical_ranking` 测候选扫描的 `O(n)` 增长，并对照脚本内剪枝原型（先廉价过滤再打分）。
   - `--kind concurrency`：1/2/4/8 独立子进程，成功率 / 每秒完成数 / 峰值内存。
   - `--kind profile`：路由 / Catalog 装载 / Catalog 召回 / 检索 / 建连 / SQL 执行分段，冷启动与预热分开；**模型段恒为 `null` 并附原因**。
   - `--kind documents`：10/100/500 页（仓库现有 PDF 循环拼接，截图目录改到临时目录，不写 `data/object_store`）。
   - 所有 v2 section 合并写入 `reports/scale_v2.json`；每个测量点都带 `n`、`p50_ms`、`p95_ms`、原始 `samples_ms`；零分母一律 `"N/A"`。
3. **[docs/PERFORMANCE.md](../../docs/PERFORMANCE.md)**：逐模块复杂度（Catalog 召回 / SQL 生成与校验 / Hybrid 检索与 RRF / 块定位查询 / 解析与切片 / 执行层）、实测瓶颈清单、优化前后对照（含被否掉的候选）。
4. **[tests/test_eval_scale_v2.py](../../tests/test_eval_scale_v2.py)**：31 项，覆盖生成器确定性、指标算术、并发口径、模型段留空、既有 `eval_scale` 行为守护。

**不做什么（明确边界）**：不改 `datamind/*.py`（不在 WP8 白名单内）、不建索引到 `datamind_meta`、不动 CAD/DWG 与大图解析、不调用上游模型、不跑全量摄取、不重跑 v1 全量扫描。

## 数据与集

| 项 | 内容 |
|---|---|
| SQL 规模数据 | `tmp/scale_v2_data`，生成器 `build_scale_data.py/v1`，seed `20261010`，marker `datamind-scale-probe`，`schema_sql_sha256 = dce77286bb9891d0…`，manifest sha256 `18a5be12e3923a2bef85e3c73d47f6a0c66cfd4db30edb90a8f4bda6f89394fd` |
| 三档行数 | 1 000：`products=20 customers=50 orders=200 order_items=1000`（合计 1 270）；10 000：`20/200/2000/10000`（12 220）；100 000：`20/2000/20000/100000`（122 020） |
| 三档摘要（sha256 前 16） | 1 千：products `905f35c3f284ed85`、customers `bd93a7dadb320bcf`、orders `b1c1a436d11863c2`、order_items `9a14abefa1fc85d1`；1 万：`905f35c3f284ed85`/`b1cfddc44f2edb1d`/`65a0efab6a6c69b6`/`313952934caa980a`；10 万：`905f35c3f284ed85`/`34f644ed93cf6f2f`/`f15b2eb006ad9cb9`/`d932a6aaad0fba11`（10 万档 order_items 完整摘要 `d932a6aaad0fba11a51a0e40ae967ab18c94de1d790cb3763b6bd0e58efcfa9f`） |
| 数据自校验 | `--check` 通过：**12/12** 文件「重新生成内容一致」（`scale_v2.json.sql_scale.data.ok = true`） |
| 固定查询集 | `q1_single_filter`（单表区间过滤）、`q2_two_table_join`（两表 JOIN 聚合）、`q3_three_table_join`（三表 JOIN 聚合）、`q4_customer_detail`（客户维度明细 Top-20）；SQL 原文存进 `scale_v2.json.sql_scale.queries` |
| 文档源 | `data/knowledge_base/01_重点产品市场分析报告_2025.pdf`（2 页）+ `06_2025年度经营分析报告.pdf`（3 页），循环拼接到 10/100/500 页（原生文本，不含扫描件 OCR） |
| 分段题面 | 3 条冻结题面（`data/evaluation/registry/nl2sql_v1.json` 的 `nl2sql-001/003/006`），循环 4 轮 = 12 次调用，第 1 次为冷启动 |
| 检索集 | 真实演示知识库：`eligible_chunks = 169`，`mode = hybrid`（BGE `BAAI/bge-small-zh-v1.5` + Qdrant `qdrant/qdrant:v1.12.4`，`warnings = []`） |
| 规模数据性质 | **自建合成数据**，非真实企业数据；固定查询集也是自建 |

## 结果（分子分母）

所有数字原样摘录自 [reports/scale_v2.json](../scale_v2.json)；单位 ms，`n` = 样本数。

**A. SQL 三档 × 4 查询（临时库 `datamind_scale_*`，以 `readonly_user` 只读连接测量，`n=5`/点，1 次预热不计时；最大档装载 1 884.9 ms）**

| order_items | q1 p50/p95 | q2 p50/p95 | q3 p50/p95 | q4 p50/p95 |
|---:|---|---|---|---|
| 1 000 | 0.243 / 0.325 | 0.666 / 0.947 | 1.036 / 1.346 | 0.308 / 0.611 |
| 10 000 | 0.331 / 0.368 | 2.884 / 3.567 | 6.472 / 7.180 | 0.413 / 0.450 |
| 100 000 | 1.129 / 1.297 | 28.415 / 29.574 | 72.184 / 75.141 | 4.319 / 4.618 |

**B. 优化前后对照 ①（生效）：演示库索引集，100 000 行档，`n=5`/组，含 `DROP INDEX` 回退复测**

| 查询 | 前 p50 | 后 p50 | 回退 p50 | Δp50 | 前 p95 | 后 p95 | 回退 p95 | Δp95 | 加速比 p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| q4 客户维度明细 | 4.240 | **0.577** | 3.836 | **+3.663** | 4.550 | **0.814** | 4.355 | **+3.736** | **×7.348** |
| q3 三表 JOIN 聚合 | 74.125 | 68.122 | 71.383 | +6.003 | 75.150 | 70.705 | 73.669 | +4.445 | ×1.088 |
| q2 两表 JOIN 聚合 | 26.274 | 25.845 | 26.235 | +0.429 | 26.797 | 27.128 | 28.007 | −0.331 | ×1.017 |
| q1 单表区间过滤 | 1.124 | 1.208 | 1.215 | −0.084 | 1.323 | 1.668 | 1.533 | −0.345 | ×0.931 |

回退后 q4 回到 3.836 ms（≈优化前），说明差距来自索引而非缓存/偶发。q1/q2 **无收益**（诚实记录）。

**C. 优化前后对照 ②（生效，测量级原型）：检索候选剪枝，`n=5`/点，约 984 字符/行，1/20 行含热词**

| 切片数 | 产品打分 p50/p95 | 剪枝后（含过滤）p50/p95 | 加速比 p50 | 每 1000 行（产品） | 打分行数（产品/剪枝） | 候选集相同 | top-5 相同 |
|---:|---|---|---:|---:|---:|---|---|
| 500 | 46.262 / 46.682 | 16.076 / 16.298 | ×2.878 | 92.524 ms | 25 / 25 | ✅ | ✅ |
| 1 000 | 92.089 / 94.549 | 32.341 / 32.495 | ×2.847 | 92.089 ms | 50 / 50 | ✅ | ✅ |
| 5 000 | 470.407 / 478.534 | 170.865 / 170.959 | ×2.753 | 94.081 ms | 251 / 251 | ✅ | ✅ |
| 10 000 | 932.187 / 958.114 | 330.225 / 331.474 | ×2.823 | 93.219 ms | 501 / 501 | ✅ | ✅ |

**D. 优化对照 ③（被实测否掉）：块定位「批量取回」，`n=5`/点，两组输出 sha256 相同（`equivalent=true`）**

| 切片数（每切片 4 块） | 逐行子查询 p50/p95 | 批量 JOIN p50/p95 | 加速比 p50 | 回退/等价 |
|---:|---|---|---:|---|
| 1 000（2 000 块） | 21.587 / 22.792 | 23.745 / 24.823 | ×0.909 | 输出等价 ✅ |
| 2 000（4 000 块） | 46.738 / 48.767 | 59.513 / 60.006 | ×0.785 | 输出等价 ✅ |
| 5 000（10 000 块） | 121.416 / 126.360 | 153.363 / 168.461 | ×0.792 | 输出等价 ✅ |

**E. 并发矩阵（100 000 行档，每格 1 次重复，每 worker 8 次查询，1 次预热）**

| 并发 | 完成/计划 | 请求成功率 | 墙钟 s | 每秒完成数 | 峰值内存 max/worker | 峰值内存合计 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | **8/8** | **1.0** | 0.272 | **29.4299** | 34 750 464 B | 34 750 464 B |
| 2 | **16/16** | **1.0** | 0.281 | **56.9287** | 34 832 384 B | 69 632 000 B |
| 4 | **32/32** | **1.0** | 0.314 | **102.0335** | 35 061 760 B | 139 837 440 B |
| 8 | **64/64** | **1.0** | 0.327 | **195.9689** | 35 340 288 B | 280 559 616 B |

失败 worker 数 **0/15**（`errors` 为空数组）；拒绝/超时/上游错误**本轮为 0，但分母未调整**。

**F. 分段计时（冷启动 vs 预热，`n=11`）**

| 分段 | 冷启动 | 预热 p50 | 预热 p95 | n |
|---|---:|---:|---:|---:|
| 路由 `route_for` | 0.009 | 0.010 | 0.012 | 11 |
| Catalog 装载 | 20.533 | — | — | 0 |
| Catalog 召回 | 0.265 | 0.122 | 0.168 | 11 |
| 检索（hybrid） | **373.572** | **34.852** | **36.267** | 11 |
| 规模库建连 | 7.529 | 4.883 | 5.235 | 11 |
| SQL 执行（三表 JOIN@10 万） | 83.719 | 70.525 | 74.807 | 11 |
| **工具链合计（不含模型）** | **465.094** | **110.877** | **114.059** | 11 |
| 模型 / 首字 / 完整响应 | `null` | `null` | `null` | 0 |

**G. 文档三档**

| 页数 | 解析 p50/p95 (n) | 切片 p50/p95 (n) | 块数 | 切片数 | 峰值 RSS |
|---:|---|---|---:|---:|---:|
| 10 | 841.628 / 871.707 (3) | 0.114 / 0.131 (3) | 114 | 44 | 175.4 MiB |
| 100 | 8 377.814 / 8 461.121 (2) | 0.844 / 1.095 (2) | 1 140 | 440 | 189.7 MiB |
| 500 | **42 265.107 / 42 265.107 (1)** | 4.361 / 4.361 (1) | 5 700 | 2 200 | 261.5 MiB |

**H. 测试**

| 检查 | 分子/分母 | 结论 | 退出码 |
|---|---:|---|---:|
| 直接单测 `python -m unittest tests.test_eval_scale_v2` | **31/31** | `Ran 31 tests` / `OK` | `0` |
| 守护测试 `... LegacyBehaviorGuardTests` | **7/7** | `Ran 7 tests` / `OK` | `0` |
| 隔离回归 `scripts/test_isolated.py --pattern test_eval_scale_v2.py`（22:39:30 那次） | **31/31** | summary `{"tests": 31, "failures": 0, "errors": 0, "metadata_isolated": true, "model_settings_copied": false, "private_pdf_snapshot_rows_copied": false, "private_pdf_knowledge_rows_copied": false, "source_database_modified": false, "commit": "e4e2ebf", "ran_at": "2026-10-09T14:39:30.117778+00:00", "failed_tests": []}` | `0` |
| 隔离回归复跑（22:40 后，冻结最终代码） | **被外部阻断，未进入测试** | 种子阶段即失败：并发工作包新增的 `db/migrations/019_evidence_graph.sql` → `psycopg2.errors.InsufficientPrivilege: must be owner of table evidence_nodes`；原始日志 `tmp/wp8_isolated_retry.log` | `1`（非 WP8 引起） |
| 冻结最终代码直接单测（与 22:39 的差别只是删掉一个未使用 import） | **31/31** | `Ran 31 tests` / `OK` | `0` |
| 数据自校验 | **12/12 文件** | 重新生成逐字节一致 | `0` |
| 临时库清理 | **4/4 个用库模式** | `sql_scale`/`locator_ab`/`concurrency`/`profile` 四个 section 的 `database.drop` 均为 `{"dropped": true, "stderr": null}`（`documents`/`retrieval_scan` 不建库）；复跑 `docker exec datamind-postgres psql -U postgres -d postgres -tAc "select count(*) from pg_database where datname like 'datamind_scale%'"` → `0` | `0` |

## 失败样本

1. **模型段 `not_attempted`（真实未完成，不伪装）。** 本轮**没有**真实调用上游模型。可核对的原因：`POST http://127.0.0.1:8000/v1/chat` 实测返回 `{"detail":{"error_code":"CSRF_REQUIRED"}}`（未带会话/CSRF），要拿令牌就要写 `datamind_meta` 的 `auth_sessions`，超出本轮允许的写入范围。因此 `model` / `first_token` / `complete_response` 三段一律 `null`（`status=not_measured`，`reason` 落在报告里），**没有填 0、没有用 mock 延迟**。守卫测试专门断言「必须留空而不是 0」。
2. **索引优化对三个聚合/过滤查询无效（负结果）。** q2 Δp50 = +0.429 ms、q1 Δp50 = −0.084 ms，q3 只有 +8.9%（回退组 71.4 ms 介于两者之间）。**不能声称「加索引能提升聚合查询」**；能声称的只有 q4（×7.35，且回退可复现）。
3. **块定位「批量取回」是负优化（×0.785–0.909）。** 审计 §7.2 猜测它是瓶颈，本轮实测**不支持**这个猜测：5 000 切片时逐行形态也只有 121.4 ms，批量形态反而 153.4 ms。
4. **`--kind sql` 只覆盖执行层**：模型生成 SQL、工具链重试与安全门禁的耗时都不在这些毫秒数里。
5. **500 页档 `n=1`**（p50=p95=该次观测值）；**并发每格只重复 1 次**（无 QPS 的 p50/p95）；**每点 `n=5 < 20`，p95 即观测最大值**——都已写在报告的 `notes`/`samples_per_point`/`caveats` 里。
6. **文档侧是拼接文档**：内容重复会放大缓存命中与表格识别稳定性；且不含入库、向量化、检索。
7. **CAD 后两档仍是受限输出路径**：`reports/scale_v1.json` 里 5000/20000 实体两档均为 `partial`（块数触顶 2000），本轮没有重跑 v1，该免责已写入 `scale_v2.json.caveats`。
8. **`reports/latest_regression.json` 的副作用（如实声明）**：`scripts/test_isolated.py` 无条件重写该文件（其设计如此），本次把它写成 `tests=31`。我在运行前已备份，运行后**按原字节恢复**为运行前状态（sha256 `10511d27b77b2fb47b48d099cdb2249455889168b6a2e5382517a3d7174a1455`）。需要说明：该备份的内容是 `"tests": 16`——**那是同一工作区里另一个工作包并发跑隔离测试留下的**，不是本轮产物；git HEAD 版本为 `1297/1512439`。该文件同时被多个 WP 的隔离运行改写，无法对所有并发方同时正确，我选择「不留新痕迹」。
9. **工作区并发改动**：测量期间 `datamind/agent.py`、`datamind/api.py`、`datamind/sql_engine.py`、`datamind/industrial_rules.py`、`web/app.js` 等被其它工作包修改（`git status` 可见）。本轮被测的 `datamind/knowledge.py` 未被改动；行号引用以测量时刻文件为准。
10. **隔离回归在 22:40 后无法复跑（外部阻塞，如实记录）**。`scripts/test_isolated.py` 在跑测试**之前**会克隆 `datamind_meta` 并重放 `db/migrations/`；并发工作包在此期间新增了 `db/migrations/019_evidence_graph.sql`，克隆库里的 `evidence_nodes` 不属于当前迁移账号，于是 `migrate()` 抛 `psycopg2.errors.InsufficientPrivilege: must be owner of table evidence_nodes`，脚本以 `1` 退出（两次复跑同样结果，日志 `tmp/wp8_isolated_retry.log`）。**这与 WP8 的测试文件无关，也不在 WP8 可修的文件白名单内**（不得改 `db/migrations/` 与 `scripts/test_isolated.py`，故不尝试修复）：受影响的其实是所有人的隔离回归。WP8 的证据是 22:39:30 那次成功的 31/31（summary 见上表），加上在冻结最终代码上的直接单测 31/31。按纪律「同一阻塞不反复重试」已在第 2 次后停止。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"

# 1) 生成三档 + 只读校验
.venv/bin/python scripts/build_scale_data.py               # EXIT=0
.venv/bin/python scripts/build_scale_data.py --check       # EXIT=0（12/12）

# 2) SQL 三档 + 索引优化前后对照（临时库，跑完自动删）
.venv/bin/python scripts/eval_scale.py --kind sql --tiers 1000,10000,100000 --samples 5   # 退出码 0（打印「计划外失败模式数=0」）

# 3) 块定位「逐行 vs 批量」（实测为负优化）
.venv/bin/python scripts/eval_scale.py --kind locator_ab --locator-sizes 1000,2000,5000 --samples 5   # EXIT=0

# 4) 检索候选扫描 O(n) + 剪枝原型
.venv/bin/python scripts/eval_scale.py --kind retrieval_scan --scan-sizes 500,1000,5000,10000 --samples 5   # EXIT=0

# 5) 并发矩阵
.venv/bin/python scripts/eval_scale.py --kind concurrency --workers 1,2,4,8 --queries-per-worker 8   # EXIT=0

# 6) 分段计时（模型段留空）
.venv/bin/python scripts/eval_scale.py --kind profile --scale-tier 100000 --profile-repeats 4   # EXIT=0

# 7) 文档三档
.venv/bin/python scripts/eval_scale.py --kind documents --doc-sizes 10,100,500 --doc-samples 3,2,1   # EXIT=0

# 8) 守护测试（既有 --kind v1 语义不变；本轮未重跑 v1 全量以免覆盖 reports/scale_v1.json）
.venv/bin/python -m unittest tests.test_eval_scale_v2.LegacyBehaviorGuardTests   # EXIT=0（7/7）

# 9) 自有测试的隔离回归
.venv/bin/python scripts/test_isolated.py --pattern test_eval_scale_v2.py
#   22:39:30 那次 EXIT=0（31/31）；22:40 后复跑被 db/migrations/019_evidence_graph.sql 阻断（见失败样本 10）
.venv/bin/python -m unittest tests.test_eval_scale_v2      # 冻结最终代码：EXIT=0（31/31）
```

## 诚实边界（不能据此声称什么）

1. **不能据此声称任何端到端问答时延**。模型段为空：整条「问 → 生成 SQL → 执行 → 作答」只测到了工具链的一半（路由/召回/检索/执行），`工具链合计 110.88 ms` **不等于**问答时延。
2. **不能声称生产吞吐或容量**。单机、无 CPU 绑定、无 cgroup 隔离、并发每格 1 次重复；`195.97 次/秒` 是小查询在 8 个进程下的短窗口读数，不是服务容量。
3. **不能声称真实数据上的性能**。SQL 侧是合成同 schema 数据；10 万行也只是「几十万级」，不是千万/亿级；没有分区、没有并行查询调优。
4. **不能声称优化已落地产品**。§B 的索引优化落在**临时库 DDL**（已回退验证）；§C 的剪枝原型只存在于 `scripts/eval_scale.py`，落地需改 `datamind/knowledge.py`（不在 WP8 白名单）。§C 只声称「候选集不变（可证）+ 该样本 top-5 相同」，不声称分数逐位相同（BM25 长度均值会变）。
5. **不能把 p95 当统计分位**：`n=5`（SQL/检索/块定位）与 `n=3/2/1`（文档）都 < 20，p95 就是观测最大值；`n=1` 的点没有离散度信息。
6. **不能外推 CAD/大图与数百万切片**：CAD 后两档是受限输出路径（上限 2000 块）；检索侧产品上限是 `MAX_SCOPE_CHUNKS = 5000`，10 000 行只是余量参考。
7. **不能把本文件的复杂度分析当作已证明的上界**：时间复杂度是从代码结构读出的（引用了文件与行号），实测只覆盖了上述规模点。
8. **不能声称 E7/F6 得分**。官方分值由评委判定，本卡只提供材料。

## 评分联动

| 官方/计划项 | 本卡作用 | 目标分 | 当前判断 |
|---|---|---|---|
| **E7 系统效率**（5 分，官方 p.10） | ①复杂度分析 → [docs/PERFORMANCE.md](../../docs/PERFORMANCE.md) §2；②多规模实测 → SQL 3 档 + 并发 4 档 + 文档 3 档（另加 v1 的 CAD 4 档/文档选择 4 档）；③瓶颈与优化 → §4/§8，含 1 次生效 + 2 次被否候选 | 5 | **材料齐备（partial）**：③ 的三件套本轮首次补齐；扣分风险在「模型段留空」与「无生产级吞吐」 |
| **F6 效率与伸缩性**（5 分，官方 p.13） | 复杂度分析 + 3 档以上规模 + 吞吐量（29.43→195.97 次/秒）+ 优化前后 p50/p95 差值 | 5 | **材料齐备（partial）**：同上，吞吐只覆盖 SQL 工具链 |
| E1/E2/E5/E6 | 不涉及（本卡不产生准确率） | — | 无变化 |
| 《提升计划》WP8 验收「≥3 档 × ≥2 路径 × 并发矩阵齐备」 | SQL 3 档 × 4 查询 + 文档 3 档 + 并发 4 格 + 分段 6 段 | — | **达成** |
| 《提升计划》WP8 验收「每个数字有环境说明」 | `scale_v2.json.environment` + 每点 `n`/`samples_ms` + 8 条 caveats | — | **达成** |
| 《提升计划》WP8 验收「优化对照可复现」 | 索引对照含回退组；剪枝与块定位对照各有独立 `--kind` | — | **达成** |
| 计划风险表「模型不可用」行 | 分工具链/模型两段，模型段留空并附原因，未把上游错误移出分母（本轮分母内 0 错误，如实记录） | — | **如实执行** |

> 本卡只声明：**SQL 三档、并发四档、分段（工具链）、文档三档、两组优化对照**是真实跑出来的；模型段是 `not_attempted`；E7/F6 是否得 5 分由评委判定。
