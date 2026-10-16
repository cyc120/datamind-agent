# 便携交付包（免 Docker / 免 Python / 免装数据库）

日期：2026-10-16。本文说明**怎么打包**、包里各部分的**来源与边界**，以及打包时被
验证过的事实。使用者视角的一页纸说明在包内 `README.txt` / `启动说明.txt`。

---

## 1. 一句话

`scripts/build_delivery.ps1` 把「便携运行时 + 项目源码 + 已建好并摄取完的数据库」
组装成一个目录，并打成 zip。审核老师解压后**双击 `run.bat`** 就能用浏览器打开
`http://127.0.0.1:8000/`——不需要 Docker、不需要装 Python、不需要装数据库。

打包**不是一次性压缩**：脚本可反复执行，源码与前端产物每次都重新同步，
所以前端改完之后再跑一次，包里就是新的前端。

---

## 2. 怎么重新打包（用户会反复用的命令）

```powershell
cd D:\ICT中国电子杯\datamind-agent

# 前端改了 → 一条命令重新构建前端并重新打包（推荐）
pwsh -File scripts/build_delivery.ps1 -Frontend build

# 前端已经自己跑过 npm run deploy，只想重新同步源码/产物
pwsh -File scripts/build_delivery.ps1

# 代码改了但不想重装运行时/不动数据库（最快，通常 1~2 分钟）
pwsh -File scripts/build_delivery.ps1

# 连数据库/向量库一起重建（改了迁移、种子、知识库文档时）
pwsh -File scripts/build_delivery.ps1 -RebuildData

# 运行时也要重装（换了 requirements.lock 或要强制重下依赖）
pwsh -File scripts/build_delivery.ps1 -RebuildRuntime

# 只出目录不出 zip（调试用）
pwsh -File scripts/build_delivery.ps1 -NoZip

# 只想先把 4 个运行时压缩包下到 delivery_cache/（可单独跑，重复跑不重下）
pwsh -File scripts/fetch_runtime.ps1
```

常用参数：`-OutDir`（默认 `<仓库上级>\delivery`）、`-CacheDir`（默认 `<仓库上级>\delivery_cache`）、
`-Proxy`（默认 `http://127.0.0.1:7890`）、`-Direct`（直连）、`-EmbedModelKeyFile`（把密钥写进包，
**默认不做**，见第 8 节）。

产物：

```
delivery\DataMindAgent_Demo\            解压即用的目录
delivery\DataMindAgent_Demo.zip         分发给老师的压缩包
delivery\_build\                        打包中间件（pgdata 临时目录、stamp、日志），可随时删
delivery_cache\                         运行时压缩包与 pip 缓存，重复打包复用
```

`.gitignore` 已追加 `delivery/`、`delivery_cache/`（这两个目录默认就在仓库之外，
追加是为了防止有人用 `-OutDir` 指回仓库内时误提交）。

---

## 3. 包结构（各部分的来源）

```
DataMindAgent_Demo\
  run.bat                 老师双击的入口（**纯 ASCII** + chcp 65001，纯 cmd；见第 12 节）
  stop.bat                停止脚本（含中文提示，无 for /f，实测安全）
  note_running.txt        运行中的中文提示（run.bat 用 type 显示；UTF-8 无 BOM）
  note_broken.txt         包不完整时的中文排查提示（同上）
  README.txt              一页纸说明
  启动说明.txt            带常见问题的详细说明
  BUILD_INFO.txt          本次构建信息（时间/提交/各部分大小）
  tools\dmstack.ps1       服务编排：检查端口 / 启停 / 等待就绪 / 状态（run.bat、stop.bat 调用）
  runtime\
    pgsql\                PostgreSQL 16.4 免安装二进制（bin+lib+share，已裁掉 pgAdmin/symbols/doc）
                          并随包附带 vcruntime140.dll / vcruntime140_1.dll / msvcp140.dll
    qdrant\qdrant.exe     Qdrant 1.12.4 单文件
    python\               CPython 3.14.5 嵌入式发行版（与仓库 .venv 同一个小版本）+ pip
    pylibs\               pip install --target 装的全部依赖（可重定位，靠 ._pth 找到）
  app\                    项目源码（datamind/ db/ ingestion/ deploy/ scripts/ web/ docs/ data/
                          tests/ frontend/ 源码 + README/requirements*.txt/PROGRESS.md）
    .cache\fastembed\     向量模型缓存（knowledge.py:103 把路径硬编码成 ROOT/.cache/fastembed）
    .cache\ocr_models\    表头 OCR 模型（tables.py:13 同样硬编码）
  data\
    pgdata\               预置数据目录（构建时 initdb + 迁移 + 种子 + 摄取，端口写死 55432）
    qdrant_storage\       预置向量库（集合 datamind_knowledge）
    logs\                 运行日志：postgres.log / qdrant.*.log / api_console.log / api_console.err.log / *worker.log
    .cache\               应用侧临时目录
    model_config.env.example  模型密钥文件示例（默认**没有**真 key）
```

> 与最初设想的差异只有一处：应用自己的模型缓存（fastembed / ocr_models）放在
> `app\.cache\` 而不是 `data\.cache\`，因为对应路径在冻结代码里是
> `ROOT/.cache/...`（`datamind/knowledge.py:103`、`ingestion/tables.py:13`），
> 没有环境变量可覆盖；`data\.cache\` 仍保留，用于应用侧临时文件。

---

## 4. 为什么数据库在构建时就初始化好

老师首次运行时如果还要 `initdb + 迁移 + 种子 + 文档摄取`，至少要几分钟，而且最容易
失败（OCR、向量模型、编码、权限都可能出问题）。因此 `build_delivery.ps1` 在打包阶段就
把 `data/pgdata` 与 `data/qdrant_storage` 建好，老师那边只做「起服务」：

* 启动阶段：`pg_ctl start` → `qdrant.exe` → `uvicorn` → 等 `/health` → 开浏览器；
* 实测冷启动耗时见 `docs/DELIVERY.md` 第 10 节（构建日志里也有）。

构建期的数据初始化**用的是仓库自己的脚本**（`db/migrate.py`、`db/seed_demo_retail.py`、
`db/build_catalog.py`、`db/seed_eval_cases.py`、`db/seed_industrial.py`、
`db/build_industrial_catalog.py`、`ingestion/ingest.py`），并且用的是**包内那套嵌入式
Python + pylibs**——等于顺带验证了「包里这套运行时真的能跑这个仓库的代码」。

---

## 5. 端口与配置：一律用环境变量，不改冻结代码

| 组件 | 端口 | 为什么 |
|---|---|---|
| Web/API | 8000 | 应用固定端口，前端 origin 白名单里写死了它（`datamind/auth.py:32`） |
| PostgreSQL | 55432 | 避开老师机器上可能已存在的 5432 |
| Qdrant HTTP/gRPC | 56333 / 56334 | 避开 6333/6334 |

应用侧代码把 DSN 默认值硬编码在多个模块里（`datamind/settings_store.py:33`、
`sql_engine.py:357,830`、`agent.py:47-54`、`wiki.py:164-176`、`wiki_projection.py:79-81`、
`knowledge.py:52`、`binding.py:418`、`cross_source.py:1117`）。这些模块**一个字都没改**，
`run.bat`（经 `tools\dmstack.ps1` 的 `Set-AppEnvironment`）设置的覆盖变量就是它们
`os.environ` 真正读取的那些键：

```
DATAMIND_META_WRITE_DSN / DATAMIND_WRITE_DSN    应用角色（读写）
DATAMIND_META_DSN                               只读角色（问数、文档读取）
DATAMIND_WIKI_WRITE_DSN / DATAMIND_WIKI_READ_DSN
DATAMIND_DB_HOST / DATAMIND_DB_PORT / DATAMIND_QUERY_USER / DATAMIND_QUERY_PASSWORD
DATAMIND_QDRANT_URL                             http://127.0.0.1:56333
PYTHONUTF8=1 / PYTHONIOENCODING=utf-8
```

**刻意不设**的两项：

* `DATAMIND_QUERY_DSN`：`sql_engine._execute` 会拒绝「一个 DSN 复用给不同 catalog 库」
  （`sql_engine.py:310-315`），所以改用 `DATAMIND_DB_HOST/PORT/USER/PASSWORD` 这一组；
* `DATAMIND_LOCAL_NO_LOGIN`：它是**全局认证旁路**（`docs/DEPLOYMENT_BOUNDARIES.md` 第 1 节），
  老师本机访问是回环，默认就已经拿到 demo admin，不需要也不应该设它。

`uvicorn` 启动参数固定带 `--no-proxy-headers`（同文档第 2 节），只绑 `127.0.0.1`。

---

## 6. 打包时踩到的硬编码 / 边界（如实记录）

### 6.1 构建期脚本硬编码 5432 / 6333（无环境变量开关）

`ingestion/ingest.py:35,40`、`db/build_catalog.py:16`、`db/seed_demo_retail.py:81`、
`db/seed_eval_cases.py:11`、`db/seed_industrial.py:15` 把端口写死在模块常量里。
按边界要求不改这些文件，改为打包时用 `delivery_template/build_support/sitecustomize.py`
（构建期适配器，**不进交付包**）把 `psycopg2.connect` 与 `qdrant_client.QdrantClient`
指向便携实例：

* 只改写「本机 + 5432/5433」与「本机 + localhost:6333」这类连接；
* `DM_BUILD_PG_PORT` / `DM_BUILD_QDRANT_URL` 缺一个就直接抛异常，**绝不静默回落**；
* 每次构建会往 `delivery\_build\logs\shim_hits.log` 记命中次数，便于自证。

因为嵌入式发行版的 `._pth` 会忽略 `PYTHONPATH`，构建期统一用
`delivery_template/build_support/run_with_shim.py` 作为入口加载这个适配器。

### 6.2 PostgreSQL 的 initdb 要求 ASCII 路径（实测）

* `initdb` 在**安装目录或数据目录含中文**时报
  `FATAL: invalid byte sequence for encoding "UTF8": 0xd6 0xd0`；
  用 `subst` 把安装目录换成 ASCII 盘符也一样，说明卡的是「安装目录（share/）」；
* 但**已经建好的 pgdata，postgres 服务器在中文路径下能正常启动、能读写**（本机实测：
  安装目录 `…\ICT中国电子杯\…\pgsql`、数据目录 `…\中文数据\pgdata` 都含中文，照样
  `database system is ready to accept connections`，`psql` 查询正常）；
* 因此方案是：**只在建库这一段**用 `subst` 造一个 ASCII 盘符（物理文件仍在本目录内，
  建完就撤掉），交付包本身与 `run.bat` **不依赖 subst**；
* pgdata 可跨路径搬运（构建期在 `X:\pgdata` 建好，搬到中文真实路径后启动验证通过）。

### 6.3 模型缓存路径硬编码

`datamind/knowledge.py:103`、`ingestion/tables.py:13`、`ingestion/ingest.py:44` 把缓存写成
`ROOT/.cache/...`，无环境变量。所以 `fastembed`（约 90 MB）与 `ocr_models`（约 80 MB）
随包放在 `app\.cache\` 下，老师机器无需联网下载模型。
（`datamind/pdf_vectors.py:189` 支持 `DATAMIND_PDF_EMBED_CACHE`，那条路径不涉及本站点。）

### 6.4 没有启用的功能（与开发环境一致，不是打包引入的）

* **私有 PDF 检索**（`DATAMIND_PDF_QDRANT_URL` + API Key 的独立 Qdrant）：不设这两个变量，
  该路径按设计「fail closed」返回 `PDF_INDEX_QDRANT_CONFIG_INVALID`；开发用的
  `deploy/compose.yaml` 也没起这个实例。
* **公式识别**：`.cache/formula_models/pp_formulanet_plus_m.onnx` 在本机未安装
  （`ingestion/formula.py:44` 会判缺失），未识别图片按既有设计保留为解析缺口。
* **Wiki 词条为 0**：词条要靠模型/人工创建（`POST /v1/wiki/entries`、Wiki 整理 worker），
  构建期没有模型密钥，所以不预置词条；`GET /v1/wiki/entries` 返回 200 + 空列表。

---

## 7. 可重复性：哪些会重建，哪些会复用

| 内容 | 复用判据 | 强制重建 |
|---|---|---|
| 4 个运行时压缩包 | 文件 sha256 与 `delivery_cache\runtime_manifest.json` 一致 | `-Force`（fetch 脚本） |
| PostgreSQL / Qdrant / Python 解压结果 | 压缩包 sha256 写进 `_build\build_stamps.json` | `-RebuildRuntime` |
| pylibs 依赖 | 依赖清单（`requirements.lock` + tzdata）哈希 + Python 包哈希 | `-RebuildRuntime` |
| `app\` 源码与前端产物 | **每次重新同步**（这正是「前端改了要能同步」的保证） | 无需 |
| `data\pgdata` / `data\qdrant_storage` | 存在即复用 | `-RebuildData` |

pip 的下载缓存在 `delivery_cache\pip`（`PIP_CACHE_DIR`），重装依赖不重新下载。

---

## 8. 模型密钥怎么带（默认不带）

密钥在应用里的位置（读了 `datamind/settings_store.py`）：**元数据库 `datamind_meta`
的 `app_settings` 表**，键 `model.profiles`（多套接入配置，`active` 指向当前启用那套），
`readonly_user` 对该表没有权限（`docs/DEPLOYMENT_BOUNDARIES.md` 第 4 节）。
环境变量只是兜底，优先级为：请求显式字段 > 启用配置（**只有它自带 api_key 时才参与**）
> 环境变量 > 默认值（`settings_store.resolve_profile_values`）。

因此有三种带 key 的方式：

1. **不预置（默认，本包采用）**：老师启动后在页面左侧「模型接入」里填。
   密钥落进预置数据库的 `app_settings`，不写任何文件。
2. **环境变量 / 文件**：把 `data\model_config.env.example` 复制成
   `data\model_config.env` 并填 `NEW_API_KEY=…`（可加 `NEW_API_BASE_URL`、`NEW_API_MODEL`），
   `run.bat` 启动时会把它逐行注入进程环境。**这个文件里的 key 会随包一起被拷走。**
3. **打包时写死**：`scripts/build_delivery.ps1 -EmbedModelKeyFile <某个.env>`，
   脚本会把它复制成包内 `data\model_config.env` 并在日志里警告。

**风险（务必知情）**：方式 2/3 会让 zip 里带着一个可用的真实密钥——转发压缩包、
传网盘、放进共享目录都可能外泄；请只在「给特定评委、点对点交付」时使用，
并且**不要**把带 key 的包提交进 git。默认（方式 1）下，包里 grep 不到任何 `sk-` 密钥，
本次交付也据此自检过。

---

## 9. 安全边界（与仓库既有约定一致）

* 三个服务都只绑 `127.0.0.1`，不对局域网开放；
* 不安装系统服务、不写注册表、不改系统 PATH、不写 `%ProgramFiles%`；
* 不设 `DATAMIND_LOCAL_NO_LOGIN`；`uvicorn` 带 `--no-proxy-headers`；
* 数据库口令仍是开发口令（`datamind_app_dev` / `readonly_dev` / `datamind_dev`），
  与 `deploy/init/01_roles.sql` 一致——单机回环演示的既有前提，未加固；
* `stop.bat` 只结束「可执行文件路径在本包目录内」的进程，不会误杀老师机器上的
  其它 postgres/python/qdrant。

---

## 10. 验证记录

### 10.1 2026-10-16 的实测结论（本次修复后复跑）

| 项目 | 结果 |
|---|---|
| 构建脚本连跑两次 | 两次均退出码 0（第 1 次 `-RebuildData` 全量 1.4 分钟；第 2 次 `-Frontend build` 重建前端后 0.8 分钟） |
| 干净目录解压 | `D:\dmtest\中文 测试目录\`（**中文 + 空格**）解压 12 秒，16811 个文件 / 1.24 GB |
| `run.bat` | 在中文+空格路径下能正常启动、能识别「8000 已被本应用占用」并给出正确提示 |
| `/health` | 200，`database=ready`、`vector_store=ready`、`knowledge=ready`、`document_count=27` |
| `/v1/documents` | 200，27 篇文档，26 篇 `searchable=true` |
| `POST /v1/chat`（`2024年一共有多少笔订单？`） | `status=COMPLETED`，答案「查询结果：997。」 |
| 不依赖 Docker | 便携实例跑在 55433/56343，Docker 那套仍在 5432/6333，验证前后 `docker ps` 完全一致 |
| 不依赖系统 Python | 8000/18000 的监听进程与 worker 的 `ExecutablePath` 全在包内 `runtime\python\python.exe` |
| 包内运行时依赖自检 | `import_check.py` 通过（32 个第三方依赖 + 64 个应用模块 + 2 个 worker 入口） |

> `POST /v1/chat` 要在**没有模型密钥**的默认包里返回 `COMPLETED` 是不可能的（见第 8 节）：
> 上面这条 `COMPLETED` 是在启动便携实例时用**环境变量** `NEW_API_KEY/NEW_API_BASE_URL/
> NEW_API_MODEL` 临时提供密钥跑出来的，密钥**没有写进包、也没有落进预置数据库**（已复查
> `app_settings` 为空）。默认包在同样问题上的实际返回是
> `status=FAILED_WITH_EVIDENCE`、`error_code=MODEL_KEY_MISSING`，并附带中文提示
> 「尚未配置模型 API 密钥，请在左侧『模型接入』中填写并保存后重试。」——这是设计行为，
> 不是缺陷。

要自己复跑：按第 2 节重新打包，然后

```powershell
# 1) 干净目录解压（用 .NET 的 UTF-8 解压，PowerShell 5.1 的 Expand-Archive 会毁掉中文名）
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory(
  'delivery\DataMindAgent_Demo.zip', 'D:\dmtest\中文 测试目录', [System.Text.Encoding]::UTF8)

# 2) 端到端自检（只读；POST 只发一次 /v1/chat）
pwsh -File scripts\verify_delivery_pack.ps1 -PackRoot 'D:\dmtest\中文 测试目录\DataMindAgent_Demo'
```

---

## 11. 构建脚本的「不许无声挂起」约定与踩坑记录

### 11.1 曾经的故障：构建在某一步无声挂起 18 分钟

修复前的现场：`_build\logs\build_postgres.log` 显示便携 PostgreSQL 在 18:15:19 干净关闭，
之后 **18 分钟没有任何文件被修改**，只能靠人工终止；`qdrant.exe` 变成占着 56333 的孤儿。
当时脚本里三处写法都会导致这种「静默挂起」：

1. `Start-Process -Wait -RedirectStandardOutput/-RedirectStandardError`：Windows
   PowerShell 5.1 会一直等重定向流的 EOF，而 postgres / cmd 会把句柄复制给子进程，
   子进程不退，EOF 就永远不来（同一个坑在 `scripts/share_public.ps1` 上先踩过一次）；
2. 把 `pg_ctl` 的输出接进管道（`& $pgCtl ... | Out-Null`）：postgres 继承管道句柄，
   实测把调用方挂住 3 分钟以上；
3. 等待循环没有上限，任何一步卡住都没有人叫停。

### 11.2 现在的防护（三层，缺一不可）

1. **分步心跳日志**：每一步、每个外部命令、每次等待都往
   `delivery\_build\logs\build_steps.log` 写带毫秒时间戳的一行，同时刷新
   `delivery\_build\build_heartbeat.txt`。想知道「挂在哪一行」直接看这个文件；
   每次运行会先清空，避免上一轮的尾部误导定位。
2. **无进展看门狗**：一个独立进程盯心跳文件，默认 **15 分钟**没有任何推进就写
   `logs\build_hang_report.txt`（含触发时的最后一行）并结束构建进程。
   可用 `-WatchdogMinutes` 调整。失败时脚本以退出码 2 结束。
3. **每个外部命令单独设硬超时**：`Invoke-NativeChecked` 用 .NET `Process` 直接启动
   （`UseShellExecute=$false`、不重定向任何流，子进程继承控制台所以进度实时可见），
   到点就杀整个进程树并抛中文错误。`-StepTimeoutMinutes`（默认 40 分钟）是应用脚本
   （迁移 / 种子 / OCR 摄取）的上限。
4. **绝不留孤儿**：`finally` 里按「可执行文件位于本交付目录内」精确结束
   `qdrant.exe` / `postgres.exe`，不碰机器上其它实例；`subst` 盘符一定撤销；
   数据目录**在关 Qdrant 之前**就搬到位（pgdata 是几十分钟的成果，Qdrant 只是缓存）。

### 11.3 本次一并修掉的坑（都验证过）

| 现象 | 根因 | 修法 |
|---|---|---|
| `ModuleNotFoundError: No module named 'pywintypes'`，`qdrant_client` 直接 import 失败 | 嵌入式发行版走 `._pth` 通道时**不处理** `pylibs` 里的 `*.pth`，pywin32 那套 hackery 根本不执行 | `._pth` 显式加入 `..\pylibs\pywin32_system32`、`..\pylibs\win32\lib`、`..\pylibs\win32`（顺序：`win32\lib` 在前）；并把 `pywin32==312` 加进额外 pin（`requirements.lock` 里没有它） |
| 脚本在 Windows PowerShell 5.1 下报一堆语法错、中文乱码 | `scripts/build_delivery.ps1` 存成了 **UTF-8 无 BOM**，PS 5.1 会按 ANSI 读 | 存成 UTF-8 **带 BOM**（仓库里其它 .ps1 本来就是带 BOM 的） |
| `pg_ctl: unrecognized operation mode "55432"`，PostgreSQL 90 秒都起不来 | `Start-Process` 不替参数加引号，`-o` 只吃掉紧邻的一个 token | `-o` 的值自带引号：`"`"-p $Port`""` |
| 解压到含**空格**的路径（`D:\dmtest\中文 测试目录\`）后 `pg_ctl: unrecognized operation mode "测试目录\..."` | 同上：含空格的 `-D` 路径被拆成两个 token | 所有可能含空格的路径参数过 `Quote-Arg`（`dmstack.ps1` 与构建脚本各有一份） |
| `pg_ctl start 失败（退出码 ）`——明明启动成功却判失败 | 配了 `-RedirectStandardOutput` 之后 `$proc.HasExited` / `$proc.ExitCode` 在本机不可靠 | 改用 `Wait-Process -Timeout` + `Get-Process -Id ... .ExitCode` |
| 前端每次重建，旧哈希 assets 永远堆在 `app\web\vue\assets\` 里（实测一次 +36 个文件） | `robocopy /E` 只增不删 | `app\` 的同步加 `/PURGE`（`Copy-Tree -Mirror`）；`data\` 绝不用，否则会清掉 pgdata |
| 自检阶段报 `Index was outside the bounds of the array` | 用 `-split` 解析 psql 输出却没判空；而且门槛写的是 `evidence_nodes >= 10`，那个表只由**用户提问时**写入（`agent.py:220`、`evidence_graph.py:514`），构建期本来就该是 0 | 先判空并给出中文错误；门槛改成构建期真正产出的 `document_assets>=10`、`chunks>=50`、`evaluation_cases>=10`、`demo_retail.orders>=100`、`chinook.Track>=100` |
| 中途被打断后 `pg_ctl start` 拒绝启动 | 残留 `postmaster.pid` | `Clear-StalePostmasterPid`：只在里面记的 PID **确实已不存在**时才删 |
| 交付包在含空格路径下 `run.bat` 起不来 PostgreSQL | `dmstack.ps1` 的 `start-postgres` 有同一处引号问题，`start-qdrant` 还用了会挂起的 `Start-Process -RedirectStandardOutput` | 两处都改：路径过 `Quote-Arg`；Qdrant 改成 `UseShellExecute=$true` 的脱离启动，输出由 `cmd` 自己重定向到文件 |

### 11.4 「前端改了要一键重打包」

```powershell
cd D:\ICT中国电子杯\datamind-agent
pwsh -File scripts\build_delivery.ps1 -Frontend build
```

`-Frontend build` 会先 `npm install`（仅当缺 `node_modules`）再 `npm run deploy`
（vite 直接输出到 `web\vue`），然后把 `app\` 与仓库**镜像同步**（`/PURGE` 会清掉上一次
构建遗留的旧哈希资源），最后重建 zip。实测耗时约 50 秒，两次连跑均成功。

---

## 12. 「双击 run.bat 之后永远卡死」的两个缺陷（2026-10-16 修复，均已实测复现+验证）

老师在干净目录双击 `run.bat` 后看到的是**一个永远不动的黑窗口**。现场证据：
`[4/6] 启动 Web 服务...` 之后 131 秒零输出、进程仍存活；用 `stop.bat` 杀掉 API 后
窗口**立刻继续**，并把一直被吞掉的 `INFO: 127.0.0.1:54971 - "GET /health HTTP/1.1" 200 OK`
当成变量值打印出来（说明它被 `for /f` 当成子进程输出抓走了）。
拆开看是**两个独立缺陷**，而且第二个的成因与最初的判断不同。

### 12.1 缺陷 A：[4/6] 永久阻塞 —— `for /f` 等一个永远不来的管道 EOF

```bat
rem 修复前 run.bat:108
for /f "delims=" %%A in ('%PS% "%STACK%" -Action start-api -Quiet 2^>nul') do set "APILINE=%%A"
```
```powershell
# 修复前 dmstack.ps1:282-283（第 279 行算好的 $apiLog 从未被使用）
$proc = Start-Process -FilePath $Python -ArgumentList $apiArgs -WorkingDirectory $AppDir `
    -NoNewWindow -PassThru
```

uvicorn **继承了 `for /f` 的管道作为自己的 stdout**；`for /f` 必须等到管道 EOF
（= API 进程退出）才结束循环，而 API 是常驻服务 → 永久阻塞。
`data\logs\api_console.log` 因此永远不会生成，窗口里也看不到任何服务日志。

用 cmd 实测（子进程持续写 25 秒，等价复现）：

| 启动方式 | `for /f` 返回耗时 | 抓到的内容 |
|---|---|---|
| 修复前：`-NoNewWindow -PassThru`，不重定向 | **25.5 s** | 子进程的输出行（与现场一致） |
| 只加 `-RedirectStandardOutput/-RedirectStandardError` | **25.5 s** | 无输出，但照样被拖住 |
| **不捕获输出**（run.bat 直接调用，PID 走文件） | **0.3 s** | 不适用；子进程继续存活 |

也就是说：**只加重定向并不能解决**。原因（PS 5.1 的实现）：`Start-Process` 的重定向
是「父进程建管道 + 后台把管道内容拷进文件」，孙进程仍然持有那条指向调用方的管道写端，
`for /f` 依旧等不到 EOF。真正的修法是**让 run.bat 不再捕获 `start-api` 的输出**。

修复（两处一起，互为双保险）：

* `tools\dmstack.ps1` `start-api`：
  * `-RedirectStandardOutput $apiLog -RedirectStandardError $apiErrLog`
    —— 必须是**两个不同文件**：PS 5.1 的 `Start-Process` 在两者相同时直接报
    `"RedirectStandardOutput" and "RedirectStandardError" are same`；
  * 启动前清空这两个文件（避免老师把上一轮的残留当成已就绪）；
  * **整个分支不再往 stdout 写任何东西**，PID 只落盘到 `data\logs\api.pid`。
    即使以后有人把该分支重新包进 `for /f`，也没有东西会被挡住。
* `run.bat`：`start-api` **直接调用**（不再 `for /f`），PID 从 `api.pid` 读。

顺带修掉一个同源的坑：`stop.bat`/手工排查时不要写
`powershell ... -Action start-postgres | ForEach-Object {...}` —— `postgres.exe`
会继承管道，父进程会挂到 postgres 退出为止（实测挂住 3 分钟以上）。

### 12.2 缺陷 B：横幅报 `'Web' is not recognized...` —— 真因是 if 块里未转义的括号

老师看到的原文：

```
   Qdrant     : 便携版 1.12.4，端口 56333  （避开 6333）
'Web' is not recognized as an internal or external command,
operable program or batch file.
```

最初判断是「cmd 读 UTF-8 批处理的字节/字符错位」。**实测后修正了结论**：
真正的第一现场是 `run.bat` 第 64 行（原版）那个**全角右括号**：

```bat
if /i "%PORTBUSY%"=="BUSY" (
    echo [2/6] 错误：端口 %PGPORT% 已被占用（可能是上一次没退干净，或另有程序在用）。
                                                                        ↑ 全角 ）
```

cmd 把块内 echo 里的括号当**块边界**：这个未转义的 `）` 提前结束了 `if` 块，紧跟其后
第 70 行的 `echo [2/6] 启动 PostgreSQL（数据已预置，通常 3-10 秒）...` 变成块外命令，
行内的 `(` 触发 `. was unexpected at this time.`，**整个 [2/6] 分支被静默跳过**。

极小化验证（3 个变体，同一块内）：

| 变体 | 结果 |
|---|---|
| `echo (leftover).` —— 括号不转义 | `. was unexpected at this time.`，输出为空 |
| `echo ^(leftover^).` —— 转义 | 正常输出 `(leftover).` |
| 该行完全不含括号 | 正常 |

用「逐行截断 + 实跑」在**原版文件**上定位到：截到第 82 行（含中文那行）开始报错。
同一份实验还确认了一个次要现象：cmd 读含多字节字符且用 `for /f` 的批处理时，
**会从行中间开始解析**（4 个长度中性变体 3/3 复现 `'Web' is not recognized`），
所以多字节字符确实也要避开 —— 但它不是这个报错的第一现场。

修复：

* 半角括号一律转义：`echo         ^(leftover from a previous run, or another program^).`
* `run.bat` 改成**纯 ASCII**（关键行不再依赖 cmd 对多字节字符的处理）。
  中文提示挪到 `note_running.txt` / `note_broken.txt`，由 run.bat 用 `type` 打印
  —— `type` 只做字节转发，不受批处理解析影响（实测 `type` 显示中文正常）。
  这两个 note 文件**不要 BOM**：带 BOM 时行的开头会多出一个 U+FEFF，
  老师看到的就是「﻿提示（中文）：」。
* `stop.bat` 只有 25 行、不含 `for /f`，实测连跑 3 次 stderr 全空，因此保持中文原样。

### 12.3 打包阶段的回归断言（防止再犯）

`scripts/build_delivery.ps1` 在写入包内启动脚本后新增 `Assert-BatchSyntax`：

| 断言 | 作用 |
|---|---|
| `run.bat` 必须纯 ASCII（`stop.bat` 不要求） | 防止有人把中文写回入口脚本 |
| 两个 .bat 都不许有 UTF-8 BOM | cmd 会把 BOM 当命令 |
| 两个 .bat 的 `if (...)` / `for (...)` 块内 echo 不许有未转义括号（半角**与全角**都算） | 就是 12.2 那个缺陷；用原版文件验证过会红着脸失败 |
| `note_*.txt` 不许有 BOM | 防止 `type` 显示时多一个怪字 |

### 12.4 修复后的实测（干净目录 + 中文空格路径）

* 解压：`.NET ZipFile.ExtractToDirectory(..., UTF8)` → `D:\dmtest\hangfix\中文 测试\`，
  16830 个文件，13.9 秒（PS 5.1 的 `Expand-Archive` 会毁中文名，别用）。
* `run.bat`：`[0/6]` → `[6/6]` **全程自己走完并打开浏览器**，stderr **完全为空**，
  `[4/6]` 之后不再卡住。
* `data\logs\api_console.log` 生成且非空（uvicorn 的 access log），
  `api_console.err.log` 里有 `Uvicorn running on http://127.0.0.1:8000`。
* 另起一个标题为 `DataMind Agent service log` 的窗口实时 tail 这两份日志
  （`run.bat` 用 `start` 拉起 `dmstack.ps1 -Action start-api-tail`；
  该窗口只读日志，`stop.bat` 不关它，老师可以自己关掉）。
* 从启动到浏览器可用约 **9-10 秒**（PostgreSQL 3 s + Qdrant 2 s + uvicorn 1 s + /health 1-3 s）。
* 端到端：`/health` 三个 ready 且 `document_count=27`、`/v1/documents` 27 篇、
  `GET /v1/wiki/entries` 200、`POST /v1/chat` 在默认无 key 时返回
  `status=FAILED_WITH_EVIDENCE` + `error_code=MODEL_KEY_MISSING`（设计行为）。
  注意 `POST /v1/chat` 是变更请求，必须带 `X-CSRF-Token`（取自 `GET /v1/auth/me`），
  否则 403 `CSRF_REQUIRED`；带 `Origin` 头则要求它在 allow-list 内。
* `stop.bat`：三个端口全部释放、包内进程 0 残留、无 stderr 解析错误。

### 12.5 手工验证清单（改动 run.bat / dmstack.ps1 后必做）

```powershell
# 1) 重新打包（前端改过就带 -Frontend build）
cd D:\ICT中国电子杯\datamind-agent
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_delivery.ps1 -Frontend build

# 2) 让出 8000
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1 -Stop

# 3) 干净目录解压（必须用 .NET，别用 Expand-Archive）
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory(
  'D:\ICT中国电子杯\delivery\DataMindAgent_Demo.zip',
  'D:\dmtest\中文 测试', [System.Text.Encoding]::UTF8)
# 4) 双击 D:\dmtest\中文 测试\DataMindAgent_Demo\run.bat，确认走到 [6/6]
# 5) 确认 data\logs\api_console.log 非空、窗口里能看到 uvicorn 的 INFO 行
# 6) stop.bat 收尾；最后恢复主服务：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1 -Background
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1 -Status
```

> 双击验证时不要用 `< nul` 之类重定向 stdin：`pause` 会立刻拿到 EOF，
> `run.bat` 会继续往下走完并调用 `stop.bat`，看不到真实交互。
> 也不要把 `run.bat` 的输出接进管道做「测时间」—— 那正是缺陷 A 的触发条件。

