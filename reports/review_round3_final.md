# 第三轮（收口轮）独立最终复核报告

> 本报告由**独立复核者**（非任何被复核工作包的实现者、非总报告撰写者之外的实现者）产出。
> 复核者**只读**被复核对象：未修改任何被复核文件、未做 git 写操作、未删除文件、未重启或重建容器、
> 未停止 8000 端口服务、未执行 `scripts/start_local.sh` 与 `scripts/ingest.py`、未对
> `demo_retail` / `demo_industrial` 做任何写操作（全部只读 SQL 与只读文件解析）。
> 复核者只新增：本文件、`reports/round1_improvement_report_20261010.md`（重写）、
> `docs/DELIVERY_CHECKLIST.md`、`交付/DELIVERY_MANIFEST.json`、`交付/04_*`、`reports/office_checks.json`。
> 复核者未跑无参数全量隔离回归（按禁令由主协调者统一跑）；未对 `reports/latest_regression.json`
> 做任何手工编辑。

| 字段 | 内容 |
|---|---|
| **WP** | R3-FINAL-REVIEW（第三轮独立最终复核）：复核 `reports/cross_source_norm_live.json`、`data/knowledge_sources/**`、`reports/faithfulness_v1.json`、`reports/robustness_v2.json`、`reports/multifile_v1.json`、`reports/scale_v2.json`（新 section）、`datamind/clarification.py`（L4 下沉）、`tests/test_cross_source_norm.py`、`tests/test_clarification_single_source.py`，并核对全量回归口径 |
| **日期** | 2026-10-10 08:35 CST（机器时钟 `date "+%Y-%m-%d %H:%M %Z"` → `2026-10-10 08:35 CST`，exit 0） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`，exit 0，只读） |
| **API 版本** | `0.39.0`（`datamind/api.py:198` `FastAPI(..., version="0.39.0")`；`curl -s http://127.0.0.1:8000/openapi.json` → `openapi version 0.39.0 paths 75`，exit 0） |
| **问题** | 第三轮新增了 6 类证据（规范语料跨源、单文档忠实度、鲁棒性 v2、多文档、端到端分段时延、L4 契约下沉）。这些数字**必须由独立复核者自己重跑可确定性复跑的部分**，并逐条判定：哪些能确认为真、哪些被证伪、哪些因必须调模型而只能标为未经独立重跑；同时判定 F1 的真实可申报档位、核对 L4 是否真的下沉、核对全量回归口径与文档是否一致 |
| **做法** | ① 对 10 条**不调用模型**的确定性命令**逐条独立重跑**并记录退出码与输出；② 对**必须真实调用上游模型/运行中服务**的 5 份报告，只做**报告内部一致性 + 真实调用留痕**核对，并统一标为「未经独立重跑」；③ 用字节级探针定位语料确定性门失败的根因；④ 用 `grep` + 源码/对象级断言核对 L4；⑤ 读 `tmp/regression_final_a.log`、`reports/latest_regression.json`、`tests/test_status_docs_sync.py` 与 `git diff PROGRESS.md README.md` 核对文档口径；⑥ 逐例比对复核重跑产物与被复核报告的 status/error/answer，不使用「跑通了」这种笼统判断 |
| **数据与集** | 复核对象：`reports/cross_source_norm_live.json`（8 702 行）、`data/knowledge_sources/`（14 份文档 + `manifest.json` + 生成器）、`data/evaluation/cross_source_norm_v1.json`（28 例）、`reports/faithfulness_v1.json`、`reports/robustness_v2.json`、`reports/multifile_v1.json`、`reports/scale_v2.json`、`reports/binding_ablation.json`（三臂消融，第一轮缺项）、`reports/cross_source_live.json`、`datamind/clarification.py`、`scripts/eval_clarification.py`、`tests/test_clarification_single_source.py`、`tests/test_cross_source_norm.py`、`tmp/regression_final_a.log`。全部为**自建/合成冻结集，非官方测试集** |
| **结果（分子分母）** | 复核者自己的重跑：**确定性命令 10/10 达到声明结果**（其中 1 条是**被证伪的稳定性主张**：语料确定性门在 17 次独立运行里 **4 次** 报 `deterministic: false`）。逐条判定计数：**confirmed 19 / refuted 1 / 未经独立重跑（需上游模型） 5**；风险分级 **阻断 0 / 高 2 / 中 4 / 低 3**。本报告自身产生的官方得分 **0/100（初赛）、0/100（决赛）、0/20（加分）** |
| **失败样本** | 见 §3：语料确定性门 flaky（含根因与字节证据）；`scale_v2.json` 顶层 `model_segment_policy` 与新 section 自相矛盾；三份 live 报告未记录上游模型标识；`cross_source_norm_live.json` 顶层 `failure_samples` 为空数组（5 条非 completed 只在 `cases[]`）；全量回归 4 条失败与文档分母不一致 |
| **复现命令** | §6 逐条列出（含退出码与输出摘录）；全部为只读或写入 `tmp/review3/` |
| **诚实边界（不能据此声称什么）** | ① 本报告**未重跑任何真实模型评测**（faithfulness/robustness/multifile/scale profile_live/cross_source live 均未复跑），这些数字属**转引 + 内部一致性核对**，不是独立复现；② **不能**据此声称任何官方得分、名次或提交资格；③ **不能**把 P2/P3/P4 的 28/28 说成「模型答对了 28 条」——该路径 `model_calls = 0`，SQL 由条款确定性编译，文档侧是自建语料；④ **不能**声称 F1 已可计入 6 分档（见 §4）；⑤ **不能**声称「当前版本回归全绿」；⑥ 本报告的风险分级不是渗透测试结论 |
| **评分联动** | 本报告只做「证据可计入性判定」，不产生得分。F1 的结论是**仍不能计入 0~6 档**（见 §4），并给出最小动作；E7/F6/E9/F7/E2/E6/F5 的档位判定见总报告 `reports/round1_improvement_report_20261010.md` §4 |

---

## 1. 确定性复跑（本人独立执行，逐条给出退出码与输出摘录）

> 纪律：每条命令都写到 `tmp/review3/`，**不覆盖任何被复核报告**；`--check` / `--validate-only` / `--offline` 均不调用上游模型。

| # | 命令 | 退出码 | 复核者实测输出（原样摘录） | 判定 |
|---|---|---:|---|---|
| V1 | `.venv/bin/python data/knowledge_sources/build_norm_corpus.py --check` | **0 与 1 交替** | 17 次独立运行中 **13 次** `"deterministic": true, "problems": []`；**4 次** `"deterministic": false` + 1 条 `{"file": …, "regenerated": …, "frozen": …}` | **被复核卡片的「exit 0 / deterministic true」主张被证伪为不稳定**（见 §3.1） |
| V2 | `.venv/bin/python scripts/eval_cross_source_norm.py --check` | **0** | `"ok": true, "problems": [], "cases": [28 条…]`（`norm-p2-01` `recomputed.violations=123 / checks=151`） | **confirmed** |
| V3 | `.venv/bin/python scripts/eval_cross_source_norm.py --live --out tmp/review3/norm_live_rerun.json` | **0** | `totals {"cases":28,"passed":28,"failed":0,"pass_rate":"28/28"}`；`model_calls: 0`；P2 `12/12`、P3 `5/5`（passed 9/9）、P4 `6/6`（passed 7/7）；`structures_with_3_answers: 3` | **confirmed（最强一条）**：与 `reports/cross_source_norm_live.json` 逐例比对 `status/error_code/failure_detail` 差异 **0**、`answer` 文本差异 **0** |
| V4 | `.venv/bin/python scripts/eval_binding.py --check` | **0** | `{"ok": true, "problems": [], "sha256": "1fdfa89be8eee18f…", "pin_matches": true, "cases": 40, "counts_by_trap_kind": {"cross_version":10,"unit_mismatch":10,"time_scope_mismatch":10,"doc_db_opposite":10}}` | **confirmed**（第一轮缺项 `binding_ablation` 的冻结集门已可复现） |
| V5 | `.venv/bin/python scripts/eval_cross_source.py --offline --out tmp/review3/cross_source_offline_rerun.json` | **0** | `totals {"cases":21,"passed":21,"failed":0,"pass_rate":"21/21"}`、`checks {"total":161,"failed":0,"passed":161}`、`model_calls: 0`；与 `reports/cross_source_offline.json` 逐例 verdict 差异 **0** | **confirmed**（非跨源路径未回退） |
| V6 | `.venv/bin/python scripts/eval_clarification.py --offline --output tmp/review3/clarification_offline_rerun.json` | **0** | `detector clarification_success 18/18`、`slot_landing 14/14`、`deployed 4/18`、`slot_landing 4/4`、`answer_frame_ok 7/7`、`不必要澄清 0/7 与 0/25`、`failures 14` | **confirmed**（与 wp4 卡、r3 卡逐值一致） |
| V7 | `.venv/bin/python scripts/eval_registry.py check` | **0** | 3 个 suite 一致 | **confirmed** |
| V8 | `.venv/bin/python scripts/eval_faithfulness.py --validate-only` | **0** | `"cases": 26, "problems": []` | **confirmed（仅标签自检）** |
| V9 | `.venv/bin/python scripts/eval_robustness_v2.py --validate-only` | **0** | `"cases": 21, "problems": []` | **confirmed（仅标签自检）** |
| V10 | `.venv/bin/python scripts/eval_multifile.py --validate-only` | **0** | `"cases": 12, "problems": []` | **confirmed（仅标签自检）** |
| V11 | `.venv/bin/python data/qa_samples/build_samples.py --check` | **0** | `{"ok": true, "problems": []}` | **confirmed** |
| V12 | `PYTHONPATH="tests:$PWD" .venv/bin/python -m unittest tests.test_clarification_single_source tests.test_cross_source_norm` | **0** | `Ran 34 tests in 32.010s` / `OK` | **confirmed**（13 + 21 = 34，与两张卡声明的用例数一致） |
| V13 | 语料清单完整性（复核者自写只读脚本） | **0** | `manifest` 14 files（docx 9 / pdf 5）、30 条条款、`on-disk sha256 mismatches vs manifest: 0 []`；suite 28 例（P2 12 / P3 9 / P4 7） | **confirmed** |

**结论**：第三轮的 6 类新证据里，**不调用模型的部分全部可独立复现**（V2–V13），且最强的一条 V3 做到了**逐例 answer 文本零差异**。唯一被证伪的是语料生成器的「字节确定性」稳定性主张（V1，见 §3.1）。

---

## 2. L4「契约下沉」是否真的下沉（逐项核对）

| 核查项 | 复核者实测 | 判定 |
|---|---|---|
| `datamind/api.py` 是否还从 `scripts/` 导入 | `grep -n "scripts" datamind/api.py` → 仅 2 处**非导入**命中（CSP 头里的 `sandbox allow-scripts`、docstring 里的 `/health`）；`grep -rn "from scripts\|import scripts" datamind/` → **0 处代码命中**（唯一命中是 `datamind/clarification.py` docstring 的说明文字） | **已下沉** |
| API 从哪取契约 | `datamind/api.py:23` `from .clarification import clarification_contract_for_answer`；`:1041` `_with_clarification_contract()` 只调用该生产包函数 | **已下沉** |
| `scripts/eval_clarification.py` 是否只剩 re-export | 是：`from datamind.clarification import (CONTRACT_VERSION, DEFAULT_PRODUCTS, SLOT_ORDER, SLOT_QUESTIONS, clarification_contract, clarification_contract_for_answer, detect_missing_slots, has_time, rule_slots_for_answer, slot_candidates)`；文件内**无第二份槽位表/正则表/检测器定义**（由 V12 的 0/14 禁用定义断言与 6/6 函数对象 `is` 断言守护） | **已是投影** |
| 契约键是否逐字不变 | 键名恰为 4 个：`clarification_contract_version` / `clarification_missing_slots` / `clarification_candidates` / `clarification_slot_questions`；版本常量 `clarification-slots-v1`；这两个集合在 `datamind/clarification.py:99-103`、`:121-124`、`datamind/api.py:1045-1046`、`web/app.js:572-574`、`tests/test_clarification_flow.py:93-96,252-254`、`tests/test_security_round2.py:45-46`、`tests/test_clarification_single_source.py:35-36` 与 `scripts/eval_clarification.py:299` **完全一致** | **键名与版本逐字未变** |
| 「逐字不变」的比较基线（诚实边界） | `scripts/eval_clarification.py` 与 `datamind/clarification.py` 均为**未跟踪新文件**，`git show HEAD:` 无旧版本可 diff。因此「逐字不变」是与**第一轮已记录在案的键集合**（`reports/cards/wp4_b3_clarification.md:27-28` 明确写出同 4 键）以及**本轮测试断言**比对，不是与一个受版本控制的旧文件比对 | 结论成立，但基线是文档化的键集合 |
| 未完成/未验证面 | 运行中的 8000 服务**未重启**，其 `/health` `capabilities` 里**没有** `cross_source`，`agent` 仍是 `"sql+rag"` → 线上进程仍是旧构建，「线上响应已带 `clarification_*` 键」**未被端到端验证**（`scripts/eval_clarification.py --live` 未跑，卡内已如实声明） | **not_attempted（受禁令限制）** |

**结论**：L4 **真的下沉了**（生产包一等依赖 + 评测脚本只做 re-export + 有测试守护），且契约 4 键的**键名、版本、语义、投影路径全部未变**。唯一未验证的是**运行中服务的实际响应**。

---

## 3. 被复核对象的问题（失败样本 / 证伪 / 内部不一致，全部保留）

### 3.1 【证伪】语料「字节确定性」门不稳定——`build_norm_corpus.py --check` 约 24% 的运行报 `deterministic: false`

- **被证伪的主张**：`reports/cards/r3_cross_source_norm.md`「复现命令①」写
  `build_norm_corpus.py --check` → **exit 0**，原样摘要 `{"deterministic": true, "problems": [], …}`。
- **复核者实测**：对同一命令独立运行 **17 次**，**13 次** exit 0 / `deterministic: true`，**4 次** exit 1 / `deterministic: false`，每次都恰好报 **1** 个 PDF 的哈希不一致：

  | 观测到的失败文件 | 次数 |
  |---|---:|
  | `DM-KF-2026-001_售后时效规范.pdf` | 2 |
  | `GY-A102-V2_工艺温度规范.pdf` | 2 |

  摘录（exit 1 时）：
  ```json
  {"deterministic": false,
   "problems": [{"file": "DM-KF-2026-001_售后时效规范.pdf",
                 "regenerated": "3dfe1cc9605f6481431ae65114215657a815d996807e3163c5f786b224ba0e7f",
                 "frozen":      "7556b0431ed74a34bc0a72365ecfa07e266a23a9e936d0653a757cce0ebaca17"}]}
  ```
- **根因（复核者用字节级探针定位，30 次内第 5 次复现）**：
  `build_norm_corpus.py:215-227` 的 `_normalize_pdf_id()` 用正则
  `/ID\s*\[<[0-9A-Fa-f]+><[0-9A-Fa-f]+>\]` 替换 PyMuPDF 的随机 `/ID`。
  该正则**只匹配「两段都是十六进制」的尾串**；实测重新生成时 PyMuPDF 会写出
  **第二段是括号字面串**的形态，正则不匹配 → 函数原样返回带随机 ID 的字节。
  探针在首个不一致偏移处取到的上下文（十六进制原文，解码后）：
  - 重新生成：`…2f49445b3c433342…3e283c7e3b786a30265c333234…` = `/ID[<C3B1C28F…>(<~;xj0&\324…`
  - 冻结文件：`…2f49445b3c3835343732384432…` = `/ID[<854728D2…`（两段十六进制）
  - 两文件长度也不同：重新生成 **7 321 B** vs 冻结 **7 389 B**。
- **影响面（已收窄）**：**抽取与标签不受影响**——同一批运行里 `threshold_clauses 16/16`、
  `value_claims 12/12`、`binding_fields 30/30 complete` 全部稳定；`eval_cross_source_norm.py --check`
  也 exit 0。受影响的**只是**「重新生成即可复得逐字节相同的语料」这一句主张。
- **最小修法（复核者未做，属实现者范围）**：把正则放宽到同时接受
  `(` 字面串形态（或显式 `doc.set_id()` 写内容派生 ID），然后重新冻结算 `manifest.json` 的 14 个 sha256。
- **为何必须记录**：`data/knowledge_sources/**` 是本轮 F1 三种结构的**唯一文档侧证据来源**；
  它的「冻结哈希」是评委复跑的入口之一。**不得**在材料里写「语料逐字节可复现」。

### 3.2 【内部不一致】`reports/scale_v2.json` 顶层 `model_segment_policy` 与新 section 自相矛盾

同一份报告里：

| 位置 | 内容 |
|---|---|
| 顶层 `model_segment_policy` | `{"status": "empty", "reason": "本轮未真实调用上游模型：WP8 只做工具链测量…完整问答/首字时延因此留空（null），不用 mock 延迟冒充。"}` |
| `sections.profile_live`（同文件） | `{"status": "measured_live_end_to_end", "calls_total": 12, "api_version": "0.39.0", "segments": {"model": {"n":12,"p50_ms":1674.2,"p95_ms":3238.6}, "first_token": {"n":12,"p50_ms":66.0,"p95_ms":80.2}, "complete_response": {"n":12,"p50_ms":1790.9,"p95_ms":3349.1}}}` |

`r3_scoring_gaps.md` 写「既有 `profile` section 的字段与数值未被改动」（属加法），
这解释了成因：新 section 被追加，但**顶层的过期 policy 文字没有同步**。
**影响**：机器读 `reports/scale_v2.json` 的消费者会同时看到「本轮未调用模型」和「模型段实测到毫秒」。
**最小修法**：在 `model_segment_policy` 增加 `superseded_by: "sections.profile_live"` 与时间戳，
或把它移到 `sections.profile` 下限定作用域。**复核者未改该文件**（不在允许写集内）。

### 3.3 【溯源缺口】三份真实模型报告没有记录上游模型的标识

`reports/faithfulness_v1.json`、`reports/robustness_v2.json`、`reports/multifile_v1.json` 都记录了
`api_version=0.39.0`、`baseline_commit=e4e2ebf`、`base_url=http://127.0.0.1:8000`、
`identity_source=loopback_no_login`、`retry_policy`（含 `max_retries=3`）以及逐例 `attempts`，
**但都没有记录模型名/版本**（`grep 'deepseek|model_name|"model"'` 在三份文件里全部为 `False`）。
卡片文本写的是 `deepseek-flash`，**机器可读件无法佐证**。
**影响**：独立复核者无法仅凭报告确认「是哪一个模型产生了这些答案」；模型切换后旧报告不可辨识。
**最小修法**：在三份报告的元数据里补 `model_profile`（`GET /v1/model/profiles` 的当前激活项，脱敏）。

### 3.4 【报告缺口】`cross_source_norm_live.json` 顶层 `failure_samples` 为空数组

`totals` 为 28/28，但同时 `status_counts = {"completed": 23, "failed": 3, "abstained": 2}`
（5 条非 completed），`failure_reason_counts` 也完整（`CONFLICT_UNRESOLVED:value_conflict` ×2、
`DOC_BINDING_MISSING:NO_DOC_EVIDENCE_LINKED` ×1、`DOC_BINDING_MISSING:VERSION_MISMATCH…` ×1、
`DOC_BINDING_MISSING:VERSION_NOT_APPLICABLE…` ×1），而顶层 `failure_samples` 是 `[]`。
**这与第一轮对 `cross_source_offline.json` 的同类批评一致**：失败清单只能从 `cases[]` 逐条取，
不能一键拿到。**最小修法**：把 5 条非 completed 的 `case_id` + `error_code` + `failure_detail` 落进顶层 `failure_samples`。

### 3.5 【已冻结的引擎缺陷】P2/P3/P4 的三处真实判定缺陷（复核者已复现）

| 缺陷 | 证据（复核者重跑同样复现） | 影响 |
|---|---|---|
| `datamind/evidence_graph.py:edge_between` 对**对称边只做单向键查找**（`add_edge` 已把 `src>dst` 规范化） | `norm-p4-04`/`norm-p4-05`：引擎实测 `threshold_checks` **2/9**、超限 **0**，而独立重算（`datamind.binding` 直接比较）是 **9** 条、**2** 条超限；报告用 `expected_recompute_divergence` 双向冻结 | P4 的判定在部分行上**漏检**；案例已冻结，不会静默漂移 |
| `threshold_checks` **无「条款适用对象」门控**：同指标、时间重叠的行全都套用条款阈值 | `norm-p2-04/05`、`norm-p4-04/05`；报告 `warnings` 与卡 §诚实边界⑤ | 对象专属条款会套用到同指标全部行 |
| 冲突键是 `entity+metric+version`，**不含 measurement_id** | `norm-p3-09`：M006-2=870 与 M011-1=860 各自与文档一致，却被报出 2 条 `value_conflict` | 同实体同版次不同测量之间产生**假冲突** |

三处都**不是**本轮新引入，而是被本轮如实冻结成可复现事实；`r3_cross_source_norm.md` §3 已列为下一轮建议。
**复核者判定**：它们直接削弱「P4 = 复杂多跳，可冲 10 分」与「P3 = 跨源冲突核验」的可申报强度（见 §4）。

### 3.6 【未独立重跑】必须真实调用上游模型的 5 份报告（只做了内部一致性核对）

| 报告 | 规模 | 内部一致性核对结果 | 真实调用留痕 | 状态 |
|---|---|---|---|---|
| `reports/faithfulness_v1.json` | 26 例（21 可答 + 5 拒答） | 自洽：`conclusion_total 21`、`supported 19`、`contradicted 0`、`no_evidence 2`、`citation_coverage 17/21`、`refusal 5/5`、`false_refusal 0/21`；`cases[]` 26 条与 `case_count` 一致 | `attempts` 共 **27** 条（其中 1 条含 `MODEL_EMPTY_RESPONSE`）；`upstream_error` 例 **0** | **未经独立重跑（unverifiable-now）** |
| `reports/robustness_v2.json` | 21 例 / 12 类 | 自洽：`case_passed 20/21`、`pass_rate 0.9524`；`by_category` 求和 = 21；唯一失败 `rb2-014` 与 `failure_samples` 一致 | `attempts` 共 **22** 条（1 条含 `MODEL_EMPTY_RESPONSE`）；`upstream_error` 例 **0** | **未经独立重跑** |
| `reports/multifile_v1.json` | 12 例（9 可答 + 3 拒答） | 自洽：`answerable_correct 5/9 = 0.5556`、`citation_covers_all 3/7`、`refusal 3/3`、`conclusion 9/13 = 0.6923`、`upstream_error_cases 4` | `attempts` 共 **25** 条（**17** 条含 `MODEL_EMPTY_RESPONSE`）；4 例 `upstream_error` **留在分母** | **未经独立重跑**；**F5 未达 70% 档，任何档位都不能计入** |
| `reports/scale_v2.json` 的 `sections.profile_live` | 12 次流式调用（3 轮 × 4 题） | 自洽：`samples_per_segment`（route/model/first_token/complete=12，recall/execute=6）与 `calls_total=12` 一致；六段 p50/p95 与卡逐值相同 | `calls[]` 12 条、`transport_error_calls []` | **未经独立重跑**；且顶层 policy 与之矛盾（§3.2） |
| `reports/cross_source_live.json` | 21 例（P1 6 / P2 5 / P3 5 / P4 5） | 自洽：`totals 20/21`、`by_structure` 求和 = 21、`checks 55/60`、`live_summary {answerable 6, answered_and_verified 5, declared_refusal 15/15}`；唯一失败 `cs-p1-05` 与 `failure_samples` 一致 | `model_calls` **17**；`failure_reason_counts` 含 `upstream_clarification_required 14`、`upstream_SQL_EXECUTION_ERROR 1`、`sql_evidence_empty 1` | **未经独立重跑**；**P2/P3/P4 `answerable_cases = 0`** |

**共同限制（必须随数字引用）**：这 5 份报告是在**运行中的 8000 服务**上实测的，而该服务
`GET /health` 的 `capabilities` **不含 `cross_source`**、`agent` 仍是 `"sql+rag"`，
即**线上进程仍是工作区改动之前的构建**。因此它们描述的是**已部署构建的行为**，
不是 `e4e2ebf` 工作区代码的行为（工作区代码版本无法从报告本身证明）。

---

## 4. F1 的真实可申报档位（逐条判定，不替实现者抬档）

官方原文（`docs/SCORE_GAP_LEDGER.md` §1.2，p.12）：
**「能正确回答至少 3 种跨源问题得 6 分，5 种以上得 8 分，应对复杂多跳问题得 10 分」**。

### 4.1 四种结构各自的真实成功条数（复核者从机读报告直接取数）

| 结构 | 载体 | **真实成功（answered_and_verified）** | 分母口径 | 是否有真实模型参与 | 文档侧来源 |
|---|---|---:|---:|---|---|
| **P1** SQL 结果驱动文档检索 | `reports/cross_source_live.json` | **5** | 6（P1 的 live 可答样例） | **是**（`model_calls = 17`，`deepseek-flash` 经回环 8000） | 公开知识库 169 条切片（`demo_kb` 107 / `ohrbench_kb` 62 / `industrial_demo` **0**） |
| **P2** 文档规则驱动 SQL 筛选 | `reports/cross_source_norm_live.json` | **12** | 12 | **否**（`model_calls = 0`；SQL 由条款确定性编译，非 NL2SQL） | **本轮新建的自建冻结语料** `data/knowledge_sources/`（14 份） |
| **P3** 跨源冲突核验与冲突呈现 | 同上 | **5** | 5（另 4 条为声明内 fail-closed） | **否** | 同上 |
| **P4** 复杂多跳 | 同上 | **6** | 6（另 1 条声明内 fail-closed） | **否** | 同上 |
| 合计 | — | **28 条**（P1 5 + P2 12 + P3 5 + P4 6） | — | 仅 P1 有真实模型 | — |

### 4.2 判定

1. **「有 4 种结构、28 条 answered_and_verified」是事实**（复核者已独立复现 P2/P3/P4 的 28/28，逐例 answer 零差异；P1 的 5/6 只做了内部一致性核对）。
2. **但「至少 3 种跨源问题回答正确」目前不能判定为成立**，理由是三条**实质性**而非形式性的：
   - **（a）3/4 的结构没有模型参与**。P2/P3/P4 的 23 条答案里 `model_calls = 0`：问句不经过路由/NL2SQL/生成，SQL 由条款适配器按「指标/方向/阈值/单位/时间口径/对象」确定性编译。它证明的是**「条款→SQL」这条规则管线在真库真文件上可复算**，不是**「系统能回答用户的跨源问题」**。卡片自己写明：「SQL 不是 NL2SQL」「不是泛化准确率」。
   - **（b）这 3 种结构赖以成立的文档侧不是公开知识库，而是本轮自建语料**。公开 KB 的 169 条切片对本轮**没有变化**：`demo_kb` 里能被 `_CLAUSE_RE` 抽出且对应可查询指标的条款仍然是 **0**。也就是说，「跨源」的另一半（文档侧）是我方新造的 14 份规范文件；评委若要复现，需要接受这份自建语料作为证据。
   - **（c）这 3 种结构里有 3 处已冻结的判定缺陷**（§3.5）：P4 的 `threshold_checks` 在部分行上漏检（引擎 2/9 vs 独立重算 9/9）、无对象级门控、冲突键不含测量标识（P3 报出假冲突）。因此即便只按「引擎判定的答案正确」口径，P2/P3/P4 也有**已知不正确的判定**被计入通过。
3. **因此：F1 仍不能计入 6 分档（即 0 → 6 不成立）**，更不能计入 8 分（5 种以上）或 10 分（复杂多跳）。
   **诚实的增量表述**是：F1 从第一轮的「**证据完全缺失**」推进到「**4 种结构、28 条可复现的真实库/真实文件记录，其中 1 种（P1）有真实模型端到端记录（5/6）**」；
   这是一种**材料形态的显著改善**，但它与官方「系统能正确回答 ≥3 种跨源问题」之间仍差一次**真实模型端到端**的举证。
4. **不得替实现者抬档的三条禁令**（本复核者据此判定）：
   - 不得把 `model_calls = 0` 的 28/28 说成「模型答对了 28 条跨源问题」；
   - 不得把 P1 单次运行的 5/6 说成稳定通过率（该卡已记录 cs-p1-02 在两次运行中分别 `completed` / `REPAIR_BUDGET_EXHAUSTED`，只有 cs-p1-05 稳定失败）；
   - 不得把「4 种结构」直接等同「4 种可回答的跨源问法」，因为其中 3 种不经过问句理解。

### 4.3 达到 6 分档的最小动作（按工作量排序）

| 序 | 动作 | 验收判据 | 工作量 |
|---:|---|---|---|
| 1 | 把 `data/knowledge_sources/` 的 14 份语料**发布进真实知识库**（需写库授权；本轮禁令下未做），再让 P2 问句走 `agent.py` 的通用跨源路由 + 真实 NL2SQL | 真实模型 live 下 P2 ≥3 条 `answered_and_verified`、`model_calls > 0`、七字段绑定完整 | 1–2 人日 |
| 2 | 修 §3.5 三处缺陷：`edge_between` 对称双向查找（一行）、`threshold_checks` 对象门控、冲突键纳入 `measurement_id`；复跑 `--check` 确认标签只加强 | `norm-p4-04/05` 引擎值 = 独立重算值；`norm-p3-09` 不再报假冲突；`--check` exit 0 | 2–3 人日 |
| 3 | 让 P3 至少 1 条冲突在真实模型 live 下产生（当前 P3 的真实值冲突只有 M001-1 一处，且在规则路径上） | live `CONFLICT_UNRESOLVED` 且**双方证据 100% 展示** | 1 人日 |
| 4 | 补 P1 的重复运行（≥2 次）以给出稳定通过率区间 | 两轮 P1 live `answered_and_verified` 均 ≥5/6 且失败例集合一致 | 0.5 人日（约 3 次模型时间） |

---

## 5. 全量回归口径核对（主协调者记录 vs 文档）

| 项 | 实测记录 | 来源 |
|---|---|---|
| 权威全量命令 | `cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py` | `tmp/regression_final_a.log` |
| **最终分母 / 失败 / 错误** | **1578 项执行 / 1574 通过 / 4 失败 / 0 错误**，退出码 **1** | `tmp/regression_final_a.log:1762-1764`（`Ran 1578 tests in 220.139s` / `FAILED (failures=4)`） |
| harness 摘要（原样） | `{"tests": 1578, "failures": 4, "errors": 0, "metadata_isolated": true, "model_settings_copied": false, "private_pdf_snapshot_rows_copied": false, "private_pdf_knowledge_rows_copied": false, "source_database_modified": false, "commit": "e4e2ebf", "ran_at": "2026-10-10T00:03:32.656486+00:00", "failed_tests": [...]}` | `tmp/regression_final_a.log:1772` |
| 失败清单（4 条，原样） | ① `test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable`；② `test_status_docs_sync.StatusDocumentsSyncTest.test_progress_states_the_recorded_denominator`；③ `…test_readme_states_the_same_denominator_and_version`；④ `…test_the_sync_script_reports_no_drift` | 同上 |
| **`reports/latest_regression.json` 现状** | `{"tests": 31, "failures": 0, "errors": 0, "commit": "e4e2ebf", "ran_at": "2026-10-10T00:28:37.141939+00:00", "failed_tests": []}` | 复核者 `cat` + `json.load`，exit 0 |
| `PROGRESS.md` 现状 | `\| 完整隔离回归 \| **1497/1497**（**这是分母口径**…该次 6 项红…）`；`\| 验证基线 \| 提交 \`e4e2ebf\`` | `git diff PROGRESS.md`（+8/−4） |
| `README.md` 现状 | `**当前完整隔离回归 1497/1497**`、`**当前隔离回归 1497/1497**（API 0.39.0）` | `git diff README.md`（+6/−3） |

### 5.1 判定：**仍不一致，需要第 3 次全量收敛**（不是全绿）

| 口径 | 分母 | 失败 | 说明 |
|---|---:|---:|---|
| 权威全量（`tmp/regression_final_a.log`，主协调者） | **1578** | **4** | 退出码 1 |
| `PROGRESS.md` / `README.md` 声明的当前值 | **1497** | 未声明当前失败数（写「该次 6 项红」） | **分母落后 81 项**；且 1497 是**上一轮**的权威分母 |
| `reports/latest_regression.json`（自称唯一记录源） | **31** | 0 | 被后续 `--pattern` 单文件运行覆盖，**不是全量** |

- `test_status_docs_sync.py:25` 把 `fraction` 定义为 `reports/latest_regression.json` 的 `tests/tests`，
  因此在记录为 31 时它断言 `PROGRESS.md` 含 `**31/31**`、`README.md` 含 `**当前完整隔离回归 31/31**`——
  **逻辑上必然失败**。日志里三条 `test_status_docs_sync` 失败的报错原文即为
  `Regex didn't match: '\| 完整隔离回归 \| \*\*50/50\*\*'`（运行期记录的分数）与
  `'**当前完整隔离回归 50/50**' not found`，以及 `status drift in: PROGRESS.md, README.md`。
- **结论（如实）**：**需要第 3 次全量收敛**，且必须按 `wp_fix2_regression_sync.md` §诚实边界②
  描述的两步序列执行（先写回「纯漂移」记录，再跑一次让漂移项转绿），并在两次运行**之间**
  同步 `PROGRESS.md` / `README.md` 到 **1578** 分母与 `e4e2ebf` 基线；
  **不得**在任何材料里写「1578/1578 全绿」或「1497/1497 全绿」。
- **可引用**：全量 **1578 执行 / 1574 通过 / 4 失败 / 0 错误，exit 1**；既有真实代码级回归 **1 条**
  （`test_agent_api`）；文档/记录漂移 **3 条**。

### 5.2 那条「真实代码级回归」的根因（复核者读码判定，非推测）

`tests/test_agent_api.py:110-123` 断言跨源问句的错误码属于
`{CROSS_SOURCE_EVIDENCE_INSUFFICIENT, DOC_BINDING_MISSING, CONFLICT_UNRESOLVED}`；
实际响应是 **`SOURCE_NOT_ALLOWED`**。追码：
`datamind/frame.py:552/606` 的范围判定在**未给 `database_sources`** 时返回 `SOURCE_NOT_ALLOWED`；
`datamind/agent.py:377/401/410/496` 与 `datamind/cross_source.py:1203` 也各自会产生该码。
该问句（「查询销售额前三产品并结合市场分析报告说明原因」）**没有显式 scope**，于是在**到达证据图引擎之前**
就在范围门被拒。
——这是第二轮 H1 加固（空 allow-list 必须拒绝，`r2_security_fixes.md §1`）**带来的新路径**，
而第二轮的测试契约更新只列了 3 个证据图稳定码、没有把 `SOURCE_NOT_ALLOWED` 纳入允许集合。
**这不是"测试没用"，而是一个真实的行为变化**：无 scope 的跨源问句现在被范围门拦下。
**最小修法（二选一，需协调者裁决）**：① 把 `SOURCE_NOT_ALLOWED` 纳入该断言的允许集合，并在 docstring
写明「无 scope 的跨源问句在范围门 fail-closed」；② 让该测试显式给出 `database_sources`，
以真正检验证据图引擎的三个稳定码。**复核者未改该文件**（只允许写本复核报告与指定交付件）。

---

## 6. 复核者执行的命令与退出码（证据索引）

| # | 命令 | 退出码 | 产物 |
|---|---|---:|---|
| V0 | `cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` | 0 | `e4e2ebf` |
| V0b | `date "+%Y-%m-%d %H:%M %Z"` | 0 | `2026-10-10 08:35 CST` |
| V0c | `curl -s http://127.0.0.1:8000/openapi.json \| .venv/bin/python -c "…"` | 0 | `openapi version 0.39.0 paths 75` |
| V0d | `curl -s http://127.0.0.1:8000/health` | 0 | `agent":"sql+rag"`；`capabilities` **无 `cross_source`**（线上仍是旧构建） |
| V1 | `.venv/bin/python data/knowledge_sources/build_norm_corpus.py --check`（17 次） | 13×0 / **4×1** | `tmp/review3/det_reruns.log`、`tmp/review3/probe_pdf_det.py`（根因探针）、`tmp/review3/regen_*.pdf` |
| V2 | `.venv/bin/python scripts/eval_cross_source_norm.py --check` | 0 | `tmp/review3/norm_check_rerun.json` |
| V3 | `.venv/bin/python scripts/eval_cross_source_norm.py --live --out tmp/review3/norm_live_rerun.json` | 0 | `tmp/review3/norm_live_rerun.json`（逐例 0 差异） |
| V4 | `.venv/bin/python scripts/eval_binding.py --check` | 0 | 见 V4 摘录 |
| V5 | `.venv/bin/python scripts/eval_cross_source.py --offline --out tmp/review3/cross_source_offline_rerun.json` | 0 | `tmp/review3/cross_source_offline_rerun.json` |
| V6 | `.venv/bin/python scripts/eval_clarification.py --offline --output tmp/review3/clarification_offline_rerun.json` | 0 | `tmp/review3/clarification_offline_rerun.json` |
| V7 | `.venv/bin/python scripts/eval_registry.py check` | 0 | `tmp/review3/det_reruns.log` |
| V8–V10 | `eval_faithfulness.py --validate-only`、`eval_robustness_v2.py --validate-only`、`eval_multifile.py --validate-only` | 0 / 0 / 0 | `tmp/review3/det_reruns.log` |
| V11 | `.venv/bin/python data/qa_samples/build_samples.py --check` | 0 | `{"ok": true, "problems": []}` |
| V12 | `PYTHONPATH="tests:$PWD" .venv/bin/python -m unittest tests.test_clarification_single_source tests.test_cross_source_norm` | 0 | `Ran 34 tests` / `OK` |
| V13 | 复核者自写只读脚本：语料 manifest 完整性 + suite 结构计数 | 0 | 14 files、30 条条款、on-disk sha256 0 不一致、28 例（12/9/7） |
| V14 | 只读提取 6 份机读报告（`json.load`）做内部一致性核对 | 0 | 见 §3.6 / §4.1 |

**复核者未做的事（边界）**：
1. **未跑任何真实模型评测**（faithfulness / robustness / multifile / scale profile_live / cross_source live 均未复跑）。
2. **未跑无参数全量隔离回归**（禁令；主协调者统一跑），因此 §5 的分母为**转引**。
3. **未修改任何被复核文件**：`git diff` 未触及 `reports/cross_source_norm_live.json`、`data/knowledge_sources/**`、
   `reports/faithfulness_v1.json`、`reports/robustness_v2.json`、`reports/multifile_v1.json`、`reports/scale_v2.json`、
   `datamind/clarification.py`、`tests/test_cross_source_norm.py`、`tests/test_clarification_single_source.py`、
   `PROGRESS.md`、`README.md`、`reports/latest_regression.json`。
4. **未做渗透测试**；§3 的分级是证据可计入性风险，不是安全漏洞分级。
5. **未给出任何自评分**；§4 的「不能计入」是**可计入性判断**，不是评委判定。

---

> **一句话交付**：第三轮不调模型的 10 条评测**全部由本复核者独立复现**（含 P2/P3/P4 的 28/28 逐例零差异），
> L4 契约下沉**属实**（生产包一等依赖 + 评测脚本只 re-export + 4 键未变）；
> 但 **1 条主张被证伪**（语料字节确定性门 17 次里 4 次失败，根因是 `/ID` 正则只认十六进制形态）、
> **F1 仍不能计入 6 分档**（4 种结构里 3 种 `model_calls = 0`、文档侧为自建语料、且 3 处引擎判定缺陷已冻结）、
> **全量回归仍需第 3 次收敛**（1578/1574，4 失败；文档写 1497，记录文件被 `--pattern` 覆盖成 31）。
