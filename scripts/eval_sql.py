"""Run the versioned NL2SQL cases and compare execution results to gold SQL."""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from datamind.catalog import Catalog
from datamind.sql_engine import _execute, ask, settings
from datamind.sql_guard import guard_sql


def norm(value):
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(Decimal(str(value)).normalize())
    return str(value)


def includes(actual_row, gold_row):
    actual = [norm(x) for x in actual_row]
    target = [norm(x) for x in gold_row]
    pos = 0
    for value in actual:
        if pos < len(target) and value == target[pos]:
            pos += 1
    return pos == len(target)


def same_result(actual, gold, ordered):
    if len(actual) != len(gold):
        return False
    if ordered:
        return all(includes(a, g) for a, g in zip(actual, gold))
    # Grouped queries without ORDER BY have no meaningful row order.
    remaining = list(actual)
    for row in gold:
        match = next((i for i, candidate in enumerate(remaining) if includes(candidate, row)), None)
        if match is None:
            return False
        remaining.pop(match)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Save per-case JSON results")
    parser.add_argument("--suite", default="nl2sql", choices=["nl2sql", "robustness"],
                        help="Which evaluation suite to run")
    args = parser.parse_args()
    cfg = settings()
    if not cfg.get("NEW_API_KEY"):
        print("未配置模型 API 密钥：请在 http://127.0.0.1:8000 的「模型接入」中保存，"
              "或设置 NEW_API_KEY 环境变量后重试。")
        raise SystemExit(2)
    meta_dsn = cfg.get("DATAMIND_META_DSN") or "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"
    with psycopg2.connect(meta_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT case_id, question, gold_sql, clarification_label, category "
                    "FROM evaluation_cases WHERE suite=%s ORDER BY case_id", (args.suite,))
        cases = cur.fetchall()
    catalogs = {s: Catalog(meta_dsn, s) for s in ("demo_retail", "chinook")}
    records = []
    for cid, question, gold_sql, clarification_label, category in cases:
        source = "chinook" if cid in {"nl2sql-011", "nl2sql-012"} else "demo_retail"
        result = ask(question, source)
        expected = ("clarification_required"
                    if clarification_label in {"needed", "abstain"} else "completed")
        match = result["status"] == expected
        if category == "空结果":
            # 「无数据」考的是系统能否说明结果为空：0 行或整行 NULL 都算对，
            # 但必须由系统显式标记 EMPTY_RESULT，不能靠投影形状碰巧相等。
            rows = result.get("rows") or []
            is_empty = not rows or all(value is None for row in rows for value in row)
            match = (result["status"] == "completed" and is_empty
                     and "EMPTY_RESULT" in (result.get("validator_findings") or []))
        elif gold_sql and result["status"] == "completed":
            safe_gold = guard_sql(gold_sql, catalogs[source])
            gold = _execute(safe_gold, catalogs[source], cfg)
            match = same_result(result["rows"], gold["rows"], "ORDER BY" in gold_sql.upper())
        record = {"case_id": cid, "status": result["status"], "match": match,
                  "error_code": result.get("error_code"), "sql_hash": result.get("sql_hash"),
                  "sql": result.get("sql"), "gold_sql": gold_sql, "attempts": result.get("attempts"),
                  "row_count": len(result.get("rows") or []),
                  "rows_preview": (result.get("rows") or [])[:3],
                  "source_snapshot_id": result.get("source_snapshot_id")}
        records.append(record)
        detail = "" if match else " | SQL=" + " ".join((result.get("sql") or "-").split())[:160]
        print(f"{cid}: {'PASS' if match else 'FAIL'} "
              f"({result['status']}{', ' + result['error_code'] if result.get('error_code') else ''}{detail})",
              flush=True)
    passed = sum(r["match"] for r in records)
    print(f"Execution match: {passed}/{len(records)}; model={cfg.get('NEW_API_MODEL', 'deepseek-flash')}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"cases": records, "passed": passed, "total": len(records)},
                                          ensure_ascii=False, indent=2, default=str))
    if passed != len(records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
