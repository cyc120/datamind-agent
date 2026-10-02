# 工业演示契约 v1

来源：仓库自建合成样本，不包含工厂生产数据。原文中的审批及结论也是模拟声明，不代表真实审批或实际产品质量。

- schema `demo_industrial` 位于元数据库，独立于零售/Chinook；写入只用于演示 seed，查询使用 readonly_user，只读事务、参数绑定、5 秒超时。
- `parts` 1:N `production_batches`；批次 1:N `process_measurements`；每个 measurement_id 至多一个 `quality_records` 登记。用独立测量 ID 比较，禁止把两次采样笛卡尔积或平均后比较。
- 工艺版次 1:N operations，同版工序唯一；有效期为 `[valid_from, valid_to)`，空结束时间仅在原文明确“无”时表示开放区间。只接受源中明确已批准的版次；重叠、未知与缺失均不可裁定。
- 温度指标只实现 `holding_temperature`，方法为显式 core/surface。已登记单位只有 `degC` 与 K，Decimal 换算 `degC = K - 273.15`；未知单位保留原值。
- PDF dossier 与 SQL seed 分开读取。工艺范围从实际 PDF 文本层提取，报告实测值从报告页提取；seed/gold 不传入解析器。PDF 标签位置、源 SHA、解析版本与字段摘要均保留。
- 跨源比较的键：零件、工序、批次、measurement_id、quality_id/report_id、指标、测量时点、方法。编号或口径不符转复核。未批准报告不用于确定的“一致”判断。
- 工艺要求和生产实测值的差异叫“超限”；同一测量的生产记录与报告原值不一致叫“来源冲突”。无权威裁定规则时冲突保持 unresolved，原值不覆盖，禁止汇总成确定的“合格”。
- 支持问题范围：显式零件编号、工序号、年月的温度核验；其他问题需澄清或走原有单源路径。此次采用固定查询与规则模板，不计 NL2SQL/LLM 准确率。

冻结源 `seed_v1.json`、人工预期 `../evaluation/industrial_v1.json` 均为研发集；不是独立泛化基准。包含双采样、边界、单位等价、过期/重叠工艺、未知单位、错实体/方法、待审及缺失报告。
