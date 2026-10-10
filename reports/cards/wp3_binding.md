# 结论卡：WP3 创新点度量与三臂对照消融

**WP**：WP3 创新点度量与三臂对照消融（`plain` / `bind_only` / `full`，版本化证据绑定）
**日期**：2026-10-09 23:36 CST（机器 `date`）
**基线提交**：`e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`，只读，exit 0）
**API 版本**：`0.39.0`（`reports/binding_ablation.json` 的 `api_version`，取自 `datamind.api.app.version`）

## 问题

第一轮 **WP3 完全未启动**，独立复查已固定这一事实：

- `reports/review_round1_evidence.md` §6.2：「**`reports/binding_ablation.json` 完全缺失**……全仓检索：无
  `binding_ablation*`、无 `data/evaluation/binding_traps_v1.json`、无 `scripts/eval_binding.py`。因此
  《提升计划》§WP3 的三臂消融 `plain/bind_only/full`、**错误绑定率**、冲突 P/R、误修复率**均无任何数据**」；
- `reports/round1_improvement_report_20261010.md` §2.4/§6：WP3 记 `not_attempted`；§「创新加分」行
  「门槛判定：无法判定 → 视为不通过 → **不能计入，维持 0**」；
- 任务书要求的 `datamind/binding_eval.py` 亦不存在（`review_round1_security.md` §附录确认 `wc -l` 报
  `No such file or directory`）。

即：创新点的旗舰度量既没有冻结集、也没有对照数字，8–12 分的申报门槛无法判定。

## 做法

| # | 交付物 | 关键设计 |
|---|---|---|
| 1 | `data/evaluation/binding_traps_v1.json`（40 例） | 四类陷阱 **各 10 例**：同对象跨版本、单位不同/不可归一、时间口径不同、文档与库数值相反；每例先冻结 `expected_binding{entity_id, version, unit, time_scope}`、`expected_answerable`、`expected_conflict`、`expected_resolution ∈ {resolved_by_authority, unresolved, needs_review}`、`trap_kind`、`provenance`。含 **9 例负向对照**（单位等价 1123.15 K==850 degC、50 cm==500 mm、口径一致、数值一致、声明版本已正确）约束误报与过度拒答。摘要 `1fdfa89b…` 钉在 `binding_eval.TRAPS_SHA256`。 |
| 2 | `datamind/binding_eval.py`（约 900 行） | 确定性生成器 `generate_dataset()`（陷阱集的唯一来源）+ 独立标签审计 `validate_labels()`（**从 fixture 重推** `expected_conflict`：同 `entity+metric+version` 且「单位不可比 or 时间口径不同 or 数值不等」必须为真）+ `check_frozen()`（重新生成逐字节比对）；三臂 runner；指标算术沿用 `datamind/ablation.py` 风格（零分母 → `None`/N/A）；绑定与冲突语义复用 `datamind/binding.py`、`evidence_graph.py`、`cross_source.py`（**未改动这三个模块**）。 |
| 3 | 三臂语义 | `plain`：把 SQL 行与文档片段直接拼接，原样取用 SQL 行的 unit/version/scope，**一律 high 置信**；`bind_only`：做规范化绑定（approved 别名→entity_id、单位规范化、时间口径解析、取声明/快照版本）但**不核验**（无版本适用性、无可比性、无跨源比对、无修复）；`full`：绑定 + 核验（版本按**登记有效期窗口**判定、单位不可精确归一必须拒绝、冲突门未登记权威则保留双方）+ **最多 1 次**受控修复并逐条记账。三臂在**同一冻结集、同一确定性 stub 作答器**上跑。 |
| 4 | `scripts/eval_binding.py` | `--freeze`（重写冻结集，无 `--force` 拒绝覆盖）/ `--check`（复现+审计，标签被放宽即 exit 1 `BINDING_TRAPS_LABELS_LOOSENED`）/ 默认出报告（`--force` 才允许覆盖 `reports/binding_ablation.json`）。报告含 `per_arm`、`per_trap_kind`、`failures[]`、`verdict`、`sensitivity`、逐例索引与依赖文件 sha256。 |
| 5 | `reports/binding_ablation.json`（215 KB） | 字段齐备：`per_arm{binding_accuracy, misbinding_rate, conflict_precision, conflict_recall, false_positive_rate, repair_ledger{attempts, success_rate, over_repair_rate, cost_ms}}` + `per_trap_kind` + `failures[](case_id, trap_kind, expected, actual, why)` + 影响判定。 |
| 6 | `tests/test_binding_eval.py`（33 项） | 冻结集摘要与四类各 ≥8；标签放宽（conflict/answerable/删例/改 fixture）必须 `--check` 失败；零分母 N/A（含「冻结集有真冲突时召回是真实 0 而非 N/A」）；误修复率分母 = 发生改写的修复数；`full` 在无权威登记时不得收敛为单一来源（保留双方原值、不得 high 断言）；硬判定与「plain 不犯错则禁止申报领先性」；报告 schema 与拒绝覆盖。 |

## 数据与集

| 陷阱类别 | 例数 | 期望冲突（真/假） | 期望可作答 | 结构要点 |
|---|---:|---:|---:|---|
| 同对象跨版本 `cross_version` | 10 | 0 / 10 | 9 | V1/V2 登记有效期窗口（含半开区间边界、反向过期、无窗口覆盖→适用性未知）；3 例库侧标签是未登记别名，必须按 approved 别名规范化 |
| 单位不同/不可归一 `unit_mismatch` | 10 | 8 / 2 | 2 | 英寸 vs 毫米（未登记）、degF→degC **非终止小数换算必须拒绝**、自定义单位 AU/LU/mm·s⁻¹、单位缺失可由同 metric 兄弟声称补全、K↔degC 等价负向对照 |
| 时间口径不同 `time_scope_mismatch` | 10 | 8 / 2 | 2 | 自然月 / 季度 / 生产批次期间（不重叠与部分重叠都测）、无时区时间戳必须拒绝而非补时区 |
| 文档与库数值相反 `doc_db_opposite` | 10 | 9 / 1 | 4 | 同对象同版本两侧矛盾（含金额/计数/压力/时长）；3 例带登记权威分别指向库侧/文档侧；1 例两侧一致负向对照 |

出处：**全部为自建合成 fixture**（`demo_industrial` / `demo_kb` 的命名沿用演示库，但行与文档块都是注入的确定性数据）。
文档 `source_sha256` 是 fixture 文本的 `sha256`，不是真实文件摘要。`--check` 可逐字节复现。

## 结果（分子分母）

| 项 | 分子/分母 | 证据（原样摘录） |
|---|---|---|
| **硬判定（错误绑定率）** | full **0/17 = 0.0%** vs plain **32/40 = 80.0%**；相对下降 **100% ≥ 50%**，绝对值 **0% ≤ 5%** → **满足** | `"verdict": {"criterion": "full 臂错误绑定率相对 plain 臂下降 ≥50% 且绝对值 ≤5%", "plain_misbinding_rate": 0.8, "full_misbinding_rate": 0.0, "relative_drop": 1.0, "meets": true, "plain_already_clean": false}` |
| 绑定准确率（答案声称级） | plain 8/40 = 0.200；bind_only 8/40 = 0.200；full **40/40 = 1.000** | `per_arm[*].binding_accuracy` |
| 字段级绑定（分母 = 17 例可作答） | plain `entity 15/17, version 9/17, unit 17/17, time 16/17`；bind_only `entity 17/17, version 9/17, unit 17/17, time 16/17`；full **四项各 17/17** | `per_arm[*].binding_field_accuracy` |
| 错误绑定率（**全例口径**，防「断言变少使分母缩水」的质疑） | plain **32/40 = 0.800**；bind_only 27/40 = 0.675；full **0/40 = 0.000** | `per_arm[*].misbinding_rate_all_cases`；high 断言数 plain 40/40、bind_only 35/40、full 17/40（另 23 例按设计拒答）→ **两个口径下 full 都是 0** |
| 冲突精确率 / 召回率 / 误报率 | plain N/A / 0/25 / 0/15；bind_only N/A / 0/25 / 0/15；full **25/25 / 25/25 / 0/15**（TP 25、FP 0、FN 0、TN 15） | `per_arm[*].conflict_*`、`conflict_confusion` |
| 修复账本（full） | attempts **15**、changed **9**、rejected **6**、succeeded **9**、misrepair **0**；成功率 9/9 = 1.0，**误修复率 0/9 = 0.0**（分母 = 发生改写的修复数）；plain/bind_only 无修复 → 成功率与误修复率 **N/A（null）** | `per_arm.full.repair_ledger` |
| 耗时（stub，CPU，墙钟每次运行会浮动） | plain 0.34 ms、bind_only 0.45 ms、full 8.90 ms；`model_calls = 0`、`tokens = 0` | `per_arm[*].repair_ledger.cost_ms` |
| within-full 敏感性 | 去掉版本核验+修复 → 绑定 0.775、错误绑定率 **0.500 (9/18)**；去掉冲突门 → 绑定 0.450、错误绑定率 **0.541 (20/37)**；去掉修复 → 绑定 0.800（转为过度拒答，high 断言 9） | 报告 `sensitivity` |
| 复现（--check） | 40 例、四类各 10、`problems: []`、exit 0 | `{"ok": true, ..., "sha256": "1fdfa89b…", "pin_matches": true, "problems": []}` |
| 隔离回归 | **33 tests, failures 0, errors 0**，exit 0，`commit e4e2ebf` | `{"tests": 33, "failures": 0, "errors": 0, "metadata_isolated": true, "source_database_modified": false, "commit": "e4e2ebf", "failed_tests": []}` |

**加分预算建议（明确结论）**：门槛**满足**，且 `plain` **并非「几乎不犯错」**（80% 高置信错误绑定），
因此不属于「基线无差异 → 只能申报实用创新」的例外；建议按 8–12 区间**下限**申报，且自述必须同时写明
下方诚实边界。若不接受 stub/自建冻结集作为领先性证据，则应降级为「实用创新与转化价值」并**维持 0**。

## 失败样本

1. **报告内保留 68 条 arm-case 失败**（`failures[]`，逐条给 `expected` / `actual` / `why`）：plain 34 条、
   bind_only 34 条、**full 0 条**。原因码分布：`BINDING_MISMATCH`（可作答例字段不符）18 条；
   `ASSERTED_WITHOUT_DETECTION + CONFLICT_FALSE_NEGATIVE + MISBINDING_AT_HIGH_CONFIDENCE` 39 条；
   `NO_CONFLICT_DETECTED + CONFLICT_FALSE_NEGATIVE` 5 条（绑定不完整 → 既不检出也不断言，按定义仍算错）；
   `CONFLICT_FALSE_NEGATIVE` 4 条；`ASSERTED_WITHOUT_DETECTION + MISBINDING…` 2 条。
   典型：`bt-a-01`（plain 把生产行绑到快照版本 `ind-snapshot-5` 而非登记窗口选定的 `A102-V2`）、
   `bt-c-01`（plain 把 2 月口径的 905 与 3 月口径的 880 当作同一声称照答）。
2. **full 臂 0 条失败**是「陷阱集与机制同源设计」的结果，**不是泛化证据**（见诚实边界 3）；能说明机制有效的是
   敏感性探针：拆掉任一子机制立刻出现 9–20 条 high 置信错误绑定。
3. **开发期真实失败（全部保留）**：
   - 首次冻结后我自己的标签审计报 `CONFLICT_LABEL_LOOSENED:bt-b-03`：我按「`degF` 未登记」写标签，但同一轮
     另一个 WP 正在改 `datamind/binding.py`（新增 `degF` 的**理性精确换算**、新增 `万元`）。我据此把该例改为
     **非终止小数**陷阱（70 degF→190/9 degC 必须拒绝而不是四舍五入），`bt-b-04` 同理。这正是「标签被放宽/与
     实现漂移会被 `--check` 拦下」的实证。
   - `full` 的修复账本里有 **6 次被拒绝的修复尝试**（`rejected`）：4 次 `unit_from_sibling` 被显式拒绝——
     当声称**已声明但未登记**的单位（如 `in`）时，`cross_source._repair_plan` 的原有路径会把它直接改写为兄弟
     声称的单位（"20.04 in" → "20.04 mm"，只换标签不换算数值），这是**误修复**。本轮 `full` 臂显式拒绝该路径并
     记账（`UNSUPPORTED_UNIT_NOT_REPAIRABLE`），但**未修改** `cross_source.py`（不在本 WP 允许改动范围）；
     `review_round1_security.md` L2 的同一问题因此仍然存在于 cross_source 本体，需主协调者裁决。
   - 1 次 `none_applicable`（`bt-c-08`：库侧时间戳无时区 → 无可确定性修复，只能拒答）。
4. **未做**：`--live` 端到端（真实演示库 + 真实模型）不在本 WP 范围；本卡不给任何 live 数字。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"
git rev-parse --short HEAD; echo "exit=$?"        # e4e2ebf，exit=0
.venv/bin/python scripts/eval_binding.py --check; echo "exit=$?"   # exit=0
#   → {"ok": true, "error_code": null, "sha256": "1fdfa89be8eee18fc166a906250639a8f01b89b5ef663c831c335fa9faf0b281",
#      "problems": [], "pin_matches": true, "cases": 40,
#      "counts_by_trap_kind": {"cross_version": 10, "unit_mismatch": 10, "time_scope_mismatch": 10, "doc_db_opposite": 10}}
.venv/bin/python scripts/eval_binding.py --force; echo "exit=$?"        # exit=0，写 reports/binding_ablation.json
#   → plain 0.2 / 0.8 / 40 断言；bind_only 0.2 / 0.7714 / 35；full 1.0 / 0.0 / 17
#   → 硬判定：full 臂错误绑定率相对 plain 臂下降 ≥50% 且绝对值 ≤5% → 满足（plain=0.8 full=0.0 相对下降=1.0）
#   → 失败样本 68 条（全部保留在报告 failures[]）
.venv/bin/python scripts/test_isolated.py --pattern test_binding_eval.py; echo "exit=$?"   # exit=0
#   → {"tests": 33, "failures": 0, "errors": 0, "metadata_isolated": true, "source_database_modified": false,
#      "commit": "e4e2ebf", "failed_tests": []}
.venv/bin/python -m unittest tests.test_binding_eval; echo "exit=$?"             # exit=0（33 tests OK，无需数据库）
```

## 诚实边界（不能据此声称什么）

1. **stub ≠ 真实模型准确率**：作答器是确定性桩，不调用任何上游模型（`model_calls = 0`），token 成本不适用；
   本卡的任何数字都不是模型准确率，也不能外推为业务准确率。
2. **自建冻结集**：40 例由本仓库编写并先冻结标签。`plain` 的失败来自陷阱的设计，相对下降是**机制对照**
   结果，不是泛化能力、不是业界领先。
3. **`full` 40/40 不构成领先性证据**：它是「陷阱集与机制同源设计」的结果；报告同时给出 within-full 的
   降级探针（去掉版本核验/冲突门/修复 → 0.775/0.450/0.800 绑定准确率）来说明增益来源。
4. **不是端到端 live**：不衡量真实 SQL 执行、真实检索、真实模型或前端；不含演示库写操作（本 WP 全程只读、
   只跑纯 Python 与隔离克隆）。
5. **冲突口径**：只统计 `source_kind` 不同的同 `(entity, metric, version)` 数值对；不可比（单位/口径/版本适用性）
   记为 `needs_review` 而非 `unresolved`；「检出冲突」不等于判对了数值。
6. **依赖仍在漂移**：`datamind/binding.py`（本工作开始后被另一个 WP 加入 `degF`/`万元`）、`evidence_graph.py`、
   `cross_source.py` 在本轮被并发修改；报告记录了本次数值对应的文件 sha256（`dependency_digests`，例如
   `datamind/binding.py = a9137195…`、`cross_source.py = 6a51f21a…`）。复跑前应比对；标签层不受影响
   （数据集摘要 + `--check` 独立审计）。
7. **误修复率的分母口径**：分母 = **发生改写的修复数**（本轮 9），另给 `rejected`（6 次未改写）；
   zero 分母一律报 `null`（N/A），不用 0 冒充。
8. **我未修改任何其他模块**：`binding.py` / `evidence_graph.py` / `cross_source.py` / `agent.py` / 迁移文件均未动；
   `cross_source._repair_plan` 的「改写已声明未登记单位」风险仍在（第 2.3 条已如实记录，需主协调者裁决）。
9. **隔离 harness 的副产物（如实记录）**：`scripts/test_isolated.py` 按自身设计把 `reports/latest_regression.json`
   覆写为本次 **33 项**摘要（HEAD 里记录的是 1297 项，而 `PROGRESS.md`/`README.md` 写 1497 项）。因此
   `tests/test_status_docs_sync.py` 现在报 `status drift in: PROGRESS.md, README.md`——**这不是本 WP 引入的漂移**
   （HEAD 的 1297 与文档的 1497 本来就不一致），也不是我手工编辑的文件；主协调者跑完全量回归后该文件会被重写回全量摘要。
10. **加分预算的边界**：门槛在**本冻结集 + stub 定义下**满足；「技术突破领先性」需要真实模型 + 独立外部数据
    的证据，本轮**不具备**，因此建议按下限申报并把本项主要叙事放在「实用创新与转化价值」。

## 评分联动

| 评分项 | 本卡能支撑什么 | 不能支撑什么 |
|---|---|---|
| **创新加分（技术突破领先性 / 实用创新与转化价值，0–20）** | 首次给出可复现的旗舰度量：四类陷阱 40 例冻结集 + `plain/bind_only/full` 三臂同集对照 + 错误绑定率 0.800→0.000 + 冲突 P/R 1.0/1.0 + 误修复率 0/9 + 修复账本 + 68 条失败样本 + 硬判定「满足」+ 敏感性探针 | **不能主张技术突破领先性**：stub 作答器、自建陷阱集、无 live 端到端 → 只能按「实用创新与转化价值」举证，加分按区间下限 |
| **F9 中级任务改进质量（10）** | 提供了「与简单基线对照」的完整机制（同集三臂、同口径指标、失败保留）与可复用 runner | 本卡不主张任何新的中级任务「攻克」；WP3 只做机制度量 |
| **F1 多源异构融合问数（10）** | 复用并压测了 WP2 的绑定/证据图/裁决门语义：登记窗口选版本、不可比单位拒绝、无权威不收敛 | live 跨源端到端仍未验证（WP2 的 `--live` 不在本 WP 范围） |
| **F3/E3 答案可解释性（5）** | 每条答案声称带七字段绑定与冲突双方原值；报告逐例给 expected/actual/why，可回放 | 前端证据图卡片不在本 WP 改动范围 |
| **E6/F5 忠实度口径** | 硬性写成「stub ≠ 真实准确率」「自建集 ≠ 泛化」；冲突未登记权威时保留双方，不以单选冒充裁定 | 不能把绑定准确率当作语义忠实度或事实正确率 |
