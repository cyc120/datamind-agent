# CAD 能力分层冻结验收集 v1（`cad-capability-v1`）

日期：2026-10-09。用途：补上 `reports/project_audit_20261009_561316c.md` 5.5 指出的证据
缺口——CAD v2 的能力证据此前只来自"开发中用来修 bug 的两份真实图纸"，那两份图纸因此
**不能同时当作独立泛化测试集**。

## 这是什么，不是什么

- **是**：作者自建的合成验收集。材料由 `build_suite.py` 用 `ezdxf` 1.4.4 与手写 ASCII
  标签流生成，覆盖单位 / 版本 / 句柄 / 实体 / 布局 / 声明式拒绝六个能力层；每份材料配
  **冻结标签**（期望 `parse_status`、期望出现/不出现的缺口码、实体与块计数、单位、
  identity 模式）。
- **不是**：独立工厂数据，不是盲测，不是识别准确率。`reports/cad_capability_v1.json`
  只给"逐项断言通过比例"，不给出 `accuracy`。
- 两份真实图纸**只作开发样本**，见 `data/cad_demo/DEVELOPMENT_SAMPLES.md`；它们的
  CRLF、位置身份（positional identity）、LEADER 缺陷已被用于修改读取器，所以不能再用作
  独立测试集。本目录**不含**任何真实图纸。

## 目录

| 文件 | 作用 |
|---|---|
| `build_suite.py` | 确定性生成器：生成 `materials/`、`labels.json`、`manifest.json` |
| `materials/*.dxf` | 31 份冻结材料（sha256 记在 `manifest.json`） |
| `labels.json` | 冻结标签（`manifest.labels_sha256` 守护） |
| `manifest.json` | 材料 sha256、标签 sha256、分层例数、被评代码清单、未覆盖清单 |

## 能力分层覆盖（v1，31 例）

一个用例可以同时属于多层；`manifest.layers` 与报告的分层汇总按"用例是否属于该层"计数。

| 层 | 例数 | 覆盖内容 |
|---|---:|---|
| `unit` | 4 | `$INSUNITS`=1(in) / 4(mm) / 缺失(unknown，**不猜 mm**) / 5(cm，非建模码 → unknown + 声明) |
| `version` | 8 | AC1009(R12) / AC1015(R2000) / AC1018(R2004) / AC1021(R2007) / AC1024(R2010) / AC1027(R2013) / AC1032(R2018)；另含 `unit_missing` |
| `handle` | 7 | 有源句柄(LF/CRLF) / 无 `code 5` 的 positional 模式(LF/CRLF) / 重复句柄拒绝 / "部分带句柄"拒绝 / R12 有句柄 |
| `entity` | 16 | LINE、CIRCLE、ARC、LWPOLYLINE(闭合/开口)、POLYLINE(闭合/bulge)、TEXT(旋转)、MTEXT、原生 DIMENSION(两种 value_authority)、INSERT(两层嵌套)、缺块 INSERT、循环块、positional 下的 INSERT、SPLINE/HATCH/3DFACE |
| `layout` | 8 | Model 布局（每个正常解析用例都断言 locator layout=Model）/ 纸空间实体按布局声明跳过 |
| `rejected` | 10 | 重复句柄、半缺句柄、循环块、SPLINE、HATCH、3DFACE、纸空间、三维 Z≠0、OCS/extrusion、隐藏图层 |

### 关键"诚实"用例

- `unit_missing`：材料源里**没有** `$INSUNITS`。标签要求 `unit='unknown'` 且
  `CAD_UNIT_CODE_NOT_MODELLED` **不得出现**（变量缺失按 DXF 默认 0 处理）。覆盖度守护另外
  直接扫描材料字节，确认 `$INSUNITS` 真的不存在——防止有人事后改成"猜 mm"。
- `handle_positional_lf/crlf`：材料由手写标签流生成（`ezdxf` 写入器总会补句柄，写不出这种
  源）。覆盖度守护直接扫描材料，断言 `code 5` 记录数为 **0**；标签要求读到
  `identity_mode='positional'`、`CAD_SOURCE_HANDLE_UNAVAILABLE`、`CAD_POSITIONAL_BLOCK_UNSUPPORTED`、
  实体无 `handle`、`entity_index` 为 `[1,2,3]`。
- `entity_polyline_bulge`：一条平直闭合 POLYLINE + 一条带 bulge 的 POLYLINE。bulge 按
  **逐项声明**处理（`CAD_POLYLINE_BULGE_UNSUPPORTED`），因此只应产出 **1** 条 POLYLINE——
  这不是"支持 bulge"，而是"不静默丢几何"。
- `entity_native_dimension`：一个 `<>`（值由几何生成）与一个文字声明 `25.4` 的
  DIMENSION。标签要求两个值分开：`value_authority` 分别为 `dimension_measurement` 与
  `declared_text`，`geometry_measurement` 都是 `10.0`。**几何测量值不得当作制造尺寸。**
- `reject_3d_z` / `reject_ocs_extrusion` / `reject_hidden_layer`：分别断言
  `CAD_3D_OR_NONFINITE_UNSUPPORTED`、`CAD_OCS_OR_THICKNESS_UNSUPPORTED`、
  `CAD_HIDDEN_ENTITY_UNSUPPORTED` 被逐项声明，且 2D 实体仍然产出。

## 冻结与守护

- `manifest.json` 记每份材料的 sha256、`labels.json` 的 sha256、被评代码清单
  （`ingestion/cad_adapter.py`、`ingestion/cad_dxf_v2.py`、`ingestion/cad_contract_v2.py`、
  `datamind/parse_contract.py`）。
- `scripts/eval_cad_capability.py` 在评测前核对材料与标签哈希；不符时以
  `SuiteIntegrityError` 终止（退出码 2），**不产出成绩**。被评代码的哈希记入报告
  （`integrity.code_files`），并与 `data/evaluation/frozen/cad_dxf_v2/manifest.json` 的
  冻结值交叉核对（`integrity.code_pin`），漂移项如实列出。
- `tests/test_cad_capability_suite.py` 守护材料/标签哈希、分层覆盖度、篡改拒绝、失败项不被
  丢弃。

## 重建

```bash
cd "/Users/caicai/DataMind Agent"
.venv/bin/python data/evaluation/cad_capability_v1/build_suite.py          # 重建（会更新 manifest.created_at）
.venv/bin/python data/evaluation/cad_capability_v1/build_suite.py --check  # 校验冻结字节可逐字节重建
.venv/bin/python scripts/eval_cad_capability.py                            # 评测并写 reports/cad_capability_v1.json
```

生成器把 `ezdxf` 写出的易变字段（`$TDCREATE`/`$TDUPDATE`、GUID、版本时间戳）钉成常量，
并**按类名排序 `CLASSES` 段的 `CLASS` 记录**（`ezdxf` 1.4.4 会以进程哈希种子相关的顺序写出
`LAYOUT`/`ACDBPLACEHOLDER`）。因此同一 `ezdxf` 版本重复生成得到逐字节相同的材料；
跨进程、跨 `PYTHONHASHSEED` 均已验证。

## 标签修订记录（如实保留，便于复核"有没有事后改标签蒙分"）

标签**在第一次评测之前**就按设计写好；第一次评测 31 例中 28 例失败。逐条说明失败原因与
处理方式，处理原则是**加强**断言而不是放宽：

| 失败 | 根因 | 处理 |
|---|---|---|
| 25 例 `parse_status` | **评测器自身缺陷**：拿包装层的 `status`（`'returned'`）去比标签里的 `parse_status`，没有读返回值里的 `payload['parse_status']` | 只改评测器（改为读返回值字段）。**未改任何标签** |
| `entity_native_dimension` 的 `entity_total` | 计数把 `block_type='dimension'` 的**特征块**也算成了实体（实际 5，期望 3） | 明确 `entity_counts` 只统计带源 `entity_type` 的块；新增 `feature_counts`（`native_dimension: 2`）让特征被单独断言——**比原来更严** |
| `reject_spline_only` 的 `manifest_limits` | 生产路径在 `parse_status='unsupported'` 的返回里**不含** `manifest.limits` | 不改代码；标签补 `entity_total={'min':0,'max':0}` 明确"零块"，该缺口记入报告的 `observations` |

另外：`reject_spline_only` 的 locator 断言原本在零块时**恒真**（空集合），已改为"有块才断言
locator 身份"，并用 `entity_total=0` 补上"零块"的正向断言——去掉了一处空转检查。
