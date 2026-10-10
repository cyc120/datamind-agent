"""F5 多文档问答评测：固定多文档集（2–4 份/题）+ 跨文档对比 + 未覆盖拒答。

* 真实端到端：``POST /v1/chat``，``scope.document_refs`` 固定 2–4 份 demo_kb 文档，串行调用。
* 每题报告：status、引用覆盖（是否引到全部指定文件）、正确性判定、拒答正确性。
* 逐结论忠实度复用 ``scripts/eval_faithfulness.py`` 的确定性规则引擎（证据 = 引用片段原文），
  因此多文档题的忠实度口径与单文档题完全一致，不另设一套标准。
* 上游错误最多重试 3 次，仍失败记为 ``upstream_error``，不移出分母。

用法::

    .venv/bin/python scripts/eval_multifile.py
    .venv/bin/python scripts/eval_multifile.py --validate-only
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_faithfulness import (  # noqa: E402  (同一批次交付的规则引擎，复用同一条口径)
    Blocked, is_upstream_failure, label_answer, normalize)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SUITE_PATH = ROOT / "data/evaluation/multifile_v1.json"
OUTPUT_PATH = ROOT / "reports/multifile_v1.json"
MAX_RETRIES = 3


def http_json(url: str, *, payload=None, csrf: str | None = None, timeout: float = 180.0):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {"raw": body[:400]}


def open_session(base_url: str, timeout: float, username: str | None, password: str | None) -> dict:
    import http.cookiejar
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if username and password:
        status, payload = http_json(base_url + "/v1/auth/login",
                                    payload={"username": username, "password": password},
                                    timeout=timeout)
        if status != 200:
            raise Blocked(json.dumps({"step": "login", "http_status": status, "payload": payload},
                                     ensure_ascii=False))
        return {"base_url": base_url, "csrf": payload["csrf_token"], "opener": opener,
                "identity_source": "explicit_login", "username": username}
    status, payload = http_json(base_url + "/v1/auth/me", timeout=timeout)
    if status != 200 or not isinstance(payload, dict) or not payload.get("csrf_token"):
        raise Blocked(json.dumps({"step": "auth_me", "http_status": status, "payload": payload},
                                 ensure_ascii=False))
    return {"base_url": base_url, "csrf": payload["csrf_token"], "opener": opener,
            "identity_source": "loopback_no_login",
            "username": (payload.get("user") or {}).get("username")}


def post_chat(session: dict, question: str, references: list, timeout: float) -> dict:
    payload = {"question": question, "mode": "rag",
               "scope": {"document_collections": ["demo_kb"], "document_refs": references}}
    request = urllib.request.Request(
        session["base_url"] + "/v1/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": session["csrf"]})
    started = time.monotonic()
    try:
        with session["opener"].open(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
            return {"http_status": response.status,
                    "wall_ms": round((time.monotonic() - started) * 1000, 1), "payload": body}
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw[:400]}
        return {"http_status": error.code,
                "wall_ms": round((time.monotonic() - started) * 1000, 1), "payload": body}
    except Exception as error:
        return {"http_status": None, "wall_ms": round((time.monotonic() - started) * 1000, 1),
                "payload": None, "transport_error": type(error).__name__ + ": " + str(error)[:200]}


def post_with_retries(session, question, references, timeout, retries) -> dict:
    attempts = []
    for attempt in range(1, retries + 2):
        result = post_chat(session, question, references, timeout)
        attempts.append({"attempt": attempt, "http_status": result.get("http_status"),
                         "error_code": (result.get("payload") or {}).get("error_code"),
                         "transport_error": result.get("transport_error")})
        if not is_upstream_failure(result):
            result["attempts"] = attempts
            return result
        time.sleep(1.0)
    result["attempts"] = attempts
    result["upstream_error"] = True
    return result


def read_documents() -> dict:
    import psycopg2
    from datamind.knowledge import meta_dsn

    with psycopg2.connect(meta_dsn()) as conn, conn.cursor() as cur:
        cur.execute("""SELECT title, document_id, doc_version, sha256, parser_version, status,
                              (SELECT count(*) FROM chunks c WHERE c.document_id=a.document_id)
                       FROM document_assets a WHERE a.collection='demo_kb'""")
        documents = {}
        for title, document_id, version, sha, parser, status, chunks in cur.fetchall():
            cur.execute("SELECT exact_text FROM chunks WHERE document_id=%s ORDER BY chunk_index",
                        (document_id,))
            documents[title] = {
                "document_id": document_id, "status": status, "chunk_count": chunks,
                "text": normalize(" ".join((row[0] or "") for row in cur.fetchall())),
                "reference": {"document_id": document_id, "document_version": version,
                              "source_sha256": sha, "parser_version": parser}}
    return documents


def validate_suite(suite: dict, documents: dict) -> list:
    problems = []
    seen = set()
    for case in suite["cases"]:
        case_id = case["case_id"]
        if case_id in seen:
            problems.append({"case_id": case_id, "problem": "duplicate_case_id"})
        seen.add(case_id)
        titles = case["document_titles"]
        if not 2 <= len(titles) <= 4:
            problems.append({"case_id": case_id, "problem": "document_count_out_of_range",
                             "count": len(titles)})
        for title in titles:
            document = documents.get(title)
            if not document:
                problems.append({"case_id": case_id, "problem": "document_not_found",
                                 "document_title": title})
            elif document["status"] != "indexed" or not document["chunk_count"]:
                problems.append({"case_id": case_id, "problem": "document_not_searchable",
                                 "document_title": title, "status": document["status"]})
        for title, quotes in (case.get("evidence_quotes") or {}).items():
            document = documents.get(title)
            if not document:
                problems.append({"case_id": case_id, "problem": "evidence_document_unknown",
                                 "document_title": title})
                continue
            for quote in quotes:
                if normalize(quote) not in document["text"]:
                    problems.append({"case_id": case_id, "problem": "quote_not_verbatim",
                                     "document_title": title, "quote": quote})
        if case.get("expected") == "answer" and not (
                case.get("required_values") or case.get("required_term_groups")):
            problems.append({"case_id": case_id, "problem": "no_acceptance_criterion"})
    if not any(len(case["document_titles"]) >= 3 for case in suite["cases"]):
        problems.append({"case_id": "*", "problem": "no_three_document_case"})
    if not any(case.get("expected") == "refuse" for case in suite["cases"]):
        problems.append({"case_id": "*", "problem": "no_refusal_case"})
    return problems


def evaluate_case(case: dict, payload: dict, citations: list) -> dict:
    answer = payload.get("answer") or ""
    answer_norm = normalize(answer)
    status = payload.get("status")
    required_values = [str(value) for value in (case.get("required_values") or [])]
    groups = case.get("required_term_groups") or []
    values_present = {value: (normalize(value) in answer_norm) for value in required_values}
    groups_present = {index: any(normalize(alt) in answer_norm for alt in group)
                      for index, group in enumerate(groups)}
    label_case = dict(case, evidence_quotes=[quote
                                             for quotes in (case.get("evidence_quotes") or {}).values()
                                             for quote in quotes])
    labels = label_answer(label_case, answer, citations)
    cited_ids = sorted({citation.get("document_id") for citation in citations})
    selected_ids = sorted({document_id for document_id in case["_selected_document_ids"]})
    covers_selected = all(document_id in cited_ids for document_id in selected_ids)
    if case.get("expected") == "refuse":
        abstained = status == "ABSTAINED" or payload.get("error_code") == "INSUFFICIENT_EVIDENCE"
        passed = bool(abstained and not citations)
        correctness = {"refusal_correct": passed, "abstained": bool(abstained)}
    else:
        values_ok = all(values_present.values()) if values_present else True
        groups_ok = all(groups_present.values()) if groups_present else True
        passed = (status == "COMPLETED" and values_ok and groups_ok
                  and (covers_selected or not case.get("cite_all_expected")))
        correctness = {"required_values_present": values_present, "all_values_present": values_ok,
                       "required_term_groups_present": groups_present, "all_groups_present": groups_ok,
                       "citation_covers_all_selected": covers_selected}
    return {
        "correctness": correctness,
        "cited_document_ids": cited_ids,
        "selected_document_ids": selected_ids,
        "citation_covers_all_selected": covers_selected,
        "citation_count": len(citations),
        **labels,
        "passed": passed,
    }


def summarize(cases: list) -> dict:
    answerable = [case for case in cases if case["expected"] == "answer"]
    refusals = [case for case in cases if case["expected"] == "refuse"]
    correct = sum(1 for case in answerable if case["passed"])
    cite_all = [case for case in answerable if case.get("cite_all_expected")]
    cite_all_ok = sum(1 for case in cite_all if case["citation_covers_all_selected"])
    refusal_ok = sum(1 for case in refusals if case["passed"])
    conclusions = {"supported": 0, "contradicted": 0, "no_evidence": 0}
    for case in cases:
        for key in conclusions:
            conclusions[key] += case.get(key) or 0
    total = sum(conclusions.values())
    return {
        "case_total": len(cases),
        "answerable_cases": len(answerable),
        "answerable_correct": correct,
        "answerable_accuracy": round(correct / len(answerable), 4) if answerable else None,
        "citation_covers_all_selected_cases": cite_all_ok,
        "citation_covers_all_selected_denominator": len(cite_all),
        "citation_covers_all_selected_rate": round(cite_all_ok / len(cite_all), 4) if cite_all else None,
        "citation_covers_all_selected_overall": sum(
            1 for case in answerable if case["citation_covers_all_selected"]),
        "citation_covers_all_selected_overall_rate": (
            round(sum(1 for case in answerable if case["citation_covers_all_selected"]) / len(answerable), 4)
            if answerable else None),
        "refusal_cases": len(refusals),
        "refusal_correct": refusal_ok,
        "refusal_accuracy": round(refusal_ok / len(refusals), 4) if refusals else None,
        "conclusion_total": total,
        "conclusion_supported": conclusions["supported"],
        "conclusion_contradicted": conclusions["contradicted"],
        "conclusion_no_evidence": conclusions["no_evidence"],
        "faithfulness_rate": round(conclusions["supported"] / total, 4) if total else None,
        "upstream_error_cases": sum(1 for case in cases if case.get("upstream_error")),
    }


def api_version(base_url: str):
    try:
        status, payload = http_json(base_url + "/openapi.json", timeout=10)
        return (payload or {}).get("info", {}).get("version") if status == 200 else None
    except Exception:
        return None


def git_commit():
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=MAX_RETRIES)
    parser.add_argument("--pause", type=float, default=0.3)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    documents = read_documents()
    problems = validate_suite(suite, documents)
    if args.validate_only:
        print(json.dumps({"cases": len(suite["cases"]), "problems": problems},
                         ensure_ascii=False, indent=2))
        return 1 if problems else 0
    if problems:
        print("冻结集校验失败：" + json.dumps(problems, ensure_ascii=False), file=sys.stderr)
        return 2
    cases = [case for case in suite["cases"] if not args.case or case["case_id"] in args.case]
    if args.limit:
        cases = cases[:args.limit]
    try:
        session = open_session(args.base_url, args.timeout, args.username, args.password)
    except Blocked as exc:
        payload = {"status": "blocked", "reason": str(exc), "suite_version": suite["suite_version"],
                   "created_at": datetime.now(timezone.utc).isoformat()}
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
        print("会话不可用，已写 blocked 报告：" + str(exc), file=sys.stderr)
        return 1

    details = []
    for case in cases:
        references = [documents[title]["reference"] for title in case["document_titles"]]
        case = dict(case, _selected_document_ids=[documents[title]["document_id"]
                                                  for title in case["document_titles"]])
        result = post_with_retries(session, case["question"], references, args.timeout, args.retries)
        payload = result.get("payload") or {}
        rag = payload.get("rag_result") or {}
        citations = rag.get("citations") or []
        verdict = evaluate_case(case, payload, citations)
        detail = {
            "case_id": case["case_id"], "question": case["question"],
            "document_titles": case["document_titles"],
            "document_ids": case["_selected_document_ids"],
            "cross_document": bool(case.get("cross_document")),
            "cite_all_expected": bool(case.get("cite_all_expected")),
            "expected": case["expected"], "note": case.get("note"),
            "http_status": result.get("http_status"), "status": payload.get("status"),
            "error_code": payload.get("error_code"), "wall_ms": result.get("wall_ms"),
            "upstream_error": bool(result.get("upstream_error")),
            "transport_error": result.get("transport_error"), "attempts": result.get("attempts"),
            "answer": payload.get("answer") or "",
            "citations": [{"document_id": citation.get("document_id"), "title": citation.get("title"),
                           "page_start": citation.get("page_start"),
                           "quote_head": (citation.get("quote") or "")[:120]}
                          for citation in citations],
            "retrieval": {key: (rag.get("retrieval") or {}).get(key)
                          for key in ("eligible_chunks", "warnings", "mode")},
            **verdict,
        }
        details.append(detail)
        print(f"{case['case_id']:<10} {str(payload.get('status')):<20} "
              f"docs={len(case['document_titles'])} 引用文件={len(verdict['cited_document_ids'])} "
              f"覆盖全选={verdict['citation_covers_all_selected']} "
              f"结论支持={verdict['supported']}/{verdict['conclusion_count']} "
              f"pass={verdict['passed']}", flush=True)
        time.sleep(args.pause)

    report = {
        "suite_version": suite["suite_version"],
        "evaluator_version": "multifile-eval-v1",
        "data_version": suite["data_version"],
        "scope": suite["scope"],
        "labeling": {"method": "deterministic_rule_engine_v1", "model_judgment": None,
                     "note": "逐结论忠实度复用 scripts/eval_faithfulness.py 的同一规则引擎"},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": " ".join([sys.executable, "scripts/eval_multifile.py", *sys.argv[1:]]),
        "baseline_commit": git_commit(),
        "api_version": api_version(args.base_url),
        "base_url": args.base_url,
        "identity_source": session["identity_source"],
        "identity_username": session["username"],
        "retry_policy": {"max_retries": args.retries,
                         "note": "上游/模型错误重试；仍失败记为 upstream_error，不移出分母"},
        "case_count": len(details),
        "summary": summarize(details),
        "failure_samples": [
            {"case_id": case["case_id"], "expected": case["expected"], "status": case["status"],
             "error_code": case["error_code"], "document_titles": case["document_titles"],
             "cited_document_ids": case["cited_document_ids"],
             "answer_head": case["answer"][:200], "correctness": case["correctness"]}
            for case in details if not case["passed"]],
        "code_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                        ("datamind/rag_engine.py", "datamind/agent.py",
                         "scripts/eval_multifile.py", "scripts/eval_faithfulness.py")},
        "cases": details,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                                 encoding="utf-8")
    summary = report["summary"]
    print(f"多文档正确 {summary['answerable_correct']}/{summary['answerable_cases']} = "
          f"{summary['answerable_accuracy']}；引用覆盖全选 "
          f"{summary['citation_covers_all_selected_cases']}/{summary['citation_covers_all_selected_denominator']}；"
          f"拒答 {summary['refusal_correct']}/{summary['refusal_cases']}；"
          f"逐结论忠实度 {summary['conclusion_supported']}/{summary['conclusion_total']} → {args.output}")
    return 1 if summary["upstream_error_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
