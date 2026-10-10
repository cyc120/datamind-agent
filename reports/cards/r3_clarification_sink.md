# R3 · 收口：澄清槽位契约下沉（复查 L4）+ L3/L5 收尾核实

- **WP**：R3 收口轮 · 生产服务路径解耦（第 1 轮复查 L4），附带 L3/L5 收尾核实
- **日期**：2026-10-10
- **基线提交**：`e4e2ebf`（只读 `git rev-parse --short HEAD`；本 WP 未做任何 git 写操作）
- **API 版本**：`0.39.0`（`datamind/api.py:198` `FastAPI(..., version="0.39.0")`）

## 问题（一句话）

第 1 轮独立复查 L4：`datamind/api.py` 在**请求期**执行
`from scripts.eval_clarification import clarification_contract_for_answer`，而 `scripts/`
没有 `__init__.py`——**生产服务路径**耦合了评测脚本，评测脚本一旦被移动/删除，线上澄清
投影就只剩 `try/except` 空兜底（不 500，但契约字段静默消失）。契约实现（槽位表 / 正则表 /
检测器 / 投影）当时也长在评测脚本里（`scripts/eval_clarification.py:38-148`），位置本身就是根因。

## 做法（改了什么，不做什么）

1. **契约唯一实现下沉到生产包**：新建 `datamind/clarification.py`，把原
   `scripts/eval_clarification.py:38-148` 的 `CONTRACT_VERSION` / `SLOT_ORDER` /
   `SLOT_QUESTIONS` / `DEFAULT_PRODUCTS` / 12 条正则 /
   `RULE_ANSWER_SLOT_PATTERNS` / `has_time` / `detect_missing_slots` /
   `slot_candidates` / `clarification_contract` / `rule_slots_for_answer` /
   `clarification_contract_for_answer` **逐字搬移**（返回键与语义不变：四个键
   `clarification_candidates` / `clarification_contract_version` /
   `clarification_missing_slots` / `clarification_slot_questions`）。
2. **API 改从生产包导入**：`datamind/api.py` 顶部新增
   `from .clarification import clarification_contract_for_answer`，请求期不再有
   `from scripts...`；`_with_clarification_contract()` 只保留「投影抛错 → 原样返回」的兜底，
   `/v1/chat` 与 `/v1/chat/stream` 的接线一字未动。
3. **评测脚本只做再导出**：`scripts/eval_clarification.py` 删除全部槽位表/正则表/检测器定义
   （顺带删掉因此不再使用的 `import re`），改为 `from datamind.clarification import (...)`；
   评测逻辑（`merge_supplement` / `merge_slot_state` / `stub_reply` / `_metrics` / 两臂 /
   `--offline` / `--live` / CLI 参数与输出）保持原样。
4. **契约测试**：新增 `tests/test_clarification_single_source.py`（13 例）——断言两条路径是
   **同一函数对象**（`is` 比较）、脚本源码里没有第二份定义（14 个禁用定义串全 0 命中）、
   API 源码不再引用 `eval_clarification`、同一输入下 API 与脚本逐键一致、投影抛错时响应不变。
5. **L3 收尾（只核实）**：`web/style.css` 已含 `.clarification` / `.clarification-row`
   （5 处命中，`style.css:72-76`，含 `.clarification-row button`）；`web/app.js:576,587`
   渲染同名类。**缓存版本确认失同步并最小修复**：`app.js`（+40 行澄清面板）与
   `style.css`（+5 行）在工作树中已改，而 `web/index.html` 仍是 `?v=0.41.0` 且相对 HEAD 未改
   ——正是 `test_frontend_hygiene.py` 文档警告的「改了 app.js 不提升 ?v=」。本 WP 允许改
   `web/index.html`，故把三处 `?v=0.41.0` 同步提升为 `?v=0.42.0`（三处保持一致）。
6. **L5 收尾（只核实，不改代码）**：`datamind/agent.py:136-152` `_event` 已是
   **先 `sink(event)` 后 `INSERT trace_events`**，落库用的就是 sink 改过的同一个
   `event["output_summary"]`；`datamind/api.py:1094-1097` 的 `chat_stream` sink 在
   `event_type == "answer.clarification"` 时补齐澄清键后再入队。读代码 + 跑既有
   `tests/test_security_round2.py`（含 `L5EventPersistenceParityTest`、`L3ClarificationStyleTest`）
   确认，本 WP **未重复修改**这两处。

## 数据与集

| 项 | 值 |
|---|---|
| 冻结集 | `data/evaluation/clarification_v1.json`（`clarification-v1`，25 例：clarify 18 / answer 7），本 WP 未改 |
| 契约版本 | `clarification-slots-v1`（键名与取值不变） |
| 新增实现文件 | `datamind/clarification.py`（唯一实现，只依赖标准库 `re`） |
| 新增测试 | `tests/test_clarification_single_source.py`（13 例，无 DB 依赖） |
| 再导出文件 | `scripts/eval_clarification.py`（调用面不变） |

## 结果（分子分母）

### 单一实现核查（全部为源码/对象级断言）

| 核查 | 结果（分子/分母） |
|---|---|
| `scripts.eval_clarification` 与 `datamind.clarification` 同名函数是同一对象 | **6/6**（`is` 比较：`clarification_contract`、`clarification_contract_for_answer`、`detect_missing_slots`、`slot_candidates`、`rule_slots_for_answer`、`has_time`） |
| 脚本内第二份定义（槽位表/正则表/检测器共 14 个定义串） | **0/14 命中**（全部只存在于 `datamind/clarification.py`） |
| 全仓库 `SLOT_QUESTIONS =` / `RULE_ANSWER_SLOT_PATTERNS =` / `def detect_missing_slots` 定义处 | **1/1 份**（`datamind/clarification.py`；`datamind/sql_engine.py:184` 的 `CLARIFY_TIME_RANGE` 是规则式反问**话术**，不是槽位表） |
| `datamind/api.py` 中 `scripts.eval_clarification` 引用 | **0/0**（`grep -n "scripts.eval_clarification\|from scripts" datamind/api.py` 无输出） |

### 回归（隔离克隆，`scripts/test_isolated.py --pattern`，commit `e4e2ebf`）

| 测试文件 | 退出码 | 结果（分子/分母） |
|---|---|---|
| `test_clarification_flow.py` | **0** | **23/23**（failures=0, errors=0） |
| `test_clarification_single_source.py`（新） | **0** | **13/13**（failures=0, errors=0） |
| `test_frontend_hygiene.py` | **0** | **8/8**（含 `?v=` 三处一致且 > 0.37.0，现 0.42.0） |
| `test_multi_turn.py` | **0** | **33/33** |
| `test_agent_api.py` | **1** | **4/5**（1 例失败，见失败样本 1，与本 WP 无关） |
| `test_security_round2.py`（附加：L3/L5 守护） | **0** | **31/31** |
| `test_upload_ui.py` / `test_upload_selector_ui.py`（附加：版本下界 0.38.0） | **0 / 0** | **22/22 / 16/16** |

### 评测脚本行为未变（`--offline`，本 WP 实跑）

```json
detector success {'numerator': 18, 'denominator': 18, 'rate': 1.0}
         slot_landing {'numerator': 14, 'denominator': 14, 'rate': 1.0}  asked 18/18
deployed success {'numerator': 4, 'denominator': 18, 'rate': 0.2222}
         slot_landing {'numerator': 4, 'denominator': 4, 'rate': 1.0}   asked 4/18
两臂 不必要澄清 = 0/7（占全部 25 例 0/25）；上表与第 1 轮 `wp4_b3_clarification.md` 逐值一致
failures 14（deployed 14 例「根本没反问」，仍在分母内）
```

## 失败样本（全部保留）

1. **`test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable` 失败
   （exit 1）**：响应 `error_code='SOURCE_NOT_ALLOWED'`，测试期望
   `{CROSS_SOURCE_EVIDENCE_INSUFFICIENT, DOC_BINDING_MISSING, CONFLICT_UNRESOLVED}` 之一。
   **与本 WP 无关，且不在本 WP 允许改动集内**，证据：
   (a) `SOURCE_NOT_ALLOWED` 由 `datamind/frame.py:552,606` 的范围判定产生，`frame.py` 本会话
   未改动；该响应 `status != NEEDS_CLARIFICATION`，`_with_clarification_contract()` 对其是
   纯 no-op（函数第一行即原样返回）；
   (b) 该测试文件本身是**别人未提交的改动**（`git diff --stat tests/test_agent_api.py` =
   +18/−2，第 1 轮复查 H2 已指出这里的契约断言在改）；
   (c) 与本 WP 并发的全量隔离回归记录
   `reports/latest_regression.json`（`{"tests": 1578, "failures": 4, "commit": "e4e2ebf"}`）
   已含同一条失败，另有 3 条 `test_status_docs_sync` 失败（文档分母同步，非本 WP 范围）。
2. **未做**：`--live`（真实 `/v1/chat`）未跑——硬性禁令禁止重启 8000 服务，运行中进程仍是
   旧代码，live 澄清键结论与第 1 轮相同（`clarification_contract_keys = []`），本 WP 不重复。
3. **共享产物副作用**：`scripts/test_isolated.py` 每次会把
   `reports/latest_regression.json` 改写成本次运行计数（现为该文件的并发的全量记录
   `{"tests": 1578, ...}` 之前/之后被本 WP 的 8 次单 pattern 跑覆盖过）；引用本卡数字请以
   卡内命令输出为准，不要用该文件反推本 WP 用例数。

## 复现命令

```bash
# 0) 基线
cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD          # e4e2ebf

# 1) 单一实现/解耦契约测试（无 DB，可直跑）
cd "/Users/caicai/DataMind Agent" && PYTHONPATH="tests:$PWD" .venv/bin/python -m unittest tests.test_clarification_single_source -v
#    → exit 0；Ran 13 tests / OK

# 2) 隔离回归（逐 pattern；禁止无参数全量）
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_clarification_flow.py         # exit 0；{"tests": 23, "failures": 0, "errors": 0, "commit": "e4e2ebf"}
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_clarification_single_source.py # exit 0；{"tests": 13, "failures": 0, "errors": 0}
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_frontend_hygiene.py            # exit 0；{"tests": 8,  "failures": 0, "errors": 0}
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_multi_turn.py                 # exit 0；{"tests": 33, "failures": 0, "errors": 0}
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_agent_api.py                 # exit 1；{"tests": 5, "failures": 1, "failed_tests": ["test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable"]}
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_security_round2.py            # exit 0；{"tests": 31, "failures": 0, "errors": 0}（L3 样式 + L5 落库/SSE 一致）

# 3) 评测脚本行为未变（真实命令；输出写 tmp/ 不覆盖交付报告）
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/eval_clarification.py --offline --output tmp/r3_l4_clarification_offline.json
#    → exit 0；detector 18/18、deployed 4/18、不必要澄清 0/7、slot_landing 14/14 与 4/4、failures 14

# 4) 静态核查（只读）
cd "/Users/caicai/DataMind Agent" && grep -n "scripts.eval_clarification\|from scripts" datamind/api.py        # 无输出
cd "/Users/caicai/DataMind Agent" && grep -c "clarification" web/style.css                                     # 5
cd "/Users/caicai/DataMind Agent" && grep -n "?v=" web/index.html                                              # 三处均为 0.42.0
```

## 诚实边界（不能据此声称什么）

- **不能说运行中的 8000 服务已解耦**：本会话禁止重启服务，验证都在进程内
  （`fastapi.testclient` 与源码断言）完成。运行中进程仍是旧代码——线上响应仍可能不带
  `clarification_*` 键；解耦要等文件负责人重启后生效。
- **不能说「导入不可能失败」**：语义有**一处刻意的收紧**——原先 `try/except` 兜住的是
  `from scripts...` 的导入失败，现在 `datamind/clarification.py` 是生产包的一等依赖
  （与 `.agent` / `.sql_engine` 同级），它若损坏则 `datamind.api` 直接导入失败；`try/except`
  现在兜的是**投影调用**抛错（新增测试 `test_a_failing_projection_never_breaks_the_clarification_response`
  覆盖）。这正是「生产路径不再依赖评测脚本」的应有形态，但确与改前的兜底范围不同。
- **不能说「0.42.0 已解决缓存问题」**：版本号同步提升只改 `web/index.html` 三处字符串，
  **未在浏览器里验证**回访加载；真实浏览器行为与 `?v=` 是否已在部署环境生效未测。
- **不能说 L3/L5 是本 WP 修的**：`web/style.css` 的样式与 `agent.py` 的
  sink→落库顺序是第 1/2 轮成果（`test_security_round2.py` 已守护），本 WP 只做只读核实，
  未重复改动；`web/app.js` 本 WP 一字未改。
- **不能把 `test_agent_api.py` 的 4/5 说成全绿**：1 例失败如实保留（失败样本 1），
  根因在跨源范围判定与他人未提交的测试契约改动，本 WP 无权修；另有 3 条
  `test_status_docs_sync` 失败见 `reports/latest_regression.json`。
- **单测的「唯一实现」是模式级证明**：断言的是指定文件里 14 个定义串与同名函数对象，
  不等于「全仓库语义上不存在任何重复规则」；`datamind/sql_engine.py` 的 `clarify()` 话术
  仍是独立的正则（它是**被投影的输入**，不是槽位契约的第二份实现）。
- **不能外推评测分数**：`clarification-v1` 是自建冻结集，18/18 是检测器与标签同源的自洽性
  检查（review 已指出），不代表官方 E8/F2 得分。

## 评分联动

- **官方 E8 中级任务（B3 语义校验与澄清）**：澄清契约现在有唯一实现、单一来源可测，且
  **生产服务不再在请求期依赖评测脚本**——把「会反问 + 有结构化契约」从「靠一个评测脚本顺带
  提供」升级为「生产包内的一等能力 + 回归守护」。
- **官方 F2 意图理解与澄清**：键名/键值/语义零变化（新增测试逐键比对），既有澄清指标
  （detector 18/18、deployed 4/18、不必要澄清 0/7）在重构后**逐值复现**，可作为「重构未
  改变行为」的证据；但线上 live 结论仍受禁重启限制，不能主张 F2 满分。
- **官方 F1（多源融合）**：本 WP 不触碰绑定/跨源逻辑，**不贡献** F1 分数；跨源 P2/P3/P4 的
  文档侧证据缺口仍按主协调者口径保留。
- **工程健壮性/交付卫生**：消除一处「生产 ← 评测」反向依赖（第 1 轮复查 L4），并把
  `web/index.html` 的 `?v=` 从失同步的 0.41.0 提升到 0.42.0；L3（样式）与 L5（流式/落库一致）
  经既有守护测试复核关闭，两项均**有测试守护**（`test_security_round2.py` 31/31）。
