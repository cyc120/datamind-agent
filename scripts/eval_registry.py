# -*- coding: utf-8 -*-
"""WP1-a 评测登记 CLI（离线；对 datamind_meta 只读，不调用上游模型）。

用法::

    # 冻结（写 data/evaluation/registry/<suite>_v1.json；已存在则拒绝覆盖）
    .venv/bin/python scripts/eval_registry.py freeze --suite nl2sql
    .venv/bin/python scripts/eval_registry.py freeze --suite rag
    .venv/bin/python scripts/eval_registry.py freeze --suite robustness

    # 校验（登记文件 vs 库内题面；不一致给出逐字段 diff 并退出码 1）
    .venv/bin/python scripts/eval_registry.py check
    .venv/bin/python scripts/eval_registry.py check --suite nl2sql

    # 统一报告 schema 检查（逐题 status/sql/rows/citations/verdict/failure_reason
    # + 汇总 accuracy/refusal_rate/failure_counts；零分母必须是 'N/A'）
    .venv/bin/python scripts/eval_registry.py schema --report reports/nl2sql.json

退出码：0 通过；1 校验不一致/不符合 schema；2 用法或文件缺失/已存在。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS = ("reports/nl2sql.json", "reports/rag.json", "reports/robustness.json")


def _load_registry():
    """从 data/ 下按文件路径加载库模块（data 不是 Python 包）。"""
    path = ROOT / "data/evaluation/registry/registry.py"
    spec = importlib.util.spec_from_file_location("wp1a_eval_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = _load_registry()


def _git_commit() -> str | None:
    """只读读取当前提交（失败不致命：登记体的 digest 不依赖 commit）。"""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              check=True, capture_output=True, text=True).stdout.strip() or None
    except Exception:
        return None


def cmd_freeze(args) -> int:
    path, payload = registry.freeze(args.suite, dsn=args.dsn, version=args.version,
                                    out_dir=args.out_dir, commit=_git_commit(),
                                    created_at=args.created_at)
    print(f"已冻结 {args.suite}：{path}")
    print(f"  suite_version={payload['suite_version']} data_version={payload['data_version']} "
          f"cases={payload['case_count']} digest={payload['digest']}")
    return 0


def cmd_check(args) -> int:
    out_dir = Path(args.out_dir or registry.REGISTRY_DIR)
    suites = args.suite or list(registry.SUITES)
    targets = []
    missing = []
    for suite in suites:
        candidates = sorted(out_dir.glob(f"{suite}_v*.json"))
        if candidates:
            targets.extend(candidates)
        else:
            missing.append(suite)
    if missing:
        print(f"尚未冻结，无登记文件：{', '.join(missing)}（{out_dir}）", file=sys.stderr)
    if not targets:
        return 2
    failed = False
    for path in targets:
        try:
            summary = registry.check_registry(path, dsn=args.dsn)
        except registry.RegistryMismatch as error:
            failed = True
            print(f"[不一致] {path}")
            print(registry.render_diff(error.diff))
        else:
            print(f"[一致] {path.name}: suite={summary['suite']} cases={summary['cases']} "
                  f"digest={summary['digest']}")
    return 1 if failed else 0


def cmd_schema(args) -> int:
    paths = args.report or [ROOT / name for name in DEFAULT_REPORTS]
    failed = False
    for path in paths:
        path = Path(path)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            failed = True
            print(f"[缺失] {path}：报告文件不存在")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            failed = True
            print(f"[非法 JSON] {path}：{error}")
            continue
        problems = registry.validate_report(payload)
        if problems:
            failed = True
            print(f"[不符合 schema {registry.REPORT_SCHEMA_VERSION}] {path}（{len(problems)} 项）")
            for problem in problems[:20]:
                print(f"  - {problem}")
            if len(problems) > 20:
                print(f"  - ...另有 {len(problems) - 20} 项未展开")
        else:
            print(f"[符合 schema] {path}")
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="从库冻结一套题到登记文件（已存在则拒绝覆盖）")
    freeze.add_argument("--suite", required=True, choices=list(registry.SUITES))
    freeze.add_argument("--version", default="v1", help="登记版本号（默认 v1）；改动题面请用新版本号")
    freeze.add_argument("--dsn", help=f"元数据库 DSN（默认只读 {registry.DEFAULT_DSN}）")
    freeze.add_argument("--out-dir", help=f"登记目录（默认 {registry.REGISTRY_DIR}）")
    freeze.add_argument("--created-at", help="覆盖 created_at（测试/复现用；不进 digest）")
    freeze.set_defaults(func=cmd_freeze)

    check = sub.add_parser("check", help="校验登记文件与库内题面一致")
    check.add_argument("--suite", action="append", choices=list(registry.SUITES))
    check.add_argument("--dsn")
    check.add_argument("--out-dir")
    check.set_defaults(func=cmd_check)

    schema = sub.add_parser("schema", help="检查评测脚本输出 JSON 是否符合统一 schema")
    schema.add_argument("--report", action="append", type=Path,
                        help=f"报告路径，可重复（默认 {', '.join(DEFAULT_REPORTS)}）")
    schema.set_defaults(func=cmd_schema)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except registry.RegistryExistsError as error:
        print(str(error), file=sys.stderr)
        return 2
    except registry.RegistryError as error:
        print(f"登记错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
