# 私有 PDF 知识版本发布与恢复

2026-10-07，API 0.16.0。本阶段从已认证登记的原生 PDF 快照创建私有知识版本：请求入队、冻结向量计划、预写/逐点确认、短事务 CAS 发布、原请求恢复和显式历史切片读取。准备与登记分别见 [PDF 准备](PDF_PREPARATION.md)、[PDF 快照登记](PDF_SNAPSHOT_REGISTRY.md)。

发布只面向 admin 私有空间。`private_indexed=true` 仅表示数据库已提交该私有版本；`public_knowledge_visible=false`、`source_authority_verified=false`。旧公开问答、旧知识表、事实/复核/审批和演示向量 collection 不接入此流程。当前提供状态和带来源的切片读取，没有新增 PDF 上传、操作页面、解析/OCR 队列或私有模型问答入口。

后续 API 0.17.0 增加已发布版本的管理员多文件证据检索，完整计划摘要和历史选择保持本契约；仅返回本地检索证据，未增加模型回答。范围、跨进程检索槽与复核窗口见 [私有 PDF 检索](PDF_PRIVATE_SEARCH.md)。

## 发布与中断恢复

`pdf_knowledge_requests` 保存原 owner、执行会话摘要、规范请求、目标快照/登记证据、状态/租约、冻结向量计划和发布结果；它同时是持久 outbox。`pdf_knowledge_heads` 保存每文档当前私有 publication ID，复合外键防止指针引用别的文档。新表不给 PUBLIC/readonly 权限；应用读取使用受 admin 接口保护的数据库连接。

提交请求必须明确指定快照版本、manifest SHA 和预期前 publication ID（首次为 null）。完整文件验证在事务外进行，事务内复用快照登记的文档锁，重新检查真实身份、目标快照当前头和私有头 CAS。队列记录不是发布完成。同请求、同 owner、同规范载荷重试返回最初 publication，头已换版也不创建重复版本；改载荷或复用他人请求拒绝。

worker 默认一项、可并行两项；所有 worker 通过 PostgreSQL 共用最多两个 running 槽。领取 token 随机，租约 120 秒、约 40 秒续租；到期最多自动领取三次，随后由原 owner 明确重试。失败项重试刷新原 owner 当前会话，保留目标、预期前头与冻结计划。原会话失效会记录失败，worker 不能用自身权限代替申请人。

嵌入和 Qdrant 操作在数据库事务外运行。首次计划只有在有效 token/租约和真实原 owner 权限下成功持久化后才允许写向量。计划保存完整来源切片、profile、点 ID、不可变 payload 与 float32 向量；一旦冻结，恢复直接重放这些字节，不重新嵌入或替换 profile。旧 worker 即使迟到写向量，也只能重放同一计划，不能提交知识头。

预写使用小批次和 `wait=true`，随后逐点读取实际 vector/payload，比对完整 ID 集合、维度、值和来源。ack 或点数不算确认。最后再次检查文件/数据库证据、原 owner、目标快照头、预期私有头和 token/租约，同事务提交 published 状态、结果与当前指针。CAS 冲突保留错误，不改旧请求基线，不删除其他请求或旧版本的点。

计划已冻结但进程中断、部分写入或确认失败，可在租约回收/失败项重试后恢复；计划不存在则重新做首次构建。已发布版本的历史不会因为新头而被重放到当前。发布后的外部点丢失或篡改由切片读取对账并拒绝。API 0.21.0 新增受控维护入口：`POST /v1/pdf-knowledge/publications/{id}/repair` 按冻结计划补齐丢失点（不改计划、发布行或当前头），`GET /v1/pdf-knowledge/orphans` 与 `POST /v1/pdf-knowledge/orphans/reclaim` 扫描/回收无任何冻结计划引用的私有集合点（默认 dry-run，仅限 `pdf_private_*`）。见 [向量修复与孤立回收](PDF_VECTOR_MAINTENANCE.md)。普通任务不执行 reset 或删除 collection。

## 向量 profile 与独立存储

生产嵌入使用本地 `BAAI/bge-small-zh-v1.5`，profile 绑定实际模型文件摘要、SDK/运行环境与向量契约。没有自动下载或外部模型请求；本检出无缓存时须显式配置本地模型缓存。向量为 512 维，有限且非零，归一化后冻结为 float32；单版本最多 512 个切片、20 万 UTF-8 文本字节和 16 MiB 规范计划。超限独立拒绝，不截断后声称完整。

Qdrant 采用独立 `pdf_private_<profile 摘要>` collection，Dot 距离与固定 float32 配置；point ID 绑定 publication、profile 与切片身份，payload 不包含 lease/current/published 等可变标志。来源 chunk 的 bbox 保持原数值，向量 payload 使用 `bbox_f64_hex` 保存同一坐标的精确十六进制字符串，避免实际服务器 JSON float64 回读差异。Cosine 上传会自动归一化，因此本实现用显式 float32 规范化与 Dot 确保可核对实际读回值，见 [Qdrant collection 说明](https://qdrant.tech/documentation/manage-data/collections/)；返回原始 vector/payload 的接口见 [读取点说明](https://api.qdrant.tech/api-reference/points/get-points)。

独立部署配置为 `deploy/compose.pdf-private.yaml`，只映射 `127.0.0.1:6335` REST，独立卷且必须配置 API key。生产客户端只接受明确的 `DATAMIND_PDF_QDRANT_URL` 和 `DATAMIND_PDF_QDRANT_API_KEY`，不回退旧演示实例；连接前确认无凭据访问被拒绝。payload ACL 本身不是底层鉴权，[Qdrant 安全说明](https://qdrant.tech/documentation/security/)要求自行配置认证和网络绑定。本阶段的边界是应用 admin 与可信本机/数据库/向量存储操作者，摘要不是签名。

在需要运行的新检出与目标数据库中，先设置写入 DSN、私有 Qdrant key 和本地缓存，再执行：

```bash
export DATAMIND_PDF_QDRANT_URL=http://127.0.0.1:6335
export DATAMIND_PDF_EMBED_CACHE=/absolute/path/to/fastembed-cache
docker compose -f deploy/compose.pdf-private.yaml up -d
python db/migrate.py
python scripts/pdf_index_worker.py --watch --workers 1
```

迁移会写所配置数据库，启动会创建独立向量服务；开发回归只使用临时数据库/向量实例。本阶段没有升级原检出或原 8000 服务。跨进程两个任务槽不等于 CPU/内存硬隔离，5 秒数据库锁等待限制不等于整个 embedding/请求 deadline。

## 私有接口与版本读取

全部接口只允许当前有效 admin，写操作还需 CSRF/同源，私有响应均 no-store。结果构建完成后再次检查当前会话，权限在读取期间失效时不返回内容；入队/重试若已提交、随后最终认证失败，不代表回滚，应恢复身份后原请求查询/重试。

| 接口 | 作用 |
|---|---|
| `POST /v1/pdf-knowledge/publications` | 已登记快照入队，HTTP 不执行嵌入 |
| `GET /v1/pdf-knowledge/publications` | 分页安全状态，limit 默认 20、最多 50，offset 最多 10,000 |
| `GET /v1/pdf-knowledge/publications/{publication_id}` | 固定请求状态 |
| `POST /v1/pdf-knowledge/publications/{publication_id}/retry` | 原 owner 重试 failed 项，保留原计划/基线 |
| `GET /v1/pdf-knowledge/publications/{publication_id}/chunks?expected_plan_sha256=...` | 已发布历史切片，完整验证快照/计划与实际向量 |
| `GET /v1/pdf-knowledge/documents/{document_id}` | 当前私有头；快照已换版而知识尚未追上时拒绝 |

状态元数据明确 `vector_integrity_checked=false`，不为列表逐点访问向量。历史切片读取核对客户端选择的完整持久计划摘要，并返回 `vector_integrity_checked=true`，范围限于本次核验时刻；新快照头不改变显式旧 publication 的内容。响应包含文本、页、块、坐标和块内字符偏移，保留来源边界，不返回向量、执行会话摘要或绝对路径。

首次请求结构如下。manifest 全零值只是占位符，须替换为已登记快照真实值：

```json
{
  "request_id": "index-demo-1",
  "document_id": "doc-example",
  "document_version": 1,
  "expected_manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "previous_publication_id": null
}
```

字段不接受路径、客户端角色、发布标志、向量或模型配置。当前版本改动需另一个明确目标/前 publication 请求，不自动把失败任务改成最新基线。切片与向量一致不证明复杂表格结构、来源权威或自然语言语义蕴含。

## 验证记录

控制面最终 **31/31**（初始 29/29）、向量最终 **24/24**、HTTP 最终 **18/18**，进程退出 0；完整隔离回归 **411/411**（原 338 + 31 + 24 + 18）、失败/错误 0、退出 0。控制面故意使用假向量 adapter，向量与 HTTP 使用真实内存 Qdrant 和明确合成嵌入，不能混称真实模型验证。日志分别保留在 `reports/pdf_index_targeted_*.txt`、`pdf_vectors_targeted_*.txt`、`pdf_index_api_*.txt`、`pdf_index_regression.txt`。

独立临时 Qdrant 1.12.4 完成 **7 项检查**：无 key/错 key 拒绝、生产 factory、实际并发创建竞争、70 点完整 float32/payload 读回、重复重放和损坏点拒绝且不覆盖。首轮实际服务器发现 bbox float64 相差 1 ULP，向量 bytes 全相同；改为 `bbox_f64_hex` 后才通过。失败、诊断、最终通过记录分列在 `reports/pdf_vectors_real_qdrant_*.txt`，不采用宽容数值比较来放行来源变化。

另有一个隔离 DB/独立带 key 服务器/本地真实 BGE/实际 worker 子进程的端到端场景通过：v1 发布、v2 后旧响应字节不变、过时当前拒绝、原请求恢复不移头、错误 key 导致失败 exit 1、原 owner 重试且缺少模型缓存时仍重放冻结计划成功。该场景最初两次分别因脚本只接受 403 和 macOS `/var` 别名不符合严格源路径门禁失败，修正验证脚本后最终退出 0；准备模块未放宽。原日志与模型 profile 见 `reports/pdf_index_real_local_*.txt`、`pdf_index_real_local_verification.json`。

原库计数一致、新表未在原库创建，临时库/账号/产物/向量容器已清理；模型缓存只读，无下载或外部模型调用。准备、认证快照、旧摄取/知识/事实/worker 模块与 CAD/尺寸/DXF 的 11/13/8 冻结文件摘要不变。核查及审查边界见 `reports/pdf_index_verification.json` 和 `pdf_index_review.md`。合成测试与本地嵌入烟测不算问答/识别准确率、工厂泛化或正式来源批准。
