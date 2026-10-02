"""Run frozen document questions through retrieval, Agent, citations and file endpoints."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2
from fastapi.testclient import TestClient

from datamind.agent import DataMindAgent
from datamind.api import app
from datamind.knowledge import KnowledgeRetriever, meta_dsn, normalize
from datamind.sql_engine import settings


class FrozenRetrieval:
    def __init__(self, result):
        self.result = result

    def retrieve(self, question, *, collections=None, limit=5):
        return {**self.result, "hits": self.result["hits"][:limit]}


def is_gold_source(hit, case):
    return hit["title"].startswith(case["document_prefix"]) and hit["page_start"] <= case["page"] <= hit["page_end"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--mode", choices=("hybrid", "lexical"), default="hybrid")
    args = parser.parse_args()
    suite = json.loads((ROOT / "data/evaluation/rag_v2.json").read_text())
    cfg = settings()
    details, reciprocal_ranks, recalls = [], [], []
    with psycopg2.connect(meta_dsn()) as conn, conn.cursor() as cur, TestClient(app) as client:
        for case in suite["cases"]:
            if args.case and case["case_id"] not in args.case:
                continue
            cur.execute("SELECT question,data_version FROM evaluation_cases WHERE case_id=%s", (case["case_id"],))
            if cur.fetchone() != (case["question"], suite["data_version"]):
                raise ValueError(f"Seeded case differs from frozen suite: {case['case_id']}")
            if case.get("source_file"):
                source_hash = hashlib.sha256((ROOT / case["source_file"]).read_bytes()).hexdigest()
                if source_hash != case["source_sha256"]:
                    raise ValueError(f"Source changed for frozen case: {case['case_id']}")
            scope = ["ohrbench_kb"] if case["case_id"] == "rag-011" else ["demo_kb"]
            retrieved = KnowledgeRetriever(mode=args.mode).retrieve(case["question"], collections=scope, limit=10)
            rank = next((i for i, hit in enumerate(retrieved["hits"], 1) if case.get("document_prefix") and is_gold_source(hit, case)), None)
            if not case.get("expected_abstain"):
                recalls.append(rank is not None and rank <= 5)
                reciprocal_ranks.append(1 / rank if rank else 0)
            detail = {"case_id": case["case_id"], "question": case["question"], "known_gap": case.get("known_gap"),
                      "gold_document_rank": rank, "retrieval": {key: value for key, value in retrieved.items() if key != "hits"},
                      "retrieved": [{"chunk_id": hit["chunk_id"], "title": hit["title"], "pages": [hit["page_start"],hit["page_end"]]} for hit in retrieved["hits"]]}
            if not args.retrieval_only:
                agent = DataMindAgent()
                try:
                    final = agent.run(case["question"], mode="rag", document_collections=scope,
                                      retriever=FrozenRetrieval(retrieved))
                finally:
                    agent.close()
                rag = final.get("rag_result") or {}
                citations = rag.get("citations") or []
                # Presentation equivalence only; frozen factual/source labels are unchanged.
                answer = re.sub(r"_\{([a-z])\}", r"_\1", normalize(final["answer"]).replace("\\%", "%"))
                answer_checks = [any(normalize(term) in answer for term in alternatives) for alternatives in case["answer_checks"]]
                cited_gold = any(is_gold_source(citation, case) and citation["document_sha256"] == case["source_sha256"]
                                 for citation in citations) if not case.get("expected_abstain") else None
                locator_ok = bool(citations) and all(client.get(citation["source_url"]).status_code == 200 and
                    (not citation["page_url"] or client.get(citation["page_url"]).status_code == 200) for citation in citations)
                passed = final["status"] == "ABSTAINED" if case.get("expected_abstain") else (
                    final["status"] == "COMPLETED" and all(answer_checks) and cited_gold and locator_ok)
                detail.update({"run_id": final["run_id"], "status": final["status"], "error_code": final["error_code"],
                    "answer": final["answer"], "answer_checks": answer_checks, "cited_gold": cited_gold,
                    "locators_resolved": locator_ok, "citations": citations, "attempts": rag.get("attempts"), "passed": bool(passed),
                    "versions": client.get(f"/v1/runs/{final['run_id']}").json()["versions"]})
            details.append(detail)
            print(case["case_id"], "rank=" + str(rank), detail.get("status", "retrieval-only"),
                  "pass=" + str(detail.get("passed")), flush=True)
    report = {"suite_version": suite["suite_version"], "data_version": suite["data_version"], "method": suite["method"],
              "evaluator_version": "rag-eval-v2", "answer_normalization": "NFKC, whitespace/case; LaTeX escaped percent and single-letter subscript braces",
              "created_at": datetime.now(timezone.utc).isoformat(), "model": cfg.get("NEW_API_MODEL"),
              "retrieval_only": args.retrieval_only, "case_count": len(details), "answerable_count": len(recalls),
              "recall_at_5": sum(recalls) / len(recalls) if recalls else None,
              "mrr_at_10": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else None,
              "passed": None if args.retrieval_only else sum(case["passed"] for case in details),
              "code_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                              ("datamind/knowledge.py", "datamind/rag_engine.py", "datamind/agent.py", "scripts/eval_rag.py")}, "cases": details}
    output = args.output or ROOT / ("reports/rag_retrieval.json" if args.retrieval_only else "reports/rag.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Recall@5={report['recall_at_5']}, MRR@10={report['mrr_at_10']}, passed={report['passed']}/{len(details)}; {output}")
    return 0 if args.retrieval_only or report["passed"] == len(details) else 1


if __name__ == "__main__":
    raise SystemExit(main())
