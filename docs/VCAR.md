# V-CAR：通用依赖计划与有界修复

2026-10-07，API **0.22.0**。本阶段实现技术设计说明书 §6.1/§6.4 与 §15-P3 记载的通用编排：`Plan → Execute → Verify → Repair → Reverify`。此前只有「限定工业规则 DAG」，通用动态 DAG 与断点恢复为待实现。

实现位于 `datamind/vcar.py`。API 0.23.0 起提供两种执行方式：调用方注入 `(node, inputs) -> dict` 的普通执行器，或用 `bounded_runners` 让**每个节点在自己的进程组内**按统一任务预算执行。前者用于进程内编排，后者用于隔离与资源约束。

## 节点契约（§6.1）

| 字段 | 约束 |
|---|---|
| `node_id` | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`，计划内唯一 |
| `depends_on` | 计划内已存在的 id 列表，不含自身、不重复、无环 |
| `tool` | `sql` / `rag` / `industrial_rules` / `fields` / `cad` / `ocr` / `source_authority` |
| `input` | 结构化输入引用（必须是可 JSON 序列化的 dict） |
| `expect` | 期望输出说明（字符串或空） |
| `permission` | `viewer` / `reviewer` / `approver` / `admin` |

计划节点上限 64；`plan(nodes)` 返回**按拓扑排序**的 `Plan`，并提供 `subgraph(roots)` 计算「受影响子图」。

## 统一预算（§6.4）

`deadline_s`、`max_steps`、`max_rows`、`max_tokens`、`max_repair`、`max_parallel` 共用一个预算对象；`max_repair` 上限为 **2**（与文档「最多两次修复」一致）。预算在**派发边界**检查：批次按剩余 `max_steps` 截断，`deadline`/`max_rows`/`max_tokens` 在每次派发前检查；未执行节点标记 `budget_exhausted` 并进入 `gaps`，**不会被静默当作成功**。

并发批次内已经派发的节点仍会完成——预算是派发边界的约束，不是中途取消。

## 执行语义

- **并发**：无依赖节点在同一批次内并发执行（受 `max_parallel` 约束）。
- **依赖消费**：依赖节点接收 `{父节点 id: 结果}` 的结构化前序结果。
- **失败隔离**：失败节点只把其**传递依赖**标记为 `skipped`（`PLAN_DEPENDENCY_FAILED`），独立分支继续执行。
- **局部修复**：修复只重跑**受影响子图**（失败节点 + 其后继）；独立分支不重跑。每次尝试写入 `history`，事件保留 `started` / `failed` / `failed_detail` / `repair_scheduled`——**原始失败不删除**。
- **重新验证**：修复后后继节点重新执行，而不是复用旧结论。
- **未消解冲突**：节点返回 `{"unresolved": true, "evidence": [...]}` 时**终止自动消解**、不进入修复，双方向证据写入该 gap 的 `evidence`，后继标记 `PLAN_DEPENDENCY_UNRESOLVED`（对应「真实来源矛盾无规则时返回双方证据」）。
- **断点恢复**：`state` 中 `succeeded` 复用且不重跑，`unresolved` 不自动消解，`pending/failed/skipped/budget_exhausted` 重新调度。
- **状态必须绑定计划（API 0.34.0）**：恢复状态格式为 `{'plan_digest': …, 'nodes': {…}}`，`plan_digest` 来自 `Plan.digest`（`PLAN_SCHEMA` + 规范化节点内容的 SHA-256）。裸节点映射一律 `PLAN_STATE_UNBOUND`（失败关闭），摘要不符一律 `PLAN_STATE_PLAN_MISMATCH`——因为节点 id 相同并不能说明持久化结果描述的是同一份工作。可用 `bound_state(plan, records)` 构造，`execute` 结果与 `plan.public()` 都会回报 `plan_digest`；其余非法 state 仍为 `PLAN_STATE_INVALID`。

## 验证

`tests/test_vcar.py` **16/16**，完整隔离回归 **539/539**（上一阶段 523 + 新增 16），见 `reports/vcar_verification.json`。覆盖拓扑排序、全量校验（环/重复 id/未知依赖/工具/权限/额外字段/路径式 id）、依赖消费、**真实并发**（`threading.Barrier` 验证重叠）、失败隔离、**只重跑受影响子图**（独立节点恰好执行 1 次）、修复上限（attempts=3）、四类预算耗尽、未消解冲突（repairs=0）、缺失执行器与非法结果、断点恢复与非法 state 拒绝。

## 机制消融与修复指标（API 0.24.0）

`datamind/ablation.py` + `scripts/eval_vcar_ablation.py` 在同一**冻结合成集**（`data/evaluation/vcar_ablation_v1.json`，16 例，标签在任何机制实现前固定）上对照四条机制臂：

| 臂 | 机制 |
|---|---|
| `ocr_text` | 普通 OCR 文本+词法读数；无结构化事实、无规则、无修复 |
| `structured` | 结构化事实/规则替代或补全读数；察觉两来源数值不一致但**一律偏向一侧** |
| `local` | 在上者基础上，只要案例提供局部重识别结果就采用（**盲目改写**） |
| `full` | 结构化事实 + **仅在有据时**局部重识别；冲突只在有已登记权威规则时消解，否则保留双方证据并标记未消解 |

冲突检测**只由来源比对得出**（两来源数值是否一致，仅做项目真实实现的 K→C 归一化），不读标签；单位/实体差异属于完整性/绑定缺陷，不当作来源冲突。指标按 §9.2 口径：修复成功率＝修复成功数/发生改写的案例数、误修复率＝正确输入被改错数/发生改写的案例数（另给占原正确输入的份额）、冲突 Precision/Recall、正确/错误/未消解，零分母报告 N/A。

实测（`reports/vcar_ablation.json`）：

| 臂 | 准确率 | 冲突 P / R | 修复成功率 | 误修复 |
|---|---|---|---|---|
| `ocr_text` | 0.1875 | N/A / 0.0 | N/A | 0 |
| `structured` | 0.6875 | 1.0 / 1.0 | 0.5455 | 0 |
| `local` | 0.6875 | 1.0 / 1.0 | 0.5714 | **2（14.29%）** |
| `full` | **1.0** | 1.0 / 1.0 | 0.8333 | **0** |

`local` 臂盲目重识别把 2 个原本正确的值改错——这正是 §6.4 假设要验证的风险；`full` 臂 0 误修复，并把 2 例无权威规则的冲突正确保留为未消解。

**这是机制消融，不是模型精度**：不调用任何模型，局部重识别为机制替身，未实现真实视觉载荷；结果不能外推为 OCR/问答准确率。

## 边界

> **本节记录 API 0.22.0 时的边界快照**，保留原文以免与历史记录脱节。其中两项已在后续版本完成：**0.23.0** 已把节点接入 `task_budget` 的进程组/资源槽与取消传播（见「受控节点执行与 HTTP 入口」），**0.24.0** 已完成机制消融与修复/冲突指标（见「机制消融与修复指标」）。**仍未做的是真实视觉载荷与模型规模对比**（外部依赖：本机可用的上游为纯文本模型、无视觉权重目录）。

- 交付的是**编排原语与契约**；尚未替换既有固定工业规则链路。
- （0.22.0 边界，已由 0.23.0 完成）节点仍在执行进程内运行、未接入 `task_budget` 的进程组/资源槽。
- （0.22.0 边界，已由 0.24.0 完成）§6.4 的机制消融实验（普通 OCR＋检索 / 结构化规则 / 局部重识别 / 完整闭环）与误修复率、错误消解率统计；结果见「机制消融与修复指标」。
- 未提供运行/事件的数据库持久化适配层；状态以可 JSON 序列化字典往返。

## 受控节点执行与 HTTP 入口（API 0.23.0）

`bounded_runners(targets, *, dsn, slots, context, grace_s, deadline_s, output_limit, env)` 把「工具 → 可执行文件」的映射交给**服务端**，为每个工具返回一个执行器：

| 属性 | 行为 |
|---|---|
| 进程组隔离 | 每个节点经 `task_budget.run_bounded` 在独立进程组执行；`start_new_session`，超时 `SIGTERM`→`SIGKILL` 终止整组 |
| 节点时限 | `min(调用方给定的节点时限, 全局剩余挂钟时间)`；不足 0.5 秒直接 `PLAN_DEADLINE_EXCEEDED` |
| 资源槽 | 有 DSN 时按工具取跨进程 advisory lock 槽（默认 2）；无空槽立即 `TASK_BUDGET_BUSY` 并记为节点失败 |
| 取消传播 | `ExecutionContext.cancel_event` 置位后终止在飞进程组；未派发节点标记 `skipped` / `PLAN_CANCELLED` |
| 结果预算 | 节点返回的 `rows`/`tokens` 累加进全局预算，超限后未派发节点标记 `budget_exhausted` |
| 子进程载荷 | 唯一参数是包装体 `{node_id, tool, input, expect, inputs}`，必须返回 JSON 对象 |

`execute` 会自动绑定执行器自带的 `ExecutionContext`，因此调用方忘记共享 context 时**不会**静默退回默认节点时限。

### HTTP

`POST /v1/plans/execute`（admin + CSRF + no-store，完成后重查身份），请求体只包含 `nodes` 与可选 `budget`：

```json
{"nodes": [{"node_id": "a", "depends_on": [], "tool": "sql",
            "input": {"ref": "demo"}, "expect": null, "permission": "admin"}],
 "budget": {"deadline_s": 60}}
```

**调用方不能指定可执行文件**：工具到模块函数的映射来自服务端环境变量 `DATAMIND_VCAR_TARGETS`（`{"<tool>": ["<module>", "<function>"]}`），模块与函数名受白名单正则约束。未配置即 `503/VCAR_TARGETS_NOT_CONFIGURED`，仓库不附带真实工具注册表。

## 验证（0.23.0 增量）

`tests/test_vcar_bounded.py` **10/10**，完整隔离回归 **549/549**，见 `reports/vcar_bounded_verification.json`。覆盖：子进程 pid 与父进程不同、包装体载荷与依赖 inputs、目标白名单与路径穿越/命令注入样式拒绝、全局预算耗尽时节点被终止（2 秒预算下 `sleep 300` 在 20 秒内返回）、取消传播后子进程 pid 消失且未派发节点 `skipped`、`rows`/`tokens` 累加、资源槽占满返回 `TASK_BUDGET_BUSY`、以及 HTTP 的 401/403、422、未配置 503 fail closed 与配置后真实子进程执行。
