# V-CAR：通用依赖计划与有界修复

2026-10-07，API **0.22.0**。本阶段实现技术设计说明书 §6.1/§6.4 与 §15-P3 记载的通用编排：`Plan → Execute → Verify → Repair → Reverify`。此前只有「限定工业规则 DAG」，通用动态 DAG 与断点恢复为待实现。

实现位于 `datamind/vcar.py`，是编排原语：**它不自己调用工具**，由调用方注入每个工具的执行器。

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
- **断点恢复**：`state` 中 `succeeded` 复用且不重跑，`unresolved` 不自动消解，`pending/failed/skipped/budget_exhausted` 重新调度；非法 state 一律 `PLAN_STATE_INVALID`。

## 验证

`tests/test_vcar.py` **16/16**，完整隔离回归 **539/539**（上一阶段 523 + 新增 16），见 `reports/vcar_verification.json`。覆盖拓扑排序、全量校验（环/重复 id/未知依赖/工具/权限/额外字段/路径式 id）、依赖消费、**真实并发**（`threading.Barrier` 验证重叠）、失败隔离、**只重跑受影响子图**（独立节点恰好执行 1 次）、修复上限（attempts=3）、四类预算耗尽、未消解冲突（repairs=0）、缺失执行器与非法结果、断点恢复与非法 state 拒绝。

## 边界

- 交付的是**编排原语与契约**；尚未接入 HTTP 路由或替换既有固定工业规则链路。
- 节点仍在执行进程内运行，**未接入 `task_budget` 的进程组/资源槽**；跨进程节点执行与取消传播待做。
- **未实现视觉载荷与局部重识别**——§6.4 的机制消融实验（普通 OCR＋检索 / 结构化规则 / 局部重识别 / 完整闭环）与误修复率、错误消解率统计仍待做。
- 未提供运行/事件的数据库持久化适配层；状态以可 JSON 序列化字典往返。
