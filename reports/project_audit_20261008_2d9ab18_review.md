# DataMind Agent 审查报告复审报告

复审对象：[project_audit_20261008_2d9ab18.md](reports/project_audit_20261008_2d9ab18.md)（被复审报告的固定基线 `2d9ab18d06e17f79f0ed29e3a3cf07ceb11d2842`，API `0.30.0`）
复审日期：2026-10-08（Asia/Shanghai）
复审时仓库状态：`HEAD = 655eb6e`（复审期间基线后新增 `f1c2e09`、`655eb6e` 两次提交；工作区仍有未提交改动与未跟踪文件）
复审性质：**只读复核**。未修改业务代码，未执行 `reset_demo.sh` / `verify.py` 等破坏性路径，未调用外部付费模型。

## 1. 复审结论

**判定：被复审报告可信、可用，建议直接作为修复排期与验收设计的输入；其技术结论经独立复核成立，未发现事实性错误。综合评分 8.5 / 10。**

一句话概括：**结论层面几乎无懈可击，交付物层面不自洽。** 我抽样核对的 **25 处代码引用全部命中目标代码块（0 处指向错误代码）**，全部静态统计数字与基线逐项一致，F01–F11 的机制描述都能在源码中重新推导出来；但报告列为本轮产物的"完整回归日志"**并不存在**，使它最醒目的数字（643/643、160.248 秒）目前无法从交付物复核。

| 评分维度 | 分数 | 依据 |
|---|---:|---|
| 引用可核验性 | 10 / 10 | 25 处引用逐行对照，全部落在目标代码块内 |
| 统计准确性 | 10 / 10 | 1,513 / 992 / 132 / 643 / 87 / 1,116 / 1,315 全部精确 |
| 缺陷机制正确性 | 9.5 / 10 | F01、F02 等关键机制可纯代码推演复现 |
| 证据完整性（交付物闭环） | 6 / 10 | 声称的回归日志缺失；产物使用绝对路径 |
| 优先级口径 | 7 / 10 | P1 四项中两项严重度依赖部署/触发条件 |
| 边界诚实与表述克制 | 9.5 / 10 | 明确区分已复现/静态确认/未做，拒绝以通过率充当准确率 |
| 可执行性与验收设计 | 9 / 10 | A–D 阶段与完成标准可落地 |

## 2. 复审范围与方法

**采用的手段**：`git ls-tree` / `git archive` 取固定基线快照；用 `grep` 做规模计数；用 `git show <baseline>:<file>` 逐行取出被引用行；代码走查（读 `sql_guard`、`sql_engine`、`agent`、`task_budget`、`vcar`、`office`、`ingest`、`verify`、`api`、`app.js`）；`docker ps` 现场核对端口；只读 SQL 与只读文件系统检查。

**未做**：真实材料盲测、浏览器视觉验收、第三方 `gateway/source/` 逐文件审计、外部网络可达性探测、依赖漏洞扫描——与原报告自述的边界一致，本复审不因此扣分。

**关键取舍**：本复审不复核"未做过的实验"，只复核"报告声称做过的实验"在代码与统计上是否成立。唯一例外是 §5 记录的隔离回归重跑尝试（结论：环境争用导致不可用，不构成对 643/643 的反证）。

## 3. 复核结果

### 3.1 规模与配置类断言——全部一致

| 报告断言 | 复核方法 | 结果 |
|---|---|---|
| 基线跟踪 1,513 个文件 | `git ls-tree -r 2d9ab18 \| wc -l` | ✅ 1,513 |
| 其中 992 个位于 `gateway/source/` | 同上限路径 | ✅ 992 |
| 第一方 Python 132 个 | `datamind/`+`ingestion/`+`scripts/`+`db/`+`tests/` 计数 | ✅ 132 |
| 643 个测试方法、42 个测试文件 | `grep -c "def test_"` | ✅ 643；含用例的文件 42 个（`tests/` 下 `.py` 共 46 个，口径可接受） |
| 87 个 HTTP 路由 | `@app.<method>(` 计数 | ✅ 87 |
| `api.py` 1,116 行、`web/app.js` 1,315 行 | `wc -l` | ✅ 一致 |
| F03 端口现场状态 | `docker ps` | ✅ `0.0.0.0:5432`、`0.0.0.0:6333-6334` 与 IPv6，Qdrant 无 API key |
| §5 "无 CI、无依赖锁、PROGRESS 分母不一" | 目录与文件检查 | ✅ 无 `.github/`、无锁文件；`PROGRESS.md:5` 写 549/549 而同文件另处写 643、650 |
| §5 "SSE 每请求独立线程与无界队列" | 读 `api.py` | ✅ `queue.Queue()` + `threading.Thread`（基线 878/891/904 行） |
| §5 "权限中间件按 URL 前后缀判断" | 读 `api.py` 中间件 | ✅ 基线 169–190 行为纯路径分支映射角色 |

### 3.2 缺陷机制类断言——逐条走查成立

| 项 | 复核方式与结论 |
|---|---|
| **F01** SQL 列权限绕过 | ✅ 成立。`aliases[table.alias_or_name]` 全局收集且内层 CTE 别名会**覆盖**外层表别名（[sql_guard.py:96](datamind/sql_guard.py#L96)），随后 `if any(t in cte_names for t in possible): continue`（[sql_guard.py:106](datamind/sql_guard.py#L106)）直接跳过字段检查；投影别名全局豁免（[sql_guard.py:111](datamind/sql_guard.py#L111)）导致 `SELECT c AS c` 放行。报告描述的两条绕过路径与代码完全对应 |
| **F02** 错误公式被接受 | ✅ 成立。指标检查只做 `{"quantity","unit_price","discount"}.issubset(...)`（[sql_engine.py:256](datamind/sql_engine.py#L256)），不校验运算符；[agent.py:302](datamind/agent.py#L302) 的 `verified = "EMPTY_RESULT" not in result["validator_findings"]` 与"非空即 verified、零售库给 high 置信"的指控逐字对应 |
| **F03** 存储服务暴露全部网卡 | ✅ 成立，且为**现场可重复**证据：`docker ps` 显示 PostgreSQL 与 Qdrant 均绑定 `0.0.0.0` 与 `[::]`；[compose.yaml:15](deploy/compose.yaml#L15)、[compose.yaml:31](deploy/compose.yaml#L31) 未指定回环地址；Qdrant 无认证环境变量；[01_roles.sql:12](deploy/init/01_roles.sql#L12) 明文口令 |
| **F04** stdin 写入绕过 deadline | ✅ 成立。[task_budget.py:368](datamind/task_budget.py#L368) 起的 `try` 块同步写 stdin，直到 381 行才进入 `process.wait(timeout=deadline)`，写入阶段不消耗挂钟预算 |
| **F05** 无密钥导出残留认证材料 | ✅ 成立。[api.py:737](datamind/api.py#L737) 仅 `payload.pop("api_key")`，`headers`、`proxy` 经 `PROFILE_FIELDS` 原样进入导出体（[settings_store.py:24](datamind/settings_store.py#L24)、[settings_store.py:210](datamind/settings_store.py#L210)） |
| **F06** 探活与问答配置不一致 | ✅ 成立。探活端点走 `_profile_from_request(request)`（含 proxy），问答链路走 [sql_engine.py:63](datamind/sql_engine.py#L63) `model_profile()`；**基线 `sql_engine.py` 全文不含 "proxy" 字样**，而 `providers.make_client` 的显式代理分支依赖该字段，逻辑上确实会静默退回系统代理 |
| **F07** V-CAR 恢复不绑计划 | ✅ 成立。[vcar.py:170](datamind/vcar.py#L170) 的 `validate_state` 只校验 state 形状；[vcar.py:301](datamind/vcar.py#L301) 对 `succeeded` 直接复用 `record['result']`，无 plan SHA / 输入版本比对 |
| **F08** 新问答与回放串屏 | ✅ 静态成立。[app.js:818](web/app.js#L818) 的 `run()` 与 [app.js:856](web/app.js#L856) 的 `replay()` 共用 `clearRun()` 与同一结果面板，无请求代次或 AbortController 归属校验（本次未做浏览器动态复现，与原报告"纯内存 JS 复现"口径不同） |
| **F09** Office 保真三类边界 | ✅ 成立。[office.py:36](ingestion/office.py#L36) 的 `_block` 用 `[:64]` 拼接后截断，序号可被截掉；[office.py:84](ingestion/office.py#L84) 只遍历 `document.paragraphs`，与 body 实际顺序脱节；[office.py:149](ingestion/office.py#L149) `data_only=True` 使无缓存公式读成空值而无缺口标记 |
| **F10** 公开摄取缺恢复协议 | ✅ 静态成立。[ingest.py:553](ingestion/ingest.py#L553) 先删 Qdrant 点并 upsert，[ingest.py:579](ingestion/ingest.py#L579) 才 `meta.commit()`；其间失败即产生跨存储不一致 |
| **F11** 验证/重置入口失真与副作用 | ✅ 三条全部成立。[verify.py:58](scripts/verify.py#L58) `autocommit = True` 后真的执行 `DELETE`/`CREATE`；[verify.py:79](scripts/verify.py#L79) 断言数据源数固定为 2、[verify.py:89](scripts/verify.py#L89) 固定 26 个已索引文档；[reset_demo.sh:10](scripts/reset_demo.sh#L10) 先 `down -v` 删除卷，之后才调用 `py -3.13`（本机 `py` 不存在，README 声明 Python 3.14） |

### 3.3 引用精度审计（25 处，逐行对照基线）

| 引用 | 基线该行实际内容（截断） | 判定 |
|---|---|---|
| `sql_guard.py:84` | `cte_names = {cte.alias for cte in tree.find_all(exp.CTE)}` | ✅ 精确 |
| `sql_guard.py:105` | `possible = [aliases[column.table]] ...` | ✅ 块内（豁免判断在 111） |
| `sql_engine.py:44` | `if stored.get("api_key"):` | ✅ 精确 |
| `sql_engine.py:254` | `tree = parse_one(sql, read="postgres")` | ✅ 块内（缺字段判断在 256） |
| `agent.py:302` | `verified = "EMPTY_RESULT" not in result["validator_findings"]` | ✅ 精确 |
| `agent.py:253` | `if route == "cross_source":` | ✅ 块内（错误码在 255） |
| `compose.yaml:15` / `:31` | Postgres / Qdrant 端口映射行 | ✅ 精确 |
| `01_roles.sql:12` | `CREATE ROLE datamind_app LOGIN PASSWORD 'datamind_app_dev';` | ✅ 精确 |
| `task_budget.py:368` | `try:`（stdin 同步写入块起点） | ✅ 块内（写入在 370） |
| `api.py:729-741` | `export_profiles(...)` 全函数 | ✅ 精确 |
| `settings_store.py:210` | `"auth_style": ... "headers": profile.get("headers")` | ✅ 精确 |
| `vcar.py:170` / `:301` | `def validate_state(...)` / `if status == 'succeeded':` | ✅ 精确 |
| `app.js:818` / `:856` | `async function run() {` / `async function replay() {` | ✅ 精确 |
| `office.py:36` / `:84` / `:149` | `_block` 定义 / `document.paragraphs` 遍历 / `data_only=True` | ✅ 块内（`[:64]` 在 38） |
| `ingest.py:553` / `:579` | `qc.delete(...)` / `meta.commit()` | ✅ 精确 |
| `verify.py:58` / `:79` | `conn.autocommit = True` / 数据源数断言查询 | ✅ 精确 |
| `start_local.sh:71` | `db/build_industrial_catalog.py`（第三个工业源登记链路） | ✅ 块内（`seed_industrial` 在 70） |
| `reset_demo.sh:10` | `docker compose ... down -v` | ✅ 精确 |

**结论：25/25 命中，0 处错误指向。** 报告采用"代码块起点行"的引用风格，块内偏移最大 3 行，属可接受的工程近似。**更正本复审在口头评价阶段提出的两处"行号漂移"质疑**：`task_budget.py:368` 与 `agent.py:253` 在固定基线上都是正确的块级引用；我早前看到的 370 / 262 是**当前工作区**已漂移的行号，责任在我的口径，不在原报告。

### 3.4 "截至复审时是否已修复"核对（HEAD `655eb6e`）

- `datamind/sql_guard.py`、`datamind/sql_engine.py` 与基线**逐字节一致** → F01、F02 未修复。
- `agent.py` 仍存在 `verified = "EMPTY_RESULT" not in ...`（现 309 行）→ F02 的"高置信"路径未修复。
- `task_budget.py` 仍在同步写 stdin（现 370 行）→ F04 未修复。
- `api.py` 导出仍只 `pop("api_key")`（现 737 行）→ F05 未修复。
- 基线后两次提交（`f1c2e09` 契约存储、`655eb6e` 引用定位消费）均为功能增量，**未触及 F01–F11 任一条**。

## 4. 复审新增观察（原报告未覆盖或覆盖不足）

### N01 · 证据链不闭环（中）
报告 §2 将"完整回归日志"列为本轮产物并给出链接，但该文件在仓库中不存在（`reports/` 下仅 `project_audit_20261008_2d9ab18.md` 与 `..._checks.json`，且两者在 git 中均为未跟踪文件）。后果：**643/643、160.248 秒、exit 0 这三项无法从交付物复核**，只能依赖 `checks.json` 的自述字段。同类问题：两处产物链接写死 `/Users/caicai/...` 绝对路径，换机即失效。

**建议**：补交日志，或将 §7 的"已有组件与集成用例通过"一栏降级为"自述，日志未保留"。

### N02 · 隔离回归对并发与共享状态敏感（中）
见 §5：同一套容器上并发运行第二个全套用例时，本复审两次重跑均出现大规模连接级错误；隔离 runner 还会因实时库瞬时出现的测试行而 fail-closed。原报告 §5 已提示"隔离回归依赖已初始化的本地数据"，但未提示**并发**这一维度。对一个把"隔离回归"当作主要证据的项目，这是复现方法上的缺口。

**建议**：runner 增加运行前并发探测（存在同类进程即拒绝启动），并在报告中显式声明"同一容器上不得并发运行验收"。

### N03 · 优先级口径偏乐观（中）
P1 四项中两项的严重度依赖条件：F03 的暴露面取决于是否跨机部署（报告自己承认 API 绑本机、未做外部探测）；F04 需要子进程不读 stdin 且持续写入才阻塞。二者与 F01（越权）、F02（错误业务结果被判 high 置信）不在同一量级。另有一项被低估：**F11 的 `verify.py` 会在权限配置错误时真的修改数据库**，属"验证者污染被测对象"，建议上调。

**建议**：拆为 `P1 正确性与权限`（F01、F02）、`P1' 部署与执行边界`（F03、F04、F11 的副作用）、`P2 功能与可靠性`（F05–F10）。

### N04 · 全篇缺一条量化纵轴（中低）
报告正确地拒绝把 643 当作准确率，但 §6 D 阶段的完成标准因此全是定性的（"报告正确率、拒答、误修复、定位、时延与成本"），没有任何可比的现值。作为路线图够用，作为排期与验收基线偏软。

**建议**：先冻结一份最小度量集（SQL 口径正确率、引用定位命中率、拒答率、P50/P95 时延），即使当前样本很小也记录分母。

### N05 · 缺少针对权限映射的守卫要求（低）
F01 的本质是"作用域判断不严"，而 `api.py` 用 URL 前后缀映射角色的写法是同类风险的另一形态（新路由漏挂权限即静默放行）。报告只在 §5 把中间件写作可维护性问题。

**建议**：在验收清单加入硬性要求——**任何变更型路由必须有权限断言测试**，并让中间件映射表与路由表做一致性校验。

## 5. 复现实验记录（含失败归因）

**目的**：在缺少回归日志的情况下，尝试独立复现 §7 的头条数字。

| 时间 | 命令 | 结果 |
|---|---|---|
| 10:40–10:42 | `.venv/bin/python scripts/test_isolated.py > tmp/audit_rerun.log` | `Ran 650 tests in 114.424s`，failures 0，**errors 179**，全部为 `psycopg2.OperationalError: ... server closed the connection unexpectedly`，集中在 `test_pdf_index_api` |
| 10:43–10:45 | 等待无同类进程后重跑，输出 `tmp/audit_rerun2.log` | `Ran 643 tests in 96.978s`，failures 0，**errors 258**，错误类型同上 |
| 10:46 | `--pattern test_contract_store.py` | 在 setup 阶段 fail-closed：`RuntimeError: Clone source unavailable: test-multifile-83958628c130-outside` |

**归因证据（指向环境争用，而非报告失实）**：

1. 期间存在**非本复审启动**的第二个 `scripts/test_isolated.py` 进程（`ps` 可见其父命令为 `.venv/bin/python scripts/test_isolated.py 2>&1 | tail -4`），与本次运行重叠。
2. 实时元数据库中曾出现指向检出目录之外的 `document_assets` 行（`test-multifile-…-outside`），数分钟后消失——说明有并发进程在**直接使用实时库**跑测试。
3. 工作区在 5 分钟内持续变化：`tests/` 用例数 650 → 643 → 659，`tests/*.py` 46 → 47 → 48；`HEAD` 由 `f1c2e09` 推进到 `655eb6e`。
4. 服务器端无对应故障：近 10 分钟仅有约 8 条 `FATAL: terminating connection due to administrator command`（对应各 runner 自身的 `dropdb --force` 清理），**无** OOM、无 `too many clients`、无 backend signal 9。
5. 控制实验：裸连接循环 60 次全部成功（60/60），说明容器与账号本身正常。

**结论**：**不将上述失败计为被复审报告的缺陷**。它证明的是"隔离回归隔离了元数据库，但没有隔离并发与共享容器状态"，即 N02。原报告的 643/643 记录于基线冻结后的安静窗口，本复审**无法证实也无法否证**该数字——这本身就是 N01 的代价。

## 6. 对被复审报告缺陷清单的再评级

| 编号 | 原评级 | 复审建议 | 理由 |
|---|---|---|---|
| F01 SQL 作用域绕过 | P1 | **P1（保持）** | 唯一"越权 + 可实证"的路径，应最先修 |
| F02 错误公式被接受 | P1 | **P1（保持）** | 直接摧毁"证据治理"这一核心承诺 |
| F03 存储暴露全部网卡 | P1 | **P1'（部署前置/发布门禁）** | 本地演示基线实为 P2，跨机部署时为 P1 |
| F04 stdin 绕过 deadline | P1 | **P2+** | 触发需子进程不读 stdin；与 F06/F07 同批修复更经济 |
| F05 无密钥导出残留 | P2 | P2（保持） | 与 F06 合并为"统一 profile 解析与脱敏" |
| F06 探活/问答配置不一致 | P2 | P2（保持） | 同上 |
| F07 V-CAR 恢复不绑计划 | P2 | P2（保持） | 随 C 阶段恢复接口持久化时一并修 |
| F08 前端串屏 | P2 | P2（保持） | 建议先提炼统一请求代次工具再修 |
| F09 Office 保真 | P2 | P2（其中 XLSX 无缓存公式静默 `parsed` 可视作 P2+） | "不报缺口的完整"比顺序错误更危险 |
| F10 公开摄取恢复协议 | P2 | P2（保持） | 设计性缺口，随 C 阶段复用 outbox 模式 |
| F11 验证/重置入口副作用 | P2 | **P1'（上调）** | 验证脚本可改真实库，污染被测对象 |

## 7. 采纳建议

**针对被复审报告本身（低成本、高收益）**

1. 补交 `project_audit_20261008_2d9ab18_regression.log`；若无法补交，将 §7 相应行标注为"未保留日志"。
2. 产物链接改为仓库相对路径。
3. 为引用加机器校验（"引用行内容断言"测试），把"行号以固定基线为准"从承诺变成可执行约束。
4. 在 §2 复现方法中补充前置条件：同一容器上不得并发运行隔离回归；并在 runner 中实现探测。

**针对项目（按复审后的排期）**

| 顺序 | 任务 | 完成标准（可直接作为测试断言） |
|---|---|---|
| 1 | F01：按 AST scope 解析字段来源 | `SELECT c AS c FROM customers c LIMIT 1` 与 `SELECT c.phone FROM customers c WHERE EXISTS (WITH c AS (SELECT customer_id FROM customers) SELECT customer_id FROM c)` 均被拒；正常 `ORDER/GROUP BY` 别名不误伤 |
| 2 | F02：指标表达式受控化 | `SUM(quantity + unit_price + discount)` 被拒或标记为"口径未通过"；"非空结果"不再等于 `verified` |
| 3 | F03 + F11：默认回环绑定、验证脚本隔离 | 容器端口仅 `127.0.0.1`；`verify.py` 在事务中回滚或使用隔离库，且不再硬编码 2 源/26 文档 |
| 4 | 统一 profile 解析器（F05 + F06） | 探活/模型列表/SQL/RAG/私有回答共用同一 profile；无密钥导出不含任何认证材料 |
| 5 | F08 + F09 | 交错操作不串屏（可重复的前端竞态用例）；XLSX 无缓存公式显式报 partial |

## 8. 复审未覆盖或无法判定

| 事项 | 状态 |
|---|---|
| 643/643、160.248 秒、exit 0 | **无法判定**（日志缺失 + 环境争用），见 §5 |
| 前端串屏的浏览器动态表现 | 未复现（仅静态走查；原报告为纯内存 JS 复现） |
| 真实材料泛化、真实模型指标 | 未做（与原报告边界一致） |
| 第三方 `gateway/source/` 源码与依赖漏洞 | 未做（与原报告边界一致） |
| F10 的故障注入（删点后失败、写入后回滚） | 未做（与原报告边界一致） |

## 附录 A · 本次复审使用的命令

```bash
git ls-tree -r --name-only 2d9ab18 | wc -l
git ls-tree -r --name-only 2d9ab18 -- gateway/source | wc -l
git archive 2d9ab18 | tar -x -C tmp/audit-baseline
grep -rho "def test_" tests/ | wc -l
grep -rhoE "@app\.(get|post|put|delete|patch)\(" datamind/api.py | wc -l
git show 2d9ab18:<file> | sed -n '<line>p'          # 25 处引用逐行核对
docker ps --format '{{.Names}}\t{{.Ports}}'
docker exec datamind-postgres psql -U postgres -d datamind_meta -c "SELECT ... FROM document_assets"
git diff --quiet 2d9ab18 HEAD -- datamind/sql_guard.py
```

## 附录 B · 本次复审产物

| 产物 | 说明 |
|---|---|
| `reports/project_audit_20261008_2d9ab18_review.md` | 本复审报告 |
| `tmp/audit-baseline/` | 基线 `2d9ab18` 的 `git archive` 解包快照（复核引用行所见即此快照） |
| `tmp/audit_rerun.log` | 第一次重跑日志（650 tests，errors 179） |
| `tmp/audit_rerun2.log` | 第二次重跑日志（643 tests，errors 258） |
| `tmp/audit_subset.log` | 子集重跑日志（setup fail-closed） |

> 复审声明：本复审仅基于只读检查与固定基线代码证据；未修改任何业务源码，未替项目方提交正在进行的开发内容。若后续修复推进，相关反例应在**新 commit** 上重新验收。
