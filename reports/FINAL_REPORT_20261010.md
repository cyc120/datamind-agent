# DataMind Agent 多智能体提升执行 · 总报告

日期：2026-10-10（北京时间）　基线提交：`e4e2ebf`（工作区含全部改动，**未提交**）　API：0.39.0
执行方式：四轮多智能体编排（并行实现 → 独立复查 → 修复轮 → 收口轮），三轮独立复查，主协调者统一收敛全量回归。
详细版：[第三轮收口轮总报告](round1_improvement_report_20261010.md)（九节，逐项官方档位判定）· [第三轮独立复核](review_round3_final.md) · [提升计划](improvement_plan_20261010_competition.md) · [审计与估分](project_audit_20261009_competition_e4e2ebf.md)

> **本报告不是官方成绩**，不含任何自评分。所有「档位」判断都是条件性的可计入性判断。
> 纪律：凡数字必附来源文件与命令；分子分母同给；拒答/超时/上游错误单列但不移出分母；
> **stub/离线结果一律标注，绝不说成模型成绩**；没做到的写 `not_attempted`；被证伪的数字按撤回处理并留档。

---

## 1. 三句话结论

1. **做了什么**：按提升计划把十个工作包全部落地，并额外补出计划外证据；四轮里第二轮修掉 8 条安全/兼容发现，收口轮补出跨源规范语料、逐结论忠实度、鲁棒性 v2、多文档固定集、端到端含模型分段时延、三臂绑定消融，并把澄清契约从评测脚本下沉到生产包。
2. **真实拿到什么**：**全量隔离回归 1664/1664、0 失败、0 错误、exit 0**（四轮从 1497/6 → 1578/4 → 1664/3 → 1664/0 收敛）；跨源规范集 **28/28**（P2 12/12、P3 9/9、P4 7/7）；真实模型侧单文档忠实度 **19/21**、鲁棒性 **20/21**、跨源 P1 **5/6**、多文档 **5/9**（未达标，如实保留）；三臂消融错误绑定率 **plain 0.800 → full 0.000**（stub，非模型）。
3. **还差什么**：**F1（多源融合）仍不能计入 6 分档**——4 种结构里 3 种 `model_calls=0`（SQL 由条款确定性编译、文档侧是自建语料），且余 1 处引擎对象级门控未做；**F5 多文档 5/9 未达 70% 档**；上传自动索引仍未接入（`vector_index=deferred`），新文件问答闭环停在"解析→复核→定位"；干净环境演练与 ZIP 实包未做；**运行中的 8000 服务仍是旧构建**（`/health` 无 `cross_source` 能力，未重启）。

---

## 2. 工作包状态一览（16 个 WP）

| WP | 状态 | 交付物（关键） | 关键数字 |
|---|---|---|---|
| WP0 差距台账 | done | [SCORE_GAP_LEDGER.md](../docs/SCORE_GAP_LEDGER.md) | 官方评分行抄录 27/27；映射表过期 9/9 |
| WP1a 评测登记 | partial | `data/evaluation/registry/`、`scripts/eval_registry.py` | check 3/3 一致；新增子集题 `not_attempted` |
| WP1b 基线复测 | done | [baseline_current.json](baseline_current.json) | **28/30**（nl2sql 13/14、rag 11/12、robustness 4/4），上游错误 0，误拒 1 |
| WP2 跨源引擎（离线） | done | `datamind/{binding,evidence_graph,cross_source}.py`、migration 019 | 离线 21/21、断言 161/161、`model_calls=0` |
| WP2-R2 跨源真实链路 | partial | [cross_source_live.json](cross_source_live.json) | P1 **5/6**（真实模型 `model_calls=17`）；P2/P3/P4 当时 0/5 |
| WP3 三臂绑定消融 | done | [binding_ablation.json](binding_ablation.json)、`datamind/binding_eval.py` | 40 例陷阱集；错误绑定率 plain 32/40 → full **0/40**；误修复 0/9 |
| WP4 B3 澄清闭环 | partial | [clarification_v1.json](clarification_v1.json) | detector 18/18、deployed 4/18、不必要澄清 **0/7 与 0/25** |
| WP5a B2 可扩展提取 | done | `data/evaluation/extensible_field_v1/` | 120/120；三档示例极差 **0.0 pp**；引擎改动 0 行 |
| WP5b B8 复杂度 | done | `datamind/complexity.py`、[complexity_v1.json](complexity_v1.json) | 26 份材料 0 解析失败；ρ 0.641（两次运行不复现，贴门槛） |
| WP6 B4 Schema Linking v2 | done（未过门槛） | [schema_linking_v2.json](schema_linking_v2.json) | 46 表；表 65/88=73.9%、列 48/56、路径 25/25、拒绝 9/9、干扰误选 0/32 |
| WP7 多轮上下文 | partial | `datamind/frame.py`、[multi_turn_v1.json](multi_turn_v1.json) | offline 61/61、整段 10/10、越权拒绝 2/2；**live 58/61** |
| WP8 效率与伸缩性 | done | [scale_v2.json](scale_v2.json)、[PERFORMANCE.md](../docs/PERFORMANCE.md) | SQL 3 档；并发 29.4→196.0 次/秒；索引优化 ×7.35；**含模型 p50 1674 ms** |
| WP9 新文件问答闭环 | partial | `data/qa_samples/`（11 份/6 格式） | 样本 23/23 断言；**入库 1/11、自动索引 `not_done`** |
| WP10 交付材料 | done | `交付/01–04`、[DELIVERY_MANIFEST.json](../交付/DELIVERY_MANIFEST.json) | 6 件交付物、页数 13/12/4/20、结构检查 4/4、PPT 匿名命中 0 |
| **R3 跨源规范语料** | done | `data/knowledge_sources/`（14 份）、[cross_source_norm_live.json](cross_source_norm_live.json) | **28/28**（P2 12/12、P3 9/9、P4 7/7），`model_calls=0` |
| **R3 评分缺口证据** | done | [faithfulness_v1.json](faithfulness_v1.json)、[robustness_v2.json](robustness_v2.json)、[multifile_v1.json](multifile_v1.json) | 忠实度 19/21、鲁棒性 20/21、多文档 5/9 |
| **R4 引擎缺陷与收敛** | done | `datamind/evidence_graph.py`、[r4_engine_defects.md](cards/r4_engine_defects.md) | 对称边 2/9→**9/9**；假冲突消除；**回归 1664/0** |

---

## 3. 真实实测结果（按可核验程度分三类）

### 表 A｜离线确定性评测（可独立复跑，不含模型）

| 评测 | 结果（分子/分母） | 来源 |
|---|---|---|
| 跨源规范语料 P2/P3/P4 | **28/28**（P2 12/12、P3 9/9、P4 7/7）；`model_calls=0` | [cross_source_norm_live.json](cross_source_norm_live.json) |
| 跨源离线（原始 21 例） | **21/21**、断言 161/161 | [cross_source_offline.json](cross_source_offline.json) |
| 三臂绑定消融 | plain 8/40 → full **40/40** 绑定准确；错误绑定率 plain 32/40 → full 0/40 | [binding_ablation.json](binding_ablation.json) |
| 语料字节确定性 | 修前 12 次 **11 true/1 false** → 修后 **12/12 true** | [r4_determinism_delivery.md](cards/r4_determinism_delivery.md) |
| Schema Linking v2 | 表 65/88、列 48/56、路径 25/25、拒绝 9/9、干扰误选 0/32 | [schema_linking_v2.json](schema_linking_v2.json) |
| B2 可扩展提取 | 120/120；无示例/3 示例/10 示例极差 0.0 pp | [extensible_fields_v1.json](extensible_fields_v1.json) |
| 复杂度评分 | 26 份材料、解析失败 0；ρ 0.641（**两次运行不一致，仅量级可核**） | [complexity_v1.json](complexity_v1.json) |
| 多轮（决策层） | 逐轮 61/61、整段 10/10、可追溯 46/46 | [multi_turn_v1.json](multi_turn_v1.json) |
| 效率与伸缩 | SQL 3 档；并发 1→8 = 29.4→196.0 次/秒、成功率 1.0；q4 索引 4.240→0.577 ms | [scale_v2.json](scale_v2.json) |

### 表 B｜真实模型调用（可复现但受上游波动，**未由复查者重跑**）

| 评测 | 结果（分子/分母） | 说明 |
|---|---|---|
| 基线复测（30 题） | **28/30**；上游错误 0；误拒 1/30 | [baseline_current.json](baseline_current.json) |
| 单文档忠实度（逐结论） | 支持 **19/21**、矛盾 0、无依据 2；拒答 5/5；引用覆盖 17/21 | [faithfulness_v1.json](faithfulness_v1.json) |
| 鲁棒性 v2（21 题/12 类） | **20/21**；5xx/内部错误 0 | [robustness_v2.json](robustness_v2.json) |
| 多文档（固定集） | 答案正确 **5/9**（55.6%，**未达 70%**）；引用覆盖 3/7；拒答 3/3 | [multifile_v1.json](multifile_v1.json) |
| 跨源 P1 live | **5/6**；`model_calls=17`；fail-closed 15/15 | [cross_source_live.json](cross_source_live.json) |
| 多轮 live | 逐轮 **58/61**（3 条失败全在指代复问） | [multi_turn_v1.json](multi_turn_v1.json) |
| 端到端含模型分段 | model p50 **1674.2** / p95 3238.6 ms（n=12）；首字 66.0/80.2 | [scale_v2.json](scale_v2.json) `profile_live` |

### 表 C｜stub 模拟（`model_calls=0`，**不是模型成绩**）

| 项 | 值 | 边界 |
|---|---|---|
| 三臂消融 full 臂 | 错误绑定率 0/40、冲突 P/R 25/25、误修复 0/9 | 同源设计结果 + 确定性 stub；**不能主张领先性** |
| 跨源规范集 P2/P3/P4 | 28/28 | SQL 由条款确定性编译，非 NL2SQL；文档侧为自建语料 |

---

## 4. 官方评分项：现在能计入什么（逐项判定）

| 项 | 档位要求 | 本轮证据 | 判定 |
|---|---|---|---|
| E1/E5 单表与执行准确率 | ≤10；档位 70/80/90% | 基线 30 题 **28/30**（单表子集更小） | 有实测，**不外推** ≥90% 档 |
| E2 单文档 RAG | 10 | rag 11/12；忠实度 19/21 | 有实测；拒答与上游错误保留在分母 |
| E3 可解释性 | 5 | SQL/引用/定位/证据图/回放齐备 | 材料齐备 |
| E4 交互 Demo | 5 | 前端跨源/澄清面板已加；**服务未重启** | 材料齐备，运行态待重启验证 |
| E6 忠实度 | 10 | 逐结论三态标注 19/21、矛盾 0 | **推进最大的一项**：从「无独立标注」到有 |
| E7/F6 效率 | 5 / 5 | 复杂度分析 + 3 档规模 + 并发 + **含模型端到端** + 优化前后对照 | 证据齐备度最高的一项 |
| E8/F8 中级任务 | 15 / 15 | B3（澄清 18/18、不必要 0/7）、B4（46 表复核**未过 90% 门槛**）、B2（120/120、引擎改动 0 行） | **三项可申报**，B4 需附预算敏感性说明 |
| E9/F7 鲁棒性 | 10 / 5 | 21 题/12 类 **20/21**；越权稳定拒答 | 有实测 |
| E10 多轮 | 5 | offline 61/61、live 58/61 | 有实测；帧未进提示词、前端会话未接入 |
| E11/F10 文档 | 10 / 5 | 2 DOCX + 3 PDF + PPT，结构检查 4/4 | 齐备 |
| **F1 多源融合** | **6 / 8 / 10** | P1 5/6（真实模型）；P2/P3/P4 28/28（**`model_calls=0`**） | **仍不能计入 6 分档** |
| F2 意图与澄清 | 4 / 7 / 10 | 澄清 detector 18/18、不必要 0/7；live 4/18 | 部分 |
| F3 可解释 | 5 | 绑定字段与冲突双方可展示 | 材料齐备 |
| F4 复杂 NL2SQL | 10 | 无公开基准；自建复杂子集小 | 无法判定 |
| F5 多文档/多模态 | 10 | **5/9（55.6%）** | **未达 70% 档** |
| 加分 F14/F15 | ≤20 | 门槛在 stub/自建集下满足（错误绑定率 0.800→0.000） | **只能按"实用创新"下限叙事** |

---

## 5. 独立复查结论（三轮汇总）

| 轮次 | 复查范围 | confirmed | refuted / 风险 | 处理 |
|---|---|---:|---|---|
| 第一轮 | 123 条数字主张 | 107 | refuted 2（`rb-004` 库内事实、`cross_source_offline` 时效性）；风险高 2/中 4/低 5 | 数字撤回或更正；安全项第二轮全修 |
| 第二轮 | 修复轮 4 项 | — | 高 2（空 allow-list 越权、`ROUTE_UNSUPPORTED` 断言必然红）已修；中 4（时区、集合白名单、019 注释不实、别名降级）已修 | H1/M1/M2/M3/M4 逐条前后对照 |
| 第三轮 | 10 条确定性命令 + 5 份模型报告 | 19 | refuted 1（语料 `deterministic: true` 不稳定，17 次 4 次假） | 根因定位（`/ID` 两种形态）并修复至 12/12 |

**复查发现并已修复的真实缺陷**（都曾是冻结证据，不是猜测）：

1. 跨源**空 allow-list 越权**（`database_sources=[]` 被当成"不限制"）→ 双层拒绝。
2. `None`（未给范围）被当成空列表 → 默认范围被误拒（这是 1578 分母里唯一真实回归）。
3. **对称边单向查找**：`edge_between` 反查返回 None，`threshold_checks` 引擎 2/9 vs 独立重算 9/9 → 修后 9/9。
4. **冲突键缺记录作用域**：不同测量互相假冲突（`norm-p3-09` 2 条假 `value_conflict`）→ 修后 0 条，真冲突仍检出。
5. **语料字节不确定**（PyMuPDF 两种 `/ID` 形态）→ 改为生成时显式写入确定性 `/ID`。
6. `web/style.css` 缺 `.clarification` 样式、`index.html` 缓存版本失同步、SSE 与落库澄清键不一致 → 已修。
7. 生产服务请求期 `from scripts.eval_clarification import …` → 契约下沉到 `datamind/clarification.py`，脚本只做 re-export。

---

## 6. 失败样本与未完成项（不许含糊）

| # | 未完成 | 阻塞原因 | 最小下一步 |
|---|---|---|---|
| 1 | **F1 不能计入 6 分档** | P2/P3/P4 `model_calls=0`（SQL 由条款编译）、文档侧是自建语料而非公开 KB | 语料发布进真实知识库 + P2 走真实 NL2SQL；补 `threshold_checks` 对象级门控（4–5 人日） |
| 2 | **F5 多文档 5/9** | 4 题上游 `MODEL_EMPTY_RESPONSE`；`mf-002` 只引到 2 份中的 1 份 | 记录模型标识、重跑两轮取稳定区间；补引用覆盖策略 |
| 3 | 上传自动索引未接入 | `ingestion/upload_pipeline.py` 恒为 `vector_index: deferred` | 接入切片/索引，再跑「上传→提问→定位」全链 |
| 4 | 运行态未更新 | 未重启 8000 服务（避免打断你正在用的会话） | 重启后 `/health` 应出现 `cross_source` 能力，再验前端跨源卡片 |
| 5 | 干净环境演练与 ZIP | 需先停服/换库，风险高 | 空库 + 仅依赖锁启动一次，跑通演示主线后再 `build_delivery_zip.py` 实包 |
| 6 | migration 019 未对 `datamind_meta` 执行 | 属生产库 DDL，需你确认 | 已用一次性临时库验证 fresh 顺序；授权后在真实库执行 |
| 7 | 三份模型报告未记录模型标识 | 本轮未纳入 | 报告加 `model`/`base_url` 字段 |
| 8 | Schema v2 未过 90% 门槛 | 全局 8 候选预算截断 30 条 | 放宽预算到 32 → 表召回升 96.6%（已实测） |
| 9 | 复杂度 ρ 不可复现 | 贴门槛（0.641 vs 0.5639），样本 n=12 且有离群点 | 固定材料集与预热策略后重测 |

---

## 7. 回归与安全状态

| 项 | 值 | 来源 |
|---|---|---|
| **全量隔离回归** | **1664 执行 / 1664 通过 / 0 失败 / 0 错误 / exit 0** | `tmp/regression_final_d.log`、[latest_regression.json](latest_regression.json) |
| 收敛轨迹 | 1497/6 → 1578/4 → 1664/3 → **1664/0** | 四轮日志 `tmp/regression_final_{a,b,c,d}.log` |
| 分母文档 | PROGRESS.md / README.md 均为 **1664**，与记录一致 | `test_status_docs_sync` 6/6 绿 |
| 修改的既有文件 | 16 个（`datamind/agent.py`、`api.py`、`sql_engine.py`、`industrial_rules.py`、`evidence_graph.py`、`cross_source.py`、`binding.py`、`eval_scale.py`、`eval_schema.py`、`eval_clarification.py`、`web/app.js`、`web/style.css`、`web/index.html`、`PROGRESS.md`、`README.md`、`start_local.sh`） | `git status --porcelain` |
| 删除 | **0** | 同上 |
| 遗留风险 | 阻断 0 / 高 0 / 中 3 / 低 3 | [review_round3_final.md](review_round3_final.md) |
| 未提交 | 全部改动仍在工作区（16 M + 89 ??），**未做任何 git 写操作** | 按你的仓库纪律 |

关于 `scripts/start_local.sh`：它的改动是本轮之前就存在的（审计已记录），我全程未触碰。

---

## 8. 交付物

| 文件 | 内容 | 页数/规模 |
|---|---|---|
| [01_设计报告](../交付/01_设计报告_DataMind_Agent.docx) + [PDF](../交付/01_设计报告_DataMind_Agent.pdf) | 官方设计报告（第 1–2 页核心亮点预览页） | 13 页 |
| [02_技术实现说明书](../交付/02_技术实现说明书_DataMind_Agent.docx) + [PDF](../交付/02_技术实现说明书_DataMind_Agent.pdf) | 架构/NL2SQL/RAG/多源融合/可解释/中级任务/评测 | 12 页 |
| [03_答辩演示](../交付/03_答辩演示.pptx) | 答辩 PPT（匿名检查命中 0） | 20 张 |
| [04_突破与创新性自述](../交付/04_突破与创新性自述.pdf) + [DOCX](../交付/04_突破与创新性自述_DataMind_Agent.docx) | 单一贡献：版本化跨源证据绑定 + 可控修复 | 4 页（≤5） |
| [DELIVERY_MANIFEST.json](../交付/DELIVERY_MANIFEST.json) | 逐文件 sha256/页数/生成命令 + 证据索引 + 未完成项 | 46 KB |
| [DELIVERY_CHECKLIST.md](../docs/DELIVERY_CHECKLIST.md) | 官方提交物逐项状态 | — |
| [提升计划](improvement_plan_20261010_competition.md) | 十工作包、验收判据、评分联动矩阵 | 364 行 |
| [总报告（详细版）](round1_improvement_report_20261010.md) | 九节，含官方逐项档位判定与三轮复查 | — |
| 结论卡 24 张 | `reports/cards/*.md`（每 WP 一页：分子分母/失败样本/复现命令/诚实边界） | 24 |

---

## 9. 下一步（按"距得分最近"排序）

| 顺序 | 动作 | 验收判据 | 工作量 |
|---|---|---|---|
| 1 | **重启服务并验收跨源前端** | `/health` 出现 `cross_source`；前端跑通 P1 一条并显示证据图与绑定字段 | 0.5 人日 |
| 2 | **把 F1 从"不能计入"推到"可计入 6 分"** | 语料发布进真实知识库 + P2 走真实 NL2SQL live，≥3 种结构各 ≥3 条真实 `answered_and_verified`、`model_calls>0` | 4–5 人日 |
| 3 | **上传自动索引** | 新文件走完 上传→索引→提问→引用点击，0 切片仍被拒 | 2–3 人日 |
| 4 | **F5 多文档达标** | 固定集 ≥70% 且引用覆盖全部指定文件；记录模型标识 | 1.5–2 人日 |
| 5 | **干净环境演练 + 实包** | 空库 + 依赖锁启动成功、演示主线跑通、ZIP 清单逐项勾选 | 1–2 人日 |

> 若时间只够做一件事：做第 1 项（0.5 人日），它能让已经做出来的跨源与澄清能力在评委面前**真的可见**；
> 第 2 项是唯一能再撬动 6 分的单项，但它需要写库授权。

---

## 10. 诚实边界（本报告不能用来声称什么）

1. 本报告**不含任何官方得分**；第 4 节的"档位"是可计入性判断，不是评委给分。
2. **1664 是测试分母，不是准确率**；四轮之间的红色记录保留在日志与复查报告里，不回填。
3. 表 C 的 stub 结果**不是模型成绩**；表 B 的模型结果**未经独立重跑**，且是在**旧构建服务**上测的。
4. F1 的 28/28 **不能**说成"模型答对了 28 条跨源问题"，也不能据此主张技术领先性。
5. 所有评测集均为**自建/合成冻结集**，不是官方测试集，不能外推泛化能力。
6. 本轮**未做**干净环境演练、ZIP 实包、运行态重启、真实库 DDL 执行与逐页人工视觉检查。
