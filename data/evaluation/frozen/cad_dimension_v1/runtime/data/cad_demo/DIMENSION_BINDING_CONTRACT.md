# 直径测量、显式图纸绑定与独立使用批准契约 v1

冻结日期：2026-10-06，版本 `cad-dimension-binding-v1`。本阶段是纯函数/Schema/合成源文件及冻结 CLI 的契约验证；**没有生产尺寸登记或审批 API，没有真实认证/ERP 集成，也没有正式图纸审批或产品质量结论**。代码 API 保持 0.12.0，既有认证关联复核功能不变。

现有工业生产库为温度测量，不作为图纸直径值。新样例单独声明尺寸源、批次/工序/工艺版次/方法，不写入运行知识库或生产表。所有身份、角色和批准输入为合成作者声明；即使结果 `compared`，也固定返回 `authentication_verified=false`。不能把上传的这些 JSON 直接当可信业务权限或批准。

## 输入链与版本

`DimensionMeasurement` 限正值或未知值的 diameter 测量，保留原字符串、单位 mm/in/unknown、零件、批次、工序、工艺版次、尺寸角色和方法，以及带时区的测量时刻。`MeasurementSource` 单列源 ID、记录版本、源 SHA、provenance 和 `/measurements/0` 一类 JSON 指针；CLI 逐字节核对源文件 SHA、元数据和具体记录，不读取温度字段，不把图纸值当实测值。

`DimensionBinding` 显式绑定图纸资产/文档版本/源 SHA/解析版本/bundle SHA、图号/图纸版次/零件、特征 ID/哈希及目标句柄；另绑定测量 ID/记录版本/源及载荷哈希、批次/工序/工艺版次/尺寸角色/方法。图纸 R2 不等于工艺 PROCESS-A200-R5，模型不推定二者关系。绑定含半开有效期 `[valid_from, valid_to)`，针对测量的历史时间，不按最新文件或查询当天自动选版。

`AssociationReview` 引用源/图纸/特征/解析/bundle、已确认的目标及复核账户/角色声明；必须与绑定中的 review_id/完整 review SHA 一致，目标在原候选集合中。未确认、未找到目标、几何与标注矛盾或单位未知继续阻断。人工覆盖只在临时快照中构造；原 bundle、原始候选和文字不修改。

`ApprovalEvent` 独立绑定 binding ID/版本/载荷哈希、review ID/哈希及测量载荷哈希，记录前序 ID、approved/rejected/revoked、批准账户/角色/理由/时刻。登记声明须 admin，复核声明须 reviewer/admin，批准声明须 approver/admin；批准人必须同时区别于登记人及复核人。角色检查是输入契约政策，**不是实际会话/权限验证**，后续服务必须从锁定的认证记录投影，禁止客户端自报。

`EvaluationContext` 必须由未来可信适配层提供当前文档/源、测量记录、复核及绑定的登记头和 evaluated_at。纯函数无法自行查询“当前”；caller 自报上下文只能模拟，不能赋予生产有效性。换版、更换测量源/值或新复核应更新真实当前头，旧绑定/批准会被阻断；历史回放需使用持久化旧上下文，不能把旧文件冒充当前源。

## 门禁和比较

1. 严格类型、有限 Decimal 字符串、同源定位和时区；不接受温度 metric、隐式类型转换、额外权限字段或指数/非有限数值。
2. 核对复核/绑定/批准记录载荷哈希、当前登记头、图纸身份/源/解析/bundle/特征、完整生产范围和测量记录。
3. 核对测量时刻在绑定有效期、且不晚于 evaluated_at；再核对确认关联和原生单位/原文/几何门禁。几何不以公差宽度容忍，也不覆盖标注制造范围。
4. 批准历史最多 1000 条，输入顺序必须形成无重复 ID、正确前序、时刻非递减的链；撤销须前一状态 approved。全部事件绑定同一份当前绑定/复核/测量，并检查独立角色声明。未来事件不提前生效。
5. 无批准、不批准、撤销均返回 blocked、comparison=null。按明确 evaluated_at 可重放撤销前状态；已经保存的旧结果不因新事件而改写。
6. 只有声明的独立批准有效且全部门禁满足才比较。mm/in 按精确 25.4 以 Decimal 计算，不四舍五入，公差上下界含端点；返回 `within_annotation_range` / `outside_annotation_range`，保留双方原值/单位及标准化毫米值。

比较只针对这一条图纸标注和明确绑定的单次 diameter 测量；不称产品整体合格、不改测量行、不批准工艺、不授予图纸权威。输出独立列 `formal_drawing_approval=false` 与 `product_quality_conclusion=false`。本契约不检查设备校准、不确定度、抽样规则或真实授权，后续真实质量规则需另立范围，不能扩张当前输出。

## 冻结、样例和复现

`dimension_binding_v1/measurement_source.json` 为人工合成实测声明 20.04 mm；图纸为既有 `development/dev-mm.dxf` 的 CAD-DEV-1/R2/A200、20±0.1 mm、标注3B/圆38。`evaluation.json` 手工提供当前头/复核/独立批准声明；`expected.json` 为作者明确预期 19.9–20.1 与 20.04，比较器不读预期标签。不是新工厂样本、独立盲测或准确率评测。

`dimension_binding_v1/manifest.json` 冻结实现、Schema、测试/夹具、验证 CLI、依赖、源 DXF/测量 JSON、声明/预期和本说明；源码及材料快照位于 `data/evaluation/frozen/cad_dimension_v1/runtime/`。CLI 拒绝哈希/库版本/Schema/源记录变化，报告采用独占新路径，不覆盖首轮证据。哈希是复现措施，不是签名或防管理员篡改。

```bash
.venv/bin/python -m unittest discover -s tests -p test_cad_dimension_contract.py -v
.venv/bin/python scripts/verify_cad_dimension_contract.py
# 新报告必须使用不存在的路径：
.venv/bin/python scripts/verify_cad_dimension_contract.py --output reports/cad_dimension_reproduction.json
.venv/bin/python scripts/test_isolated.py
```

新增 16 项契约测试：批准前/批准/不批准/撤销与历史时刻、独立主体/角色、范围/当前头/完整版本/源哈希、关联/单位/几何、半开有效期/时区、严格值/结构、事件链与不可变输入。成绩是机制测试数，不是 16 张图纸或真实生产准确率。

## 后续适配要求

先实现真实登录的尺寸记录登记/只读源适配、不可覆盖记录版本、显式绑定及另一账户使用批准接口；事务内锁定身份、当前文档/测量/复核/绑定头，处理幂等、并发、撤销与持久化回放。认证复核需要来自既有已校验的 cad_association_reviews，不从客户端 JSON 接受角色/当前状态。新增生产尺寸数据和校验规则必须与既有温度材料分开；只接受经授权真实模板/测量源。当前 API 和页面尚未接本纯函数，通用 CAD/工业计划、V-CAR 和完整 M6/P1 仍待完成。
