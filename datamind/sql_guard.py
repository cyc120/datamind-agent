"""PostgreSQL SELECT policy based on a parsed AST and the approved catalog."""
from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import Scope, traverse_scope

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


def _in_sort_clause(column: exp.Column) -> bool:
    """True when the column sits in the sort/having clause of its own query.

    Only there may a projection alias be referenced by name; in a projection or a WHERE
    clause the same identifier can mean a table alias or a whole-row reference.
    """
    node = column.parent
    while node is not None:
        if isinstance(node, (exp.Order, exp.Group, exp.Having)):
            return True
        if isinstance(node, (exp.Select, exp.Subquery, exp.Union)):
            return False
        node = node.parent
    return False


def _scope_projection_names(scope) -> set[str]:
    names = set()
    for projection in scope.expression.selects:
        name = projection.alias_or_name
        if name:
            names.add(name)
    return names


def cte_reference_table_ids(tree) -> set[int]:
    """``id()`` of the ``exp.Table`` nodes that really reference a CTE.

    Classification uses the *source object of the node's own scope*, never a global set of
    CTE names. Two consequences matter for the security boundary:

    - a table reference that is schema qualified (``public.customers``) can never be a CTE
      reference, so it can never inherit a same-named CTE's exemption; and
    - a CTE reference must resolve to a ``Scope`` whose ``is_cte`` is true in that scope,
      so an inner CTE that shadows an outer name does not exempt the outer real table.
    """
    ids: set[int] = set()
    for scope in traverse_scope(tree):
        if not scope.cte_sources:
            continue
        for table in scope.tables:
            if table.catalog or table.db:
                continue  # schema/catalog-qualified names are always real tables
            source = scope.sources.get(table.alias_or_name)
            if isinstance(source, Scope) and getattr(source, "is_cte", False):
                ids.add(id(table))
    return ids


def _check_columns(tree, catalog: Catalog, base_tables: list) -> None:
    """Resolve every column against the sources of *its own* query scope.

    A single global alias table is unsafe: an inner CTE alias can shadow an outer table
    alias, and a projection alias (`SELECT c AS c`) would then exempt the very column it
    renames. Both were reachable bypasses, so resolution is per scope. ``scope.sources``
    already distinguishes a real ``exp.Table`` from a CTE ``Scope``; there is deliberately
    no name-based shortcut left here.
    """
    for scope in traverse_scope(tree):
        sources = {alias: source.name for alias, source in scope.sources.items()
                   if isinstance(source, exp.Table)}
        # A CTE reference resolves inside its own scope, which is checked separately; a
        # derived table stays unverifiable and keeps the historical refusal.
        unverifiable = {alias for alias, source in scope.sources.items()
                        if isinstance(source, Scope) and getattr(source, "is_cte", False)}
        local_aliases = set(scope.expression.selects and _scope_projection_names(scope) or set())
        for column in scope.columns:
            if isinstance(column.this, exp.Star):
                raise PolicyError("SELECT_STAR")
            if column.table:
                if column.table in unverifiable:
                    continue
                if column.table not in sources:
                    raise PolicyError("UNKNOWN_TABLE_ALIAS")
                possible = [sources[column.table]]
            else:
                possible = list(sources.values())
                if not possible:
                    possible = list(base_tables)
            matches = [catalog.columns[(name, column.name)] for name in possible
                       if (name, column.name) in catalog.columns]
            if not matches:
                if _in_sort_clause(column) and column.name in local_aliases:
                    continue
                raise PolicyError("COLUMN_NOT_ALLOWED")
            if any(record.get("sensitivity") == "restricted" for record in matches):
                raise PolicyError("RESTRICTED_COLUMN")


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
    # CTE references are identified by their scope source object, not by name. A real table
    # that merely shares a name with a CTE (especially a schema-qualified one) must still be
    # validated here; the old global name set let `public.customers` skip both the schema
    # check and the restricted-column check.
    cte_refs = cte_reference_table_ids(tree)
    aliases: dict[str, str] = {}
    base_tables = []
    for table in tree.find_all(exp.Table):
        if id(table) in cte_refs:
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
    _check_columns(tree, catalog, base_tables)
    for join in tree.find_all(exp.Join):
        if join.args.get("kind") == "CROSS" or not (join.args.get("on") or join.args.get("using")):
            raise PolicyError("CARTESIAN_JOIN")
        if join.args.get("using"):
            raise PolicyError("JOIN_USING_NOT_VERIFIED")
        if any(isinstance(node, exp.Or) for node in join.args["on"].walk()):
            raise PolicyError("AMBIGUOUS_JOIN_CONDITION")
        joined = join.this
        if not isinstance(joined, exp.Table) or id(joined) in cte_refs:
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
