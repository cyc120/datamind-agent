# WP5a 结论卡：B2 可扩展要素提取（未参与开发的新领域）

- **WP**：WP5a / 官方中级任务难题②「可扩展要素提取（不依赖 Few-Shot 大提示词，新增领域可低成本扩展）」
- **日期**：2026-10-10（北京时间）
- **基线提交**：`e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`）
- **API 版本**：0.39.0（`GET /openapi.json` → `info.version`；75 条路径）
- **机读报告**：[reports/extensible_fields_v1.json](../extensible_fields_v1.json)
- **说明文档**：[docs/EXTENSIBLE_EXTRACTION.md](../../docs/EXTENSIBLE_EXTRACTION.md)

## 问题

官方难题②要求「新增领域可低成本扩展」，而审计（§B2）判定现状仅「有对应机制」：领域知识确实存在数据库注册表
（`schema_snapshots/_tables/_columns`、`entity_aliases`、`business_metrics`）而不是提示词里，但**仓库没有任何
「新增领域成本」的实测**，因此该项扩展性得分无法判定。

## 做法

1. 选一个**未参与开发的领域**：设备维保工单（设备编号/故障代码/维保日期/工时/备件号/维保结论 6 个字段）。
2. 只用**声明式配置 + 现有注册表属性契约**：`data/evaluation/extensible_field_v1/manifest.json` 的字段属性
   刻意与 `schema_columns` 同名（`business_name`→`label`、`aliases`、`enum_map`、`semantic_role`、
   `sensitivity`、`unit`）。**不改元数据库表结构、不写库、不加迁移、不调用上游模型**。
3. 6 份材料（txt/docx/xlsx 三种格式、6 种标签拼写、20 条记录）由 `build_suite.py` 确定性生成；
   `labels.json` 的 120 条断言来自作者手写 SPEC（先冻结再评测）；`--check` 会在材料/标签/摘要被篡改时退出码 1。
4. 用**未修改的** `Catalog.retrieve()` 完成新领域的字段定位（fixture 注入声明式行），
   再按 manifest 声明的解析模式做确定性取值抽取，得到「问题 → 字段 → 取值 → 定位」链路。
5. 三档对照：声明式臂 vs 「只有示例、没有配置」的 stub 对照臂，在 `0/3/10` 示例下比较同一冻结集的同一分母。

## 数据与集

| 项 | 值 |
|---|---|
| 集 | `data/evaluation/extensible_field_v1/`（自建、冻结） |
| 材料 | 6 份（txt ×2 / docx ×2 / xlsx ×2），20 条记录，3 种格式 |
| 标注 | 120 条字段断言（849 行 `labels.json`），其中 3 条期望 `null`（材料里没有该字段） |
| 冻结摘要 | manifest `0980242f6cd547332962f2c071e949dfe4c5a9580f2ed18b589c4471a5c2d5c8`；labels `a74d66e6276b4bc3705c7b24fc13dfa0887d8caacd05aead09629e42d8284d30` |
| 确定性 | 跨秒重跑逐字节一致（zip 时间戳与 `docProps/core.xml` 的 `modified` 已归一化为 2026-01-01） |

## 结果（分子分母）

| 指标 | 分子/分母 | 比率 |
|---|---|---|
| 声明式臂字段断言 | **120/120** | 100.0% |
| 按格式 | docx 36/36、txt 36/36、xlsx 48/48 | 均 100% |
| 按字段 | 6 个字段各 20/20 | 均 100% |
| 带定位的取值 | 117/117 | 100%（另 3 条期望 null，无取值） |
| 缺字段未补值 | 3/3 | 100% |
| **三档（声明式）** | 0 示例 120/120；3 示例 120/120；10 示例 120/120 | **极差 0.0 个百分点** |
| **三档（stub 对照）** | 0 示例 3/120=2.5%；3 示例 63/120=52.5%；10 示例 120/120=100.0% | 极差 97.5 个百分点 |
| 非法取值被拒 | 0 条进入字段值 | （单测用 `SP-99X` 构造 1 条被拒样本） |
| 引擎代码改动 | `datamind/catalog.py` diff **0 行**；新增迁移 **0** 个 | `git diff --numstat HEAD -- datamind/catalog.py` → 空 |
| 配置行数 | manifest **273 行**（字段定义 97 + 材料/解析声明 131 + 其余 45）+ 标注 849 行 | 报告 `cost.config_lines` |
| 原领域守卫 | 快照表数 6/11/2、评测用例 nl2sql 14 / rag 12 / robustness 4 均不变 | `original_domain_guard.match=true` |

结论：**该实现不靠示例堆**（声明式臂三档极差 0.0 ≤ 5 个百分点）；对照组如实显示示例买到的是
「标签拼写覆盖面」（2.5% → 52.5% → 100.0%），而拼写已经写进配置的别名表里。

## 失败样本

- 声明式臂在本集 **0 失败**；失败样本来自对照组与边界单测（如实列出，不做美化）：
  1. stub 臂 0 示例档 117 条 `missed_value`、3 示例档 57 条 `missed_value`（集中在 m02/m04/m06 的别名拼写）。
  2. 边界单测：`SP-99X` 不符合声明的 `^SP-\d{3}$` → 进入 `value_failed_declared_pattern`，**不产出字段值**。
  3. 缺字段（m02/m04 无备件号、m06 无工时）必须返回 `null`，补值记为 `fabricated_value`（本集 0 条）。

## 复现命令（实测退出码）

```bash
cd "/Users/caicai/DataMind Agent"
.venv/bin/python data/evaluation/extensible_field_v1/build_suite.py --write   # exit 0
.venv/bin/python data/evaluation/extensible_field_v1/build_suite.py --check   # exit 0
.venv/bin/python scripts/eval_extensible_fields.py                            # exit 0 (declarative: 120/120 = 100.0%)
.venv/bin/python scripts/test_isolated.py --pattern test_extensible_fields.py # exit 0；Ran 17 tests ... OK
#   {"tests": 17, "failures": 0, "errors": 0, "metadata_isolated": true,
#    "source_database_modified": false, "commit": "e4e2ebf"}
```

## 诚实边界（不能据此声称什么）

1. **不是大模型 few-shot 实验**：抽取器是确定性规则解释器，本卡不度量任何模型的示例依赖。
2. **「引擎 0 行」只覆盖本实验范围**（离线要素提取 + 字段定位复用 `Catalog.retrieve`）。
   把声明式配置写入运行时注册表所需的 loader/权限登记/发布流程**未实现**，端到端接入改动量**未测量**。
3. **stub 臂是替身**，不等于真实 few-shot 提示词的收益。
4. **自建合成小集**（6 份材料 / 20 条记录）：不能外推为真实工单系统准确率，也不是官方评测成绩。
5. **确定性依赖归一化**：未归一化 zip 时间戳时，同一秒外重跑字节就不同（已在测试中断言该风险被消除）。
6. 原领域守卫只做**只读计数**比对；工作区同时存在其他工作包的改动，本卡不评价它们。

## 评分联动

| 官方项 | 本卡提供的材料 | 不能提供 |
|---|---|---|
| 中级任务难题②（E8 单点 / F8 广度第三项） | 新领域成本清单（配置 273 行、标注 120 条、引擎 0 行）+ 三档对照 + 冻结集 + 失败清单 + 复现命令 | 「低成本／攻克」的官方主观判定与分值 |
| E8/F8 质量加分 | 原领域不受影响、引擎零改动的可核对证据 | 官方分数 |
| F4（复杂 NL2SQL 支撑） | 字段定位复用同一 `Catalog.retrieve` 的证据 | 跨库泛化结论 |

> 所有数字取自 [reports/extensible_fields_v1.json](../extensible_fields_v1.json) 的本次实测输出；本卡不声明任何官方分值。
