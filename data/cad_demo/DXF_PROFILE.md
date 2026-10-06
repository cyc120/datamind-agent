# 原生 DXF 读取与实体预览 v1

日期：2026-10-05。读取器 `dxf-native-v1`，交换约定 `datamind-2d-labelled-dxf-v1`，输出兼容上一阶段冻结的 `cad-contract-v1`。本轮真实读取源 DXF 字节，不接人工 gold、模型、数据库或网络。原手工契约样例及其首轮报告不变。

## 文件和字段范围

- 输入为 UTF-8/ASCII 文本 DXF，单文件最多 2 MiB，ENTITIES 最多 10000；拒绝 DWG、二进制 DXF、读取/审计要求修复、空输入和缺失/重复原始实体句柄。SHA-256 来自解析的同一份字节，读取时限制字节量；不用 recover 修复后冒充成功。冻结工具为 `ezdxf==1.4.4`。
- 限 Model 布局的二维世界 XY 坐标，显式检查 LINE/CIRCLE/TEXT、Z=0、默认 extrusion、厚度为零。坐标绝对值≤1e9，半径≤1e9、文字高度≤1e6；不支持纸空间实体、隐藏实体/关闭或冻结图层、INSERT/块展开、原生 DIMENSION/ARC/MTEXT、OCS/三维和旋转/镜像/倾斜/特殊文字对齐或字体样式。遇到这些输入返回明确边界，整图不成功解析，不静默忽略实体。
- 原始图层/句柄/文本/坐标分别保存。三类标题字段以精确图层 `DM_DRAWING_ID`、`DM_REVISION`、`DM_PART_ID` 为标识，每层必须有且仅有一个 TEXT；值来自该 TEXT，不从文件名/邻近对象/编号模式推断。编号遵循冻结契约的 ASCII 标识符约束。这是明确交换约定，不是任意标题栏理解。
- `DM_DIAMETER` 层保存选定直径文字，已知数值仅支持 `%%c20 +/-0.1` 形式的标称/对称公差。该受控约定中标注单位继承源 `$INSUNITS`，仅 4/mm、1/in 或 0/unknown，未知/缺失单位不猜 mm。未知/不可读尺寸保留原文字与实体，以全 null 数值和复核状态输出，不丢失后再报高召回率。

## 来源中的显式关联

在标注 TEXT 上读取 appid=`DATAMIND` 的 XDATA：

```python
[(1000, 'diameter_target'), (1005, target_circle_handle)]
```

1005 引用必须真实存在并指向本 Model 中的圆。一个引用才确认关联，多个有效引用保留 ambiguous，失联/错类型/重复/格式错误明确拒绝。没有该 XDATA 时列出本模型圆候选，多个为 ambiguous，零或一个为 unresolved，不选最近圆；候选超过 100 拒绝有界 profile。XDATA 是自定义交换引用，不是通用 CAD 原生 DIMENSION 的对象推理，也不表示厂方批准/来源权威。评测 gold 不进入读取器；合成作者提供的关联已明确写进源 DXF，这使样例能验证引用读取，不证明能从任意图纸恢复关联。

模型里所有实体属于逻辑 `model` 视图，用实体范围构成可显示边界；当前没有识别工程图正视/侧视/剖视图或跨视图关系。业务关联仍要求明确图号/图纸版次/零件/源哈希，不等同工艺版次；后续图纸 API/入库前仍需接源注册与审批。

## SVG 与交互预览

`ingestion/cad_render.py` 从验证后的实际提取实体生成纯 SVG 和离线 HTML，每个图元绑定原句柄；Y 轴反转用于屏幕显示，原 JSON 坐标不改。点击标注或目标显示原文/坐标/源哈希/关联依据，并高亮明确关联的双方。纯 SVG 也保留逐实体标题和定位 ID。文本与属性经过转义，JSON 脚本数据转义 `<>&`，不加载远程资源。

图形采用统一颜色/线型；文字 bbox 来自 ezdxf 的近似默认字体计算，SVG 字体也近似，对应警告为 `CAD_TEXT_BOUNDS_APPROXIMATE`。未评测文字轮廓/字体还原、像素级定位 IoU 或打印保真；不能按预览像素量取制造尺寸。[官方 bbox 文档](https://ezdxf.readthedocs.io/en/stable/bbox.html) 也说明文字边界存在精度限制。几何直径只用于矛盾诊断，制造尺寸仍采用明确文字；差异保留，未知单位和对象关联不确定均被使用门禁排除。

## 运行与冻结

```bash
# 在本项目虚拟环境安装可选依赖：
python -m pip install -r requirements-cad.txt
python scripts/parse_cad.py data/cad_demo/development/dev-mm.dxf \
  --document-id doc-dev-mm --provenance synthetic_demo --output-dir tmp/my-cad-preview
python -m unittest discover -s tests -p test_cad_dxf.py -v
python scripts/eval_cad_dxf.py --suite data/evaluation/cad_dxf_acceptance_v1.json \
  --output reports/cad_dxf_reproduction.json
python scripts/test_isolated.py
```

输出目录必须不存在；保存 snapshot.json、drawing.svg 和 preview.html。解析失败不生成成功目录，中途写入失败清理本次新目录，已有目录不覆盖。评测报告以独占模式创建，首轮不覆盖。

`dxf_reader_v1_freeze.json` 及 `data/evaluation/frozen/cad_dxf_v1/` 保存读取器/契约/渲染/CLI/评测器/依赖/测试/编写器的 8 文件哈希和快照、工具及环境版本。先开发/修复，再冻结，最后生成新验收图；验收检查冻结代码、ezdxf 版本和每份源 SHA。代码改变要使用冻结快照复现或开展新版本，不能覆盖旧首轮。

## 本轮证据与分母

首次专项回归为 15 项、5 失败/8 错误：库写出后 Standard 字体默认 font=`txt`，被初版空字体限制错误拒绝；修正为受控默认字体并检查生成标志后 **15/15** 通过。初始日志保留 `reports/cad_dxf_targeted_initial.txt`，最终为 `reports/cad_dxf_targeted.txt`。两份独立开发图均通过，报告 `reports/cad_dxf_development_initial.json`。

冻结后新图 6 份首轮 **6/6 场景符合预期**：毫米/英寸 2 份可用，1 份无显式引用保持多候选，1 份标注/几何矛盾被门禁排除，1 份未知单位保留待复核，1 份带 ARC 被拒绝。后 4 份不能被称为“成功理解且可用于制造”的图纸。源自同一作者和约定模板，编号/字段顺序/尺寸/公差/坐标/单位有变化，非独立工厂盲测。

| 指标 | 新样本首轮 | 含义 |
|---|---:|---|
| 标题字段 | 15/15 | 5 份受支持文件 × 图号/图纸版次/零件；值与句柄一起正确 |
| 已知尺寸字段 | 16/16 | 4 份已知单位文件 × 标称/下偏差/上偏差/单位；含保留真实几何冲突和关联不确定的文件 |
| 原生实体与定位 | 35/35 | 5 × 7 实体，核对句柄/类型/层/原文本/插入或几何坐标；不含文字 bbox IoU |
| 标注定位/对象关联/使用门禁 | 各 5/5 | 包括 confirmed/ambiguous 和拒绝使用的预期状态；不是推理关联准确率 |
| 未知单位字段保留 | 4/4 | 全 null 数值和 unknown 单位，没有填造已知值 |
| 不支持图元拒绝 | 1/1 | ARC 未被静默丢弃 |
| SVG 原句柄覆盖 | 5/5 文件 | 每个已提取实体有对应可定位 SVG 元素；不代表像素保真 |

完整隔离回归 **153/153**（上一阶段 138 + 本轮 15），`reports/cad_dxf_regression.txt`；不调用模型，临时库删除。`reports/cad_dxf_acceptance_initial.json` 保存全部逐例输出/警告/门禁；浏览器核对真实源句柄 3B 的标注和 38 的目标同时高亮、详情显示原文与 SHA，截图 `reports/cad_dxf_browser.jpg`。冻结后未改读取代码，另验证改动副本代码拒绝和首轮报告不覆盖。

API 保持 0.10.0，未接图纸上传/持久化/公开图纸 API、认证的关联复核、通用图纸 PDF/扫描/DWG 或生产尺寸问数。下一步接图纸资产、源版本和实体/特征查看接口；扩大格式/原生尺寸/真实授权图纸与非自定义模板时必须另做可复现验收。
