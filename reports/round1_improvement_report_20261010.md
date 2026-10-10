# DataMind Agent 提升总报告（第三轮·收口轮定稿）

> 本报告由**复核者与撰写者**（非任何被汇总工作包的实现者）产出，保留第一轮的文件名作为本轮总报告。
> 本报告**不是**官方成绩，不含任何自评分；所有分值为《提升计划》与官方档位的**条件目标/可计入性判断**，
> 不因本报告产生得分。纪律：凡数字必附来源文件与产生命令；**分子分母同给**；拒答/超时/上游错误
> **单列但不移出分母**；stub/离线结果**不得**说成真实模型成绩；**没做到的写 `not_attempted`**；
> 被证伪的数字一律按撤回处理并在 §5 保留记录。
>
> 配套机读件与一览卡：`reports/review_round3_final.md`（第三轮独立复核全文）、`reports/cards/*.md`（各 WP 结论卡）、
> `reports/improvement_plan_20261010_competition.md`（计划）、`reports/project_audit_20261009_competition_e4e2ebf.md`（审计）、
> `docs/SCORE_GAP_LEDGER.md`（官方评分行原文抄录）。

| 固定字段 | 内容 |
|---|---|
| **WP** | 第三轮总报告（汇总 WP0–WP10、第二轮修复轮、第三轮收口轮的全部结论卡、机读报告与三轮复查；本报告不新增实验，仅**独立重跑**不调模型的确定性命令） |
| **日期** | 2026-10-10 08:35 CST（`date "+%Y-%m-%d %H:%M %Z"` → `2026-10-10 08:35 CST`，exit 0；计划自署日期同为 2026-10-10，历史台账记录机器时钟曾在其前 1 天，见 `reports/cards/gaps_ledger.md` §3.3 P8） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`，exit 0，只读；工作区含 16 个既有文件改动 + 88 组未跟踪新增，**未提交**） |
| **API 版本** | `0.39.0`（`datamind/api.py:198`；`curl -s http://127.0.0.1:8000/openapi.json` → `openapi version 0.39.0 paths 75`，exit 0） |
| **问题** | 第三轮补出了 6 类新证据，但其中 5 份依赖真实上游模型/运行中服务，且 F1 的档位判定、L4 下沉是否属实、全量回归口径是否与文档一致，都需要一个不参与实现的复核者给出**可计入性判定**；同时第一轮总报告的总报告结构需要按「三轮合并 + 官方逐项对照」重写 |
| **做法** | ① 逐条独立重跑 10 条**不调用模型**的确定性命令（写入 `tmp/review3/`，不覆盖任何被复核报告）；② 对必须调模型的 5 份报告只做**内部一致性 + 真实调用留痕**核对并标为「未经独立重跑」；③ 用字节级探针定位语料确定性门失败的根因；④ 用源码/对象级断言核对 L4；⑤ 读全量回归日志与文档 diff 核对口径；⑥ 按「离线确定性 / 真实模型调用 / stub 模拟」三分口径重排全部数字 |
| **数据与集** | 第三轮 6 类：`data/knowledge_sources/`（14 份自建规范文档，5 PDF + 9 DOCX，30 条条款）、`data/evaluation/cross_source_norm_v1.json`（28 例）、`faithfulness_v1.json`（26 例）、`robustness_v2.json`（21 例/12 类）、`multifile_v1.json`（12 例）、`scale_v2.json`.`profile_live`（12 次真实流式调用）、`binding_ablation.json`（40 例陷阱集）。**全部为自建/合成冻结集，非官方测试集** |
| **结果（分子分母）** | 见 §3。核心：离线确定性 10/10 条命令达到声明结果（含 1 条稳定性主张被证伪）；真实模型：单文档忠实度 19/21、鲁棒性 20/21、多文档 5/9、跨源 live P1 5/6；stub：三臂消融 `full` 错误绑定率 0/40 vs `plain` 32/40。全量回归 **1664 执行 / 1664 通过 / 0 失败 / 0 错误，exit 0（收口轮最终值）**。本报告新增官方得分 **0/100 + 0/100 + 0/20** |
| **失败样本** | 见 §6（20 条，含 `not_attempted` 与阻塞原因）。最关键三条：**F1 仍不能计入 6 分档**；**收口轮最终全量 1664/1664、exit 0；四轮之间文档分母曾三次滞后（1497→1578→1664），现已同步**；**语料字节确定性门 17 次运行 4 次失败（已定位根因）** |
| **复现命令** | §9 逐条列出（含退出码与输出摘录）；复核命令全部只读或写入 `tmp/review3/` |
| **诚实边界（不能据此声称什么）** | ① 本报告**未重跑任何真实模型评测**，表 B 全部为转引 + 内部一致性核对，**未经独立重跑**；② **不能**据此声称任何官方得分、名次或提交资格；③ **不能**把 P2/P3/P4 的 28/28 说成「模型答对了 28 条」——`model_calls = 0`、SQL 由条款确定性编译、文档侧为自建语料；④ 可宣称「当前版本全量隔离回归 **1664/1664、0 失败 0 错误、exit 0**」（`tmp/regression_final_d.log` + `reports/latest_regression.json`，run_at 2026-10-10T01:29Z 起）；**但不得**把该分母说成模型准确率，也不得回填早期轮次的红色记录；⑤ **不能**声称「语料逐字节可复现」（已证伪）；⑥ 表 B 的 5 份报告是在**运行中的旧构建 8000 服务**上实测（`/health` 无 `cross_source` 能力），不是工作区代码的行为 |
| **评分联动** | 见 §4。本轮证据把 E2/E6/E7/F6 从「无独立证据」推到「有独立实测（可计入性上升）」，把 F5 从「无实测」推到「有实测且**未达标**」，把 F1 从「证据完全缺失」推到「4 种结构可复现但**仍不能计入 6 分档**」，把加分从「门槛无法判定」推到「门槛在 stub/自建集下满足、**只能按实用创新下限叙事**」 |

---

## 1. 三句话结论

1. **本轮做了什么**：在第二轮修完 8 条安全/兼容发现之后，收口轮补出了 6 类此前完全缺失的证据——自建规范语料把 P2/P3/P4 的文档侧从「空白」做成可复现（28/28、`model_calls = 0`）、单文档逐结论忠实度（19/21）、鲁棒性 v2（20/21）、多文档固定集（5/9）、端到端六段时延（模型段 1674.2/3238.6 ms）、三臂绑定消融（`plain` 32/40 → `full` 0/40），并把澄清槽位契约从评测脚本**下沉**到生产包（第一轮复查 L4）。
2. **真实拿到了什么**：**不调模型的 10 条评测命令全部由独立复核者复现**（含 P2/P3/P4 的 28/28 逐例 answer 文本零差异、三臂冻结集 `--check` exit 0、非跨源离线 21/21 与澄清 detector 18/18 未回退）；**L4 确实下沉**（`datamind/api.py` 不再从 `scripts/` 导入，评测脚本只做 re-export，4 个契约键逐字未变）。真实模型侧拿到的是**单文档忠实度 19/21、鲁棒性 20/21、多文档 5/9、跨源 live P1 5/6**——都还没到官方档位线。
3. **还差什么**：① **F1 仍不能计入 0→6**（4 种结构里 3 种 `model_calls = 0`、文档侧是自建语料、且有 3 处引擎判定缺陷已冻结）；② **全量回归已在收口轮收敛为 1664/1664、0 失败、exit 0**；此前三轮的分母漂移（1497 → 1578 → 1664）与 `reports/latest_regression.json` 被单文件运行覆盖的问题，按「先同步两文档、再跑一次全量」的两步序列已解决；③ **语料字节确定性门的不稳定已修**（根因是 PyMuPDF 会写出两种 `/ID` 形态而旧正则只认十六进制；改为生成时显式写入确定性 `/ID`，修后 12/12 稳定，14 份语料字节未变）；④ `scale_v2.json` 顶层 `model_segment_policy` 与新 section 的矛盾已修（改为按报告里是否真有 `profile_live` 样本推导，现为 `measured` n=12）；⑤ 三份真实模型报告仍未记录模型标识（遗留）。

---

## 2. 本轮工作包清单表（两轮 + 收口轮全部工作包）

**状态口径**：`done` = 该 WP 声明的交付物齐备且复查未推翻；`partial` = 有实现但有明确未完成面（逐项写明）；`blocked` = 被硬性禁令/外部依赖卡住；`not_attempted` = 计划要求但完全未做。
**`done` 不等于「官方项已达成」**，只表示该 WP 交付物在证据层面成立。

### 2.1 第一轮工作包（WP0–WP10）

| WP | 状态 | 交付物路径 | 关键数字（来源） | 第三轮复查结论 |
|---|---|---|---|---|
| WP0 差距台账 | done | `docs/SCORE_GAP_LEDGER.md`、`reports/cards/gaps_ledger.md` | 官方评分行抄录 27/27；映射表过期 9/9；计划内部不一致 9/9 | 不在第三轮复核覆盖范围；其自证数字未独立复核 |
| WP1-a 冻结登记 | partial | `data/evaluation/registry/{nl2sql,rag,robustness}_v1.json`、`scripts/eval_registry.py` | check **3/3 一致**（第三轮复跑 exit 0，V7）；统一 schema 检查既有报告 0/3 | **confirmed**（V7 复跑）；WP1 第 3 项新增子集仍 `not_attempted` |
| WP1-b 基线复测 | done（含 1 条已更正） | `reports/baseline_current.json`、`nl2sql_current.json`、`rag_current.json`、`robustness_current.json` | nl2sql 13/14、rag 11/12、robustness 4/4 → 28/30；上游错误 0；误拒 1/30 | **confirmed**；第一轮 C116 的事实陈述已撤回并在 §5 保留 |
| WP2 跨源引擎（离线） | partial | `datamind/{binding,evidence_graph,cross_source}.py`、`db/migrations/019_evidence_graph.sql`、`data/evaluation/cross_source_v1.json`、`reports/cross_source_offline.json` | 离线 21/21、断言 161/161、`model_calls 0`；第三轮复跑逐例 verdict 差异 **0**（V5） | **confirmed**（V5）；`--live` 由 WP2-R2 补，只跑通 P1 |
| WP2-R2 跨源真实链路 | partial | `reports/cross_source_live.json`、`reports/cards/wp2_cross_source_live.md`、`tests/test_cross_source.py`（33→50） | P1 **5/6**；P2/P3/P4 `answerable_cases = 0`（0/5 各）；`model_calls 17`；`checks 55/60` | 内部一致；**未经独立重跑**（需模型） |
| WP3 三臂绑定消融 | done（stub） | `data/evaluation/binding_traps_v1.json`、`datamind/binding_eval.py`、`scripts/eval_binding.py`、`reports/binding_ablation.json`、`tests/test_binding_eval.py`（33） | `plain` 错误绑定率 32/40 = 0.800 → `full` **0/40 = 0.000**；冲突 P/R 25/25、25/25；误修复 0/9；`model_calls 0`；第三轮 `--check` **exit 0**（V4） | **confirmed（冻结集与门槛算术）**；`full 40/40` 是陷阱集同源设计结果，**不是泛化证据** |
| WP4 B3 澄清闭环 | partial | `data/evaluation/clarification_v1.json`、`scripts/eval_clarification.py`、`reports/clarification_v1.json`、`web/app.js` | detector **18/18**、deployed **4/18**、不必要澄清 **0/7 与 0/25**、slot_landing 14/14 与 4/4；第三轮复跑逐值一致（V6） | **confirmed**（V6）；线上 API 未返回结构化槽位、六类缺项只接入 detector |
| WP5-a B2 可扩展提取 | done（离线） | `data/evaluation/extensible_field_v1/`、`scripts/eval_extensible_fields.py`、`docs/EXTENSIBLE_EXTRACTION.md` | 声明式臂 120/120；三档示例极差 0.0 pp；引擎改动 0 行 | confirmed（第一轮）；运行时 loader/权限/发布未实现 |
| WP5-b B8 文档复杂度 | done（离线，未过一致性） | `datamind/complexity.py`、`data/evaluation/complexity_v1.json`、`reports/complexity_v1.json` | 26 份材料 / 0 解析失败；期望一致 **20/26**；策略一致 12/12；ρ **0.641 vs 0.5639（不复现）** | confirmed，除 ρ 为 partial；未接入生产摄取 |
| WP6 B4 Schema Linking v2 | done（未过门槛，如实保留） | `data/evaluation/schema_linking_v2.json`、`scripts/eval_schema.py --suite v2`、`reports/schema_linking_v2.json` | 表 65/88 = 73.9%、列 48/56 = 85.7%、指标 10/10、路径 25/25、拒绝 9/9、干扰误选 0/32；命令 **exit 1** | confirmed；失败全为 `global_budget_cut (30)` + `kind_top5_cut (1)` |
| WP7 多轮上下文 | partial | `datamind/frame.py`、`data/evaluation/multi_turn_v1.json`、`reports/multi_turn_v1.json`、`docs/MULTI_TURN.md` | offline 61/61、整段 10/10、可追溯 46/46、越权拒绝 2/2；**live 58/61** | confirmed；**live 未独立重跑**；帧未进提示词、前端会话未接入 |
| WP8 效率与伸缩性 | partial | `scripts/eval_scale.py`、`scripts/build_scale_data.py`、`reports/scale_v2.json`、`docs/PERFORMANCE.md` | SQL 3 档 ×4 查询 p50/p95；并发 1/2/4/8 = 29.43→195.97 次/秒；工具链分段冷 465.094 / 预热 110.877 ms；q4 索引优化 4.240→0.577 ms（×7.348） | confirmed（工具链）；**第三轮补的 `profile_live` 模型段未经独立重跑，且与顶层 policy 矛盾（见 §6-9）** |
| WP9 新文件问答闭环 | partial | `data/qa_samples/`（11 份 / 6 格式 + manifest）、`scripts/eval_qa_samples.py`、`docs/QA_SAMPLE_GUIDE.md` | 样本 11/11、问题 23/23、断言离线重算 23/23；`--check` **exit 0**（V11）；`vector_index=deferred`；入库 1/11 | confirmed；**索引→问答→引用闭环 not_done** |
| WP10 交付材料成套化 | partial | `交付/01–04`（2 DOCX + 3 PDF + 1 PPTX）、`交付/DELIVERY_MANIFEST.json`、`reports/office_checks.json`、`docs/DELIVERY_CHECKLIST.md` | 交付物 6/6；页数 13/12/4/20；结构检查 4/4；PPT 匿名命中 0/0 | confirmed；**本轮已把这套件的证据索引与未完成项同步到第三轮（§9）** |

### 2.2 第二轮（修复轮）

| WP | 状态 | 交付物路径 | 关键数字（来源） | 第三轮复查结论 |
|---|---|---|---|---|
| WP-FIX2 回归口径同步 | partial | `reports/cards/wp_fix2_regression_sync.md`、`PROGRESS.md`、`README.md`、`tests/test_agent_api.py` | 修复前权威全量 1497/1491（6 失败）；`--pattern test_agent_api.py` 5/5、`test_extensible_fields.py` 17/17 | **部分成立**：当时两条已修；但第三轮全量又出现 4 条失败、且文档分母与最终 1578 不一致（§7） |
| R2 安全与兼容修复 | done | `reports/cards/r2_security_fixes.md`、`tests/test_security_round2.py`（31）、`datamind/agent.py`、`db/migrations/019_evidence_graph.sql`、`web/style.css` | H1 已修（`if not sources or any(...)`，第三轮读码确认 `datamind/agent.py:399/494`）；M 4/4 已修；L 4/5 已修；`test_security_round2` **31/31**；10 个 pattern 合计 238 项 / 4 失败 | confirmed（读码 + 测试）；**但 H1 的加固引入了 §7.2 那条真实回归** |
| R2 交付同步 | done | `reports/cards/r2_delivery_sync.md`、`交付/04_*`、`reports/office_checks.json` | 04 重写为 4 页；01/02/03 sha256 与第一轮一致 5/5；4/4 结构检查通过 | confirmed；其「`cross_source_live.json` 未产出」的表述**已被第三轮之后的实测取代**（§5 撤回-3） |

### 2.3 第三轮（收口轮）

| WP | 状态 | 交付物路径 | 关键数字（来源） | 第三轮复查结论 |
|---|---|---|---|---|
| R3 规范语料 + P2/P3/P4 证据 | done（1 条主张被证伪） | `data/knowledge_sources/**`、`ingestion/norm_facts.py`、`datamind/cross_source.py`（只加 `norm_corpus_evidence`）、`data/evaluation/cross_source_norm_v1.json`、`scripts/eval_cross_source_norm.py`、`tests/test_cross_source_norm.py`（21）、`reports/cross_source_norm_live.json` | 语料 14 份 / 30 条记录；`--check` **exit 0**、28 例标签复算通过（V2）；`--live` **28/28**（P2 12/12、P3 5/5、P4 6/6）、`model_calls 0`、`structures_with_3_answers 3`；第三轮复跑**逐例 answer 零差异**（V3） | **confirmed（管线可复现）**，但 **`build_norm_corpus.py --check` 的「deterministic: true」被证伪为不稳定**（§5 撤回-4、§6-1） |
| R3 澄清契约下沉（L4） | done | `datamind/clarification.py`、`datamind/api.py`（只改导入）、`scripts/eval_clarification.py`（只 re-export）、`tests/test_clarification_single_source.py`（13）、`web/index.html`（`?v=0.42.0`） | 6/6 函数对象同一；脚本内第二份定义 **0/14**；API 中 `scripts` 导入 **0 处**；4 键逐字未变；第三轮 34 项测试 `OK`（V12） | **confirmed**（V12 + 读码）；线上 8000 服务未重启，**线上响应未端到端验证**（`not_attempted`） |
| R3 评分缺口补证 | partial | `data/evaluation/faithfulness_v1.json`、`robustness_v2.json`、`multifile_v1.json`、`scripts/eval_{faithfulness,robustness_v2,multifile}.py`、`scripts/eval_scale.py`（`--kind profile_live`）、`reports/{faithfulness_v1,robustness_v2,multifile_v1,scale_v2}.json` | 忠实度 **19/21 = 0.9048**（矛盾 0）；鲁棒 **20/21 = 0.9524**（0 个 5xx）；多文档 **5/9 = 0.5556**（引用覆盖 3/7、拒答 3/3、上游错误 4/12）；`profile_live` 模型段 1674.2/3238.6 ms（n=12） | **标签自检 confirmed**（V8–V10 全 exit 0）；**live 数字未经独立重跑**；**F5 未达 70% 档**；`scale_v2.json` 顶层 policy 与新 section 矛盾 |
| R3 最终独立复核 | done | `reports/review_round3_final.md` | 确定性命令 10/10 达到声明结果；**refuted 1 / 未经独立重跑 5**；风险 阻断 0 / 高 2 / 中 4 / 低 3；F1 判定「仍不能计入 6 分档」 | 本报告即为该复核的汇总承接 |
| R3 交付件同步 | done | 本报告、`docs/DELIVERY_CHECKLIST.md`、`交付/DELIVERY_MANIFEST.json`、`交付/04_*`（DOCX+PDF）、`reports/office_checks.json` | 04 自述更新为第三轮同步版（页数 ≤5）；证据索引与未完成项两节同步 | 见 §9 证据索引 |

---

## 3. 真实实测结果（严格分三类）

> 三张表**必须分开读**。表 A 不调用上游模型；表 B 真实调用 `deepseek-flash`（经运行中的 8000 服务）；
> 表 C 是 stub 作答器上的机制对照，**不是模型成绩**。分子分母同时给出。
> **表 A 中带 ✅ 的行由第三轮复核者亲手复跑**；表 B/C 的数字**均未经独立重跑**，仅做过内部一致性核对。

### 3.1 表 A｜离线确定性评测（不调用上游模型；✅ = 第三轮复核者亲手复跑）

| 项目 | 分子/分母 | 比率 | 来源文件 | 产生命令（退出码） |
|---|---:|---:|---|---|
| ✅ 规范语料标签冻结门 | 28/28 例、`problems 0` | 1.0 | `data/evaluation/cross_source_norm_v1.json` | `.venv/bin/python scripts/eval_cross_source_norm.py --check`（**0**） |
| ✅ **P2/P3/P4 真实库+真实文件执行** | **28/28**（P2 12/12、P3 5/5、P4 6/6）；`model_calls 0` | 1.0 | `reports/cross_source_norm_live.json` | `… eval_cross_source_norm.py --live --out tmp/review3/norm_live_rerun.json`（**0**；与报告逐例 status/error/answer 差异 **0**） |
| ✅ 语料清单完整性 | 14 份文件（5 PDF + 9 DOCX）、30 条条款、磁盘 sha256 不一致 **0/14** | 1.0 | `data/knowledge_sources/manifest.json` | 只读脚本（**0**） |
| ✅ 语料**字节**确定性 | **13/17 次** `deterministic: true`，**4/17 次 false** | 0.765 | 同上 | `… build_norm_corpus.py --check`（**0 与 1 交替**；**证伪**，见 §5 撤回-4） |
| ✅ 跨源离线用例（注入 fixture） | 21/21 | 1.0 | `reports/cross_source_offline.json` | `.venv/bin/python scripts/eval_cross_source.py --offline`（**0**；逐例 verdict 差异 0） |
| ✅ 跨源离线断言 | 161/161 | 1.0 | 同上 | 同上；`model_calls 0` |
| ✅ 绑定陷阱集冻结门 | 40/40 例、`problems []`、`pin_matches true` | 1.0 | `data/evaluation/binding_traps_v1.json` | `.venv/bin/python scripts/eval_binding.py --check`（**0**；sha256 `1fdfa89b…`） |
| ✅ 澄清 detector / deployed | 18/18 与 **4/18** | 1.0 / 0.2222 | `reports/clarification_v1.json` | `.venv/bin/python scripts/eval_clarification.py --offline`（**0**） |
| ✅ 不必要澄清率 | 0/7（占全部 0/25） | 0.0 | 同上 | 同上 |
| ✅ 补槽落到问句帧 | 14/14（detector）、4/4（deployed），另 4 例 N/A | 1.0 | 同上 | 同上 |
| ✅ 冻结登记一致性 | 3/3 suite | 1.0 | `data/evaluation/registry/*.json` | `.venv/bin/python scripts/eval_registry.py check`（**0**） |
| ✅ 三套 live 冻结集**标签**自检 | 26 / 21 / 12 例，`problems []` | 1.0 | `faithfulness_v1.json` / `robustness_v2.json` / `multifile_v1.json` | `… --validate-only`（**0 / 0 / 0**） |
| ✅ 新文件样本 manifest | 11 份 / 6 格式 / 23 问题，`ok true` | 1.0 | `data/qa_samples/manifest.json` | `.venv/bin/python data/qa_samples/build_samples.py --check`（**0**） |
| ✅ 第三轮新增测试（两项） | 34/34（13 + 21） | 1.0 | `tests/test_clarification_single_source.py`、`tests/test_cross_source_norm.py` | `PYTHONPATH="tests:$PWD" .venv/bin/python -m unittest …`（**0**） |
| 复杂度材料评测 | 26/26 跑完、解析失败 0；期望一致 **20/26**；ρ 0.641 vs 0.5639 | 0.7692 / 不复现 | `reports/complexity_v1.json` | `.venv/bin/python scripts/eval_complexity.py`（0） |
| 可扩展要素提取（声明式） | 120/120；三档极差 0.0 pp | 1.0 | `reports/extensible_fields_v1.json` | `.venv/bin/python scripts/eval_extensible_fields.py`（0） |
| Schema v2 表/列召回 Top-5 | 65/88 = 0.7386；48/56 = 0.8571；路径 25/25、拒绝 9/9 | — | `reports/schema_linking_v2.json` | `.venv/bin/python scripts/eval_schema.py --suite v2`（**1**，默认 90% 门槛未达） |
| 多轮 offline | 61/61、整段 10/10、可追溯 46/46、越权拒绝 2/2 | 1.0 | `reports/multi_turn_v1.json` | `.venv/bin/python scripts/eval_multi_turn.py --offline`（0） |
| 规模数据自校验 | 12/12 文件重生成一致 | 1.0 | `reports/scale_v2.json` | `.venv/bin/python scripts/build_scale_data.py --check`（0） |
| 答案断言离线重算 | 23/23；可检索性一致 27/27 | 1.0 | `reports/qa_samples_v1.json` | `.venv/bin/python scripts/eval_qa_samples.py --stored`（0） |
| 交付物结构检查 | 4/4 通过 | 1.0 | `reports/office_checks.json` | `check_office.py`（0） |

### 3.2 表 B｜真实模型调用（`deepseek-flash` 经运行中的 8000 服务；上游错误单列、留在分母；**均未经独立重跑**）

| 项目 | 分子/分母 | 比率 | 上游错误/拒答（单列） | 来源文件 | 命令（退出码） |
|---|---:|---:|---|---|---|
| 当前版本基线总计 | **28/30** | 0.9333 | 上游错误 0；拒答 5/30（4 预期 + 1 误拒） | `reports/baseline_current.json` | `.venv/bin/python scripts/eval_sql.py`（1）/ `eval_rag.py`（1）/ `--suite robustness`（0） |
| — nl2sql / rag / robustness | 13/14、11/12、4/4 | 0.9286 / 0.9167 / 1.0 | 0 / 0 / 0 | 同上 | 同上 |
| **单文档逐结论忠实度（E6/E2）** | **19/21 = 0.9048**；矛盾 0/21；无依据 2/21；引用覆盖 17/21 = 0.8095；拒答 5/5；误拒 0/21 | 0.9048 | 上游错误例 **0/26**；`attempts` 27 条（1 条含 `MODEL_EMPTY_RESPONSE`） | `reports/faithfulness_v1.json` | `.venv/bin/python scripts/eval_faithfulness.py`（0） |
| **鲁棒性 v2（E9/F7）** | **20/21 = 0.9524**；5xx/内部错误 0/21 | 0.9524 | 上游错误例 **0/21**；唯一失败 `rb2-014`（空结果，`REPAIR_BUDGET_EXHAUSTED`） | `reports/robustness_v2.json` | `.venv/bin/python scripts/eval_robustness_v2.py`（0） |
| **多文档问答（F5）** | 可答正确 **5/9 = 0.5556**；引用覆盖全部指定文件 3/7 = 0.4286；拒答 3/3；逐结论 9/13 = 0.6923 | 0.5556 | 上游 `MODEL_EMPTY_RESPONSE` **4/12**（`attempts` 25 条中 17 条含该码） | `reports/multifile_v1.json` | `.venv/bin/python scripts/eval_multifile.py`（**1**，有 upstream_error_cases） |
| 多轮 live | **58/61** | 0.9508 | 未通过 3/61（`mt-07` 三轮复问） | `reports/multi_turn_v1.json` | `.venv/bin/python scripts/eval_multi_turn.py --live`（0） |
| 澄清 live | **4/18** | 0.2222 | 上游 `MODEL_EMPTY_RESPONSE` 5 例；`ABSTAINED` 2；`null` 3 | `reports/clarification_v1.json` | `.venv/bin/python scripts/eval_clarification.py --offline --live`（0） |
| **跨源 live（F1·P1）** | **5/6** | 0.8333 | `model_calls 17`；`upstream_clarification_required 14`、`upstream_SQL_EXECUTION_ERROR 1`、`sql_evidence_empty 1` | `reports/cross_source_live.json` | `.venv/bin/python scripts/eval_cross_source.py --live`（**1**，唯一失败 `cs-p1-05`） |
| 新文件提问 | 0/2 完成 | 0.0 | `DOCUMENT_SELECTION_UNAVAILABLE`、`INSUFFICIENT_EVIDENCE` | `reports/qa_samples_v1.json` | `.venv/bin/python scripts/eval_qa_samples.py --http`（0） |
| 端到端分段（E7/F6） | 12 次（3 轮 × 4 题）；模型段 n=12 | — | `transport_error_calls []` | `reports/scale_v2.json`.`sections.profile_live` | `.venv/bin/python scripts/eval_scale.py --kind profile_live --profile-live-repeats 3`（0） |
| P2/P3/P4 的「live」 | **不属表 B**：`model_calls = 0` | — | — | `reports/cross_source_norm_live.json` | 见表 A（该文件的 `mode: live` 指「真实库/真实文件」，不是「真实模型」） |

**表 B 失败样本（原样保留）**
1. `nl2sql-010`（同比，hard）：`REPAIR_BUDGET_EXHAUSTED`，三次修复门禁拒绝，fail-closed 未产出 SQL。
2. `rag-010`（扫描件，hard）：检索把 gold 排到 rank=1，生成阶段仍 `INSUFFICIENT_EVIDENCE`——**检索对、答案错**。
3. `faith-014` / `faith-015`（01 报告）：`RAG_VERIFICATION_FAILED`，0 引用、无结论，`faith-015` 三轮复现。
4. `faith-012`：上游抖动（报告现状 2 次尝试、另一轮 4 次尝试），重试策略生效，`attempts` 逐次留痕。
5. `rb2-014`「2026年1月的订单有多少笔？」：`REPAIR_BUDGET_EXHAUSTED`，未返回 0（同类年份粒度用例 `rb-004` 通过）。
6. `mf-001/003/006/012`（跨文档核对）：每题多次尝试全部 `MODEL_EMPTY_RESPONSE`（`mf-012` 末次 `RAG_VERIFICATION_FAILED`）；`mf-002` 答案正确但只引用了 2 份指定文档中的 1 份。
7. `cs-p1-05`：`CROSS_SOURCE_EVIDENCE_INSUFFICIENT`/`SQL_EVIDENCE_EMPTY`（NL2SQL 把「2025 年会员数最多的等级」按注册窗口过滤 → 0 行）。
8. `mt-07` 第 4/5/6 轮：真实模型连续复问「上一年指哪一年」（帧不进提示词）。
9. 澄清 live：5 例上游空响应 + 2 例拒答 + 3 例「反问但接不上」。
10. `norm-p3-06/07`、`norm-p4-07`：3 条声明内 fail-closed（`NO_DOC_EVIDENCE_LINKED` / `VERSION_MISMATCH` / `VERSION_NOT_APPLICABLE`）。
11. `norm-p3-09`：冲突键不含 `measurement_id` → 报出 2 条假 `value_conflict`。**收口轮已修**（冲突键纳入记录作用域，独立复算同步带上 `measurement_id`，标签按修复后的真实值更正并留下修订记录）：该例现为 `completed`、0 冲突，`norm-p3-09` 不再报假冲突。
12. `norm-p4-04/05`：引擎 `threshold_checks` **2/9**，独立重算 **9** 条（已双向冻结）。**收口轮已修**（`edge_between` 对对称边支持双向查找，方向边语义不变）：引擎值现为 **9/9**，与独立重算一致；把取值退回缺陷期的 2 仍会让 `--check` 失败，证明修订不是放宽。

### 3.3 表 C｜stub 模拟（三臂绑定消融；`model_calls = 0`，**不是模型成绩**）

| 臂 | 绑定准确率 | 错误绑定率（high 声称口径） | 错误绑定率（全例口径） | 冲突 P/R | 误修复率 |
|---|---:|---:|---:|---|---|
| `plain` | 8/40 = 0.200 | **32/40 = 0.800** | 32/40 = 0.800 | N/A / 0/25 | N/A（无改写） |
| `bind_only` | 8/40 = 0.200 | 27/35 = 0.7714 | 27/40 = 0.675 | N/A / 0/25 | N/A（无改写） |
| `full` | **40/40 = 1.000** | **0/17 = 0.000** | **0/40 = 0.000** | 25/25 / 25/25 | 0/9 = 0.000（分母 = 发生改写的修复数） |

- 硬判定原文：`{"criterion":"full 臂错误绑定率相对 plain 臂下降 ≥50% 且绝对值 ≤5%","plain_misbinding_rate":0.8,"full_misbinding_rate":0.0,"relative_drop":1.0,"meets":true,"plain_already_clean":false}`。
- 修复账本（`full`）：attempts **15** / changed **9** / rejected **6** / succeeded **9** / misrepair **0**；`failures[]` 保留 **68** 条 arm-case 失败（plain 34、bind_only 34、full 0）。
- 敏感性探针：去掉版本核验 → 绑定 0.775、错误绑定率 0.500(9/18)；去掉冲突门 → 0.450、0.541(20/37)；去掉修复 → 0.800（转为过度拒答）。
- **诚实边界**：stub ≠ 真实模型准确率；自建陷阱集；`full 40/40` 是**陷阱集与机制同源设计**的结果；`claim_boundary` 原文：「full 臂通过门槛只能支持『在冻结定义下机制有效』的实用创新举证；若要主张技术突破领先性，还需要真实模型 + 独立外部数据的证据。」

---

## 4. 官方评分项逐项对照（E1–E12 / F1–F13 / 加分 F14–F15）

> 「官方行」与档位原文引自 `docs/SCORE_GAP_LEDGER.md` §1.1–§1.3（该文件逐行抄录官方 PDF p.10–14，含页码）。
> 「能支撑到哪一档」= **本轮证据是否足以支撑该档位声明**（不是评委判定，也不是自评分）。
> 「E/F 编号是仓库内部编号，不是官方编号」（台账 §0.1）。

### 4.1 初赛（E1–E12）

| 项 | 官方档位（原文要点） | 本轮证据（分子/分母） | 能支撑到哪一档 | 为什么不能更高 | 还差什么最小动作 |
|---|---|---|---|---|---|
| **E1 单表 NL2SQL（10）** | ≥10 用例，每个正确 1 分 | `evaluation_cases` suite `nl2sql` **14**；`nl2sql_current.json` **13/14** | 方向可支撑；**档位不能计入**（需 ≥10 用例、当前 14 用例中单表仅 4–5 题） | 独立单表子集 `not_attempted`；1 题 fail-closed（`nl2sql-010`） | 建 ≥10 题独立单表冻结集并逐题给 SQL+结果（1–2 人日） |
| **E2 单文档 RAG（10）** | ≥10 用例，每个正确 1 分 | **26 题**单文档冻结集（21 可答 + 5 拒答），逐结论三态标注 **19/21**、引用覆盖 17/21、拒答 5/5、误拒 0/21 | **「≥10 用例 + 拒答单列」形式条件满足**；分值由评委/官方测试集定 | 分母仅 21 个结论句；轮间波动（同集另一轮 20/21） | 扩到 ≥30 结论句并报区间 |
| **E3 可解释性（5）** | 具备展示 3 / 质量高 5 | 引用卡片、`/v1/runs/{id}/evidence`、证据图逐例 `threshold_checks.expression`（如 `950400 > 604800`）与 `repair_ledger` | 具备展示；「质量高」不能计入 | P4 答案正文只给**内部节点 id**，不含条款编号/文档标题 | P4 答案改引条款编号与标题（0.5 人日） |
| **E4 交互 Demo（5）** | 基础可用 3 / 体验良好 5 | `web/` 多页面、`?v=0.42.0`、澄清补槽 UI、75 条 API 路径 | 基础可用；「体验良好」不能计入 | 上传/审批无前端入口；新文件闭环断在索引 | 接入向量索引并录一次上传→问答→引用点击（2–3 人日） |
| **E5 NL2SQL 执行准确率（10）** | ≥70%→6 / ≥80%→8 / ≥90%→10 | **13/14 = 0.9286** | **≥80% 档有独立实测**；≥90% 档不能计入 | 仅 14 题，且 1 题即翻转 0.9286→0.8571；`not_attempted` 独立集 | 扩样到 ≥30 题并刷新分母（2–3 人日） |
| **E6 RAG 忠实度（10）** | ≥70%→6 / ≥80%→8 / ≥90%→10 | 逐结论三态：**19/21 = 0.9048**、矛盾 0/21、无依据 2/21 | **≥80% 档有独立实测**（数值落在 ≥90% 区间但分母仅 21、轮间波动） | 判定是**确定性规则**（`model_judgment=null`），非语义蕴含；分母小 | 扩样 + 明确「规则判定 ≠ 语义蕴含」（1–2 人日） |
| **E7 系统效率（5）** | 完整 5 / 深度不足 3 / 仅提及 0 | 复杂度分析 + SQL 3 档（1k/10k/100k）+ 文档 3 档（10/100/500 页）+ 并发 1/2/4/8 + 优化前后（q4 4.240→0.577 ms ×7.348）+ **六段端到端**（route 69.6/104.2、recall 53.2/57.1、execute 0.8/1.7、model **1674.2/3238.6**、first_token 66.0/80.2、complete 1790.9/3349.1 ms，n=12/6） | **有条件可计入 5 分档**（三段不再为 null） | n=6/12（`n<20` 时 p95 即观测最大值）；单机单并发、无 cgroup 隔离；首字是「首个 SSE 事件」而非 token 级 | 把 n 提到 ≥20、补环境隔离说明（1 人日） |
| **E8 中级任务数量与质量（15）** | 每攻克 1 个 5 分，质量突出加分 | 可申报材料 4 项（B3 澄清 deployed 4/18、B4 Schema v2 73.9%、B2 120/120 自建、B8 20/26 期望一致） | **不能计入 15**（3 项门槛的材料形态接近，但质量与详述未定稿） | B3 只接入 detector、B4 未过 90% 门槛（exit 1）、B2 loader 未实现、B8 未接生产 | 3 项各自「输入—基线—改进—失败—复现」写进技术文档 + B3 接入线上 |
| **E9 鲁棒性（10）** | 基础 5 / 良好 7 / 优异 10 | **21 题/12 类真实端到端 20/21 = 0.9524**；5xx 0/21；越权 3 类稳定拒答；注入 2/2 无泄漏；超长/空输入 422 | **可支撑「良好」档的材料**；「优异」不能计入 | `rb2-014` 空结果失败；无并发/低质量极端矩阵 | 修 `rb2-014` + 补极端矩阵（1–2 人日） |
| **E10 多轮上下文（5）** | 具备 3 / 连续 5 轮以上准确 5 | offline 61/61、整段 10/10；**live 58/61** | **只能计入 3 分档方向** | live 有 3 轮真实复问（`mt-07`）→「连续 5 轮以上准确」不能声称；帧未进提示词、无服务端会话 | 帧注入提示词 + 重跑 live 61 轮（1–2 人日） |
| **E11 技术文档（10）** | 完整 6 / 图表规范 8 / 工业水准 10 | 2 DOCX + 3 PDF，页数 13/12/4 达标，结构检查 4/4 | 形态可支撑；档位不能计入 | 无逐页人工视觉判定；决赛 ≤50 页深化稿未做 | 决赛稿 + 逐页视觉复核 |
| **E12 线上演示（5）** | 清晰展示核心功能 5 | 截图 + 可运行 Demo；`/health` `status: ok` | 材料在；现场项不能计入 | 无演示视频；运行中服务是旧构建 | 录一次演示视频（0.5 人日） |

### 4.2 决赛（F1–F13）

| 项 | 官方档位（原文要点） | 本轮证据（分子/分母） | 能支撑到哪一档 | 为什么不能更高 | 还差什么最小动作 |
|---|---|---|---|---|---|
| **F1 多源融合（10）** | **≥3 种→6 / 5 种以上→8 / 复杂多跳→10** | P1 **5/6**（真实模型，`model_calls 17`）；P2 **12/12**、P3 **9/9**（answered 6/6）、P4 **7/7**（answered 6/6）——`model_calls 0`，SQL 由条款确定性编译，文档侧为自建 14 份语料；规范集 **28/28**、`--check` exit 0 | **仍不能计入 0→6**：3 种结构无模型参与且文档侧非公开 KB | ① 4 种结构里 3 种**无模型参与**；② 这 3 种的文档侧不是公开 KB（169 条切片对可查询指标的条款仍是 0）；③ 余下 1 处引擎缺口（`threshold_checks` 的对象级门控）未做 | ① 语料发布进真实知识库 + P2 走真实 NL2SQL live；② 补对象级门控；③ P1 重复两轮给稳定区间（合计 4–5 人日） |
| **F2 意图理解与澄清（10）** | 意图识别 4 / 主动澄清 7 / 精准且体验好 10 | detector 18/18、deployed **4/18**、不必要澄清 0/7；契约 4 键下沉到生产包 | **不能计入 10**；active clarification 的**线上**能力只有 4/18 | 六类缺项只接入 detector，线上规则式 `clarify()` 覆盖率低；线上键未端到端验证 | 六类缺项接入 `sql_engine.clarify()` 并重跑 deployed/live（2–3 人日） |
| **F3 可解释性（5）** | 完整链路 3 / 可视化清晰 5 | 证据图逐例节点/边/绑定字段/冲突双方；`threshold_checks.expression` | 完整链路 3；「可视化清晰 5」不能计入 | 前端证据图卡片未做；P4 答案只给节点 id | 前端证据图卡片（2 人日） |
| **F4 复杂 NL2SQL（10）** | ≥70%→5 / ≥80%→7 / ≥90%→10 | v1 31/31 复跑一致；B4 路径 25/25、拒绝 9/9；v2 表 73.9% | **不能计入 7** | 无独立复杂集（计划要求 ≥15 条）；无公开基准 | 建 ≥15 条复杂冻结集（3–4 人日） |
| **F5 多文档/多模态 RAG（10）** | ≥70%→5 / ≥80%→7 / ≥90%→10 | 12 题固定多文档集：可答正确 **5/9 = 0.5556**、引用覆盖 3/7、拒答 3/3、上游错误 4/12 | **未达 70%，任何档位都不能计入** | 三轮实测 3/9→5/9 且跨文档核对题在三次独立跑中都触发上游空响应 | 定位跨文档提示词空响应 + 引用覆盖；再扩到 ≥30 题（3–5 人日） |
| **F6 效率与伸缩性（5）** | 完整 5 / 缺实测 3 / 仅提及 1 | 同 E7 + 并发 29.43→195.97 次/秒 + 环境说明 | **有条件可计入 5** | 与 E7 同：n<20、无 cgroup 隔离、吞吐只覆盖 SQL 工具链 | 同 E7 |
| **F7 低质量数据鲁棒性（5）** | 基础 2 / 较强 4 / 极端稳定 5 | 同 E9：20/21、0 个 5xx、越权 3 类拒答 | **可支撑「较强」4 分档的材料** | 「极端条件」缺：无质量分驱动处理策略的对照、`rb2-014` 失败 | 质量分→策略对照 + 修 `rb2-014`（2 人日） |
| **F8 中级任务广度（15）** | 3 个→9 / 4 个→12 / 5 个→15（以技术文档详述为准） | 同 E8，4 项材料 | **不能计入 9** | 「以技术文档详述为准」且详述未定稿；各项均有未闭合面 | 把 3 项写成可申报详述（3–4 人日） |
| **F9 中级任务质量（10）** | 改进显著 6 / 独创 8 / 业界领先 10 | 三臂对照机制（同集、同口径、68 条失败）+ `vcar_ablation` 16 例替身 | **可支撑「改进显著 6」的材料**；8/10 不能计入 | 三臂是 **stub + 自建陷阱集**；各中级任务仍缺「与简单基线对照」 | 每个申报任务补基线对照；绑定消融补 live（3–4 人日） |
| **F10 技术文档（5）** | 单档「完整、结构清晰 5」 | 初赛口径 2 DOCX + 2 PDF 已成稿 | **部分**（可能得 5，但决赛稿未做） | 决赛 ≤50 页深化稿未做 | 出决赛深化稿 |
| **F11/F12 答辩与演示（各 5）** | 良好 3 / 优秀 5；成功 3 / 互动优秀 5 | 20 张 PPT（匿名 0 命中） | **不能计入（现场项）** | 干净环境演练 **0/2**；无现场记录 | ≥2 次干净环境演练 + 彩排 |
| **F13 综合评估（5）** | 单档 | 全程证据纪律 + 三轮复查 + 失败样本保留 | 材料在；**不能自评** | 本报告与三轮复查本身即证据 | — |

### 4.3 高级加分（F14/F15，上限 20）

| 方向 | 本轮证据 | 能支撑到哪一档 | 为什么不能更高 | 最小动作 |
|---|---|---|---|---|
| **F14 技术突破领先性（0–10）** | 无公开基准成绩；无真实视觉模型；对照实验只在自建冻结集 + stub 上 | **不能计入，维持 0** | 官方要求「达到业界领先水平 / 学术界启发 / 极限挑战下有效」；`claim_boundary` 原文已明确需要「真实模型 + 独立外部数据」 | 做公开基准（如 Spider/BIRD）或真实模型 live 三臂对照 |
| **F15 实用创新与转化价值（0–10）** | 门槛在**冻结定义下满足**：`plain` 错误绑定率 32/40 = 0.800 → `full` 0/40 = 0.000（相对下降 100%、绝对 0% ≤ 5%）、冲突 P/R 25/25、误修复 0/9、68 条失败样本、敏感性探针；`scripts/eval_binding.py --check` 由第三轮独立复跑 **exit 0** | **可按下限申报「实用创新与转化价值」**（判定权在评委） | ① stub 作答器、自建陷阱集、无 live 端到端；② `plain` 的差来自陷阱设计；③ 无商业化路径材料 | 补成本效益对照 + 产品化路径（2 人日）；若要技术突破方向则需独立外部数据 |

**档位错配提醒（沿用台账 §3.3，本报告不修改计划文件）**：计划把 E5 目标写成「9–10」、E9 写成「8–9」、F9 写成「7」，
官方档位分别是 6/8/10、5/7/10、6/8/10，**9、8、7 三个值不可达**；计划 §0 的 85–87 与其 §2.1 逐项相加（95）不符。

---

## 5. 独立复查汇总（三轮：confirmed / refuted / unverifiable / risks）

### 5.1 第一轮三份复查（结论已冻结；本报告只转引，不覆盖）

| 复查 | 覆盖 | confirmed | refuted | unverifiable / partial | risks | verdict |
|---|---|---:|---:|---:|---|---|
| `reports/review_round1_evidence.md` | 11 份机读报告 + 12 张卡，**123 条数字主张** | **107** | **2** | **14** | 口径风险 2 条（「不必要澄清率 0/7」结构性成立；detector 臂与期望同源） | **证据可信度：中** |
| `reports/review_round1_regression.md` | 全量隔离回归 + 越界改动枚举 | 抽样 **123/123** 全绿（占既有 1297 的 9.5%） | — | 1497/5 的推导值非实测 | **7 条**（R1 契约回归未交底、R2 共享记录被覆写、R3 API 运行时依赖 `scripts/`、R4 工业规则结构性抽取、R5 未提交迁移染红他人断言、R6 `start_local.sh` 未授权改动、R7 抽样覆盖 9.5%） | **回归未守住** |
| `reports/review_round1_security.md` | 19/19 受阅文件、11/11 可复现断言 | **11/11** | — | — | **阻断 0 / 高 2 / 中 4 / 低 5** | **有条件通过**（2 条高须先修） |

### 5.2 第二轮两张复查/修复卡

| 卡 | 覆盖 | 结果 | 第三轮判定 |
|---|---|---|---|
| `reports/cards/r2_security_fixes.md` | 逐条修第一轮安全复查的 H1/M1/M2/M3/M4/L2/L3/L5；H2 改文档；L4 当时保留 | 新增 `tests/test_security_round2.py` **31/31**；10 个 pattern 合计 **238 项 / 4 失败**；高 **1/2** 已修、中 **4/4** 已修、低 **4/5** 已修 | **confirmed**；读码确认 H1 守卫已在 `datamind/agent.py:399/494`；**但该加固引入一条真实回归（§7.2）** |
| `reports/cards/r2_delivery_sync.md` | 修第一轮两条 refuted 的交付表述（C130/C131） | 04 重写为 4 页；01/02/03 sha256 与第一轮一致 **5/5**；过时措辞残留 **0/2** | confirmed；其「`cross_source_live.json` 未产出」**已被后续实测取代**（撤回-3） |

### 5.3 第三轮独立复核（本报告配套全文：`reports/review_round3_final.md`）

| 项 | 计数 |
|---|---|
| **confirmed**（复核者亲手复跑且与被复核声明一致） | **19** |
| **refuted** | **1**（撤回-4） |
| **unverifiable-now / 未经独立重跑**（必须真实调模型 + 运行中服务） | **5**（`faithfulness_v1`、`robustness_v2`、`multifile_v1`、`scale_v2.profile_live`、`cross_source_live`） |
| **risks** | 阻断 **0** / 高 **2** / 中 **4** / 低 **3** |
| 确定性命令复跑达标率 | **10/10 条命令达到声明结果**（其中 1 条是稳定性主张被证伪） |

**高（2）**
- **H-R3-1 全量回归未收口且三处口径不一致**：权威全量 1578/1574（4 失败、exit 1），`PROGRESS.md`/`README.md` 写 1497，`reports/latest_regression.json` 被单文件运行覆盖成 31。**任何「当前版本回归」类声明在收敛前都不可验证。**
- **H-R3-2 F1 仍不能计入 6 分档**：4 种结构里 3 种 `model_calls = 0` + 文档侧自建语料（两处引擎缺陷已在收口轮修复，余 1 处对象级门控；详见 §4.2 F1）。

**中（4）**
- **M-R3-1** 语料字节确定性门不稳定（17 次运行 4 次失败；根因 `/ID` 正则只认十六进制形态）。**M-R3-2** 三份 live 报告未记录上游模型标识。**M-R3-3** `scale_v2.json` 顶层 `model_segment_policy` 与新 section 自相矛盾。**M-R3-4** `cross_source_norm_live.json` 顶层 `failure_samples` 为空数组（5 条非 completed 只在 `cases[]`）。

**低（3）**
- **L-R3-1** P4 答案正文只给内部节点 id，不含条款编号/标题（削弱 E3/F3）。**L-R3-2** 运行中 8000 服务仍是旧构建（`/health` `capabilities` 无 `cross_source`、`agent` 为 `sql+rag`），表 B 描述的是已部署构建而非工作区代码。**L-R3-3** `data/knowledge_sources/__pycache__/` 是冻结核查目录内的未跟踪构建产物（不影响 `--check` 的 `*.pdf/*.docx` 匹配，但应清理）。

### 5.4 三轮累计被证伪/被撤回的数字（含更正）

| # | 被撤回的主张 | 原出处 | 更正后的实测 | 处理 |
|---|---|---|---|---|
| 撤回-1 | 「`rb-004` 库内 `category` 实为 `鲁棒-空结果`，故 `EMPTY_RESULT` 断言未被检验」 | `reports/cards/wp1b_baseline.md:89-90`；依据 C116 | 只读 SQL 实测 `rb-004|空结果|robustness`；`eval_sql.py:72` 确为 `if category == "空结果":` | 第一轮已撤回；robustness 4/4 **包含** `EMPTY_RESULT` 检验；但 4 题仍远少于 ≥10 |
| 撤回-2 | 「`binding_ablation.json` 与 `cross_source_offline.json` 两份文件均不存在」 | `reports/cards/wp10_delivery.md:58`；依据 C130/C131 | 前者当时确实缺失、后者已存在 | 第一轮半条撤回；第二轮已重写 04 自述 |
| 撤回-3 | 「`reports/cross_source_live.json` 本轮未产出（`ls` 退出码 1）→ 跨源 live 无成功记录」 | `reports/cards/r2_delivery_sync.md`、`docs/DELIVERY_CHECKLIST.md` §2.1 | 该文件**现已存在**：P1 **5/6**、P2/P3/P4 `answerable_cases = 0`、`model_calls 17`、`ran_at 2026-10-09T19:07:04Z` | **第三轮撤回**；04 自述与清单已同步（§9） |
| 撤回-4 | 「`build_norm_corpus.py --check` → exit 0；`{"deterministic": true, "problems": []}`」（作为稳定主张） | `reports/cards/r3_cross_source_norm.md` 复现命令① | 17 次独立运行：**13 次 true / 4 次 false**；失败文件 `DM-KF-2026-001_售后时效规范.pdf` ×2、`GY-A102-V2_工艺温度规范.pdf` ×2；根因是 `/ID` 正则只匹配两段十六进制，实测会出现 `<hex>(字面串)` 形态 | **第三轮撤回该稳定性主张**；抽取数字（16/16、12/12、30/30）**仍然成立**；材料中不得写「语料逐字节可复现」 |
| 撤回-5 | 「绑定三臂消融数字属同轮自报、未经独立复查，引用前须限定」 | `reports/cards/wp3_binding.md` §诚实边界、`docs/DELIVERY_CHECKLIST.md` §2.1 | 第三轮复核者独立复跑 `scripts/eval_binding.py --check` → **exit 0**、`problems []`、`pin_matches true`、40 例；冻结集与门槛算术**已独立确认** | **限定条件收窄**：仍须写「stub + 自建冻结集、`model_calls = 0`，不是模型成绩」，但「未经独立复查」不再适用于**冻结集/门槛算术**这一层 |

> 除上述五条外，§3 各表数字均以来源文件原文为准；表 B/C 的「未经独立重跑」状态在该表内逐行标注。

---

## 6. 失败样本与未完成项（逐条给阻塞原因与最小下一步）

| # | 未完成 / 失败项 | 阻塞原因（如实） | 最小下一步 | 来源 |
|---|---|---|---|---|
| 1 | **语料字节确定性门 4/17 失败** | `_normalize_pdf_id()` 正则 `/ID\s*\[<hex+><hex+>\]` 不匹配 PyMuPDF 的 `<hex>(字面串)` 尾串形态（字节级探针已定位；长度 7321 vs 7389） | 放宽正则或显式 `set_id()`；重新冻结算 14 个 sha256 | `reports/review_round3_final.md` §3.1 |
| 2 | **全量回归 4 失败、exit 1** | 1 条真实契约回归（H1 加固后无 scope 跨源问句在范围门返回 `SOURCE_NOT_ALLOWED`，测试允许集合未含该码）+ 3 条文档/记录漂移（禁令下 `PROGRESS.md`/`README.md` 由协调者统一同步） | ① 把 `SOURCE_NOT_ALLOWED` 纳入断言或给测试显式 scope；② 按 1578 分母同步两文档；③ 按「两次全量」序列收敛 | §7.2、`tmp/regression_final_a.log` |
| 3 | **`reports/latest_regression.json` 被覆盖成 31** | `scripts/test_isolated.py` 无条件覆写该共享文件（第一轮 R2 已记录） | harness 增加「仅全量运行才写权威记录 / 带 pattern 的运行写独立文件」 | §7.1 |
| 4 | **F1 仍不能计入 6 分档** | 3/4 结构 `model_calls = 0`；文档侧为自建语料（引擎缺陷已修 2/3） | 见 §4.2 F1 的 4 步（4–5 人日） | `reports/review_round3_final.md` §4 + `reports/cards/r4_engine_defects.md` |
| 5 | **P4 漏检（引擎 2/9 vs 独立重算 9/9）** | `evidence_graph.edge_between` 对对称边只做单向键查找（`datamind/evidence_graph.py` 不在本轮允许改动集） | 改双向键查找并复跑全量确认影响面 | `reports/cards/r3_cross_source_norm.md` §诚实边界⑥ |
| 6 | **条款无「适用对象」门控** | `threshold_checks` 对同指标、时间重叠的所有行套用条款阈值 | 增加对象/适用范围门控 | 同上 ⑤ |
| 7 | ~~冲突键不含 `measurement_id`（`norm-p3-09` 假冲突）~~ **收口轮已修** | 冲突键现含记录作用域；`norm-p3-09` 转为 `completed`/0 冲突，真冲突（`norm-p3-01` 850 vs 870）仍检出 | — | `reports/cards/r4_engine_defects.md` |
| 8 | **P4 答案只给内部节点 id** | `_answer_text` 未回填条款编号/文档标题 | 答案正文改引条款编号与标题 | 同上 §诚实边界⑦ |
| 9 | **`scale_v2.json` 顶层 policy 与新 section 矛盾** | 新 section 为加法追加，旧顶层文字未同步 | 加 `superseded_by` 或限定作用域 | `reports/review_round3_final.md` §3.2 |
| 10 | **三份 live 报告无模型标识** | 脚本元数据未记录激活的模型 profile | 补 `model_profile`（脱敏） | 同上 §3.3 |
| 11 | **`cross_source_norm_live.json` 顶层 `failure_samples` 为空** | 失败清单只在 `cases[]` | 把 5 条非 completed 落进顶层 | 同上 §3.4 |
| 12 | **F5 多文档未达 70%** | 跨文档核对题在三次独立跑中均触发上游 `MODEL_EMPTY_RESPONSE`；引用覆盖 3/7 | 定位提示词/上下文长度问题；扩到 ≥30 题 | `reports/cards/r3_scoring_gaps.md` §5 |
| 13 | **线上澄清未端到端** | 禁令禁止重启 8000 服务，运行中进程仍是旧代码（`clarification_*` 键结论同第一轮） | 文件负责人重启后跑 `eval_clarification.py --live` | `reports/cards/r3_clarification_sink.md` §诚实边界 |
| 14 | **E10「连续 5 轮以上准确」** | 帧不进提示词（为守「不传 history 逐字节不变」）；无服务端会话、前端未接入 | 同步更新字节级断言后把帧注入提示词，重跑 live 61 轮 | `reports/cards/wp7_multi_turn.md` |
| 15 | **WP9 新文件问答闭环 `not_done`** | `vector_index='deferred'`，上传不写 `chunks` | 接入索引或明确「上传仅契约」口味，并录一次闭环 | `reports/cards/wp9_qa_samples.md` |
| 16 | **E5/E1/F4 独立集 `not_attempted`** | 新增题需写库 + 真实模型；超出相关 WP 允许范围 | 由协调者在允许写库的范围补题并冻结新版本号 | `reports/cards/wp1a_registry.md` |
| 17 | **B4 v2 未过 90% 门槛（exit 1）** | 失败全部是 `global_budget_cut (30)` + `kind_top5_cut (1)`；机制本身 `not_matched=0` | `Catalog.retrieve` 按类配额或先分桶再截断 | `reports/cards/wp6_schema.md` |
| 18 | **B8 未接生产 + 期望不一致 6/26** | `ingestion/*` 不在该 WP 允许改动集；分档阈值对扫描件/跨页续表偏低 | 把 `assess_pdf()/policy_for()` 接入上传路径；在不调参过拟合前提下重设阈值 | `reports/cards/wp5b_complexity.md` |
| 19 | **B2 loader/权限/发布未实现** | 只做离线抽取 + 字段定位复用 | 实现声明式配置写入运行时注册表的 loader 并测改动行数 | `reports/cards/wp5a_extensible.md` |
| 20 | **干净环境演练 0/2、决赛稿与 ZIP 未做、逐页视觉检查未做** | 硬性禁令（禁重建容器/库）+ 本轮明确不打 ZIP + 会话模型不接受图像输入 | 决赛稿 ≤50 页；`--write` 打真包；≥2 次干净环境演练；人工逐页看图 | `reports/cards/wp10_delivery.md`、`docs/DELIVERY_CHECKLIST.md` §4 |
| 21 | **`complexity` ρ 不可复现（0.641 vs 0.5639）** | 单机单次时延统计，被 2 个离群点拉扯 | 材料引用给双值；多次采样报区间 | 第一轮 C33 |
| 22 | **F1/P2–P4 的「文档侧非公开 KB」** | 公开切片 169 条里满足「可抽条款 + 对应可查询指标」的仍是 **0**；本轮用自建语料绕过 | 若要评委按公开知识库口径认可，需真实文档侧抽取落地 | `reports/cards/wp2_cross_source_live.md` ③④⑤ |

---

## 7. 回归与安全状态

### 7.1 全量隔离回归（主协调者记录 + 复核者只读核对）

| 项 | 内容 |
|---|---|
| 命令 | `cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py`（收口轮由主协调者串行执行四次全量：A 轮 1578/4、B 轮 1664/4、C 轮 1664/3、D 轮 **1664/0**） |
| **退出码** | **0**（最终 D 轮） |
| 尾部输出（原样摘录） | `Ran 1664 tests in 250.615s` / `OK` |
| harness 汇总（原样摘录） | `{"tests": 1664, "failures": 0, "errors": 0, "metadata_isolated": true, "model_settings_copied": false, "private_pdf_snapshot_rows_copied": false, "private_pdf_knowledge_rows_copied": false, "source_database_modified": false, "commit": "e4e2ebf", "ran_at": "2026-10-10T01:27:34.956825+00:00", "failed_tests": []}` |
| 失败清单（最终 0 条） | 无。四轮共修复 4 条：① `test_agent_api` 跨源路由契约（`None` 被当成空 allow-list，见 §7.2）；②③④ `test_status_docs_sync` 三条（分母/提交/`status_sync --check` 漂移，按「先同步文档再跑一次」的两步序列收敛） |

**分母口径（必须同时给出）**

| 口径 | 分母 | 失败 | 错误 | 说明 |
|---|---:|---:|---:|---|
| **收口轮最终全量（D 轮）** | **1664** | **0** | **0** | 退出码 0；`tmp/regression_final_d.log`；`reports/latest_regression.json` 同值 |
| 收口轮 A 轮（第三轮复核者引用的权威值） | 1578 | 4 | 0 | 退出码 1；`tmp/regression_final_a.log` |
| 第二轮权威全量（历史） | 1497 | 6 | 0 | `ran_at 2026-10-09T15:24:54Z`，`wp_fix2_regression_sync.md` §3 |
| 第一轮全量（历史） | 1497 | 6 | 0 | 第一轮 `reports/review_round1_regression.md` |
| 增量（1664 − 1497） | **+167** | **−6** | 0 | 收口轮新增测试文件（跨源规范语料、澄清单一实现、评分缺口三套、scope 语义、引擎缺陷断言等）；**新增文件内 0 红** |
| `PROGRESS.md` / `README.md` 声明 | **1664** | 不声明失败数（指向记录文件） | — | 已同步（收口轮由主协调者更新） |

> **可引用**：全量隔离回归 **1664 执行 / 1664 通过 / 0 失败 / 0 错误 / exit 0**（`tmp/regression_final_d.log`）。
> **禁止引用**：「既有 1297 项全绿」（1297 是四轮之前的旧分母，未在当前工作区重跑）；也不得把 1664 这
> 个**测试分母**说成任何模型准确率或官方得分。四轮之间的红色记录（1497/6、1578/4）保留在
> `tmp/regression_final_a.log` 与 `reports/review_round1_regression.md`，不回填、不改写。

### 7.2 那条真实契约回归的根因

`tests/test_agent_api.py:110-123` 断言跨源问句错误码 ∈ `{CROSS_SOURCE_EVIDENCE_INSUFFICIENT, DOC_BINDING_MISSING, CONFLICT_UNRESOLVED}`；
实测返回 **`SOURCE_NOT_ALLOWED`**。追码：`datamind/frame.py:552/606` 的范围判定在**未给 `database_sources`** 时返回该码，
而测试用的问句没有显式 scope，于是在到达证据图引擎前就被范围门拒。
这是**第二轮 H1 加固带来的新路径**（空 allow-list 必须拒绝，`r2_security_fixes.md §1`），
而第二轮的测试契约更新没有把 `SOURCE_NOT_ALLOWED` 纳入允许集合。
**最小修法（二选一）**：① 把它纳入允许集合并在 docstring 写明「无 scope 的跨源问句在范围门 fail-closed」；
② 给测试显式 `database_sources` 以真正检验证据图引擎的三个稳定码。

### 7.3 被修改的既有文件清单（只读枚举，`git status --porcelain` + `git diff --stat`）

本报告核对时刻：**16 个 `M`、88 组 `??`、无 `D`（无删除）**；测试文件 94 个。

| 既有文件 | +/- | 判定 | 来源轮次 |
|---|---|---|---|
| `datamind/agent.py` | +240 / −9 | 计划内（WP2 跨源接线 + H1 守卫 + WP7 帧入口） | 一/二轮 |
| `datamind/api.py` | +33 / −2 | 计划内，**L4 已下沉**（`from .clarification import …`） | 一/三轮 |
| `datamind/industrial_rules.py` | +48 / −20 | 计划内但超出「最小」；既有工业测试全绿 | 一/二轮 |
| `datamind/sql_engine.py` | +37 / −5 | 计划内（澄清文案常量化 + `frame=` 入口） | 一轮 |
| `scripts/eval_scale.py` | +1533 / −7 | 计划内（WP8 v2 + `profile_live` 加法扩展，`run_v1()` 保留） | 一/三轮 |
| `scripts/eval_schema.py` | +373 / −11 | 计划内（WP6 `--suite v2`，默认仍走 v1） | 一轮 |
| `tests/test_agent_api.py` | +20 / −2 | 计划内（旧码 `ROUTE_UNSUPPORTED` → 新稳定码集合） | 二轮，**仍 1 红** |
| `web/app.js` | +40 / −0 | 计划内（澄清补槽 UI，纯新增） | 一轮 |
| `web/style.css` | +5 / −0 | 计划内（L3 样式） | 二轮 |
| `web/index.html` | +6 / −3 | 计划内（`?v=` 0.41.0 → 0.42.0，三处一致） | 三轮 |
| `docs/MULTI_TURN.md` | +3 / −1 | 计划内（文档与证据对齐） | 二/三轮 |
| `docs/OFFICIAL_REQUIREMENTS_MAPPING.md` | +2 / −1 | 计划内（版本/行号更正） | 二轮 |
| `PROGRESS.md` | +8 / −4 | **⚠ 口径已过期**（写 1497，权威为 1578） | 二轮 |
| `README.md` | +6 / −3 | **⚠ 同上** | 二轮 |
| `reports/latest_regression.json` | +6 / −3 | **⚠ 共享产物被单文件运行反复覆写**（现 31） | 全程 |
| `scripts/start_local.sh` | +4 / −1 | **先于本轮存在**（macOS bash 3.2 `set -u` 修复；计划 §11 与审计已记录） | 基线前 |

- `tests/` 与 `db/migrations/` 下**无既有文件被改**（只有 `??` 新增）→ **没有出现「改测试迁就实现」**。
- 本报告未修改 `PROGRESS.md`、`README.md`、`requirements*.txt`、`reports/latest_regression.json`（禁令遵守）。

### 7.4 遗留风险分级

| 级别 | 条数 | 条目 |
|---|---:|---|
| **阻断** | **0** | 未发现「线上 500 / 数据丢失 / 未授权读写 / 大范围越权」 |
| **高** | **2** | H-R3-1 全量回归未收口 + 三处口径不一致；H-R3-2 F1 仍不能计入 6 分档 |
| **中** | **4** | M-R3-1 语料字节确定性门不稳定；M-R3-2 live 报告无模型标识；M-R3-3 `scale_v2.json` 顶层 policy 自相矛盾；M-R3-4 `cross_source_norm_live.json` 顶层 `failure_samples` 为空 |
| **低** | **3** | L-R3-1 P4 答案只给节点 id；L-R3-2 运行中服务是旧构建；L-R3-3 `data/knowledge_sources/__pycache__` 未清理 |

**第一轮安全发现的状态（三轮合并）**：H1 ✅已修（`agent.py:399/494`）、H2 ✅已改文档、
M1/M2/M3/M4 ✅已修、L1 保留、L2 ✅已修（`degF` 仅在结果为有尽小数时可换算）、L3 ✅已修、L4 ✅**第三轮已下沉**、L5 ✅已修。
**不能声称**：跨源「权限边界与 SQL 路径已一致到可抓包证明」（未对 8000 端口发跨源请求）；
019 的 `fresh-first + 完整 init` 已覆盖（只验证到最小 `runs` 桩 + 019 + legacy 列清单）。

---

## 8. 下一步建议（按距得分最近排序的 5 件事 + 验收判据 + 工作量）

> 排序依据 = 「官方档位可达性 × 现有证据成熟度 × 是否能在一个工作包内闭环」。
> **不要把没做的事情写成本轮成果。**

**① 全量回归第 3 次收敛（阻断级，先做）**
- 内容：① 裁决 `test_agent_api` 的 `SOURCE_NOT_ALLOWED`（纳入允许集合或给测试显式 scope）；② 按 **1578** 分母同步 `PROGRESS.md`/`README.md`；③ 按「先写回漂移记录 → 再跑一次」的两步序列收敛；④ 让 harness 只在全量运行时写 `reports/latest_regression.json`（单文件运行写独立文件）。
- 验收判据：全量隔离回归 **exit 0**、`failed_tests` 为空；`latest_regression.json` 记录权威分母与 `commit`；两文档与记录三方一致。
- 工作量：**0.5 人日** + 约 8 分钟机器时间（2 次全量）。
- 为什么最近：**它不直接加分，但不做则所有「当前版本回归」类材料失实**，且 3 条漂移红会持续污染每一轮。

**② F1 从「材料齐备」推到「可计入 6 分档」**
- 内容：① 把 `data/knowledge_sources/` 发布进真实知识库（需写库授权），让 P2 走 `agent.py` 通用跨源路由 + 真实 NL2SQL；② 修 3 处引擎缺陷（`edge_between` 双向查找、`threshold_checks` 对象门控、冲突键纳入 `measurement_id`）；③ 让 P3 至少 1 条冲突在真实模型 live 下产生且 **100% 展示双方**；④ P1 重复两轮给稳定区间。
- 验收判据：真实模型 live 下 **≥3 种结构各 ≥3 条 `answered_and_verified`**、`model_calls > 0`、七字段绑定完整、冲突类 100% 展示双方；`norm-p4-04/05` 引擎值 = 独立重算值、`norm-p3-09` 不再报假冲突；`--check` exit 0。
- 工作量：**4.5–6.5 人日**（可拆 4 个子任务并行）。
- 对应分值：**F1 6 分档**（现为「4 种结构可复现但不能计入」）。

**③ E7/F6 把时延与规模证据补到「n≥20 + 生产级口径」**
- 内容：把 `profile_live` 的重复次数提到使每段 n≥20；补环境说明（CPU 绑定/cgroup 隔离的诚实声明）；修正 `scale_v2.json` 顶层 `model_segment_policy` 与新 section 的矛盾；给三份 live 报告补 `model_profile`。
- 验收判据：`model`/`first_token`/`complete_response` 三段 `p50/p95` 非 `null` 且 **n≥20**；报告内无自相矛盾的 policy 字段；三份 live 报告含模型标识。
- 工作量：**1–1.5 人日**。
- 对应分值：**E7 5 分 + F6 5 分**（当前已是「有条件可计入」，这一步是消除口径风险）。

**④ E9/F7 从「良好」推到「优异」**
- 内容：修 `rb2-014`（月份粒度空结果 → 应返回 0 而非 `REPAIR_BUDGET_EXHAUSTED`）；补低质量极端矩阵（并发 + 扫描件 + 超长/乱码混合）；给出「质量分 → 处理策略 → 实际成本」的对照（把 B8 接入生产路径）。
- 验收判据：鲁棒集 ≥25 题、通过率 ≥22/25；`rb2-014` 通过；质量分与解析成本呈单调关系，否则如实写「未成立」。
- 工作量：**2–3 人日**。
- 对应分值：**E9 10 分档 / F7 5 分档**（当前只到「良好/较强」）。

**⑤ F5 多文档问答跨过 70% 门槛**
- 内容：定位跨文档核对题的上游 `MODEL_EMPTY_RESPONSE`（提示词长度/结构）；把「引用覆盖全部指定文件」从 3/7 提升；扩到 ≥30 题。
- 验收判据：可答正确率 **≥70%（21/30）**、引用覆盖 ≥70%、拒答 100%、上游错误单列且不移出分母。
- 工作量：**3–5 人日**。
- 对应分值：**F5 5 分档**（当前 0 档）。

**加分线单独建议**：按 §4.3，**只能按下限申报「实用创新与转化价值」**，叙事放在「可复现的绑定度量 + 68 条失败样本 + 敏感性探针」；
若要碰「技术突破领先性」，必须先补**真实模型 live 三臂对照**与**独立外部数据/公开基准**（当前不具备，工作量 ≥5 人日）。
**在 `reports/binding_ablation.json` 的冻结集与门槛算术已被独立复跑确认（第三轮 V4）之后，仍不得把 stub 数字说成模型成绩。**

**其余按收益排序的次优先项**：E10 帧注入提示词（2 人日）→ 澄清六类缺项接入线上（2–3 人日）→ B4 修全局截断过 90% 门槛（1–2 人日）→ 决赛稿 + ZIP 真包 + 干净环境演练 + 逐页视觉检查（3–4 人日）→ B2 loader（2 人日）。

---

## 9. 证据索引（本轮新文件路径 + 命令 + 退出码）

### 9.1 第三轮新增/更新文件

| 类别 | 路径 |
|---|---|
| 新增业务模块 | `datamind/clarification.py`（契约唯一实现，L4 下沉） |
| 新增解析/摄取 | `ingestion/norm_facts.py` |
| 新增语料 | `data/knowledge_sources/`（14 份：5 PDF + 9 DOCX）、`manifest.json`、`build_norm_corpus.py` |
| 新增数据集 | `data/evaluation/cross_source_norm_v1.json`（28 例）、`faithfulness_v1.json`（26）、`robustness_v2.json`（21）、`multifile_v1.json`（12）、`binding_traps_v1.json`（40） |
| 新增评测脚本 | `scripts/eval_cross_source_norm.py`、`eval_faithfulness.py`、`eval_robustness_v2.py`、`eval_multifile.py`、`eval_binding.py`（`eval_scale.py` 加 `--kind profile_live`） |
| 新增测试 | `tests/test_cross_source_norm.py`(21)、`test_clarification_single_source.py`(13)、`test_binding_eval.py`(33)、`test_security_round2.py`(31)、`test_eval_faithfulness.py`(18)、`test_eval_robustness_v2.py`(15)、`test_eval_multifile.py`(9) |
| 新增机读报告 | `reports/cross_source_norm_live.json`、`faithfulness_v1.json`、`robustness_v2.json`、`multifile_v1.json`、`binding_ablation.json`、`cross_source_live.json`、`scale_v2.json`（新 section） |
| 新增结论卡 | `reports/cards/r3_cross_source_norm.md`、`r3_clarification_sink.md`、`r3_scoring_gaps.md`、`wp3_binding.md`、`wp2_cross_source_live.md`、`r2_security_fixes.md`、`r2_delivery_sync.md`、`wp_fix2_regression_sync.md` |
| **本报告** | `reports/round1_improvement_report_20261010.md`（重写为第三轮定稿） |
| **第三轮独立复核** | `reports/review_round3_final.md`（新增） |
| 交付件同步 | `docs/DELIVERY_CHECKLIST.md`、`交付/DELIVERY_MANIFEST.json`、`交付/04_突破与创新性自述_DataMind_Agent.docx` + `交付/04_突破与创新性自述.pdf`、`reports/office_checks.json` |
| 复核临时产物（不入交付） | `tmp/review3/`（`det_reruns.log`、`norm_check_rerun.json`、`norm_live_rerun.json`、`cross_source_offline_rerun.json`、`clarification_offline_rerun.json`、`probe_pdf_det.py`、`regen_*.pdf`） |

### 9.2 本报告撰写期间真实执行的命令与退出码

| # | 命令 | 退出码 | 关键输出（原样摘录） |
|---|---|---:|---|
| V0 | `git rev-parse --short HEAD` | 0 | `e4e2ebf` |
| V0b | `date "+%Y-%m-%d %H:%M %Z"` | 0 | `2026-10-10 08:35 CST` |
| V0c | `curl -s http://127.0.0.1:8000/openapi.json \| .venv/bin/python -c "…"` | 0 | `openapi version 0.39.0 paths 75` |
| V0d | `curl -s http://127.0.0.1:8000/health` | 0 | `"agent":"sql+rag"`；`capabilities` 含 sql/rag/multifile_rag/…/upload_worker，**无 `cross_source`** |
| V1 | `… build_norm_corpus.py --check`（17 次） | 13×0 / 4×1 | `"deterministic": true` 13 次；`false` 4 次（售后时效规范 ×2、GY-A102-V2 ×2） |
| V1b | `… tmp/review3/probe_pdf_det.py`（30 次内第 5 次复现） | 0 | `first_diff_offset 7233`、`len_reg 7321` vs `len_frozen 7389`、`/ID[<hex>(literal)]` 形态 |
| V2 | `… eval_cross_source_norm.py --check` | 0 | `"ok": true, "problems": [], "cases": 28` |
| V3 | `… eval_cross_source_norm.py --live --out tmp/review3/norm_live_rerun.json` | 0 | `28/28`、`model_calls 0`；与报告逐例 `status/error/failure_detail` 差异 **0**、`answer` 差异 **0** |
| V4 | `… eval_binding.py --check` | 0 | `{"ok": true, "problems": [], "pin_matches": true, "cases": 40, …}` |
| V5 | `… eval_cross_source.py --offline --out tmp/review3/…` | 0 | `21/21`、`161/161`、`model_calls 0`；逐例 verdict 差异 0 |
| V6 | `… eval_clarification.py --offline --output tmp/review3/…` | 0 | detector 18/18、deployed 4/18、不必要澄清 0/7 与 0/25、`failures 14` |
| V7 | `… eval_registry.py check` | 0 | 3/3 suite 一致 |
| V8–V10 | `eval_faithfulness.py --validate-only` / `eval_robustness_v2.py --validate-only` / `eval_multifile.py --validate-only` | 0 / 0 / 0 | `{"cases": 26, "problems": []}` / `21` / `12` |
| V11 | `… data/qa_samples/build_samples.py --check` | 0 | `{"ok": true, "problems": []}` |
| V12 | `PYTHONPATH="tests:$PWD" .venv/bin/python -m unittest tests.test_clarification_single_source tests.test_cross_source_norm` | 0 | `Ran 34 tests in 32.010s` / `OK` |
| V13 | 只读语料完整性 + suite 结构计数脚本 | 0 | 14 files（docx 9 / pdf 5）、30 条条款、磁盘 sha256 不一致 0/14、28 例（12/9/7） |
| V14 | 只读提取 `cross_source_norm_live` / `cross_source_live` / `faithfulness_v1` / `robustness_v2` / `multifile_v1` / `binding_ablation` / `scale_v2`（`json.load`） | 0 | 见 §3、§4 |
| V15 | `git status --porcelain \| awk '{print $1}' \| sort \| uniq -c` | 0 | `87 ??` / `16 M`（后续复核时刻为 `88 ??` / `16 M`） |
| V16 | `git diff --stat` | 0 | 16 files changed, 2288 insertions(+), 76 deletions(-) |
| V17 | `grep -rn "ROUTE_UNSUPPORTED" datamind/ \| wc -l` | 0 | `0`（旧运行时码已退役） |

### 9.3 本报告未做的事（边界）

1. **未跑任何真实模型评测**：表 B 与 `profile_live` 的数字均为转引 + 内部一致性核对，**未经独立重跑**。
2. **未跑无参数全量隔离回归**：§7.1 的 1578/1574/4 为转引主协调者日志（禁令），未由本报告复跑。
3. **未修改任何被复核文件**（`reports/cross_source_norm_live.json`、`data/knowledge_sources/**`、
   `reports/faithfulness_v1.json`、`robustness_v2.json`、`multifile_v1.json`、`scale_v2.json`、`binding_ablation.json`、
   `cross_source_live.json`、`datamind/clarification.py`、`tests/test_cross_source_norm.py`、
   `tests/test_clarification_single_source.py`、`PROGRESS.md`、`README.md`、`reports/latest_regression.json`）；
   未执行 git 写操作、未删除文件、未重启或重建容器、未停止 8000 服务、未执行 `scripts/start_local.sh` / `scripts/ingest.py`、
   未对 `demo_retail` / `demo_industrial` 写入。
4. **未给出任何自评分**：§4 的「能支撑到哪一档」是本报告基于证据的**可计入性判断**，不是评委判定。
5. **未做逐页人工视觉判定**：交付物只过结构检查 + pymupdf 文本块边界检查（会话模型不接受图像输入）。

---

> **一句话交付**：第三轮把 6 类缺失证据补齐，其中**不调模型的部分全部被独立复现**（含 P2/P3/P4 的 28/28 逐例零差异），
> L4 契约下沉**属实**，E2/E6/E7/F6 从「无独立证据」变为「有独立实测」，F5 从「无实测」变为「有实测且未达标」，
> 加分从「门槛无法判定」变为「门槛在 stub/自建集下满足、只能按实用创新下限叙事」。
> 但三条硬缺口仍在：**F1 仍不能计入 6 分档**（4 种结构里 3 种 `model_calls = 0` + 自建语料 + 3 处已冻结的判定缺陷）、
> **全量回归 1578/1574（4 失败、exit 1）需要第 3 次收敛且两文档分母落后 81 项**、
> **语料字节确定性门不稳定（17 次 4 失败，根因已定位）**。共 **1 条主张被证伪、5 份报告未经独立重跑**，全部如实保留。
