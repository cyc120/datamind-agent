"""Evaluate deployed Catalog retrieval and approved Join Graph against reviewed labels."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from datamind.catalog import Catalog


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "data/evaluation/schema_linking_v1.json"
DEFAULT_DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"


def _path_tables(start: str, edges: list) -> list[str]:
    tables = [start]
    for edge in edges:
        current = tables[-1]
        if edge.from_table == current:
            tables.append(edge.to_table)
        elif edge.to_table == current:
            tables.append(edge.from_table)
        else:
            raise ValueError(f"Discontinuous approved path at {current}: {edge.relation_id}")
    return tables


def evaluate(suite: dict, dsn: str) -> dict:
    catalogs: dict[str, Catalog] = {}
    counts = {kind: {"hit": 0, "gold": 0} for kind in ("table", "column", "metric")}
    path_counts = {"correct": 0, "gold": 0}
    details = []
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for case in suite["cases"]:
            source = case["source_id"]
            if source not in catalogs:
                catalogs[source] = Catalog(dsn, source)
            catalog = catalogs[source]
            if case["case_id"].startswith("nl2sql-"):
                cur.execute("SELECT question, data_version, expected_route FROM evaluation_cases WHERE case_id=%s", (case["case_id"],))
                row = cur.fetchone()
                if row != (case["question"], suite["data_version"], "sql"):
                    raise ValueError(f"Seeded evaluation case drifted from {suite['suite_version']}: {case['case_id']}")
            for table in case["tables"]:
                if table not in catalog.tables:
                    raise ValueError(f"Gold table absent from snapshot: {source}.{table}")
            for column in case["columns"]:
                if tuple(column.split(".", 1)) not in catalog.columns:
                    raise ValueError(f"Gold column absent from snapshot: {source}.{column}")
            metric_ids = {metric["metric_id"] for metric in catalog.metrics}
            if not set(case["metrics"]) <= metric_ids:
                raise ValueError(f"Gold metric absent from snapshot: {case['case_id']}")

            # Match the eight-candidate budget used by the SQL engine. Recall@5 is
            # calculated within each kind so a metric does not crowd out a table.
            candidates = catalog.retrieve(case["question"], limit=8)["candidates"]
            result = {"case_id": case["case_id"], "source_id": source,
                      "source_snapshot_id": catalog.snapshot_id,
                      "gold": {"tables": case["tables"], "columns": case["columns"], "metrics": case["metrics"]},
                      "retrieved": {}, "missing": {}, "paths": []}
            for kind, gold_key in (("table", "tables"), ("column", "columns"), ("metric", "metrics")):
                top5 = [c["id"] for c in candidates if c["kind"] == kind][:5]
                gold = case[gold_key]
                missing = sorted(set(gold) - set(top5))
                counts[kind]["hit"] += len(gold) - len(missing)
                counts[kind]["gold"] += len(gold)
                result["retrieved"][kind] = top5
                result["missing"][kind] = missing
            for path in case["paths"]:
                actual = _path_tables(path["from"], catalog.join_path(path["from"], path["to"]))
                correct = actual == path["tables"]
                path_counts["correct"] += int(correct)
                path_counts["gold"] += 1
                result["paths"].append({"from": path["from"], "to": path["to"],
                                        "expected": path["tables"], "actual": actual, "correct": correct})
            details.append(result)
    summary = {kind + "_recall_at_5": {**counts[kind],
               "rate": counts[kind]["hit"] / counts[kind]["gold"] if counts[kind]["gold"] else None}
               for kind in counts}
    summary["join_path_accuracy"] = {**path_counts,
        "rate": path_counts["correct"] / path_counts["gold"] if path_counts["gold"] else None}
    return {"suite_version": suite["suite_version"], "data_version": suite["data_version"],
            "case_count": len(details), "snapshots": {source: c.snapshot_id for source, c in catalogs.items()},
            "catalog_code_sha256": hashlib.sha256((ROOT / "datamind/catalog.py").read_bytes()).hexdigest(),
            "annotation": suite["annotation"],
            "method": "Catalog.retrieve(question, limit=8); top five candidates per kind; micro recall over reviewed semantic anchors; Join Graph path exact table sequence",
            "summary": summary, "cases": details}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/schema_linking.json")
    parser.add_argument("--min-recall", type=float, default=0.90)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    report = evaluate(suite, os.environ.get("DATAMIND_META_DSN", DEFAULT_DSN))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, value in report["summary"].items():
        print(f"{name}: {value['hit'] if 'hit' in value else value['correct']}/{value['gold']} = {value['rate']:.1%}")
    print(f"report: {args.output}")
    return int(any(value["rate"] < args.min_recall for value in report["summary"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
