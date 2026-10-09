# 上传 → 解析 → 入库 → 查询 管道接口契约（冻结）

本文件是 `/v1/uploads` 系列接口的**唯一规范来源**，供前端与后端并行实现时对齐。
实现位于 `ingestion/upload_pipeline.py`（管道与队列）、`datamind/file_uploads.py`
（暂存）、`datamind/api.py`（路由与权限）、`ingestion/contract_store.py`（统一契约存储）。

约定：所有响应均为 JSON；除 `/v1/documents*`、`/v1/runs*` 之外，`/v1/uploads*` 全部
位于**默认拒绝**中间件之内（`datamind/api.py: _required_permission` 把 `/v1/uploads`
按 `files_path` 同类处理）。

---

## 0. 权限、CSRF 与同源

| 方法 | 路径 | 身份 | 权限 | CSRF | 同源 |
| --- | --- | --- | --- | --- | --- |
| POST | `/v1/uploads` | 必须已登录 | `admin` | 必须 | 必须 |
| GET | `/v1/uploads` | 必须已登录 | `admin` | — | — |
| GET | `/v1/uploads/{upload_id}` | 必须已登录 | `admin` | — | — |
| POST | `/v1/uploads/{upload_id}/approve` | 必须已登录 | `admin` | 必须 | 必须 |

- 未登录 → `401 {"detail":{"error_code":"LOGIN_REQUIRED"}}`；会话失效 → 401
  `SESSION_INVALID`（均沿用既有中间件 `datamind/auth.py`）。
- 已登录但非 admin → `403 {"detail":{"error_code":"PERMISSION_DENIED"}}`。
- 变异请求缺少 CSRF 头 → `403 CSRF_REQUIRED`；跨源 → `403 ORIGIN_NOT_ALLOWED`。
- 响应头：`Cache-Control: no-store`（`/v1/uploads` 已加入 `_NO_STORE_PREFIXES`）。

---

## 1. `POST /v1/uploads` —— 一次调用完成「暂存 + 入队」

```
POST /v1/uploads?filename=<name>&provenance=<synthetic_demo|authorized_external>
Content-Type: application/octet-stream
<原始字节>
```

- `filename`：查询参数，1–200 字符，必须是**纯文件名**（无路径分隔符、无 `\`、无控制字符）。
- `provenance`：`synthetic_demo` 或 `authorized_external`；决定落盘目录
  （`development` / `authorized`）。非这两个值 → FastAPI 422 校验错误。
- 请求体：原始字节，**不做 multipart**，不做内容嗅探前的解码。

### 接受类型与上限

| 后缀 | media_type | 上限 |
| --- | --- | --- |
| `.dxf` | `image/vnd.dxf` | 8 MiB |
| `.json` | `application/json` | 1 MiB |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | 16 MiB |
| `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | 16 MiB |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | 16 MiB |
| `.pdf` | `application/pdf` | 32 MiB |

`.dwg` 属于**已知但不可处理**类型：本仓库没有 DWG 转换器。

### 成功：201

```json
{
  "upload_id": "upl-<40 hex>",
  "job_id": "job-<40 hex>",
  "filename": "dev-mm.dxf",
  "media_type": "image/vnd.dxf",
  "status": "queued",
  "review_required": true
}
```

- `upload_id` / `job_id` / `document_id` 都是**内容寻址**：由暂存字节的 SHA-256 派生。
  同一份字节重复上传是**幂等**的——返回同一 `upload_id`，队列被重新置为 `queued`，
  复核门重新打开（`needs_review=true`、`status='parsing'`）。不同文件名/来源的同一份
  字节仍映射到同一条记录。
- `review_required` 恒为 `true`：解析成功也**默认不可检索**，必须显式 approve；且 approve
  只是必要条件——是否可检索由 §2 的 `searchable` 派生判定给出。

### 失败：稳定错误码

| 触发 | HTTP | `detail.error_code` | 额外字段 |
| --- | --- | --- | --- |
| 文件名为非法（路径、反斜杠、控制字符、超长、空） | 422 | `FILE_TYPE_OR_NAME_INVALID` | — |
| 扩展名未支持（未知后缀、无后缀） | 415 | `UNSUPPORTED_MEDIA_TYPE` | `details.suffix` |
| `.dwg` | 415 | `CAD_DWG_CONVERTER_UNAVAILABLE` | **无**（响应体恰为 `{"detail":{"error_code":"CAD_DWG_CONVERTER_UNAVAILABLE"}}`） |
| 空 body | 422 | `UPLOAD_EMPTY` | — |
| 超过上限（`Content-Length` 或流式累计） | 413 | `UPLOAD_TOO_LARGE` | — |
| `Content-Length` 不是整数 | 422 | `UPLOAD_LENGTH_INVALID` | — |

> **旧接口同族错误码的归属（务必区分）**
>
> 这些码**只属于**旧接口 `POST /v1/files/uploads`（`datamind/file_uploads.py: stage`），
> 且其上限仍是 `.dxf` 2 MiB / `.json` 1 MiB：
>
> - `FILE_UPLOAD_TOO_LARGE_OR_EMPTY`（413）——旧接口的「超限或空」
> - `FILE_UPLOAD_INVALID_LENGTH`（422）——旧接口的 `Content-Length` 非整数
> - `FILE_TYPE_OR_NAME_INVALID`（422）——**两个接口共用**（含义相同：文件名/后缀非法）
>
> 新接口 `/v1/uploads` **不会**返回 `FILE_UPLOAD_TOO_LARGE_OR_EMPTY` 或
> `FILE_UPLOAD_INVALID_LENGTH`。前端按新接口实现时只需处理上表。

---

## 2. `GET /v1/uploads` —— 列表（**只读，不推进任何解析**）

```
GET /v1/uploads?status=&media_type=&since=&limit=
```

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `status` | string | 不过滤 | 必须属于 `status` 枚举，否则 422 `UPLOAD_FILTER_INVALID`（`details.filter="status"`） |
| `media_type` | string | 不过滤 | 精确匹配 `media_type` |
| `since` | string | 不过滤 | epoch 秒（如 `1710000000`）或 ISO-8601（如 `2026-10-08T00:00:00Z`）；非法 → 422 `UPLOAD_FILTER_INVALID`（`details.filter="since"`） |
| `limit` | int | `50` | 范围 1–200 |

- 排序：`created_at DESC`（最新在前）。
- `count` = **本页返回条数**（不是匹配总数，也不受分页偏移影响；本接口无 offset）。
- 时间格式：`created_at` 为带时区的 ISO-8601（服务器本地时区，如
  `2026-10-08T20:20:54.793442+08:00`）。做「最近 N 分钟」过滤请用 epoch 秒或带时区的
  ISO 字符串，不要用无时区的本地时间字符串。
- **列表不驱动解析**：只有详情轮询（或外部 worker 调用 `drain()`）会推进 job。

响应：

```json
{
  "uploads": [
    {
      "upload_id": "upl-...",
      "job_id": "job-...",
      "document_id": "doc-upload-...",
      "filename": "demo-diameter.json",
      "media_type": "application/json",
      "provenance": "synthetic_demo",
      "status": "partial_failure",
      "created_at": "2026-10-08T20:20:54.793442+08:00",
      "review_state": "pending_review",
      "review_required": true,
      "searchable": false,
      "index_state": "not_approved",
      "index_note": "UPLOAD_NOT_APPROVED",
      "block_count": 0,
      "locator_count": 0,
      "unsupported_features": ["locator_unavailable"]
    }
  ],
  "count": 1
}
```

### `status` 枚举（队列语义，与既有 `file_jobs` 一致）

`queued` | `running` | `succeeded` | `failed` | `partial_failure` | `cancelled`

映射规则（`parse_status` → `status`）：

| `parse_status` | `status` |
| --- | --- |
| `parsed` | `succeeded` |
| `partial` | `partial_failure` |
| `unsupported`（显式声明缺口，如 measurement JSON 无块级定位） | `partial_failure` |
| `failed` | `failed` |

### `review_state` 枚举（**只有两个值**）

- `pending_review`：`needs_review=true`（未复核，不可检索）。
- `approved`：`needs_review=false`（已 approve；若契约确有块，`status` 同时置 `indexed`）。

`review_required` 是布尔镜像（= `needs_review`），与 `review_state` 同时出现在列表项与
详情中。**没有** `not_applicable` / `rejected` 取值；失败的上传仍为 `pending_review`。

### 可检索性 `searchable` / `index_state` / `index_note`（R05，本版新增）

「解析完成」「人工复核」「检索就绪」是**三件独立的事**，本管道此前把它们混为一个
`status='indexed'`：审批只要求契约有块，而检索器（`datamind/knowledge.py`）**只读
`chunks`**，本管道又不写 `chunks`（`vector_index` 恒为 `deferred`）。结果是一份审批后的
上传会出现在问答文件选择器里，却永远检索不到。

从本版起，**可检索是派生判定，不是存储的状态**。列表项、详情与 approve 响应统一新增：

```json
{
  "searchable": false,
  "index_state": "no_chunks",
  "index_note": "UPLOAD_HAS_NO_CHUNKS"
}
```

- `searchable`（bool，**派生**）：
  `searchable == (asset.status == 'indexed') AND NOT needs_review AND chunk_count > 0`。
  消费方（问答文件选择器）必须要求 `searchable=true`；它是唯一可以据此宣称「该文件可
  用于问答检索」的字段。
- `index_state`（**恰好三个值**，`index_state=="indexed"` 当且仅当 `searchable=true`）：

  | 值 | 含义 |
  | --- | --- |
  | `indexed` | 已复核、已发布、且 `chunks` 非空——真正可检索 |
  | `no_chunks` | 已复核，但检索器要读的切片存储为空（本管道审批后的正常状态） |
  | `not_approved` | 复核门未打开（`needs_review=true`），无论切片是否存在 |

- `index_note`（str \| null）：机器可读的**原因码**，人类可读文案由消费方映射，**不要
  直接展示原文**。取值集合：

  | `index_note` | 出现条件 |
  | --- | --- |
  | `null` | `searchable=true` |
  | `UPLOAD_NOT_APPROVED` | `index_state='not_approved'` |
  | `UPLOAD_HAS_NO_CHUNKS` | 已复核、契约有块（`block_count>0`）、但 `chunks=0` |
  | `UPLOAD_HAS_NO_CONTRACT_BLOCKS` | 已复核、契约也无块（`block_count=0`）、`chunks=0` |
  | `UPLOAD_STATUS_NOT_INDEXED` | `chunks>0` 但资产未被标记 `indexed`（只可能由人工改库产生） |

- 这三个字段在 `/v1/uploads` 列表项、`/v1/uploads/{upload_id}` 详情、approve 响应与
  `GET /v1/documents` 的每一项中**含义完全一致**：四个端点全部调用**同一个**判定函数
  `ingestion.upload_pipeline.searchability(status, needs_review, chunks, block_count=...)`，
  不各自写一份条件（守护测试：
  `test_upload_searchability.py::test_documents_and_upload_detail_agree_on_the_index_state`
  —— 同一文档在两个端点上必须得到相同的 `index_state`/`index_note`）。它们**都不改变**
  既有字段（`status`/`review_state`/`review_required`/`vector_index`/计数）的含义。
- **诚实**：`vector_index` 仍为 `"deferred"`——本管道确实不建向量，也不写 `chunks`。
  `searchable` 不会因此撒谎：将来某条路径真的写入切片并标记 `indexed` 后，同一判定会**自动**
  变为 `true`（无需再加状态字段），`tests/test_upload_searchability.py` 的反向对照正是这么
  断言的。

### `GET /v1/documents` 的 `searchable` / `index_state` / `index_note`

问答文件选择器的数据源 `GET /v1/documents` 的**每一项也新增这三个字段**（同一派生定义、
同一批 code、同一函数）。该路由的发布过滤条件（`status='indexed'` + `public` + 非待复核）
**本版未改**：历史遗留的「`indexed` 但 `chunks=0`」行仍会出现，但带
`searchable=false` + `index_state="no_chunks"` + `index_note`（如
`UPLOAD_HAS_NO_CHUNKS` / `UPLOAD_HAS_NO_CONTRACT_BLOCKS`），于是选择器既能排除它，也能对
用户说明原因，而不是只给一句通用文案。消费方必须显式要求 `searchable=true`，否则会重现
R05 的症状。

---

## 3. `GET /v1/uploads/{upload_id}` —— 详情（**轮询即推进**）

该请求先为这**一个**上传推进**一波有界解析**（见 §6），再返回当前真实状态。

- `upload_id` 必须匹配 `^upl-[0-9a-f]{40}$`，否则 404 `UPLOAD_NOT_FOUND`。
- 不存在 → 404 `UPLOAD_NOT_FOUND`。

响应 = 列表项的全部字段，另加：

```json
{
  "source_sha256": "<64 hex>",
  "parser_version": "cad-contract-adapter-v1 或 null",
  "page_count": null,
  "collection": "demo_kb",
  "analysis": {
    "parse_status": "parsed",
    "adapter": "dxf-native",
    "blocks": 8,
    "locators_written": 8,
    "relations": 1,
    "block_types": {"title": 3, "figure": 3, "dimension": 1, "paragraph": 1},
    "unsupported_features": [],
    "quality_findings": ["CAD_TEXT_BOUNDS_APPROXIMATE"],
    "contract_version": "parse-contract-v1",
    "layers": 5,
    "layer_names": ["GEOMETRY", "..."],
    "entities": 7,
    "dimensions": 1
  },
  "error_code": null,
  "vector_index": "deferred",
  "vector_index_note": "上传管道只落元数据与契约证据，不写 chunks/Qdrant 行；向量索引由离线路径完成（scripts/pdf_index_worker.py、ingestion/ingest.py）。",
  "wave_deadline_seconds": 45.0,
  "wave_timeouts": 0,
  "locator_stores": {"contract_blocks": 8, "page_shaped": 0},
  "contract_url": "/v1/documents/doc-upload-.../contract",
  "approval": null,
  "started_at": "2026-10-08T20:20:54.5+08:00",
  "finished_at": "2026-10-08T20:20:54.9+08:00"
}
```

详情同样带列表项里的 `searchable` / `index_state` / `index_note`（定义见 §2，与 approve
响应逐字段一致）。

### `analysis` 字段

- `parse_status`：**直接来自统一解析契约的 `ParseResult.parse_status`**，取值
  `parsed|partial|needs_review|failed|unsupported`（`datamind/parse_contract.py`）。
  它**不是** `status`：`status` 是队列终态，`parse_status` 是解析器对本次解析完整性的
  自述。
- `adapter`：产生该结果的适配器名。取值来源优先级：`ParseResult.manifest.adapter`，
  否则按后缀取
  `dxf-native|measurement-json|pdf-native|docx-native|pptx-native|xlsx-native`。
- `blocks`：写入统一契约存储（`document_contract_blocks`）的块数。
- `locators_written`：**已验证定位**的块数（当前等于 `blocks`，因为契约要求每个块都带
  合法 locator）。
- 其余为可用计数：DXF 给 `layers/layer_names/entities/dimensions`；PDF 给 `pages`；
  Office/measurement 只有 `block_types`。

### `locator_count` 的口径（与 `/v1/documents/{id}/contract` 的差异，必须知悉）

- 上传详情 / 列表的 `locator_count` = `document_contract_blocks` 行数 +
  `document_block_locators` 行数，并额外给出 `locator_stores` 明细。
- 既有 `GET /v1/documents/{id}/contract` 的 `locator_count` **只数分页型
  `document_block_locators`**（它是为 PDF 分页定位设计的历史口径），Office/DXF 的
  locator 存在 `blocks[].locator` 里，所以 DXF 会出现「`locator_count=0` 但
  `blocks[].locator` 齐全」。
- **为消除这个误导，`/contract` 自本版起同时返回三种口径**（`ingestion/contract_store.py`
  的 `read_result`）：`locator_count`（分页型）、`block_locator_count`（块内嵌）、
  `locator_total`（两者之和）。消费方**读 `locator_total` 或 `block_locator_count`**
  即可，不要再从单一的 `locator_count` 推断"没有定位"。
  守护测试：`tests/test_contract_store.py::LocatorCountSemanticsTests`。

### `/contract` 的可见性与分页（本版新增，必须知悉）

**可见性**（`datamind/api.py` 的 `_published_document` + `_contract_visible`，与发布路径共用同一判定）：

| 文档状态 | 匿名 | viewer | 已认证 admin |
|---|---|---|---|
| 已发布（`indexed` + `public` + 非待复核） | **可读** | 可读 | 可读 |
| 待复核上传（`origin='upload'`，未 `indexed`） | **404** | **404** | **可读**（复核流程依赖） |
| 未解析 / 取消 / 失败 | 404 | 404 | 404 |

- 非 admin 的 404 响应体与"该 id 不存在"**逐字节相同**，因此不泄露存在性；
- 待复核分支的响应带 `Cache-Control: no-store`，已发布分支不带；
- 该路由位于公开读前缀，因此**身份在路由内解析**（`current(..., required=False, provision_demo=False)`），身份解析用写 DSN、且 GET 不写库。
- 守护测试：`tests/test_contract_visibility.py`。

**分页与截断诚实性**（`read_result` 三存储统一 `LIMIT/OFFSET` + 稳定排序）：

- 旧的扁平键（`block_count`/`locator_count`/`block_locator_count`/`locator_total`/`relation_count`）**含义不变，仍是"本页数量"**；
- 新增 `totals`（库内总数）、`counts`（本页数量）、`page`（契约块的窗口：`limit/offset/total/returned/truncated/has_more`）、`store_pages`（三存储各自的窗口）；
- 查询参数 `limit`（默认 2000，1..5000）与 `offset`（0..1000000）；非法值返回 400 `CONTRACT_PAGE_LIMIT_INVALID` / `CONTRACT_PAGE_OFFSET_INVALID`，**不静默钳制**；
- 关系查询与块查询受同一显式上限约束，`store_pages.relations.truncated` 可让"关系可能指向本页之外的块"这件事**显式可查**。
- 守护测试：`tests/test_contract_store.py::ContractPaginationHonestyTests`。

### `vector_index`

- 取值恒为字符串 `"deferred"`：**上传管道不做向量索引**，不写 `chunks`，不写 Qdrant。
- `vector_index_note` 是**规范字段**（说明离线路径）。当前值见上方示例。
- 离线向量化路径：`scripts/pdf_index_worker.py`、`ingestion/ingest.py`（PDF 资产离线
  publish）。前端不得把 `status='indexed'` 解读为「已向量化」；要判断「能否用于问答」
  只看 §2 派生的 `searchable`。
- 一旦某条路径真的写入 `chunks` 并把资产标为 `indexed`，上述派生判定会**自动**变为
  `searchable=true`——本字段无需迁移、无需回填，也不会因历史数据而撒谎。

### 失败的 `error_code`

`error_code` 只在 `status='failed'` 时非空，取解析器/管道抛出的稳定码，例如：

| 场景 | `error_code` |
| --- | --- |
| 内容嗅探不出已注册适配器（如伪装的 `.dxf`） | `PARSE_ADAPTER_UNKNOWN` |
| DWG 内容出现在 `.dxf` 文件中 | `CAD_DWG_CONVERTER_UNAVAILABLE` |
| 暂存路径越界/后缀不符 | `UPLOAD_STAGED_SOURCE_INVALID` |
| 暂存字节被改动（SHA 不符） | `UPLOAD_STAGED_SOURCE_CHANGED` |
| measurement JSON 结构非法 | `UPLOAD_MEASUREMENT_SOURCE_INVALID` 或 `DIMENSION_SOURCE_INVALID` |
| 解析抛未知异常 | `UPLOAD_PARSE_FAILED` |
| 资产行丢失 | `UPLOAD_ASSET_MISSING` |
| 连续 `MAX_WAVE_ATTEMPTS` 波都用尽预算 | `UPLOAD_WAVE_ATTEMPTS_EXHAUSTED` |

失败时 `document_assets.status='failed'`、`needs_review=true`，不会出现在
`/v1/documents`；approve 会被 409 拒绝。

---

## 4. `POST /v1/uploads/{upload_id}/approve` —— 最小复核动作

- 请求体：无（可选 JSON `{"reason": "..."}`，当前实现忽略 body）。
- 权限：admin + CSRF + 同源。
- 成功：200，响应体 = **详情对象**（§3）的完整字段，另加 `indexed`（bool），并始终带
  `searchable` / `index_state` / `index_note`。**`indexed == searchable`**，仅为 true 时
  才是真的可检索。关键字段示例（本管道不写 `chunks`，所以审批后的正常结果是**不可检索**）：

```json
{
  "upload_id": "upl-b2408306ce5fae7c07a62f5a733bc986cc2f20f1",
  "status": "succeeded",
  "review_state": "approved",
  "review_required": false,
  "indexed": false,
  "searchable": false,
  "index_state": "no_chunks",
  "index_note": "UPLOAD_HAS_NO_CHUNKS",
  "block_count": 8,
  "vector_index": "deferred",
  "approval": {
    "user_id": "user-...",
    "display_name": "本机管理员",
    "roles": ["admin"],
    "approved_at": "2026-10-08T12:20:55.665966+00:00",
    "reason": null,
    "indexed": false,
    "block_count": 8,
    "chunk_count": 0
  }
}
```

行为：

1. 记录操作者到 `document_assets.quality_detail.review_approval`（含 `user_id`、
   `display_name`、`roles`、`approved_at`、`block_count`、`chunk_count`、`indexed`）。
2. `needs_review := false`（复核门打开；这是 approve 唯一无条件做的事）。
3. **当且仅当**检索器真正读取的切片存储非空（`chunks` 行数 > 0）时 `status := 'indexed'`；
   否则 `status` **保持原值**（解析成功即 `parsed`），并返回 `indexed=false` +
   `searchable=false` + `index_state="no_chunks"` + `index_note`（见 §2 的取值表；契约有块
   时为 `UPLOAD_HAS_NO_CHUNKS`）。**契约块数 > 0 不再等于 `indexed`**——块是证据，不是索引。
   因此本管道的审批结果**不会**出现在 `GET /v1/documents`（它按 `status='indexed'` 过滤），
   也就不会出现在问答文件选择器里。
4. **幂等**：重复 approve 只是重新记录一次操作者与时间，仍返回 200，`needs_review` 保持
   false，不产生第二次状态变更、不新建 job。已 `indexed` 的资产不会被后续慢 worker 降级
   （`_finish` 带 `status<>'indexed'` 保护）。若两次调用之间确有 `chunks` 写入，第二次
   approve 会如实变为 `indexed=true`（判定是派生的，不是缓存的）。

失败：

| 触发 | HTTP | `error_code` |
| --- | --- | --- |
| 非 admin / 缺 CSRF / 跨源 | 403 | 中间件既有码 |
| `status` 不是 `succeeded`/`partial_failure`（仍在队列或已失败） | 409 | `UPLOAD_NOT_REVIEWABLE`（`details.job_status`） |
| 未知 upload_id | 404 | `UPLOAD_NOT_FOUND` |

---

## 5. 复核门（可见性规则，fail-closed）

`document_assets` 行的可见性门是**两把锁同时打开**：`status='indexed'`
**且** `needs_review=false`，且 `'public'=ANY(acl)`、`collection` 属于
`{demo_kb, ohrbench_kb, industrial_demo}`。**但可见 ≠ 可检索**：`status='indexed'`
只说明它通过了发布门，检索器仍要求 `chunks`（见 §2 的 `searchable` 定义）。

- 未 approve 时：`needs_review=true` 且 `status` ∈ {`parsing`（队列中）, `parsed`
  （解析成功）, `failed`}。因此
  - `GET /v1/documents` **不列出**它；
  - `GET /v1/documents/{id}` 返回 404 `DOCUMENT_NOT_FOUND`；
  - 向量检索（RAG）选不中它（无 chunks，且 gate 为 indexed）。
- approve 后：`needs_review=false`；`status='indexed'` **仅当** `chunks>0`。本管道不写
  `chunks`，所以审批后的上传**仍然不可检索**、**不出现在** `GET /v1/documents`
  （`index_state='no_chunks'`），这正是 R05 的修复点。
- 只有真正存在切片（例如离线路径写完 `chunks` 并把资产标为 `indexed`）后，`searchable`
  才为 true、文档才出现在 `/v1/documents` 并被检索器选中。
- **唯一例外**：`GET /v1/documents/{id}/contract` 对 `origin='upload'` 的行在
  approve **之前**也可读（契约是证据浏览，不是检索；上传详情的 `contract_url` 指向
  它）。其它 `/v1/documents*` 路由的门保持不变。
- 守护测试：`tests/test_upload_searchability.py`（①未审批 ②已审批无切片 ③有切片反向对照
  ④`/v1/documents` 每项带 `searchable` ⑤幂等 ⑥失败解析不可审批）。

---

## 6. 异步与「轮询推进」语义（有界执行）

**job 只在被轮询时推进。** 具体地：

1. `POST /v1/uploads` 只做：校验 → 暂存 → 写 `document_assets`（`status='parsing'`,
   `needs_review=true`）→ 写 `ingest_jobs`（`job_type='upload_parse'`, `status='queued'`）。
   **不解析**。
2. `GET /v1/uploads/{upload_id}` 为该上传推进**恰好一波**有界解析：每一波都运行在
   **独立子进程**中（`datamind.task_budget.run_bounded`，模块
   `ingestion.upload_pipeline`，函数 `run_parse`），带硬时限
   `wave_deadline()`。默认 **45 秒**，可用环境变量
   `DATAMIND_UPLOAD_WAVE_DEADLINE_SECONDS` 覆盖；非法值回退默认值（**不会**变成无界）。
   响应里的 `wave_deadline_seconds` 就是本次预算。
3. 若这一波用尽预算：子进程被杀，job **保持 `running`**（不写成 failed、不假装成功），
   `GET` 如实返回 `status='running'`、`error_code=null`，并把
   `wave_timeouts` 计数 +1。下一次轮询在超过
   `stale_running_seconds()`（= max(120s, 波次时限+30s)）后会重新抢占并继续。
   波次超时**最多重试 `MAX_WAVE_ATTEMPTS = 3` 次**：第 3 次仍超时就把 job 置为
   `failed`、`error_code='UPLOAD_WAVE_ATTEMPTS_EXHAUSTED'`，避免一个永远放不进任何一波
   的上传被无限重试。
4. `GET /v1/uploads`（列表）**只读**，不推进任何解析。
5. **无人值守/生产路径**：调用 `ingestion.upload_pipeline.drain(dsn)`（或
   `work_once`）的 worker 循环，而不是依赖浏览器轮询。`drain` 会连续推进所有可抢占的
   job，并在下一次调用时回收陈旧 `running` 行。

并发安全：`_claim` 用 `SELECT ... FOR UPDATE SKIP LOCKED` 抢占，`running` 行带
`started_at`；终态写入以 `WHERE status='running'` 作栅栏（父进程抢不回时不覆盖）。

### 一次 GET 的最坏耗时

- 上界 ≈ `wave_deadline_seconds` + 子进程启动/回收开销。实测（默认 45s 预算）：
  DXF/DOCX/PDF/JSON 首次轮询 **0.23–0.57 s**（状态直接到终态），第二次轮询（终态）**~0.034 s**；
  强制一个 400 页 PDF 在 `wave_deadline_seconds=0.5` 下超时，GET 实测 **0.556 s** 返回
  `status='running'`（波次被杀、job 保持 running）。
- PDF 分支在**子进程内**可能触发 OCR 与公式识别（`ingestion/ingest.py: parse_pdf`），
  它们没有各自的子预算，但整段都在上述波次时限内，超时即整波被杀——因此一次 GET 不会
  超过波次时限太多。
- 一个永久超时上传的最坏总耗时（默认参数）≈ `MAX_WAVE_ATTEMPTS × (wave_deadline +
  stale_running)` ≈ 3 × (45s + 120s) ≈ 8 分钟，之后稳定失败为
  `UPLOAD_WAVE_ATTEMPTS_EXHAUSTED`。

### `run_parse` 不会做的事

- 不调用 embedding 模型，不写 `chunks`/Qdrant（`vector_index` 恒为 `deferred`）。
- 不在 API 进程内解析：`work_once`/`drain` 默认走子进程；`in_process=True` 是**显式**
  选项（测试与已自带一次性进程的 worker 使用），默认不可达。

---

## 7. 落库与契约

解析成功后（`run_parse`）在**同一个数据库事务**内：

1. `ingestion.contract_store.persist_result(cur, document_id=..., payload=..., dsn=...)`
   写入 `document_contract_blocks` / `document_block_relations`（先删后插，幂等替换）。
   只有通过 `datamind/parse_contract.py` 校验的结果才会落库；校验失败即抛稳定错误码，
   不写半份文档。
2. 更新 `document_assets.quality_detail`，形状为
   `{"upload": {...}, "parse_contract": {...}, "analysis": {...}, "review_approval": {...}}`。
   其中 `parse_contract` 是 `/v1/documents/{id}/contract` 会回显的摘要，含
   `unsupported_features`（显式缺口如实声明，如 measurement JSON 的
   `locator_unavailable`）。

`quality_detail` 中**不写任何明文凭据**；上传记录只含 `user_id`/`display_name`/`roles`
与文件身份，不含 session 哈希。

---

## 8. 与既有接口的关系

| 既有接口 | 与本管道的关系 |
| --- | --- |
| `POST /v1/files/uploads` | 旧暂存路径，上限 `.dxf` 2 MiB / `.json` 1 MiB，错误码见 §1 注记。本管道**未改动**它。 |
| `POST /v1/files/jobs` + `scripts/file_job_worker.py` | DXF/measurement 的**登记**队列（`cad_versions` / `dimension_measurement_versions`）。上传队列不写这两张表（原因见 `ingestion/upload_pipeline.py` 模块文档：`file_job_items.kind` 有 CHECK 约束，且两个 target 都不写统一契约与 `document_assets`）。 |
| `GET /v1/documents/{id}/contract` | 上传的契约读取入口；`contract_url` 指向它。 |
| `GET /v1/documents` | 问答文件选择器的数据源；只有 `indexed` 且 `needs_review=false` 才列出，且每项带 `searchable` / `index_state` / `index_note`（与上传端点同一判定函数）。选择器必须要求 `searchable=true`。 |
