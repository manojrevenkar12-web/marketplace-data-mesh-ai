"""Lineage between data products, down to columns, read from the transforms' SQL.

Product lineage comes from the contracts' declared inputs; column lineage from parsing each transform with
sqlglot, qualifying it against the contracts' schemas and resolving every column through CTEs and aliases.
The change reviewer uses it to tell which consumers read a changed column; the failure triage uses it to
walk upstream from a failed product.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage as sg_lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

if TYPE_CHECKING:
    from mesh.contracts import Registry

SQL_TYPES = {
    "string": "VARCHAR",
    "int": "INT",
    "bigint": "BIGINT",
    "double": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}


def schema_of(registry: Registry) -> dict[str, dict[str, dict[str, str]]]:
    out: dict[str, dict[str, dict[str, str]]] = {}
    for c in registry:
        out.setdefault(c.domain, {})[c.name] = {k: SQL_TYPES.get(v, "VARCHAR") for k, v in c.schema().items()}
    return out


def _qualified(sql: str, registry: Registry) -> exp.Expression:
    tree = sqlglot.parse_one(sql, read="athena")
    return qualify(tree, schema=schema_of(registry), dialect="athena", validate_qualify_columns=False, identify=False)


def source_columns(sql: str, registry: Registry) -> set[tuple[str, str]]:
    """Every (product, column) a query reads from a base table, through CTEs, subqueries and aliases."""
    tree = _qualified(sql, registry)
    out: set[tuple[str, str]] = set()
    for scope in traverse_scope(tree):
        for col in scope.columns:
            src = scope.sources.get(col.table)
            if isinstance(src, exp.Table) and src.db:
                out.add((f"{src.db}.{src.name}", col.name))
        # COUNT(*) and friends read no column; SELECT * was expanded by qualify
    return out


def tables_read(sql: str, registry: Registry) -> set[str]:
    tree = _qualified(sql, registry)
    out = set()
    for scope in traverse_scope(tree):
        for src in scope.sources.values():
            if isinstance(src, exp.Table) and src.db:
                out.add(f"{src.db}.{src.name}")
    return out


def column_lineage(product: str, registry: Registry) -> dict[str, set[tuple[str, str]]]:
    """For a derived product: output column -> the input (product, column) pairs it is computed from."""
    from mesh.contracts import render_sql

    sql = render_sql(registry.sql[product], "2000-01-01")
    schema = schema_of(registry)
    out: dict[str, set[tuple[str, str]]] = {}
    for col in registry[product].column_names:
        try:
            node = sg_lineage(col, sql, schema=schema, dialect="athena")
        except Exception:  # an unresolvable column is reported by lint, not here
            out[col] = set()
            continue
        leaves: set[tuple[str, str]] = set()
        for n in node.walk():
            if isinstance(n.expression, exp.Table) and n.expression.db:
                leaves.add((f"{n.expression.db}.{n.expression.name}", n.name.split(".")[-1]))
        out[col] = leaves
    return out


@dataclass(frozen=True)
class Edge:
    upstream: str
    downstream: str


def edges(registry: Registry) -> list[Edge]:
    return [Edge(i, c.product) for c in registry for i in c.inputs]


def upstream(product: str, registry: Registry) -> list[str]:
    """All products ``product`` depends on, nearest first."""
    seen: list[str] = []
    q = deque(registry[product].inputs)
    while q:
        p = q.popleft()
        if p in seen or p not in registry:
            continue
        seen.append(p)
        q.extend(registry[p].inputs)
    return seen


def downstream(product: str, registry: Registry) -> list[str]:
    seen: list[str] = []
    q = deque(registry.consumers(product))
    while q:
        p = q.popleft()
        if p in seen:
            continue
        seen.append(p)
        q.extend(registry.consumers(p))
    return seen


def readers_of(product: str, column: str, registry: Registry) -> list[str]:
    """Derived products whose transform reads ``product.column``."""
    out = []
    for c in registry:
        if product in c.inputs and c.product in registry.sql:
            from mesh.contracts import render_sql

            if (product, column) in source_columns(render_sql(registry.sql[c.product], "2000-01-01"), registry):
                out.append(c.product)
    return sorted(out)


__all__ = ["Edge", "Scope", "column_lineage", "downstream", "edges", "readers_of", "schema_of", "source_columns", "tables_read", "upstream"]
