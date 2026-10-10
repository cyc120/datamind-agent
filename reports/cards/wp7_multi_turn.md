# WP7 多轮上下文：路由前问题帧 + 多轮质量评测

| 字段 | 值 |
|---|---|
| **WP** | WP7「多轮上下文：路由前问题帧 + 多轮质量评测」（《提升计划》§WP7；官方 E10 多轮对话上下文保持 5 分、F2 意图理解与澄清 10 分） |
| **日期** | 2026-10-09 22:50（CST，`date "+%Y-%m-%d %H:%M %Z"` → `2026-10-09 22:47 CST`，卡片写作时间 22:50） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`；`reports/multi_turn_v1.json` 的 `commit` 同为 `e4e2ebf`） |
| **API 版本** | 0.39.0（`curl -s http://127.0.0.1:8000/openapi.json` 的 `info.version` = `0.39.0`） |
| **机读件** | [reports/multi_turn_v1.json](../multi_turn_v1.json)（sha256 `8c23dd83ee585246d1e90ac69cde2cd0f4aae933963e314b51f1c58c8802737b`；61 轮逐轮明细 + 61 条 live 记录 + 失败样本） |
| **新增文件** | [datamind/frame.py](../../datamind/frame.py)（626 行，sha256 `f51b53e5…`）、[data/evaluation/multi_turn_v1.json](../../data/evaluation/multi_turn_v1.json)（1392 行，sha256 `bc364e51…`）、[scripts/eval_multi_turn.py](../../scripts/eval_multi_turn.py)（373 行，sha256 `80071bf2…`）、[tests/test_frame_multi_turn.py](../../tests/test_frame_multi_turn.py)（569 行，sha256 `36f2c834…`） |
| **最小接线改动** | [datamind/agent.py](../../datamind/agent.py)（`resolve_turn` 前置于路由；范围闸门；`_FrameRetriever`）、[datamind/sql_engine.py](../../datamind/sql_engine.py)（澄清文案常量化 + `frame=` 可选入口 + 澄清抑制）。注：`agent.py` / `sql_engine.py` 是多工作包共享文件，`git diff --stat` 的 `+226/-14` 中含其他 WP 的并行改动；本 WP 只动了上述三处 + 两个导入。 |
| **状态** | **partial**：帧 + 路由/澄清/范围接线 + 冻结 10 段 ×61 轮评测（offline 61/61、live 58/61）已完成；**有意未做**：把帧注入模型提示词、服务端会话、前端会话（见「诚实边界」）。`docs/MULTI_TURN.md` 未更新（不在本轮允许改动的文件集内）。 |

---

## 问题

《提升计划》§WP7 与《审计》§7.1 原文：`datamind/agent.py` 仍按**当前** question 路由；`datamind/sql_engine.py` 在生成前按当前 question 澄清、召回；`datamind/rag_engine.py` 按当前 question 检索；history 主要在后面的生成提示词中使用。因此「那上一年的呢？」「第二个产品呢？」等依赖上轮实体/时间的请求，即使模型最后能看到历史，也可能已在前面的路由、澄清或检索阶段走偏。审计 §7.1 同时要求：范围继承必须显式且继续走权限检查，安全授权边界与语义上下文分开；并冻结 10 段 ×≥6 轮对话、同时报逐轮正确率与整段全对率。

本轮开工前的实测确认（读代码，非估算）：`agent.run` 的 `route = route_for(question, mode)` 只用当前问题；`ask()` 的 `clarify(question)`、`source_for(question, source_id)` 只用当前问题；`ask_documents()` 的 `retriever.retrieve(question, …)` 只用当前问题。即「缺槽→澄清」与「指代→继承」这两件事**在路由/澄清/检索阶段完全不可见**。

## 做法

### 1. `datamind/frame.py`：本轮问题帧（7 个契约字段 + provenance）

`ResolutionFrame`（frozen dataclass）字段严格为 `entities / metrics / time_range / sources / document_collections / overrides / inherited_from_turn` 七项，另带 `provenance`（字段 → 1-based 转录轮次索引，含 assistant 轮）；`inherited_from_turn = max(provenance.values())`，无继承必须为 `None`。

- `update_frame(prev_frame, question, history)`：从**封闭词表**（演示库地区/品类/产品/客户等级/工业零件号 + `business_metrics` 短别名 + 文档关键词）做最大匹配解析；历史用既有 `normalize_history` 校验（≤20 条 / 单条 ≤2000 / 合计 ≤8000，**超限拒绝**，复用同一份实现，边界不可能漂移）；历史逐轮重放（transitive），并把上一帧缺失的槽位回填——**仅在指代「无新主题」轮**（「那上一年呢」「第二个产品呢」「2025年」），指代且带新内容的轮（「那退款的呢」）只继承上下文槽（时间/数据源/集合），指标槽留空交给既有澄清，不猜。
- 继承必须可定位：`_locate` 优先返回**自己陈述了该值**的轮次；找不到来源轮次就不继承（fail closed，`prev_frame` 由调用方伪造也无效）。
- 序数指代（「第二个产品呢」）只从上一条**列出足够多产品**的 assistant 回答里取封闭词表内的产品名，来源轮次写进 `provenance`；「上一年」相对继承到的年份推导（不是相对今天），并记 `rule="上一年"` 覆盖项；`不是华东，是华南` 的否定实体被排除，改为记录 `overrides`。
- `frame_to_query(question, frame)`：无继承时 `text` 与 question **逐字节相同**；有继承时 `text` = question + 继承槽位的人类可读标签（**只**用于 RAG 检索查询与可解释展示）。
- `scope_check`：**只对继承来的** sources/集合提案做本请求允许集校验，越界返回稳定码 `SOURCE_NOT_ALLOWED` / `COLLECTION_NOT_ALLOWED`；本轮显式范围优先、不被当前问题里的关键词提示越过（当前问题的文档集合提示可在**允许范围内**收窄检索，收窄永不等于扩权）。
- `clarification_resolved_by_frame`：只允许「缺时间范围」这一条澄清被继承帧回答；**只读拒绝与「退款金额还是笔数」歧义永不被抑制**。
- `resolve_turn(question, history, …)`：把「帧 + 范围闸门 + 路由 + 澄清 + 源」合成一次纯函数决策（无数据库、无模型），Agent 调用的就是它，评测测的也是它——不是抄一份。

### 2. 最小接线（只改 agent.py / sql_engine.py）

- `agent.run`：先 `resolve_turn`（**路由前**），把帧写进 `runs.plan["resolution_frame"]`（可从 `/v1/runs/{id}` 回放）；`route_for(question, mode, frame)` 仅在「本轮无文档词但继承到文档线程」时改判 RAG，且**本轮自带指标/实体时不劫持**（换回数据的主题切换仍走 SQL）；继承来的数据源提案**参与既有 allow-list 检查**，越界即拒；RAG 只把帧的检索文本经 `_FrameRetriever` 代理传入（`rag_engine.py` 不在本轮允许改动的文件集内，故不修改它）；`ask(..., frame=...)` 只在帧**真的解析出来自历史的槽位**时才附加。
- `sql_engine.ask(..., frame=None)`：新增可选入口（默认 None → 既有调用与提示词一字不变）；用帧回答「缺时间范围」澄清、必要时取单一继承源；`query_frame` 里附 `resolution` 供展示。澄清文案提为常量（字符串未改），确保 frame 与引擎不会各写一份。
- **提示词不变**：帧绝不进入 system/user 提示词（`tests/test_multi_turn.py` 有历史提示词「恰好插入一个区块」的字节级断言），因此模型仍从不可信转录里理解指代——这正是 live 出现 3 次复问的原因（见失败样本）。

### 3. 冻结评测集与评测器

`data/evaluation/multi_turn_v1.json`：**10 段 × 61 轮**，覆盖 指代 / 省略 / 纠错 / 换主题 / 澄清补充 / 权限变化（各至少 1 段）+ 指标指代 / 时间链 / 文档指代 / 危险历史；每轮给出 `expected_frame`（写出的键才比对）与 `expected_action`，标注 `must_reject`（3 轮：2 轮范围、1 轮只读拒绝）。`scripts/eval_multi_turn.py --offline` 用 `resolve_turn` 判定，并**独立**核对继承可追溯性（逐字段检查被引用轮次文本是否真的含该值/该年，而不是重跑帧代码）；`--live` 逐轮跑真实 Agent + 真实模型，把模型自己的回答当作下一轮历史。

## 数据与集

| 来源 | 版本/规模 | 只读实测 |
|---|---|---|
| 冻结多轮集 | `data/evaluation/multi_turn_v1.json` | 10 段 / 61 轮；段类型 anaphora、ellipsis、correction、topic_switch、clarification_supplement、metric_anaphora、time_chain、document_reference、scope_change、hostile_history；`must_reject` 3 轮 |
| 帧词表 | 演示库封闭词表 | 地区 4（华东/华北/华南/华中）、品类 4、产品 20、客户等级 4、工业零件号 `[A-Z]\d{3}`、指标 8 个短别名（`business_metrics` 只读核对：11 行） |
| 既有契约 | `docs/MULTI_TURN.md`、`tests/test_multi_turn.py` | history ≤20/2000/8000 拒绝式；33 项测试（本轮全部通过） |
| 上游模型（仅 live） | `deepseek-flash`（`reports/multi_turn_v1.json` `live.model`） | 61 轮真实调用，53 COMPLETED / 6 NEEDS_CLARIFICATION / 2 FAILED_WITH_EVIDENCE（稳定码拒答） |

## 结果（分子分母）

**离线（offline，决策层：帧 + 范围闸门 + 路由 + 澄清；无数据库、无模型）**

| 指标 | 分子/分母 | 备注 |
|---|---|---|
| 逐轮正确率 | **61/61** | 写出期望的键全中 + 动作全中 + 拒绝/澄清码全中 |
| 整段全对率 | **10/10** | 10 段每段 6–7 轮全对 |
| 继承可追溯率 | **46/46** | 46 个「有继承」轮次，逐字段被引用轮次文本独立核对通过 |
| 权限变化轮拒绝率 | **2/2（100%）** | 继承越界数据源 → `SOURCE_NOT_ALLOWED`；继承越界文档集合 → `COLLECTION_NOT_ALLOWED` |
| 危险轮拒绝率 | **1/1** | 历史含 `DROP TABLE`/越权指令的轮次被只读拒绝（单列，未移出分母） |
| 有继承的轮次 | 46 / 61 | 无继承轮 `inherited_from_turn` 全为 `null` |

**在线（live，真实模型逐轮端到端；判定 = 决策层正确 且 状态与期望动作一致）**

| 指标 | 分子/分母 | 备注 |
|---|---|---|
| live 逐轮通过 | **58/61** | 53 COMPLETED、6 NEEDS_CLARIFICATION（其中 2 轮是期望的澄清）、2 稳定码拒答 |
| 未通过 | 3/61 | 全部在 mt-07（见失败样本）；**未做答案内容判定**（套件未给 gold `answer_checks`，不伪造） |
| `turns_used=0` 的轮次 | 17/61 | 6 轮澄清 + 2 轮拒答 + 9 轮段首（无历史，如实为 0） |

**测试**

| 命令 | 结果 |
|---|---|
| `scripts/test_isolated.py --pattern test_frame_multi_turn.py` | exit 0；`Ran 30 tests … OK`；`{"tests": 30, "failures": 0, "errors": 0, "commit": "e4e2ebf"}` |
| `scripts/test_isolated.py --pattern test_multi_turn.py` | exit 0；`Ran 33 tests … OK`；`{"tests": 33, "failures": 0, "errors": 0, "commit": "e4e2ebf", "ran_at": "2026-10-09T14:46:59Z"}`（既有 33 项**继续通过**） |
| `scripts/eval_multi_turn.py --offline` | exit 0；`{"turn_accuracy": {"numerator": 61, "denominator": 61}, "segment_accuracy": {"numerator": 10, "denominator": 10}, "inheritance_traceability": {"numerator": 46, "denominator": 46}, "scope_change_rejection": {"numerator": 2, "denominator": 2}, "dangerous_turn_refusal": {"numerator": 1, "denominator": 1}}` |
| `scripts/eval_multi_turn.py --live` | exit 0；`live_turn_accuracy {"numerator": 58, "denominator": 61}` |

**评测轮次与期望值修正（如实记录）**：第 1 轮 offline **32/61**（29 处失败 = 1 处我自己选了不触发歧义澄清的问法 + 18 处 `inherited_from_turn` 期望值手算错（漏把 assistant 轮计入 1-based 索引）+ 10 处真实缺陷/检查口径）；修真实缺陷（序数指代取到错的那条回答、否定式纠错未排除被否定实体、RAG 集合未按帧收窄、回填越过实质轮次、链式「上一年」来源不够精确）后第 2 轮 **41/61**；18 处期望值按已文档化规则（`inherited_from_turn = max(provenance)`）修正，并由**独立**可追溯性检查（46/46）复核每个被引用的轮次文本确实携带该值，第 3 轮 **61/61**。

## 失败样本

1. **live mt-07 第 4/5/6 轮（真实模型复问，3/61）**：第 1 轮「2023年各地区的订单数是多少？」→ COMPLETED；第 2/3 轮「2024年呢？」「2025年呢？」→ COMPLETED；第 4 轮「那上一年呢？」→ `NEEDS_CLARIFICATION` / `AMBIGUOUS_QUERY`，模型原文：「「上一年」具体指哪一年？请确认：是 2024 年（2025 的上一年），还是其他年份（如 2022 年）？…」；第 5 轮同问同答；第 6 轮「华南地区呢？」→ 模型问「请确认「华南地区」订单数要统计哪一年？」。**原因如实说明**：帧在本轮解析出了 2024/2023 与继承来源（offline 判定为对），但帧**不进入提示词**（为保住「不传历史逐字节不变」与既有 33 项提示词断言），模型仍须自行从历史转录消解指代，于是出现复问。对照：同一模型在 mt-01 第 4 轮「那上一年呢？」能直接回答（COMPLETED，`turns_used=6`）——同一句式在不同线程上表现不稳定。
2. **offline 第 1 轮 29 处失败中的 10 处真实缺陷**（已修，保留作为回归证据）：序数指代解析到「最近一条只列了 1 个产品的回答」而非「列出三名的回答」（mt-05 第 4–6 轮）；「不是华东，是华南」把被否定的华东也算进 `entities`（mt-03 第 2 轮）；RAG 未按帧收窄到 `demo_kb`（mt-04 与 mt-08 共 6 轮）；回填越过实质轮次导致主题切换被拖回旧实体（mt-04 第 5 轮）；链式「上一年」的来源轮次不够精确（mt-07 第 5/6 轮可追溯性）。
3. **外部阻断（不是本 WP 的代码）**：2026-10-09 22:41（CST）隔离回归在 `db/seed_industrial.py → db/migrate.py` 处失败，`psycopg2.errors.InsufficientPrivilege: must be owner of table evidence_nodes`——另一工作包当时正在写入 `db/migrations/019_evidence_graph.sql`（22:41 落盘）。同一命令 22:42 重试即通过（日志出现 `applied 019_evidence_graph.sql`）。**我未修改该文件**，未据此改动任何代码。
4. **危险历史的重放边界**：mt-10 第 2 轮（含 `DROP TABLE orders` 与越权指令）帧为空、被只读拒绝；第 3 轮「那2025年呢？」会**跳过这个被拒绝的轮次**回到第 1 轮继承（`inherited_from_turn=1`），而不是让危险轮次在语义链上留痕。这是刻意的：只跳过**帧为空的实质轮**，遇到任何有槽位的实质轮即停止回填。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"
git rev-parse --short HEAD                       # -> e4e2ebf            (只读)
curl -s http://127.0.0.1:8000/openapi.json | .venv/bin/python -c "import json,sys;print(json.load(sys.stdin)['info']['version'])"   # -> 0.39.0
.venv/bin/python scripts/test_isolated.py --pattern test_frame_multi_turn.py   # 30 tests OK   (exit 0)
.venv/bin/python scripts/test_isolated.py --pattern test_multi_turn.py         # 33 tests OK   (exit 0)
.venv/bin/python scripts/eval_multi_turn.py --offline                          # 61/61,10/10,46/46,2/2,1/1 (exit 0)
.venv/bin/python scripts/eval_multi_turn.py --live                             # + live 58/61 (真实模型, exit 0)
shasum -a 256 reports/multi_turn_v1.json datamind/frame.py data/evaluation/multi_turn_v1.json scripts/eval_multi_turn.py tests/test_frame_multi_turn.py
```

说明：交付的 `reports/multi_turn_v1.json` 由最后一次 `--live` 运行生成，文件内**同时**含完全相同的 offline 段落（`metrics` / `turns` / `failures`）与 `live` 段落；只要离线口径时重跑 `--offline`（`metrics` 段逐字节一致）。

## 诚实边界（不能据此声称什么）

1. **不是官方评测集**：10 段 61 轮是自建冻结对话，不代表官方 E10/F2 得分；分值与名次一律不自评。
2. **offline 只测决策层**：61/61 说明「帧解析 + 范围闸门 + 路由 + 澄清抑制」在冻结集上全对，**不说明答案正确**；答案侧只有 live 的 58/61，且 live 判定 = 决策正确 + 状态符合期望动作，**未做答案内容判定**（套件未提供 gold `answer_checks`，不编造）。
3. **live 的 3 处失败是真的**：真实模型在 mt-07 连续复问「上一年指哪一年」。因此本轮**不能**声称「连续 5 轮以上准确」；只能说决策层已能把 6 轮线程每轮解析正确、且真实链路能跑通 61 轮。
4. **帧不进提示词**：为保住「不传 history 时提示词与结果逐字节不变」与既有 33 项提示词断言，帧只影响路由/澄清/源/检索文本。模型复问的根因就在这里。把帧注入提示词需要同时更新 `tests/test_multi_turn.py` 的字节级断言与 `docs/MULTI_TURN.md`，两者都不在本轮允许改动的文件集内。
5. **封闭词表，不是通用 NER**：地区/品类/产品/指标/零件号/文档关键词之外的措辞解析为空（帧留空、交给既有澄清）；无实体链接模型、无同义泛化、无跨语言。
6. **无服务端会话**：历史仍**完全由客户端每次自带**；没有 session、没有落库、没有跨请求记忆；`prev_frame` 参数是给未来会话用的，且**来源不可定位即不继承**（fail closed）。
7. **范围继承是「拒绝而不是替换」**：继承到越界数据源/集合一律稳定码拒答（2/2），不会静默换成允许的源；反过来，**当前问题显式指向范围外数据源**时仍沿用既有 `source_for` 语义（请求范围优先），这是改动前就有的行为，本轮未改——`tests/test_frame_multi_turn.py::test_an_explicit_out_of_scope_source_question_keeps_the_existing_behaviour` 明确固定了这一点。
8. **未接线部分**：`rag_engine.py` 未改（不在允许文件集内），RAG 检索查询的改写通过 agent.py 里的只读代理完成；`web/app.js` 未改（别的 WP 正在修改它，且前端会话属于计划 §WP7 的体验部分）→ **前端多轮会话、当前范围可视、一键重开、澄清补充内联均未实现**。
9. **`docs/MULTI_TURN.md` 未更新**：其文件头状态行仍写「未做过真实多轮质量评测」，§8 仍写「**不做质量评测**」「**前端未接入**」。本轮之后前半句已过期（已做决策层 61 轮 + live 61 轮），但该文件不在我允许改动的文件集内——需要文档负责人据此卡片更新，否则 E11 会留下「文档与证据不一致」的隐患。
10. **不构成性能/成本结论**：live 61 轮只记 `turns_used`（0/4/6/8/10…），未记 token、首字、端到端时延与成本。
11. **评测轮次已如实披露**：首轮 32/61 的部分失败源于我把期望值算错（已按文档化规则修正并由独立检查复核），不是「一开始就全对」。

## 评分联动

| 官方项 | 本轮材料能支撑什么 | 仍缺什么 |
|---|---|---|
| **E10 多轮上下文保持（5）**「具备多轮能力 3 分，连续 5 轮以上准确 5 分」 | 契约与边界（既有 33 项）+ **路由前帧**（46/46 可追溯继承）+ 冻结 10 段 ×61 轮双指标 + 真实模型 61 轮记录（`run_id` 61 个在 `reports/multi_turn_v1.json`）；权限变化 100% 拒绝、危险历史不影响 SQL/参数 | live 有 3 轮真实复问 → 「连续 5 轮以上准确」**尚不能声称**；把帧注入提示词后重跑 live 才可能补上 |
| **F2 意图理解与澄清（10）**「主动澄清 7 分，精准且体验好 10 分」 | 跨轮补槽：mt-05 第 2 轮「2025年」实测抑制了缺时间澄清（决策层），同时**退款金额/笔数歧义与只读拒绝永不被抑制**（有测试固定）；`resolve_turn` 让澄清决策可在无模型下复现 | 「不必要澄清率」未统计（需要真实模型多次采样）；前端澄清补充内联未做 |
| **E4 交互 Demo（5）** | 帧写进 `runs.plan["resolution_frame"]`，`/v1/runs/{id}` 可回放本轮解析 | 前端未接入多轮 → 体验侧本轮**没有增加** |
| **F4/F8 相关** | 帧把「继承的数据源/集合」接入既有 allow-list（安全边界不因多轮放宽），可写进技术说明书的「安全授权边界与语义上下文分离」一节 | 需文档侧落笔 |

**结论**：本轮把 E10 从「只有契约与边界、无质量评测」推进到「有路由前帧、有冻结双指标、有真实模型 61 轮记录」，但**不宣称兑现 5 分**——live 的 3 轮复问是真实缺口，只有把帧正式注入提示词并重跑 live 才能主张「连续 5 轮以上准确」。
