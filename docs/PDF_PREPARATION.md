# 原生 PDF 的版本化准备边界

2026-10-07。这一阶段实现离线准备，不修改现有摄取、元数据库、Qdrant 或 HTTP API。API 保持 0.14.0。准备产物明确 `prepare_only=true`、`indexed=false`、`knowledge_visible=false`，不会自动成为问答证据。

## 输入、产物和重试

`ingestion/pdf_prepare.py` 接受显式文档 ID、文档版本、源 SHA-256、任务 ID 和 attempt token。输入版本只是这次准备的目标声明；当前还没有数据库当前头、认证发布或版本 CAS，不能把任意本地请求当作正式登记。

验证要求清单中的契约、解析器和资源上限与当前实现一致；解析器标识包含实际 PyMuPDF 版本。升级依赖/契约后不自动兼容旧 attempt，应保留原环境用于旧产物核对，而不是改写旧清单。

源字节按内容摘要固定，解析从已经核对的内存字节打开 PDF。页图、带坐标和阅读顺序的原生文本块、切片与清单分别绑定文档版本、源摘要及解析器版本。不同 attempt 使用独立目录；同 attempt 串行封存，不覆盖已有产物。相同请求重试必须先核对现有完整清单及所有源/产物摘要；复用 attempt 改请求、缺少封存标记或修改产物都会拒绝。失败后应使用新的 attempt token，而不是覆盖损坏目录。

`bbox` 使用旋转、裁剪后的渲染页坐标，左上角为原点、单位为 point；在 110 dpi 页图上换算像素需乘以 `110/72`。`source_bbox` 同时保留未旋转裁剪页的原生坐标，页记录明确旋转角、cropbox 和 mediabox，验证会重算二者关系。阅读顺序来自未旋转页的原生文本排序，不能当作复杂版面或表格的语义阅读顺序。

所有清单路径均相对于管理目录，拒绝目录逃逸和符号链接；源路径及存储目录的每级父目录也必须是真实目录。macOS `/tmp`、`/var` 等符号链接别名应先由调用者核对并转换为真实绝对路径。哈希用于本地完整性检测，不是认证签名；拥有文件系统写权限的人仍可能修改文件和清单。准备产物属于私有运行数据，默认目录 `data/object_store/pdf_prepared/` 不进入 Git，需要按用途备份和清理。

源固定后渲染失败可能留下未引用的内容副本；本阶段没有自动回收器。损坏或未完整封存的 attempt 不会被就地修复。完成标记和摘要检查也不等于完整的断电耐久保证；正式发布前还需补齐父目录持久化与存储恢复演练。

## 受控原生范围

| 资源 | 上限 |
|---|---|
| 源 PDF | 8 MiB |
| 页数 | 30 |
| 110 dpi 页图 | 每页 400 万像素、累计 3200 万像素 |
| 原生文本块 | 2000 |
| 抽取文本 | UTF-8 合计 200,000 字节 |
| 切片 | 最多 2250 段；每段最多 800 字符 |
| 图片预算 | 128 次；单张 1600 万像素、累计 6400 万像素 |
| 输出清单 / 文本 bundle / 单页 PNG | 64 KiB / 4 MiB / 17 MiB |
| 完成标记 | 2 KiB |

图片预算保守累计资源图片、软/显式遮罩与实际显示次数，也检查 inline image；同一图片资源可能多次计入，计数不代表去重后的图片数量。准备不以图像哈希解码来去重，也不解释图片内容。

拒绝加密 PDF、无有效页的文件及超过资源限额的输入。任一页原生非空白文字少于 30 字符时返回 `PDF_OCR_REQUIRED`；本阶段不启动 OCR、表头识别、公式模型或嵌入模型，不用原生对照文件补扫描内容。文本规范换行并去除块首尾空白，保留原生内容、页坐标和块内字符偏移；这些定位不证明完整表格结构、图片/公式含义或自然语言蕴含。

字节、页数和绘制像素限制降低一次准备的工作量，但不是原生库 CPU/内存的硬隔离。文件锁面向本地 macOS/Linux；跨主机任务的租约、资源槽和子进程 deadline 仍需工作器实现。

多文件准备使用独立进程，每个进程独立打开文档；模块拒绝在线程中执行准备。[PyMuPDF 官方多进程说明](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)明确其不支持线程并发，文件锁不能替代原生库的进程隔离。

## 使用

准备一个本地文件时，先独立核对源 SHA，再运行：

```bash
python scripts/prepare_pdf.py --source /absolute/path/native.pdf \
  --document-id doc-example --document-version 1 \
  --expected-sha256 '替换为实际64位小写源SHA' \
  --job-id prepare-demo --attempt-token attempt-1

python scripts/prepare_pdf.py --verify \
  --job-id prepare-demo --attempt-token attempt-1
```

可用 `--storage-root` 指定一个独立运行目录。命令不接受 reset、向量发布或当前头更新参数。验证模式只使用已封存请求，不能通过命令行改变文档身份、版本或源 SHA。

## 本阶段验证

合成边界测试最终 **31/31**、进程退出 0，见 `reports/pdf_prepare_targeted_final.txt`。覆盖独立进程的不同文件/相同 attempt/共享源竞争、版本 ID、摘要/路径/封存失败、FIFO、加密及扫描拒绝、旋转与裁剪定位，以及真实 inline/SMask/Mask 超限。部分资源数值边界使用 mock，测试中明确用途；这些结果不是 PDF 识别准确率或真实工厂盲测。

首轮 24 项中有两个共享锁创建竞争错误，退出 1；诊断失败和原日志保留为 `reports/pdf_prepare_targeted.txt`、`reports/pdf_prepare_concurrency_diagnostic.txt`。改为排他创建锁文件后再打开已有文件，第二轮 24/24、退出 0；继续补齐实际文件边界后才得到上述最终 31/31，不合并不同轮次的分母。

实际 CLI 准备、重放、验证、v2 隔离、错 SHA、验证身份覆盖拒绝与篡改拒绝 **8/8**，临时源及产物已删除，见 `reports/pdf_prepare_cli_verification.json`。本模块没有数据库、索引或外部模型调用；原生库版本和实际代码摘要随报告保存。CAD/尺寸/DXF 的 11/13/8 个冻结文件摘要均一致，见 `reports/pdf_prepare_frozen_compatibility.json`。

完整隔离回归 **292/292**（原 261 + 新增 31），进程退出 0，见 `reports/pdf_prepare_regression.txt`。原库文档/块/切片/事实/复核/模型配置计数未变，临时元数据库已删除；没有升级原检出或原服务。核查记录见 `reports/pdf_prepare_verification.json`。

## 后续发布条件

旧 `document_blocks` 没有版本字段，旧 `chunks` 的 `UNIQUE(document_id,chunk_index)` 不支持两版并存，工业事实提取器也固定输出版本 1。现有来源/页图接口只能校验当前版本，尚不是不可变历史读取。因此下一步应新增独立的准备/登记表和认证 API，保留旧数据及复核/批准快照。

发布需要事务内重验真实账户、完整清单和预期前版/前源 SHA，再以 CAS 追加新版本并推进当前指针；解析期间不持有数据库事务。接入 RAG 前还要建立版本化向量预写/确认、元数据发布及恢复对账，检索只接受当前已发布版本。扫描件需另接有资源槽和子进程时限的 OCR/公式阶段。完整路线见 [多文件并发方案](MULTIFILE_CONCURRENCY.md)。
