# 统一任务预算与进程时限

2026-10-07，API **0.19.0** 引入预算原语，**0.20.0** 接入 OCR/公式。本阶段为解析、OCR 与登记类任务提供统一的资源预算和**硬进程时限**，补齐 [原生 PDF 准备](PDF_PREPARATION.md) 与 [多文件并发方案](MULTIFILE_CONCURRENCY.md) 中记录的缺口：「跨主机任务的租约、资源槽和子进程 deadline 仍需工作器实现」。

## 提供的能力

实现位于 `datamind/task_budget.py`，只提供原语；既有进程内路径保持原样，调用方显式选择后才启用。

| 原语 | 作用 |
|---|---|
| `run_bounded(module, function, args, …)` | 在**独立进程组**中执行 `module.function(*args)`，挂钟超时后终止整组 |
| `run_bounded_command(argv, stdin_text, …)` | 同上，但执行**任意命令**（可为独立解释器/入口脚本），成功仅表示退出码为 0，stdout 由调用方解释 |
| `resource_slot(dsn, name, concurrency)` | 用 PostgreSQL 会话 advisory lock 限制跨进程同时运行数，无空槽立即 `503/TASK_BUDGET_BUSY` |
| `effective_dsn(dsn)` | 解析当前进程**实际连接**的数据库名，避免子进程写入原库 |
| `validate_deadline` / `configured_*` | 参数与环境变量校验（`DATAMIND_TASK_DEADLINE_SECONDS`、`DATAMIND_TASK_GRACE_SECONDS`、`DATAMIND_TASK_OUTPUT_BYTES`、`DATAMIND_TASK_SLOTS`） |

超时处理为 `SIGTERM` 进程组，宽限期后 `SIGKILL`；子进程与其**孙进程**一并终止。传入 `cancel_event` 时，取消会**传播到运行中的子进程**：轮询期间置位即终止整组并返回 `status=cancelled`。结果以状态字典返回，不抛异常、不外传堆栈：

| status | error_code | 含义 |
|---|---|---|
| `ok` | — | 正常返回 `value` |
| `timeout` | `TASK_DEADLINE_EXCEEDED` | 超过挂钟时限，进程组已终止 |
| `cancelled` | `TASK_CANCELLED` | 收到取消请求，进程组已终止；不声称撤销已提交写入 |
| `failed` | 目标的 `detail.error_code` 或异常 `.code` | 目标以稳定错误码失败 |
| `failed` | `fallback_error_code` / `TASK_RESULT_INVALID` | 子进程无有效结果或输出被截断 |

每个结果都带 `elapsed_ms`、`deadline_seconds`、`exit_code`、`stdout_bytes`、`stderr_bytes`、`output_truncated`。stdout/stderr 各自截断到上限并计数，**洪水输出无法伪造一个可解析的结果**。

## 已接入的位置

- `datamind/file_jobs.py`：`work_once` / `drain` 新增可选 `deadline_s`。设置后每次登记在该预算下的独立进程组执行，超时记为 `FILE_JOB_DEADLINE_EXCEEDED` 并释放租约；数据库中的取消请求会终止运行中的进程组并记为 `cancelled`。不设置时保持原有进程内线程路径。
- `scripts/file_job_worker.py`：新增 `--deadline`、`--grace`。
- `scripts/prepare_pdf.py`：新增 `--deadline`、`--grace`，在独立进程组内运行准备；`ingestion/pdf_prepare.py` 新增 JSON 入口 `prepare_json` / `verify_json`。
- `ingestion/formula.py`：公式识别经 `run_bounded_command` 在 `.venv-formula` 独立解释器中执行，可选跨进程槽，保留 `FORMULA_TIMEOUT` 等错误码。
- `ingestion/ocr.py` + `ingestion/ocr_worker.py`：`page_blocks_bounded` 在独立进程组内渲染并 OCR 单页，映射 `OCR_TIMEOUT`/`OCR_CANCELLED`/`OCR_FAILED`；`ingestion/ingest.py` 通过 `--ocr-deadline`/`--ocr-slots` 可选启用（默认仍在解析进程内）。

```bash
# 受控登记 worker：每次登记独立进程组，60 秒硬时限
.venv/bin/python scripts/file_job_worker.py --watch --workers 2 --deadline 60 --grace 5

# 原生 PDF 准备：独立进程组 + 硬时限
.venv/bin/python scripts/prepare_pdf.py --source /absolute/native.pdf \
  --document-id doc-example --document-version 1 --expected-sha256 '<64位小写SHA>' \
  --job-id prepare-demo --attempt-token attempt-1 --deadline 120
```

## 验证

`tests/test_task_budget.py` **23/23**、`tests/test_file_jobs.py` **25/25**、`tests/test_pdf_ocr.py` **13/13**，完整隔离回归见 `reports/task_budget_verification.json`。其中：

- 真实子进程（`sleep 60`）在 1 秒时限内被终止，且 `sh -c 'echo $$ > pid; sleep 60'` 的孙进程一并消失。
- 输出上限：10 万字节 stdout 被截到 4096 字节，`output_truncated=true`，且无法产生结果。
- 资源槽：两个槽占满后第三个立即 `503/TASK_BUDGET_BUSY`；异常退出后槽释放。
- 取消传播：`cancel_event` 在 0.5 秒后置位，`sleep 60` 子进程被立即终止并返回 `TASK_CANCELLED`。
- `effective_dsn` 指向当前数据库；登记接线把 claim、deadline 与该 DSN 交给子进程。
- CLI 冒烟：真实原生 PDF 在 60 秒时限下准备成功、`verified=true`；非法时限与孤立 `--grace` 均以 exit 2 拒绝。
- 真实扫描页 OCR：子进程路径与进程内路径的块与 detail 关键字段**完全一致**（16 块、27 行、1.79 秒）；同一页给 1 秒时限时进程组被终止并返回 `OCR_TIMEOUT`。

这些是组件与契约证据，不是 OCR/问答准确率。

## 边界

- 本阶段证明**挂钟 deadline 与进程组终止**，不承诺 CPU/内存 cgroup 级硬隔离；PDF 准备清单仍保留原始告警 `NO_HARD_NATIVE_CPU_OR_MEMORY_ISOLATION`。
- 资源槽是 PostgreSQL 会话锁，依赖健康数据库连接；会话丢失即释放槽。
- 子进程不继承父进程的 monkeypatch 状态。真实登记在子进程运行要求目标模块的存储配置可显式解析；测试以接线与 DSN 交接验证该路径，未把测试夹具的临时根目录伪装成生产配置。
- 对已经进入原生推理的线程不做物理取消，只终止其进程组。
- 跨主机租约与授权真实输入验收仍待完成（OCR/公式接入同一预算已于 **0.20.0** 完成，见上文「已接入的位置」）。

## CPU/内存限额（API 0.25.0）

`run_bounded` / `run_bounded_command` 新增两个限额参数，并在结果里回报实际生效的限额：

| 参数 | 机制 | 稳定错误码 |
|---|---|---|
| `cpu_seconds`（1–3600） | 子进程 `RLIMIT_CPU`；超限内核发 `SIGXCPU` | `TASK_CPU_LIMIT_EXCEEDED` |
| `memory_bytes`（16 MiB–64 GiB） | 子进程 `RLIMIT_AS`（**地址空间**，不是 RSS） | `TASK_MEMORY_LIMIT_EXCEEDED` |
| 限额在 preexec 阶段设置失败 | 不执行任务 | `TASK_BUDGET_SPAWN_FAILED` |

**deadline 覆盖 stdin 写入（API 0.34.0）**：子进程的 stdin 由守护线程写入，主路径按**同一**挂钟预算轮询并在超时或取消时终止整个进程组，因此"子进程从不读 stdin、管道写满即阻塞"不再能绕过 deadline。不修复时 8 MiB 的 stdin 会让 `run_bounded_command` 在 `deadline_s=1` 下永久阻塞；修复后实测 < 6 秒返回 `TASK_DEADLINE_EXCEEDED`。正常读取 stdin 的子进程与大载荷（4 MiB）仍完整送达。

**平台事实与失败关闭**：macOS 拒绝设置 `RLIMIT_AS`/`RLIMIT_DATA`（`setrlimit` 返回 EINVAL），只有 `RLIMIT_CPU`/`NOFILE`/`FSIZE` 可用。`rlimit_supported(kind)` 在一次性子进程中探测并缓存结果；**请求了内存上限而平台不支持时直接拒绝**并返回 `TASK_BUDGET_RLIMIT_UNSUPPORTED`，绝不静默当作已生效。真正的内存隔离仍需 Linux cgroup，本模块不主张提供。

## 跨主机租约

`db/migrations/013_task_leases.sql` 建 `task_leases(name, token, holder, fence, acquired_at, renewed_at, expires_at)`。

| 接口 | 语义 |
|---|---|
| `acquire_lease(dsn, name, ttl, holder=…)` | 空闲或已过期时取得；**接管会递增 `fence`**；仍被占用时返回 `None` |
| `renew_lease(dsn, name, token, ttl)` | 仅持有者可续期；已被接管则返回 `None` |
| `release_lease(dsn, name, token)` | 仅持有者可释放；已失去则 `False` |
| `lease_holder(dsn, name)` | 当前持有者，含 `expired` 标记 |
| `sweep_leases(dsn)` | 删除过期行并返回其名称（清理用） |

**这是协作式协调，不是分布式共识**：它假设数据库健康可达，并要求**每个写入方在提交前比较自己取得的 `fence`**，否则失去租约的持有者仍可能覆盖新结果。租约尚未接入 V-CAR 或文件登记的执行路径，当前只提供原语。

## 验证（0.25.0 增量）

`tests/test_task_budget_resources.py` **11/11**，完整隔离回归 **603/603**，见 `reports/parse_contract_verification.json`。覆盖：CPU 上限真实终止自旋子进程并给出 `TASK_CPU_LIMIT_EXCEEDED`；内存上限按平台能力要么执行要么**失败关闭且任务未运行**；无限制运行回报空限额；参数边界校验；租约互斥、`fence` 递增、过期接管后旧 token 无法续期/释放、过期标记与清理、参数校验，以及**独立进程无法取得已被占用的租约**（子进程使用 `effective_dsn` 指向同一数据库）。
