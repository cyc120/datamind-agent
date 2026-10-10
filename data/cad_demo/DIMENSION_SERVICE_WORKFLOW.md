# 认证尺寸登记、显式绑定与独立使用批准服务

**前端版本补充（2026-10-10）**：本文旧版页面操作与深链属于历史流程；旧前端已退役删除，尺寸登记、绑定与审批 API 保留，新版管理页面待接入。当前页面范围见 [前端评估](../../docs/FRONTEND_NEXT_STEPS_20261010.md)，旧页面源码可从 Git 历史查阅。

2026-10-06，当前 API 0.14.0。服务和操作页面在独立开发工作树、一次性 PostgreSQL 副本验收。原检出/原 8000 服务尚未升级；API 0.13.0 的历史服务验收记录保留在本文末尾。

## 页面操作

进入 `/#dimensions`，管理员读取测量 ID 的当前基线后选择素材并登记。随后读取绑定 ID 基线，明确选择测量、图纸、已认证关联的标注与目标圆，填写带时区有效期和依据，提交待批准绑定。页面不会根据同名零件自动配对。

另一位审批账户登录后选择绑定版本，核对原值、双方版号及复核记录，再批准、退回或撤销。绑定登记人和关联复核人不能批准该绑定。管理员可保存当前核验快照，包括阻断结果；历史列表按页加载，回放使用当时的版本和批准链。撤销阻断当前比较，已保存的批准时快照保持原样。

加载期间选择器与写按钮禁用；换账户、注销或旧请求迟到不能恢复已清理的尺寸表单。接口错误仍以服务端权限、版本、哈希及当前头检查为准。

`/#files` 可一次上传并排队登记多份受控 DXF 和单记录测量 JSON；后台须独立运行 `python scripts/file_job_worker.py --watch --workers 2`。队列只完成逐项登记，仍需上述明确关联、绑定与独立批准。范围及恢复机制见 [多文件并发说明](../../docs/MULTIFILE_CONCURRENCY.md)。

## 输入与角色

测量素材只从仓库下 `data/dimension_demo/development/*.json`（synthetic_demo）或 `data/dimension_demo/authorized/*.json`（authorized_external）登记。每个文件 **恰好一条测量**，最多 1 MiB，必须 UTF-8，禁止重复 JSON 键、非有限/指数/数字型数值、无时区时间或额外字段。真实仪器、ERP、校准、测量不确定度和制造授权尚未接入；authorized_external 仅为管理员的来源声明。

顶层形状为 `source_id / record_version / provenance / measurements`，示例见 `data/dimension_demo/development/demo-diameter.json`。单条测量包含 measurement_id、part_id、batch_id、operation_no、process_revision、metric（diameter）、feature_role、method、measured_at、value（十进制字符串或 null）和 unit（mm/in/unknown）。source 内的字节 SHA、JSON Pointer 和版本由后端生成，客户端不能提交角色或当前头。

| 操作 | 权限与约束 |
|---|---|
| 素材列表、测量/绑定登记、持久化比较 | admin |
| 关联复核（既有图纸接口） | reviewer/admin |
| 批准/不批准/撤销本次尺寸使用 | approver/admin；身份必须同时不同于绑定登记人和关联复核人 |
| 已登记测量/绑定、批准历史、当前门禁、历史比较回放 | 所有已登录账户 |

所有写请求检查同源、CSRF，并在事务中重新验证和共享锁定账户/会话。权限来自数据库记录，不从请求载荷、显示名称或前端按钮推断。读取返回 no-store。

## API 顺序

1. `GET /v1/dimensions/sources` 列出管理员可用的测量素材路径/字节 SHA。`POST /v1/dimensions/measurements` 提交 request_id、measurement_id、source_path、source_sha256、provenance、previous_version 和 previous_sha256。首次为 0/null；随后源文件的 record_version 必须为当前版本+1，并提交原测量 payload 的 sha256。源字节和规范测量一同保存于数据库事务，修改暂存文件不影响历史。
2. 图纸在既有 `/v1/drawings` 登记。由 reviewer/admin 在当前版本明确选择候选圆并认证复核。调用 `/v1/drawings/{id}/features`，取得特征哈希及 latest_review。服务使用真实复核记录的 `AssociationReview` 字段投影，其 sha256 是该投影的规范 JSON 哈希，不是整个审计记录哈希。
3. `POST /v1/dimensions/bindings` 明确选择 binding_id、图纸版本/源 SHA/bundle SHA、feature_id/feature SHA、target_handle、measurement_id/版本/payload SHA、review_id/投影 SHA、有效期和 rationale；首次 previous_version=0、previous_sha256=null。后端取回登记源中的工序、批次、工艺版号、尺寸角色和方法，取回图纸中的图号、零件和图纸版次。两类版号不相互推导。绑定有效期覆盖测量时间，左闭右开。
4. `GET /v1/dimensions/bindings/{id}` 返回 binding、批准链及 current_result。未批准为 DIMENSION_USE_APPROVAL_REQUIRED，comparison=null。批准是独立尺寸使用决定，既有认证关联复核不是批准。
5. 另一 approver/admin 账户 `POST /v1/dimensions/bindings/{id}/approvals`，字段为 request_id、binding_version、binding_sha256、previous_approval_id、decision（approved/rejected/revoked）、reason。首次前序=null，后续须提交最新事件 ID；服务生成身份、角色和带时区时间。没有当前批准不能撤销；过期版本不能新增批准，已有批准仍可在换版后撤销。
6. admin `POST /v1/dimensions/bindings/{id}/comparisons`，提交 request_id、binding_version、binding_sha256，保存本次真实当前头、原 CAD bundle、测量、复核、绑定和完整批准链以及比较结果。通过后按精确 Decimal 和 25.4 换算，保留双方原值/单位，输出 within_annotation_range/outside_annotation_range；阻断结果同样保存。
7. 所有登录账户 `GET /v1/dimensions/bindings/{id}/comparisons?limit=20&offset=0` 取得历史摘要及 next_offset（limit 1–50、offset 0–10000）；`GET /v1/dimensions/comparisons/{comparison_id}` 回放原快照。回放校验记录哈希，并对保存的输入重算结果一致性，不使用今天的当前头。

测量和绑定列表分别为 `GET /v1/dimensions/measurements`、`GET /v1/dimensions/bindings`，最多 200，truncated 表示存在更多。单条读取支持 `?version=N`；绑定读取还提供该版本完整批准链与今天的门禁状态。

## 换版、并发和证据

每次测量登记/绑定换版均追加不可变版本并推进当前头；复核变化、图纸换版、测量换版或绑定换版使旧绑定的新比较返回 DIMENSION_CURRENT_HEAD_MISMATCH。新绑定版本无继承批准。撤销后新比较返回 DIMENSION_USE_APPROVAL_REVOKED，历史 comparison 不被改写。

同一 request_id 与同一账户/规范请求重试返回原结果，修改请求或换账户复用被拒绝。审批重试不会重新激活已撤销批准；比较重试回放原比较，不重新执行当前门禁。请求 ID 在各记录类别中唯一。写入按排序后的事务咨询锁串行化；CAD 文档锁与原登记/复核共用。读取绑定在 REPEATABLE READ 只读事务取一致视图。

数据库六张 dimension_* 表与生产事实、RAG 资产分开。测量源 bytes、规范载荷、认证角色快照、请求和审计哈希均保留。哈希用于篡改检测，不构成数据库所有者不可伪造的密码学签名。

冻结纯函数仍输出 `authentication_verified=false`；外层 `authentication.verified=true` 只说明本地服务已使用认证记录与可信当前头，不能修改纯函数的语义或扩称为制造授权。formal_drawing_approval 与 product_quality_conclusion 始终 false。

## 验证与复现

在项目检出且依赖/现有 PostgreSQL 容器齐备时：

```bash
python scripts/test_isolated.py --pattern test_cad_dimensions.py
python scripts/test_isolated.py
python scripts/verify_cad_dimension_contract.py
```

17 项专项覆盖权限、身份/CSRF、源字节/版本、独立批准、撤销、换版、历史回放、精准换算/超界、不批准、几何冲突、CAS 并发、幂等、篡改和分页。初轮 13 项中 9 项失败源于测试测量 A200 与测试 DXF A104 不匹配；修正合成测试输入为 A104 后 13/13，通过后增加 4 项覆盖，最终 17/17。初轮日志保留，不当作验收成绩。

API 0.13.0 历史完整隔离回归 **212/212**（195 + 17）、旧 13 文件尺寸契约/11 文件 CAD 契约/8 文件 DXF 冻结兼容报告见 reports/cad_dimension_service_*。没有新增模型调用、真实生产库写入或原库迁移。

API 0.14.0 完整隔离回归 **261/261**，见 `reports/next_stage_regression.txt`。本轮三角色页面验证完成测量登记、明确绑定、登记人批准禁用、独立批准、保存比较、撤销阻断与旧结果回放；批准历史响应字节和原 CAD 文件/预览/实体摘要保持不变，见 `reports/next_stage_browser_verification.json`。授权真实模板、测量材料与 ERP/设备接入仍待扩展；完整工业计划/整体质量问答/M6/P1 尚未验收。
