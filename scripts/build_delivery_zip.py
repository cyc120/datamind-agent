#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataMind Agent 决赛 ZIP 交付包构建脚本（**默认 dry-run，不落盘**）。

设计原则
--------
1. 默认只「列出将包含的文件与总大小」，不创建任何 ZIP（WP10 明确要求本轮不打 ZIP，
   避免把 .venv / .cache / 临时产物打进包造成污染）。
2. 逐项对应官方 ZIP 提交要求：完整源代码（含目录结构说明与 README）/ 数据库 schema /
   知识库文档样本（不少于 10 份、涵盖不同类型格式）/ 模型权重文件（若适用）/
   运行环境配置说明（依赖包列表及版本号）/ 可执行 Demo 的快速启动脚本。
3. 类型缺口如实输出（缺什么写什么），不伪造「已涵盖不同类型格式」。
4. 真实打包必须显式加 --write，并强制两次确认标志（--i-know-this-writes-a-zip）。

用法
----
  python scripts/build_delivery_zip.py                 # dry-run，列出文件与总大小
  python scripts/build_delivery_zip.py --json out.json  # dry-run + 机读清单
  python scripts/build_delivery_zip.py --strict         # 有任何缺口时退出码 2
  python scripts/build_delivery_zip.py --write --i-know-this-writes-a-zip   # 真正打包（本轮未使用）
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "datamind_agent_delivery.zip"

# 绝不进入交付包的目录/文件名（防污染）
EXCLUDE_DIRS = {
    ".git", ".venv", ".venv-formula", ".cache", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "tmp", "output", "交付/_build",
    ".repo-state", ".uploads",
}
EXCLUDE_GLOBS = [
    "*.pyc", "*.pyo", "*.log", "*.tmp", "*.bak", "*.swp", ".DS_Store",
    "*.sqlite", "*.db", "*.zip", "*.tar", "*.gz",
]
# 显式排除：禁止把演示数据库、容器卷、上传临时件、私有大文件打进包
EXCLUDE_PREFIX = [
    "data/object_store/page_images",   # 页面图像缓存（可由源文件重建）
    "data/uploads/",                   # 上传暂存区（开发临时件，非交付样本）
]


def is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[: i + 1])
        if prefix in EXCLUDE_DIRS:
            return True
    for pref in EXCLUDE_PREFIX:
        if rel.startswith(pref):
            return True
    for pat in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(parts[-1], pat):
            return True
    return False


def git_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT),
                             capture_output=True, text=True, check=True)
    except Exception:
        return []
    return [p for p in out.stdout.split("\0") if p]


def walk_files() -> list[str]:
    found = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), ROOT).replace(os.sep, "/")
            if not is_excluded(rel):
                found.append(rel)
    return sorted(found)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> dict:
    tracked = git_files()
    source_mode = "git ls-files" if tracked else "os.walk（git 不可用）"
    untracked_extra = [f for f in walk_files() if f not in set(tracked)]
    files = sorted(set(tracked + walk_files()))
    files = [f for f in files if not is_excluded(f) and (ROOT / f).is_file()]

    # ---- 官方 ZIP 要求的逐项清单 ----
    kb_pdfs = sorted(p for p in files if p.startswith("data/knowledge_base/") and p.endswith(".pdf"))
    kb_main_pdfs = [p for p in kb_pdfs if "/native_counterparts/" not in p]
    kb_native_pdfs = [p for p in kb_pdfs if "/native_counterparts/" in p]
    ohr_json = sorted(p for p in files if p.startswith("data/ohr_bench_samples/") and p.endswith(".json"))
    cad_dxf = sorted(p for p in files if p.startswith("data/cad_demo/") and p.endswith(".dxf"))
    page_images = sorted(p for p in files if p.startswith("data/object_store/page_images/"))
    # Office 样本：只统计 data/ 下的（交付/ 下的 DOCX 是文档交付物，不是知识库样本）
    office_samples = [p for p in files
                      if p.startswith("data/") and p.endswith((".docx", ".pptx", ".xlsx"))]
    sections = [
        {
            "id": "source_code",
            "requirement": "完整项目源代码（含清晰的目录结构说明和 README 文件）",
            "status": "satisfied",
            "paths": ["datamind/", "ingestion/", "web/", "scripts/", "tests/", "deploy/",
                      "README.md", "PROGRESS.md", "requirements.txt"],
            "note": "README.md 含目录树与快速开始；PROGRESS.md 为唯一权威状态块。",
        },
        {
            "id": "db_schema",
            "requirement": "数据库 schema 定义文件",
            "status": "satisfied",
            "paths": ["db/migrations/001_document_facts.sql … 018_upload_claim_generation.sql",
                      "db/chinook/Chinook_PostgreSql.sql", "deploy/init/*.sql"],
            "note": "迁移 001–018 共 18 个文件；deploy/init 为容器初始化 DDL（与迁移并存，两者等效性未在空库验证）。",
        },
        {
            "id": "kb_samples",
            "requirement": "知识库文档样本（不少于 10 份，涵盖不同类型文档格式）",
            "status": "partial",
            "paths": kb_pdfs + ohr_json + cad_dxf + office_samples,
            "counts": {"knowledge_base_pdf_main": len(kb_main_pdfs),
                       "knowledge_base_pdf_native_counterparts": len(kb_native_pdfs),
                       "ohr_bench_json": len(ohr_json),
                       "cad_demo_dxf": len(cad_dxf),
                       "office_docx_pptx_xlsx": len(office_samples)},
            "note": ("数量：12 份演示知识库 PDF（多级目录/表格密集/公式/24 行跨页续表/规章/FAQ/手册/数据字典，含 2 份扫描件）"
                     "+ 2 份对应原生版 + 15 份 OHR-Bench JSON 语料 + 14 份 DXF 验收材料，远超「≥10 份」。"
                     "类型（按扩展名实测）：PDF（原生 + 扫描）、JSON、DXF、DOCX、XLSX、PPTX 六类。"
                     "其中多类型真实样本位于 data/qa_samples/（01–11：原生 PDF 2、扫描 PDF 2、DOCX 2、XLSX 2、PPTX 2、DXF 1）"
                     "与 data/evaluation/extensible_field_v1/materials/（DOCX/XLSX 各 2）。"
                     "**打包前须确认**：data/qa_samples/ 与 extensible_field_v1/materials/ 在本轮工作区中为未跟踪文件"
                     "（git ls-files 无输出），打包方需先决定是否显式纳入版本控制；"
                     "data/uploads/development/ 下的上传临时件不应进包。"),
        },
        {
            "id": "model_weights",
            "requirement": "模型权重文件（若适用）",
            "status": "satisfied_by_list",
            "paths": ["docs/MODEL_WEIGHTS.md"],
            "note": "仓库不含权重（.cache/ 被忽略）；按官方「列明获取/缓存/网络依赖」提供逐项清单（路径/体积/SHA256/获取脚本/缺失降级）。",
        },
        {
            "id": "dependencies",
            "requirement": "运行环境配置说明（依赖包列表及版本号）",
            "status": "satisfied",
            "paths": ["requirements.txt", "requirements.lock", "requirements-sql.txt", "requirements-rag.txt",
                      "requirements-ocr.txt", "requirements-formula.txt", "requirements-office.txt",
                      "requirements-cad.txt"],
            "note": "requirements.lock 固定 65 个版本，并有 tests/test_dependency_lock.py 守护「锁与实际安装一致」。",
        },
        {
            "id": "quickstart",
            "requirement": "可执行 Demo 的快速启动脚本",
            "status": "satisfied",
            "paths": ["scripts/start_local.sh", "scripts/start_api_background.py"],
            "note": "本轮未在干净空库环境演练启动（禁止重建容器/库），不主张「新环境已验证可启动」。",
        },
        {
            "id": "deliverables",
            "requirement": "初赛/决赛文档与演示材料（随包可选）",
            "status": "partial",
            "paths": ["交付/01_设计报告_DataMind_Agent.docx", "交付/01_设计报告_DataMind_Agent.pdf",
                      "交付/02_技术实现说明书_DataMind_Agent.docx", "交付/02_技术实现说明书_DataMind_Agent.pdf",
                      "交付/03_答辩演示.pptx", "交付/04_突破与创新性自述.pdf",
                      "交付/DELIVERY_MANIFEST.json", "docs/DELIVERY_CHECKLIST.md"],
            "note": "决赛 ≤50 页深化稿未做；本脚本默认不打 ZIP。",
        },
    ]

    by_sec = {s["id"]: s for s in sections}
    included = set()
    for s in sections:
        for p in s["paths"]:
            if isinstance(p, str) and (ROOT / p).exists() and not p.endswith("/"):
                included.add(p)
    # 目录式 source_code 条目展开为实际文件（受排除规则约束）
    for pref in ("datamind/", "ingestion/", "web/", "scripts/", "tests/", "deploy/", "db/", "docs/"):
        for f in files:
            if f.startswith(pref):
                included.add(f)
    for f in files:
        if (f.startswith("交付/") or f.endswith((".md", ".txt", ".lock", ".sql"))):
            if not f.startswith("交付/_build"):
                included.add(f)
    included = sorted(p for p in included if (ROOT / p).is_file())

    total = sum((ROOT / p).stat().st_size for p in included)
    by_ext: dict[str, int] = {}
    for p in included:
        ext = Path(p).suffix.lower() or "(无扩展名)"
        by_ext[ext] = by_ext.get(ext, 0) + 1

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo_root": str(ROOT),
        "source_listing": source_mode,
        "dry_run": True,
        "zip_written": False,
        "zip_path_if_written": str(DEFAULT_OUT),
        "excluded_dirs": sorted(EXCLUDE_DIRS),
        "excluded_globs": EXCLUDE_GLOBS,
        "excluded_prefixes": EXCLUDE_PREFIX,
        "sections": sections,
        "untracked_included": sorted(
            f for f in included if f in set(untracked_extra) and not f.startswith("交付/")),
        "stats": {
            "file_count": len(included),
            "total_bytes": total,
            "total_mib": round(total / 1048576, 2),
            "by_extension": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
            "tracked_file_count": len(tracked),
        },
        "files": [
            {"path": p, "bytes": (ROOT / p).stat().st_size,
             "sha256": sha256(ROOT / p) if (ROOT / p).stat().st_size < 8 * 1024 * 1024 else "skipped(>8MiB)"}
            for p in included
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="DataMind Agent 决赛 ZIP 交付包构建（默认 dry-run）")
    ap.add_argument("--json", dest="json_out", default=None, help="把机读清单写到该路径")
    ap.add_argument("--strict", action="store_true", help="存在 partial 项时退出码 2")
    ap.add_argument("--write", action="store_true", help="真正写出 ZIP（需再给确认标志）")
    ap.add_argument("--i-know-this-writes-a-zip", dest="confirm", action="store_true",
                    help="确认真的写盘；WP10 本轮不使用")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="ZIP 输出路径（仅在 --write 时生效）")
    args = ap.parse_args()

    plan = collect()
    print(f"[dry-run] 清单来源: {plan['source_listing']}")
    print(f"[dry-run] 将包含 {plan['stats']['file_count']} 个文件、"
          f"{plan['stats']['total_bytes']} 字节（{plan['stats']['total_mib']} MiB）")
    print(f"[dry-run] 排除目录: {', '.join(plan['excluded_dirs'])}")
    print("[dry-run] 官方 ZIP 要求逐项：")
    for s in plan["sections"]:
        mark = {"satisfied": "OK  ", "satisfied_by_list": "OK*", "partial": "部分"}.get(s["status"], "??")
        cnt = s.get("counts") or {}
        cnt_s = (" " + json.dumps(cnt, ensure_ascii=False)) if cnt else ""
        print(f"  [{mark}] {s['requirement']}{cnt_s}")
        if s["status"] == "partial":
            print(f"        缺口/说明: {s['note']}")
    print("[dry-run] 按扩展名统计（前 12）：")
    for ext, n in list(plan["stats"]["by_extension"].items())[:12]:
        print(f"         {ext:<14} {n}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[dry-run] 机读清单已写: {args.json_out}")

    if args.write:
        if not args.confirm:
            print("拒绝写盘：需要同时给出 --i-know-this-writes-a-zip（防误用）。", file=sys.stderr)
            return 3
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in plan["files"]:
                zf.write(ROOT / item["path"], arcname=item["path"])
        print(f"[write] 已写出 {out}（{out.stat().st_size} 字节）")
        return 0

    if args.strict and any(s["status"] == "partial" for s in plan["sections"]):
        print("[strict] 存在 partial 项，退出码 2", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
