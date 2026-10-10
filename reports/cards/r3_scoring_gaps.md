# R3 结论卡：补齐官方评分表仍缺独立证据的行

## 固定字段

| 字段 | 内容 |
|---|---|
| **WP** | R3-scoring-gaps（收口轮：单文档 RAG 忠实度 / 鲁棒性扩展 / 端到端分段时延 / 多文档问答） |
| **日期** | 2026-10-10（CST；撰写时 `date` = `Sat Oct 10 08:18:14 CST 2026`） |
| **基线提交** | `e4e2ebf`（`git rev-parse --short HEAD`，只读，exit 0；工作区含他人并行未提交改动，未由本 WP 提交任何东西） |
| **API 版本** | `0.39.0`（`GET /openapi.json` → `info.version`） |
| **上游模型** | `deepseek-flash`（`NEW_API_PROTOCOL=openai`，`NEW_API_BASE_URL=https://api.deepseek.com/v1`；`datamind/providers.py:87` 为 `stream=false`） |
| **会话获取** | 回环免登录身份：`GET /v1/auth/me`（`datamind/api.py:714`，`provision_demo=False` → 纯读，不写 `auth_users`）取 `csrf_token`，再带 `X-CSRF-Token` 调 `POST /v1/chat`、`POST /v1/chat/stream`。**没有绕过认证**，也没有伪造会话。 |

## 1. 差距盘点（只读：`docs/SCORE_GAP_LEDGER.md` + `reports/round1_improvement_report_20261010.md` §4/§5 + `docs/OFFICIAL_REQUIREMENTS_MAPPING.md`）

**盘点时「已有独立实测证据」的行**：E1/E5/F4（`nl2sql` 14 条、`reports/nl2sql.json`）、E10（`multi_turn_v1.json`）、E8/F8（B3 澄清、B4 Schema Linking、B2、B8 四份材料）、E7/F6 的工具链部分（`scale_v1/v2.json` 的 CAD 4 点 + 文档 4 点 + SQL 3 档 + 并发 4 点）、E11/F10（DOCX/PDF 交付稿）、F1 的离线部分（`cross_source_offline.json`）、E9 的 4 条老用例（`robustness.json`）。

**盘点时「没有独立证据」且必须在收口轮补的行**（本 WP 的靶子）：

| 官方行 | 盘点时的缺口（原文/实测核对） |
|---|---|
| 初赛性能 2. RAG 答案忠实度（E6） | `reports/rag_fields.json` 10/12 是**端到端正确率**，不是逐结论忠实度；`reports/rag_current.json` 只有端到端对错 |
| 初赛功能 2. RAG 单文档（E2） | 只有历史 12 题（较早代码）；公开切片 169 条里条款级证据不足 |
| 初赛优化 2. 鲁棒性（E9）+ 决赛性能 4. 低质量数据（F7） | 库内 `robustness` 仅 **4** 条（错别字/口语/越权/空结果各 1） |
| 初赛性能 3. 系统效率（E7）+ 决赛性能 3.（F6） | `reports/scale_v2.json`.`profile` 的 `model`/`first_token`/`complete_response` 三段为 `null`（当时取不到 CSRF 会话） |
| 决赛性能 2. 多文档/多模态 RAG（F5） | 「无固定多文档端到端集；准确率未在固定多文档集上评测」 |
| 仍无独立证据、**不属本 WP** | E1/E5/F4 的当前版本独立 NL2SQL 集；E3/E4/F3、E8/F8/F9、E11/F10、E12/F11/F12 的评委/现场项；F1 的 P2–P4 文档侧绑定；F2、F13；F14/F15 加分项 |

## 2. 做法

1. **E2/E6 单文档忠实度**：新建 26 题单文档冻结集 `data/evaluation/faithfulness_v1.json`（21 可答 + 5 拒答），每条冻结 `evidence_quotes`（引用片段原文）、`required_values`/`allowed_values`/`required_term_groups`；`scripts/eval_faithfulness.py` 用 `scope.document_refs` 固定到 1 份文档真实调用 `POST /v1/chat`，再把答案切成结论句做三态标注。
   **判定证据 = 本次运行 `citations[].quote` 的原文拼接**（不是冻结集期望答案、不是模型自述）。**规则判定与模型判定分列**：本轮 `model_judgment=null`，只用确定性规则引擎，不存在把模型判定混入规则数的情况。拒答句/版式残片单列，不进结论分母。
2. **E9/F7 鲁棒性扩展**：新建 21 题 / 12 类 `data/evaluation/robustness_v2.json`（错别字、口语简称、中英混写、全角半角、多实体歧义、越权数据源、越权列、空结果、超长输入、注入样式、空输入、控制组），`scripts/eval_robustness_v2.py` 逐条按**预先写死的判据**机械判定（http_status/statuses/错误码/答案词/SQL 执行/引用/零结果），失败样本全部保留在分母。
3. **E7/F6 分段时延**：`scripts/eval_scale.py` **加法扩展**（新增 `--kind profile_live` 与 `--profile-live*` 参数；既有 `--kind profile`、`--kind v1` 与既有字段一律不变），用回环免登录身份流式调用 `POST /v1/chat/stream`，按 SSE `trace` 事件切六段，写入 `reports/scale_v2.json` 的**新 section `profile_live`**（既有 `profile`/`sql_scale`/`concurrency`/`documents`/`locator_ab`/`retrieval_scan` 未覆盖、未改字段含义）。
4. **F5 多文档问答**：新建 12 题固定多文档集 `data/evaluation/multifile_v1.json`（每题 2–3 份文档，含跨文档对比题、含一份不覆盖文件的题、含 3 份文档的多跳题、含 3 条未覆盖拒答题），`scripts/eval_multifile.py` 逐题报告 status、引用覆盖（是否引到全部指定文件）、正确性、拒答正确率，逐结论忠实度复用同一规则引擎。

## 3. 数据与集

| 集 | 文件 | 规模 | 说明 |
|---|---|---|---|
| 单文档忠实度 | `data/evaluation/faithfulness_v1.json` | 26 题（21 可答 + 5 拒答），107 条 demo_kb 切片中取证据 | 每条引用片段都由脚本校验逐字出自目标文档（`--validate-only` exit 0） |
| 鲁棒性 v2 | `data/evaluation/robustness_v2.json` | 21 题 / 12 类 | 含 2700 字超限输入与空输入两个契约拒绝用例 |
| 端到端分段 | `scripts/eval_scale.py` 内置 4 题（2 SQL + 2 RAG） | 3 轮 × 4 题 = 12 次流式调用 | RAG 题固定到单份文档，避免检索随机性混入时延 |
| 多文档 | `data/evaluation/multifile_v1.json` | 12 题（9 可答 + 3 拒答），7 题要求引用覆盖全部指定文件 | 文档引用取 `document_assets`（read-only SQL）的 `document_id/doc_version/sha256/parser_version` |

## 4. 结果（分子分母同给）

### 4.1 E2/E6 单文档 RAG 忠实度（`reports/faithfulness_v1.json`）

| 指标 | 第 2 轮（`tmp/r3_faithfulness_run2.log`） | 第 3 轮（报告文件现状） |
|---|---|---|
| 结论句总数（分母） | 21 | 21 |
| supported | **20/21 = 0.9524** | **19/21 = 0.9048** |
| contradicted | **0** | **0** |
| no_evidence | 1 | 2 |
| 引用覆盖率（冻结证据被引用原文覆盖） | 18/21 = 0.8571 | 17/21 = 0.8095 |
| 拒答正确率 | 5/5 = 1.0 | 5/5 = 1.0 |
| 误拒（可答题被拒答） | 0/21 | 0/21 |
| 上游错误 | 0/26 | 0/26 |
| 列表题完整性（`require_all_term_groups`） | 2/2 | 2/2 |

原样摘录（run3 尾部输出）：`逐结论忠实度：支持 19/21 = 0.9048；矛盾 0；无依据 2` / `拒答正确：5/5；误拒：0/21；上游错误：0/26`。

### 4.2 E9/F7 鲁棒性（`reports/robustness_v2.json`）

| 类别 | 通过/总数 | 类别 | 通过/总数 |
|---|---:|---|---:|
| 错别字 | 2/2 | 越权数据源 | 2/2 |
| 口语简称 | 2/2 | 越权列 | 1/1 |
| 中英混写 | 2/2 | 空结果 | **1/2** |
| 全角半角 | 2/2 | 超长输入 | 2/2 |
| 多实体歧义 | 2/2 | 注入样式 | 2/2 |
| 空输入 | 1/1 | 控制组 | 1/1 |

- 总：**20/21 = 0.9524**；5xx / `INTERNAL_ERROR`：**0/21**；上游错误：**0/21**。
- 稳定拒答证据：`internal_hr` → `SOURCE_NOT_ALLOWED`；`secret_kb` → `COLLECTION_NOT_ALLOWED`；不存在的 `credit_code` 列 → `NEEDS_CLARIFICATION`（未编造列数据）；2700 字输入与空问题 → HTTP 422。
- 注入 2/2：答案中没有 `sk-`/`NEW_API_KEY`/`password=`/`credit_code=`/`密钥：` 等泄漏样式。
- 原样摘录：`通过 20/21 = 0.9524；5xx/内部错误 0；上游错误 0`。

### 4.3 E7/F6 端到端分段时延（`reports/scale_v2.json`.`sections.profile_live`）

12 次串行流式调用（3 轮 × 4 题，2 SQL + 2 RAG），客户端单调时钟实测：

| 分段 | 口径 | n | p50 (ms) | p95 (ms) |
|---|---|---:|---:|---:|
| route（路由） | 请求发出 → `query.parsed` | 12 | 69.6 | 104.2 |
| recall（召回） | RAG：`tool.started` → `retrieval.completed` | 6 | 53.2 | 57.1 |
| execute（执行） | SQL：`sql.validated` → `sql.executed` | 6 | 0.8 | 1.7 |
| model（模型） | RAG：`retrieval.completed` → 终态事件；SQL：`tool.started` → `sql.validated` | 12 | 1674.2 | 3238.6 |
| first_token（首字） | 请求发出 → 首个 SSE trace 事件 | 12 | 66.0 | 80.2 |
| complete_response（完整响应） | 请求发出 → 流结束 | 12 | 1790.9 | 3349.1 |

分路径：RAG `model` p50/p95 = 1645.7/2471.1 ms（n=6）、引擎自报 `rag_result.elapsed_ms` p50 = 1679.0 ms；SQL `model` = 1674.2/3238.6 ms（n=6）、引擎自报 `sql_result.elapsed_ms` p50 = 5.5 ms。**模型/首字/完整响应三段不再是 `null`**，且既有 `profile` section 的字段与数值未被改动。

### 4.4 F5 多文档问答（`reports/multifile_v1.json`，三轮真实跑）

| 指标（分子/分母） | run1 | run2 | run3（报告文件现状） |
|---|---|---|---|
| 可答题正确 | 3/9 = 0.3333 | 4/9 = 0.4444 | **5/9 = 0.5556** |
| 引用覆盖全部指定文件（`cite_all_expected` 题） | 1/7 | 2/7 | **3/7 = 0.4286** |
| 拒答正确 | 3/3 | 3/3 | 3/3 |
| 逐结论忠实度 | 6/11 | 8/13 | 9/13 = 0.6923 |
| 上游 `MODEL_EMPTY_RESPONSE` 单列（留在分母） | 5/12 | 3/12 | 4/12 |

原样摘录：`多文档正确 5/9 = 0.5556；引用覆盖全选 3/7；拒答 3/3；逐结论忠实度 9/13`。

## 5. 失败样本（全部保留、未移出分母）

| # | 样本 | 实测 | 性质 |
|---|---|---|---|
| 1 | `faith-014`、`faith-015`（01_重点产品市场分析报告：售出 190 台 / 2026 年风险） | `RAG_VERIFICATION_FAILED`，0 引用，无结论 | **产品侧 fail-closed**；同一文档 3 轮中复现（faith-015 三轮都失败） |
| 2 | `faith-012`（01_重点产品市场分析报告：冰箱销售额） | 报告文件现状：2 次尝试（第 1 次 `MODEL_EMPTY_RESPONSE`，第 2 次成功）；另一轮记录到 4 次尝试（前 3 次空响应） | 上游抖动；重试策略生效，`attempts` 逐次记入 `reports/faithfulness_v1.json` |
| 3 | `rb2-014`「2026年1月的订单有多少笔？」 | `FAILED_WITH_EVIDENCE` + `REPAIR_BUDGET_EXHAUSTED`，未返回 0 | **真实鲁棒性缺口**（同类的年份粒度空结果用例 rb-004 通过，月份粒度不通过） |
| 4 | `mf-001`/`mf-003`/`mf-006`/`mf-012`（跨文档核对） | 每题 4 次尝试全部 `MODEL_EMPTY_RESPONSE`（mf-012 末次为 `RAG_VERIFICATION_FAILED`） | **跨文档核对提示词下上游复现性空响应**，三轮中 mf-001/003 均复现；系统 fail-closed 未编造 |
| 5 | `mf-002` | 答案正确（3 个工作日）但只引用了 2 份指定文档中的 1 份 | 多文档**引用覆盖**短板（覆盖率 3/7） |
| 6 | 鲁棒性第 1 轮 5 条未通过 | 16/21；其中 4 条是**判据误标**（rb2-010 未列 `ABSTAINED`；rb2-011 把合法数据源 `chinook` 当越权源；rb2-012 预期 4xx 而实测稳定码 200；rb2-019 把「说明没有该列」当成泄漏） | 已在冻结集里改为实测契约（每条 note 写明改动原因），run1 原始日志保留在 `tmp/r3_robustness_run1.log`、报告快照 `tmp/r3_robustness_run1_report.json` |

## 6. 复现命令（真实命令 + 退出码）

```bash
cd "/Users/caicai/DataMind Agent"
git rev-parse --short HEAD                     # → e4e2ebf                exit 0
curl -s http://127.0.0.1:8000/openapi.json | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"   # → 0.39.0   exit 0

# 冻结集自检：每条引用片段必须逐字出自目标文档（只读元数据库）
.venv/bin/python scripts/eval_faithfulness.py --validate-only   # {"cases": 26, "problems": []}   exit 0
.venv/bin/python scripts/eval_robustness_v2.py --validate-only  # {"cases": 21, "problems": []}   exit 0
.venv/bin/python scripts/eval_multifile.py --validate-only      # {"cases": 12, "problems": []}   exit 0

# 真实端到端（串行，允许上游模型）
.venv/bin/python scripts/eval_faithfulness.py       # → reports/faithfulness_v1.json   exit 0
.venv/bin/python scripts/eval_robustness_v2.py      # → reports/robustness_v2.json     exit 0
.venv/bin/python scripts/eval_multifile.py          # → reports/multifile_v1.json      exit 1（有 upstream_error_cases）
.venv/bin/python scripts/eval_scale.py --kind profile_live --profile-live-repeats 3   # → scale_v2.json sections.profile_live  exit 0
.venv/bin/python scripts/eval_scale.py --kind profile --profile-live                  # 既有 profile + 新 profile_live（加法）

# 隔离回归（逐文件 pattern，未跑无参数全量）
.venv/bin/python scripts/test_isolated.py --pattern test_eval_faithfulness.py   # {"tests": 18, "failures": 0, "errors": 0, ...}  exit 0
.venv/bin/python scripts/test_isolated.py --pattern test_eval_robustness_v2.py  # {"tests": 15, "failures": 0, "errors": 0, ...}  exit 0
.venv/bin/python scripts/test_isolated.py --pattern test_eval_multifile.py      # {"tests": 9,  "failures": 0, "errors": 0, ...}  exit 0
.venv/bin/python scripts/test_isolated.py --pattern test_eval_scale_v2.py       # {"tests": 31, "failures": 0, "errors": 0, ...}  exit 0（既有文件，验证加法扩展未破坏）
```

三条隔离回归的 harness 汇总均含 `"metadata_isolated": true, "source_database_modified": false, "commit": "e4e2ebf"`。

## 7. 诚实边界

1. **不是官方测试集**：四套冻结集都是自建小样本（26/21/12/4 题；忠实度分母只有 21 个结论句），不能外推成官方准确率档位，也不自评任何分值。
2. **判定是确定性规则，不是语义蕴含模型**：`supported`/`contradicted`/`no_evidence` 由「引用片段原文 + 冻结允许值/术语锚点」机械判定；`contradicted` 的含义是「同题数值不在冻结允许集合内」（数值冲突），不是通用语义矛盾检测；`no_evidence` 也可能来自判定口径差异。模型判定本轮**未使用**（`model_judgment=null`）。
3. **上游抖动已单列**：`deepseek-flash` 出现 `MODEL_EMPTY_RESPONSE`（faith-012 报告现状 2 次尝试、另一轮 4 次尝试；多文档跨文档核对题在三轮里复现）。上游/模型错误各脚本最多重试 3 次后记为 `upstream_error`，**留在分母**，没有被剔除以美化数字。
4. **首字不是 token 级**：上游 `stream=false`，`first_token` 的口径是「首个 SSE trace 事件（首个可见输出）」，报告 note 已写明，不冒充模型首 token 时延。
5. **时延样本小**：n=6/12，p50/p95 用最近秩法，n<20 时 p95 等于观测最大值；单机单并发、无 CPU 绑定、无 cgroup 隔离，不是吞吐/容量承诺。
6. **写库范围**：回环免登录下 `POST /v1/chat`、`/v1/chat/stream` 会让服务端**幂等**确保 `user-local-admin` 审计行存在（`INSERT ... ON CONFLICT DO NOTHING`），只落 `datamind_meta` 身份表；**未对 `demo_retail`/`demo_industrial` 做任何写操作**，只读 SQL 仅用于取文档引用与切片原文。
7. **判据改动已披露**：鲁棒性冻结集在首轮实测后修正了 4 条误标判据（上表第 6 行），修正原因逐条写进 `data/evaluation/robustness_v2.json` 的 `expect.note`，首轮日志与报告快照保留在 `tmp/`。
8. **未做**（`not_attempted`，不属本 WP）：E1/E5/F4 当前版本独立 NL2SQL 集；E10 连续 5 轮以上准确；F1 的 P2–P4 文档侧绑定；F14/F15 加分；多模态视觉识别。

## 8. 评分联动（不构成自评）

| 官方行 | 本轮补到的证据 | 能否计入（保守判断） |
|---|---|---|
| 初赛性能 2. RAG 忠实度（10；≥70%→6、≥80%→8、≥90%→10） | 逐结论三态标注：19/21=0.9048（另一轮 20/21=0.9524）、矛盾 0、无依据 2/21；拒答 5/5 | 数值区间落到 ≥90% 档，但官方测试集未提供、分母仅 21、轮间波动 → **只主张「≥80% 档有独立证据」**，10 分档不主张 |
| 初赛功能 2. RAG 单文档（10；≥10 用例，每题 1 分） | 26 题单文档冻结集 + 逐句判定 + 引用覆盖率 17/21 | 满足「≥10 用例」的形式要求；分值由评委/官方测试集判定 |
| 初赛优化 2. 鲁棒性（10；5/7/10）+ 决赛性能 4.（5；2/4/5） | 21 题/12 类真实端到端 20/21=0.9524、0 个 5xx、越权 3 类稳定拒答、注入 2/2 无泄漏、超长/空输入 422 | 支撑「基础容错/鲁棒性良好」的材料；「优异/极端条件」仍缺（rb2-014 空结果失败、无并发与低质量极端矩阵） |
| 初赛性能 3.（5）+ 决赛性能 3.（5） | 六段端到端分段（含模型 1674.2/3238.6 ms、首字 66.0/80.2 ms、完整 1790.9/3349.1 ms），模型段不再为 null | 「至少两种/三种规模 + 分段实测」的缺口被补上；复杂度/瓶颈/优化由既有无 WP 材料承担 |
| 决赛性能 2. 多文档（10；≥70%→5、≥80%→7、≥90%→10） | 12 题固定多文档集三轮：正确 3/9–5/9、引用覆盖 1/7–3/7、拒答 3/3 | **未达 70%，不能计入任何档位**；本轮把 F5 从「无实测」变成「有实测且未达标 + 失败清单」 |
