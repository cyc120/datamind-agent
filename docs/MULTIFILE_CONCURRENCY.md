# 多文件并发与多文档问答

更新：2026-10-07。本文区分现有行为、历阶段门禁及后续 PDF 摄取方案。多文件解析、资产登记和问答证据属于不同操作，不能靠同时启动多个旧摄取进程解决。

## 当前边界与代码审计

| 环节 | 当前实现 | 并发问题或限制 |
|---|---|---|
| PDF 演示摄取 | `ingestion/ingest.py` 收集目录后逐文件解析、提取、嵌入、落库 | `main()` 不是安全的多 worker 入口；没有任务领取、租约、取消或重试记录 |
| 全局重建 | `--reset` 清空文档、块、切片、任务并删除共享 Qdrant collection | 任一重建可使其他读取/写入消失；并发普通摄取也有 collection 检测/创建竞争 |
| 单文件刷新 | 原文件对应块/事实/切片执行 `DELETE` 后重建；Qdrant 先删旧 point 后 upsert，最后提交 PostgreSQL | 向量和元数据无法同一事务提交；中途失败可出现旧元数据配新向量、缺向量或孤立向量 |
| PDF 标识与版本 | 文档 ID 基于标题和部分源 SHA，块/切片 ID 基于文档与序号；`doc_version`、向量 `document_version` 固定为 1 | 相同目标缺少版本 CAS；同一源改名可能撞 `(collection,sha256)`；旧版内容不能稳定保留 |
| 页图和公式裁剪 | 页图路径 `page_images/{document_id}/page_NNN.png`；公式裁剪为 `.cache/formula/{source_sha}/page-X-figure-Y.png` | 同文档/同源重复解析会写相同路径；页图无版本隔离，写入并非原子发布 |
| OCR/表头识别 | 进程内缓存 RapidOCR 与表头 engine，ONNX 每实例 2 个 intra-op / 1 个 inter-op 线程 | 缓存没有覆盖推理的互斥；不能假设共享 engine 可任意多线程重入；多进程模型副本占内存 |
| 公式识别 | 每次识别创建隔离子进程，最多 6 个原生图像候选、90 秒超时 | 单任务有限额，但没有全服务的 worker 限额；并发文件会同时加载多份模型 |
| 问答 SSE | 每请求一 daemon thread 和无界 `queue.Queue` | SSE 为传输通道，不是持久任务队列；断线不会自动终止模型工作，重启不能恢复领取状态 |
| `ingest_jobs` 表 | 仅 ID/文档/类型/状态/错误/时间字段，旧主流程不写该表 | 没有输入快照、请求幂等、任务占有权、租约、attempt、cancel 状态和事件序号 |
| CAD 资产/版本 | 认证后按文档、请求 ID 获取排序 advisory lock；前版/源 SHA CAS；不可变版本和内容 SHA 固定副本 | 不同资产可并发，同一资产旧状态请求返回 409；解析仍处于短事务中，当前仅受控 DXF 大小和实体数 |
| 尺寸登记/绑定 | 按测量、绑定、图纸、请求 ID 排序锁，真实账户重验、前版/摘要 CAS；比较保存原始快照 | 可复用现有写入服务，队列不能替它批准关联或使用；测量与图纸不自动配对 |

审计没有修改冻结解析器或原数据库。旧演示 CLI 继续作为串行离线工具；不能给它加一个线程池便称为安全并发。

## 本轮多文档问答门禁

原 RAG 已经在多个公开 collection 中检索，并把文件 ID、版本、源 SHA、页和块写入引用。现在显式选择使用 `document_refs`，每项必须同时提供：

```json
{
  "document_id": "doc-example",
  "document_version": 1,
  "source_sha256": "64 位小写 SHA256",
  "parser_version": "当前解析器版本"
}
```

约束为 1–8 个无重复文件。服务在同一个 PostgreSQL repeatable-read 只读快照中校验所选文件公开、当前已索引、无待复核标志、属于允许的 collection，并精确匹配版本/源 SHA/解析器版本。显式选择不受问题中的标题推测影响，未选择的文件无法进入上下文；不存在、私有或不可用的所选文件会阻断，不回退全库。

检索继续采用词法/向量融合。显式模式最多 8 个上下文片段，每份选中文件保留至少一个有相关度的有效片段；长文件不能占满全部名额。范围最多 5,000 个候选切片，超限明确拒绝。`selected_documents` 保存每文件当前头和有效切片集合 SHA，`coverage` 报告相关片段覆盖及缺证文件。一份文件无相关有效证据时，在模型调用前返回 `MULTIFILE_EVIDENCE_INCOMPLETE`。

模型提示与最终引用保留各自文件/版本/源 SHA。原文连续片段和数字校验仍执行；不能按同名对象自动跨文件关联，冲突值应分别引用双方。完成答案前重新校验所选头及切片集合；生成期间换版、撤销公开访问或刷新内容均以 `DOCUMENT_SELECTION_CHANGED` 阻断，要求基于最新范围重新发问。这个检查只证明检查时刻没有变更，不承诺答案返回后原文件永远不变。

`coverage` 是检索片段覆盖，不能解释为完整阅读或已解决全部问题。`answer_coverage` 另列实际引用和未引用的文件。现有逐字/数字校验不证明语义蕴含；答案继续显示 `semantic_entailment=not_independently_verified`，不宣称能够自动发现或裁定任意自然语言冲突。

| 错误码 | 含义 |
|---|---|
| `DOCUMENT_SELECTION_INVALID` | 空选择、超过 8 文件、重复 ID 或结构/身份格式无效 |
| `DOCUMENT_SELECTION_UNAVAILABLE` | 所选文件不存在、不公开、未索引、需复核或在范围外 |
| `DOCUMENT_SELECTION_CHANGED` | 版本/源/解析器与选择不符，或模型生成期间当前证据变动 |
| `DOCUMENT_SELECTION_TOO_LARGE` | 显式范围超过 5,000 候选切片 |
| `MULTIFILE_EVIDENCE_INCOMPLETE` | 至少一份所选文件没有检索到相关有效证据；不调用模型 |

## 本轮受控文件任务队列

首个适配器接入受控 CAD DXF 与单条尺寸测量 JSON 登记：它们已经有源字节上限、内容校验、事务内身份检查、幂等请求和版本 CAS。`POST /v1/files/uploads` 逐文件接收原始字节，DXF 上限 2 MiB、JSON 上限 1 MiB，当前 API 进程最多 4 个上传槽。按内容 SHA 原子固定到允许的 staging 目录，上传返回路径与 SHA；上传成功仅代表源已暂存，解析/登记结果由后续任务单独报告。

`POST /v1/files/jobs` 每批 1–20 项、默认并发 2，可配置 1–4。迁移 `db/migrations/010_file_jobs.sql` 新增 `file_jobs/file_job_items`，持久保存批次请求、真实申请账户、每文件固定输入与预期前版、领取尝试、结果摘要及状态。所有 worker 进程通过 PostgreSQL 共享限制最多 4 个 running 项，并同时遵守每批限额。`GET /v1/files/jobs/{job_id}` 返回逐项结果，另有 `POST /v1/files/jobs/{job_id}/cancel` 与 `/retry`。上传、任务提交、查询及控制接口均需当前有效的管理员会话；写操作还校验 CSRF 和同源。任务仅操作受控 DXF/单记录尺寸 JSON，不接通用 PDF/OCR；问答选择的文件仍来自已索引公开文档库，CAD/测量不会被自动混入 RAG。

worker 是独立本地进程，运行 `scripts/file_job_worker.py`，支持 `--once/--drain/--watch` 及 `--workers 1..4`。队列提交后必须有 worker 执行；HTTP 服务的 SSE、页面轮询不替代 worker。一个格式错误文件失败不会撤销其他文件已成功登记的不可变版本。

在准备运行的 checkout 根目录、使用已安装项目依赖的 Python 解释器，先将 `DATAMIND_META_WRITE_DSN` 指向要升级的元数据库，再执行迁移与 worker：

```bash
python db/migrate.py
python scripts/file_job_worker.py --watch --workers 2
```

迁移命令会写所配置数据库；开发回归通过 `scripts/test_isolated.py` 先创建临时元数据库并在副本执行迁移，没有升级原演示库。`--watch` 空闲时每 2 秒查询一次，直到进程被中断；重启后沿用数据库中的任务与租约。`--once` 只处理一波可领取项，`--drain` 处理目前可领取的项，二者都不会等待其他进程尚未到期的租约。可用 `--job-id file-job-…` 限定处理批次。多个 worker 进程仍共用全局上限，不能把 `--workers 4` 理解为每个进程额外获准四个槽。

请求 ID 复用会比较规范载荷与账户，不能把他人的请求当作本人的成功重试。领取以 advisory lock 及批次→文件行锁为原子边界，任务持有随机 claim token 和 120 秒租约，40 秒续租；执行、续租和提交结果均检查有效租约，提交结果同时核对 token，过期 worker 无权覆盖新的 owner。每轮最多领取 3 次，租约耗尽后由原申请账户明确重试。重试只重新排队 failed 项，刷新原账户当前认证会话，不改输入或已成功项；cancelled 批次不能借此恢复。私有 session hash 不进入公开响应。实际登记事务再次读取原申请账户的当前角色、启用状态与有效会话，不能使用前端角色声明，也不能把 worker 自己的管理员权限归给原申请人。

下层登记使用由批次/文件身份确定的固定子请求 ID。源路径、SHA 和预期前版保持不变，执行时 staging 字节变化会独立拒绝。每项继续通过 CAD/测量服务自己的身份复核、事务锁、源校验及前版 CAS；同资产两个请求基于相同旧版时只有一项成功，其余项保留冲突错误，不自动把旧请求改成新版本。恢复首先按固定子请求 ID 查询已提交版本并验证输入、申请账户和源证据，再发布已有成功结果；需要重放时仍使用同一子请求 ID，避免增加重复版本。

取消立即标记 pending/failed 项，并在 running 项执行前协作检查；已经提交的登记仍记为 succeeded、保留历史，取消不会回滚成功资产。登记事务与队列结果写入是两个提交边界，因此 running 不等于尚未写入资产：如果 worker 提交后崩溃，恢复可找回真实成功。取消后租约过期、尚无可见提交时，任务记为 failed 和 `FILE_JOB_CANCELLED_OUTCOME_UNCERTAIN`，提示核查实际资产头；它不假称已经回滚可能进入下层事务的写入。取消请求无法中断已进入的数据库登记事务。

状态来自 PostgreSQL 中逐项记录：`pending → running → succeeded/failed/cancelled`，批次根据各项汇总为 queued、running、succeeded、partial_failure、failed 或 cancelled。任务不是整批全有或全无的事务；一份文件失败只影响该项。未经测量与 CAD 明确绑定、关联复核及独立使用批准，队列登记成功也不允许尺寸比较，更不构成生产质量结论。

后续 PDF/OCR 应先使用保守资源上限：默认 2 个轻量文件解析 worker、OCR/表头 1 个工作槽、公式 1 个槽、嵌入 1 个槽；每任务仅有限个待处理页，并新增全局待处理容量上限。页数/像素/总字节和内存阈值必须与测试报告一起公布；这些值是启动配置，不是硬件容量结论。不要一次 `gather()` 全部文件，更不要仅按 CPU 核数生成 OCR worker。

批量上传与批量读取本地 staged 文件明确区别。底层登记 API 继续只接受允许目录中的路径；本轮上传入口只支持受控 DXF 和 JSON，按流计数并核对哈希。前端展示逐文件成功、拒绝、等待和可重试错误。Office、PDF、图像、压缩包和一般 CAD 文件仍未接入此队列；格式和业务范围未通过下层解析的文件独立失败。

## 原生 PDF 离线准备基础（2026-10-07）

`ingestion/pdf_prepare.py` 与 `scripts/prepare_pdf.py` 已实现按 SHA 固定源、`job_id/attempt_token` 私有产物、版本化文本块/切片和最后写入的封存标记。不同文件通过独立进程准备；同 attempt 和共享源用本地文件锁协调，重放必须核对全部摘要且不覆盖原产物。模块拒绝线程准备、FIFO、加密/缺少原生文本及资源超限，旋转/裁剪定位、inline 图像与 SMask/Mask 纳入验证。

这一步明确 `prepare_only=true`、`indexed=false`、`knowledge_visible=false`。本地声明版本不代表数据库当前头或认证登记；现有 DXF/测量队列没有接入 PDF，原生 PDF 也没有进入问答库。准备没有 OCR/公式/模型调用与非挂钟硬内存隔离；API 0.19.0 起 `scripts/prepare_pdf.py --deadline` 在独立进程组内运行准备，超时后终止整组（含孙进程），见[统一任务预算与进程时限](TASK_BUDGET.md)。使用和具体上限见 [原生 PDF 准备说明](PDF_PREPARATION.md)。

最终专项 **31/31**、实际 CLI **8/8**、完整隔离回归 **292/292**，进程退出 0。首轮两个共享锁创建竞争错误和诊断日志保留；最终修复和资源/定位边界补测后才通过。原库计数未变，临时数据库与 CLI 产物已删除，冻结 11/13/8 文件摘要一致；报告见 `reports/pdf_prepare_*`。这些结果是合成边界/兼容验证，不是识别准确率。后续完成的私有快照登记和知识发布见下两节，通用解析队列仍待实现。

## PDF 私有快照认证登记（API 0.15.0，2026-10-07）

新增独立 `pdf_snapshot_assets/pdf_snapshot_versions`，已封存 attempt 通过真实 admin、CSRF/同源与事务内当前身份检查登记；请求/attempt 唯一约束、预期前版/前源 SHA CAS、原请求/原 owner 恢复与不可覆盖历史读取均已实现。`/v1/pdf-snapshots` 全部仅 admin，文件/页图/bundle 必须指定版本及 manifest SHA；返回 bytes 再核对摘要，最终响应前重验真实权限。POST 可能已经提交但最后权限检查失败，恢复身份后必须原请求重试，不能假称回滚。

本阶段仅 `registration_only=true`，继续 `indexed=false`、`knowledge_visible=false`、`source_authority_verified=false`；旧知识表、事实/审批与 Qdrant 不被登记改动，新表不给 SQL readonly 访问。两个验证槽是每服务进程上限，5 秒限于单次 DB 锁等待，尚非整个请求 deadline。使用、分页元数据与实际文件验证的区别见 [PDF 快照登记说明](PDF_SNAPSHOT_REGISTRY.md)。

服务专项最终 **27/27**、HTTP **19/19**、完整隔离回归 **338/338**（292 + 46），进程退出 0。初始服务 25/25 与 HTTP 15/15 保留，新增锁等待/完成后认证变化回归；原库计数未变，新表未在原库创建，原准备模块与冻结 11/13/8 文件未改，临时测试库/账户/产物已清理。报告为 `reports/pdf_publication*`，没有新增浏览器页面或模型评分。该登记阶段没有知识索引，后续独立私有知识发布见下一节；PDF/OCR 解析 worker 仍待实现。

## PDF 私有知识版本发布（API 0.16.0，2026-10-07）

已封存且认证登记的快照可以另提交私有知识发布请求，HTTP 只入队，独立 CLI worker 做本地嵌入/向量预写/逐点确认，最后短事务 CAS 更新私有知识头。独立 outbox 冻结规范输入与完整计划，120 秒 token/lease 和约 40 秒续租防止过期 owner 提交；全 worker 共用最多 2 个 running 槽，原 owner 重试保留基线与计划，不重新嵌入已有冻结输出。准备/登记与知识头是不同命名空间，不覆盖旧知识表或公共向量。

只有新独立带 key、loopback 的 Qdrant 实例可以作为生产连接；Dot/float32、profile/来源/payload/点 ID 均固定，实际逐点 readback 确认而非只看 ack/count。chunk bbox 保持原数值，payload 使用 `bbox_f64_hex` 克服真实服务器 float64 JSON 回读差异。admin 历史切片要求计划摘要并重验快照/实际点；当前元数据在快照头已换版时拒绝。仍 `public_knowledge_visible=false`，没有公开 RAG 或上传/解析/OCR 队列；发布后点损失已可按冻结计划受控修复，孤立点可扫描/回收（API 0.21.0，见 [向量修复与孤立回收](PDF_VECTOR_MAINTENANCE.md)）。详见 [私有知识发布说明](PDF_KNOWLEDGE_PUBLICATION.md)。

控制面 31/31、向量 24/24、HTTP 18/18、完整隔离回归 **411/411**，进程退出 0。假向量控制面、内存 Qdrant+合成嵌入、独立真实 Qdrant 7 项、实际本地 BGE/CLI 一个场景分别记录；真实服务器首次精度失败和两个 CLI 验证脚本失败保留，最终修正后才通过。原库/旧模块/冻结 11/13/8 文件未改，临时库/账户/产物/向量服务已清理，无外部模型调用，不作新识别或问答准确率。报告见 `reports/pdf_index_*` 与 `reports/pdf_vectors_*`。

## PDF 私有多文件检索（API 0.17.0，2026-10-07）

`POST /v1/pdf-knowledge/search` 只接管理员显式选择的 1–4 个已发布 publication/完整计划 SHA；历史选择不随当前头换版，重复文档、缺损点或任一证据变化拒绝整次请求。同完整本地 embedding profile 才可联合计算 Dot，候选严格闭域，不读其他已发布/孤立点。每文件优先保留一个过实验阈值的片段，再全局补齐；逐文件覆盖明确返回，未生成答案。

全部 API 进程共享同一数据库的两个 session advisory 检索槽，忙时 503，模型/文件/向量操作没有长事务；连接失效后探测并拒绝结果。总候选 512、源文本 20 万 UTF-8 字节、DB JSONB 计划 16 MiB，取行时同 SQL 过滤预算并再次核对实际内容。所有文件复核后联合读取完整点集合，再单条 SQL 复核原始 DB JSONB 摘要，响应编码后重查身份。

两个检索槽与两个索引 running 槽独立，数据库断连不能物理取消已进入原生推理的任务，不是全部任务统一 CPU/内存硬隔离或完整 deadline。API 0.19.0 已新增[统一任务预算与进程时限](TASK_BUDGET.md)提供挂钟 deadline、进程组终止与跨进程资源槽，登记 worker、PDF 准备 CLI、扫描页 OCR 与公式会话均可选接入；统一 CPU/内存配额与跨主机租约仍待接入。专项 14/14、联合 capture 8/8、HTTP 32/32，本地真实 BGE/独立认证 Qdrant 的两文件历史场景通过；合成与实际模型记录分别保留。私有模型回答、受控向量修复与孤立点回收已完成，PDF/OCR 解析队列与统一 CPU/内存配额待补，完整限制见 [私有 PDF 检索](PDF_PRIVATE_SEARCH.md)。

## PDF/OCR 完整发布路线（私有索引/冻结计划恢复与检索已实现，解析队列和完整回收待补）

1. 接收每文件源字节到按 SHA 命名的不可变存储，并保留批次/原文件名/独立资产 ID。页图、裁剪和解析产物写入 `job_id/attempt_token` 私有目录，不覆盖当前可见产物。
2. 在短事务中入队；worker 原子领取后释放数据库事务再做耗时解析。数据库连接不横跨 OCR/模型调用。页数、像素、时间、源总字节及输出产物均有限额，超限文件独立失败。
3. 在新 `document_version` 下生成版本化块/事实/切片 ID。事实仍保留文件、页、块、字段和生产范围；同名零件、数值或相近位置不能成为自动关联依据。修改现有 schema 的唯一索引前，先制定向后兼容迁移和旧审批隔离方案。
4. 将新版本 point 写入 Qdrant 并确认该版本可查询，暂不移动 PostgreSQL 当前指针。point ID 与 payload 都包含资产版本和嵌入模型版本，旧向量暂时保留。
5. 事务内以预期前版 CAS 提交新版本元数据并更新当前指针。检索只接纳 PostgreSQL 当前版本中的 chunk ID，再与向量候选交集；失败的新向量无权成为证据。CAS 失败保留任务错误，不能删除其他 worker 发布的版本。
6. 使用 outbox/对账记录恢复向量写入或未完成发布；回收只删除无版本引用且经过宽限期的孤立产物。普通摄取任务永远不调用全局 reset 或删除共享 collection。
7. 将“完整比较所选文件”扩展为按文件小任务、依赖图和归并步骤，文件不足的阶段明确停下或返回缺证范围。一次模型上下文的 top-k 相关片段不等于全文审阅能力。

## 必须验收的竞争场景

- 多文件批次部分格式失败：其余文件独立完成，重试失败文件不重复登记成功文件。
- 两个 worker 同时领取：每次 claim 只有一个 owner；租约过期后旧 owner 的完成写入被拒绝。
- 同资产两份请求基于相同旧版：一份成功，另一份返回状态冲突；不同资产可同时推进。
- 重启/断线：任务状态在 PostgreSQL，可续查；SSE/页面不承担任务事实来源。
- 取消和完成竞争：登记与队列完成写入分别提交，恢复真实成功；取消后的不可判定写入明确标记不确定，既有成功历史保持真实。
- 问题选择 A/B：即使 C 更相似，C 不能进入上下文或引用；A 长而 B 短时 B 仍保留相关证据。
- 所选 B 私有、错版或缺证：不回退全库，不假称已比较全部文件。
- 模型生成时 A 换版、同头刷新切片或撤销公开访问：旧答案不得以 completed 返回。

RAG 专项位于 `tests/test_multifile_rag.py`，使用隔离数据库和确定性生成器，不消耗真实模型配额。本轮专项首轮 14/14，增加块定位变更及版本链接核查后 15/15，分别保存 `reports/multifile_rag_targeted_initial.txt` 和 `reports/multifile_rag_targeted_final.txt`；没有在本轮重跑真实模型准确率。运行：

```bash
.venv/bin/python scripts/test_isolated.py --pattern test_multifile_rag.py
```

持久队列专项位于 `tests/test_file_jobs.py`，共 20 项，覆盖全局/单批并发、同资产 CAS、混合批次部分失败、租约恢复与旧 token 拒绝、认证过期及原账户重试、取消/完成竞争、结果完整性和 API 权限。最终执行 20/20、进程退出码 0，保存为 `reports/file_jobs_targeted_final_exit0.txt`。首次 20 项断言均通过，但进程随后原生退出 134，原始记录保留为 `reports/file_jobs_targeted_initial_exit134.txt`；不能把该次进程称为通过。崩溃栈定位 ONNX Runtime 遥测 HTTP 后台线程，证据与限制见 `reports/file_jobs_native_shutdown_note.md`，没有把故障归因于 CAD 解析或改动冻结引擎。

```bash
python scripts/test_isolated.py --pattern test_file_jobs.py
```

## 本阶段最终验证（2026-10-07）

完整隔离回归 **261/261**（原 212 + 新增 49），进程退出 0；另有 HTTP/SSE/版本链接 11 项、流上传 3 项，前端异步竞态 5/5。混合 DXF/JSON 页面登记、相同输入重复提交、多文件选择与逐文件引用已验证；使用本地确定性生成器，无新增真实模型评分。尺寸页面完整三角色批准、撤销和历史回放已验证。两个预览数据库和临时素材/凭据均已清理，原数据库前后计数一致。完整报告见 `reports/next_stage_regression.txt`、`reports/next_stage_browser_verification.json`、`reports/next_stage_preview_cleanup*.json` 与 `reports/multifile_ui_races.json`。
