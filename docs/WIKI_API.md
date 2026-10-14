# Wiki 知识图谱：接口契约（P0 冻结）

日期：2026-10-14　状态：**P0 冻结稿**。本文件只定义接口、权限、错误码、查询边界与视觉基线，
**没有任何实现**。领域语义（四类词条、关系词表、三类身份、三套状态）见
[Wiki 领域契约](WIKI_DOMAIN.md)；需求来源为 `WIKI_KNOWLEDGE_GRAPH_PLAN(1).md`（335 行，权威版）。

本阶段的产物**只是文档**：没有新增路由、没有新建表、没有执行迁移、没有提交 git。
文中出现的 `/v1/wiki/*` 路径**全部尚不存在**，`api.py` 当前最新路由是 `:1621` 的
`GET /v1/runs/{run_id}/conflicts`。

> **实现时的第一条硬约束**：`api.py:285-304` 的 `_required_permission()` 是一张
> **硬编码的路径 → 权限映射表**。新增 14 条路由时，**每一条都必须同步扩进这张表**，
> 漏一条就是越权或误拒。仓库里已有现成测试 `tests/test_route_auth_coverage.py`（207 行，
> 默认拒绝面）可用于扩写——**不需要从零写**。详见 §3.2。

---

## 1. 全局约定

| 项 | 冻结值 |
| --- | --- |
| 前缀 | `/v1/wiki` |
| 方法 | 只用 `GET` 与 `POST`（沿用仓库惯例，不用 `PUT`/`PATCH`/`DELETE`） |
| 响应 | JSON；时间一律带时区（ISO 8601，`+08:00`）；数值一律字符串化的十进制（沿用既有 `normalized_values` 惯例，避免浮点误差） |
| 未知枚举值 | **原值保留** + `supported=false`（`WIKI_DOMAIN.md` §3.4） |
| 缓存 | 全部 `/v1/wiki/*` 恒 `Cache-Control: no-store`（§2.3） |
| 未授权对象 | 与不存在对象返回**完全相同**的 404（存在性隐藏，沿用 `/v1/sessions` 先例） |
| 幂等 | 写操作按 `request_id` 幂等；同 owner + 同规范载荷返回首次结果 |
| 身份 | 必须已登录；`/v1/wiki/*` **不进** `PUBLIC_PATHS` / `PUBLIC_READ_PREFIXES` / `PUBLIC_WRITE_PATHS` |
| CSRF | 所有 `POST` 必须 `X-CSRF-Token` + 同源（中间件自动执行，`api.py:307-334`） |
| 写事务 | 写路径在事务内调 `lock_identity(cur, identity, 'viewer')` 复查并锁定身份（先例 `auth.py:202`、`file_jobs.py:150`、`sessions.py`） |

**ID 格式**（沿用仓库既有的 `前缀 + hex` 约定：`run-` + hex16、`sess-` + hex24、
`evt-` + hex16、`approval-` + hex24）：

| 对象 | 格式 | 说明 |
| --- | --- | --- |
| 词条 | `wiki-` + hex24 | 分配型，不可推导 |
| 版本 | `wrev-` + hex24 | 不可变 |
| 证据引用 | `wev-` + hex24 | 指纹派生 + 唯一性校验 |
| 运行映射 | `wprov-` + hex24 | 一次 (引用, 运行) 对一行 |
| 关系 | `wrel-` + hex24 | |
| 复核事件 | `wevt-` + hex24 | 追加式 |
| 整理任务 | `wjob-` + hex24 | |
| 空间 | `wsp-` + hex24 | 第一版一用户一空间 |
| 冲突 | 沿用既有 `conflict_id` | 工业侧 `conf-` + hex20；跨源侧 `conf-` + hex20（`evidence_graph.py:454-456`），**不重新分配** |

---

## 2. 路由总表（14 条）

按需求 §7 的原始顺序编号，本文件后续章节与 P0 验收均引用这些编号。

| # | 方法 | 路径 | 作用 | 所需权限（`_required_permission` 返回值） | 写事务 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/v1/wiki/entries` | 授权词条目录与关键词搜索 | `None`（任意已登录身份） | — |
| 2 | GET | `/v1/wiki/spaces/{space_id}/graph` | 当前授权范围图，分页返回节点与关系 | `None` | — |
| 3 | GET | `/v1/wiki/entries/{entry_id}` | 当前发布版本（草稿需编辑权限） | `None` | — |
| 4 | GET | `/v1/wiki/entries/{entry_id}/revisions` | 可见版本历史 | `None` | — |
| 5 | GET | `/v1/wiki/entries/{entry_id}/revisions/{revision_id}` | 固定版本详情 | `None` | — |
| 6 | GET | `/v1/wiki/entries/{entry_id}/graph` | 授权关系邻域（返回截断标识） | `None` | — |
| 7 | GET | `/v1/wiki/entries/{entry_id}/provenance` | 固定版本的证据与执行事件摘要 | `None` | — |
| 8 | GET | `/v1/wiki/entries/{entry_id}/export` | 带状态与引用的 Markdown | `None` | — |
| 9 | POST | `/v1/wiki/builds` | 从指定运行创建候选草稿 | `reviewer` | ✅ |
| 10 | GET | `/v1/wiki/builds/{build_id}` | 查看自己的整理任务 | `None` + owner 校验 | — |
| 11 | POST | `/v1/wiki/builds/{build_id}/retry` | 原请求失败重试 | `reviewer` + 原 owner | ✅ |
| 12 | POST | `/v1/wiki/entries/{entry_id}/revisions` | 以基线创建新版本 | `reviewer` | ✅ |
| 13 | POST | `/v1/wiki/entries/{entry_id}/reviews` | 对指定版本追加复核结果 | `reviewer` | ✅ |
| 14 | POST | `/v1/wiki/entries/{entry_id}/publications` | 发布指定版本并检查旧发布头 | `approver` | ✅ |

**权限字符串逐条论证**（必须与 `_required_permission` 的返回值逐字一致）：

| # | 需求 §7 的建议权限 | 映射到的权限字符串 | 理由 |
| --- | --- | --- | --- |
| 1–8 | "任意有效身份" | `None` | 读接口只要求身份，不做角色门（先例：`/v1/sessions` 返回 `None`，见 `docs/SESSIONS.md` §1） |
| 9 | `reviewer` 或 `admin` | `'reviewer'` | 只能用已存在的权限字符串；`admin` 由 `auth.py:195` 的旁路自动通过 |
| 11 | 原 owner 且当前仍有编辑权限 | `'reviewer'` | 同 #9 |
| 12 | `reviewer` 或 `admin` | `'reviewer'` | 同 #9 |
| 13 | `reviewer` 或 `admin` | `'reviewer'` | 同 #9；**不能**用现有的"`POST` 且路径以 `/reviews` 结尾 → reviewer"兜底规则（见 §3.2） |
| 14 | `approver` 或 `admin` | `'approver'` | 只能用已存在的权限字符串；`admin` 自动通过 |

### 2.1 与既有权限兜底规则的关系（易错点）

`api.py:303-304` 有两条**通用**兜底规则：

```python
'reviewer' if request.method=='POST' and path.endswith('/reviews') else
'approver' if request.method=='POST' and path.endswith('/approvals') else None
```

它对 #13 恰好给出正确结果（`/v1/wiki/entries/{id}/reviews` 以 `/reviews` 结尾 → `reviewer`），
但这是**巧合**，不是契约：

- #14 的路径以 `/publications` 结尾，**不命中** `/approvals` 兜底，会落到 `None`（="任意已登录身份"），
  即**默认放开**。必须显式加规则。
- #9–#12 都不以 `/reviews` / `/approvals` 结尾，**全部落到 `None`**。必须显式加规则。

**因此在 `_required_permission` 里必须新增 5 条显式规则**（#9、#11、#12、#13、#14）。
建议新增一个 `wiki_path=path.startswith('/v1/wiki/')` 分支，**不依赖**结尾匹配。
`tests/test_route_auth_coverage.py` 应扩展出一条断言：**每一条 `/v1/wiki/*` 的 POST 路由
都必须在映射表里被显式命名**（漏一条即失败）。

### 2.2 白名单与自动授权红线

- `/v1/wiki` **不得**出现在 `PUBLIC_READ_PREFIXES`（`api.py:235`，当前为
  `('/v1/documents','/v1/runs','/assets/')`）。需求 §7 第 146 行明确"首次实现不加入匿名读取例外"。
- `/v1/wiki` **不得**加入 `PUBLIC_PATHS` / `PUBLIC_WRITE_PATHS`（`api.py:232` / `:246`）。
- **不得复用** `GET /v1/runs*`（`api.py:1564-1630`，匿名可读）做授权。分析报告 §6① 已核实
  该接口的处理函数不接收 identity、SQL 无 ACL 过滤，匿名者可列出全部 run 的 question/status。
  需求 §2 第 42 行也是这个要求。Wiki 必须**自建**授权查询。

### 2.3 缓存头

`_NO_STORE_PREFIXES`（`api.py:247-260`）必须新增 `'/v1/wiki'`。加进去之后
`_no_store_path()`（`api.py:281-282`）用 `prefix + '/'` 匹配，因此 `/v1/wiki` 本身与
`/v1/wiki/...` 的全部子路径都会命中。

| 响应 | `Cache-Control` | 附加 |
| --- | --- | --- |
| 全部 `/v1/wiki/*`（成功与失败一致） | `no-store` | 中间件自动加；`HTTPException` 分支也已带（`api.py:322-323`） |
| 导出（#8） | `no-store` + `Content-Disposition: attachment; filename="wiki-<id>.md"` | 导出内容含私有来源文本，**不得**被代理缓存 |
| 长任务（#9、#11） | `no-store` | 返回任务状态，不阻塞 |

**不使用 ETag / `If-None-Match`**：仓库当前没有任何 ETag 实现
（`grep ETag datamind/api.py` 为空），且私有资源用条件请求会引入"304 泄露存在性"的边界。
仓库既有的"固定版本读取"先例是**查询参数**式校验（`api.py:1347-1348`、
`:1544-1545` 的 `expected_version` + `expected_sha256`），Wiki 沿用这个形态（见 §5.5、§7）。

---

## 3. 权限矩阵（4 个扁平角色 × 14 个接口）

### 3.1 矩阵

`✓` = 允许；`✗` = 403 `WIKI_PERMISSION_DENIED`。

| # | 接口 | `viewer` | `reviewer` | `approver` | `admin` |
| --- | --- | --- | --- | --- | --- |
| 1 | `GET /v1/wiki/entries` | ✓ | ✓ | ✓ | ✓ |
| 2 | `GET /v1/wiki/spaces/{id}/graph` | ✓ | ✓ | ✓ | ✓ |
| 3 | `GET /v1/wiki/entries/{id}` | ✓ ¹ | ✓ ¹ | ✓ ¹ | ✓ ¹ |
| 4 | `GET /v1/wiki/entries/{id}/revisions` | ✓ | ✓ | ✓ | ✓ |
| 5 | `GET /v1/wiki/entries/{id}/revisions/{rev}` | ✓ | ✓ | ✓ | ✓ |
| 6 | `GET /v1/wiki/entries/{id}/graph` | ✓ | ✓ | ✓ | ✓ |
| 7 | `GET /v1/wiki/entries/{id}/provenance` | ✓ | ✓ | ✓ | ✓ |
| 8 | `GET /v1/wiki/entries/{id}/export` | ✓ | ✓ | ✓ | ✓ |
| 9 | `POST /v1/wiki/builds` | ✗ | **✓** | ✗ ² | ✓ |
| 10 | `GET /v1/wiki/builds/{id}` | ✓ ³ | ✓ ³ | ✓ ³ | ✓ ³ |
| 11 | `POST /v1/wiki/builds/{id}/retry` | ✗ | **✓** ³ | ✗ ² | ✓ ³ |
| 12 | `POST /v1/wiki/entries/{id}/revisions` | ✗ | **✓** | ✗ ² | ✓ |
| 13 | `POST /v1/wiki/entries/{id}/reviews` | ✗ | **✓** | ✗ ² | ✓ |
| 14 | `POST /v1/wiki/entries/{id}/publications` | ✗ | ✗ ² | **✓** | ✓ |

**脚注（矩阵的边界，逐条说明，不要省略）**：

1. **#3 的"草稿需编辑权限"**：`GET` 本身不做角色门。当目标版本是 `draft` / `in_review`
   且请求者不是该版本的作者、也没有 `reviewer`/`approver`/`admin` 时，返回
   `404 WIKI_ENTRY_NOT_FOUND`（与不存在**完全相同**）。`admin` 与 `reviewer` 可以读草稿；
   `approver` **也可以**读草稿（发布前必须能读，否则 #14 不可用）——但 `approver` **不能**写草稿。
2. **`✗ ²` 是刻意的**：`reviewer` 与 `approver` **互不继承**（`auth.py:195`：
   `if permission not in user['roles'] and 'admin' not in user['roles']`）。
   `approver` 访问 #9/#11/#12/#13 会得到 `403 WIKI_PERMISSION_DENIED`；
   `reviewer` 访问 #14 同样 403。**不要**在 Wiki 里补一层
   "approver 隐含 reviewer" 的放宽——那会与既有认证面不一致，且需求 §7 第 165 行明说
   "不能假设 `approver` 自动拥有 `reviewer` 权限"。
3. **owner 校验是权限之外的独立一道门**：#10 与 #11 额外要求
   `identity['user_id'] == build.owner_id`。非 owner 且非 `admin` 读别人的整理任务
   返回 `404 WIKI_BUILD_NOT_FOUND`（与不存在不可区分）。`admin` 读所有 owner 的任务。
4. **角色门通过不扩大来源权限**（需求 §7 第 165 行）：`reviewer` 能建词条，
   但只能引用**他自己空间内**的证据引用；引用了不可访问的来源 → `403 WIKI_SOURCE_FORBIDDEN`。
5. **空间归属**：第一版 `space_id` 与 `user_id` 一一对应。请求别人的 `space_id` →
   `404 WIKI_SPACE_NOT_FOUND`（与不存在不可区分）。

### 3.2 权限实现落点（写代码时的检查清单）

| 落点 | 文件:行 | 要做的事 |
| --- | --- | --- |
| 路径→权限映射 | `api.py:285-304` | 新增 5 条显式规则（§2.1）。**漏一条 = 越权或误拒** |
| 匿名白名单 | `api.py:232` / `:235` / `:246` | **不动**（确认没被加进去） |
| no-store 前缀 | `api.py:247-260` | 新增 `'/v1/wiki'` |
| 中间件 | `api.py:307-334` | 不需要改；`/v1/wiki/*` 自动落进"需要身份 + 写操作要 CSRF + 同源"的分支 |
| 默认拒绝测试 | `tests/test_route_auth_coverage.py` | 扩展：每条 `/v1/wiki/*` POST 路由都必须在映射表里被显式命名 |
| 越权测试 | 新增 `tests/test_wiki_authz.py` | 用户 A 不得读取/搜索/导出/通过计数推断用户 B 的私有内容 |

### 3.3 `DATAMIND_LOCAL_NO_LOGIN` 对矩阵的影响（必须知道的演示边界）

```
auth.py:59-63  demo_allowed():
               flag = os.environ.get('DATAMIND_LOCAL_NO_LOGIN')
               flag is not None → 任意来源都是 admin（只要不为空/0/false/no/off）
               否则 → socket_host(request) in {'127.0.0.1','::1','localhost'} 时是 admin
```

- Wiki 演示环境**绝不能**设置 `DATAMIND_LOCAL_NO_LOGIN=1`：设了之后**任意来源**拿到 admin，
  §3.1 的矩阵**全部失效**（`reviewer`/`approver`/`viewer` 的区分没有意义）。
- 不设置也不行：**回环来源默认也是 admin**（`auth.py:63`）。本机浏览器演示时看到的仍然是 admin，
  矩阵同样退化。
- **因此矩阵的验收必须用非回环来源 + 真实登录账号**（`POST /v1/auth/login` 拿 cookie 与
  `csrf_token`），或至少显式声明"本次验收在 admin 旁路下进行，角色隔离未被验证"。
  需求 §7 的验收要求（用户 A 不能读用户 B）**无法在回环演示下验证**。
- 隧道 / 反向代理**不得**把来源变成回环：`auth.py:44-56` 的 `socket_host` 只读
  `scope['client']`，`--no-proxy-headers` 已在启动脚本里（见 `auth.py` 模块注释）。

---

## 4. 分页与查询上限（冻结）

| 对象 | 参数 | 默认 | 上限 | 超出 | 游标 |
| --- | --- | --- | --- | --- | --- |
| 词条目录（#1） | `limit` | **20** | **50** | 框架 422 | `cursor` 不透明 |
| 空间图（#2） | `limit`（**每批节点数**） | **50** | **50** | 固定批次，不接受更大值 | `cursor` |
| 空间图（#2） | 累计已加载节点 | — | **100** | 达到上限后 `has_more=true` 但 `next_cursor=null`、`truncated=true` | — |
| 空间图（#2） | 累计已加载关系 | — | **300** | 同上 | — |
| 图邻域（#6） | `depth` | **1** | **2** | 框架 422 | — |
| 图邻域（#6） | `limit` | 50 | 100（受同一 100 节点 / 300 关系上限约束） | 截断 + `truncated=true` | — |
| 证据与事件（#7） | `limit` | 50 | **200** | 框架 422 | `cursor` |
| 版本历史（#4） | `limit` | 20 | 50 | 框架 422 | `cursor` |
| 整理任务列表 | `limit` | 20 | 50 | 框架 422 | `cursor` |
| 导出（#8） | — | — | 单次最多 **300** 条证据引用 | `WIKI_EXPORT_TOO_LARGE` | — |

**分页语义（冻结，全部读接口一致）**：

1. **游标是不透明字符串**，客户端**不得**解析。第一版实现建议
   `base64(排序键值)`，因为仓库已有稳定排序惯例
   （`/v1/runs` 用 `ORDER BY started_at DESC, run_id DESC`，`/v1/sessions` 用 `updated_at DESC, session_id`）。
2. 排序必须带**唯一兜底键**（`entry_id` / `relation_id` / `event_id`），否则同一窗口重复请求
   可能翻出重复行。
3. 响应恒带 `limit`、`has_more`、`next_cursor`、`truncated` 四个字段。
   `truncated=true` 表示**因为上限而截断**，不是"没有更多"。
4. **图层或筛选条件变化时，服务端视为新集合**：忽略传入的 `cursor`，
   返回第一批并置 `truncated=false`。客户端**必须**同时重置已加载集合
   （否则"继续加载绕过上限"会在客户端侧发生）。
5. **达到 100 节点 / 300 关系上限后**：响应必须带
   `limit_reached: {'nodes': 100, 'relations': 300}` + 明确的缩小范围提示文案键
   （`WIKI_GRAPH_LIMIT_REACHED`），**并且 `next_cursor` 为 `null`**。
   需求 §7 第 169 行："达到显示上限后提示缩小范围，**不能通过继续加载绕过上限**"。
6. **关系两端都必须在当前已授权且已加载的节点集合内**（需求 §7 第 169 行）。
   跨批次的"半条边"要么延后到两端都在时才返回，要么不返回——**不得**只返回一端。
7. **节点连接数只统计当前授权 + 当前图层 + 已加载的连接**（需求 §7 第 171 行 /
   §8.2 第 198 行）。**不返回**隐藏内容的总度数——那会通过计数泄露存在性。

### 4.1 上限的具体数字为什么是这些（可核对）

- 100 节点 / 300 关系 / 300 迭代或 5 秒：来自需求 §8.4 第 221 行，本次一并冻结（§9.3）。
- 目录 20/50：与既有 `GET /v1/sessions` 一致（`docs/SESSIONS.md` §2）。
- 事件每页 200：与既有 `GET /v1/runs/{run_id}/events` 的 `LIMIT 200` 一致（`api.py:1603`）。
- 空间图每批 50 节点：`50 × 2 = 100`，正好两批达到节点上限；
  `300 / 100 = 3`，即平均每节点 3 条关系。三者是同一组自洽的数。

---

## 5. 接口契约

统一响应包装（全部接口一致）：

```jsonc
{
  "data":   { /* 接口专属 */ },
  "meta":   { "space_id": "wsp-…", "as_of": "2026-10-14T09:00:00+08:00",
              "authorized_scope": { "nodes_considered": 48, "nodes_visible": 48 } },
  "paging": { "limit": 20, "has_more": false, "next_cursor": null, "truncated": false }
}
```

`meta.authorized_scope` 是**授权投影的自证**：`nodes_considered` 是投影前看到的对象数，
`nodes_visible` 是过滤后返回的数。两者不等时**必须**在响应的 `warnings` 里给出原因码
（`AUTHORIZATION_FILTERED`）。这条的意义是：**客户端不能把"我看不到"误读成"不存在"**，
服务端也不能把"过滤掉了"悄悄吞掉。

错误响应统一沿用仓库惯例：

```json
{ "detail": { "error_code": "WIKI_ENTRY_NOT_FOUND", "message": "…", "hint": "…" } }
```

### 5.1 `GET /v1/wiki/entries` —— 词条目录与关键词搜索

| 参数 | 类型 | 默认 | 边界 | 说明 |
| --- | --- | --- | --- | --- |
| `q` | string | 无 | ≤ 200 字符 | 先覆盖**标题、别名、分类**；空白等于不传（沿用 `/v1/sessions` 的 `q` 语义） |
| `type` | enum | 无 | `business_entity` / `metric` / `norm` / `conclusion` | 可重复 |
| `status` | enum | 无 | `draft` / `in_review` / `published` / `archived` | 发布流程状态（不是核验状态，见 §7） |
| `category` | string | 无 | ≤ 64 字符 | 目录分类 |
| `updated_since` | ISO8601 | 无 | — | 最近更新窗口 |
| `space_id` | string | 当前用户空间 | — | 请求别人的空间 → 404 |
| `limit` / `cursor` | — | 20 / 无 | ≤ 50 | §4 |

**`q` 的匹配规则必须显式冻结**（沿用 `docs/SESSIONS.md` §6 的既有语义）：

- `ILIKE`（大小写不敏感）+ **转义通配符**：`\`、`%`、`_` 全部是字面量，带 `ESCAPE '\'`。
  用户搜 `100%` 时 `%` **不是**通配符。
- **中文正文检索策略未定**（需求 §7 第 171 行）：第一版只覆盖**标题 / 别名 / 分类**，
  中文正文的检索方式在样本验证后确定。响应里的 `search.scope` 字段必须**如实**声明本次
  检索覆盖了哪些字段，避免用户以为搜了正文。
- **不引入任何 Postgres 扩展**（仓库没有 `CREATE EXTENSION`，`docs/SESSIONS.md` §6 同款约定）。

`200`：

```jsonc
{ "entries": [ { /* entry 对象，见下 */ } ],
  "search": { "q": "温度", "scope": ["title","aliases","category"], "body_searched": false } }
```

**entry 对象**（目录、详情、图节点三处共用同一投影，**字段一字不差**）：

```jsonc
{
  "entry_id": "wiki-1f2e3d4c5b6a798801234567",
  "entry_type": "business_entity",
  "title": "设备 A102",
  "aliases": ["A102", "工序30设备"],
  "category": "工业设备",
  "business_key": { "space_id": "wsp-…", "namespace": "industrial-demo",
                    "entry_type": "business_entity",
                    "key_part": "part\x1fA102", "confirmation": "pending" },
  "confirmation": "pending",
  "summary": "合成演示零件；实体键尚未人工确认。",
  "status": { "publication": "published", "assertion": "verified", "source_validity": "current" },
  "head_revision_id": "wrev-…",
  "revision_count": 3,
  "evidence_ref_count": 12,
  "relation_counts": { "visible": 7, "truncated": false, "scope": "authorized_loaded" },
  "updated_at": "2026-10-14T09:00:00+08:00",
  "demo_data": true
}
```

- `status` 是**三个独立字段**，**不得**合并成一个总状态（`WIKI_DOMAIN.md` §5.4）。
- `relation_counts.scope` 恒为 `authorized_loaded`：**只统计当前授权、当前图层、已加载的连接**。
- `demo_data`：本库全部数据都是合成演示数据，词条**必须**带这个标记（需求 §1 第 20 行）。

错误码：`401 LOGIN_REQUIRED` / `404 WIKI_SPACE_NOT_FOUND` / `422`（框架，参数边界）。

### 5.2 `GET /v1/wiki/spaces/{space_id}/graph` —— 当前授权范围图

| 参数 | 类型 | 默认 | 边界 |
| --- | --- | --- | --- |
| `layer` | enum | `entries` | `entries`（词条）/ `evidence`（来源证据）/ `runs`（运行轨迹） |
| `q` | string | 无 | ≤ 200 |
| `type` / `category` / `status` | — | 无 | 同 §5.1 |
| `limit` | int | **50** | 固定 50，不接受更大值（§4） |
| `cursor` | string | 无 | 图层或筛选变化时服务端忽略 |

`200`：

```jsonc
{ "nodes": [ { "node_kind": "entry", "entry": { /* entry 对象 */ },
               "degree": 3, "degree_scope": "authorized_loaded",
               "position_hint": null } ],
  "relations": [ { "relation_id": "wrel-…", "src": "wiki-…", "dst": "wiki-…",
                   "relation_type": "supports", "direction": "directed",
                   "origin": "projected", "raw_type": "supports", "supported": true,
                   "label": "支撑", "basis": { /* §5.6 的 basis */ } } ],
  "layer": "entries",
  "loaded": { "nodes": 50, "relations": 41 },
  "limits": { "nodes": 100, "relations": 300 },
  "limit_reached": null }
```

**必须遵守的四条**（需求 §7 第 169-171 行 + §8.2）：

1. 图层或筛选变化 → 重置游标与加载集合，`truncated=false`。
2. 关系两端都必须在已授权且已加载的节点集合内。
3. 达到上限 → `next_cursor=null` + `limit_reached` 非空 + `WIKI_GRAPH_LIMIT_REACHED` 提示键。
4. **界面称之为"当前授权范围图"，不得描述为全库全图**；分页、邻域、轨迹分别标明
   "尚有后续"或"已截断"。

错误码：`401` / `404 WIKI_SPACE_NOT_FOUND` / `422`。

### 5.3 `GET /v1/wiki/entries/{entry_id}` —— 当前发布版本

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `revision_id` | string | 可选；固定版本深链 |
| `include` | csv | `assertions` / `relations` / `provenance_summary`；默认全含 |

`200` 返回**版本对象**（不可变），而不是可变词条：

```jsonc
{ "entry": { /* entry 对象 */ },
  "revision": {
    "revision_id": "wrev-…", "entry_id": "wiki-…", "base_revision_id": null,
    "title": "设备 A102", "body_markdown": "…",
    "citations": [ { "evidence_ref_id": "wev-…", "marker": "[1]" } ],
    "assertions": [ {
      "assertion_id": "wassert-…",
      "comparison_key": "sha256:…",
      "entity_key": { "part_id": "A102", "confirmation": "pending" },
      "metric": "holding_temperature",
      "raw_value": "870", "raw_unit": "degC",
      "normalized_value": "870.000", "normalized_unit": "degC",
      "time_scope": { "from": "2026-09-01T00:00:00+08:00", "to": null,
                      "timezone": "Asia/Shanghai" },
      "applicability": { "source_revision": "A102-V2",
                         "valid_from": "2026-09-01T00:00:00+08:00", "valid_to": null },
      "record_scope": { "measurement_id": "M001-1", "batch_id": "B001", "operation_no": "30" },
      "statistic": "core",
      "evidence_refs": ["wev-…"],
      "verification": { /* §7 的核验记账 */ }
    } ],
    "author": { "user_id": "…", "display_name": "…" },
    "generation": { "method": "template" | "human" | "model_polish",
                    "template_version": "wiki-template-v1", "model_id": null },
    "created_at": "2026-10-14T09:00:00+08:00"
  },
  "relations": [ /* 该版本的关系，§5.2 同款 */ ],
  "review_events": [ /* §5.9 同款 */ ],
  "provenance_summary": { /* §5.7 的 summary 段 */ } }
```

- **读取历史版本时同时返回该版本的关系与核验记录**（需求 §5.2 第 117 行）。
- 草稿 / 待复核版本：需要编辑权限；否则与不存在一致的 404（§3.1 脚注 1）。
- `base_revision_id` 是必填字段（可为 `null` 表示首版）；**不接受**"以当前头为基线"的隐式语义。

错误码：`401` / `404 WIKI_ENTRY_NOT_FOUND` / `404 WIKI_REVISION_NOT_FOUND` / `403 WIKI_PERMISSION_DENIED`。

### 5.4 `GET /v1/wiki/entries/{entry_id}/revisions` —— 可见版本历史

| 参数 | 默认 | 边界 |
| --- | --- | --- |
| `limit` | 20 | ≤ 50 |
| `cursor` | 无 | — |

`200` 每个版本一条**摘要**（不含正文，正文走 #5）：

```jsonc
{ "revisions": [ { "revision_id": "wrev-…", "base_revision_id": null,
                   "status": "published", "title": "…", "author": {…},
                   "generation_method": "template",
                   "citation_count": 3, "assertion_count": 2,
                   "body_sha256": "…", "created_at": "…",
                   "events": [ { "event": "published", "at": "…", "actor": "…" } ] } ] }
```

- `body_sha256` 让客户端能判断"两个版本正文是否真的不同"，而不用拉全文。
- **不可变**：同一个 `revision_id` 的任何请求，正文与引用必须逐字节一致
  （需求 §5.2 第 116 行）。

### 5.5 `GET /v1/wiki/entries/{entry_id}/revisions/{revision_id}` —— 固定版本详情

`200` = §5.3 的 `revision` 对象（**不带** `entry.status` 的当前值，
只带**该版本记录的**状态快照）。理由：词条当前头可能已经前进，
把当前状态贴在历史版本上就是"用新事实改写历史读取"。

额外支持仓库既有的**固定版本校验**形态（先例 `api.py:1347-1348` / `:1544-1545`）：

| 参数 | 说明 |
| --- | --- |
| `expected_body_sha256` | 可选，`^[a-f0-9]{64}$`；不一致 → `409 WIKI_VERSION_CONFLICT` |
| `expected_base_revision_id` | 可选；不一致 → `409 WIKI_VERSION_CONFLICT` |

**历史来源定位显式绑定旧版本**（需求 §5.2 第 121 行）：本接口返回的每次来源定位
都带 `source_version`，**禁止**静默跳转到最新文档或最新 SQL 数据。

### 5.6 `GET /v1/wiki/entries/{entry_id}/graph` —— 授权关系邻域

| 参数 | 默认 | 边界 |
| --- | --- | --- |
| `depth` | **1** | **1..2**，超出 → 422 |
| `limit` | 50 | ≤ 100，且受 100 节点 / 300 关系上限约束 |
| `layer` | `entries` | 同 §5.2 |
| `direction` | `both` | `in` / `out` / `both`（无向关系在三种取值下都返回） |

`200` 与 §5.2 同构，另加：

```jsonc
{ "root": "wiki-…",
  "truncated": true,
  "truncated_reason": "NODE_LIMIT" | "RELATION_LIMIT" | "DEPTH_LIMIT",
  "unreached_neighbors": 12 }
```

- `unreached_neighbors` **只统计当前授权范围内**、因截断而未返回的邻居数；
  非授权对象**不计入**（否则用数字就能推断隐藏图的存在性）。
- `truncated=true` 时**必须**给 `truncated_reason`，前端才可能给出"缩小范围"的具体建议。

### 5.7 `GET /v1/wiki/entries/{entry_id}/provenance` —— 证据与执行事件摘要

| 参数 | 默认 | 边界 |
| --- | --- | --- |
| `revision_id` | 当前头 | 必须是同一词条的版本 |
| `limit` | 50 | **≤ 200**（事件每页上限，与 `api.py:1603` 一致） |
| `cursor` | 无 | — |
| `layer` | `both` | `evidence` / `runs` / `both` |

`200`：

```jsonc
{
  "summary": {
    "evidence_ref_count": 12, "run_count": 3, "event_count": 27, "error_count": 2,
    "source_validity": { "current": 10, "stale": 1, "unavailable": 1 },
    "provenance_complete": false,
    "provenance_incomplete": {
      "applies": true,
      "missing_fields": ["edge.evidence", "edge.binding_type", "edge.confidence"],
      "reason_code": "EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA",
      "detected_at": "2026-10-14T09:00:00+08:00"
    }
  },
  "evidence_refs": [ {
      "evidence_ref_id": "wev-…",
      "fingerprint": "sha256:…",
      "source_kind": "doc" | "sql",
      "source_ref": "doc-c43b1575ac57",
      "source_version": { "document_version": 1, "source_sha256": "805851ba…" },
      "locator": { "locator_kind": "pdf_region", "page_no": 1,
                   "bbox": [62.7, 233.7, 532.6, 366.7],
                   "block_id": "blk-a7910ea1fc5c",
                   "contract_version": "parse-contract-v1" },
      "content_sha256": "4934e981…",
      "excerpt": "|项目|参数|…|续航|降噪开启约 38 小时|…",
      "source_validity": "current",
      "raw_node_types": ["Chunk"],
      "supported": true
  } ],
  "runs": [ {
      "run_id": "run-894b0785725c45f3",
      "question": "无线降噪耳机Pro的续航时间是多少？",
      "route": "rag", "status": "COMPLETED", "confidence_level": "medium",
      "started_at": "…", "ended_at": "…", "elapsed_ms": 6629.4,
      "error_summary": null,
      "event_count": 8, "event_types": ["run.started","query.parsed","plan.created",
                                        "tool.started","retrieval.completed",
                                        "evidence.attached","verification.passed",
                                        "answer.completed"]
  } ],
  "events": [ {
      "event_id": "evt-f50186b0d3f44017-0009",
      "run_id": "run-…", "event_type": "answer.completed", "status": "succeeded",
      "agent_name": "orchestrator", "tool_name": null,
      "started_at": "…", "ended_at": "…", "duration_ms": 12,
      "input_summary": {}, "output_summary": { /* 已脱敏 */ },
      "error_code": null, "retry_index": 0, "visibility": "visible"
  } ]
}
```

**四条硬约束**：

1. **`provenance_incomplete` 的语义见 §6**，必须照抄，不得推测补全。
2. **`error_summary` 必须存在且非空语义**（`WIKI_DOMAIN.md` §7.1）：
   - 本次运行**有**错误 → `{ "error_code": "...", "event_type": "...", "agent_name": "...",
     "at": "...", "detail": "..." }`；
   - 本次运行**无**错误 → **显式** `{ "error_code": null, "none": true }`，
     **不是** `null`、**不是**缺字段；
   - 读取失败 → `{ "error_code": null, "unavailable": true }`，
     **绝不**显示成"无错误"。
3. **脱敏**：`visibility != 'visible'` 的事件**不返回**；`output_summary` 只返回白名单字段，
   不返回凭据、内部绝对路径、私有执行会话载荷（需求 §7 第 167 行）。
   **不得**生成或保存模型隐藏思维过程（需求 §3.3 第 67 行）。
4. **窄屏可访问性是接口义务，不是前端义务**：本接口在窄屏下必须能在**不加载完整图谱**的前提下
   独立调用（`WIKI_DOMAIN.md` §7.2）。因此 `provenance` **不得**依赖 #2 的图数据。

### 5.8 `GET /v1/wiki/entries/{entry_id}/export` —— Markdown 导出

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `revision_id` | 当前发布头 | 固定版本导出 |
| `expected_body_sha256` | 无 | 校验，不一致 → 409 |

`200` → `text/markdown; charset=utf-8` + `Content-Disposition: attachment` + `no-store`。

**导出必须保留**（需求 §3.1 第 54 行）：

1. **知识状态**（三套状态分别标注，不合并）；
2. **引用**（`[1]` 标记 + 引用清单，含来源版本与定位）；
3. **来源版本**（`source_sha256` / `document_version` / 快照 id）；
4. **运行标识**（`run_id` 逐个列出）；
5. **演示属性**（`demo_data: true` 时必须写入导出正文，需求 §1 第 20 行）；
6. `provenance_incomplete` 的声明（§6），**不得**因为在 Markdown 里就省略。

**导出不扩大访问范围**：导出与页面走**同一个授权投影**（需求 §3.3 第 68 行、§7 第 167 行）。
超出 300 条证据引用 → `WIKI_EXPORT_TOO_LARGE`（不是截断后声称完整）。

### 5.9 `POST /v1/wiki/builds` —— 从指定运行创建候选草稿

权限：`reviewer`（§2 表）。CSRF + 同源必须，写事务内 `lock_identity`。

```jsonc
{ "request_id": "build-20261014-1",          // 幂等键，必填，≤ 64
  "run_id": "run-894b0785725c45f3",          // 必填
  "target_entry_id": null,                    // 可选：并入已有词条；null = 新建候选
  "entry_type_hint": "conclusion",            // 可选
  "space_id": "wsp-…",                        // 可选，默认当前用户空间
  "mode": "template",                         // 'template'（默认）| 'template+polish'
  "selection": { "claim_ids": ["claim-80ac4ad77526407b"],   // 可选：只整理指定主张
                 "evidence_ids": null } }
```

`202` → `{ "build": { "build_id": "wjob-…", "status": "queued",
"request_id": "build-20261014-1", "payload_sha256": "…", "run_id": "…",
"created_at": "…", "result_revision_id": null, "error_code": null } }`

**幂等与恢复（需求 §5 表 + P2 验收）**：

| 情形 | 行为 |
| --- | --- |
| 同 `request_id` + 同 owner + **同规范载荷摘要** | 返回**首次**结果（200/202），**不创建**重复版本 |
| 同 `request_id` + **不同载荷摘要** | `409 WIKI_IDEMPOTENCY_CONFLICT` |
| 同 `request_id` + **不同 owner** | `404 WIKI_BUILD_NOT_FOUND`（存在性隐藏） |
| 失败任务重试（#11） | 保留原请求、原 owner、原载荷摘要 |
| 目标运行不属于请求者且无 admin | `403 WIKI_RUN_FORBIDDEN` |
| 运行证据不足 / 运行失败 | **仍可建草稿**，但草稿里的主张核验状态为 `insufficient`，且**不得**出现"已核验"主张 |

**整理失败不影响原问答**（需求 §4 第 89 行、P2 验收）：`builds` 的任何失败**不得**
改写 `runs` / `claims` / `trace_events`。这是本仓库"记账 best-effort"惯例的直接应用
（先例：`sessions.touch()`，`docs/SESSIONS.md` §8）。

错误码：`401` / `403 WIKI_PERMISSION_DENIED` / `403 WIKI_RUN_FORBIDDEN` /
`403 WIKI_SOURCE_FORBIDDEN` / `404 WIKI_RUN_NOT_FOUND` / `409 WIKI_IDEMPOTENCY_CONFLICT` /
`422 WIKI_BUILD_REQUEST_INVALID`。

### 5.10 `GET /v1/wiki/builds/{build_id}` —— 查看自己的整理任务

无角色门，但**owner 校验在 SQL 里**：非 owner 且非 admin → `404 WIKI_BUILD_NOT_FOUND`
（与不存在字节一致）。

```jsonc
{ "build": { "build_id": "wjob-…", "status": "succeeded",  // queued|running|succeeded|failed
             "run_id": "…", "request_id": "…", "payload_sha256": "…",
             "owner": { "user_id": "…" },
             "result_revision_id": "wrev-…", "result_entry_id": "wiki-…",
             "error_code": null, "error_detail": null,
             "created_at": "…", "updated_at": "…",
             "attempts": 1, "candidate_entry_id": "wiki-…",
             "suggested_merges": [ { "entry_id": "wiki-…", "reason": "alias_overlap",
                                     "requires_human_confirmation": true } ] } }
```

- `suggested_merges` **只能**是建议，**必须**带 `requires_human_confirmation: true`
  （需求 §3.3 第 64 行：名称相近只能产生合并建议）。
- 失败时 `error_code` 取 §8 的错误码表中的值。

### 5.11 `POST /v1/wiki/builds/{build_id}/retry` —— 原请求失败重试

```jsonc
{ "request_id": "build-20261014-1-retry" }   // 可选；省略则沿用原 request_id 的新尝试
```

`200` → 同 #9 的 `build` 对象（`attempts` 递增）。

- 只有 `status='failed'` 才能重试；其他状态 → `409 WIKI_BUILD_NOT_RETRYABLE`。
- **必须是原 owner 且当前仍有编辑权限**；权限已撤 → `403 WIKI_PERMISSION_DENIED`。
- 保留原目标运行、原载荷摘要、原空间；**不得**改成"最新运行"（需求 §6 第 140 行同款精神）。

### 5.12 `POST /v1/wiki/entries/{entry_id}/revisions` —— 以基线创建新版本

```jsonc
{ "request_id": "rev-20261014-1",
  "base_revision_id": "wrev-…",        // 必填；首版传 null
  "title": "设备 A102 的温度要求",
  "body_markdown": "…",
  "citations": [ { "marker": "[1]", "evidence_ref_id": "wev-…" } ],
  "assertions": [ { /* §5.3 的 assertion，人工调整数值/单位/适用范围时会填 */ } ],
  "relations": [ { "src": "wiki-…", "dst": "wiki-…",
                   "relation_type": "threshold_of", "basis_note": "…" } ],
  "change_note": "按 B004 批次重算" }
```

`201` → `{ "revision": {…}, "entry": {…} }`。

**必守规则**：

1. `base_revision_id` **必填**。与当前头不一致 → `409 WIKI_VERSION_CONFLICT`
   （需求 §5.2 第 118 行：发布采用 CAS，过时基线返回冲突）。
2. **人工调整数值、引用、单位、适用范围或关系依据后，创建新版本并重新核验**
   （需求 §5.2 第 119 行）。因此当 `assertions` 与基线的对应项不同时，
   服务端**必须**把这些主张的核验状态重置为 `unverified`，
   **不得**沿用基线版本的核验结论；响应的 `revalidation_required: true` 必须置位。
3. **已保存版本的正文与引用不可原地覆盖**：本接口**只**新增版本。
4. 引用不可达 → `422 WIKI_REFERENCE_INVALID`（对应需求 §8.4"引用失效"）。
5. 正文上限：`body_markdown` ≤ 256 KiB；`assertions` ≤ 200 条；`relations` ≤ 100 条。
   超出 → `422 WIKI_REVISION_TOO_LARGE`（**不截断后声称完整**）。

### 5.13 `POST /v1/wiki/entries/{entry_id}/reviews` —— 对指定版本追加复核结果

```jsonc
{ "request_id": "review-20261014-1",
  "revision_id": "wrev-…",
  "action": "submit_review" | "approve_facts" | "reject_facts" | "request_changes",
  "scope": { "assertion_ids": ["wassert-…"], "full_revision": false },
  "verdict": { "assertion": "verified" | "insufficient" | "conflicted" | "unverified" },
  "basis": { "rule_version": "…", "method": "human_review",
             "coverage": "A102 / holding_temperature / 2026-09",
             "evidence_ref_ids": ["wev-…"] },
  "note": "…" }
```

`201` → `{ "review_event": { "review_event_id": "wevt-…", "action": "…", "actor": {…},
"at": "…", "expected_head_revision_id": "wrev-…", "based_on_head_revision_id": "wrev-…" },
"revision": {…} }`

**必守规则**：

1. **追加式**：复核记录**不可修改、不可删除**，只能追加。
2. `expected_head_revision_id` 必须带上；与当前头不一致 → 409（需求 §5 表：预期旧发布头）。
3. `verdict.assertion='verified'` 时，`basis` 的六项**必须齐全**
   （核验对象、方法、覆盖范围、规则版本、操作者、时间）——缺项 → `422 WIKI_VERIFICATION_SCOPE_REQUIRED`。
   时间由服务端写，不接受客户端传入。
4. **复核不得自动激活来源权威规则**（需求 §6 第 138 行）；
   相关规则继续使用既有 `source_authority_rules` / `source_authority_events` 登记与审批机制。

### 5.14 `POST /v1/wiki/entries/{entry_id}/publications` —— 发布指定版本

```jsonc
{ "request_id": "pub-20261014-1",
  "revision_id": "wrev-…",
  "expected_head_revision_id": "wrev-…",   // 必填；首次发布传 null
  "note": "…" }
```

`201` → `{ "entry": {…}, "publication": { "revision_id": "wrev-…",
"previous_head_revision_id": "wrev-…", "published_at": "…", "published_by": {…},
"review_events": ["wevt-…"] } }`

**必守规则**：

1. **CAS**：`expected_head_revision_id` 与当前发布头不一致 → `409 WIKI_VERSION_CONFLICT`，
   **不改**发布头、**不删**其他版本（需求 §5.2 第 118 行、P1 验收"并发发布不会覆盖较新的发布头"）。
2. 发布头**必须指向同一词条的版本**（复合外键语义）。
3. `business_key.confirmation='pending'` 时 → `409 WIKI_ENTRY_IDENTITY_PENDING`
   （`WIKI_DOMAIN.md` §5.1：可进草稿，不可发布）。
4. 目标版本必须**至少有一条复核记录**；否则 → `409 WIKI_REVIEW_REQUIRED`。
5. **允许发布存在 `unresolved` 冲突的说明词条**，但响应与正文必须保留双方依据
   （需求 §6 第 137 行）。此时 `publication.conflicted_assertions` 必须列出冲突主张 id。
6. 发布**不改变**主张核验状态与来源有效性状态（三套状态独立，`WIKI_DOMAIN.md` §5.4）。

---

## 6. `provenance_incomplete` 的语义（冻结）

### 6.1 为什么必然存在

线上 `evidence_edges` 只有 6 列（§12.1 实测）：

```
edge_id | run_id | src_evidence_id | dst_evidence_id | edge_type | weight
```

边的**理由**（`evidence`）、**置信度**（`confidence`）、**绑定类型**（`binding_type`）
**在部署库里没有列**。迁移 `019:90-96` 声明了这三列，但**线上不存在**；
唯一的写入者 `evidence_graph.py:519-522` 也只写 4 列
（`run_id, src_evidence_id, dst_evidence_id, edge_type`）。
`evidence_graph.py:215-225` 的 `add_edge(evidence=..., binding_type=..., confidence=...)`
**只在内存里保存**这些值，落库时丢失。

### 6.2 语义定义

`provenance_incomplete` 表示：**该对象的出处信息中，有一部分从未被持久化，因此不可恢复。**

它**不是**：数据损坏、数据错误、投影失败、可以靠推断补齐的字段。

### 6.3 三条硬规则

| # | 规则 |
| --- | --- |
| 1 | **历史数据必须标记缺失。** 任何投影自 `evidence_edges` 的关系，若其 `evidence` / `binding_type` / `confidence` 无法从库中读到，必须在关系对象上带 `provenance_incomplete`（`applies: true` + `missing_fields`）。 |
| 2 | **不得推测补全。** 不得用 `edge_type` 反推 `binding_type`；不得用节点 `payload` 里的值反算 `confidence`；不得用检索得分、排序位置或模型置信度充当理由（需求 §3.3 第 66 行：排序、检索得分和模型置信度**不决定权威**）。 |
| 3 | **缺失要可见。** 关系详情、词条正文、导出 Markdown、事件时间线四处都必须能读到这个标记；**不得**只在调试接口里暴露。 |

### 6.4 字段与理由码

```jsonc
"provenance_incomplete": {
  "applies": true,
  "missing_fields": ["edge.evidence", "edge.binding_type", "edge.confidence"],
  "reason_code": "EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA",
  "detected_at": "2026-10-14T09:00:00+08:00",
  "recoverable": false
}
```

| `reason_code` | 触发 | `recoverable` |
| --- | --- | --- |
| `EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA` | 部署库的 `evidence_edges` 没有 `evidence` / `binding_type` / `confidence` 列 | `false` |
| `NODE_BINDING_NOT_PERSISTED` | 节点缺 `binding`（本库 **2 760 个节点全部如此**，见 §12.2） | `false` |
| `SOURCE_SNAPSHOT_ABSENT` | `source_sha256` 与 `document_sha256` **都**为空（实测：`Document` 节点两者互斥，13 个文档只有 `document_sha256`、工业文档 `doc-industrial-0ac6c55838c3` 的 35 个节点只有 `source_sha256`；**没有任何一个两者兼有**） | `false` |
| `TRACE_EVENT_PRUNED` | 该运行的事件已不在保留窗口内 | `false` |
| `REVISION_PREDATES_PROJECTION` | 版本早于 P1 投影上线，没有边的元数据 | `false` |

**新增数据（P1 之后投影的关系）必须携带完整元数据**，`applies=false`。
需求 P1 验收："保存完整的新投影关系依据；历史缺失字段明确标记"。

### 6.5 与"未支持类型"的区别（不要混淆）

| | `provenance_incomplete` | `supported=false` |
| --- | --- | --- |
| 含义 | 字段**存在过但没被存下来** | 字段**存下来了但 Wiki 不认识这个类型** |
| 数据是否在 | 不在（不可恢复） | 在（原值保留） |
| 例子 | `evidence_edges.evidence` 缺失 | `raw_edge_type='retrieved_from'` 若未收录 |
| 关系 | 该关系**仍可用**，只是依据不完整 | 该关系**仍展示**，只是不参与 Wiki 推导 |

---

## 7. 三套状态在响应里的呈现（冻结）

### 7.1 字段

```jsonc
"status": {
  "publication":     "draft" | "in_review" | "published" | "archived",
  "assertion":       "unverified" | "verified" | "insufficient" | "conflicted",
  "source_validity": "current" | "stale" | "unavailable"
}
```

**三个字段分别出现，任何接口都不得只给一个合并状态。**
列表筛选参数 `status` **只**过滤 `publication`（§5.1 的参数表已冻结这一点）；
要按核验状态筛选必须显式传 `assertion_status`，要按有效性筛选必须传 `source_validity`。

### 7.2 `verified` 的必填记账

```jsonc
"verification": {
  "status": "verified",
  "checked_at": "2026-10-14T09:05:00+08:00",
  "checked_by": { "user_id": "…", "method": "human_review" | "rule_engine" },
  "coverage": { "entity_key": {…}, "metric": "holding_temperature",
                "time_scope": {…}, "record_scope": {…} },
  "rule_version": "industrial-rules-v1",
  "accounting": {
    "source_integrity":   { "checked": true,  "value": "sha256 一致" },
    "entity_binding":     { "checked": true,  "binding_status": "bound",
                            "binding_problems": [] },
    "rule_verification":  { "checked": true,  "verification_contract": "retail_metric_ast_contract",
                            "semantically_verified": true, "validator_findings": [] },
    "semantic_support":   { "checked": false, "full_semantic_entailment": "not_independently_verified" },
    "source_authority":   { "checked": false, "authority_rule_id": null }
  }
}
```

**五项分别记账，不互相替代**（需求 §6 第 136 行）。
**`semantic_support` 恒为 `checked: false`**：`full_semantic_entailment` 在既有代码里是
**固定字面量** `"not_independently_verified"`（`agent.py:238` 与 `:629`），
Wiki **不得**把它改写成"已核验"。

**特别提醒**（本库实测）：`verification_contract='time_scope_only'` 时
`semantically_verified=false`，它只证明**时间范围被规范化**，
**不证明指标语义**。词条正文必须显式标注契约名，不能只显示"已核验"。

### 7.3 与 `runs` / `claims` 的既有状态的映射（只读，不改写既有表）

| Wiki 状态 | 既有来源 | 映射（**只读**，Wiki 不写回这些表） |
| --- | --- | --- |
| `assertion=verified` | `claims.status='verified'` **且** `runs.status='COMPLETED'` | 照抄 `agent.py:618-623` 的判据作为**输入**，并额外要求五项记账里已记的部分不为空 |
| `assertion=insufficient` | `claims.status='insufficient_evidence'`；或 `runs.status='ABSTAINED'` 且 `error_code='INSUFFICIENT_EVIDENCE'` | 直接映射 |
| `assertion=conflicted` | `conflict_records.resolution_status='unresolved'`（存在该运行的行）或跨源 `unresolved_conflicts` 非空 | 见 §7.4 |
| `assertion=unverified` | `claims.status='warned'`、无 `claims` 行、或人工新建版本 | `warned` **不得**映射成 `verified`：本库实测 `warned` 同时被用于"演示数据来源"和"核验警示"两种含义 |
| `publication` | **无既有来源** | Wiki 独占，不与 `runs.status` / `claims.status` 混用 |
| `source_validity` | `document_assets.status` / `acl` / `doc_version` + 快照存在性 + 权限复查 | 见 §7.5 |

> **注意**：`claims.status` 的完整取值集是 `verified` / `warned` / `insufficient_evidence`；
> `runs.status` 是 `COMPLETED` / `FAILED_WITH_EVIDENCE` / `NEEDS_CLARIFICATION` / `ABSTAINED`。
> **两个词表都不同于 Wiki 的三套状态**，映射必须显式写出来，**不得**靠同名假设。

### 7.4 冲突的统一响应结构

两条来源路径（`WIKI_DOMAIN.md` §3.5）在 Wiki 里统一成**同一个对象形状**：

```jsonc
"conflicts": [ {
  "conflict_id": "conf-dac12deffa0f4caab917",
  "source_path": "industrial_table",              // 或 "cross_source_trace"
  "source_locator": { "table": "conflict_records", "run_id": "run-77823a01bc9e4986" },
  "comparison_key": "sha256:…",
  "entity_key": { "part_id": "A102", "batch_id": "B001",
                  "operation_no": "30", "measurement_id": "M001-1" },
  "metric": "holding_temperature",
  "conflict_type": "value_conflict",
  "resolution": "unresolved",
  "rule_id": "NO_REGISTERED_AUTHORITY_RULE",
  "authority_rule_id": null,
  "rule_version": "industrial-rules-v1",
  "left":  { "side": "production", "value": "870", "unit": "degC",
             "normalized_value": "870.000", "normalized_unit": "degC",
             "refs": [ { "kind": "sql_measurement", "snapshot": "…", "fact_id": "…" } ] },
  "right": { "side": "report", "value": "850", "unit": "degC",
             "normalized_value": "850.000", "normalized_unit": "degC",
             "refs": [ { "kind": "document_fact", "fact_id": "ifact-…",
                         "source_sha256": "…", "source_revision": "A102-V2" } ] },
  "selected": null,
  "comparison_context": { "unit": "degC", "method": "core",
                          "measured_at": "…", "produced_at": "…" },
  "provenance_incomplete": { "applies": false }
} ]
```

**两条路径到该结构的映射（冻结）**：

| 统一字段 | 工业路径（`conflict_records.payload`） | 跨源路径（`cross_source_result.conflicts[i]`） |
| --- | --- | --- |
| `source_path` | `'industrial_table'` | `'cross_source_trace'` |
| `source_locator` | `{table:'conflict_records', run_id}` | `{event_type:'answer.completed'|'answer.abstained'|'run.failed', run_id, event_id}` |
| `comparison_key` | 由 `entity_key + metric + rule_version` 现场计算 | 由 `entity_id + metric + version + record_scope` 现场计算 |
| `entity_key` | `payload.entity_key`（`part_id`/`batch_id`/`operation_no`/`measurement_id`） | 由 `left.node_id` / `right.node_id` 反查节点 `payload` |
| `conflict_type` | `payload.conflict_type`（实测 5 类：`value_conflict` / `unit_incomparable` / `unit_equivalence` / `measurement_context` / `version_overlap`） | `record.conflict_type`（`value_conflict` / `unit_incomparable`） |
| `resolution` | `payload.resolution_status`（`unresolved` / `needs_review` / `resolved_equivalent`） | `record.resolution_status`（`CONFLICT_UNRESOLVED` / `resolved_by_registered_authority`） |
| `left` / `right` | `payload.left_fact_refs` + `payload.right_fact_refs` + `payload.normalized_values`（键为 `production` / `report_raw` / `report_unit`） | `record.left` / `record.right`（每侧含 `node_id` / `value` / `unit` / `normalized_value` / `normalized_unit` / `source_sha256` / `version` / `record_scope`） |
| `selected` | `payload.selected_fact_ref` | `record.selected_node_id` |
| `rule_id` | `payload.rule_id` | `record.rule_id`（`NO_REGISTERED_AUTHORITY_RULE` 或 `REGISTERED_AUTHORITY_RULE`） |
| `authority_rule_id` | `payload.rule_id` 若来自登记规则 | `record.authority_rule_id` |

**`resolution` 的取值必须归一**：

| 原始值 | 归一到 | 含义 |
| --- | --- | --- |
| `CONFLICT_UNRESOLVED` | `unresolved` | 未裁决 |
| `unresolved` | `unresolved` | 未裁决 |
| `needs_review` | `needs_review` | **有冲突记录但不判定为矛盾**，待人工 |
| `resolved_equivalent` | `resolved_equivalent` | 单位换算后等价，**不是**矛盾 |
| `resolved_by_registered_authority` | `resolved_by_authority` | 登记规则选中了一侧 |

**`needs_review` 与 `resolved_equivalent` 都不得被读成"存在未裁决冲突"**
（§12.3 实测：175 行里只有 35 行是 `unresolved`）。
**`selected: null` 时不得推测选了哪一侧**；
**`unresolved` 的主张在页面上的显示不得是"已确认结论"**（需求 §6 第 137 行）。

### 7.5 `source_validity` 的判定（读取时复查，不缓存）

```
current      : 定位快照存在 AND 权限通过 AND 版本 == 引用时的版本
stale        : 定位快照仍可读 AND 版本 != 当前版本（换版）
unavailable  : 权限失败 OR 定位记录消失 OR 快照缺失
```

- `unavailable` → **不返回任何受限正文、摘要、excerpt 或计数**，
  只返回权限允许的状态提示（需求 §6 第 141 行）。
- `stale` → 返回**旧版本的定位** + "待重新核验"提示，**禁止**静默跳到最新文档
  （需求 §5.2 第 121 行）。
- 依赖 `stale` / `unavailable` 引用的新版本，其主张核验状态**最多**到 `insufficient`。

---

## 8. 错误码表（冻结）

命名统一为 `WIKI_*` 大写蛇形；错误体恒为 `{"detail":{"error_code":"…","message":"…","hint":"…"}}`。
**框架级**（`422`，请求模型/查询参数越界）不走本表，按 FastAPI 惯例处理——与
`docs/SESSIONS.md` §3 的口径一致。

### 8.1 需求 §8.4 的七类失败场景覆盖核对

需求 §8.4 第 223 行列出的场景**必须全部有码**：

| 需求场景 | 覆盖的错误码 |
| --- | --- |
| 加载失败 | `WIKI_LOAD_FAILED` |
| 无权限 | `WIKI_PERMISSION_DENIED` / `WIKI_SOURCE_FORBIDDEN` / `WIKI_RUN_FORBIDDEN` |
| 无证据 | `WIKI_NO_EVIDENCE` |
| 引用失效 | `WIKI_REFERENCE_INVALID` |
| 定位不可用 | `WIKI_LOCATOR_UNAVAILABLE` |
| 整理失败 | `WIKI_BUILD_FAILED` |
| 版本冲突 | `WIKI_VERSION_CONFLICT` |

### 8.2 完整错误码表

| `error_code` | HTTP | 触发 | 客户端应做 |
| --- | --- | --- | --- |
| `LOGIN_REQUIRED` | 401 | 无身份（中间件，沿用既有码） | 跳登录 |
| `SESSION_INVALID` | 401 | 会话失效（既有码） | 跳登录 |
| `CSRF_REQUIRED` | 403 | POST 缺 `X-CSRF-Token`（既有码） | 重新取 token |
| `ORIGIN_NOT_ALLOWED` | 403 | 跨源（既有码） | — |
| `WIKI_PERMISSION_DENIED` | 403 | 角色门不通过（如 `viewer` 调 #9） | 提示无权限，**不**重试 |
| `WIKI_SOURCE_FORBIDDEN` | 403 | 角色门通过但来源不可访问 | 提示来源不可访问 |
| `WIKI_RUN_FORBIDDEN` | 403 | 目标运行不属于请求者且非 admin | 提示运行不可访问 |
| `WIKI_ENTRY_NOT_FOUND` | 404 | 词条不存在 / 无权限读草稿 / 属于别人（三者不可区分） | 显示"不存在或无权限" |
| `WIKI_REVISION_NOT_FOUND` | 404 | 版本不存在或不属于该词条 | — |
| `WIKI_SPACE_NOT_FOUND` | 404 | 空间不存在或不属于请求者 | — |
| `WIKI_BUILD_NOT_FOUND` | 404 | 整理任务不存在 / 非 owner / 非 admin | — |
| `WIKI_RUN_NOT_FOUND` | 404 | 目标 `run_id` 不存在 | 回到问答页 |
| `WIKI_VERSION_CONFLICT` | 409 | 基线/发布头 CAS 失败；`expected_*` 校验失败；`base_revision_id` 过时 | **重新拉取最新版本后重试** |
| `WIKI_IDEMPOTENCY_CONFLICT` | 409 | 同 `request_id` 不同载荷摘要 | 换 `request_id` |
| `WIKI_BUILD_NOT_RETRYABLE` | 409 | 重试非 `failed` 的任务 | 刷新任务状态 |
| `WIKI_ENTRY_IDENTITY_PENDING` | 409 | 发布 `confirmation='pending'` 的词条 | 先确认实体键 |
| `WIKI_REVIEW_REQUIRED` | 409 | 发布没有任何复核记录的版本 | 先提交复核 |
| `WIKI_NO_EVIDENCE` | 409 | 目标运行没有可投影的证据（`evidence_nodes` 无行） | 提示"该运行无证据可整理" |
| `WIKI_BUILD_FAILED` | 409 | 整理任务失败（`build.error_code` 详情在任务对象里） | 查看任务详情 / 重试 |
| `WIKI_REFERENCE_INVALID` | 422 | 引用的证据引用不存在或已被过滤 | 移除该引用 |
| `WIKI_LOCATOR_UNAVAILABLE` | 422 | 定位记录存在但不可解析（bbox 缺失 / 页号越界 / 记录已删） | 显示"定位不可用"，仍展示摘要 |
| `WIKI_BUILD_REQUEST_INVALID` | 422 | 整理请求载荷不合法（`run_id` 空、`mode` 未知…） | 修正载荷 |
| `WIKI_VERIFICATION_SCOPE_REQUIRED` | 422 | `verdict=verified` 但核验记账六项不全 | 补全核验范围 |
| `WIKI_REVISION_TOO_LARGE` | 422 | 正文/主张/关系超上限（§5.12） | 拆分 |
| `WIKI_EXPORT_TOO_LARGE` | 422 | 导出超过 300 条证据引用 | 按版本或范围缩小 |
| `WIKI_GRAPH_LIMIT_REACHED` | 200（提示键） | 达到 100 节点 / 300 关系上限 | 缩小筛选或改用局部邻域 |
| `WIKI_LOAD_FAILED` | 502 | 投影或数据库读取失败 | 可重试；**不得**显示成"无数据" |

**两条实现纪律**：

1. `WIKI_LOAD_FAILED`（读取失败）与 `WIKI_NO_EVIDENCE`（读取成功但没有证据）**必须区分**。
   需求 §10 的验证表要求"轨迹与定位：仅展示真实事件和已证明的定位；**缺失原因可见**"。
   把读取失败显示成"无数据"就是伪造了"确实没有"这个结论。
2. `WIKI_GRAPH_LIMIT_REACHED` 不是错误而是**成功的截断**：HTTP 200，
   通过 §4 的 `limit_reached` 与提示键传达。**不得**用 4xx 表达"到上限了"。

---

## 9. 视觉基线与布局边界（冻结）

### 9.1 视觉基线

以用户提供的参考图为视觉与交互基线（需求 §8.1 第 179 行）：

| 项 | 冻结内容 |
| --- | --- |
| 背景 | **白色底 + 充足留白**（沿用现有页面设计变量，`frontend` 的 Tailwind v4 token，不引入新色板） |
| 节点 | **圆点 + 标题**；孤立节点**仍可见、可点击** |
| 连线 | **低对比度细线**，代表**实际已保存且允许访问**的关系 |
| 布局 | **自由分布的力导向布局**（不是分层卡片布局） |
| 设置面板 | **右上角**紧凑的**可折叠**面板 |
| 路由 | `/#/wiki`（默认关系图）与 `/#/wiki/:entryId`（正文深链），沿用 `createWebHashHistory` |

**参考图的边界**：参考图**只约束呈现方式**；图中的标签与文件名**不得**作为项目知识或示例数据导入
（需求 §8.1 第 179 行）。

**不引入 d3 的既成事实**：`frontend/package.json` 当前**没有** `d3`（也没有 `d3-force`）。
已有的物理依赖只有 **`matter-js ^0.20.0`**（`@types/matter-js ^0.20.2`），
它**已被使用**——唯一用处是 `frontend/src/components/bits/FallingText/FallingText.vue:3`
的 `import Matter from 'matter-js'`（掉落文字动画），**不是**图布局引擎。
`frontend/src/components/stargraph/ConstellationGraph.vue` 是**分层卡片布局**且与 DOM 强耦合，
可参考交互逻辑，**不强行复用其布局和节点呈现**（需求文档第 219 行）。新增 `WikiForceGraph.vue`。

### 9.2 图层、大小与颜色语义（冻结）

| 语义 | 冻结规则 |
| --- | --- |
| 默认图层 | **词条关系**（`layer=entries`） |
| 图层切换 | 来源证据 / 运行轨迹为可切换图层；切换时**重新取得授权数据**，并保留可继续访问的选中词条 |
| 圆点大小 | **只**表示**当前授权且已加载**的连接数；采用**有上下限**的缩放 |
| 圆点大小**不**表示 | 置信度 / 权威 / 全库连接数 / 语义得分 |
| 初始颜色 | 深浅灰色 |
| 颜色组 | 启用后按**词条类型或分类**着色，提供**图例与文字说明** |
| 冲突与核验状态 | **另以文字或标识**表达，**不能只靠颜色辨认** |
| 细线 | 代表实际已保存且允许访问的关系；选中后显示关系类型、方向与依据 |
| 空间距离 / 布局位置 | **只用于排布**，**不代表**语义得分、可靠性或因果关系 |
| 标题 | 默认可读；缩放后按可用空间**减少非选中标签**；选中节点与邻域标题**优先显示**；完整标题始终可在详情或替代目录读取 |
| 运行图层 | 展示运行与词条、证据的**对应关系**；详细事件留在**真实时间顺序的时间线**，不把所有事件铺到空间图中 |

### 9.3 布局开销边界（冻结）

| 边界 | 值 | 达到后 |
| --- | --- | --- |
| 单次显示节点 | **100** | 停止新增，`limit_reached` + 提示缩小范围 |
| 单次显示关系 | **300** | 同上 |
| 迭代次数 | **300** | **先到即停** |
| 运行时长 | **5 秒** | **先到即停** |

**必须做到的六条**（需求 §8.4 第 221 行）：

1. **收敛、暂停、页面隐藏或离开图视图后停止模拟。**
2. **仅在**节点或关系集合、布局参数变化、或用户明确恢复时**重新排布**。
3. **缩放、平移与打开详情不触发全图重新计算**（打开详情后保持当前视野与选中状态）。
4. 超限时采用**筛选或局部邻域**，并**显示范围说明**。
5. 布局计算**不得阻塞**选择、拖拽与详情交互。
6. 尊重系统**低运动偏好**：低运动模式使用静态或短时排布；**暂停后不持续移动节点**。

**异步批次 vs worker 的选择**：需求 §8.4 第 221 行说"在 P4 样本验证后确定"。
**本阶段不做该选择**，但冻结判据：若 5 秒内无法在 100 节点 / 300 边上完成一次排布，
则必须改 worker；否则主线程异步批次足够。

### 9.4 力导向布局自研（已拍板，理由如下）

**决定：自研，不引入 d3（含 `d3-force`），不复用 `matter-js`。**

理由（逐条可核对，非偏好）：

1. **需求已把问题封闭成有界问题**：节点 ≤ 100、边 ≤ 300、迭代 ≤ 300 或 5 秒
   （需求 §8.4 第 221 行），且"收敛、暂停、页面隐藏、离开视图即停"是**硬要求**。
   d3-force 的 `simulation.stop()` / `alpha` 生命周期**可以**表达这些，
   但"后台停算"与"低运动静态排布"需要额外包一层调度，等于自己写了控制层。
2. **自研能精确实现四组设置的有界参数**（需求 §8.3）：排斥力、连接拉力、连接长度、
   向中心聚合程度——这四个参数在有界实现里是四个标量，直接可测；
   接第三方力模型时，"参数如何映射到实际行为"需要额外的适配与测试。
3. **不引依赖**：`frontend/package.json` 现无 `d3`；引入它意味着新增一个运行时依赖、
   构建产物变大、并需要为它写锁版本策略。仓库现有前端依赖面很窄
   （运行时依赖只有 `vue` / `vue-router` / `matter-js`），
   一次为单页图引入大型库不成比例。**也不复用已有的 `matter-js`**：
   它现在只服务一个掉落文字动画（`FallingText.vue:3`），
   是刚体物理引擎而非力导向布局，拿它做有界图布局等于把它当通用求解器用，
   既不能保证"300 次迭代"的语义，也要自己重写约束求解。
4. **暂停/低运动/后台停算的可验证性**：自研的迭代循环是显式的 `for`/`requestAnimationFrame`
   批次，测试可以直接断言"页面隐藏后没有下一帧计算"；
   第三方库的调度是黑盒，只能间接断言。
5. **不在 P0 引入性能承诺**：需求已声明"布局计算应避免阻塞选择、拖拽和详情交互"，
   并允许在 P4 用样本验证后再决定实现形态（异步批次 / worker）。
   自研让这个决定**保持开放**；引 d3 会把决定提前锁死在主线程模型上。

**代价（如实记录）**：自研需要自己实现 O(n²) 的排斥力近似、
需要自己处理数值不稳定（距离趋零）、以及自己保证"确定性"（同一输入同一输出）
——但这些在 100 节点规模下都是可控的，且需求**没有**要求与 d3 的布局结果一致。

---

## 10. P1 迁移的接口侧前置约束（不在本阶段执行）

本节**只记录约束**，不写 SQL、不建表。需求 §5 的 8 张建议表与
`db/migrations/021_wiki_knowledge.sql` 的编号由分析报告 §7 给出（下一个编号是 **021**）。

| # | 约束 | 依据 |
| --- | --- | --- |
| 1 | **绝不 ALTER / CREATE INDEX / COMMENT / GRANT `runs` / `trace_events` / `evidence_*` / `claims` / `conflict_records`** | 这些表 owner 是 `postgres`（§12.4），迁移以 `datamind_app` 执行；实测报错 `must be owner of table <name>`（`docs/SESSIONS.md` §7 记录了 `runs` 的原文）。仓库有历史事故：`InsufficientPrivilege` 导致**整批迁移回滚** |
| 2 | 每张 Wiki 新表**必须** `REVOKE ALL ... FROM PUBLIC, readonly_user` | `deploy/init/02_metadata.sql:322-327` 的 `ALTER DEFAULT PRIVILEGES` 会把新表的 SELECT **自动授予** `readonly_user`，遗漏 REVOKE **不报错、只静默泄露**。既有惯例：`005:50` / `011:36` / `012:50` / `013:17` / `020:78` |
| 3 | 每条语句**自身幂等** | `db/migrate.py` 启动时重放全部 `db/migrations/*.sql`，**单事务、无 ledger、无 checksum**。`CREATE TABLE/INDEX IF NOT EXISTS`；`COMMENT` / `REVOKE` / `GRANT` 天然幂等 |
| 4 | 条件式索引/授权沿用 `019:103-129` 的 `DO $$ ... tableowner = current_user` 守卫 | 同上；这是仓库已验证过的写法 |
| 5 | 新表 owner 是 `datamind_app` → **可自由建索引** | 迁移执行角色即 `datamind_app`（`020:39`） |
| 6 | **兼容线上的窄形状** | §12.1 / §12.2：投影必须能在**没有** `evidence` / `binding_type` / `confidence` 列、
且 `evidence_nodes` 只有 6 列的库上工作（§6） |
| 7 | `readonly_user` 当前可 SELECT **43** 张表 | 迁移后**必须重新核对**这个数字（预期**不变**），作为"没有新增泄露"的检查点 |

---

## 11. P0 样本（真实数据，已用线上库核实）

**数据源**：容器 `datamind-postgres`，库 `datamind_meta`，用户 `postgres`，只读查询。
**库的规模**（实测）：`runs` **238** 行（2026-10-10 ~ 2026-10-14），`conversation_id` 只有 **2** 个非空会话，
`trace_events` **2 386** 行，`evidence_nodes` **2 760** 行，`evidence_edges` **3 111** 行，`claims` **681** 行，
`conflict_records` **175** 行。

**全部数据均为合成演示数据**（`runs.versions.dataset.provenance` 自述：
"Synthetic demonstration authored for this repository; no factory data, real approvals or independent
accuracy claims"；种子摘要 `seed_sha256=5d08098d…`，PDF 摘要 `0ac6c558…`）。
样本词条**必须**标注演示属性。

### 11.1 第一组：文档问答（RAG）

| 项 | 实测值 |
| --- | --- |
| `run_id` | **`run-894b0785725c45f3`** |
| 会话 | `sess-2714a043eadf4e3e8627850d` |
| 问题 | `无线降噪耳机Pro的续航时间是多少？` |
| 状态 | `COMPLETED`，`confidence_level=medium` |
| 时间 | 2026-10-14 08:55:02 → 08:55:08（6 629.4 ms） |
| 答案 | `无线降噪耳机 Pro 在降噪开启时续航约为 38 小时。 [1]` |
| 路由 | RAG（`query.parsed.output_summary.route='rag'`） |
| 事件 | **8** 条：`run.started` / `query.parsed` / `plan.created` / `tool.started` / `retrieval.completed` / `evidence.attached` / `verification.passed` / `answer.completed` |
| 检索 | `mode=hybrid`，`collections=["demo_kb"]`，`snapshot_id=kb-667a76e6874184e3`，`eligible_chunks=107`，`retriever_version=hybrid-rrf-v3` |
| 证据图 | **7 个节点 / 6 条边**：5 × `Chunk`、1 × `Document`（`doc-c43b1575ac57`）、1 × `Claim`（`claim-80ac4ad77526407b`）；边为 5 条 `retrieved_from` + 1 条 `supports` |
| 引用 | 1 条：标题 `09_无线降噪耳机Pro产品手册`，`chunk_id=chk-f4227338f4e1`，`page_no=1`，`bbox=[62.7,233.7,532.6,366.7]`，`block_id=blk-a7910ea1fc5c`，`contract_version=parse-contract-v1`，`document_sha256=805851bac6cc6eaaac1c6ede7dee913899f9fd20877499b627578824fcd46401`，`parser_version=pdf-ocr-0.4` |
| 引用原文 | `\|项目\|参数\|\n\|---\|---\|\n\|蓝牙版本\|5.3\|\n\|驱动单元\|40mm\|\n\|续航\|降噪开启约 38 小时\|\n\|充电时长\|约 1.5 小时\|\n\|重量\|252 g\|\n\|防水等级\|IPX4\|` |
| 主张 | `claim-80ac4ad77526407b`：`status=**warned**`，`confidence_level=medium`，`verification={"excerpt_matches":true,"numeric_support":true,"semantic_entailment":"not_independently_verified"}` |
| 轨迹错误摘要 | **无错误**（8 条事件 `error_code` 全为 `NULL`） |

**预期词条**（P0 验收要求"每个样本明确预期词条、引用、关系与可见范围"）：

| 预期 | 内容 |
| --- | --- |
| 词条 1 | `type=metric`，`metric_id` 需人工确认（正文里没有规范指标名，只有"续航"）→ `business_key.confirmation='pending'` |
| 词条 2 | `type=business_entity`，产品「无线降噪耳机 Pro」；业务键取自 `09_无线降噪耳机Pro产品手册` 的标题，无已批准别名 → `pending` |
| 词条 3 | `type=conclusion`，`run_id=run-894b0785725c45f3` + `claim_id=claim-80ac4ad77526407b`，正文为答案原文（含 `[1]` 标记） |
| 预期引用 | **1** 条证据引用，指纹含 `space_id + doc + document_id=doc-c43b1575ac57 + source_sha256=805851ba… + locator_kind=pdf_region + page_no=1 + bbox + block_id=blk-a7910ea1fc5c + content_hash=4934e981…` |
| 预期关系 | `conclusion —supports→` ? **注意方向**：库里是 `Claim supports Chunk`（`ev-a8a707386d9445dc → ev-12be959b7a1e46c9`），而 `supports` 是**有向**关系，Wiki 必须保留这个方向 |
| 预期关系（派生） | `Chunk —retrieved_from→ Document` × 5；`metric` 与 `business_entity` 之间 `measures`（Wiki 派生） |
| 可见范围 | 该 run 无 `conversation_id` 之外的归属信息；`/v1/wiki` 侧按请求者空间过滤。**实证的权限缺口**：`GET /v1/runs/run-894b0785725c45f3` 目前**匿名可读**（`api.py:235` 的 `PUBLIC_READ_PREFIXES` 含 `/v1/runs`），所以 **Wiki 侧的归属校验是唯一一道门** |
| **预期核验状态** | **不是 `verified`**：`claims.status='warned'`（不是 `verified`），且 `semantic_entailment='not_independently_verified'`。按 §7.3 应映射为 `unverified`，并把 `excerpt_matches` / `numeric_support` 记为已记的两项记账 |

### 11.2 第二组：SQL 查询

| 项 | 实测值 |
| --- | --- |
| `run_id` | **`run-f50186b0d3f44017`** |
| 会话 | `sess-8b202c1532b147c2bd34c0d2` |
| 问题 | `2025年销售额最高的前三个产品是什么？` |
| 状态 | `COMPLETED`，`confidence_level=medium` |
| 时间 | 2026-10-14 08:54:13 → 08:54:18（5 585.5 ms） |
| 事件 | **9** 条（比 RAG 多 `sql.validated` / `sql.executed`，少 `retrieval.completed`） |
| SQL | 4 表 JOIN + `GROUP BY` + `ORDER BY … DESC LIMIT 3`，5 个绑定参数（`sql_hash=92f8efc1ef21314feef08e63903ea953afaf5646ad00d8a24a38a102e14103d7`） |
| 结果 | `row_count=3`：`对开门冰箱 450L / 442543.37400`、`65英寸4K电视 / 315333.14700`、`平板电脑 T11 / 237135.10800` |
| 来源 | `source_id=demo_retail`，`source_snapshot_id=snap-demo_retail-9b6637f76e61` |
| 口径 | `query_frame.intent=rank_entities`，`metrics=["demo_retail.sales_amount"]`，`time_filters=[{field_id:"orders.created_at", from:"2025-01-01", to:"2026-01-01", timezone:"Asia/Shanghai"}]` |
| 证据图 | **3 个节点 / 2 条边**：`SQLQuery`（`ev-324eaec9ad734341`）、`SQLRow`（`ev-95765dbba209492d`）、`Claim`（`ev-71e313a3cc73493c`）；边 `SQLRow —derived_from→ SQLQuery`、`Claim —supports→ SQLRow` |
| 主张 | `claim-e98176232e0f4297`：`status=**verified**`，`verification={"row_count":3,"sql_hash_matches":true,"validator_findings":[],"verification_reasons":[],"semantically_verified":true,"verification_contract":"retail_metric_ast_contract"}` |
| 轨迹错误摘要 | **无错误** |

**预期词条**：

| 预期 | 内容 |
| --- | --- |
| 词条 1 | `type=metric`，业务键 `demo_retail.sales_amount`（**规范指标名，来自 `query_frame.metrics`，不是中文标题**）→ `confirmation='confirmed'`（有来源内规范名） |
| 词条 2 | `type=conclusion`，`run_id` + `claim_id=claim-e98176232e0f4297` |
| 预期引用 | **1** 条，`source_kind='sql'`，`source_ref='demo_retail'`，`source_sha256='snap-demo_retail-9b6637f76e61'`，`locator_kind='sql_aggregate'`，`locator={sql_hash, row_count:3, aggregation: 'SUM(quantity*unit_price*(1-discount)) GROUP BY product_name ORDER BY DESC LIMIT 3'}` |
| 预期关系 | `SQLRow —derived_from→ SQLQuery`（**2 056 行的旧词表，方向必须保留**）；`Claim —supports→ SQLRow` |
| 可见范围 | 同 11.1 |
| **预期核验状态** | `verified`，且 §7.2 的五项记账：`source_integrity=checked`、`rule_verification.verification_contract='retail_metric_ast_contract'` + `semantically_verified=true`、`semantic_support` **仍为 `checked:false`**；`entity_binding` 在 SQL 路径**没有数据**（该路径不产 `binding`）→ 空白必须如实标为"未记"，**不得**当作通过 |

> **这条是 P0 的关键区分样本**：11.1 的 RAG 结论是 `warned` + `not_independently_verified`，
> 11.2 的 SQL 结论是 `verified` + `retail_metric_ast_contract`。
> 两者在 Wiki 里**不能显示成同一个"已核验"**。

### 11.3 第三组：跨源核验

**❌ 未找到合适样本。** 如实记录三条查询结果：

```sql
SELECT count(*) FROM trace_events WHERE agent_name='cross_source_agent';   -- 0
SELECT count(*) FROM trace_events WHERE event_type='requirements.loaded';  -- 35（全部工业路径）
-- 终态事件 output_summary 的 13 个键中不含 cross_source_result
```

- 本库 **238** 个 run 里**没有一次**走跨源路由；`cross_source_agent` 的事件数为 **0**；
  `cross_source_result` 从未出现在任何终态负载里。
- `evidence_edges` 里 `entity_match` / `version_scope` / `unit_equivalent` / `time_overlap` / `conflicts`
  **全部 0 行**（§12.3）。
- **原因**：前端/接口没有把跨源路由暴露成默认可达路径，`agent.py:519` 的 `_run_cross_source`
  需要调用方显式选择 `sources` + `document_collections`。

**替代样本（工业核验路径，不是跨源，但含真实冲突与核验结论）**：
**`run-9ab6d6af60c64f32`** —— 见 §11.5。它走的是 `industrial_agent`，
落 `conflict_records` 表（跨源路径不写表）。**明确说明：它不是跨源核验样本，
不能用来验收 §7.4 的跨源分支。**

**给 P1/P2 的结论**：§7.4 的 `source_path='cross_source_trace'` 分支
**只能用合成 / 回放数据验证**，真实数据验证要等到有跨源运行之后。
P0 验收不得声称该分支已被真实数据覆盖。

### 11.4 来源缺失样本

**`run-28ebd0921f8f43a5`**（实测）：

| 项 | 实测值 |
| --- | --- |
| 问题 | `有没有关于火星殖民地的记录？` |
| 状态 | `ABSTAINED`，`confidence_level=insufficient`，`error_code=INSUFFICIENT_EVIDENCE` |
| 答案 | `当前可访问的知识库没有提供足够证据，请补充相关文档或确认问题范围。` |
| 事件 | **8** 条，含 `verification.failed`（`error_code=INSUFFICIENT_EVIDENCE`，`output_summary={"evidence_sufficient": false}`） |
| 检索 | `mode=hybrid`，`collections=["demo_kb","industrial_demo","ohrbench_kb"]`，`snapshot_id=kb-a1021da6bad89c60`，`eligible_chunks=169` |
| 检索到的块 | **5** 条：`chk-4c358eb26689` / `chk-c9562315bbbf` / `chk-1f2d0925222f` / `chk-8dfe0bd50acb` / `chk-3d9d8b9b03dd`（来自 `demo_kb` 与 `ohrbench_kb` 两个集合） |
| **主张数** | **0**！`evidence.attached` 的 `output_summary={"chunk_count":5,"claim_count":0}`；`rag_result.claims=[]`，`citations=[]` |
| 证据节点 | **10 个**：5 × `Chunk` + 5 × `Document`（实测 `GROUP BY node_type`）。**没有 `Claim` 节点** |
| 证据边 | **5 条**（`Chunk —retrieved_from→ Document`），每个块一条 |
| `claims` 表 | **该 run 没有 `claims` 行**（实测 `count(*)=0`） |
| 5 个块的归属 | 5 个块来自 **5 个不同文档**（`doc-158815ed856f` p1 / `doc-20698e587ac0` p2 / `doc-7f15ca835754` p1 / `doc-a18d8692c3b0` p5 / `doc-d0ce188dc04d` p37），因此 5 个 `Document` 节点是**正确**的，不是重复 |
| **注意** | 这 5 个块的 `payload.quote` **全为空字符串**：检索到了块，但**没有可用于引用的原文片段**。Wiki 呈现这类证据引用时**不得**用相邻正文或摘要替代 `excerpt` —— 空就是空，并让"检索到但无可引用原文"这个事实可见 |

**预期行为**：

| 预期 | 内容 |
| --- | --- |
| `builds` 请求 | **可以成功**（有证据节点），但产出的草稿**不得**包含任何"已核验"主张 |
| 词条 | `type=conclusion` 的候选，`status.assertion='insufficient'`；正文必须呈现"检索到 5 个块但没有形成主张"这一事实 |
| 预期错误码 | 读取轨迹时 `error_summary` 必须给出 `error_code='INSUFFICIENT_EVIDENCE'` 与 `event_type='verification.failed'`（**不是** `null`） |
| **边界** | "检索到 5 个块"与"没有可引用的证据"**必须同时可见**——只显示"无证据"会掩盖"检索到了但不够"这个真实状态 |

**第二个来源缺失样本**：`run-21a0507244264c7a`（问题 `发票怎么开`，同样 `ABSTAINED` + `INSUFFICIENT_EVIDENCE`，
`kb-a1021da6bad89c60` 快照）。两个样本用同一快照，可用来验证"同一缺失原因在不同问题下的一致呈现"。

### 11.5 版本变化样本

**❌ 没有找到「同一文档换版」的样本。** 如实记录：

```sql
SELECT count(*) FROM document_assets WHERE doc_version > 1;   -- 0
SELECT count(*) FROM pdf_snapshot_versions;                   -- 0
-- evidence_nodes 里每个 document_id 只有一个 document_version
```

**但找到了「规范版本变化」样本，且它是一条真实的冲突记录**：

| 项 | 实测值 |
| --- | --- |
| 承载运行 | `run-9ab6d6af60c64f32`（与 §11.6 同一运行） |
| `conflict_id` | **`conf-db4974a290e34c279095`** |
| `conflict_type` | `version_overlap` |
| `rule_id` | **`MULTIPLE_APPLICABLE_REVISIONS`** |
| `resolution_status` | `needs_review`（**不是 `unresolved`**） |
| `entity_key` | `{part_id: "A102", batch_id: "B004", operation_no: "30", measurement_id: "M004-1"}` |
| `metric` | `holding_temperature` |
| `normalized_values` | `{production: "850", report_raw: "850", report_unit: "degC"}` ← **两侧值相同，冲突来自版本而非数值** |
| `left_fact_refs` | `[{fact_id: "ifact-08658ba4e2b71b7fc7d65878"}]` |
| `right_fact_refs` | `[{fact_id: "ifact-9f8c47a8934c9ce4de917dbd"}]` |
| `selected_fact_ref` | **`null`** |
| `rule_version` | `industrial-rules-v1` |
| `comparison_context` | `{unit:"degC", method:"core", measured_at:"2026-09-17T10:00:00+08:00", produced_at:"2026-09-17T10:00:00+08:00"}` |

**版本账的实测事实**（`evidence_nodes` 的 `DocumentFact`）：

| `source_revision` | `fact_kind` | `valid_from` | `valid_to` |
| --- | --- | --- | --- |
| `A102-V2` | `requirement` | 2026-09-01T00:00:00+08:00 | `NULL` |
| `A102-V3` | `requirement` | 2026-09-15T00:00:00+08:00 | 2026-09-20T00:00:00+08:00 |
| `R1` | `measurement` | — | — |

两份规范的有效期**重叠于 2026-09-15 ~ 2026-09-20**，而 B004 的测量发生在 **2026-09-17** ——
正落在重叠区内。`VerificationCheck` 的 `recorded_revision` 恒为 `A102-V2`（实测 350 行全部如此），
`applicable_revision` 多数也是 `A102-V2`，仅部分行为空。

**预期行为**：

| 预期 | 内容 |
| --- | --- |
| 词条 | **两条** `type=norm` 词条：`A102-V2` 与 `A102-V3`（业务键含 `source_revision`，**不合并**） |
| 预期关系 | `A102-V3 —supersedes→ A102-V2`（Wiki 派生，**依据是更晚的 `valid_from`**），同时 `version_scope` 关系反映重叠区 |
| 预期冲突呈现 | `conflict_type='version_overlap'`，`resolution='needs_review'`；**必须显示双方 `fact_id`**，`selected=null` → 页面**不得**暗示某一侧更权威 |
| 预期状态 | `status.assertion` **不得**因为 `resolution='needs_review'` 就置为 `conflicted` —— 按 §7.4 的归一表，只有 `unresolved` 才算未裁决冲突。这个词条的核验状态由它自己的 `VerificationCheck` 决定 |
| **不确定项** | 哪个版本对 B004 生效，**需要业务确认**（`source_revision` 与 `valid_from` 的对应关系在数据里不自洽）。Wiki **只如实呈现，不做仲裁** |

### 11.6 冲突样本

**`run-9ab6d6af60c64f32`**（实测，本库冲突最丰富的运行）：

| 项 | 实测值 |
| --- | --- |
| 问题 | `2026年9月A102工序30哪些批次温度超限？生产记录和质检报告是否一致？` |
| 状态 | `COMPLETED`，`confidence_level=medium` |
| 时间 | 2026-10-11 23:43:50（总时长 178.6 ms） |
| 事件 | **9** 条，含 `requirements.loaded`（`fact_count=5`，`source_sha256=0ac6c558…`） |
| 证据图 | **48 节点 / 66 边**：`DocumentFact` 11、`Claim` 10、`SQLRow` 10、`VerificationCheck` 10、`ConflictRecord` 5、`Document` 1、`SQLQuery` 1；边 `derived_from` 56 + `supports` 10 |
| `claims` | **10** 行：`verified` **4** / `warned` **6** / `insufficient_evidence` **0** |
| `conflict_records` | **5** 行（见下） |

5 条冲突记录的完整实测：

| `conflict_id` | `batch_id` | `measurement_id` | `conflict_type` | `resolution_status` | `rule_id` | `normalized_values` |
| --- | --- | --- | --- | --- | --- | --- |
| `conf-2e18959e00c9479abd2d` | B001 | M001-1 | `value_conflict` | **`unresolved`** | `NO_REGISTERED_AUTHORITY_RULE` | `{production:"870", report_raw:"850", report_unit:"degC"}` |
| `conf-9349df43aeff4ee4911f` | B002 | M002-1 | `unit_equivalence` | `resolved_equivalent` | `DECIMAL_UNIT_CONVERSION_EQUAL` | `{production:"850.00", report_raw:"850", report_unit:"degC"}` |
| `conf-db4974a290e34c279095` | B004 | M004-1 | `version_overlap` | `needs_review` | `MULTIPLE_APPLICABLE_REVISIONS` | `{production:"850", report_raw:"850", report_unit:"degC"}` |
| `conf-d632b574fde14e569748` | B009 | M009-1 | `measurement_context` | `needs_review` | `METHOD_TIME_OR_APPROVAL_MISMATCH` | `{production:"850", report_raw:"850", report_unit:"degC"}` |
| `conf-d99b0d8e74084b14a6d7` | B010 | M010-1 | `measurement_context` | `needs_review` | `METHOD_TIME_OR_APPROVAL_MISMATCH` | `{production:"850", report_raw:"850", report_unit:"degC"}` |

`conf-2e18959e00c9479abd2d` 的 `left` / `right` 原值（实测 `payload`）：

```json
{ "metric": "holding_temperature", "reason": "NO_REGISTERED_AUTHORITY_RULE",
  "rule_id": "NO_REGISTERED_AUTHORITY_RULE", "rule_version": "industrial-rules-v1",
  "conflict_type": "value_conflict", "resolution_status": "unresolved",
  "entity_key": {"part_id":"A102","batch_id":"B001","operation_no":"30","measurement_id":"M001-1"},
  "left_fact_refs":  [{"kind":"sql_measurement","snapshot":"…"}],
  "right_fact_refs": [{"kind":"document_fact","fact_id":"…","source_revision":"A102-V2"}],
  "normalized_values": {"production":"870","report_raw":"850","report_unit":"degC"},
  "selected_fact_ref": null, "comparison_context": {"unit":"degC","method":"core"},
  "repair_attempt_refs": [] }
```

**预期行为**：

| 预期 | 内容 |
| --- | --- |
| 词条 | 一条 `type=conclusion` 的词条，`status.assertion='conflicted'`（因为有 `unresolved` 行） |
| 预期关系 | 5 条 `conflicts` 关系（`origin='projected'`，`raw_type='conflicts'`），**但**这 5 条在库里是 `evidence_nodes` 的 `ConflictRecord` 节点 + `conflict_records` 表行，**不是** `evidence_edges` 的边 —— 投影层必须从**两条来源**都取（`WIKI_DOMAIN.md` §3.5） |
| 冲突呈现 | `conf-2e18959e…` 必须同时显示 `870`（production）与 `850`（report）；`resolution='unresolved'`、`selected=null` → **不得**显示为已确认结论（需求 §6 第 137 行） |
| `needs_review` 三条 | **不得**渲染成"冲突未解决"；必须显示"有差异记录，待人工复核" |
| `resolved_equivalent` 一条 | 显示为"单位换算后等价"，**不是**矛盾 |
| 轨迹错误摘要 | 该运行**无 `error_code`**（9 条事件全为 NULL）→ `error_summary` 必须是显式的"本次运行无错误"（§5.7 约束 2） |
| **可见范围** | 该 run 的 `conversation_id` 为 **NULL**（工业运行没有会话归属）→ Wiki 侧**无法从会话推导归属**，只能按触发整理请求的用户空间归属。需求 §P0 验收"缺少可靠归属时的行为没有模糊默认值"针对的正是这种情形：**没有归属 = 只有触发者自己的空间可见，且必须标注"运行无会话归属"** |

### 11.7 三组样本的覆盖度自评

| P0 要求 | 是否覆盖 | 说明 |
| --- | --- | --- |
| 文档问答（RAG） | ✅ 真实样本 | §11.1，`run-894b0785725c45f3` |
| SQL 查询 | ✅ 真实样本 | §11.2，`run-f50186b0d3f44017` |
| 跨源核验（要有冲突或核验结论的） | ❌ **未找到** | §11.3。本库零跨源运行。**替代**：§11.5/§11.6 的工业核验运行含真实冲突与核验结论 |
| 来源缺失 | ✅ 真实样本 | §11.4，`run-28ebd0921f8f43a5`（+ `run-21a0507244264c7a`） |
| 版本变化 | ⚠️ **部分** | 无文档换版样本（`doc_version > 1` 为 0 行）；有**规范版本重叠**样本 §11.5 |
| 冲突 | ✅ 真实样本 | §11.6，`run-9ab6d6af60c64f32`（5 条冲突，4 种类型，3 种 `resolution_status`） |

---

## 12. 本文件引用的实测事实（供 P1 复核）

### 12.1 线上 `evidence_edges` 实际形状

```
        Column      |         Type          | Nullable |           Default
--------------------+-----------------------+----------+------------------------------
 edge_id            | bigint                | not null | nextval('…_edge_id_seq')
 run_id             | character varying(64) |          |            ← 可空
 src_evidence_id    | character varying(64) | not null |
 dst_evidence_id    | character varying(64) | not null |
 edge_type          | character varying(24) | not null |            ← 无 CHECK
 weight             | numeric(4,3)          |          |
Indexes:  PK (edge_id)；idx_edges_run (run_id)
FK:       src/dst → evidence_nodes(evidence_id) ON DELETE CASCADE
          ← 没有 FK 到 runs；没有 binding_type / confidence / evidence / created_at
```

与迁移 `019:81-100` 的差异：**缺 3 列**（`binding_type` / `confidence` / `evidence`）、
**多 1 列**（`weight`）、`run_id` 变为可空、**无 CHECK**、**无 FK 到 `runs`**、
索引从 3 个变 1 个。

### 12.2 线上 `evidence_nodes` 实际形状

```
 evidence_id | varchar(64) | not null |   PRIMARY KEY
 run_id      | varchar(64) |          |            ← 可空，无 FK
 node_type   | varchar(24) | not null |            ← 无 CHECK
 ref_key     | varchar(256)|          |            ← 可空
 payload     | jsonb       |          |
 created_at  | timestamptz | not null | now()
```

- 与迁移 `019:62-79` 相比：缺 `source_sha256` / `version` / `unit` / `time_scope` / `binding` 五列。
- **实测 `SELECT count(*) FILTER (WHERE payload ? 'binding')` = 0 / 2760**：
  线上**没有任何节点**带 `binding` 键。→ `WIKI_DOMAIN.md` §4.2 的绑定信息只能来自
  `claims.verification.binding`（仅跨源路径写），**本库为 0 行**。
- **`Document` 节点的两个摘要字段互斥**（实测 221 个 `Document` 节点）：
  13 个文档（186 个节点）只有 `document_sha256`，工业文档 `doc-industrial-0ac6c55838c3`
  （35 个节点）只有 `source_sha256`；**没有任何一个节点两者兼有**。
  → 证据指纹必须取 `COALESCE(payload->>'document_sha256', payload->>'source_sha256')`，
  否则工业文档的证据会全部退化成"无快照 → 运行隔离"（`WIKI_DOMAIN.md` §4.2 规则 1）。
- 节点类型分布（实测）：`Claim` 681、`SQLRow` 444、`DocumentFact` 385、`Chunk` 375、
  `VerificationCheck` 350、`Document` 221、`ConflictRecord` 175、`SQLQuery` 129。
  **`sql_row` / `doc_block` / `measurement` / `spec_clause` 全部 0 行。**

### 12.3 边、冲突与授权的实测计数

```sql
-- 边词表
derived_from 2054 | supports 682 | retrieved_from 375              -- 共 3111
-- 重复边（同一 run + 同端点 + 同类型）
SELECT count(*) FROM (SELECT run_id,src,dst,type,count(*) c FROM evidence_edges
                      GROUP BY 1,2,3,4 HAVING count(*)>1) t;        -- 0
-- 冲突
measurement_context/needs_review 70 | value_conflict/unresolved 35
unit_equivalence/resolved_equivalent 35 | version_overlap/needs_review 35   -- 共 175
-- selected_fact_ref 非空的行数：0
-- 授权
readonly_user 可 SELECT 的表：43
datamind_app 对 runs 的权限：7 项（SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER）
-- owner
postgres 18 张 / datamind_app 30 张（共 48）
-- 错误码全集（trace_events.error_code）
REPAIR_BUDGET_EXHAUSTED 40 | INSUFFICIENT_EVIDENCE 12 | SQL_POLICY_BLOCKED 6
AMBIGUOUS_QUERY 6 | MODEL_EMPTY_RESPONSE 4 | MODEL_KEY_MISSING 4
INTERNAL_ERROR 2 | INDUSTRIAL_SCOPE_REQUIRED 2
```

### 12.4 `postgres` 持有的表（迁移**不得**触碰）

```
app_settings, business_metrics, chunks, claims, data_sources, document_assets,
document_blocks, entity_aliases, evaluation_cases, evidence_edges, evidence_nodes,
ingest_jobs, runs, schema_columns, schema_relations, schema_snapshots, schema_tables,
trace_events
```

（**18 张**；其余 30 张归 `datamind_app`，其中包括 `conflict_records`。）

---

## 13. 未解决项（P0 结束时如实记录）

| # | 事项 | 影响 |
| --- | --- | --- |
| 1 | 跨源核验样本不存在（§11.3） | §7.4 的 `cross_source_trace` 分支无法用真实数据验收 |
| 2 | 文档换版样本不存在（§11.5） | `source_validity='stale'` 无法用真实数据验收；只能靠规范版本重叠样本近似 |
| 3 | 类型化证据图 4 个闭集节点词表零数据（§12.2） | 词表投影正确性只能靠合成/回放测试 |
| 4 | `source_revision` 与 `valid_from` 的语义对应不自洽（§11.5） | 需要业务确认；Wiki 只如实呈现 |
| 5 | 中文正文检索策略未定 | 第一版只覆盖标题/别名/分类，响应里必须如实声明（§5.1） |
| 6 | 异步批次 vs worker 未选 | 判据已冻结（§9.3），实现形态在 P4 决定 |
| 7 | 权限矩阵无法在回环演示下验收（§3.3） | 角色隔离验收必须用非回环来源 + 真实账号，或明确声明未验证 |
| 8 | `deploy/init/02_metadata.sql` 与 `db/migrations/019` 谁是权威 | 两者形状不一致，且**线上形状与两者都不完全相同**；P1 必须以线上实际形状为兼容基线 |
| 9 | 既有问题：`/v1/runs` 无 ACL 过滤、不过滤软删会话（分析报告 §6① ②） | **本阶段只报告，不动手**。Wiki 不得复用该接口做授权（§2.2） |
| 10 | `/#/wiki` 默认入口反转（关系图而非正文） | 按新版冻结（需求 §8.1 第 181 行）；分析报告 §3.9 记录为有意反转，用户已确认 |

---

## 14. 本文的核实方式

- 代码事实：只读检索，每条附 `文件:行号`。
- 数据事实：只读 SQL（容器 `datamind-postgres`，库 `datamind_meta`，用户 `postgres`），
  结果逐条写在 §11 与 §12。
- **未做**：没有新增路由、没有新建表、没有执行迁移、没有修改现有文件、没有提交 git。
- **未核实**：100 节点 / 300 边 / 300 迭代 / 5 秒在目标硬件上的真实表现（需 P4 样本）；
  中文检索召回；跨源路径与文档换版路径的真实数据行为。
