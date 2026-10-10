# WP5b · B8 文档复杂度评估（分数 → 策略 → 实际解析成本）

- **WP**：WP5b（B8 文档复杂度评估；WP5 的第二项）
- **日期**：2026-10-10
- **基线提交**：`e4e2ebf`
- **API 版本**：`0.39.0`

## 问题（一句话）

审计 §6 判定 B8 为「尚无完整能力证据 / 未实现」：全仓库没有可解释的复杂度评分，更没有「评分 →
处理策略 → 实际解析成本」的对照，静态人工 difficulty 标签不算完成。

## 做法（改了什么，不做什么）

新增 `datamind/complexity.py`：

1. **9 个可解释维度**（权重合计 1.00）：页数与字数 0.10、图片面积占比 0.10、表格数与跨页续表 0.12、
   公式/符号密度 0.12、旋转与倾斜页比例 0.08、OCR 置信度分布 0.12、标题层级缺失率 0.10、
   编码/字体混杂 0.10、扫描件比例 0.16。每维给出 `raw` / `score` / `weight` / `contribution` 与规则说明，
   总分 = 加权和 × 100（`band`：low <25 / medium <50 / high <75 / very_high）。
2. **缺数据不伪造**：观测不到的维度标 `available=False`（如原生文本 PDF 没有 OCR 逐行置信度），
   总分在可用权重上重新归一化并报告 `coverage`；不把未知按 0 分或满分算。
3. **策略映射是确定性的**：`policy_for()` → `{ocr_enabled, max_blocks, retrieval_candidates,
   require_human_review}`。预算只看档位；OCR 开关 =「档位 high/very_high **或** 无文本层页比例 ≥0.3」
   且确实存在无文本层页面（纯数字化 PDF 不被强制 OCR）。两个触发条件都写进 `reasons`。
4. **真的被用于一条路径**：`scripts/eval_complexity.py` 用这条策略去**门控真实解析**
   （`ocr_enabled` 关时像素 OCR 被策略拒绝并留下稳定码 `OCR_DISABLED_BY_POLICY`，块数按 `max_blocks`
   截断），并跑一条 `no_policy_ocr_off` 对照臂回答「没有策略会怎样」。两臂都在临时目录里解析
   （`PAGE_IMG_DIR` 重定向），未写演示对象存储。
5. 解析后把实测 OCR 逐块置信度与公式候选数回填，重算一次「解析后分数」，用于说明哪些维度
   只能在解析后才可得。
6. **不做**：不改摄取主流程（`ingestion/*` 不在本 WP 允许改动集内），因此生产上传路径尚未调用
   `assess_pdf()`；接入方式是「新增纯函数供调用 + 在评测里对照调用」，见下方诚实边界。

## 数据与集

`data/evaluation/complexity_v1.json`（**先冻结期望，后评测**，共 26 份材料）：

- 12 份真实 PDF：`data/knowledge_base/`（含 2 份扫描件、1 份公式文档、1 份跨页续表长文档）
- 14 条 OHR-Bench 页面记录：`data/ohr_bench_samples/*.json`（只有 `gt_text`/`noise_text`，无几何与置信度）
- 期望依据写在文件里：按 `data/knowledge_base/manifest.json` 声明的文档类型与页数/字数规模冻结；
  扫描件期望 `high`，跨页续表长文档期望 `medium`，OHR 页 `gt_chars ≥ 8000` 期望 `medium`、其余 `low`。

## 结果（分子分母）

**隔离回归**（`test_complexity.py`）：`19/19` 通过，`failures=0, errors=0`（见复现命令 1）。

**评测**（`reports/complexity_v1.json`，26/26 份材料全部跑完，`failures: 0`）：

| 指标 | 数值 |
|---|---:|
| 材料总数 / 评测失败 | 26 / 0 |
| 复杂度档位与期望一致 | 20/26（`expectation_mismatches` 6 条，全部保留） |
| PDF 策略期望（ocr_enabled）一致 | 12/12 |
| Spearman(解析前分数, 实测解析耗时 ms) | **ρ = 0.641**（n=12） |
| Spearman(解析前分数, ms/页) | ρ = 0.620（n=12） |
| 判定门槛 | \|ρ\| ≥ 0.50 → **相关性成立** |
| 档位均值耗时 | low 档 339.4 ms（10 份）；medium 档 1646.8 ms（2 份）→ **4.9 倍差** |

**分数 → 策略 → 实际成本**（节选，全部 12 份 PDF 明细在报告 `documents[]`）：

| 文档 | 声明类型 | 分数/档位 | 策略 | 实测耗时 | 块/字数 | 解析状态 |
|---|---|---:|---|---:|---|---|
| kb-02 | 原生文本白皮书 | 1.87 / low | OCR off，400 块，候选 5 | 45.8 ms | 22 / 479 | parsed |
| kb-05 | 3 个 LaTeX 公式 | 5.97 / low | OCR off，400 块 | **2342.5 ms** | 24 / 620 | parsed |
| kb-07 | 扫描件（旋转+噪声） | 45.16 / medium | OCR on，600 块 | 1758.8 ms | 11 / 425 | partial |
| kb-08 | 扫描件（负角旋转） | 45.16 / medium | OCR on，600 块 | 1534.7 ms | 11 / 352 | partial |

**策略 vs 无策略对照**（`policy_vs_no_policy`）：策略把 OCR 打开的 2 份扫描件，若按
`no_policy_ocr_off` 一律关 OCR，则 **字数 425 → 0、352 → 0**（页 `OCR_DISABLED_BY_POLICY`），
即无策略路径会**整页丢文本**；代价是 1758.8 ms / 1534.7 ms（对照臂 26.5 ms / 26.8 ms）。

## 失败样本（全部保留，不删不改期望）

`expectation_mismatches` 6 条（分子 6 / 分母 26）：

1. `kb-07`/`kb-08` 期望 `high`、实际 `medium`（45.16 分）——扫描件被识别为「需 OCR」是对的
   （`ocr_enabled=true` 与期望策略一致），但**分档上限偏低**：扫描页 1.0、图片占比 1.0 已经满格，
   而 OCR 置信度维度因解析前无数据被排除，导致 45.16 分卡在中档。这是分档阈值的一次真实偏差。
2. `kb-06` 期望 `medium`、实际 `low`（10.87 分）——24 行跨页续表只落在 3 页文档上，
   表格维度 0.4167 × 0.12 不足以抬档，说明该维度权重对「小页数+高表格密度」不敏感。
3. `ohr-005`（4421 字）与 `ohr-010`（766 字）期望 `low`、实际 `medium`（32.49 / 28.31）——
   OHR 观测只有 5 个维度可用（`coverage=0.54`），可用维度上的高分在重新归一化后被放大。
4. `ohr-011`（8316 字）期望 `medium`、实际 `low`（4.34）——该页表格/公式/标题信号都很弱，
   字数维度（0.10 权重、40000 字饱和）单独无法把 8 千字推到中档；与第 3 条合看，
   说明**重新归一化会同时放大和压缩不同文档的分数**，这是覆盖率不足的代价。

另外 `kb-07`/`kb-08` 的 `parse_status=partial`：扫描页仍存在未解析图块（既有行为，
非本次改动引入），如实记录。

## 复现命令

```bash
# 1) 隔离回归（19 用例）
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_complexity.py
#    → exit 0；{"tests": 19, "failures": 0, "errors": 0, "commit": "e4e2ebf", ...}

# 2) 复杂度评测（现场解析 12 份 PDF + 14 条 OHR，含无策略对照臂）
cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/eval_complexity.py
#    → exit 0；{"documents": 26, "correlation": "成立", "spearman_elapsed_ms": {"n": 12, "rho": 0.641},
#               "band_mean_elapsed_ms": {"low": 339.4, "medium": 1646.8}, "mismatches": 6, "failures": 0}
```

## 诚实边界（不能据此声称什么）

- **不能说「复杂度分已经驱动生产摄取」**：生产入口仍是 `ingestion/ingest.py` 的既有路径，
  `assess_pdf()`/`policy_for()` 目前由 `scripts/eval_complexity.py` 调用并门控评测用解析。
  把策略接到上传/摄取主流程需要改 `ingestion/*`，不在本 WP 允许改动的文件集内，**未完成**。
- **不能说相关性很强**：ρ=0.641 刚过 0.50 门槛，n=12，且明显被两个离群点拉扯——
  `kb-05`（公式文档，分数 5.97 却 2342.5 ms）与 `kb-01..04/06/09..12`（分数 1.5–18.9、耗时 21.8–288.3 ms）。
  公式识别成本没有被「公式符号密度」这个维度充分刻画（符号少但识别贵）。
- **不能说分档阈值已校准**：6/26 期望不符，扫描件只到中档、跨页续表长文档只到低档。
  这些是校准证据，不是「模型不准」的结论；本 WP 不做阈值调参以避免对期望过拟合。
- **不能说 OHR 页的复杂度分可比**：OHR 记录只有 5 个维度可用（coverage 0.54），
  分数与 PDF 分数不同分母，不能横向比较。
- **不能说「无一失败」**：`failures: 0` 只表示没有解析异常/空文本，6 条期望不符单列在
  `expectation_mismatches`，没有被算进通过。
- 耗时是单次现场测量（本机、容器内 PostgreSQL 与本地 OCR），不是并发或长期稳定性数据。
- **共享产物副作用**：`scripts/test_isolated.py` 会把 `reports/latest_regression.json` 改写成本次
  运行的计数；本卡的 `19/19` 以命令 1 的输出为准，不要用该共享文件反推。

## 评分联动

- 官方 **E8/F8 中级任务（B8 文档复杂度评估）**：给出可解释 9 维评分 + 确定性策略 + 真实解析成本对照，
  从审计的「未实现」变为「有实现、有对照、有失败样本」。
- **F7 低质量数据鲁棒性**：扫描件比例与 OCR 置信度维度为「质量影响处理策略」提供了可复算的接口；
  本次只证明策略能改变解析行为（OCR 开关导致 425/352 字文本得失），未接入生产链路。
- **E7/F6 效率**：提供了「复杂度 → 块预算/检索候选数」的成本控制旋钮与一次真实成本对照。
