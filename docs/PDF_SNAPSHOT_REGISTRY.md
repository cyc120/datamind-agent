# 原生 PDF 快照的认证登记与历史读取

2026-10-07，代码 API 0.15.0。本阶段把已封存的原生 PDF 产物登记到独立版本库，支持短事务 CAS、原请求恢复和固定版本读取。离线准备流程见 [PDF 准备说明](PDF_PREPARATION.md)。这是解析快照登记，`registration_only=true`、`indexed=false`、`knowledge_visible=false`、`source_authority_verified=false`；管理员登记不等于来源权威、业务批准或知识库发布。

## 登记边界

迁移 `011_pdf_snapshots.sql` 只新增 `pdf_snapshot_assets`、`pdf_snapshot_versions`。每个文档有独立当前指针，历史行绑定文档/版本、源/解析器、job/attempt、manifest 与快照摘要、完整规范请求、真实登记账户/名称/角色和时间摘要。请求 ID 全局唯一，同一 job/attempt 只登记一次；延迟复合外键保证指针引用有效版本。正常应用流程不删除或覆盖历史版本；数据库所有者仍可管理记录，摘要不构成签名。

全部 `/v1/pdf-snapshots` 接口只允许当前有效的 admin。写操作还需 CSRF 和同源校验，客户端不能提供角色、登记人、授权标签或发布标志。应用读取走认证后的应用数据库连接；新表不给 `readonly_user` 或 PUBLIC 访问，结构化问数不能绕开私有文档接口。没有默认账户或新增账户注册入口。

API 只读取本检出的 `data/object_store/pdf_prepared/`，不接受自定义目录或源路径。离线 CLI 的 `--storage-root` 若指向其他目录，该目录产物不能直接通过 API 登记。源与产物仍是私有运行数据，不进 Git。

## 并发、认证与恢复

服务先在数据库事务外核对完整封存清单及源/产物摘要，再排序获取文档、请求和 attempt 锁。获取账户/会话共享行锁后，以独立 SQL 的 `clock_timestamp()` 重验当前启用、撤销、过期和 admin 角色，避免锁等待前的旧时间判断。登记要求准备产物的文档 ID 匹配请求、目标版本等于预期前版加一、原当前版与前源 SHA 均匹配；不同文档独立推进，同文档竞争只有符合 CAS 的请求成功。

新版本与当前指针在同一事务提交。事务失败不消耗 attempt、不删除已准备文件，可以原请求重试；CAS 失败保持预期前版，不把旧请求自动改为最新版本。同请求、同账户、同规范载荷重试返回原版本，即使当前头已经升级；换账户、改载荷或用新请求重消费 attempt 都拒绝。

所有新接口在完成读取/字节捕获后、构建响应前再次查询真实 admin 会话。读取期间注销、过期、禁用或降权时，不返回私有内容。POST 如果已经提交、随后最终权限检查失败，客户端可能收到 401/403；这不表示登记已回滚。恢复 admin 身份后应使用原请求查询/重试，不能换请求 ID 猜测结果。

每个服务进程最多 2 个验证操作，满额返回 503 `PDF_VERIFICATION_BUSY`；这不是跨进程全局限额。登记事务设置 5 秒 `lock_timeout`，锁等待超时返回 503 `PDF_REGISTRATION_BUSY`，原请求可重试。[PostgreSQL 官方说明](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-LOCK-TIMEOUT)规定它分别作用于每次锁获取，并不是整个请求或文件校验的 deadline。硬资源隔离、文件锁等待时限和跨进程资源槽仍待 worker 阶段实现。

文件摘要只证明校验时刻的本地完整性；拥有存储写权限的人仍可修改文件。下载使用 no-follow、非阻塞打开，捕获同一份 bytes 后再次核对其大小和注册摘要，防止返回校验后改变的内容；不声称能阻止可信文件系统操作者忽略文件锁。

## 接口

| 接口 | 作用 |
|---|---|
| `POST /v1/pdf-snapshots` | 登记已封存 attempt，追加版本与 CAS 当前头 |
| `GET /v1/pdf-snapshots` | 当前版本元数据列表 |
| `GET /v1/pdf-snapshots/{document_id}/versions` | 版本元数据历史 |
| `GET /v1/pdf-snapshots/{document_id}?version=N` | 完整核对指定版本；省略 version 查询当前指针在读取时的版本 |
| `GET /v1/pdf-snapshots/{document_id}/file` | 固定版本原 PDF 下载 |
| `GET /v1/pdf-snapshots/{document_id}/pages/{page_no}` | 固定版本 PNG |
| `GET /v1/pdf-snapshots/{document_id}/bundle` | 固定版本原生文本、块/切片与定位 |

文件、页图和 bundle 必须同时指定 `version>=1` 与 `expected_manifest_sha256`；摘要不符返回 409 `PDF_SNAPSHOT_SELECTION_CHANGED`。历史链接不随当前指针换版而变。响应包括身份/权限拒绝均 `Cache-Control: no-store`；文件和页图还设置 `nosniff`，原 PDF 按附件下载。

列表和历史支持 `limit`（默认 20、最多 50）与 `offset`（0–10,000），逐行核对元数据身份与摘要，明确 `artifact_integrity_checked=false`；不会为列表逐一读取全部页图。登记、单版本详情与 bundle 元数据中的 `artifact_integrity_checked=true` 限于本次完整校验时刻；文件和页图同样完整核对，并对实际返回 bytes 再验摘要。响应不包含 session hash、原始会话凭据或绝对存储路径。

新文档首次登记的请求结构如下。将全零 manifest 占位符替换为离线准备输出中的真实 `manifest_sha256`，其余身份也必须与准备请求一致：

```json
{
  "request_id": "register-demo-1",
  "document_id": "doc-example",
  "title": "原生 PDF 示例",
  "job_id": "prepare-demo",
  "attempt_token": "attempt-1",
  "manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "previous_version": 0,
  "previous_source_sha256": null
}
```

换版时先准备 `document_version=当前版+1` 的新 attempt，再提交当前版号及源 SHA。前版为 0 时 SHA 必须为 null，非零前版必须携带 SHA；JSON 版号不接受布尔值或字符串。标题去除首尾空白后必须非空。重试保留原规范请求。

## 验证和下一步

服务专项最终 **27/27**、HTTP 专项最终 **19/19**，进程退出 0。首轮服务 25/25、HTTP 15/15 日志保留，新增锁等待和完成后权限变化边界另行补测；这些断言不等于识别准确率、真实工厂审批或新模型成绩。报告位于 `reports/pdf_publications_targeted_*.txt` 和 `reports/pdf_publication_api_*.txt`。

完整隔离回归 **338/338**（原 292 + 服务 27 + HTTP 19）、进程退出 0，见 `reports/pdf_publication_regression.txt`。原库文档/块/切片/事实/复核/模型配置计数未变，新表未在原库创建；临时测试库和账号/产物已清理。测试副本不复制模型设置或私有快照行，原准备模块与 CAD/尺寸/DXF 的 11/13/8 个冻结摘要不变。核查记录见 `reports/pdf_publication_verification.json`；原检出和原 8000 服务未升级。

快照登记仍没有 PDF 上传/准备队列或操作页面，HTTP 不运行原生解析、OCR、公式或嵌入模型。旧 `document_assets/blocks/chunks/facts`、复核/审批和 Qdrant 不被登记改动；同字符串文档 ID 在这个独立命名空间中也不会替换原知识库文档。后续 API 0.16.0 已另实现 [私有知识版本发布](PDF_KNOWLEDGE_PUBLICATION.md)，通过独立 outbox/向量存储完成预写、确认、CAS 与冻结计划恢复；快照登记行的标志不随之改写。私有模型问答、有资源槽/时限的 PDF/OCR worker、已发布点修复与孤立回收仍待实现，普通任务不能调用全局 reset。
