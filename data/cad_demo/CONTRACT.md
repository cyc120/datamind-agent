# 二维 CAD 输出及业务关联契约 v1

冻结日期：2026-10-05；版本 `cad-contract-v1`。本轮交付的是可执行输出契约和门禁，**没有 DXF 读取器、DWG 转换、图纸识别、数据库迁移或图纸 API**。合成 DXF 和对应 JSON 均为人工编写；JSON 不是解析器运行结果，作者关联标签不是工厂授权。原工业温度成绩不用于 CAD。

## 对象和版本

`source` 分列文档 ID、文档版本、源文件 SHA-256、解析版本和材料来源；`identity` 分列图号、图纸版次和零件号，每项必须对应同源原生 TEXT。图纸 R1 不能推定为工艺 A102-V2；业务调用方必须提供显式 `BindingContext`，图号/图纸版次/零件/源哈希全部一致才返回尺寸候选。契约没有批准状态，不自动批准图纸、尺寸或产品，也不按最新文件日期选图纸。

## 当前限定几何和定位

- 首个输出族为 DXF `Model` 布局、`WCS_XY` 二维世界坐标。LINE/CIRCLE/TEXT 必须有唯一大写十六进制句柄、原图层和视图 ID；坐标有限，线非零长，圆半径正。实体范围必须落在所属视图内。单位仅接受源 `$INSUNITS` 0/1/4，对应 unknown/in/mm；单位缺失时保留几何但阻止尺寸业务使用。不得根据图纸文字或几何大小猜模型单位。
- Locator 为 `cad_entity`，携带源哈希、句柄、图层、视图、布局和坐标系，逐项关联实体。不使用 PDF 页码或像素框代替模型坐标。当前 TEXT 的 bbox 是手工合成标签；真实适配器需要计算文字范围并验证字体/旋转。渲染及实体高亮尚未实现，当前契约不能宣称可在浏览器打开图纸证据。
- 首个尺寸族仅 `diameter`，保留完整 `annotation_raw` 和原 TEXT 引用；数值字符串不隐式转换、不接受非有限值/指数写法，范围用 Decimal 原单位计算。唯一允许的已知数值文本形式是 `%%c20 +/-0.1` 一类 ASCII 直径和对称公差，值、公差须与原文一致。未知/未识别数值允许三个数值字段全 null，进入复核；非对称公差、DIMENSION 覆盖文字、MTEXT、块属性、INSERT、OCS/3D/旋转变换、其他单位或图纸 PDF/扫描均留待后续版本，不能静默降为成功。

## 对象关联和门禁

`association_status` 为 confirmed/ambiguous/unresolved。确认必须恰好一个目标、明确选中它，并提供绑定源版本/标注句柄/目标句柄的 `native_reference` 或 `human_label` 依据。目标必须是同视图圆；多候选不按距离选最近者，未确定的候选不生成制造范围。合成确认来自 `association_labels_v1.json` 中的作者标签；本轮不解析也不验证真实 native_reference，后续适配器须解析真实引用或接认证人工复核，不得把 JSON 中的声明当真实证据。

`eligible_features` 每次重校验快照和业务上下文，输出 ready/needs_review、可用字段、排除项和原因。它是供未来可信适配器使用的纯函数，并不验证原 DXF 字节或注册来源。CLI 额外核对已冻结文件/源哈希及作者标签，但也没有验证 JSON 是否忠实于 DXF。结构验证和源哈希不能证明提取正确。

当圆的几何直径与明确标注的标称值不相等，返回 `CAD_GEOMETRY_ANNOTATION_CONFLICT`，双方保留，不把公差误差当几何一致的容忍阈值。已知英寸按精确 25.4 mm 换算比较。几何只用于差异诊断；制造尺寸取明确标注。本轮输出辅助候选和公差范围，不读取生产尺寸，不产生产品合格/不合格结论。

## 冻结与复现

`manifest_v1.json` 冻结契约实现、生成 JSON Schema、测试、CLI、合成 DXF、手工快照、作者标签、绑定上下文、预期门禁和本说明的 SHA-256。CLI 拒绝任何哈希变化，报告路径以独占方式创建，不覆盖首轮证据。该机制用于可复现性，不是签名或防管理员篡改；JSON Schema 仅表达结构约束，引用/原文/几何关联必须同时运行 Python 验证器。

```bash
python -m unittest discover -s tests -p test_cad_contract.py -v
python scripts/verify_cad_contract.py
# 保存新报告需使用不存在的路径：
python scripts/verify_cad_contract.py --output reports/cad_contract_reproduction.json
python scripts/test_isolated.py
```

当前样例是开发契约材料：一张合成图纸、7 个实体（4 TEXT、2 CIRCLE、1 LINE）、1 个作者确认直径。负例由独立契约测试变更得到，不是独立识别验收集，不报字段召回率或 CAD 准确率。

## 下一步 DXF 适配验收条件

实现原生 DXF 读取及实体渲染，记录工具版本/警告，拒绝 DWG/不支持布局或坐标变换。实体读取不得接 gold；标签由评测器单独读取。先完成独立开发图，再冻结读取器和评测器，新增未参与修复的图纸；同一图纸的渲染/扫描变体归同一划分。分列标题字段、标称/公差、句柄/定位和对象关联的分母，保留覆盖文字矛盾、未知单位、错版、多候选和缺失实体，另设 CAD 成绩，不复用温度基准。真实授权图纸和来源审批需要单列。

单位依据：[ezdxf 官方 DXF Units](https://ezdxf.readthedocs.io/en/stable/concepts/units.html)。结构模型依据：[Pydantic 官方 Models](https://docs.pydantic.dev/latest/concepts/models/)。工具文档说明不等于本项目已接入这些读取能力。
