# 赛题交付就绪性审查（只读）

审查日期：2026-10-08 · 审查对象：`/Users/caicai/DataMind Agent`（工作树 + 运行中的 PostgreSQL/Qdrant）
审查方式：**只读**。除本文件外未创建/修改/删除任何文件；未打包、未提交、未运行被禁脚本。

---

## 0. 审查边界与结论速览

**一句话结论：技术实现已相当完整（API 0.37.0，隔离回归 845/845），但"赛题交付物"本身基本尚未开始——交付包被项目自己的路线图明确标注为"延后"。** 现仓库**不能直接打包提交**：缺少设计报告/技术实现说明书（Word+PDF）、答辩 PPT、ZIP 打包；存在真实 API 密钥、另一 agent 的产物（部分已被 git 提交）、2.2 GB 工作树杂物；且 `requirements.lock` 不足以在全新机器复现。

**判定分布（13 项交付物）**：达标 3 · 部分达标 3 · 不达标 5 · 判据不清/无法判定 2。

---

## 1. 判据来源（逐条摘录 + 出处）

### 1.1 已发现的关键事实：判据来源本身与官方要求脱节

任务指定判据来源为 `技术文档/DataMind_Agent_技术设计说明书_V1.3.md` 与 `README.md`。**这两份文档都没有完整记载赛题交付要求**，而且文档自己承认了这一点：

- `技术文档/DataMind_Agent_技术设计说明书_V1.3.md:51`：「赛题依据沿用 V1.2 记录的用户需求：基础为单表查询与单文档检索；中级初赛至少攻克官方九项中的一项、决赛三项以上；高级提交创新自述；采用国产模型。**正式交付前核对官方完整要求，本文不新增官方分值、编号或比赛日期。**」
- `技术文档/DataMind_Agent_技术设计说明书_V1.3.md:606`：「[赛题原始资料](<../赛题/8 赛题八：….pdf>)：**正式提交前核对**。本次沿用旧稿需求记录，未重新核定评分或官方九项编号。」
- `技术文档/DataMind_Agent_技术设计说明书_V1.3.md:61`：「T1/T2/T3 是内部追踪代号，**须映射官方九项后才能计入专项数量**。」

因此：**页数上限、Word/PDF 双格式、≥10 份样本、≥10 条测试用例、多轮对话、效率分析等硬指标在 V1.3/README 中均无记载**（见 §1.4「判据不清」）。本报告以仓库内随附的官方赛题 PDF（`赛题/8 赛题八：…（中电云计算技术有限公司）.pdf`，已跟踪，14 页）作为**可核验的官方判据**，并同时标注项目自身文档的记载位置。

### 1.2 官方赛题判据（来源：`赛题/8 赛题八：….pdf`，PDF 页码）

| # | 判据（原文摘录/要点） | 出处 |
|---|---|---|
| J1 | 「（1）技术文档——设计报告：提交 **Word 版本及 PDF 版本各 1 份**，总篇幅**建议不超过 20 页**（含封面、目录、正文及附录）。封面或目录后的**第 1-2 页必须为"项目核心亮点预览页"**」 | PDF p8 |
| J2 | 「（2）技术文档——技术实现说明书：格式要求同上，**总篇幅建议不超过 30 页**」，须系统阐述 7 个模块：总体架构 / NL2SQL（Schema Linking、要素识别、SQL 生成与校验）/ RAG（解析与切片、向量检索、生成融合）/ 多源异构融合推理 / 可解释性实现 / 中级任务攻克方案 / 系统测试与性能评估 | PDF p9 |
| J3 | 「（3）技术数据：作品原始文件（**ZIP 数据包**），应包含：**完整项目源代码（需包含清晰的目录结构说明和 README 文件）**；**数据库 schema 定义文件**；**知识库文档样本（不少于 10 份，涵盖不同类型文档格式）**；**模型权重文件（若适用）**；**运行环境配置说明（包括依赖包列表及版本号）**；**可执行 Demo 的快速启动脚本**」 | PDF p9 |
| J4 | 基础任务：单表 NL2SQL 正确执行；单文档 RAG 检索问答；来源展示（SQL / 引用片段）；可交互 Demo | PDF p4、p10 |
| J5 | 基础任务评分口径：「采用**至少 10 个测试用例**进行客观评估」（NL2SQL 10 分、RAG 10 分）；NL2SQL 执行准确率 ≥70/80/90% 分档；RAG 忠实度同档 | PDF p10 |
| J6 | 中级任务：「每攻克 1 个中级任务基础得分 5 分…总分不超过 15 分」；初赛 ≥1 个、决赛 ≥3 个 | PDF p5、p10 |
| J7 | 「系统效率…①各模块时间复杂度理论分析；②**至少两种不同数据规模**（如 1 千行、1 万行）的实测或预估响应性能对比；③瓶颈及优化策略」= 5 分 | PDF p10 |
| J8 | 「多轮对话上下文保持能力」= 5 分（「具备多轮能力得 3 分，连续 5 轮以上准确得 5 分」） | PDF p10 |
| J9 | 初赛总分 100：「参赛作品**必须至少完成 1 个中级任务**方可视为有效参赛作品」 | PDF p11 |
| J10 | 决赛：「（1）**答辩 ppt**…**总页数建议不超过 20 页**」；「（2）技术文档（决赛版）…**篇幅建议不超过 50 页**」；「（3）技术数据（决赛版）…ZIP」；「（4）**突破与创新性自述**…PDF…**不超过 5 页**」 | PDF p11–p12 |
| J11 | 平台建议：Python + Vue；数据集建议开源（OHR-Bench、Chinook）；「大模型建议订购**国产 Token**」 | PDF p8 |
| J12 | 高级任务（20 分加分，仅决赛）：「需在提交材料中专门撰写一份《突破与创新性自述》报告（篇幅不超过 5 页）」 | PDF p7–p8 |

> 注意 J1/J2/J10 的页数是「**建议**不超过」，非强制；但「Word 版本及 PDF 版本各 1 份」与「第 1-2 页必须为核心亮点预览页」是硬要求。

### 1.3 项目自身文档记载的交付判据（行号）

| # | 判据 | 出处 |
|---|---|---|
| P1 | 「初赛交付设计报告、技术实验说明书与可运行软件包；决赛准备实时演示、专项证据和高级创新自述。**大权重/外部数据不随包时列明合法获取、缓存和网络依赖；未经验证不宣称离线。**」 | V1.3:581 |
| P2 | P4 交付门禁完成条件：「**新机初始化/启动与演示可复现；版本/数据/模型/报告一致；失败/回放明示**」 | V1.3:577 |
| P3 | 「**国产 manifest**、独立模型/效率测试、官方专项映射和交付包」= P4 待办 | V1.3:577 |
| P4 | 「**真实授权模板与依据、DWG/图纸 PDF、完整 P1/M6、V-CAR 与正式竞赛包仍未完成**」 | V1.3:579 |
| P5 | 「正式交付与多用户部署 | **未完成** | 官方专项核对、模型 manifest、新机复现与认证部署」 | V1.3:645 |
| P6 | 内部样本量目标：「扩样计划可沿用约 **40 份核心文件**（15 工艺、15 报告、10 图纸）和约 **60 条问题**的建设目标，**当前尚未达成**」 | V1.3:399 |
| P7 | 「每次实验 manifest 保存代码 commit/哈希、源集合、Catalog、标签、解析/转换配置、**模型权重/来源/规模**、提示词、硬件与缓存状态」 | V1.3:363 |
| P8 | 「沿用竞赛国产模型要求，分别登记语言、视觉、OCR、Embedding 和重排的**研发来源、模型卡/许可、版本、权重哈希及用途**」 | V1.3:530 |
| P9 | 「**赛题交付包（ZIP）等前置技术任务完成后再做**：在此之前不投入交付打包、答辩 PPT、官方专项核对与《突破与创新性自述》。」 | PROGRESS.md:27 |
| P10 | 「D1 | 赛题交付包、答辩材料与官方专项核对 | **延后**」 | PROGRESS.md:176 |
| P11 | 「**赛题交付包等前置技术任务完成后再做**」（README 正文强调） | README.md:40 |
| P12 | 「虚拟环境、权重/密钥、本地日志、临时渲染和可再生预览不随仓库发布。实时数据库、模型凭据和配额不属于 Git 内容；**新机器需要独立初始化和配置**」 | PROGRESS.md:202 |

### 1.4 判据不清 / 无法判定

| 项 | 说明 |
|---|---|
| C1 | **≤30 页无法判定**：V1.3/README 均未记载页数要求；仓库只有 Markdown（`V1.3.md` 54 043 字符 / 22 400 汉字），无 Word/PDF 渲染产物，页数只能粗估（见 §3 第 6 行）。 |
| C2 | **「模型权重文件（若适用）」中的"适用"边界不清**：赛题未定义何谓适用；V1.3:581 只给了"不随包时列明获取方式"的替代口径。 |
| C3 | **「涵盖不同类型文档格式」的"类型"粒度不清**：按容器格式（PDF/DOCX/XLSX）还是按内容类型（扫描件/表格/公式）？若按容器格式，当前缺口明显（见 §3 第 3 行）。 |
| C4 | **是否申报高级任务未决**：J12/P1 的《突破与创新性自述》是否提交，仓库无任何记载（PROGRESS.md:27 明确列为"延后"项）。 |
| C5 | **初赛/决赛阶段未标注**：仓库同时存在初赛（≤20/30 页）与决赛（PPT ≤20 页 / 文档 ≤50 页）要求，但无任何"当前处于哪个阶段"的声明。 |

---

## 2. 方法与实际执行的命令

允许范围内使用的命令（全部只读）：

```bash
# 结构与版本
git ls-files | wc -l                # 540
git status --porcelain -uall        # 4 项未跟踪
git log --oneline -3
wc -l README.md 技术文档/DataMind_Agent_技术设计说明书_V1.3.md
# 官方判据抽取（不落盘，仅 stdout）
.venv/bin/python -c "import pymupdf; d=pymupdf.open('赛题/8 赛题八：….pdf'); [print(d[i].get_text()) for i in range(14)]"
# 只读 SQL / 只读接口
docker exec datamind-postgres psql -U postgres -d datamind_meta -c "<SELECT ...>"
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:6333/collections/datamind_knowledge
curl -s -X POST http://127.0.0.1:6333/collections/datamind_knowledge/points/count -d '{...}'
# 体积与忽略检查
du -sh .venv .venv-formula .cache tmp output reports data
git check-ignore -q <path>
```

**未执行**（按约束）：任何 git 写操作、格式化工具、`docker compose` 子命令、`scripts/reset_demo.sh`、`scripts/verify.py`、`ingestion/ingest.py`、`scripts/test_isolated.py`。

运行态基线（只读确认）：PostgreSQL 与 Qdrant 容器 healthy；`GET /health` 返回
`{"status":"ok","database":"ready","agent":"sql+rag","knowledge":"ready","document_count":30}`。

---

## 3. 交付物核对表

| # | 要求（判据） | 现状证据（命令 / 计数 / 文件:行） | 结论 | 缺口 | 补齐动作与验收标准 |
|---|---|---|---|---|---|
| 1 | **完整项目源代码 + 目录结构说明 + README**（J3；V1.3:577） | `git ls-files \| wc -l` = **540**；`git log -1` = `005b9cc`；README.md 497 行含「二、目录结构」代码块（README.md:98–128）；`datamind/` 36 项、`ingestion/` 19 项、`db/` 迁移 001–016 | **达标** | 工作树混入 2.2 GB 非源码内容（见 §6） | 用 `git archive` 而非 `zip -r` 生成源码包；ZIP 内文件数 = `git ls-files \| wc -l`（540）± 交付说明；解压后 `du -sh` ≤ 30 MB |
| 2 | **数据库 schema 定义文件**（J3） | `deploy/init/`：`01_roles.sql`、`02_metadata.sql`（18 张表）、`03_demo_retail.sql`（6 张表）、`04_industrial_schema.sql`；`db/migrations/*.sql` **16 个**（001–016，`CREATE TABLE IF NOT EXISTS` 幂等）；`db/migrate.py` 全量重放 + advisory lock。逐名核对：**当前 `datamind_meta.public` 46 张表 100% 可在 `deploy/init` ∪ `db/migrations` 中找到 DDL**（脚本见 §7 Q0，输出为空） | **达标** | README.md:106 写「元数据底座 17 张表」，实测 `02_metadata.sql` 有 **18** 处 `CREATE TABLE`（文档漂移） | 修正 README.md:106 的表数；验收：`grep -c "CREATE TABLE" deploy/init/02_metadata.sql` = README 所载数字 |
| 3 | **知识库文档样本 ≥10 份，涵盖不同类型文档格式**（J3） | 实测（只读 SQL + 文件计数）：`document_assets` = **30** 行，全部 `status='indexed'`；其中 `demo_kb` 15、`ohrbench_kb` 14（`file_type='json'`）、`industrial_demo` 1。**`data/knowledge_base/*.pdf` 实测 15 个 PDF**（12 份演示 + 3 份 field-test 杂物）；`git ls-files 'data/knowledge_base/*.pdf'` = **12 份跟踪**。格式覆盖：PDF 原生 / PDF 扫描 / JSON(gt_text)；**无 DOCX/PPTX/XLSX 样本**（`find data -name "*.docx" -o …` 结果为空，尽管 Office 适配器已实现）；CAD 样本 9 个 DXF 在 `data/cad_demo/`，未进知识库 | **部分达标**（数量达标，格式覆盖不达标 / 判据 C3 不清） | ① 3 份 `field-test-*.pdf`（另一 agent 产物）已被摄取进 demo_kb，污染样本集（见 §6、§7）；② 交付样本缺 Office 三类格式；③ README.md:156 写「26 份文档 / 169 切片」，与实测 30/184 不符 | ① 清除 3 份 field-test 及其切片/字段/页图；② 至少补 1 份 DOCX + 1 份 XLSX（可走 `ingestion/office.py`）作为样本，或明确声明本期只支持 PDF 并给出理由；③ 更新 README.md:156、README.md:178 的计数。验收：`SELECT count(*), count(DISTINCT file_type) FROM document_assets WHERE collection<>'ohrbench_kb'` ≥ 10 且 `file_type` 覆盖 ≥3 种；`ls data/knowledge_base/*.pdf \| wc -l` = 12（仅演示集） |
| 4 | **模型权重文件（若适用）**（J3；P1「不随包时列明合法获取、缓存和网络依赖」；P8 登记「来源/许可/版本/权重哈希」） | 无任何权重随包（`.gitignore:11` 忽略 `.venv/`、`.cache/`）。运行依赖的权重实测体积：`.cache/formula_models` **789 MB**（`pp_formulanet_plus_m.onnx` 594 MB + `_s` 233 MB）、`.cache/ocr_models` **154 MB**、`.cache/fastembed` **91 MB**（BGE）。获取脚本存在且**带 SHA256 锁定**：`scripts/setup_formula.py`（MODEL_SHA256 + modelscope URL）、`scripts/setup_ocr_models.py`（`MODEL_SHA` + RapidOCR URL + `ingestion/tables.py:13-14`）；BGE 由 `datamind/knowledge.py:100-101` 经 FastEmbed **运行时自动下载**（无 SHA 锁定）。**仓库内不存在任何汇总的"权重清单"文件**（`find . -iname "*manifest*"` 命中 8 处，均为数据清单，非模型清单） | **不达标**（清单缺失；单件获取方式部分具备） | ① 无统一的权重/模型 manifest（名称、用途、来源 URL、许可、版本、SHA256、体积、是否随包）；② BGE 无哈希校验；③ V1.3:530 声称要登记"模型卡/许可"，实测未登记许可 | 新增 `docs/MODEL_MANIFEST.md`（或 `models/manifest.json`），逐条列：`BAAI/bge-small-zh-v1.5`（FastEmbed，91 MB，许可 MIT 待核）、`PP-FormulaNet_plus-M`（locator/SHA 已存在，594 MB）、`ch_PP-OCRv5_rec_server`（SHA 已存在，84 MB）、RapidOCR 内置 det/rec/cls、语言模型 `deepseek-flash`（**只能登记为别名，规模未知**，V1.3:532 已声明不主张小模型优势）。验收：清单每行都有 `source_url` + `sha256` + `license` + `shipped: yes/no`；对 `shipped: no` 的行给出 §6 的获取命令 |
| 5 | **运行环境配置说明（依赖包列表及版本号）**（J3；P2 新机可复现） | `requirements.txt`（15 项，Python 注释写 **3.13**）、`requirements-{sql,rag,ocr,formula,office,cad}.txt`、`requirements.lock`（**65 个 `==` 固定版本**）；守护测试 `tests/test_dependency_lock.py:16-17` 的 CRITICAL 仅 7 项。**实测锁文件缺 4 个被代码 import 的包**：`grep -qi "^reportlab==" requirements.lock` → MISSING；`matplotlib`、`img2pdf`、`pyarrow` 同样 MISSING（分别被 `ingestion/generate_demo_docs.py:19,31-32`、`ingestion/sample_ohrbench.py:14`、多个 `scripts/build_*.py` 使用）。当前 `.venv/bin/pip list` 仅 **68 项且不含 reportlab/matplotlib/img2pdf/pyarrow**。Python 版本三方不一致：`requirements.txt:1` 写 3.13 / README.md:394 写「本次验收使用 3.14」/ `.venv/bin/python -V` = **3.14.5** / README.md:199·396 公式 worker 要求 3.10–3.12 | **部分达标** | ① 锁文件与代码依赖不一致（数据生成链路无法按锁复现）；② 版本口径三处冲突；③ 无 `python_requires`/`.python-version` 固化 | ① 把 4 个缺失包补入 `requirements.lock` 并把它们加入 `tests/test_dependency_lock.py:16` 的 CRITICAL；② 统一 Python 版本口径（建议：主环境 ≥3.13 写清楚，公式 worker 单列 3.10–3.12）；③ 新增 `.python-version` 或 README 首屏显式声明。验收：`pip install -r requirements.lock` 后 `python -c "import reportlab, matplotlib, img2pdf, pyarrow"` 不报错；`requirements.lock` 与 `pip freeze` 零漂移（现有测试已覆盖运行时 7 项） |
| 6 | **技术实现说明书（Word + PDF，≤30 页，含 7 个模块 + 亮点预览页）**（J1/J2；P1 设计报告 + 技术实验说明书） | `技术文档/` 仅 3 个 **Markdown**：V1.3（104 826 B / 22 400 汉字 / 886 行）、V1.2（222 027 B / 49 394 汉字，历史）、参考 MD。**无 `.docx`、无 `.pdf`**（`find . -iname "*.docx" -o -iname "*.pdf"` 在 `技术文档/` 下命中 0）。**无「核心亮点预览页」**（grep「亮点」「预览页」于 V1.3/README 均 0 命中）。模块覆盖：J2 的 7 项中「中级任务攻克方案详述」只在 V1.3:53–59 有内部代号 T1/T2/T3 表且 V1.3:61 自认「须映射官方九项后才能计入」；「系统测试与性能评估」在 V1.3 §9 有指标定义但**无效率/复杂度专章**（见 #7） | **不达标** | ① 完全没有 Word/PDF 交付物；② 没有设计报告（≤20 页）与亮点预览页；③ 中级任务未映射官方九项；④ 页数无法判定（C1） | ① 从 V1.3 裁出《技术实现说明书》并渲染 Word+PDF（含目录）；② 新写 ≤20 页《设计报告》，第 1–2 页为亮点预览页（架构图 + 融合链路 + 创新点 + 中级任务清单 + 关键数据）；③ 新增「官方九项 ↔ 实现」映射表；④ 渲染后统计页数。验收：`技术文档/` 下同时存在 `.docx` 与 `.pdf`，且 PDF 页数 ≤30（说明书）/≤20（设计报告），亮点页存在 |
| 7 | **系统效率分析（复杂度 + 两种数据规模实测 + 瓶颈优化）= 5 分**（J7） | `grep -n "复杂度\|时延\|p50\|p95"` 于 V1.3 仅命中 4 处（V1.3:389/534/596/694），全部是指标表/实验计划措辞，**没有任何模块的时间复杂度理论分析，也没有两种数据规模（1 千行 / 1 万行）的实测或预估对照**。`business_metrics`/`schema_*`/`chunks` 表也没有性能基准记录 | **不达标** | 整章缺失 | 在说明书中新增「效率与伸缩性」章：① `sql_engine`/`rag_engine`/`knowledge` 各链路复杂度；② 在 demo_retail（3 000 订单 / 7 587 行项）与放大到 1 万+ 行的对照实测（`DATAMIND_SQL_TIMEOUT` 同参）；③ 瓶颈与优化策略。验收：报告含 ≥2 组不同规模实测数字 + 每链路复杂度表达式 |
| 8 | **多轮对话上下文保持 = 5 分**（J8） | `datamind/api.py:51-54`：`ChatRequest` 仅 `question` / `scope` / `mode`，`model_config={'extra':'forbid'}`（多余字段被拒）；V1.3:449 明写「当前 `ChatRequest` 只有 `question/mode/scope`；…**不把旧设计的 `knowledge_collections/conversation_id/options` 当已实现请求契约**」。全仓 `grep "conversation"` 于 `datamind/` 仅命中历史/版本历史函数名，无会话上下文 | **不达标** | 无 conversation/session 概念，无历史轮次注入；LLM 侧不接受多轮消息 | 二选一并写入文档：① 实现 `conversation_id` + 历史（建议只保留最近 N 轮 + 只读回放，注明上下文不越权范围）；② 明确声明本期不做多轮并接受该项 0 分。验收（若做）：连续 5 轮追问使用同一 `conversation_id` 且 `runs` 表可回放上下文 |
| 9 | **基础任务测试用例 ≥10（NL2SQL）+ ≥10（RAG）**（J5） | 只读 SQL：`SELECT suite, count(*) FROM evaluation_cases GROUP BY 1` → `nl2sql 14`、`rag 12`、`robustness 4`；共 **30** 条，与 README.md:113「14 NL2SQL + 12 RAG + 4 鲁棒 = 30 条」一致；种子脚本 `db/seed_eval_cases.py` 随仓库 | **达标** | 用例已绑定 `demo_retail`/`chinook`/`demo_kb`；`rag-011` 依赖 OHR-Bench 样本、`rag-012` 是拒答题（`required_evidence` = `{"doc": null, "section": null}`） | 交付前用 `scripts/eval_sql.py` / `eval_rag.py` 重跑一次并随包附报告；验收：报告逐例可核、失败保留（不得只交成功子集） |
| 10 | **可执行 Demo 的快速启动脚本**（J3；P2 新机启动可复现） | `scripts/start_local.sh`（83 行，自动建 venv → 按需 `pip install` → `docker compose up -d postgres` → `db/migrate.py` → chinook/零售种子 → Catalog → 评测种子 → Qdrant → `ingestion/ingest.py` → 工业种子 → 起 uvicorn）**存在且逻辑完整**；`scripts/start_api_background.py`；README.md:366–390 有「六、快速开始」。**但脚本无执行位**（`ls -la` = `-rw-r--r--`），必须 `bash scripts/start_local.sh` 调用（README 写法正确） | **部分达标** | ① 无执行位（低风险）；② 脚本会**顺手摄取 `data/knowledge_base/*.pdf` 全量 glob**（`ingestion/ingest.py:559`），任何人放入该目录的文件都会被索引 —— 这正是 field-test 产物进入知识库的通道；③ 无「一键打包/一键自检」脚本 | ① `chmod +x` 或保持 README 的 `bash` 调用并在脚本首行说明；② 让 `ingest.py` 只摄取 `manifest.json` 中列出的文件（或对未登记 PDF 显式报错），从根上防止杂物入库；③ 新增 `scripts/check_delivery.sh`：校验包内无 `.env`/`.venv`/`.cache`/密钥/未跟踪文件，并打印交付物清单。验收：`bash scripts/check_delivery.sh` 退出码 0 |
| 11 | **答辩 PPT（≤20 页，无识别信息）**（J10） | `find . -iname "*.pptx" -o -iname "*.ppt"`（排除 .venv/.cache/tmp）→ **0 命中**；PROGRESS.md:27 明示「在此之前不投入交付打包、**答辩 PPT**、官方专项核对与《突破与创新性自述》」 | **不达标** | 完全不存在 | 按 J10 六段结构（赛题理解/总体架构/中级任务重点/高级创新/测试结果/总结展望）制作 ≤20 页 PPT；注意「不能出现队伍单位、人名等识别信息」。验收：`.pptx` 存在、页数 ≤20、`grep` 无姓名/单位 |
| 12 | **打包 ZIP（技术数据）**（J3/J10；P9「延后」） | 仓库内 `find`（排除 .venv/.venv-formula/.git/.cache/tmp）针对 `*.zip/*.tar.gz/*.7z` → **0 命中**；README.md:40 与 PROGRESS.md:27/176 均记录交付包**延后** | **不达标** | 未打包；且当前工作树 **2.2 GB**（见 §6），直接压缩会产出含密钥/缓存/杂物的问题包 | 见 §6 清理清单 + §9 行动顺序；打包必须基于 `git archive`（或 `git ls-files`）白名单，产出后解压自检。验收：ZIP ≤ 30 MB、解压后 `du -sh` ≤ 50 MB、包内无 §6 命中项 |
| 13 | **官方专项映射 + 国产 model manifest（P3/P5）** | V1.3:61「须映射官方九项后才能计入专项数量」；V1.3:645「官方专项核对、模型 manifest…**未完成**」；`grep -rn "官方九项\|官方专项"` 无映射表；PROGRESS.md:176「官方专项核对 **延后**」 | **不达标** | 官方 9 个中级任务与实现的对应关系、可计分数量均未产出；国产来源登记未成文（V1.3:530 只写了"应登记"） | 产出《官方九项映射表》：逐项写「官方要求 → 实现位置(file:line) → 证据报告 → 是否可计分」。基于实测，至少 #3 澄清、#4 Schema Linking、#5 多表关联、#6 公式、#7 目录结构、#9 质量评估 有代码与报告可对应，但**须逐项核对官方措辞**（本审查不代做判定）。验收：≥1 项（初赛）映射成立且有独立分母报告 |

---

## 4. 全新机器复现步骤推演

按 README.md:366–390「六、快速开始」与 `scripts/start_local.sh` 的**实际执行顺序**逐步推演。**结论：全新机器上会卡在 4 处，其中 2 处会导致"看似成功但结果不同"。**

| 步 | README/脚本动作 | 会卡住吗 | 前置条件与失败表现 |
|---|---|---|---|
| 0 | 安装 Docker Desktop | ⚠️ | README.md:394 要求 Docker Desktop；未装则第 3 步 `docker compose` 直接失败（明确报错，非静默） |
| 1 | `python -m venv .venv` | ✅ 不卡 | 需 Python ≥3.13（`requirements.txt:1`）；若用 3.10–3.12，`rapidocr/onnxruntime/pymupdf` 新版本可能无轮子 → pip 报错 |
| 2 | `pip install -r requirements-ocr.txt`（脚本第 22 行自动触发） | ⚠️ | **网络必达**：需从 PyPI 或镜像下载 fastapi/uvicorn/pymupdf/fastembed/qdrant-client/rapidocr/onnxruntime。国内无镜像配置则会超时。`requirements-ocr.txt` 未固化镜像源 |
| 3 | `docker compose -f deploy/compose.yaml up -d postgres` | ⚠️ | 需拉取 `postgres:16-alpine` 与 `qdrant/qdrant:v1.12.4` 镜像。`deploy/.env` 不存在可（compose 有默认值），但**README 未提示要先 `cp deploy/.env.example deploy/.env`** |
| 4 | `db/migrate.py`（16 个迁移） | ✅ 不卡 | 依赖 `deploy/init` 已由容器首次启动执行；全新卷才会执行 → 若卷已存在但未初始化过 `01_roles.sql`，`migrate.py` 会因缺角色报错（明确报错） |
| 5 | chinook 加载 / 零售种子 / Catalog / 评测种子 | ✅ 不卡 | 只需 PostgreSQL；`db/chinook/Chinook_PostgreSql.sql` 已随包（588 KB） |
| 6 | **`docker compose up -d qdrant` + `ingestion/ingest.py`** | ❌ **会卡** | ① **`ingest.py` 需要 BGE 权重**（`datamind/knowledge.py:100-101`、`ingestion/ingest.py:545`），FastEmbed 默认从 **huggingface.co** 下载；仓库内**没有任何地方为常规启动路径设置 `HF_ENDPOINT`** —— 实测全仓只有 `scripts/reset_demo.sh:40` 有 `export HF_ENDPOINT=https://hf-mirror.com`，而该脚本是"重置"脚本、且 README 主路径不会执行它。故 README.md:397「Embedding 模型经 hf-mirror.com 自动下载」**在快速开始路径上不成立**。大陆网络下这一步会长时间挂起或失败。② 首次 OCR 需要 `scripts/setup_ocr_models.py` 从 modelscope 下载 84 MB（`start_local.sh:53-55` 只打印提示、**不阻断**） |
| 7 | `db/seed_industrial.py` + `build_industrial_catalog.py` | ✅ 不卡 | 纯 SQL + 本地 PDF |
| 8 | 起 `uvicorn`，浏览器打开 8000 | ⚠️ | 服务能起来，但 `/health` 的 `knowledge` 字段**只统计 `document_assets` 行数**（`datamind/api.py:671-675`），**不检查 Qdrant 是否可达、BGE 是否加载成功**。因此第 6 步失败时 `/health` 仍返回 `"knowledge":"ready"` |
| 9 | 提问问数/问答 | ❌ **会卡（需用户自备凭据）** | 直连上游，**必须自备 API Key**。缺失时表现**明确**：`datamind/sql_engine.py:189` `raise RuntimeError("MODEL_KEY_MISSING")`，`datamind/agent.py:20` 映射为中文提示「尚未配置模型 API 密钥，请在左侧「模型接入」中填写并保存后重试」。**但 README 未在"六、快速开始"一节写明这一前置条件** —— 该信息只在 README.md:72（模块一）间接出现 |
| 10 | 公式能力（可选） | ❌ 会卡 | 需**另装 Python 3.10–3.12**（`scripts/setup_formula.py:26-29` 显式校验并 `parser.error`），再下 594 MB 权重。当前主环境是 3.14.5 → 必须额外准备解释器。README.md:396 有提示，但未给"没有 3.12 怎么办" |
| 11 | 全套回归 `.venv/bin/python scripts/test_isolated.py` | ⚠️ | 需要 `requirements.lock` 全部 65 项 + 隔离库；**当前锁文件缺 reportlab/matplotlib/img2pdf/pyarrow**，若测试触达数据生成脚本会 `ImportError`（本审查未运行，标记**疑似**） |
| 12 | 文档/样张再生成（`ingestion/generate_demo_docs.py`） | ❌ 会卡 | 需要 `reportlab/matplotlib/img2pdf`，**三者都不在 `requirements.lock`，也不在当前 `.venv`（68 包）中**。缓解：12 份 PDF 已随包跟踪，正常快速开始**不需要**再生成 |

**会让你卡住的步骤汇总**：
1. **BGE 权重下载走 huggingface.co**（快速开始路径未设 `HF_ENDPOINT`）——最可能的静默长挂；
2. **必须自备上游 API Key**——README 快速开始章节未写；
3. **公式 worker 需要另一个 Python 3.10–3.12** + 594 MB 权重；
4. **`requirements.lock` 缺 4 个代码 import 的包**。

**误导性的"成功"**：`/health` 的 `knowledge` 不反映 Qdrant/BGE 状态；检索侧 `datamind/knowledge.py:222-225` 对 dense 失败是 `except Exception: warnings.append("DENSE_RETRIEVAL_UNAVAILABLE")`，**HTTP 仍 200**、只在返回体的 `mode` 从 `hybrid` 变 `lexical` 且 `warnings` 数组里有个字符串。这符合 README.md:172「向量服务不可用时降级为词法检索并显示告警」的设计，但从"验收"角度看属于**静默降级**。

---

## 5. 首次运行依赖的外部输入/凭据

| 外部依赖 | 用途 | 缺失时表现 | 判断依据 | 是否已在文档写明 |
|---|---|---|---|---|
| **上游语言模型 API Key** | NL2SQL 生成、RAG 回答、工业模型问数 | **明确报错**：`MODEL_KEY_MISSING` + 中文提示 | `datamind/sql_engine.py:189`、`datamind/agent.py:20` | 写在 README.md:72，但**不在"六、快速开始"**（README.md:366–390） |
| 上游额度/网络 | 同上 | **明确报错**：`MODEL_AUTH_FAILED`(401/403)、`MODEL_BALANCE_EMPTY`、`MODEL_UNREACHABLE`、`MODEL_TIMEOUT` | `datamind/providers.py:135,139,153,154`、`datamind/agent.py:21-25` | ✅ README.md:72 |
| **HuggingFace / hf-mirror（BGE 权重 ≈91 MB）** | 向量检索 | **静默降级**：dense 失败 → 词法检索，HTTP 200，仅 `warnings:["DENSE_RETRIEVAL_UNAVAILABLE"]`；`/health` 仍 `knowledge:"ready"` | `datamind/knowledge.py:222-225`、`datamind/api.py:671-675` | ⚠️ README.md:397 声称走 hf-mirror，但**代码/脚本未配置**（只有 `scripts/reset_demo.sh:40`） |
| ModelScope（RapidOCR 表头 84 MB） | 扫描件表头识别 | **明确提示但不阻断**：`start_local.sh:53-55` 打印「表头模型尚未安装…缺失表头将保留为解析缺口」，摄取继续、该页表头为空 | `scripts/start_local.sh:53-55`、`scripts/setup_ocr_models.py` | ✅ README.md:219 |
| ModelScope（PP-FormulaNet_plus-M 594 MB）+ **Python 3.10–3.12** | 公式图 → LaTeX | **明确拒绝**：`setup_formula.py:26-29` `parser.error(...)`；运行期公式块留为解析缺口 | `scripts/setup_formula.py:26-29`、`ingestion/formula_worker.py:18` | ✅ README.md:199,396 |
| **视觉模型 / 真实工厂材料** | V-CAR 真实视觉对比、真实授权模板 | 未实现（`V1.3:579`、PROGRESS.md:11「未决外部依赖：T6 授权真实输入；V-CAR 真实视觉载荷」）；无静默失败，属功能缺口 | V1.3:523「视觉/替代解析 尚未接入通用 VLM」 | ✅ 多处 |
| Docker Hub 镜像（postgres:16-alpine、qdrant/qdrant:v1.12.4） | 基础设施 | **明确报错**（compose 拉取失败） | `deploy/compose.yaml` | ✅ README.md:377 |
| OHR-Bench parquet（≈200 MB） | 重新抽样 OHR 语料 | 非必需：14 份样本 JSON 已随包跟踪（`git ls-files data/ohr_bench_samples` = **15**，含 manifest） | README.md:371「非必需」 | ✅ |

**结论**：「外部凭据缺失」这条链路的错误处理**质量较高**（稳定错误码 + 中文提示 + 逐例记录不把失败计为正确，README.md:72）。**唯一严重问题是 BGE 下载的静默降级 + 快速开始章节未写 API Key 前置条件。**

---

## 6. 提交前必须清理/补交的清单（含体积与体积风险）

### 6.1 高危：真实凭据（不进 ZIP，但任何 DB dump / 快照都会泄漏）

| 项 | 位置 | 证据 | 体积 | 处理 |
|---|---|---|---|---|
| **真实上游 API Key（2 条）** | 运行库 `datamind_meta.app_settings` | `SELECT setting_key, value FROM app_settings` → `model.profiles`（启用配置 `p-78df54fa`，`api_key` = `sk-4213…e5db`，`base_url=https://api.deepseek.com/v1`）；`model.gateway`（遗留，`api_key` = `sk-Mpwx…5CQ`，`base_url=http://localhost:4000/v1`，**指向已删除的网关**） | 1 行 ×2 | ① 交付前**轮换**这两把密钥；② 交付包**不得包含数据库 dump 或 `pg_meta_data` 卷**；③ 若必须带演示库，先删除 `model.gateway` 行并清空 `model.profiles[].api_key`。验收：`pg_dump datamind_meta \| grep -c "sk-"` = 0 |
| 开发明文口令 | `deploy/init/01_roles.sql`、`deploy/compose.yaml`、`deploy/.env.example`、`db/*.py`、`scripts/verify.py` | `datamind_app_dev` / `readonly_dev` / `datamind_dev`；compose.yaml 自述「已知安全边界」 | <1 KB | 属**本机演示默认值**，可保留但需在说明书中一行声明「仅本机回环演示，非生产凭据」 |
| 私钥文件 | 全仓 | `git grep "BEGIN .*PRIVATE KEY"` → **0 命中** | 0 | ✅ 无需处理 |

### 6.2 必须清理的杂物

| 项 | 路径 | 是否被 `.gitignore` 覆盖 | 体积 | 风险 |
|---|---|---|---|---|
| **另一 agent（Codex）的 3 份 field-test PDF** | `data/knowledge_base/field-test-{782a6a0da3e1,d0a8111e10e0,faee21b33952}.pdf` | ❌ **未被忽略**（`/*.pdf` 只匹配根目录） | 3 × 41.5 KB = **125 KB** | **已被摄取进知识库**（见 §7）：3 份 doc、15 个 chunk、18 条 fact、Qdrant 15 个点；`zip -r` 会直接带入 → 交付样本集被污染 |
| Codex 审计产物（**已 git 提交**） | `reports/project_audit_20261008_2d9ab18.md`、`..._review.md` | ❌ 未忽略，且**已被 commit `28122c7` 跟踪** | 43 KB | `git archive` 会包含；`..._checks.json` 未跟踪（2.9 KB）也会被 `zip -r` 带入 |
| **`.cache/`（模型缓存）** | `.cache/` | ✅ 已忽略 | **1.0 GB**（formula 789 MB / ocr 154 MB / fastembed 91 MB / ocr_probe 4.3 MB） | `zip -r` 若不加排除会爆炸；含公式 594 MB 单文件 |
| **`.venv/` + `.venv-formula/`** | 根目录 | ✅ 已忽略 | **1.15 GB**（576 M + 574 M） | 同上。注意 `.venv-formula` 是公式 worker 的独立环境 |
| `tmp/` | `tmp/audit-baseline`（另一 agent 的工作树拷贝）、`audit_*.log`、`f09/`、`pdfs/` | ✅ 已忽略（`/tmp/`） | **26 MB**（其中 `tmp/audit-baseline` 25 MB） | `zip -r` 带入；含 gateway 残留目录名 |
| `output/pdf/` 3 份生成 PDF | `output/pdf/industrial_forms_*.pdf` | ❌ **未被忽略，且已跟踪** | 1.2 MB | 属再生成产物；跟踪本身可接受，但需在说明书中定位为"生成的验收材料" |
| `.DS_Store` × 6 | 根、`技术文档/`、`data/`、`data/object_store/`×2、`data/knowledge_base/` | ✅ 已忽略 | 45 KB | `zip -r` 带入 |
| `__pycache__/` × 6 | `db/`、`scripts/` 等 | ✅ 已忽略 | — | 无害 |
| `.repo-state/` | 根 | ✅ 已忽略 | 6.8 MB | `zip -r` 带入 |
| **孤儿页图目录 × 38（测试/预览残留）** | `data/object_store/page_images/{col-smoke,compact-integration-fixture,route-04,smoke-04,test-multifile-api-*,doc-1098855ff90b,…}` | ✅ 已忽略 | 含在 5.8 MB 内 | 无对应 `document_assets`；其中 **12 个已被 git 跟踪**（约 **1.5 MB**），连 `git archive` 也会带上 |
| `reports/` 全量 | 190 个跟踪文件 + 若干未跟踪 | ❌ 未忽略 | **5.3 MB**（跟踪部分 5.4 MB） | 是"证据"不是"交付物"；建议随包但单列 `evidence/`，并剔除 Codex 产物 |

### 6.3 体积风险总账

| 范围 | 体积 |
|---|---|
| 工作树总计（排除 `.git`） | **2.2 GB** |
| **git 跟踪文件总计** | **15 MB** |
| `git archive` 白名单打包后预期 | ≈ **15–17 MB**（含 12 份 KB PDF 1.6 MB、赛题 PDF 588 KB、chinook SQL 588 KB、reports 5.4 MB） |
| 若直接 `zip -r 仓库` | **≈ 2.2 GB**（含密钥风险内容） |

**结论：必须用 `git archive`（或 `git ls-files` 白名单）打包，禁止 `zip -r`。** 但注意 `git archive` 目前仍会带上 6.2 中"已跟踪"的两类杂物（Codex 审计 .md ×2、12 个孤儿页图目录），需要在打包前**单独提交一次删除**（本次审查为只读，未执行）。

### 6.4 `.gitignore` 覆盖评估

- ✅ 已覆盖：`.venv/`、`.venv-formula/`、`.cache/`、`/tmp/`、`deploy/.env`、`.env`、`*.key`、`__pycache__/`、`.DS_Store`、`.repo-state/`、`/data/object_store/page_images/`（新增部分）、`OHR-Bench/`、`chinook-database/`、`/*.pdf`（仅根目录）
- ❌ **未覆盖**：`data/knowledge_base/field-test-*.pdf`（应加入 `data/knowledge_base/*.pdf` 白/黑名单策略）、`reports/project_audit_*`（他方产物）、`output/`（生成物）、`*.log`
- ⚠️ 设计缺陷：`/*.pdf` 只挡根目录 PDF，**任何子目录放入的 PDF 都会被 git 与 ingestion 同时接收** —— 这正是 field-test 产物既未跟踪又被索引的根因。

---

## 7. 数据自洽性实测结果

所有查询均为只读 `SELECT`，执行于运行中的 `datamind-postgres`（`datamind_meta`）。

### Q0 交付数据 ✓ 一致

```sql
-- 预期 27 份(12 演示 + 14 OHR + 1 工业)，实测 30
SELECT a.collection, a.status, count(*) FROM document_assets a GROUP BY 1,2 ORDER BY 1;
--  demo_kb         | indexed | 15
--  industrial_demo | indexed |  1
--  ohrbench_kb     | indexed | 14
```

### Q1 ★ 有 `indexed` 但无切片、且**属于有意设计**的文档

```sql
SELECT a.document_id, a.title, a.collection, count(c.chunk_id) AS chunks
FROM document_assets a LEFT JOIN chunks c ON c.document_id = a.document_id
GROUP BY 1,2,3 HAVING count(c.chunk_id) = 0;
```
结果：**仅 1 行** — `doc-industrial-0ac6c55838c3`（工业合成演示，17 页，0 切片）。
**判定：非缺陷（已确认）**。V1.3:881 明确说明：「`doc-industrial-0ac6c55838c3` 状态 `indexed` 但无 chunks **不是缺陷**：由 `db/seed_industrial.py` 有意注册并持有 17 条 `document_facts`，工业路径查事实而非切片。」实测佐证：该 doc 确有 17 条 `document_facts`（见 Q4）。

### Q2 ★ 孤儿切片 / 悬挂 block 引用

```sql
-- 孤儿切片（chunks 指向不存在的文档）
SELECT count(*) FROM chunks c LEFT JOIN document_assets a USING(document_id) WHERE a.document_id IS NULL;   -- 0
-- 切片 block_ids 引用了不存在的块
SELECT count(*) FROM chunks c WHERE EXISTS (
  SELECT 1 FROM unnest(c.block_ids) b
  WHERE NOT EXISTS (SELECT 1 FROM document_blocks db WHERE db.block_id = b));                             -- 0
```
**判定：无孤儿（已确认）。**

### Q3 ★ 切片 ↔ 向量 ↔ 块 三方一致

```sql
SELECT embedding_model, count(*), count(vector_id) AS with_vector,
       count(*) FILTER (WHERE page_start IS NULL) AS null_page_start FROM chunks GROUP BY 1;
-- BAAI/bge-small-zh-v1.5 | 184 | 184 | 0
```
```bash
curl -s http://127.0.0.1:6333/collections/datamind_knowledge   # points_count = 184, size 512, Cosine
curl -s -X POST .../points/count -d '{"exact":true,"filter":{"must":[{"key":"document_id",
  "match":{"any":["doc-2e8b9f4b10d9","doc-42476c9bf937","doc-f254d5b1345e"]}}]}}'  # count = 15
SELECT count(*) FROM document_blocks;   -- 586
```
**判定：PG chunks 184 = Qdrant points 184，`vector_id` 100% 非空，无空页码（已确认）。**

### Q4 ★ 字段（facts）归属

```sql
SELECT f.document_id, left(a.title,40), a.collection, count(*) FROM document_facts f
JOIN document_assets a USING(document_id) GROUP BY 1,2,3 ORDER BY 3,1;
-- doc-158815ed856f            | 07_门店巡检记录_2025年12月_扫描版 | demo_kb         |  6
-- doc-2e8b9f4b10d9            | field-test-782a6a0da3e1            | demo_kb         |  6   ← 他方产物
-- doc-42476c9bf937            | field-test-faee21b33952            | demo_kb         |  6   ← 他方产物
-- doc-f254d5b1345e            | field-test-d0a8111e10e0            | demo_kb         |  6   ← 他方产物
-- doc-industrial-0ac6c55838c3 | 工业合成演示：工艺与质检原文      | industrial_demo | 17
-- 孤儿 fact（文档不存在）= 0
```
**判定：无孤儿，但 demo_kb 的 24 条 fact 中有 18 条来自他方产物**。历史口径 README.md:214「已保存 6 条门店得分候选」对应 `doc-158815ed856f`（6 条），与 V1.3:471「23 候选事实（6 门店＋17 工业）」一致 —— 当前 41 条 = 23 + 18 杂物。

### Q5 ★ 定位（locators）与关系（relations）

```sql
SELECT a.collection, count(DISTINCT l.document_id) AS docs, count(*) AS locators
FROM document_block_locators l JOIN document_assets a USING(document_id) GROUP BY 1;
--  demo_kb | 3 | 33
SELECT count(*) FROM document_block_locators l
  LEFT JOIN document_blocks b USING(block_id) WHERE b.block_id IS NULL;          -- 孤儿定位 = 0
SELECT count(*) FROM document_block_locators l JOIN document_blocks b USING(block_id)
  WHERE b.document_id <> l.document_id;                                          -- 文档错配 = 0
SELECT count(*) FROM document_block_relations;   -- 0
SELECT count(*) FROM document_contract_blocks;   -- 0
```
**判定：无孤儿定位、无文档错配（已确认）。** `relations=0` 与 `contract_blocks=0` **属有意设计 + 无数据（已确认）**：V1.3:883 说明「两套存储仍未合并」；`document_block_relations`/`document_contract_blocks` 只服务 Office/DXF 等"无页概念"的格式（`db/migrations/015_contract_store.sql` 头注释），而当前库**没有摄取任何 Office/DXF 文档**（`file_type` 仅 pdf/json）。注意这只覆盖 3 个文档、33 条定位（其余 27 个文档的块没有定位行），符合 V1.3:781「缺定位的块不写行，不编造历史定位」。

### Q6 ★ 运行/证据链

```sql
SELECT 'evidence_nodes' k, count(*) FROM evidence_nodes            -- 1119
UNION ALL SELECT 'evidence_edges', count(*) FROM evidence_edges    -- 1032
UNION ALL SELECT 'edges_orphan_src', count(*) FROM evidence_edges e
  LEFT JOIN evidence_nodes n ON n.evidence_id=e.src_evidence_id WHERE n.evidence_id IS NULL   -- 0
UNION ALL SELECT 'edges_orphan_dst', count(*) FROM evidence_edges e
  LEFT JOIN evidence_nodes n ON n.evidence_id=e.dst_evidence_id WHERE n.evidence_id IS NULL   -- 0
UNION ALL SELECT 'nodes_orphan_run', count(*) FROM evidence_nodes e
  LEFT JOIN runs r ON r.run_id=e.run_id WHERE e.run_id IS NOT NULL AND r.run_id IS NULL       -- 0
UNION ALL SELECT 'runs_without_events', count(*) FROM runs r
  WHERE NOT EXISTS (SELECT 1 FROM trace_events t WHERE t.run_id=r.run_id);                    -- 0
-- runs 190 / trace_events 1503
```
**判定：`evidence_nodes` 1119、`evidence_edges` 1032，孤儿边 0、孤儿节点 0、无事件 run 0（已确认）。** 两个外键均为 `ON DELETE CASCADE`（实测 `\d evidence_edges`），结构性上也不易产生孤儿边。

### Q7 ★ 工业种子自洽

```sql
SELECT 'parts' t, count(*) FROM demo_industrial.parts          -- 3
UNION ALL SELECT 'operations', count(*) FROM demo_industrial.operations            -- 5
UNION ALL SELECT 'process_revisions', count(*) FROM demo_industrial.process_revisions  -- 5
UNION ALL SELECT 'process_measurements', count(*) FROM demo_industrial.process_measurements -- 13
UNION ALL SELECT 'production_batches', count(*) FROM demo_industrial.production_batches    -- 12
UNION ALL SELECT 'measurement_observations', count(*) FROM demo_industrial.measurement_observations -- 13
UNION ALL SELECT 'quality_records', count(*) FROM demo_industrial.quality_records  -- 13
UNION ALL SELECT 'dataset_manifest', count(*) FROM demo_industrial.dataset_manifest; -- 1
```
**判定：完全等于 V1.3:469 声明的「3 零件、12 批次、13 测量、13 质检登记与 5 工艺版本」（已确认，逐项吻合）。** `dataset_manifest` 记录 `dataset_version=industrial-demo-v1`、`seed_sha256`、`pdf_sha256=0ac6c55838c3…`（与 `doc-industrial-0ac6c55838c3` 的 id 一致），provenance 自述「Synthetic demonstration…no factory data」。`quality_records` 通过 `measurement_id` 唯一约束保证「每个测量至多一条质检登记」，与 V1.3:469 一致。

### Q8 ★ 零售/Chinook 基线

```sql
-- demo_retail: regions 6 / products 20 / customers 500 / orders 3000 / order_items 7587 / refunds 531
-- chinook:     artist 275 / track 3503 / invoice 412
```
**判定：与 README.md:150–152 逐项一致（已确认）。** Catalog：`schema_snapshots 5`、`business_metrics 11`、`schema_tables 36`、`schema_columns 216`、`schema_relations 37`、`entity_aliases 14`、`data_sources 3`。

### Q9 ★ 评测用例

```sql
SELECT suite, count(*) FROM evaluation_cases GROUP BY 1;  -- nl2sql 14 / rag 12 / robustness 4
SELECT case_id, required_evidence FROM evaluation_cases WHERE required_evidence IS NOT NULL;
```
11 条 RAG 用例的 `required_evidence` 指向 `data/knowledge_base/` 内的 **文件名**（如 `04_售后服务与退款管理制度.pdf`）而非 `document_id`，因此**无法用外键式校验**；其中 `rag-011` 指向 `"ohr-005 (finance)"`、`rag-012` 为 `{"doc": null}`（拒答题，有意）。
**判定：与声明的 30 条一致；`required_evidence` 用文件名引用属设计选择，不是缺陷，但削弱了机器可校验性（标记 疑似 可改进）。**

### Q10 ★★ 自洽性最严重发现：`field-test-*.pdf` 已进入生产知识库，且 V1.3 对其成因的解释与实测不符

**实测事实（已确认）**：

| 证据 | 值 |
|---|---|
| 3 份 `field-test-*.pdf` 的 md5 | 三份**各不相同**（`805d7982…` / `0cc4d122…` / `a6e9aedb…`），非重复拷贝 |
| 对应 `document_assets` 行 | `doc-2e8b9f4b10d9`、`doc-f254d5b1345e`、`doc-42476c9bf937`，`collection='demo_kb'`，`status='indexed'` |
| `uploaded_at` | **2026-10-08 16:32:57 / 16:32:59 / 16:33:00** |
| 切片 | 各 5 个，共 **15** |
| Qdrant 点 | `points/count` 过滤这 3 个 doc id = **15** |
| 字段 | 各 6 条 `document_facts`，共 **18** |
| 页图 | `data/object_store/page_images/` 下这 3 个 doc id 目录**存在**（未跟踪） |
| 其它文档 `uploaded_at` | `ohrbench_kb` **全部为 2026-10-01 23:38:38**（未变）；`demo_kb` 其余 12 份最早 10-01 23:38 |

**结论（已确认的算术）**：当前库 30 文档 / 184 切片的增量（相对 README.md:156「26 份文档 / 169 切片」）**恰好等于这 3 份 field-test 文档**：
```
文档： 30 − 27 = 3          （12 演示 + 14 OHR + 1 工业 = 27）
切片： 184 − 169 = 15       （3 × 5 = 15）
其中 demo_kb 切片 122 = 107（12 份演示）+ 15（3 份 field-test）
      ohrbench 切片  62 = 62
      合计 184 ✓
```
**而 V1.3:881 把这一增量归因于「R1 修复后 OHR-Bench JSON 分支不再 KeyError」**：「切片数由 169 升至 184、文档由 27 升至 30。这是 R1 修复后的实际效果」。**实测不支持该归因**：OHR 的 14 份文档自 10-01 起 `uploaded_at` 未变、其 62 个切片在 README 的 169 里**本已计入**；而 3 份 field-test 文档是 **10-08 16:32–16:33** 才被摄取的，时间晚于同日的 V1.3 §18.25 记述。
标记：**疑似**（无法在不运行 `ingest.py` 的前提下重建 R1 之前的状态；但计数与时间戳的两项独立证据高度一致）。
**影响**：① 交付样本集含他方产物；② 设计说明书对自身数据规模的解释在评审追问下可能站不住脚；③ README.md:156/178 的「26 份 / 169 切片」与运行库「30 / 184」并存，评审会看到矛盾数字。

**建议的核验命令（只读，供交付负责人复算）**：
```bash
docker exec datamind-postgres psql -U postgres -d datamind_meta -P pager=off -c "
SELECT a.document_id, left(a.title,34) AS title, a.collection, a.uploaded_at,
       (SELECT count(*) FROM chunks c WHERE c.document_id=a.document_id) AS chunks,
       (SELECT count(*) FROM document_facts f WHERE f.document_id=a.document_id) AS facts
FROM document_assets a WHERE a.title LIKE 'field-test-%' ORDER BY a.uploaded_at;"
```

### 数据自洽性小结

| 检查项 | 结果 |
|---|---|
| 孤儿切片 / 悬挂 block 引用 | ✅ 0 |
| 孤儿定位 / 定位文档错配 | ✅ 0 |
| indexed 无切片且非有意设计 | ✅ 0（唯一 1 例为有意设计，V1.3:881 已声明） |
| PG chunks ↔ Qdrant points | ✅ 184 = 184，`vector_id` 无空 |
| 孤儿关系 / 孤儿证据边 | ✅ 0（且 `relations` 表空属预期） |
| 工业种子（零件/批次/测量/质检/工艺） | ✅ 逐项等于 V1.3:469 |
| 零售 / Chinook 基线 | ✅ 逐项等于 README.md:150–152 |
| 评测用例数量 | ✅ 14 / 12 / 4 = 30 |
| **样本集是否被外部产物污染** | ❌ **是**：3 份 field-test 文档、15 切片、18 字段、15 向量 |
| **V1.3:881 的成因解释** | ⚠️ **疑似错误归因**（实测增量为 field-test 产物） |
| README 计数（26 份 / 169 切片） | ⚠️ 与运行库（30 / 184）不符（但 169 与"干净状态"吻合） |

---

## 8. 技术文档是否够用（只列影响评审理解的缺口）

### 8.1 代码里有、文档里没有

| 机制 | 代码位置 | 文档状态 | 影响 |
|---|---|---|---|
| 统一解析契约 + media type 探测 + Adapter 路由 + 契约存储（`document_block_locators`/`document_contract_blocks`/`document_block_relations`） | `datamind/parse_contract.py`、`ingestion/contract_store.py`、`ingestion/cad_adapter.py`、`ingestion/office.py`、迁移 014/015/016 | §18.11–18.17 有记载，`docs/PARSE_CONTRACT.md` 有专文；**但 §14 代码落点表（V1.3:559）仍写「规划 `ingestion/router.py`、`parsers/`…**当前未创建为通用能力**」** | 同一份文档内"已实现"与"未创建"并存，评审会质疑实现范围 |
| Office（DOCX/PPTX/XLSX）适配器 | `ingestion/office.py`、`scripts/parse_office.py`、`requirements-office.txt` | §18.15 记载完整；**但 §12.1 技术选型表 V1.3:521 仍写「Office｜尚未接入」** | 同上；这是"中级任务 T4 已完成"的关键证据，被自己的选型表否定 |
| V-CAR 机制消融 + 误修复率指标 | `datamind/ablation.py`、`scripts/eval_vcar_ablation.py`、`data/evaluation/vcar_ablation_v1.json`、`docs/VCAR.md` | §18.10 记载；**但 §1.2 表 V1.3:59 仍写「高级：V-CAR…尚待视觉接入、机制消融与模型效率实验」** | 高级任务申报（若做）会因自述矛盾被质疑 |
| 统一任务预算 / 进程组终止 / 跨进程资源槽 / 租约 | `datamind/task_budget.py`、迁移 013、`docs/TASK_BUDGET.md` | §18.5、§18.20 记载 | ✅ 覆盖尚可 |
| 直连上游（移除 4000 网关） | `datamind/providers.py`、`datamind/settings_store.py`、commit `4c246b3` | §18.24 记载完整 | ✅ 但 README.md:72 仍提及「本地网关」预设项，措辞需核对 |
| 契约关系被检索侧消费（引用载荷 `relations`） | `docs/PARSE_CONTRACT.md`、V1.3:857 | §18.23 记载 | ✅ |
| **API Key 存放位置与"接口只回显掩码"** | `datamind/settings_store.py`、`app_settings` 表 | README.md:72 记载 | ⚠️ 但**未提示 `pg_dump` 会明文泄漏**（见 §6.1） |

### 8.2 文档里有、代码/交付物里没有（文档承诺缺口）

| 文档承诺 | 出处 | 实际 |
|---|---|---|
| 「统一目标答案将包含 **Claim**、证据 ID、版本快照、冲突/缺口与校验状态」 | V1.3:453 | Claim 表存在（195 行），但"统一目标答案"未实现；V1.3:453 自己也标注为"目标" |
| 「文件类型/大小/**页数**/时长限制、解析隔离、deadline/取消传播与并发预算、**密钥引用和脱敏**」 | V1.3:544 | 列为"目标运行保障"；实测未全部落地（`docs/TASK_BUDGET.md` 自述非 cgroup 硬隔离） |
| 「OpenAPI」口径 | V1.3:453 | 「不能将示意响应直接作为当前 OpenAPI」—— 即无正式 OpenAPI 文件，评审无法据此对接 |
| 「约 40 份核心文件 + 约 60 条问题」 | V1.3:399 | 当前 30 份样本 / 30 条评测用例，**未达成**（文档已诚实标注） |
| 「中文别名/枚举召回、**向量召回**及其权重调参」 | README.md:78 | 「向量召回尚未接入」—— 已诚实标注 |

### 8.3 影响评审理解的版本/口径矛盾（★ 必改）

| 矛盾 | 位置 | 问题 |
|---|---|---|
| **API 版本四处不一致** | V1.3:5「0.33.0 基线」、V1.3:7「代码 API 0.33.0」、V1.3:16「当前 API 版本为 0.33.0」、V1.3:626「代码 API 0.33.0」 **vs** V1.3:855/865/877「API 0.37.0」**vs** README.md:13/15「0.37.0 基线」**vs** PROGRESS.md:7「0.37.0」 | 同一文档头部说 0.33、末尾说 0.37。P2 门禁要求"版本一致"，此处不满足 |
| V1.3:10 方案摘要 | 「当前（**API 0.18.0**）已实现…」 | 第三个版本号 |
| 文档日期 | V1.3:7「实施证据截止 2026 年 10 月 7 日」 | 但 §18.24/18.25 记载 10 月 8 日、文件 mtime 10-08 16:37 |
| 回归分母 | README.md:17「完整隔离回归 **502/502**」、README.md:38「本轮隔离回归 465/465」**vs** PROGRESS.md:8「**845/845**（当前唯一权威值）」 | README 的多处历史分母已被 PROGRESS.md:14 明确宣布为非当前值，但 README 未同步 |
| 知识库计数 | README.md:156「26 份文档 / 169 切片」、README.md:178「26 文档、169 切片」、README.md:89「169 向量点」**vs** 实测 30 / 184 | 见 §7 Q10 |
| 元数据表数 | README.md:106「17 张表」**vs** `deploy/init/02_metadata.sql` 18 处 `CREATE TABLE` | 见 §3 #2 |

这些不是排版问题，而是**同一份交付文档自相矛盾**，直接冲击 J2「系统测试与性能评估」与 P2「版本/数据一致」门禁。

---

## 9. 结论与最小行动清单（按依赖顺序）

**结论：距交付差距不在算法，而在"交付工程"整条线。** 技术侧 P1c/P2/P3 大部分已完成（845/845 隔离回归、30 条评测、数据自洽性除他方产物外全部通过），但 J1/J2/J3/J7/J8/J10 对应的交付物**全部缺失或不合格**，且这一状态是项目**主动选择**的（PROGRESS.md:27）。若今天打包，风险点是：真实密钥泄漏、他方产物污染样本集、2.2 GB 体积、全新机器卡在 BGE 下载、以及没有 Word/PDF/PPT。

### 必做（阻塞交付）

1. **轮换 2 把上游 API Key**，并从运行库清除 `model.gateway` 行；确认交付物不含 DB dump（§6.1）。
2. **移除他方产物**：`data/knowledge_base/field-test-*.pdf`（3 份）+ 已提交的 `reports/project_audit_20261008_2d9ab18.md` / `..._review.md` + 未跟踪的 `..._checks.json`；同步清除库内 3 份 doc、15 切片、18 字段、15 向量点、3 个页图目录（§6.2、§7 Q10）。
3. **修正 `.gitignore`**：把 KB 样本改为白名单策略（只允许 `manifest.json` 列举的文件），加入 `output/`、`*.log`、`reports/project_audit_*`（§6.4）。
4. **生成两类 Word+PDF 技术文档**：≤20 页设计报告（含第 1–2 页亮点预览页）+ ≤30 页技术实现说明书（含 J2 七模块），并解决 §8.3 的版本/口径矛盾（§3 #6）。
5. **补齐模型权重清单**（名称/用途/URL/许可/SHA256/体积/是否随包）与**官方九项映射表**（§3 #4、#13）。
6. **修 `requirements.lock`**（补 reportlab/matplotlib/img2pdf/pyarrow）并统一 Python 版本口径（§3 #5）。
7. **用 `git archive` 白名单打包 ZIP**，产出后解压自检：无 `.env`、无 `.cache`、无 `.venv*`、无密钥、`du -sh` ≤ 50 MB（§3 #12）。
8. **答辩 PPT**（≤20 页，无识别信息）（§3 #11）。

### 建议（提分项，非阻塞）

9. 补「系统效率与伸缩性」章（J7，5 分）—— 复杂度分析 + 两种规模实测。
10. 决定是否实现多轮对话（J8，5 分），并在文档中明确声明。
11. 在 README「六、快速开始」显式写入：**必须自备上游 API Key**；并让 `start_local.sh` 在无 `HF_ENDPOINT` 时给出中文提示或自动指向 hf-mirror（当前仅 `reset_demo.sh:40` 有该设置）。
12. 让 `/health` 增加 Qdrant 可达性与 embedding 加载状态的显式字段，避免"knowledge: ready"掩盖降级。
13. 补 1–2 份 Office（DOCX/XLSX）样本进知识库，使"涵盖不同类型文档格式"更站得住（J3 / 判据 C3）。

---

## 10. 只读声明

本次审查为**只读**：

- **未打包**（未执行任何 `zip`/`tar`/`git archive`）；
- **未删除**任何文件（未删除 field-test PDF、未删除 audit 产物、未删除孤儿页图）；
- **未修改**任何业务文件、代码、数据库、配置或 git 索引；
- **未执行**被禁止的命令（无 `git` 写操作、无格式化、无 `docker compose` 子命令、无 `scripts/reset_demo.sh`、无 `scripts/verify.py`、无 `ingestion/ingest.py`、无 `scripts/test_isolated.py`）；
- 数据库访问全部为只读 `SELECT`；Qdrant 访问全部为只读 `GET`/`points/count`。

**唯一写入的文件是本报告**：`reports/review_delivery_readiness.md`。
