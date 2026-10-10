# 结论卡：官方评分项差距台账

**WP**：WP0 差距台账（官方评分项 × 当前仓库证据 × 本轮计划）
**日期**：2026-10-09 22:19 CST（机器 `date`；计划文件自署 2026-10-10，见台账 §3.3 P8）
**基线提交**：`e4e2ebf`（`git rev-parse --short HEAD`，只读）
**API 版本**：`0.39.0`（`datamind/api.py:197`）

## 问题

官方评分表（PDF p.10–14）与仓库内两份自评文件口径不一致、且映射表含过期事实，导致「本轮该做什么、能拿几分」无法作为可靠计划输入：

1. `docs/OFFICIAL_REQUIREMENTS_MAPPING.md` 存在 **9 处**过期/错位，最严重的是结尾 L143「E10 无会话字段、E7/F6 无多规模材料」与本文件 L137 自相矛盾（审计 L187 已点名）。
2. 映射表拒绝自评（L139），审计却给 69/48 规划分——两套口径并行，易被误用为官方得分。
3. 计划自身有**三处档位错配**（E5 目标 9–10、E9 目标 8–9、F9 目标 7），而官方档位分别只有 6/8/10、5/7/10、6/8/10；且 §0 的 85–87 与 §2.1 逐项目标相加（95）不符。
4. 计划 §WP2 称「新增 migration 019 建证据图」，但 `evidence_nodes`/`evidence_edges` 已由 `deploy/init/02_metadata.sql:252-269` 建立并在运行。

## 做法

- 用 `pymupdf` **只读**提取赛题 PDF 全文，逐行抄录 p.10–14 的初赛 12 行、决赛 13 行、加分 2 方向的分值与档位原文（不改写分值；未命名 E/F 编号，内部编号在台账中显式声明为仓库自造）。
- 用只读命令逐条复核两份自评文件的事实主张：`git ls-files`、`grep`、`/openapi.json`、`/health`、PostgreSQL 只读 `SELECT count(*)`、Qdrant `points_count`、`reports/*.json`。
- 新建 [docs/SCORE_GAP_LEDGER.md](../docs/SCORE_GAP_LEDGER.md)：27 行官方评分项逐项台账 + 差异表 + 前 5 项 + 不可能项。
- **不写业务代码、不跑评测、不改数据、不动禁改文件。**

## 数据与集

| 来源 | 版本/规模 | 只读实测 |
|---|---|---|
| 官方赛题 PDF | 2026 年 5 月版，14 页 | 评分第 10–14 页，全文提取至 `tmp/wp_gaps_official_pdf_full.txt` |
| `evaluation_cases` | 现有评测用例 | `nl2sql`=14、`rag`=12、`robustness`=4（只读 SQL） |
| 知识元数据 | `document_assets` / `chunks` / `document_facts` | 29（indexed 27 + parsed 2）/ 169 / 23 |
| 向量库 | Qdrant `datamind_knowledge` | points=169 |
| 证据图（已存在） | `evidence_nodes` / `evidence_edges` | 1456 / 1359 |
| 既有报告 | `nl2sql.json`、`rag.json`、`schema_linking.json`、`robustness.json`、`scale_v1.json`、`latest_regression.json` | 14/14；10/12；31/31、28/28、4/4、6/6；4/4；tests=1297 failures=0 |

均为**自建/合成**集，非官方测试集。

## 结果（分子分母）

| 项 | 分子/分母 | 说明 |
|---|---|---|
| 官方评分行抄录覆盖 | **27/27** | 初赛 12/12 + 决赛 13/13 + 加分 2/2；分值加总核对 初赛=100、决赛=100、加分≤20 |
| 映射表过期/错位定位 | **9/9** | M1–M9，均附行号与只读实测原值 |
| 映射表↔审计口径差异定位 | **5/5** | A1–A5，判定为「口径不同，非事实冲突」 |
| 计划内部不一致定位 | **9/9** | P1–P9（含 3 处档位错配、1 处证据图表述不完整） |
| 已复核为「一致」的关键数字 | **5/5** | test_multi_turn 33 项、schema 31/31·28/28·4/4·6/6、工业 13/13、PDF 14 页、用例 14/12/4 |
| **本轮新增官方得分** | **0/100（初赛）、0/100（决赛）、0/20（加分）** | 本任务为文档/分析工作，不产生任何官方得分 |

档位可达性（官方离散档位 vs 计划目标）：**3 处不可达** —— E5 目标「9–10」（档位 6/8/10）、E9 目标「8–9」（档位 5/7/10）、F9 目标「7」（档位 6/8/10）。

## 失败样本

| 失败 | 原因分类 | 处置 |
|---|---|---|
| `psql -U datamind` → `FATAL: role "datamind" does not exist` | 环境/凭据假设错误 | 改用 `-U datamind_app` 成功；未隐藏该次失败 |
| `grep -rl 'CREATE TABLE IF NOT EXISTS evidence_nodes' db/migrations/` → 无输出、**exit 1** | 预期证据（非故障） | 作为「证据图不在 migrations 中」的正面证据记入台账 §3.3 P6 |
| E1 目标「10」不可由 1297 回归数支撑 | 分母口径混淆 | 在台账中单列：回归 1297/1297 ≠ 模型准确率（审计 §2 已警示） |
| E5/F4/F5 的「≥90% 档」无法核实 | 官方测试集缺失 | 标为**无法判定**，不给出任何 ≥90% 声称 |
| F14 领先性无对比实验 | 无公开基准 + 无本地权重 | 列入「不可能完成、不要假装完成」 |
| `deploy/init` 与 `db/migrations` 的 DDL 分叉未做空库等效性验证 | 本轮禁用重建容器/库 | 在诚实边界中明写为**未验证**，留给 M2 前单独演练 |

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD          # e4e2ebf, exit 0
cd "/Users/caicai/DataMind Agent" && grep -n 'version=' datamind/api.py   # 197: version="0.39.0", exit 0
cd "/Users/caicai/DataMind Agent" && git ls-files | wc -l                 # 605, exit 0
cd "/Users/caicai/DataMind Agent" && ls db/migrations/*.sql | wc -l       # 18, exit 0
cd "/Users/caicai/DataMind Agent" && grep -n 'def clarify' datamind/sql_engine.py  # 179, exit 0
cd "/Users/caicai/DataMind Agent" && curl -s http://127.0.0.1:8000/openapi.json | .venv/bin/python -c "import sys,json;print(len(json.load(sys.stdin)['paths']))"  # 75, exit 0
cd "/Users/caicai/DataMind Agent" && docker exec datamind-postgres psql -U datamind_app -d datamind_meta -tAc "select suite||'='||count(*) from evaluation_cases group by suite order by suite"  # nl2sql=14 rag=12 robustness=4, exit 0
cd "/Users/caicai/DataMind Agent" && docker exec datamind-postgres psql -U datamind_app -d datamind_meta -tAc "select 'evidence_nodes='||(select count(*) from evidence_nodes)||' evidence_edges='||(select count(*) from evidence_edges)"  # 1456/1359, exit 0
cd "/Users/caicai/DataMind Agent" && grep -rl 'CREATE TABLE IF NOT EXISTS evidence_nodes' db/migrations/  # 无输出, exit 1（预期证据）
```

完整清单见 [docs/SCORE_GAP_LEDGER.md](../docs/SCORE_GAP_LEDGER.md) §6。

## 诚实边界

- **不能据此声称**：任何官方得分、排名、晋级概率；本卡与台账均为内部规划材料。
- **不能据此声称**：官方使用 E1–E12/F1–F15 编号（官方表无此编号，为仓库内部编号）。
- **不能据此声称**：E1/E2/E5/E6/F4/F5 已达到某档准确率——官方测试集未提供，既有的 14/14、10/12 来自较早代码与小样本。
- **不能据此声称**：F11/F12（答辩/演示 各 5 分）已得分——审计给的 3+3 是条件假设，无现场证据。
- **不能据此声称**：证据图存储「已建成」——现存表缺绑定列与目标边类型；019 的真实工作范围与 `deploy/init`↔`db/migrations` DDL 分叉**未做空库验证**。
- 本卡未改动 `PROGRESS.md`、`README.md`、`requirements*.txt`、`db/migrations/` 及任何业务代码；未执行任何 git 写操作、未删文件、未重启容器、未跑全量摄取。核对期间工作区被并发写入（22:19:02–22:19:22 出现 WP1 的 `scripts/eval_registry.py`、`tests/test_eval_registry.py`、`data/evaluation/registry/`），非本任务产物，未纳入本卡任何数字。

## 评分联动

本项**不直接对应任何官方评分行**，其价值在于把 27 行官方评分项的「档位—证据—目标—可离线完成性」一次性对齐，作为其余 WP 的计划输入：

| 联动对象 | 作用 |
|---|---|
| E7 + F6（效率与伸缩，5+5） | 台账判为**最可能真实提升**第 1 位（工具链可离线复现，`reports/scale_v1.json` 已有 4+4 规模点） |
| E8 + F8（中级任务，15+15） | 第 2 位：B3（`sql_engine.py:179`）与 B4（`schema_linking.json` 31/31）已有基础，官方 F8 三项门槛=9 分 |
| E10（多轮，5） | 第 3 位：契约 + 33 项测试已存在，差冻结 6 轮评测与前端接入 |
| E11 + F10（文档，10+5） | 第 4 位：内容资产 885 行已存在，官方 E11 的 8 分档只需「图表规范、逻辑严谨」；注意 F10 单档封顶 5 |
| F1（跨源融合，10） | 第 5 位：分值最高、风险最高，必须在 M2 出口做 Go/No-Go（<60% 降级） |
| F11/F12/F14/F15 | 明确列入「本轮不可能完成」：现场表现、公开最优对比、商业化路径均超出仓库可产出范围 |

> 目标分一律为「完成对应工作并经评委认可后」的条件值，**不得**单独引用为得分。基线正确率若新测下滑，按计划 §5 M1 出口先修质量再谈广度。
