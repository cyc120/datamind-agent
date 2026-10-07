# DataMind Agent 项目进度

更新日期：2026-10-07。当前代码 API：**0.14.0**，在隔离数据库副本验收；原检出未升级，本轮不启动或升级原 8000 服务。开发分支 `codex/compact-industrial-forms`；本阶段 API 0.14.0 功能提交 [`91132db`](https://github.com/cyc120/datamind-agent/commit/91132db7eb873ba6e56b88d0cf0e33af775f01c6) 已推送至该分支。仓库：[cyc120/datamind-agent](https://github.com/cyc120/datamind-agent)。

本次推进：认证尺寸服务接入操作页面；新增 DXF/单条测量 JSON 的受控上传与持久登记队列，以及最多 8 份公开文档的显式 RAG 选择、版本门禁与证据覆盖。新增 49 项专项、完整隔离回归 261/261 与前端竞态 5/5 通过；三角色尺寸、混合上传和两文件问答页面已验证。冻结纯契约保持原认证标志，服务认证另列；单次尺寸结果不产生制造授权或整批质量结论。服务流程见 `data/cad_demo/DIMENSION_SERVICE_WORKFLOW.md`，多文件方案见 `docs/MULTIFILE_CONCURRENCY.md`。

## 当前交付范围

| 能力 | 已完成 | 验证/限制 |
|---|---|---|
| 认证尺寸服务与页面 | 单条测量登记/版本、显式绑定、独立使用批准/撤销、当前核验、保存快照、分页历史回放；`/#dimensions` | 三角色页面与隔离回归通过；真实 ERP、计量校准与制造授权待扩展 |
| 多文件登记队列 | DXF/单测量 JSON 上传、逐文件状态、全局/批次限额、租约、幂等、失败重试、协作取消；`/#files` | 必须独立启动 CLI worker；PDF/OCR/Office 未并发化，成功文件不因同批失败回滚 |
| 显式多文档问答 | 1–8 份无重复公开文件、精确版本/源/解析器、每文件相关片段、生成结束重验、检索/实际引用覆盖 | 已索引文档库范围；不是全文审阅或通用跨文件语义裁决，真实模型评分未在本阶段重跑 |
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

当前数据：27 文档资产、23 候选事实、169 切片/向量；工业底座有 3 零件、12 批次、13 测量及 13 质检登记。测试模板未混入运行知识库。浏览器提交测试使用隔离副本，测试后已清理；正式文档没有被代理复核。

本阶段没有修改原库的数据、模型配置或复核记录。完整回归在临时元数据库中把原 17 页工业文档重新提取为 v2，验证新版读取/复核和旧模块兼容；模板仅在离线评测中使用。测试库不复制模型设置、账户、会话、登录限流、权威规则、CAD 资产或关联复核数据，运行后删除。

## 本阶段验收与历史证据

API 0.14.0 完整隔离回归 **261/261**（212 + 49），进程退出码 0；专项为队列 20、RAG 15、HTTP/SSE 11、上传 3。前端受控竞态检查 **5/5**；浏览器验证尺寸登记/独立批准/撤销/历史回放，混合 DXF/JSON 登记和重复点击幂等，以及两文件问答的检索/实际引用覆盖。见 `reports/next_stage_regression.txt`、`reports/next_stage_browser_verification.json`、`reports/multifile_ui_races.json`。两个一次性预览副本均清理，原数据库前后计数一致；本轮未调用外部模型。当前实现见技术说明书 §18.29 和 `docs/MULTIFILE_CONCURRENCY.md`。下列数字保留先前阶段原始分母。

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
- 技术说明书 §18.12–18.19：阶段历史、代码路径、错误、边界和下一步。

## 下一阶段顺序与完成条件

| 顺序 | 工作 | 完成条件 |
|---|---|---|
| 1 | 紧凑工业格式解析 | 用单独开发材料实现；保留 v1 首轮报告和冻结代码；接受字段必须满足原文、区域、实体/工序/测量绑定与单位校验 |
| 2 | 新样本验收与扩样 | 新验收页不参与修复；标签先冻结；分列原生/扫描的字段与事实分母，记录全部失败；授权真实模板与合成模板区分 |
| 3 | 身份认证、审批与权威规则 | 复核者可认证、权限可验证；审批与提取复核分开；有明确依据才裁定来源，未登记规则继续保留冲突 |
| 4 | CAD 与通用工业计划 | 冻结 CAD 实体/几何/版本及业务关联契约后实现；另设基准，不复用现有温度开发成绩宣称验收 |

1、2 的合成紧凑格式阶段和 3 的本地认证/权限、提取复核与独立字段审批已完成。3 的显式来源权威登记与局部可解释裁决已在合成材料完成机制验证。4 的受控 CAD 契约、原生 DXF、图纸资产/版本、认证关联复核、尺寸绑定与独立使用批准服务已完成；本阶段补尺寸操作页面，并实现受控文件队列与显式多文档问答，合成流程验收完成。授权真实模板和真正的来源裁定依据仍需征集，当前机制验收不等于工厂权威验收。历史新样本由同一实现者编写，共四份原生表单及其扫描变体，不能视作独立工厂盲测。复杂工业 SQL、通用跨源动态计划、通用 CAD/图纸 API、V-CAR、国产小模型和正式竞赛交付包仍待实现，完整 P1/M6 尚未验收。

下一阶段 PDF/OCR 并发应先完成版本化产物与短事务发布，建立向量写入/元数据发布的恢复与对账，再接资源受限 worker。现有 `ingestion/ingest.py` 仍是串行离线工具，不能同时启动多个旧摄取进程或在并发任务中执行全局 reset。受控队列登记不自动把图纸与测量配对，也不替人工复核/批准。

## 启动与报告复现

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
