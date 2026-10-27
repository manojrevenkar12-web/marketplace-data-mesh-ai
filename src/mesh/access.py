"""Who may read what: Lake Formation tag-based access control, evaluated the way Lake Formation evaluates it.

``config/access.yaml`` holds the tag values, the principals and their grants. A table's ``domain`` tag comes
from its contract; a column's ``sensitivity`` tag is its classification. A principal may read a column when
any of its grants matches both tags. Grants are additive. A row filter applies to a table when every grant
that gives access to that table carries one (one unfiltered grant means the principal sees every row), and
it only applies to tables that have the filtered column. The Terraform renderer in ``mesh.aws.terraform``
turns the same objects into ``aws_lakeformation_*`` resources, so the platform's checks and Athena agree.

Approved access requests (service) are extra grants layered on top, valid until they expire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import sqlglot
from sqlglot import exp

from mesh import config
from mesh.contracts import Contract


@dataclass(frozen=True)
class Grant:
    domains: tuple[str, ...] | None  # None means every domain
    sensitivities: tuple[str, ...] | None
    row_filter: str | None = None
    source: str = "config"  # "config" or "request:<id>"
    expires: date | None = None
    products: tuple[str, ...] | None = None  # access requests are for named products, not tag expressions

    def matches_table(self, c: Contract) -> bool:
        if self.products is not None and c.product not in self.products:
            return False
        return self.domains is None or c.domain in self.domains

    def matches_column(self, sensitivity: str) -> bool:
        return self.sensitivities is None or sensitivity in self.sensitivities

    def active(self, on: date | None = None) -> bool:
        return self.expires is None or (on or date.today()) <= self.expires


def _tuple(v: Any) -> tuple[str, ...] | None:
    if v == "*" or v is None:
        return None
    return tuple(v)


@dataclass
class Principal:
    name: str
    description: str
    kind: str
    grants: list[Grant] = field(default_factory=list)


@dataclass(frozen=True)
class User:
    id: str
    name: str
    principal: str


@dataclass(frozen=True)
class TableAccess:
    product: str
    columns: tuple[str, ...]  # readable columns, in contract order (partition column included when any is readable)
    hidden: tuple[str, ...]  # columns that exist but this principal may not read
    row_filter: str | None

    @property
    def visible(self) -> bool:
        return bool(self.columns)


class AccessPolicy:
    def __init__(self, raw: dict[str, Any] | None = None):
        raw = raw if raw is not None else config.load("access")
        self.tags: dict[str, list[str]] = raw["lf_tags"]
        self.principals: dict[str, Principal] = {}
        for name, p in raw["principals"].items():
            grants = [
                Grant(_tuple(g.get("domain")), _tuple(g.get("sensitivity")), g.get("row_filter")) for g in p.get("grants", [])
            ]
            self.principals[name] = Principal(name, p.get("description", ""), p.get("kind", "team"), grants)
        self.users: dict[str, User] = {uid: User(uid, u["name"], u["principal"]) for uid, u in raw.get("users", {}).items()}
        for g in [g for p in self.principals.values() for g in p.grants]:
            if g.row_filter:
                _check_filter(g.row_filter)

    def principal_of(self, user: str) -> str:
        if user in self.users:
            return self.users[user].principal
        if user in self.principals:
            return user
        raise KeyError(f"unknown user or principal '{user}'")

    def add_grant(self, principal: str, grant: Grant) -> None:
        self.principals[principal].grants.append(grant)

    def table_access(self, principal: str, c: Contract, on: date | None = None) -> TableAccess:
        grants = [g for g in self.principals[principal].grants if g.active(on) and g.matches_table(c)]
        readable = [col.name for col in c.columns if any(g.matches_column(col.classification) for g in grants)]
        hidden = [col.name for col in c.columns if col.name not in readable]
        filt: str | None = None
        if grants and readable:
            filters = [g.row_filter for g in grants]
            if all(filters):
                usable = [f for f in filters if f and _filter_columns(f) <= set(c.column_names)]
                if usable:
                    filt = " OR ".join(f"({f})" for f in dict.fromkeys(usable))
        cols = (*readable, c.partition) if readable else ()
        return TableAccess(c.product, tuple(cols), tuple(hidden), filt)

    def owner_principal(self, c: Contract) -> str:
        return c.owner


def _check_filter(text: str) -> None:
    tree = sqlglot.parse_one(text, read="athena")
    if tree.find(exp.Select) or tree.find(exp.Subquery):
        raise ValueError(f"row filter must be a plain predicate: {text}")


def _filter_columns(text: str) -> set[str]:
    return {c.name for c in sqlglot.parse_one(text, read="athena").find_all(exp.Column)}
