# New API 模型接入核心版

此目录是基于 [QuantumNous/new-api](https://github.com/QuantumNous/new-api) `v1.0.0-rc.40`（`0aec08fee811ec6136828fda790551b49e410301`）制作的本地裁剪版，保留原项目名称、许可证和第三方声明。

运行镜像提供模型服务和精简管理接口：

- `GET /api/status`：健康检查。
- `GET /v1/models`：使用应用令牌查询可用模型。
- `POST /v1/chat/completions`：使用应用令牌聊天，支持 `stream: true`。
- `/api/channel`：管理员管理上游渠道；可从上游自动获取模型 ID。
- `/api/token`：管理模型调用令牌。

保留的渠道适配类型为 DeepSeek、通义千问、Kimi、智谱、火山方舟、百度、腾讯、讯飞、MiniMax、硅基流动，以及通用 OpenAI 兼容渠道。当前只有 DeepSeek 已完成真实调用验收；其他渠道需配置凭据后逐一验证。

渠道和令牌仍保存在 New API 原有数据库中。核心镜像内置简洁管理页，打开 `http://localhost:4000` 可管理渠道和令牌、从上游读取模型 ID 并测试调用；登录使用原 New API 管理员账号。

`Dockerfile.core` 不构建原版 React 前端，产物包含后端程序、内嵌的精简管理页及许可证文件。原版前端、桌面端、旧路由、通用文档和旧部署脚本已移除。其他后端包仍用于模型转发、令牌鉴权、渠道选择和用量结算，不应按目录名直接删除。

本地构建命令：

```bash
docker build -f Dockerfile.core -t new-api-model-core:local .
```

项目采用原版 AGPL-3.0 许可证，见 `LICENSE`、`NOTICE` 和 `THIRD-PARTY-LICENSES.md`。
