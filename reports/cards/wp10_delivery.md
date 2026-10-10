# WP10 交付材料成套化（Word/PDF/PPT + ZIP 清单 + 创新自述）

| 字段 | 值 |
|---|---|
| **WP** | WP10「交付材料成套化」（设计报告 / 技术说明书 / 答辩 PPT / 创新自述 / 官方核对清单 / 机读清单 / ZIP 计划） |
| **日期** | 第一轮 2026-10-09 22:2x CST；**第二轮（修复轮）交付同步 2026-10-09 23:4x CST**（北京时间，`date` 机器时钟；提升计划自署 2026-10-10，见 reports/cards/gaps_ledger.md §3.3 P8）。第二轮一页结论卡见 [r2_delivery_sync.md](r2_delivery_sync.md) |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`，只读） |
| **API 版本** | `0.39.0`（`curl -s http://127.0.0.1:8000/openapi.json` → `{'title': 'DataMind Agent', 'version': '0.39.0'}`；paths=75） |

---

## 问题

审计 §1 L17 与 §10 明确指出：**当前估分评价技术内容，不代表可以原样提交**；设计报告/实现说明书的 Word 与 PDF、最终 ZIP、答辩 PPT 及加分自述**尚未形成合规成套交付物**。官方映射表 D1/D9/D10 三项判「未实现」、D2/D11 判「部分」。提升计划 §WP10 把这一项定为 P0：**保住提交资格**。

本轮要回答的问题只有一个：**这些文件能不能真的生成出来，并且经得起结构与合规检查**——而不是「计划里写了要交」。

## 做法

1. 先加载 office-docx / office-pptx 技能，按技能要求使用仓库 `.venv/bin/python`（python-docx / python-pptx / pymupdf）与 **DSH 自带 LibreOffice Kit**（绝对路径，未使用 PATH 里的 soffice）。
2. 用只读实测与既有报告收集真实数字：`/health`、`/openapi.json`、只读 SQL（`docker exec datamind-postgres psql -U datamind_app`）、`reports/*.json`、`docs/`、`PROGRESS.md`。所有引用数字在文档内标注来源路径。
3. 生成 6 份交付物 + 3 份治理文件（清单/机读清单/结论卡），生成脚本集中在 `交付/_build/`；**不修改任何既有代码或文档**。
4. 匿名检查内置于 PPT 生成脚本：对每页所有 `text_frame` 与表格单元格做单位名黑名单 + 称谓提示词正则扫描。
5. ZIP 只做 **dry-run**：`scripts/build_delivery_zip.py` 默认不落盘，`--write` 还需再给 `--i-know-this-writes-a-zip` 确认标志。

## 数据与集

| 来源 | 版本/规模 | 只读实测 |
|---|---|---|
| 官方赛题 PDF | 第 8–12 页提交要求、第 10–14 页评分表 | 分值档位原文引自 docs/SCORE_GAP_LEDGER.md §1 |
| 元数据库 | `datamind_meta` | `evaluation_cases`：nl2sql=14、rag=12、robustness=4；`document_assets`：indexed=27、parsed=2；chunks=169、facts=23；evidence_nodes=1456、evidence_edges=1359 |
| 向量库 | Qdrant `datamind_knowledge` | `points_count=169` |
| 运行服务 | `http://127.0.0.1:8000` | `status=ok, database=ready, knowledge=ready, vector_store=ready, document_count=27`；paths=75 |
| 既有报告 | `reports/*.json` | nl2sql 14/14；rag 10/12；robustness 4/4；schema 表 31/31、列 28/28、指标 4/4、路径 6/6；scale_v1 4+4 规模点；vcar_ablation 四臂 16 例 |
| 交付物生成 | 本轮新建 | 6 份二进制交付物 + 3 份治理文件；生成脚本 6 个在 `交付/_build/` |

## 结果（分子分母）

| 检查 | 分子/分母 | 关键输出（原样摘录） | 退出码 |
|---|---:|---|---:|
| 交付物真实生成 | **6/6** | 两个 DOCX、三个 PDF、一个 PPTX 全部写出 | 0 |
| DOCX → PDF 转换 | **3/3** | LibreOffice Kit 返回 `{"backend":"native","missingFonts":[],"outputPath":"…"}` | 0 |
| 结构检查（check_office.py） | **4/4 通过** | `docx1 0 pass`、`docx2 0 pass`、`pptx 0 pass`、`docx4 0 pass`；结果存 `reports/office_checks.json` | 0 |
| 设计报告页数 | **13 页**（限 ≤20） | `pdf1 pages= 13`；第 1–2 页为核心亮点预览页（6 个亮点框） | 0 |
| 技术说明书页数 | **12 页**（限 ≤30） | `pdf2 pages= 12` | 0 |
| 答辩 PPT 页数 | **20 张**（限 ≤20） | `slides: 20`；含演示流程页与失败边界页 | 0 |
| 创新自述页数 | **4 页**（限 ≤5；第二轮重写后实测） | `pdf4 pages= 4`（第一轮为 3 页）；DOCX 源同时留存（46 485 B） | 0 |
| 第二轮：04 重写后结构检查 | **1/1 通过**（累计 **4/4**） | `docx4 exit=0 verdict=pass`（`--contains 突破与创新性自述 / 本轮未产出 / binding_ablation.json`） | 0 |
| 第二轮：04 渲染 + 边界检查 | **4/4 页渲染**，文本块越界 **0/158** | render `missingFonts: []`；pymupdf 边界检查 `pages=4, text_blocks=158, overflowing=0` | 0 |
| 第二轮：01/02/03 未被改动 | **3/3 sha256 与第一轮一致** | `reports/office_checks.json` 的 `sha256[k] == sha256_round1[k]`（docx1/pdf1/docx2/pdf2/pptx 全为 True） | 0 |
| 第二轮：证据状态逐份点名 | **2 存在 / 1 未产出** | `cross_source_offline.json` 存在（13 798 B）、`binding_ablation.json` 存在（219 605 B）、`cross_source_live.json` 未产出（`ls` exit 1） | 0 |
| PPT 匿名检查 | **黑名单命中 0/0、称谓提示命中 0/0** | `blocklist hits: 0` / `name-hint hits: 0`；明细 `交付/_build/anonymity_scan.json` | 0 |
| ZIP dry-run 计划 | **427 文件 / 10,573,573 字节（10.08 MiB）** | `[dry-run] 将包含 427 个文件、10573573 字节（10.08 MiB）`；**zip_written=False** | 0 |
| 官方 ZIP 要求逐项自评 | **4 项 satisfied / 1 项 satisfied_by_list / 2 项 partial** | 源码、schema、依赖锁、启动脚本 OK；样本与文档项标 partial 并写明缺口 | 0 |
| ZIP 真包 | **0/1（本轮明确不做）** | `--write` 未使用；`written_this_round: false` | — |
| 干净环境演练 | **0/2（未做）** | 本轮硬性禁止重建容器/库 | — |
| 视觉检查 | **0/6（未做）** | 未生成渲染预览图，版式风险未消除 | — |
| 本轮新增官方得分 | **0** | 本卡只声明交付物形态，不作任何得分结论 | — |

## 失败样本

1. **【已由第二轮修复】证据状态表述过时（第一轮复查 refuted 半条）**——原写法称 `reports/binding_ablation.json` 与 `reports/cross_source_offline.json`「均不存在」，并把自述措辞描述为「待本轮复查确认」。
   - 复查实测（`review_round1_evidence.md` §6.1 C130/C131）：`cross_source_offline.json` **当时已存在**（13 798 B，22:41 落盘，晚于本卡 22:2x 的写作时刻）、`binding_ablation.json` 确实缺失；自述 docx 全文该短语出现 **0 次**。故卡片与交付件描述均与实际不符。
   - 第二轮处理：**重写 04 自述**（4 页），改为**逐份点名**文件真实状态 + 记录数 + 复查判定，并写明核对时刻；本卡与 `docs/DELIVERY_CHECKLIST.md`、`交付/DELIVERY_MANIFEST.json` 同步更正。**未编造任何绑定准确率或错误绑定率。**
   - 第二轮核对（2026-10-09 23:45 CST）：`cross_source_offline.json` 存在 13 798 B；`binding_ablation.json` 存在 219 605 B（23:36 落盘，**同轮自报、未经独立复查**）；`cross_source_live.json` 未产出。
2. **check_office.py 首轮对设计报告报 fail（exit 1）** → 原因是我传入的断言文本 `成果与创新` 在该文档中不存在（属断言写错，不是文档缺陷）；改为文档中真实存在的 `多源融合与证据绑定` 后 `verdict=pass`。该次失败已保留在本卡与 `reports/office_checks.json` 的 `checks.docx1` 中。
3. **页面溢出被真实修掉**：设计报告首轮 14 页且第 3 页挤入亮点预览内容（违反「第 1–2 页预览」要求）→ 压缩导读区与高亮框行距后回到 13 页，且亮点框恰好收在第 1–2 页。
4. **PPT 匿名扫描首轮 1 命中**：`先生成证据图` 中的子串「先生」被称谓提示词命中（**误报**）→ 改写为「优先构建证据图」，复检 0 命中。这属于文案修改，不是删除命中项。
5. **首次 dry-run 的样本计数错误（已修正，非隐藏）**：初版把 `data/qa_samples/` 与 `data/evaluation/extensible_field_v1/materials/` 的 DOCX/XLSX/PPTX 记为「0 份、格式不完整」。复核 `git ls-files` 后确认这些文件在**工作区存在但未被跟踪**，故更正为「六类格式、数量远超 ≥10 份」，同时**新增未跟踪文件清单**提示打包方先决定是否纳入版本控制。
6. **ZIP 文件数在测量期间漂移**：同一命令先后得到 422 → 424 → 425 → 427 个文件（工作区被其他工作包并发写入，新增 `datamind/binding.py`、`datamind/evidence_graph.py`、`datamind/frame.py`、`data/qa_samples/` 等）。已在机读清单中写明该数字是「运行 dry-run 那一刻的快照」。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"

# 只读基线
git rev-parse --short HEAD                                   # → e4e2ebf  (exit 0)
curl -s http://127.0.0.1:8000/openapi.json | .venv/bin/python -c "import sys,json;d=json.load(sys.stdin);print(d['info'],len(d['paths']))"   # 0.39.0 / 75
curl -s http://127.0.0.1:8000/health                         # status=ok, document_count=27
docker exec datamind-postgres psql -U datamind_app -d datamind_meta -tAc \
  "select suite||'='||count(*) from evaluation_cases group by suite order by suite"        # nl2sql=14 rag=12 robustness=4

# 生成 6 份交付物（每条 exit 0）
.venv/bin/python 交付/_build/make_01_design_report.py
.venv/bin/python 交付/_build/make_02_tech_spec.py
.venv/bin/python 交付/_build/make_03_deck.py          # 同时输出匿名检查明细
.venv/bin/python 交付/_build/make_04_innovation.py
rm -f "交付/01_设计报告_DataMind_Agent.pdf" "交付/02_技术实现说明书_DataMind_Agent.pdf" "交付/04_突破与创新性自述.pdf"
NODE="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node"
CLI="/Applications/DeepSeek Harness.app/Contents/Resources/app.asar.unpacked/dsh/node_modules/@deepseek-ai/libreoffice-kit/lib/cli.js"
"$NODE" "$CLI" convert --input "交付/01_设计报告_DataMind_Agent.docx"      --output "交付/01_设计报告_DataMind_Agent.pdf"
"$NODE" "$CLI" convert --input "交付/02_技术实现说明书_DataMind_Agent.docx" --output "交付/02_技术实现说明书_DataMind_Agent.pdf"
"$NODE" "$CLI" convert --input "交付/04_突破与创新性自述_DataMind_Agent.docx" --output "交付/04_突破与创新性自述.pdf"
.venv/bin/python 交付/_build/finalize_measure.py        # 结构检查 + 回填页数 + 写 reports/office_checks.json

# ZIP 计划（dry-run，不落盘）
.venv/bin/python scripts/build_delivery_zip.py --json tmp/wp10/zip_plan.json     # exit 0
.venv/bin/python 交付/_build/make_manifest.py          # 写 交付/DELIVERY_MANIFEST.json
```

### 第二轮（修复轮）追加的复现命令（全部只读 / 不写演示库）

```bash
cd "/Users/caicai/DataMind Agent"

# 1) 证据状态逐份点名（对照第一轮复查 §6.1 的 refuted）
ls -l reports/binding_ablation.json reports/cross_source_offline.json reports/cross_source_live.json
#   → 前两份存在；cross_source_live.json: No such file or directory（exit 1）

# 2) 重写 04 自述并转 PDF（4 页）
.venv/bin/python 交付/_build/make_04_innovation.py                              # exit 0
NODE="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node"
CLI="/Applications/DeepSeek Harness.app/Contents/Resources/app.asar.unpacked/dsh/node_modules/@deepseek-ai/libreoffice-kit/lib/cli.js"
rm -f "交付/04_突破与创新性自述.pdf"
"$NODE" "$CLI" convert --input "交付/04_突破与创新性自述_DataMind_Agent.docx" --output "交付/04_突破与创新性自述.pdf"
.venv/bin/python -c "import pymupdf;d=pymupdf.open('交付/04_突破与创新性自述.pdf');print('pages=',d.page_count)"   # pages= 4

# 3) 结构检查 4/4 + 重新实测页数/sha256（合并进 reports/office_checks.json，保留 round1 字段）
.venv/bin/python /Applications/DeepSeek\ Harness.app/Contents/Resources/runtime/office-skills/scripts/check_office.py \
  "交付/04_突破与创新性自述_DataMind_Agent.docx" --contains 突破与创新性自述 --contains 本轮未产出 --contains binding_ablation.json
#   → verdict=pass, exit 0

# 4) 刷新机读清单（含 round2_evidence_index，逐份 sha256）
.venv/bin/python 交付/_build/make_manifest.py
```

## 诚实边界（不能据此声称什么）

1. **不能声称任何官方得分、名次或提交资格已获得**：本卡只声明文件已生成并通过结构检查；官方无「缺交材料扣几分」的统一公式，本卡不编造。
2. **不能声称版式没问题**：结构检查只验证 ZIP/XML 完整、关系可解析、段落/表格/幻灯片计数与必需文本存在；本轮**未做逐页视觉检查**（未生成渲染预览图），分页/裁切/字体渲染风险未消除。LibreOffice 分页也可能与 Microsoft Word/PowerPoint 不一致。
3. **不能声称 PPT 绝对无单位名/人名**：检查只覆盖文本框与表格文本，**不覆盖嵌入图片内文字**，也不覆盖母版/主题文本。
4. **不能声称「新环境可一键启动」**：本轮硬性禁止重建容器/库，未做干净空库演练。
5. **不能声称 ZIP 已交付**：`--write` 未执行，`written_this_round=false`；只有计划与脚本。
6. **不能声称「不同类型样本已完整覆盖」**：六类格式确实存在于工作区，但 `data/qa_samples/`、`data/evaluation/extensible_field_v1/materials/` 为**未跟踪文件**；打包前须先决定纳入方式。
7. **不能把本卡数字当作能力成绩**：13/12/20/3 页是**排版页数**，20 张是幻灯片数，与模型准确率、召回率无关。
8. **不能声称创新点已被独立验证**：跨源绑定的三臂消融数字（错误绑定率 32/40 = 0.800 → 0/17 = 0.000、冲突 P/R 25/25、误修复率 0/9）**已落盘**于 `reports/binding_ablation.json`，硬判定 `meets=true`；但该报告是**自建冻结陷阱集 + 确定性 stub（`model_calls=0`）**的机制对照，且**在同一轮内自报、产出晚于第一轮复查结论冻结时刻，尚无独立复查者复跑**——因此只能按「实用创新与转化价值」举证，**不得主张技术突破领先性或业界领先**。跨源 `--live` 端到端仍无成功记录（`reports/cross_source_live.json` 未产出）。
9. **本轮未执行 git 写操作**；未改 `PROGRESS.md`/`README.md`/`requirements*.txt`/`db/migrations/`；未重启容器、未停止 8000 服务、未跑全量摄取或全量隔离回归；未对演示库（`demo_retail` / `demo_industrial`）写任何数据。**唯一删除动作**：LibreOffice Kit 拒绝覆盖已存在输出，故重生成 PDF 前会 `rm -f` 该 PDF（见「复现命令」），随后立即由 DOCX 源重新生成；未删除源代码/文档/数据文件。

## 评分联动

| 官方项 | 本卡作用 | 目标分 | 当前判断 |
|---|---|---|---|
| E11 技术文档完整性/规范性/可读性（10） | Word + PDF 成套、按评分项组织、含表格与失败说明 | 8 | **形态已具备**；「图表规范/工业水准」由评委判定 |
| F10 决赛技术文档（5，单档） | 初赛口径两份 DOCX/PDF 已成稿 | 5 | **部分**：决赛 ≤50 页深化稿未做 |
| D1/D2 设计报告与实现说明书（提交物） | 两份 DOCX + 两份 PDF，页数 13/12 ≤ 建议上限 | — | **已具备**（D1/D2 由「未实现/部分」转为「已具备」） |
| D9 答辩 PPT（≤20 页、无单位人名） | 20 张；黑名单与称谓提示命中均为 0 | — | **已具备**（形态）；现场表现仍不由仓库决定 |
| D11 决赛 ZIP | dry-run 计划 427 文件 / 10.08 MiB + 打包脚本 | — | **部分**：未打真包、未做干净环境验证 |
| C1《突破与创新性自述》（≤5 页） | **4 页**独立 PDF + DOCX 源，含定位/贡献/举证/边界/评审价值 | — | **形态已具备**；证据状态已逐份点名（`cross_source_offline.json` 已 supporting、`binding_ablation.json` 已落盘但未经独立复查、`cross_source_live.json` 未产出），自述如实标注 |
| F11 现场答辩 / F12 现场演示（各 5） | 演示流程页与失败边界页已备 | 各 5 | **未兑现**（现场项，仓库无法产出） |
| E12 线上演示（5） | 交付物与演示脚本可作材料 | 5 | **部分**（仍需截图/视频组织） |

> 本卡为工作产物，不是官方成绩；所有目标分均为「完成并通过验收后经评委认可」的条件值，且**与本卡无关地取决于后续工作包**。
