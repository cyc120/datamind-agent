# -*- coding: utf-8 -*-
"""生成 交付/DELIVERY_MANIFEST.json（逐文件元数据 + ZIP 应包含内容清单 + 本轮新增证据索引）。

数据来源：
- reports/office_checks.json（页数/幻灯片数/字节数/sha256/结构检查结论；含 round1 保留字段）
- 交付/_build/anonymity_scan.json（PPT 匿名检查）
- 运行 scripts/build_delivery_zip.py --json tmp/wp10/zip_plan.json 得到的 ZIP 计划（dry-run，不落盘）

第二轮（修复轮）改动：
- 04 创新自述已重写（4 页）；本清单同步刷新页数/sha256/结构检查结论；
- 新增 `round2_evidence_index` 一节，登记第一轮新增的报告与结论卡（真实字节数 + sha256）；
- 删除会过期的整句「两份文件均不存在」，改为逐份点名文件真实状态。

运行：cd "/Users/caicai/DataMind Agent" && .venv/bin/python 交付/_build/make_manifest.py
"""
import hashlib
import json
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
DELIV = ROOT / "交付"
PY = str(ROOT / ".venv" / "bin" / "python")

checks = json.loads((ROOT / "reports" / "office_checks.json").read_text(encoding="utf-8"))
anon = json.loads((DELIV / "_build" / "anonymity_scan.json").read_text(encoding="utf-8"))

# 1) 重新跑一次 dry-run 计划（保证数字是本次运行的真实产物；默认不落盘）
plan_json = ROOT / "tmp" / "wp10" / "zip_plan.json"
r = subprocess.run([PY, str(ROOT / "scripts" / "build_delivery_zip.py"), "--json", str(plan_json)],
                   capture_output=True, text=True)
plan = json.loads(plan_json.read_text(encoding="utf-8"))

GEN_NOW = time.strftime("%Y-%m-%dT%H:%M:%S")


def sha256_of(rel):
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def bytes_of(rel):
    p = ROOT / rel
    return p.stat().st_size if p.exists() else None


FILES = [
    {
        "path": "交付/01_设计报告_DataMind_Agent.docx",
        "type": "docx",
        "role": "初赛设计报告 Word 稿（≤20 页，第 1–2 页为核心亮点预览页）",
        "page_count": checks["measured"]["pdf1"]["pages"],
        "bytes": checks["measured"]["docx1"]["bytes"],
        "sha256": checks["sha256"]["docx1"],
        "generated_by": ".venv/bin/python 交付/_build/make_01_design_report.py",
        "structure_check": {"tool": "office-skills/scripts/check_office.py", "exit_code": checks["checks"]["docx1"]["exit_code"], "verdict": checks["checks"]["docx1"]["report"]["verdict"]},
        "checked_at": checks["generated_at"],
        "changed_in_round2": False,
    },
    {
        "path": "交付/01_设计报告_DataMind_Agent.pdf",
        "type": "pdf",
        "role": "初赛设计报告 PDF（LibreOffice Kit 转换）",
        "page_count": checks["measured"]["pdf1"]["pages"],
        "bytes": checks["measured"]["pdf1"]["bytes"],
        "sha256": checks["sha256"]["pdf1"],
        "generated_by": checks["convert_log"]["pdf1"]["cmd"],
        "structure_check": {"tool": "pymupdf 页数读取 + 结构检查在 DOCX 源文件上执行", "exit_code": 0, "verdict": "n/a（PDF 不做 XML 结构检查）"},
        "page_count_measured_by": "pymupdf",
        "changed_in_round2": False,
    },
    {
        "path": "交付/02_技术实现说明书_DataMind_Agent.docx",
        "type": "docx",
        "role": "技术实现说明书 Word 稿（≤30 页；架构/NL2SQL/RAG/多源融合/可解释性/中级任务/测试评估）",
        "page_count": checks["measured"]["pdf2"]["pages"],
        "bytes": checks["measured"]["docx2"]["bytes"],
        "sha256": checks["sha256"]["docx2"],
        "generated_by": ".venv/bin/python 交付/_build/make_02_tech_spec.py",
        "structure_check": {"tool": "office-skills/scripts/check_office.py", "exit_code": checks["checks"]["docx2"]["exit_code"], "verdict": checks["checks"]["docx2"]["report"]["verdict"]},
        "checked_at": checks["generated_at"],
        "changed_in_round2": False,
    },
    {
        "path": "交付/02_技术实现说明书_DataMind_Agent.pdf",
        "type": "pdf",
        "role": "技术实现说明书 PDF",
        "page_count": checks["measured"]["pdf2"]["pages"],
        "bytes": checks["measured"]["pdf2"]["bytes"],
        "sha256": checks["sha256"]["pdf2"],
        "generated_by": checks["convert_log"]["pdf2"]["cmd"],
        "structure_check": {"tool": "pymupdf 页数读取", "exit_code": 0, "verdict": "n/a（PDF 不做 XML 结构检查）"},
        "page_count_measured_by": "pymupdf",
        "changed_in_round2": False,
    },
    {
        "path": "交付/03_答辩演示.pptx",
        "type": "pptx",
        "role": "答辩演示（≤20 页；含演示流程页与失败边界页；不得出现单位/人名）",
        "slide_count": checks["measured"]["pptx"]["slides"],
        "page_count": checks["measured"]["pptx"]["slides"],
        "bytes": checks["measured"]["pptx"]["bytes"],
        "sha256": checks["sha256"]["pptx"],
        "generated_by": ".venv/bin/python 交付/_build/make_03_deck.py",
        "structure_check": {"tool": "office-skills/scripts/check_office.py", "exit_code": checks["checks"]["pptx"]["exit_code"], "verdict": checks["checks"]["pptx"]["report"]["verdict"]},
        "anonymity_check": {
            "method": anon["method"],
            "blocklist": anon["blocklist"],
            "blocklist_hits": len(anon["blocklist_hits"]),
            "person_name_hint_list": anon["person_name_hint_list"],
            "person_name_hint_hits": len(anon["person_name_hint_hits"]),
            "limits": anon["limits"],
            "evidence_file": "交付/_build/anonymity_scan.json",
        },
        "checked_at": checks["generated_at"],
        "changed_in_round2": False,
    },
    {
        "path": "交付/04_突破与创新性自述.pdf",
        "type": "pdf",
        "role": "《突破与创新性自述》独立 PDF（≤5 页；加分申报形态；第二轮重写）",
        "page_count": checks["measured"]["pdf4"]["pages"],
        "bytes": checks["measured"]["pdf4"]["bytes"],
        "sha256": checks["sha256"]["pdf4"],
        "generated_by": checks["convert_log"]["pdf4_round2"]["cmd"],
        "structure_check": {"tool": "pymupdf 页数读取 + pymupdf 文本块边界检查（158 块 / 越界 0）", "exit_code": 0, "verdict": "n/a（PDF 不做 XML 结构检查）"},
        "page_count_measured_by": "pymupdf",
        "changed_in_round2": True,
    },
    {
        "path": "交付/04_突破与创新性自述_DataMind_Agent.docx",
        "type": "docx",
        "role": "创新自述的 DOCX 源文件（第二轮重写；逐份点名证据文件真实状态）",
        "page_count": checks["measured"]["pdf4"]["pages"],
        "bytes": checks["measured"]["docx4"]["bytes"],
        "sha256": checks["sha256"]["docx4"],
        "generated_by": ".venv/bin/python 交付/_build/make_04_innovation.py",
        "structure_check": {"tool": "office-skills/scripts/check_office.py", "exit_code": checks["checks"]["docx4"]["exit_code"], "verdict": checks["checks"]["docx4"]["report"]["verdict"]},
        "checked_at": checks["generated_at"],
        "changed_in_round2": True,
    },
    {
        "path": "docs/DELIVERY_CHECKLIST.md",
        "type": "markdown",
        "role": "官方提交物逐项清单（设计报告/技术说明书/ZIP/PPT/深化文档/加分自述 × 状态 × 路径 × 缺什么）",
        "bytes": bytes_of("docs/DELIVERY_CHECKLIST.md"),
        "sha256": sha256_of("docs/DELIVERY_CHECKLIST.md"),
        "generated_by": "手工撰写（内容基于仓库只读核对）；第二轮同步页数与证据状态",
        "structure_check": {"tool": "n/a", "exit_code": 0, "verdict": "markdown"},
        "changed_in_round2": True,
    },
    {
        "path": "交付/DELIVERY_MANIFEST.json",
        "type": "json",
        "role": "本清单（逐文件元数据 + ZIP 应包含内容清单 + 本轮新增证据索引）",
        "bytes": None,
        "sha256": None,
        "generated_by": ".venv/bin/python 交付/_build/make_manifest.py",
        "structure_check": {"tool": "json.loads 可解析", "exit_code": 0, "verdict": "pass"},
    },
    {
        "path": "scripts/build_delivery_zip.py",
        "type": "python",
        "role": "ZIP 打包脚本（默认 dry-run，不落盘；--write 需再给确认标志）",
        "bytes": bytes_of("scripts/build_delivery_zip.py"),
        "sha256": sha256_of("scripts/build_delivery_zip.py"),
        "generated_by": "新增（WP10）",
        "structure_check": {"tool": "dry-run 实跑", "exit_code": r.returncode, "verdict": "pass" if r.returncode == 0 else "fail"},
    },
    {
        "path": "reports/office_checks.json",
        "type": "json",
        "role": "两个 DOCX + PPTX 的 check_office.py 结果与实测页数/幻灯片数/sha256（含第一轮保留字段）",
        "bytes": bytes_of("reports/office_checks.json"),
        "sha256": sha256_of("reports/office_checks.json"),
        "generated_by": "第二轮：内联合并脚本（重跑 check_office + 重新实测，保留 round1 字段）",
        "structure_check": {"tool": "json.loads 可解析", "exit_code": 0, "verdict": "pass"},
    },
]

# 2) ZIP 应包含内容清单：逐项标注仓库中对应路径
zip_contents = []
for s in plan["sections"]:
    zip_contents.append({
        "id": s["id"],
        "official_requirement": s["requirement"],
        "status": s["status"],
        "repo_paths": s["paths"],
        "counts": s.get("counts"),
        "note": s["note"],
    })

# 3) 本轮新增证据索引（第一轮产出的报告 + 结论卡；真实字节数与 sha256）
EVIDENCE = [
    ("reports/round1_improvement_report_20261010.md", "第一轮提升总报告（含结论卡/实测表/差距表/失败样本）"),
    ("reports/review_round1_evidence.md", "独立复查：证据真实性（123 条数字主张，confirmed 107 / refuted 2 / unverifiable 14）"),
    ("reports/review_round1_regression.md", "独立复查：全量隔离回归（1497 项执行 / 6 失败，逐条列出）"),
    ("reports/review_round1_security.md", "独立复查：安全（阻断 0 / 高 2 / 中 4 / 低 5，每条含文件:行号与复现命令）"),
    ("reports/binding_ablation.json", "WP3 三臂绑定消融（40 例冻结陷阱集；产出晚于第一轮复查冻结，尚未经独立复查）"),
    ("reports/cross_source_offline.json", "WP2 跨源离线评测（21/21 用例、161/161 断言、model_calls=0）"),
    ("reports/vcar_ablation.json", "V-CAR 合成机制消融（16 例；视觉载荷替身，非跨源成绩）"),
    ("reports/cards/wp10_delivery.md", "WP10 结论卡（本清单与交付物的来源卡）"),
    ("reports/cards/wp3_binding.md", "WP3 结论卡（绑定消融数字与诚实边界）"),
    ("reports/cards/r2_delivery_sync.md", "第二轮交付同步结论卡（本轮）"),
]
round2_evidence_index = []
for rel, note in EVIDENCE:
    round2_evidence_index.append({"path": rel, "note": note, "exists": (ROOT / rel).exists(),
                                  "bytes": bytes_of(rel), "sha256": sha256_of(rel)})
# 其余结论卡一并登记（真实字节数与 sha256）
for p in sorted((ROOT / "reports" / "cards").glob("*.md")):
    rel = str(p.relative_to(ROOT))
    if rel in [e["path"] for e in round2_evidence_index]:
        continue
    round2_evidence_index.append({"path": rel, "note": "工作包结论卡", "exists": True,
                                  "bytes": bytes_of(rel), "sha256": sha256_of(rel)})

# 4) 关键证据文件的真实状态（逐份点名，取代旧的整句否定表述）
evidence_status = []
for rel in ["reports/cross_source_offline.json", "reports/binding_ablation.json",
            "reports/cross_source_live.json"]:
    p = ROOT / rel
    evidence_status.append({
        "path": rel,
        "status": "存在" if p.exists() else "本轮未产出",
        "bytes": bytes_of(rel),
        "sha256": sha256_of(rel),
        "reviewer_verdict": {
            "reports/cross_source_offline.json": "confirmed（review_round1_evidence C01–C10；R1 复跑逐位吻合）",
            "reports/binding_ablation.json": "同轮自报、未经独立复查（复查冻结 23:14，该文件 23:36 落盘）",
            "reports/cross_source_live.json": "不适用（无该文件）→ 跨源 live 无成功记录，自述不作主张",
        }[rel],
    })

manifest = {
    "manifest_version": "wp10-delivery-manifest-v2",
    "round": "round2-delivery-sync",
    "generated_at": GEN_NOW,
    "generated_by": ".venv/bin/python 交付/_build/make_manifest.py",
    "baseline_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                                      capture_output=True, text=True).stdout.strip(),
    "api_version": "0.39.0（GET http://127.0.0.1:8000/openapi.json → info.version）",
    "notes": [
        "页数为 pymupdf 读 PDF 的实测值；幻灯片数为 python-pptx 读 PPTX 的实测值；不是估算。",
        "docx/pdf 的字节数在同一轮生成后测量；sha256 覆盖 docx/pdf/pptx 与被登记的证据文件。",
        "本轮（第二轮）未做人工逐页视觉判定：本会话模型不支持图像输入。已用 LibreOffice Kit 渲染 4 页"
        "（missingFonts: []）并用 pymupdf 做文本块边界检查（158 块 / 越界 0）作为部分替代；"
        "渲染图见 tmp/r2_delivery_preview/，供人眼复核。结构检查通过不等于版式无问题。",
        "本轮明确未打 ZIP；ZIP 计划由 scripts/build_delivery_zip.py 的 dry-run 产出。",
        "ZIP 计划的文件数与总字节是「运行该 dry-run 的那一刻」的工作区快照；工作区在测量期间被其他工作包"
        "并发写入，因此该数字会随工作区变化而变，引用时必须带生成时间。",
        "01 设计报告 / 02 技术说明书 / 03 答辩演示的内容在本轮未被改动：其 sha256 与第一轮一致"
        "（reports/office_checks.json 的 sha256 与 sha256_round1 逐键相同）；只有 04 自述被重写。",
        "**字节级可复现性的边界（实测）**：DOCX/PPTX 由 python-docx / python-pptx 写出，其 ZIP 条目携带生成"
        "时刻时间戳（实测 04 DOCX 的 18 个条目为同一 date_time = 生成时刻），因此**重跑生成脚本会得到相同内容但"
        "不同字节**，本清单中的 sha256 是「本次落盘产物」的指纹，**不承诺跨重跑一致**；页数与结构可复现。",
    ],
    "files": FILES,
    "round2_evidence_index": round2_evidence_index,
    "evidence_status": evidence_status,
    "zip": {
        "written_this_round": False,
        "reason_not_written": "WP10 明确要求本轮不打 ZIP，避免把 .venv/.cache/临时产物打进包造成污染。",
        "builder": "scripts/build_delivery_zip.py（默认 dry-run）",
        "builder_dry_run_command": ".venv/bin/python scripts/build_delivery_zip.py --json tmp/wp10/zip_plan.json",
        "builder_dry_run_exit_code": r.returncode,
        "planned_zip_path_if_written": plan["zip_path_if_written"],
        "planned_file_count": plan["stats"]["file_count"],
        "planned_total_bytes": plan["stats"]["total_bytes"],
        "planned_total_mib": plan["stats"]["total_mib"],
        "planned_by_extension": plan["stats"]["by_extension"],
        "excluded_dirs": plan["excluded_dirs"],
        "excluded_prefixes": plan["excluded_prefixes"],
        "untracked_included": plan["untracked_included"],
        "contents_checklist": zip_contents,
    },
    "honesty": [
        "本清单不包含任何官方得分、名次或晋级判断。",
        "「structure_check.verdict=pass」只表示 ZIP/XML 完整、关系可解析、计数与必需文本存在。",
        "PPT 匿名检查基于文本框与表格文本扫描，不覆盖嵌入图片内文字，因此不能声称「绝对无单位名」。",
        "创新自述中的证据状态是逐份点名的：reports/cross_source_offline.json 存在（13 798 B，已由第一轮"
        "复查 supporting）；reports/binding_ablation.json 存在但属同轮自报、未经独立复查；"
        "reports/cross_source_live.json 本轮未产出，自述明确不作 live 主张。",
        "reports/binding_ablation.json 与本清单的 ZIP dry-run 数字均会随工作区/轮次变化，引用时必须带生成时间。",
    ],
}

out = DELIV / "DELIVERY_MANIFEST.json"
out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

# 第二轮：把本清单自身的字节数与 sha256 填回（自引用需要两次写入）
b = out.read_bytes()
h = hashlib.sha256(b).hexdigest()
for f in manifest["files"]:
    if f["path"] == "交付/DELIVERY_MANIFEST.json":
        f["bytes"] = len(b)
        f["sha256"] = h
        f["self_reference_note"] = ("sha256 为第一次写入时的值；因本字段写回会改变文件内容，"
                                    "该值与磁盘上最终文件的 sha256 不同（自引用不可自洽，如实标注）。")
manifest["manifest_sha256_first_write"] = h
manifest["manifest_bytes_first_write"] = len(b)
out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print("WROTE", out, out.stat().st_size, "bytes")
print("files:", len(FILES), "| evidence entries:", len(round2_evidence_index),
      "| zip planned:", plan["stats"]["file_count"], plan["stats"]["total_bytes"])
