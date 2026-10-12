# 会话（chat session）接口契约

本文件是 `/v1/sessions` 系列接口的规范来源。实现位于 `datamind/sessions.py`（会话元数据与
transcript 推导）、`datamind/api.py`（路由、请求模型与授权面）、`datamind/agent.py`
（run 写入时带 `conversation_id` 并入组）；表结构见 `db/migrations/020_chat_sessions.sql`，
`runs.conversation_id` 的索引见 `deploy/init/02_metadata.sql`。

## 0. 会话是什么、不是什么

* **是什么**：会话是**对既有 `runs` 行的命名分组**——一个标题、一个归属用户、一段软删除
  记账。**没有消息表**：一次问答的完整记录早已落在 `runs`（`question` / `answer` / `status` /
  `confidence_level` / `started_at`），会话的 transcript 由 `runs` **推导**：`question` 是用户
  轮次、`answer` 是助手轮次，按 `started_at ASC, run_id ASC` 排序。run 通过
  `runs.conversation_id` 入组（该列早已存在，本轮开始被写入）。
* **不是什么**：会话**不是多轮记忆**。它不向模型提供历史、不做跨请求实体记忆、不改变
  `/v1/chat`、`/v1/chat/stream` 的 `history` 契约。历史仍然**完全由客户端每次请求自带**，
  会话只是把已经发生过的 run 组织起来，属于元数据/记账；两者的准确边界见
  [多轮上下文契约](MULTI_TURN.md) §2 与 §8。

约定：所有响应均为 JSON；`/v1/sessions*` 位于**默认拒绝**中间件之内（不进
`PUBLIC_PATHS` / `PUBLIC_READ_PREFIXES` / `PUBLIC_WRITE_PATHS`），因此必须有身份，变更还自动
带同源检查与 CSRF。`/v1/sessions` 已加入 `_NO_STORE_PREFIXES`，响应头恒为
`Cache-Control: no-store`：会话列表/详情就是聊天记录本身，一旦被浏览器或中间代理缓存，
同一台机器的下一位使用者就可能读到别人的对话。

---

## 1. 权限、CSRF 与同源

| 方法 | 路径 | 身份 | 角色 | CSRF | 同源 |
| --- | --- | --- | --- | --- | --- |
| POST | `/v1/sessions` | 必须已登录 | 不限（任意已登录用户） | 必须 | 必须 |
| GET | `/v1/sessions` | 必须已登录 | 不限 | — | — |
| GET | `/v1/sessions/{session_id}` | 必须已登录 | 不限 | — | — |
| PATCH | `/v1/sessions/{session_id}` | 必须已登录 | 不限 | 必须 | 必须 |
| DELETE | `/v1/sessions/{session_id}` | 必须已登录 | 不限 | 必须 | 必须 |

* **角色列是“不限”而不是某个角色**：`_required_permission` 对 `/v1/sessions` 返回 `None`，
  即只要求身份、不做角色门；任何已登录用户**只管理自己的会话**。
* 未登录 → `401 {"detail":{"error_code":"LOGIN_REQUIRED"}}`；会话失效 → `401 SESSION_INVALID`
  （均沿用既有中间件 `datamind/auth.py`）。
* 变更请求缺少 CSRF 头 → `403 CSRF_REQUIRED`；跨源 → `403 ORIGIN_NOT_ALLOWED`。
* 写事务里额外调用 `lock_identity(cur, identity, 'viewer')`（`datamind/auth.py:202`）：
  作用是在写入前重新锁定登录态与被吊销/过期的会话，让**已吊销的身份无法继续写**。
  角色词表恰为 viewer/reviewer/approver/admin 且 admin 是超管，所以 `'viewer'` 就是
  “任意已认证用户”这一档（先例：`datamind/file_jobs.py:150`）。
* 响应体**永不**包含 `csrf_token` 或 `_session_hash`：投影只输出白名单列（见 §6 的
  `_public`）。

---

## 2. 接口

ID 格式：`"sess-" + uuid4().hex[:24]`（与仓库既有的 `run-` + hex[:16]、`approval-` + hex[:24]
同一约定）。`conversation_id` 请求字段要求匹配 `^sess-[0-9a-f]{24}$`，不匹配是框架 422。

### `POST /v1/sessions` —— 新建

```jsonc
POST /v1/sessions
{ "title": "订单统计" }        // 可选；省略即用服务端默认标题「新对话」
```

`200` → `{"session": {"session_id","title","created_at","updated_at"}}`。

* `title` 省略 → 默认标题 `新对话`，并把 `updated_at`/`created_at` 置为 `now()`。
* `title` 显式给出但全空白 → `422 SESSION_TITLE_REQUIRED`（与“省略”不同：显式空串是错误）。
* 首个 run 入组时若标题仍是 `新对话`，会按第一轮提问**自动命名**（见 §5）。

### `GET /v1/sessions` —— 列表

```
GET /v1/sessions?q=&limit=&offset=
```

`200` → `{"sessions":[...], "limit":int, "offset":int, "has_more":bool}`。

* `limit` 默认 `20`，`ge=1 le=50`；`offset` 默认 `0`，`ge=0 le=10000`；越界由框架返回 422
  （`Query(...)` 边界，不是业务错误码）。
* 每个列表项 = 会话公开列 + 两个廉价聚合：`run_count`（该会话的 run 数）、
  `last_question`（最近一个 run 的 `question`，无 run 时为 `null`）。
* 排序 `updated_at DESC, session_id`：最近有活动的会话在最前。
* 分页沿用服务层惯例：一次取 `limit + 1` 行，切到 `limit` 行并据此给出 `has_more`，
  不做第二次 `COUNT`。

### `GET /v1/sessions/{session_id}` —— 详情（含 transcript）

`200` → `{"session": {...}, "messages": [...]}`，实现另外附带把两侧摊平的 `turns`
（`{"session":..., "messages":[...], "turns":[...]}`，两者语义完全一致）：

* `messages` 每个 run 一条，字段固定为
  `run_id/question/answer/status/confidence_level/started_at`，按 `started_at ASC, run_id ASC`
  排序（即对话顺序）。
* `turns` 把每个 run 摊成两条：`role=user` 的 `text=question`、`role=assistant` 的
  `text=answer`；run 尚未完成时 assistant 的 `text` 为 `null`，前端可据此渲染待完成气泡。
  每条 turn 还带 `run_id`、`at`（即 `started_at`）、`status`、`confidence_level`。

### `PATCH /v1/sessions/{session_id}` —— 重命名

```jsonc
PATCH /v1/sessions/{session_id}
{ "title": "必须" }
```

`200` → `{"session": {...}}`。`title` 必填；全空白 → `422 SESSION_TITLE_REQUIRED`；命中
`deleted_at IS NULL` 与 `user_id` 的约束，越权或已删除 → `404 SESSION_NOT_FOUND`。
成功时 `updated_at=now()`。

### `DELETE /v1/sessions/{session_id}` —— 软删除

`200` → `{"session": {...}}`（返回被删除的会话本身，含其 `created_at`/`updated_at`）。
实现只 `SET deleted_at=now(), updated_at=now()`，**不删除任何 `runs` 行**。删除一个已经
删除的会话、或不存在的会话、或别人的会话，都返回 `404 SESSION_NOT_FOUND`。

---

## 3. 错误码

| `error_code` | HTTP | 触发 |
| --- | --- | --- |
| `SESSION_NOT_FOUND` | 404 | 会话不存在、已被软删除、或属于**其他用户**（三者刻意不可区分） |
| `SESSION_TITLE_REQUIRED` | 422 | 重命名缺 `title`/非字符串/全空白；创建时显式传全空白标题 |
| `SESSION_TITLE_TOO_LONG` | 422 | 标题（`strip()` 后）长度 > 200 字符 |

错误体恒为 `{"detail":{"error_code":"CODE"}}`，`error_code` 为大写蛇形、`SESSION_*` 前缀。
超出请求模型/查询参数边界的（如 `limit=0`、`offset=20000`、`title` 超过 200 字符被
`Field(max_length=200)` 拦下）由框架按 FastAPI 惯例返回 422，不走上面的业务错误码——因此
`SESSION_TITLE_TOO_LONG` 是服务层的防御性兜底，正常 HTTP 路径上仍应能观察到它。

服务层另有三个防御性代码，仅在绕过路由直接调用服务函数时出现：
`SESSION_QUERY_INVALID`（`q` 非字符串）、`SESSION_QUERY_TOO_LONG`（`q` > 200 字符）、
`SESSION_PAGINATION_INVALID`（`limit`/`offset` 不是范围内的整数）。它们同样是 422。

---

## 4. 归属规则：越权 = 不存在（存在性隐藏）

会话**严格按 `identity['user_id']` 归属**。每一次读/写都在**同一条 SQL** 里同时约束
`session_id`、`user_id = <当前身份>` 和 `deleted_at IS NULL`：

* 别人的会话与不存在的会话返回**完全相同**的 `404 SESSION_NOT_FOUND`（连同 `detail` 字节
  一致），因此无法用错误差异探测某个 `session_id` 是否存在。
* 列表永远只返回当前用户的活跃会话，`q` 只是在**自己的**会话集合内过滤，不会因搜索词
  命中别人的标题或问答而泄露。
* `_user(identity)` 对没有 `user_id` 的身份直接返回 404（空 owner 绝不匹配任何行），
  避免空值意外绕过 owner 条件。

---

## 5. 软删除：为什么不能物理删

DELETE 只写 `deleted_at=now()`，所有读取（列表/详情/重命名/再次删除）都带
`deleted_at IS NULL`。原因是**内容不在 `chat_sessions` 里**：

* 会话的问答就是 `runs` 行，而 `runs` 的删除会**级联**清掉
  `trace_events` / `evidence_nodes` / `evidence_edges` / `claims` 等证据链
  （`ON DELETE CASCADE`），物理删除会话等于顺手毁掉答案的审计轨迹。
* `runs` 由 `postgres` 拥有、由所有回答路径写入；`chat_sessions` 只归 `datamind_app`。
  让 API 去删 `runs` 既越权也会破坏共享历史，因此本接口**从不改动 `runs`**。
* 运营侧日后需要彻底清除时，可以在 API 之外按 `deleted_at` 批量清理，风险与本接口无关。

---

## 6. 搜索语义

`GET /v1/sessions?q=...` 的 `q` 是**可选单参数**：

* 长度上限 `200` 字符（服务层超出 → `422 SESSION_QUERY_TOO_LONG`）；空白/纯空白等同于
  **不传**（不做任何过滤），而非匹配空串。
* 命中范围：**会话标题** 或 **该会话下任意 run 的 `question`/`answer` 文本**。答案未完成时
  按 `COALESCE(answer,'')` 处理。
* 匹配用 `ILIKE`（大小写不敏感），并**转义通配符**：`\`、`%`、`_` 都会被加上
  `ESCAPE '\'` 前缀。因此用户搜索 `100%` 或 `a_b` 时，`%` 与 `_` 是**字面量**，不是通配符。
  转义顺序先处理 `\` 自身再处理 `%`/`_`，避免二次转义。
* 不引入任何 Postgres 扩展（本仓库没有 `CREATE EXTENSION`），也不用全文检索/`pg_trgm`；
  搜索就是一次带 `ESCAPE` 的 `ILIKE` + `EXISTS` 子查询。

---

## 7. Schema

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  VARCHAR(64) PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ,
    CHECK (length(btrim(session_id)) > 0),
    CHECK (length(btrim(user_id)) > 0),
    CHECK (length(btrim(title)) > 0),
    CHECK (length(title) <= 200),
    CHECK (deleted_at IS NULL OR deleted_at >= created_at)
);

CREATE INDEX IF NOT EXISTS chat_sessions_owner_updated_idx
    ON chat_sessions(user_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS chat_sessions_owner_created_idx
    ON chat_sessions(user_id, created_at DESC) WHERE deleted_at IS NULL;
```

* **FK `user_id → auth_users(user_id) ON DELETE CASCADE` 是允许的**：`auth_users` 由迁移 005
  创建、归 `datamind_app` 所有，所以以 `datamind_app` 身份运行的迁移可以给它加 FK（已实测
  通过）。删账号不会留下指向不存在 owner 的会话元数据。
* 两个部分索引都排除已删除行，让“我的活跃会话、最近在前”同时吃上 `user_id` 与
  `deleted_at IS NULL`。索引名是契约的一部分。
* 表与**每一列**都有 `COMMENT ON`。`migrate.py` 每次启动重放全部 `db/migrations/*.sql`、
  单事务、无 ledger、无 checksum、以 `datamind_app` 身份执行，所以每条语句都必须幂等
  （`CREATE ... IF NOT EXISTS`；`COMMENT ON` / `REVOKE` 天然幂等）。
* 隐私：`REVOKE ALL ON chat_sessions FROM PUBLIC, readonly_user;`（先例 005:50
  `auth_sessions`、011:36 `pdf_snapshot_*`、013:17 `task_leases`）。会话内容私有，只经写身份
  在应用层按 owner 访问。

### `runs(conversation_id)` 的索引为什么不在迁移里

`runs.conversation_id` 早已存在（`deploy/init/02_metadata.sql`），本轮开始被写入。它的索引
**必须**放在 `deploy/init/02_metadata.sql`：

```sql
CREATE INDEX IF NOT EXISTS runs_conversation_idx ON runs(conversation_id);
```

因为 `runs` 由 `postgres` 拥有，而 `db/migrate.py` 以 `datamind_app` 身份执行每个迁移文件，
`CREATE INDEX ON runs(...)` 会失败——**实测报错原文**：

```
ERROR:  must be owner of table runs
```

同理，任何迁移里都不得对 `runs` / `trace_events` 做 `ALTER` / `CREATE INDEX` / `COMMENT` /
`GRANT`。`deploy/init/02_metadata.sql` 以 `postgres` 身份执行，是唯一能建这个索引的位置。

---

## 8. run 如何入组与自动命名

* `POST /v1/chat` 与 `POST /v1/chat/stream` 接受可选的 `conversation_id`。**不传时该 kwarg
  完全不出现在 `DataMindAgent.run` 的关键字参数里**（不是传 `None`）：既有调用方的提示词
  与调用签名必须逐字节不变，`tests/test_multi_turn.py` 冻结了这一点。
* 传了 `conversation_id` 时，run 写入 `runs(..., conversation_id)` 取得归属；随后
  `sessions.touch()` 只做两件事：把 `updated_at` 推到 `now()`；若标题仍是默认值 `新对话`，
  用第一轮提问自动命名。
* 自动命名规则：`question` 去首尾空白、**内部连续空白折叠为单个空格**、按**字符**截断到
  `40` 个字符（**不追加省略号**）；若提问归一化后为空则保留原标题。用户显式命名过的会话
  （标题 ≠ `新对话`）**永不**被自动覆盖。
* `touch()` 是 **best-effort**：会话不存在、已软删除或数据库瞬时错误都被吞掉，绝不因为
  记账失败而让用户已经付出的回答失败；它也不需要身份，因为 `agent.py` 已在自己的写事务里
  锁定并校验过调用者。

---

## 9. 如何验证

```bash
# 1) 迁移：每次启动都会重放全部 db/migrations/*.sql，可安全重复执行
.venv/bin/python db/migrate.py
# 期望输出包含 applied 020_chat_sessions.sql；连跑两次输出一致（幂等）
# 迁移以 datamind_app 身份执行，因此不应出现 runs 的 DDL

# 2) 文档静态扫描（docs 契约测试，本文件的 ROUTE_UNSUPPORTED/退役 约定也在其中）
.venv/bin/python -m unittest discover -s tests -p 'test_security_round2.py' -v

# 3) 会话用例：按仓库隔离回归惯例在临时库副本上跑（不污染演示数据；datamind-postgres 须已运行）
.venv/bin/python scripts/test_isolated.py --pattern 'test_sessions*.py'

# 4) 前端构建（本能力不引入新依赖）
npm --prefix frontend ci
npm --prefix frontend run deploy
```

curl 往返（写操作需要 CSRF，所以显式登录拿 `csrf_token`；把账号密码换成你的真实账号）：

```bash
BASE=http://127.0.0.1:8000
JAR=/tmp/dm-sessions.jar

# 登录：拿 cookie 与 CSRF 令牌
CSRF=$(curl -s -c "$JAR" -H 'Content-Type: application/json' \
  -d '{"username":"<用户名>","password":"<密码>"}' "$BASE/v1/auth/login" \
  | .venv/bin/python -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')

# 新建会话（200），取出 session_id
SID=$(curl -s -b "$JAR" -H "x-csrf-token: $CSRF" -H 'Content-Type: application/json' \
  -d '{}' "$BASE/v1/sessions" \
  | .venv/bin/python -c 'import json,sys;print(json.load(sys.stdin)["session"]["session_id"])')

# 带会话提问：run 通过 conversation_id 入组，并触发自动命名
curl -s -b "$JAR" -H "x-csrf-token: $CSRF" -H 'Content-Type: application/json' \
  -d "{\"question\":\"2024年一共有多少笔订单？\",\"conversation_id\":\"$SID\"}" "$BASE/v1/chat"

# 列表（含 run_count/last_question）与详情（messages/turns 由 runs 推导）
curl -s -b "$JAR" "$BASE/v1/sessions?limit=20&offset=0"
curl -s -b "$JAR" "$BASE/v1/sessions/$SID"

# 搜索：% 与 _ 按字面量匹配；空白 q 视为不传
curl -s -b "$JAR" --get --data-urlencode 'q=100%' "$BASE/v1/sessions"

# 重命名
curl -s -b "$JAR" -X PATCH -H "x-csrf-token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"title":"订单统计"}' "$BASE/v1/sessions/$SID"

# 软删除；删除后详情与再次删除都是 404 SESSION_NOT_FOUND
curl -s -b "$JAR" -X DELETE -H "x-csrf-token: $CSRF" "$BASE/v1/sessions/$SID"
curl -s -b "$JAR" "$BASE/v1/sessions/$SID"                 # -> 404 {"detail":{"error_code":"SESSION_NOT_FOUND"}}
curl -s -b "$JAR" -X DELETE -H "x-csrf-token: $CSRF" "$BASE/v1/sessions/$SID"   # -> 同样 404
```

自检清单：

* 所有 `/v1/sessions*` 响应都带 `Cache-Control: no-store`；
* 响应里不出现 `csrf_token` / `_session_hash`；
* 未登录访问 → `401 LOGIN_REQUIRED`；跨源或缺少 CSRF 的写 → `403`；
* 用另一个账号访问同一 `session_id` → 与不存在完全一致的 `404 SESSION_NOT_FOUND`；
* 不传 `conversation_id` 的 `/v1/chat` 行为与多轮之前逐字节一致（`tests/test_multi_turn.py`）。

## 10. 历史 run：`GET /v1/runs` 与「运行记录」页

会话列表**只**显示真的归属于某个会话的 run，所以它天生看不到「会话功能上线之前」的
历史。历史本身一直都在 `runs` 表里，由 `GET /v1/runs` 提供（前端页面 `#/runs`）。

| 方法 | 路径 | 身份 | 说明 |
|---|---|---|---|
| GET | `/v1/runs?limit=&offset=` | 匿名可读（`PUBLIC_READ_PREFIXES` 含 `/v1/runs`） | 按 `started_at DESC, run_id DESC` 稳定排序；返回 `{runs, limit, offset, has_more}` |

* `limit` 默认 20、范围 1..200；`offset` 范围 0..100000；越界由框架直接 422。
* 分页用仓库通用的 **`limit+1` 取一行判 `has_more`** 口径；排序带 `run_id` 兜底，
  因此同一窗口重复请求结果稳定、相邻两页不会重复。
* 每行**始终**投影 `conversation_id`，且它**可以是 `null`** —— 这是"该 run 早于会话功能、
  从未归属任何会话"的正常历史状态，UI 显示「未归属会话」。它**不是错误**，也不能被
  前端替换成猜测的会话（否则就是在编造归属）。
* `GET /v1/runs/{run_id}` 为单条详情（`SELECT *`），不存在时 `404 RUN_NOT_FOUND`。

### 10.1 回填历史会话

`runs.conversation_id` 在 020 迁移之前从未被写入过，所以历史 run 全是孤儿。回填脚本
按**时间邻近**把它们聚成会话：

```bash
# 先干跑看形状（不写库）
.venv/bin/python scripts/backfill_sessions.py --dry-run
# 确认后真跑；默认相邻间隔 > 5 分钟即断开
.venv/bin/python scripts/backfill_sessions.py
# 只回填某个时间之后、或改阈值/归属者
.venv/bin/python scripts/backfill_sessions.py --since 2026-10-09T00:00:00+08:00 --gap-minutes 10
.venv/bin/python scripts/backfill_sessions.py --owner user-local-admin
```

**必须如实声明的局限**：会话分组边界是**推断**出来的，原始数据里并没有这个结构 ——
它只记录了"谁在什么时间问了什么"。因此：

* 脚本只在 `conversation_id IS NULL` 的 run 上工作，已有归属的 run **绝不重写**；
* 幂等：重复执行会发现无待归属 run 而什么都不做；
* 自动化测试会以秒级间隔连续发问，所以**无论阈值多小都会聚出几百轮的巨型会话**
  （本机实测 2 分钟阈值下最大仍有 219 轮）。这不是调参问题，是数据本身的性质；
  要让历史更干净，应配合 `--since` 只回填真实使用的时间段。
* 会话的 `created_at`/`updated_at` 取该簇首/末一条 run 的时间，标题取第一条问题
  （空白则回落为固定文案）——都不使用"现在"，也不编造标题。
