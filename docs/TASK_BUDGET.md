# 统一任务预算与进程时限

2026-10-07，API **0.19.0**。本阶段为解析、OCR 与登记类任务提供统一的资源预算和**硬进程时限**，补齐 [原生 PDF 准备](PDF_PREPARATION.md) 与 [多文件并发方案](MULTIFILE_CONCURRENCY.md) 中记录的缺口：「跨主机任务的租约、资源槽和子进程 deadline 仍需工作器实现」。

## 提供的能力

实现位于 `datamind/task_budget.py`，只提供原语；既有进程内路径保持原样，调用方显式选择后才启用。

| 原语 | 作用 |
|---|---|
| `run_bounded(module, function, args, …)` | 在**独立进程组**中执行 `module.function(*args)`，挂钟超时后终止整组 |
| `resource_slot(dsn, name, concurrency)` | 用 PostgreSQL 会话 advisory lock 限制跨进程同时运行数，无空槽立即 `503/TASK_BUDGET_BUSY` |
| `effective_dsn(dsn)` | 解析当前进程**实际连接**的数据库名，避免子进程写入原库 |
| `validate_deadline` / `configured_*` | 参数与环境变量校验（`DATAMIND_TASK_DEADLINE_SECONDS`、`DATAMIND_TASK_GRACE_SECONDS`、`DATAMIND_TASK_OUTPUT_BYTES`、`DATAMIND_TASK_SLOTS`） |

超时处理为 `SIGTERM` 进程组，宽限期后 `SIGKILL`；子进程与其**孙进程**一并终止。结果以状态字典返回，不抛异常、不外传堆栈：

| status | error_code | 含义 |
|---|---|---|
| `ok` | — | 正常返回 `value` |
| `timeout` | `TASK_DEADLINE_EXCEEDED` | 超过挂钟时限，进程组已终止 |
| `failed` | 目标的 `detail.error_code` 或异常 `.code` | 目标以稳定错误码失败 |
| `failed` | `fallback_error_code` / `TASK_RESULT_INVALID` | 子进程无有效结果或输出被截断 |

每个结果都带 `elapsed_ms`、`deadline_seconds`、`exit_code`、`stdout_bytes`、`stderr_bytes`、`output_truncated`。stdout/stderr 各自截断到上限并计数，**洪水输出无法伪造一个可解析的结果**。

## 已接入的位置

- `datamind/file_jobs.py`：`work_once` / `drain` 新增可选 `deadline_s`。设置后每次登记在该预算下的独立进程组执行，超时记为 `FILE_JOB_DEADLINE_EXCEEDED` 并释放租约；不设置时保持原有进程内线程路径。
- `scripts/file_job_worker.py`：新增 `--deadline`、`--grace`。
- `scripts/prepare_pdf.py`：新增 `--deadline`、`--grace`，在独立进程组内运行准备；`ingestion/pdf_prepare.py` 新增 JSON 入口 `prepare_json` / `verify_json`。

```bash
# 受控登记 worker：每次登记独立进程组，60 秒硬时限
.venv/bin/python scripts/file_job_worker.py --watch --workers 2 --deadline 60 --grace 5

# 原生 PDF 准备：独立进程组 + 硬时限
.venv/bin/python scripts/prepare_pdf.py --source /absolute/native.pdf \
  --document-id doc-example --document-version 1 --expected-sha256 '<64位小写SHA>' \
  --job-id prepare-demo --attempt-token attempt-1 --deadline 120
```

## 验证

`tests/test_task_budget.py` **16/16**、`tests/test_file_jobs.py` **24/24**，完整隔离回归见 `reports/task_budget_verification.json`。其中：

- 真实子进程（`sleep 60`）在 1 秒时限内被终止，且 `sh -c 'echo $$ > pid; sleep 60'` 的孙进程一并消失。
- 输出上限：10 万字节 stdout 被截到 4096 字节，`output_truncated=true`，且无法产生结果。
- 资源槽：两个槽占满后第三个立即 `503/TASK_BUDGET_BUSY`；异常退出后槽释放。
- `effective_dsn` 指向当前数据库；登记接线把 claim、deadline 与该 DSN 交给子进程。
- CLI 冒烟：真实原生 PDF 在 60 秒时限下准备成功、`verified=true`；非法时限与孤立 `--grace` 均以 exit 2 拒绝。

这些是组件与契约证据，不是 OCR/问答准确率。

## 边界

- 本阶段证明**挂钟 deadline 与进程组终止**，不承诺 CPU/内存 cgroup 级硬隔离；PDF 准备清单仍保留原始告警 `NO_HARD_NATIVE_CPU_OR_MEMORY_ISOLATION`。
- 资源槽是 PostgreSQL 会话锁，依赖健康数据库连接；会话丢失即释放槽。
- 子进程不继承父进程的 monkeypatch 状态。真实登记在子进程运行要求目标模块的存储配置可显式解析；测试以接线与 DSN 交接验证该路径，未把测试夹具的临时根目录伪装成生产配置。
- 对已经进入原生推理的线程不做物理取消，只终止其进程组。
- OCR/公式阶段接入同一预算、跨主机租约与授权真实输入验收仍待完成。
