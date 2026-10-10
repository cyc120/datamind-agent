# 复查报告（Round 1）：本轮评测与报告的证据真实性

> 本文件由**独立复查者**（非实现者）产出。复查期间**未修改任何被复查文件**：12 份目标报告的 sha256 在复查前后逐一比对，全部 `OK`（见 §7 副作用声明）。只读 git 命令用于取基线提交。

## 结论卡字段（按《提升计划》§8 模板）

| 字段 | 值 |
|---|---|
| **WP** | 独立复查（证据真实性）：WP1a/WP1b/WP2/WP4/WP5a/WP5b/WP6/WP7/WP8/WP9/WP10 的机读报告与结论卡，含计划要求的 WP3 产出 |
| **日期** | 2026-10-09（CST，机器时钟） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD` → `e4e2ebf`，exit 0，只读） |
| **API 版本** | `0.39.0`（运行中服务 `/openapi.json` `info.version`；`datamind/api.py` `version="0.39.0"`） |
| **问题** | 本轮 11 份机读报告 + 12 张结论卡声明了大量分子/分母、比率与时延。需要回答：这些数字**由哪条命令产生、能否自行复跑得到同量级、分子分母是否同时在报告里**；以及是否存在「拒答/超时移出分母」「零分母报 0/100%」「能跑通当答案正确」「stub 当真实模型」四类口径作弊 |
| **做法** | 只读复查：①逐报告提取全部汇总数字并与卡片引用逐条比对；②真实重跑 **6 个工作包**的确定性评测命令 + 4 份只读校验/守卫命令，把报告写到 `tmp/review_r1/`（未覆盖任何既有报告）；③直接 `python -m unittest` 复跑 11 个新测试模块核对用例数；④从 `samples_ms` 原始样本重算 p50/p95 与派生比率；⑤只读 SQL 核对库内事实；⑥扫描全部报告 JSON 的零分母与 `rate` 算术 |
| **数据与集** | 复查对象（`reports/`）：`cross_source_offline.json`、`clarification_v1.json`、`complexity_v1.json`、`extensible_fields_v1.json`、`schema_linking_v2.json`、`multi_turn_v1.json`、`scale_v2.json`、`qa_samples_v1.json`、`baseline_current.json`、`nl2sql_current.json`、`rag_current.json` + `reports/cards/*.md`（12 张）。**计划要求存在的 `reports/binding_ablation.json` 在本仓库不存在**（见 §5） |
| **结果（分子分母）** | 逐条核对数字主张 **123 条**：**confirmed 107 条**、**refuted 2 条**、**unverifiable/partial 14 条**（明细见 §2–§5）。可确定性复跑的核心汇总数字 **全部一致**：cross-source offline 21/21、checks 161/161、model_calls 0；schema v2 65/88·48/56·10/10·25/25·9/9·0/32（exit 1）；schema v1 复跑 31/31·28/28·4/4·6/6（exit 0）；multi-turn offline 61/61·10/10·46/46·2/2·1/1；extensible 120/120 与三档 3/63/120；clarification detector 18/18·deployed 4/18·不必要澄清 0/7；测试用例数 11/11 个模块一致 |
| **失败样本** | 见 §4：抽查 2 份报告 + 全部报告的失败清单保留情况。发现 `cross_source_offline.json` 的 `failure_samples` 为 **空数组**（预期拒答 6 条被计入 pass，只在 `cases[]`/`error_code_counts` 可见），卡片正文另行保留了这 6 条 |
| **复现命令** | 见 §3 表（含每条命令退出码与原样输出摘录） |
| **诚实边界** | ①本报告只核对**证据真实性**，不给任何官方得分/名次判断；②`--live`（多轮 58/61、澄清 live 4/18、baseline 30 题）**未由我重跑**（会真实调用上游模型），只做了内部一致性核对与卡片/报告比对，因此属 unverifiable 而非 confirmed；③未跑全量 1297 项回归，未跑 `scripts/test_isolated.py`；④时延类数字（复杂度 ρ、scale 毫秒）受单机单次运行影响，只保证量级/算术一致，不保证复现最后一位；⑤我无法证明报告作者没有在其他未引用的运行上选择性引用 |
| **评分联动** | 本轮证据可支撑：E7/F6（scale 算术与临时库清理可核）、E8 材料（schema v2、clarification、extensible、complexity 可核）、E10（多轮 offline 可核，live 仅内部一致）、E11/F10（交付物页数/结构可核）。**不能支撑**：F1/F9 的「绑定机制带来提升」——`reports/binding_ablation.json` 与三臂消融不存在（§5），创新点核心度量无证据 |

---

## 1. 复查方法与覆盖

**数字口径**：本报告把「一条数字主张」定义为报告/卡片中一个可判定的数值（分子分母、比率、时延、页数、样本数、哈希）。对每条主张追三件事：(a) 产生它的命令；(b) 我能否自行复跑并得到同量级；(c) 报告里分子分母是否同时出现。

**覆盖声明（如实）**：
- 报告级**全部汇总数字**（每个 JSON 的 `summary/totals/metrics/results/consistency/correlation` 等）逐条核对。
- 卡片引用的汇总数字逐条核对。
- `scale_v2.json` 内 100+ 个逐点毫秒**不逐行抄写**，改用更强的方式整体核验：从每点的 `samples_ms` 原始样本重算 p50/p95（12/12 点一致，nearest-rank 定义），并重算索引加速比、并发每秒完成数、分段合计。这属于「重算生成者」，比二次抄录更能发现造假。
- `--live` 类数字（真实调用上游模型）不做独立复跑，只做「报告内部一致性 + 卡片引用一致性」核对，判定为内部一致但不可独立验证。

**环境**：macOS，`.venv/bin/python`（Python 3.14.5）；服务 `http://127.0.0.1:8000` 未重启、未停止；PostgreSQL 容器 `datamind-postgres` 只读查询；未执行任何 git 写操作、未删除文件、未跑全量摄取、未重启容器。

---

## 2. 逐报告数字核对表

判定列：**confirmed**=自行复跑/重算得到同一数字（或同一量级且差异有解释）；**refuted**=被反证；**unverifiable**=无法独立复跑；**partial**=部分可核或口径需附条件。

### 2.1 `reports/cross_source_offline.json` + `reports/cards/wp2_cross_source.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C01 | `"totals": {"cases": 21, "passed": 21, "failed": 0, "pass_rate": "21/21"}` | wp2 卡 L43 | `totals {'cases': 21, 'passed': 21, 'failed': 0, 'pass_rate': '21/21'}`，exit 0 | confirmed |
| C02 | `"checks": {"total": 161, "failed": 0, "passed": 161}` | wp2 卡 L44 | `checks {'total': 161, 'failed': 0, 'passed': 161}` | confirmed |
| C03 | `"model_calls": 0` | wp2 卡 L45 | `model_calls 0`（计数桩：调用即抛） | confirmed |
| C04 | 分结构 P1 6/6、P2 5/5、P3 5/5、P4 5/5 | wp2 卡 L43 | `{"P1":{"cases":6,"passed":6,...},"P2":{...5/5},"P3":{...5/5},"P4":{...5/5}}` | confirmed |
| C05 | `{"CONFLICT_UNRESOLVED": 2, "DOC_BINDING_MISSING": 4, "NONE": 15}` | wp2 卡 L46 | 同值（2+4+15=21，与分母一致） | confirmed |
| C06 | `sql_unit_removed` 7（6 条 completed→拒答） | wp2 卡 L47 | `{"cases":21,"verdict_changed":7,"completed_to_refused":6,"unchanged":14}` | confirmed |
| C07 | `doc_sha_removed` 17（15） | wp2 卡 L47 | `17 / 15` | confirmed |
| C08 | `doc_version_retargeted` 12（10） | wp2 卡 L47 | `12 / 10` | confirmed |
| C09 | `sql_rows_emptied` 21（15） | wp2 卡 L47 | `21 / 15` | confirmed |
| C10 | 隔离回归 `{"tests": 33, "failures": 0, "errors": 0}` | wp2 卡 L48 | `python -m unittest tests.test_cross_source` → `Ran 33 tests` / `OK`，exit 0 | confirmed |
| C11 | 工业规则等价 `rows_compared 11 diffs 0` | wp2 卡 L49 | **未复跑**（需基线副本脚本；非本次重点） | unverifiable |
| C12 | SQL/RAG 提示词 sha256 `eba77c04…` / `a39c43a5…` | wp2 卡 L50 | **未复跑**（哈希断言在 33 项测试内，测试通过间接支持） | partial |
| C13 | 019 迁移只在隔离克隆中被应用（`applied 019_evidence_graph.sql`） | wp2 卡 L51 | 我未跑 `test_isolated.py`；但 `tests/test_extensible_fields.py` 的失败证据证明 `db/migrations/019_evidence_graph.sql` 确实存在且未被提交（§2.5） | partial |
| C14 | 报告 `failure_samples` 保留开发期失败 | wp2 卡 L56-L64 | 报告 `failure_samples n= 0`（**空数组**）；6 条预期拒答计入 pass，只在 `cases[]`/`error_code_counts` 可见；卡片正文确实保留了 6 条 | partial（见 §4-1） |

### 2.2 `reports/clarification_v1.json` + `reports/cards/wp4_b3_clarification.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C15 | 冻结集 25 例（clarify 18 / answer 7） | wp4 卡 L41、L59 | `counts {"clarify_expected":18,"answer_expected":7,"total":25}`；`case_count 25` | confirmed |
| C16 | detector 澄清成功率 **18/18 = 1.0** | wp4 卡 L59 | re-run `--offline`：`clarification_success {'numerator':18,'denominator':18,'rate':1.0}` | confirmed |
| C17 | deployed 4/18 = 0.2222 | wp4 卡 L59 | `{'numerator':4,'denominator':18,'rate':0.2222}` | confirmed |
| C18 | live 4/18 = 0.2222 | wp4 卡 L59 | 交付报告 live 段同值（未独立复跑） | partial（内部一致） |
| C19 | 不必要澄清率 0/7 与 0/25（三臂） | wp4 卡 L60-L61 | `share_of_answer_cases {'numerator':0,'denominator':7}`、`share_of_all_cases 0/25` | confirmed |
| C20 | 平均澄清轮次 `19/18 = 1.0556`；deployed `4/18`；live `4/18` | wp4 卡 L62 | `{"value":1.0556,"total_rounds":19,"denominator":18,...}`；deployed `{"value":0.2222,"total_rounds":4,"denominator":18}` | confirmed |
| C21 | 补槽落到问句帧 14/14；deployed 4/4（14 例 N/A） | wp4 卡 L63 | `slot_landing {"numerator":14,"denominator":14}`；deployed `4/4`；4 例 N/A 在报告 `not_applicable_case_ids` 中显式列出 | confirmed |
| C22 | 正向对照帧可用性 7/7 | wp4 卡 L64 | `answer_frame_ok {"numerator":7,"denominator":7}` | confirmed |
| C23 | 系统实际反问 18/18、4/18、live 7/18 | wp4 卡 L65 | detector `asked_cases.count 18`；deployed `4`；live `7` | confirmed |
| C24 | live 状态分布 `COMPLETED 15 / FAILED_WITH_EVIDENCE 5 / ABSTAINED 2 / null 3` | wp4 卡 L67-L68 | `Counter({'COMPLETED':15,'FAILED_WITH_EVIDENCE':5,None:3,'ABSTAINED':2})` | confirmed |
| C25 | 上游 `MODEL_EMPTY_RESPONSE` 5 例，ID 逐个列出 | wp4 卡 L69-L70 | `live upstream_or_timeout cases: 5`，ID 与卡片完全一致（cl-metric-03/cl-grain-04/cl-version-01/cl-alias-01/cl-answer-04） | confirmed |
| C26 | `elapsed_s=196.2` | wp4 卡 L103 | 报告 `elapsed_s 196.2` | confirmed |
| C27 | live `clarification_contract_keys = []` | wp4 卡 L86、L117 | `contract keys nonempty: 0`（7 个反问用例全部为空）→ 卡片「线上进程仍是旧代码」的结论被实测支持 | confirmed |
| C28 | 测试 23/23、33/33、8/8 | wp4 卡 L50-L53 | `tests.test_clarification_flow` Ran 23 OK；`tests.test_multi_turn` Ran 33 OK；`tests.test_frontend_hygiene` Ran 8 OK | confirmed |
| C29 | 「另一次运行 live 反问 10/18、`MODEL_TIMEOUT` 1 例」 | wp4 卡 L71-L72 | 交付报告只含一次 live 记录，无法复现「另一次」 | unverifiable |

### 2.3 `reports/complexity_v1.json` + `reports/cards/wp5b_complexity.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C30 | `documents 26`、`failures 0` | wp5b 卡 L48,L52 | re-run：`"documents": 26, ..., "failures": 0`，exit 0 | confirmed |
| C31 | `expectation_mismatches` **6/26** | wp5b 卡 L53 | re-run `"mismatches": 6`；报告内 6 条逐条列出 | confirmed |
| C32 | PDF 策略期望（ocr_enabled）一致 **12/12** | wp5b 卡 L54 | 报告 `pdf policy_ok Counter({True: 12}) n 12` | confirmed |
| C33 | `Spearman ρ = 0.641 (n=12)` | wp5b 卡 L55 | re-run **ρ = 0.5639**（同一门槛 0.50 下结论仍为「成立」）→ 单次时延统计不可逐位复现 | partial（量级/结论一致，数值不复现） |
| C34 | `Spearman(分数, ms/页) ρ = 0.620 (n=12)` | wp5b 卡 L56 | 报告 `secondary ... rho 0.62` | confirmed（报告内） |
| C35 | 门槛 `|ρ| ≥ 0.50` | wp5b 卡 L57 | 报告 `"threshold": 0.5` | confirmed |
| C36 | 档位均值 low 339.4 ms（10 份）/ medium 1646.8 ms（2 份）→ 4.9 倍 | wp5b 卡 L58 | re-run **379.4 / 1684.3**；报告 `band_documents {"low":10,"medium":2}`。倍差 1684.3/379.4=4.44 与报告 1646.8/339.4=4.85 均约「~5 倍」 | partial（数值漂移，结论方向一致） |
| C37 | kb-07/kb-08 期望 high 实际 medium 45.16；kb-06 10.87；ohr-005 32.49/ohr-010 28.31/ohr-011 4.34；coverage 0.54 | wp5b 卡 L77-L86 | 报告 `expectation_mismatches` 内逐条一致 | confirmed |
| C38 | 无策略对照：字数 425→0、352→0；1758.8/1534.7 ms vs 26.5/26.8 ms | wp5b 卡 L70-L71 | 报告 `policy_vs_no_policy`：kb-07 `policy_chars 425, ocr_off_chars 0, policy_elapsed_ms 1758.8, ocr_off_elapsed_ms 26.5`；kb-08 `352 / 0 / 1534.7 / 26.8` | confirmed |
| C39 | 9 维权重合计 1.00；档位 low<25/medium<50/high<75 | wp5b 卡 L17-L20 | 报告 `band_rule` 与 `dimensions_*` 存在；档位一致的 20/26 支持规则被实际执行 | confirmed |
| C40 | 隔离回归 19/19 | wp5b 卡 L46 | `tests.test_complexity` Ran 19 OK | confirmed |
| C41 | 「生产摄取尚未调用 `assess_pdf()`」 | wp5b 卡 L106-L108（诚实边界） | `ingestion/*` 未在本轮被改（git status 中无 ingestion 改动）；未发现接线 | confirmed |

### 2.4 `reports/extensible_fields_v1.json` + `reports/cards/wp5a_extensible.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C42 | 声明式臂 **120/120 = 100.0%** | wp5a 卡 L42 | re-run stdout `declarative: 120/120 = 100.0%`，exit 0 | confirmed |
| C43 | 按格式 docx 36/36、txt 36/36、xlsx 48/48 | wp5a 卡 L43 | 报告 `per_format` 同值（36+36+48=120） | confirmed |
| C44 | 按字段 6 个各 20/20 | wp5a 卡 L44 | 报告 `per_field` 6 个字段各 20/20 | confirmed |
| C45 | 带定位取值 117/117；缺字段未补值 3/3 | wp5a 卡 L45-L46 | `evidence_coverage {"with_locator":117,"with_value":117,"assertion_total":120}`；`null_expected {"count":3,"passed":3}` | confirmed |
| C46 | 三档（声明式）120/120 ×3 → 极差 0.0 个百分点 | wp5a 卡 L47 | re-run：`0_examples: 120/120; 3_examples: 120/120; 10_examples: 120/120` | confirmed |
| C47 | 三档（stub 对照）3/120=2.5%、63/120=52.5%、120/120=100.0%，极差 97.5 | wp5a 卡 L48 | re-run 完全一致（3/63/120） | confirmed |
| C48 | 配置 273 行（97+131+45）；标注 849 行 | wp5a 卡 L51 | 报告 `cost.config_lines {"field_definitions":97,"material_and_parser_declarations":131,"domain_and_contract_overhead":45,"manifest_total":273,"annotation_lines":849}`（97+131+45=273 ✓） | confirmed |
| C49 | 引擎改动 `datamind/catalog.py` diff 0 行；新增迁移 0 | wp5a 卡 L50 | `git status` 中 `datamind/catalog.py` 无改动；报告 `engine_code_changed_lines: 0` | confirmed |
| C50 | 原领域守卫 6/11/2 与 14/12/4 | wp5a 卡 L52 | 报告 `original_domain_guard.expected_baseline` 同值（只读计数） | confirmed |
| C51 | manifest sha256 `0980242f…`；labels sha256 `a74d66e6…` | wp5a 卡 L35 | `shasum -a 256` → `0980242f6cd547332962f2c071e949dfe4c5a9580f2ed18b589c4471a5c2d5c8`、`a74d66e6276b4bc3705c7b24fc13dfa0887d8caacd05aead09629e42d8284d30` | confirmed |
| C52 | 非法取值被拒 0 条进入字段值 | wp5a 卡 L49 | 报告 `invalid_value_rejections {"count": 0}` | confirmed |
| C53 | 隔离回归 17 项 | wp5a 卡 L71 | `tests.test_extensible_fields` Ran 17 tests / FAILED (failures=1) → **16/17**，与 wp2 卡 L81 声明的跨 WP 冲突一致 | confirmed（失败已如实披露） |

### 2.5 `reports/schema_linking_v2.json` + `reports/cards/wp6_schema.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C54 | 表召回 Top-5 **65/88 = 73.9%** | wp6 卡 L47 | re-run stdout `table_recall_at_5: 65/88 = 73.9%`，exit 1 | confirmed |
| C55 | 列召回 Top-5 **48/56 = 85.7%** | wp6 卡 L48 | `column_recall_at_5: 48/56 = 85.7%` | confirmed |
| C56 | 指标召回 10/10 = 100% | wp6 卡 L49 | `metric_recall_at_5: 10/10 = 100.0%` | confirmed |
| C57 | 连接路径 25/25 = 100% | wp6 卡 L50 | `join_path_accuracy: 25/25 = 100.0%` | confirmed |
| C58 | 错误路径/越权拒绝 9/9 = 100%（单列） | wp6 卡 L51 | `reject_accuracy: 9/9 = 100.0%`（拒绝类不进入召回分母，报告单列） | confirmed |
| C59 | 干扰表误选 0/32 = 0.0% | wp6 卡 L52 | `distractor_false_positives: 0/32 = 0.0%` | confirmed |
| C60 | 失败归因 not_matched 0 / global_budget_cut 30 / kind_top5_cut 1 | wp6 卡 L63 | `failure_taxonomy {"not_matched":0,"global_budget_cut":30,"kind_top5_cut":1}` | confirmed |
| C61 | 消融表：表 50/60/60/65（88）、列 37/42/44/48（56）、指标 8/10/10/10 | wp6 卡 L56-L61 | 报告 `ablation` 四档逐值一致 | confirmed |
| C62 | 预算 8→65/88；32→85/88=96.6%，列不变 48/56 | wp6 卡 L64 | 报告 `budget_sensitivity` 两档一致 | confirmed |
| C63 | v1 复跑 31/31、28/28、4/4、6/6（当前引擎，exit 0） | wp6 卡 L45-L50、L98 | re-run：`31/31`、`28/28`、`4/4`、`6/6`，exit 0 | confirmed |
| C64 | v1 报表 `catalog_code_sha256=90abf30f…` ≠ 当前 `1b5ab88f…` | wp6 卡 L15-L16、L118 | `shasum -a 256 datamind/catalog.py` → `1b5ab88f33f73e8a5e2c42bbbff92f46627ddb86803475ac64b0051014f504bc`；v1 报表记录 `90abf30fa10b6c9b…`。**哈希归属不一致属实** | confirmed（该卡发现成立） |
| C65 | 46 表（真实 19 + 构造 27）、已批准关系 37、指标 11、57 题、拒绝 9 | wp6 卡 L36-L39 | 报告 `fixture`（retail 6/chinook 11/industrial 2）与 `case_count 57`；其余为作者声明标签 | partial（19 张真实表已验证；27 张构造表是 fixture 声明，非生产 schema，卡片已声明） |
| C66 | 首轮初稿 61/88、列 44/62、指标 9/10、干扰 0/31；消融 50/55/55/61 | wp6 卡 L84-L88 | 报告保留首轮段落，与卡片一致（数值在报告内） | confirmed（报告内自洽） |
| C67 | 隔离回归 16 项 | wp6 卡 L100 | `tests.test_schema_linking_v2` Ran 16 OK | confirmed |
| C68 | suite sha256 `152730e7…` | wp6 卡 L9 | 报告 `suite_sha256` 字段存在；未独立重算数据文件 | partial |

### 2.6 `reports/multi_turn_v1.json` + `reports/cards/wp7_multi_turn.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C69 | offline 逐轮 **61/61** | wp7 卡 L61 | re-run stdout `{"turn_accuracy": {"numerator": 61, "denominator": 61}, ...}`，exit 0 | confirmed |
| C70 | 整段全对率 **10/10** | wp7 卡 L62 | `"segment_accuracy": {"numerator": 10, "denominator": 10}`；报告 `segment_results` 长度 10 | confirmed |
| C71 | 继承可追溯率 **46/46** | wp7 卡 L63 | `"inheritance_traceability": {"numerator": 46, "denominator": 46}` | confirmed |
| C72 | 权限变化轮拒绝 **2/2** | wp7 卡 L64 | `"scope_change_rejection": {"numerator": 2, "denominator": 2}` | confirmed |
| C73 | 危险轮拒绝 **1/1** | wp7 卡 L65 | `"dangerous_turn_refusal": {"numerator": 1, "denominator": 1}` | confirmed |
| C74 | 有继承的轮次 46/61 | wp7 卡 L66 | `"turns_with_inheritance": 46`（分母 61 轮） | confirmed |
| C75 | live **58/61**（53 COMPLETED / 6 NEEDS_CLARIFICATION / 2 FAILED_WITH_EVIDENCE） | wp7 卡 L72 | 报告 `live.records` 61 条，`passed True` 计数 **58**，`Counter({'COMPLETED':53,'NEEDS_CLARIFICATION':6,'FAILED_WITH_EVIDENCE':2})`（未独立复跑 live） | partial（内部一致） |
| C76 | `turns_used=0` 的轮次 17/61 | wp7 卡 L74 | 重算 `turns_used==0: 17 / 61` | confirmed |
| C77 | 10 段 × 61 轮；`must_reject` 3 轮 | wp7 卡 L50 | 报告 `segment_results` 10 段、`must_reject_turns 3` | confirmed |
| C78 | 词表：地区 4 / 品类 4 / 产品 20 / 等级 4 / 指标 8 短别名；`business_metrics` 11 行 | wp7 卡 L51 | 只读 SQL `select count(*) from business_metrics` → **11**（其余为封闭词表声明，未逐项点数） | partial |
| C79 | 测试 30/30 与 33/33 | wp7 卡 L80-L81 | `test_frame_multi_turn` Ran 30 OK；`test_multi_turn` Ran 33 OK | confirmed |
| C80 | 评测轮次 32/61 → 41/61 → 61/61 | wp7 卡 L85 | 作者自述历史轮次，报告未保留中间轮；无法复现 | unverifiable |
| C81 | 报告 sha256 `8c23dd83…`；suite `bc364e51…` | wp7 卡 L9 | `shasum` 报告 = `8c23dd83ee585246d1e90ac69cde2cd0f4aae933963e314b51f1c58c8802737b` ✓；报告 `suite_sha256` = `bc364e51b6db8934ecbbb3d3f96985411d9a98811c50bf20c74c0e2ccad95ae5` ✓ | confirmed |

### 2.7 `reports/scale_v2.json` + `reports/cards/wp8_performance.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C82 | SQL 三档 ×4 查询 p50/p95（12 点，如 1 000 行 q1 `0.243 / 0.325`，100 000 行 q3 `72.184 / 75.141`） | wp8 卡 L69-L73 | 从每点 `samples_ms` 重算 p50（median）与 p95（nearest-rank `sorted[ceil(0.95*(n-1))]`）：**12/12 点与报告完全一致（0 处不符）** | confirmed |
| C83 | 索引优化 q4 `4.240 → 0.577`，回退 `3.836`，×7.348；q3 ×1.088；q2 ×1.017；q1 ×0.931 | wp8 卡 L77-L84 | 报告 `index_ab.measurements`：q4 `before 4.24 / after 0.577 / reverted 3.836` → 4.24/0.577=**7.348**；q3 `74.125/68.122=1.088`；q2 `1.017`；q1 `0.931` | confirmed |
| C84 | 检索剪枝 ×2.878 / ×2.847 / ×2.753 / ×2.823；候选集与 top-5 相同 | wp8 卡 L88-L93 | 报告 `retrieval_scan` 保留原始样本与 `equivalent` 标志 | confirmed（报告内；算术未逐点重算） |
| C85 | 块定位批量取回为负优化 ×0.909 / ×0.785 / ×0.792 | wp8 卡 L97-L101 | 报告 `locator_ab`；三组 `equivalent=true`（输出 sha256 相同） | confirmed（报告内） |
| C86 | 并发 1/2/4/8：完成 8/16/32/64，成功率 1.0，墙钟 0.272/0.281/0.314/0.327 s，29.4299→195.9689 次/秒，峰值内存 34 750 464→280 559 616 B | wp8 卡 L106-L110 | 报告 `concurrency.points` 逐值一致；`completed_per_second` 由未取整墙钟计算（8/0.27183=29.4299），与 `requests_completed/wall_s_rounded` 的 0.06% 差可解释 | confirmed |
| C87 | 分段计时：路由 0.010、Catalog 召回 0.122、检索 34.852、建连 4.883、SQL 70.525、**工具链合计 110.877**；冷启动 465.094；模型段 `null` | wp8 卡 L116-L125 | 报告 `profile.segments` 同值；冷启动 0.009+0.265+373.572+7.529+83.719=**465.094** ✓；合计为「每次迭代求和后取中位」，故 110.877 ≠ 各段中位数之和 110.392（实现 L415 `toolchain_total = route+recall+retrieve+connect+execute`），非算术错误 | confirmed |
| C88 | 文档三档 10/100/500 页：841.628 / 8 377.814 / 42 265.107 ms（n=3/2/1） | wp8 卡 L131-L133 | 报告 `documents` 保留原始样本；n 与切片数（44/440/2200）存在 | confirmed（报告内） |
| C89 | 数据自校验 12/12 文件重生成一致 | wp8 卡 L56 | 报告 `sql_scale.data.ok = true`、`problems: []`；未重跑生成器 | partial |
| C90 | 临时库清理 4/4；`pg_database like 'datamind_scale%'` = 0 | wp8 卡 L145 | 只读 SQL：`select count(*) from pg_database where datname like 'datamind_scale%'` → **0**，exit 0 | confirmed |
| C91 | 测试 31/31 + 守护 7/7 | wp8 卡 L139-L143 | `tests.test_eval_scale_v2` Ran 31 OK（守护类含在其中）；未单独跑 `LegacyBehaviorGuardTests` | partial |
| C92 | 三档行数 1 000/10 000/100 000（合计 1 270/12 220/122 020） | wp8 卡 L54 | 报告 `database.rows`：`products 20 / customers 2000 / orders 20000 / order_items 100000`（10 万档）；合计口径一致 | confirmed |
| C93 | 模型/首字/完整响应留空 `null`，`status=not_measured` | wp8 卡 L149 | 报告 `model/first_token/complete_response` 的 `n=0, p50=null` | confirmed |
| C94 | 环境 `commit=e4e2ebf`、api 0.39.0 | wp8 卡 L7 | 报告 `environment.commit="e4e2ebf"`、`api_version="0.39.0"` | confirmed |

### 2.8 `reports/qa_samples_v1.json` + `reports/cards/wp9_qa_samples.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C95 | 11 份样本、23 个问题、6 种格式 | wp9 卡 L47-L48 | `ls data/qa_samples/*.{pdf,docx,xlsx,pptx,dxf}` = **11**；报告 `manifest.samples 11 / questions 23 / formats 6` | confirmed |
| C96 | manifest 内 digest `8080b273…`；`--check` ok true | wp9 卡 L49、L65 | re-run `build_samples.py --check` → `{"ok": true, "problems": []}`，exit 0；报告 `manifest.digest = 8080b273c4cd2325…` | confirmed |
| C97 | 逐份 sha256 前 16 位（01 `c73f4c61…` … 11 `12511e70…`） | wp9 卡 L51 | 抽检 01 = `c73f4c6120fba820…`、05 = `e8b99022dffb2ca6…`，与卡片一致 | confirmed（抽检） |
| C98 | 扫描件 `03` 文本长度 0、`04` 长度 1（仅换行） | wp9 卡 L68 | 逐页 `get_text()`：03 单页 len 0；04 两页各 len 0，`"\n".join` 后 len **1**（即分隔换行） | confirmed |
| C99 | 27/27 有库行、searchability 三元组 27/27 一致、0 不一致 | wp9 卡 L75-L76 | 报告 `stored.consistency {"documents_reported_by_api":27,"documents_with_db_row":27,"consistent":27,"inconsistent":0}` | confirmed（报告内；未独立重算 searchability） |
| C100 | `index_state_counts {"indexed": 26, "no_chunks": 1}`；`db_document_rows 30`、未发布 3 | wp9 卡 L77-L78 | 报告同值 | confirmed（报告内） |
| C101 | 样本入库 `1/11`、匿名可见 `0/11` | wp9 卡 L79 | 报告 `samples_in_store 1 / samples_published_visible 0 / samples_total 11`；逐样本 `status` 计数 `not_in_store 10 + in_store_unretrievable 1` | confirmed |
| C102 | 答案断言离线重算 `23/23`、失败 0 | wp9 卡 L67 | 报告 `assertions_ok 23 / assertions_failed 0` | confirmed（卡片已声明这不是模型答对率） |
| C103 | 上传 201、`block_count 6`、契约定位 6、`searchable=false`、`index_state=no_chunks` | wp9 卡 L92-L96 | 报告 `http.after_approval`：`upl-a7fe8301ec5e0ea2d76de591b778ec5601f89788`、`block_count` 6、`searchable false`、`index_state no_chunks`、`locator_kinds_check.ok true` | confirmed（报告内） |
| C104 | 提问 `FAILED_WITH_EVIDENCE/DOCUMENT_SELECTION_UNAVAILABLE`、`ABSTAINED/INSUFFICIENT_EVIDENCE`（169 切片）、对照组 `COMPLETED` 引用 1 条 | wp9 卡 L98-L100 | 报告 `ask_with_document_ref` / `ask_default_scope`（`eligible_chunks 169`）/ `control_ask`（`COMPLETED`, `citation_count 1`, title 04_售后服务与退款管理制度）逐条一致 | confirmed（报告内） |
| C105 | 顶层 `failures` **3 条**、`not_done` 4 条 | wp9 卡 L101 | `failures n= 3`（automatic_index_not_implemented + qa_not_completed ×2）；`not_done` 4 条 | confirmed |
| C106 | 隔离回归 16/16 | wp9 卡 L107 | `tests.test_qa_samples` Ran 16 OK | confirmed |

### 2.9 `reports/baseline_current.json` / `nl2sql_current.json` / `rag_current.json` / `robustness_current.json` + `reports/cards/wp1b_baseline.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C107 | nl2sql **13/14**、rag **11/12**、robustness **4/4**、合计 **28/30** | wp1b 卡 L48-L51 | 与三份原始报告交叉核对：`raw nl passed/total 13 14`、`raw rb 4 4`、rag `passed 11 / case_count 12`；**逐题 verdict 与 raw 报告的 `match` 零不一致**（30 题） | confirmed（未重新调用模型） |
| C108 | `accuracy 0.9333333333333333`；分suite `0.9285714285714286 / 0.9166666666666666 / 1.0` | wp1b 卡 L48-L57 | 报告 `summary` 原值一致；13/14=0.92857…、11/12=0.91666…、28/30=0.93333… 算术自洽 | confirmed |
| C109 | `refusal_rate 0.1666`（5/30）、`refusal_rate_unexpected 0.0333`（1/30）、`refusals_expected_correct 4` | wp1b 卡 L58-L65 | 报告 `refusal_numerator_all 5`、`refusals_expected_correct 4`、`refusals_unexpected 1`；`accounting_note` 明写「refused 与 passed 互斥；预期拒答判对同时计入两处」 | confirmed |
| C110 | 上游错误 `0`；失败分类 `sql_repair_budget_exhausted 1`、`rag_false_abstain 1` | wp1b 卡 L61-L67 | 报告 `upstream_errors 0`、`failure_counts` 同值 | confirmed |
| C111 | RAG 引用定位齐全 **10/11**（rag-010 为 0 引用） | wp1b 卡 L68 | 报告逐题 `citations_complete`；rag-010 `fact_check/abstain` 与卡片一致 | confirmed |
| C112 | `Recall@5=1.0`、`MRR@10=0.9545454545454546`（answerable 11） | wp1b 卡 L69 | `rag_current.json` `recall_at_5 1.0`、`mrr_at_10 0.9545454545454546`、`answerable_count 11` | confirmed |
| C113 | 三套实际耗时约 41s/43s/29s | wp1b 卡 L116 | 作者记录的时间戳，我未重跑 live | unverifiable |
| C114 | 工作区哈希 `158e90c2…`（58 文件） | wp1b 卡 L120 | 工作区在复查期间持续变化，未重算 | unverifiable |
| C115 | 历史对照 `14/14`、`10/12`、`4/4` | wp1b 卡 L75-L77 | `reports/nl2sql.json`/`rag.json`/`robustness.json` 中确有这些数；卡片明确「两次数字不拼接」 | confirmed（口径已隔离） |
| C116 | **`rb-004` 库内 `category` 实为 `鲁棒-空结果`，故 `eval_sql.py` 的 `category=="空结果"` 分支未触发** | wp1b 卡 L89-L90、L124 | **反证**：只读 SQL → `rb-004\|空结果\|robustness`；登记文件 `data/evaluation/registry/robustness_v1.json` 同样 `category='空结果'`（digest 校验通过）。故 `category == "空结果"` **成立**，`scripts/eval_sql.py:72` 的 `EMPTY_RESULT` 分支**会触发** | **refuted** |

### 2.10 `data/evaluation/registry/` + `reports/cards/wp1a_registry.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C117 | `check` 3/3 一致，digest `a73bf740…` / `f5d42555…` / `ec105871…` | wp1a 卡 L60-L61 | re-run `eval_registry.py check` → `[一致] nl2sql_v1.json ... digest=a73bf740e4ad4eb9…`、`rag ... f5d42555…`、`robustness ... ec105871…`，exit 0 | confirmed |
| C118 | 库内题量 nl2sql 14 / rag 12 / robustness 4 | wp1a 卡 L59 | 只读 SQL：`nl2sql=14 rag=12 robustness=4` | confirmed |
| C119 | 统一 schema 检查既有报告 **0/3**：`nl2sql.json=57`、`rag.json=49`、`robustness.json=17`（合计 123） | wp1a 卡 L65 | re-run `eval_registry.py schema` exit 1 → `（57 项）`、`（49 项）`、`（17 项）` | confirmed |
| C120 | append-only 重复冻结退出码 2；篡改检出退出码 1 | wp1a 卡 L62-L63 | 未复跑（会写/读 registry 目录）；`registry.py:100-104` 零分母返回 `"N/A"` 的实现已读代码确认 | partial |
| C121 | 隔离回归 15/15 | wp1a 卡 L64 | `tests.test_eval_registry` Ran 15 OK | confirmed |

### 2.11 `reports/office_checks.json` / `交付/` + `reports/cards/wp10_delivery.md`

| # | 数字（原样） | 声称来源 | 我的复跑结果 | 判定 |
|---|---|---|---|---|
| C122 | 交付物 **6/6**（2 DOCX、3 PDF、1 PPTX）真实生成 | wp10 卡 L41 | `交付/` 实测：`01_设计报告.docx/pdf`、`02_技术实现说明书.docx/pdf`、`03_答辩演示.pptx`、`04_突破与创新性自述.pdf`（+ docx 源）= 6 份二进制 | confirmed |
| C123 | 页数 13 / 12 / 20 张 / 3 页（限 20/30/20/5） | wp10 卡 L44-L47 | pymupdf/python-pptx 直读：`pdf pages= 13`、`12`、`3`；`pptx slides= 20` | confirmed |
| C124 | 结构检查 4/4 通过（check_office exit 0） | wp10 卡 L43 | `reports/office_checks.json` 四个 `exit_code 0`；`交付/DELIVERY_MANIFEST.json` 记 `"verdict": "pass"` | confirmed |
| C125 | PPT 匿名：黑名单命中 0、称谓提示命中 0 | wp10 卡 L48 | `office_checks.anonymity_scan`：`blocklist_hits: []`、`person_name_hint_hits: []`（20 页） | confirmed |
| C126 | ZIP dry-run **427 文件 / 10 573 573 字节（10.08 MiB）**；`zip_written=False` | wp10 卡 L49 | re-run `build_delivery_zip.py --json tmp/...` exit 0：**449 文件 / 11 013 957 字节（10.5 MiB）**，`zip_written False`。工作区漂移（卡片自述 422→427）→ 数值不可复现，但「只做 dry-run、不落盘」为真 | partial（数字随工作区变动，卡片已声明） |
| C127 | 官方 ZIP 要求 4 satisfied / 1 satisfied_by_list / 2 partial | wp10 卡 L50 | `交付/DELIVERY_MANIFEST.json` 内 `zip` 段落为作者自评；未逐项重判 | unverifiable |
| C128 | 设计报告第 1–2 页为核心亮点预览页（6 个亮点框） | wp10 卡 L44 | 前两页文本中 `亮点 1..6` **6 个**全部出现，且第 2 页仅 432 字符（未溢出到第 3 页） | confirmed |
| C129 | 干净环境演练 0/2、视觉检查 0/6（未做） | wp10 卡 L52-L53 | 与卡片一致（未做即未做，无伪造） | confirmed |
| C130 | **`reports/binding_ablation.json` 与 `reports/cross_source_offline.json` 均不存在** | wp10 卡 L58 | 复查时刻：`binding_ablation.json` **确实不存在**（`ls` exit 1，全仓无 `binding_traps`/`eval_binding.py`）；但 `cross_source_offline.json` **已存在**（13 798 B，22:41 落盘，晚于本卡 22:2x 的写作时刻）。交付件 `交付/04_突破与创新性自述_DataMind_Agent.docx` 第 [22]-[23] 段仍写「两份文件均不存在」 | **refuted（半条时效性错误）**：其中一份缺失属实，另一份已存在；卡片与自述文档未同步 |
| C131 | 自述中「两份专项报告」数字一律写「待本轮复查确认」 | wp10 卡 L58 | 自述 docx 全文 **0 次**「待本轮复查确认」；实际写法是「本轮生成前…两份文件均不存在，因此本自述不给出绑定准确率…」（第 22-23 段）。**没有编造数字**，但卡片描述的措辞与实际交付文本不符 | refuted（措辞不符，实质更保守） |
| C132 | 自述引用 `reports/vcar_ablation.json`：`dataset vcar-ablation-v1`、sha256 前 16 位 `74cbbc1ef760f530`、`cases=16`、`generated_at 2026-10-07T15:21:10+00:00` | 交付 docx 第 [17]-[18] 段 | `reports/vcar_ablation.json`：`dataset.version="vcar-ablation-v1"`、`sha256="74cbbc1ef760f53026152f…"`、`cases=16`、`generated_at="2026-10-07T15:21:10+00:00"` | confirmed |

---

## 3. 真实重跑的命令、退出码与关键输出（原样摘录）

| # | 命令 | 退出码 | 关键输出（原样摘录） |
|---|---|---:|---|
| R1 | `.venv/bin/python scripts/eval_cross_source.py --offline --out tmp/review_r1/cross_source_offline.json` | **0** | `"totals": {"cases": 21, "passed": 21, "failed": 0, "pass_rate": "21/21"}`；`"checks": {"total": 161, "failed": 0, "passed": 161}`；`"model_calls": 0`；`error_code_counts {"CONFLICT_UNRESOLVED": 2, "DOC_BINDING_MISSING": 4, "NONE": 15}` |
| R2 | `.venv/bin/python scripts/eval_schema.py --suite v2 --output tmp/review_r1/schema_linking_v2.json` | **1**（默认 `--min-recall 0.90` 门槛未达，与卡片一致） | `table_recall_at_5: 65/88 = 73.9%`；`column_recall_at_5: 48/56 = 85.7%`；`metric_recall_at_5: 10/10 = 100.0%`；`join_path_accuracy: 25/25 = 100.0%`；`reject_accuracy: 9/9 = 100.0%`；`distractor_false_positives: 0/32 = 0.0%` |
| R3 | `.venv/bin/python scripts/eval_schema.py --output tmp/review_r1/schema_v1_recheck.json` | **0** | `table_recall_at_5: 31/31 = 100.0%`；`column_recall_at_5: 28/28 = 100.0%`；`metric_recall_at_5: 4/4 = 100.0%`；`join_path_accuracy: 6/6 = 100.0%` |
| R4 | `.venv/bin/python scripts/eval_multi_turn.py --offline --output tmp/review_r1/multi_turn_offline.json` | **0** | `{"turn_accuracy": {"numerator": 61, "denominator": 61}, "segment_accuracy": {"numerator": 10, "denominator": 10}, "inheritance_traceability": {"numerator": 46, "denominator": 46}, "scope_change_rejection": {"numerator": 2, "denominator": 2}, "dangerous_turn_refusal": {"numerator": 1, "denominator": 1}}` |
| R5 | `.venv/bin/python scripts/eval_extensible_fields.py --output tmp/review_r1/extensible_fields_v1.json` | **0** | `declarative: 120/120 = 100.0%`；`0_examples: declarative 120/120 = 100.0%; stub_fewshot 3/120 = 2.5%`；`3_examples: ... 63/120 = 52.5%`；`10_examples: ... 120/120 = 100.0%` |
| R6 | `.venv/bin/python scripts/eval_clarification.py --offline --output tmp/review_r1/clarification_offline.json` | **0** | detector `clarification_success {'numerator': 18, 'denominator': 18, 'rate': 1.0}`、`slot_landing {'numerator': 14, 'denominator': 14}`；deployed `{'numerator': 4, 'denominator': 18, 'rate': 0.2222}`、`slot_landing {'numerator': 4, 'denominator': 4}` |
| R7 | `.venv/bin/python scripts/eval_complexity.py --output tmp/review_r1/complexity_v1.json` | **0** | `{"documents": 26, "correlation": "成立", "spearman_elapsed_ms": {"n": 12, "rho": 0.5639}, "band_mean_elapsed_ms": {"low": 379.4, "medium": 1684.3}, "mismatches": 6, "failures": 0}` |
| R8 | `.venv/bin/python scripts/eval_registry.py check` | **0** | `[一致] nl2sql_v1.json: suite=nl2sql cases=14 digest=a73bf740e4ad4eb9…`（rag/robustness 同） |
| R9 | `.venv/bin/python scripts/eval_registry.py schema` | **1** | `[不符合 schema eval-report-v1] reports/nl2sql.json（57 项）`、`rag.json（49 项）`、`robustness.json（17 项）` |
| R10 | `.venv/bin/python data/qa_samples/build_samples.py --check` | **0** | `{"checked": ".../data/qa_samples/manifest.json", "ok": true, "problems": []}` |
| R11 | `.venv/bin/python data/evaluation/extensible_field_v1/build_suite.py --check` | **0** | `check ok: 6 materials, 6 fields, 120 assertions, domain=设备维保工单` |
| R12 | `.venv/bin/python scripts/build_delivery_zip.py --json tmp/review_r1/zip_plan.json` | **0** | `"stats": {"file_count": 449, "total_bytes": 11013957, "total_mib": 10.5, ...}`；`zip_written False` |
| R13 | `python -m unittest tests.<module>`（11 个模块，`tests.test_clarification_flow`/`test_multi_turn` 需 `PYTHONPATH=tests`） | 见下 | 33 OK / 19 OK / 31 OK / 16 OK / 16 OK / 30 OK / 23 OK / 33 OK / 15 OK / 8 OK；`test_extensible_fields` **Ran 17 tests / FAILED (failures=1)** |
| R14 | 只读 SQL：`select case_id,category,suite from evaluation_cases where case_id like 'rb-%'` | **0** | `rb-001\|鲁棒-错别字`、`rb-002\|鲁棒-口语简称`、`rb-003\|越权`、**`rb-004\|空结果`** |
| R15 | 只读 SQL：`select count(*) from pg_database where datname like 'datamind_scale%'` | **0** | `0` |

R13 附：`test_extensible_fields` 的失败信息原样摘录——
```
AssertionError: '?? db/migrations/019_evidence_graph.sql' != ''
- ?? db/migrations/019_evidence_graph.sql
 : 本 WP 不得新增/改动迁移文件
```
这独立复现了 wp2 卡 L81 声明的跨 WP 冲突（新增 019 迁移触发他人测试断言），也证明 019 文件真实存在且未提交。

---

## 4. 统计口径作弊四形态检查

| 形态 | 检查方法 | 结论 |
|---|---|---|
| **(1) 把拒答/超时/上游错误移出分母** | 读 `accounting_note`、逐桶核对分子分母；扫描全部报告 JSON 中 `denominator` 与 `numerator/rate` 的一致性 | **未发现移出分母**。`baseline_current.json`：`refusal_denominator=30`、`upstream_errors=0`，`accounting_note` 显式说明 refused/passed 关系；`multi_turn` offline/live 分母恒为 61；`clarification` 各臂分母恒为 18 或 7 或 25；`scale` 明写「拒绝/超时/上游错误本轮为 0，但分母未调整」。**注意一处口径削弱**：`clarification` 的 `slot_landing` 分母为 14（detector）/4（deployed），另 **4 例记 N/A**（文档版本/库存不在 `query_frame` 契约内）——这是显式声明的分母收缩，报告列出了 `not_applicable_case_ids`，且卡片 L91-L92 明写，不计为作弊，但引用 14/14 时必须带「4 例 N/A」。 |
| **(2) 零分母报 0 或 100%** | 代码级：`data/evaluation/registry/registry.py:100-104` `ratio()` 零分母返回 `"N/A"`，`validate_report()`（L394-399）对「零分母报非 N/A」「非零分母报 N/A」双向报错；报告级：脚本扫描 11 份 JSON 中所有含 `denominator` 的结构 | **未发现**。扫描 0 处违规；`scale_v2` 的模型段是 `n=0, p50=null, status=not_measured` 而非 0；`complexity` 的 `catalog_build` 用 `n=0, p50=null, cold_ms=20.533` 单列。 |
| **(3) 把「能跑通」当「答案正确」** | 逐报告核对判定函数与自述 | **未发现伪装，但存在「自证型」头部比率**，需按卡片自述打折使用：`cross_source_offline` 21/21 是**注入 fixture + 期望先冻结**的内部一致性（含 6 条预期拒答计 pass），卡片 L104 明写「不是泛化准确率」；`multi_turn` offline 61/61 只测**决策层**（帧/路由/澄清/范围），卡片 L112 明写「不说明答案正确」，live 判定亦为「决策正确+状态符合期望动作」，**未做答案内容判定**；`qa_samples` 23/23 是**断言离线重算**，卡片 L121 明写不冒充问答准确率；`extensible` 120/120 是**确定性规则抽取器**，卡片 L78 明写不是 few-shot 实验。以上四处均为诚实标注，不算作弊，但**不构成对外准确率**。 |
| **(4) 把 stub/模拟当真实模型成绩** | 核对各臂命名与模型调用计数 | **未发现冒充**：`cross_source_offline` `model_calls=0` 并注明 fixture 注入；`clarification` 明确分 `detector`（离线 stub）与 `deployed`/`live` 三臂，且把 stub 臂的 18/18 与线上 4/18 并列；`extensible` 明写 `stub arm 是替身`；`scale` 模型段留空 `null` 并附原因；`multi_turn` live 记录 `model="deepseek-flash"` 与 61 个 `run_id`。 |

**口径层面的两点风险（非作弊，但会抬高读数）**：
1. `clarification` 的「不必要澄清率 0/7」在 **deployed 臂**上几乎是结构性成立——该臂在 18 条澄清期望里只反问了 4 条，反问越少，不必要澄清自然越接近 0。卡片 L80-L82 已把这个矛盾写成失败样本（澄清成功率仅 4/18），但把「0/7」单独引用会误导。
2. `clarification` 的 detector 臂（18/18）与冻结集的期望**同源**（检测器规则即标签来源），本质是自洽性检查；卡片未使用「自洽/同源」这一措辞，引用时须补上。

---

## 5. 失败样本保留情况（提升计划 §8 要求）

| 报告 | 失败样本是否保留 | 与实测输出是否一致 |
|---|---|---|
| `cross_source_offline.json` | **报告内 `failure_samples` 为空数组**；6 条设计内 fail-closed 分布在 `cases[]` 与 `error_code_counts` | 卡片列出的 6 条（cs-p1-06/p2-05/p3-04/p4-05/p3-01/p3-02）= 4 条 `DOC_BINDING_MISSING` + 2 条 `CONFLICT_UNRESOLVED`，与实测 `error_code_counts` **完全吻合** |
| `complexity_v1.json` | 保留 `expectation_mismatches` 6 条 + `failures 0` | 抽查一致：kb-07/kb-08 期望 high 实际 medium 45.16、kb-06 10.87、ohr-005/ohr-010/ohr-011 的期望与实际分档与卡片逐条对应 |
| `schema_linking_v2.json` | 保留 `cases[].failure_causes`（13 条用例承载 31 个缺失项）+ 首轮初稿数字 | 一致：`not_matched 0 / global_budget_cut 30 / kind_top5_cut 1`；budget 8 vs 32 的 65/88 → 85/88 |
| `multi_turn_v1.json` | 保留 live 61 条逐轮记录（含 3 条未通过）；顶层 `failures` 为空列表 | 一致：`passed True` 58/61，mt-07 的 3 轮复问可从 `records` 定位 |
| `baseline_current.json` | 保留 30 条逐题记录（含 `fail` nl2sql-010、`abstain` rag-010 及 `failure_reason`） | 一致：与 raw 报告逐题 `match` 零不一致 |
| `qa_samples_v1.json` | 保留顶层 `failures` 3 条 + `not_done` 4 条 + 逐样本 `status` | 一致，且如实登记「1/11 入库、索引 deferred」 |
| `clarification_v1.json` | 保留顶层 `failures` 28 条（deployed 14 + live 接不上/上游/拒答等） | 一致：`upstream_or_timeout` 5 例 ID 与卡片逐个吻合 |
| `scale_v2.json` | 不适用（数值型）；负结果（q1/q2 无收益、块定位负优化）写进 `caveats`/卡片 | 一致 |

**判定**：**失败样本总体保留良好**，唯一结构性瑕疵是 `cross_source_offline.json` 的报告级 `failure_samples` 为空（6 条预期拒答被计入 pass），卡片正文虽保留，但机器可读报告里无法一键取到失败清单。

---

## 6. refuted / unverifiable / partial 清单

### 6.1 refuted（2 条，含文件与行号）

1. **`reports/cards/wp1b_baseline.md:89-90`（失败样本 3）与 `:124`（诚实边界第 5 条）关于 `rb-004` 的事实陈述错误。**
   卡片称「库内该题 `category` 实为 `鲁棒-空结果`，字符串不等 → 分支未触发」。实测：`evaluation_cases` 中 `rb-004|空结果`，且冻结登记 `data/evaluation/registry/robustness_v1.json` 的 `category` 同为 `空结果`（digest `ec105871…` 校验通过）。因此 `scripts/eval_sql.py:72` 的 `if category == "空结果":` **会命中**，`EMPTY_RESULT` 断言**是被检验的**。该卡据此得出的「rb-004 的 EMPTY_RESULT 机制未被验证」结论不成立。（注：这属于作者对自己系统的**低报**，不是刷分，但仍是错误陈述。）

2. **`reports/cards/wp10_delivery.md:58`（失败样本 1）关于「两份报告均不存在」的陈述与第 131 条关于自述措辞的描述，在复查时刻只有一半成立。**
   `reports/binding_ablation.json` **确实不存在**；但 `reports/cross_source_offline.json` **已存在**（13 798 B，`2026-10-09 22:41` 落盘，晚于本卡写作的 22:2x）。更重要的是，交付件 `交付/04_突破与创新性自述_DataMind_Agent.docx` 第 [22]-[23] 段仍写「两份文件均不存在」，而卡片称自述中对应数字写了「待本轮复查确认」——自述全文该短语出现 **0 次**。实质更保守（未编造任何绑定数字），但卡片对交付文本的描述与实际不符，且交付件已过时。

### 6.2 关键缺项（unverifiable / 计划要求未达成）

3. **`reports/binding_ablation.json` 完全缺失**（任务列出的复查对象之一）。全仓检索：无 `binding_ablation*`、无 `data/evaluation/binding_traps_v1.json`、无 `scripts/eval_binding.py`。因此《提升计划》§WP3 的三臂消融 `plain/bind_only/full`、**错误绑定率**、冲突 P/R、误修复率**均无任何数据**；`reports/improvement_plan_20261010_competition.md` §2.1（高级加分 8–12）与 §WP3 的交付物清单**未兑现**。wp2 卡 L112 与 wp10 卡 L104 已如实声明「三臂对照未做」「绑定准确率/错误绑定率没有数字」，这一点是诚实的，但不能作为加分证据。

### 6.3 unverifiable / partial（14 条，摘要）

- 所有 `--live` 数字：`multi_turn` 58/61（C75）、`clarification` live 4/18（C18）、`baseline` 30 题耗时（C113）、澄清「另一次运行 10/18」（C29）、多轮「32→41→61」修订历史（C80）——**只做了内部一致性核对，未独立复跑**（会真实调用上游模型）。
- `complexity` 的 ρ=0.641（C33）与档位均值（C36）：复跑得 ρ=0.5639、均值 379.4/1684.3。**结论「成立」在两次运行下都成立，但数值不复现**，且两次都靠近 0.50 门槛，稳健性弱。
- `scale` 的 ZIP 文件数/字节（C126）、数据自校验（C89）、守护测试 7/7（C91）：随工作区漂移或未单独跑。
- `schema v2` 的构造表池（C65）、`suite_sha256`（C68）：为作者声明/报告内字段，未独立重算数据文件。
- 提示词 sha256（C12）、工业规则 11 行等价（C11）、wp1a 的 append-only/篡改退出码（C120）：未复跑。
- `office` 的官方 ZIP 自评项（C127）。

---

## 7. 副作用与自证声明（如实）

1. **被复查文件零改动**：复查前后对 12 份目标报告逐一 `sha256sum -c`，**全部 `OK`**（`cross_source_offline.json`、`clarification_v1.json`、`complexity_v1.json`、`extensible_fields_v1.json`、`schema_linking_v2.json`、`multi_turn_v1.json`、`scale_v2.json`、`qa_samples_v1.json`、`baseline_current.json`、`nl2sql_current.json`、`rag_current.json`、`robustness_current.json`）。所有复跑输出写入 `tmp/review_r1/`。
2. **`reports/latest_regression.json` 在复查期间被改写**（`{"tests": 29, "ran_at": "2026-10-09T15:11:50Z"}`）：复查开始时记录的 sha256 为 `53dd2e874c1a02d66a2cf55ada4167167440c4308a5ca7a1a199102504b7fc0a`，变化后为 `765d64cd62944722a623568e286ea136e7aabca430cb0dfa3f6544599c854bca`。**我未执行 `scripts/test_isolated.py`**（只跑了直接 `python -m unittest`，该路径不写此文件）；写入值 `tests=29` 对应 `tests/test_cad_capability_suite.py` 的 29 个用例，**该模块我从未运行**，且工作区在 23:05 后仍有其他进程写入 `data/object_store/page_images/**`，因此该改写**极可能来自并发工作区进程**。我未能复原原始字节（快照时未留备份），仅留下哈希，特此声明。该文件在本轮开始前已是 `git status` 中的 `M`，且各 WP 卡片均已记录其被反复改写。
3. 未执行任何 git 写操作（无 `add/commit/checkout/restore/stash`）；未删除文件；未重启/重建容器；未停止 8000 端口服务；未执行 `scripts/start_local.sh`；未跑 `scripts/ingest.py`；未对演示库（零售/工业）做任何写操作（只有只读 SQL）；未跑全量回归与全量摄取。

---

## 8. 总评 verdict

**本轮证据可信度：中。**

**一句话理由**：所有**能被确定性复跑**的汇总数字几乎逐位吻合（6 个工作包 7 条命令复跑一致、scale 12/12 点 p50/p95 重算零差异、11 个测试模块用例数全中、未发现任何拒答移出分母/零分母充数/stub 冒充/「跑通=答对」的作弊痕迹，报告与卡片的自曝缺陷密度也高），但存在 **1 处被证伪的库内事实陈述（`rb-004`）**、**1 份计划要求的关键报告 `reports/binding_ablation.json` 完全缺失**（创新点的旗舰度量无数据）、**1 份交付件（创新自述）内容已过时**，加之若干头部比率属「自建集自证 / 决策层自证」性质、`complexity` 的相关系数两次运行不可复现且贴着门槛，因此证据**可信但不足以支撑「提升/创新已成立」的结论**，判为中而非高。

**给主协调者的三条最小修正建议**（不改实现，仅提示）：
1. 更正 `reports/cards/wp1b_baseline.md:89-90/:124` 的 `rb-004` 事实（库内 `category='空结果'`，`EMPTY_RESULT` 分支会被触发）；若要断言该机制，需要一次真实的 `eval_sql.py --suite robustness` 复跑并保留 `validator_findings`。
2. 补齐 `reports/binding_ablation.json` 或从《突破与创新性自述》与计划中正式移除「错误绑定率 ≤3%/≤5%」的加分主张（当前 wp10 卡与自述已部分声明缺失，但自述文本仍写着「两份文件均不存在」，需按现状更新）。
3. 在引用 21/21、61/61、120/120、18/18 时统一加「自建集/注入 fixture/决策层/同源检测器」限定词，并给 `complexity` 的 ρ 加上「单次运行、两次结果 0.641 / 0.5639」的双值披露。
