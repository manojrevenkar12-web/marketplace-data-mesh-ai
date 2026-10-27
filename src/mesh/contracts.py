"""Data contracts: what a data product promises its consumers, and the rules for changing that promise.

A contract is the YAML file a domain team keeps next to its product (products/<domain>/<product>.yaml). It
declares the schema with a classification per column (which becomes the column's Lake Formation tag), the
quality checks the platform runs before publishing a partition, the freshness SLA, and for derived products
the inputs and the SQL transform. ``lint`` checks one contract on its own and against the others;
``diff`` classifies a change for the consumers that exist today.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from mesh import config

Classification = Literal["public", "internal", "confidential", "pii"]
SENSITIVITY_ORDER: dict[str, int] = {"public": 0, "internal": 1, "confidential": 2, "pii": 3}
PARTITION_TYPE = "date"

# A consumer reading the old type can still read the new one.
WIDENING: set[tuple[str, str]] = {("int", "bigint"), ("int", "double"), ("bigint", "double"), ("date", "timestamp")}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Column(_M):
    name: str
    type: str
    description: str = ""
    classification: Classification = "internal"
    required: bool = False
    deprecated_on: date | None = None  # announced removal; the column may go after the deprecation window


class QualityCheck(_M):
    check: Literal["not_null", "unique", "accepted_values", "range", "null_share", "volume", "referential"]
    severity: Literal["error", "warn"] = "error"
    column: str | None = None
    values: list[str] | None = None
    min: float | None = None
    max: float | None = None
    min_ratio: float | None = None
    max_ratio: float | None = None
    ref: str | None = None  # referential: "<product>.<column>"

    @property
    def id(self) -> str:
        return f"{self.check}:{self.column or '*'}"


class Sla(_M):
    freshness_hours: float


class Source(_M):
    system: str
    format: Literal["jsonl", "csv", "parquet"] = "jsonl"


class Contract(_M):
    product: str
    domain: str
    owner: str
    owner_contact: str = ""
    kind: Literal["source", "derived"]
    version: int
    description: str
    tags: list[str] = Field(default_factory=list)
    partition: str = "dt"
    sla: Sla
    source: Source | None = None
    inputs: list[str] = Field(default_factory=list)
    transform: str | None = None
    status: Literal["active", "deprecated"] = "active"
    columns: list[Column]
    quality: list[QualityCheck] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.product.split(".", 1)[1]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def schema(self) -> dict[str, str]:
        """Column -> type, with the partition column, as the query engine sees the table."""
        return {**{c.name: c.type for c in self.columns}, self.partition: PARTITION_TYPE}


def parse(text: str) -> Contract:
    return Contract.model_validate(yaml.safe_load(text))


@dataclass
class Registry:
    """The contracts in force, keyed by product name, with the transforms' SQL."""

    contracts: dict[str, Contract]
    sql: dict[str, str] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path | None = None) -> Registry:
        root = root or config.products_dir()
        contracts: dict[str, Contract] = {}
        sql: dict[str, str] = {}
        paths: dict[str, Path] = {}
        for f in sorted(root.glob("*/*.yaml")):
            c = parse(f.read_text())
            contracts[c.product] = c
            paths[c.product] = f
            if c.transform and (f.parent / c.transform).exists():
                sql[c.product] = (f.parent / c.transform).read_text()
        return cls(contracts, sql, paths)

    def __getitem__(self, product: str) -> Contract:
        return self.contracts[product]

    def __contains__(self, product: object) -> bool:
        return product in self.contracts

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.contracts.values())

    def order(self) -> list[str]:
        """Products in dependency order (inputs before the products that read them)."""
        done: list[str] = []
        seen: set[str] = set()

        def visit(p: str, path: tuple[str, ...]) -> None:
            if p in seen:
                return
            if p in path:
                raise ValueError(f"cycle in product inputs: {' -> '.join((*path, p))}")
            for i in self.contracts[p].inputs:
                if i in self.contracts:
                    visit(i, (*path, p))
            seen.add(p)
            done.append(p)

        for p in sorted(self.contracts):
            visit(p, ())
        return done

    def consumers(self, product: str) -> list[str]:
        return sorted(c.product for c in self if product in c.inputs)

    def with_contract(self, contract: Contract, sql: str | None = None) -> Registry:
        """A copy with one contract replaced (to check a proposed change against everything else)."""
        contracts = {**self.contracts, contract.product: contract}
        new_sql = dict(self.sql)
        if sql is not None:
            new_sql[contract.product] = sql
        return Registry(contracts, new_sql, dict(self.paths))


# ---------------------------------------------------------------- lint


@dataclass(frozen=True)
class Problem:
    product: str
    rule: str
    message: str
    severity: Literal["error", "warn"] = "error"


def render_sql(sql: str, dt: str) -> str:
    return sql.replace("{{ dt }}", dt)


def output_columns(sql: str) -> list[str]:
    """Names of the columns a transform's final SELECT produces."""
    tree = sqlglot.parse_one(render_sql(sql, "2000-01-01"), read="athena")
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return []
    return [e.alias_or_name for e in select.expressions]


def referenced_tables(sql: str) -> set[str]:
    """Products a transform reads (``domain.product``), CTE names excluded."""
    tree = sqlglot.parse_one(render_sql(sql, "2000-01-01"), read="athena")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    out = set()
    for t in tree.find_all(exp.Table):
        if t.name in ctes and not t.db:
            continue
        out.add(f"{t.db}.{t.name}" if t.db else t.name)
    return out


def lint(c: Contract, registry: Registry, path: Path | None = None, sql: str | None = None) -> list[Problem]:
    p: list[Problem] = []

    def err(rule: str, msg: str, severity: Literal["error", "warn"] = "error") -> None:
        p.append(Problem(c.product, rule, msg, severity))

    allowed_types = set(config.platform()["contracts"]["allowed_types"])
    if c.product.count(".") != 1 or c.product.split(".")[0] != c.domain:
        err("name", f"product must be '<domain>.<name>' with domain '{c.domain}'")
    if path is not None and (path.parent.name != c.domain or path.stem != c.name):
        err("path", f"file should be products/{c.domain}/{c.name}.yaml")
    if c.domain not in config.load("access")["lf_tags"]["domain"]:
        err("domain", f"domain '{c.domain}' has no Lake Formation tag value; add it to config/access.yaml first")
    if not c.description.strip() or len(c.description.split()) < 8:
        err("description", "describe the product in at least one sentence; the catalog and the assistant search it", "warn")
    names = [col.name for col in c.columns]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        err("columns", f"column '{dup}' is declared twice")
    if c.partition in names:
        err("partition", f"'{c.partition}' is the partition column and must not be declared as a column")
    for col in c.columns:
        if col.type not in allowed_types:
            err("type", f"column '{col.name}' has type '{col.type}'; allowed: {', '.join(sorted(allowed_types))}")
        if not col.description.strip():
            err("description", f"column '{col.name}' has no description", "warn")
    for q in c.quality:
        if q.column and q.column not in names:
            err("quality", f"check {q.id} refers to unknown column '{q.column}'")
        if q.check == "accepted_values" and not q.values:
            err("quality", f"check {q.id} needs values")
        if q.check == "referential" and (not q.ref or q.ref.rsplit(".", 1)[0] not in registry):
            err("quality", f"check {q.id} must reference '<product>.<column>' of a known product")
    if c.kind == "source" and c.source is None:
        err("source", "a source product declares the system it comes from")
    if c.kind == "derived":
        if not c.inputs:
            err("inputs", "a derived product declares its inputs")
        for i in c.inputs:
            if i not in registry:
                err("inputs", f"input '{i}' is not a known product")
            elif registry[i].status == "deprecated":
                err("inputs", f"input '{i}' is deprecated", "warn")
            elif registry[i].sla.freshness_hours > c.sla.freshness_hours:
                err("sla", f"freshness {c.sla.freshness_hours} h is tighter than input '{i}' ({registry[i].sla.freshness_hours} h)")
        text = sql if sql is not None else registry.sql.get(c.product)
        if not c.transform or text is None:
            err("transform", "a derived product needs its SQL transform next to the contract")
        else:
            try:
                outs = output_columns(text)
                tables = referenced_tables(text)
            except sqlglot.errors.ParseError as e:
                err("transform", f"the transform does not parse: {str(e).splitlines()[0]}")
            else:
                if sorted(outs) != sorted(names):
                    missing, extra = sorted(set(names) - set(outs)), sorted(set(outs) - set(names))
                    err("transform", f"transform output does not match the columns (missing {missing}, not declared {extra})")
                for t in sorted(tables - set(c.inputs)):
                    err("transform", f"transform reads '{t}', which is not a declared input")
                try:
                    for t in c.inputs:
                        if t in registry and text is not None:
                            used = columns_used(text, t, registry)
                            gone = sorted(u for u in used if registry[t].column(u) is None and u != registry[t].partition)
                            for g in gone:
                                err("transform", f"transform reads '{t}.{g}', which that contract does not declare")
                except sqlglot.errors.OptimizeError as e:
                    err("transform", f"the transform reads a column its inputs do not have ({e})")
                else:
                    p += _propagation(c, registry, text)
    return p


def _propagation(c: Contract, registry: Registry, sql: str) -> list[Problem]:
    """A derived column must be classified at least as high as the columns it is computed from, or the
    derived product would publish confidential or personal data under a lower tag."""
    from mesh.lineage import column_lineage

    out: list[Problem] = []
    reg = registry.with_contract(c, sql)
    for col, sources in column_lineage(c.product, reg).items():
        mine = c.column(col)
        if mine is None:
            continue
        for prod, src in sorted(sources):
            sc = reg[prod].column(src) if prod in reg else None
            if sc and SENSITIVITY_ORDER[sc.classification] > SENSITIVITY_ORDER[mine.classification]:
                out.append(Problem(c.product, "classification", f"'{col}' is {mine.classification} but is computed from "
                                                                 f"{prod}.{src} ({sc.classification}); classify it {sc.classification}"))
    return out


def lint_all(registry: Registry) -> list[Problem]:
    out: list[Problem] = []
    for c in registry:
        out += lint(c, registry, registry.paths.get(c.product))
    try:
        registry.order()
    except ValueError as e:
        out.append(Problem("*", "inputs", str(e)))
    return out


def columns_used(sql: str, product: str, registry: Registry) -> set[str]:
    """Columns of ``product`` that a transform reads, resolved through CTEs and aliases."""
    from mesh.lineage import source_columns

    return {col for (prod, col) in source_columns(render_sql(sql, "2000-01-01"), registry) if prod == product}


# ---------------------------------------------------------------- diff


ChangeKind = Literal[
    "column_added",
    "column_removed",
    "column_renamed",
    "column_deprecated",
    "type_widened",
    "type_changed",
    "became_optional",
    "became_required",
    "classification_raised",
    "classification_lowered",
    "quality_removed",
    "quality_loosened",
    "quality_added",
    "sla_loosened",
    "sla_tightened",
    "partition_changed",
    "inputs_changed",
    "transform_changed",
    "product_deprecated",
    "description_changed",
]


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    product: str
    column: str | None
    detail: str
    old: Any = None
    new: Any = None


def diff(old: Contract, new: Contract, old_sql: str | None = None, new_sql: str | None = None) -> list[Change]:
    ch: list[Change] = []
    p = new.product
    old_cols, new_cols = {c.name: c for c in old.columns}, {c.name: c for c in new.columns}
    removed = [old_cols[n] for n in old_cols if n not in new_cols]
    added = [new_cols[n] for n in new_cols if n not in old_cols]
    # a removed and an added column with the same type and classification is most likely a rename
    renames: list[tuple[Column, Column]] = []
    for r in list(removed):
        cand = [a for a in added if a.type == r.type and a.classification == r.classification]
        if len(cand) == 1 and (r.name in cand[0].name or cand[0].name in r.name or _similar(r.description, cand[0].description)):
            renames.append((r, cand[0]))
            removed.remove(r)
            added.remove(cand[0])
    for r, a in renames:
        ch.append(Change("column_renamed", p, r.name, f"'{r.name}' renamed to '{a.name}'", r.name, a.name))
    for r in removed:
        ch.append(Change("column_removed", p, r.name, f"'{r.name}' removed", r.deprecated_on, None))
    for a in added:
        ch.append(Change("column_added", p, a.name, f"'{a.name}' added ({a.type}, {a.classification})", None, a.type))
    for n in sorted(set(old_cols) & set(new_cols)):
        o, nw = old_cols[n], new_cols[n]
        if o.type != nw.type:
            kind: ChangeKind = "type_widened" if (o.type, nw.type) in WIDENING else "type_changed"
            ch.append(Change(kind, p, n, f"'{n}' type {o.type} -> {nw.type}", o.type, nw.type))
        if o.required and not nw.required:
            ch.append(Change("became_optional", p, n, f"'{n}' may now be empty", True, False))
        if not o.required and nw.required:
            ch.append(Change("became_required", p, n, f"'{n}' is now required", False, True))
        so, sn = SENSITIVITY_ORDER[o.classification], SENSITIVITY_ORDER[nw.classification]
        if sn > so:
            ch.append(Change("classification_raised", p, n, f"'{n}' {o.classification} -> {nw.classification}", o.classification, nw.classification))
        if sn < so:
            ch.append(Change("classification_lowered", p, n, f"'{n}' {o.classification} -> {nw.classification}", o.classification, nw.classification))
        if nw.deprecated_on and not o.deprecated_on:
            ch.append(Change("column_deprecated", p, n, f"'{n}' deprecated from {nw.deprecated_on}", None, nw.deprecated_on))
    renamed = {r.name: a.name for r, a in renames}
    oq = {q.model_copy(update={"column": renamed.get(q.column or "", q.column)}).id: q for q in old.quality}
    nq = {q.id: q for q in new.quality}
    for qid in sorted(set(oq) - set(nq)):
        ch.append(Change("quality_removed", p, oq[qid].column, f"check {qid} removed"))
    for qid in sorted(set(nq) - set(oq)):
        ch.append(Change("quality_added", p, nq[qid].column, f"check {qid} added"))
    for qid in sorted(set(oq) & set(nq)):
        if _looser(oq[qid], nq[qid]):
            ch.append(Change("quality_loosened", p, oq[qid].column, f"check {qid} loosened"))
    if new.sla.freshness_hours > old.sla.freshness_hours:
        ch.append(Change("sla_loosened", p, None, f"freshness {old.sla.freshness_hours} h -> {new.sla.freshness_hours} h"))
    elif new.sla.freshness_hours < old.sla.freshness_hours:
        ch.append(Change("sla_tightened", p, None, f"freshness {old.sla.freshness_hours} h -> {new.sla.freshness_hours} h"))
    if old.partition != new.partition:
        ch.append(Change("partition_changed", p, None, f"partition {old.partition} -> {new.partition}"))
    if sorted(old.inputs) != sorted(new.inputs):
        ch.append(Change("inputs_changed", p, None, f"inputs {old.inputs} -> {new.inputs}"))
    if old_sql is not None and new_sql is not None and _norm_sql(old_sql) != _norm_sql(new_sql):
        ch.append(Change("transform_changed", p, None, "transform SQL changed"))
    if old.status != "deprecated" and new.status == "deprecated":
        ch.append(Change("product_deprecated", p, None, "product deprecated"))
    if old.description.strip() != new.description.strip():
        ch.append(Change("description_changed", p, None, "description changed"))
    return ch


def _similar(a: str, b: str) -> bool:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.5


def _looser(o: QualityCheck, n: QualityCheck) -> bool:
    if o.severity == "error" and n.severity == "warn":
        return True
    if o.check == "accepted_values" and set(n.values or []) > set(o.values or []):
        return True
    if o.check == "range" and ((o.min is not None and (n.min is None or n.min < o.min)) or (o.max is not None and (n.max is None or n.max > o.max))):
        return True
    if o.check == "null_share" and (n.max or 0) > (o.max or 0):
        return True
    return o.check == "volume" and ((n.min_ratio or 0) < (o.min_ratio or 0) or (n.max_ratio or 1e9) > (o.max_ratio or 1e9))


def _norm_sql(s: str) -> str:
    try:
        return sqlglot.parse_one(render_sql(s, "2000-01-01"), read="athena").sql(dialect="athena", comments=False)
    except sqlglot.errors.ParseError:
        return " ".join(s.split())


def is_widening(old_type: str, new_type: str) -> bool:
    return (old_type, new_type) in WIDENING


def changed_products(changes: Iterable[Change]) -> set[str]:
    return {c.product for c in changes}
