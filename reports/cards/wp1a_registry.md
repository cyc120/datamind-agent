# WP1-a 冻结评测登记（离线部分）

| 字段 | 值 |
|---|---|
| **WP** | WP1-a「冻结评测登记」（对应《提升计划》WP1 第 1/2/4 项；只做离线部分，不调用上游模型） |
| **日期** | 2026-10-09 22:19（CST，北京时间） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`；冻结文件内 `commit` 字段同为 `e4e2ebf`） |
| **API 版本** | 0.39.0（`GET http://127.0.0.1:8000/openapi.json` 的 `info.version` = `0.39.0`；`datamind/api.py:197` `version="0.39.0"`） |

---

## 问题

《提升计划》WP1 原文：「历史 NL2SQL 14/14 与 RAG 10/12 来自更早代码和小样本，不能代表 0.39.0；且题库在 `evaluation_cases` 表里、冻结哈希只在部分 RAG 题上生效，证据链不自洽。」
《审计报告》§4 亦指出：「历史 SQL 14/14 和 RAG 10/12 均来自小样本与较早代码，不能替代当前版本的正式评测。」

具体缺口（本轮开始前实测）：

1. 题库**只存在于 PostgreSQL**，仓内没有任何可离线比对的题面副本 → 无法证明「评委看到的题」与「当时跑评测用的题」是同一份。
2. `scripts/eval_rag.py` 只用 jsonb 单题校验题面（`scripts/eval_rag.py:48-50`），`scripts/eval_sql.py` 完全不校验；**没有跨 suite 的统一登记与摘要**。
3. 没有任何机制保证已发布的题面不被就地改写（可被 `UPDATE evaluation_cases` 静默改题）。

## 做法

新增（不改任何既有文件、不写库、不调用上游模型）：

1. **`data/evaluation/registry/registry.py`**（纯标准库 + `psycopg2` 只读）
   - 连接强制 `set_session(readonly=True)`，只执行 `SELECT`；库只读角色 `readonly_user`。
   - 把 `evaluation_cases` 的 `nl2sql / rag / robustness` 三套题冻结为统一结构：每例含 `case_id / question / category / difficulty / expected_route / gold{sql,claims} / clarification_label / failure_category / data_version / required_evidence / case_digest`。
   - 顶层：`suite / suite_version / data_version / data_versions / source('datamind_meta.evaluation_cases') / created_at / commit / model(null) / prompt_version(null) / offline_freeze(true) / case_count / digest`。
   - **append-only**：`open(path, "x")` 独占创建，已存在抛 `RegistryExistsError` 并提示 `--version v2`。
   - `digest` 只覆盖 `suite / suite_version / data_version / data_versions / source / cases`，**不含 `created_at`/`commit`** → 同一题面重复冻结得到同一摘要（幂等）。
   - `check_registry()` 在「登记 ≠ 库内」时抛 `RegistryMismatch`，`diff` 逐字段给出 `missing_in_db / extra_in_db / changed[{case_id,field,registry,database}] / digest_ok`。
   - `validate_report()`：统一报告 schema 校验（逐题 `case_id/status/sql/rows/citations/verdict/failure_reason`；汇总 `total/passed/failed/refused/errors/accuracy/refusal_rate/failure_counts`），零分母必须为 `"N/A"`。
   - **不做**：不改题、不写库、不补新题、不跑模型。
2. **`scripts/eval_registry.py`**：`freeze --suite X [--version v1]`、`check [--suite X]`、`schema [--report PATH]`。
3. **`tests/test_eval_registry.py`**：15 项，覆盖冻结一致性、题面被改动时 check 失败（临时文件 + 内存题面，不碰库）、append-only 拒绝覆盖、schema 缺字段报错、零分母报 N/A。

## 数据与集

| suite | 登记文件 | suite_version | data_version | 题数 | digest（sha256，原样摘录） |
|---|---|---|---:|---:|---|
| nl2sql | `data/evaluation/registry/nl2sql_v1.json` | `nl2sql-v1` | `retail-v2` | 14 | `a73bf740e4ad4eb9bab19594b4f6209e6a96bc5c08762d7b160673a79c3da6e9` |
| rag | `data/evaluation/registry/rag_v1.json` | `rag-v1` | `kb-v2` | 12 | `f5d42555a58310480b4892eac34fe2afab9195e8fa1dba2de7e8645e6e2e8a33` |
| robustness | `data/evaluation/registry/robustness_v1.json` | `robustness-v1` | `retail-v2` | 4 | `ec105871014d29318d0a946bc3aaa18825784475a268ef9f6e0fdc610fd3943b` |

题面来源：库内 `datamind_meta.evaluation_cases`（本文件即该表的**只读镜像**）；数据为自建演示集（零售 demo_retail / Chinook / 演示知识库），**非官方测试集**。三份登记文件均为本轮新建，`created_at` = `2026-10-09T14:19:2x+00:00`，`commit` = `e4e2ebf`，`model` = `null`（离线冻结，未调用模型）。

结构分布（从登记文件重算，非人工填写）：

- `nl2sql`（14）：单表过滤 2、时间范围 1、枚举值 1、别名/黑话 1、JOIN 2表 1、聚合+JOIN 1、排序Top-k 1、退款JOIN 3表 1、同比 1、跨库-Chinook 1、跨库-Chinook JOIN 1、澄清 2；difficulty `{easy:4, medium:5, hard:3, clarify:2}`。
- `rag`（12）：原文段落 1、规章制度 2、表格 2、公式 1、产品手册 1、FAQ 1、扫描件 2、OHR-Bench英文 1、不可得 1；difficulty `{easy:6, medium:3, hard:3}`。
- `robustness`（4）：错别字 1、口语简称 1、越权 1、空结果 1。

## 结果（分子分母）

| 检查 | 分子/分母 | 结论 | 命令退出码 |
|---|---:|---|---:|
| 冻结（三套题写登记文件） | 3/3 suite 成功 | `cases=14/12/4`，摘要已落盘 | `0` |
| `check`（登记 vs 库内题面） | **3/3 一致** | `[一致] nl2sql_v1.json: suite=nl2sql cases=14` / `rag` `cases=12` / `robustness` `cases=4` | `0` |
| 摘要自校验（重算 digest 与文件内 `digest` 比对） | **3/3 相同** | `stored=a73bf740e4ad4eb9 recomputed=a73bf740e4ad4eb9 match=True`（另两套同） | `0` |
| append-only（重复冻结同名版本） | **1/1 被拒绝** | 退出码 `2`，stderr：`登记文件已存在，拒绝覆盖（append-only）：…/nl2sql_v1.json；若题面确有变更，请使用新版本号，例如 --version v2` | `2`（预期拒绝） |
| 题面篡改检出（临时副本，未改库、未改仓内登记） | **1/1 检出** | 逐字段 diff：`字段不一致 nl2sql-001.question：登记='被篡改的题面…' 库内='有多少个客户注册了大区信息？'` + `文件整体 digest 与内容不符` | `1`（预期失败） |
| 隔离回归（自有测试文件） | **15/15 通过** | `Ran 15 tests` / `OK`；summary：`{"tests": 15, "failures": 0, "errors": 0, "commit": "e4e2ebf", "failed_tests": []}` | `0` |
| 统一 schema 检查既有评测报告 | **0/3 符合** | 三份历史报告问题数 `nl2sql.json=57`、`rag.json=49`、`robustness.json=17`（合计 123 项） | `1` |

## 失败样本

1. **既有评测脚本输出不符合统一 schema（0/3）——真实未完成项，不伪装。**
   - `reports/nl2sql.json`：14 例全部缺 `rows / citations / verdict / failure_reason`，且**无顶层 `summary`**（准确率/拒绝率/失败分类计数），问题 57 项。
   - `reports/rag.json`：12 例缺 `sql / rows / verdict / failure_reason`，无 `summary`，问题 49 项。
   - `reports/robustness.json`：4 例缺 `rows / citations / verdict / failure_reason`，无 `summary`，问题 17 项。
   - 原因：改造 `scripts/eval_sql.py` / `scripts/eval_rag.py` 的输出属**本次任务允许文件清单之外**（我不得改他人负责文件），故只交付检查器，未改造产出方。
2. **库内题面标签与实际 SQL 不一致（只读发现，未修改）。**
   - `nl2sql-006` 分类为「JOIN 2表」，其 `gold_sql` 实际 JOIN 3 张表（`orders`/`customers`/`regions`）。
   - `nl2sql-009` 分类为「退款JOIN 3表」，其 `gold_sql` 实为单表 `FROM refunds f WHERE … GROUP BY f.reason`。
   - 说明：分类字段只是标签，登记文件按库内原值冻结（不悄悄的替库改题）；发现即登记在此。
3. **WP1 第 3 项要求的新增子集未做（`not_attempted`，见下）。**

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"

# 冻结（已存在则退出码 2，拒绝覆盖）
.venv/bin/python scripts/eval_registry.py freeze --suite nl2sql      # EXIT=0
.venv/bin/python scripts/eval_registry.py freeze --suite rag          # EXIT=0
.venv/bin/python scripts/eval_registry.py freeze --suite robustness   # EXIT=0

# 校验登记 vs 库内题面（一致 EXIT=0；不一致打印逐字段 diff，EXIT=1）
.venv/bin/python scripts/eval_registry.py check                       # EXIT=0

# 题面篡改检出（临时副本；不碰库、不碰仓内登记文件）
mkdir -p tmp/wp1a_tamper
.venv/bin/python - <<'PY'
import json, pathlib
p = json.loads(pathlib.Path("data/evaluation/registry/nl2sql_v1.json").read_text(encoding="utf-8"))
p["cases"][0]["question"] = "被篡改的题面（仅临时副本，未改库、未改仓内登记）"
pathlib.Path("tmp/wp1a_tamper/nl2sql_v1.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
PY
.venv/bin/python scripts/eval_registry.py check --suite nl2sql --out-dir tmp/wp1a_tamper   # EXIT=1

# 统一 schema 检查（既有报告当前不符合，EXIT=1）
.venv/bin/python scripts/eval_registry.py schema                      # EXIT=1

# 隔离回归（只跑自有测试文件）
.venv/bin/python scripts/test_isolated.py --pattern test_eval_registry.py   # EXIT=0
```

失败样本 3 的复现（口径核算，只读）：

```bash
.venv/bin/python -c "import json,c;import collections;d=json.load(open('data/evaluation/registry/nl2sql_v1.json'));print(collections.Counter(x['category'] for x in d['cases']))"
```

## 诚实边界（不能据此声称什么）

1. **本轮是 `partial`，不是 WP1 完成。** 登记只覆盖**库内已存在的 30 条题（14+12+4）**，按《提升计划》WP1 第 3 项要求的**新增子集全部 `not_attempted`**：
   - 复杂 SQL（JOIN 3 表 + 聚合 + 嵌套）**≥15**：当前 0 条独立新增；库内 14 条里带 JOIN/聚合/CTE 标签的仅 5 条（JOIN 2表 1、聚合+JOIN 1、退款JOIN 3表 1、同比 1、跨库-Chinook JOIN 1）。
   - 多文档 RAG **≥8**：当前 0 条（`rag` 套题均为单文档问答）。
   - 拒答/不可答 **≥6**：当前 3 条非直接作答用例（`rag-012` `clarification_label=abstain` 1 条 + `nl2sql-013/014` `clarification_label=needed` 2 条）。
   - 鲁棒 **≥10**：当前 4 条。
   - **原因**：新增题必须「写库 + 真实调用上游模型」才能产出与验分，而本轮显式禁止写演示库、禁止调用上游模型；我选择如实写 `not_attempted`，**不伪造题数、不伪造通过率**。
2. **登记文件不是成绩。** 它只是「题面快照 + 一致性护栏」，不含任何准确率/通过率；本卡不给出任何 E1/E2/E5/E6 的分值结论，也不把历史上的 14/14、10/12 当作当前版本成绩。
3. **`check` 通过 ≠ 评测结果正确。** 它只证明「仓内题面 == 库内题面」；题目本身的正确性（例如失败样本 2 的分类标签）未被本轮校验覆盖。
4. **摘要不防「库与仓同时被改」。** 若有人同时改库与重冻结新版本号，`check` 只会在旧版本号上经由 `digest` 与题面差异暴露；配合 append-only 与版本号纪律才成立，本轮未实现签名/时间戳等更强机制。
5. **统一 schema 只落地了检查器，未落地产出方。** 因此「一次命令产出全部子集报告」这一 WP1 验收条件**未达成**；`schema` 现在对三份历史报告报 `0/3` 符合是**如实结果**，不是脚本缺陷。
6. **隔离回归只跑了自有测试文件（15 项），不代表全量 1297 项。** 本轮未跑全量回归；全量分母未变化。
7. **副作用如实声明**：`scripts/test_isolated.py` 会无条件写 `reports/latest_regression.json`（其设计如此）。本次运行把它从 `{"tests": 1297, "commit": "1512439"}` 改写为 `{"tests": 15, "commit": "e4e2ebf"}`；我在本卡交付前**已按原字节恢复**，恢复后 `shasum -a 256` = `c9e4d9d15382a1af66ee14ed2b0bb94336dc9c36a3c31e7c6d4a2bd84018a565`，与运行前一致（否则 `tests/test_status_docs_sync.py` 会因文档分母漂移报红）。另在 `tmp/wp1a_tamper/nl2sql_v1.json` 留有一份篡改临时副本（`tmp/` 已被 `.gitignore` 忽略），非仓内登记文件。
8. **未做 git 写操作**：本轮无 `add/commit/checkout/restore/stash`，`git status --porcelain` 仅显示新增文件（`data/evaluation/registry/`、`scripts/eval_registry.py`、`tests/test_eval_registry.py`）与仓库既有的未跟踪启动计划文件/`scripts/start_local.sh` 既有改动（非本卡产物）。

## 评分联动

| 官方/计划项 | 本卡作用 | 目标分 | 当前判断 |
|---|---|---|---|
| E1 基础单表 NL2SQL（10） | 提供「题面一致性」护栏与可离线比对快照 | 10 | **未兑现**：单表独立集仍需新增题 + 真实模型复跑 |
| E2 单文档 RAG（10） | 同上（12 条单文档题已冻结并可校验） | 8–9 | **未兑现**：无当前版本实测 |
| E5 NL2SQL 执行准确率（10） | 消除「旧高分在新版本失效」的题面漂移风险 | 9–10 | **未兑现**：本轮无准确率数字 |
| E6 RAG 忠实度（10） | 同上 | 8 | **未兑现** |
| E9 鲁棒性（10） | 冻结现有 4 条鲁棒题并纳入统一登记 | 8–9 | **部分**：4/10 题，覆盖不足 |
| F4 复杂 NL2SQL / F5 多文档 RAG | WP1 新增子集是其前提 | F4 7 / F5 7 | **未启动**（`not_attempted`） |
| 计划 M1 出口判据 | 「冻结基线与澄清验收齐备」 | — | **未达成**：仅登记与校验层完成；新增子集与当前版本复测缺失 |

> 本卡只声明已完成的三件事：**题面入仓（30 条）**、**一致性护栏（append-only + diff 校验）**、**统一 schema 检查器（0/3 既有报告）**。不据此声称任何准确率、任何官方得分或 WP1 整体完成。
