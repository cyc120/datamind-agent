"""E9/F7 鲁棒性扩展评测：错别字 / 口语 / 中英混写 / 全半角 / 歧义 / 越权 / 空结果 / 超长 / 注入。

* **真实端到端**：``POST /v1/chat``（回环免登录身份 + CSRF），串行调用，允许上游模型。
* **判据显式**：每条用例在 ``data/evaluation/robustness_v2.json`` 里写明通过条件；
  脚本只做机械判定，逐条列出失败判据。
* **失败保留**：任何失败样本都留在分母里，并原样摘录状态码、错误码与答案片段。
* **上游错误**：传输错误 / 5xx / INTERNAL_ERROR 最多重试 3 次，仍失败记为 upstream_error。

用法::

    .venv/bin/python scripts/eval_robustness_v2.py
    .venv/bin/python scripts/eval_robustness_v2.py --validate-only
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SUITE_PATH = ROOT / "data/evaluation/robustness_v2.json"
OUTPUT_PATH = ROOT / "reports/robustness_v2.json"
MAX_RETRIES = 3
EMPTY_ANSWER = re.compile(r"(0|零|没有|无|空|不存在)")


class Blocked(RuntimeError):
    """真实前置条件缺失（没有会话）——如实记录。"""


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).lower()


# --------------------------------------------------------------------------- HTTP

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


def error_code_of(payload):
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return detail.get("error_code") or detail.get("code")
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        return detail[0].get("type")
    if isinstance(detail, str):
        return detail
    return payload.get("error_code")


def post_chat(session: dict, question: str, mode: str, scope, timeout: float) -> dict:
    payload = {"question": question, "mode": mode}
    if scope:
        payload["scope"] = scope
    request = urllib.request.Request(
        session["base_url"] + "/v1/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": session["csrf"]})
    started = time.monotonic()
    try:
        with session["opener"].open(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
            return {"http_status": response.status,
                    "wall_ms": round((time.monotonic() - started) * 1000, 1), "payload": body,
                    "error_code": error_code_of(body)}
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw[:400]}
        return {"http_status": error.code,
                "wall_ms": round((time.monotonic() - started) * 1000, 1), "payload": body,
                "error_code": error_code_of(body)}
    except Exception as error:
        return {"http_status": None, "wall_ms": round((time.monotonic() - started) * 1000, 1),
                "payload": None, "transport_error": type(error).__name__ + ": " + str(error)[:200],
                "error_code": None}


def is_upstream_failure(result: dict) -> bool:
    if result.get("transport_error"):
        return True
    if result.get("http_status") is None or (result.get("http_status") or 0) >= 500:
        return True
    code = result.get("error_code") or ""
    return code in {"INTERNAL_ERROR", "MODEL_EMPTY_RESPONSE", "MODEL_ERROR", "MODEL_TIMEOUT"} \
        or code.startswith("UPSTREAM_") or code.startswith("MODEL_")


def post_with_retries(session, question, mode, scope, timeout, retries) -> dict:
    attempts = []
    for attempt in range(1, retries + 2):
        result = post_chat(session, question, mode, scope, timeout)
        attempts.append({"attempt": attempt, "http_status": result.get("http_status"),
                         "error_code": result.get("error_code"),
                         "transport_error": result.get("transport_error")})
        if not is_upstream_failure(result):
            result["attempts"] = attempts
            return result
        time.sleep(1.0)
    result["attempts"] = attempts
    result["upstream_error"] = True
    return result


# --------------------------------------------------------------------------- 冻结集

def read_documents() -> dict:
    import psycopg2
    from datamind.knowledge import meta_dsn

    with psycopg2.connect(meta_dsn()) as conn, conn.cursor() as cur:
        cur.execute("""SELECT title, document_id, doc_version, sha256, parser_version, status,
                              (SELECT count(*) FROM chunks c WHERE c.document_id=a.document_id)
                       FROM document_assets a WHERE a.collection='demo_kb'""")
        documents = {}
        for title, document_id, version, sha, parser, status, chunks in cur.fetchall():
            documents[title] = {
                "document_id": document_id, "document_version": version, "source_sha256": sha,
                "parser_version": parser, "status": status, "chunk_count": chunks,
                "reference": {"document_id": document_id, "document_version": version,
                              "source_sha256": sha, "parser_version": parser}}
    return documents


def build_question(case: dict) -> str:
    pad = case.get("question_pad")
    if not pad:
        return case["question"]
    return pad["prefix"] + pad["repeat"] * pad["repeat_count"] + pad["suffix"]


def resolve_scope(case: dict, documents: dict) -> dict:
    scope = case.get("scope")
    if not scope:
        return None
    resolved = dict(scope)
    titles = resolved.pop("document_titles", None)
    if titles:
        resolved["document_collections"] = resolved.get("document_collections") or ["demo_kb"]
        resolved["document_refs"] = [documents[title]["reference"] for title in titles]
    return resolved


def validate_suite(suite: dict, documents: dict) -> list:
    problems = []
    seen = set()
    for case in suite["cases"]:
        case_id = case["case_id"]
        if case_id in seen:
            problems.append({"case_id": case_id, "problem": "duplicate_case_id"})
        seen.add(case_id)
        if case.get("category") not in suite["categories"]:
            problems.append({"case_id": case_id, "problem": "unknown_category",
                             "category": case.get("category")})
        if "question" not in case and "question_pad" not in case:
            problems.append({"case_id": case_id, "problem": "missing_question"})
        if not case.get("expect"):
            problems.append({"case_id": case_id, "problem": "missing_expect"})
        for title in (case.get("scope") or {}).get("document_titles", []) or []:
            document = documents.get(title)
            if not document:
                problems.append({"case_id": case_id, "problem": "document_not_found",
                                 "document_title": title})
            elif document["status"] != "indexed" or not document["chunk_count"]:
                problems.append({"case_id": case_id, "problem": "document_not_searchable",
                                 "document_title": title, "status": document["status"]})
    return problems


# --------------------------------------------------------------------------- 判据

def evaluate(case: dict, result: dict) -> dict:
    expect = case["expect"]
    payload = result.get("payload") or {}
    answer = payload.get("answer") or ""
    answer_norm = normalize(answer)
    status = payload.get("status")
    error_code = result.get("error_code")
    sql_result = payload.get("sql_result") or {}
    citations = (payload.get("rag_result") or {}).get("citations") or []
    checks = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    allowed_statuses = expect.get("http_status") or [200]
    check("http_status", result.get("http_status") in allowed_statuses,
          {"expected": allowed_statuses, "actual": result.get("http_status")})
    if expect.get("statuses"):
        check("status", status in expect["statuses"],
              {"expected": expect["statuses"], "actual": status})
    forbidden = expect.get("forbidden_error_codes",
                          ["INTERNAL_ERROR", "UPSTREAM_ERROR", "UPSTREAM_TIMEOUT"])
    check("forbidden_error_codes", error_code not in forbidden, {"actual": error_code})
    if expect.get("require_error_code"):
        check("require_error_code", bool(error_code), {"actual": error_code})
    if expect.get("error_codes_any"):
        check("error_codes_any", error_code in expect["error_codes_any"],
              {"expected": expect["error_codes_any"], "actual": error_code})
    if expect.get("answer_regex"):
        check("answer_regex", re.search(expect["answer_regex"], answer_norm) is not None,
              {"regex": expect["answer_regex"], "answer_head": answer_norm[:80]})
    if expect.get("answer_any"):
        check("answer_any", any(normalize(term) in answer_norm for term in expect["answer_any"]),
              {"terms": expect["answer_any"], "answer_head": answer_norm[:120]})
    if expect.get("answer_none"):
        hits = [term for term in expect["answer_none"] if normalize(term) in answer_norm]
        check("answer_none", not hits, {"leaked": hits})
    if expect.get("require_sql_result"):
        check("require_sql_result", bool(sql_result.get("sql")), {"sql_present": bool(sql_result)})
    if expect.get("require_citation"):
        check("require_citation", bool(citations), {"citation_count": len(citations)})
    if expect.get("empty_or_zero_answer"):
        row_count = sql_result.get("row_count")
        zero = (row_count == 0) or (EMPTY_ANSWER.search(answer_norm) is not None)
        check("empty_or_zero_answer", zero, {"row_count": row_count, "answer_head": answer_norm[:80]})
    failures = [item for item in checks if not item["ok"]]
    return {"passed": not failures, "checks": checks, "failures": failures}


def summarize(cases: list) -> dict:
    by_category = {}
    for case in cases:
        bucket = by_category.setdefault(case["category"], {"total": 0, "passed": 0, "failed_ids": []})
        bucket["total"] += 1
        if case["passed"]:
            bucket["passed"] += 1
        else:
            bucket["failed_ids"].append(case["case_id"])
    for bucket in by_category.values():
        bucket["pass_rate"] = (round(bucket["passed"] / bucket["total"], 4)
                               if bucket["total"] else None)
    passed = sum(1 for case in cases if case["passed"])
    return {
        "case_total": len(cases),
        "case_passed": passed,
        "case_failed": len(cases) - passed,
        "pass_rate": round(passed / len(cases), 4) if cases else None,
        "by_category": by_category,
        "upstream_error_cases": sum(1 for case in cases if case.get("upstream_error")),
        "false_500_or_internal": sum(1 for case in cases
                                     if case.get("error_code") in {"INTERNAL_ERROR"}
                                     or (case.get("http_status") or 0) >= 500),
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
        question = build_question(case)
        scope = resolve_scope(case, documents)
        result = post_with_retries(session, question, case["mode"], scope, args.timeout, args.retries)
        payload = result.get("payload") or {}
        verdict = evaluate(case, result)
        detail = {
            "case_id": case["case_id"], "category": case["category"], "mode": case["mode"],
            "question": question, "question_chars": len(question),
            "scope": scope,
            "http_status": result.get("http_status"), "status": payload.get("status"),
            "error_code": result.get("error_code"), "wall_ms": result.get("wall_ms"),
            "upstream_error": bool(result.get("upstream_error")),
            "transport_error": result.get("transport_error"),
            "attempts": result.get("attempts"),
            "answer": payload.get("answer") or "",
            "sql_row_count": (payload.get("sql_result") or {}).get("row_count"),
            "sql_present": bool((payload.get("sql_result") or {}).get("sql")),
            "citation_titles": [(citation.get("title") or "") for citation in
                                ((payload.get("rag_result") or {}).get("citations") or [])],
            "expect": case["expect"],
            "passed": verdict["passed"], "checks": verdict["checks"], "failures": verdict["failures"],
        }
        details.append(detail)
        print(f"{case['case_id']:<9} {case['category']:<7} http={result.get('http_status')} "
              f"status={str(payload.get('status')):<20} pass={verdict['passed']} "
              f"fail={[item['check'] for item in verdict['failures']]}", flush=True)
        time.sleep(args.pause)

    report = {
        "suite_version": suite["suite_version"],
        "evaluator_version": "robustness-eval-v2",
        "data_version": suite["data_version"],
        "scope": suite["scope"],
        "pass_rule": suite["pass_rule"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": " ".join([sys.executable, "scripts/eval_robustness_v2.py", *sys.argv[1:]]),
        "baseline_commit": git_commit(),
        "api_version": api_version(args.base_url),
        "base_url": args.base_url,
        "identity_source": session["identity_source"],
        "identity_username": session["username"],
        "retry_policy": {"max_retries": args.retries,
                         "note": "上游传输错误/5xx/INTERNAL_ERROR 重试；仍失败记为 upstream_error，不移出分母"},
        "case_count": len(details),
        "summary": summarize(details),
        "failure_samples": [
            {"case_id": case["case_id"], "category": case["category"],
             "question": case["question"][:120], "http_status": case["http_status"],
             "status": case["status"], "error_code": case["error_code"],
             "failed_checks": [item["check"] for item in case["failures"]],
             "failure_detail": case["failures"], "answer_head": case["answer"][:160]}
            for case in details if not case["passed"]],
        "code_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                        ("datamind/agent.py", "datamind/sql_engine.py",
                         "scripts/eval_robustness_v2.py")},
        "cases": details,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                                 encoding="utf-8")
    summary = report["summary"]
    print(f"通过 {summary['case_passed']}/{summary['case_total']} = {summary['pass_rate']}；"
          f"5xx/内部错误 {summary['false_500_or_internal']}；上游错误 {summary['upstream_error_cases']}"
          f" → {args.output}")
    return 1 if summary["upstream_error_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
