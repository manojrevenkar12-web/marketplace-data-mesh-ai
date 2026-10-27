"""Contract versions over time. A partition is always processed under the contract (and transform) in force
on its date, so a change merged on Tuesday does not rewrite Monday, and the triage can ask "what changed
upstream just before this broke"."""

from __future__ import annotations

import itertools

from mesh.contracts import Change, Registry, diff, parse
from mesh.state import ContractVersion, State


class Versions:
    def __init__(self, state: State, base: Registry):
        self.state = state
        self.base = base
        self._cache: list[ContractVersion] | None = None

    def bootstrap(self, effective: str, author: str = "repository") -> None:
        if self.state.contract_versions():
            return
        for c in self.base:
            path = self.base.paths.get(c.product)
            text = path.read_text() if path else ""
            self.state.add_contract_version(
                ContractVersion(
                    c.product,
                    c.version,
                    effective,
                    text,
                    self.base.sql.get(c.product),
                    author,
                    "initial",
                    f"{effective}T00:00:00Z",
                )
            )
        self._cache = None

    def publish(
        self, contract_yaml: str, sql: str | None, effective: str, author: str, note: str, published: str = ""
    ) -> ContractVersion:
        c = parse(contract_yaml)
        v = ContractVersion(
            c.product, c.version, effective, contract_yaml, sql, author, note, published or f"{effective}T00:00:00Z"
        )
        self.state.add_contract_version(v)
        self._cache = None
        return v

    def all(self) -> list[ContractVersion]:
        if self._cache is None:
            self._cache = self.state.contract_versions()
        return self._cache

    def history(self, product: str, published_before: str | None = None) -> list[ContractVersion]:
        vs = [v for v in self.all() if v.product == product and (published_before is None or v.published <= published_before)]
        return sorted(vs, key=lambda v: (v.effective, v.published))

    def in_force(self, product: str, dt: str) -> ContractVersion | None:
        hs = [v for v in self.history(product) if v.effective <= dt]
        return hs[-1] if hs else None

    def registry_at(self, dt: str) -> Registry:
        contracts, sql = {}, {}
        for product in {v.product for v in self.all()} | set(self.base.contracts):
            v = self.in_force(product, dt)
            if v is None:
                if product in self.base:
                    contracts[product] = self.base[product]
                    if product in self.base.sql:
                        sql[product] = self.base.sql[product]
                continue
            contracts[product] = parse(v.contract)
            if v.sql is not None:
                sql[product] = v.sql
        return Registry(contracts, sql, dict(self.base.paths))

    def changes(
        self, product: str, since: str, until: str, published_before: str | None = None
    ) -> list[tuple[ContractVersion, list[Change]]]:
        """Versions that took effect in (since, until], each with what changed against the one before."""
        hs = self.history(product, published_before)
        out = []
        for prev, cur in itertools.pairwise(hs):
            if since < cur.effective <= until:
                out.append((cur, diff(parse(prev.contract), parse(cur.contract), prev.sql, cur.sql)))
        return out
