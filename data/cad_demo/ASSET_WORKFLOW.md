# CAD 本地资产与版本查看（API 0.11.0 起）

本流程接入已冻结的限定 DXF 读取器，不扩展图纸识别范围。读取范围、材料限制见 [DXF_PROFILE.md](DXF_PROFILE.md)，结构与业务绑定门禁见 [CONTRACT.md](CONTRACT.md)。资产接口固定提取和显式对象引用；API 0.12.0 新增独立的 [认证关联复核](ASSOCIATION_REVIEW_WORKFLOW.md)，正式图纸审批或生产尺寸判定仍待实现。

## 准备与页面操作

1. 在目标检出配置环境和元数据库，安装 `requirements-cad.txt`，应用增量迁移至 `007_cad_assets.sql`；`scripts/start_local.sh` 启动时会执行迁移。先按项目流程备份数据库和运行源副本。当前开发工作树验收使用隔离库，原检出/原库未升级。
2. 用 `scripts/manage_users.py` 创建本地 admin 账户（交互输入密码，没有默认密码）；已配置的普通登录账户可以查看，只有 admin 可读取素材列表并登记。
3. 合成材料放到仓库 `data/cad_demo/development/`；经授权的外部材料放到 `data/cad_demo/authorized/`。素材列表只列每个目录顶层最多 200 个有界 `.dxf` 文件。直接 API 的相对路径可以指向该素材目录内文件，绝对路径/越界路径被拒绝。不接受上传或任意本机路径。
4. 登录 `http://127.0.0.1:8000/#cad`，选择素材、填写资产 ID/显示名、登记。素材的路径、SHA256 和 provenance 来自管理员素材列表；保存时再次核对。
5. 同一资产新版本保持图号/零件身份，图纸版次可变化。保存新文件后选新素材，保持资产 ID，登记时核对当前文档版本和源 SHA；过期页面先刷新。相同当前源不再登记。
6. 已登记列表显示当前版本，历史选择器可回放旧版。尺寸表显示标注原文/单位/偏差/对象引用；点击预览实体显示句柄、模型坐标、源哈希及关联双方。原 DXF 下载、SVG 与交互预览固定选中版本。

`synthetic_demo` / `authorized_external` 是材料声明，后者不自动证明授权文件或授权语义。外部源文件、原始副本和备份需按本地数据管理要求保存，不能随合成演示材料公开。

## HTTP 契约

全部 `/v1/drawings` 读取要求登录会话；素材列表与登记要求 admin。POST 还需登录返回的 `X-CSRF-Token`，如发送 Origin 必须同源。登记人从认证账户读取，载荷不接受身份字段。

| 方法 / 路径 | 返回内容 |
|---|---|
| `GET /v1/drawings/sources` | 素材文件相对路径、SHA256、provenance |
| `POST /v1/drawings` | 固定源副本并追加版本，返回 `version` |
| `GET /v1/drawings` | 最新资产摘要；打开时才校验完整副本和快照 |
| `GET /v1/drawings/{id}/versions` | 当前指针、所有已校验版本 |
| `GET /v1/drawings/{id}` | 图纸身份、源和解析元数据 |
| `GET /v1/drawings/{id}/entities` | 实体、视图及版本摘要 |
| `GET /v1/drawings/{id}/features` | 特征、inspection_gate、语义限制 |
| `GET /v1/drawings/{id}/file` | 校验后的原 DXF 字节 |
| `GET /v1/drawings/{id}/preview` | SVG，CSP sandbox |
| `GET /v1/drawings/{id}/viewer` | 交互 HTML，CSP sandbox allow-scripts |

单图读取接受 `?version=1`；省略时读取当前版本。`inspection_gate` 的 ready 仅表示当前快照中的提取/对象一致性，不授予制造范围或生产合格结论，也不跳过未来业务绑定门禁。

登记 JSON 形状（SHA 必须替换为素材列表给出的实际值）：

```json
{
  "request_id": "cad-register-unique-1",
  "document_id": "drawing-demo",
  "title": "合成轴类图纸",
  "source_path": "data/cad_demo/development/dev-mm.dxf",
  "source_sha256": "<actual staged SHA256>",
  "provenance": "synthetic_demo",
  "previous_version": 0,
  "previous_source_sha256": null
}
```

新资产前序为 `0/null`；后续登记使用当前版本与源 SHA。request_id 为 1–64 位 ASCII 字母/数字/下划线/连字符，全局唯一；同一认证登记人重试相同规范化载荷返回原版本，即使素材已移除；不同载荷/登记人复用 ID 拒绝。重试不会把旧版本重新设为当前。document_id 为 1–64 位、以字母/数字起始，后续可含 `_ . -`；标题 trim 后不能为空。

## 保存、审计与限制

源副本按 SHA 固定在 `data/object_store/cad_sources/{sha}.dxf`，为忽略的运行文件。bundle JSON 与哈希、源 SHA、文档版本、解析版本、认证登记人/角色和时间保存在 `cad_versions`；当前指针保存在 `cad_assets`。应用只追加版本，不提供覆盖/删除接口。原素材修改不影响已登记源副本；读取历史核对快照契约/哈希/身份和固定源哈希，副本缺失或损坏会阻止查看，不能自动改用素材最新字节。

登记写入使用文档/请求锁、当前版本+源 CAS 和同一数据库事务。源副本先发布，SQL 失败可能留下未引用内容副本；当前没有回收器。备份/恢复必须同时覆盖数据库与源副本。文件系统和数据库管理员仍可管理存储，哈希验证不构成防篡改保证；既有公共问数也没有完整多租户访问控制。

CAD 资产与 RAG 文档、生产事实分开，不自动写入向量库或问答。二维默认字体轮廓/bbox 近似，不从图像像素量取制造尺寸。通用 DIMENSION、块、三维、纸空间、DWG、扫描等仍在限定读取器之外。

## 本轮证据

- `reports/cad_assets_regression.txt`：167/167；新增 14 项资产/版本、认证、CSRF、源/快照、路径、幂等/并发测试。
- `reports/cad_assets_browser_verification.json`：v1→v2→v1，旧源、SVG 和特征响应哈希不变。
- `reports/cad_assets_v1_context_browser.jpg`：当前 v2 资产选择旧版 v1；`cad_assets_v2_browser.jpg` / `cad_assets_v1_replay_browser.jpg`：对象引用高亮。
- `reports/cad_assets_preview_cleanup.json`：隔离预览副本/凭据/临时源清理，原库计数不变。
- `reports/cad_dxf_assets_compatibility.json`：六份既有冻结材料复现通过；不是新的独立准确率验收。
