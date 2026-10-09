# CAD 开发样本登记（真实图纸，**不得**当作泛化测试集）

日期：2026-10-09。本文登记 CAD v2 开发期使用的**两份真实工程图**，并如实说明它们的用途
与边界。它们不是验收样本；**不得**用它们报告"识别准确率"或"泛化能力"。

对应审查条目：`reports/project_audit_20261009_561316c.md` 5.5——
"CAD v2 从真实工程图获得反馈是有效进步，但开发中用于修复的两份图纸不应同时被当作独立
泛化测试集。保留开发样本；另冻结独立样本集。"

- **保留开发样本**：本文（这两份图纸的登记与实测分层统计）。
- **独立（合成）样本集**：`data/evaluation/cad_capability_v1/`（31 例，六层，
  标签与材料一起冻结）。

## 为什么这两份图纸不能当独立测试集

它们的缺陷**已经被用来修改读取器**，所以再用它们打分就是"拿训练/调试数据当测试数据"：

| 缺陷 | 由哪份图纸暴露 | 对应修复 |
|---|---|---|
| CRLF 换行让整个 `ENTITIES` 段被忽略（值带尾 `\r`、段名成 `'ENTITIES\r'`） | 两份图纸都是 CRLF | `ingestion/cad_dxf_v2.py` 在交给 `ezdxf.read` 前做换行归一化 |
| 老 ASCII DXF **一个 `code 5` 都没有**，被此前逻辑整图拒绝；`ezdxf` 又会**补造**句柄 | `test.dxf`（`code 5` 记录数 = 0） | 新增**位置身份**（positional identity）：`entity_index` + `handle` 缺失 + `CAD_SOURCE_HANDLE_UNAVAILABLE` |
| `LEADER` 实体让读取器崩成 `PARSE_ADAPTER_UNSUPPORTED/handler_failed`（`_visited` 从 `LEADER.vertices` 的纯坐标元组读 `.dxf`） | `J2_HE_8K_US_Aluminum_Plate-20260922.dxf` | `_visited` 防御性修正 + `LEADER` 走逐项声明 `CAD_ENTITY_TYPE_UNSUPPORTED` |

因此本文件只给**实测分层统计**，不给分数。任何"这两份图纸解析成功"的结论都只说明这**两张
特定的图**在**当前代码**下可读，不说明对其他工厂图纸的泛化能力——真实图纸的实体类型分布、
块嵌套深度、编码回退保真度、非均匀缩放占比均**未测量**。

## 样本 1：`test.dxf`

| 项 | 值 |
|---|---|
| 原始路径 | `/Users/caicai/Desktop/test.dxf` |
| 入库文档 id | `doc-upload-122e8ba69b4f524aedf2ba2f592cfc31` |
| 仓库内副本 | `data/cad_demo/authorized/upload-b0655759300a2d13a32e8b90ccfb08addc0d8343123ea34987615f24df0dc1af.dxf`（该目录被 `.gitignore` 忽略，不入库） |
| 字节 / sha256 | 453870 字节 / `b0655759300a2d13a32e8b90ccfb08addc0d8343123ea34987615f24df0dc1af` |
| 用途 | 暴露**无句柄源**与 **CRLF** 两个缺陷；驱动位置身份实现与换行归一化 |

### 实测分层

| 层 | 实测 |
|---|---|
| 版本 | `$ACADVER = AC1003`（R2.6） |
| 单位 | `$INSUNITS` **不存在** → `unit=unknown`、`insunits=0`、`insunits_modelled=true`；告警 `CAD_UNIT_UNKNOWN`。**没有猜 mm** |
| 句柄 | `$HANDLING` 不存在；源 `code 5` 记录数 = **0**；`identity.mode='positional'`、`source_handle_records=0`、`positional_records=572`；缺口码 `CAD_SOURCE_HANDLE_UNAVAILABLE:572`；告警 `CAD_IDENTITY_MODE_POSITIONAL` |
| 换行 | 纯 CRLF（72704 个 `\r\n`，0 个孤立 LF） |
| 实体 | `POLYLINE` **572** 条（全部 `handle=None`、`entity_index` 1..572 唯一）；无其它实体类型；无 TEXT/DIMENSION/INSERT |
| 源记录 | `source_entities=7492`（= 572 `POLYLINE` + 6348 `VERTEX` + 572 `SEQEND`），`source_block_entities=0` |
| 块 / 尺寸 / 布局 | 真实块定义 0 个；`DIMENSION` 0 条、尺寸特征 0 条；布局 `Model` + `Layout1`，纸空间实体 0 条 |
| 图层 | 2 个 |
| `parse_status` | `partial`（唯一缺口是"源无句柄"这一条声明） |
| 可提取语义 | 572 条折线几何（顶点坐标、闭合标志、图层 `0`、WCS 锚点）；每个块 locator 形如 `{handle_source: positional, entity_index: n, unit: unknown}` |
| 没有的语义 | `DM_*` 标题、直径标注与尺寸特征、原生 `DIMENSION`、块引用、可确认的尺寸关联 |

## 样本 2：`J2_HE_8K_US_Aluminum_Plate-20260922.dxf`

| 项 | 值 |
|---|---|
| 原始路径 | `/Users/caicai/Desktop/J2_HE_8K_US_Aluminum_Plate-20260922.dxf` |
| 入库文档 id | `doc-upload-a6f95b32a2562c4805e03be25d4f5e7a` |
| 仓库内副本 | `data/cad_demo/authorized/upload-413e98271c756963d8ec96d65882d818a68c81424e3100b5723031dd9e63331e.dxf`（同样被 `.gitignore` 忽略） |
| 字节 / sha256 | 677727 字节 / `413e98271c756963d8ec96d65882d818a68c81424e3100b5723031dd9e63331e` |
| 用途 | 暴露 **`LEADER` 崩溃**；同时是 CRLF 与"真实图含本项目不建模图元"的实证 |

### 实测分层

| 层 | 实测 |
|---|---|
| 版本 | `$ACADVER = AC1021`（R2007） |
| 单位 | `$INSUNITS = 1` → `unit='in'`（英寸，**声明取自源**，不是推断） |
| 句柄 | `$HANDLING` 不存在；源 `code 5` 记录数 = **1353**；`identity.mode='source'`、`positional_records=0`；实体句柄来自源 |
| 换行 | CRLF 64816 处（0 个孤立 LF） |
| 实体（Model 空间产出） | `LINE` 1123、`CIRCLE` 49、`ARC` 80、`MTEXT` 41、`TEXT` 4、`DIMENSION` 5 → 共 **1302** 条 |
| 源记录 | `source_entities=1303`（1302 条产出 + 1 条未支持的 `LEADER`），`source_block_entities=50` |
| 块 / 尺寸 / 布局 | 真实块定义 5 个，全部是 `DIMENSION` 的匿名几何块 `*D0..*D4`（各 10 条实体，共 50 条块内源记录）；Model 空间**无 `INSERT`**，因此无块展开；`DIMENSION` 5 条 → 尺寸特征 5 条；布局 `Model` + `Layout1`，纸空间实体 0 条；图层 8 个（含中文层名 `图层3`/`图框`/`标注尺寸`） |
| 缺口码 | `CAD_ENTITY_TYPE_UNSUPPORTED:LEADER:36F8`（1 条，逐项声明，**不静默忽略**） |
| 其它告警 | `CAD_TEXT_BOUNDS_APPROXIMATE`（文字外框为近似值）；`CAD_NATIVE_DIMENSION_ASSOCIATION_NOT_RECOVERED:5`（不推断某个 native dimension 量的是哪条几何） |
| `parse_status` | `partial`（唯一逐项缺口是 1 条 `LEADER`） |

### 尺寸特征的取值纪律（**几何测量值 ≠ 制造尺寸**）

5 条原生 `DIMENSION` 实测（`dimension_type` 全部 `linear`，单位 `in`）：

| # | `declared_text`（源文字） | `declared_value` | `geometry_measurement` | `value_authority` |
|---:|---|---|---|---|
| 1 | `<>%%P0.1` | `null`（文字不是纯数字） | `318.7999999984198` | `declared_text` |
| 2 | `<>%%P0.1` | `null` | `130.50000000024002` | `declared_text` |
| 3 | `<>{\H0.7x;\S+0.05^ -0.02;}` | `null` | `1.4999999997399982` | `declared_text` |
| 4 | `null`（`<>`，显示值由几何生成） | `null` | `3.500000000239993` | `dimension_measurement` |
| 5 | `{\fVineta BT\|b0\|i0\|c0\|p82;<}0.5` | `null` | `1.9999999999975557` | `declared_text` |

纪律说明：

- `geometry_measurement` **一律不得当作制造尺寸**。第 1/2 条的 `318.8` 与 `130.5` 是几何
  测量值，文字声明里带公差（`%%P0.1`），二者不是等价关系。
- `declared_value` 全为 `null`：这些文字都不是**纯数字**，合同层因此以
  `CAD_DECLARED_TEXT_NOT_A_NUMBER` 排除，而不是把 `<>` 形式硬解析成一个数字。
- 第 4 条只有几何测量值（`value_authority='dimension_measurement'`），**不可**提升为声明
  尺寸——合同层以 `CAD_VALUE_IS_GEOMETRY_MEASUREMENT` 排除。
- 本项目不推断 native dimension 与几何的关联，5 条全部以
  `CAD_NATIVE_DIMENSION_ASSOCIATION_NOT_RECOVERED` 明示，不伪装成已确认关联。

## 与验收集的关系

| | 开发样本（本文） | 冻结验收集（`data/evaluation/cad_capability_v1/`） |
|---|---|---|
| 材料 | 2 份真实工程图 | 31 份作者合成材料 |
| 是否被用于修改代码 | **是**（CRLF / 位置身份 / LEADER） | 否（先写标签再评测；材料与标签一起冻结） |
| 用途 | 暴露缺陷、驱动修复、实测分布 | 分层能力证据、可重复评测、失败项留分母 |
| 能否作为泛化证据 | **不能** | 也不能声称泛化准确率（合成样本，非工厂分布） |

真实图纸的**独立**泛化证据仍然缺失；补齐它需要**新的、未参与开发**的真实图纸，并按
`data/evaluation/cad_capability_v1/README.md` 的分层方式标注。本任务的验收集是合成替代，
其局限已在 `reports/cad_capability_v1.json` 的 `provenance` / `uncovered` / `not_measured`
中逐条写明。
