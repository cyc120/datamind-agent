"""B3 澄清闭环验收：缺项检测 → 用户补充 → 补槽合并 → 落到问句帧。

两种模式：

* ``--offline``（默认，不调用上游模型）：用冻结的「用户补充话术」做确定性 stub 补槽器，
  补槽后调用 ``datamind/sql_engine.py:query_frame()``（只读）判定是否真的落到问句帧。
  同时评测两条臂：
    - ``detector``：本文件的六类结构化缺项检测（time_range / metric / grain /
      document_version / object_alias）；
    - ``deployed``：线上 ``/v1/chat`` 实际使用的规则式 ``sql_engine.clarify()``。
* ``--live``：真实调用 ``http://127.0.0.1:8000/v1/chat``，逐例记录轮次、状态与错误码。

纪律：拒答/超时/上游错误单列但不移出分母；零分母报 N/A；失败样本全部保留。

澄清槽位契约（缺项检测 / 候选值 / 追问话术）的**唯一实现**在第 1 轮复查 L4 之后已下沉到
生产包 ``datamind/clarification.py``；本模块只做再导出，保持既有调用面（测试与文档引用
``scripts.eval_clarification.clarification_contract*`` 的写法不变），服务端不再在请求期
依赖 ``scripts/`` 下的评测脚本。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUITE_PATH = ROOT / "data/evaluation/clarification_v1.json"
REPORT_PATH = ROOT / "reports/clarification_v1.json"
META_DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"
MAX_ROUNDS = 3

#: 槽位契约（常量、正则、检测器、候选值、投影）唯一实现在 ``datamind/clarification.py``。
#: 这里只再导出，既保证本脚本既有调用面不变，也保证不存在第二份槽位表/正则表。
from datamind.clarification import (  # noqa: E402  (需先插入 ROOT 到 sys.path)
    CONTRACT_VERSION,
    DEFAULT_PRODUCTS,
    SLOT_ORDER,
    SLOT_QUESTIONS,
    clarification_contract,
    clarification_contract_for_answer,
    detect_missing_slots,
    has_time,
    rule_slots_for_answer,
    slot_candidates,
)


def merge_supplement(question: str, values: dict) -> str:
    """把一轮用户补充合并进原问题：本轮补充必须覆盖历史缺项（累加，不覆盖前轮）。"""
    parts = [str(values[slot]) for slot in SLOT_ORDER if values.get(slot)]
    return (question + " " + " ".join(parts)).strip() if parts else question


def merge_slot_state(history: dict, new_values: dict) -> dict:
    """跨轮槽位状态：新值覆盖同槽旧值，其余历史槽位保留。"""
    merged = dict(history)
    merged.update({k: v for k, v in new_values.items() if v})
    return merged


def stub_reply(case: dict, remaining_slots: list[str]) -> tuple[dict, list[str]]:
    """确定性 stub 补槽器：一问一答，每轮只补第一个「系统问过且冻结话术里已有」的槽位。"""
    frozen = case.get("expected_slot_values") or {}
    for slot in remaining_slots:
        if frozen.get(slot):
            return {slot: frozen[slot]}, []
    return {}, list(remaining_slots)


def _frame_checks(question: str, catalog, frame_expectation: list[dict]) -> dict:
    """用 query_frame 契约判定补槽是否落到问句帧。返回 (结果, 明细)。"""
    from datamind.sql_engine import query_frame

    candidates = catalog.retrieve(question)["candidates"]
    frame = query_frame(question, catalog, candidates)
    metric_ids = [c["id"] for c in candidates if c["kind"] == "metric"]
    checks, na = [], []
    for expectation in frame_expectation:
        kind = expectation["kind"]
        if kind == "not_representable":
            na.append(expectation.get("reason", ""))
            continue
        if kind == "metrics_contains":
            ok = all(m in metric_ids and m in frame.get("metrics", []) for m in expectation["ids"])
            checks.append({"check": kind, "expected": expectation["ids"], "ok": ok,
                           "observed": metric_ids})
        elif kind == "time_filter":
            observed = [t.get("from") for t in frame.get("time_filters", [])]
            checks.append({"check": kind, "expected": expectation["expect_from"], "ok":
                           expectation["expect_from"] in observed, "observed": observed})
        elif kind == "intent_is":
            checks.append({"check": kind, "expected": expectation["intent"], "ok":
                           frame.get("intent") == expectation["intent"], "observed": frame.get("intent")})
        elif kind == "candidates_contains":
            ok = any(c["kind"] == expectation["kind_id"] and c["id"] == expectation["id"]
                     for c in candidates)
            checks.append({"check": kind, "expected": expectation["id"], "ok": ok,
                           "observed": [(c["kind"], c["id"]) for c in candidates[:8]]})
        else:
            checks.append({"check": kind, "ok": False, "observed": "unknown_check_kind"})
    return {"checks": checks, "na_reasons": na, "frame": frame,
            "candidates": [(c["kind"], c["id"]) for c in candidates[:8]]}, na


def run_offline_arm(suite: dict, catalog, arm: str) -> dict:
    """离线跑一条臂。arm ∈ {detector, deployed}。"""
    from datamind.sql_engine import clarify

    details = []
    for case in suite["cases"]:
        question = case["question"]
        if arm == "detector":
            system_slots = detect_missing_slots(question)
        else:
            message = clarify(question)
            system_slots = rule_slots_for_answer(message) if message else []
            # 规则式 clarify() 的第三种话术（写操作拒绝）不是缺项澄清。
            if message and not system_slots and "只支持查询" in message:
                system_slots = []
        asked = bool(system_slots)
        rounds = 0
        values: dict = {}
        remaining = list(system_slots)
        unresolved: list[str] = []
        merged = question
        while remaining and rounds < MAX_ROUNDS:
            reply, missing = stub_reply(case, remaining)
            if missing:
                unresolved = missing
                break
            values = merge_slot_state(values, reply)
            merged = merge_supplement(question, values)
            rounds += 1
            remaining = [s for s in (detect_missing_slots(merged) if arm == "detector"
                                     else rule_slots_for_answer(clarify(merged) or ""))
                         if s in system_slots or s in values]
        expectation = case.get("frame_expectation") or []
        frame_result, _ = _frame_checks(merged, catalog, expectation)
        resolvable = [c for c in frame_result["checks"]]
        na_reasons = list(frame_result["na_reasons"])
        if resolvable:
            landed = all(c["ok"] for c in resolvable)
        else:
            landed = None
        if case["expected_action"] == "clarify" and not asked:
            # 系统根本没问 → 「补槽是否落到帧」无从谈起，记 N/A 而不是拿原问题的帧冒充通过。
            landed = None
            na_reasons.append("SYSTEM_DID_NOT_ASK")
        details.append({
            "case_id": case["case_id"], "category": case["category"], "question": question,
            "expected_action": case["expected_action"],
            "expected_missing_slots": case["expected_missing_slots"],
            "system_missing_slots": system_slots, "asked": asked,
            "supplement_rounds": rounds, "supplement_values": values,
            "merged_question": merged, "unresolved_slots": unresolved,
            "frame_checks": frame_result["checks"], "frame_na_reasons": na_reasons,
            "frame_landed": landed, "frame_observed_candidates": frame_result["candidates"],
            "must_not_ask": bool(case.get("must_not_ask")),
        })
    return {"arm": arm, "cases": details, "metrics": _metrics(details)}


def _rate(numerator: int, denominator: int):
    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "rate": "N/A"}
    return {"numerator": numerator, "denominator": denominator, "rate": round(numerator / denominator, 4)}


def _metrics(details: list[dict]) -> dict:
    clarify_cases = [d for d in details if d["expected_action"] == "clarify"]
    answer_cases = [d for d in details if d["expected_action"] == "answer"]
    success = [d for d in clarify_cases if d["asked"] and not d["unresolved_slots"]
               and d["frame_landed"] is not False and d["supplement_rounds"] >= 1]
    unnecessary = [d for d in answer_cases if d["asked"]]
    frame_cases = [d for d in clarify_cases if d["frame_landed"] is not None]
    frame_ok = [d for d in frame_cases if d["frame_landed"]]
    answer_frame_cases = [d for d in answer_cases if d["frame_landed"] is not None]
    answer_frame_ok = [d for d in answer_frame_cases if d["frame_landed"]]
    rounds = [d["supplement_rounds"] for d in clarify_cases]
    asked_rounds = [d["supplement_rounds"] for d in clarify_cases if d["asked"]]
    return {
        "clarification_success": _rate(len(success), len(clarify_cases)),
        "unnecessary_clarification": {
            "absolute": len(unnecessary),
            "share_of_answer_cases": _rate(len(unnecessary), len(answer_cases)),
            "share_of_all_cases": _rate(len(unnecessary), len(details)),
            "answer_case_ids": [d["case_id"] for d in unnecessary],
        },
        "average_clarification_rounds": {
            "value": round(sum(rounds) / len(rounds), 4) if rounds else "N/A",
            "total_rounds": sum(rounds), "denominator": len(rounds),
            "value_when_asked": (round(sum(asked_rounds) / len(asked_rounds), 4)
                                 if asked_rounds else "N/A"),
            "asked_denominator": len(asked_rounds),
        },
        "slot_landing": _rate(len(frame_ok), len(frame_cases)),
        "slot_landing_na_cases": [d["case_id"] for d in clarify_cases if d["frame_landed"] is None],
        "answer_frame_ok": _rate(len(answer_frame_ok), len(answer_frame_cases)),
        "asked_cases": {"count": sum(1 for d in details if d["asked"]),
                        "ids": [d["case_id"] for d in details if d["asked"]]},
        "counts": {"clarify_expected": len(clarify_cases), "answer_expected": len(answer_cases),
                   "total": len(details)},
    }


def _post_chat(session: dict, question: str, timeout_s: float) -> dict:
    payload = json.dumps({"question": question, "mode": "auto"}).encode()
    request = urllib.request.Request(session["base_url"] + "/v1/chat", data=payload,
                                     headers={"Content-Type": "application/json",
                                              "X-CSRF-Token": session["csrf"]})
    with session["opener"].open(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode())


def open_live_session(base_url: str, timeout_s: float, username: str | None = None,
                      password: str | None = None) -> dict:
    """打开一个带身份与 CSRF 的实时会话。

    ``/v1/chat`` 默认拒绝匿名与跨站请求（identity + CSRF），所以现场验收必须真的登录。
    没有给账号时按 ``tests/auth_fixtures.py`` 的同一机制临时开户（reviewer 角色），
    跑完即删；这条写操作只落在元数据库的 ``auth_users`` 一行，不碰零售/工业演示数据。
    """
    import http.cookiejar
    import secrets
    import uuid

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    temporary_user_id = None
    if not (username and password):
        import psycopg2
        from datamind.agent import _write_dsn
        from datamind.auth import provision

        username = "wp4-live-" + uuid.uuid4().hex[:12]
        password = secrets.token_urlsafe(24)
        temporary_user_id = provision(username, "WP4 现场验收",
                                      password, ["reviewer"], _write_dsn())
    body = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(base_url + "/v1/auth/login", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=timeout_s) as response:
            csrf = json.loads(response.read().decode())["csrf_token"]
    except Exception:
        if temporary_user_id:
            close_live_session({"temporary_user_id": temporary_user_id})
        raise
    return {"base_url": base_url, "opener": opener, "csrf": csrf,
            "temporary_user_id": temporary_user_id, "username": username}


def close_live_session(session: dict) -> None:
    if not session.get("temporary_user_id"):
        return
    try:
        import psycopg2
        from datamind.agent import _write_dsn

        with psycopg2.connect(_write_dsn()) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM auth_users WHERE user_id=%s", (session["temporary_user_id"],))
    except Exception:
        pass


def run_live_arm(suite: dict, session: dict, timeout_s: float, case_ids: list[str] | None) -> dict:
    details = []
    for case in suite["cases"]:
        if case_ids and case["case_id"] not in case_ids:
            continue
        case_id = case["case_id"]
        detail = {"case_id": case_id, "category": case["category"],
                  "expected_action": case["expected_action"], "asked": False,
                  "supplement_rounds": 0, "upstream_or_timeout": False,
                  "statuses": [], "error_codes": [], "final_status": None}
        question, merged = case["question"], case["question"]
        values: dict = {}
        try:
            for _round in range(MAX_ROUNDS + 1):
                started = time.perf_counter()
                payload = _post_chat(session, merged, timeout_s)
                detail["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
                status, code = payload.get("status"), payload.get("error_code")
                detail["statuses"].append(status)
                if code:
                    detail["error_codes"].append(code)
                if status == "NEEDS_CLARIFICATION":
                    detail["asked"] = True
                    detail["clarification_text"] = payload.get("answer")
                    detail["clarification_contract_keys"] = sorted(
                        key for key in payload if key.startswith("clarification_"))
                    slots = (payload.get("clarification_missing_slots")
                             or rule_slots_for_answer(payload.get("answer") or ""))
                    frozen = case.get("expected_slot_values") or {}
                    reply = {slot: frozen[slot] for slot in slots if frozen.get(slot)}
                    if not reply:
                        detail["failure_reason"] = "NO_FROZEN_REPLY_FOR_SLOTS"
                        detail["missing_slots"] = slots
                        break
                    values = merge_slot_state(values, reply)
                    merged = merge_supplement(question, values)
                    detail["supplement_rounds"] += 1
                    continue
                detail["final_status"] = status
                if code and (str(code).startswith("MODEL") or code in {
                        "SCHEMA_NOT_FOUND", "SQL_EXECUTION_ERROR"}):
                    detail["upstream_or_timeout"] = True
                break
        except urllib.error.HTTPError as exc:
            detail["failure_reason"] = "HTTP_%d" % exc.code
            detail["http_status"] = exc.code
            detail["upstream_or_timeout"] = exc.code in (429, 500, 502, 503, 504)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            detail["upstream_or_timeout"] = True
            detail["failure_reason"] = "TRANSPORT_" + type(exc).__name__
        details.append(detail)
    # live 臂的指标口径：expected_action=clarify 的成功 = 系统问过且最终 COMPLETED；
    # expected_action=answer 的不必要澄清 = 系统问过。
    clarify_cases = [d for d in details if d["expected_action"] == "clarify"]
    answer_cases = [d for d in details if d["expected_action"] == "answer"]
    success = [d for d in clarify_cases if d["asked"] and d["final_status"] == "COMPLETED"]
    unnecessary = [d for d in answer_cases if d["asked"]]
    rounds = [d["supplement_rounds"] for d in clarify_cases]
    metrics = {
        "clarification_success": _rate(len(success), len(clarify_cases)),
        "unnecessary_clarification": {
            "absolute": len(unnecessary),
            "share_of_answer_cases": _rate(len(unnecessary), len(answer_cases)),
            "share_of_all_cases": _rate(len(unnecessary), len(details)),
            "answer_case_ids": [d["case_id"] for d in unnecessary],
        },
        "average_clarification_rounds": {
            "value": round(sum(rounds) / len(rounds), 4) if rounds else "N/A",
            "total_rounds": sum(rounds), "denominator": len(rounds),
        },
        "upstream_or_timeout_cases": [d["case_id"] for d in details if d["upstream_or_timeout"]],
        "asked_cases": {"count": sum(1 for d in details if d["asked"]),
                        "ids": [d["case_id"] for d in details if d["asked"]]},
        # 线上澄清话术无法唯一映射到冻结槽位的用例：说明「系统确实反问了，但补充话术对不上契约」。
        # 这不是把失败移出分母，而是把失败原因分类，便于区分"没问"与"问了但接不上"。
        "asked_but_unmapped_slots": [d["case_id"] for d in details
                                     if d.get("failure_reason") == "NO_FROZEN_REPLY_FOR_SLOTS"],
        "final_status_counts": {status: sum(1 for d in details if d["final_status"] == status)
                                for status in sorted({d["final_status"] for d in details},
                                                     key=lambda value: str(value))},
        "counts": {"clarify_expected": len(clarify_cases), "answer_expected": len(answer_cases),
                   "total": len(details)},
    }
    return {"arm": "live", "cases": details, "metrics": metrics}


def read_products(dsn: str) -> list[str]:
    """只读读取演示库产品名，作为对象别名候选的权威来源。"""
    import psycopg2

    retail_dsn = dsn.replace("dbname=datamind_meta", "dbname=demo_retail")
    try:
        with psycopg2.connect(retail_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT product_name FROM products ORDER BY product_name")
            names = [row[0] for row in cur.fetchall()]
        return names or list(DEFAULT_PRODUCTS)
    except Exception:
        return list(DEFAULT_PRODUCTS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="确定性 stub 补槽，不调用上游模型")
    parser.add_argument("--live", action="store_true", help="真实调用 /v1/chat")
    parser.add_argument("--limit", type=int, default=0, help="live 模式只跑前 N 条（0=全部）")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default=None, help="live 登录账号；缺省时临时开户并跑完删除")
    parser.add_argument("--password", default=None)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--dsn", default=META_DSN)
    args = parser.parse_args()
    if not args.offline and not args.live:
        args.offline = True

    from datamind.catalog import Catalog

    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    started = time.time()
    report = {
        "suite_version": suite["suite_version"],
        "contract_version": suite["contract_version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(suite["cases"]),
        "modes": [],
    }
    products = read_products(args.dsn)
    report["alias_candidates_source"] = {
        "count": len(products), "sample": products[:6],
        "source": "demo_retail.products 只读查询",
    }
    if args.offline:
        catalog = Catalog(args.dsn, "demo_retail")
        report["modes"].append(run_offline_arm(suite, catalog, "detector"))
        report["modes"].append(run_offline_arm(suite, catalog, "deployed"))
    if args.live:
        case_ids = [c["case_id"] for c in suite["cases"]][:args.limit] if args.limit else None
        session = None
        try:
            session = open_live_session(args.base_url, args.timeout, args.username, args.password)
            report["live_session"] = {"base_url": args.base_url, "username": session["username"],
                                      "temporary_account": bool(session["temporary_user_id"])}
            report["modes"].append(run_live_arm(suite, session, args.timeout, case_ids))
        finally:
            if session:
                close_live_session(session)
    report["elapsed_s"] = round(time.time() - started, 2)

    failures = []
    for mode in report["modes"]:
        for case in mode["cases"]:
            if case["expected_action"] == "clarify":
                if mode["arm"] == "live":
                    ok = bool(case.get("asked")) and case.get("final_status") == "COMPLETED"
                else:
                    ok = bool(case.get("asked")) and not case.get("unresolved_slots") \
                        and case.get("frame_landed") is not False
                if not ok:
                    failures.append({"arm": mode["arm"], "case_id": case["case_id"],
                                     "reason": case.get("failure_reason") or
                                     ("ASKED_BUT_FRAME_NOT_LANDED" if case.get("asked")
                                      else "NO_CLARIFICATION_ASKED")})
            elif case["expected_action"] == "answer" and case.get("asked"):
                failures.append({"arm": mode["arm"], "case_id": case["case_id"],
                                 "reason": "UNNECESSARY_CLARIFICATION"})
    report["failures"] = failures
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output),
                      "modes": {m["arm"]: m["metrics"] for m in report["modes"]},
                      "failures": len(failures)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
