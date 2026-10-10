# WP4 · B3 语义校验与澄清完整闭环验收

- **WP**：WP4（B3 语义校验与澄清完整闭环）
- **日期**：2026-10-10
- **基线提交**：`e4e2ebf`
- **API 版本**：`0.39.0`

## 问题（一句话）

审计 §6 判定 B3「较强，暂列限定完成」：缺的正是完整闭环验收与**不必要澄清率**——
`sql_engine.py:179` 的 `clarify()` 只是三段规则式缺项检测，`agent.py` 把它映射成
`NEEDS_CLARIFICATION + AMBIGUOUS_QUERY`，既没有「缺项字段 + 候选值」的结构化契约，
也没有度量「本可直接回答却反问」，更没证明补槽后能真的落到问句帧。

## 做法（改了什么，不做什么）

1. **冻结集**：新增 `data/evaluation/clarification_v1.json`，25 条（≥20），覆盖六类——
   时间缺失 4、口径歧义（销售额 vs 订单数 / 退款金额 vs 笔数）4、粒度歧义（按客户 vs 按订单）4、
   跨版本歧义 3、对象别名歧义 3、**不该澄清的正向对照 7 条**。每例含 `expected_action`、
   `expected_missing_slots`、`expected_slot_values`（冻结的用户补充话术）、`frame_expectation`、
   `must_not_ask`。对象别名候选取自 `demo_retail.products` 的**只读查询**（真实产品名）。
2. **评测**：新增 `scripts/eval_clarification.py`，`--offline` 用确定性 stub 补槽器（逐字复用冻结
   补充话术，一问一答、不调用上游模型），补槽后调用真实 `sql_engine.query_frame()` 判定是否落到问句帧；
   同时评测两条臂——`detector`（本文件的六类结构化检测）与 `deployed`（线上实际使用的规则式
   `clarify()`）。`--live` 真实调用 `http://127.0.0.1:8000/v1/chat`（自动登录，临时账号跑完即删）。
3. **API 最小接线**：`datamind/api.py` 在 `NEEDS_CLARIFICATION` 时新增
   `clarification_missing_slots` / `clarification_candidates` / `clarification_slot_questions` /
   `clarification_contract_version` 四个键，`/v1/chat` 与 `/v1/chat/stream` 的终态事件同一套；
   `status` / `answer` / `error_code` / `confidence_level` / `turns_used` 的取值与含义**不变**。
   槽位契约只有一份实现（与冻结集共用），API、前端、评测不再各写一套。
4. **前端最小接线**：`web/app.js` 新增 `clarificationPanel/completeWithSlot/showClarification`：
   `NEEDS_CLARIFICATION` 时把候选值渲染成按钮，**点一次**即把槽位并进问题框并重跑（不重开对话）；
   缺新键时退化为提示「在问题框里补充」，绝不猜测槽位。
5. **不做**：不改 `sql_engine.py` / `agent.py`（不在本 WP 允许改动集内），因此**检测器新增的
   grain / version / alias 三类缺项没有被接进线上 API 路径**；不重启 8000 端口服务（硬性禁令）。

## 数据与集

| 项 | 值 |
|---|---|
| 冻结集 | `data/evaluation/clarification_v1.json`（`clarification-v1`，25 例：clarify 期望 18 / answer 期望 7） |
| 契约版本 | `clarification-slots-v1`（槽位 `time_range / metric / grain / object_alias / document_version`） |
| 补齐话术 | 冻结在集内，stub 逐字复用（不调用模型） |
| 帧契约 | 真实 `datamind/sql_engine.py:query_frame()`，只读调用 `Catalog.retrieve` |
| 是否自建 | 自建冻结集（非官方基准），期望先冻结后评测 |
| 别名候选 | 演示库 `products` 只读查询（如 `无线降噪耳机 Pro` / `真无线耳机 Air`） |

## 结果（分子分母）

**测试**（隔离回归，`commit e4e2ebf`）：
`test_clarification_flow.py` **23/23**（`failures=0, errors=0`）；
回归护栏 `test_multi_turn.py` **33/33**（确认 API 接线未改变既有澄清语义）；
`test_frontend_hygiene.py` **8/8**（app.js 语法与静态资源版本一致性）。

**评测**（`reports/clarification_v1.json`，25 例全部在分母内）：

| 指标（分子/分母） | detector 臂（离线） | deployed 臂（离线规则） | live 臂（真实 /v1/chat） |
|---|---|---|---|
| 澄清成功率 | **18/18 = 1.0** | 4/18 = 0.2222 | 4/18 = 0.2222 |
| **不必要澄清率**（期望 answer 的 7 例） | **0/7 = 0.0** | **0/7 = 0.0** | **0/7 = 0.0** |
| 不必要澄清率（全部 25 例） | 0/25 = 0.0 | 0/25 = 0.0 | 0/25 = 0.0 |
| 平均澄清轮次（18 例 clarify 期望） | 19/18 = 1.0556（问过的：18/18 = 1.0556） | 4/18 = 0.2222（问过的：4/4 = 1.0） | 4/18 = 0.2222 |
| 补槽落到问句帧 | 14/14 = 1.0（4 例 N/A） | 4/4 = 1.0（14 例 N/A） | 不适用（线上无新键，见诚实边界） |
| 正向对照问题的帧可用性 | 7/7 = 1.0 | 7/7 = 1.0 | — |
| 系统实际反问过的用例 | 18/18 | 4/18 | 7/18（另一次运行 10/18） |

**live 臂状态分布**（`final_status_counts`）：`COMPLETED 15`、`FAILED_WITH_EVIDENCE 5`、
`ABSTAINED 2`、`null 3`（反问后无法用冻结话术接上，未进入最终状态）。
**上游错误单列**（不移出分母）：`MODEL_EMPTY_RESPONSE` 5 例
（`cl-metric-03`、`cl-grain-04`、`cl-version-01`、`cl-alias-01`、`cl-answer-04`）。
同一脚本的**第一次** live 运行：成功率 4/18、不必要澄清 0/7、反问 10/18、上游 `MODEL_TIMEOUT` 1 例
（`cl-answer-07`）——两次差异说明 live 臂受上游模型影响，非确定性结论见诚实边界。

**闭环示例（live，真实链路）**：`cl-time-01` 问「销售额最高的品类是哪个？」→ 真实 API 返回
`NEEDS_CLARIFICATION / AMBIGUOUS_QUERY` + 「请指定销售额的统计时间范围」→ 追加「2025年」重发 →
`COMPLETED`，1 轮补充完成。同型成功用例：`cl-metric-01/02/04`。

## 失败样本（全部保留）

1. **deployed / live 臂只覆盖 4 类中的 2 类**：`cl-grain-*`、`cl-version-*`、`cl-alias-*` 共 14 例
   在真实规则下**根本不反问**（直接回答或拒答），因此澄清成功率只有 4/18。这不是把失败移出分母，
   而是「规则式澄清覆盖面不足」的实测结论。
2. **live: 反问了但接不上**（`NO_FROZEN_REPLY_FOR_SLOTS`）：第二次运行 3 例
   （`cl-time-03`、`cl-grain-03`、`cl-alias-03`），第一次运行 6 例。原因是**运行中的 8000 进程早于
   本次改动**，响应里没有 `clarification_*` 键（实测 `clarification_contract_keys = []`），
   而模型生成的澄清话术又无法唯一映射到冻结槽位，评测无法给出补充话术。
3. **上游模型错误 5 例**（`MODEL_EMPTY_RESPONSE`）与第一次运行的 `MODEL_TIMEOUT` 1 例：单列，
   **不**移出分母。
4. **live 拒答 2 例**：`cl-version-02/03` 返回 `ABSTAINED / INSUFFICIENT_EVIDENCE`，
   即系统选择拒答而不是澄清（期望是澄清）。
5. **帧契约不可表示的 4 例**（`cl-version-01/02/03`、`cl-alias-03`）：文档版本与库存不在 SQL
   `query_frame` 契约里，如实记 N/A，**没有**拿原问题的帧冒充通过。

## 复现命令

```bash
# 1) 冻结集 + 离线评测（确定性 stub，不调模型）
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/eval_clarification.py --offline
#    → exit 0；detector 18/18、deployed 4/18、两臂不必要澄清 0/7、slot_landing 14/14 与 4/4

# 2) 现场评测（真实 /v1/chat；自动临时登录，跑完删除账号）
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/eval_clarification.py --offline --live --timeout 120
#    → exit 0；live 4/18、不必要澄清 0/7、反问了 7/18、上游错误 5 例（elapsed_s=196.2）

# 3) 隔离回归（23 用例；另跑回归护栏与前端卫生）
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_clarification_flow.py
#    → exit 0；{"tests": 23, "failures": 0, "errors": 0, "commit": "e4e2ebf", ...}
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_multi_turn.py
#    → exit 0；{"tests": 33, "failures": 0, "errors": 0, ...}
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_frontend_hygiene.py
#    → exit 0；{"tests": 8, "failures": 0, "errors": 0, ...}
```

## 诚实边界（不能据此声称什么）

- **不能说线上接口已经返回结构化缺项**：本会话禁止重启 8000 端口服务，运行中的进程仍是旧代码——
  实测 live 响应 `clarification_contract_keys = []`。新键只在 `fastapi.testclient` 的进程内测试中
  被验证（`ChatClarificationContractTest` 4 用例，含「一次补充后不再澄清」）。**前端一键补槽未在
  运行中的 GUI 上做到端到端演示**（旧进程不发候选值，点击面板只会显示退化的提示文案）。
- **不能说六类缺项已在线上生效**：检测器的 grain / version / alias 三类只存在于评测与测试路径；
  接入 `sql_engine.clarify()` 需要改 `datamind/sql_engine.py`、`datamind/agent.py`，
  这两个文件不在本 WP 允许改动集内，**未完成**。
- **不能说 live 指标稳定**：同一脚本两次 live 运行的反问数 10/18 vs 7/18、上游错误 1 vs 5，
  差异来自上游模型（含模型生成的澄清话术）。确定性结论只能引离线两臂。
- **不能说「平均澄清轮次」反映真实用户**：离线轮次来自冻结的补充话术（stub 一问一答），
  衡量的是契约能否收敛，不是人类交互行为。
- **不能说「不必要澄清率 0%」是广泛结论**：分母只有 7 条自建正向对照，样本小且非官方基准；
  它证明的是「没有出现一律反问的伪修」，不是普遍分布。
- **前端静态资源版本未提升**：`web/index.html` 的 `?v=0.41.0` 未改（该文件不在允许改动集内），
  回访浏览器可能仍用缓存里的旧 `app.js`，验收前需硬刷新或由文件负责人补一行版本号。
- 不应把本卡的 `clarification-v1` 自建冻结集分数外推为官方 E8/F2 得分。
- **共享产物副作用**：隔离回归按纪律只跑本 WP 的 pattern，但 `scripts/test_isolated.py` 会把
  `reports/latest_regression.json` 改写成本次运行的计数（本卡最后一次为 `{"tests": 8, ...}`，
  即前端卫生那一跑）。该文件是「最近一次已验证运行」的唯一来源，后续跑全量回归时会自然刷新；
  引用本卡数字时请以本卡引用的命令输出为准，不要用该文件反推本 WP 的用例数。

## 评分联动

- 官方 **E8 中级任务（B3 语义校验与澄清）**：从「会反问」升级为「有缺项字段、有候选值、有补槽后
  落到问句帧的证据、有不必要澄清率与失败清单」，并把「不必要澄清」登记为独立失败项。
- 官方 **F2 意图理解与澄清**：给出澄清成功率、不必要澄清率、平均轮次的分子分母与两次 live 现场记录；
  但线上 API 尚未暴露结构化槽位、前端一键补槽未现场演示，故**不能**据此主张 F2 满分或体验已完成。
- **E4 交互 Demo**：前端补槽交互代码与接线已就位并通过卫生测试，仍缺运行中 GUI 的端到端演示。
