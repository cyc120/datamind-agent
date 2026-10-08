# DataMind Agent 项目进度

更新日期：2026-10-07。当前代码 API：**0.27.0**；本轮在契约之上加入**媒体类型探测与 Adapter 路由分发**（`detect_media_type` 按文件签名判断，未知返回 octet-stream、不可读报 `PARSE_MEDIA_UNREADABLE`；`register_adapter` 接受 handler；`dispatch` 按类型路由、调用 handler 并校验返回值；`parse_pdf` 经该路由执行并在质量明细回报 `adapter=pdf-native`）；上一轮把**统一解析契约接入生产摄取链路**（`ingestion/ingest.py::parse_pdf` 在返回前把真实块映射进契约并校验，结果写入质量明细 `parse_contract`：`parsed`/`failed`/`skipped` + 稳定错误码；契约违反不中断摄取但保持可见；真实原生/扫描/公式 PDF 均验证为 `parsed`）；上一轮**并行推进两条线**：按 §5.2/§5.4 新增**统一解析契约**（`datamind/parse_contract.py`：parse_status 与摄取状态机、七类 SourceLocator 校验、Block/Relation 约束、Adapter 能力登记、真实 PDF 块映射；`docs/PARSE_CONTRACT.md`），并按 §13 新增**CPU/内存限额与跨主机租约**（`cpu_seconds` 经 RLIMIT_CPU 生效；`memory_bytes` 在平台不支持时失败关闭；migration 013 的 `task_leases` 提供接管递增 fence 的协作式租约）；上一轮按 §6.4/§9.2 新增 **V-CAR 机制消融与修复/冲突指标**（`datamind/ablation.py`：四条机制臂在 16 例冻结合成集上对照，按 §9.2 报告修复成功率、误修复率、冲突 Precision/Recall、正确/错误/未消解与触发比例，零分母报告 N/A；实测 `local` 臂 2 例误修复、`full` 臂 0 误修复并保留 2 例未消解）；上一轮把 **V-CAR 节点接入统一任务预算与 HTTP**（`bounded_runners` 在独立进程组内执行节点、按工具占跨进程资源槽、取消可传播、节点时限取自全局剩余预算；`POST /v1/plans/execute` 目标由服务端白名单决定，未配置即 fail closed）；上一轮按技术文档 §6.1/§6.4/§15-P3 新增**通用 V-CAR 依赖计划**（`datamind/vcar.py`：节点契约与拓扑校验、独立节点并发、失败只重跑受影响子图并保留原始失败、统一全局预算 `deadline/max_steps/max_rows/max_tokens/max_repair`、无权威规则的冲突终止自动消解并返回双方证据、断点恢复）；上一轮为已发布私有 PDF 向量点的受控修复与孤立点回收（`/v1/pdf-knowledge/publications/{id}/repair`、`/v1/pdf-knowledge/orphans`、`/orphans/reclaim`；只按冻结计划补点、默认 dry-run、仅限 `pdf_private_*`、不改计划/发布行/当前头）；上一轮把扫描页 OCR 与公式会话接入统一任务预算（运行器泛化为可执行任意命令/独立解释器；`ocr.page_blocks_bounded` 与 `formula.recognize(dsn=…)` 在进程组内受 deadline/槽/取消约束，ingest 可选启用）；上一轮为统一任务预算原语（`datamind/task_budget.py`），再上一轮为受控私有模型回答。完整回归在隔离数据库副本验收；原检出和原 8000 服务未升级。设计主入口：[V1.3 技术设计说明书](技术文档/DataMind_Agent_技术设计说明书_V1.3.md)（0.27.0 基线），历史阶段记录保留在 [V1.2 说明书](技术文档/DataMind_Agent_技术设计说明书_V1.2.md) §18.12–18.33。私有知识发布提交 [`6913ccc`](https://github.com/cyc120/datamind-agent/commit/6913ccc9a459b7bd91a72f72ec642d56e92d8a24) 已随 PR #1/#2 并入 `main`。仓库：[cyc120/datamind-agent](https://github.com/cyc120/datamind-agent)，当前只保留 `main` 分支。

本次推进：把 V-CAR 节点接入统一任务预算与 HTTP（`datamind/vcar.py` 的 `bounded_runners` / `ExecutionContext` / `run_request`：节点在独立进程组内执行、节点时限取自全局剩余预算、按工具占跨进程资源槽、取消传播杀进程组；新增 `POST /v1/plans/execute`，目标由服务端白名单决定）。这一轮之前依次完成：通用 V-CAR 依赖计划原语、私有向量修复与孤立回收、统一任务预算与进程时限、扫描页 OCR/公式接入同一预算、受控私有模型回答。当前完整隔离回归 **549/549**。历史分母（465/465、480/480、502/502、513/513、523/523、539/539）与原始失败全部保留，不跨轮拼接为模型成绩。

## 现阶段优先级（2026-10-07 确认）

1. **以技术实现为主**：按 [V1.3 技术设计说明书](技术文档/DataMind_Agent_技术设计说明书_V1.3.md) §15 的顺序，把文档中仍标记为「规划／待实现」的技术项落地；每次改动都要有测试、验证报告与文档同步，不破坏既有回归。
2. **赛题交付包（ZIP）等前置技术任务完成后再做**：在此之前不投入交付打包、答辩 PPT、官方专项核对与《突破与创新性自述》。
3. **授权真实输入单独验收**：真实模板、图纸、扫描件需外部材料，拿到后另立样本与分母，不复用合成材料成绩。

## 当前交付范围

| 能力 | 已完成 | 验证/限制 |
|---|---|---|
| 认证尺寸服务与页面 | 单条测量登记/版本、显式绑定、独立使用批准/撤销、当前核验、保存快照、分页历史回放；`/#dimensions` | 三角色页面与隔离回归通过；真实 ERP、计量校准与制造授权待扩展 |
| 多文件登记队列 | DXF/单测量 JSON 上传、逐文件状态、全局/批次限额、租约、幂等、失败重试、协作取消；`/#files` | 必须独立启动 CLI worker；PDF/OCR/Office 未并发化，成功文件不因同批失败回滚 |
| 显式多文档问答 | 1–8 份无重复公开文件、精确版本/源/解析器、每文件相关片段、生成结束重验、检索/实际引用覆盖 | 已索引文档库范围；不是全文审阅或通用跨文件语义裁决，真实模型评分未在本阶段重跑 |
| 原生 PDF 离线准备 | 按 SHA 固定源、版本化块/切片、独立 attempt/进程、封存重放与摘要校验、旋转/裁剪坐标和保守图片预算；可选 `--deadline` 在独立进程组内运行 | 31 项合成边界与 8 项 CLI 验证；不发布到知识库，不替代 OCR/表格/公式模型；仍非 CPU/内存 cgroup 硬隔离 |
| PDF 私有快照登记 | admin/CSRF/同源、当前头 CAS、同 owner 原请求恢复、固定版本原文件/页图/bundle、读取后权限重验、分页元数据 | 服务 27/27、HTTP 19/19；不索引、不裁定权威、不接上传/页面；两个验证槽仅单服务进程，5 秒仅单次 DB 锁等待 |
| PDF 私有知识发布 | 只入队 HTTP、冻结向量计划、独立认证 Qdrant、实际点确认、私有头 CAS、token/lease 栅栏、原 owner 重试、显式历史切片 | 全 worker 最多 2 个 running；无解析/OCR 或公开 RAG；发布后点损失可由受控修复入口补齐 |
| PDF 私有多文件检索 | 固定 publication/完整计划 SHA，1–4 份文件、同 embedding profile、本地 Dot、逐文件覆盖、完整点与 DB/文件复核 | 两个跨进程检索槽、总候选 512；本接口只返回证据（模型回答见下一行）；仍未接入 CPU/内存硬隔离与完整 deadline |
| PDF 私有模型回答 | 只用本次选定私有片段生成；原文连续片段与数字支持校验、最多一次修复、文件未覆盖即拒答；生成后重查身份与证据 | 15 项组件/契约测试；不接公开知识、不写向量；真实模型忠实度与准确率未评测 |
| 私有向量修复与孤立回收 | 按冻结计划补齐丢失点（不改计划/发布行/当前头）；孤立点扫描与默认 dry-run 回收，仅限 `pdf_private_*`，删除前后重读引用集 | 10 项组件/契约测试；测试用内存 Qdrant；无自动调度与告警，真实认证私有 Qdrant 烟测未重跑 |
| V-CAR 通用依赖计划 | 节点契约与拓扑校验；独立节点并发；失败只重跑受影响子图且保留原始失败；统一预算与两次修复上限；冲突终止自动消解；断点恢复 | 16 项单元测试；视觉载荷/局部重识别与机制消融待做 |
| V-CAR 受控节点执行 | 节点在独立进程组内执行；节点时限取自全局剩余预算；按工具占跨进程资源槽；取消传播杀进程组；目标由服务端白名单决定 | 10 项组件/契约测试；HTTP 需运维显式配置注册表；同主机执行，非同主机租约 |
| V-CAR 机制消融 | 四条机制臂在同一冻结集对照；修复成功率、误修复率（含占原正确输入份额）、冲突 P/R、正确/错误/未消解、触发比例；零分母 N/A | 18 项测试；合成 16 例、机制替身，非模型精度；真实视觉载荷与模型规模对比未做 |
| 统一解析契约 | parse_status 五态与摄取状态机；七类 SourceLocator 校验；Block/Relation 约束；Adapter 能力登记与 handler 分发（签名探测→路由→校验）；真实 PDF 块映射不补造；parse_pdf 经路由产出并校验契约 | 36 项测试；契约存储列未接入；仅注册 PDF 适配器（ZIP 类只识别为 application/zip）；Chunk/DocumentFact/DrawingFeature 未纳入 |
| CPU/内存限额与跨主机租约 | cpu_seconds 经 RLIMIT_CPU 生效；memory_bytes 平台不支持时失败关闭；task_leases 协作式租约与接管递增 fence | 11 项测试；本机 macOS 不支持 RLIMIT_AS；非 cgroup 硬隔离、非分布式共识；租约未接入执行路径 |
| 统一任务预算与进程时限 | 跨进程资源槽、挂钟 deadline、进程组终止（含孙进程）、取消传播、输出上限与稳定超时码；登记 worker、PDF 准备 CLI、扫描页 OCR 与公式会话可选接入 | 34 项组件/契约测试；非 CPU/内存 cgroup 硬隔离；跨主机租约待接入 |
| 结构化单源问数 | Catalog、Schema Linking、SQL Guard、参数化只读执行、修复、Trace/SSE/回放 | 历史零售 14/14，鲁棒性 4/4；未在本次重跑模型评分 |
| 文档问答 | Hybrid/RRF 检索、真实模型生成、原文引用/定位与拒答 | 最近完整 RAG 10/12，单题上游错误复测 1/1 单列；语义蕴含未独立验证 |
| OCR/公式/字段 | 原像素 OCR、隔离公式 worker、表头与字段候选、文件/块/页坐标校验 | 历史扫描表头 5/9、字段 3/15；完整表格关系未达成 |
| 工业跨源规则 | 合成工业底座、17 页工艺/质检 PDF、单位/版本/逐测量核验和冲突持久化 | 13/13 开发规则；真实冲突保留双方原值，未登记权威时不选来源 |
| 工业模型问数 | 限定工业 Catalog、计数/温度聚合/明细、语义门禁与回放 | 8/8 开发题、2/2 模型前澄清；初始 7/8 评测类型错误及同输出复算均保留 |
| 新工业模板测试 | 4 页原生/扫描材料、56 字段区域标签、首轮冻结与逐项报告 | 28/56 字段，2/4 事实；原生/扫描各 14/28；不是独立工厂盲测 |
| 紧凑工业格式 v2 | 半角/全角冒号、标签内空格、工艺/质检上下文、原块定位与单位核验 | 单独开发 52/52 字段、4/4 事实；冻结后新样本首轮 104/104、8/8；合成材料 |
| 字段人工复核 | 登录账户、复核权限、原页定位、确认/存疑/退回、版本审计、历史、幂等/并发处理 | 历史未认证记录不补身份；复核只针对提取忠实度 |
| 来源权威规则 | 管理员登记、另一审批账户启用/不批准/撤销、精确范围/版本/生产记录/依据页绑定、独立证据与历史回放 | 仅本地合成温度数值冲突；依据内容的业务授权含义由人核对，未做真实工厂授权验收 |
| 尺寸绑定与独立使用批准契约 | 显式测量/批次/工序/工艺版次/方法、图纸/复核/批准哈希、有效期、独立角色声明与撤销回放 | 冻结 13 文件；契约阶段新增 16 项、隔离回归 195/195；纯引擎不验证真实认证，服务适配另列 |
| CAD 认证关联复核 | reviewer/admin 确认/存疑/退回、明确候选选择、版本/源/快照绑定、历史与幂等/并发门禁 | 复核阶段新增 12 项、隔离回归 179/179；换版不继承；原图与提取不覆盖；不代表正式图纸审批 |
| 图纸资产与版本查看 | 认证登记、固定源副本/快照、版本历史、实体/特征/原文件/预览接口及页面 | 资产阶段新增 14 项、隔离回归 167/167；v2 后 v1 原文件/预览/特征哈希不变；正式图纸审批待扩展 |
| 原生 DXF 与实体预览 | 二维 LINE/CIRCLE/TEXT 源读取、图层标题/显式 XDATA 引用、尺寸/单位/门禁、句柄 SVG/离线交互 | 冻结后新合成图 6/6 场景符合预期：2 可用、3 保留/排除、1 不支持拒绝；限定自定义交换 profile |
| CAD 契约与关联门禁 | 冻结源/图号/版次/句柄/模型坐标/单位、LINE/CIRCLE/TEXT 及直径/公差、对象关联与错版/矛盾门禁 | 合成手工快照 7 实体、1 尺寸；12 项契约检查；没有 DXF 读取/渲染或 CAD 准确率 |
| 独立字段审批 | 审批权限、另一账户批准/不批准/撤销、文件/提取/复核版本绑定、独立审计记录 | 本地字段审批；不覆盖原文声明、不裁定来源权威；不等于工厂审批系统 |

当前数据：27 文档资产、23 候选事实、169 切片/向量；工业底座有 3 零件、12 批次、13 测量及 13 质检登记。测试模板未混入运行知识库。上一阶段浏览器提交测试使用隔离副本，测试后已清理；本轮私有检索测试/真实烟测的 PDF 与私有产物也已清理。正式文档没有被代理复核。

本阶段没有修改原库的数据、模型配置或复核记录。完整回归在临时元数据库中把原 17 页工业文档重新提取为 v2，验证新版读取/复核和旧模块兼容；模板仅在离线评测中使用。测试库不复制模型设置、账户、会话、登录限流、权威规则、CAD 资产、关联复核、私有 PDF 快照或知识发布行，运行后删除。

## 本阶段验收与历史证据

本轮媒体类型探测与 Adapter 路由完整隔离回归 **614/614**（上一阶段 607 + 新增 7）、失败/错误 0、退出 0，见 `reports/parse_contract_verification.json`。新增 7 项覆盖：PDF/PNG 按签名识别、未知内容返回 `application/octet-stream`、不可读路径报 `PARSE_MEDIA_UNREADABLE`；`dispatch` 路由到已登记 handler 并校验其输出；未登记类型与无 handler 适配器分别报 `PARSE_ADAPTER_UNKNOWN`/`PARSE_ADAPTER_UNSUPPORTED`；handler 输出非法、签名不符、抛普通异常与抛 `HTTPException` 四种情形的稳定错误码；handler 必须可调用。真实解析路径已改走该路由，质量明细中出现 `adapter: pdf-native` 作为**路由确实被走到**的证据。

本轮解析契约接入生产摄取完整隔离回归 **607/607**（上一阶段 603 + 新增 4）、失败/错误 0、退出 0，见 `reports/parse_contract_verification.json`。新增 4 项为摄取集成测试：真实原生 PDF 校验为 `parsed` 且块数与 locator 类型匹配；真实扫描页 OCR 块同样通过；越界块被记录为 `failed`（`PARSE_LOCATOR_INVALID`）而**不中断摄取**；无块时记为 `skipped` 而不编造结果。手工核对三份真实 PDF（原生制度、扫描巡检、公式说明）均返回 `validation: parsed`、locator 全为 `pdf_region`。

本轮统一解析契约与资源限额完整隔离回归 **603/603**（上一阶段 567 + 新增 36）、失败/错误 0、退出 0，见 `reports/parse_contract_verification.json`。新增 36 项为 25 项解析契约测试与 11 项资源/租约测试。平台事实：`RLIMIT_CPU` 受支持并实测生效（1 秒上限下 300 秒自旋被终止），`RLIMIT_AS` 在本机 macOS 被拒（`setrlimit` EINVAL），因此内存上限**失败关闭**而不是假装生效；独立进程无法取得已被占用的租约。过程记录：并行的子代理在实现期间对仓库做了越界自动改写（删除 `web/app.js` 一行行为代码、删除 `style.css` 的 `.step/.num` 规则、批量移除 import 与 requirements 条目），已中断该子代理并**逐项回退全部 33 个受影响文件**，只保留受控改动；其产出的 1234 行契约模块因与规范偏差较大且无测试而弃用，改为受控实现。租约跨进程测试首轮误用父进程原始 DSN，子进程在隔离回归中连到真实库并成功取租约；改用 `effective_dsn` 后通过，同时验证了该原语的必要性。

本轮 V-CAR 机制消融与修复指标完整隔离回归 **567/567**（上一阶段 549 + 新增 18）、失败/错误 0、退出 0，见 `reports/vcar_ablation_verification.json`；实测报告 `reports/vcar_ablation.json`。18 项覆盖：冻结数据集摘要固定与变化拒绝、畸形数据集拒绝、逐例失败保留、零分母报告 N/A、修复成功率/误修复率/误修复占原正确输入份额/冲突 P-R 与假阳性算术、冲突检测与标签解耦（同一来源相反标签得到相同检出）、臂行为（本地臂产生误修复、full 臂零误修复并把无规则冲突留作未消解并返回双方证据、ocr_text 在冻结集漏检全部真实冲突）、两次运行指标一致、脚本拒绝覆盖与数据集变化时拒绝运行。实测对比：`ocr_text` 0.1875／`structured` 0.6875／`local` 0.6875（**2 例误修复，14.29%**）／`full` **1.0（0 误修复，2 例正确未消解）**。开发中修正两处度量缺陷：机制臂原依据 defect 标签判定冲突（标签泄漏使 Precision 恒为 1），改为纯来源比对；「未察觉冲突」原被记为正确消解，现要求先检出才计分。

本轮 V-CAR 受控节点执行与 HTTP 入口完整隔离回归 **549/549**（上一阶段 539 + 新增 10）、149.719 秒、失败/错误 0、退出 0，见 `reports/vcar_bounded_verification.json`。10 项覆盖：节点在独立子进程运行且收到包装体载荷（`input` 与依赖 `inputs` 正确）；目标白名单与模块/函数名校验（未知工具、非二元组、路径穿越、命令注入样式、非法类型）；全局预算耗尽时节点被终止（2 秒预算下 `sleep 300` 在 20 秒内返回）；取消传播后子进程 pid 消失、未派发依赖标记 `skipped`；`rows`/`tokens` 累加触发 `budget_exhausted`；资源槽占满时节点失败码为 `TASK_BUDGET_BUSY`；HTTP 未登录 401/403、非法请求体 422、未配置注册表 503 fail closed、配置后真实子进程执行。开发中修掉一个真实缺陷：调用方漏传共享 context 时节点时限会静默退回默认 120 秒，现由 `execute` 自动绑定。

本轮 V-CAR 通用依赖计划完整隔离回归 **539/539**（上一阶段 523 + 新增 16）、152.249 秒、失败/错误 0、退出 0，见 `reports/vcar_verification.json`。16 项覆盖：拓扑排序与受影响子图、节点/预算/依赖/工具/权限/环/重复 id 校验、依赖节点消费前序结果、独立节点真实并发（`threading.Barrier` 验证重叠）、失败跳过依赖但不影响独立分支、**只重跑受影响子图**（独立节点恰好执行 1 次）、修复上限（attempts=3）、`max_steps`/`deadline`/`max_rows`/`max_tokens` 四类预算耗尽、未消解冲突（repairs=0）并返回双方证据、缺失执行器与非法结果、断点恢复与非法 state 拒绝。本阶段为纯编排原语，未调用模型或数据库。

本轮私有向量修复与孤立回收完整隔离回归 **523/523**（上一阶段 513 + 新增 10）、147.273 秒、失败/错误 0、退出 0，见 `reports/pdf_maintenance_verification.json`。10 项覆盖：删除已发布点后修复恢复点集且发布行/计划摘要/结果摘要不变；完整时报告 0；未知与路径式 id 拒绝；孤立点扫描只统计无引用点；dry-run 不删除、confirm 删除且引用点全保留；回收后再修复仍为 0；非私有集合与非法批次拒绝；请求体严格校验；HTTP 未登录 401/403 与 admin 全链路。测试使用内存 Qdrant，本阶段未重跑真实认证私有 Qdrant 烟测。

本轮 OCR/公式接入统一任务预算完整隔离回归 **513/513**（上一阶段 502 + 新增 11）、149.428 秒、失败/错误 0、退出 0，见 `reports/ocr_formula_budget_verification.json`。真实扫描页（`data/knowledge_base/07_门店巡检记录_2025年12月_扫描版.pdf` 第 1 页）子进程 OCR 与进程内路径的**块与 detail 关键字段完全一致**（16 块、27 行、1.79 秒）；同一页给 1 秒时限时进程组被终止并返回 `OCR_TIMEOUT`。公式会话经 `run_bounded_command` 在 `.venv-formula` 独立解释器中执行，可选占用跨进程槽并保留 `FORMULA_TIMEOUT` 等错误码。OCR 默认仍在解析进程内，只有显式 `--ocr-deadline` 才启用受控路径；本阶段未调用上游语言模型。原库计数与旧模块未变。

本轮统一任务预算与进程时限完整隔离回归 **502/502**（上一阶段 480 + 新增 22）、147.653 秒、失败/错误 0、退出 0，见 `reports/task_budget_verification.json`。新增 22 项为 17 项任务预算单元测试与 5 项登记接线/映射/取消测试。真实子进程（`sleep 60`）在 1 秒挂钟时限内被终止，`sh -c 'echo $$ > pid; sleep 60'` 的孙进程一并消失；10 万字节 stdout 被截到 4096 且无法伪造结果；三个并发槽中第三个立即 `503/TASK_BUDGET_BUSY`，异常退出后释放；`effective_dsn` 指向当前实际数据库；`cancel_event` 置位后运行中的子进程被立即终止并返回 `TASK_CANCELLED`。CLI 冒烟：真实原生 PDF 在 60 秒时限下准备成功、封存清单 `verified=true`，非法时限与孤立 `--grace` 均以 exit 2 拒绝，产物已清理。原库计数与旧模块未变。

本轮受控私有模型回答完整隔离回归 **480/480**（上一阶段 465 + 新增 15）、142.189 秒、失败/错误 0、退出 0，见 `reports/pdf_answer_verification.json`。新增 15 项为 12 项生成/校验单元测试与 3 项 HTTP 契约测试；检索结果与模型回复均为明确合成注入，未调用上游模型、未下载、未改原库。校验覆盖：只把本次选定私有片段交给模型、逐字连续片段引用、数字必须出自引用片段或标题、未知 chunk 拒绝、片段不匹配拒绝、最多一次修复、文件未覆盖即拒答、生成后证据变化返回 409、重复引用合并。原库计数与旧模块未变。

本轮私有 PDF 检索完整隔离回归 **465/465**（原 411 + profile/并发 14 + 联合 capture 8 + HTTP 32）、125.833 秒、失败/错误 0、退出 0，见 `reports/pdf_search_regression.txt`。专项使用明确合成向量和实际内存 Qdrant；跨进程 DB 槽、slot backend 失效、无长事务、admission 后真实计划扩容、最终联合读回及未更新 SHA 标签的 DB 原始 JSON 变化均通过。首轮 12 项中的 4 个错误是验证脚本误用已脱敏 `connection.dsn`，失败保留；改为原连接参数仅替换临时库名后 12/12，再补失效边界 14/14，未放宽生产认证。HTTP 27/27→30/30→32/32 和联合向量 8/8 分别保存。实际本地 BGE/认证 Qdrant 烟测一个场景通过，两文件/三次发布、固定历史响应与来源链接、错误内部 SHA/缺失缓存/错误 key 拒绝；未调用生成模型、下载或外部模型。原库/旧模块/11/13/8 冻结摘要保持，临时库/账号/产物/向量实例清理。核查与边界见 `reports/pdf_search_verification.json`、`pdf_search_review.md`、`pdf_search_real_local_verification.json`；未新增页面或问答准确率。

上一私有 PDF 知识发布完整隔离回归 **411/411**（338 + 控制面 31 + 向量 24 + HTTP 18）、进程退出 0，见 `reports/pdf_index_regression.txt`。控制面使用假 vector adapter 专注 SQL/认证/租约；向量与 HTTP 使用真实内存 Qdrant 和明确合成嵌入，另在独立真实 1.12.4 服务验证 7 项协议边界。真实服务器首轮发现 bbox float64 JSON 回读相差 1 ULP；payload 改成精确 `bbox_f64_hex`，来源 chunk 数值不改，诊断/失败与最终通过记录分列。实际本地 BGE/CLI 场景验证发布、失败 exit 1、原 owner 重试且缺失模型缓存时重放冻结计划、v2 后 v1 字节不变。该场景最初两次因验证脚本只接受 403 和 macOS `/var` 路径别名失败，修正脚本后通过，未放宽冻结源门禁；原始日志保留。原库计数及旧模块、11/13/8 冻结摘要保持，临时库/账号/产物/向量实例已清理，无下载或外部模型调用。核查见 `reports/pdf_index_verification.json`、`pdf_index_real_local_verification.json`；没有新增页面或问答模型评分。

上一 PDF 私有快照登记完整隔离回归 **338/338**（292 + 服务 27 + HTTP 19）、进程退出 0，见 `reports/pdf_publication_regression.txt`。服务专项最终 27/27、HTTP 最终 19/19；初始 25/25 与 15/15 分别保留，增加锁等待期间过期及读取/提交后权限变化边界后再完整验证。覆盖 CAS 竞争、不同文档、request/owner/attempt 重用、事务回滚、v2 后 v1 字节保持、DB/文件篡改拒绝、实际会话/账户变化、readonly 无访问与旧表不写。原库计数未变、临时测试库/账户/产物已清理；副本不复制模型设置或私有快照行，未调用外部模型。见 `reports/pdf_publication_verification.json`，实现见 §18.31 和 `docs/PDF_SNAPSHOT_REGISTRY.md`。没有新增操作页面，该阶段使用真实 HTTP/数据库/文件测试。

上一准备阶段完整隔离回归 **292/292**（261 + 31）、进程退出 0，见 `reports/pdf_prepare_regression.txt`。专项最终 31/31、实际 CLI 8/8；不同文件/同 attempt/共享源的独立进程竞争、版本 ID、残缺/篡改、FIFO、加密、旋转/裁剪及真实 inline/SMask/Mask 超限均验证。首次 24 项中两个锁创建竞争错误和诊断失败保留，第二轮 24/24 与补齐边界后的最终 31/31 分列，不视作识别准确率。原库计数未变、临时测试库和 CLI 产物已清理、无新增外部模型调用；见 `reports/pdf_prepare_verification.json`、`reports/pdf_prepare_cli_verification.json`、`reports/pdf_prepare_review.md`。实现见 §18.30 和 `docs/PDF_PREPARATION.md`。

上一阶段 API 0.14.0 完整隔离回归 **261/261**（212 + 49），进程退出码 0；专项为队列 20、RAG 15、HTTP/SSE 11、上传 3。前端受控竞态检查 **5/5**；浏览器验证尺寸登记/独立批准/撤销/历史回放，混合 DXF/JSON 登记和重复点击幂等，以及两文件问答的检索/实际引用覆盖。见 `reports/next_stage_regression.txt`、`reports/next_stage_browser_verification.json`、`reports/multifile_ui_races.json`。两个一次性预览副本均清理，原数据库前后计数一致；该轮未调用外部模型。实现见技术说明书 §18.29 和 `docs/MULTIFILE_CONCURRENCY.md`。下列数字保留先前阶段原始分母。

- API 0.13.0 认证尺寸服务：`reports/cad_dimension_service_regression.txt` 保存上一阶段完整隔离回归 212/212；工作流见 `data/cad_demo/DIMENSION_SERVICE_WORKFLOW.md`，技术说明书 §18.28。上一阶段完成服务 API，本阶段补操作页面。

- `reports/cad_dimension_regression.txt`：完整隔离回归 **195/195**（179 + 16 项尺寸契约/独立声明/时间/版本/单位/事件链/回放测试），专项首轮 **16/16**。Docker 起初未运行，原失败日志保留在 `cad_dimension_regression_docker_initial.txt`；恢复既有服务后在副本验收。
- `reports/cad_dimension_contract_initial.json`：13 文件冻结、实际 DXF/测量源字节核对；合成 20.04 mm 与标注 19.9–20.1 mm 比较。身份/批准均作者声明，authentication_verified=false，不是生产准确率。
- `reports/cad_dimension_lifecycle.json`：未批准/批准/自己批准拒绝/撤销/撤销前历史时刻五个合成状态；`cad_dimension_freeze_verification.json`：代码与测量源改动拒绝、首轮报告拒绝覆盖、13 份运行快照。
- `data/cad_demo/DIMENSION_BINDING_CONTRACT.md`、`dimension_binding_v1/`、`data/evaluation/frozen/cad_dimension_v1/`：契约/Schema/材料/复现；旧 CAD 契约和六图兼容复现单列。技术说明书 §18.27。

- `reports/cad_reviews_regression.txt`：上一认证复核阶段隔离回归 **179/179**（167 + 12 项认证关联/历史/换版/目标/单位几何/完整性/权限/幂等并发测试）；专项首轮 12/12，无模型调用。
- `reports/cad_reviews_browser_verification.json`：合成页面存疑→模拟指定圆 38 确认→v2 未继承→v1 历史只读；原文件/预览/实体哈希不变，v2 后 v1 特征与复核状态响应不变。三张 `cad_reviews_*_browser.jpg` 为页面证据。
- `reports/cad_reviews_preview_cleanup.json`：副本/临时素材/凭据已清理，原库计数一致；`cad_reviews_contract_compatibility.json` 与 `cad_reviews_dxf_compatibility.json` 保存冻结范围核查与六图复现。工作流见 `ASSOCIATION_REVIEW_WORKFLOW.md`，技术说明书 §18.26。

- `reports/cad_assets_regression.txt`：上一资产阶段隔离回归 **167/167**（153 + 14 项资产/版本/权限/幂等/并发/源与快照完整性测试）。
- `reports/cad_assets_browser_verification.json`：页面登记 v1→v2→旧版回放；v1 原文件、SVG、特征响应 SHA256 均保持不变。`cad_assets_v2_browser.jpg`、`cad_assets_v1_replay_browser.jpg` 为页面证据。
- `reports/cad_assets_preview_cleanup.json`：预览副本、临时源/凭据清理及原库计数不变。`cad_dxf_assets_compatibility.json` 复现既有六份冻结图，不是新增准确率。工作流见 `data/cad_demo/ASSET_WORKFLOW.md`，技术说明书 §18.25。

- `reports/cad_dxf_regression.txt`：上一读取阶段完整隔离组件/集成回归 **153/153**（138 + 15 项原生读取/渲染/边界测试）。
- `reports/cad_dxf_acceptance_initial.json`：冻结后 6 新合成图首轮；标题 **15/15**，已知尺寸字段 **16/16**，原生实体/句柄/坐标 **35/35**，对象关联状态/使用门禁各 **5/5**，未知值保留 **4/4**，不支持 ARC 拒绝 **1/1**；不是任意 CAD 泛化准确率。
- `data/cad_demo/DXF_PROFILE.md`、`dxf_reader_v1_freeze.json`、`data/evaluation/frozen/cad_dxf_v1/`：受控 profile 边界、8 文件冻结哈希与源码快照；`reports/cad_dxf_browser.jpg` 验证原文/源 SHA/明确关联的双方高亮。初次默认字体校验失败日志保留。详见技术说明书 §18.24。

- `reports/cad_contract_regression.txt`：本轮完整隔离组件/集成回归 **138/138**（上一轮 126 + 新增 12 项 CAD 契约/门禁测试）；不是图纸识别准确率。
- `data/cad_demo/CONTRACT.md`、`manifest_v1.json`：冻结 `cad-contract-v1`；`reports/cad_contract_initial.json` 验证 11 个冻结文件、7 实体/1 作者确认直径、明确业务绑定及 19.9–20.1 mm 标注范围；源改动拒绝、首轮报告拒绝覆盖。DXF 和输出 JSON 均人工编写，未执行读取器，详见技术说明书 §18.23。

- `reports/source_authority_regression.txt`：上一权威阶段完整组件/集成回归 **126/126**；新增 14 项权限、独立启用、有效期/依据/版本、重叠、撤销、幂等/并发与历史回放检查。
- `reports/source_authority_verification.json`：页面登记→独立启用→新查询裁决→撤销恢复未消解；批准前后运行和冲突快照不变；原库未修改。截图为 `reports/source_authority_result_browser.png`、`reports/source_authority_revoked_browser.png`，技术说明书 §18.22 记录边界。

- `reports/identity_regression.txt`：上一身份/审批阶段完整组件/集成回归 **112/112**，新增 13 项身份、角色、CSRF、会话、独立审批、版本/幂等/并发与失效门禁检查；不是模型准确率。
- `reports/identity_approval_verification.json`、`reports/identity_approval_browser.png`：隔离副本中复核员确认→另一审批员批准→查看历史；批准后 B001 冲突仍未消解，原值/声明保留；副本与临时凭据已清理。技术说明书 §18.21 记录边界。

- `reports/industrial_forms_regression.txt`：上一紧凑格式阶段完整组件/集成回归 **99/99**；新增 10 项格式、绑定、坐标、复核与篡改检查。旧 89/89 报告保留；不是模型准确率分母。
- `reports/industrial_forms_acceptance_v2_initial.json`：冻结代码后新样本首轮 **104/104 字段、8/8 完整事实**；原生/扫描各 **52/52、4/4**，字段必须同时满足文本、定位、实体绑定和单位校验。
- `reports/industrial_forms_development_v2.json`：单独开发材料 **52/52、4/4**，不与新样本成绩混合。
- `reports/industrial_forms_v2_verification.json`：代码/评测/PDF 哈希、首轮历史不变、代码版本、数据库隔离与页面检查；技术说明书 §18.20 说明范围和限制。
- `reports/field_review_verification.json`：API 0.7.0、服务健康、源字段/审批不变、旧运行回放与冻结解析器哈希核查。
- `reports/industrial_forms_initial.json`：首轮 **28/56、2/4**，保留所有缺失和失败。紧凑模板原文块能被读取，但 v1 格式解析未接受；诊断不计字段通过。
- `reports/industrial_nl2sql_initial.json` 与 `reports/industrial_nl2sql.json`：同一批真实模型输出的初始评分与数值类型复算；不拼接重试成绩。
- `reports/field_review_current.png`、`reports/field_review_browser.png`：最终页面及隔离副本提交验证。
- [V1.2 历史说明书](技术文档/DataMind_Agent_技术设计说明书_V1.2.md) §18.12–18.33：阶段历史、代码路径、错误、边界和下一步；[V1.3 §18](技术文档/DataMind_Agent_技术设计说明书_V1.3.md#18-实施状态历史成绩与修订记录) 集中汇总当前状态与全部分母。

## 下一阶段顺序与完成条件

按「技术实现优先」排序；交付类工作排在最后，等前置技术任务完成后再启动。

| 顺序 | 工作（技术文档出处） | 当前状态 | 完成条件 |
|---|---|---|---|
| T1 | V-CAR 接入 HTTP 与 `task_budget`（§6.1/§6.4） | **已完成**（10 项新测试；见 `reports/vcar_bounded_verification.json`） | 节点在独立进程组内执行、占用跨进程资源槽、取消可传播；HTTP 目标由服务端白名单决定 |
| T2 | V-CAR 机制消融与误修复/错误消解统计（§6.4） | **已完成**（机制层；18 项测试，见 `reports/vcar_ablation_verification.json`） | 四条机制臂已对照并报告误修复率/错误消解/未消解；**真实模型与视觉载荷、模型规模对比仍待做** |
| T3 | 通用 Parser Adapter 与统一 Locator/Relation 契约（§5.2/§5.4） | **契约、映射、解析出口与 media type 路由已完成**（36 项测试）；**契约结果未写入存储列，仅注册 PDF 适配器** | 契约存储列、Office/CAD 适配器、跨格式统一定位与关系存储 |
| T4 | Office（DOCX/PPTX/复杂 XLSX）与图纸 PDF/DWG 适配（§5.1.1/§5.8） | 未实现 | 逐格式原生对象解析、定位与关系；每格式各有样本与独立分母 |
| T5 | 统一 CPU/内存配额与跨主机租约（§13） | **CPU 限额与租约已完成**；内存限额在本机 macOS 不可执行（失败关闭） | 资源硬隔离（需 Linux cgroup）、租约接入执行路径 |
| T6 | 授权真实输入验收（§9.1） | 缺外部材料 | 真实模板/图纸/扫描件独立样本，标签先冻结，分列原生/扫描分母 |
| D1 | 赛题交付包、答辩材料与官方专项核对 | **延后**（见「现阶段优先级」） | 前置技术任务完成后启动；不复用合成材料成绩宣称验收 |

已完成且已验收的前序项：紧凑工业格式 v2（开发 52/52、新样本 104/104）、本地认证/权限与独立字段审批、来源权威登记机制、受控 CAD 契约/原生 DXF/资产/关联复核/尺寸服务、原生 PDF 准备与私有快照/发布/检索/回答、向量修复与孤立回收、统一任务预算与 OCR/公式接入、V-CAR 编排原语。这些是组件与契约验收，**不等于真实工厂盲测或模型准确率**。

仍有明确缺口的旧项：复杂工业 SQL、通用跨源动态计划（T1/T2 完成后才成立）、通用 CAD/图纸 API、国产小模型与视觉级联、完整 P1/M6。`ingestion/ingest.py` 仍是串行离线工具，不能同时启动多个旧摄取进程或在并发任务中执行全局 reset；受控队列登记不自动把图纸与测量配对，也不替人工复核/批准。

## 启动与报告复现

**本机免登录（默认）**：服务只绑定 `127.0.0.1`，来自本机的请求自动以已登记的 `local-admin`（admin）身份进入，各页面与接口不再要求密码；CSRF、同源检查与角色门禁代码保留，设 `DATAMIND_LOCAL_NO_LOGIN=0` 即恢复强制登录，`=1` 可对非本机客户端也启用。需要区分身份（如“另一账户审批”）时，按下面步骤创建真实账户并关闭该开关。

```bash
bash scripts/start_local.sh --background
.venv/bin/python scripts/test_isolated.py
.venv/bin/python scripts/verify_cad_contract.py
# DXF 需要可选依赖：.venv/bin/python -m pip install -r requirements-cad.txt
.venv/bin/python scripts/parse_cad.py data/cad_demo/development/dev-mm.dxf --document-id doc-dev-mm --provenance synthetic_demo --output-dir tmp/cad-new-preview
# 账户由本地管理员交互创建；没有默认密码，复核/审批应使用不同账户
.venv/bin/python scripts/manage_users.py create reviewer1 --name 复核员 --role reviewer
.venv/bin/python scripts/manage_users.py create approver1 --name 审批员 --role approver
.venv/bin/python scripts/manage_users.py create admin1 --name 管理员 --role admin
# 受控多文件登记队列必须另启 worker；与 HTTP 服务使用相同写入 DSN
.venv/bin/python scripts/file_job_worker.py --watch --workers 2
```

v1 首轮复现需使用 `data/evaluation/frozen/industrial_native_v1/manifest.json` 记录的 Git 基线；当前 v2 代码不能冒充旧冻结解析器。v2 新样本验收拒绝代码/评测器哈希变化，`--current-parser` 只允许开发套件。本次审批功能改变了冻结范围内的字段读取代码；历史 v2 复现须在独立目录按 `data/evaluation/frozen/industrial_labelled_v2/manifest.json` 的 `runtime_snapshots` 恢复冻结文件，输出到新报告路径。当前检出直接运行旧验收会因哈希变化停止，不能把本次代码算作旧首轮。升级原服务前需在目标检出中完成合并、重新提取工业文档并重启；旧解析版本的复核决定不会自动继承。

页面入口：`http://127.0.0.1:8000/#fields`；图纸入口为 `/#cad`，所有图纸读取需登录，素材列表与登记仅 admin，关联复核仅 reviewer/admin，历史图纸只读。尺寸入口 `/#dimensions`：admin 登记/绑定与保存快照，独立 approver/admin 批准/撤销，所有已登录角色可读取当前状态与历史。多文件入口 `/#files`：管理员上传受控 DXF/单测量 JSON，提交逐项任务并查看结果；后台必须另启 `--watch` worker。问答页可选最多 8 份已索引公开文档，显示相关片段与实际引用覆盖，不能视作全文比较。图纸和尺寸保持独立资产，不自动进入 RAG 或生产事实。升级后登录本地配置账户；模型接入只允许管理员，复核只允许 reviewer/admin，独立审批只允许 approver/admin 且不能审批自己。来源权威入口为 `/#authority`：管理员登记，另一 approver/admin 账户启用，规则仅绑定具体测量及原始版本。公共问数/文档仍为本机演示范围，不是完整多租户访问控制。启动保留既有数据并应用增量迁移；OCR/公式权重按 README 的设置脚本安装，虚拟环境、权重/密钥、本地日志、临时渲染和可再生预览不随仓库发布。实时数据库、模型凭据和配额不属于 Git 内容；新机器需要独立初始化和配置。模型评测会调用上游，以上历史组件回归不新增真实模型调用。
