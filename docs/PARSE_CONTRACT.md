# 统一解析契约：ParseResult / SourceLocator / Block / Relation

2026-10-07，API **0.25.0**。本阶段实现技术设计说明书 **§5.2**（Parser Adapter 与解析状态）与 **§5.4**（Block、Relation、Chunk 与来源定位）记载的目标契约。

契约位于 `datamind/parse_contract.py`；API 0.26.0 起 `ingestion/ingest.py` 的 `parse_pdf` **实际产出并校验**该契约（结果写入质量明细的 `parse_contract` 字段）。模块本身**只描述并校验契约**：不读文件、不调用模型、不接管摄取路由；缺失信息一律报错，**不用默认值或猜测补造**。

两个目标字段在本版**未纳入契约**：`Chunk` 与 `DocumentFact`/`DrawingFeature`。原因见「边界」——现有 `chunks`/`document_facts` 表尚未统一到本契约，先冻结 Block/Relation/Locator 三个边界，避免一次改动同时触碰存储与检索。

## 状态与阶段

| 集合 | 取值 |
|---|---|
| `parse_status` | `parsed`、`partial`、`needs_review`、`failed`、`unsupported` |
| 摄取状态 | `queued → classifying → parsing → validating → indexing → ready`，任一步可转 `failed` |
| `parsed_scope` / `failed_scope` 记录 | `kind ∈ {page, region, object}`，`status ∈ {parsed, failed, gap, unsupported}` |

`ingest_step(state, target)` 只放行文档中的相邻迁移；`queued → ready` 这类跳步抛 `PARSE_STATUS_INVALID`。

## SourceLocator（§5.4）

七类定位器，各自的必需字段按文档固定；**未知键一律拒绝**，防止把未校验的坐标空间塞进引用。

| `locator_kind` | 必需字段要点 |
|---|---|
| `pdf_region` | `page_no ≥ 1`、`page_width/page_height`、`bbox` 在页内、`unit`、`coordinate_system`、`rotation_degrees` |
| `image_region` | `image_id`、图像尺寸、`bbox` 在内、`unit`、`coordinate_system` |
| `office_object` | `part` 加 `paragraph`/`table`/`slide`/`shape` 之一 |
| `sheet_range` | `sheet` 加 `cell`（A1 形式）或 `range` |
| `cad_entity` | `layout`、`handle`（十六进制）、`layer`、`model_xy`、`unit` |

## 写入存储

`ingestion/ingest.py` 在重建某文档的块时：先删除该文档的 `document_blocks` 与 `document_block_locators`，再按 `BLOCK_INSERT_SQL`（13 列，历史形状不变）写入块，最后对**有校验定位的块**执行 `LOCATOR_INSERT_SQL` 写入 `(block_id, document_id, contract_version, locator)`。缺少定位的块不写行。
| `temporal_segment` | `start < end`、`time_unit`、`time_base` |
| `model_spatial_object` | `object_id`、`coordinate_reference`、`unit` |

## Block 与 Relation

- 块必需字段：`block_id`、`document_id`、`source_version`、`parser_version`、`block_type`、`text`、`native_attributes`、`reading_order`、`locator`、`quality`、`content_hash`（SHA-256）。
- 块的 locator 必须与其 `document_id` 一致，否则 `PARSE_BLOCK_INVALID`。
- 关系必需字段：`source_block_id`、`target_block_id`、`relation_type`、`evidence`、`recovery_method`、`state`；类型限于 `precedes/contains/caption_of/header_of/continuation_of/annotates/connected_to/flows_to/references`，状态限于 `candidate/verified/needs_review`。
- 两端必须在**同一结果内存在**；`evidence` 必须非空。
- `name_similarity`、`proximity`、`adjacency`、`guess` 作为 `recovery_method` 一律拒绝——名称相近或位置相邻不足以确认关系。

## 媒体类型探测与 Adapter 路由（API 0.27.0）

- `detect_media_type(path)` 按**文件签名**判断类型（PDF、PNG、JPEG、ZIP），未知内容返回 `application/octet-stream`，不可读则 `PARSE_MEDIA_UNREADABLE`。**扩展名只作声明，从不作为答案**；`application/zip` 无法区分 DOCX/XLSX/PPTX，这是刻意的保守结论。
- `register_adapter(name, media_types=…, capabilities=…, handler=…)`：`handler(**kwargs)` 负责真正的映射并返回 `ParseResult` 载荷；不给 handler 时仍可声明能力，但**分发会被拒绝**。
- `dispatch(media_type, **kwargs)`：按探测到的类型找到适配器，调用其 handler，并对返回值执行 `validate_parse_result`。未登记类型 → `PARSE_ADAPTER_UNKNOWN`；无 handler → `PARSE_ADAPTER_UNSUPPORTED`；handler 签名不符 → `PARSE_RESULT_INVALID`；handler 抛其它异常 → `PARSE_ADAPTER_UNSUPPORTED`；handler 主动抛 `HTTPException` 则原样透传其错误码。

`ingestion/ingest.py` 注册了 `pdf-native`（capabilities：`native_text`/`pixel_ocr`/`tables`/`formula`），`parse_pdf` 经 `dispatch` 走这条路由，因此质量明细里会出现 `adapter: pdf-native`——这是路由**真的被走到**的证据，而不只是登记表里有一行。

## Adapter 能力登记

`register_adapter(name, media_types=…, capabilities=…)` 登记进程内契约（**不是生产路由表**）；同一 media type 不能重复登记。`adapter_for(media_type)` 对未登记类型抛 `PARSE_ADAPTER_UNKNOWN`，**绝不返回「空解析成功」**。`require_capability` 区分「适配器未知」（`PARSE_ADAPTER_UNKNOWN`）与「能力不支持」（`PARSE_ADAPTER_UNSUPPORTED`）。

## PDF 块映射

`from_pdf_blocks(blocks, *, page_sizes, parser_version, document_id, source_version, …)` 把 `ingestion/ingest.py` 与 `ingestion/ocr.py` 实际产出的块字典映射进契约：

- 只搬运解析器**真正产出**的键（`page_no`、`bbox`、`block_type`、`text`、`heading_level`、`table_cells`、`formula_latex`、`reading_order`、`ocr_confidence`、`content_hash`）。
- 每个块生成一个 `pdf_region` locator；页尺寸缺失、页号非法或 bbox 越界即拒绝，**不猜页尺寸**。
- `text` 缺失按空串保留（原文确实没有文本），`heading_level`/`ocr_confidence` 缺失保持 `None`——不推断。

## 验证

`tests/test_parse_contract.py` **25/25**，完整隔离回归见 `reports/parse_contract_verification.json`。覆盖：常量与文档一致、摄取状态机放行/拒绝、七类定位器逐一校验与必需字段缺失、页号/bbox 越界与倒置、CAD 句柄、关系端点/类型/状态/证据/恢复方法、结果必需字段与未知字段拒绝、适配器登记/查询/未知与不支持区分，以及用真实 ingest/OCR 块形状做映射往返（字段不丢、缺失保持缺失、页尺寸缺失拒绝）。

## 检索侧消费定位（API 0.32.0）

`datamind/rag_engine.py` 的引用现在携带**已验证**的定位：`citation_from_hit(hit, locators=…)` 只附上确实存在于存储中的块定位（`locators: [{block_id, locator, contract_version}]`），缺失即不附——**不从页码或 bbox 推导区域**。

`ask_documents(..., locator_lookup=…)` 在检索后**一次性**查询全部命中块的定位（去重排序，只传真实 id）；`datamind/agent.py` 注入基于只读 DSN 的实现。查询失败**不阻断回答**：错误码写入 `locator_lookup_error`，引用保持无定位。未注入 lookup 时行为与之前完全一致。

## 契约存储与读接口（API 0.31.0）

| 表 | 用途 |
|---|---|
| `document_contract_blocks` | 非页面形状的契约块（Office/DXF）：`block_id`、`contract_version`、`block_type`、`text`、`reading_order`、`native_attributes`、`locator`、`content_hash` |
| `document_block_relations` | 关系：`relation_type`、`relation_state`、`recovery_method`、`evidence`；`evidence` 非空与禁止自引用由 CHECK 兜底 |
| `document_block_locators` | PDF 路径的 locator（块同时存在于 `document_blocks`） |

Office/DXF 的块按 `part`/`paragraph`/`slide`/`sheet`/`handle` 定位，**没有页概念**，因此不写入页面形状的 `document_blocks`（那需要编造页码）；PDF 路径继续用 `document_blocks` + `document_block_locators`。

`ingestion/contract_store.py`：`persist_result(cur, document_id=…, payload=…)` 在写库前**再次** `validate_parse_result`，然后按文档整体替换块与关系——重复导入不产生重复行，半成品不会残留。`relation_id` 由 `(document_id, 源, 目标, 类型)` 决定，可复现。

入口：`scripts/import_contract.py`（`--dry-run` 只校验）与 `GET /v1/documents/{id}/contract`（返回契约块含 locator、关系与计数）。

## 图纸适配器（API 0.30.0）

`ingestion/cad_adapter.py` 把 `ingestion/cad_dxf.py`（冻结 profile `datamind-2d-labelled-dxf-v1`）的结果映射进统一契约，并注册 `dxf-native`（媒体类型 `image/vnd.dxf`，探测支持 ASCII 与二进制 DXF）。入口：`scripts/parse_drawing.py`。

| 产出 | 规则 |
|---|---|
| 实体块 | 身份层（`DM_DRAWING_ID`/`DM_REVISION`/`DM_PART_ID`）→ `title`（`heading_level=1`）；`LINE`/`CIRCLE` → `figure`（无文本）；其余 `TEXT` → `paragraph` |
| 尺寸块 | 每个特征一块 `dimension`，`native_attributes` 携带 `nominal_value`/`tolerance_lower`/`tolerance_upper`/`unit`/`association_status` |
| locator | `cad_entity`：`layout`、`handle`、`layer`、`model_xy`（插入点或 bbox 中心）、`unit`，附 `view_id` 与坐标系 |
| 关系 | 源 DXF 的**显式 XDATA** 引用：`relation_type=references`、`recovery_method=native_reference`、`confirmed → state=verified`，`evidence` 为源依据文本 |

**有界与诚实**：引用目标不在本次有界读取内时记录 `dimension_target_not_included` 并把 `parse_status` 置 `partial`，**不编造关系**；超 `MAX_BLOCKS` 记录 `block_limit_reached`；CAD 解析告警进入 `quality_findings`。

**DWG**：专有二进制格式，需要外部转换器。**不注册无能力的假适配器**——`parse` 直接返回 `CAD_DWG_CONVERTER_UNAVAILABLE`，直接 `dispatch` 则返回 `PARSE_ADAPTER_UNKNOWN`。

## 边界

- **已接入解析出口**：`parse_pdf` 在返回前把真实块映射进契约并校验，结果记录为 `detail['parse_contract']`（`parsed`/`failed`/`skipped` + 稳定错误码）。契约违反**不会中断摄取**，而是带错误码写入质量明细保持可见。
- **定位写入独立表**：契约 locator 落在 `document_block_locators(block_id, document_id, contract_version, locator)`，与 `document_blocks` 分列存放。原因是基表 `document_blocks` 属主为 `postgres`，而迁移以应用角色运行——项目既有迁移从不 ALTER 基表，因此遵循既有模式另建应用所有的表，而不是变更表属主。
- **只有校验过的块才有定位行**：契约出现之前解析的块不写任何行，**不编造 locator**；块与定位行在同一文档重建时一起删除后重插。
- **已注册 PDF、三种 Office 与原生 DXF**：DOCX/PPTX/XLSX 通过**包内索引**（`word/document.xml`、`ppt/presentation.xml`、`xl/workbook.xml`）区分；不含已知部件的 ZIP 仍报 `application/zip` 并被拒绝，不猜类型。DWG 是专有格式，**不注册假适配器**。
- **DWG/DGN 未实现**：需要外部转换器；`image/vnd.dwg` 只被识别与拒绝，不做转换猜测。图纸 PDF/扫描区域路径仍走通用 PDF/OCR 链路，未做 CAD 专项关联。
- **引用已消费定位，界面尚未使用**：引用与证据现在带 `locators`，但前端高亮尚未改用它们；关系也尚未进入检索排序或消解逻辑。
- **两个存储未合并**：PDF 路径用 `document_blocks` + `document_block_locators`，Office/DXF 用 `document_contract_blocks`，尚未统一为单一契约表。
- **历史文档没有定位**：契约出现之前解析的 27 份文档没有定位行，其引用自然不带区域（不回填、不推导）。

## Office 适配器（API 0.29.0）

可选依赖见 `requirements-office.txt`（python-docx / python-pptx / openpyxl）；未安装时 `office._require` 以 `PARSE_ADAPTER_UNSUPPORTED`（`library_missing`）明确拒绝，而不是返回空解析。入口：`ingestion/office.py`（`parse_docx`/`parse_pptx`/`parse_xlsx`/`parse`）与 `scripts/parse_office.py`。

| 格式 | 产出块 | locator | 刻意不做 |
|---|---|---|---|
| DOCX | 标题（`title` + `heading_level`）、段落、表格（整表为一块，保留 `table_cells` 二维结构） | `office_object`，`part=word/document.xml` + `paragraph=N` 或 `table=N` | 不做单元格级 locator、不解析样式/批注/修订 |
| PPTX | 每个文本框一块；标题占位符标为 `title` | `office_object`，`part=ppt/slides/slideN.xml` + `slide=N`、`shape=M` | 不解析图片、图表、SmartArt、备注 |
| XLSX | **按行**一块，`table_cells` 为当行值 | `sheet_range`，`sheet=<名>` + `range=A1:B1` | 不解析公式（只取缓存值）、合并单元格语义、隐藏表、多表区 |

行号保留**真实电子表格行号**（跳过空行但不重编号），因此引用指向操作员看到的同一行。

**有界而非静默截断**：超过 `MAX_BLOCKS`（默认 2000）或单表 `max_rows_per_sheet`（默认 500）时，`parse_status` 置 `partial` 并在 `unsupported_features` 写入 `block_limit_reached` / `sheet_row_limit_reached`。
- **未实现 Office/CAD 新解析器**：`office_object`/`sheet_range`/`cad_entity` 只是可校验的定位器契约，尚无产出它们的解析器。
- **`Chunk`、`DocumentFact`、`DrawingFeature` 未纳入本契约**：现有存储与检索尚未统一，留待后续阶段。
- 契约版本为 `parse-contract-v1`；变更契约需同时更新测试与本文件。
