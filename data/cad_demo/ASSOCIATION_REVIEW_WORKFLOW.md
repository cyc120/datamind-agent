# 图纸尺寸对象的认证关联复核（API 0.12.0）

**前端版本补充（2026-10-10）**：本文旧版页面操作与深链属于历史流程；旧前端已退役删除，认证关联复核 API 保留，新版管理页面待接入。当前页面范围见 [前端评估](../../docs/FRONTEND_NEXT_STEPS_20261010.md)，旧页面源码可从 Git 历史查阅。

在 [资产登记流程](ASSET_WORKFLOW.md) 后进入 `/#cad`。图纸登记只固定原文件和提取快照；关联复核独立追加，不修改原始实体、尺寸、公差、单位、候选对象或 XDATA 依据。当前复核确认“这条标注对应哪个候选圆”，不是正式图纸审批、生产绑定或质量结论。

## 页面操作

1. 目标环境应用 `008_cad_association_reviews.sql`，用本地 reviewer/admin 账户登录。viewer/approver 可以查看，单独的 approver 角色没有复核权限。原检出/原业务库尚未升级。
2. 选择当前图纸版本，在尺寸表下查看「尺寸对象关联复核」。原始表与原图仍显示提取结果；人工决定显示在单独卡片中。
3. 若需确认，明确选择已经列出的候选圆。下拉显示句柄、圆心和半径，默认空选，不按最近对象或同尺寸自动选圆。确认不能选择原始候选集以外的对象；有明确源引用时只允许其候选对象，认为源引用有误可退回或存疑。
4. 填写理由，保存「确认关联」「保留存疑」或「退回关联」。存疑/退回不携带选定目标。原文提取的值/单位未知或几何与标注矛盾时，即使确认关联仍排除尺寸使用。
5. 「查看复核历史」展示本版本所有追加决定、账户和理由。后续存疑/退回取代有效确认，但旧记录保持存在。
6. 新图纸版本不继承旧复核，即使实体句柄与直径不变。历史版本只读，可查看原决定；新增复核需当前版本。退出/会话失效清空复核卡片和原图。

用户理由是认证账户的人工声明，不自动证明其业务权威、制造授权或真实关联正确性。当前验证材料是合成样本，页面理由明确声明模拟选择。

## HTTP 接口与门禁

`GET /v1/drawings/{id}/features?version=1` 保留原 `features` 和 `inspection_gate`，新增：

- `association_reviews`：每项特征的 `feature_sha256`、最新认证记录和历史数量。
- `review_gate`：将本版本最新人工确认作为独立 human_label 覆盖层重新检查关联/单位/几何；未复核、存疑或退回均排除。该字段不是正式审批或生产判定。
- `review_semantics`：明确语义边界。原始 `inspection_gate` 不会因人工确认改变。

`GET /v1/drawings/{id}/features/{feature_id}/reviews?version=1` 返回该版本的 `reviews/latest_review`。省略版本取当前；全部读取需登录。每文档版本最多读取 1000 条复核记录，超过上限明确拒绝，不截断后冒充完整历史。

`POST /v1/drawings/{id}/features/{feature_id}/reviews` 仅 reviewer/admin，需要登录 Cookie、`X-CSRF-Token`，如发送 Origin 必须同源。载荷不接受 reviewer 身份字段；账户/角色在事务内再次锁定验证。

```json
{
  "request_id": "review-unique-1",
  "document_version": 1,
  "source_sha256": "<source SHA256 from version>",
  "parser_version": "dxf-native-v1",
  "bundle_sha256": "<bundle SHA256 from version>",
  "feature_sha256": "<feature SHA256 from association_reviews>",
  "previous_review_id": null,
  "decision": "confirmed",
  "selected_target": "38",
  "reason": "合成模拟：本次测试指定标注与候选圆的关系；不代表正式审批。"
}
```

哈希占位值必须替换；特征与版本值从 GET 获取。decision 可选 `confirmed/rejected/needs_review`；后两种 selected_target 必须 null。reason trim 后不能为空，最长 1000。request_id 为 1–64 位字母/数字/下划线/连字符，全局唯一；prev 取本版本本特征最新 review_id，第一条为 null。

相同认证账户重试同请求返回原记录；不同内容/账户复用请求 ID 拒绝。登记新源版本与复核共用文档事务锁；复核另锁请求 ID，核对当前版本、源/解析/bundle/feature 哈希和最新前序。旧请求在换版后仍可幂等重放原记录，但不产生新版决定或改变当前指针；换版后以新请求新增旧版复核拒绝。

## 保存与验证边界

`cad_association_reviews` 保存原 feature_snapshot、请求载荷、源/文档/解析/bundle/feature 身份、选定目标/决定/理由、前序、认证账户/角色与时间，以及覆盖载荷/身份/快照的 record_sha256。应用只追加；读取逐项比对原快照、记录哈希、选择约束和历史链，损坏则阻止复核/有效状态读取。原文件接口仍独立核对源，可以查看原始文件排查复核问题。

人工覆盖层只在读取 review_gate 时构造，不保存回 bundle，也不更改原图高亮。哈希不是签名或防数据库管理员篡改措施；既有角色仅本地认证，没有多租户隔离、SSO 或正式审批系统。当前没有生产尺寸表/绑定、质量问数或 CAD 向量摄取。下一步为显式生产尺寸绑定和独立审批门禁另立契约。

证据：`reports/cad_reviews_targeted_initial.txt` 12/12 首轮；`cad_reviews_regression.txt` 179/179；`cad_reviews_browser_verification.json` 页面存疑→模拟确认→换版→旧版回放；`cad_reviews_preview_cleanup.json` 隔离清理和原库计数一致。冻结契约/读取器未修改，兼容复现单列。
