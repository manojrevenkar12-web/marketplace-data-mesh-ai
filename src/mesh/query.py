"""SQL over the lake for people and assistants, checked before it runs: who may read it, what it will cost.

Every query, whether a person typed it or an assistant drafted it, goes through ``QueryEngine.plan``:

1. It must be one read-only statement over catalog tables (``<domain>.<product>``): no DDL, no DML, no table
   functions, no system schemas.
2. Every column it names must be readable by the principal under the Lake Formation grants (``mesh.access``);
   ``SELECT *`` expands to the readable columns only, as it would in Athena under Lake Formation.
3. Row filters apply through secure views, so a filtered principal never sees other rows, whatever the SQL.
4. The bytes it would scan are estimated from the Parquet footers of the partitions its ``dt`` predicates
   select and the columns it reads; queries over the workgroup limit, or over many partitions without a
   partition filter, are refused with the reason and the estimate.

Locally the plan runs on DuckDB over secure views. On AWS (``mesh.aws.athena``) the same plan is checked and
the SQL goes to Athena in the principal's workgroup, where Lake Formation enforces the grants again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, cast

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from mesh import config
from mesh.access import AccessPolicy, TableAccess
from mesh.contracts import Registry
from mesh.lake import Lake, connect, query_arrow, register_view
from mesh.lineage import SQL_TYPES

Purpose = Literal["person", "assistant", "service"]
FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Merge, exp.Command, exp.Copy, exp.Set)


@dataclass(frozen=True)
class Denial:
    product: str
    column: str | None  # None: the whole table
    reason: str


@dataclass
class Plan:
    sql: str
    principal: str
    purpose: Purpose
    tables: list[str] = field(default_factory=list)
    columns: dict[str, list[str]] = field(default_factory=dict)
    row_filters: dict[str, str] = field(default_factory=dict)
    partitions: dict[str, tuple[int, int]] = field(default_factory=dict)  # product -> (scanned, total)
    bytes_scanned: int = 0
    cost_usd: float = 0.0
    denied: list[Denial] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    duckdb_sql: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tables": self.tables,
            "columns": self.columns,
            "row_filters": self.row_filters,
            "partitions": {k: {"scanned": a, "total": b} for k, (a, b) in self.partitions.items()},
            "bytes_scanned": self.bytes_scanned,
            "cost_usd": round(self.cost_usd, 6),
            "denied": [d.__dict__ for d in self.denied],
            "unknown": self.unknown,
            "problems": self.problems,
        }


@dataclass
class Result:
    plan: Plan
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int


class QueryRejected(Exception):
    def __init__(self, plan: Plan):
        super().__init__("; ".join(plan.problems))
        self.plan = plan


class QueryEngine:
    def __init__(self, registry: Registry, policy: AccessPolicy, lake: Lake, today: date | None = None):
        self.registry = registry
        self.policy = policy
        self.lake = lake
        self.today = today
        self._bytes: dict[str, tuple[int, dict[str, dict[str, int]]]] = {}

    # ---------------------------------------------------------------- plan

    def plan(self, sql: str, principal: str, purpose: Purpose = "person") -> Plan:
        plan = Plan(sql=sql, principal=principal, purpose=purpose)
        try:
            statements = [s for s in sqlglot.parse(sql, read="athena") if s is not None]
        except sqlglot.errors.ParseError as e:
            plan.problems.append(f"the SQL does not parse: {str(e).splitlines()[0]}")
            return plan
        if len(statements) != 1:
            plan.problems.append("send exactly one statement")
            return plan
        tree = statements[0]
        if not isinstance(tree, exp.Query) or tree.find(*FORBIDDEN):
            plan.problems.append("only SELECT queries can run here")
            return plan

        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        access: dict[str, TableAccess] = {}
        for t in tree.find_all(exp.Table):
            if not t.db and t.name.lower() in ctes:
                continue
            if isinstance(t.this, exp.Func) or not t.name:
                plan.problems.append("table functions cannot be used; query catalog tables by name")
                continue
            name = f"{t.db}.{t.name}" if t.db else t.name
            if name not in self.registry:
                if name not in plan.unknown:
                    plan.unknown.append(name)
                continue
            if name not in access:
                access[name] = self.policy.table_access(principal, self.registry[name], self.today)
                if not access[name].visible:
                    c = self.registry[name]
                    plan.denied.append(
                        Denial(name, None, f"no grant covers {name} (owner {c.owner}); request access in the catalog")
                    )
                elif name not in plan.tables:
                    plan.tables.append(name)
        if plan.unknown:
            plan.problems.append(f"unknown table(s): {', '.join(plan.unknown)}; tables are named <domain>.<product>")
        if plan.problems or any(d.column is None for d in plan.denied):
            plan.problems += [d.reason for d in plan.denied if d.column is None]
            return plan

        # columns named explicitly must be readable (stars are expanded to readable columns later)
        try:
            explicit = self._columns(tree, {n: list(self.registry[n].table_schema()) for n in access}, expand_stars=False)
        except sqlglot.errors.OptimizeError as e:
            plan.problems.append(f"the SQL does not resolve: {e}")
            return plan
        for product, col in sorted(explicit):
            ta = access[product]
            if col in ta.hidden:
                cls = self.registry[product].column(col)
                plan.denied.append(
                    Denial(
                        product,
                        col,
                        f"{product}.{col} is {cls.classification if cls else 'restricted'}; your grants do not include it",
                    )
                )
            elif col not in ta.columns and col != self.registry[product].partition:
                plan.problems.append(f"{product} has no column '{col}'")
        if plan.denied:
            plan.problems += [d.reason for d in plan.denied]
            return plan
        if plan.problems:
            return plan

        readable = {n: list(a.columns) for n, a in access.items()}
        try:
            read = self._columns(tree, readable, expand_stars=True)
        except sqlglot.errors.OptimizeError as e:
            plan.problems.append(f"the SQL does not resolve: {e}")
            return plan
        for product, col in sorted(read):
            plan.columns.setdefault(product, []).append(col)
        plan.row_filters = {n: a.row_filter for n, a in access.items() if a.row_filter}

        # cost: partitions selected by dt predicates x columns read
        settings = config.platform()["query"]
        limit_bytes = int(settings["bytes_limit"][purpose])
        for product in plan.tables:
            c = self.registry[product]
            all_parts = self.lake.partitions(product)
            chosen = self._prune(tree, product, all_parts)
            plan.partitions[product] = (len(chosen), len(all_parts))
            sizes = self._sizes(product)
            cols = [x for x in plan.columns.get(product, []) if x != c.partition] or c.column_names[:1]
            plan.bytes_scanned += sum(sizes.get(d, {}).get(col, 0) for d in chosen for col in cols)
            if len(chosen) == len(all_parts) and len(all_parts) > int(settings["require_partition_filter_over"]):
                plan.problems.append(
                    f"{product} has {len(all_parts)} daily partitions; add a filter on {c.partition} "
                    f"(for example {c.partition} >= DATE '{all_parts[-1]}' - INTERVAL '7' DAY)"
                )
        plan.cost_usd = plan.bytes_scanned / 1e12 * float(settings["price_usd_per_tb"])
        if plan.bytes_scanned > limit_bytes:
            plan.problems.append(
                f"would scan {plan.bytes_scanned / 1e6:.0f} MB, over the {limit_bytes / 1e6:.0f} MB limit for {purpose} queries"
            )
        plan.duckdb_sql = sqlglot.transpile(sql, read="athena", write="duckdb")[0]
        return plan

    def run(self, sql: str, principal: str, purpose: Purpose = "person", max_rows: int | None = None) -> Result:
        plan = self.plan(sql, principal, purpose)
        if not plan.ok:
            raise QueryRejected(plan)
        max_rows = max_rows or int(config.platform()["query"]["max_rows"])
        con = connect()
        for product in self.registry.contracts:
            ta = self.policy.table_access(principal, self.registry[product], self.today)
            if ta.visible:
                register_view(con, self.lake, self.registry[product], list(ta.columns), ta.row_filter)
        t0 = time.perf_counter()
        try:
            table = query_arrow(con, f"SELECT * FROM ({plan.duckdb_sql}) AS q LIMIT {max_rows + 1}")
        except Exception as e:  # the engine's own errors (binder, conversion) become a reason, not a crash
            plan.problems.append(f"the query failed: {str(e).splitlines()[0][:300]}")
            raise QueryRejected(plan) from e
        rows = [list(r.values()) for r in table.to_pylist()]
        return Result(plan, table.column_names, rows[:max_rows], len(rows) > max_rows, int((time.perf_counter() - t0) * 1000))

    # ---------------------------------------------------------------- helpers

    def _columns(self, tree: Any, tables: dict[str, list[str]], expand_stars: bool) -> set[tuple[str, str]]:
        schema: dict[str, dict[str, dict[str, str]]] = {}
        for name, cols in tables.items():
            c = self.registry[name]
            types = c.table_schema()
            schema.setdefault(c.domain, {})[c.name] = {col: SQL_TYPES.get(types.get(col, "string"), "VARCHAR") for col in cols}
        q = qualify(
            tree.copy(),
            schema=cast(dict[str, object], schema),
            dialect="athena",
            expand_stars=expand_stars,
            validate_qualify_columns=False,
            identify=False,
            quote_identifiers=False,
        )
        out: set[tuple[str, str]] = set()
        for scope in traverse_scope(q):
            for col in scope.columns:
                src = scope.sources.get(col.table)
                if isinstance(src, exp.Table) and src.db:
                    out.add((f"{src.db}.{src.name}", col.name))
                elif not col.table and len([s for s in scope.sources.values() if isinstance(s, exp.Table)]) == 1:
                    only = next(s for s in scope.sources.values() if isinstance(s, exp.Table))
                    if only.db:
                        out.add((f"{only.db}.{only.name}", col.name))
        return out

    def _sizes(self, product: str) -> dict[str, dict[str, int]]:
        n = len(self.lake.partitions(product))
        cached = self._bytes.get(product)
        if cached is None or cached[0] != n:
            self._bytes[product] = (n, self.lake.partition_bytes(product))
        return self._bytes[product][1]

    def _prune(self, tree: Any, product: str, parts: list[str]) -> list[str]:
        """Partitions selected by conjunctive dt predicates on every reference to the table (conservative)."""
        c = self.registry[product]
        chosen_union: set[str] = set()
        refs = 0
        for scope in traverse_scope(tree):
            aliases = [a for a, s in scope.sources.items() if isinstance(s, exp.Table) and f"{s.db}.{s.name}" == product]
            for alias in aliases:
                refs += 1
                where = scope.expression.args.get("where") if isinstance(scope.expression, exp.Select) else None
                lo, hi, eq = None, None, None
                if where is not None:
                    for cond in _conjuncts(where.this):
                        bound = _dt_bound(cond, c.partition, alias, len(aliases) == 1, self.today)
                        if bound is None:
                            continue
                        op, val = bound
                        if op == "eq":
                            eq = (eq or set(val)) & set(val)
                        elif op in ("ge", "gt"):
                            v = val[0] if op == "ge" else (date.fromisoformat(val[0]) + timedelta(1)).isoformat()
                            lo = max(lo, v) if lo else v
                        elif op in ("le", "lt"):
                            v = val[0] if op == "le" else (date.fromisoformat(val[0]) - timedelta(1)).isoformat()
                            hi = min(hi, v) if hi else v
                sel = {p for p in parts if (lo is None or p >= lo) and (hi is None or p <= hi) and (eq is None or p in eq)}
                chosen_union |= sel
        return sorted(chosen_union) if refs else parts


def _conjuncts(e: Any) -> list[Any]:
    if isinstance(e, exp.And):
        return _conjuncts(e.left) + _conjuncts(e.right)
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    return [e]


def _date_value(e: Any, today: date | None) -> str | None:
    if isinstance(e, exp.Cast) and isinstance(e.this, exp.Literal):
        return _iso(e.this.name)
    if isinstance(e, exp.Literal) and e.is_string:
        return _iso(e.name)
    if isinstance(e, (exp.CurrentDate,)):
        return (today or date.today()).isoformat()
    if isinstance(e, (exp.Sub, exp.DateSub)) and isinstance(e.expression, exp.Interval):
        base = _date_value(e.this, today)
        iv = e.expression
        unit = (iv.unit.name if iv.unit else "DAY").upper()
        try:
            n = int(iv.this.name)
        except (ValueError, AttributeError):
            return None
        if base and unit.startswith("DAY"):
            return (date.fromisoformat(base) - timedelta(n)).isoformat()
    if isinstance(e, exp.Add) and isinstance(e.expression, exp.Interval):
        base = _date_value(e.this, today)
        iv = e.expression
        try:
            n = int(iv.this.name)
        except (ValueError, AttributeError):
            return None
        if base and (iv.unit.name if iv.unit else "DAY").upper().startswith("DAY"):
            return (date.fromisoformat(base) + timedelta(n)).isoformat()
    return None


def _iso(s: str) -> str | None:
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _is_dt(e: Any, partition: str, alias: str, sole: bool) -> bool:
    return isinstance(e, exp.Column) and e.name == partition and (e.table == alias or (not e.table and sole))


def _dt_bound(cond: Any, partition: str, alias: str, sole: bool, today: date | None) -> tuple[str, list[str]] | None:
    ops = {exp.EQ: "eq", exp.GTE: "ge", exp.GT: "gt", exp.LTE: "le", exp.LT: "lt"}
    flip = {"ge": "le", "gt": "lt", "le": "ge", "lt": "gt", "eq": "eq"}
    for klass, op in ops.items():
        if isinstance(cond, klass):
            if _is_dt(cond.left, partition, alias, sole) and (v := _date_value(cond.right, today)):
                return op, [v]
            if _is_dt(cond.right, partition, alias, sole) and (v := _date_value(cond.left, today)):
                return flip[op], [v]
    if isinstance(cond, exp.Between) and _is_dt(cond.this, partition, alias, sole):
        lo, hi = _date_value(cond.args["low"], today), _date_value(cond.args["high"], today)
        if lo and hi:
            # two bounds; represented as an inclusive range through ge/le handled by the caller twice
            return "eq", [
                (date.fromisoformat(lo) + timedelta(i)).isoformat()
                for i in range((date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1)
            ]
    if isinstance(cond, exp.In) and _is_dt(cond.this, partition, alias, sole):
        vals = [_date_value(v, today) for v in cond.expressions]
        if vals and all(vals):
            return "eq", [v for v in vals if v]
    return None
