# 持久文件队列专项的原生退出记录

日期：2026-10-06。仅记录观察到的执行结果与崩溃栈，不以一次重跑证明原生依赖永远不会复发。

## 两次执行

| 执行 | unittest 与隔离报告 | 进程退出 | 原始日志 |
|---|---|---|---|
| 首次 20 项执行 | 20 tests，0 failures，0 errors；报告元数据库隔离、原数据库未修改 | 134；测试结束后出现 C++ `recursive_mutex lock failed: Invalid argument` | `file_jobs_targeted_initial_exit134.txt` |
| 随后复核执行 | 同样 20/20，0 failures，0 errors；报告元数据库隔离、原数据库未修改 | 0 | `file_jobs_targeted_final_exit0.txt` |

首次执行不能计作进程验证通过。两次之间没有为消除此原生退出而更改 CAD 解析器、队列业务代码或串行化解析。

## 原生栈证据

本机 macOS 诊断文件 `Python-2026-10-06-135118.ips` 报告 `EXC_CRASH / SIGABRT`，faulting thread 为 3。仅摘录相关符号；未复制含本机完整进程环境的诊断文件：

```text
libc++.1.dylib
  std::__1::recursive_mutex::lock()
onnxruntime_pybind11_state.so
  Microsoft::Applications::Events::DebugEventSource::DispatchEvent(...)
  Microsoft::Applications::Events::LogManagerImpl::DispatchEvent(...)
  Microsoft::Applications::Events::HttpResponseDecoder::DispatchEvent(...)
  Microsoft::Applications::Events::HttpResponseDecoder::handleDecode(...)
  Microsoft::Applications::Events::HttpClientManager::onHttpResponse(...)
  Microsoft::Applications::Events::PlatformAbstraction::WorkerThread::threadFunc(...)
libsystem_pthread.dylib
  _pthread_start
```

这是 ONNX Runtime 遥测 HTTP 后台线程的原生栈；该 faulting thread 没有 CAD 解析、字体、PostgreSQL 登记或 Python 队列续租函数。现有证据支持定位该次中止的直接发生处，不能据此推断每个并发依赖都已完成线程安全证明，也不支持将这次中止归因于 DXF 解析。

## 可核查的依赖事实与后续限制

本机 ONNX Runtime 为 1.30.0，包导出 `onnxruntime.disable_telemetry_events()`；其本地 docstring 为 `Disables platform-specific telemetry collection.`。单独导入并调用的进程退出码为 0。隔离回归的工业演示 seed 会导入 `ingestion/ingest.py`，该模块顶层导入 Qdrant 客户端，形成可引入 FastEmbed/ONNX Runtime 的依赖路径；路径判断为代码推断，不是网络遥测请求的完整追踪。

队列没有用吞掉异常、强制退出或跳过测试来掩盖该结果。工作器续租线程使用停止事件并在每项结束时 join；无需在测试报告中把它当作上述原生遥测线程。最终通过日志和失败日志均保留，完整回归需另行报告实际退出状态。禁用不需要的遥测可在运行配置/测试入口处理，其有效性仍应结合后续执行核查，不能只凭一个成功重跑承诺依赖问题已经彻底修复。

2026-10-07 交付补录：`scripts/test_isolated.py` 在工业 seed 前调用可用的 `onnxruntime.disable_telemetry_events()`，不更改模型推理。完整隔离回归 261/261、进程退出码 0，见 `next_stage_regression.txt`。此结果说明这次最终执行正常，仍不构成原生依赖永不复发的保证。
