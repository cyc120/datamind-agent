"""Report whether the optional runtime dependency sets are importable.

Callers (scripts/start_local.ps1, CI) use the exit code to decide whether to
install `requirements-sql.txt` / `requirements-ocr.txt`. Kept as a file rather
than an inline `python -c` because Windows PowerShell 5.1 mangles quotes and
semicolons in native argument lists.

Usage:
    python scripts/check_env.py sql   -> exit 0 when the SQL set is importable
    python scripts/check_env.py rag   -> also requires the pinned RAG/OCR set
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

SQL_MODULES = ["fastapi", "uvicorn", "psycopg2", "sqlglot"]
RAG_MODULES = ["pymupdf", "fastembed", "qdrant_client", "rapidocr", "onnxruntime"]
RAG_VERSIONS = {"qdrant-client": "1.12.", "rapidocr": "3.9.2"}


def check(modules: list[str]) -> list[str]:
    problems: list[str] = []
    for name in modules:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001 - report every failure, not the first
            problems.append(f"{name}: {type(exc).__name__}: {exc}")
    return problems


def check_versions(expected: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for package, prefix in expected.items():
        try:
            found = version(package)
        except PackageNotFoundError:
            problems.append(f"{package}: not installed")
            continue
        if not found.startswith(prefix):
            problems.append(f"{package}: {found} does not start with {prefix}")
    return problems


def main(argv: list[str]) -> int:
    stage = argv[1] if len(argv) > 1 else "sql"
    modules = list(SQL_MODULES)
    versions: dict[str, str] = {}
    if stage == "rag":
        modules += RAG_MODULES
        versions = RAG_VERSIONS

    problems = check(modules) + check_versions(versions)
    if problems:
        for problem in problems:
            print(f"MISSING {problem}")
        return 1
    print(f"{stage} dependencies OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
