# R2 安全与向后兼容修复 · 结论卡

| 字段 | 内容 |
|---|---|
| **WP** | 修复轮 2「安全与向后兼容」——逐条修 `reports/review_round1_security.md` §5 的高/中级发现（H1 / M1 / M2 / M3 / M4 / L2 / L3 / L5），H2 只改文档文字，L4 保留原样并说明理由 |
| **日期** | 2026-10-10（北京时间；机器时钟为 2026-10-09 23:2x–23:4x CST） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`，exit 0，只读）。本轮**未做任何 git 写操作** |
| **API 版本** | `0.39.0`（`curl -s http://127.0.0.1:8000/openapi.json` → `api_version=0.39.0`、`paths=75`，exit 0）；`GET /health` → `{"status":"ok","database":"ready",...}`，exit 0。**未改 API 版本号、未改路由** |
| **问题** | 复查发现：跨源路径漏检空 allow-list（H1，可越权自动选源）；跨源集合白名单写错名字（M2，主 demo 知识库被误拒）；线上带时间过滤的跨源必然绑定失败（M1）；别名注册表不可用时实体身份静默降级（M4）；019 注释两处不实声明 + fresh 顺序与 legacy 写入者不兼容（M3）；`degF`/`万元` 合法单位被硬拒（L2）；澄清面板无样式（L3）；SSE 与落库事件不一致（L5）；文档仍把已退役的 `ROUTE_UNSUPPORTED` 写成现行行为（H2） |
| **做法** | 只改被指定的文件：`datamind/agent.py`、`datamind/cross_source.py`、`datamind/binding.py`、`db/migrations/019_evidence_graph.sql`、`web/style.css`、`docs/` 三份文件；新增唯一命名测试 `tests/test_security_round2.py`（31 项）。每条都有「改前复现 → 改后对照」；H1/M1/M2/M4/L2 用**离线纯函数**复现（不调模型、不写演示库），M3 用**一次性临时库**真实执行迁移，L5 用**改前顺序的逐字复刻**做反例，H2 只改文档文字、**不把旧码加回来** |
| **数据与集** | ① 离线最小证据容器 **自造**（非评测集）：1 条 SQL 行 + 1 个文档块 / 1 条条文，不外推准确率；② 只读查询：`datamind_meta`（`document_assets.collection` 分布）、`Catalog(_read_dsn(),'demo_retail')` 的真实 `query_frame`；③ M3 一次性临时库 `datamind_fresh_ck_<unix>`（`createdb` 建、验证后 `dropdb` 删，**从不碰 `datamind_meta`**）；④ 隔离回归用 `datamind_test_*` 克隆库。**全程 0 次模型调用、0 次评测集运行、未写 demo_retail/demo_industrial** |
| **结果（分子/分母）** | ① 新增测试 `tests/test_security_round2.py`：**31/31 通过**（exit 0）；② 指定的 5 个 pattern + 4 个副作用自查 pattern：`test_cross_source.py` **30/33**、`test_clarification_flow.py` **23/23**、`test_agent_api.py` **4/5**、`test_multi_turn.py` **33/33**、`test_frame_multi_turn.py` **30/30**、`test_binding_eval.py` **32/32**、`test_frontend_hygiene.py` **8/8**、`test_sql_scope_variants.py` **21/21**、`test_industrial.py` **11/11**、`test_rag.py` **11/11**；合计 **238 项执行 / 4 失败**；③ 发现修复：高 **1/2**（H1 已修；H2 运行时确认已退役、文档已改，测试断言由 R2 负责）、中 **4/4**、低 **4/5 已修 + L4 保留原样并说明**；④ `node --check web/app.js` exit 0（1/1）；⑤ 019 临时库 fresh 顺序：改前 `legacy_edge_rows=0` + NOT NULL 报错 → 改后 `legacy_edge_rows=2`、显式空白 justification 仍被 CHECK 拒 |
| **失败样本** | ① **M2 修复的下游**：`tests/test_cross_source.py` 3 条 `AgentWiringTest` 变红（`FAILED_WITH_EVIDENCE/COLLECTION_NOT_ALLOWED`），因为它们在 `tests/test_cross_source.py:560` 用 `document_collections=['public_kb']` —— 一个**从不存在的集合名**，其通过本身就依赖 M2 的缺陷。该文件是第一轮新增的 **untracked** 文件（`git ls-files` 未跟踪），不是基线冻结契约；但按硬性禁令 #5 我**未改该测试文件**，留待协调者决定（修法见 §M2）；② `tests/test_agent_api.py:110` 的 `ROUTE_UNSUPPORTED` 断言仍红（这是**真正被跟踪的既有契约测试**，由 R2 回归修复负责，不属于我）；③ `L4` 保留原样（理由见 §L4）。以上失败样本**全部保留、未隐藏、未用 `--pattern` 之外的过滤规避** |
| **复现命令** | 逐条见 §1–§9；网关级三条：`git rev-parse --short HEAD`；`curl -s http://127.0.0.1:8000/openapi.json \| .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"`；`node --check web/app.js`。回归：`.venv/bin/python scripts/test_isolated.py --pattern test_security_round2.py`（其余 8 个同形，完整清单见 §11）。**未跑无参数全量**（硬性禁令 #4） |
| **诚实边界** | ① 本轮**没有跑无参数全量隔离回归**，因此「全量绿/红」不由本卡背书；后台全量（1497 项 / 6 失败，`ran_at 2026-10-09T15:24:54Z`）是在我改动**之前**完成的，是改前基线而非本轮结论；② 未对 8000 端口发 `/v1/chat`，H1 的端到端越权未抓包（守卫 + `plan()` + `live_sql_evidence` 三层逻辑已分别实证）；③ M3 的 fresh 顺序只验证到「最小 `runs` 桩 + 019 + legacy 列清单」，**没有复现完整 `deploy/init`**，「fresh-first + 完整 init」仍为**未验证**；④ 未做渗透测试/并发；⑤ L2 的 `degF` 只在结果是有尽小数时可换算（`70 degF` 仍拒，与 `s→min` 同纪律），不是「degF 全可换算」；⑥ L3 只做了样式存在性与 `node --check`，**未在浏览器渲染验证观感**；⑦ L4 未下沉；⑧ 未触碰 `README.md`（其 :74 仍写 `ROUTE_UNSUPPORTED`，由文档智能体负责），也未触碰 `PROGRESS.md`、`reports/latest_regression.json` 的口径 |
| **评分联动** | F13（工程纪律与安全性）、E9/F7（鲁棒性与权限边界）：H1 修复把跨源路径的权限边界拉回与 SQL 路径同形，「权限最小化」可再声称；H2 文档退役后，跨源失败码叙事与运行时一致（E1/E5 的证据链）；M1 直接恢复 F1（跨源）在**带时间过滤**场景的可演示性（改前必 abstain）；M2 让 `demo_kb` 可用于跨源（F1/F5）；M4/L2/L5 属「不猜测 + 可回放」的诚实性加分项 |

---

## 0. 一句话结论

**H1 已修且三层证据齐备；M1/M2/M3/M4/L2/L3/L5 已修并各带前后对照；H2 只改文档文字（运行时早就 0 处旧码）；L4 按「风险 > 收益 + 禁令 #5」保留原样并写明。**
代价是 `tests/test_cross_source.py`（第一轮新增的 untracked 测试文件）3 条用例暴露了它们自身依赖 M2 缺陷，按禁令我未修改该文件，已升级为需协调者裁决的交付项。

---

## 1. H1（高）跨源路径漏检「空 allow-list」

**改前复现**（`.venv/bin/python`，exit 0，原样摘录）：

```
== H1: guard truth table (agent._run_cross_source style) ==
  sources=[]                   old_rejected=False  sql_path_rejected=True
  sources=['demo_retail']      old_rejected=False  sql_path_rejected=False
  sources=['nope']             old_rejected=True   sql_path_rejected=True
  sources=None                 old_rejected=False  sql_path_rejected=True
  route_for('查询销售额前三产品并结合市场分析报告说明原因') = cross_source
```

即：`[]` 在 SQL 路径被拒、在跨源路径**不拒**，且该问法确实进跨源分支。

**改法**：
1. `datamind/agent.py` 新增单一来源常量 `DATABASE_SOURCES = ("demo_retail","chinook","demo_industrial")`，SQL 路径与跨源路径**同形**守卫：`if not sources or any(s not in DATABASE_SOURCES for s in sources)`。
2. `datamind/cross_source.py:live_sql_evidence` 在 `sources == []` 时抛 `CrossSourceError('SOURCE_NOT_ALLOWED','EMPTY_ALLOW_LIST')`，**不再** `(sources or [None])[0]`；`run()` 对 `CrossSourceError` 不再吞进 warnings 而是上抛；`agent._run_cross_source` 新增 `except cross_source.CrossSourceError` → 直接返回该稳定码。
3. `cross_source.plan()` 在空 allow-list 时**先于任何 adapter** 返回结构完整的 fail-closed 结果（`status=failed`、`error_code=SOURCE_NOT_ALLOWED`、`failure_detail=EMPTY_ALLOW_LIST`）。

**改后对照**（exit 0，原样摘录）：

```
== H1 after ==
  plan(sources=[]) -> failed SOURCE_NOT_ALLOWED EMPTY_ALLOW_LIST | sources: []
  plan(sources=['demo_retail']) -> completed None
  live_sql_evidence([]) raised: CrossSourceError SOURCE_NOT_ALLOWED EMPTY_ALLOW_LIST
```

**测试**：`H1EmptyAllowListTest` 5 项（守卫真值表 / `plan` 拒答 / SQL 证据入口抛码 / 非空 allow-list 仍 completed / agent 端 `database_sources=[]` → `FAILED_WITH_EVIDENCE`+`SOURCE_NOT_ALLOWED`、`['demo_retail']` → `COMPLETED`）→ 全绿。

---

## 2. H2（高）`ROUTE_UNSUPPORTED` 退役

- **运行时确认**：`grep -rn "ROUTE_UNSUPPORTED" datamind/` → **0 命中**（exit 1）。运行时只会产生 `CROSS_SOURCE_EVIDENCE_INSUFFICIENT` / `DOC_BINDING_MISSING` / `CONFLICT_UNRESOLVED`。**没有**把旧码加回来。
- **文档文字**（只改文字，共 3 份文件 5 处）：
  - `docs/OFFICIAL_REQUIREMENTS_MAPPING.md:111`（F1 行）
  - `docs/MULTI_TURN.md:93`
  - `docs/SCORE_GAP_LEDGER.md:113 / :144 / :159`（台账行，保留基线历史，逐处标注「该码**已退役**，仅描述 `e4e2ebf` 基线」）
- **改后核验**：

```
$ grep -rn "ROUTE_UNSUPPORTED" docs/ | wc -l          → 5
$ grep -rn "ROUTE_UNSUPPORTED" docs/ | grep -vc "退役" → 0   （每一处都带退役标注）
$ grep -rn "ROUTE_UNSUPPORTED" datamind/              → 无输出（exit 1，0 处）
```

- **边界**：`README.md:74` 仍写旧码，但该文件**不在我的可改清单内**（明确划给文档智能体），故未动；`tests/test_agent_api.py:110` 的旧断言由 R2 回归修复负责。我的测试 `H2RetiredCodeDocumentationTest` 只对 `datamind/` 与上述 3 份 docs 断言，避免越权。

---

## 3. M1（中）带时间过滤的跨源必然绑定失败

**根因链**：`sql_engine.query_frame` 的 `time_filters` 是「裸日期 `from/to` + 兄弟键 `timezone`」→ 旧 `_frame_scope` 只取 `from/to` → `binding._scope_bounds` 对裸时间戳返回 `None` → `TIMEZONE_REQUIRED` → `SQL_CLAIM_BINDING_INCOMPLETE:time_scope` → abstain。

**改前复现**（exit 0，原样摘录）：

```
== M1: _frame_scope drops timezone ==
  _frame_scope -> {'start': '2025-01-01', 'end': '2026-01-01'}
  binding._scope_bounds -> None
```

真实形状取证（只读，不调模型）：

```
real sql_engine.query_frame time_filters = [{'field_id': 'orders.created_at', 'from': '2025-01-01', 'to': '2026-01-01', 'timezone': 'Asia/Shanghai'}]
```

全链路改前（把 row 的 `time_scope` 置为 `_frame_scope(...)`，与 `live_sql_evidence` 同路径）：

```
  timezone dropped (pre-fix naive scope)     status='abstained'  error='CROSS_SOURCE_EVIDENCE_INSUFFICIENT' detail='SQL_CLAIM_BINDING_INCOMPLETE:time_scope'
```

**改法**：`_frame_scope` 取 `item['timezone']`，用 `_aware_iso()` 拼成 tz-aware ISO（`2025-01-01T00:00:00+08:00`）；已是 aware 的值原样保留；时区名不可用时**不猜测**、原样返回（下游照旧拒答）。

**改后对照**（exit 0，原样摘录）：

```
== M1 after ==
  _frame_scope -> {'start': '2025-01-01T00:00:00+08:00', 'end': '2026-01-01T00:00:00+08:00'} | _scope_bounds -> True
  no-timezone frame -> {'start': '2025-01-01', 'end': None}
```

```
  timezone dropped (pre-fix naive scope)     status='abstained'  error='CROSS_SOURCE_EVIDENCE_INSUFFICIENT' detail='SQL_CLAIM_BINDING_INCOMPLETE:time_scope'
  timezone kept (post-fix aware scope)       status='completed'  error=None detail=None
```

**测试**：`M1FrameScopeTimezoneTest` 6 项（真实形状→aware / 无时区仍拒 / 坏时区不猜 / 显式 offset 不动 / 全链路改前 abstain 改后 completed / 与真实 `sql_engine.query_frame` 形状对照）→ 全绿。纪律不变：**无时区 → 仍拒答**。

---

## 4. M2（中）跨源集合白名单写错名字

**改前复现**（exit 0，原样摘录）：

```
== M2: cross-source collection whitelist vs COLLECTIONS ==
  knowledge.COLLECTIONS = ['demo_kb', 'industrial_demo', 'ohrbench_kb']
  agent literal        = ['industrial_demo', 'ohr_bench', 'public_kb']
  demo_kb accepted by cross-source guard? False
```

只读实证真实集合（`document_assets.collection` 分布）：

```
collections: [('demo_kb', 15), ('industrial_demo', 1), ('ohrbench_kb', 14)]
```

→ `public_kb` / `ohr_bench` **不存在**；`demo_kb` 被误拒。同时 `datamind/agent.py` 的重复数据源字面量也收敛为单一常量：三个数据源名的字面量在改前出现 **3 处**（`run()` 默认列表、SQL 路径 `set`、跨源路径 `set`），改后 **1 处**（`DATABASE_SOURCES`）。

**改法**：跨源守卫改为 `if collections is not None and any(c not in knowledge.COLLECTIONS for c in collections)`（单一来源，第二份白名单删除）。

**改后对照**（隔离回归，exit 0）：

```
test_every_real_collection_is_accepted_by_the_agent ... ok      # demo_kb / ohrbench_kb / industrial_demo 三个真集合均 COMPLETED
test_the_legacy_misspelled_collection_is_now_refused ... ok     # public_kb → FAILED_WITH_EVIDENCE / COLLECTION_NOT_ALLOWED
```

**⚠️ 下游代价（必须裁决）**：`tests/test_cross_source.py:560` 用 `document_collections=['public_kb']` 构造 `AgentWiringTest`，改后 3 条用例变红（原样摘录）：

```
AssertionError: Tuples differ: ('FAILED_WITH_EVIDENCE', 'insufficient') != ('COMPLETED', 'medium')
AssertionError: Tuples differ: ('FAILED_WITH_EVIDENCE', 'COLLECTION_NOT_ALLOWED') != ('ABSTAINED', 'CONFLICT_UNRESOLVED')
AssertionError: Tuples differ: ('FAILED_WITH_EVIDENCE', 'COLLECTION_NOT_ALLOWED') != ('ABSTAINED', 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT')
```

这 3 条不是「被修坏」，而是**它们把不存在的集合名当成合法输入**（`test_cross_source.py:301/381` 走 `plan()` 不受守卫影响，只有 `:560` 受影响）。补充事实：该文件是**第一轮新增的 untracked 文件**（`git ls-files --error-unmatch tests/test_cross_source.py` → 未跟踪），不是基线冻结的契约测试；但硬性禁令 #5 只允许我改指定文件、并明确点名了另外两个测试文件，故我**未修改** `tests/test_cross_source.py`。
**建议修法（交由协调者或其指定智能体）**：把 `tests/test_cross_source.py:560` 的 `document_collections=['public_kb']` 改为 `['demo_kb']`；`:301/:381` 可一并改为 `demo_kb` 以保持语义为真。这是 1 行改动，不改变任何断言语义。

---

## 5. M3（中）019 注释不实 + fresh 顺序与 legacy 写入者不兼容

**改前真实复现**（一次性临时库 `datamind_fresh_ck_<unix>`，最小 `runs` 桩 → 旧 019 → legacy 写入者列清单；`createdb`/`dropdb` 都是我建的临时库，**未碰 `datamind_meta`**；原样摘录）：

```
CREATE TABLE
CREATE TABLE
DO
INSERT 0 1
INSERT 0 1
ERROR:  null value in column "evidence" of relation "evidence_edges" violates not-null constraint
DETAIL:  Failing row contains (1, run-1, ev-1, ev-2, derived_from, null, null, null, 2026-10-09 23:25:09.894855+08).
 legacy_edge_rows 
------------------
                0
```

**改法**：
1. `evidence TEXT` 由 `NOT NULL` 改为**可空 + 非空白 CHECK**：`CHECK (evidence IS NULL OR length(btrim(evidence)) > 0)`。理由写进文件：**本仓库所有写入者都不带该列**（`datamind/agent.py` 三处 + `datamind/evidence_graph.py:persist`），因为线上 deploy/init 形状根本没有该列（只有 `weight`）；`NOT NULL` 会让 fresh-first 顺序下的 legacy 写入全部失败。
2. 注释**说实话**，并更正复查报告的一处机制误判：harness **是**会套用迁移的，但走的是间接链 `scripts/test_isolated.py → db/seed_industrial.py → db/migrate.py`（后者 `glob("db/migrations/*.sql")` 并 `print("applied", path.name)`），且作用于**已初始化的克隆**，`CREATE TABLE IF NOT EXISTS` 命中旧表 → **section 1 的 fresh 分支从未被执行**。原注释声称「`test_isolated.py` 会应用 `db/migrations/` 下每个文件」不准确（不是它直接做的，且不是 fresh 库）；原注释暗示「union 词表让 legacy 写入者继续工作」也漏掉了 `evidence NOT NULL` 这一条致命列。
3. `COMMENT ON COLUMN evidence_edges.evidence` 文案同步改为「可空、提供时不得空白」。

**改后对照**（同样的一次性临时库，exit 0，原样摘录）：

```
INSERT 0 1
INSERT 0 1
INSERT 0 1
INSERT 0 1
ERROR:  new row for relation "evidence_edges" violates check constraint "evidence_edges_evidence_check"
DETAIL:  Failing row contains (3, run-1, ev-3, ev-1, supports, null, null,    , 2026-10-09 23:30:23.789827+08).
 legacy_edge_rows | null_evidence_rows 
------------------+--------------------
                2 |                  2
```

即：legacy 写入者（不带 `evidence` 列）**现在可写入**（2 行、`evidence` 均为 NULL），而**显式提供的空白 justification 仍被 CHECK 拒绝**（第 3 行报错）。

**测试**：`M3MigrationFreshOrderTest` 3 项（列定义不得含 `NOT NULL` 且 CHECK 存在 / 所有写入者的 `INSERT INTO evidence_edges(...)` 列清单都不含 `evidence` / 注释与调用链必须同事实）→ 全绿。

**未验证（诚实边界）**：临时库只搭了最小 `runs` 桩，**没有复现完整 `deploy/init`**，所以「fresh-first + 完整 init」的端到端顺序仍是**未验证**；该结论已写进 019 注释本身。

---

## 6. M4（中）别名注册表不可用时实体身份静默降级

**改前复现**（exit 0，原样摘录；此时 `plan` 还没有 `alias_registry_available` 这一维）：

```
aliases=None (registry not consulted)      status='completed'  error=None entity_match_kinds=['canonical_literal']
aliases=[] (registry UNAVAILABLE)          status='completed'  error=None entity_match_kinds=['canonical_literal']
```

即：`load_aliases` 抛错时 `aliases=[]` + warning，实体全部退化成表面字面串（`match='canonical_literal'`），仍判 `bound`，仍可 `completed`。

**改法**：把「注册表不可用」与「无此别名」分开——
- `run()` 里 `load_aliases` 失败时置 `alias_registry_available=False`（不再只是 append warning）；
- `plan(..., alias_registry_available=...)` 把该事实带进裁决：`_entities_verified()` 在「注册表不可用 且 存在 `canonical_literal` 实体」时为 False；
- `_decide` 在 `unbound_sql` 之后、任何 doc 侧放行之前降级：`abstained` + `CROSS_SOURCE_EVIDENCE_INSUFFICIENT` + detail `ENTITY_UNVERIFIED:ALIAS_REGISTRY_UNAVAILABLE`；
- 结果体新增 `alias_registry_available` 键（便于回放区分），`warnings` 里的 `ALIAS_REGISTRY_UNAVAILABLE:*` 保留。

**改后对照**（exit 0，原样摘录）：

```
== M4 after ==
  alias_registry_available=True, aliases=[]      status='completed'  error=None detail=None
  alias_registry_available=False, aliases=[]     status='abstained'  error='CROSS_SOURCE_EVIDENCE_INSUFFICIENT' detail='ENTITY_UNVERIFIED:ALIAS_REGISTRY_UNAVAILABLE'
```

**测试**：`M4AliasRegistryTest` 3 项（打桩 `load_aliases` 抛错 → 不得 completed（如实为 abstained + 该 detail + warning）；**正向对照**：同一份证据在注册表可用时仍 `completed`，证明不是一律拒答；`plan` 层标记与降级）→ 全绿。

---

## 7. L2（低）`degF` / `万元` 合法单位被硬拒

**改前复现**（exit 0，原样摘录）：

```
== L2: units accepted by clause regex but unregistered ==
  unit_status('degF') -> {'unit': None, 'unit_unknown': True, 'dimension': None, 'reason': 'UNIT_UNKNOWN'}
  unit_status('万元') -> {'unit': None, 'unit_unknown': True, 'dimension': None, 'reason': 'UNIT_UNKNOWN'}
  unit_status('degC') -> {'unit': 'degC', 'unit_unknown': False, 'dimension': 'temperature', 'reason': 'OK'}
```

**改法**（`datamind/binding.py`）：
- `万元`：登记为货币量纲的普通换算（`factor=10000`，有尽小数），别名表补 `degf / °f / 华氏度 / fahrenheit`。
- `degF`：`5/9` **不是**有尽小数，因此**不放进**十进制单位表，而是放进新的 `_EXACT_UNITS`（`Fraction(5,9)`、`Fraction(-160,9)`），走 `_normalize_exact()`：**只有当结果是有尽小数时才返回 Decimal**，否则与 `1 s → min` 同纪律**拒绝**（不四舍五入）。既有单位的十进制路径逐字未动（`1123.15 K → 850.00` 尺度不变）。
- 「不可换算」不再伪装成绑定缺失：`extract_clause` 抽取阶段给不可换算单位打 `unsupported_unit`（写进 `spec_clause` / `doc_block` / `sql_row` 的 payload 与 claim 字典），展示层把 `unit:UNIT_UNKNOWN` 重写为 `unsupported_unit`；`binding_problems` 里的原始 token 保留（有界修复 `unit_from_sibling` 仍要识别它）。

**改后对照**（exit 0，原样摘录）：

```
  68 degF -> degC        = 20
  212 degF -> degC       = 100
  98.6 degF -> degC      = 37
  70 degF -> degC        = None            ← 190/9 非有尽小数：拒绝而非四舍五入
  1 万元 -> CNY            = 10000
  1123.15 K -> degC      = 850.00          ← 既有尺度不变
values_agree 68degF vs 20degC -> True
source_agreement -> {'status': 'resolved_equivalent', ..., 'left_value': '20', 'right_value': '20'}
```

全链路（`degF` 条文）改前会 `DOC_NODE_BINDING_INCOMPLETE:unit:UNIT_UNKNOWN` → 改后：

```
unit=degF     status='completed'  error=None          checks=[{... 'unit': 'degF', 'expression': '60 <= 60', 'status': 'checked'}]
unit=furlong  status='failed'     error='DOC_BINDING_MISSING' detail='DOC_NODE_BINDING_INCOMPLETE:unsupported_unit'
```

（`140 degF` 被精确归一到 `60 degC`，与 `60 degC` 的 SQL 声称正好在阈值上；不可换算的 `furlong` 明确报 `unsupported_unit`，不再报 `unit:UNIT_UNKNOWN`。）

**测试**：`L2UnitTableTest` 6 项（正则接受的全部 27 个单位都已登记 / degF 正反例 / 万元正反例 / 既有尺度不变 / degF 条文完整核验 / 不可换算→`unsupported_unit`）→ 全绿。副作用自查：`test_binding_eval.py` **32/32 通过**（它把 `70 degF` 冻结为「不可精确归一」的陷阱，我的实现同样拒绝，verdict 未变）。

---

## 8. L3（低）澄清面板无样式

**改前**：`git show HEAD:web/style.css | grep -c clarification` → **0**（`web/style.css` 改前未被任何轮次改动，故 HEAD 即改前）。
**改法**：在 `web/style.css` 的 `.mini` 之后补 4 条最小样式（`.clarification`、`.clarification .mini`、`.clarification-row`、`.clarification-row button`），复用既有色板/圆角/间距，未改任何既有选择器。
**改后**：`grep -c clarification web/style.css` → **5**；`node --check web/app.js` → **exit 0**。
**边界**：未在浏览器渲染验证观感；`web/index.html` 的 `?v=` 未提升（该文件不在可改清单，且 `tests/test_frontend_hygiene.py` 只要求三处 `?v=` 一致且 > 0.37.0，现为 0.41.0，未受影响）。

---

## 9. L5（低）SSE 事件与 `trace_events` 落库不一致

**改前**（`git diff datamind/agent.py` 的 `-` 行即改前代码，逐字）：

```
         cur.execute("""INSERT INTO trace_events(...) ...""", (...))
-        if self.sink:
-            self.sink(event)
         return event
```

即**先落库、后交 sink**；而 `datamind/api.py` 的 `sink` 在入队前才补 `clarification_*` 四个键 → 流式拿得到、回放（读库）拿不到。

**改法**：`_event` 改为**先交 sink、再落库**（落库用的就是 sink 可能补齐后的同一份 payload）；sink 抛异常改为记日志，避免因为交付失败丢掉 trace 行。**既有键一个未增未删**。

**改后对照 / 反例**（隔离回归 `L5EventPersistenceParityTest`，exit 0，两项都过）：

```
test_the_persisted_event_carries_the_same_clarification_keys_as_the_stream ... ok
test_the_pre_fix_order_left_the_clarification_keys_out_of_the_trace ... ok
```

第二项是**改前顺序的逐字复刻**（测试内 `_legacy_event`，与上面 diff 的被删行一致）：同样跑一次「销售额最高的品类是哪个？」（打桩 `datamind.agent.ask`，不调模型），断言 SSE 交付的事件**带** `clarification_missing_slots`，而落库事件**不带** —— 失败样本被保留为可执行反例；第一项则断言改后两者在四个澄清键上**逐一相等**。

**残留（诚实边界）**：顺序反转为「先交付、后落库」后，流式客户端理论上可能在 INSERT 落库前的一瞬（同一工作线程内的一条语句）就收到终态事件；若它此刻立刻 `GET /v1/runs/{id}/events`，存在极窄的可见性窗口。这比改前的「键永远不落库」小得多，但不是零；彻底的消除属于 L4 的范围（把契约下沉到 `datamind/` 并在 `_finish` 之内补齐再落库），本轮按禁令保留。

---

## 10. L4（低，可选）请求期导入评测脚本 —— 保留原样 + 理由

**未修改**，理由（如实说明，不粉饰）：
1. **硬性禁令 #5** 限定我只能改 `datamind/agent.py`、`datamind/cross_source.py`、`datamind/binding.py`、`datamind/api.py`、`datamind/industrial_rules.py`、`db/migrations/019_evidence_graph.sql`、`web/style.css`、`docs/`（ROUTE_UNSUPPORTED 退役相关）与新增的 `tests/test_security_round2.py`。下沉契约需要**新建** `datamind/clarification.py` 并**修改** `scripts/eval_clarification.py`（`tests/test_clarification_flow.py` 从它导入 10 个符号）——两者都在清单之外。
2. 风险 > 收益：契约函数被 3 处消费（API 投影、B3 冻结集评测、前端键名同源测试），移动定义会牵动评测脚本与既有测试的导入面，而当前实现的**故障影响已被限制**：`_with_clarification_contract` 是**惰性导入 + 宽 try/except**，导入失败时原样返回 payload（澄清响应不会因此 500）。
3. 现状与残留风险已记录：生产路径确实在请求期 `from scripts.eval_clarification import ...`（`datamind/api.py:1053`），`scripts/` 无 `__init__.py`（本环境实测可导入）。若协调者愿意放宽文件清单，最小安全改法是「新建 `datamind/clarification.py` 承载契约实现 → `scripts/eval_clarification.py` 改为 re-export 该实现」；`tests/test_clarification_flow.py` 的既有导入**无需改动**即可继续工作。

---

## 11. 验证（真实命令 + 退出码 + 原样摘录）

命令（`cd "/Users/caicai/DataMind Agent"`；逐个跑，**未跑无参数全量**）：

```
.venv/bin/python scripts/test_isolated.py --pattern test_security_round2.py
.venv/bin/python scripts/test_isolated.py --pattern test_cross_source.py
.venv/bin/python scripts/test_isolated.py --pattern test_clarification_flow.py
.venv/bin/python scripts/test_isolated.py --pattern test_agent_api.py
.venv/bin/python scripts/test_isolated.py --pattern test_multi_turn.py
.venv/bin/python scripts/test_isolated.py --pattern test_frame_multi_turn.py
.venv/bin/python scripts/test_isolated.py --pattern test_binding_eval.py   # L2 副作用自查
.venv/bin/python scripts/test_isolated.py --pattern test_frontend_hygiene.py    # L3 / 静态资源自查
.venv/bin/python scripts/test_isolated.py --pattern test_sql_scope_variants.py  # H1 邻接路径自查
.venv/bin/python scripts/test_isolated.py --pattern test_industrial.py
.venv/bin/python scripts/test_isolated.py --pattern test_rag.py
```

结果（每个 pattern 一行，原样摘录 `test_isolated.py` 末尾的 JSON summary）：

| pattern | 退出码 | tests | failures | errors | failed_tests |
|---|---:|---:|---:|---:|---|
| `test_security_round2.py` | **0** | 31 | 0 | 0 | — |
| `test_cross_source.py` | **1** | 33 | 3 | 0 | 3 条 `AgentWiringTest`（M2 下游，见 §4） |
| `test_clarification_flow.py` | **0** | 23 | 0 | 0 | — |
| `test_agent_api.py` | **1** | 5 | 1 | 0 | `test_cross_source_route_is_explicitly_unavailable`（H2，R2 负责） |
| `test_multi_turn.py` | **0** | 33 | 0 | 0 | — |
| `test_frame_multi_turn.py` | **0** | 30 | 0 | 0 | — |
| `test_binding_eval.py` | **0** | 32 | 0 | 0 | — |
| `test_frontend_hygiene.py` | **0** | 8 | 0 | 0 | — |
| `test_sql_scope_variants.py` | **0** | 21 | 0 | 0 | — |
| `test_industrial.py` | **0** | 11 | 0 | 0 | — |
| `test_rag.py` | **0** | 11 | 0 | 0 | — |
| **合计** | | **238** | **4** | **0** | 3 条 M2 下游 + 1 条 H2/R2 |

（后 4 个 pattern 是**副作用自查**：`test_frontend_hygiene.py` 覆盖 `web/` 静态资源版本一致性，`test_sql_scope_variants.py` 覆盖 scope 守卫，`test_industrial.py`/`test_rag.py` 覆盖我改动的守卫邻接路径。）

原样摘录（示例两行）：

```
{"tests": 31, "failures": 0, "errors": 0, "metadata_isolated": true, ..., "source_database_modified": false, "commit": "e4e2ebf", "ran_at": "2026-10-09T15:36:40.712541+00:00", "failed_tests": []}
{"tests": 33, "failures": 3, "errors": 0, ..., "failed_tests": ["test_cross_source.AgentWiringTest.test_completed_cross_source_adds_only_the_new_key_and_persists_the_graph", "test_cross_source.AgentWiringTest.test_conflict_reaches_the_api_shape_as_abstained_with_both_values", "test_cross_source.AgentWiringTest.test_missing_document_side_stays_fail_closed_through_the_agent"]}
```

其它原样摘录：

```
$ git rev-parse --short HEAD                                   → e4e2ebf   (exit 0)
$ curl -s http://127.0.0.1:8000/openapi.json | ...info.version → 0.39.0    (exit 0)；paths=75
$ curl -s http://127.0.0.1:8000/health                         → {"status":"ok","database":"ready",...} (exit 0)
$ node --check web/app.js                                      → 无输出    (exit 0)
$ .venv/bin/python -m py_compile datamind/agent.py datamind/cross_source.py datamind/binding.py datamind/api.py → exit 0
```

**注意**：`scripts/test_isolated.py` 每次运行都会重写 `reports/latest_regression.json`（其自身行为，非我改动）。本卡不引用该文件的口径；全量分母/提交的同步由协调者负责（硬性禁令 #4）。

---

## 12. 改动文件清单

| 文件 | 改了什么 |
|---|---|
| `datamind/agent.py` | `DATABASE_SOURCES` 单一常量；SQL/跨源同形空 allow-list 守卫（H1）；集合白名单改用 `knowledge.COLLECTIONS`（M2）；`_event` 先 sink 后落库（L5）；`CrossSourceError` 稳定码映射；注入证据透传 `alias_registry_available`（M4） |
| `datamind/cross_source.py` | `SOURCE_NOT_ALLOWED` 常量；`live_sql_evidence` 空 allow-list 抛码、`run()` 不吞；`plan()` 空 allow-list 早退结果；`_frame_scope` + `_aware_iso` 时区补齐（M1）；`alias_registry_available` / `_entities_verified` 降级门（M4）；`unsupported_unit` 抽取标记与展示重写（L2） |
| `datamind/binding.py` | 登记 `万元`（factor 10000）；`_EXACT_UNITS`（degF，Fraction）与 `_normalize_exact` / `_terminating_decimal` / `_dimension`；单位别名补 `degf/°f/华氏度/fahrenheit`（L2）。既有十进制路径与尺度不变 |
| `db/migrations/019_evidence_graph.sql` | `evidence` 改可空 + 非空白 CHECK；注释改为与事实一致（含更正「harness 是否跑迁移」的机制描述）；列 COMMENT 同步 |
| `web/style.css` | 补 `.clarification` / `.clarification-row` 最小样式（L3） |
| `docs/OFFICIAL_REQUIREMENTS_MAPPING.md`、`docs/MULTI_TURN.md`、`docs/SCORE_GAP_LEDGER.md` | `ROUTE_UNSUPPORTED` 逐处标注「已退役」，指向三个新稳定码（H2，只改文字） |
| `tests/test_security_round2.py` | **新增**，31 项断言覆盖 H1/M1/M2/M3/M4/L2/L3/L5 + H2 退役 |
| `reports/cards/r2_security_fixes.md` | **新增**，本卡 |

**未改动**（按禁令）：`tests/test_agent_api.py`、`tests/test_extensible_fields.py`、`tests/test_cross_source.py`、`PROGRESS.md`、`README.md`、`datamind/api.py`、`datamind/industrial_rules.py`、`scripts/eval_clarification.py`。

---

## 13. 交给协调者的三个动作项

1. **裁决 M2 下游**：`tests/test_cross_source.py:560` 的 `['public_kb']` → `['demo_kb']`（1 行，语义不变；该文件是第一轮新增的 untracked 测试文件，改动不涉及任何基线冻结契约）。不改则 `--pattern test_cross_source.py` 恒 3 红。
2. **H2 测试与 README**：`tests/test_agent_api.py:110` 的旧断言与 `README.md:74` 仍写 `ROUTE_UNSUPPORTED`，均由 R2/文档智能体负责；我这边只确认运行时 0 处旧码。
3. **口径同步**：本卡不含全量分母；请在全量隔离回归后统一同步 `PROGRESS.md` / `README.md` / `reports/latest_regression.json`（我未改这三处，避免与 R2/文档智能体互相覆盖）。
