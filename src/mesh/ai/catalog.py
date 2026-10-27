"""Dataset discovery: search the catalog the way people ask, and show each person only what they may read.

The catalog is built from the contracts (descriptions, tags, column docs), the business glossary (English and
German terms), and the platform's own state (latest partition, freshness, last quality result). Search is BM25
over that text with glossary expansion: "Händler" finds ``dealers.dealers``. A product the person cannot read
still shows up by name and owner so they know whom to ask (data mesh is about discoverability), but none of
its columns or rows do.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from mesh import config
from mesh.access import AccessPolicy
from mesh.ai.guard import screen
from mesh.contracts import Contract, Registry
from mesh.lake import Lake
from mesh.state import State

_TOKEN = re.compile(r"[a-z0-9äöüß]+")
STOP = {
    "the",
    "a",
    "an",
    "of",
    "for",
    "in",
    "on",
    "by",
    "per",
    "and",
    "or",
    "to",
    "is",
    "are",
    "how",
    "many",
    "what",
    "which",
    "was",
    "were",
    "with",
    "from",
    "each",
    "did",
    "do",
    "does",
    "show",
    "me",
    "give",
    "list",
    "der",
    "die",
    "das",
}


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower().replace("_", " ").replace(".", " ")):
        if t in STOP:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


@dataclass
class Entry:
    product: str
    domain: str
    owner: str
    owner_contact: str
    kind: str
    description: str
    tags: list[str]
    columns: list[dict[str, Any]]  # visible columns only
    hidden_columns: int
    row_filter: str | None
    partition: str
    latest_dt: str | None
    partitions: int
    freshness: str  # fresh | late | no data
    quality: str  # passed | warnings | failed | not run
    inputs: list[str]
    consumers: list[str]
    flagged_text: list[str] = field(default_factory=list)  # sentences removed from the description for the model
    visible: bool = True

    def as_prompt(self) -> dict[str, Any]:
        """What a model may see about this dataset: no hidden column, no row."""
        return {
            "table": self.product,
            "description": self.description,
            "partition": f"{self.partition} (DATE), latest {self.latest_dt}",
            "columns": [{"name": c["name"], "type": c["type"], "description": c["description"]} for c in self.columns],
            **({"rows_visible": f"only rows where {self.row_filter}"} if self.row_filter else {}),
        }


@dataclass
class Hit:
    entry: Entry
    score: float
    matched: list[str]


class Catalog:
    def __init__(
        self,
        registry: Registry,
        policy: AccessPolicy,
        lake: Lake,
        state: State | None = None,
        glossary: dict[str, Any] | None = None,
        today: date | None = None,
    ):
        self.registry = registry
        self.policy = policy
        self.lake = lake
        self.state = state
        self.glossary = (glossary or config.load("glossary")).get("terms", [])
        self.today = today

    # ---------------------------------------------------------------- entries

    def entry(self, product: str, principal: str) -> Entry:
        c = self.registry[product]
        ta = self.policy.table_access(principal, c, self.today)
        parts = self.lake.partitions(product)
        latest = parts[-1] if parts else None
        sc = screen(c.description)
        cols = [
            {
                "name": col.name,
                "type": col.type,
                "description": screen(col.description).text,
                "classification": col.classification,
                "required": col.required,
                "deprecated_on": str(col.deprecated_on) if col.deprecated_on else None,
            }
            for col in c.columns
            if col.name in ta.columns
        ]
        flagged = sc.flagged + [f for col in c.columns for f in screen(col.description).flagged]
        return Entry(
            product=product,
            domain=c.domain,
            owner=c.owner,
            owner_contact=c.owner_contact,
            kind=c.kind,
            description=sc.text,
            tags=c.tags,
            columns=cols if ta.visible else [],
            hidden_columns=len(ta.hidden) if ta.visible else len(c.columns),
            row_filter=ta.row_filter,
            partition=c.partition,
            latest_dt=latest if ta.visible else None,
            partitions=len(parts) if ta.visible else 0,
            freshness=self._freshness(c, latest) if ta.visible else "unknown",
            quality=self._quality(c, latest) if ta.visible else "unknown",
            inputs=c.inputs,
            consumers=self.registry.consumers(product),
            flagged_text=flagged,
            visible=ta.visible,
        )

    def entries(self, principal: str) -> list[Entry]:
        return [self.entry(p, principal) for p in sorted(self.registry.contracts)]

    def _freshness(self, c: Contract, latest: str | None) -> str:
        if latest is None:
            return "no data"
        now = datetime.combine(self.today or date.today(), datetime.min.time()) + timedelta(
            hours=config.platform()["freshness"]["check_at_hour_utc"]
        )
        # a partition for day D is complete at the end of D; its age is measured from then
        age_h = (now - (datetime.fromisoformat(latest) + timedelta(days=1))).total_seconds() / 3600
        return "fresh" if age_h <= c.sla.freshness_hours else "late"

    def _quality(self, c: Contract, latest: str | None) -> str:
        if self.state is None or latest is None:
            return "not run"
        results = self.state.quality(c.product, latest)
        if not results:
            return "not run"
        last = {q.check: q for q in results}  # the last result per check (re-runs append)
        if any(not q.passed and q.severity == "error" for q in last.values()):
            return "failed"
        return "warnings" if any(not q.passed for q in last.values()) else "passed"

    # ---------------------------------------------------------------- search

    def _doc(self, e: Entry) -> list[str]:
        c = self.registry[e.product]
        text = [e.product, e.description, " ".join(e.tags)]
        if e.visible:
            text += [f"{col['name']} {col['description']}" for col in e.columns]
        for g in self.glossary:
            if e.product in g.get("products", []):
                text.append(" ".join([g["term"], *g.get("also", [])]))
        return tokens(" ".join(text)) + tokens(c.name) * 2

    def expand(self, query: str) -> tuple[list[str], set[str]]:
        """Query tokens plus glossary terms, and the products the glossary points at."""
        q = query.lower()
        boost: set[str] = set()
        extra: list[str] = []
        for g in self.glossary:
            names = [g["term"], *g.get("also", [])]
            if any(re.search(rf"(?<![a-z0-9äöüß]){re.escape(n.lower())}", q) for n in names):
                boost |= set(g.get("products", []))
                extra += tokens(g["term"])
        return tokens(query) + extra, boost

    def search(self, query: str, principal: str, k: int = 5) -> tuple[list[Hit], list[Hit]]:
        """(visible hits, restricted hits): the second list names products the person cannot read."""
        entries = self.entries(principal)
        docs = {e.product: self._doc(e) for e in entries}
        q, boost = self.expand(query)
        n = len(docs)
        avg = sum(len(d) for d in docs.values()) / max(n, 1)
        df: Counter[str] = Counter()
        for d in docs.values():
            df.update(set(d))
        hits = []
        for e in entries:
            tf = Counter(docs[e.product])
            score, matched = 0.0, []
            for t in set(q):
                if tf[t] == 0:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(docs[e.product]) / avg))
                matched.append(t)
            if e.product in boost:
                score += 2.5
            if score > 0:
                hits.append(Hit(e, round(score, 3), sorted(matched)))
        hits.sort(key=lambda h: (-h.score, h.entry.product))
        return [h for h in hits if h.entry.visible][:k], [h for h in hits if not h.entry.visible][:3]

    def glossary_text(self) -> str:
        return "\n".join(
            f"- {g['term']} ({', '.join(g.get('also', []))}): {g['means']} -> {', '.join(g.get('products', []))}"
            for g in self.glossary
        )
