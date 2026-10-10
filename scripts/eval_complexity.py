"""B8 文档复杂度评估：分数 → 策略 → 实际解析耗时/失败率 对照评测。

做法（不是"只算分"）：

1. 对 ``data/evaluation/complexity_v1.json`` 的每份材料先算 **解析前** 复杂度分与策略
   （``datamind/complexity.py``，只读观测，不跑 OCR）。
2. 然后**真的用这条策略去解析**：
   - ``policy`` 臂：按 ``policy.ocr_enabled`` 决定是否允许像素 OCR；块数按 ``max_blocks`` 截断；
   - ``no_policy_ocr_off`` 臂（对照）：一律不跑 OCR、不设块预算——用来回答"没有策略会怎样"。
   两臂都在临时目录里解析，``PAGE_IMG_DIR`` 被重定向，**不写演示对象存储**。
3. 解析后把实测 OCR 逐块置信度与公式候选数回填，重算一次 **解析后** 分数，用于说明
   哪些维度只能在解析后才知道。
4. 报告「分数 → 策略 → 耗时/失败率」对照表，并做一次 Spearman 相关性判断：
   ``|rho| < 0.5`` 或方向为负时**如实写"未成立"**，不挑指标凑结论。

OHR-Bench 记录没有几何与逐行置信度，相关维度记 N/A 并在分数里报告 coverage；其解析成本是
确定性 JSON→块→切片，不与 PDF 的 OCR/公式成本放在同一条相关性里比较。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUITE_PATH = ROOT / "data/evaluation/complexity_v1.json"
REPORT_PATH = ROOT / "reports/complexity_v1.json"
BASELINE_DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"
CORRELATION_THRESHOLD = 0.5


def _spearman(pairs: list[tuple[float, float]]) -> dict:
    """Spearman 秩相关（并列取平均秩），只用标准库实现，便于复算。"""
    if len(pairs) < 3:
        return {"n": len(pairs), "rho": "N/A", "note": "样本不足 3 条，不做相关性判断"}

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        index = 0
        while index < len(order):
            end = index
            while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
                end += 1
            average = (index + end) / 2.0 + 1.0
            for position in range(index, end + 1):
                result[order[position]] = average
            index = end + 1
        return result

    xs = ranks([pair[0] for pair in pairs])
    ys = ranks([pair[1] for pair in pairs])
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return {"n": len(pairs), "rho": "N/A", "note": "某一维秩方差为 0，无法计算"}
    return {"n": len(pairs), "rho": round(cov / (var_x * var_y) ** 0.5, 4)}


def _ocr_gate(policy: dict):
    """按策略包住像素 OCR：策略关闭时返回空块并留下稳定错误码，不静默丢页。"""
    from ingestion import ocr as ocr_module

    original = ocr_module.page_blocks
    if policy.get("ocr_enabled"):
        def allowed(page, page_no, **kwargs):
            blocks, detail = original(page, page_no, **kwargs)
            detail = dict(detail)
            detail["policy"] = "ocr_enabled"
            return blocks, detail
        return allowed

    def denied(page, page_no, **kwargs):
        return [], {"page_no": page_no, "route": "pixel_ocr_skipped_by_policy",
                    "error_code": "OCR_DISABLED_BY_POLICY", "policy": "ocr_disabled"}
    return denied


def measure_pdf(path: Path, doc_id: str, policy: dict, page_dir: Path) -> dict:
    """在策略门下真实解析一份 PDF，返回耗时与失败面。"""
    from ingestion import ingest

    started = time.perf_counter()
    detail, error = {}, None
    with patch.object(ingest, "PAGE_IMG_DIR", page_dir), \
            patch("ingestion.ocr.page_blocks", _ocr_gate(policy)):
        try:
            blocks, page_count, detail = ingest.parse_pdf(path, doc_id, include_quality=True)
        except Exception as exc:  # 解析失败必须留在报告里，不能吞掉
            return {"elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "failed": True, "error": type(exc).__name__ + ": " + str(exc)[:200],
                    "blocks": 0, "chars": 0, "page_count": None, "ocr_pages": 0,
                    "ocr_confidences": [], "formula_candidates": 0, "parse_status": "exception",
                    "truncated": False, "skipped_ocr_pages": 0}
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    budget = int(policy.get("max_blocks") or 0)
    truncated = bool(budget and len(blocks) > budget)
    kept = blocks[:budget] if truncated else blocks
    pages = detail.get("pages", [])
    ocr_confidences = [float(b["ocr_confidence"]) for b in blocks
                       if b.get("ocr_confidence") is not None]
    return {"elapsed_ms": elapsed_ms, "failed": False, "error": error,
            "blocks": len(kept), "blocks_before_budget": len(blocks), "truncated": truncated,
            "chars": sum(len(b.get("text") or "") for b in kept),
            "page_count": page_count,
            "ocr_pages": sum(1 for p in pages if p.get("route") == "pixel_ocr"),
            "skipped_ocr_pages": sum(1 for p in pages
                                     if p.get("error_code") == "OCR_DISABLED_BY_POLICY"),
            "ocr_confidences": ocr_confidences,
            "formula_candidates": len(detail.get("formula_candidates") or []),
            "parse_status": detail.get("parse_status"),
            "empty_result": not any(b.get("text") for b in kept)}


def measure_ohr(path: Path, doc_id: str) -> dict:
    """OHR 记录的确定性解析成本：JSON → 块 → 切片（无 OCR、无公式识别）。"""
    from ingestion import ingest

    started = time.perf_counter()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        block = ingest.ohr_block(record)
        chunks = ingest.build_chunks([block], doc_id)
        return {"elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "failed": False,
                "blocks": 1, "chars": len(block.get("text") or ""), "page_count": 1,
                "chunks": len(chunks), "ocr_pages": 0, "skipped_ocr_pages": 0,
                "ocr_confidences": [], "formula_candidates": 0, "parse_status": "unsupported",
                "known_gap": ingest.OHR_LOCATOR_GAP, "truncated": False, "empty_result": False}
    except Exception as exc:
        return {"elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "failed": True,
                "error": type(exc).__name__ + ": " + str(exc)[:200], "blocks": 0, "chars": 0,
                "page_count": None, "ocr_pages": 0, "skipped_ocr_pages": 0,
                "ocr_confidences": [], "formula_candidates": 0, "parse_status": "exception",
                "truncated": False, "empty_result": True}


def expectation_mismatches(entry: dict, score_result: dict, policy: dict) -> list[dict]:
    """冻结期望与实际结果的差额；不符合的留在报告里，不改期望、不改指标口径。"""
    mismatches = []
    if score_result["band"] != entry["expected_band"]:
        mismatches.append({"doc_id": entry["doc_id"], "kind": "BAND_MISMATCH",
                           "expected": entry["expected_band"], "observed": score_result["band"],
                           "score": score_result["complexity_score"]})
    for key, value in (entry.get("expected_policy") or {}).items():
        if policy.get(key) != value:
            mismatches.append({"doc_id": entry["doc_id"], "kind": "POLICY_MISMATCH",
                               "expected": {key: value}, "observed": {key: policy.get(key)}})
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--skip-counterfactual", action="store_true",
                        help="跳过 no_policy_ocr_off 对照臂（只跑策略臂）")
    args = parser.parse_args()

    from datamind import complexity

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    documents, failures, mismatches = [], [], []
    with tempfile.TemporaryDirectory(prefix="complexity-eval-") as temporary:
        page_dir = Path(temporary) / "page_images"
        page_dir.mkdir(parents=True, exist_ok=True)
        for entry in suite["documents"]:
            path = ROOT / entry["path"]
            if not path.is_file():
                failures.append({"doc_id": entry["doc_id"], "reason": "MATERIAL_MISSING",
                                 "path": entry["path"]})
                continue
            if entry["kind"] == "pdf":
                observation = complexity.observe_pdf(path)
                pre = complexity.score(observation)
                policy = complexity.policy_for(pre, observation)
                measured = measure_pdf(path, entry["doc_id"], policy, page_dir)
                if measured["failed"]:
                    failures.append({"doc_id": entry["doc_id"], "reason": "PARSE_EXCEPTION",
                                     "detail": measured.get("error")})
                elif measured.get("empty_result"):
                    failures.append({"doc_id": entry["doc_id"], "reason": "PARSE_EMPTY_TEXT",
                                     "detail": "块预算内没有任何文本，策略下的解析结果为空"})
                post_observation = complexity.observe_pdf(
                    path, ocr_confidences=measured["ocr_confidences"] or None,
                    formula_candidates=measured["formula_candidates"])
                post = complexity.score(post_observation)
                counterfactual = None
                if not args.skip_counterfactual:
                    counterfactual = measure_pdf(path, entry["doc_id"] + "-noc",
                                                 {"ocr_enabled": False, "max_blocks": 0}, page_dir)
            else:
                record = json.loads(path.read_text(encoding="utf-8"))
                observation = complexity.observe_ohr_record(record)
                pre = complexity.score(observation)
                policy = complexity.policy_for(pre, observation)
                measured = measure_ohr(path, entry["doc_id"])
                post = pre
                counterfactual = None
                if measured.get("known_gap"):
                    # 已知缺口单列：契约无法表示没有 bbox 的块，这与复杂度无关。
                    measured["known_gap_note"] = ("OHR_LOCATOR_GAP：契约不能表示无坐标块，"
                                                  "解析状态 unsupported 是既有设计，不计入复杂度失败")
            expected_band = entry["expected_band"]
            expected_policy = entry.get("expected_policy") or {}
            band_ok = pre["band"] == expected_band
            policy_ok = all(policy.get(key) == value for key, value in expected_policy.items())
            mismatches.extend(expectation_mismatches(entry, pre, policy))
            documents.append({
                "doc_id": entry["doc_id"], "kind": entry["kind"], "path": entry["path"],
                "declared_type": entry["declared_type"],
                "expected_band": expected_band, "observed_band": pre["band"],
                "band_expectation_ok": band_ok, "expected_policy": expected_policy,
                "policy_ok": policy_ok,
                "score_pre_parse": pre["complexity_score"], "band_pre_parse": pre["band"],
                "score_post_parse": post["complexity_score"], "band_post_parse": post["band"],
                "coverage_pre_parse": pre["coverage"],
                "missing_dimensions_pre_parse": pre["missing_dimensions"],
                "policy": policy, "measurement": measured,
                "counterfactual_ocr_off": counterfactual,
                "dimensions_pre_parse": [
                    {key: dimension[key] for key in
                     ("id", "weight", "available", "raw", "score", "contribution_normalized")}
                    for dimension in pre["dimensions"]],
                "dimensions_post_parse": [
                    {key: dimension[key] for key in
                     ("id", "available", "raw", "score", "contribution_normalized")}
                    for dimension in post["dimensions"]],
            })

    pdf_rows = [row for row in documents if row["kind"] == "pdf"]
    pairs_ms = [(row["score_pre_parse"], row["measurement"]["elapsed_ms"]) for row in pdf_rows]
    pairs_per_page = [(row["score_pre_parse"],
                       row["measurement"]["elapsed_ms"] / max(row["measurement"]["page_count"] or 1, 1))
                      for row in pdf_rows]
    correlation = {
        "primary": {"target": "elapsed_ms", "spearman": _spearman(pairs_ms)},
        "secondary": {"target": "elapsed_ms_per_page", "spearman": _spearman(pairs_per_page)},
        "threshold": CORRELATION_THRESHOLD,
        "scope": "仅 %d 份 PDF（OHR JSON 的解析成本与 OCR/公式成本不可比，单列）" % len(pdf_rows),
    }
    primary_rho = correlation["primary"]["spearman"].get("rho")
    if isinstance(primary_rho, (int, float)) and primary_rho >= CORRELATION_THRESHOLD:
        correlation["verdict"] = "成立"
        correlation["verdict_reason"] = ("解析前复杂度分与实测解析耗时 Spearman rho=%.4f ≥ %.2f"
                                         % (primary_rho, CORRELATION_THRESHOLD))
    else:
        correlation["verdict"] = "未成立"
        correlation["verdict_reason"] = ("解析前复杂度分与实测解析耗时的 Spearman rho=%s，"
                                         "未达到 ≥%.2f 的判定门槛；如实记录，不换指标凑结论"
                                         % (primary_rho, CORRELATION_THRESHOLD))
    band_costs = {}
    for row in pdf_rows:
        band = row["observed_band"]
        band_costs.setdefault(band, []).append(row["measurement"]["elapsed_ms"])
    correlation["band_mean_elapsed_ms"] = {band: round(sum(values) / len(values), 1)
                                           for band, values in sorted(band_costs.items())}
    correlation["band_documents"] = {band: len(values) for band, values in sorted(band_costs.items())}

    policy_delta = []
    for row in documents:
        counterfactual = row.get("counterfactual_ocr_off")
        if not counterfactual:
            continue
        policy_delta.append({
            "doc_id": row["doc_id"], "ocr_enabled": row["policy"]["ocr_enabled"],
            "policy_chars": row["measurement"]["chars"],
            "ocr_off_chars": counterfactual["chars"],
            "policy_blocks": row["measurement"]["blocks"],
            "ocr_off_blocks": counterfactual["blocks"],
            "ocr_off_skipped_pages": counterfactual["skipped_ocr_pages"],
            "policy_elapsed_ms": row["measurement"]["elapsed_ms"],
            "ocr_off_elapsed_ms": counterfactual["elapsed_ms"],
            "text_lost_when_ocr_off": row["measurement"]["chars"] - counterfactual["chars"],
        })

    report = {
        "suite_version": suite["suite_version"], "rules_version": complexity.RULES_VERSION,
        "policy_version": complexity.POLICY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": suite["baseline_commit"],
        "document_count": len(documents),
        "band_rule": suite["band_rule"],
        "expectation_basis": suite["expectation_basis"],
        "documents": documents,
        "correlation": correlation,
        "policy_vs_no_policy": policy_delta,
        "expectation_mismatches": mismatches,
        "failures": failures,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "documents": len(documents),
                      "correlation": correlation["verdict"],
                      "spearman_elapsed_ms": correlation["primary"]["spearman"],
                      "band_mean_elapsed_ms": correlation["band_mean_elapsed_ms"],
                      "mismatches": len(mismatches), "failures": len(failures)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
