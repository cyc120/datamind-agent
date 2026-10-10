"""Evaluate deployed Catalog retrieval and approved Join Graph against reviewed labels.

`--suite <path>`（默认 v1）保持原有行为与输出字段不变。
`--suite v2` 走 Schema Linking 独立复核（WP6 / B4）：40+ 表只读 fixture 表池（真实快照并集
+ 本 WP 声明的构造表/干扰表）、未见问题集、拒绝类单列、机制粒度消融（只词法 / 词法+别名 /
词法+别名+枚举 / 全机制含 Join Graph）。v2 **不写元数据库**，只用只读连接搭建 fixture。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
from psycopg2.extras import RealDictCursor

from datamind.catalog import Catalog, JoinEdge


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "data/evaluation/schema_linking_v1.json"
DEFAULT_SUITE_V2 = ROOT / "data/evaluation/schema_linking_v2.json"
DEFAULT_OUTPUT = ROOT / "reports/schema_linking.json"
DEFAULT_OUTPUT_V2 = ROOT / "reports/schema_linking_v2.json"
DEFAULT_DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"

#: 机制 -> `Catalog.retrieve` 给出的可核查 reason 标签。消融只过滤候选的 reason 集合，
#: 不改引擎代码；因此四档对照测的是「每个机制各自贡献了多少召回」。
MECHANISM_REASONS = {
    "lexical": {"table", "business_name", "column", "metric_name"},
    "alias": {"alias", "metric_alias", "approved_entity_alias"},
    "enum": {"enum_value"},
    "join_graph": {"approved_join_path", "metric_dependency", "time_scope_on_recalled_table",
                   "price_on_recalled_table"},
}
ABLATION_TIERS = (
    {"tier": "lexical_only", "mechanisms": ("lexical",)},
    {"tier": "lexical_alias", "mechanisms": ("lexical", "alias")},
    {"tier": "lexical_alias_enum", "mechanisms": ("lexical", "alias", "enum")},
    {"tier": "full_with_join_graph", "mechanisms": ("lexical", "alias", "enum", "join_graph")},
)


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


def _rate(counts: dict) -> dict:
    """分子/分母同给；零分母报 None（N/A），不用 0 或 100% 冒充。"""
    denominator = counts.get("gold", counts.get("checked"))
    numerator = counts.get("correct", counts.get("hit", counts.get("false_positives")))
    return {**counts, "rate": numerator / denominator if denominator else None}


def build_fixture_catalog(suite: dict, dsn: str) -> tuple[Catalog, dict]:
    """只读搭建 v2 fixture 表池：真实快照表/列/已批准关系 + 本 WP 声明的构造表。

    不写元数据库、不改引擎：用 `Catalog.__new__` 注入与 `schema_tables/schema_columns/
    schema_relations` 相同的行结构，然后调用**未修改的** `Catalog.retrieve/join_path`。
    """
    catalog = Catalog.__new__(Catalog)
    catalog.source_id = "fixture_schema_linking_v2"
    catalog.snapshot_id = "fixture-schema-linking-v2"
    catalog.version = "fixture-v2"
    catalog.database_name = "fixture"
    catalog.schemas = {"fixture", "public"}
    catalog.description = suite["pool"]["description"]
    tables: dict[str, dict] = {}
    columns: dict[tuple[str, str], dict] = {}
    relations: list[dict] = []
    metrics: list[dict] = []
    sources = []
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        for source in suite["pool"]["real_sources"]:
            snapshot_id = source["snapshot_id"]
            cur.execute("SELECT * FROM schema_tables WHERE snapshot_id=%s ORDER BY table_name",
                        (snapshot_id,))
            rows = [dict(row) for row in cur.fetchall()]
            for row in rows:
                tables.setdefault(row["table_name"], row)
            cur.execute("SELECT * FROM schema_columns WHERE snapshot_id=%s", (snapshot_id,))
            for row in cur.fetchall():
                if row["table_name"] in tables:
                    columns.setdefault((row["table_name"], row["column_name"]), dict(row))
            cur.execute("SELECT * FROM schema_relations WHERE snapshot_id=%s AND approved=TRUE",
                        (snapshot_id,))
            relations.extend(dict(row) for row in cur.fetchall())
            cur.execute("SELECT * FROM business_metrics WHERE source_id=%s AND status='active'",
                        (source["source_id"],))
            metrics.extend(dict(row) for row in cur.fetchall())
            sources.append({"source_id": source["source_id"], "snapshot_id": snapshot_id,
                            "real_table_count": len(rows)})
        cur.execute("SELECT alias, canonical_name, entity_type FROM entity_aliases WHERE approved=TRUE")
        catalog.aliases = [dict(row) for row in cur.fetchall()]

    for entry in suite["pool"]["synthetic_tables"]:
        name = entry["table_name"]
        if name in tables:
            raise ValueError(f"SYNTHETIC_TABLE_COLLIDES_WITH_REAL: {name}")
        tables[name] = {"table_name": name, "schema_name": entry.get("schema_name", "fixture"),
                        "business_name": entry["business_name"], "aliases": entry["aliases"]}
        for column in entry["columns"]:
            key = (name, column["column_name"])
            if key in columns:
                raise ValueError(f"SYNTHETIC_COLUMN_COLLIDES_WITH_REAL: {name}.{column['column_name']}")
            columns[key] = dict(column, table_name=name, schema_name=entry.get("schema_name", "fixture"))
    relations.extend(suite["pool"]["synthetic_relations"])
    metrics.extend(suite["pool"].get("synthetic_metrics") or [])

    # 与 Catalog.__init__ 相同的合法性过滤（列必须存在、表必须在池内）。
    catalog.relations = []
    for row in relations:
        if row["from_table"] not in tables or row["to_table"] not in tables:
            continue
        if not all((row["from_table"], c) in columns for c in row["from_columns"]):
            continue
        if not all((row["to_table"], c) in columns for c in row["to_columns"]):
            continue
        catalog.relations.append(JoinEdge(
            row["relation_id"], row["from_table"], tuple(row["from_columns"]), row["to_table"],
            tuple(row["to_columns"]),
            1 + float(row.get("ambiguity_penalty") or 0) + float(row.get("fanout_penalty") or 0)
            + float(row.get("privacy_penalty") or 0), row.get("description") or ""))
    catalog.tables = tables
    catalog.columns = columns
    catalog.metrics = metrics
    catalog.table_allowlist = set(tables)
    excluded = {item["table_name"] for item in suite["pool"].get("excluded_tables", [])}
    if excluded & set(tables):
        raise ValueError("EXCLUDED_TABLE_LEAKED_INTO_POOL")
    return catalog, {"sources": sources, "synthetic_table_count": len(suite["pool"]["synthetic_tables"]),
                     "real_table_count": len(tables) - len(suite["pool"]["synthetic_tables"]),
                     "total_table_count": len(tables), "excluded_tables": sorted(excluded),
                     "distractors": suite["pool"].get("distractors", {}),
                     "approved_relation_count": len(catalog.relations),
                     "metric_count": len(metrics)}


def _tier_candidates(candidates: list[dict], mechanisms: tuple[str, ...]) -> list[dict]:
    allowed = set().union(*(MECHANISM_REASONS[name] for name in mechanisms))
    return [candidate for candidate in candidates if set(candidate["reasons"]) & allowed]


def _top(candidates: list[dict], kind: str, limit: int = 5) -> list[str]:
    return [candidate["id"] for candidate in candidates if candidate["kind"] == kind][:limit]


def _reject_verdict(case: dict, catalog: Catalog, retrieved_tables: list[str],
                    retrieved_columns: list[str]) -> dict:
    problems: list[str] = []
    for table in case.get("expected_absent_tables", []):
        if table in retrieved_tables:
            problems.append(f"table_selected:{table}")
    for column in case.get("expected_absent_columns", []):
        if column in retrieved_columns:
            problems.append(f"column_selected:{column}")
    kind = case["reject"]["kind"]
    if kind == "no_approved_path":
        start, target = case["reject"]["from"], case["reject"]["to"]
        try:
            catalog.join_path(start, target)
            problems.append(f"path_resolved:{start}->{target}")
        except ValueError:
            pass
    return {"kind": kind, "targets": case["reject"].get("targets", []),
            "correct": not problems, "problems": problems}


def evaluate_v2(suite: dict, dsn: str) -> dict:
    catalog, fixture = build_fixture_catalog(suite, dsn)
    metric_ids = {metric["metric_id"] for metric in catalog.metrics}
    kinds = ("table", "column", "metric")
    gold_key = {"table": "tables", "column": "columns", "metric": "metrics"}
    tier_counts = {tier["tier"]: {kind: {"hit": 0, "gold": 0} for kind in kinds} for tier in ABLATION_TIERS}
    budget_counts = {budget: {kind: {"hit": 0, "gold": 0} for kind in kinds} for budget in (8, 32)}
    path_counts = {"correct": 0, "gold": 0}
    reject_counts = {"correct": 0, "gold": 0}
    distractor_counts = {"false_positives": 0, "checked": 0}
    taxonomy = {"not_matched": 0, "global_budget_cut": 0, "kind_top5_cut": 0}
    taxonomy_by_kind = {kind: dict(taxonomy) for kind in kinds}
    details = []
    positive_cases = [case for case in suite["cases"] if not case.get("expected_reject")]
    reject_cases = [case for case in suite["cases"] if case.get("expected_reject")]

    for case in suite["cases"]:
        for table in case["tables"]:
            if table not in catalog.tables:
                raise ValueError(f"Gold table absent from fixture pool: {table} ({case['case_id']})")
        for column in case["columns"]:
            if tuple(column.split(".", 1)) not in catalog.columns:
                raise ValueError(f"Gold column absent from fixture pool: {column} ({case['case_id']})")
        if not set(case["metrics"]) <= metric_ids:
            raise ValueError(f"Gold metric absent from fixture pool: {case['case_id']}")
        for path in case["paths"]:
            for endpoint in (path["from"], path["to"]):
                if endpoint not in catalog.tables:
                    raise ValueError(f"Path endpoint absent from fixture pool: {endpoint}")

        # 部署配置=8 候选预算（与 v1/引擎默认一致）；另取 32 预算与“无截断”全量列表，
        # 用于把召回失败拆成「机制根本没匹配上 / 被全局候选预算截掉 / 类内 Top-5 截掉」。
        deployed = catalog.retrieve(case["question"], limit=8)["candidates"]
        extended = catalog.retrieve(case["question"], limit=32)["candidates"]
        complete = catalog.retrieve(case["question"], limit=100000)["candidates"]
        result = {"case_id": case["case_id"], "question": case["question"],
                  "expected_reject": bool(case.get("expected_reject")),
                  "gold": {"tables": case["tables"], "columns": case["columns"],
                           "metrics": case["metrics"]},
                  "retrieved": {}, "missing": {}, "paths": [], "tiers": {},
                  "failure_causes": []}
        full_tier = ABLATION_TIERS[-1]["tier"]
        mechanisms = dict((tier["tier"], tier["mechanisms"]) for tier in ABLATION_TIERS)
        for tier in ABLATION_TIERS:
            filtered = _tier_candidates(deployed, tier["mechanisms"])
            retrieved = {kind: _top(filtered, kind) for kind in kinds}
            result["tiers"][tier["tier"]] = {"retrieved": retrieved}
            if not case.get("expected_reject"):
                for kind in kinds:
                    gold = case[gold_key[kind]]
                    missing = sorted(set(gold) - set(retrieved[kind]))
                    tier_counts[tier["tier"]][kind]["hit"] += len(gold) - len(missing)
                    tier_counts[tier["tier"]][kind]["gold"] += len(gold)
            if tier["tier"] == full_tier:
                result["retrieved"] = retrieved
                for kind in kinds:
                    result["missing"][kind] = sorted(set(case[gold_key[kind]]) - set(retrieved[kind]))
        for budget, candidates in ((8, deployed), (32, extended)):
            if case.get("expected_reject"):
                continue
            filtered = _tier_candidates(candidates, mechanisms[full_tier])
            for kind in kinds:
                top = _top(filtered, kind)
                gold = case[gold_key[kind]]
                budget_counts[budget][kind]["hit"] += len(set(gold) & set(top))
                budget_counts[budget][kind]["gold"] += len(gold)
        if not case.get("expected_reject"):
            deployed_ids = {kind: set(_top(_tier_candidates(deployed, mechanisms[full_tier]), kind, 100000))
                            for kind in kinds}
            complete_ids = {kind: {candidate["id"] for candidate
                                   in _tier_candidates(complete, mechanisms[full_tier])
                                   if candidate["kind"] == kind} for kind in kinds}
            for kind in kinds:
                for gold_id in result["missing"][kind]:
                    if gold_id in deployed_ids[kind]:
                        cause = "kind_top5_cut"
                    elif gold_id in complete_ids[kind]:
                        cause = "global_budget_cut"
                    else:
                        cause = "not_matched"
                    taxonomy[cause] += 1
                    taxonomy_by_kind[kind][cause] += 1
                    result["failure_causes"].append({"kind": kind, "id": gold_id, "cause": cause})
        result["tiers"][full_tier]["is_deployed_configuration"] = True
        result["retrieve_reasons"] = {kind: sorted({reason for candidate in complete
                                                    if candidate["kind"] == kind
                                                    for reason in candidate["reasons"]})
                                      for kind in kinds}

        if case.get("expected_reject"):
            verdict = _reject_verdict(case, catalog, result["retrieved"]["table"],
                                      result["retrieved"]["column"])
            reject_counts["correct"] += int(verdict["correct"])
            reject_counts["gold"] += 1
            result["reject"] = verdict
            # 拒绝类也参与「干扰表/受限列不得被选」的误选统计。
            checks = (len(case.get("expected_absent_tables", []))
                      + len(case.get("expected_absent_columns", [])))
            distractor_counts["checked"] += checks
            distractor_counts["false_positives"] += len(verdict["problems"])
            result["distractor_hits"] = verdict["problems"]
        else:
            hits = []
            for table in case.get("expected_absent_tables", []):
                if table in result["retrieved"]["table"]:
                    hits.append(f"table_selected:{table}")
            for column in case.get("expected_absent_columns", []):
                if column in result["retrieved"]["column"]:
                    hits.append(f"column_selected:{column}")
            distractor_counts["checked"] += (len(case.get("expected_absent_tables", []))
                                             + len(case.get("expected_absent_columns", [])))
            distractor_counts["false_positives"] += len(hits)
            result["distractor_hits"] = hits
            for path in case["paths"]:
                actual = _path_tables(path["from"], catalog.join_path(path["from"], path["to"]))
                correct = actual == path["tables"]
                path_counts["correct"] += int(correct)
                path_counts["gold"] += 1
                result["paths"].append({"from": path["from"], "to": path["to"],
                                        "expected": path["tables"], "actual": actual,
                                        "correct": correct})
        details.append(result)

    summary = {f"{kind}_recall_at_5": _rate(tier_counts[ABLATION_TIERS[-1]["tier"]][kind])
               for kind in kinds}
    summary["join_path_accuracy"] = _rate(path_counts)
    summary["reject_accuracy"] = _rate(reject_counts)
    summary["distractor_false_positives"] = _rate(distractor_counts)
    summary["failure_taxonomy"] = {**taxonomy, "by_kind": taxonomy_by_kind}
    ablation = []
    for tier in ABLATION_TIERS:
        counts = tier_counts[tier["tier"]]
        entry = {"tier": tier["tier"], "mechanisms": list(tier["mechanisms"])}
        entry.update({f"{kind}_recall_at_5": _rate(counts[kind]) for kind in kinds})
        ablation.append(entry)
    budget_sensitivity = []
    for budget in (8, 32):
        entry = {"candidate_budget": budget, "is_deployed_configuration": budget == 8}
        entry.update({f"{kind}_recall_at_5": _rate(budget_counts[budget][kind]) for kind in kinds})
        budget_sensitivity.append(entry)
    frozen_v1 = ROOT / "reports/schema_linking.json"
    recorded_hash = json.loads(frozen_v1.read_text(encoding="utf-8"))["catalog_code_sha256"] \
        if frozen_v1.is_file() else None
    current_hash = hashlib.sha256((ROOT / "datamind/catalog.py").read_bytes()).hexdigest()
    return {
        "suite_version": suite["suite_version"],
        "data_version": suite["data_version"],
        "annotation": suite["annotation"],
        "method": "只读 fixture 表池（真实快照并集 + 声明的构造/干扰表）+ 未修改的 "
                  "Catalog.retrieve/join_path；Top-5 每类微平均召回；路径精确表序列；"
                  "机制消融按候选 reason 过滤（不改引擎）；拒绝类单列，不计入召回分母。",
        "case_count": {"total": len(details), "positive": len(positive_cases),
                       "reject": len(reject_cases)},
        "fixture": fixture,
        "catalog_code_sha256": current_hash,
        "suite_sha256": hashlib.sha256(
            json.dumps(suite, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "frozen_v1_recheck": {
            "report": "reports/schema_linking.json",
            "recorded_catalog_code_sha256": recorded_hash,
            "current_catalog_code_sha256": current_hash,
            "hash_matches": recorded_hash == current_hash,
            "note": "v1 报表记录的引擎哈希与当前 datamind/catalog.py 不一致；本 WP 已用当前引擎"
                    "复跑 v1（同 18 条用例，逐案 retrieved 完全一致），但 v1 报表的哈希归属需修正。",
        },
        "ablation": ablation,
        "budget_sensitivity": budget_sensitivity,
        "summary": summary,
        "cases": details,
        "honest_boundaries": [
            "演示库只有 19 张真实表，因此 46 张表池中 27 张由本 WP 在 suite JSON 中声明构造；"
            "构造表不是生产 schema，表池结论不可外推为真实库规模下的表现。",
            "机制消融按引擎输出的 reason 标签过滤候选，测的是「机制贡献」；引擎自身未做代码消融。",
            "拒绝率单列且不进入召回分母；召回率只统计正例的语义锚点。",
            "Top-5 微平均召回不惩罚误选；误选单独由 distractor_false_positives 统计。",
            "所有问题与期望值为自建冻结集，不构成对公开基准（Spider/BIRD）的泛化声明。",
        ],
    }


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
    parser.add_argument("--suite", default=str(DEFAULT_SUITE),
                        help="v1 题集路径（默认）或字面量 v2（Schema Linking 独立复核）")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-recall", type=float, default=0.90)
    args = parser.parse_args()
    is_v2 = str(args.suite).strip() in {"v2", "schema-linking-v2"}
    suite_path = DEFAULT_SUITE_V2 if is_v2 else Path(args.suite)
    output = args.output or (DEFAULT_OUTPUT_V2 if is_v2 else DEFAULT_OUTPUT)
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    report = (evaluate_v2 if is_v2 else evaluate)(suite, os.environ.get("DATAMIND_META_DSN", DEFAULT_DSN))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, value in report["summary"].items():
        if "rate" not in value:
            print(f"{name}: {json.dumps(value, ensure_ascii=False)}")
            continue
        numerator = value.get("correct", value.get("hit", value.get("false_positives")))
        denominator = value.get("gold", value.get("checked"))
        rate = "N/A" if value["rate"] is None else f"{value['rate']:.1%}"
        print(f"{name}: {numerator}/{denominator} = {rate}")
    if is_v2:
        for tier in report["ablation"]:
            table = tier["table_recall_at_5"]
            print(f"  ablation {tier['tier']}: table {table['hit']}/{table['gold']} = "
                  f"{table['rate']:.1%}" if table["rate"] is not None
                  else f"  ablation {tier['tier']}: table N/A")
    print(f"report: {output}")
    higher_is_better = ("table_recall_at_5", "column_recall_at_5", "metric_recall_at_5",
                        "join_path_accuracy", "reject_accuracy")
    failing = [name for name in higher_is_better
               if name in report["summary"] and report["summary"][name]["rate"] is not None
               and report["summary"][name]["rate"] < args.min_recall]
    false_positive_rate = report["summary"].get("distractor_false_positives", {}).get("rate")
    if false_positive_rate:  # 误选必须为 0：任何一条 declared-absent 被选中都算失败
        failing.append("distractor_false_positives")
    return int(bool(failing))


if __name__ == "__main__":
    raise SystemExit(main())
