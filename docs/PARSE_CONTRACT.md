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

## 边界

- **已接入解析出口**：`parse_pdf` 在返回前把真实块映射进契约并校验，结果记录为 `detail['parse_contract']`（`parsed`/`failed`/`skipped` + 稳定错误码）。契约违反**不会中断摄取**，而是带错误码写入质量明细保持可见。
- **定位写入独立表**：契约 locator 落在 `document_block_locators(block_id, document_id, contract_version, locator)`，与 `document_blocks` 分列存放。原因是基表 `document_blocks` 属主为 `postgres`，而迁移以应用角色运行——项目既有迁移从不 ALTER 基表，因此遵循既有模式另建应用所有的表，而不是变更表属主。
- **只有校验过的块才有定位行**：契约出现之前解析的块不写任何行，**不编造 locator**；块与定位行在同一文档重建时一起删除后重插。
- **只注册了 PDF 一个适配器**：ZIP 类（DOCX/XLSX/PPTX）只被识别为 `application/zip`，没有更深的内容判定与适配器。
- **未实现 Office/CAD 新解析器**：`office_object`/`sheet_range`/`cad_entity` 只是可校验的定位器契约，尚无产出它们的解析器。
- **`Chunk`、`DocumentFact`、`DrawingFeature` 未纳入本契约**：现有存储与检索尚未统一，留待后续阶段。
- 契约版本为 `parse-contract-v1`；变更契约需同时更新测试与本文件。
