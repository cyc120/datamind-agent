# 结论卡：WP2 跨源融合引擎 + 证据图

**WP**：WP2 跨源融合引擎 + 证据图（决赛 F1）
**日期**：2026-10-09 22:42 CST（机器 `date`）
**基线提交**：`e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`，只读，exit 0）
**API 版本**：`0.39.0`（`datamind/api.py:197`）

## 问题

1. `datamind/agent.py` 的 `route_for()` 命中跨源关键词后，`run()` 直接 `FAILED_WITH_EVIDENCE` + `error_code="ROUTE_UNSUPPORTED"`——通用「数据库 + 文档」联合推理根本没有实现，审计 §5 据此给决赛 F1 = 0。
2. 工业核验（`datamind/industrial_rules.py:evaluate()`）只覆盖「一份工艺文档 + 生产 SQL」的单一结构：版本适用、单位核验、来源一致的语义被写死在这一个函数里，无法复用到跨源问答，也没有「数值声称必须绑定对象/版本/单位/时间口径」的强制。
3. 仓库没有任何带类型的证据图（node 类型 / edge 类型）、没有 binding 字段、没有把跨源冲突双方原值同时呈现的裁决门，也没有「修复必须记账」的受控修复。

## 做法

| # | 交付物 | 关键设计 |
|---|---|---|
| 1 | `datamind/binding.py`（446 行） | 七字段绑定 `binding{entity_id, entity_label, version, unit, time_scope, source_ref, source_sha256}`；`normalize_unit()` 只做**登记过的确定性等价换算**（`K↔degC` 保留 `Decimal` 标度，`1123.15 K → 850.00 degC`），不能确定（未知单位、量纲不同、逆除不尽如 `s→min`）返回 `None`，调用方用 `unit_status()` 标 `unit_unknown`；`version_applies()` 保持工业半开区间 `valid_from ≤ t < valid_to` 且无时区即拒；`time_scope_overlap()` 返回 `True/False/None`（未知≠重叠）；`entity_bind()` 只用 `entity_aliases` 的 **approved** 行做规范化（只读），一个别名对应多个 canonical 即判 `ambiguous` 并拒绝绑定。 |
| 2 | `datamind/industrial_rules.py`（最小接线） | 单位核验/来源一致/版本适用/包含式区间合规四段语义改为调用 `binding`；字段名、issue kind、reason code、错误串与 `rules` 输出逐字保持不变。 |
| 3 | `datamind/evidence_graph.py`（467 行） | node 类型白名单 `sql_row/doc_block/measurement/spec_clause`（**结论不是节点**）；edge 类型白名单 `entity_match/version_scope/unit_equivalent/time_overlap/supports/conflicts`；拓扑校验（未知类型、悬挂、自引用、空 evidence、**有向环**）；内容寻址 stable id + 排序序列化 + `digest()`（同证据必得同 id/同 digest）；冲突判定：同一 `entity+metric+version`、不同来源、归一后数值不等（或单位不可比）→ `conflicts` 边，**双方原值全部保留**；未登记权威 → `CONFLICT_UNRESOLVED`；命中 `source_authority_rules` payload 语义（`selected_source` / `context.scope.metric` / `part_id`，只读）才允许收敛，且两值仍保留。 |
| 4 | `db/migrations/019_evidence_graph.sql`（106 行） | 两张表 + CHECK（node_type/edge_type 白名单、`evidence` 非空、`src<>dst`、`run_id → runs(run_id)`）。**未对 `datamind_meta` 执行。** 文件顶部写明与 `deploy/init/02_metadata.sql` 同名旧表共存的两种形态：该表已存在时 `CREATE TABLE IF NOT EXISTS` 是纯 no-op（不需要表所有权，不重写旧行），索引/授权/注释用 `tableowner = current_user` 守卫，避免在任何形态下中断整批迁移。 |
| 5 | `datamind/cross_source.py`（967 行） | `plan(question, sources, document_collections, …)`：结构化解析（P1–P4）→ 建带类型证据图 → 绑定 → 链接（entity_match/version_scope/unit_equivalent/time_overlap/supports）→ 阈值核验 → 冲突裁决 → **最多 1 次**受控修复（只修绑定错误：approved 别名 / 由时间口径取适用版本 / 同 metric 兄弟单位）且每次记账 → 答案携带数值声称列表、每条声称的证据节点 id、binding 字段与冲突列表。`run()` 用真实适配器（SQL 走 `sql_engine.ask`，文档走 `KnowledgeRetriever` 仅检索不生成）。失败一律 fail-closed：`CROSS_SOURCE_EVIDENCE_INSUFFICIENT` / `DOC_BINDING_MISSING`（含 `VERSION_NOT_APPLICABLE`、`VERSION_MISMATCH` 子原因）/ `CONFLICT_UNRESOLVED`。 |
| 6 | `datamind/agent.py`（最小接线） | `route_for()` **未改动**；只把 cross_source 分支从「一律 ROUTE_UNSUPPORTED」改为 `_run_cross_source()`；响应只**新增** `cross_source_result` 键（`_plain` 后写入），证据图 best-effort 落 `evidence_nodes/evidence_edges`（只用两种表形态都有的列）并把 claims 写入 `claims` 表；非跨源路径的提示词与结果逐字节不变（见下）。测试用注入证据走 `cross_source_evidence=`（与既有 `generator=`/`retriever=` 同一注入缝）。 |
| 7 | `data/evaluation/cross_source_v1.json` | 四种结构冻结样例 21 条（P1 6 / P2 5 / P3 5 / P4 5），fixture 21 个，断言 161 条（含每条 `expected_failure_mode` 一致性 1 条）；每条含 `expected_answer_checks` / `expected_evidence_kinds` / `expected_binding_fields` / `expected_failure_mode`。 |
| 8 | `scripts/eval_cross_source.py`（359 行） | `--offline`（注入 fixture，把 `providers.chat` 与两个引擎的 `provider_chat` 换成计数桩，任何模型调用即失败）与 `--live`（真实适配器）；报告含逐结构分子分母、失败样本、错误码计数、以及**绑定敏感性探针**。 |
| 9 | `tests/test_cross_source.py`（632 行，唯一名） | 33 项：拓扑校验/白名单/环、冲突判定与双方保留、权威收敛、binding 七字段、单位与版本语义、fail-closed 三个码、受控修复记账、P1–P4 样例可解析且离线全通过、非跨源三条路径逐字节不变、agent 接线与落库。 |

## 数据与集

| 结构 | 冻结样例 | 断言数 | 结构要点 |
|---|---:|---:|---|
| P1 SQL 结果驱动文档检索 | 6 | 46 | 先查库得到对象（品类/渠道/商品/区域/会员等级），再按对象检索文档解释；含别名规范化（华东地区→华东、VIP→钻石）与 1 条文档对象不匹配的负向样例 |
| P2 文档规则驱动 SQL 筛选 | 5 | 36 | 从文档取阈值/适用范围，绑定单位与时间后筛库；含 `K→degC`、`天→小时` 单位归一与 1 条旧版本制度负向样例 |
| P3 跨源异常与多文档核验 | 5 | 42 | 同对象同版本跨来源数值矛盾、单位等价不算冲突、错版本拒绝、已登记权威收敛 |
| P4 复杂多跳 | 5 | 37 | 测量 → 实体/版本 → 规范条款 → 说明材料，逐跳证据边；含 1 条中间跳版本不适用 |

证据类型分布（去重组合）：`measurement+spec_clause+doc_block` 8 条、`sql_row+doc_block` 7 条、`sql_row+spec_clause+doc_block` 4 条、`measurement+doc_block` 2 条。均为**自建合成 fixture**：文档 `source_sha256` 是 fixture 文本的 `sha256`，不是真实文件摘要；SQL 行为注入的确定性行，不是真实演示库查询结果。

## 结果（分子分母）

| 项 | 分子/分母 | 证据（原样摘录） |
|---|---|---|
| `--offline` 用例通过 | **21/21**（P1 6/6、P2 5/5、P3 5/5、P4 5/5） | `"totals": {"cases": 21, "passed": 21, "failed": 0, "pass_rate": "21/21"}`（`reports/cross_source_offline.json`） |
| `--offline` 断言通过 | **161/161** | `"checks": {"total": 161, "failed": 0, "passed": 161}` |
| 离线模型调用 | **0 次** | `"model_calls": 0`（计数桩：调用即抛 `MODEL_CALL_FORBIDDEN_IN_OFFLINE_MODE`） |
| 错误码分布（拒答单列、不移出分母） | `{"CONFLICT_UNRESOLVED": 2, "DOC_BINDING_MISSING": 4, "NONE": 15}` | 同上报告 `error_code_counts` |
| 绑定敏感性探针（扰动后判定变化/21） | `sql_unit_removed` 7（6 条 completed→拒答）、`doc_sha_removed` 17（15）、`doc_version_retargeted` 12（10）、`sql_rows_emptied` 21（15） | 报告 `binding_sensitivity_probe` |
| 隔离回归 `test_cross_source.py` | **33 tests, failures 0, errors 0** | `{"tests": 33, "failures": 0, "errors": 0, "metadata_isolated": true, "model_settings_copied": false, "source_database_modified": false, "commit": "e4e2ebf", "failed_tests": []}` |
| 工业规则等价（`evaluate` 逐行对比基线副本 `tmp/audit-baseline/datamind/industrial_rules.py`） | **11 行 0 差异** | `rows_compared 11 diffs 0`；`temperature('1123.15','K') = Decimal('850.00')`（新=旧） |
| 非跨源路径逐字节不变 | SQL 提示词 sha256、RAG 提示词 sha256、industrial 结果键集 | SQL `eba77c041a7f0fbcf8caf528069a2ac3bcff68f7f7083cd5da19f95ca22de014`；RAG `a39c43a5898bb80735a7acb5281c8af082a33b6f7558ea57c2c103e6557ad9a5`；三条路径响应均无 `cross_source_result` 键（测试断言） |
| 019 迁移对 `datamind_meta` 的执行 | **0 次**（未执行） | 仅 `scripts/test_isolated.py` 的一次性克隆里被 harness 逐文件应用，输出 `applied 019_evidence_graph.sql` 且整轮 33 项全绿（在旧表形态下为 no-op） |
| `--live` | **not_attempted** | 按任务约定由主协调者串行执行；本卡不给 live 数字 |

基线对照（**未执行，代码路径推断**）：基线提交对同样的 21 条问句只会走到 `agent.py` 的 cross_source 分支并立即返回 `ROUTE_UNSUPPORTED`，即 0/21 可答；本卡不把该推断当作实测数字。

## 失败样本

**开发期真实失败（已定位并修复，全部保留在此）**

| 失败 | 原因分类 | 处置 |
|---|---|---|
| `psycopg2.errors.InsufficientPrivilege: must be owner of table evidence_nodes`（`db/migrate.py` 应用 019 时整批迁移中断，隔离回归直接崩） | 迁移与 `deploy/init` 同名旧表的所有权冲突 | 019 改为 `CREATE TABLE IF NOT EXISTS`（旧表形态纯 no-op）+ `tableowner = current_user` 守卫索引/授权/注释；白名单 CHECK 采用两套词表并集，避免旧写入路径反被约束打断 |
| 首次隔离回归 `{"tests": 33, "failures": 2, "errors": 4}`：`NameError: name 'time' is not defined`（agent 接线）、SQL 提示词捕获为空 `IndexError`（克隆库无 `app_settings` → `MODEL_KEY_MISSING`）、2 条断言写错（`time_scope_overlap(9月,3月)` 期望 True、缺文档时 `claims` 期望为空） | 接线遗漏 + 测试环境假设错误 + 断言错误 | 补 `import time`；测试内 patch `sql_engine.model_profile` 提供假 key（提示词组成与 profile 无关）；改断言为「9 月 vs 9 月中重叠=True、9 月 vs 3 月=False」与「缺文档仍可列出已绑定 SQL 声称，但答案不含结论」；重跑 33/33 全绿 |
| 单元/拓扑探针最初报 `EDGE_CYCLE`（entity_match 与 version_scope 反向成环）、`unit_equivalent` 缺失（限额不是 `value`） | 图构造方向不唯一 + 节点可比值定义过窄 | 对称边统一按 node id 规范化、环检测只算有向边（supports/version_scope）、条款不与声明它的文档块成有向回边；`comparable_value` 允许「限额」参与单位等价 |

**设计内的 fail-closed 样本（离线 21 条中 6 条，均为预期拒答而非失败）**

| 用例 | 结构 | 期望与实测 | 拒答的可判定理由 |
|---|---|---|---|
| `cs-p1-06` | P1 | `DOC_BINDING_MISSING` / `failed` | 库侧对象「洗衣机」与文档对象「空调」无任何实体边：`failure_detail=NO_DOC_EVIDENCE_LINKED` |
| `cs-p2-05` | P2 | `DOC_BINDING_MISSING` / `failed` | 旧版制度有效期 2024 不覆盖 2026-03 声称：`VERSION_NOT_APPLICABLE`，`repair_ledger` 1 条 `applied=false` |
| `cs-p3-04` | P3 | `DOC_BINDING_MISSING` / `failed` | 质检报告声明 `A102-V1` 而生产行是 `A102-V2`：`VERSION_MISMATCH:holding_temperature:A102-V2!=A102-V1` |
| `cs-p4-05` | P4 | `DOC_BINDING_MISSING` / `failed` | 中间跳（规范条款）版本有效期不覆盖声称，链路断在版本适用 |
| `cs-p3-01` | P3 | `CONFLICT_UNRESOLVED` / `abstained` | 1123.15 K（=850.00 degC，生产）与 845 degC（质检报告）矛盾且无登记权威；答案同时给出两值与两侧节点 id |
| `cs-p3-02` | P3 | `CONFLICT_UNRESOLVED` / `abstained` | 退款金额 7800 vs 7500 CNY 同对象同版本矛盾，未裁定 |

**跨 WP 冲突（我无法在允许范围内修复，需主协调者裁决）**

| 冲突 | 证据 | 说明 |
|---|---|---|
| 我的强制交付物 `db/migrations/019_evidence_graph.sql` 使另一个 WP 的测试失败：`tests/test_extensible_fields.py::test_registry_attribute_contract_is_reused_without_ddl` 断言 `git status --porcelain -- db/migrations` 为空（"本 WP 不得新增/改动迁移文件"） | `.venv/bin/python -m unittest tests.test_extensible_fields` → `Ran 17 tests ... FAILED (failures=1)`，`AssertionError: '?? db/migrations/019_evidence_graph.sql' != ''` | 该断言检查的是**未提交**改动，因此一旦主协调者把 019 提交入库即自动恢复；我不能 `git add`（禁写 git），也不能删自己的交付物或改他人测试文件。除此之外该模块 16/17 通过 |

**未做（如实标注）**：`--live` 未运行（约定由主协调者串行执行）；019 未对 `datamind_meta` 执行；未跑全量 1297 项回归（只用隔离 harness 跑本文件的 33 项）；`route_for` 关键词门未放宽（未做通用跨源问法识别）；真实 `source_authority_rules` 表里的跨源登记行不存在，权威收敛只用注入规则验证。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"
git rev-parse --short HEAD                 # e4e2ebf，exit 0
.venv/bin/python scripts/test_isolated.py --pattern test_cross_source.py
#   → {"tests": 33, "failures": 0, "errors": 0, ..., "failed_tests": []}，exit 0
.venv/bin/python scripts/eval_cross_source.py --offline
#   → totals 21/21、checks 161/161、model_calls 0，exit 0；写 reports/cross_source_offline.json
# 工业规则等价（临时对比基线副本，只读）
.venv/bin/python -c "import importlib.util,json,copy; \
spec=importlib.util.spec_from_file_location('base','tmp/audit-baseline/datamind/industrial_rules.py'); \
base=importlib.util.module_from_spec(spec); spec.loader.exec_module(base); \
import datamind.industrial_rules as new; print('loaded')"
#   → rows_compared 11 diffs 0（完整脚本见本卡「结果」一行；不落盘、不改仓库文件）
```

## 诚实边界（不能据此声称什么）

1. **不能声称跨源准确率**：21/21 是我本人编写、与本实现同期冻结的自建集，度量的是「同一套定义下的内部一致性」；fixture 里的证据是我为让契约成立而构造的，不是真实业务查询。它**不是**泛化准确率，也**不是**官方成绩。
2. **不能声称 live 端到端可用**：`--live` 未运行。且 live 路的 SQL 投影契约尚不成立——真实 NL2SQL 结果的列若不带 `entity/metric/value/unit/time_scope`，引擎会 fail-closed 拒答（`SQL_CLAIM_BINDING_INCOMPLETE`），这是设计而非缺陷，但目前没有可跑通的真实跨源问句证据。
3. **不能声称跨源问法已被通用识别**：`route_for()` 的门是既有词表（结合/联合/关联 + 数据库/销售额最高/…），我**没有**放宽它；只有命中该门且属文档类问句的问法才会进入新引擎，其余跨源问法仍走 sql/rag 或被当普通问题处理。
4. **不能声称绑定正确等于结论正确**：binding 只保证「数值声称绑定到对象/版本/单位/时间口径」；文档语义是否真的支持该数值没有独立校验（与 RAG 的 `warned`/`medium` 同一口径，`full_semantic_entailment: not_independently_verified`）。
5. **不能声称冲突裁决已在真实登记数据上验证**：裁决门只复用 `source_authority_rules` 的 payload 语义，且只读；本环境的该表没有覆盖这些 metric 的登记行，收敛路径仅由注入规则测试覆盖。
6. **不能声称迁移已生效**：019 从未对 `datamind_meta` 执行。它只在隔离 harness 的一次性克隆里被顺带应用（旧表形态 no-op，`applied 019_evidence_graph.sql` 出现在整轮全绿的输出里）；「新库建表 + CHECK 约束」分支从未运行过，因此不能声称约束在生产库上已生效。
7. **不能引用全量回归**：`reports/latest_regression.json` 被隔离 harness 覆写为**本次 33 项**摘要（harness 行为，非我手工编辑）；任何「1297 项全绿」的说法都不能引用本卡，需要主协调者跑全量。
8. **单位换算的能力边界**：只做终止小数的确定性等价（`K↔degC`、`mm↔cm↔m`、`g↔kg↔t`、`min↔s` 等）；`s→min`、未知单位、量纲不同一律返回 `None` 并标 `unit_unknown`，不取整、不近似；`degF` 未登记。
9. **`plain`/`bind_only`/`full` 三臂对照（WP3）未做**，因此不能据此声称「绑定机制带来了提升」，只能说「实现了绑定并在注入扰动下会拒答」。
10. **我造成了另一个 WP 的一条测试失败**：新增未提交的 `db/migrations/019_evidence_graph.sql` 触发 `tests/test_extensible_fields.py` 的「不得新增迁移文件」断言（17 项中 1 项红）。这不是隐藏的回归，也不能由我在允许范围内修复（禁用 git 写、禁删交付物、禁改他人文件）；提交 019 即恢复。
11. **运行中的服务仍是改动前的代码**：按禁令我没有停止/重启 8000 端口服务（`GET /health` 仍返回 200、`"agent":"sql+rag"`、capabilities 里没有 `cross_source`），因此新分支要等主协调者重启后才在 `http://127.0.0.1:8000` 生效；`/health` 的 capabilities 文案属 `datamind/api.py`，不在本 WP 允许改动范围内，我没有改它。

## 评分联动

| 评分项 | 本卡能支撑什么 | 不能支撑什么 |
|---|---|---|
| **F1 多源异构数据融合问数（10 分）** | 从 0 到「4 种结构（含复杂多跳 P4）+ 带类型证据图 + 冲突双方呈现 + 受控修复记账 + 可一键复现的离线集（21/21、161/161、model_calls=0）+ 33 项隔离测试」；官方按「≥3 种 6 分、≥5 种 8 分、复杂多跳 10 分」给分，材料形态已具备 | live 端到端与现场演示未验证、跨源问法识别仍受旧词表限制 → **不宜预支 8–10 分**，按「结构齐备但仅离线自证」保守主张 |
| **F3/E3 答案可解释性（5 分）** | `/v1/chat` 新增 `cross_source_result`：数值声称列表、每条的证据节点 id、七字段 binding、冲突双方原值与裁定状态、图 digest 与逐边 evidence 文本 | 前端证据图卡片未做（不在本 WP 允许新增文件内），可视化加分仍缺 |
| **F8 中级任务广度（15 分）** | 证据图/绑定可作为 B2（可扩展要素提取）、B8（按证据强度排序）的落地载体 | 本卡不主张任何新的中级任务「攻克」 |
| **F9 中级任务改进质量（10 分）** | 提供了机制与可复现集，具备做三臂消融的载体 | 未做对照消融，不能声称「改进显著」 |
| **E6/F5 忠实度口径** | 沿用「绑定正确 ≠ 语义蕴涵」的诚实标注 | 不能把绑定通过率当忠实度 |
