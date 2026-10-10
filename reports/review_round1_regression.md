# 独立复查（回归影响）：第 1 轮全量隔离回归复查

> 本文件同时充当结论卡与复查报告。复查者角色约束为「只新增 `reports/review_round1_regression.md`」，
> 因此按纪律要求的固定字段放在文首结论卡，明细在后文；未另建 `reports/cards/` 新文件。
> 复查者未修改任何被复查文件；除本文件外未新增任何仓库文件；未执行任何 git 写操作。

---

## 结论卡（固定字段）

| 字段 | 内容 |
|---|---|
| **WP** | 独立复查（回归影响）；覆盖被复查对象 WP1a / WP1b / WP2 / WP4 / WP5a / WP5b / WP6 / WP7 / WP8 / WP9 / WP10 的全部未提交改动 |
| **日期** | 2026-10-09（北京时间 23:13；全量回归 `ran_at` = `2026-10-09T15:11:17.460816+00:00`） |
| **基线提交** | `e4e2ebf`（`git rev-parse --short HEAD`；PROGRESS.md 声明的回归基线仍为 `1512439`） |
| **API 版本** | 0.39.0（`datamind/api.py:197`；`/health` 返回 `{"status":"ok",...,"document_count":27}`） |
| **问题** | 本轮各工作包普遍声明「未跑全量、只跑自有测试文件」，个别（WP7/WP4）声称既有 `test_multi_turn.py` 仍 33/33，并普遍以「非跨源路径逐字节不变」等说法支撑「不改变既有行为」。无人跑过全量回归。需要独立确认：全量隔离回归是否守住、新增测试对分母的影响、实现者的既有测试声明是否成立、有无越界改动。 |
| **做法** | ① 真实执行一次全量隔离回归（唯一一次全量，不改任何测试或源码）；② 用 `grep -c 'def test'` 统计本轮 9 个新增测试文件的用例数并与全量分母对账；③ 抽样重跑 3 组既有测试文件（`test_multi_turn.py`、`test_industrial*.py`、`test_parse_contract*.py`）；④ 用只读 `git status --porcelain` / `git diff --numstat` 枚举全部被改的既有文件并逐个判定。 |
| **数据与集** | 隔离克隆库 `datamind_test_*`（由 `scripts/test_isolated.py` 克隆 `datamind_meta` 后立即删库）；题库仍为库内 `evaluation_cases`（nl2sql 14 / rag 12 / robustness 4），本轮未新建题库、未调用上游模型。 |
| **结果（分子分母）** | 全量：**1497 项执行，通过 1491（= 1497 − 6 失败 − 0 错误），失败 6 / 错误 0，退出码 1**。口径注：分母 1497 = 既有 1297（PROGRESS.md 声明，`1512439`）+ 本轮新增 **200**（9 个新测试文件，见 §2）。<br>剔除本轮新增测试文件后（由失败清单推导，未单独重跑）：**1297 项中 5 项红** = 既有真实回归 1 项（`test_agent_api`）+ 文档/记录漂移 4 项（`test_status_docs_sync`）。<br>抽样重跑既有文件：`test_multi_turn.py` **33/33**、`test_industrial*.py` **29/29**、`test_parse_contract*.py` **61/61**，合计 **123/123 全绿**。 |
| **失败样本** | 6 条，全部列出（原样摘录见 §1/§3）：①`test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable` → `AssertionError: 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT' != 'ROUTE_UNSUPPORTED'`（**真实既有测试回归**）；②`test_extensible_fields…test_registry_attribute_contract_is_reused_without_ddl` → `AssertionError: '?? db/migrations/019_evidence_graph.sql' != ''`；③–⑥`test_status_docs_sync` 4 项 → 记录文件为 `33/33`、`commit e4e2ebf`，而 PROGRESS.md 仍写 `1297/1297`、`1512439`（分母/提交漂移）。 |
| **复现命令** | 全量：`cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py`（约 240 s）；抽样：`… scripts/test_isolated.py --pattern test_multi_turn.py`（其余两组把 pattern 换成 `test_industrial*.py`、`test_parse_contract*.py`）。 |
| **诚实边界（不能据此声称什么）** | ① **不能声称「既有 1297 项在当前工作区仍全绿」**：剔除新增测试后仍有 5 项红。② **不能声称「1497/1497 全绿」**：退出码 1、6 失败。③ 剔除新增文件后的 1297 分母是**由失败清单与文件新增清单推导**的，不是另跑一次既有全量套件得到的实测值。④ 抽样只覆盖 3 组共 123 项（约占既有 1297 的 9.5%），不能外推为「其余 1174 项也无回归」。⑤ 4 项漂移失败的根因在共享记录文件与 PROGRESS/README 的写权限之外，复查者**没有**、也**不允许**修 PROGRESS.md/README.md，故该 4 项在修复前会持续为红。⑥ 全量回归会覆写 `reports/latest_regression.json`（`scripts/test_isolated.py:109-110` 的设计行为），本次亦已覆写；复查者未手工编辑该文件。**该文件最终停在最后一次抽样运行的值 `{"tests": 61, "failures": 0, "commit": "e4e2ebf", "ran_at": "2026-10-09T15:11:55.046873+00:00"}`（不是全量的 1497/6），复查者按「只新增一个文件」的约束未做任何还原。** ⑦ 本次未调用上游模型、未写演示库、未重启容器、未停止 8000 服务。 |
| **评分联动** | 回归守不住直接影响「新代码不得改变既有行为」的纪律项（提升计划 §1.5）与 E1/E2/E5/E6/F4/F5 的**证据可信度**（既有契约测试是这些分项的第一道防线）；不直接产生任何得分，也不能据此扣任何官方分。被 `test_agent_api` 打破的旧契约正是 WP2 为 F1（多源融合 0→6）主动替换的路由行为，属于「计划内行为变更但未同步既有断言」的欠账。 |

---

## 1. 全量隔离回归原始结果

**命令（唯一一次全量）**

```
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py
```

**退出码：`1`**（`EXIT_CODE=1`，由运行包装原样记录到日志末行）

**原始尾部输出（原样摘录）**

```
Ran 1497 tests in 239.655s

FAILED (failures=6)
```

**harness 自己写出的汇总 JSON（原样摘录）**

```json
{"tests": 1497, "failures": 6, "errors": 0, "metadata_isolated": true, "model_settings_copied": false, "private_pdf_snapshot_rows_copied": false, "private_pdf_knowledge_rows_copied": false, "source_database_modified": false, "commit": "e4e2ebf", "ran_at": "2026-10-09T15:11:17.460816+00:00", "failed_tests": ["test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable", "test_extensible_fields.OriginalDomainRegressionTest.test_registry_attribute_contract_is_reused_without_ddl", "test_status_docs_sync.StatusDocumentsSyncTest.test_progress_states_the_recorded_denominator", "test_status_docs_sync.StatusDocumentsSyncTest.test_readme_states_the_same_denominator_and_version", "test_status_docs_sync.StatusDocumentsSyncTest.test_the_baseline_commit_is_recorded", "test_status_docs_sync.StatusDocumentsSyncTest.test_the_sync_script_reports_no_drift"]}
```

**逐条失败原文（原样摘录关键断言行）**

| # | 测试 | 原始断言 |
|---|---|---|
| 1 | `test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable`（`tests/test_agent_api.py:111`，**既有文件**） | `AssertionError: 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT' != 'ROUTE_UNSUPPORTED'` |
| 2 | `test_extensible_fields.OriginalDomainRegressionTest.test_registry_attribute_contract_is_reused_without_ddl`（`tests/test_extensible_fields.py:225`，本轮新增文件） | `AssertionError: '?? db/migrations/019_evidence_graph.sql' != ''` |
| 3 | `test_status_docs_sync…test_progress_states_the_recorded_denominator` | `AssertionError: Regex didn't match: '\| 完整隔离回归 \| \*\*33/33\*\*' not found in '# DataMind Agent 项目进度…\| 完整隔离回归 \| **1297/1297**…'` |
| 4 | `test_status_docs_sync…test_readme_states_the_same_denominator_and_version` | `AssertionError: '**当前完整隔离回归 33/33**' not found in '# DataMind Agent — 多模态数据驱动的可解释精准问数…'` |
| 5 | `test_status_docs_sync…test_the_baseline_commit_is_recorded` | `AssertionError: '\| 验证基线 \| 提交 `e4e2ebf`' not found in '# DataMind Agent 项目进度…'` |
| 6 | `test_status_docs_sync…test_the_sync_script_reports_no_drift` | `AssertionError: 1 != 0 : status drift in: PROGRESS.md, README.md` |

**原始失败归因（不做任何改写）**

- #1 是**真实的行为回归**：`tests/test_agent_api.py:108-111` 是既有冻结契约测试，断言跨源问句返回 `ROUTE_UNSUPPORTED`；WP2 按计划把该分支换成证据图引擎后，实际返回 `CROSS_SOURCE_EVIDENCE_INSUFFICIENT`。**没有任何工作包的结论卡提到这条测试**（WP2 卡只自曝了 #2）。
- #2 由 WP2 新增的未提交 `db/migrations/019_evidence_graph.sql` 触发（该断言检查 `git status --porcelain -- db/migrations` 为空）。WP2 卡第 10 条已如实自曝此条，属「自造交付物踩到他人守护断言」。
- #3–#6 是**纯文档/记录漂移**，不是代码缺陷：测试运行期间读到的 `reports/latest_regression.json` 是某个工作包**只跑自己测试文件**时被 harness 覆写的残留（`"tests": 33`、`"commit": "e4e2ebf"`），而 `PROGRESS.md`/`README.md` 仍声明 `1297/1297`、`1512439`。修复方式是按同一口径重跑全量并把新分母写进 PROGRESS.md/README.md —— 两者都在复查者与实现者的允许写集之外。

---

## 2. 分母口径：新增测试数与「剔除新增后是否仍全绿」

### 2.1 本轮新增测试文件与用例数（`grep -c 'def test' <file>`）

| 新增测试文件（`git status` 显示均为 `??`） | `def test` 行数 |
|---|---:|
| `tests/test_clarification_flow.py` | 23 |
| `tests/test_complexity.py` | 19 |
| `tests/test_cross_source.py` | 33 |
| `tests/test_eval_registry.py` | 15 |
| `tests/test_eval_scale_v2.py` | 31 |
| `tests/test_extensible_fields.py` | 17 |
| `tests/test_frame_multi_turn.py` | 30 |
| `tests/test_qa_samples.py` | 16 |
| `tests/test_schema_linking_v2.py` | 16 |
| **合计** | **200** |

**对账**：全量实测分母 1497 − 声明基线 1297 = **200**，与上表合计**逐位吻合**。这同时反向验证了（a）本轮没有向任何**既有**测试文件追加用例（`git status` 也显示 `tests/` 下无 `M`），（b）`grep -c 'def test'` 在这 9 个文件上没有多计/少计。抽样单文件亦吻合：`test_cross_source.py` 计 33、隔离实跑 `{"tests": 33}`；`test_extensible_fields.py` 计 17（卡片亦自称 17）。

### 2.2 剔除本轮新增测试文件后是否仍全绿

**结论：否，1297 项中仍有 5 项红。**

推导依据（**不是**另跑一次既有套件得到的实测值）：全量 6 条失败中，唯一落在本轮新增测试文件里的是 #2（`test_extensible_fields.py`）；其余 5 条（#1 `test_agent_api.py`、#3–#6 `test_status_docs_sync.py`）所在文件均为既有文件（`git ls-files` 可证）。因此剔除 9 个新增文件后：

| 口径 | 分母 | 失败 | 错误 |
|---|---:|---:|---:|
| 全量（本轮工作区） | 1497 | 6 | 0 |
| 剔除本轮 9 个新增测试文件（推导） | 1297 | 5 | 0 |
| 其中：既有真实回归 | — | 1（`test_agent_api`） | 0 |
| 其中：文档/记录漂移 | — | 4（`test_status_docs_sync`） | 0 |

**必须同时说明的两种读法（防止被误读为「既有代码大面积回归」）**：

1. 若把工作区还原成 **HEAD 干净态**（即 `reports/latest_regression.json` 回到已提交的 `{tests:1297, commit:1512439}`、无未提交迁移/新文件），#2 与 #3–#6 这 5 项**都不会红**（推断：这 5 条的失败原因分别直接来自该记录文件与未提交迁移，而非代码行为）；此时既有 1297 项中只剩 #1 一条红。即：**真正的代码级回归仅 1 条**，其余 5 条是「未提交产物 + 共享记录文件被局部运行覆写 + 文档未同步分母」造成的。
2. 但在**当前工作区**上，既有 1297 项确实有 5 条红，#3–#6 在 PROGRESS.md/README.md 被更新前会**持续**为红（`test_status_docs_sync` 的机制设计要求如此）。

**局限**：复查者按纪律未再跑一次「仅既有文件」的全量套件（本轮全量只跑一次；`--pattern` 只用于自有/指定文件）。上表 1297/5 是**推导值**，不是实测值。

---

## 3. 逐项核对实现者「既有测试仍通过」的说法

### 3.1 抽样重跑（真实执行，均退出码 0）

| 抽样命令 | 命中文件（既有） | 实测结果（原样摘录 summary） | 退出码 |
|---|---|---|---|
| `… scripts/test_isolated.py --pattern test_multi_turn.py` | `tests/test_multi_turn.py`（33 项） | `{"tests": 33, "failures": 0, "errors": 0, "metadata_isolated": true, "model_settings_copied": false, "private_pdf_snapshot_rows_copied": false, "private_pdf_knowledge_rows_copied": false, "source_database_modified": false, "commit": "e4e2ebf", "ran_at": "2026-10-09T15:11:43.691900+00:00", "failed_tests": []}` | `0` |
| `… scripts/test_isolated.py --pattern 'test_industrial*.py'` | `test_industrial.py`(11) + `test_industrial_forms.py`(10) + `test_industrial_query.py`(8) | `{"tests": 29, "failures": 0, "errors": 0, …, "commit": "e4e2ebf", "ran_at": "2026-10-09T15:11:50.425193+00:00", "failed_tests": []}` | `0` |
| `… scripts/test_isolated.py --pattern 'test_parse_contract*.py'` | `tests/test_parse_contract.py`（61 项） | `{"tests": 61, "failures": 0, "errors": 0, …, "commit": "e4e2ebf", "ran_at": "2026-10-09T15:11:55.046873+00:00", "failed_tests": []}` | `0` |

抽样合计 **123/123 全绿**（占既有 1297 的 9.5%）。三组日志中均出现 `applied 019_evidence_graph.sql` 与 `industrial-demo-v1: 3 parts, 12 batches, 13 measurements; 17 native PDF facts; 296 locators`，说明隔离克隆库确实按当前迁移集重建、工业种子确实重放（不是空跑）。

### 3.2 与实现者声称的对照

| 工作包 | 实现者声称 | 复查结果 | 判定 |
|---|---|---|---|
| WP7 / WP4 | 「回归护栏 `test_multi_turn.py` **33/33**（既有 33 项继续通过）」 | 抽样实跑 `{"tests": 33, "failures": 0}`，退出码 0 | **一致** |
| WP5a（可扩展要素提取） | 未声称跑过既有回归；其测试内断言「本 WP 不得新增/改动迁移文件」 | 该断言被 WP2 的 019 触发为红（16/17）；WP2 卡已自曝 | **一致（双方均已如实记录）** |
| WP5b / WP6 / WP8 / WP9 / WP1a | 均明确写「只跑自有测试文件，未跑全量 1297；全量分母未变」 | 全量实测执行了其自有文件且**全部通过**（新增 200 项中仅 `test_extensible_fields` 1 项红） | **声称成立，但都不构成「既有测试仍通过」的证据** |
| WP1b / WP2 / WP8 / WP10 | 明确写「未跑全量回归 / 不能引用全量回归」 | 与复查发现一致 | **一致** |
| （无人声称） | —— | `test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable` 被 WP2 打破，**所有结论卡均未提及** | **不一致：漏报 1 条既有契约回归** |

**小结**：本轮实现者的「既有测试仍通过」类声称，**凡明确声明过的部分都为真**（WP7/WP4 的 33/33 经独立复跑证实）；但存在**一处未声明的既有测试回归**（`test_agent_api`），因此「本轮不改变既有行为」整体**不成立**。

---

## 4. 越界改动清单（只读枚举）

`git status --porcelain` 与 `git diff --numstat` 的**全部**既有文件改动如下（9 个 `M`；无 `D`，无删除）：

| 既有文件 | +/- | 判定 | 说明 |
|---|---|---|---|
| `datamind/agent.py` | +194 / −9 | **计划内（最小必要接线）** | WP2 跨源分支接线 + WP7 路由前问题帧；只新增 `cross_source_result` 键、`run()` 新增可选 `cross_source_evidence=` 注入缝。 |
| `datamind/api.py` | +31 / −2 | **计划内** | WP4「`NEEDS_CLARIFICATION` 语义在 API/前端一致」：只新增 `clarification_*` 键，`status/answer/error_code` 取值不变。**耦合风险**：`_with_clarification_contract()` 在请求路径内 `from scripts.eval_clarification import …`，让 API 运行时依赖 `scripts/` 下的评测脚本（见 risks）。 |
| `datamind/industrial_rules.py` | +28 / −20 | **计划内，但超出「最小」** | WP2 把单位/版本/一致性四类语义抽到新增 `datamind/binding.py`，本文件只保留工业词表。语义等价性经人工比对（`normalize_unit`/`source_agreement` 状态与 reason 码一致，`temperature()` 对未知单位仍抛 `UNKNOWN_UNIT`）；既有 `test_industrial*.py` 29/29 实测通过，未见行为漂移。仍属对既有模块的结构性抽取，建议实现者保留等价性断言。 |
| `datamind/sql_engine.py` | +32 / −5 | **计划内（最小必要接线）** | 澄清话术提为具名常量（字符串未变）、`ask()` 新增可选 `frame=`、`query_frame` 结果新增只读 `resolution` 说明键。 |
| `scripts/eval_scale.py` | +1224 / −7 | **计划内** | WP8 交付物；`run_v1()` 保留原路径与字段。 |
| `scripts/eval_schema.py` | +362 / −11 | **计划内** | WP6 交付物；`--suite v2` 新增分支，默认仍走 v1。 |
| `web/app.js` | +40 / −0 | **计划内** | WP4 澄清补槽 UI；纯新增函数 + 一处 `showFinal` 追加调用。 |
| `reports/latest_regression.json` | +12 / −5 | **⚠ 可疑（shared-artifact 覆写）** | 非任何 WP 的交付物，是 `scripts/test_isolated.py:109-110` 无条件覆写的共享记录。全量运行前它的内容是某个工作包局部运行的残留 `{"tests": 33, "commit": "e4e2ebf"}`（与 PROGRESS.md 的 1297/1512439 冲突），正是 #3–#6 四条红的直接原因。WP8 卡称已恢复、WP2 卡称未恢复——**实际未能保持字节一致**。 |
| `scripts/start_local.sh` | +3 / −1 | **越界但不属本轮** | 空数组展开改为 `… ${background_args[@]+"${background_args[@]}"}`（macOS bash 3.2 `set -u` 修复）。提升计划 §11 与审计报告均记录「立稿时工作区仅 `start_local.sh` 有未提交改动」，故**先于本轮存在**，不计入本轮实现者越界；但它是本工作区唯一的、提升计划未授权的既有文件改动，仍计入 risks。 |

**补充核查**

- `tests/` 与 `db/migrations/` 下**没有任何既有文件被改**（`git status --porcelain -- tests/ db/migrations/` 只输出新增文件 `??`，无 `M`）。**没有**出现「改测试来迁就实现」的情况。
- `PROGRESS.md`、`README.md`、`requirements*.txt` 未被改动（硬性禁令遵守）。
- 新增文件（`??`）中，除本轮的 WP 交付物（`datamind/{binding,complexity,cross_source,evidence_graph,frame}.py`、`db/migrations/019_evidence_graph.sql`、`data/evaluation/*`、`scripts/eval_*.py`、`tests/test_*.py`、`reports/cards/`、`docs/*`、`交付/`、`data/qa_samples/`、`data/uploads/`）外，未发现与本轮 WP 无关的意外产物；`data/uploads/`、`交付/` 属 WP9/WP10 交付面。

---

## 5. Verdict

> **回归未守住。**

依据与分量：

1. **1 条真实既有契约回归（阻断级）**：`tests/test_agent_api.py:108`「跨源路由必须显式不可用」被 WP2 打破（`ROUTE_UNSUPPORTED` → `CROSS_SOURCE_EVIDENCE_INSUFFICIENT`），且**无任何结论卡披露或改写该断言**。这是提升计划 §1.5「新代码不得改变既有行为」的直接反例——即便该行为变更是 WP2 计划内的目标，也欠一次「既有断言同步/显式声明」的交底。
2. **4 条文档/记录漂移（非代码缺陷、但会持续为红）**：根因是 `reports/latest_regression.json` 被多个工作包的局部隔离运行反复覆写，且新分母（1497）与无提交产物的新提交尚未写入 `PROGRESS.md`/`README.md`。这四条在文档同步前不会自行转绿，会持续污染「全量是否全绿」的判定。
3. **正面结论**：本轮 9 个新增测试文件共 200 项**全部通过**（唯一新增文件内的失败 #2 是「自造迁移踩到他人守护断言」，而非被测功能失败）；既有测试抽样 123/123 全绿（含 WP7/WP4 声称的 `test_multi_turn` 33/33，独立复跑证实）；没有改测试迁就实现、没有改既有迁移、没有删除文件、没有 git 写操作。
4. **可放行条件（供协调者）**：①同步 `tests/test_agent_api.py` 的跨源断言（或显式声明该契约已升级，并给出新稳定码的等价守护）；②把 `db/migrations/019_evidence_graph.sql` 提交入库（#2 自动转绿）；③按 `1497`（或修复后的新分母）重跑一次全量并同步 `PROGRESS.md`/`README.md` 与 `reports/latest_regression.json`（#3–#6 自动转绿）。三项完成前，**不得**在任何材料中引用「1297 项全绿」或「1497/1497」。

---

## 6. risks（写进复查风险清单）

| # | 风险 | 证据 | 建议 |
|---|---|---|---|
| R1 | **既有契约测试被计划内行为变更打破且未交底**，后续材料若引用「既有测试全绿」即失实 | 失败 #1；原始断言 `'CROSS_SOURCE_EVIDENCE_INSUFFICIENT' != 'ROUTE_UNSUPPORTED'` | 由协调者决定：同步断言，或保留新旧两态并显式声明契约升级 |
| R2 | **共享记录文件 `reports/latest_regression.json` 被局部运行反复覆写**，导致「当前分母」在多轮之间互相污染（本轮全流程实测被依次写成 33 → 1497 → 33 → 29 → 61） | WP2 卡 §110、WP8 卡 §156 的自曝 + 实测 4 条漂移失败 + 复查结束时该文件停在 `{"tests": 61}` | harness 应写入带 pattern 的运行标识或按 pattern 分文件；协调者在发布前统一跑一次全量收口并还原为权威值 |
| R3 | **API 运行时依赖评测脚本**：`datamind/api.py` 在请求路径内 `from scripts.eval_clarification import …` | `datamind/api.py` diff；`_with_clarification_contract()` 用 `try/except` 兜底 | 槽位契约下沉为 `datamind/` 内的模块，`scripts/` 只做投影；至少补一条「该导入失败时响应不变」的守护测试 |
| R4 | **`datamind/industrial_rules.py` 结构性抽取**（+28/−20）虽未观测到行为漂移，但既有工业断言粒度可能不足以覆盖全部等价性 | 既有 `test_industrial*.py` 29/29 通过；抽取值人工比对 | 建议实现者补「原实现 vs `binding` 实现」的等价性对照断言（至少覆盖未知单位、K↔degC、维度不匹配、有效期半开区间） |
| R5 | **未提交产物（019 迁移）会把他人守护断言染红**，形成「提交前必然红」的窗口 | 失败 #2（WP2 卡已自曝） | 主协调者先提交迁移再跑全量；或把该断言改为「不得改动已存在迁移」而非「不得出现任何未提交迁移」 |
| R6 | `scripts/start_local.sh` 存在**提升计划未授权**的既有改动（macOS bash 3.2 修复），本轮与审计均未对其做部署验收 | `git diff -- scripts/start_local.sh`；提升计划 §11 记录 | 明确其归属与验收结论，或回退到计划口径后再由授权方提交 |
| R7 | 抽样覆盖面有限（123/1297 ≈ 9.5%），其余 1174 项既有测试在「剔除新增文件」口径下**未被本轮独立复验** | §3.1 抽样范围 | 由协调者在同步 #②③ 后跑一次完整的全量并公布新分母，作为唯一权威值 |

---

## 附：本报告真实执行过的命令与退出码

| 命令 | 退出码 |
|---|---|
| `cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf` | 0 |
| `git status --porcelain` / `git diff --stat` / `git diff --numstat`（只读） | 0 |
| `git ls-files tests/test_agent_api.py tests/test_status_docs_sync.py tests/test_multi_turn.py` | 0 |
| `grep -c 'def test' tests/test_*.py`（9 个新增文件） | 0 |
| `.venv/bin/python scripts/test_isolated.py` | **1** |
| `.venv/bin/python scripts/test_isolated.py --pattern test_multi_turn.py` | 0 |
| `.venv/bin/python scripts/test_isolated.py --pattern 'test_industrial*.py'` | 0 |
| `.venv/bin/python scripts/test_isolated.py --pattern 'test_parse_contract*.py'` | 0 |
| `curl -s http://127.0.0.1:8000/health` → `{"status":"ok",…,"document_count":27}` | 0 |

原始日志（本机临时目录，未入仓）：`/tmp/rev1/full_isolated.log`（468 KB，含 6 条失败完整 traceback）、`/tmp/rev1/p_test_multi_turn.log`、`/tmp/rev1/p_test_industrial.log`、`/tmp/rev1/p_test_arse_contract.log`。

> 本报告为工作产物，不是官方成绩。所有分数联动均为条件值，且**不因本报告产生任何得分或扣分**。
