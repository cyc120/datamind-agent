# R4 范围语义回归修复 · 结论卡

| 字段 | 内容 |
|---|---|
| **WP** | 第四轮（最后收口）：修掉 1578 分母里**唯一一条真实回归**（非文档漂移）——跨源路径把「请求未显式给 `scope`（`None`）」当成「空 allow-list」拒绝，返回范围码 `SOURCE_NOT_ALLOWED` 而不是证据码 |
| **日期** | 2026-10-10（北京时间；隔离运行的 `ran_at` 为 `2026-10-10T00:55Z–01:00Z`） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`，只读，exit 0）。本轮**未做任何 git 写操作**；工作树内还有同轮其他 WP 的未提交改动（`git status` 显示 `datamind/frame.py`、`tests/test_security_round2.py` 等为 untracked，那是第三轮遗留，不是本卡引入） |
| **API 版本** | `0.39.0`（`.venv/bin/python -c "from datamind.api import app; print(app.version)"` → `0.39.0`）。**未改版本号、未改路由** |
| **问题** | 第二轮为修 H1（空 allow-list 越权）在 `datamind/agent.py::_run_cross_source` 加了范围门 `if not sources or any(s not in DATABASE_SOURCES ...)`。该门在 SQL 路径上读的是**已归一化**的 `sources`（`None` 早已被换成 `DATABASE_SOURCES`），但在跨源路径上读的是**原始请求值**，于是 `None`（=「没给范围」）与 `[]`（=「什么都不许」）被当成同一件事。后果：`tests/test_agent_api.AgentAPITest.test_cross_source_route_is_explicitly_unavailable`（无 `scope` 的跨源问句）在任何证据适配器启动**之前**就被拒绝，得到 `SOURCE_NOT_ALLOWED`，触发 1578 分母里唯一非文档漂移的失败 |
| **做法** | 只改被指定的文件：`datamind/agent.py`（两处守卫 + 注释）、`tests/test_agent_api.py`（仅新增一条「范围码不得当作证据码」断言，原断言保留）、`tests/test_security_round2.py`（既有 H1 断言**逐字未动**，仅加注释 + 新增 1 条双向断言）、新增 `tests/test_scope_default_and_empty.py`（9 项）。语义改为三段：**`None` → 默认范围（`DATABASE_SOURCES` / `knowledge.COLLECTIONS`）允许**；**显式 `[]` → 拒绝**（`SOURCE_NOT_ALLOWED` / `COLLECTION_NOT_ALLOWED`，H1 不动）；**未注册名 → 拒绝**。空集合同样按「显式空 = 拒绝」处理，与 `KnowledgeRetriever.retrieve`（对空集合本来抛 `COLLECTION_NOT_ALLOWED`）和工业路径 `set(collections) != {'industrial_demo'}` 一致。`datamind/frame.py` **未改**：`scope_check` 收到的是调用方已归一化的默认允许集，行为本就正确（用「无 scope + history」新用例实证） |
| **数据与集** | ① 跨源问句：`查询销售额前三产品并结合市场分析报告说明原因`；② 单测用**注入式最小证据夹具**（1 行 SQL 证据 + 1 个文档块，取自 `tests/test_security_round2.py::cross_evidence`，**非评测集**，不据此声称准确率）；③ API 级用例走**真实只读链路**（克隆 `datamind_meta` + `demo_retail` 只读 + 真实文档检索），本环境 `model_settings_copied=false`，故 SQL 证据侧稳定得到 `MODEL_KEY_MISSING` → 无 SQL 证据；④**全程 0 次评测集运行、0 次写演示库、未重启 8000 服务、未跑 `start_local.sh`/`ingest.py`、未跑无参数全量** |
| **结果（分子/分母）** | 改前复现：`test_agent_api.py` **4/5**（exit 1，失败 1）。改后（均 exit 0）：`test_agent_api.py` **5/5**、`test_cross_source.py` **50/50**、`test_security_round2.py` **32/32**（31 条既有 H1/M1/M2/M3/M4/L2/L5 + 1 条新增）、`test_frame_multi_turn.py` **30/30**、新增 `test_scope_default_and_empty.py` **9/9**。旁证 pattern 全绿：`test_rag.py` 11/11、`test_industrial.py` 11/11、`test_cross_source_norm.py` 26/26、`test_binding_eval.py` 33/33、`test_clarification_flow.py` 23/23、`test_clarification_single_source.py` 13/13（均 exit 0）。四条必需 pattern 合计 **117/117**；含新增与旁证合计 **243/243** |
| **失败样本** | ① 改前原文（保留）：`AssertionError: 'SOURCE_NOT_ALLOWED' not found in {'CROSS_SOURCE_EVIDENCE_INSUFFICIENT','CONFLICT_UNRESOLVED','DOC_BINDING_MISSING'} : 跨源分支必须返回证据图引擎的稳定错误码（旧码 ROUTE_UNSUPPORTED 已退役）`，位置 `tests/test_agent_api.py:123`，`Ran 5 tests in 1.036s / FAILED (failures=1)`；② 改前六种输入探针（`/v1/chat` 实测，原样）：`scope=null → FAILED_WITH_EVIDENCE/SOURCE_NOT_ALLOWED`（错）、`database_sources=[] → SOURCE_NOT_ALLOWED`（对）、`['demo_retail'] → ABSTAINED/CROSS_SOURCE_EVIDENCE_INSUFFICIENT`（对）、`['nope'] → SOURCE_NOT_ALLOWED`（对）、`document_collections=['public_kb'] → COLLECTION_NOT_ALLOWED`（对）、`document_collections=[] → ABSTAINED/CROSS_SOURCE_EVIDENCE_INSUFFICIENT`（漏拒）；③ 改后同一探针：`null → ABSTAINED/CROSS_SOURCE_EVIDENCE_INSUFFICIENT`、`[] → SOURCE_NOT_ALLOWED`、`['demo_retail'] → ABSTAINED/CROSS_SOURCE_EVIDENCE_INSUFFICIENT`、`['nope'] → SOURCE_NOT_ALLOWED`、`['public_kb'] → COLLECTION_NOT_ALLOWED`、**`[]` 集合 → COLLECTION_NOT_ALLOWED**；④ 改前失败原文与六种输入探针的原始输出已**逐字保留在本卡**（失败用例本身保留在仓库，未被删除或改写语义）；改后四条 pattern 的完整日志在 `/tmp/r4_*.log`，未隐藏、未用过滤规避 |
| **复现命令** | 基线：`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`；失败复现（改前）：`.venv/bin/python scripts/test_isolated.py --pattern test_agent_api.py`；四条验收：同形分别 `--pattern test_agent_api.py` / `test_cross_source.py` / `test_security_round2.py` / `test_frame_multi_turn.py`；新增契约：`--pattern test_scope_default_and_empty.py`；其余旁证 pattern 同形（日志 `/tmp/r4_*.log`）。**未跑无参数全量**（硬性禁令） |
| **诚实边界** | ① 本轮**没有跑无参数全量隔离回归**，所以「全量绿」不由本卡背书；权威分母 1578 是主协调者给的改前事实；② 我**新增了 10 项测试**（新文件 9 + `test_security_round2.py` 1），因此下一轮全量的分母应为 **1578 + 10 = 1588**（预期 1588/1588：原 4 条失败里 3 条是文档漂移、1 条即本条已修），但**这是我推算、不是我验证的数值**；`PROGRESS.md`/`README.md` 的 1497 → 新分母由主协调者统一同步；③ `reports/latest_regression.json` 被本轮 pattern 运行按既有设计覆盖（**我没有手工编辑**）；且同 checkout 内有并发兄弟 WP 在跑测试（观测到 `-m unittest tests.test_cross_source_norm -v` 进程），该文件在并行下会被互相覆盖，所以证据以各 pattern 的独立日志为准，最终由主协调者的全量运行收敛；④ 跨源「无 scope」的最终结果是 `ABSTAINED/CROSS_SOURCE_EVIDENCE_INSUFFICIENT`，这**只证明范围放行、证据不足**（克隆库未复制模型密钥），**不声称**跨源在该环境能给出答案；⑤ 未做渗透/并发测试，`[]` 的拒绝证据来自计划层守卫 + `cross_source.plan`/`live_sql_evidence` 的既有断言（三层，未删任何一层）；⑥ 未在浏览器/8000 服务上做端到端手工验证，也未重启任何服务；⑦ 未改 `datamind/frame.py`，只验证了其调用契约，未改其语义 |
| **评分联动** | **F13（工程纪律与安全性）/ E9 / F7（鲁棒性与权限边界）**：把「未给范围」与「越权」拆开，范围门恢复与 SQL 路径同形，空 allow-list 拒绝仍是硬约束（三层断言未削弱）；**F1 / E1 / E5（跨源与证据链）**：跨源无 scope 重新走完证据图引擎并给证据码，`SOURCE_NOT_ALLOWED`（范围问题）与 `CROSS_SOURCE_EVIDENCE_INSUFFICIENT`（证据不足）不再混用，fail-closed 叙事与运行时一致；**E2/E3（可复现）**：六种输入的前后对照 + 四条 pattern 退出码齐备，回归可一键重跑 |

---

## 0. 一句话结论

**根因是「原始请求值 vs 归一化值」读错一层：`None` 被 `if not sources` 当成 `[]`。把语义改成 `None→默认范围 / []→拒绝 / 非法值→拒绝` 后，唯一真实回归消失，H1 的空列表拒绝一条未削，范围码与证据码各归其位。**

---

## 1. 根因定位（自己复现，非照抄猜测）

| 位置 | 判定 |
|---|---|
| `datamind/agent.py` `_run_cross_source`（改前 `:494`） | **真凶**。门是 `if not sources or any(s not in DATABASE_SOURCES ...)`，而调用方 `run()` 传的是**原始** `database_sources`（无 scope 时为 `None`） |
| `datamind/agent.py` `run()` SQL 路径（`:399` 附近） | **无辜**。它先做 `sources = database_sources if database_sources is not None else list(DATABASE_SOURCES)`，同样的门从来看不到 `None` |
| `datamind/frame.py` `scope_check`（`:552`）/ `resolve_turn`（`:606`） | **无辜**。`resolve_turn(allowed_sources=sources)` 收到的已是归一化默认集，继承提案只要在默认范围内就通过；`allowed_collections=None` 表示默认集合集。用新用例「无 scope + history」实证 |
| 「跨源真的缺证据」？ | **不是**。改前 `SOURCE_NOT_ALLOWED` 在**任何适配器启动之前**返回，`cross_source_result` 都不存在，不可能是证据图引擎给的码 |

改后错误码契约（写进卡片与测试）：

* `SOURCE_NOT_ALLOWED` / `COLLECTION_NOT_ALLOWED` = **范围**判定（显式空 allow-list、未注册源/集合），在计划层产生；
* `CROSS_SOURCE_EVIDENCE_INSUFFICIENT` / `DOC_BINDING_MISSING` / `CONFLICT_UNRESOLVED` = **证据**判定，由证据图引擎在范围被接受之后产生。两者不得混用。
