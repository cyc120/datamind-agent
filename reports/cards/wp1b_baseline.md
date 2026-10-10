# WP1-b 当前版本基线复测

| 字段 | 值 |
|---|---|
| **WP** | WP1-b「当前版本基线复测」（串行调用上游模型；任务规定的唯一允许并发调用模型的智能体） |
| **日期** | 2026-10-09 22:57（CST，北京时间） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`）**注意：工作区不干净**，见「诚实边界」第 1 条 |
| **API 版本** | 0.39.0（`curl -s http://127.0.0.1:8000/openapi.json` → `{'title': 'DataMind Agent', 'version': '0.39.0'}`） |

---

## 问题

《提升计划》WP1 与《审计报告》§4 均指出：历史 NL2SQL `14/14`、RAG `10/12` 来自**更早代码与更小样本**，不能代表 0.39.0。在动任何新功能之前，必须先拿到一份**当前版本、可复现、口径唯一**的基线复测，否则后续所有「提升」都缺少对照点。

## 做法

只做复测，**不改任何脚本或源码，不写库**：

1. 只读确认服务与题量：`/health` + `datamind_meta.evaluation_cases` 按 `suite` 分组（只读角色 `readonly_user`）。
2. **严格串行**（一条跑完再跑下一条）执行三套既有评测脚本，直接复用其原生判分逻辑，不调整语义：
   - `scripts/eval_sql.py`（nl2sql，14 题）
   - `scripts/eval_rag.py`（rag，12 题，冻结 `data/evaluation/rag_v2.json` + 库内题面/文件哈希双校验）
   - `scripts/eval_sql.py --suite robustness`（robustness，4 题）
3. 把三份原始输出汇总为统一 schema 的 `reports/baseline_current.json`（顶层 `cases[]` + `summary`，与 `scripts/eval_registry.py` / `data/evaluation/registry/registry.py` 的 `eval-report-v1` 对齐），并用官方校验器验收。
4. 与历史成绩**分列对照**，不合并口径。

## 数据与集

| suite | 题数 | 题面来源 | 判分口径（脚本原生） | 上游模型 |
|---|---:|---|---|---|
| nl2sql | 14 | 库内 `evaluation_cases`（只读镜像 `data/evaluation/registry/nl2sql_v1.json`，digest `a73bf740…3da6e9`） | 与 gold SQL 执行结果比对；预期澄清题看 `clarification_required` | deepseek-flash |
| rag | 12 | 冻结 `data/evaluation/rag_v2.json`（`suite_version=rag-v2`，`data_version=kb-v2`）+ `rag_v1.json` digest `f5d42555…2e8a33` | 固定事实断言 + gold 文档引用 + 引用 URL 200 定位 | deepseek-flash |
| robustness | 4 | 库内（`robustness_v1.json` digest `ec105871…d3943b`） | 同 nl2sql | deepseek-flash |

- `/health`：`{"status":"ok","database":"ready","knowledge":"ready","document_count":27,"vector_store":"ready"}`（实际执行 `curl -s http://127.0.0.1:8000/health`，**EXIT=0**）。
- 库内题量只读确认：`nl2sql 14 / rag 12 / robustness 4`（**EXIT=0**），与任务给定数字一致。
- 三套题面经 `scripts/eval_registry.py check` 校验：**3/3 一致，EXIT=0**（`[一致] nl2sql_v1.json: suite=nl2sql cases=14 digest=a73bf740e4ad4eb9…`）。
- 数据为**自建演示集**（零售 demo_retail / Chinook / 演示知识库 kb-v2），**非官方测试集**；快照 `snap-demo_retail-e9495c660cd1`、`kb-b016fe60e51852d6`。
- **上游错误与重试：本轮 0 次**，未触发 20s×3 重试（即三套评测全程无上游 API 失败）。

## 结果（分子分母）

统一汇总件：`reports/baseline_current.json`（30 题）。**准确率 = 通过数 / 总题数，所有拒答/预期澄清均留在分母内。**

| suite | 通过/总题数 | 准确率 | failed | refused（误拒） | errors（上游） | 预期拒答判对 |
|---|---:|---:|---:|---:|---:|---:|
| nl2sql | **13/14** | 0.9285714285714286 | 1 | 0 | 0 | 2（nl2sql-013/014） |
| rag | **11/12** | 0.9166666666666666 | 0 | 1 | 0 | 1（rag-012） |
| robustness | **4/4** | 1.0 | 0 | 0 | 0 | 1（rb-003） |
| **合计** | **28/30** | **0.9333333333333333** | **1** | **1** | **0** | **4** |

总表关键数字（原样摘录 `reports/baseline_current.json` 的 `summary`）：

```
"total": 30, "passed": 28, "failed": 1, "refused": 1, "errors": 0,
"accuracy": 0.9333333333333333, "accuracy_denominator": 30,
"refusal_rate": 0.16666666666666666, "refusal_denominator": 30,
"refusal_numerator_all": 5, "refusals_expected_correct": 4,
"refusals_unexpected": 1, "refusal_rate_unexpected": 0.03333333333333333,
"upstream_errors": 0,
"failure_counts": {"sql_repair_budget_exhausted": 1, "rag_false_abstain": 1}
```

- **拒绝率**：系统拒答/澄清共 **5/30 = 0.1667**（其中 4 题为**预期**澄清/拒答且判对，1 题为**误拒**）；**误拒率 = 1/30 = 0.0333**。`refused` 桶与 `passed` 互斥；预期拒答判对同时计入 `passed` 与 `refusals_expected_correct`（二者不互斥，已在 JSON 的 `accounting_note` 写明）。
- **上游错误数 = 0**（无超时/限流/额度错误）。
- **失败分类计数**：`sql_repair_budget_exhausted = 1`、`rag_false_abstain = 1`。
- **引用与定位是否齐全（RAG 11 道应答题口径）**：**10/11 齐全**（`cited_gold=True` 且所有 `source_url`/`page_url` 返回 200）；`rag-010` 为 0 引用（False）；`rag-012` 为预期拒答，引用要求 **N/A**。
- RAG 检索层（与答案层分开报）：`Recall@5=1.0`、`MRR@10=0.9545454545454546`（`answerable_count=11`，摘录自 `reports/rag_current.json`）。**检索全中，失败发生在生成/拒答判定。**

### 与历史成绩对照（**两次数字不拼接**）

| suite | 历史（文件与时间） | 本次（0.39.0 复测） | 差值 | 口径差异 |
|---|---:|---:|---:|---|
| nl2sql | `reports/nl2sql.json` **14/14**（2026-09-30 10:09） | **13/14** | **−1** | 历史为更早代码；本次工作区含并行未提交改动 |
| rag | `reports/rag.json` **10/12**（2026-10-01 23:54，`rag-v2`） | **11/12** | **+1** | `datamind/knowledge.py`/`rag_engine.py`/`agent.py` 哈希均已变化 |
| robustness | `reports/robustness.json` **4/4**（2026-09-30 10:09） | **4/4** | 0 | 同题同集，仅代码版本不同 |

**RAG 失败集合发生替换，不是单纯「进步」**：历史失败 `{rag-002, rag-006}` → 本次失败 `{rag-010}`，即 `rag-002`、`rag-006` **新通过**，`rag-010` **新失败**（历史该题为 COMPLETED 通过）。因此「11/12 vs 10/12」不能用来说明答案质量整体提升，只能说失败位置转移了。

## 失败样本

1. **`nl2sql-010`（同比，hard）— status=`failed`，`error_code=REPAIR_BUDGET_EXHAUSTED`，`sql=null`，`row_count=0`，分类 `sql_repair_budget_exhausted`。**
   三次受控修复尝试全部被门禁拒绝（原样摘录 `attempts`）：
   `[{"attempt": 1, "error_code": "METRIC_EXPRESSION_MISMATCH"}, {"attempt": 2, "error_code": "COLUMN_NOT_ALLOWED"}, {"attempt": 3, "error_code": "METRIC_EXPRESSION_MISMATCH"}]`
   这是**系统侧受控生成/修复预算耗尽**，不是上游 API 错误（无上游错误码，未重试）。系统最终 fail-closed 未产出 SQL，而非给出错误答案。
2. **`rag-010`（扫描件，hard）— status=`ABSTAINED`，`error_code=INSUFFICIENT_EVIDENCE`，分类 `rag_false_abstain`（误拒）。**
   检索已把 gold 文档排到 **rank=1**（`08_产品质检报告_数码品类_扫描版`），但生成阶段拒答：`answer="当前可访问的知识库没有提供足够证据，请补充相关文档或确认问题范围。"`，`cited_gold=False`，`locators_resolved=False`，`answer_checks=[False]`，`citations=[]`。**检索对、答案错**。
3. **脚本标签匹配边界（只读发现，未修改脚本）— `rb-004`（鲁棒-空结果）的 PASS 未真正检验「空结果必须显式标记 EMPTY_RESULT」。**
   `scripts/eval_sql.py` 中该特判分支的条件是 `category == "空结果"`，而库内该题 `category` 实为 `鲁棒-空结果`，字符串不等 → 分支未触发，判定退回到与 `gold_sql` 的普通行比对（双方均为 1 行 NULL）后判 PASS。即：**该题的「EMPTY_RESULT 显式标记」断言本轮未被检验**。本次未改脚本，如实登记。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"

# 0) 服务与题量（只读）
curl -s http://127.0.0.1:8000/health                       # EXIT=0；document_count=27
docker exec datamind-postgres psql -U readonly_user -d datamind_meta \
  -c "SELECT suite, count(*) FROM evaluation_cases GROUP BY suite ORDER BY suite;"   # EXIT=0；14/12/4

# 1) 三套评测，严格串行
.venv/bin/python scripts/eval_sql.py  --output reports/nl2sql_current.json
#   EXIT=1（存在失败题）；stdout: Execution match: 13/14; model=deepseek-flash
.venv/bin/python scripts/eval_rag.py  --output reports/rag_current.json
#   EXIT=1；stdout: Recall@5=1.0, MRR@10=0.9545454545454546, passed=11/12
.venv/bin/python scripts/eval_sql.py --suite robustness --output reports/robustness_current.json
#   EXIT=0；stdout: Execution match: 4/4; model=deepseek-flash

# 2) 统一 schema 验收（对 reports/baseline_current.json）
.venv/bin/python scripts/eval_registry.py schema --report reports/baseline_current.json
#   EXIT=0；stdout: [符合 schema] …/reports/baseline_current.json
.venv/bin/python scripts/eval_registry.py check             # EXIT=0；[一致] ×3
```

三套评测实际耗时约 41s / 43s / 29s（`START/END` UTC 时间戳：`14:54:25→14:55:06`、`14:55:09→14:55:52`、`14:55:57→14:56:26`）。

## 诚实边界（不能据此声称什么）

1. **测的不是纯净 `e4e2ebf`。** `HEAD=e4e2ebf`，但工作区含**并行其他 WP 的未提交改动**（`git status --porcelain` 实测：`M datamind/agent.py`、`M datamind/api.py`、`M datamind/industrial_rules.py`、`M datamind/sql_engine.py`、`M scripts/eval_scale.py`、`M scripts/eval_schema.py`、`M scripts/start_local.sh`，另有多个未跟踪新文件）。8000 端口服务即由此工作区启动。为此记录工作区摘要：`sha256(datamind/*.py + scripts/eval_*.py 按路径排序的逐文件哈希) = 158e90c2c28d5071cbeed1f5886de13b0ad184b43a3511989d434361d05a1b4c`（58 个文件），RAG 侧另有 `reports/rag_current.json.code_hashes`（`knowledge.py=5d570f1c…`、`rag_engine.py=ab0baf05…`、`agent.py=3b3ae4b2…`）。**结论只对上述工作区状态成立**，不能等同于某次干净提交。
2. **样本小，非官方集。** 总计 30 题（14/12/4），全部自建；单次运行、无重复、无显著性检验。不能据此外推官方得分、排名或「业界水平」。
3. **两次数字不拼接。** 本次 `nl2sql 13/14` 与历史 `14/14` 分属不同代码/时间/工作区，**不得合并或择优引用**；`rag 11/12` 亦不得与历史 `10/12` 说成「提升 +1」，因为失败集合已替换（见上）。
4. **子集规模不足，不能声称高档位。** `robustness` 仅 4 题（计划要求 ≥10）、复杂 SQL 与多文档 RAG 无独立新增子集、「拒答/不可答」仅 3 条。因此**不能**据此声称 E1/E5 ≥90% 满分档、E2/E6 高档、E9 达标，也不能声称 F4/F5 已有独立支撑。
5. **`rb-004` 的空结果断言未被真正检验**（见失败样本 3）；该题 PASS 不应被读作「EMPTY_RESULT 机制已验证」。
6. **未做**：多轮上下文、跨源融合、效率/并发、新文件问答闭环均未在本轮测试范围内；未跑隔离回归（本轮未新增测试文件，其不在允许新增清单内，且 `scripts/test_isolated.py` 会改写 `reports/latest_regression.json`）；全量 1297 项回归未跑、分母未变。
7. **上游错误数 0 不等于上游稳定**：仅表示本轮 3 次串行评测恰未遇到超时/限流/额度错误；重试策略（20s×3）本轮未被实际触发验证。
8. **未改任何脚本或源码，未做任何 git 写操作，未写演示库**；仅新增 5 个文件（3 份原始输出 + 1 份汇总 + 本卡）。统一 schema 只覆盖 `baseline_current.json`；三份脚本原生输出（`*_current.json`）**不符合** `eval-report-v1`（缺 `rows/citations/verdict/failure_reason/summary`），这是脚本产出方的既有形态，本轮未改造。

## 评分联动

| 官方/计划项 | 本卡提供的证据 | 目标分 | 当前判断 |
|---|---|---|---|
| E1 基础单表 NL2SQL（10） | nl2sql 13/14，其中单表/时间/枚举/别名题全通过 | 10 | **部分**：单表仅 4–5 题，样本不足以判满分档 |
| E2 单文档 RAG（10） | rag 11/12，引用与定位 10/11 齐全 | 8–9 | **接近**：有当前版本实测，但仅 12 题 |
| E5 NL2SQL 执行准确率（10） | 0.9285714285714286（13/14），误拒 0 | 9–10 | **有条件**：≥90% 档差 0.7 个百分点，1 题即翻转，须扩样 |
| E6 RAG 忠实度（10） | 逐题「断言+引用+定位」三态判定，失败样本保留 | 8 | **未坐实**：本轮判的是答案正确性，非逐结论忠实度标注 |
| E9 鲁棒性（10） | robustness 4/4 | 8–9 | **部分**：4/10 题，覆盖不足 |
| 计划 M1 出口判据 | 「冻结基线与澄清验收齐备」 | — | **本卡补齐「当前版本基线」一环**；新增子集仍缺（WP1 第 3 项） |
| F4 复杂 NL2SQL / F5 多文档 RAG | 失败样本与口径模板可复用 | F4 7 / F5 7 | **未启动**（子集缺失） |

> 本卡只声明：在 `e4e2ebf` + 上述工作区状态、0.39.0 API、deepseek-flash、30 条自建题上一次串行复测得到 **28/30**，并如实保留 2 个失败样本与 1 个脚本标签边界。**不据此声称任何官方得分、不把两次成绩拼接、不外推业界水平。**
