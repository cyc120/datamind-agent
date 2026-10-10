# WP9 新文件问答闭环 + 演示素材（多格式样本集）

| 字段 | 值 |
|---|---|
| **WP** | WP9「新文件问答闭环 + 演示素材」（《提升计划》§4 WP9；审计 §7.3） |
| **日期** | 2026-10-09 22:39（CST，北京时间；本机 `date` = `Fri Oct 9 22:39 CST 2026`，最后一次报告写入 UTC `2026-10-09T14:39Z`） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`；`data/qa_samples/manifest.json` 的 `commit` 字段同为 `e4e2ebf`） |
| **API 版本** | `0.39.0`（`GET http://127.0.0.1:8000/openapi.json` 的 `info.version`，由 `scripts/eval_qa_samples.py` 写入报告 `api_version`；`datamind/api.py:197` `version="0.39.0"`） |

---

## 问题

审计 §7.3 原文：「上传审批现在已正确表达『无切片，不可问答』，但自动切片/索引仍 deferred。」以及「比赛演示至少需要一份新上传文件走完：解析 → 复核 → 建立索引 → 提问 → 正确回答 → 点击原位证据。」《提升计划》WP9 补充：需要 **≥10 份、≥4 种格式**（原生 PDF、扫描 PDF、DOCX、XLSX、PPTX、DXF）的固定样本集，且缺索引时要如实提示。

本轮开始前的实测缺口（只读核对）：

1. `ingestion/upload_pipeline.py:156` 的 `VECTOR_INDEX = 'deferred'`：上传路径**不写 `chunks`**，而检索器 `datamind.knowledge` 只读 `chunks` ⇒ 新文件必然不可检索（设计如此，不是偶发故障）。
2. 仓库里没有面向「多格式问答」的冻结样本集：`data/knowledge_base/` 是 12 份 PDF，Office/DXF 没有带答案断言的问答样本，也没有逐份声明「期望引用定位类型」的清单。
3. 前端 7 个视图（`web/index.html:17-26`）里**没有上传/审批入口**（`grep -n "uploads" web/app.js` 无命中），所以新文件闭环只能用 API 演示。

## 做法

**只新增文件，未改动任何既有业务代码**（`web/`、`datamind/`、`db/migrations/` 未动；新增：`data/qa_samples/`（11 份样本 + `manifest.json` + 生成脚本）、`scripts/eval_qa_samples.py`、`docs/QA_SAMPLE_GUIDE.md`、`tests/test_qa_samples.py`、`reports/qa_samples_v1.json`、本卡）：

1. **`data/qa_samples/build_samples.py`**：确定性生成 11 份样本（6 种格式）。
   - 固定 ZIP 条目时间戳与 OOXML `docProps/core.xml` 的 `dcterms:created/modified`；
     PDF 用 `pymupdf` 的 `tobytes(no_new_id=True, reproducible=True)` + 固定 metadata；
     DXF 归一化 `CLASSES`/`OBJECTS` 记录顺序（按 class/handle 排序，根字典保持第一条）、
     julian 时间戳、GUID 与库版本注释。⇒ 同样内容重复生成得到**同一个 sha256**（测试 `test_regeneration_reproduces_the_frozen_bytes` 逐份验证）。
   - `manifest.json` **独占创建**（已存在且未加 `--force` 即拒绝覆盖），先冻结文件、格式、大小、sha256、期望可回答问题（带答案断言）、期望引用定位类型，再评测。
   - 答案断言可离线重算：`exact_text` / `arith_sum` / `arith_diff` / `arith_product` / `arith_ratio_percent` / `count_matching` / `count_less_than`；操作数用**文件内指针**（`cell:销售明细:C2`、`docx_table:0:1:1`、`page:2`、`slide:2`）落回原文件。
2. **`scripts/eval_qa_samples.py`**：
   - `--stored`：不带会话 cookie 只读 `GET /v1/documents` 与 `/v1/documents/{id}/contract`；再用 **`ingestion.upload_pipeline.searchability`（只 import、只调用，未改实现）** 从只读库字段（`status/needs_review/chunks/block_count`）重算三元组并逐条比对。
   - `--http`：真实 `POST /v1/uploads`（带 `X-CSRF-Token`）→ `python -m ingestion.upload_worker --once` → 轮询详情 → `approve` → `GET /contract` → `POST /v1/chat`（显式文件范围 / 默认范围 / 对照组），每步状态码与响应摘要进报告。
3. **`docs/QA_SAMPLE_GUIDE.md`**：演示主线（界面 4 个入口 + 新文件 API 六步）、每步预期、9 条失败点与现场话术。
4. **`tests/test_qa_samples.py`**：16 项（冻结校验、篡改检出、字节确定性、断言算术、定位期望对齐 `parse_contract`、searchability 语义真值表与真实行三态）。
5. **`reports/qa_samples_v1.json`**：逐样本状态 + 失败清单 + `index_wiring` + `not_done`。

**明确不做**：不接入向量索引、不改 `upload_pipeline`/`knowledge`/`web`、不跑全量摄取、不对演示库写入。

## 数据与集

| 项 | 值 |
|---|---|
| 样本目录 | `data/qa_samples/`（11 份文件 + `manifest.json`） |
| 格式分布 | `pdf-native` 2、`pdf-scanned` 2、`docx-native` 2、`xlsx-native` 2、`pptx-native` 2、`dxf-native` 1（6 种格式，满足 ≥4） |
| 问题数 | 23 条（每份 2–3 条），全部带可判定断言 |
| manifest digest | `8080b273c4cd2325a5fec45142b812b0df0f9a06aa07d818defa03e9886acc67` |
| 冻结时间 | `created_at = 2026-10-09T14:35:54.996944+00:00`（生成脚本运行时刻；因失败样本 8 的 DXF 修复重新冻结过一次，旧 digest `dfba9eb8…`） |
| 逐份 sha256（前 16 位，原样摘录） | 01 `c73f4c6120fba820`、02 `ab4eae6f4cbba5c5`、03 `e67a5d62129af51c`、04 `861d44e5068ea186`、05 `e8b99022dffb2ca6`、06 `7520a0bb3dccd51f`、07 `b9942c79cb4ca165`、08 `e8d4c2e3c38a063d`、09 `8777e601c0c5577e`、10 `4e87f29ea8994314`、11 `12511e7061c1636d` |
| 扫描件渲染字体 | `/System/Library/Fonts/Supplemental/Arial Unicode.ttf`，sha256 `876af2cd4854644e7f3e7feb2f688997fdb3343c6df6693611209c9dfb47ccec`（记入 manifest） |
| HTTP 上传的那一份 | `05_service_sla_manual.docx`（36907 B，sha256 `e8b99022dffb2ca620d2374728bff85c42d2bad7dbc77e3fc43f5018917676a4`） |
| 是否自建 | 全部自建合成样本，**非官方测试集**，不可外推 |

## 结果（分子分母）

全部数字来自本轮真实执行，逐条附命令退出码。

### A. 离线样本集（不调用模型）

| 检查 | 分子/分母 | 关键输出（原样摘录） | 退出码 |
|---|---:|---|---:|
| 生成样本 + 冻结 manifest | 11/11 份、23/23 问题 | `已生成 11 份样本（23 个问题），格式：docx-native,dxf-native,pdf-native,pdf-scanned,pptx-native,xlsx-native`；`manifest digest：8080b273c4cd2325a5fec45142b812b0df0f9a06aa07d818defa03e9886acc67` | `0` |
| 冻结校验 `--check` | **1/1 通过** | `{"checked": ".../manifest.json", "ok": true, "problems": []}` | `0` |
| 字节确定性（重新生成比对 sha256） | **11/11 相同** | 测试输出 `test_regeneration_reproduces_the_frozen_bytes ... ok`；另用 `PYTHONHASHSEED=0/1/2/4242/12345/999` 六个不同进程复核 11/11 一致 | `0` |
| 答案断言离线重算 | **23/23 通过** | `stored.consistency.assertions_ok = 23`、`assertions_failed = 0` | `0` |
| 扫描件无文本层 | **2/2 确认** | `03` 提取文本长度 `0`、`04` 长度 `1`（仅换行）；`test_scanned_samples_have_no_text_layer_and_native_ones_do ... ok` | `0` |
| DXF 可被 `ezdxf` 回读 | 1/1 | 规范化后回读成功：`reread_ok layers= 5 entities= 7` | `0` |

### B. `--stored`：可检索性判定一致性（匿名只读 + 只读库复核）

| 检查 | 分子/分母 | 关键输出（原样摘录） | 退出码 |
|---|---:|---|---:|
| API 文档条数 | 27/27 有库行 | `"documents_reported_by_api": 27, "documents_with_db_row": 27` | `0` |
| searchability 三元组一致 | **27/27 一致、0 不一致** | `"consistent": 27, "inconsistent": 0` | `0` |
| 状态分布 | — | `"index_state_counts": {"indexed": 26, "no_chunks": 1}`；`"searchable_true": 26, "searchable_false": 1` | `0` |
| 只读库总行数 / 未发布 | — | `"db_document_rows": 30, "db_rows_not_published": 3` | `0` |
| 样本入库情况 | **1/11 入库、0/11 出现在匿名文档列表** | `"samples_in_store": 1, "samples_published_visible": 0, "samples_total": 11` | `0` |
| manifest 冻结状态 | 1/1 | `"manifest_frozen": true` | `0` |
| 逐样本状态字段 | 11/11 | 每份显式给出 `status`（`not_in_store` 10、`in_store_unretrievable` 1）与 `stored`（未入库为 `null`）；入库那份：`chunks=0`、`contract_blocks=6`、`locators=0`、`searchable=false` | `0` |

> 读身份：`/v1/auth/me` 返回 `{"user_id": "user-local-admin", "roles": ["admin"]}`。本机 loopback 请求由
> `datamind/auth.py` 的 `demo_allowed` 视为已配置的 demo 管理员，因此「匿名」只意味着**未发送会话 cookie、
> 未使用任何伪造凭证**；报告里 `stored.read_identity_note` 如实记录该边界。

### C. `--http`：真实上传 → 解析 → 复核 → （索引未接入）→ 提问

| 步骤 | 请求/命令 | 结果（原样摘录） | 退出码 |
|---|---|---|---:|
| 取身份 | `GET /v1/auth/me` | `200`，`"csrf_token_present": true` | `0` |
| 上传 | `POST /v1/uploads?filename=05_service_sla_manual.docx&provenance=synthetic_demo` | **201**，`"upload_id": "upl-a7fe8301ec5e0ea2d76de591b778ec5601f89788"`，`"status": "queued"`，`"review_required": true`，发送 36907 B | `0` |
| 解析 | `.venv/bin/python -m ingestion.upload_worker --once` | `returncode: 0`；`[upload_worker] ... start mode=once workers=1 deadline=45`（已有 watch worker 在跑，`claimed` 数随轮次变化，以详情为准） | `0` |
| 轮询详情 | `GET /v1/uploads/{id}` | `"status": "succeeded"`，`"searchable": false`，`"index_state": "not_approved"` | `0` |
| 复核 | `POST /v1/uploads/{id}/approve` | `"review_state": "approved"`，但 `"searchable": false`，`"index_state": "no_chunks"`，`"index_note": "UPLOAD_HAS_NO_CHUNKS"`，`"block_count": 6`，`"locator_count": 6` | `0` |
| 索引 | 详情字段 `vector_index` | **`"deferred"`**，`automatic_index = not_done` | `0` |
| 契约定位 | `GET /v1/documents/doc-upload-…/contract` | `block_count=6`、`block_locators_in_page=6`、`sampled locator_kind = ["office_object"]`，示例 `{"locator_kind":"office_object","part":"word/document.xml","paragraph":0}`；`locator_kinds_check.ok = true` | `0` |
| 提问（显式文件范围） | `POST /v1/chat` + `scope.document_refs` | `status = FAILED_WITH_EVIDENCE`，`error_code = DOCUMENT_SELECTION_UNAVAILABLE`，`citation_count = 0` | `0`（HTTP 200，失败在业务码） |
| 提问（默认范围） | `POST /v1/chat` `mode=rag` | `status = ABSTAINED`，`error_code = INSUFFICIENT_EVIDENCE`（`eligible_chunks = 169`，新文件 0 切片），`citation_count = 0` | `0` |
| **对照组**（已入库文档） | `POST /v1/chat`「一般商品退款多久到账？」 | **`COMPLETED`**，`citation_count = 1`，`title = 04_售后服务与退款管理制度`，`locator_kinds = ["pdf_region"]`，`locators[0].bbox = [62.7, 351.8, 220.2, 366.8]` | `0` |
| 报告落盘 | `reports/qa_samples_v1.json` | 顶层 `failures` **3 条**：`automatic_index_not_implemented`、`qa_not_completed`×2（`DOCUMENT_SELECTION_UNAVAILABLE`、`INSUFFICIENT_EVIDENCE`，对照组 `COMPLETED` 未计入）；`not_done` 4 条，含 `"automatic_index": "not_done"` | `0` |

### D. 测试

| 检查 | 分子/分母 | 关键输出（原样摘录） | 退出码 |
|---|---:|---|---:|
| 隔离回归（仅自有测试文件） | **16/16 通过** | `Ran 16 tests in 0.444s` / `OK`；summary：`{"tests": 16, "failures": 0, "errors": 0, "metadata_isolated": true, "source_database_modified": false, "commit": "e4e2ebf", "ran_at": "2026-10-09T14:39:27.221850+00:00", "failed_tests": []}` | `0` |

## 失败样本

1. **新文件的问答与引用点击闭环 `not_done`（本轮最主要缺口，未伪装）。**
   - 现象：approve 后 `searchable=false`、`index_state=no_chunks`；提问分别得到
     `DOCUMENT_SELECTION_UNAVAILABLE` 与 `INSUFFICIENT_EVIDENCE`，`citation_count=0`。
   - 根因：`ingestion/upload_pipeline.py` 的 `VECTOR_INDEX='deferred'`，上传路径只写元数据与契约证据，
     **不写 `chunks`/Qdrant 行**；`datamind.knowledge` 只读 `chunks`。
   - 因此**「解析 → 复核 → 契约定位」已闭环，「索引 → 问答 → 引用点击」未闭环**。
2. **11 份样本只有 1 份入库（`samples_in_store: 1/11`）。**
   - 其余 10 份没有经过解析/索引，其 23 条断言中的 21 条只做了**离线可判定性复核**，不是模型回答；
     报告对每条问题写 `qa_verdict: "not_attempted"`、`qa_reason: "no_chunks_in_store_vector_index_deferred"`。
   - 后果：审计 §7.3 要求的「固定多文档/多模态端到端集」只完成**样本集与判定层**，
     **端到端问答准确率仍未测量**（不能拿 23/23 断言通过冒充问答准确率）。
3. **对照组的引用定位只在 PDF 上有 `bbox`；上传的 DOCX 没有可检索引用。**
   - 对照组 `locator_kinds=["pdf_region"]` 且带 `page_no/bbox`（可画框）；
     新文件是 `office_object`（`part/paragraph`），只能给到「第几段」，且因为没有索引，问答阶段根本到不了引用。
4. **界面无上传/审批入口。** `web/app.js` 里没有 `/v1/uploads` 调用；E4「交互 Demo」这条链目前只能靠 API
   与 `docs/QA_SAMPLE_GUIDE.md` §2 的 curl 步骤演示。本 WP 不允许改 `web/`，故如实登记为未做。
5. **已批准的上传不会出现在匿名文档列表（`samples_published_visible: 0`）。**
   `datamind/visibility.is_published` 要求 `status='indexed'`，而 approve 只在有切片时才置 `indexed`。
   这是「无切片不可问答」的正确表达，但意味着它**无法在前端文件选择器里被选中**。
6. **DXF 样本的 `cad_entity` 期望定位未被本轮实测覆盖**（该样本未入库），只做了 manifest 声明与
   `parse_contract.LOCATOR_KINDS/LOCATOR_REQUIRED_FIELDS` 对齐断言。
7. **扫描件 OCR 问答未验证。** 两份扫描件故意无文本层（提取长度 0/1），本轮没有跑 OCR 摄取，
   因此「扫描版 PDF 的问答」仍是未测能力。
8. **本轮自己抓到并修复的缺陷（如实登记）**：DXF 生成最初**不是**跨进程确定的。
   `ezdxf` 的 `CLASSES`/`OBJECTS` 记录顺序来自 Python set 迭代，随 `PYTHONHASHSEED` 变化，
   于是「冻结 sha256」只描述生成它的那个进程。第一版 `test_regeneration_reproduces_the_frozen_bytes`
   在隔离回归里以 1 项失败抓到了它（同一个进程内两次生成一致，跨进程不一致）。
   修法：`_save_dxf` 对两个 section 的记录按 `(class/handle)` 规范排序（`OBJECTS` 根字典保持第一条，
   否则 `ezdxf.readfile` 报 `invalid root dictionary entity`），并用
   `PYTHONHASHSEED=0/1/2/4242/12345/999` 六个进程复核 11/11 字节一致后重新冻结 manifest
   （digest 由 `dfba9eb8…` 变为 `8080b273…`）。这条缺陷说明该测试**必须**跑在
   与生成脚本不同的进程里才有意义。
9. **本轮自己抓到并修复的脚本缺陷（如实登记）**：`scripts/eval_qa_samples.py` 第一版对报告做
   「按 section 合并」时**每次都从磁盘重读**报告，于是 `--all` 里后跑的 `--http` 段会把本次运行刚算好的
   `stored` 段整段丢弃、换回上一轮磁盘上的旧副本。可见症状是：重新冻结 manifest 后报告里仍是旧
   `digest`，以及逐样本缺 `status`。已改为「启动时读一次 → 内存合并 → 末尾只写一次」，重跑后
   `digest` 与 `data/qa_samples/manifest.json` 一致（`match True`）、逐样本字段齐备。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"

# 1) 样本集：首次冻结（已冻结后再跑会拒绝覆盖；--force 才会重写）
.venv/bin/python data/qa_samples/build_samples.py            # EXIT=0
.venv/bin/python data/qa_samples/build_samples.py --check    # EXIT=0，{"ok": true, "problems": []}

# 2) 两条评测路径 + 对照组问题（真实 HTTP；约 40 次调用，含 3 次上游模型）
.venv/bin/python scripts/eval_qa_samples.py --all            # EXIT=0
.venv/bin/python scripts/eval_qa_samples.py --stored         # 仅一致性审计，EXIT=0
.venv/bin/python scripts/eval_qa_samples.py --http --no-control   # 跳过对照组

# 3) 自有测试文件（隔离回归；会克隆元数据库）
.venv/bin/python scripts/test_isolated.py --pattern test_qa_samples.py   # EXIT=0，Ran 16 tests / OK
```

失败样本 1 的最小复现（只读命令，写操作只有上传自身）：

```bash
CSRF=$(curl -s http://127.0.0.1:8000/v1/auth/me | .venv/bin/python -c "import json,sys;print(json.load(sys.stdin)['csrf_token'])")
curl -s -X POST "http://127.0.0.1:8000/v1/uploads?filename=05_service_sla_manual.docx&provenance=synthetic_demo" \
  -H "X-CSRF-Token: $CSRF" --data-binary @data/qa_samples/05_service_sla_manual.docx
.venv/bin/python -m ingestion.upload_worker --once
# 用上一步返回的 upload_id
curl -s -X POST "http://127.0.0.1:8000/v1/uploads/<upload_id>/approve" -H "X-CSRF-Token: $CSRF" | head -c 300
```

## 诚实边界（不能据此声称什么）

1. **不能声称新文件问答闭环已打通。** 索引未接入，`automatic_index = not_done`；「提问 → 引用定位」在新文件上
   是**拒答**而不是正确答案，也不是「原地可以点击」。
2. **不能声称多文档/多模态问答准确率。** 11 份样本、23 条断言是**样本与断言层**；23/23 只是「断言可离线重算」，
   不是模型答对率、不是 F5 的准确率数字。
3. **不能声称多模态能力。** 上游只有文本模型；扫描件只证明「能生成无文本层的扫描样本」，OCR 问答未跑。
4. **27/27 一致只说明「规则一致」。** 它校验的是 API 报告的可检索性与
   `upload_pipeline.searchability` 重算结果一致；不等于答案正确、也不等于索引正确（26 份 `indexed` 的检索质量
   由 WP1 的 RAG 评测负责，不在本卡范围）。
5. **「匿名只读」有限定。** 本机 loopback 被 `datamind/auth.py` 视为 demo 管理员，因此
   `GET /v1/documents/{id}/contract` 对未发布上传也返回 200；真正的匿名（非 loopback）读取未在本轮验证。
6. **样本是自建合成集**，且扫描件依赖 macOS 系统字体
   `/System/Library/Fonts/Supplemental/Arial Unicode.ttf`（sha256 记入 manifest）。换机器若字体不同，
   `--force` 重新生成会得到不同字节；冻结领域的判据是 manifest 里的 sha256，而不是「可任意重生成」。
7. **未做 git 写操作，未改既有文件。** 本轮只新增
   `data/qa_samples/**`、`scripts/eval_qa_samples.py`、`docs/QA_SAMPLE_GUIDE.md`、`tests/test_qa_samples.py`、
   `reports/qa_samples_v1.json`、`reports/cards/wp9_qa_samples.md`。
8. **副作用如实声明**：
   - `POST /v1/uploads` 由服务自身把样本暂存为 `data/uploads/development/upload-e8b99022….docx`，
     并写入 `document_assets`/`ingest_jobs`/`document_contract_blocks` 各 1 行（**这是 `--http` 路径本身的要求**，
     未删除任何既有数据、未清库、未跑全量摄取、未对零售/工业演示库写入）。
   - `scripts/test_isolated.py` **按设计**无条件重写 `reports/latest_regression.json`。本轮最后一次运行前该文件
     sha256 = `086e8d694634d9c01d67ee31783474d9a5fb998331c110794446eb8b65313a8b`
     （内容为他人最近一次运行结果 `"tests": 8, "ran_at": "2026-10-09T14:39:19.789388+00:00"`，非本卡产物），运行后我按原字节恢复，
     恢复后 sha256 相同并再次核对。该文件被并发的其他工作包反复重写，其相对 HEAD 的差异在本轮开始前就存在，
     不是本卡引入的。
   - `scripts/eval_qa_samples.py --http` 会真实调用上游模型 3 次（2 次新文件、1 次对照），报告里逐条给出
     `run_id`、状态、`error_code` 与耗时。

## 评分联动

| 官方/计划项 | 本卡作用 | 目标分 | 当前判断 |
|---|---|---|---|
| E4 交互 Demo（5） | 上传→解析→复核→契约定位可脚本化演示；**界面入口与索引问答缺失** | 3 → 5 | **部分**：新增 API 主线与演示脚本，但 UI 没有上传/审批/新文件问答入口，E4 的 +2 未兑现 |
| E12 线上演示（5） | `docs/QA_SAMPLE_GUIDE.md`：界面 4 个入口 + 新文件 6 步 + 9 条失败话术 + 8 分钟主线 | 5 | **有材料**：脚本可复跑；分值由评委 |
| F5 多文档/多模态 RAG（10） | 提供 11 份/6 格式/23 断言的**冻结自建集**与判定口径 | 5 → 7 | **未兑现**：仍是单文档范围；端到端多文档问答准确率未测（`not_attempted`） |
| F12 现场演示（5） | 同上（含「索引未接入」的现场口径，避免现场翻车） | 3 → 5 | **部分** |
| E3/F3 可解释性 | 对照组引用带 `pdf_region(bbox)`；上传件带 `office_object(part/paragraph)` 契约定位 | 保持 | **有材料**：定位类型与字段可核对 |
| WP9 验收「新文件走完全链路 + 引用可点击」 | 见上 | — | **未达成**：链路到复核为止，索引与问答闭环 `not_done` |

> 本卡只声明已完成的三件事：**11 份多格式样本集与冻结 manifest**、**可检索性判定一致性（27/27）**、
> **1 份新文件的解析/复核/契约定位全链证据**。不据此声称任何问答准确率、自动索引能力或 WP9 整体完成。
