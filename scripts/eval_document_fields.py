"""Evaluate frozen development labels against persisted fields and their public locators."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi.testclient import TestClient
from datamind.api import app
from datamind.knowledge import meta_dsn


def compact(text):
    return re.sub(r"\s+", "", text or "")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "reports/document_fields.json")
    args = ap.parse_args()
    source = ROOT / "data/evaluation/document_fields_v1.json"
    suite = json.loads(source.read_text())
    details = []
    with psycopg2.connect(meta_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur, TestClient(app) as client:
        for case in suite["cases"]:
            assert hashlib.sha256((ROOT / case["source_file"]).read_bytes()).hexdigest() == case["source_sha256"]
            cur.execute("SELECT document_id,parser_version,quality_detail FROM document_assets WHERE sha256=%s AND status='indexed'", (case["source_sha256"],))
            doc = cur.fetchone()
            if not doc:
                raise ValueError("FROZEN_DOCUMENT_NOT_INDEXED")
            response = client.get('/v1/documents/' + doc["document_id"] + '/facts')
            assert response.status_code == 200
            payload = response.json()
            page = next((p for p in doc["quality_detail"].get("pages", []) if p["page_no"] == case["page"]), {})
            table = next(iter(page.get("tables", [])), {})
            headers = table.get("headers", [])
            header_checks = [{"column": i, "gold": gold, "actual": headers[i].get("text") if i < len(headers) else None,
                "raw_candidate": headers[i].get("raw_candidate") if i < len(headers) else None,
                "passed": i < len(headers) and compact(headers[i].get("text")) == compact(gold)} for i, gold in enumerate(case["headers"])]
            checks = []
            for gold in case["facts"]:
                matches = [f for f in payload["facts"] if compact(f["entity_name"]) == compact(gold["entity_name"]) and f["metric"] == gold["metric"]]
                actual = matches[0] if len(matches) == 1 else None
                values = bool(actual) and all(actual.get(k) == gold[k] for k in ("raw_value", "normalized_value", "normalized_unit"))
                refs = actual.get("evidence_refs", []) if actual else []
                bounds = len(refs) == 3 and all(r["page_no"] == case["page"] and r["source_sha256"] == case["source_sha256"] and
                    0 <= r["bbox"][0] < r["bbox"][2] <= r["page_size"][0] and 0 <= r["bbox"][1] < r["bbox"][3] <= r["page_size"][1] for r in refs)
                bindings = bool(actual) and all(compact(r["text"]) == compact({"entity": gold["entity_name"], "value": gold["raw_value"], "header": actual["raw_header"]}[r["role"]]) for r in refs)
                unknowns = bool(actual) and all(actual.get(k) is None for k in ("entity_id", "batch", "operation", "valid_from", "valid_to")) and actual["approval_status"] == "unreviewed"
                locators = bool(refs) and all(client.get('/v1/documents/' + doc["document_id"] + '/pages/' + str(r["page_no"])).status_code == 200 for r in refs)
                checks.append({"gold": gold, "actual": actual, "values_match": bool(values), "bounds_valid": bool(bounds),
                               "bindings_match": bool(bindings), "unknowns_preserved": bool(unknowns), "locators_resolved": bool(locators),
                               "passed": bool(values and bounds and bindings and unknowns and locators)})
            details.append({"case_id": case["case_id"], "document_id": doc["document_id"], "parser_version": doc["parser_version"],
                            "header_checks": header_checks, "field_checks": checks, "quality": payload["quality"],
                            "persisted_fact_count": payload["fact_count"], "header_model": table.get("header_model")})
    report = {"suite_version": suite["suite_version"], "suite_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "evaluator_version": "fields-eval-v1", "scope": suite["scope"], "method": suite["method"],
              "created_at": datetime.now(timezone.utc).isoformat(), "headers_passed": sum(h["passed"] for d in details for h in d["header_checks"]),
              "header_count": sum(len(d["header_checks"]) for d in details), "fields_passed": sum(f["passed"] for d in details for f in d["field_checks"]),
              "field_count": sum(len(d["field_checks"]) for d in details), "unscored_fields": "Store rows outside the frozen 3 target stores are persisted but not scored",
              "cases": details, "code_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                    ("ingestion/ocr.py", "ingestion/tables.py", "ingestion/facts.py", "ingestion/ingest.py", "datamind/api.py", "scripts/eval_document_fields.py")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f"headers={report['headers_passed']}/{report['header_count']}; fields={report['fields_passed']}/{report['field_count']}; {args.output}")
    return 0 if report["fields_passed"] == report["field_count"] and report["headers_passed"] == report["header_count"] else 1


if __name__ == '__main__':
    raise SystemExit(main())
