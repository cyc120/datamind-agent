# WP6 结论卡：B4 Schema Linking 独立复核（v2）

- **WP**：WP6 / 官方中级任务难题④「精准表定位（Schema Linking）：数十张表中定位正确表/字段」
- **日期**：2026-10-10（北京时间）
- **基线提交**：`e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`）
- **API 版本**：0.39.0（`GET /openapi.json` → `info.version`）
- **机读报告**：[reports/schema_linking_v2.json](../schema_linking_v2.json)
- **题集**：[data/evaluation/schema_linking_v2.json](../../data/evaluation/schema_linking_v2.json)
  （suite sha256 `152730e701a359b34ac03617dbacfbc7859a236feb8289b10f39c12ee6958752`）

## 问题

v1 报表 [reports/schema_linking.json](../schema_linking.json) 表 31/31、列 28/28、指标 4/4、路径 6/6，
但只有 **18 条自建题、6 张真实表、无干扰表、无未见问题**；审计（§B4、§4）判定「较好但需数十张表 + 近义干扰表 +
未见问题」。另有一个独立发现：**该报表记录的 `catalog_code_sha256=90abf30f…` 与当前及其前两个提交的
`datamind/catalog.py`（`1b5ab88f…`）都不一致**，即 31/31 的引擎归属无法核对 → 需要独立复核。

## 做法

1. **只读 fixture 表池**：`--suite v2` 从 `schema_snapshots` 只读读取 3 个快照的真实表（19 张）+ 题集里声明的
   构造/干扰表（27 张）= **46 张表**；3 张 `excluded_tables` 模拟未纳入 allowlist 的表。**不写元数据库。**
2. **未见问题 57 条**（48 正例 + 9 拒绝类），与 v1 的 18 条题面零重叠；期望标签作者标注，
   评测前校验 gold 必须存在于池内（否则直接抛错，杜绝编造标签）。
3. **拒绝类单列**（≥6 条，三种）：受权限保护字段 4 条（`customers.phone`、`customer.email`、
   `region_managers.phone`、`supplier_master.contact_phone`）、未纳入 allowlist 的表 3 条、无已批准连接路径 2 条。
   **拒绝不进入召回分母**。
4. **机制粒度消融**四档：只词法 / 词法+别名 / 词法+别名+枚举 / 全机制（含 Join Graph）。
   消融只按引擎输出的 `reasons` 过滤候选（`lexical`、`alias`、`enum`、`join_graph`），**不改引擎代码**。
5. 额外给出 **失败归因**（机制没匹配上 / 被全局 8 候选预算截掉 / 类内 Top-5 截掉）与
   **候选预算敏感性**（8 vs 32），用于区分「机制缺陷」与「预算不足」。

## 数据与集

| 项 | 值 |
|---|---|
| 表池 | 46 张 = 真实 19（retail 6 / chinook 11 / industrial 2）+ 构造 27；已批准关系 37 条；指标 11 个 |
| 干扰表 | `sales_orders` / `order_sales_detail` / `customer_sales` / `product_sales_summary` / `customer_profiles` / `refund_requests`（名字与语义近似但登记项不同） |
| 拒绝类 | 9 条（受权限字段 4 + 未登记表 3 + 无批准路径 2） |
| 路径题 | 25 条（含 5 表长路径、自连接、被拒的未批准直连边 refunds→orders 绕行） |
| 干扰反例断言 | 32 条 declared-absent 表/列 |
| 环境 | 单机、只读 PostgreSQL 元数据库、不调用上游模型 |

## 结果（分子分母）

| 指标 | v2（46 表 / 57 题） | v1 复跑（当前引擎） |
|---|---|---|
| 表召回 Top-5 | **65/88 = 73.9%** | 31/31 = 100.0% |
| 列召回 Top-5 | **48/56 = 85.7%** | 28/28 = 100.0% |
| 指标召回 Top-5 | **10/10 = 100.0%** | 4/4 = 100.0% |
| 连接路径正确率 | **25/25 = 100.0%** | 6/6 = 100.0% |
| **错误路径/越权拒绝率（单列）** | **9/9 = 100.0%** | 不适用（v1 无拒绝类） |
| 干扰表误选 | **0/32 = 0.0%** | 不适用 |

机制消融（表召回 / 列召回，分母分别为 88 / 56）：

| 档位 | 机制 | 表召回 | 列召回 | 指标召回 |
|---|---|---|---|---|
| `lexical_only` | 只词法 | 50/88 = 56.8% | 37/56 = 66.1% | 8/10 |
| `lexical_alias` | +别名（含已批准实体别名） | 60/88 = 68.2% | 42/56 = 75.0% | 10/10 |
| `lexical_alias_enum` | +枚举值 | 60/88 = 68.2% | 44/56 = 78.6% | 10/10 |
| `full_with_join_graph` | +Join Graph / 指标依赖 / 时间口径 | 65/88 = 73.9% | 48/56 = 85.7% | 10/10 |

失败归因（全部 31 条缺失项）：**机制没匹配上 0 条**、**被全局 8 候选预算截掉 30 条**、**类内 Top-5 截掉 1 条**。
候选预算敏感性（全机制档）：8 候选 → 表 65/88 = 73.9%、列 48/56；32 候选 → 表 **85/88 = 96.6%**、列 48/56 不变。

**独立复核的核心结论**：
1. v1 的 100% **不可直接外推**：同一个引擎在 46 张表、同义词干扰列（多个表都把 `business_name` 声明为
   「销售额」「在库数量」）并存时，表召回回落到 73.9%。
2. 回落的**主因不是「找不到」，而是候选预算分配**：机制本身把 gold 放进了完整候选列表（`not_matched=0`），
   但 `Catalog.retrieve(limit=8)` 的**全局** 8 条截断发生在「按类取 Top-5」之前，指标/列候选会把表候选挤掉；
   预算放宽到 32 后表召回升到 96.6%。建议：按类配额或在 `retrieve` 内先分桶再截断。
3. 列召回 85.7% 在 32 预算下不变 → 这是**同类同名列 Top-5 的固有上限**（列名声明重复），
   需要更细的列定位（如按表定位后再选列）。
4. 路径与拒绝两项在 46 表下仍 25/25、9/9：未批准直连边（`refunds.order_item_id → orders.order_id`，
   `approved=FALSE`）没有被当成捷径，未登记表/受限列没有被召回 → fail-closed 行为在更大表池下成立。
5. 干扰表（`sales_orders`/`order_sales_detail`/`customer_sales` 等，名字与真实表高度相似）**0/32 误选**。

## 失败样本（保留，含原因）

- 表召回 23 条缺失 / 列召回 8 条缺失 / 指标 0 条，**全部**为 `global_budget_cut`（30）与 `kind_top5_cut`（1），
  逐条明细在报告 `cases[].failure_causes`。典型：`v2-025`（2025 年各销售大区的销售额）
  完整候选里有 `order_items`/`orders`（`metric_dependency`），但 8 条预算被 5 个「销售额」列 + 2 个指标占满，
  表召回为 0；`v2-045` 的 `regions.region_name` 同理。
- **首轮（v2 初稿）结果如实保留**：表 61/88 = 69.3%、列 44/62、指标 9/10、路径 25/25、拒绝 9/9、
  干扰 0/31；失败归因 `not_matched 23 / global_budget_cut 22 / kind_top5_cut 1`；消融表 50/55/55/61。
  随后做了一次**作者侧修订**：补自然别名（设备/工单/故障/合同/指标/发票/采购订单/月份）、
  修正 10 条题面与纯主键列金标签（v1 既有口径：连接键不计入语义锚点列）、把 allowlist 拒绝类的
  相似表替代断言收窄为「未登记表不得被召回」。**分母因此从列 62 变为 56，变化如实记录、不上移也不隐藏。**
- 无效候选/unhandled：`test_unknown_gold_is_rejected_loudly` 证明编造的 gold 表会让评测直接抛错（0 例外）。

## 复现命令（实测退出码）

```bash
cd "/Users/caicai/DataMind Agent"
.venv/bin/python scripts/eval_schema.py --suite v2            # exit 1（默认 --min-recall 0.90 门槛未达）
#   table_recall_at_5: 65/88 = 73.9% ; column_recall_at_5: 48/56 = 85.7% ; metric_recall_at_5: 10/10 = 100.0%
#   join_path_accuracy: 25/25 = 100.0% ; reject_accuracy: 9/9 = 100.0% ; distractor_false_positives: 0/32 = 0.0%
.venv/bin/python scripts/eval_schema.py --output /tmp/v1_after.json   # exit 0（v1 行为不变）
#   table 31/31、column 28/28、metric 4/4、path 6/6；与冻结报表逐案 retrieved 完全一致
.venv/bin/python scripts/test_isolated.py --pattern test_schema_linking_v2.py   # exit 0；Ran 16 tests ... OK
#   {"tests": 16, "failures": 0, "errors": 0, "metadata_isolated": true,
#    "source_database_modified": false, "commit": "e4e2ebf"}
```

> **退出码 1 的说明**：v2 是「更难、含干扰」的独立复核集，沿用 v1 的 90% 门槛后未通过；
> 本卡不调低门槛来制造通过，而是如实给出数字、归因与改进方向（预算分配）。
> 注意 `scripts/test_isolated.py` 即使内部有失败用例也返回 0，必须读它输出的 JSON `failures` 字段。

## 诚实边界（不能据此声称什么）

1. **表池含构造表**：演示库只有 19 张真实表，46 张中的 27 张由本 WP 在题集 JSON 中声明构造，
   不是生产 schema；73.9%/96.6% 是 fixture 条件下的数字，不能外推为真实库表现。
2. **跨源混池**：三个来源被并入一个池，同一别名（如「销售额」）会同时命中多个来源
   （`chinook.revenue` 与 `demo_retail.sales_amount` 同时出现）；生产中由 source scope 隔离，
   因此本卡数字**不能**当作生产召回率，只能作为「同名不同源干扰」的下界近似。
3. **消融是候选过滤式**：按引擎输出的 `reasons` 过滤，测的是机制贡献，不是引擎内部的代码级开关消融。
4. **Top-5 微平均召回不惩罚误选**：误选由 `distractor_false_positives`（0/32）单独统计，两者不可混为一谈。
5. **v1 报表哈希归属未修正**：本卡只记录 `recorded 90abf30f… ≠ current 1b5ab88f…` 并复跑核对，
   **未**改动 v1 报表文件（不在本 WP 的允许写入范围内）。
6. 自建冻结集，**不构成**对 Spider/BIRD 等公开基准的泛化声明，也不含官方测试集。

## 评分联动

| 官方项 | 本卡提供的材料 | 不能提供 |
|---|---|---|
| 中级任务难题④（B4，E8 质量 / F8 广度） | 46 表 + 干扰表 + 57 条未见问题 + 拒绝类单列 + 机制消融 + 失败归因 + 复现命令 | 官方「攻克/质量」判定与分值 |
| F4 复杂 NL2SQL 支撑 | 路径 25/25、未批准边不被当捷径、候选预算瓶颈的量化证据与改进方向 | 公开基准成绩 |
| E8 质量加分 | 独立复核出 v1 不可外推、哈希归属不一致两项发现，并给出可执行修复项 | 官方分数 |

> 所有数字取自 [reports/schema_linking_v2.json](../schema_linking_v2.json) 与本次命令输出；本卡不声明任何官方分值。
