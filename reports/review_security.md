# 安全审查报告：凭据、权限与暴露面

- **审查对象**：`/Users/caicai/DataMind Agent`（HEAD `005b9cc50abed31e4f69859f27161a1696881a39`，短哈希 `005b9cc`）
- **审查时间**：2026-10-08
- **审查性质**：**只读审查**。除本文件外未修改/新建/删除任何文件；未执行 `git` 写操作、格式化、`docker compose`、`scripts/reset_demo.sh`、`scripts/verify.py`、`ingestion/ingest.py`、`scripts/test_isolated.py`。
- **Python**：`/Users/caicai/DataMind Agent/.venv/bin/python`
- **运行环境**：API `uvicorn datamind.api:app --host 127.0.0.1 --port 8000`（PID 7487）；容器 `datamind-postgres`、`datamind-qdrant`。
- **脱敏约定**：本报告不出现任何真实凭据明文。密钥一律写作 `sk-421…e5db`（首 6 位 + 末 4 位）或只写「存在/不存在」。开发用固定口令属已知口令，按仓库既有文档处理，仅列位置。

> **本次审查的副作用披露（诚实声明）**：为验证「未挂鉴权的变更型路由」与「凭据转发原语」，实际调用过 `POST /v1/diagnostics/sql`（固定只读自检查询）2 次、`POST /v1/model/config/test` 若干次（其中 1 次把请求发往本机临时监听端口，见 F01）。前者在 `datamind_meta.runs` 新增 2 行运行记录：`run-57257a7a0f214cd4`（16:49:07）、`run-3436fe54975b4a15`（16:49:17），均为 `COMPLETED`。临时监听进程已终止，未落任何文件。除此之外未改动任何状态。

---

## 1. 范围与方法

审查六个方面：①受版本控制的凭据（含 git 历史）；②运行中的暴露面（容器端口/挂载/口令/Qdrant 认证）；③HTTP 认证与授权（`api.py` 中间件 + `auth.py`）；④凭据处理链（`settings_store.py` / `providers.py` / `api.py` 读取与导出侧）；⑤数据面权限（`deploy/init/01_roles.sql` 与代码假设）；⑥模型调用安全（TLS 校验、超时、重定向、代理）。

实际使用的命令与结果（全部脱敏）：

| # | 命令（节选） | 结果 |
|---|---|---|
| M1 | `git ls-files -z \| xargs -0 grep -nEI '(sk-[A-Za-z0-9_-]{16,}\|AKIA…\|-----BEGIN …PRIVATE KEY-----…)'` | 仅命中 `tests/` 下合成夹具：`sk-ant…4321`、`sk-abc…wxyz`、`sk-liv…6789` 等，无真实外部服务凭据 |
| M2 | 逐 revision 扫描全部 46 个提交：`git log --all --format=%H \| while read h; do git grep -lIE '<secret patterns>' "$h"; done` | 历史中出现过密钥形状字符串的文件只有 2 个：`tests/test_model_config_api.py`（合成夹具 43 个 revision）、`gateway/source/relay/channel/vertex/service_account.go`（PEM **头部常量**，非私钥，43 个 revision）。**无 DeepSeek 形状密钥（`sk-[0-9a-f]{32}`）曾进入历史** |
| M3 | `git log --all --diff-filter=A --name-only -- '*env*' '*.key' '*.pem'` | 仅 `deploy/.env.example`、`gateway/.env.example`；从未提交过真实 `.env` / `.key` / `.pem` |
| M4 | `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'` | `datamind-postgres` → `127.0.0.1:5432->5432/tcp`；`datamind-qdrant` → `127.0.0.1:6333-6334->6333-6334/tcp`（均仅回环） |
| M5 | `docker inspect <c> --format '{{json .HostConfig.PortBindings}}'` / `{{json .Mounts}}` | Postgres：`{"5432/tcp":[{"HostIp":"127.0.0.1","HostPort":"5432"}]}`；Qdrant：6333/6334 同为 `127.0.0.1`。挂载仅 `deploy/init → /docker-entrypoint-initdb.d (ro)` 与两个命名卷；**无 `docker.sock`、无 `$HOME`、无宿主敏感目录** |
| M6 | `docker inspect datamind-qdrant --format '{{json .Config.Env}}'` | `["QDRANT__SERVICE__HTTP_PORT=6333", …]` → **无 API key** |
| M7 | `curl --noproxy '*' http://127.0.0.1:6333/collections` → `200`；`POST …/collections/datamind_knowledge/points/scroll` | 匿名可读：`points_count=184`，payload 键 `acl/chunk_id/collection/document_id/…` |
| M8 | `lsof -nP -iTCP -sTCP:LISTEN \| grep -E ':(8000\|5432\|6333\|6334)'` | 仅 `127.0.0.1:8000`（Python）、`127.0.0.1:5432/6333/6334`（docker） |
| M9 | `.venv/bin/python`（httpx）读 `GET /v1/model/profiles/export?include_keys=true` | 回环 → `200`，`api_key` 非空（`sk-421…e5db`）；带 `X-Forwarded-For: 203.0.113.9` → `401` |
| M10 | `.venv/bin/python`（httpx + 本机临时 sink）`POST /v1/model/config/test {"base_url":"http://127.0.0.1:8899"}` | sink 收到 `POST /v1/chat/completions`，`Authorization: Bearer sk-421…e5db`（见 F01） |
| M11 | `curl -X POST /v1/drawings -H 'Host: evil.example' -H 'Origin: http://evil.example' -H 'x-csrf-token: <demo>'` vs `Origin: http://other.example` | 前者 `422`（同源校验通过），后者 `403`（见 F03） |
| M12 | `curl -X POST /v1/diagnostics/sql -H 'Origin: http://evil.example' -H 'Sec-Fetch-Site: cross-site'` | `200`（执行并落库）；`POST /v1/authority/rules` 同头 → `403`（见 F04） |
| M13 | `curl -X POST /v1/chat -d '{}'`（含伪造 Origin/XFF） | `422`（仅 body 校验失败，说明中间件未介入） |
| M14 | `docker exec … psql -U postgres -tAc "SELECT grantee, privilege_type …"` / `has_table_privilege(…)` | `readonly_user` 无任何非 SELECT 授权；`app_settings/auth_users/auth_sessions/auth_login_limits` 对其均为 `false` |
| M15 | `docker exec … psql -d datamind_meta -tAc "SELECT … length(api_key), 'sk-****'\|\|right(api_key,4), api_key ~ '^sk-[0-9a-f]{32}$' …"` | 1 个启用配置，`base_url=https://api.deepseek.com/v1`，`model=deepseek-flash`，密钥长度 35，形状匹配 → 掩码 `sk-421…e5db`（真实外部凭据**存在**） |
| M16 | `.venv/bin/python`：`providers.chat()` 打向 307 重定向 sink（A:8899 → B:8898） | A 收到 `Authorization`，**B 未收到**（httpx 0.28.1 跨源重定向自动剥离凭据） |
| M17 | `.venv/bin/python`（httpx）：`GET https://api.deepseek.com/v1/models` | TLS 握手成功，`HTTP 401`（无 key 的预期结果）→ 应用真实客户端证书校验正常 |
| M18 | `.venv/bin/python` 扫描 `.cache/api/server.log` 等运行日志 | 密钥形状匹配数 `0` |
| M19 | `.venv/bin/python` 扫描未跟踪本地文件 | 仅 `tests/__pycache__/*.pyc` 内嵌合成夹具字符串；`deploy/.env`、`.env`、`work/` 均不存在 |

---

## 2. 问题表

| 编号 | 严重度 | 标题 | 证据（文件:行 + 复现，脱敏） | 影响 | 建议修法与验收标准 | 置信度 |
|---|---|---|---|---|---|---|
| **F01** | **P1** | 存储的真实模型密钥可被转发到调用方指定的任意地址（凭据外泄原语） | `datamind/api.py:862-877`（`probe_models`/`probe_chat` → `resolve_profile(request)`）、`datamind/api.py:711-738`、`datamind/settings_store.py:413-481`（请求字段优先、`api_key` 回落到已存配置）、`datamind/providers.py:54-69`；复现 M10：body 只给 `base_url`，sink 收到 `Authorization: Bearer sk-421…e5db` | 真实 DeepSeek 密钥被送往攻击者主机（可计费、可滥用），且不触发任何告警 | ① 探活/列表接口增加目标白名单或「密钥只允许发往已存 `base_url` 的同源地址」；② 显式传 `base_url` 且与已存不同源时，必须要求请求同时提供 `api_key`，禁止隐式继承；③ 对每次出站目标变更写审计事件。**验收**：`POST /v1/model/config/test` 带新 `base_url` 且不带 `api_key` → `400 CREDENTIAL_TARGET_MISMATCH`，且 sink 收不到 `Authorization`；同源探活仍 `200` | 已确认 |
| **F02** | **P2** | 「本机免登录 admin」依赖 `request.client.host`，经 `X-Forwarded-For` 可被伪造 | `datamind/auth.py:26,29-33`（`_LOOPBACK` / `demo_allowed`）；uvicorn 0.54.0 默认 `proxy_headers=True`、`forwarded_allow_ips=127.0.0.1`；复现：`GET /v1/model/profiles` 带 `X-Forwarded-For: 203.0.113.9` → `401`，不带 → `200`；`POST /v1/diagnostics/sql` 带同一头 → `200` | 只要本机前置了反向代理（TLS 终结极常见），远端客户端只要能让代理透传 `X-Forwarded-For: 127.0.0.1` 即可获得管理员身份，进而触发 F01/F05 | ① 启动参数显式 `--no-proxy-headers`（或 `--forwarded-allow-ips=""`）；② 身份判定改为「服务自身配置的显式开关」而非客户端地址：默认要求登录，`DATAMIND_LOCAL_NO_LOGIN=1` 才开启免登录；③ 若保留自动 admin，用**连接套接字地址**（`request.scope["client"]` 在代理处理前的值）并忽略转发头。**验收**：带 `X-Forwarded-For` 的请求不再改变身份（`/v1/auth/me` 的 `user` 与不带该头一致）；默认配置下非显式开关时 `required=True` 路由一律要求会话 | 已确认（代码事实与头信任行为）；今日可利用性受「无前置代理」限制 |
| **F03** | **P2** | 同源校验以 `Host` 头为基准，无法防 DNS rebinding；读取型接口完全没有同源校验 | `datamind/auth.py:92-97`（`expected=str(request.base_url)`）；复现 M11：`Host: evil.example` + `Origin: http://evil.example` → `422`（通过）vs `Origin: http://other.example` → `403`；全仓库无 `TrustedHostMiddleware`/Host 白名单；`datamind/api.py:598-604`（`GET /v1/auth/me` 无条件下发 `csrf_token`）、`datamind/api.py:752-770`（`GET …/export?include_keys=true`） | 恶意网页用 DNS rebinding 把 `evil.example` 解析到 `127.0.0.1` 后：同源成立 → 命中回环免登录 admin → 读走 `csrf_token` 与**真实密钥**，或用 F01 把密钥外送；读取接口（含 `/v1/documents/*/file`、`/v1/runs/*`、`/v1/auth/me`）无任何来源控制 | ① 增加 Host 白名单（`allow_hosts={127.0.0.1:8000, localhost:8000}`）；② `origin_check` 改为与**配置的固定允许来源**比较，不用 `Host` 推导；③ 所有返回凭据或令牌的接口至少校验 `Origin`/`Sec-Fetch-Site`，`/v1/auth/me` 的 `csrf_token` 改为仅在已持有会话时下发。**验收**：`Host: evil.example` 的任意请求 → `400 INVALID_HOST`；`Origin: http://evil.example` 的 `GET /v1/auth/me` → `403`；导出接口在跨源条件下不返回密钥 | 已确认（代码）；端到端浏览器可利用性为*疑似*（取决于浏览器对本地网络的拦截策略） |
| **F04** | **P2** | 未挂鉴权/CSRF/同源检查的变更型路由：`POST /v1/chat`、`POST /v1/chat/stream`、`POST /v1/diagnostics/sql` | 中间件映射 `datamind/api.py:169-199`（仅覆盖 `/v1/authority/`、`/v1/drawings*`、`/v1/dimensions/`、`/v1/files/`、`/v1/pdf-snapshots*`、`/v1/pdf-knowledge*`、`/v1/plans*` 及 `*/reviews`、`*/approvals`）；逐路由比对（25 条路由无身份校验）见 §3；复现 M12/M13：`/v1/diagnostics/sql` 带伪造 Origin → `200` 并写库，`/v1/chat` → `422`（仅 body 校验），对照 `/v1/authority/rules` → `403` | 即使设置 `DATAMIND_LOCAL_NO_LOGIN=0`，这三条 POST 仍**永不调用** `current()`：可匿名触发计费模型调用、匿名执行只读 SQL 自检、匿名写入 `runs`/`trace_events`；任何能到达端口的进程/页面都可消耗配额 | ① 在中间件里为 `/v1/chat`、`/v1/chat/stream`、`/v1/diagnostics/sql` 指定权限（建议 `admin` 或 `reviewer`）；② 把「需要鉴权的路径集合」改为默认拒绝的白名单式判定（除 `/health`、`/`、`/v1/auth/login` 外全部要求身份），避免新增路由漏挂。**验收**：新增未分类 POST 路由默认返回 `403/401`；对 `/v1/chat` 带伪造 Origin 且无会话 → `403 ORIGIN_NOT_ALLOWED`，带会话但无 CSRF → `403 CSRF_REQUIRED` | 已确认 |
| **F05** | **P3** | `include_keys=True` 导出缺少审计记录与 CSRF/同源约束 | `datamind/api.py:752-770`；GET ⇒ 中间件 `mutation=False` ⇒ 无 `origin_check`/CSRF；`grep -rn audit datamind/api.py datamind/settings_store.py` 无命中；M9 证明其鉴权本身有效（非回环 `401`、回环 `200` 且含密钥） | 密钥导出这一高危动作**无任何可审计痕迹**（谁、何时、导了哪些 profile 均无记录）；GET 语义使其可被跨站发起（读取受同源策略保护，但结合 F03 的 rebinding 即可读） | ① 改为 `POST`（自然纳入 `mutation=True`，获得 CSRF+同源校验）；② 返回前写入审计事件（含 `user_id`、时间、profile id 列表、`include_keys=true`）；③ 可选要求二次确认参数。**验收**：`GET …/export?include_keys=true` → `405`；审计表出现对应记录且不含密钥值 | 已确认 |
| **F06** | **P3** | 唯一真实外部凭据以明文存于 `app_settings`（无静态加密、无轮换元数据） | M15：`datamind_meta.app_settings.setting_key='model.profiles'`，1 个 profile，`api_key` 长度 35、形状 `sk-<32 hex>`，掩码 `sk-421…e5db`；`datamind/settings_store.py:1-6,31-33`（设计文档明说「密钥只存在于元数据库」） | 数据库卷/转储/备份泄露即等于密钥泄露；无加密、无到期/轮换字段；`readonly_user` 已被正确拒绝（`has_table_privilege=false`），但任何能连上 Postgres 的本地进程可读 | ① 静态加密（KMS/`pgcrypto`）或改由环境变量/外部 secret 注入；② 增加 `key_updated_at`/轮换审计；③ 文档化「数据库卷按机密介质保护」。**验收**：`select value::text from app_settings` 不再直接包含 `sk-` 前缀明文；轮换后旧值不可解 | 已确认 |
| **F07** | **P3** | 远端上游默认沿用系统代理，`http://` 上游的 `Authorization` 会经过代理 | `datamind/providers.py:177`（非本地且未显式配置代理时 `httpx.Client(timeout=limits, follow_redirects=True)`，`trust_env` 默认 `True`）；本地地址与显式代理路径均为 `trust_env=False`（`:173`、`:176`） | 若系统/环境代理不可信，或 profile 配置了第三方代理，凭据会经过该代理；HTTPS 经 CONNECT 隧道不可见，明文 `http://` 上游则直接暴露头 | ① 远端上游也改为 `trust_env=False`，需要代理时**仅**允许 profile 显式配置；② 非 HTTPS 上游一律拒绝（或要求显式 `allow_insecure_upstream`）。**验收**：设置 `HTTPS_PROXY=http://127.0.0.1:1` 后对远端上游的调用仍直连成功（或明确报 `PROXY_NOT_ALLOWED`） | 已确认（行为）；影响取决于代理可信度 |
| **F08** | **P3** | 读取面暴露与角色使用不一致：敏感读接口无鉴权；只读路由用读写账号 | 无鉴权读取（见 §3）：`GET /v1/documents*`（含 `/file` 直接返回源文件）、`GET /v1/runs*`、`GET /v1/auth/me`、`GET /docs`、`/openapi.json`、`/redoc`；`datamind/api.py` 中 `GET /v1/pdf-snapshots*`（226/235/243/…）、`GET /v1/pdf-knowledge*`（302/310/327/337/345/353）、`GET /v1/files/jobs*`（577/583）用 `_write_dsn()`（`datamind_app`）；`GET /v1/auth/me`（`datamind/auth.py:36-44`）在 GET 路径上执行 `INSERT INTO auth_users … ON CONFLICT DO NOTHING` | 本机任意进程可枚举全部文档与运行历史/证据；纯读接口持有不必要的写权限，扩大误用面；GET 触发写库违反幂等语义；`/docs` 公开完整接口面 | ① 给 `/v1/documents*`、`/v1/runs*` 加最低 `viewer` 门禁，关闭或鉴权 `/docs`、`/openapi.json`；② 纯读路径统一改用 `_read_dsn()`；③ `ensure_demo_user` 改为启动期一次性 provision，不在请求路径写库。**验收**：无会话访问 `/v1/runs` → `401`；`GET /v1/pdf-snapshots` 的连接用户为 `readonly_user`；`GET /v1/auth/me` 不产生写事务 | 已确认 |

---

## 3. 路由表 × 中间件逐条对照（问题 3 的交付物）

用应用自身的路由表 + 逐字复制中间件判定逻辑生成（`datamind/api.py:169-199`）：

**A. 完全未进入身份/CSRF 中间件的路由（`current()` 从不调用）— 25 条**

变更型（3 条，**F04**）：`POST /v1/chat`、`POST /v1/chat/stream`、`POST /v1/diagnostics/sql`
预鉴权/自校验（2 条，正常）：`POST /v1/auth/login`（登录前无身份，内含限流与同源校验）、`POST /v1/auth/logout`（`datamind/auth.py:171-172` 内部以 `mutation=True` 调用 `current()`，同源+CSRF 实际生效 ✔）
读取型（20 条）：`GET /`、`GET /health`、`GET /docs`、`GET /redoc`、`GET /openapi.json`、`GET /docs/oauth2-redirect`、`GET /v1/auth/me`、`GET /v1/documents`、`GET /v1/documents/{id}`、`GET /v1/documents/{id}/file`、`GET /v1/documents/{id}/contract`、`GET /v1/documents/{id}/facts`、`GET /v1/documents/{id}/facts/{fid}/reviews`、`GET /v1/documents/{id}/facts/{fid}/approvals`、`GET /v1/documents/{id}/pages/{n}`、`GET /v1/runs`、`GET /v1/runs/{id}`、`GET /v1/runs/{id}/events`、`GET /v1/runs/{id}/evidence`、`GET /v1/runs/{id}/conflicts`

**B. 变更型路由已全部被中间件覆盖（权限要求）— 30 条**：`admin` 24 条（`/v1/pdf-snapshots`、`/v1/pdf-knowledge/*` 的 7 条 POST、`/v1/plans/execute`、`/v1/drawings`、`/v1/dimensions/measurements|bindings|comparisons`、`/v1/files/*` 4 条、`/v1/model/*` 10 条）、`reviewer` 2 条（`/v1/drawings/{id}/features/{id}/reviews`、`/v1/documents/{id}/facts/{id}/reviews`）、`approver` 4 条（`/v1/dimensions/bindings/{id}/approvals`、`/v1/authority/rules/{id}/events`、`/v1/documents/{id}/facts/{id}/approvals`，以及 `/v1/authority` 事件分支）。

**C. CSRF/同源检查覆盖**：仅 `mutation=True`（非 GET/HEAD/OPTIONS）且进入中间件的请求执行 `origin_check` + `X-CSRF-Token` 比对。**结论**：变更型路由的 CSRF 覆盖在 B 组内完整，缺口仅在 A 组的 3 条（F04）；读取型路由完全没有同源校验（F03）。

---

## 4. 已确认**没有**问题的高风险项（专项验证）

1. **无 TLS 校验削弱**：全仓库 `grep -n "verify\s*=\|CERT_NONE\|check_hostname\|ssl._create"` 在 `datamind/` 下**零命中**；`httpx 0.28.1` 默认 `verify=True`。用应用真实客户端实测（M17）：`https://api.deepseek.com/v1/models` TLS 握手成功并返回 `401`。**历史上报的「自签证书/TLS 拦截」无法用 httpx 复现**，故不认定漏洞（与任务给定的假警报提示一致）。
2. **重定向不泄露凭据**：`providers.py:173-177` 开启 `follow_redirects=True`，但实测（M16）307 跨源重定向时 **Authorization 被 httpx 剥离**（A 收到、B 未收到）。已确认无跨源重定向凭据泄露。
3. **NL2SQL 只读执行链无越权**：`datamind/sql_engine.py:226-235` 强制连接用户等于 `DATAMIND_QUERY_USER`（默认 `readonly_user`），且显式拒绝 `postgres`/`datamind_app`，并 `conn.set_session(readonly=True)`；`sql_guard.py:124-213` 仅允许单条 `SELECT/WITH`、禁用 `INSERT/UPDATE/DELETE/CREATE/…` 与 `pg_read_file/set_config/dblink` 等函数、拒绝 `SELECT *`、`restricted` 列、笛卡尔/未批准 JOIN，并把比较字面量参数化绑定。生成 SQL 从不使用 `_write_dsn()`。
4. **数据库角色最小权限成立**：`readonly_user` 在三个库中**没有任何非 SELECT 授权**（M14：`SELECT grantee,privilege_type … WHERE privilege_type<>'SELECT'` 返回空）；`has_table_privilege('readonly_user', …)` 对 `app_settings`/`auth_users`/`auth_sessions`/`auth_login_limits` 全部为 `false`；`readonly_user` 非 superuser/createdb/createrole，且无 public schema `CREATE`。`datamind_app` 无 superuser 属性。`deploy/init/01_roles.sql` 的「应用读写 / 问数只读」划分与代码假设（`_write_dsn`→`datamind_app`、`_read_dsn`→`readonly_user`）**一致**，迁移脚本对敏感表逐个 `REVOKE ALL … FROM PUBLIC,readonly_user`（`db/migrations/005,011,012,013`）。
5. **密钥不进入日志/事件流/质量明细/响应体（脱敏侧）**：`grep` 显示 `datamind/` 无 logger/print/traceback 输出配置；`.cache/api/server.log` 密钥形状匹配 `0`（M18）；`trace_events` 只记录 `model`/`protocol`/`profile` 名（`datamind/agent.py:87-104,294-296`）；`quality_detail` 只承载解析质量（`ingestion/ingest.py:488-491`、`datamind/rag_api` 读取侧）；`public_profile`（`settings_store.py:609-634`）对 `api_key` 只回掩码、敏感头**只回键名**、`proxy` 去 userinfo，`redact_profile`（`:218-240`）另有 `_scrub_secrets` 兜底；SSE（`datamind/api.py:931`）只推送上述事件对象。Web 前端不落盘密钥（`web/app.js:1219,1350,1405`，仅 `localStorage` 存视图名与最近 run id）。
6. **容器暴露面已收敛**：两个容器均只绑定 `127.0.0.1`（M4/M5/M8），仅挂载只读的 `deploy/init` 与命名卷，无 `docker.sock`、无宿主家目录、无默认空口令账户（Postgres 口令来自 `POSTGRES_PASSWORD:-datamind_dev`，Qdrant 无账户体系）。
7. **会话与凭据存储实现良好**：口令 `scrypt(n=16384,r=8,p=1)`+16 字节盐（`auth.py:55-70`）；会话令牌与登录失败计数**仅存 SHA-256 摘要**（`auth.py:110-124`）；登录限流按「客户端+账号」双桶、15 分钟窗口、10 次上限、`FOR UPDATE` 串行化（`auth.py:100-124`）；Cookie `httponly` + `samesite='strict'` + `secure` 随 scheme（`api.py:611-612`）；CSRF 比对用 `hmac.compare_digest`。

---

## 5. 已知且有文档化边界的风险（不计为本次问题）

1. **本地开发固定口令**：`datamind_dev`（`deploy/compose.yaml:10`）、`datamind_app_dev` / `readonly_dev`（`deploy/init/01_roles.sql:12,21`），并硬编码于 `datamind/agent.py:36-43`、`datamind/settings_store.py:31-33`、`db/*.py`、`scripts/verify.py:21-22`、`ingestion/generate_demo_docs.py`。**为何接受**：仓库明确文档化（`deploy/compose.yaml:57-64`、`README.md:389`），且真正的边界是「仅回环绑定」（实测 M4/M8）；从宿主经 Docker 端口转发连接时仍走 `scram-sha-256`（实测：错误口令被拒绝），故这些口令只在容器/本机范围内有意义。**残留边界**：口令在代码与文档中明文，任何未来放宽端口绑定的改动都会立即把它变成 P1，建议改为环境变量注入并在 `deploy/.env`（已 gitignore）中承载。
2. **Qdrant 无认证**（`deploy/compose.yaml:56-58` 已记录；`compose.pdf-private.yaml` 的私有实例则要求 `DATAMIND_PDF_QDRANT_API_KEY`）。实测 M7：匿名可读集合信息与向量 payload。**为何接受**：6333/6334 仅绑定 `127.0.0.1`，当前无跨机路径。
3. **本机免登录 demo admin**（`README.md:328`、`datamind/auth.py:20-33`）。**为何接受（有条件）**：是产品明确的本机演示形态，且服务只绑定回环。**但它是 F01/F03/F05 的放大器**：一旦前置代理、DNS rebinding 或端口绑定放宽，免登录 admin 会直接让真实密钥可被外送，故 F02/F03 的修法应视为配套必要条件。
4. **`docker exec … psql -h 127.0.0.1` 免密（`pg_hba.conf` 含 `host all all 127.0.0.1/32 trust`）**：为 `postgres:16-alpine` 镜像默认。实测：宿主经端口转发连接时 `127.0.0.1/32` 规则不适用，错误口令被 `scram-sha-256` 拒绝，故当前不可从宿主利用；建议后续显式改为 `scram-sha-256` 并在 `POSTGRES_INITDB_ARGS`/自定义 `pg_hba` 中固化，避免容器内其它进程免密成为超级用户。
5. **`data/knowledge_base/field-test-*.pdf`、`reports/project_audit_20261008_*`、`reports/review_docs_truthfulness.md`**：按任务约定属另一 agent 产物，**本次未纳入**审查结论（仅确认其未含真实凭据形状字符串）。

---

## 6. 结论与优先修复顺序

- **必须先修（阻断凭据外泄链）**：F02（去掉对转发头的隐式信任 / 默认要求登录）→ F03（Host 白名单 + 固定允许来源 + 读接口来源校验）→ F01（禁止把已存密钥发往请求指定的新目标）。三者叠加构成「恶意网页 → 回环 admin → 密钥外送」的可利用链，其终点是一枚**真实、可计费的 DeepSeek 密钥**。
- **紧随其后**：F04（补齐 3 条未挂鉴权的变更型路由，并改为默认拒绝的路径判定）。
- **加固项**：F05（导出审计与 POST 化）、F06（静态加密）、F07（远端上游直连）、F08（读取面门禁与读写账号分离）。
- **未发现**：受版本控制的真实外部服务凭据；历史上从未有真实密钥被提交；无 TLS 校验削弱；无跨源重定向凭据泄露；`readonly_user` 无越权写能力。

---

## 7. 声明

本次为**只读**安全审查：除本报告 `reports/review_security.md` 外，**未修改、新建或删除任何文件**，未执行业务修复，未运行禁止清单中的脚本（`scripts/reset_demo.sh`、`scripts/verify.py`、`ingestion/ingest.py`、`scripts/test_isolated.py`）与任何 `docker compose` 子命令，未执行 `git` 写操作。**报告全文未打印任何真实密钥/口令明文**，凭据一律以掩码（如 `sk-421…e5db`）或「存在/不存在」表述。审查期间唯一的状态副作用是 2 条 `POST /v1/diagnostics/sql` 自检运行记录（`run-57257a7a0f214cd4`、`run-3436fe54975b4a15`），已在文首披露；未改动任何业务代码或配置。
