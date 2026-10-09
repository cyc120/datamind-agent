"""Single source for the "current" numbers in PROGRESS.md and README.md.

Audit 5.6 (`reports/project_audit_20261009_561316c.md`): the authoritative entry points
had drifted apart — PROGRESS.md claimed API 0.37.0 while the code was 0.39.0, and README.md
still called 959/959 "current" while the top of PROGRESS.md said otherwise. Hand-editing
those numbers failed three times, so they are now derived:

* `scripts/test_isolated.py` writes `reports/latest_regression.json` after every run
  (commit, counts, timestamp);
* the API version is read from the application itself, never typed;
* this script rewrites the references, and `tests/test_status_docs_sync.py` fails when the
  documents disagree with those two sources.

Usage:
    python scripts/status_sync.py            # rewrite the documents in place
    python scripts/status_sync.py --check    # report drift, exit 1 (no writes)
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "reports/latest_regression.json"
PROGRESS = ROOT / "PROGRESS.md"
README = ROOT / "README.md"


def api_version() -> str:
    sys.path.insert(0, str(ROOT))
    from datamind.api import app
    return app.version


def recorded() -> dict:
    if not RECORD.is_file():
        raise SystemExit(f"missing {RECORD.relative_to(ROOT)}; run scripts/test_isolated.py first")
    return json.loads(RECORD.read_text(encoding="utf-8"))


def denominators(summary: dict) -> tuple[str, str]:
    tests = int(summary["tests"])
    return f"{tests}/{tests}", str(tests)


def rewrite(text: str, *, version: str, fraction: str, commit: str, ran_at: str) -> tuple[str, int]:
    """Rewrite every 'current value' reference. Returns the text and the number of edits."""
    edits = 0
    patterns = [
        (r"\| 代码 API \| \*\*[0-9.]+\*\*", f"| 代码 API | **{version}**"),
        (r"\| 完整隔离回归 \| \*\*[0-9]+/[0-9]+\*\*", f"| 完整隔离回归 | **{fraction}**"),
        (r"上表 [0-9]+/[0-9]+ 是\*\*当前唯一权威值\*\*", f"上表 {fraction} 是**当前唯一权威值**"),
        (r"\*\*当前完整隔离回归 [0-9]+/[0-9]+\*\*", f"**当前完整隔离回归 {fraction}**"),
        (r"\| \*\*当前隔离回归 [0-9]+/[0-9]+\*\*（API [0-9.]+）",
         f"| **当前隔离回归 {fraction}**（API {version}）"),
    ]
    for pattern, replacement in patterns:
        text, count = re.subn(pattern, replacement, text)
        edits += count
    baseline = f"| 验证基线 | 提交 `{commit}`，{ran_at} 由 `scripts/test_isolated.py` 在克隆元数据库上运行 |"
    if re.search(r"\| 验证基线 \|", text):
        text, count = re.subn(r"\| 验证基线 \|[^\n]*", baseline, text)
        edits += count
    elif "| 完整隔离回归 |" in text:
        text = re.sub(r"(\| 完整隔离回归 \|[^\n]*\n)",
                      lambda match: match.group(1) + baseline + "\n", text, count=1)
        edits += 1
    return text, edits


def synchronise(check: bool) -> int:
    summary = recorded()
    version = api_version()
    fraction, _ = denominators(summary)
    commit = str(summary.get("commit") or "unknown")
    ran_at = str(summary.get("ran_at") or "")[:19].replace("T", " ")
    if summary.get("failures") or summary.get("errors"):
        # A red run must never become the documented "current" state.
        raise SystemExit("refusing to publish a regression run with failures/errors: " + str(summary))
    drift = []
    for path in (PROGRESS, README):
        original = path.read_text(encoding="utf-8")
        updated, edits = rewrite(original, version=version, fraction=fraction,
                                 commit=commit, ran_at=ran_at)
        if updated != original:
            if check:
                drift.append(path.relative_to(ROOT))
            else:
                path.write_text(updated, encoding="utf-8")
        elif not edits:
            drift.append(path.relative_to(ROOT))
    if check and drift:
        print("status drift in: " + ", ".join(str(item) for item in drift))
        return 1
    if not check:
        print(f"status synced: API {version}, regression {fraction}, commit {commit}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift instead of rewriting")
    return synchronise(parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
