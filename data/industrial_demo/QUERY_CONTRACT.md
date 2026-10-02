# 工业生产问数 v1 契约

本契约用于自建合成工业库的模型 SQL 问数，开发标签在实现前冻结于 `industrial_nl2sql_v1.json`。原始 seed、PDF、跨源规则与失败记录不改写。

- SQL Catalog 仅暴露 `demo_industrial.parts` 和 `demo_industrial.measurement_observations`。后者由批次及测量构成，粒度固定为一条 measurement_id；每条视图行是已有测量，不代表没有测量的生产批次。
- 仅支持单层 SELECT：零件目录、已有测量批次计数、测量计数、按零件分组、原始/归一化温度明细、均值/最大/最小及未知单位。禁止模型访问元数据、审批/版本表或 PDF 真值。
- 批次计数为 `COUNT(DISTINCT batch_id)`；测量计数为 `COUNT(*)` 或 `COUNT(measurement_id)`。均值按测量粒度计算，不按批次平均。温度聚合使用 `temperature_c`，degC 原值、K 减 273.15、其他单位 NULL；未知单位不参与均值，明细保留原始 value/unit。
- 明确年月（或年份）以生产时间 `produced_at`、Asia/Shanghai 半开区间过滤。单一批次查询可不指定月份。缺失或歧义时间/工序/实体请求在模型调用前澄清；多实体、多工序、测量时间口径另行扩展。
- 不使用 SQL 登记的 report_id 推断 PDF 存在、批准或真值。超限、合格、适用工艺版本及与报告的一致性请求应进入现有跨源规则工具；强制 SQL 模式时明确澄清。
- 模型只能生成 Catalog 支持的 SELECT；AST 校验实体、工序、时间、粒度、聚合与允许的过滤条件，所有常量绑定；只读账号与事务、限定 search_path、时间与行数上限保持生效。
- 模型成绩逐例记录真实 SQL、绑定值、结果、重试、模型/提示词/Catalog/代码版本和失败。注入式测试、固定 SQL、gold 执行、模型前澄清分开报告，不计作模型正确答案。
