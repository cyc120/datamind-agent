# New API 大模型接入后台

本目录运行基于 New API 的模型接入核心版。渠道、上游密钥和应用令牌仍由 New API 数据库管理；赛题代码直接使用兼容接口。网关仅监听本机 `127.0.0.1:4000`。

## 运行

已有部署可在本目录执行：

```bash
docker compose up -d
docker compose ps
```

修改 `source/` 后执行 `docker compose up -d --build` 重新构建并启动核心版。打开 `http://localhost:4000` 可使用精简管理页，只包含渠道、应用令牌和调用测试；管理员账号沿用 New API 数据库中的账号。

首次部署需从 `.env.example` 创建 `.env`，为 `NEW_API_SESSION_SECRET` 填入随机值，并创建名为 `datamind-new-api-data` 的 Docker 数据卷。已有部署请勿覆盖原 `.env` 或数据卷。`secrets/datamind-api.env` 是本机已有的 DataMind 应用令牌文件，仅供本机调用或验收使用；上游供应商密钥不要放在这里。

## 接口

网关地址为 `http://127.0.0.1:4000/v1`。赛题代码使用应用令牌访问：

- `GET /v1/models`：查看该令牌可用的模型 ID。
- `POST /v1/chat/completions`：聊天；请求体设置 `stream: true` 时返回流式结果。

当前已验收的模型 ID 为 `deepseek-flash`。裁剪版保留 DeepSeek、通义千问、Kimi、智谱、火山方舟、百度、腾讯、讯飞、MiniMax、硅基流动和通用 OpenAI 兼容渠道的适配入口。新增渠道时，可用“获取模型”按钮读取上游模型列表，再完成真实聊天调用验收；未保留的渠道类型不能在核心版调用。

## 原版控制台

精简页支持常规渠道的添加、编辑、启停、删除和测试，也支持应用令牌管理。原版控制台只作为特殊渠道配置的备用工具，需要时执行 `docker compose -f compose.admin.yml up -d`，完成后执行 `docker compose up -d` 返回精简版。两个部署文件使用同一个 Compose 项目和数据卷，切换时会替换正在运行的容器。

`backups/` 保存迁移前的数据快照，`secrets/`、`backups/`、`logs/` 和 `.env` 不应提交到版本库。
