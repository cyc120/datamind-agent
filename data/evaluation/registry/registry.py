# -*- coding: utf-8 -*-
"""WP1-a 冻结评测登记（append-only registry）—— 离线部分，只读访问 datamind_meta。

设计纪律
--------
1. **只读**：本模块只对元数据库执行 `SELECT`，连接强制 `readonly=True`；
   写入仅发生在仓内的 `data/evaluation/registry/<suite>_v<n>.json`，且用
   `open(path, "x")` 原子创建 —— 已发布的登记文件**不可被覆盖**（append-only）。
2. **题面差异必须炸**：`check_registry()` 在「登记文件 vs 库内题面」不一致时抛
   `RegistryMismatch`，并在 `diff` 里逐字段给出明细；它不会写库、也不会改题。
3. **摘要只覆盖评测相关字段**：登记冻结 `question / gold / 筛选字段`；`reviewed_by`
   这类纯审批元数据不进冻结体，避免「只是标了审阅人」就触发版本不一致。
4. **摘要与时间解耦**：整体 `digest` 只覆盖 `suite / suite_version / data_version /
   data_versions / source / cases`，不含 `created_at`（时间戳每次 freeze 都不同），
   因此同一份题面重复冻结得到同一个 digest，可做幂等比对。
5. 本登记**不调用上游模型**，`model / prompt_version` 一律为 `None`。

统一评测报告 schema（`validate_report`）
---------------------------------------
逐题字段必须是：`case_id / status / sql / rows / citations / verdict / failure_reason`
（字段必须存在，取值可为 `null`）；汇总字段必须是：`total / passed / failed /
refused / errors / accuracy / refusal_rate / failure_counts`。零分母必须报 `"N/A"`，
不得用 `0` 或 `1.0` 冒充。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SUITES = ("nl2sql", "rag", "robustness")
SOURCE = "datamind_meta.evaluation_cases"
REGISTRY_DIR = Path(__file__).resolve().parent
DEFAULT_DSN = ("host=127.0.0.1 port=5432 dbname=datamind_meta "
               "user=readonly_user password=readonly_dev")

#: 冻结进登记的行字段（database column -> frozen key）。`reviewed_by` 有意排除。
CASE_FIELDS = ("case_id", "question", "category", "difficulty", "expected_route", "gold",
               "clarification_label", "failure_category", "data_version", "required_evidence")

#: 参与整体 digest 的顶层字段。
DIGEST_KEYS = ("suite", "suite_version", "data_version", "data_versions", "source", "cases")

_SELECT_CASES = (
    "SELECT case_id, question, category, difficulty, expected_route, gold_sql, "
    "gold_answer_claims, required_evidence, clarification_label, failure_category, "
    "data_version FROM evaluation_cases WHERE suite = %s ORDER BY case_id"
)

REPORT_SCHEMA_VERSION = "eval-report-v1"
REPORT_CASE_FIELDS = ("case_id", "status", "sql", "rows", "citations", "verdict", "failure_reason")
REPORT_SUMMARY_FIELDS = ("total", "passed", "failed", "refused", "errors", "accuracy",
                         "refusal_rate", "failure_counts")
REPORT_VERDICTS = ("pass", "fail", "abstain", "error", "not_attempted")
#: 这些 verdict 必须带非空 failure_reason，用于失败分类计数。
REPORT_NEEDS_REASON = ("fail", "error")


class RegistryError(RuntimeError):
    """登记相关错误基类。"""


class RegistryExistsError(RegistryError):
    """append-only：目标登记文件已存在，拒绝覆盖。"""


class RegistryMismatch(RegistryError):
    """登记文件与库内题面不一致。`diff` 为逐字段明细。"""

    def __init__(self, diff: dict):
        self.diff = diff
        super().__init__(render_diff(diff))


# ---------------------------------------------------------------- 小工具

def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def case_digest(case: dict) -> str:
    """单例摘要；`case_digest` 自身不参与计算（避免自指）。"""
    return _sha256(_canonical({k: v for k, v in case.items() if k != "case_digest"}))


def registry_digest(payload: dict) -> str:
    """整体摘要；只覆盖 DIGEST_KEYS，与 created_at / commit / model 解耦。"""
    missing = [key for key in DIGEST_KEYS if key not in payload]
    if missing:
        raise RegistryError("登记体缺少字段，无法计算 digest：" + ", ".join(missing))
    return _sha256(_canonical({key: payload[key] for key in DIGEST_KEYS}))


def ratio(numerator: int, denominator: int):
    """零分母报 ``"N/A"``（不返回 0，也不返回 100%）。"""
    if not denominator or denominator <= 0:
        return "N/A"
    return numerator / denominator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- 只读取数

def connect(dsn: str | None = None):
    """只读连接；`readonly=True` 由服务端强制，任何写 SQL 都会失败。"""
    import psycopg2

    conn = psycopg2.connect(dsn or DEFAULT_DSN)
    try:
        conn.set_session(readonly=True, autocommit=True)
    except Exception:
        conn.rollback()
    return conn


def normalize_case(row) -> dict:
    """库行 -> 冻结体（只保留 CASE_FIELDS，且必须齐全）。"""
    claims = row["gold_answer_claims"]
    if isinstance(claims, str):
        try:
            claims = json.loads(claims)
        except ValueError:
            pass  # 保留原字符串，宁可如实记录也不静默丢弃
    return {
        "case_id": row["case_id"],
        "question": row["question"],
        "category": row["category"],
        "difficulty": row["difficulty"],
        "expected_route": row["expected_route"],
        "gold": {"sql": row["gold_sql"], "claims": claims},
        "clarification_label": row["clarification_label"],
        "failure_category": row["failure_category"],
        "data_version": row["data_version"],
        "required_evidence": row["required_evidence"],
    }


def fetch_cases(suite: str, dsn: str | None = None) -> list[dict]:
    """从 `evaluation_cases` 读一套题并规范化为冻结体；空集视为错误。"""
    if suite not in SUITES:
        raise RegistryError(f"未知 suite：{suite}（仅支持 {', '.join(SUITES)}）")
    from psycopg2.extras import RealDictCursor

    conn = connect(dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_SELECT_CASES, (suite,))
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        raise RegistryError(f"库内没有任何 {suite} 用例，拒绝冻结空集：{SOURCE}")
    cases = [normalize_case(row) for row in rows]
    ids = [case["case_id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise RegistryError(f"{suite} 存在重复 case_id，拒绝冻结：" + ", ".join(sorted(ids)))
    return cases


# ---------------------------------------------------------------- 冻结

def build_payload(suite: str, cases: list[dict], *, version: str = "v1",
                  created_at: str | None = None, commit: str | None = None,
                  model: str | None = None, prompt_version: str | None = None) -> dict:
    """构造登记体（纯函数，不触库、不落盘）。"""
    if suite not in SUITES:
        raise RegistryError(f"未知 suite：{suite}（仅支持 {', '.join(SUITES)}）")
    if not cases:
        raise RegistryError("拒绝冻结空集")
    if version and not version.startswith("v"):
        version = "v" + version
    frozen = []
    for case in cases:
        body = {key: case.get(key) for key in CASE_FIELDS}
        if not body["case_id"]:
            raise RegistryError("用例缺少 case_id，拒绝冻结")
        frozen.append({**body, "case_digest": case_digest(body)})
    frozen.sort(key=lambda item: item["case_id"])
    versions = sorted({case["data_version"] for case in frozen if case.get("data_version")})
    payload = {
        "suite": suite,
        "suite_version": f"{suite}-{version}",
        "data_version": versions[0] if len(versions) == 1 else ("multiple" if versions else None),
        "data_versions": versions,
        "source": SOURCE,
        "created_at": created_at or now_iso(),
        "commit": commit,
        "model": model,
        "prompt_version": prompt_version,
        "offline_freeze": True,
        "case_count": len(frozen),
        "cases": frozen,
    }
    payload["digest"] = registry_digest(payload)
    return payload


def registry_path(suite: str, version: str = "v1", out_dir: str | Path | None = None) -> Path:
    if version and not version.startswith("v"):
        version = "v" + version
    return Path(out_dir or REGISTRY_DIR) / f"{suite}_{version}.json"


def freeze(suite: str, *, dsn: str | None = None, version: str = "v1",
           out_dir: str | Path | None = None, commit: str | None = None,
           model: str | None = None, prompt_version: str | None = None,
           created_at: str | None = None, cases: list[dict] | None = None) -> tuple[Path, dict]:
    """写登记文件；**已存在即拒绝**（append-only，用新版本号）。"""
    path = registry_path(suite, version, out_dir)
    payload = build_payload(suite, cases if cases is not None else fetch_cases(suite, dsn),
                            version=version, created_at=created_at, commit=commit,
                            model=model, prompt_version=prompt_version)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 独占创建：即便有并发调用，也不会静默覆盖已发布版本。
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as error:
        raise RegistryExistsError(
            f"登记文件已存在，拒绝覆盖（append-only）：{path}；"
            f"若题面确有变更，请使用新版本号，例如 --version v2") from error
    return path, payload


# ---------------------------------------------------------------- 校验

def load_registry(path: str | Path) -> dict:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RegistryError(f"登记文件不存在：{path}") from error
    except ValueError as error:
        raise RegistryError(f"登记文件不是合法 JSON：{path}：{error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise RegistryError(f"登记文件结构非法（缺少 cases 数组）：{path}")
    for key in ("suite", "suite_version", "digest"):
        if key not in payload:
            raise RegistryError(f"登记文件缺少字段 {key}：{path}")
    return payload


def diff_payload(payload: dict, cases: list[dict]) -> dict:
    """逐字段比对；`cases` 为库内当前题面（或测试注入的同构数据）。"""
    registered = {case["case_id"]: case for case in payload["cases"]}
    live = {case["case_id"]: case for case in cases}
    diff = {
        "suite": payload.get("suite"),
        "suite_version": payload.get("suite_version"),
        "registry_cases": len(registered),
        "database_cases": len(live),
        "digest_ok": registry_digest(payload) == payload.get("digest"),
        "missing_in_db": sorted(set(registered) - set(live)),
        "extra_in_db": sorted(set(live) - set(registered)),
        "changed": [],
    }
    for case_id in sorted(set(registered) & set(live)):
        frozen_case, live_case = registered[case_id], live[case_id]
        for field in CASE_FIELDS:
            if field == "case_id":
                continue
            frozen_value = frozen_case.get(field)
            live_value = live_case.get(field)
            if frozen_value != live_value:
                diff["changed"].append({"case_id": case_id, "field": field,
                                        "registry": frozen_value, "database": live_value})
    diff["divergent"] = bool(diff["missing_in_db"] or diff["extra_in_db"]
                             or diff["changed"] or not diff["digest_ok"])
    return diff


def render_diff(diff: dict, limit: int = 20) -> str:
    lines = [f"[{diff.get('suite')}] 登记 {diff.get('registry_cases')} 例 / 库内 {diff.get('database_cases')} 例"
             f"（{diff.get('suite_version')}）"]
    if not diff.get("digest_ok"):
        lines.append("  文件整体 digest 与内容不符（登记文件被改动过或写入不完整）")
    if diff.get("missing_in_db"):
        lines.append(f"  库内缺失（登记有、库里没有）：{', '.join(diff['missing_in_db'])}")
    if diff.get("extra_in_db"):
        lines.append(f"  库内多出（登记没有、库里有）：{', '.join(diff['extra_in_db'])}")
    changed = diff.get("changed") or []
    for item in changed[:limit]:
        lines.append(f"  字段不一致 {item['case_id']}.{item['field']}："
                     f"登记={item['registry']!r} 库内={item['database']!r}")
    if len(changed) > limit:
        lines.append(f"  ...另有 {len(changed) - limit} 处字段不一致未展开")
    if not diff.get("divergent"):
        lines.append("  一致")
    return "\n".join(lines)


def check_registry(path: str | Path, *, dsn: str | None = None,
                   cases: list[dict] | None = None) -> dict:
    """校验登记文件与库内题面一致；不一致抛 `RegistryMismatch`（含 diff 明细）。"""
    payload = load_registry(path)
    suite = payload["suite"]
    live = cases if cases is not None else fetch_cases(suite, dsn)
    diff = diff_payload(payload, live)
    if diff["divergent"]:
        raise RegistryMismatch(diff)
    return {"suite": suite, "suite_version": payload.get("suite_version"), "path": str(path),
            "cases": len(payload["cases"]), "digest": payload.get("digest"), "diff": diff}


# ---------------------------------------------------------------- 报告 schema

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_report(payload) -> list[str]:
    """校验统一评测报告 schema，返回问题清单（空列表 = 通过）。"""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["报告顶层必须是 JSON 对象"]
    version = payload.get("schema_version")
    if version is not None and version != REPORT_SCHEMA_VERSION:
        problems.append(f"schema_version={version!r}，期望 {REPORT_SCHEMA_VERSION!r}")

    cases = payload.get("cases")
    if "cases" not in payload:
        problems.append("缺少顶层字段 cases")
    elif not isinstance(cases, list):
        problems.append("cases 必须是数组")
        cases = []
    for index, case in enumerate(cases):
        label = (case or {}).get("case_id") if isinstance(case, dict) else None
        label = label or f"#{index}"
        if not isinstance(case, dict):
            problems.append(f"用例 {label}：必须是对象")
            continue
        for field in REPORT_CASE_FIELDS:
            if field not in case:
                problems.append(f"用例 {label}：缺少字段 {field}")
        verdict = case.get("verdict")
        if "verdict" in case and verdict not in REPORT_VERDICTS:
            problems.append(f"用例 {label}：verdict={verdict!r} 不在 {REPORT_VERDICTS}")
        if verdict in REPORT_NEEDS_REASON and not case.get("failure_reason"):
            problems.append(f"用例 {label}：verdict={verdict!r} 必须给出非空 failure_reason")
        for field in ("rows", "citations"):
            if field in case and case[field] is not None and not isinstance(case[field], list):
                problems.append(f"用例 {label}：{field} 必须是数组或 null")

    summary = payload.get("summary")
    if "summary" not in payload:
        problems.append("缺少顶层字段 summary（准确率 / 拒绝率 / 失败分类计数）")
        return problems
    if not isinstance(summary, dict):
        problems.append("summary 必须是对象")
        return problems
    for field in REPORT_SUMMARY_FIELDS:
        if field not in summary:
            problems.append(f"summary：缺少字段 {field}")

    total = summary.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        problems.append(f"summary.total 必须是非负整数，实为 {total!r}")
        total = 0
    counts = {}
    for field in ("passed", "failed", "refused", "errors"):
        value = summary.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            problems.append(f"summary.{field} 必须是非负整数，实为 {value!r}")
        else:
            counts[field] = value
    if len(counts) == 4 and sum(counts.values()) != total:
        problems.append("summary 计数不自洽：passed+failed+refused+errors="
                        f"{sum(counts.values())} != total={total}")

    failure_counts = summary.get("failure_counts")
    if not isinstance(failure_counts, dict):
        problems.append("summary.failure_counts 必须是对象（失败分类 -> 计数）")
    else:
        for key, value in failure_counts.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"summary.failure_counts[{key!r}] 必须是非负整数，实为 {value!r}")

    for key, denominator_key in (("accuracy", "accuracy_denominator"),
                                 ("refusal_rate", "refusal_denominator")):
        value = summary.get(key)
        denominator = summary.get(denominator_key, total)
        if not isinstance(denominator, int) or isinstance(denominator, bool) or denominator < 0:
            problems.append(f"summary.{denominator_key} 必须是非负整数，实为 {denominator!r}")
            continue
        if denominator == 0:
            if value != "N/A":
                problems.append(f"summary.{key}：零分母必须报 'N/A'，不得用 {value!r} 冒充")
        else:
            if value == "N/A":
                problems.append(f"summary.{key}：分母 {denominator_key}={denominator} 非零，不能报 'N/A'")
            elif not _is_number(value) or not 0 <= value <= 1:
                problems.append(f"summary.{key} 必须是 0~1 的数值，实为 {value!r}")
    return problems
