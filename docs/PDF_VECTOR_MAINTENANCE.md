# 私有 PDF 向量修复与孤立点回收

2026-10-07，API **0.21.0**。本阶段补齐 [私有知识发布](PDF_KNOWLEDGE_PUBLICATION.md) 与 [私有 PDF 检索](PDF_PRIVATE_SEARCH.md) 中记录的缺口：「发布后的点损失拒绝但无自动修复」与「无孤立点回收」。前置准备、登记与发布分别见 [PDF 准备](PDF_PREPARATION.md)、[快照登记](PDF_SNAPSHOT_REGISTRY.md)、[知识发布](PDF_KNOWLEDGE_PUBLICATION.md)。

## 修复：只恢复冻结计划内的点

`POST /v1/pdf-knowledge/publications/{publication_id}/repair`（admin + CSRF + no-store）。

- 只接受 `status='published'` 且持有冻结 `vector_plan` 的版本；未知 id → 404，路径式 id → 422。
- 写字节前用**固定 snapshot/bundle** 重新 `validate_plan`，计划被篡改或被换版即失败。
- 内容只来自冻结计划：缺失点按原 id/payload/向量字节 `upsert`，随后 `require_all` 逐一核对；**不发明内容**。
- **不改**计划、`pdf_knowledge_requests` 行、`result_sha256` 或 `pdf_knowledge_heads`；测试对发布行逐字段比对，确认修复前后完全一致。
- 返回 `expected`、`missing_before`、`repaired`、`collection`、`verified`、`head_unchanged`。

底层原语是 `pdf_vectors.repair_plan(plan)`；发布路径的 `write_and_verify` 改为复用它，行为不变。

## 回收：只删没有任何冻结计划引用的点

- `GET /v1/pdf-knowledge/orphans`：列出 `pdf_private_*` 集合的点数、被引用数与孤立点数（样本最多 20 个 id）。
- `POST /v1/pdf-knowledge/orphans/reclaim`，请求体 `{"collection": "...", "confirm": false}`：
  - **默认 dry-run**，`confirm=false` 时绝不删除。
  - 集合名必须以 `pdf_private_` 开头，否则 `422/PDF_MAINTENANCE_COLLECTION_FORBIDDEN`；公开知识集合无法被回收。
  - 引用集来自**所有** `vector_plan` 非空的登记行（排队/发布中/失败/已发布），并对每份计划执行 `_checked_plan`；计划被篡改直接失败，而不是按不可信内容判断“可删”。
  - 删除前**重新读取引用集并再次相减**；删除后重扫，若仍有孤立点返回 `PDF_MAINTENANCE_ORPHANS_REMAIN`。
  - 批次默认 256（上限 1024）；返回 `deleted`、`remaining_orphans`、`orphan_sample`。

## 验证

`tests/test_pdf_maintenance.py` **10/10**，完整隔离回归 **523/523**（上一阶段 513 + 新增 10），见 `reports/pdf_maintenance_verification.json`。覆盖：删除已发布点后修复（`missing_before=1`、点集恢复、发布行不变）、完整时报告 0、未知/非法 id 拒绝、孤立点扫描只统计无引用点、dry-run 不删除、confirm 删除且引用点全保留、回收后再修复仍为 0（证明未误删引用点）、非私有集合与非法批次拒绝、请求体严格校验、HTTP 未登录 401/403 与 admin 全链路。

测试使用内存 Qdrant（`client_factory` 被替换）。**本阶段未重跑真实认证私有 Qdrant 烟测**。

## 边界

- 跨存储读取不是事务：在读取引用集之后才冻结的新登记不在本次保证范围内；删除前会重读一次以收窄窗口，但不构成分布式事务。
- 修复不做猜测性重建：计划本身损坏或快照/清单变化时拒绝。
- 回收只判断“是否有冻结计划引用”，不判断业务价值；**被删点不可恢复**。
- 未实现自动后台修复调度，也没有“发布后点损失”的自动告警。
- 普通发布/检索任务仍不执行 reset 或删除 collection。
