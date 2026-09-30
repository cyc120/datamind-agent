"""Versioned semantic catalog and approved join paths."""
from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush

import psycopg2
from psycopg2.extras import RealDictCursor


@dataclass(frozen=True)
class JoinEdge:
    relation_id: str
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    cost: float
    description: str


class Catalog:
    def __init__(self, meta_dsn: str, source_id: str):
        self.source_id = source_id
        with psycopg2.connect(meta_dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT s.snapshot_id, s.catalog_version, d.database_name, d.schema_allowlist,
                       d.table_allowlist, d.access_mode
                FROM schema_snapshots s JOIN data_sources d USING (source_id)
                WHERE s.source_id=%s ORDER BY s.catalog_version DESC LIMIT 1
            """, (source_id,))
            info = cur.fetchone()
            if not info or info["access_mode"] != "readonly":
                raise ValueError(f"No read-only catalog for {source_id}")
            self.snapshot_id = info["snapshot_id"]
            self.version = info["catalog_version"]
            self.database_name = info["database_name"]
            self.schemas = set(info["schema_allowlist"])
            self.table_allowlist = set(info["table_allowlist"] or [])
            cur.execute("SELECT * FROM schema_tables WHERE snapshot_id=%s", (self.snapshot_id,))
            self.tables = {r["table_name"]: dict(r) for r in cur.fetchall()
                           if r["schema_name"] in self.schemas}
            if self.table_allowlist:
                self.tables = {k: v for k, v in self.tables.items() if k in self.table_allowlist}
            cur.execute("SELECT * FROM schema_columns WHERE snapshot_id=%s", (self.snapshot_id,))
            self.columns = {(r["table_name"], r["column_name"]): dict(r) for r in cur.fetchall()
                            if r["table_name"] in self.tables}
            cur.execute("SELECT * FROM schema_relations WHERE snapshot_id=%s AND approved=TRUE", (self.snapshot_id,))
            self.relations = []
            for r in cur.fetchall():
                if r["from_table"] not in self.tables or r["to_table"] not in self.tables:
                    continue
                # Metadata can contain a proposed business edge whose columns do not exist.
                if not all((r["from_table"], c) in self.columns for c in r["from_columns"]):
                    continue
                if not all((r["to_table"], c) in self.columns for c in r["to_columns"]):
                    continue
                self.relations.append(JoinEdge(r["relation_id"], r["from_table"],
                    tuple(r["from_columns"]), r["to_table"], tuple(r["to_columns"]),
                    1 + float(r["ambiguity_penalty"] + r["fanout_penalty"] + r["privacy_penalty"]),
                    r["description"] or ""))
            cur.execute("SELECT * FROM business_metrics WHERE source_id=%s AND status='active'", (source_id,))
            self.metrics = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT alias, canonical_name, entity_type FROM entity_aliases WHERE approved=TRUE")
            self.aliases = [dict(r) for r in cur.fetchall()]

    def retrieve(self, question: str, limit: int = 8) -> dict:
        """Deterministic lexical/alias/value recall with inspectable reasons."""
        q = question.lower().replace(" ", "")
        candidates = []
        for name, row in self.tables.items():
            terms = [(name, "table"), (row.get("business_name") or "", "business_name")]
            if (row.get("business_name") or "").endswith("表"):
                terms.append((row["business_name"][:-1], "business_name"))
            terms += [(a, "alias") for a in row.get("aliases") or []]
            hits = [why for term, why in terms if len(term) >= 2 and term.lower() in q]
            if hits:
                candidates.append({"kind": "table", "id": name, "score": max(0.6 if x == "alias" else 0.8 for x in hits), "reasons": sorted(set(hits))})
        for (table, name), row in self.columns.items():
            if row.get("sensitivity") == "restricted":
                continue
            terms = [(name, "column"), (row.get("business_name") or "", "business_name")]
            if (row.get("business_name") or "").endswith("名称"):
                terms.append((row["business_name"][:-2], "business_name"))
            terms += [(a, "alias") for a in row.get("aliases") or []]
            enum = row.get("enum_map") or {}
            terms += [(str(k), "enum_value") for k in enum]
            terms += [(str(v), "enum_value") for v in enum.values()]
            hits = [why for term, why in terms if len(term) >= 2 and term.lower() in q]
            if hits:
                candidates.append({"kind": "column", "id": f"{table}.{name}",
                                   "score": max(1.0 if x == "enum_value" else 0.75 if x == "alias" else 0.8 for x in hits),
                                   "reasons": sorted(set(hits))})
        for metric in self.metrics:
            terms = [(metric["name"], "metric_name")] + [(a, "metric_alias") for a in metric["aliases"]]
            hits = [why for term, why in terms if term.lower() in q]
            if hits:
                candidates.append({"kind": "metric", "id": metric["metric_id"], "score": 1.2,
                                   "reasons": sorted(set(hits))})
                anchors = {
                    "sales_amount": ("order_items", "orders"), "units_sold": ("order_items", "orders"),
                    "net_sales": ("order_items", "orders", "refunds"), "refund_amount": ("refunds",),
                    "order_count": ("orders",), "customer_count": ("customers",),
                    "revenue": ("invoice",), "tracks_sold": ("invoice_line",),
                }.get(metric["metric_id"].split(".")[-1], ())
                for table in anchors:
                    if table in self.tables:
                        candidates.append({"kind": "table", "id": table, "score": 0.7,
                                           "reasons": ["metric_dependency"]})
        for alias in self.aliases:
            if alias["alias"].lower() not in q:
                continue
            for (table, column), row in self.columns.items():
                if alias["canonical_name"] in (row.get("enum_map") or {}).values():
                    candidates.append({"kind": "column", "id": f"{table}.{column}", "score": 0.95,
                                       "reasons": ["approved_entity_alias"]})
                    candidates.append({"kind": "table", "id": table, "score": 0.65,
                                       "reasons": ["approved_entity_alias"]})
        table_hits = {c["id"] for c in candidates if c["kind"] == "table"}
        for left in table_hits:
            for right in table_hits:
                if left >= right:
                    continue
                try:
                    for edge in self.join_path(left, right):
                        for table in (edge.from_table, edge.to_table):
                            candidates.append({"kind": "table", "id": table, "score": 0.45,
                                               "reasons": ["approved_join_path"]})
                except ValueError:
                    pass
        merged = {}
        for candidate in candidates:
            key = (candidate["kind"], candidate["id"])
            if key not in merged:
                merged[key] = candidate.copy()
            else:
                merged[key]["score"] = max(merged[key]["score"], candidate["score"])
                merged[key]["reasons"] = sorted(set(merged[key]["reasons"] + candidate["reasons"]))
        candidates = list(merged.values())
        candidates.sort(key=lambda x: (-x["score"], x["id"]))
        return {"candidates": candidates[:limit], "snapshot_id": self.snapshot_id}

    def join_path(self, start: str, target: str) -> list[JoinEdge]:
        if start not in self.tables or target not in self.tables:
            raise ValueError("SCHEMA_NOT_FOUND")
        queue = [(0.0, start, ())]
        seen = set()
        while queue:
            cost, table, path = heappop(queue)
            if table == target:
                return list(path)
            if table in seen:
                continue
            seen.add(table)
            for edge in self.relations:
                if table == edge.from_table and edge.to_table not in seen:
                    heappush(queue, (cost + edge.cost, edge.to_table, path + (edge,)))
                elif table == edge.to_table and edge.from_table not in seen:
                    heappush(queue, (cost + edge.cost, edge.from_table, path + (edge,)))
        raise ValueError("SCHEMA_NOT_FOUND: no approved join path")

    def prompt_context(self) -> str:
        lines = [f"Source {self.source_id}, snapshot {self.snapshot_id}."]
        for table, info in sorted(self.tables.items()):
            cols = []
            for (t, c), r in self.columns.items():
                if t != table or r.get("sensitivity") == "restricted":
                    continue
                label = r.get("business_name") or c
                aliases = "/".join(r.get("aliases") or [])
                enum = r.get("enum_map") or {}
                suffix = f" enum={enum}" if enum else ""
                cols.append(f"{c}({label}{'/' + aliases if aliases else ''}){suffix}")
            lines.append(f"TABLE {table} ({info.get('business_name')}) : {', '.join(cols)}")
        for e in self.relations:
            lines.append(f"JOIN {e.from_table}.{','.join(e.from_columns)} = {e.to_table}.{','.join(e.to_columns)}")
        for m in self.metrics:
            lines.append(f"METRIC {m['metric_id']} {m['name']} aliases={m['aliases']} definition={m['definition']}")
        for a in self.aliases:
            lines.append(f"ALIAS {a['alias']} => {a['canonical_name']} ({a['entity_type']})")
        return "\n".join(lines)
