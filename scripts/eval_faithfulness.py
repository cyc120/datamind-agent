"""E2/E6 单文档 RAG 忠实度评测：逐结论三态标注（supported / contradicted / no_evidence）。

设计要点（全部写进报告，可核对）：

* **证据基准 = 被引用片段的原文。** 每个结论句只在本次运行的 ``citations[].quote``
  拼接文本上判定；冻结集里的 ``evidence_quotes`` 只用于「引用覆盖率」这一独立指标。
* **规则判定与模型判定分列。** 本脚本不使用模型判定（``model_judgment=null``），
  因此不存在「把模型判定混进规则数」的问题。
* **拒答单列但留在分母。** 不可答用例（``expected=refuse``）计入拒答正确率；
  可答用例若被拒答，记为误拒（``false_refusal``）并保留在可答用例分母里。
* **上游错误最多重试 3 次**，仍失败则记为 ``upstream_error`` 并保留在分母里。

用法::

    .venv/bin/python scripts/eval_faithfulness.py                 # 全量真实调用
    .venv/bin/python scripts/eval_faithfulness.py --case faith-001 --case faith-ref-001
    .venv/bin/python scripts/eval_faithfulness.py --validate-only  # 只校验冻结集与文档原文一致
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
SUITE_PATH = ROOT / "data/evaluation/faithfulness_v1.json"
OUTPUT_PATH = ROOT / "reports/faithfulness_v1.json"
MAX_RETRIES = 3
CITATION_MARKER = re.compile(r"\[\d+\]")
NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)")
#: 参考性数字（第3条 / 未识别列5 / 表2 / 序号1）不是数量断言，不计入 claims。
REFERENCE_PREFIXES = ("第", "列", "表", "图", "页", "序号", "编号", "注", "版本", "Q", "q")
SENTENCE_SPLIT = re.compile(r"[。！？；\n]+")
BOILERPLATE_TITLE = re.compile(r"^(根据|依据|按照|来源)?《[^》]*》[，。、：]?$")
#: 拒答句式。刻意不含「不在」等会出现在正常答案里的词（如「不在保修范围」）。
REFUSAL_PATTERNS = ("无法回答", "无法确定", "没有足够", "证据不足", "未提供", "未涉及", "不包含",
                    "暂无", "没有相关", "未找到", "请补充", "建议补充", "未收录", "不能回答",
                    "不支持该", "未涵盖", "不覆盖", "没有提供")
LABELING_METHOD = "deterministic_rule_engine_v1"


class Blocked(RuntimeError):
    """真实前置条件缺失（没有会话 / 文档不可用）——如实记录，不用 mock 顶替。"""


# --------------------------------------------------------------------------- 文本归一化

def normalize(text: str) -> str:
    """与 ``datamind.knowledge.normalize`` 同一口径（NFKC + 去空白 + 小写）。

    本地实现一份是为了让本脚本在不连库时也能被单测导入；单元测试会断言两者一致。
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).lower()


def cjk_and_alnum_count(text: str) -> int:
    return sum(1 for char in text if char.isalnum())


def number_tokens(text: str) -> list:
    """数量断言数字；紧邻 ASCII 字母的（450L、4K、Pro3）与参考性编号（第3条、列5）不算。"""
    tokens = []
    for match in NUMBER.finditer(text or ""):
        end = match.end()
        start = match.start()
        if end < len(text) and text[end].isascii() and text[end].isalpha():
            continue
        if start > 0 and text[start - 1].isascii() and text[start - 1].isalpha():
            continue
        if start > 0 and text[start - 1] in REFERENCE_PREFIXES:
            continue
        tokens.append(match.group(1))
    return tokens


def is_refusal(sentence: str) -> bool:
    return any(pattern in sentence for pattern in REFUSAL_PATTERNS)


def is_boilerplate(sentence: str) -> bool:
    stripped = CITATION_MARKER.sub("", sentence).strip()
    if not stripped:
        return True
    if BOILERPLATE_TITLE.match(stripped):
        return True
    return cjk_and_alnum_count(stripped) < 5


def split_conclusions(answer: str) -> dict:
    """答案 → 结论句 / 拒答句 / 版式残片（后两类不进结论分母）。"""
    conclusions, refusals, boilerplate = [], [], []
    for raw in SENTENCE_SPLIT.split(answer or ""):
        sentence = CITATION_MARKER.sub("", raw).strip()
        if not sentence:
            continue
        if is_refusal(sentence):
            refusals.append(sentence)
        elif is_boilerplate(sentence):
            boilerplate.append(sentence)
        else:
            conclusions.append(sentence)
    return {"conclusions": conclusions, "refusal_sentences": refusals,
            "boilerplate_sentences": boilerplate}


def match_groups(sentence: str, groups) -> list:
    return [index for index, group in enumerate(groups or [])
            if any(alternative.lower() in sentence.lower() for alternative in group)]


# --------------------------------------------------------------------------- 标注规则

def label_conclusion(sentence: str, case: dict, question_numbers: set,
                     evidence_text: str) -> dict:
    """一个结论句的三态标注。返回 ``label`` + 可直接审计的中间量。"""
    required = list(case.get("required_values") or [])
    allowed = list(case.get("allowed_values") or [])
    groups = case.get("required_term_groups") or []
    context = {str(value) for value in (case.get("context_values") or [])}
    claims = [token for token in number_tokens(sentence)
              if token not in question_numbers and token not in context]
    matched = match_groups(sentence, groups)
    detail = {"claims": claims, "required_values": required, "allowed_values": allowed,
              "matched_term_groups": matched, "group_count": len(groups),
              "evidence_has_sentence_number": None}
    if claims:
        permitted = set(required) | set(allowed)
        detail["evidence_has_sentence_number"] = all(
            token in evidence_text for token in claims)
        if required and (set(required) & set(claims)) and set(claims) <= permitted:
            label = "supported"
        elif set(claims) - permitted:
            label = "contradicted"
        elif required and not (set(required) & set(claims)):
            label = "no_evidence"
        else:
            label = "supported" if set(claims) <= permitted else "no_evidence"
    elif groups:
        label = "supported" if matched else "no_evidence"
    else:
        # 既无数值也无可判定的术语锚点：不能声称支持。
        label = "no_evidence"
    detail["label"] = label
    return detail


def label_answer(case: dict, answer: str, citations: list) -> dict:
    evidence_text = normalize(" ".join((citation.get("quote") or "") for citation in citations))
    question_numbers = set(number_tokens(case.get("question") or ""))
    split = split_conclusions(answer)
    details = [label_conclusion(sentence, case, question_numbers, evidence_text)
               for sentence in split["conclusions"]]
    counts = {"supported": 0, "contradicted": 0, "no_evidence": 0}
    for detail in details:
        counts[detail["label"]] += 1
    groups = case.get("required_term_groups") or []
    answer_norm = normalize(answer)
    evidence_norm = evidence_text
    groups_in_answer = [index for index, group in enumerate(groups)
                        if any(normalize(alt) in answer_norm for alt in group)]
    groups_in_evidence = [index for index, group in enumerate(groups)
                          if any(normalize(alt) in evidence_norm for alt in group)]
    frozen = case.get("evidence_quotes") or []
    frozen_covered = [normalize(quote) in evidence_norm for quote in frozen]
    return {
        "conclusions": [{"sentence": sentence, **detail}
                        for sentence, detail in zip(split["conclusions"], details)],
        "refusal_sentences": split["refusal_sentences"],
        "boilerplate_sentences": split["boilerplate_sentences"],
        "conclusion_count": len(details),
        "supported": counts["supported"], "contradicted": counts["contradicted"],
        "no_evidence": counts["no_evidence"],
        "unfaithful": counts["contradicted"] + counts["no_evidence"],
        "term_groups_in_answer": groups_in_answer,
        "term_groups_in_evidence": groups_in_evidence,
        "term_groups_complete": (groups_in_answer == list(range(len(groups)))
                                 and groups_in_evidence == list(range(len(groups)))),
        "frozen_evidence_covered": (all(frozen_covered) if frozen else None),
        "frozen_evidence_cover_detail": frozen_covered,
    }


# --------------------------------------------------------------------------- 真实调用

def http_json(url: str, *, payload=None, csrf: str | None = None, timeout: float = 180.0):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {"raw": body[:400]}


def open_session(base_url: str, timeout: float, username: str | None,
                 password: str | None) -> dict:
    """回环免登录身份（``DATAMIND_LOCAL_NO_LOGIN`` 默认放行）或显式账号。

    ``GET /v1/auth/me`` 是只读路径（``provision_demo=False``），不写 ``auth_users``。
    ``POST /v1/chat`` 是变更路由：回环免登录身份会让服务端幂等地确保 ``user-local-admin``
    审计行存在（``INSERT ... ON CONFLICT DO NOTHING``）——只落在 ``datamind_meta`` 的身份表，
    不写 demo_retail / demo_industrial 演示库。
    """
    if username and password:
        opener = _cookie_opener()
        status, payload = _login(opener, base_url, username, password, timeout)
        if status != 200:
            raise Blocked(json.dumps({"step": "login", "http_status": status, "payload": payload},
                                     ensure_ascii=False))
        return {"base_url": base_url, "opener": opener, "csrf": payload["csrf_token"],
                "identity_source": "explicit_login", "username": username}
    status, payload = http_json(base_url + "/v1/auth/me", timeout=timeout)
    if status != 200 or not isinstance(payload, dict) or not payload.get("csrf_token"):
        raise Blocked(json.dumps({"step": "auth_me", "http_status": status, "payload": payload,
                                  "note": "回环免登录身份不可用，且没有提供账号密码"}, ensure_ascii=False))
    return {"base_url": base_url, "opener": _cookie_opener(), "csrf": payload["csrf_token"],
            "identity_source": "loopback_no_login", "username": (payload.get("user") or {}).get("username")}


def _cookie_opener():
    import http.cookiejar
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _login(opener, base_url: str, username: str, password: str, timeout: float):
    request = urllib.request.Request(
        base_url + "/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode())
        except ValueError:
            return error.code, {}


def chat_once(session: dict, question: str, reference: dict, timeout: float) -> dict:
    payload = {"question": question, "mode": "rag",
               "scope": {"document_collections": ["demo_kb"], "document_refs": [reference]}}
    request = urllib.request.Request(
        session["base_url"] + "/v1/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": session["csrf"]})
    started = time.monotonic()
    try:
        with session["opener"].open(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
            return {"http_status": response.status, "wall_ms": round((time.monotonic() - started) * 1000, 1),
                    "payload": body}
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode())
        except ValueError:
            body = {"raw": (error.read().decode() or "")[:400]}
        return {"http_status": error.code, "wall_ms": round((time.monotonic() - started) * 1000, 1),
                "payload": body}
    except Exception as error:                                  # transport error
        return {"http_status": None, "wall_ms": round((time.monotonic() - started) * 1000, 1),
                "payload": None, "transport_error": type(error).__name__ + ": " + str(error)[:200]}


def is_upstream_failure(result: dict) -> bool:
    if result.get("transport_error"):
        return True
    status = result.get("http_status")
    if status is None or status >= 500:
        return True
    code = (result.get("payload") or {}).get("error_code") or ""
    # 上游/模型侧失败（含模型返回空内容、模型超时）都要重试；产品侧校验失败
    # （如 RAG_VERIFICATION_FAILED）不是上游错误，不重试，如实保留为产品结果。
    return code in {"INTERNAL_ERROR", "MODEL_EMPTY_RESPONSE", "MODEL_ERROR", "MODEL_TIMEOUT"} \
        or code.startswith("UPSTREAM_") or code.startswith("MODEL_")


def chat_with_retries(session: dict, question: str, reference: dict, timeout: float,
                      retries: int) -> dict:
    """串行调用 + 上游失败最多重试 ``retries`` 次；每次尝试都留在记录里、不移出分母。"""
    attempts = []
    for attempt in range(1, retries + 2):
        result = chat_once(session, question, reference, timeout)
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


# --------------------------------------------------------------------------- 只读文档清单

def read_documents() -> dict:
    """只读元数据库：每份 demo_kb 文档的引用 + 切片原文（用于冻结集一致性校验）。"""
    import psycopg2
    from datamind.knowledge import meta_dsn

    with psycopg2.connect(meta_dsn()) as conn, conn.cursor() as cur:
        cur.execute("""SELECT a.title, a.document_id, a.doc_version, a.sha256, a.parser_version,
                              a.collection, a.status,
                              (SELECT count(*) FROM chunks c WHERE c.document_id=a.document_id)
                       FROM document_assets a WHERE a.collection='demo_kb'""")
        rows = cur.fetchall()
        documents = {}
        for title, document_id, version, sha, parser, collection, status, chunks in rows:
            cur.execute("SELECT exact_text FROM chunks WHERE document_id=%s ORDER BY chunk_index",
                        (document_id,))
            documents[title] = {
                "document_id": document_id, "document_version": version, "source_sha256": sha,
                "parser_version": parser, "collection": collection, "status": status,
                "chunk_count": chunks,
                "reference": {"document_id": document_id, "document_version": version,
                              "source_sha256": sha, "parser_version": parser},
                "text": normalize(" ".join((row[0] or "") for row in cur.fetchall())),
            }
    return documents


def validate_suite(suite: dict, documents: dict) -> list:
    problems = []
    seen = set()
    for case in suite["cases"]:
        case_id = case["case_id"]
        if case_id in seen:
            problems.append({"case_id": case_id, "problem": "duplicate_case_id"})
        seen.add(case_id)
        document = documents.get(case["document_title"])
        if not document:
            problems.append({"case_id": case_id, "problem": "document_not_found",
                             "document_title": case["document_title"]})
            continue
        if document["status"] != "indexed" or not document["chunk_count"]:
            problems.append({"case_id": case_id, "problem": "document_not_searchable",
                             "status": document["status"], "chunks": document["chunk_count"]})
        for quote in case.get("evidence_quotes") or []:
            if normalize(quote) not in document["text"]:
                problems.append({"case_id": case_id, "problem": "quote_not_verbatim",
                                 "quote": quote})
        for value in (case.get("required_values") or []) + (case.get("allowed_values") or []):
            if not any(normalize(value) in chunk for chunk in [document["text"]]):
                problems.append({"case_id": case_id, "problem": "value_not_in_document",
                                 "value": value})
        if case.get("expected") == "answer" and not (
                case.get("required_values") or case.get("required_term_groups")):
            problems.append({"case_id": case_id, "problem": "no_acceptance_criterion"})
    return problems


# --------------------------------------------------------------------------- 汇总

def _rate(numerator: int, denominator: int):
    return round(numerator / denominator, 4) if denominator else None


def summarize(cases: list) -> dict:
    conclusions = {"supported": 0, "contradicted": 0, "no_evidence": 0}
    boilerplate = refusal_sentences = 0
    answerable = refused_answerable = upstream_cases = 0
    all_supported_cases = zero_contradicted_cases = 0
    citation_covered = citation_expected = 0
    term_complete = term_expected = 0
    refusal_total = refusal_correct = 0
    for case in cases:
        conclusions["supported"] += case.get("supported") or 0
        conclusions["contradicted"] += case.get("contradicted") or 0
        conclusions["no_evidence"] += case.get("no_evidence") or 0
        boilerplate += len(case.get("boilerplate_sentences") or [])
        refusal_sentences += len(case.get("refusal_sentences") or [])
        if case.get("upstream_error"):
            upstream_cases += 1
        if case["expected"] == "answer":
            answerable += 1
            if case.get("abstained") or case.get("refusal_sentences"):
                refused_answerable += 1
            if case.get("conclusion_count") and case.get("unfaithful") == 0:
                all_supported_cases += 1
            if case.get("contradicted") == 0:
                zero_contradicted_cases += 1
            if case.get("frozen_evidence_covered") is not None:
                citation_expected += 1
                citation_covered += 1 if case["frozen_evidence_covered"] else 0
            if case.get("require_all_term_groups"):
                term_expected += 1
                term_complete += 1 if case.get("term_groups_complete") else 0
        else:
            refusal_total += 1
            refusal_correct += 1 if case.get("refusal_correct") else 0
    total = sum(conclusions.values())
    return {
        "conclusion_total": total,
        "conclusion_supported": conclusions["supported"],
        "conclusion_contradicted": conclusions["contradicted"],
        "conclusion_no_evidence": conclusions["no_evidence"],
        "faithfulness_rate": _rate(conclusions["supported"], total),
        "contradicted_rate": _rate(conclusions["contradicted"], total),
        "no_evidence_rate": _rate(conclusions["no_evidence"], total),
        "answers_with_zero_contradicted": zero_contradicted_cases,
        "answers_with_zero_contradicted_rate": _rate(zero_contradicted_cases, answerable),
        "answers_all_conclusions_supported": all_supported_cases,
        "answers_all_conclusions_supported_rate": _rate(all_supported_cases, answerable),
        "answerable_cases": answerable,
        "false_refusal_cases": refused_answerable,
        "false_refusal_rate": _rate(refused_answerable, answerable),
        "citation_coverage_cases": citation_covered,
        "citation_coverage_denominator": citation_expected,
        "citation_coverage_rate": _rate(citation_covered, citation_expected),
        "list_completeness_cases": term_complete,
        "list_completeness_denominator": term_expected,
        "list_completeness_rate": _rate(term_complete, term_expected),
        "refusal_cases": refusal_total,
        "refusal_correct": refusal_correct,
        "refusal_accuracy": _rate(refusal_correct, refusal_total),
        "upstream_error_cases": upstream_cases,
        "boilerplate_sentences_excluded": boilerplate,
        "refusal_sentences_excluded": refusal_sentences,
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


# --------------------------------------------------------------------------- 主流程

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=MAX_RETRIES)
    parser.add_argument("--pause", type=float, default=0.3, help="串行调用之间的间隔秒数")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--validate-only", action="store_true",
                        help="只校验冻结集（题面证据是否逐字来自文档原文），不调用模型")
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
    cases = [case for case in suite["cases"]
             if not args.case or case["case_id"] in args.case]
    if args.limit:
        cases = cases[:args.limit]

    try:
        session = open_session(args.base_url, args.timeout, args.username, args.password)
    except Blocked as exc:
        payload = {"status": "blocked", "reason": str(exc),
                   "suite_version": suite["suite_version"], "case_count": len(cases),
                   "created_at": datetime.now(timezone.utc).isoformat()}
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
        print("会话不可用，已写 blocked 报告：" + str(exc), file=sys.stderr)
        return 1

    details = []
    for case in cases:
        document = documents[case["document_title"]]
        result = chat_with_retries(session, case["question"], document["reference"],
                                   args.timeout, args.retries)
        payload = result.get("payload") or {}
        rag = payload.get("rag_result") or {}
        citations = rag.get("citations") or []
        answer = payload.get("answer") or ""
        status = payload.get("status")
        labels = label_answer(case, answer, citations)
        detail = {
            "case_id": case["case_id"],
            "expected": case["expected"],
            "question": case["question"],
            "document_title": case["document_title"],
            "document_id": document["document_id"],
            "http_status": result.get("http_status"),
            "wall_ms": result.get("wall_ms"),
            "status": status,
            "error_code": payload.get("error_code"),
            "abstained": status in {"ABSTAINED"} or "INSUFFICIENT_EVIDENCE" == payload.get("error_code"),
            "upstream_error": bool(result.get("upstream_error")),
            "transport_error": result.get("transport_error"),
            "attempts": result.get("attempts"),
            "answer": answer,
            "citation_count": len(citations),
            "cited_document_ids": sorted({citation.get("document_id") for citation in citations}),
            "citation_quotes": [citation.get("quote") for citation in citations],
            "retrieval": {key: (rag.get("retrieval") or {}).get(key)
                          for key in ("eligible_chunks", "warnings", "mode")},
            "require_all_term_groups": bool(case.get("require_all_term_groups")),
            **labels,
        }
        if case["expected"] == "refuse":
            refusal_text = is_refusal(answer) or detail["abstained"]
            detail["refusal_correct"] = bool(detail["abstained"] and not citations) or bool(
                refusal_text and not citations)
        else:
            detail["refusal_correct"] = None
        details.append(detail)
        print(f"{case['case_id']:<16} {str(status):<20} 结论={labels['conclusion_count']:>2} "
              f"支持={labels['supported']} 矛盾={labels['contradicted']} "
              f"无依据={labels['no_evidence']} 引用={len(citations)} "
              f"上游错误={bool(result.get('upstream_error'))}", flush=True)
        time.sleep(args.pause)

    report = {
        "suite_version": suite["suite_version"],
        "evaluator_version": "faithfulness-eval-v1",
        "data_version": suite["data_version"],
        "scope": suite["scope"],
        "labeling": {"method": LABELING_METHOD, "model_judgment": None,
                     "rules": suite["labeling_rules"]},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": " ".join([sys.executable, "scripts/eval_faithfulness.py", *sys.argv[1:]]),
        "baseline_commit": git_commit(),
        "api_version": api_version(args.base_url),
        "base_url": args.base_url,
        "identity_source": session["identity_source"],
        "identity_username": session["username"],
        "retry_policy": {"max_retries": args.retries,
                         "note": "上游传输错误/5xx/INTERNAL_ERROR 重试；仍失败记为 upstream_error，不移出分母"},
        "case_count": len(details),
        "summary": summarize(details),
        "code_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                        ("datamind/rag_engine.py", "datamind/agent.py", "scripts/eval_faithfulness.py")},
        "cases": details,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                                 encoding="utf-8")
    summary = report["summary"]
    print(f"逐结论忠实度：支持 {summary['conclusion_supported']}/{summary['conclusion_total']} "
          f"= {summary['faithfulness_rate']}；矛盾 {summary['conclusion_contradicted']}；"
          f"无依据 {summary['conclusion_no_evidence']}")
    print(f"拒答正确：{summary['refusal_correct']}/{summary['refusal_cases']}；"
          f"误拒：{summary['false_refusal_cases']}/{summary['answerable_cases']}；"
          f"上游错误：{summary['upstream_error_cases']}/{len(details)} → {args.output}")
    return 1 if summary["upstream_error_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
