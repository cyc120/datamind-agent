# 私有 PDF 多文件证据检索

2026-10-07，API **0.17.0**。本阶段为已发布的私有 PDF 知识版本增加 `POST /v1/pdf-knowledge/search`，作为私有 RAG 的检索部分。前置准备、快照登记与知识发布分别见 [PDF 准备](PDF_PREPARATION.md)、[快照登记](PDF_SNAPSHOT_REGISTRY.md)、[知识发布与恢复](PDF_KNOWLEDGE_PUBLICATION.md)。本接口返回可追溯证据，不调用生成模型；`answer_generated=false`、`public_knowledge_visible=false`、`source_authority_verified=false`。

## 显式选择与来源

请求必须明确选择 1–4 个已发布 publication，每份指定 `publication_id` 和 `expected_plan_sha256`。后者来自 publication 元数据的 `plan_sha256`，绑定完整持久计划；计划内部同名字段排除自身后计算，是另一个摘要，不能替代。不得省略选择后搜索全库；重复 publication 或同一文档的多个版本拒绝。

历史 publication 可以显式检索，快照/知识当前头前进不会把旧选择换成最新版。任一选定版本未发布、摘要变化、源/页图/bundle 损坏、实际点缺失或篡改，整个请求失败，不剔除失败文件后假称全部可用。未选文档和未发布孤立点即使在同一 collection、相似度更高，也不会被读取或返回。

示例的 ID 与 SHA 仅为占位符，必须替换为已发布版本的真实元数据：

```json
{
  "question": "比较所选文件中的温度上限和管径原文",
  "publications": [
    {
      "publication_id": "pdf-pub-00000000000000000000000000000000",
      "expected_plan_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ],
  "limit": 8
}
```

问题去首尾空白后必须非空，最多 2,000 字符、8,000 UTF-8 字节。`limit` 是严格整数 4–12，默认 8；布尔值、数字字符串、未知字段和客户端模型/向量/路径/角色配置均拒绝。HTTP 只允许当前有效 admin，POST 必须有 CSRF 并同源，响应 no-store；结果编码完成后再次重查身份，读取期间撤销会话或降权时不返回私有证据。

每个 hit 保留完整 chunk 文本、文档/版本/源 SHA、解析器、页号、块 ID、块内字符偏移与 bbox，并附 publication/完整计划/profile SHA、标题、分数。`source_url`、`page_url` 固定快照版本和 manifest SHA，指向同样受 admin 保护的源文件/页图入口。

## 模型一致与检索覆盖

选定版本必须使用同一完整 embedding profile。查询使用本地 BGE 与发布时相同的文本、归一化和 float32 契约；实际模型五文件摘要、SDK 版本及完整 profile 必须匹配，否则拒绝，不把不同向量空间的分数混排。缺失缓存返回 503，不自动下载，不使用旧公开知识客户端或远端模型。合成 profile 默认不能用于生产查询，合成 embedder/client 只是 Python 内部测试注入，不是 HTTP 能力。

从已逐点核对实际 Qdrant 的冻结向量计算 Dot，范围是显式已选 chunk ID，未执行共享 collection 的 ANN 搜索。实验分数下限固定为 0.35；先为每份有过阈值证据的文件保留最高分 chunk，再按全局分数补到 limit，最后稳定排序。该阈值未做工业相关性标定，命中不证明答案正确。

响应 `coverage.documents` 保留全部选定 publication 和各自返回数量；任一文件没有过阈值命中时 `all_covered=false`，全部未命中返回 `status=empty`。覆盖只说明各文件返回了符合本次阈值的片段，不表示全文阅读、支持相同事实、消解冲突或完成模型问答。

## 多请求并发与复核

同一元数据库的全部 API 进程通过两个 PostgreSQL session advisory 槽限制检索并发；无空槽立即返回 `503/PDF_SEARCH_BUSY`。连接 autocommit，文件验证、本地模型和 Qdrant I/O 期间没有开放数据库事务。正常返回、异常和进程连接关闭均释放槽；查询/向量操作前后及退出前探测连接，slot backend 被终止后原请求返回 503。

该限制针对健康数据库会话。会话丢失不能物理取消已经进入本地原生推理的线程，因此不是 CPU/内存硬隔离或整体 deadline。检索两槽与索引 worker 两槽分别计数，尚未统一全部任务的资源预算；PDF/OCR 解析 worker 仍待接入。

单次全部文件合计最多 512 候选 chunk、20 万 UTF-8 源文本字节、16 MiB PostgreSQL JSONB 文本计划，每 publication 另有 256 KiB 非计划元数据预算。先查询轻量计数，再在取完整行的同一 SQL 快照中过滤剩余字节预算，防止 admission 后计划扩容导致先解码越界正文；实际冻结 chunk 的聚合预算再次检查。SQL 大小门禁不是 Python RSS 硬上限。

查询完成后核对全部来源、冻结计划和实际点，并再读取显式 DB/文件证据。每轮 Qdrant capture 用一次 retrieve 同时读取全部选定点（最多 512），逐 ID 比对完整 payload 和 float32 bytes；所有文件复核后再联合 capture，避免检查第二份文件时第一份已选点损坏却被漏掉。末尾一条 SQL 同时复核所有已选 publication/快照的 SHA 标签和数据库自身 JSONB 文本 SHA，未更新标签的原始 JSON 改动也会拒绝。

数据库、文件系统与 Qdrant 没有分布式读取事务；完整性证明限于本次复核窗口，可信存储操作者仍能随后修改数据，下次请求将重新核验。本接口不修复点、不写 publication/head，不 reset 或删除 collection。

## 验证与复现

profile/跨连接并发专项 **14/14**，联合向量 capture **8/8**，HTTP **32/32**，进程退出 0。前两类和 HTTP 的 embedding adapter 为明确合成/模拟配置；Qdrant capture 与 HTTP 使用实际内存存储和原生 PDF，数据库测试使用临时副本。完整隔离回归 **465/465**（原 411 + 14 + 8 + 32）、125.833 秒、失败/错误 0、退出 0，见 `reports/pdf_search_regression.txt`。

真实临时带 key Qdrant 1.12.4 和已有本地 BGE 缓存另完成一个场景：两文件、三次发布、实际查询向量、完整来源链接、v2 后 v1 响应字节不变、错误内部计划摘要/缺失缓存/错误 key 拒绝。没有下载或外部模型调用，不是生成问答或识别准确率。运行：

```bash
python scripts/test_isolated.py --pattern 'test_pdf_search*.py'
python -m unittest discover -s tests -p test_pdf_selection_vectors.py -v
python scripts/verify_pdf_search_local.py --model-cache /absolute/path/to/existing/fastembed-cache
```

真实 smoke 要求已运行的 `datamind-postgres` 和已有 `qdrant/qdrant:v1.12.4` 镜像，用 `--pull=never` 创建独立临时服务。脚本先确认临时数据库，最后清理自有账户、行、源/页图、容器和数据库；不迁移原库或升级原 8000 服务。报告为 `pdf_search_targeted_*`、`pdf_selection_vectors_final.txt`、`pdf_search_api_*`、`pdf_search_real_local_*` 和 `pdf_search_verification.json`。首次并发测试因验证脚本使用已脱敏的 `connection.dsn` 导致 4 个认证错误，原始失败保留；改为保留原连接参数、仅替换实际临时库名后通过，未放宽生产认证。

## 受控私有模型回答（API 0.18.0）

在检索之上新增 `POST /v1/pdf-knowledge/answer`，请求体与检索相同（`question`、显式 `publications` 与 `limit`）。它先执行同一条有界私有检索，再把**只有本次返回的私有片段**交给生成模型，并复用文档 RAG 的校验：每条事实必须逐字引用所引用私有 chunk 的连续片段，且事实中的数字必须出现在该片段或标题中；未知 chunk、片段不匹配、数字无支持都被拒绝，最多允许一次修复。任一所选文件没有过阈值命中时整体拒答，不用其余文件拼凑答案。

隐私与身份：仅 admin 且需 CSRF/同源，响应 no-store；生成结束后再次重查当前身份，并复核选定 publication 的源/计划/结果摘要与全部 Qdrant 实际点，响应标记 `evidence_integrity_rechecked=true`。不调用公开 RAG、不读公开知识、不做当前头替换、不写向量。`answer_generated=true`、`public_knowledge_visible=false`、`source_authority_verified=false`。

检索槽在模型调用前释放，因此生成期间不持有私有检索并发槽；生成后的复核查关闭该窗口。组件与契约报告见 `reports/pdf_answer_verification.json`（15 项新增测试、完整隔离回归 480/480）。

下一阶段为 PDF/OCR 解析的统一资源预算与进程时限，再补已发布存储修复、孤立回收与授权真实输入验收。当前没有新增上传页面；`answer_generated=true` 只表示本次由受控私有证据生成，不是问答准确率或检索相关性成绩。
