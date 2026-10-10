"""Frozen original-pixel OCR rows and formula transcription checks (small demo set)."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from unittest.mock import patch
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion import ingest


def canonical_latex(text):
    text = re.sub(r"\s+", "", text).replace("\\left", "").replace("\\right", "")
    text = re.sub(r"\{([A-Za-z0-9])\}", r"\1", text)
    text = re.sub(r"\{(\\sum_[A-Za-z0-9])\}", r"\1", text)
    return text.casefold()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/pdf_ocr.json")
    args = parser.parse_args()
    suite_path = ROOT / "data/evaluation/pdf_ocr_v1.json"
    suite = json.loads(suite_path.read_text())
    parsed, cases = {}, []
    with tempfile.TemporaryDirectory() as pages, patch.object(ingest, "PAGE_IMG_DIR", Path(pages)):
        for case in suite["cases"]:
            source = ROOT / case["source_file"]
            if ingest.sha256_file(source) != case["source_sha256"]:
                raise ValueError("Frozen source changed: " + case["case_id"])
            if case["source_file"] not in parsed:
                start = time.perf_counter()
                blocks, page_count, quality = ingest.parse_pdf(source, case["case_id"], include_quality=True)
                parsed[case["source_file"]] = {"blocks": blocks, "page_count": page_count, "quality": quality,
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 1)}
            document = parsed[case["source_file"]]
            blocks = [b for b in document["blocks"] if b["page_no"] == case["page"]]
            with pymupdf.open(source) as pdf:
                rect = pdf[case["page"] - 1].rect
            locator_ok = bool(blocks) and all(0 <= b["bbox"][0] < b["bbox"][2] <= rect.width and
                0 <= b["bbox"][1] < b["bbox"][3] <= rect.height for b in blocks)
            detail = {"case_id": case["case_id"], "locator_bounds_valid": locator_ok}
            if "gold_rows" in case:
                spatial_rows = [line for b in blocks for line in b["text"].splitlines() if " | " in line]
                matches = [next((line for line in spatial_rows if all(
                    re.search(r"(?<![\w.])" + re.escape(term) + r"(?![\w.])", line) if term[0].isdigit()
                    else re.sub(r"\s+", "", term) in re.sub(r"\s+", "", line) for term in row)), None) for row in case["gold_rows"]]
                detail.update({"matched_rows": matches, "row_count": len(matches), "row_passed": sum(m is not None for m in matches),
                    "passed": all(m is not None for m in matches) and locator_ok and not document["quality"]["reference_inputs_used"]})
            else:
                candidates = document["quality"]["formula_candidates"]
                index = case["figure_index"]
                candidate = candidates[index] if index < len(candidates) else {}
                actual = candidate.get("latex", "")
                detail.update({"actual_latex": actual, "structural_check": candidate.get("structural_check"),
                    "canonical_actual": canonical_latex(actual), "canonical_gold": canonical_latex(case["gold_latex"]),
                    "passed": canonical_latex(actual) == canonical_latex(case["gold_latex"]) and
                              candidate.get("structural_check") == "passed" and locator_ok})
            cases.append(detail)
            print(case["case_id"], "passed=" + str(detail["passed"]), flush=True)
    report = {"suite_version": suite["suite_version"], "evaluator_version": "ocr-eval-v3", "suite_sha256": ingest.sha256_file(suite_path),
        "ocr_row_matching": "Every frozen cell must occur in a spatial multi-cell row; numeric boundaries preserved, product-label whitespace ignored",
        "created_at": datetime.now(timezone.utc).isoformat(), "method": suite["method"],
        "scope": "2 demo scan pages, 6 frozen numeric rows, 3 formula crops; not a general OCR accuracy claim",
        "passed": sum(c["passed"] for c in cases), "case_count": len(cases), "cases": cases, "documents": parsed,
        "code_hashes": {name: ingest.sha256_file(ROOT / name) for name in
            ("ingestion/ingest.py", "ingestion/ocr.py", "ingestion/tables.py", "ingestion/formula.py", "ingestion/formula_worker.py", "scripts/eval_ocr.py")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"passed={report['passed']}/{len(cases)}; {args.output}")
    return 0 if all(c["passed"] for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
