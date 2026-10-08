# 审计缺陷修复记录（F01–F11）

修复依据：[project_audit_20261008_2d9ab18_review.md](../reports/project_audit_20261008_2d9ab18_review.md)（复审基线 `2d9ab18`，API 0.30.0），按复审 §7 给出的排期与验收标准执行。
逐条机器可读记录见 [reports/audit_fix_verification.json](../reports/audit_fix_verification.json)。

**执行方式**：F01/F04/F07 由主代理实现；F02、F03+F11、F05+F06、F08+F09 由四个并行子代理实现，每个子代理被限定**只能写指定文件**、禁止仓库级格式化与 git 写操作。所有改动均由主代理独立复核并跑测试。

## 状态总览

| 编号 | 缺陷 | 复审评级 | 状态 |
|---|---|---|---|
| F01 | SQL 列权限按全局别名表判定（两条越权路径） | P1 | **已修复** |
| F02 | 指标表达式只校验列名；非空即 verified | P1 | **已修复**（含提示类语义补充） |
| F03 | 存储服务绑定全部网卡 | P1' | **配置已改，待重建容器生效** |
| F04 | stdin 写入绕过 deadline | P2+ | **已修复** |
| F05 | 无密钥导出残留认证材料 | P2 | **已修复** |
| F06 | 探活与问答链路 profile 解析不一致 | P2 | **已修复** |
| F07 | V-CAR 恢复状态不绑定计划 | P2 | **已修复** |
| F08 | 前端运行/回放串屏 | P2 | **已修复** |
| F09 | Office 保真（block_id 截断、正文顺序、公式无缓存静默通过） | P2（XLSX 部分 P2+） | **已修复** |
| F10 | 摄取跨存储一致性（Qdrant 先写、PG 后提交） | P2（设计性） | **明确延后**，见下 |
| F11 | 验证脚本污染真实库并硬编码规模 | P1' | **已修复** |
| F12 | 本机地址在配置显式 proxy 时仍走代理（修复 F06 时自查发现） | P2（同族可用性） | **已修复** |

## 逐条说明

### F01 列权限按 AST scope 解析（P1）
原实现用**全局**别名表加**全局**投影别名豁免，两条路径可越权：`SELECT c AS c FROM customers c`（`c` 实为整行引用，别名 `c` 豁免了自身，绕过 `customers.phone` 的 restricted 检查）与内层 CTE 别名覆盖外层表别名后 `c.phone` 被当作 CTE 跳过检查。
现改为按 sqlglot scope 逐查询作用域解析列来源：限定列按本作用域 sources 解析，未限定列按本作用域表解析，投影别名**仅**在 ORDER/GROUP/HAVING 中可引用；CTE 引用（`Scope.is_cte`）放行，派生表保持原有的不可验证拒绝。两条反例均被拒（`COLUMN_NOT_ALLOWED` / `RESTRICTED_COLUMN`），正常排序别名、GROUP BY 与 CTE 引用不误伤。

### F02 指标表达式受控化（P1）
原实现只判断 SUM 内部**是否出现** `quantity`/`unit_price`/`discount` 三个列名，因此 `SUM(quantity + unit_price + discount)` 这种运算符错误的表达式会被放行；`agent` 的 `verified` 又只排除 `EMPTY_RESULT`，于是口径错误的结果被标为已验证并给高置信度。
现按 `business_metrics.expression_sql` 注册表做**结构比对**（忽略表限定符、乘法链因子排序），并新增 `verification_passed(findings)` 供 `agent` 与 `sql_engine` 共用：任何 `METRIC_*`/`TIME_*` finding 都不得判为 verified。

**补充的语义区分**：把「已校验为错」与「无法独立校验」分开。复合指标（净销售额 = 销售额 − 退款额，引用其他指标）无法用单条 SUM 结构比对，若一律失败关闭，会让**合法问题永远无法回答**——那是用新缺陷换旧缺陷。因此：

| finding | 行为 |
|---|---|
| `METRIC_EXPRESSION_MISMATCH`（已校验为错） | **阻塞**，触发重写，3 次后失败 |
| `METRIC_EXPRESSION_NOT_VERIFIABLE`（无法独立校验） | 记入 `validator_findings`、verdict 非 verified、**不阻断回答** |
| 读不到注册表 / 无该指标 / 公式不可结构校验 | 失败关闭为 `NOT_VERIFIABLE`（绝不静默通过） |

### F03 默认回环绑定（P1'）
`deploy/compose.yaml` 三处发布端口改为 `127.0.0.1:…`（PostgreSQL、Qdrant 6333/6334），并在文末写明已知安全边界（Qdrant 无认证、`01_roles.sql` 与 compose 默认口令为明文）。
**尚未生效**：只改了配置，未重建容器；`docker ps` 仍显示 `0.0.0.0`/`[::]`。需下一次 `docker compose -f deploy/compose.yaml up -d` 重建后才收敛为仅回环。跨机部署还需防火墙或反向代理，认证与口令方案本轮**未动**（改动会破坏本机正在运行的栈）。

### F04 deadline 覆盖 stdin 写入（P2+）
原实现先同步 `write` 子进程 stdin，再进入 `wait(timeout=deadline)`；子进程若不读 stdin，管道写满即无限阻塞，deadline 根本来不及生效。
现 deadline 起点提前到 spawn 之后，stdin 由守护线程写入，主路径按**同一**预算轮询并在超时/取消时终止整个进程组。8 MiB stdin + 永不读 stdin 的子进程在 `deadline_s=1` 下实测 < 6 秒返回 `TASK_DEADLINE_EXCEEDED`（修复前会永久阻塞）；正常读取 stdin 的子进程与大载荷（4 MiB）仍完整送达。

### F05 无密钥导出脱敏（P2）
新增唯一脱敏入口 `redact_profile()`：移除 `api_key`、剔除敏感头（`authorization`/`api-key`/`x-api-key`/`cookie` 及 `*-key/-token/-secret/-password` 规则）、去掉 `proxy` 的 userinfo（无法安全解析则整条丢弃），并以 `_scrub_secrets()` 兜底替换任何字段中残留的密钥值。`/v1/model/profiles/export` 在 `include_keys=False` 时走它。

### F06 profile 解析统一（P2）
`api.py` 内统一为唯一入口 `resolve_profile(request, *, stored=None)`，探活、模型列表与 `_stored_config()` 共用，`headers`/`proxy` 与 `api_key` 同一套合并规则。
**问答链路的 proxy 传递已补齐**：`sql_engine.settings()` 把存储配置的 `proxy` 映射为 `NEW_API_PROXY`，`model_profile()` 输出 `proxy` 键。传输层 `providers.make_client()` **本就**支持 `profile['proxy']`——缺的只是这条传递链，所以修复很小。此前"探活走代理、问答不走代理"会让配了中继的部署出现"探活通过但问答不通"。
**仍未统一的是解析实现本身**：`sql_engine.settings()` 与 `api.resolve_profile()` 仍是两套逻辑（前者产出 `NEW_API_*` 环境风格字典，后者产出 profile 结构）。是否合并为一个共享解析器留作后续，本轮只保证**行为一致**（proxy 已一致）。

### F12 本机地址在配置显式 proxy 时仍走代理（P2，修复 F06 时自查发现）
补 F06 时写传输层测试才发现：`providers.make_client()` 先判断显式 `proxy`、再判断 `is_local`，因此 profile 里只要配了 proxy，连 `http://localhost` 网关也会被塞进代理——正是该函数注释里写明要避免的**挂起或 502**。而 F06 把 proxy 带进问答链路后，这条路径的**暴露面反而变大**，所以必须一并修。
现改为：`is_local(url)` 优先，本机地址一律直连（`trust_env=False`）且不使用任何来源的代理（含显式 profile proxy）；远端 URL 才使用显式 proxy，无 proxy 时维持尊重系统/环境代理的原行为。

### F07 恢复状态绑定计划（P2）
`Plan` 增加稳定摘要（`PLAN_SCHEMA` + 规范化节点内容 SHA-256）。恢复状态改为 `{'plan_digest': …, 'nodes': {…}}`：裸节点映射一律 `PLAN_STATE_UNBOUND`（失败关闭），摘要不符一律 `PLAN_STATE_PLAN_MISMATCH`——节点 id 相同并不能说明持久化结果描述的是同一份工作。`execute` 结果与 `plan.public()` 均回报 `plan_digest`。

### F08 前端请求代次（P2）
`web/app.js` 与 `web/multifile.js` 引入代次号：每次请求递增，异步回调**渲染前**比对代次，过期响应一律丢弃；过期的流会被 `reader.cancel()`；发送按钮的禁用状态只由**当前**代次恢复，旧请求不会解锁或覆盖新请求的输出。

### F09 Office 保真（P2，XLSX 部分 P2+）
- `block_id` 改为 `(document_id, index)` 的定长摘要（`off-` + SHA-256 前 32 位），不再靠截断长字符串——原先长 `document_id` 会让不同块塌缩到同一前缀而 id 重复。
- DOCX 按**真实正文顺序**遍历 `w:p`/`w:tbl` 交错序列；当 XML 正文与 `paragraphs`/`tables` 集合不一致时，漏掉的元素仍被追加（不静默丢弃）并声明顺序未被证实。
- **XLSX 有公式但无缓存值**不再静默报 `parsed`：工作簿载入两次（`data_only=True` 取缓存值、`data_only=False` 取公式），`data_type == 'f'` 且缓存为 `None` 的单元格触发 `formula_without_cached_value`，`parse_status` 置 `partial`；普通工作簿仍为 `parsed`。

**反证（证明修复非空转）**：用只读基线副本实测旧实现——500 个块的 block id 只有 **1 个唯一值**、DOCX 顺序为「段落,段落,表格,表格」，无缓存公式报 `parsed` 且无 feature；旧 `app.js` 下 8 项前端测试有 7 项失败。

**解析器版本**：`CONTRACT_PARSER_VERSION` 由 `office-adapter-v1` 升为 **`v2`**——block id 方案、DOCX 顺序与 XLSX 状态语义都变了，升版才会触发既有 Office 文档重新解析。

### F10 摄取跨存储一致性（P2，延后）
`ingestion/ingest.py` 先对 Qdrant 执行 delete+upsert（非事务），随后才写 `chunks` 并 `meta.commit()`；PG 提交失败时 Qdrant 已是新向量而 PG 仍是旧 chunk，检索可能用旧文本配新向量。
**延后原因**：修复需要 outbox/状态机式的多存储协议并改动摄取主链路，而该链路依赖 Qdrant 与嵌入模型，本轮预算内无法给出充分验证。**不把未验证的改动放进生产摄取路径**。
**既定修复方案**：摄取前先把文档置为不可检索状态并提交 → 写 Qdrant → 写 PG chunks 并置 `indexed` 提交；任何失败都停在「不可检索」，重试入口复用既有孤儿回收路径。

### F11 验证脚本不再污染真实库（P1'）
删除 `conn.autocommit = True`（污染根因），越权探针改为在 `SAVEPOINT` 内执行且必回滚；新增 `verify_readonly_isolation()` 在 `try/finally` 中统一 `rollback` + `close`（可注入假连接便于断言）；新增 `derive_expectations()` 动态推导期望，删掉 `== 2` 源 / `== 26` 文档等全部硬编码，改为与规模无关的自洽不变量；`reset_demo.sh` 的 `py -3.13` 改为 `.venv/bin/python`。
佐证：当前真实库是 **3 源 / 27 文档**，旧脚本的 2/26 现在就会误报。

## 未完成与后续建议

1. **F06 剩余**：问答链路（`sql_engine`/`rag_engine`/`pdf_answer`）仍不含 proxy，需统一到共享解析函数。
2. **F10 延后**：按上述 outbox 方案单独任务实施。
3. **读取侧同类暴露**：`settings_store.public_profile()`（`GET /v1/model/profiles`，admin-only）仍原样回传 `headers`/`proxy`；前端表单会回填并回传，直接脱敏会让保存写回脱敏值，需同步改 UI 与保存语义（建议：敏感头只回显键名，空值表示保持不变）。
4. **F03 生效窗口**：需重建 Postgres/Qdrant 容器；认证与明文口令方案待统一走 secret 注入。
5. **F06 解析实现未合并**：`sql_engine.settings()` 与 `api.resolve_profile()` 仍是两套代码（行为已一致，实现未统一）。
6. **F02 覆盖仍窄**：仅销售额别名族走注册表结构校验；业绩、销量、退款额、订单量、客户总量未强制；复合指标未做递归展开比对。
6. **Codex 并发**：另有 agent 在同一检出与同一 PostgreSQL 上工作，曾造成一次提交被其产物污染、一次回归被其并发测试干扰。本轮所有提交只按显式路径 `add`，并在提交前核对 `git diff --cached`。
