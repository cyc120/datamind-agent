# 结论卡：第二轮交付同步（让交付材料与第一轮复查结论一致）

| 字段 | 值 |
|---|---|
| **WP** | WP10 接力｜第二轮（修复轮）交付同步：重写《突破与创新性自述》+ 同步清单/检查/结论卡 |
| **日期** | 2026-10-09 23:4x CST（北京时间，`date` 机器时钟） |
| **基线提交** | `e4e2ebf`（`cd "/Users/caicai/DataMind Agent" && git rev-parse --short HEAD`，只读，exit 0） |
| **API 版本** | `0.39.0`（`GET http://127.0.0.1:8000/openapi.json` → `info.version`；未重启/未停 8000 服务） |

## 问题

第一轮复查对交付件固定了 **2 条 refuted**（`reports/review_round1_evidence.md` §6.1）：

1. **C130**：`reports/cards/wp10_delivery.md:58` 与自述 docx 第 [22]–[23] 段写「`binding_ablation.json` 与
   `cross_source_offline.json` **两份文件均不存在**」——复查时刻 `cross_source_offline.json` **已存在**（13 798 B，
   22:41 落盘，晚于卡片 22:2x 的写作时刻），只有 `binding_ablation.json` 确实缺失。属**半条时效性错误**。
2. **C131**：卡片称自述中对应数字写「待本轮复查确认」，但自述 docx 全文该短语出现 **0 次**。措辞描述与实际不符
   （实质更保守，未编造数字）。

且第一轮总报告 `reports/round1_improvement_report_20261010.md` §3.3/§4 记录：`binding_ablation.json` 完全缺失 →
「创新加分门槛无法判定 → 不能计入，维持 0」；§2.4「撤回-2」要求**下一轮重生成自述或撤回加分主张**。
本轮任务即为执行该同步：**交付材料必须与复查结论一致，不编数字、不写会立刻过期的话**。

## 做法

1. 先读三份复查报告与第一轮总报告，确认哪些数字被抽查 confirmed、哪些 unverifiable（`--live` 类未复跑、
   `binding_ablation.json` 当时缺失）。
2. 只读核对证据文件真实状态（`ls -l`）：**逐份点名**文件、字节数、记录数、复查判定，并写明核对时刻；
   **不再使用整句笼统否定**。
3. 重写 `交付/_build/make_04_innovation.py` → 重新生成 04 DOCX，再用 **DSH LibreOffice Kit** 转 PDF（4 页 ≤5）。
   自述新增：证据状态总览表、跨源离线数字、「三臂消融硬判定 + 未独立复查」双段、**未产出/不作主张**一节。
4. 重跑 `check_office.py`（两个 DOCX + PPTX + 自述 DOCX）、重测页数/sha256，**合并**写入
   `reports/office_checks.json`（第一轮字段原样保留在 `measured_round1` / `checks_round1` / `sha256_round1`）。
5. 更新 `交付/_build/make_manifest.py`：新增 `round2_evidence_index`（逐份真实字节数 + sha256）与
   `evidence_status`；删除会过期的 honesty 整句。更新 `docs/DELIVERY_CHECKLIST.md` 与本卡、`wp10_delivery.md`。
6. **未做的**：未执行任何 git 写操作、未重启/重建容器、未停 8000 服务、未跑
   `scripts/start_local.sh` 与 `scripts/ingest.py`、未对 `demo_retail` / `demo_industrial` 写任何数据、
   未跑无参数全量回归、未改代码与测试、未改 01/02/03 交付物内容。
7. **唯一的一次删除（如实声明）**：LibreOffice Kit `convert` **拒绝覆盖已存在的输出**，因此重生成前执行了
   `rm -f "交付/04_突破与创新性自述.pdf"`，随后**立即由同一 DOCX 源重新生成**（最终文件存在，4 页，494 530 B）。
   渲染前另用 `rm -rf tmp/r2_delivery_preview` 清空了本轮自建的临时预览目录（该目录为新建，渲染图保留在其中
   供人眼复核）。**未删除任何源代码、文档、数据或其他交付物文件；01/02/03 的 PDF 全程未被触碰。**

## 数据与集

| 项 | 值 | 核对方式 |
|---|---|---|
| 交付物 | 6 份二进制（2 DOCX、3 PDF、1 PPTX）；仅 04 被重写 | `交付/` 逐文件实测 |
| 04 自述页数 | **4 页**（限 ≤5；第一轮 3 页） | `pymupdf` 读 PDF `page_count` |
| `reports/cross_source_offline.json` | **存在**，13 798 B | 已由复查 C01–C10 / R1 复跑 supporting |
| `reports/binding_ablation.json` | **存在**，219 605 B，sha256 前 16 位 `d086dd2e24014965`，`generated_at 2026-10-09T15:36:52+00:00` | 23:36 落盘（复查冻结于 23:14）→ **同轮自报、未经独立复查** |
| `reports/cross_source_live.json` | **本轮未产出**（`ls` exit 1） | 跨源 live 无成功记录 → 自述不作主张 |
| 三臂消融集 | `binding_traps_v1`，40 例（cross_version / unit_mismatch / time_scope_mismatch / doc_db_opposite 各 10） | `dataset` 段；`dataset_file_sha256` 与磁盘一致 |

## 结果（分子分母）

| 检查 | 分子/分母 | 关键输出（原样摘录） | 退出码 |
|---|---:|---|---:|
| 交付物真实存在 | **6/6** | `ls -l 交付/` 六份二进制齐备 | 0 |
| 结构检查（check_office.py） | **4/4 通过** | `docx4 verdict=pass`；`checks=[package pass, contains pass×3]` | 0 |
| 04 自述页数 | **4/5**（限 ≤5） | `pages= 4`；页字符数 1372 / 2017 / 2020 / 1983 | 0 |
| 04 渲染 + 边界检查 | **4/4 页渲染**；越界 **0/158** | render `{"backend":"native","missingFonts":[],"pageCount":4}`；pymupdf 文本块 `overflowing=0` | 0 |
| 01/02/03 内容未改动 | **5/5 sha256 一致** | `sha256[k] == sha256_round1[k]` 对 docx1/pdf1/docx2/pdf2/pptx 全为 `True` | 0 |
| 过时措辞清除 | **0/2 残留** | 04 PDF 全文含「两份文件均不存在」**0** 次、「待本轮复查确认」**0** 次；字面 `**` **0** 次 | 0 |
| 证据状态逐份点名 | **2 存在 / 1 未产出** | 见「数据与集」表；`ls` 对 live 文件退出码 1 | 0 / 1 |
| 页数总览 | **13 / 12 / 4 / 20** | 设计报告 13、技术说明书 12、自述 4、PPT 20（限 20/30/5/20） | 0 |
| 新增证据索引登记 | 注册逐份 `bytes` + `sha256` | `交付/DELIVERY_MANIFEST.json` 新增 `round2_evidence_index` / `evidence_status` | 0 |
| 本轮新增官方得分 | **0** | 本卡只做「交付件 ↔ 复查结论」同步，不作任何得分结论 | — |

## 失败样本（保留，含未解决项）

1. **`reports/cross_source_live.json` 未产出** → 跨源 `--live` 端到端**无成功记录**；自述 §3.5 明确写「本轮未产出，
   不作主张」。**未用任何替代数字填充该空缺。**
2. **`reports/binding_ablation.json` 未经独立复查** → 其 `meets=true` 与全部逐臂数字属同轮自报；自述 §3.3 已用
   独立段落标注「在下一轮复查复跑 `scripts/eval_binding.py --check` 之前，不得把这些数字当作已验证事实引用」。
   **未把 stub 结果说成真实模型准确率。**
3. **`cross_source_offline.json` 的机器可读 `failure_samples` 为空数组** → 6 条设计内 fail-closed 只能从
   `cases[]` / `error_code_counts` 取；自述 §3.2 如实保留该结构性瑕疵（复查 §5 亦已记录）。
4. **人工逐页视觉判定仍未做** → 会话模型 `deepseek-v4.1-flash` **不声明图像输入**，`read_image` 直接报
   `does not declare image input`；故渲染图虽已生成（`tmp/r2_delivery_preview/`），**未由人/AI 逐页看图裁定版式**。
   仅以文本块边界检查（158 块 / 越界 0）部分替代。**未声称「版式无问题」。**
5. **ZIP 仍未打真包**：`scripts/build_delivery_zip.py` 只做 dry-run（`zip_written=false`）；本轮未变更该决定。

## 复现命令

```bash
cd "/Users/caicai/DataMind Agent"

# 1) 只读基线 + 证据状态逐份点名
git rev-parse --short HEAD                                            # → e4e2ebf   (exit 0)
ls -l reports/binding_ablation.json reports/cross_source_offline.json reports/cross_source_live.json
#   → 219605 B / 13798 B 存在；ls: reports/cross_source_live.json: No such file or directory  (exit 1)

# 2) 重写 04 自述 → 转 PDF → 验页数（每条 exit 0）
.venv/bin/python 交付/_build/make_04_innovation.py                    # → WROTE .../04_突破与创新性自述_DataMind_Agent.docx
NODE="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node"
CLI="/Applications/DeepSeek Harness.app/Contents/Resources/app.asar.unpacked/dsh/node_modules/@deepseek-ai/libreoffice-kit/lib/cli.js"
rm -f "交付/04_突破与创新性自述.pdf"
"$NODE" "$CLI" convert --input "交付/04_突破与创新性自述_DataMind_Agent.docx" --output "交付/04_突破与创新性自述.pdf"
#   → {"backend":"native","missingFonts":[],"outputPath":".../04_突破与创新性自述.pdf"}
.venv/bin/python -c "import pymupdf;d=pymupdf.open('交付/04_突破与创新性自述.pdf');print('pages=',d.page_count)"   # pages= 4

# 3) 结构检查（exit 0，verdict=pass）
.venv/bin/python "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/office-skills/scripts/check_office.py" \
  "交付/04_突破与创新性自述_DataMind_Agent.docx" \
  --contains 突破与创新性自述 --contains 本轮未产出 --contains binding_ablation.json \
  --out tmp/r2_delivery/card_check.json

# 4) 刷新机读清单（含 round2_evidence_index / evidence_status）
.venv/bin/python 交付/_build/make_manifest.py
```

**结果文件**：`reports/office_checks.json`（合并版：`measured`/`checks`/`sha256` 为本轮，`*_round1` 保留第一轮）、
`交付/DELIVERY_MANIFEST.json`（`manifest_version = wp10-delivery-manifest-v2`）、`docs/DELIVERY_CHECKLIST.md`、
`reports/cards/wp10_delivery.md`。

## 诚实边界（不能据此声称什么）

1. **不能声称创新点已成立或已领先**：三臂消融的 `meets=true` 仅在**自建冻结陷阱集 + 确定性 stub
   （`model_calls=0`）**定义下成立；`claim_boundary` 原文即「只能支持『在冻结定义下机制有效』的实用创新举证；
   若要主张技术突破领先性，还需要真实模型 + 独立外部数据的证据」。**不得主张业界领先。**
2. **不能声称官方得分/名次/晋级**：官方测试集未提供，本卡与自述都不作得分结论。
3. **不能声称「绑定数字已验证」**：`binding_ablation.json` 未经独立复查；引用须带「同轮自报、stub、自建集」限定。
4. **不能声称「跨源已实现/live 可用」**：`cross_source_live.json` 未产出；安全复查的高危/中危项
   （空 allow-list、时间过滤、白名单）本轮**未修**（不在本卡允许改动范围）。
5. **不能声称「版式无问题」**：结构检查只证 ZIP/XML 完整与必需文本存在；本轮**未做人工逐页视觉判定**。
6. **不能声称 01/02/03 已复核内容**：本轮只验证其 **sha256 未变**，未重读其正文。
7. **无真实视觉模型对比**：`vcar_ablation.json` 的 `local` 臂是机制替身，不得当作视觉模型成绩。
8. **本卡未执行任何 git 写操作**，未删除文件（唯一例外见「做法」第 7 条），未改代码/测试，未改演示库（仅只读查询）。
9. **字节级可复现性的边界（实测）**：DOCX/PPTX 由 python-docx / python-pptx 写出，ZIP 条目携带生成时刻时间戳
   （实测 04 DOCX 的 18 个条目为同一 `date_time` = 生成时刻），因此**重跑生成脚本得到相同内容但不同字节**。
   本卡与机读清单中的 sha256 是「本次落盘产物」的指纹，**不承诺跨重跑一致**；页数、结构检查与文本内容可复现。

## 评分联动

| 官方项 | 本卡作用 | 当前判断 |
|---|---|---|
| C1《突破与创新性自述》（≤5 页） | 4 页 PDF + DOCX 源，证据状态逐份点名；清除 2 条 refuted 的过时表述 | **形态与一致性已具备**（末尾判定权在评委） |
| 创新加分：实用创新与转化价值（0~10） | 提供可复跑的机制对照（错误绑定率 0.800 → 0.000、冲突 P/R 25/25、误修复率 0/9、68 条失败样本） | **材料已备，但证据未经独立复查**；只能按下限叙事 |
| 创新加分：技术突破领先性（0~10） | — | **不申报**（无公开基准、无真实模型、不得称业界领先） |
| E11 技术文档完整性/规范性（10） | 交付件与清单页数/sha256/结构检查均可复核 | **形态可支撑**；人工视觉判定仍缺 |
| D1/D2/D9（提交物形态） | 01/02/03 内容未动、sha256 与第一轮一致 | **未被本轮改坏**（可验证） |

> 本卡为工作产物，不是官方成绩；所有「判断」均为证据充足性判断，不是自评分。**本轮新增官方得分：0。**
