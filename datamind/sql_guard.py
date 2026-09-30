"""PostgreSQL SELECT policy based on a parsed AST and the approved catalog."""
from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .catalog import Catalog


class PolicyError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"SQL_POLICY_BLOCKED: {reason}")


@dataclass
class SafeSQL:
    sql: str
    params: list
    tables: list[str]


COMPARISON_NODES = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
                    exp.Like, exp.ILike, exp.In, exp.Between)


def _comparison_operands(node: exp.Expression) -> list:
    """返回比较表达式两侧的操作数（IN / BETWEEN 有多个）。"""
    if isinstance(node, exp.Between):
        return [node.this, node.args.get("low"), node.args.get("high")]
    if isinstance(node, exp.In):
        return [node.this, *node.expressions]
    return [node.left, node.right]


def _column_pair(node: exp.Expression, aliases: dict[str, str]):
    if not isinstance(node, exp.EQ):
        return None
    a, b = node.left, node.right
    if not isinstance(a, exp.Column) or not isinstance(b, exp.Column):
        return None
    if not a.table or not b.table or a.table not in aliases or b.table not in aliases:
        return None
    return ((aliases[a.table], a.name), (aliases[b.table], b.name))


def _approved_equality(pair, catalog: Catalog) -> bool:
    for edge in catalog.relations:
        for left, right in zip(edge.from_columns, edge.to_columns):
            accepted = ((edge.from_table, left), (edge.to_table, right))
            if pair == accepted or pair == accepted[::-1]:
                return True
    return False


def guard_sql(sql: str, catalog: Catalog, max_rows: int = 100) -> SafeSQL:
    if not sql or len(sql) > 20_000:
        raise PolicyError("EMPTY_OR_OVERSIZED_SQL")
    try:
        statements = [s for s in parse(sql, read="postgres") if s]
    except ParseError as exc:
        raise PolicyError("SYNTAX_ERROR") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise PolicyError("SELECT_ONLY")
    tree = statements[0]
    if any(node.args.get("recursive") for node in tree.find_all(exp.With)):
        raise PolicyError("RECURSIVE_CTE_NOT_ALLOWED")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
                 exp.Merge, exp.Command, exp.Copy, exp.Into, exp.Lock, exp.Set)
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise PolicyError("MUTATION_OR_COMMAND")
    if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
        raise PolicyError("SELECT_STAR")
    if any(isinstance(node, exp.Anonymous) and node.name.lower() in
           {"pg_sleep", "dblink", "lo_export", "set_config", "pg_read_file", "pg_ls_dir"}
           for node in tree.walk()):
        raise PolicyError("DANGEROUS_FUNCTION")
    for fn in tree.find_all(exp.Anonymous):
        if fn.name.lower() not in {"date_trunc", "round", "coalesce", "nullif", "concat", "lower", "upper",
                                   "substring", "to_char", "abs", "greatest", "least"}:
            raise PolicyError("UNAPPROVED_FUNCTION")
    cte_names = {cte.alias for cte in tree.find_all(exp.CTE)}
    aliases: dict[str, str] = {}
    base_tables = []
    for table in tree.find_all(exp.Table):
        if table.name in cte_names:
            aliases[table.alias_or_name] = table.name
            continue
        if table.db and table.db not in catalog.schemas:
            raise PolicyError("SCHEMA_NOT_ALLOWED")
        if table.catalog or table.name not in catalog.tables:
            raise PolicyError("TABLE_NOT_ALLOWED")
        if table.name not in base_tables:
            base_tables.append(table.name)
        aliases[table.alias_or_name] = table.name
    if not base_tables:
        raise PolicyError("NO_BASE_TABLE")
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            raise PolicyError("SELECT_STAR")
        if column.table and column.table not in aliases:
            raise PolicyError("UNKNOWN_TABLE_ALIAS")
        possible = [aliases[column.table]] if column.table else base_tables
        if any(t in cte_names for t in possible):
            continue
        matches = [catalog.columns[(t, column.name)] for t in possible if (t, column.name) in catalog.columns]
        if not matches:
            # A SELECT alias may be used in ORDER BY / GROUP BY.
            if column.name in {p.alias for p in tree.find_all(exp.Alias)}:
                continue
            raise PolicyError("COLUMN_NOT_ALLOWED")
        if any(r.get("sensitivity") == "restricted" for r in matches):
            raise PolicyError("RESTRICTED_COLUMN")
    for join in tree.find_all(exp.Join):
        if join.args.get("kind") == "CROSS" or not (join.args.get("on") or join.args.get("using")):
            raise PolicyError("CARTESIAN_JOIN")
        if join.args.get("using"):
            raise PolicyError("JOIN_USING_NOT_VERIFIED")
        if any(isinstance(node, exp.Or) for node in join.args["on"].walk()):
            raise PolicyError("AMBIGUOUS_JOIN_CONDITION")
        joined = join.this
        if not isinstance(joined, exp.Table) or joined.name in cte_names:
            raise PolicyError("DERIVED_JOIN_NOT_VERIFIED")
        pairs = [_column_pair(n, aliases) for n in join.args["on"].walk() if isinstance(n, exp.EQ)]
        if not any(p and _approved_equality(p, catalog) and joined.name in (p[0][0], p[1][0]) for p in pairs):
            raise PolicyError("UNAPPROVED_JOIN")
    # Limit large result sets even when the model omits a limit.
    limit = tree.args.get("limit")
    if limit:
        value = limit.expression
        if not isinstance(value, exp.Literal) or not value.is_int:
            raise PolicyError("INVALID_LIMIT")
        tree.set("limit", exp.Limit(expression=exp.Literal.number(min(int(value.this), max_rows))))
    else:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    offset = tree.args.get("offset")
    if offset:
        value = offset.expression
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) > max_rows * 10:
            raise PolicyError("INVALID_OFFSET")
    marker_values = {}
    # Bind every literal that participates in a comparison, wherever it appears
    # (WHERE / HAVING / JOIN ON / CASE WHEN ...), so constants never reach the SQL text.
    # Structural literals stay untouched: LIMIT, OFFSET, GROUP BY 1, ORDER BY 1, INTERVAL.
    for comparison in tree.find_all(*COMPARISON_NODES):
        for operand in _comparison_operands(comparison):
            if not isinstance(operand, exp.Expression):
                continue
            for literal in list(operand.find_all(exp.Literal)):
                if isinstance(literal.parent, exp.Interval):
                    continue
                value = literal.this if literal.is_string else (int(literal.this) if literal.is_int else float(literal.this))
                marker = f"__DM_PARAM_{len(marker_values)}__"
                marker_values[marker] = value
                literal.replace(exp.Var(this=marker))
    rendered = tree.sql(dialect="postgres")
    import re
    params = [marker_values[m.group()] for m in re.finditer(r"__DM_PARAM_\d+__", rendered)]
    rendered = re.sub(r"__DM_PARAM_\d+__", "%s", rendered)
    return SafeSQL(rendered, params, base_tables)
