# 代码与契约一致性审查报告

- 审查对象：`/Users/caicai/DataMind Agent`（HEAD `005b9cc`；审查开始时工作区只有 Codex 的 4 个未跟踪产物，审查期间出现了其他 agent 的并发改动，见下方提示）
- 审查日期：2026-10-08
- 审查性质：**只读**。除本文件外未创建/修改/删除任何文件；未执行任何 git 写操作、未执行 `docker compose`、未运行 `reset_demo.sh` / `verify.py` / `ingest.py` / `test_isolated.py`。
- 结论摘要：**未发现 P1**。发现 4 个 P2（OHR-Bench 摄取绕过契约、工业演示丢弃全部已验证 locator、DXF 块 id 截断导致长 document_id 整体解析失败、CLI 写入的契约块在 HTTP 读接口 404）与 8 个 P3。契约自身的校验器是闭合的、测试也通过；问题集中在**写路径旁路**（OHR/工业 seed）与**两套契约存储的读写不对称**。

---

## 1. 审查范围与方法

重点文件：`datamind/parse_contract.py`、`ingestion/ingest.py`、`ingestion/office.py`、`ingestion/cad_adapter.py`、`ingestion/contract_store.py`、`datamind/rag_engine.py`、`datamind/agent.py`、`datamind/knowledge.py`、`datamind/providers.py`、`datamind/settings_store.py`、`db/seed_industrial.py`、`web/app.js`、`db/migrations/014–016`。

方法：静态读代码定形（写入的列 vs 校验接受的键）+ 纯 Python 复现（不写文件/不写库）+ **只读** SQL 查真实库（`docker exec datamind-postgres psql ... -tAc`）+ 单文件测试取证（2 个文件，各跑一次）。所有结论按「已确认 / 疑似 / 无法判定」标注：下表中未写「疑似」的均为**已确认**（有复现或读到直接证据）。

> **并发修改提示（与本次审查无关）**：审查期间另一个 agent 正在修改 `ingestion/ingest.py`（新增 `knowledge_base_jobs()` 的 manifest 白名单、改变 `main()` 的作业收集），并改动了 `README.md`/`PROGRESS.md`/`技术文档…V1.3.md`，同时新增了 `tests/test_ingest_manifest_whitelist.py` 与其他 `reports/review_*.md`。这些都不是本审查的产物。本报告中 `ingestion/ingest.py` 的行号已按**审查收尾时**（含该并发改动）逐一重新核对：它把该文件后半段行号整体推移约 +21～+24（例如 reset 段 534→558、JSON 分支 608→629），但不改变本文任何结论。若该文件继续被编辑，请以「函数名 + 语句」为锚点复核行号。

## 2. 实际运行过的命令与结果

```text
# 取证测试（各跑一次，跑后核对无残留行）
PYTHONPATH=tests .venv/bin/python -m unittest tests.test_parse_contract   -> Ran 42 tests OK
PYTHONPATH=tests .venv/bin/python -m unittest tests.test_contract_store   -> Ran 8 tests OK
psql: select count(*) from document_assets where document_id like 'doc-%-test'
      -> assets|0  cblocks|0  locs|0        # 测试自清理，未污染库

# 只读库事实
document_contract_blocks=0  document_block_relations=0  document_block_locators=33
document_blocks=586  document_assets=30  status 全部 = 'indexed'|30
validation 分布：quality_detail->'parse_contract'->>'validation' = (none)|26, parsed|4
file_type/parser_version：pdf|pdf-ocr-0.4|15, json|pdf-ocr-0.4|14, pdf|industrial-labelled-v2|1
14 个 json 文档的 document_blocks.bbox 全部为 [0,0,0,0]（契约会判 bbox_inverted）
doc-industrial-0ac6c55838c3：document_blocks=296，document_block_locators=0，
   quality_detail.parse_contract = {"validation":"parsed","locators_written":296,"locator_kinds":["pdf_region"]}
孤儿检查（locator/relation/contract_block 对 assets/blocks）全部为 0

# 纯 Python 复现（read-only）
pdf_region_locator(bbox=[0,0,0,0])                        -> PARSE_LOCATOR_INVALID / bbox_inverted
parse_contract.validate_locator(cad_dxf 形状 locator)      -> PARSE_LOCATOR_INVALID / missing_field document_id
parse_contract.validate_block(带 heading_level/table_cells) -> 通过，额外键被保留
cad_adapter.parse(dev-mm.dxf, document_id='d'*59)          -> PARSE_RESULT_INVALID / duplicate_block_id
cad_adapter.parse(dev-mm.dxf, document_id='doc-short')     -> OK，8 块 8 个唯一 id
from_pdf_blocks(document_id='d'*60) 的 fallback block_id   -> 长度 66（列宽 VARCHAR(64)）
lexical 检索取证：demo_kb 3 hits/9 block_ids -> 3 个 locator；ohrbench_kb 3 hits/1 block_id -> 0 个 locator
pymupdf 扫描 data/knowledge_base/*.pdf（排除 field-test-*）-> 无任何 page.rotation != 0
```

## 3. 问题表

| 编号 | 严重度 | 标题 | 证据（文件:行 + 复现） | 影响 | 建议修法与验收标准 | 置信度 |
|---|---|---|---|---|---|---|
| F01 | P2 | OHR-Bench JSON 摄取绕过统一解析契约，写入了契约明确拒绝的形状 | `ingestion/ingest.py:627-632`（JSON 分支写 `bbox=[0,0,0,0]`，不调用 `_contract_summary`；后者只在 `parse_pdf` 内 `:332` 调用）；`datamind/parse_contract.py:179-180` 判 `bbox_inverted`。复现：`from_pdf_blocks([{"page_no":1,"bbox":[0,0,0,0],...}], ...)` → `PARSE_LOCATOR_INVALID/bbox_inverted`。库内 14 个 json 文档 `bbox=[0,0,0,0]`、`parser_version='pdf-ocr-0.4'` | 14 个 OHR-Bench 文档（62 个 chunk，ohrbench_kb 全部内容）没有任何已验证 locator；`GET /v1/documents/{id}/contract` 对它报 `block_count=0/locator_count=0`，而它在 `document_blocks` 里确有块行；检索取证显示 ohrbench_kb 的引用 100% 无定位。另：JSON 文档的 `parser_version` 被写成 PDF 解析器版本 | 给 JSON/OHR 分支一个注册适配器（或在契约里显式表达"无空间定位"的 locator kind），使该分支同样经 `dispatch` 校验并写入 locator，否则不要写块行而只留显式 `quality_findings`；`parser_version` 按真实解析器写。验收：对任一 json 文档，`locator_count + 显式 no_locator 计数 == block_count`，且 `/contract` 不再对已有块行的文档报 0 | 已确认 |
| F02 | P2 | 工业演示路径丢弃全部已验证 locator，且 `quality_detail` 谎报 296 条已写 | `db/seed_industrial.py:26` 调 `parse_pdf`（内部 `ingest.py:188-198` 已把 locator 挂到块并统计 `locators_written`），`:27` 重写 block_id 为 `iblk-*`，`:52-55` 只写 `document_blocks`，**从不写 `document_block_locators`**。库内：`locators_written=296` vs `count(*)=0` | 演示主资产（industrial_demo）的契约定位整体丢失；`/v1/documents/doc-industrial-0ac6c55838c3/contract` 返回 `locator_count=0`，而同一文档的 `quality_detail` 宣称写了 296 条 —— 持久化元数据与库内容直接矛盾。重写的 `iblk-*` id 也使已校验 locator 的块身份失效（当前因不落库而未暴露） | 让 seed 走 `ingest._locator_row`/`LOCATOR_INSERT_SQL` 用同一个（最终）block_id 落库，或不要重写 id；把计数器改名为 `locators_prepared` 以免再次误导。验收：`select count(*) from document_block_locators where document_id='doc-industrial-…'` == `locators_written` | 已确认 |
| F03 | P2 | DXF 块 id 截断：长 `document_id` 使整份图纸解析失败（office 已修同类缺陷，CAD 未修） | `ingestion/cad_adapter.py:60` `f'dxf-{document_id}-{index}'.replace(' ','_')[:64]`；`ingestion/office.py:43-52` 的 docstring 明确记录"截断导致块 id 重复"并改为摘要。复现：`cad_adapter.parse(data/cad_demo/development/dev-mm.dxf, document_id='d'*59)` → `PARSE_RESULT_INVALID/duplicate_block_id`；`document_id='doc-short'` → 8 块 8 唯一 id（阈值：doc_id ≥ 59 字符时同位数索引全部坍缩到同一 64 字符前缀） | ≥59 字符的 document_id（`file_jobs.DrawingRegistration` 允许到 64）会让整个契约导入以"重复块 id"失败，用户看不到真正原因 | 与 office 对齐：`'dxf-' + sha256(f'{document_id}\x1f{index}')[:32]`（定长、无截断）。验收：64 字符 document_id 下解析成功且 `len(set(block_id)) == len(blocks)` | 已确认 |
| F04 | P3 | `read_locators` 只读一套存储，Office/DXF 的已验证 locator 永远读不出来 | `ingestion/contract_store.py:97-113` 只查 `document_block_locators`；而 Office/DXF 的 locator 存在 `document_contract_blocks.locator`（`:75-77`）。`datamind/agent.py:215-216` 把它接进 `rag_engine.locator_lookup` | 对只存在于 `document_contract_blocks` 的块，引用会同时给出"关系"却给不出"定位"（`rag_engine.py:97-103`），两套证据对同一块自相矛盾。当前**不可达**（`persist_result` 唯一调用者是 CLI，库内 contract_blocks=0，且 Office/DXF 无 chunk），一旦 Office/DXF 进入检索就会静默产生无定位引用 | 明确哪套存储是 locator 的读权威：要么 `read_locators` 联合两表（`document_contract_blocks.locator` 已是同一份已校验对象），要么在 store 层把契约块的 locator 也写一份到 `document_block_locators`。验收：对只在 `document_contract_blocks` 落库的块，`read_locators([id])` 必须返回该 locator | 已确认（代码/库），影响为潜在 |
| F05 | P2 | CLI 写入的契约块在 HTTP 读接口上不可见（对已有数据的文档回 404） | `scripts/import_contract.py:70-76` 只写 blocks/relations，不建 `document_assets`；`datamind/api.py:1004` 先 `_public_document`（`:937-945` 要求 `document_assets` + `status='indexed'` + acl + collection），否则 404 `DOCUMENT_NOT_FOUND`。反证：`tests/test_contract_store.py:111-115` 必须先手工插入 `document_assets` 才能调该接口 | 唯一的生产写入口（CLI）产出的数据任何 HTTP 读路径都看不到；README:40 称"Office、原生 DXF 的契约适配与存储（块/定位/关系）已完成"，而对该数据集而言读接口只会说"文档不存在" | import 路径要么同时登记资产（`document_assets` + `collection`/`acl`），要么 `/contract` 在无资产行时以授权为前提直读契约存储并不再报 404。验收：`scripts/import_contract.py --document-id X --source Y` 之后 `GET /v1/documents/X/contract` 返回 200 与真实 block_count | 已确认 |
| F06 | P3 | 契约校验不含存储宽度约束：校验通过但写库失败 | `datamind/parse_contract.py:314-334` 对 `block_id/document_id/block_type` 只查"非空文本"；列宽为 `block_id/document_id VARCHAR(64)`、`block_type VARCHAR(16)`（`information_schema` 实测；`db/migrations/014:7`、`015:6-17`）。复现：`from_pdf_blocks(document_id='d'*60)` 产出的 fallback `block_id` 长度 66（`parse_contract.py:618`），`validate_block` 通过 | 该类 payload 会在 INSERT 处以裸 psycopg2 异常失败（事务回滚），而不是返回契约错误码；调用方无法在校验阶段发现 | 在 `validate_block` 增加与列宽一致的边界（`block_id`/`document_id` ≤64、`block_type` ≤16），或加宽列。验收：65 字符 `block_id` → `PARSE_BLOCK_INVALID`；单测固定该边界 | 已确认 |
| F07 | P3 | 契约块形状不闭合：校验保留的键在契约存储里无处可存 | `ingestion/office.py:60-66` 给块加 `heading_level`/`table_cells`；`parse_contract.validate_block` 不拒绝额外键（`parse_contract.py:314-334`，复现：带这两个键的块校验通过）；`contract_store.py:18-21` 的 INSERT 只有 9 列，`db/migrations/015` 无对应列，`:75-77` 读回也不返回 | DOCX 标题层级、DOCX/XLSX 表格单元格矩阵在"契约存储"里静默丢失，只剩扁平文本；校验形状 ≠ 落库形状（与 PDF 侧 `document_blocks` 保留这些列形成不对称） | 二选一：写进 `native_attributes`（现成 JSONB 列）并在 `read_result` 返回，或在校验时把不支持的键判为 `unknown_field`。验收：往返测试证明 `validate_block` 容忍的每个键要么被存储要么被拒绝 | 已确认（静态 + 库 schema） |
| F08 | P3 | 摄取状态机与真实状态值不一致，且状态机函数无调用者 | `datamind/parse_contract.py:26-30` `INGEST_STATES/INGEST_TRANSITIONS`（含 `queued/classifying/validating/indexing/ready`）vs `ingestion/ingest.py:61-63` 真实写 `parsing/indexed/failed`；**`indexed` 不在 `INGEST_STATES` 里**。库内实测 `status` 只有 `indexed`（30 行），中间态从未出现。`ingest_step` 无任何生产调用者（只有 `tests/test_parse_contract.py:111-116`） | 契约里的状态模型无法描述它所守护的库；任何按 `INGEST_STATES` 校验的代码都会拒绝管线唯一会写的终态 "indexed" | 二选一：让 `document_assets.status` 真正经 `validate_ingest_state`/`ingest_step` 并被测试覆盖（同时把 `indexed` 加入集合），或删除未使用的状态机。验收：断言"摄取路径写入的 status 集合 ⊆ INGEST_STATES" | 已确认 |
| F09 | P3 | 同一块的 `content_hash` 有两套语义，字段读取链只认其中一套 | 纯文本方案：`ingestion/ingest.py:145`、`ingestion/facts.py:72`、`ingestion/industrial_facts.py:80`（`sha256(text)`）；复合方案：`parse_contract.py:642-645`、`ingestion/office.py:70-72`、`ingestion/cad_adapter.py:30-32`。`datamind/field_store.py:53-55` 用 `ref.block_content_hash == document_blocks.content_hash == sha256(text)` 做门禁；PDF 侧契约算出的复合 hash 因 `document_block_locators` 无该列而被丢弃 | 读 `/contract` 的一方无法复算契约 hash；若将来"统一"为复合方案而不改 `field_store`，所有事实会因 `valid=False` **被静默过滤**（不报错、不告警） | 明确一个权威 hash 函数并让两处引用它；若契约 hash 必须保留，给它一列。验收：单测对同一块用同一个函数复算 `document_blocks` 与契约存储里的 hash | 已确认 |
| F10 | P3 | 同名 `cad_entity` locator 存在两套互不兼容的 schema | 契约形状：`parse_contract.py:48` + `:210-220`（`document_id/source_version/layout/handle/layer/model_xy/unit`）；CAD 快照形状：`ingestion/cad_dxf.py:112-114` + `ingestion/cad_contract.py:127-134`（`source_sha256/handle/layer/view_id/coordinate_system/layout`，无 `document_id/model_xy/unit`）。双向复现：前者喂 `validate_locator` → `missing_field document_id`；后者喂 pydantic `Locator` → 缺 `source_sha256/view_id` | 同一个 `locator_kind='cad_entity'` 在 `/v1/cad…` 与契约接口上给前端两种键集（`web/app.js:823` 只按 `locator_kind` 分派），任何共享消费者都得按来源分支 | 给快照定位改一个不冲突的 kind（如 `cad_snapshot_entity`），或把它扩成契约校验能接受的超集。验收：`/v1/cad` 返回的每个 locator 都能被 `parse_contract.validate_locator` 接受，或断言两种 kind 无交集 | 已确认 |
| F11 | P3 | 网关移除后的死引用/无效预设 | `web/app.js:22` 仍内置预设 `{'本地网关 4000', base:'http://localhost:4000/v1'}`；网关与其容器已删（`git show --stat 4c246b3`、`PROGRESS.md:10,18`），`README.md:7` 声明"不依赖任何本地网关"，但 `README.md:72` 仍把"本地网关"列为预设 | 用户一键选中只会得到连接失败（`MODEL_UNREACHABLE`）；README 与自身声明矛盾 | 删除该预设（或改名/留空为自定义提示），并从 README:72 的预设清单里去掉"本地网关"。验收：`grep -rn "localhost:4000" web/ README.md` 在测试之外无命中 | 已确认 |
| F12 | P3 | `ingest.py --reset` 不清契约三表，删除文档会留下孤儿关系/定位 | `ingestion/ingest.py:558-561` 只删 `chunks/document_blocks/ingest_jobs/document_assets`；`contract_store.py:44-45` 的清理只按 document_id 且只发生在重新 persist 时；`document_block_relations` 无外键（`db/migrations/015` 注释明确"plain column, no FK"）。当前库有 33 条 locator 会在独立 `--reset` 后全部成为孤儿 | `README.md:386` 说 `ingest.py --reset` 会"清空相应数据"，实际不清；孤儿关系/契约块不会被级联清理。注：`scripts/reset_demo.sh` 走 `docker compose down -v` 清卷，故一键路径不受影响 | 在 `--reset` 分支补三条 DELETE（`document_block_locators/document_contract_blocks/document_block_relations`），或加外键 `ON DELETE CASCADE`。验收：`--reset` 后三表计数为 0，且文档删除路径无孤儿（可用同一条 SQL 断言） | 已确认（代码/库现状），影响低 |

## 4. 「同一语义两处实现」专项清单（Q5）

**已不一致（已在上表立条目）**：F03 块 id 生成（5 处在用：`ingest.py:331`、`ingest.py:436-440`、`parse_contract.py:618`、`office.py:52`、`cad_adapter.py:60`，另有 `db/seed_industrial.py:27`）；F08 摄取状态；F09 content_hash；F10 cad_entity locator schema。

**行为一致但确为两份实现（当前不构成缺陷，仅记录，避免以后各改一处）**：

| 语义 | 实现处 | 是否已不一致 |
|---|---|---|
| 只读 DSN 默认值 | `datamind/agent.py:41-43`（`api.py:22` 复用）、`datamind/knowledge.py:49-51`、`datamind/sql_engine.py:266`、`datamind/sql_engine.py:413` | 否（默认串与 env 键相同，实测解析结果一致） |
| PDF 页尺寸 | `ingest.py:175-176`、`db/seed_industrial.py:29`、`field_store.py:29-31`（均为 pymupdf `page.rect`） | 否 |
| run 状态 → 展示 | `agent.py:128-130`（→事件名）与 `web/app.js:564-565`（→中文标签） | 否（跨语言展示层，两侧同源状态集） |
| 文档状态标签 | `web/app.js:1077-1079` 与后端 `status='indexed'` 过滤 | 否（前端已注释说明列表只出 indexed） |
| profile 解析 / proxy 传递 | **已收敛为一处**：`settings_store.resolve_profile_values`；`api.resolve_profile`（`api.py:733-738`）与 `sql_engine.settings`（`sql_engine.py:47-77`，proxy 在 `:71`）都是它的投影；唯一传输端 `providers.make_client`（`providers.py:162-177`） | 否（旧缺陷已修）；但 `docs/AUDIT_FIXES.md:63` 仍写"仍是两套逻辑"，属文档滞后 |

**有意保留的副本**：`data/evaluation/frozen/industrial_labelled_v2/runtime/ingestion/{ingest,ocr,tables,industrial_facts}.py` 是冻结评测运行时（`技术文档/…V1.2.md:1503` 与 manifest 记录），与当前 `ingestion/*` 已不同——属设计内的历史基线，不算重复实现缺陷。

## 5. 未接线能力清单（Q6）

| 能力 | 调用者 | 判断 |
|---|---|---|
| `ingestion/office.py`（DOCX/PPTX/XLSX 三适配器）+ `cad_adapter.parse_dxf` + `contract_store.persist_result` | 仅 `scripts/parse_office.py`、`scripts/parse_drawing.py`、`scripts/import_contract.py` 与测试；HTTP/文件队列均无入口（`file_jobs._BOUNDED_TARGETS` 只有 drawing/measurement 两条 CAD 注册路径，`file_uploads` 暂存后也走 `cad_store.register`） | **边界 + 遗漏**：README:30 明确"PDF/OCR/Office 不在当前队列"，故不进队列是有意的；但"已落库的契约块无 HTTP 读路径"（F05）是遗漏 |
| `parse_contract.ingest_step` / `INGEST_TRANSITIONS` | 无（仅 `tests/test_parse_contract.py:111-116`） | **遗漏**（见 F08）；`validate_ingest_state` 也只被 `ingest_step`/`validate_parse_result` 用到 |
| `parse_contract.capabilities` / `supports_capability` / `require_capability` / `supported_media_types` / `registered_adapters` | 仅测试（`registered_adapters` 还被 office/cad/ingest 的幂等注册用） | **有意边界**：模块自述是"契约与校验"，属对外能力自省接口，可保留 |
| locator kind `image_region` / `temporal_segment` / `model_spatial_object` 的校验器 | 无任何适配器产出这些 kind | **有意边界**（为设计文档 §5.2/§5.4 预留），但目前无生产路径会构造它们 |
| PDF 适配器的 `relations` | `ingestion/ingest.py:105` 恒为 `[]` | **边界**：PDF 侧不抽关系，故 `document_block_relations` 实际只能由 Office/DXF CLI 写入（库内当前 0 行）；README:40 已说明"关系进入检索排序/冲突消解"是后续工作 |
| `contract_store.read_locators/read_relations` 的上限与失败码 | 已接（`agent.py:215-223`），失败不阻断回答（见下节） | 已接线 |

## 6. 我检查过且**没有**发现问题的部分

- `parse_contract` 的 locator/relation 校验是**闭合**的：`validate_locator:289-292` 与 `validate_relation:361-363` 都拒绝未知键，`validate_relation` 拒绝自引用之外的非法端点和 `name_similarity/proximity/adjacency/guess` 恢复法；42 项契约测试全绿。
- PDF 写路径写入的是**校验过的同一对象**：`ingest.py:188-193` 把 `payload['blocks'][i]['locator']` 原样挂到块上，`:148-158` 只序列化该对象落 `document_block_locators`；`tests/test_parse_contract.py:523-543` 在真实库上用同一份语句插入（事务内回滚），列与语句匹配；跑过后无残留行。
- `/v1/documents/{id}/contract` 已同时读两套存储（`api.py:994-1011` + `contract_store.py:65-90`），PDF 有 locator 时不再谎报 `block_count:0`（回滚回归 `tests/test_contract_store.py:129-158` 通过）。
- 定位/关系的**失败路径确实不阻断回答**（看代码而非注释）：`rag_engine.py:113-123`、`:126-141` 捕获 `Exception`，返回 `{}` 并写 `locator_lookup_error`/`relation_lookup_error`；`:156-164` 在任何生成/校验之前就把这两个错误透传进 `result`；两次 lookup 都只对去重排序后的 id 集合调用一次；测试覆盖了 `RuntimeError` 与带 `code` 的异常。`ask_documents` 在无 hits 时也确实先返回 abstain（`:171-174`），不会被 lookup 影响。
- 可见性门自洽：所有列举到的读入口都过滤 `status='indexed'`——`knowledge.py:120,144`、`api.py:671,939-940,955`、`authority.py:73`、`approvals.py:30`、`fact_reviews.py:31`、`industrial.py:52`；`ingest.py:55-57` 的注释属实。发布顺序（`ingest.py:472-492`）先落"不可检索"并 commit，再动 Qdrant，最后才可能置 `indexed`，异常传播路径不会把失败文档置成 `indexed`。
- profile 解析**确实只有一处**：`settings_store.resolve_profile_values`，`api.resolve_profile` 与 `sql_engine.settings` 都是它的投影，proxy/headers 两条链路都带上（旧"探活走代理、问答不走"的缺陷已修）；`api.resolve_profile` 的敏感头合并不再受 `stored` 注入影响（`api.py:721-726`）。
- 网关移除后 `datamind/providers.py`、`datamind/settings_store.py` **没有**为网关存在的逻辑：`providers.py` 只构造标准 `/chat/completions`、`/messages`、`/models`；`settings_store.SETTING_KEY="model.gateway"` 只是旧版单配置行的键名，用于一次性迁移（`settings_store.py:527-535`），不是网关依赖；`providers.is_local/LOCAL_HOSTS` 对任何本机端点仍需要。`deploy/compose.yaml`、`scripts/start_local.sh` 无网关服务或 4000 端口。
- 冻结基线未被要求更新：`技术文档/…V1.2.md`、`reports/*.json`、`data/evaluation/frozen/**` 与当前代码的差异是设计内的历史记录。

## 7. 无法判定 / 超出本次范围

- **无法判定（设计意图问题）**：`document_contract_blocks.locator` 与 `document_block_locators` 谁是"引用定位"的读权威？代码里两种用法并存（`read_result` 双读 vs `read_locators` 单读）。这需要 V1.3 §5.2/§5.4 的明确裁定，我只按 `contract_store.py` 自身 docstring 与两处调用的实际行为报告不一致（F04/F05），不替设计做选择。
- **疑似（未复现，无样本）**：PDF 摄取从不传 `page_rotations/page_labels/page_transforms`（`ingest.py:99-101`），`parse_contract.py:621-624` 因此对每页都写 `rotation_degrees=0`。若源 PDF 有 `/Rotate`，locator 的坐标系会与实际页面不符；我扫描了 `data/knowledge_base/*.pdf`（排除 Codex 的 `field-test-*`），**没有任何旋转页**，故无法用现有样本复现，也未按猜测升级为正式条目。
- **超出范围**：`data/knowledge_base/field-test-*.pdf`、`reports/project_audit_20261008_*`、`tmp/audit-baseline/**`（另一 agent 的整库副本）与 `技术文档`/`reports` 冻结基线，均未纳入判断。
- **未做**：未运行端到端 `verify.py`/全量测试（按要求避免与其他人争用数据库），故"多进程/并发下的摄取一致性"未在本次实测；未运行 `docker compose` 任何子命令。
- **历史数据边界（非缺陷）**：库内 12/16 个 PDF 文档没有 `parse_contract` 记录（契约引入前的历史块，按 `parse_contract.py` 的自述不伪造 locator）；`ingest.py:602` 的跳过条件（`status+parser_version` 未变则跳过）会让普通重跑不补 locator，但 README:183 已给出 `--refresh` 回填路径，故记为设计内边界而非缺陷。

## 8. 声明

本次为**只读**审查：未修改、新建或删除任何业务代码、配置、测试或数据；唯一写入的文件是本报告 `reports/review_code_consistency.md`。未执行任何 git 写操作、未执行 `docker compose`、未运行 `reset_demo.sh` / `verify.py` / `ingest.py` / `test_isolated.py`；对 PostgreSQL 只做了 `SELECT`/`information_schema` 类只读查询，取证测试（`tests.test_parse_contract`、`tests.test_contract_store`）自清理，已核对无残留行。
