# 原生 DXF 宽格式读取 v2（真实工程图）

日期：2026-10-08。读取器 `dxf-native-v2`，交换约定 `datamind-2d-engineering-dxf-v2`，输出契约 `cad-contract-v2`。v1（`dxf-native-v1` / `datamind-2d-labelled-dxf-v1` / `cad-contract-v1`）原样冻结、仍可显式选择，见 `data/evaluation/frozen/cad_dxf_v1/manifest.json`。本轮读取源 DXF 字节，不接人工 gold、模型、数据库或网络。

## 为什么是 v2 而不是放宽 v1

v1 是**受控交换约定**：ASCII 文本、≤2 MiB、≤10000 ENTITIES、Model 布局、二维 `LINE`/`CIRCLE`/`TEXT`，且必须带 `DM_DRAWING_ID`/`DM_REVISION`/`DM_PART_ID` 三个标题层。真实 CAD 导出图纸普遍含折线、圆弧、块引用、`MTEXT`、原生 `DIMENSION`，因此几乎必然被 v1 整图拒绝。v1 的"不静默忽略"纪律是正确的，所以这里**新增 v2 版本**，而不是修改 v1：老证据仍按老读取器复现。

v2 保留 v1 的全部纪律：单位只从源 `$INSUNITS` 取、句柄/图层/原文/坐标可追溯、未知值不猜、几何值不冒充制造尺寸、缺口逐项声明。

## 部分成功 + 逐项声明（本轮的明确选择）

两条路（整图拒绝 / 部分成功）任选其一但必须一致。v2 选择**部分成功**，因为真实图纸几乎总含少量本项目不建模的图元，整图拒绝会让"能读的 95%"陪葬 5%。

- **逐项声明**：每个未读取的实体/块子树都记录稳定错误码、实体类型与源句柄，规范串为 `CODE:TYPE:HANDLE[:DETAIL]`，例如 `CAD_ENTITY_TYPE_UNSUPPORTED:SPLINE:2AF`；同时 `metadata.unsupported` 保留结构化记录（`code`/`entity_type`/`handle`/`block_path`/`detail`），`metadata.first_unsupported` 给出**第一个**不支持项。
- 声明列表上限 **500** 条（`CadLimits.max_declared_items`），超出追加 `CAD_UNSUPPORTED_ITEMS_TRUNCATED:<总数>`；`metadata.unsupported_counts` 始终给出**逐码总数**，所以截断只影响明细行数，不影响计数。
- 有任一未读取项时 `parse_status` 降级为 `partial`，并写入统一契约的 `unsupported_features` 与 `failed_scope`（上限 100 条）。
- **仍然整图拒绝**（读取本身不可信，不产出快照）：二进制 DXF、字节超限、标签对结构损坏、句柄缺失/非法/重复、按原始标签校验发现实体丢失、审计 errors、源实体记录超过上限、完全没有可支持实体（`CAD_NO_SUPPORTED_ENTITIES`，但错误详情仍带全部逐项声明）。这些返回稳定错误码，不静默。
- 需要说明的一致性取舍：`max_expanded_entities`（块展开产物）超出时**截断并声明**；`max_entities`（源实体记录）超出时**整图拒绝**——前者是"源在有界范围内、展开放大"，后者是"源本身超出本 profile 声明的规模"，调用者可显式调高后者。

## 文件和字段范围（v2 支持）

- 输入为文本 DXF。默认上限 **32 MiB / 200000 条源实体记录**（ENTITIES + BLOCKS 内实体），见下节参数化；读取前按原始标签扫描句柄，拒绝缺失/非法/重复句柄，读取后先与 `ezdxf.read` 结果核对（审计修复之前），再核对审计后集合，保证"库没有悄悄丢实体、审计改动有声明"。审计 `errors` 整图拒绝；审计 `fixes` 记入质量告警，若修复改变了实体集合（删除/新增），逐项声明 `CAD_DXF_AUDIT_ENTITY_REMOVED` / `CAD_DXF_AUDIT_ENTITY_ADDED`。
- 编码：优先 UTF-8；解码失败时按 `$DWGCODEPAGE` 声明的代码页回退（`ANSI_936→gbk` 等），再退 `gb18030`/`cp1252`，并把实际编码写进 `source.encoding` 与告警 `CAD_ENCODING_FALLBACK:<codec>`。回退编码下的文本保真度未验证。
- 布局：Model 布局，二维世界 XY。Z≠0 的实体逐项声明 `CAD_3D_OR_NONFINITE_UNSUPPORTED`（块变换只映射 XY，所以源 Z 单独校验）；非默认 extrusion / 厚度≠0 声明 `CAD_OCS_OR_THICKNESS_UNSUPPORTED`；关闭/冻结图层与不可见实体声明 `CAD_HIDDEN_ENTITY_UNSUPPORTED`。纸空间实体不读取，按布局汇总声明 `CAD_PAPERSPACE_ENTITIES_SKIPPED`（含数量与首个句柄）。
- 实体：`LINE`/`CIRCLE`/`TEXT`（v1 三类，v2 额外接受任意文字样式与旋转，`rotation`/`height` 记录到 WCS 值）、`LWPOLYLINE`/`POLYLINE`（顶点 + 闭合标志；bulge≠0 声明 `CAD_POLYLINE_BULGE_UNSUPPORTED`，宽度非 0 声明 `CAD_POLYLINE_WIDTH_UNSUPPORTED`，三维/网格折线声明 `CAD_POLYLINE_3D_OR_MESH_UNSUPPORTED`）、`ARC`（圆心/半径/起止角）、`INSERT`（块展开见下节）、`MTEXT`、原生 `DIMENSION`。
- `MTEXT` 同时保留两种读法：`raw_text` 是源里带格式码的原文（含 `\P`），`text` 是剥掉格式码、把 `\P` 换成换行的纯文本；超过 16384 字符截断并声明 `CAD_MTEXT_TRUNCATED`。统一契约的块 `text` 用纯文本（利于检索），原文在实体字段里可追溯。
- 原生 `DIMENSION`：记录 `dimtype`（linear/aligned/angular/diameter/radius/angular_3p/ordinate/unknown）、`geometry_measurement`（`get_measurement()` 的几何测量值）、`declared_text`（制图者显式覆盖的文字，源为 `<>`/空时为 `None`）、`defpoints`、`dimstyle`。块坐标里的 `DIMENSION` 会因变换改变测量值，逐项声明 `CAD_DIMENSION_IN_BLOCK_UNSUPPORTED`，不做缩放近似。
- 标题身份（`DM_DRAWING_ID`/`DM_REVISION`/`DM_PART_ID`）在 v2 **可选**：真实图纸不要求使用本自定义图层。缺失即缺失，不再 `CAD_TITLE_MISSING` 整图拒绝；重复或不符合标识符约束的值逐项声明 `CAD_TITLE_DUPLICATE`/`CAD_TITLE_VALUE_INVALID` 并省略该字段。
- 单位只从源 `$INSUNITS` 取：4/mm、1/in、0/unknown。其它代码（如 5/cm、6/m）记为 `unknown` 并声明 `CAD_UNIT_CODE_NOT_MODELLED`，`insunits_modelled=false`；**未知不猜 mm**。未知单位下直径特征的数值保持全 null 并记 `CAD_DIMENSION_VALUE_UNKNOWN:<handle>`。

## 尺寸纪律：几何测量值 ≠ 文字声明的尺寸

- `diameter` 特征沿用 v1：只认 `%%c20 +/-0.1` 形式，数值来自标注**文字**，配合 `DATAMIND` XDATA 的显式句柄引用；关联候选只取 Model 空间的圆（块内句柄在多次插入时不唯一）。
- `native_dimension` 特征把两类值分开放：`geometry_measurement`（几何测量值）与 `declared_text`/`declared_value`（文字声明），并用 `value_authority`（`declared_text` / `dimension_measurement` / `unavailable`）标明来源。`<>` 表示显示值由几何生成，此时 `declared_text` 为 `None`、authority 为 `dimension_measurement`。
- `cad_contract_v2.declared_sizes()` 只放行文字声明的值：未覆盖文字的 native dimension 以 `CAD_VALUE_IS_GEOMETRY_MEASUREMENT` 排除（**不把几何值提升为制造尺寸**），标注文字不是纯数字的以 `CAD_DECLARED_TEXT_NOT_A_NUMBER` 排除，未知单位以 `CAD_VALUE_OR_UNIT_UNKNOWN` 排除。直径特征仍保留 v1 的几何/标注矛盾诊断 `CAD_GEOMETRY_ANNOTATION_CONFLICT`。
- v2 不推断"某个 native dimension 量的是哪条几何"（不做最近对象猜测），因此 adapter 不为它生成关系；该限制以质量告警 `CAD_NATIVE_DIMENSION_ASSOCIATION_NOT_RECOVERED:<n>` 明示，而不伪装成已确认关联。

## 块展开语义

- `INSERT` 递归展开：按 INSERT 链合成**相似变换**（平移 + 旋转 + 均匀缩放，含镜像），子实体坐标是 WCS 坐标。
- 每个展开实体带 `block_path`：从 Model 空间最外层 INSERT 到包含它的块，按序记录引用句柄。块内句柄只有与 `block_path` 组合才唯一，统一契约 `cad_entity` 定位器据此携带 `block_path`（可选字段，Model 空间实体为空链）。
- INSERT 自身也产出一条 `INSERT` 记录（块名、插入点、旋转、缩放、`expansion_status`、`expansion_code`、`expanded_entity_count`），且记录在它展开的内容**之前**（文档顺序）。
- 逐项拒绝（声明 + 不展开）：块不存在 `CAD_BLOCK_MISSING`、循环引用 `CAD_BLOCK_RECURSION_CYCLE`（防御性：ezdxf 审计通常已先以 `CAD_DXF_AUDIT_ERRORS` 拒绝循环块）、超过深度 `CAD_BLOCK_DEPTH_LIMIT_REACHED`（默认 8）、非均匀缩放 `CAD_INSERT_NON_UNIFORM_SCALE_UNSUPPORTED`、缩放为 0 `CAD_INSERT_DEGENERATE_SCALE_UNSUPPORTED`、INSERT 阵列 `CAD_MINSERT_ARRAY_UNSUPPORTED`、镜像块内的文字 `CAD_TEXT_IN_BLOCK_TRANSFORM_UNSUPPORTED`（镜像文字的方向语义不建模；同块内的 LINE/CIRCLE/ARC 正常展开）。
- 块内图层 `0` 按**原始层**记录，不做"继承 INSERT 层"的推断（因此不会因为继承而误判标题层）。

## 上限的默认值与可调方式

| 参数 | 环境变量（adapter 参数同名） | 默认 | 允许上限 | 超出行为 |
|---|---|---:|---:|---|
| 源字节 | `DATAMIND_CAD_MAX_BYTES` / `max_bytes` | 32 MiB | 512 MiB | 整图拒绝 `CAD_SOURCE_SIZE_INVALID` |
| 源实体记录 | `DATAMIND_CAD_MAX_ENTITIES` / `max_entities` | 200000 | 200000（契约上界） | 整图拒绝 `CAD_ENTITY_COUNT_INVALID` |
| 展开后实体 | `DATAMIND_CAD_MAX_EXPANDED_ENTITIES` / `max_expanded_entities` | 200000 | 200000（契约上界） | 截断 + `CAD_ENTITY_LIMIT_REACHED`（partial） |
| 块展开深度 | `DATAMIND_CAD_MAX_BLOCK_DEPTH` / `max_block_depth` | 8 | 64 | `CAD_BLOCK_DEPTH_LIMIT_REACHED` |
| 声明明细条数 | 无（`limits=CadLimits(...)`） | 500 | 5000 | 只截断明细，计数仍完整 |

不可用取值（0、负数、非整数、超过契约上界）以 `CAD_LIMIT_INVALID` 拒绝而非静默钳制：调用者不会相信自己没拿到的上界。实际生效的上限写入 `manifest.limits`。

**放宽上限的可信性代价**：上限放大只放宽"文件规模"，不提高"理解正确率"。32 MiB/200000 的上限使读取的内存与时间随源大小线性增长，本轮没有在真实大图上测量耗时/内存，也没有真实图纸的字节分布统计；因此不得据此宣称"支持任意大图纸"。放宽后仍未验证：真实图纸的实体类型分布、块嵌套实际深度、编码回退的文本保真、非均匀块缩放占比。

## 运行与冻结

```bash
# 依赖（已装）：ezdxf==1.4.4
python -m unittest tests.test_cad_dxf_v2 tests.test_cad_adapter tests.test_parse_contract
python -m unittest tests.test_cad_dxf tests.test_cad_contract   # v1 回归，未被破坏
```

统一契约入口（默认 v2，`profile='v1'` 走老路径）：

```python
from ingestion import cad_adapter
cad_adapter.parse(path, document_id='doc-1')                    # v2
cad_adapter.parse(path, document_id='doc-1', profile='v1')       # 冻结的 v1 读取器
```

`data/evaluation/frozen/cad_dxf_v2/manifest.json` 冻结 v2 读取器/契约/adapter/统一契约/测试/文档的哈希、依赖版本与 v1 的 8 个哈希。`tests/test_cad_dxf_v2.py::FrozenV1GuardTests` 逐个 `shasum -a 256` 比对 v1 清单的 `code_hashes`，断言 8 个文件哈希全部未变。

## 本轮证据与边界

本轮夹具由 `ezdxf` 在 `tests/test_cad_dxf_v2.py` 内生成（55 项），覆盖：新实体类型逐字段、嵌套块展开的坐标/文字/半径、非均匀与镜像块、块内 DIMENSION、循环与深度、纸空间、OCS/三维/隐藏、bulge 折线、未支持实体逐项声明、上限与环境变量、单位、非 UTF-8 解码、句柄重复/二进制/超限、v1 哈希守护、契约层（重复 `(handle, block_path)`、authority 一致性、block_path 校验）。

边界（未做/未验证）：

- **真实图纸未验证**：用户的真实 DXF 在微信容器内，本环境（macOS 权限）读不到；真实图纸的验证由协调者在集成阶段完成。夹具是作者生成的自定义样本，不能代表工厂图纸分布，也不能据本轮报"识别率/泛化准确率"。
- 未评测：纸空间读取、`SPLINE`/`HATCH`/`3DFACE`/`ELLIPSE`/三维实体/`ATTRIB`、OCS 变换、bulge 折线弧段、非均匀缩放块的几何、块内 DIMENSION、DWG（仍需外部转换器，`CAD_DWG_CONVERTER_UNAVAILABLE`）、非 mm/in 单位的换算。
- 预览/渲染未扩展：`ingestion/cad_render.py` 仍只校验 v1 的 `DrawingSnapshot`，v2 实体暂不可渲染（本轮在允许改动清单之外）。
- 图纸资产登记路径（`datamind/cad_store.register`，被 `datamind/file_jobs.py` 的 `drawing` 目标使用）仍直接调用 v1 `parse_bytes`，因此**仍要求 DM_* 标题层**；本轮的 v2 只覆盖统一契约/ingest 解析链路。协调者集成时应决定是否把该路径切到 v2（该文件不在本轮改动清单内）。
- 没有新的评测 gold、没有模型调用、没有覆盖任何旧首轮报告；v1 的 6 图首轮结果与 `dxf_reader_v1_freeze.json` 不变。
