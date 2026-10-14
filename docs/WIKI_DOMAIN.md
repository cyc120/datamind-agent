# Wiki 知识图谱：领域契约（P0 冻结）

日期：2026-10-14　状态：**P0 冻结稿**。本文件只定义领域语义与身份规则，**不含任何实现**。
接口契约见 [Wiki 接口契约](WIKI_API.md)；需求来源为 `WIKI_KNOWLEDGE_GRAPH_PLAN(1).md`（335 行，权威版）。

冻结范围：四类词条、关系词表、三类身份、三套状态维度，以及需求 §3.3 中两条被弱化项的补回。
本阶段的产物**只是文档**：没有新建表、没有写业务代码、没有执行迁移。

> **与需求文档的关系**：需求 §5/§6 的表述保留，但凡与当前检出或线上库不一致的地方，以本文件为准，
> 并在 §1 逐条列出差异。需求文档第 24 行自己写了"实施时应再次核对当前检出"——本文件就是那次核对的结果。

---

## 1. 冻结前核对：与需求文档不一致的既有事实

以下 8 条均由**只读检查**得出，其中 5 条来自线上库（容器 `datamind-postgres`，库 `datamind_meta`）。
每条都改变了领域设计，因此写进契约而不是留给实现阶段。

### 1.1 线上库的表形状比迁移文件窄（阻断级）

迁移 `db/migrations/019_evidence_graph.sql:81-100` 与线上实际形状**不一致**：

| 列 | 迁移 019 声明 | 线上 `\d evidence_edges` 实际 |
| --- | --- | --- |
| `edge_id` | `BIGSERIAL PRIMARY KEY` | ✅ 同 |
| `run_id` | `NOT NULL REFERENCES runs` | ⚠️ **可空**，且线上无 FK 到 `runs`、无 CHECK |
| `src_evidence_id` / `dst_evidence_id` | `NOT NULL` + FK | ✅ 同（有 FK 到 `evidence_nodes`） |
| `edge_type` | `VARCHAR(24) NOT NULL` + **CHECK（14 个词表）** | ⚠️ `VARCHAR(24) NOT NULL`，**无 CHECK** |
| `binding_type` / `confidence` / `evidence` | 存在 | ❌ **不存在** |
| `weight` | 不存在 | ✅ 存在 `NUMERIC(4,3)` |
| 索引 | `idx_evidence_edges_run/src/dst` | 只有 `idx_edges_run(run_id)`；PK 在 `edge_id` |

`evidence_nodes` 同样比迁移窄：线上只有 6 列
（`evidence_id, run_id, node_type, ref_key, payload, created_at`），迁移里的
`source_sha256/version/unit/time_scope/binding` **线上不存在**。
**结论：边的理由（`evidence`）、绑定类型（`binding_type`）、置信度（`confidence`）在线上库里确实没有落库。**
这意味着 Wiki 投影对**历史数据**必须记 `provenance_incomplete`，且**不得推测补全**
（需求 §5.2 第 120 行；语义见 [接口契约](WIKI_API.md) §6）。

### 1.2 当前库里的边词表只有 3 个，且都是旧词表

```
SELECT edge_type, count(*) FROM evidence_edges GROUP BY 1;   -- 3 111 行
derived_from 2054 | supports 682 | retrieved_from 375
```

- 需求 §2 与 `evidence_graph.py:30-31` 的 6 个新词表（`entity_match / version_scope /
  unit_equivalent / time_overlap / supports / conflicts`）中，**线上只出现过 `supports`**；
  另外 5 个**在这个库里一行都没有**（`conflicts` 由 `detect_conflicts` 只在内存图上建边，
  落库时同样是 4 列 INSERT）。
- `019:86-89` 的 CHECK 里那 7 个旧词表（`derived_from / retrieved_from / maps_to /
  contradicts / verified_by / repaired_from / resolved_by`）中，**线上出现过 `derived_from`
  与 `retrieved_from`**，`maps_to / contradicts / verified_by / repaired_from / resolved_by`
  没有出现。
- `supports` 同时属于两套词表，**不能**按"新词表 / 旧词表"二分。语义相同，来源不同（见 §3.3）。

> 这条把"未知类型如何处理"从一个假设问题变成了**当前数据里 2 428 / 3 111 条边（78%）就是旧词表**
> 的现实问题。§3.4 给出规则。

### 1.3 线上库里没有任何跨源核验运行

```
SELECT count(*) FROM trace_events WHERE agent_name='cross_source_agent';   -- 0
终态事件 output_summary 的键全集：13 个，不含 cross_source_result
```

需求 §2 把"跨源核验"当作既有能力之一，分析报告 §3.4 也据 `agent.py` 给出"跨源冲突不写表"的结论。
**代码路径确实存在**（`agent.py:519-608` 的 `_run_cross_source`、`cross_source.py:1047-1048`
会把 `cross_source_result` 塞进终态负载），但**这个库里一次都没跑过**。
因此：

- 需求 §8.4（第 223 行）"跨源冲突整批丢失"这一风险在本库**现阶段不会触发**，因为本库没有跨源冲突；
- 但投影层**仍必须**按分析报告 §3.4 同时实现两条路径，否则一旦跑跨源就丢数据（见 §3.5）；
- P0 样本中的"跨源核验"一组**未能在本库查到**，如实记录（见 [接口契约](WIKI_API.md) §11.3）。

### 1.4 冲突只有一条路径真正落库

线上 `conflict_records` 共 **175 行**，`resolution_status` 分布：

| `conflict_type` | `resolution_status` | 行数 |
| --- | --- | --- |
| `measurement_context` | `needs_review` | 70 |
| `value_conflict` | `unresolved` | 35 |
| `unit_equivalence` | `resolved_equivalent` | 35 |
| `version_overlap` | `needs_review` | 35 |

**注意：工业侧的 `resolution_status` 也是三态**，不是布尔。
`unresolved` 只是其中一态，`needs_review` 与 `resolved_equivalent` 都"有冲突记录但未判定为矛盾"。
Wiki 不能把"存在 `conflict_records` 行"直接读成"存在未裁决冲突"（见 §5.3）。

### 1.5 表 owner 与授权（决定迁移边界，不属于本文但影响身份设计）

- 48 张表分属两个 owner（实测 `pg_tables`：`postgres` 18 张 / `datamind_app` 30 张）：

  | owner | 表 |
  | --- | --- |
  | `postgres`（18 张） | `app_settings`、`business_metrics`、`chunks`、`claims`、`data_sources`、`document_assets`、`document_blocks`、`entity_aliases`、`evaluation_cases`、`evidence_edges`、`evidence_nodes`、`ingest_jobs`、`runs`、`schema_columns`、`schema_relations`、`schema_snapshots`、`schema_tables`、`trace_events` |
  | `datamind_app`（30 张） | 其余全部，含 `auth_*`、`chat_sessions`、`conflict_records`、`pdf_*`、`cad_*`、`dimension_*`、`source_authority_*`、`task_leases`、`file_jobs`、`document_fact_*`、`document_block_*`、`ingest_job_claims`、`document_contract_blocks` |

  注意 **`conflict_records` 归 `datamind_app`**，而 `runs` / `trace_events` / `evidence_*` / `claims`
  归 `postgres`。工业冲突写表能成功、跨源冲突只能落 `trace_events`，owner 差异是原因之一。
- 实测：`datamind_app` 对 `runs` 有完整 DML（`SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER`
  共 7 项）但**不是 owner**；
- 迁移由 `datamind_app` 执行（`db/migrations/020_chat_sessions.sql:39`），
  因此 `datamind_app` **对新表可自由建索引**，对 `postgres` 持有的表则不能（见 [接口契约](WIKI_API.md) §10）；
- `readonly_user` 当前可 SELECT **43** 张表。Wiki 私有表必须显式
  `REVOKE ALL ... FROM PUBLIC, readonly_user`（既有惯例：`005:50` / `011:36` / `012:50` / `013:17` / `020:78`）。

> 结论：Wiki 领域身份**不依赖**任何对旧表的 DDL/DML 扩展。`postgres` 持有的 18 张表清单
> 见 [接口契约](WIKI_API.md) §12.4。

### 1.6 角色的真实语义：扁平四角色 + admin 超管旁路

```
auth.py:17   ROLES = {'viewer','reviewer','approver','admin'}          # 扁平，无层级
auth.py:195  if permission not in user['roles'] and 'admin' not in user['roles']: fail(403)
auth.py:217  （同一判据的第二次出现）
```

**`reviewer` 与 `approver` 互不继承**：`approver` 通过 `reviewer` 门会 403。
Wiki 的领域权限必须照此书写（矩阵见 [接口契约](WIKI_API.md) §4），**不得**写成层级模型。

另：`auth.py:59-63` 在 `DATAMIND_LOCAL_NO_LOGIN=1` 时给**任意来源** admin，
回环来源默认免登录给 admin。这条对 Wiki 的影响不是领域语义而是演示边界，
记在 [接口契约](WIKI_API.md) §4.3。

### 1.7 `verified` 的真实判据不是"事实已证实"

```
agent.py:618-623   bound = binding_status=='bound' and not binding_problems
                   'verified' if bound and status=='completed' else 'insufficient_evidence'
agent.py:238/:629  full_semantic_entailment 恒为 "not_independently_verified"（字面量）
```

本库实测的 `verification_contract` 分布证明这条不是理论担忧：

| `verification_contract` | 说明 | 出现次数 |
| --- | --- | --- |
| `time_scope_only` | 只证明了时间范围被规范化，**没有**证明指标语义 | 最多（`semantically_verified=false`） |
| `retail_metric_ast_contract` | 指标 AST 契约通过（`semantically_verified=true`） | 少数 |

**`claims.status='verified'` 与 `runs.status='COMPLETED'` 都不等于"事实已确认"。**
Wiki 的"主张核验"维度因此必须独立记账，并保留 `full_semantic_entailment` 的
`not_independently_verified` 原值（§5.2）。

### 1.8 `runs.versions` 与 `claims.verification` 是两份不同的版本账

- `runs.versions` 记**运行级**版本（`model` / `prompt` / `schema` 或 `kb` / `retriever` /
  `retrieval_mode`，工业侧另有 `rules` / `parser` / `dataset` / `authority`）；
- `claims.verification` 记**主张级**判据（`cross_source_version`、`graph_digest`、
  `binding`、`full_semantic_entailment`）；
- 两者**都不含知识词条的版本**。词条版本是 Wiki 自己新增的第三份账（§6）。

---

## 2. 四类词条定义

四类词条的划分**不是需求文档凭空规定的**，它对齐既有证据节点词表与工业事实的 `fact_kind`：

| 词条类型 | 对齐的既有节点 / 字段 | 对应需求 §5.1 身份条 |
| --- | --- | --- |
| `business_entity` 业务实体 | `DocumentFact.payload.entity_id` / `entity_name` / `entity_type`；`SQLRow.payload.part_id`；`schema_tables` | 第 1 条（词条身份） |
| `metric` 指标 | `DocumentFact.payload.metric` / `normalized_unit`；`SQLRow.payload.metric` / `unit`；`business_metrics` | 第 1 条（词条身份） |
| `norm` 规范 | `DocumentFact.payload.fact_kind='requirement'` + `source_revision` + `normalized_range` | 第 1 条（词条身份） |
| `conclusion` 分析结论 | `Claim.payload.text` + `claims.verification`；`ConflictRecord` 的裁决说明 | 第 3 条（主张比较身份） |

### 2.1 业务实体 `business_entity`

| 项 | 冻结内容 |
| --- | --- |
| **判定标准** | 有具名对象、且对象在某个来源里**可被独立引用**（有 `entity_id` 或 `part_id` 之类的标识，而不是只在正文里被提到一次）。对象可以是设备、零件、批次、客户、产品、门店、供应商。 |
| **业务键** | `space_id` + `namespace` + `entity_type` + `entity_key`。`entity_key` = 已批准别名登记（`entity_aliases`）中的规范键，或**人工确认**的归并键。 |
| **必填字段** | `type=business_entity`；`business_key`（四段）；`title`；`aliases`（可为空数组）；`confirmation`（`confirmed` / `pending`）；`summary`。 |
| **关系规则** | 可与 `metric` 建 `measures`/`applies_to`；可与 `norm` 建 `applies_to`/`scoped_by`；可与 `conclusion` 建 `about`。`entity_match`（§3.3）**只能**作为**合并建议**，不得自动归并。 |
| **禁止** | 名称相近**不能**作为业务键，也不能触发自动归并（需求 §3.3 第 64 行）。当前库里**没有**任何已批准的别名登记被用于生产问答（`entity_aliases` 存在但未在运行中被解析），所以第一版 entity 词条的默认 `confirmation` 是 `pending`。 |

**当前库的实体样本**（实测）：`part_id='A102'`（工业零件）、`batch_id ∈ {B001,B002,B004,B009,B010}`（批次）、
`demo_retail` 的产品名（`对开门冰箱 450L` / `65英寸4K电视` / `平板电脑 T11`）。
这些**全部是合成演示数据**，词条必须标注演示属性（需求 §1 第 20 行）。

### 2.2 指标 `metric`

| 项 | 冻结内容 |
| --- | --- |
| **判定标准** | 可被赋一个值 + 一个单位 + 一个口径（时间范围 / 记录对象 / 统计方式）的量。没有单位与口径的"概念"是实体，不是指标。 |
| **业务键** | `space_id` + `namespace` + `metric_id`，其中 `metric_id` 是**来源内的规范名**（`holding_temperature`、`demo_retail.sales_amount`），**不是**中文标签。单位与口径**不进业务键**，它们是主张的比较身份（§4.3）。 |
| **必填字段** | `type=metric`；`business_key`；`title`；`canonical_unit`（可空，但必须在正文里显式说明"单位未登记"）；`summary`；`source_refs`。 |
| **关系规则** | 与实体建 `measures`；与规范建 `threshold_of`；与结论建 `about`。同一 `metric_id` 在不同单位下的两条主张**不是冲突**，是 `unit_equivalent`。 |
| **禁止** | 不得把"指标相同"当作"主张相同"（需求 §5.1 第 110 行）。指标词条只承载**定义与口径说明**，不承载任何一个具体值。 |

### 2.3 规范 `norm`

| 项 | 冻结内容 |
| --- | --- |
| **判定标准** | 表达"应当 / 允许 / 限值 / 适用条件"的条款型知识，绑定一个**适用版本**。来源为 `fact_kind='requirement'` 的 `DocumentFact`，或 PDF 条款 / 合同块。 |
| **业务键** | `space_id` + `source_document_id` + `source_revision` + `clause_key`。`source_revision` **必须**进键：同一文档的 A102-V2 与 A102-V3 是两条规范，不是一条规范的两个版本号。 |
| **必填字段** | `type=norm`；`business_key`；`title`；`applicability`（`valid_from` / `valid_to` / 适用对象范围）；`clause_locator`（页 / 块 / bbox，或 SQL 记录定位）；`summary`。 |
| **关系规则** | 与指标建 `threshold_of`；与实体建 `applies_to`；与结论建 `justified_by`。规范换版时**不修改**旧规范词条，而是新建词条 + 建 `supersedes` 关系（§3.3）。 |
| **禁止** | 不得把两个版本的规范合并成一条"最新规范"——那会让历史结论失去可解释性（需求 §5.2 第 121 行、§6 第 140 行）。 |

**当前库的规范样本**（实测 `DocumentFact` 的 `source_revision` 取值）：

```
A102-V2   valid_from 2026-09-01T00:00:00+08:00   valid_to NULL
A102-V3   valid_from 2026-09-15T00:00:00+08:00   valid_to 2026-09-20T00:00:00+08:00
```

两条规范对同一 `entity_id='A102'`、同一 `metric='holding_temperature'` 生效，有效期**重叠**于
2026-09-15 ~ 2026-09-20。批量 B004 的冲突记录 `conf-db4974a290e34c279095`
（`conflict_type=version_overlap`，`rule_id=MULTIPLE_APPLICABLE_REVISIONS`）正是这次重叠。
**这就是"规范换版"在本库的真实形态**：不是文档版本号变了，而是两份规范的有效期重叠。

### 2.4 分析结论 `conclusion`

| 项 | 冻结内容 |
| --- | --- |
| **判定标准** | 由某次运行产出的、可被复核的**断言**，带证据引用与核验结论。来源为 `claims` 行、工业核验的 `VerificationCheck` 节点、或冲突裁决说明。 |
| **业务键** | `space_id` + `run_id` + `claim_id`（工业侧 `claim_id` 由运行生成；跨源侧为 `claim['claim_id']`）。结论**不跨运行合并**——同一问题跑两次得到的是两个结论词条，可以建 `restates` 关系，不能相互覆盖。 |
| **必填字段** | `type=conclusion`；`run_id`；`claim_id`；`text`（主张原文，逐字）；`verification`（`status` + `scope` + `support` + `semantic_entailment`）；`evidence_refs`；`assertions`（结构化：实体 / 指标 / 原值 / 规范值 / 单位 / 时间范围）。 |
| **关系规则** | 与实体 / 指标 / 规范建 `about` / `justified_by`；与冲突对方建 `conflicts`；与更晚的结论建 `supersedes` / `restates`。 |
| **禁止** | ① 结论**永不**由系统自动发布（需求 §3.3 第 63 行"自动生成内容进入草稿"）；② 存在 `unresolved` 冲突时，结论**不得**显示为"已确认"，必须同时展示双方原值（需求 §6 第 137 行）；③ 不得生成或保存模型的隐藏思维过程，只呈现 `trace_events` 真实事件（需求 §3.3 第 67 行）。 |

### 2.5 四类之间的允许关系（邻接表，冻结）

| 主语 ＼ 宾语 | `business_entity` | `metric` | `norm` | `conclusion` |
| --- | --- | --- | --- | --- |
| `business_entity` | `entity_match`(建议) | `measures` / `applies_to` | `applies_to` / `scoped_by` | `about` |
| `metric` | `measures` / `applies_to` | `unit_equivalent` | `threshold_of` | `about` |
| `norm` | `applies_to` / `scoped_by` | `threshold_of` | `supersedes` / `version_scope` | `justified_by` |
| `conclusion` | `about` | `about` | `justified_by` | `conflicts` / `supersedes` / `restates` / `supports` |

关系类型的确切语义与到既有边的映射见 §3。上表里 `entity_match` 在实体↔实体之间出现，
是因为**合并建议**在 Wiki 里也是一条可见关系，只是它不能改身份。

---

## 3. 关系词表（冻结）

### 3.1 与既有图边的关系（不新造词表）

Wiki 关系类型**全部**来自下面两个既有来源之一，**不引入第三套词表**：

1. `datamind/evidence_graph.py:30-31` 的 `EDGE_TYPES`（6 个，新词表）；
2. `db/migrations/019_evidence_graph.sql:86-89` CHECK 里的旧词表（7 个，其中 `supports` 与 (1) 重合）。

`019` 的 CHECK 是**两套词表的并集**（`019:16-36` 的解释），所以库里出现旧词表**不是数据错误**，
而是 SQL/RAG/工业三条既有写入路径仍在用旧词表。Wiki 必须同时认这两套。

### 3.2 Wiki 关系类型全集

方向列：`→` 有向（保留端点顺序）／`—` 无向（存储时按节点 id 规范化，
见 `evidence_graph.py:41-42` 的 `SYMMETRIC_EDGE_TYPES`）。

| # | Wiki 关系类型 | 方向 | 语义（一句话） | 判定依据 |
| --- | --- | --- | --- | --- |
| 1 | `entity_match` | — | 两条证据指向同一实体 | 已批准别名或人工确认 |
| 2 | `version_scope` | → | 左端在右端声明的版本范围内适用 | 版本 / 有效期重叠 |
| 3 | `unit_equivalent` | — | 两条证据的单位可换算 | `binding.units_equivalent` |
| 4 | `time_overlap` | — | 两条证据的时间范围重叠 | `time_scope` 交集非空 |
| 5 | `supports` | → | 左端支撑右端 | 数值 + 出处一致 |
| 6 | `conflicts` | — | 两条证据的数值不可比或不等 | 同一 `(entity, metric, version)` 且具名记录相同 |
| 7 | `derived_from` | → | 左端由右端派生（如 `Claim` ← `SQLRow`） | 运行内的派生记录 |
| 8 | `retrieved_from` | → | 左端是从右端检索得到的（如 `Chunk` ← `Document`） | 检索记录 |
| 9 | `maps_to` | → | 两个词表的等价映射（如 schema 列 ↔ 指标） | 映射登记 |
| 10 | `contradicts` | — | 旧词表的矛盾（**语义与 `conflicts` 相同**） | 同 #6 |
| 11 | `verified_by` | → | 左端被右端核验 | 核验检查记录 |
| 12 | `repaired_from` | → | 左端是右端修复后的结果 | 修复账本 |
| 13 | `resolved_by` | → | 冲突被某个规则 / 裁决解决 | 权威规则或人工裁决 |
| 14 | `measures` | → | （**Wiki 派生，非既有边**）实体被指标度量 | 同一 `entity_id` + `metric` 共现 |
| 15 | `applies_to` | → | （Wiki 派生）规范 / 指标适用于某实体 | 规范的 `applicability` 覆盖该实体 |
| 16 | `scoped_by` | → | （Wiki 派生）实体 / 指标被某规范限定 | 同上，反向 |
| 17 | `threshold_of` | → | （Wiki 派生）规范给出某指标的限值 | 规范的 `normalized_range` |
| 18 | `about` | → | （Wiki 派生）结论 / 指标讨论某实体或指标 | 主张的 `entity` / `metric` 字段 |
| 19 | `justified_by` | → | （Wiki 派生）结论的依据是某规范 | 核验时使用的 `requirement_fact_id` |
| 20 | `supersedes` | → | 左端取代右端（换版） | 同一业务键、更晚的有效期 |
| 21 | `restates` | → | 两次运行得到同一主张 | 主张比较身份（§4.3）相同 |

**#14–#21 是 Wiki 派生关系，不是既有图边。** 它们必须标记 `origin='wiki_derived'` 并附推导规则版本；
`#1–#13` 是**投影关系**，必须标记 `origin='projected'` 并保留 `raw_edge_type` 原值。

> 冻结理由：需求 §5.2 第 122 行要求"读取历史版本时同时返回该版本的关系"。
> 如果 Wiki 只认既有 6 个新词表，则当前库 78% 的边（`derived_from` / `retrieved_from`）
> 会整批消失；如果 Wiki 另造一套词表，投影层就要做有损翻译。两者都不可接受，所以取并集 + 显式来源标记。

### 3.3 每个 Wiki 关系类型到既有边的映射（冻结）

| Wiki 关系 | `raw_edge_type` 原值 | 投影来源 | 库中出现情况（实测） |
| --- | --- | --- | --- |
| `entity_match` | `entity_match` | `evidence_edges`（6 列） | **0 行**（新词表未写入） |
| `version_scope` | `version_scope` | `evidence_edges` | **0 行** |
| `unit_equivalent` | `unit_equivalent` | `evidence_edges` | **0 行** |
| `time_overlap` | `time_overlap` | `evidence_edges` | **0 行** |
| `supports` | `supports` | `evidence_edges` | **682 行** |
| `conflicts` | `conflicts` | `evidence_edges` + `conflict_records` | 边 **0 行**；表 **175 行** |
| `derived_from` | `derived_from` | `evidence_edges` | **2 054 行** |
| `retrieved_from` | `retrieved_from` | `evidence_edges` | **375 行** |
| `maps_to` | `maps_to` | `evidence_edges` + `schema_relations` | 边 **0 行** |
| `contradicts` | `contradicts` | `evidence_edges` | **0 行** |
| `verified_by` | `verified_by` | `evidence_edges` + `VerificationCheck` 节点 | 边 **0 行** |
| `repaired_from` | `repaired_from` | `evidence_edges` + `repair_ledger` | 边 **0 行** |
| `resolved_by` | `resolved_by` | `evidence_edges` + `conflict_records.rule_id` | 边 **0 行** |
| `measures` / `applies_to` / `scoped_by` / `threshold_of` / `about` / `justified_by` / `supersedes` / `restates` | **`NULL`** | Wiki 推导（**不来自任何边**）；依据如下 | 不适用（`origin='wiki_derived'`） |

**8 个 Wiki 派生关系的推导依据（逐个，冻结；本条回应需求文档第 41 行"每个类型必须映射到现有边的实际类型"）**：

| Wiki 关系 | 推导自什么**实际字段**（不是边） | 判定 |
| --- | --- | --- |
| `measures` | `DocumentFact.payload.entity_id` + `.metric`；`SQLRow.payload.part_id` + `.metric` | 同一 `entity_key` 与同一 `metric` 共现 |
| `applies_to` | `DocumentFact.payload.valid_from` / `.valid_to` / `.conditions` 与实体/指标的 `time_scope` | 规范的有效期覆盖该实体 |
| `scoped_by` | 同 `applies_to`，**方向相反**（实体 → 规范） | 同上 |
| `threshold_of` | `DocumentFact.payload.normalized_range` / `normalized_value` + `fact_kind='requirement'` | 规范对某指标给出限值 |
| `about` | `claims.text` 对应的 `entity_key` / `metric`（由 `comparison_key` 给出） | 结论明确指向某实体或指标 |
| `justified_by` | `VerificationCheck.payload.requirement_fact_id` / `report_fact_id` | 核验时实际使用了该规范 |
| `supersedes` | 同业务键（`WIKI_DOMAIN.md` §4.1）下的两条规范 / 两条结论，`valid_from` 更晚 | **仅**按更晚的 `valid_from` 判定，不按 `revision` 字符串比较 |
| `restates` | §4.3 的 `comparison_key` 完全相同、但 `run_id` 不同 | 两次运行得到同一主张 |

**派生关系必须带 `derivation: { rule_version: 'wiki-derive-v1', inputs: [...] }`**，
以便历史版本读取时能复现推导（需求 §5.2 第 117 行）。

**`contradicts` 与 `conflicts` 是同一语义的两个词**（`conflicts` 是 `evidence_graph.py` 的新写法，
`contradicts` 在 `019` 的 CHECK 里）。Wiki **统一投影为 `conflicts`**，但在 `raw_edge_type`
里保留 `contradicts` 原值；`conflict_type` 另存细节（`value_conflict` / `unit_incomparable`）。
冻结理由：需求 §6 要求冲突展示双方依据，如果同一个语义有两种类型名，前端要写两条判定分支。

### 3.4 未知类型如何处理（需求文档第 41 行："未知类型保留原始类型并标注未支持"）

**规则（冻结）**：

1. `raw_edge_type` 与 `raw_node_type` 是**不可丢的原始值**：无论 Wiki 是否认识，都原样保存在
   关系 / 证据引用的 `raw_type` 字段里。
2. Wiki **认识**的类型 → `relation_type` 取 §3.2 的值，`supported=true`。
3. Wiki **不认识**的类型 → `relation_type=null`、`raw_type` 保留、`supported=false`、
   `unsupported_reason='TYPE_NOT_IN_WIKI_VOCABULARY'`。**不猜测、不映射到最近的类型、不丢弃。**
4. 未知类型的边**照常出现在图谱里**（画成细线 + "未支持关系类型"文字标识），
   只是不参与 Wiki 派生关系的推导（§3.2 #14–#21），也不参与"已核验"判定。
5. 未知类型的**节点**同理：`evidence_refs.node_type` 保留原值，`supported=false`；
   证据详情区显示原类型名 + "Wiki 未支持该类型，仅展示原始记录"。
6. 词表是**并集制**：新增词表时只需扩展 Wiki 词表 + 迁移里的 CHECK 同步扩展；
   已存在的 `supported=false` 记录在下次读取时按新词表重判（**重判只改 `supported`，
   不改历史版本的正文与引用**，符合 §5.2 的不可变要求）。

**当前必须标为"未支持"的具体清单**（实测，就是本库现状）：

| 原始类型 | 类别 | 行数 | Wiki 词表是否收录 | 处理 |
| --- | --- | --- | --- | --- |
| `derived_from` / `retrieved_from` / `supports` | 边 | 3 111 | ✅ 收录 | 正常投影 |
| `Claim` / `SQLQuery` / `SQLRow` / `Chunk` / `Document` | 节点 | 1 849 | ✅ 收录（映射见 §4） | 正常投影 |
| `DocumentFact` / `VerificationCheck` / `ConflictRecord` | 节点 | 910 | ✅ 收录（工业侧） | 正常投影 |
| 迁移宣告但本库未出现的 5 个新边词表 | 边 | 0 | ✅ 收录 | 收录但无数据，**不得声称已验证** |
| 迁移宣告但本库未出现的 9 个节点词表（`Question`/`PlanTask`/`TableCell`/`Formula`/`AuthorityRule` 等） | 节点 | 0 | ⚠️ 部分收录 | 未收录者走规则 3 |

> **诚实声明**：`sql_row` / `doc_block` / `measurement` / `spec_clause` 这四个
> **闭集词表**（`evidence_graph.py:29`）在当前库**一行都没有**。也就是说：
> "Wiki 能正确投影类型化证据图"这件事**在当前数据上无法被验证**，
> 只能靠合成 / 回放数据的测试来证明。P1/P2 不得把这条当作已验证。

### 3.5 冲突的两条来源路径（统一规则）

| 路径 | 触发 | 落库位置 | 载体 |
| --- | --- | --- | --- |
| **工业路径** | `agent.py:720` 写 `conflict_records` | `conflict_records` 表（175 行）+ `evidence_nodes` 的 `ConflictRecord` 节点（175 个） | `payload` JSONB 含 `left_fact_refs` / `right_fact_refs` / `normalized_values` / `comparison_context` / `rule_id` / `rule_version` / `resolution_status` |
| **跨源路径** | `cross_source.py:996` / `:1013` 的 `detect_conflicts` | **不写表**；随终态事件落 `trace_events.output_summary`（`agent.py:195-202` 的 `cross_source_result.conflicts` / `unresolved_conflicts`） | `evidence_graph.py:454-461` 每侧含 `node_id` / `value` / `unit` / `normalized_value` / `normalized_unit` / `source_sha256` / `version` / `record_scope` |

**两条路径都保留双方原值**（工业侧 `left_fact_refs` + `right_fact_refs`；
跨源侧 `left` + `right`），因此需求 §3.3 第 65 行"冲突保留双方值、来源和裁决依据"**已被既有数据满足**，
Wiki 只需如实投影。统一到响应结构的具体字段见 [接口契约](WIKI_API.md) §8。

**工业路径的三态必须保留**（§1.4）：`unresolved` / `needs_review` / `resolved_equivalent`
在三态基础上再加一个 `selected` 侧（`selected_fact_ref`）。Wiki 的冲突对象字段：

```
conflict_id      两者同名字段（工业 conf-xxxx；跨源 conf-<20hex>）
source_path      'industrial_table' | 'cross_source_trace'
conflict_type    'value_conflict' | 'unit_incomparable' | 'unit_equivalence' |
                 'measurement_context' | 'version_overlap'     （工业侧五类实测）
resolution       'unresolved' | 'needs_review' | 'resolved_equivalent' | 'resolved_by_registered_authority'
rule_id          工业侧为 rule_id；跨源侧为 'NO_REGISTERED_AUTHORITY_RULE' 或 'REGISTERED_AUTHORITY_RULE'
authority_rule_id
left / right     {node_id 或 fact_refs, value, unit, normalized_value, normalized_unit,
                  source_sha256, version, record_scope}
selected         null 或选中的那一侧引用（对应工业侧 selected_fact_ref）
```

**`selected_fact_ref=null` 时不得推测选择哪一侧**（需求 §3.3 第 65 行、§6 第 137 行）。
当前库 175 行冲突里 `selected_fact_ref` **全部为 null**（没有任何登记规则选中过某一侧）。

---

## 4. 三类身份规则（冻结）

三类身份**互相独立**，任一类的变化**不自动改变**另两类。每条指纹都给出精确字段构成。

### 4.1 词条身份（entry identity）

**目标**：同一知识对象在多次运行、多个来源版本之间保持**长期不变的身份**；
标题、摘要、正文的修改**不改变**身份。

```
entry_id   = 'wiki-'  + hex24                      （分配型，不可推导）
business_key = space_id | namespace | entry_type | key_part      （业务型，可推导）
```

| `entry_type` | `key_part` 精确构成 |
| --- | --- |
| `business_entity` | `entity_type + '\x1f' + canonical_entity_key`（`canonical_entity_key` 来自已批准别名或人工确认；**没有**则 `key_part = 'pending:' + lower(btrim(title))` 且 `confirmation='pending'`） |
| `metric` | `metric_id`（来源内规范名，如 `holding_temperature`、`demo_retail.sales_amount`） |
| `norm` | `source_document_id + '\x1f' + source_revision + '\x1f' + clause_key` |
| `conclusion` | `run_id + '\x1f' + claim_id` |

**规则**：

1. `business_key` 在空间内**唯一**。两个不同 `entry_id` 不得拥有同一 `business_key`。
2. 标题变更、别名增删、正文编辑：**不改** `business_key`，也**不改** `entry_id`。
3. `pending:` 前缀的实体键是**待确认**状态，**不得**被自动升级为确认键；
   升级只发生在**人工确认**或引用**已批准别名**时，且必须留下 `wiki_review_events` 记录。
4. 没有可靠 `business_key` 时**分配** `entry_id` 并标 `confirmation='pending'`，
   **不阻塞**草稿生成，但**阻止**发布（§5.1）。
5. **跨空间不归一**：`space_id` 是 `business_key` 的第一段，用户 A 的实体与用户 B 的同名实体
   是两条词条，避免跨空间存在性泄露（需求 §5.1 第 112 行）。
6. 沿用好消息：`evidence_graph.py:208-212` 在**图不带 `run_ref`** 时用纯内容 id，
   说明系统里**已经存在"不带 run 的稳定内容身份"这条路**。词条身份沿用它（内容哈希派生的
   `key_part`）即可，**不必另造**。但**不得**用带 `run_ref` 的节点 id 当词条身份——
   那个 id 每次运行都变（`evidence_graph.py:202-207`）。

### 4.2 证据身份（evidence identity）

**目标**：**同一个来源版本的同一段内容**，无论被多少次运行引用，只有**一个**证据身份；
不同来源版本即使内容相同也是**两个**身份。

```
evidence_fingerprint = sha256( canonical_json( {
    space_id,             # 空间隔离
    source_kind,          # 'sql' | 'doc'
    source_ref,           # 来源身份（source_id 或 document_id + revision）
    source_sha256,        # 来源版本/快照摘要（D 类必填）
    locator_kind,         # 'sql_record' | 'sql_aggregate' | 'pdf_region' | 'text_span'
    locator,              # 定位本体（见下）
    record_sha256         # 原始内容摘要
} ) )
```

| 字段 | SQL 证据（`source_kind='sql'`） | 文档证据（`source_kind='doc'`） |
| --- | --- | --- |
| `source_ref` | `source_id`（例：`demo_retail`） | `document_id` |
| `source_sha256` | `source_snapshot_id` 对应的快照摘要（例：`snap-demo_retail-9b6637f76e61`；工业侧 `e8541f8b…2323b7`） | `document_sha256`（例：`805851ba…fcd46401`） |
| `locator_kind` | `sql_record`（具名记录行）或 `sql_aggregate`（聚合结果） | `pdf_region`（页 + bbox）或 `text_span` |
| `locator` | 查询口径（`sql_hash`）+ 数据快照 + 行/聚合范围（`part_id`/`batch_id`/`measurement_id` 或聚合键） | `page_no` + `bbox` + `block_id` + `contract_version` |
| `record_sha256` | 行值或聚合值的规范化摘要 | 块内容 `content_hash` |

**规则**：

1. **来源快照缺失时保持运行隔离**：若 `source_sha256` 为空（历史数据里存在——实测
   `evidence_nodes` 的 `Document` 节点**两种摘要字段互斥**：13 个文档节点只有
   `document_sha256`、`doc-industrial-0ac6c55838c3` 的 35 个节点只有 `source_sha256`，
   **没有任何一个同时具备两者**），则指纹**必须**追加 `run_id`，即该证据只能被它自己那次运行引用。
   **不得**跨运行复用（需求 §5.1 第 111 行）。指纹计算必须显式声明取哪一个字段作为
   `source_sha256`：文档类证据取 `COALESCE(payload->>'document_sha256', payload->>'source_sha256')`。
2. 继续用原 `evidence_id`（`ev-` + hex16 或内容 id `n-` + 20hex）表示**一次运行中的出现记录**；
   新增的 `evidence_fingerprint` 通过 provenance 映射关联**所有**出现记录。
   一次运行 = 一条出现记录；一个指纹 = 一个可复用证据引用（需求 §5.1 第 112 行）。
3. 指纹**不含** `run_id`（除规则 1 的降级情形）、不含 `node_id`、不含 `confidence`、
   不含检索得分。**排序、检索得分和模型置信度不决定权威**（需求 §3.3 第 66 行）。
4. 同一指纹出现在两次运行时：`wiki_evidence_refs` 只有一行，`wiki_provenance_links` 有两行
   （需求 P1 验收："形成一个可复用证据引用和两条运行映射"）。
5. 来源换版（`document_sha256` 变化）→ **新指纹、新证据引用**；旧引用与旧结论保留
   （需求 §6 第 140 行）。

### 4.3 主张比较身份（assertion comparison identity）

**目标**：判断两条主张"是否在同一个比较组里"，**只有同组才可能判为冲突**。

```
comparison_key = sha256( canonical_json( {
    space_id,
    entity_key,        # 实体身份（§4.1 的 canonical_entity_key；未确认时用 pending 键）
    predicate,         # 指标或谓词（metric_id / fact_kind）
    applicability,     # 适用版本（source_revision / version / valid_from+valid_to）
    time_scope,        # 时间范围（from/to + timezone）
    record_scope,      # 具名记录（measurement_id / batch_id / operation_no / quality_id）
    statistic          # 统计口径（method / 聚合方式）；无则为 null
} ) )
```

**规则**：

1. 同一 `comparison_key` 下的**具体值分别存储**，不合并（需求 §5.1 第 110 行）。
2. **指标相同但时间或口径不同的主张不直接判为冲突**——它们连比较组都不同。
   这正是 `evidence_graph.py:425-431` 的"具名记录不同即不配对"规则。
3. 两侧**单位不同但可换算** → `unit_equivalent` / `resolved_equivalent`，**不是** `value_conflict`
   （`evidence_graph.py:440-453`：先判 `units_equivalent`，不可比才是 `unit_incomparable`）。
4. 两侧**来源相同** → 不配对（`evidence_graph.py:423-424`：同 `source_ref` 且同 `source_kind` 跳过）。
5. `comparison_key` 相同 + 规范化值不同 + 无登记权威规则 → 冲突，`resolution='unresolved'`；
   有登记规则 → `resolved_by_registered_authority`，但**冲突边与双方值仍然保留**
   （`evidence_graph.py:16-17`、`:471-475`）。

**本库的 `comparison_key` 实例**（工业侧，实测 `conflict_records.entity_key` 结构）：

```
{part_id: 'A102', batch_id: 'B001', operation_no: '30', measurement_id: 'M001-1'}
→ metric='holding_temperature', source_revision='A102-V2', method='core'
→ normalized_values {production: '870', report_raw: '850', report_unit: 'degC'}
→ conflict_type='value_conflict', resolution_status='unresolved',
  rule_id='NO_REGISTERED_AUTHORITY_RULE'
```

---

## 5. 三套独立状态维度（冻结）

需求 §6 的三张表落实如下。**核心禁令：三者不得互相推导。**

### 5.1 发布流程状态

| 状态 | 含义 | 进入条件 |
| --- | --- | --- |
| `draft` | 草稿版本，尚未提交复核 | 自动整理或人工新建版本 |
| `in_review` | 已提交复核，等待裁决 | `POST /v1/wiki/entries/{id}/reviews` |
| `published` | 该版本是词条的当前发布头 | `POST /v1/wiki/entries/{id}/publications` 且 CAS 通过 |
| `archived` | 词条归档，只读 | 显式归档动作 |

- **只表示"编辑与发布流程走到哪一步"，不表示事实成立。** `published` ≠ 已证实。
- 发布头采用**比较并交换（CAS）**：请求必须携带预期旧发布头，过时基线返回 `WIKI_VERSION_CONFLICT`。
- 发布必须记录：目标版本、操作者、动作、依据、时间、审核范围、预期旧发布头。
- `confirmation='pending'` 的词条**可进入草稿，不可进入 `published`**（§4.1 规则 4）。
- **允许发布"存在冲突"的说明词条**，但冲突主张在正文里必须始终展示双方依据，
  不能显示为已确认结论（需求 §6 第 137 行）。

### 5.2 主张核验状态

| 状态 | 含义 | 既有对应 |
| --- | --- | --- |
| `unverified` | 尚未核验（草稿里的原始主张） | 无 `claims` 行或只有人工录入 |
| `verified` | **在明确核验范围内**证据支撑该主张 | `claims.status='verified'` 且 `runs.status='COMPLETED'` |
| `insufficient` | 证据不足 | `claims.status='insufficient_evidence'` / 运行 `ABSTAINED`+`INSUFFICIENT_EVIDENCE` |
| `conflicted` | 同一比较组内存在未裁决冲突 | `conflict_records.resolution_status='unresolved'` 或跨源 `unresolved_conflicts` 非空 |

**`verified` 必须同时记录（缺一不可）**：核验对象、方法、覆盖范围、规则版本、操作者、时间。

**五项分别记账，不互相替代**（需求 §6 第 136 行）：

| 记账项 | 既有载体 | 本库实测 |
| --- | --- | --- |
| 来源完整性 | `source_sha256` / `document_sha256` / 快照摘要 | 部分缺失（§4.2 规则 1） |
| 实体绑定 | `binding.status` + `binding_problems`（`agent.py:618`） | 跨源路径才有 |
| 规则核验 | `verification_contract` + `validator_findings` | 实测 `time_scope_only` 最多 |
| 语义支持 | `full_semantic_entailment` | **恒为 `not_independently_verified`**（`agent.py:238`/`:629`） |
| 来源权威 | `source_authority_rules` / `source_authority_events` | 既有登记与审批机制，Wiki **不得**自动激活（需求 §6 第 138 行） |

**必须写进 UI 与响应的一点**：`semantically_verified=true` **只**在
`verification_contract='retail_metric_ast_contract'` 这类契约下出现；当契约是
`time_scope_only` 时它只证明"时间范围被规范化"，**不证明指标语义**。
Wiki 词条正文必须显式标注契约名，不能只显示"已核验"。

### 5.3 来源有效性状态

| 状态 | 含义 | 判定 |
| --- | --- | --- |
| `current` | 本次检查时来源版本适用且可访问 | 定位快照仍存在、权限仍通过、版本仍是引用时的版本 |
| `stale` | 来源已换版，引用仍指向旧版本 | `document_sha256`/快照与当前头不一致，但旧版本仍可读 |
| `unavailable` | 已撤权、已删除或定位不可用 | 权限失败或定位记录消失 |

**规则**：

1. 读取时**即时复查**，不缓存有效性结论。
2. 来源撤权或删除后：读取**即时过滤**受限内容；内部保留审计记录，用户侧只显示权限允许的状态提示
   （需求 §6 第 141 行）。**`unavailable` 时不得返回任何受限正文、摘要或计数。**
3. **禁止静默跳转到最新文档或最新 SQL 数据**：历史定位显式绑定旧版本
   （需求 §5.2 第 121 行）。判为 `stale` 时必须返回**旧版本的定位**与"待重新核验"提示。
4. `stale` / `unavailable` 的证据引用**不得**作为新版本"已核验"的依据：
   依赖它的新版本，其主张核验状态最多到 `insufficient`。
5. 软删除会话**不删除运行**；已发布 Wiki 引用的运行和来源不得经普通清理任务级联销毁
   （需求 §6 第 142 行；分析报告 §3.8 已核实既有实现满足此要求，Wiki 侧只需**不破坏**它）。

### 5.4 三者不得互相推导（逐条禁令）

| 禁令 | 说明 |
| --- | --- |
| **不得**由 `published` 推导 `verified` | 发布是流程，核验是证据；`published` + `unverified` 是合法组合（例如"已知存在分歧，暂按 B 版执行"的说明词条） |
| **不得**由 `verified` 推导 `current` | 核验发生在过去；来源后来换版即为 `stale`，核验结论**不因此变为假**，但当前视图必须提示待核验 |
| **不得**由 `current` 推导 `verified` | 来源仍可访问，不等于证据支撑该主张 |
| **不得**由 `conflicted` 推导"未发布" | 允许发布冲突说明词条（§5.1） |
| **不得**由 `stale` 推导"结论作废" | 旧版本正文、引用与关系**不可变**（需求 §5.2 第 116 行） |
| **不得**由 `runs.status` 推导 `claims.status` | 本库实测：`COMPLETED` 运行里大量主张的 `verification_contract='time_scope_only'`；反过来 `claims.status='verified'` 的判据含"运行完成"，但那是**既有**判据，Wiki 不复用它 |

> **实现在此处的唯一义务**：任何一处把三个维度合并成"一个总状态"的地方（响应字段、
> 列表筛选、图例、导出），都是**契约违规**。三套状态在 API 响应里必须是三个独立字段
> （见 [接口契约](WIKI_API.md) §7）。

---

## 6. 词条版本、引用与关系的记账（需求 §5.2 的领域侧）

本节只冻结**语义**，表结构属 P1。

1. **新编辑创建新版本**，并**显式携带基线版本** `base_revision_id`。已保存版本的正文与引用
   **不可原地覆盖**。
2. **主张、引用与关系均绑定版本**。读取历史版本时**同时返回该版本的关系和核验记录**。
3. **发布头必须指向同一词条的版本**（复合外键约束语义），CAS 失败返回 `WIKI_VERSION_CONFLICT`。
4. **人工调整数值、引用、单位、适用范围或关系依据后**，创建新版本并**重新核验**；
   不得沿用旧版本的核验结论。
5. **新运行投影保存完整边元数据**；历史缺失边理由时记 `provenance_incomplete`，
   **不得推测补全**（需求 §5.2 第 120 行；字段语义见 [接口契约](WIKI_API.md) §6）。
6. **历史来源定位显式绑定旧版本**，禁止静默跳转到最新文档或最新 SQL 数据。
7. 词条携带的版本账是**第三份**账，与 §1.8 的 `runs.versions` / `claims.verification` **不合并**。

---

## 7. 补回被 (1) 版弱化的两条（需求 §3.3 与 §8 的领域义务）

分析报告 §3.9 指出 (1) 版相对旧版弱化了 4 处，其中 2 处**已确认补回**。它们不是纯前端问题，
因此在这里冻结领域义务，前端实现在 P4。

### 7.1 运行轨迹必须显示**错误摘要**

- **领域义务**：`wiki_provenance_links` 的每一条运行映射，必须能取到该运行的
  **错误摘要**，且轨迹视图必须显示它。
- **错误摘要的构成**（全部来自 `trace_events` 既有列，不新增字段）：
  - `error_code`（`VARCHAR(64)`，实测取值见下）
  - `event_type` + `status` + `agent_name`（定位是哪个环节失败）
  - `output_summary.error` / `output_summary.detail`（若存在）
  - 发生时间（`started_at`）
- **实测的错误码全集**（本库 `trace_events.error_code`，8 种）：
  `REPAIR_BUDGET_EXHAUSTED`(40) / `INSUFFICIENT_EVIDENCE`(12) / `SQL_POLICY_BLOCKED`(6) /
  `AMBIGUOUS_QUERY`(6) / `MODEL_EMPTY_RESPONSE`(4) / `MODEL_KEY_MISSING`(4) /
  `INTERNAL_ERROR`(2) / `INDUSTRIAL_SCOPE_REQUIRED`(2)。
- **禁止**：错误摘要**不得**包含模型隐藏思维过程、凭据、内部绝对路径或私有执行会话载荷
  （需求 §3.3 第 67 行、§7 第 167 行）。`trace_events.visibility` 必须被尊重。
- **降级规则**：运行没有错误时，错误摘要位置显示"本次运行无错误事件"，
  **不是**留空、**不是**显示 `null`；读取失败时显示"错误摘要不可用"，
  **不得**显示成"无错误"。
- **P4 验收补充**：轨迹时间线里，错误事件的视觉权重**不得低于**普通事件；
  错误摘要必须能在不展开完整事件负载的情况下读到。

### 7.2 窄屏下**来源和轨迹保持可访问**

- **领域义务**：窄屏（关系图与详情分步切换）时，**来源证据与运行轨迹必须仍可到达**。
  "分步切换"可以改变到达路径，**不可以**把这两个图层或它们的入口隐藏掉。
- **最小要求**：
  1. 窄屏下必须存在**可聚焦**的入口，能打开选定词条的来源证据与运行轨迹（键盘可达、屏幕阅读器可读）；
  2. 窄屏下进入详情后，**必须能返回关系图并保留原选中词条**（需求 §8.1 第 181 行）；
  3. 来源定位（页 / 块 / bbox）与事件时间线在窄屏下**不得**被裁掉或折叠成不可展开；
  4. 右上四组折叠设置面板在窄屏**可整体收起**，但收起**不得**连带隐藏来源 / 轨迹入口。
- **禁止**：以"窄屏空间不足"为由把来源或轨迹降级成"仅桌面可见"。
  替代目录（§8.1 的"替代目录"）是兜底路径，**但不是**豁免理由——它也必须能在窄屏完成同样的追溯任务。
- **P4 验收补充**：窄屏断言必须**覆盖**"从关系图 → 选中词条 → 打开来源证据 → 展开事件时间线 →
  回到关系图"这条完整链路，而不只是"页面能渲染"。

---

## 8. 未解决项与不确定处（P0 结束时如实记录）

| # | 事项 | 状态 | 影响 |
| --- | --- | --- | --- |
| 1 | `deploy/init/02_metadata.sql` 与 `db/migrations/019` 谁是权威 | **未确定** | 两者的 `evidence_edges` / `evidence_nodes` 形状不一致（§1.1）。**线上库现在的形状两处都不完全吻合**（`run_id` 可空、无 CHECK、无 `evidence` 列、有 `weight`），说明线上是由 deploy/init 建的、且 `02_metadata.sql` 的版本也早于 `019`。P1 必须以**线上实际形状**为兼容基线 |
| 2 | 类型化证据图（4 个闭集节点词表）无任何线上数据 | **未验证** | §3.4 的诚实声明。词表投影正确性只能靠测试证明 |
| 3 | 跨源核验在本库零运行 | **未验证** | §1.3。§3.5 的跨源路径投影**未被真实数据覆盖** |
| 4 | `reviewer` 与 `approver` 的实际人员分配 | **未知** | 权限矩阵（[接口契约](WIKI_API.md) §4）按代码语义写；若实际部署只有 admin 一个角色，矩阵里 `reviewer`/`approver` 两列在演示环境恒为空 |
| 5 | `DATAMIND_LOCAL_NO_LOGIN=1` 的部署实践 | **未知** | 一旦设置，任意来源获得 admin（`auth.py:59-63`），Wiki 的权限矩阵在演示环境**全部失效**。见 [接口契约](WIKI_API.md) §4.3 |
| 6 | 中文正文检索策略 | **未定** | 需求 §7 第 171 行要求在样本验证后确定。本库可用的中文语料与关键词样本见 [接口契约](WIKI_API.md) §11 |
| 7 | 规范的 `source_revision` 与 `valid_from/valid_to` 语义对应 | **存疑** | 实测 `source_revision='A102-V2'` 的 `valid_from=2026-09-01`、`A102-V3` 的 `valid_from=2026-09-15`，但工业核验的 `recorded_revision` 恒为 `A102-V2`（实测 350 行 `VerificationCheck`）。**哪个版本对哪一批次生效，需要业务确认**，Wiki 只如实呈现 |
| 8 | "词条正文 vs 关系图"谁是 `/#/wiki` 默认入口 | **已反转，按新版** | 分析报告 §3.9 记录这是有意的反转：新版 `/#/wiki` 默认展示关系图，词条深链 `/#/wiki/:entryId` 直接打开正文。**本条按新版冻结**（用户已确认） |
| 9 | 力导向布局是否引入 d3 | **已拍板：自研** | 理由见 [接口契约](WIKI_API.md) §9 |
| 10 | 既有问题 ① `/v1/runs` 无 ACL 过滤（分析报告 §6①） | **已知，本阶段不动手** | `api.py:1564-1583` 的处理函数不接收 identity、SQL 无 ACL 过滤，匿名者可列出全部 run 的 question/status。**Wiki 不得复用该接口做授权**（需求 §2 第 42 行）。是否单独修由你决定 |
| 11 | 既有问题 ② `/v1/runs` 不过滤软删会话 | **已知，本阶段不动手** | 软删会话后该 run 仍会列出并显示 `conversation_id`（分析报告 §6②） |
| 12 | 既有问题 ③ `evidence_edges` 迁移/部署不一致 | **已在 §1.1 记录** | 是否修由你决定；Wiki 侧的处理方式（记 `provenance_incomplete`）已冻结 |

---

## 9. 本文的核实方式

- 代码事实：只读检索，每条附 `文件:行号`。
- 数据事实：只读 SQL，容器 `datamind-postgres`，库 `datamind_meta`，用户 `postgres`。
  具体查询结果见 [接口契约](WIKI_API.md) §11 与本文各节的实测表。
- **未做**：没有修改任何现有文件，没有新建表，没有执行迁移，没有提交 git。
- **未核实**：`/#/wiki` 页面的真实渲染表现、100 节点/300 边的实际帧率（需 P4 样本验证）、
  中文检索策略的实际召回（需关键词样本验证）。
