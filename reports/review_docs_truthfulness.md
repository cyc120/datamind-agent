# 文档与记录真实性审查报告（只读审查）

审查日期：2026-10-08
审查角色：只读审查者（未修改任何被审文件；本报告为唯一新建文件）
审查范围：`PROGRESS.md`、`README.md`、`技术文档/DataMind_Agent_技术设计说明书_V1.3.md`（下称 V1.3）、`docs/*.md`（重点 `AUDIT_FIXES.md`、`PARSE_CONTRACT.md`、`TASK_BUDGET.md`、`VCAR.md`）、`reports/audit_fix_verification.json`（下称 JSON）。
不在审查范围：`data/knowledge_base/field-test-*.pdf`、`reports/project_audit_20261008_*`（另一 agent 产物）；`技术文档/…V1.2.md` 与 V1.2 章节（冻结历史，仅核对是否被错误当作现状引用）；`tmp/audit-baseline/**`（旧快照）。

结论摘要：**发现 3 项 P1、9 项 P2、5 项 P3**。其中 P1 集中在「README 仍把 465/465 当作当前隔离回归」「V1.3 头部/§18.1 仍宣称 API 0.33.0（摘要处甚至 0.18.0）」「`docs/PARSE_CONTRACT.md` 声称 Office/CAD 解析器未实现」。所有 Markdown 相对链接（77 条）与锚点**全部有效，0 失效**。

---

## 1. 方法与实际命令

全部为只读操作；未运行被禁命令（`scripts/reset_demo.sh`、`scripts/verify.py`、`ingestion/ingest.py`、`scripts/test_isolated.py`），未执行任何 git 写操作、格式化或 `docker compose` 子命令。

| 目的 | 命令（要点） | 结果 |
|---|---|---|
| 版本权威值 | `grep -n "version" datamind/api.py` | `datamind/api.py:166` → `version="0.37.0"` |
| 运行态版本 | `curl -s http://127.0.0.1:8000/openapi.json`（Python 解析 `info`） | `{"title":"DataMind Agent","version":"0.37.0"}`；`paths=72` |
| 健康/计数 | `curl -s http://127.0.0.1:8000/health` | `status=ok, database=ready, knowledge=ready, document_count=30` |
| 容器绑定 | `docker ps --format '{{.Names}}\t{{.Ports}}'` | `datamind-postgres 127.0.0.1:5432->5432/tcp`；`datamind-qdrant 127.0.0.1:6333-6334->…`；**无 new-api-local** |
| 数据实情 | `docker exec datamind-postgres psql -U postgres -d datamind_meta -tAc "…"` | `30 文档 / 184 切片 / 41 事实 / 33 定位行 / 0 契约块 / 0 关系`；`data_sources=3`；`indexed 文档=30`；集合 `demo_kb=15, ohrbench_kb=14, industrial_demo=1` |
| 向量点 | `curl -s http://127.0.0.1:6333/collections/datamind_knowledge` | `points_count=184` |
| 直连上游 | SQL 读 `app_settings` | 启用配置 `model.profiles` → `base_url=https://api.deepseek.com/v1`、`model=deepseek-flash`；另有遗留 `model.gateway` 行指向 `http://localhost:4000/v1` |
| 端点行为 | 读 `datamind/api.py:994-1010`、`/tmp/openapi.json` 的 `/v1/documents/{document_id}/contract` | docstring 明确「blocks=Office/DXF、locators=PDF」；`operationId=document_contract_v1_docume…` |
| 关系载荷 | 读 `datamind/rag_engine.py:93-192`、`ingestion/contract_store.py:116-141`、`datamind/agent.py:219-223`、`web/app.js:853-887` | 关系进入引用载荷、`peer_block_id` 命名、前端渲染均存在 |
| 适配器 | 读 `ingestion/office.py:290`、`ingestion/cad_adapter.py:173`、`ingestion/ingest.py:121` | 三个 `register_adapter`（pdf-native / Office / dxf-native）均存在 |
| 验证脚本隔离 | 读 `scripts/verify.py:21-71,76-95` | 仅用 `readonly_user`；`conn.autocommit=False`；探针在 `SAVEPOINT` 内并 `rollback`；`derive_expectations()` 动态推导 |
| 数字扫描 | grep `完整隔离回归 …N/N`、`\d+/\d+`、`当前数据/当前共/当前仍` | 见 §2、§3 |
| 链接检查 | Python 正则提取 22 个 md 文件的 `[..](..)`，按文件目录解析并校验存在性 + 标题锚点 | 相对链接 77 条，**失效 0**；锚点错配 **0** |
| 路径引用 | 提取反引号内的 `.py/.md/.json/.sql/...` 路径并校验 | 见 §4；唯一「不存在」的合格路径 `ingestion/router.py` 在原文已标为「规划」 |

**当前权威值（依 `PROGRESS.md:8`）**：完整隔离回归 **845/845**（失败/错误 0，由 `scripts/test_isolated.py` 在克隆库执行）。**本次未运行该回归**（被禁），故此处仅转述文件内权威值，未独立验证；`PROGRESS.md:14` 明确 845/845 是「当前唯一权威值」。

---

## 2. 不一致表

| 编号 | 严重度 | 标题 | 文档位置 | 实情与证据 | 建议修法与验收标准 | 置信度 |
|---|---|---|---|---|---|---|
| D01 | **P1** | README「当前进度」仍把历史分母 **465/465** 当作本轮/当前隔离回归 | `README.md:15`（「## 当前进度（2026-10-08，代码 API 0.37.0）」）、`README.md:17`（「完整隔离回归 **465/465**」）、`README.md:38`（「组件/集成回归 \| 本轮隔离回归 465/465（411 + 14 + 8 + 32）」） | 权威值 `PROGRESS.md:8` = **845/845**；`PROGRESS.md:14` 把 465/465 列为**历史分母**。README 这两处位于「当前进度 / API 0.37.0」标题之下，属把历史值当现状 | 将 `README.md:17`、`README.md:38` 的 465/465 改为 **845/845**，并加「(API 0.37.0)」与「历史分母见 PROGRESS.md」字样；把 411/338/292/480/502 等明确标为历史。验收：`grep -n "465/465" README.md` 仅出现在显式历史段落，且 `grep -n "845/845" README.md` 至少 1 处 | 高 |
| D02 | **P1** | V1.3 头部、摘要与 §18.1 仍宣称旧版本（0.33.0 / 0.18.0） | `技术文档/DataMind_Agent_技术设计说明书_V1.3.md:5`（「0.33.0 基线」）、`:7`（「代码 API **0.33.0**」）、`:10`（「当前（API **0.18.0**）已实现…」）、`:16`（「当前 API 版本为 **0.33.0**」）、`:626`（「代码 API **0.33.0**」） | 实情：`datamind/api.py:166` `version="0.37.0"`；`/openapi.json` `info.version=0.37.0`；`PROGRESS.md:7,12` 与 `README.md:13,15` 均称 V1.3 为「0.37.0 基线」。同文件 §18.20–§18.25（`:825-885`）已记录 0.34.0→0.37.0，头部却停在 0.33.0 | 把 `:5/:7/:16/:626` 更新为 0.37.0，`:10` 的「API 0.18.0」改为「API 0.37.0」；`:626` 同时删去「网关容器仍在运行」（网关已于 0.37.0 移除，见 D17）。验收：V1.3 内不再出现「当前…0.33.0/0.18.0」表述 | 高 |
| D03 | **P1** | `PARSE_CONTRACT.md` 声称「**未实现 Office/CAD 新解析器**」，与同文件正文和代码矛盾 | `docs/PARSE_CONTRACT.md:149` | 代码存在：`ingestion/office.py:290`（注册 Office 适配器）、`ingestion/cad_adapter.py:173`（注册 `dxf-native`）；同文件 `:110-123`「图纸适配器（API 0.30.0）」与 `:136-148`「Office 适配器（API 0.29.0）」正是这些解析器；PROGRESS `:47-48`、V1.3 `:781-799` 均记为已实现 | 将 `:149` 改为「Office/CAD 解析器已实现（API 0.29.0/0.30.0），`office_object`/`sheet_range`/`cad_entity` 均由对应适配器产出」。验收：该条不再出现「未实现…解析器」，且与 `:130` 的「已注册 PDF、三种 Office 与原生 DXF」一致 | 高 |
| D04 | **P2** | V1.3 §18.1「当前能力状态总表」把已实现项列为未实现 | V1.3 `:632`（「Office/CAD 适配器未实现」）、`:643`（「Office/工程通用适配 \| 未实现」）、`:644`（把「机制消融」列入尚缺） | 实情：Office/DXF 适配器已实现（同文件 `:781-799`；代码 D03）；机制消融已实现（同文件 `:741-749`，API 0.24.0；代码 `datamind/ablation.py` 存在）。同文件 `:632` 与 `:643` 互相重合并同时与 `:781+` 矛盾 | 更新 `:632/:643/:644` 为「已实现（限定：无页概念格式另存契约表）」，并把尚缺改为「图纸 PDF/DWG 转换、合并单元格/公式语义、真实视觉载荷」。验收：§18.1 表中不再有与 §18.10/§18.15/§18.16 冲突的「未实现」 | 高 |
| D05 | **P2** | V1.3 §18.2 表末「**最新**组件/集成回归 603/603」与文件后段及权威值矛盾 | V1.3 `:681` | 权威值 `PROGRESS.md:8` = 845/845；同文件 `:885`（§18.25）已写 **845/845**、`:871`（§18.24）写 844/844。标注为「最新」却给出 603/603，属把历史值当现状 | 把 `:681` 改为「最新组件/集成回归 \| 845/845（API 0.37.0）」或删除该行（`:676` 已单独列 603/603 为历史）。验收：V1.3 中「最新」字样只与 845/845 同现 | 高 |
| D06 | **P2** | `PROGRESS.md`「**当前数据**」计数陈旧 | `PROGRESS.md:74`（「当前数据：27 文档资产、23 候选事实、169 切片/向量」） | 实测：`document_assets=30`、`chunks=184`、`document_facts=41`、Qdrant 184 点；`/health` `document_count=30`；同仓库 JSON `runtime_acceptance` 亦写「30 文档 / 184 切片」；V1.3 `:881` 明记「切片数由 169 升至 184、文档由 27 升至 30」 | 把 `:74` 更新为「30 文档资产、41 候选事实、184 切片/向量」，或改为「见 §运行验收（30/184）」并注明 27/169 为历史。验收：`PROGRESS.md` 中「当前数据」行数字与 `/health`+SQL 一致 | 高 |
| D07 | **P2** | README 多处「当前」知识库计数陈旧且自相矛盾 | `README.md:156`（「（26 份文档 / 169 切片）」）、`:241`（「当前共 27 文档资产、23 条候选字段；原知识库仍为 169 切片/向量」）、`:288`（「当前仍为 27 文档、23 候选事实、169 切片/向量」） | 实测 30/184/41。且 `:156` 的 26 与 `:241/:288` 的 27 在同文件内互不一致，`:178` 的「26 文档」属「2026-10-01 实测」历史（该处应保留） | `:156` 标题改为「（30 份文档 / 184 切片）」；`:241/:288` 的「当前」改为 30/184/41 并注明历史为 27/169。`:178` 保留（已有日期限定），可加「历史」二字。验收：README 中带「当前」的计数只有一组，且等于 30/184/41 | 高 |
| D08 | **P2** | 两处「前端尚未渲染关系字段」与已实现的前端渲染矛盾 | `docs/AUDIT_FIXES.md:108`（「…前端也尚未渲染该字段」）、`docs/PARSE_CONTRACT.md:78`（「前端也尚未展示该字段」） | `web/app.js:620,853-887` 明确渲染 `relations`（含 `peer_block_id`、`citationRelationsNote`）；`PROGRESS.md:54`、V1.3 `:857` 均记已渲染 | 两处改为「关系已随引用在前端渲染（API 0.37.0）；仍**不参与**检索排序/打分/重排/冲突消解」。验收：grep「尚未渲染」「尚未展示」不再命中关系字段 | 高 |
| D09 | **P2** | JSON 的 F03 状态与 `AUDIT_FIXES.md` 及 JSON 自身另一处矛盾 | `reports/audit_fix_verification.json:160`（`"status": "fixed_config_not_yet_effective"`）、`:170`（`"not_effective_yet": "容器未重建：docker ps 仍显示 0.0.0.0…"`）；对照 `docs/AUDIT_FIXES.md:14`（「**已修复并生效**」）、`:46`（「已执行 docker compose … up -d 重建两个容器…`127.0.0.1`」）；JSON 自身 `roadmap_progress.F03_effectiveness.status="done"`（`:397` 附近） | 实测 `docker ps`：`127.0.0.1:5432`、`127.0.0.1:6333-6334`。JSON 的 `findings.F03.status` 停留在「配置已改、尚未生效」，与 MD 表格及自身 `F03_effectiveness` 三方不一致；`findings` 内其余 11 条均为 `fixed` | 把 `findings.F03.status` 改为 `fixed_and_effective`，删除或改写 `not_effective_yet`（保留为「历史：生效需重建容器」）。验收：JSON `findings.F03.status` 与 `docs/AUDIT_FIXES.md:14` 语义一致，且不再出现「未生效」 | 高 |
| D10 | **P2** | JSON `follow_ups` 仍列出已在 0.35.0/0.36.0 完成的事项 | `reports/audit_fix_verification.json:356`（「`public_profile()` 读取侧仍回传 headers/proxy…」）、`:357`（「问答链路…仍不含 proxy…」）、`:361`（「F10 延后：…需 outbox 模式」） | 实情：读取侧脱敏已实现（`datamind/settings_store.py:609-634` `public_profile` 只出 `header_keys`、`redact_proxy`）；问答链路 proxy 已传递（`datamind/sql_engine.py:50-61`、V1.3 `:835`）；F10 已修（`ingestion/ingest.py:446 publish_document`，JSON 自身 `findings.F10.status="fixed"`） | 从 `follow_ups` 删除这三条（或移入「已完成」并标注版本 0.35.0/0.36.0），只保留仍成立项（复合指标递归、口径面窄、跨机反代、include_keys 外带）。验收：`follow_ups` 不含与 `findings.*.status="fixed"` 冲突的条目 | 高 |
| D11 | **P2** | `VCAR.md` 边界节把已完成的机制消融/预算接入写成「未实现/未接入」 | `docs/VCAR.md:70`（「节点仍在执行进程内运行，**未接入 `task_budget`**…」）、`:71`（「**未实现视觉载荷与局部重识别** — §6.4 的机制消融实验…与误修复率、错误消解率统计仍待做」） | 同文件 `:74-103` 记 API 0.23.0 已接入进程组/资源槽/取消；`:41-65` 记 API 0.24.0 已做机制消融并给出误修复率。代码 `datamind/ablation.py`、`scripts/eval_vcar_ablation.py`、`data/evaluation/vcar_ablation_v1.json` 均存在 | 在 `:70/:71` 前加「（本段为 0.22.0 时的边界；0.23.0 已接入预算、0.24.0 已完成机制消融，真实视觉载荷仍待做）」，或直接改写该节。验收：`:67-72` 边界节不再与 `:101-103` 结论冲突 | 高 |
| D12 | **P2** | V1.3 §14 代码落点表「后续增量」列仍列已完成项，且迁移范围陈旧 | V1.3 `:557`（PDF 模块增量列「私有模型回答、统一预算/deadline、点损失修复与孤立回收」）、`:559`（增量列「PDF/Office/CAD Adapter、稳定产物契约」）、`:560`（「`db/migrations/001–012`」） | 上述增量均已实现（0.18.0/0.19.0/0.21.0/0.29.0/0.30.0；V1.3 `:696-799`）；实际迁移为 `db/migrations/001–016`（`ls db/migrations/*.sql \| wc -l` = **16**；`016_relation_target_index.sql` 存在） | 更新 `:557/:559` 的增量列为仍未做项（如「真实认证私有 Qdrant 烟测、自动调度」「图纸 PDF/DWG 转换」），并把 `:560` 改为「001–016」。验收：§14 表中行与 §18 实际进度不冲突；迁移编号区间等于现存文件数 | 高 |
| D13 | **P3** | `TASK_BUDGET.md` 边界条与自身首段矛盾 | `docs/TASK_BUDGET.md:67`（「OCR/公式阶段接入同一预算…仍待完成」） | 同文件 `:3` 明写「**0.20.0** 接入 OCR/公式」；V1.3 `:709-715`、PROGRESS `:110` 均记已完成 | 删去 `:67` 中「OCR/公式阶段接入同一预算」，保留「跨主机租约与授权真实输入验收仍待完成」。验收：该行不再与 `:3` 冲突 | 高 |
| D14 | **P3** | `AUDIT_FIXES.md` 标题范围与表格范围不一致，且列表编号重复 | `docs/AUDIT_FIXES.md:1`（标题「（F01–F11）」）、`:24`（表内含 F12）、`:111-112` 与 `:114-115`（编号 4、4 与 6、6） | JSON `scope` 亦写「修复 F01–F11」，但 JSON `findings` 含 F12；表含 R1 | 标题改为「F01–F12 与 R1」；把「未完成与后续建议」重新编号为 1..n。验收：标题范围与实际条目集合一致，编号无重复 | 高 |
| D15 | **P3** | `PROGRESS.md` 历史分母清单含无据数字 `839` 且枚举不完整 | `PROGRESS.md:14`（「…734、804、839、844」） | 全仓库（`reports/`、`tests/`、`scripts/`、md/json）除该行外**无 839 的任何记录**（grep 无命中）；同文件正文只有 643/650/664/734/804/844 等，从未出现 839。该清单还漏列同文件已记录的 603/607/614/620/630/659 | 删除 839，或补出对应报告；并补齐 603、607、614、620、630、659 的历史分母。验收：清单中每个数字都能在文件内或 `reports/` 找到出处 | 中（数字确无出处为高；是否笔误需作者确认） |
| D16 | **P3** | `PARSE_CONTRACT.md` 结构被破坏：SourceLocator 表被标题切断，章节倒序追加 | `docs/PARSE_CONTRACT.md:31`（`## 写入存储`）插在 `:23-29` 与 `:34-35` 之间；`temporal_segment`/`model_spatial_object` 两行孤立在 `:34-35`；章节顺序为 0.36→0.33→0.32→0.31→0.30→0.29 | 阅读时常被误读为「七类定位器只列了 5 类」。`:23` 自称七类，但表被切断 | 把 `## 写入存储` 移到表之后；按版本升序或加「## 版本追加记录」分隔。验收：`locator_kind` 表 7 行连续，无跨标题断裂 | 高 |
| D17 | **P3** | 遗留网关配置行仍指向已删除的 4000 网关；JSON `version_changes` 只记到 0.34.0 | `app_settings.model.gateway = {"base_url":"http://localhost:4000/v1", "api_key":"sk-Mpwx…"}`（SQL 实测）；`reports/audit_fix_verification.json:363-364`（`api_version=0.37.0` 但 `version_changes` 仅 `datamind/api.py: 0.33.0→0.34.0`） | `gateway/` 目录不存在、`new-api-local` 容器不存在、4000 端口 `lsof` 为空（PROGRESS `:10,18` 的移除结论成立）；但 `app_settings` 仍留有指向 4000 的死配置与明文密钥；V1.3 `:626` 也仍称「网关容器仍在运行」（见 D02） | 清理 `app_settings.model.gateway` 行（仅当确认无回退需求）；`version_changes` 补记 0.35.0→0.37.0 的变更。验收：不再有指向 4000 的活跃配置；JSON 版本变更与 `api_version` 自洽 | 中 |

---

## 3. 抽样核对结论表（≥8 条宣称）

| # | 宣称（出处） | 证据 | 结论 |
|---|---|---|---|
| S1 | `GET /v1/documents/{id}/contract` 同时返回两套存储，分别以 `blocks`/`locators` 返回并给计数（`PROGRESS.md:17`、V1.3 `:883-885`） | `datamind/api.py:994-1010` 返回 `block_count`/`locator_count` 等；`ingestion/contract_store.py:65-90` `read_result()` 依次读 `document_contract_blocks`、`document_block_locators`+`document_blocks`、`document_block_relations`，返回 `blocks/locators/relations`；openapi docstring 一致；SQL 实测 `document_block_locators=33` | **成立** |
| S2 | 契约关系进入引用载荷，且对端字段命名 `peer_block_id`，只附存储中确有的关系、失败不阻断（`PROGRESS.md:53`、V1.3 `:847`、`docs/PARSE_CONTRACT.md:69-78`） | `datamind/rag_engine.py:93-107`（`cited_relations`、`relations` 进 citation）、`:127-140`（`_relation_map` 失败只记录）、`:157-192`（`ask_documents` 传 `relations`）；`ingestion/contract_store.py:116-141`（两向命中、`peer_block_id`、512 上限、空输入不查库）；`datamind/agent.py:219-223` 注入只读 DSN 实现 | **成立** |
| S3 | Office（DOCX/PPTX/XLSX）解析器已注册，超限置 `partial` 并记 `block_limit_reached`/`sheet_row_limit_reached`（`PROGRESS.md:47`、V1.3 `:781-789`） | `ingestion/office.py:290` `parse_contract.register_adapter(...)`；`:91-92` 与 `:244-245` 写入两个超限原因；`requirements-office.txt` 存在 | **成立** |
| S4 | 原生 DXF 已接入统一契约并注册 `dxf-native`，DWG 明确拒绝、不注册假适配器（`PROGRESS.md:48`、V1.3 `:791-797`） | `ingestion/cad_adapter.py:173` `register_adapter(ADAPTER, media_types=[DXF_MEDIA_TYPE], …)`；`docs/PARSE_CONTRACT.md:110-123` 与 V1.3 `:797` 一致 | **成立** |
| S5 | `scripts/verify.py` 不再污染真实库、去掉硬编码规模（`docs/AUDIT_FIXES.md:102-104`、JSON F11） | `scripts/verify.py:21-22` 仅定义 `PG_R`（readonly）用于全部连接；`:66` `conn.autocommit=False`；`:44,54,56` `SAVEPOINT`/`ROLLBACK TO SAVEPOINT`/`conn.rollback()`；`:60-71` `verify_readonly_isolation` 统一 rollback；`:76-95` `derive_expectations()` 全部动态推导。仅剩硬编码为 `PG_R/PG_A` 口令（JSON F11 `known_gaps` 已如实声明） | **成立** |
| S6 | 存储容器默认仅绑回环且已生效（`PROGRESS.md:52`、`docs/AUDIT_FIXES.md:44-48`） | `deploy/compose.yaml:17,34-35` 三处 `127.0.0.1:…`；`docker ps` 实测 `127.0.0.1:5432`、`127.0.0.1:6333-6334` | **成立** |
| S7 | 模型接入为直连上游、不依赖本地网关，网关库与容器已移除（`PROGRESS.md:10,18`、V1.3 `:865-875`） | `datamind/settings_store.py:23` `DEFAULT_BASE_URL="https://api.deepseek.com/v1"`；SQL：启用 profile `base_url=https://api.deepseek.com/v1`、`model=deepseek-flash`；`ls gateway` → 不存在；`docker ps` 无 `new-api-local`；`lsof :4000` 为空。`datamind/providers.py:157-176` 仅构造标准端点 | **成立**（附 D17：`app_settings` 仍留 `model.gateway` 死行） |
| S8 | 本机地址优先直连、显式 proxy 不再作用于 localhost（F12，`docs/AUDIT_FIXES.md:65-67`、V1.3 `:837`） | `datamind/providers.py:169-176`：先 `if is_local(url): httpx.Client(..., trust_env=False)`，其后才用 `profile["proxy"]` | **成立** |
| S9 | profile 读取侧脱敏：敏感头只出键名、proxy 去 userinfo（`PROGRESS.md:20`、V1.3 `:845`） | `datamind/settings_store.py:609-634` `public_profile()` 返回 `header_keys`、`has_proxy`、`proxy_has_credentials`、`redact_proxy(proxy)` | **成立** |
| S10 | F10 摄取顺序协议：先置不可检索并提交 → 写 Qdrant → 写 PG → 置 `indexed`（`PROGRESS.md:20`、V1.3 `:843`） | `ingestion/ingest.py:446` `def publish_document(...)`；`:55-56` 注释与可见性过滤一致；`datamind/knowledge.py:120,144` 等 8 处 `status='indexed'` 过滤；R1 守护 `ingestion/ingest.py:421 ensure_block_ids` + `tests/test_ingest_block_identity.py` | **成立** |
| S11 | 前端缓存版本已提升、引用卡片消费定位与关系（`PROGRESS.md:54`、V1.3 `:857`） | `web/index.html:7-9` 三处 `?v=0.37.0`；`web/app.js:561 runStatusLabel`、`:623-628 citationLocators/citationRegionFrame`、`:808-830`（`:830` `!locator.rotation_degrees` 旋转页守卫）、`:853-887` 关系渲染 | **成立** |
| S12 | 依赖锁 65 个固定版本 + 守护测试（`PROGRESS.md:19`） | `wc -l requirements.lock` = 65；`grep -c "==" requirements.lock` = 65；`tests/test_dependency_lock.py` 存在（3030 字节） | **成立** |
| S13 | 启动经 **16 个迁移**（`PROGRESS.md:17`、V1.3 `:879`） | `ls db/migrations/*.sql \| wc -l` = 16（001…016，含 `016_relation_target_index.sql`） | **成立** |
| S14 | V-CAR 机制消融四条臂、`local` 误修复 2（14.29%）、`full` 0 误修复（`PROGRESS.md:45`、V1.3 `:747`） | `datamind/ablation.py`（13500 字节）、`scripts/eval_vcar_ablation.py`、`data/evaluation/vcar_ablation_v1.json` 均存在 | **成立**（成绩本身为合成集机制替身，文档已如实标注，见 §5） |

**抽样中发现的夸大/不成立**：无。抽样命中 14 条宣称全部在代码/数据中找到对应物；本轮 P1/P2 集中在**文档陈旧表述**（把历史值/旧版本/已完成项写成现状或未实现），而非代码能力造假。

---

## 4. 链接检查结果

对 22 个 Markdown 文件逐个提取 `[文本](目标)`，按所在目录解析相对路径并校验存在性；对含 `#anchor` 的链接额外比对目标文件标题的 GitHub 式 slug。

| 项 | 结果 |
|---|---|
| 相对链接总数 | **77** |
| 失效文件链接 | **0** |
| 失效锚点 | **0** |

逐文件链接数：`README.md` 21、`PROGRESS.md` 9、V1.3 33、`docs/AUDIT_FIXES.md` 2、`docs/MULTIFILE_CONCURRENCY.md` 7、`docs/PDF_KNOWLEDGE_PUBLICATION.md` 4、`docs/PDF_PREPARATION.md` 4、`docs/PDF_PRIVATE_SEARCH.md` 4、`docs/PDF_SNAPSHOT_REGISTRY.md` 2、`docs/PDF_VECTOR_MAINTENANCE.md` 5、`docs/TASK_BUDGET.md` 2、`docs/PARSE_CONTRACT.md` 0、`docs/VCAR.md` 0、V1.2 2、`技术文档/复杂非结构化文件分类与解析技术文档.md` 0、`reports/*.md` 0。

重点核对的锚点（全部有效）：
- `技术文档/…V1.3.md#18-实施状态历史成绩与修订记录` ↔ V1.3 `:622` `## 18. 实施状态、历史成绩与修订记录`（README `:13,:17`、PROGRESS `:162` 引用）。
- `技术文档/…V1.2.md#18-实施进度与下一步验收2026-年-10-月-2-日` ↔ V1.2 `:1039` `# 18. 实施进度与下一步验收（2026 年 10 月 2 日）`（README `:13,:17` 引用）。

**`gateway/` 指向检查**：审查目标内**不存在**任何指向 `gateway/` 的 Markdown 链接（grep 无命中；`README.md:7`、V1.3 `:867-869` 仅以文字叙述其已移除）。唯一指向已删除网关的链接位于审查范围外的旧快照 `tmp/audit-baseline/README.md:7`（`[gateway/README.md](gateway/README.md)`，已失效）——属基线副本，不属本项目当前文档，**不计入缺陷**。

**反引号路径引用检查**：逐一核对 `reports/*.json|txt|md`、`docs/*.md`、`datamind/*.py`、`ingestion/*.py` 等合格路径引用，**全部存在**。唯一不存在的合格路径 `ingestion/router.py`（V1.3 `:559`）在原文已被显式标注为「**规划** `ingestion/router.py`、`parsers/`… \| 当前未创建为通用能力」，属诚实的规划标注，**不算失效引用**。

---

## 5. 已确认一致的部分

1. **版本权威链一致（代码侧）**：`datamind/api.py:166` = `/openapi.json` `info.version` = `PROGRESS.md:7` = `README.md:15` = `reports/audit_fix_verification.json` `api_version` = **0.37.0**。仅 V1.3 自身头部未同步（D02）。
2. **Q2 权威值口径本身正确**：`PROGRESS.md:8` 给出唯一权威值 845/845，`:14` 明确「不跨轮拼接、不得当作当前值」，`:17-19` 的分母演进（844→845）按新→旧排列且与前文自洽；`:22` 的 549/549 显式标注「历史值，当前值见文首」。
3. **边界与未做项诚实**（逐项核对，全部明确写出）：
   - V-CAR 真实视觉载荷与模型规模对比：`PROGRESS.md:11,45,80,171`、V1.3 `:324,576,853`、`docs/VCAR.md:65`、`docs/AUDIT_FIXES.md:109`、JSON `roadmap_progress.vision_model_blocker="blocked_external"` —— 一致标为**外部依赖、不可执行**（本机无视觉模型/权重目录）。
   - F10 残余窗口：孤儿向量、并发同文档无锁、`pdf_maintenance` 只覆盖 `pdf_private_*` 不覆盖 demo 集合 —— `docs/AUDIT_FIXES.md:110`、`PROGRESS.md:52`、JSON F10 `residual_risk` 四处一致。
   - 两套契约存储未合并：`PROGRESS.md:49`、V1.3 `:805,885`、`docs/PARSE_CONTRACT.md:133`、JSON `runtime_acceptance.not_done` —— 一致。
   - profile 请求侧 proxy 仍为替换语义：`PROGRESS.md:55`、`docs/AUDIT_FIXES.md:113`、V1.3 `:861` —— 一致，且给出「探活路径无实际缺口」的理由。
   - `include_keys=True` 凭据外带：`docs/AUDIT_FIXES.md:58`（「一旦被调用就是完整凭据外带」）、`:111`、V1.3 `:845,853` —— 一致，未粉饰。
4. **合成材料与真实成绩分离**：`PROGRESS.md:63,64`、`:129,142,146`、V1.3 `:659,683`、`README.md:24` 均标注「合成/非独立工厂盲测」；V-CAR 消融在 `docs/VCAR.md:65`、V1.3 `:749` 明确「机制消融而非模型精度」；RAG「10/12、Recall@5 11/11」在 `README.md:178`、V1.3 `:653` 均带日期与「小样本」限定。
5. **机制与效果措辞**：`docs/AUDIT_FIXES.md:80` 用只读基线反证修复非空转（500 块仅 1 个唯一 id、旧 `app.js` 8 项中 7 项失败），把「机制/契约」与「效果」分开陈述，未以机制存在冒充效果。
6. **JSON 与 MD 的状态一致性**：`findings` 中 F01/F02/F04/F05/F06/F07/F08/F09/F10/F11/F12 共 11 条 `status="fixed"`，与 `docs/AUDIT_FIXES.md:12-24` 表格逐条对应；F03 为唯一例外（D09）。JSON 无任何 `deferred`/`partial` 状态残留于 `findings`。
7. **前端接缝守护**：`web/app.js:830` 的旋转页拒绝绘制判断、`:561` 的 `runStatusLabel`、`web/index.html:7-9` 的 `?v=0.37.0` 均与 `PROGRESS.md:54`、V1.3 `:857` 所述一致。

---

## 6. 声明

本次为**只读审查**。除本报告 `reports/review_docs_truthfulness.md` 外，**未修改、新建或删除任何文件**；未执行 `git` 写操作、格式化工具、`docker compose` 任何子命令，未运行 `scripts/reset_demo.sh`、`scripts/verify.py`、`ingestion/ingest.py`、`scripts/test_isolated.py`。全部数据侧结论来自只读 SQL（`psql -tAc "SELECT …"`）、只读 HTTP（`/health`、`/openapi.json`、`/v1/documents`、Qdrant collection 元数据）、`docker ps` 与文件读取。**845/845 是转述 `PROGRESS.md:8` 的权威值，本次未运行全量回归验证。**
