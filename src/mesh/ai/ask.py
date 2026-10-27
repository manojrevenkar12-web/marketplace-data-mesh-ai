"""Questions in plain language, answered with SQL the person may run, and the rows it returns.

The model sees the question, the glossary and the datasets this person can read (visible columns only, with
descriptions screened for instructions). It drafts one Athena query. The platform, not the model, decides
whether it runs: the query engine checks it against the person's own Lake Formation grants and the
assistant's cost limit, one repair round fixes what the model can fix (a missing ``dt`` filter, a typo), and
denials are never "repaired" into probing. Rows go to the person; they are never sent to a model.

When the data exists but the person cannot read it, the answer says which product holds it and who owns it,
so the next step is an access request, not a dead end.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from mesh.ai.catalog import Catalog, Hit
from mesh.llm.base import Block, Message, ModelRequest, ModelResponse
from mesh.llm.prompts import load
from mesh.llm.router import Budget, ModelRouter, Unavailable
from mesh.llm.schemas import RECORD_QUERY, QueryDraft
from mesh.query import Plan, QueryEngine
from mesh.state import State

Status = Literal["answered", "denied", "not_answerable", "rejected", "unavailable"]
FIXABLE = ("does not parse", "has no column", "unknown table", "add a filter on", "would scan", "does not resolve", "send exactly one")


@dataclass
class Answer:
    question: str
    user: str
    principal: str
    status: Status
    message: str
    sql: str = ""
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    plan: dict[str, Any] = field(default_factory=dict)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    restricted: list[dict[str, Any]] = field(default_factory=list)
    flagged_metadata: list[str] = field(default_factory=list)
    model: str | None = None
    cost_usd: float = 0.0
    repairs: int = 0
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rows"] = [[_json(v) for v in r] for r in self.rows]
        return d


def _json(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v) if v != v.to_integral_value() else int(v)
    return v


def _parse(resp: ModelResponse) -> QueryDraft:
    return QueryDraft.model_validate(resp.tool_input("record_query"))


class Asker:
    def __init__(self, catalog: Catalog, engine: QueryEngine, router: ModelRouter, state: State | None = None, max_datasets: int = 4):
        self.catalog = catalog
        self.engine = engine
        self.router = router
        self.state = state
        self.max_datasets = max_datasets

    async def ask(self, user: str, question: str) -> Answer:
        t0 = time.perf_counter()
        principal = self.catalog.policy.principal_of(user)
        hits, restricted_hits = self.catalog.search(question, principal, k=self.max_datasets)
        restricted = [{"product": h.entry.product, "owner": h.entry.owner, "owner_contact": h.entry.owner_contact} for h in restricted_hits]
        ans = Answer(question, user, principal, "not_answerable", "", restricted=restricted,
                     flagged_metadata=sorted({f for h in hits for f in h.entry.flagged_text}))
        if not hits:
            ans.status = "denied" if restricted else "not_answerable"
            ans.message = self._no_data_message(restricted)
            return self._done(ans, t0)
        ans.datasets = [{"product": h.entry.product, "owner": h.entry.owner, "latest_dt": h.entry.latest_dt, "freshness": h.entry.freshness,
                         "quality": h.entry.quality} for h in hits]
        latest = max((h.entry.latest_dt for h in hits if h.entry.latest_dt), default=None)
        prompt = load("ask_sql")
        user_text = prompt.render_user(
            question=question,
            latest_dt=latest or "no data yet",
            glossary=self.catalog.glossary_text(),
            datasets=json.dumps([h.entry.as_prompt() for h in hits], ensure_ascii=False, indent=1),
        )
        messages = [Message("user", [Block.t(user_text)])]
        budget = Budget(limit_usd=float(self.router.routing.get("max_cost_usd_per_request", 0.2)))
        draft: QueryDraft | None = None
        plan: Plan | None = None
        for round_ in range(2):
            req = ModelRequest(task="ask_sql", system=prompt.system, messages=messages, tools=[RECORD_QUERY], tool_choice="record_query",
                               max_tokens=1500, prompt_id=prompt.id, prompt_version=prompt.version, data_scope="internal")
            try:
                routed = await self.router.call(req, _parse, budget)
            except Unavailable as e:
                ans.status = "unavailable"
                ans.message = ("The assistant is not available right now. These datasets match your question; you can query them "
                               "directly: " + ", ".join(h.entry.product for h in hits))
                ans.explanation = str(e)
                return self._done(ans, t0, budget)
            draft = routed.value
            ans.model = routed.model.key
            if not draft.answerable or not draft.sql.strip():
                ans.status = "denied" if restricted else "not_answerable"
                ans.explanation = draft.explanation
                partly = [h.entry for h in hits if h.entry.hidden_columns]
                hint = ""
                if partly and not restricted:
                    e = partly[0]
                    hint = (f" {e.product} has {e.hidden_columns} column(s) your grants do not include; if you need them, request access "
                            f"from {e.owner} ({e.owner_contact}).")
                ans.message = (draft.explanation or "The datasets you can read do not answer this.") + hint + (
                    " " + self._no_data_message(restricted) if restricted else "")
                return self._done(ans, t0, budget)
            plan = self.engine.plan(draft.sql, principal, "assistant")
            if plan.ok:
                break
            if plan.denied or not any(any(f in p for f in FIXABLE) for p in plan.problems) or round_ == 1:
                break
            ans.repairs += 1
            messages = [*messages, Message("assistant", routed.response.tool_uses()), Message("user", [
                Block("tool_result", tool_use_id=routed.response.tool_uses()[0].tool_use_id,
                      content={"rejected": plan.problems}, is_error=True),
                Block.t("The platform rejected this query: " + "; ".join(plan.problems) + ". Call record_query again with a corrected query."),
            ])]
        assert draft is not None and plan is not None
        ans.sql, ans.explanation, ans.assumptions = draft.sql, draft.explanation, draft.assumptions
        ans.plan = plan.summary()
        if not plan.ok:
            if plan.denied:
                # the model was never shown these columns or tables: it guessed, or obeyed text in the metadata
                ans.status = "rejected"
                ans.message = ("The drafted query asked for data your grants do not include, so it was not run: "
                               + "; ".join(d.reason for d in plan.denied))
            else:
                ans.status = "rejected"
                ans.message = "The drafted query could not run: " + "; ".join(plan.problems)
            return self._done(ans, t0, budget)
        result = self.engine.run(draft.sql, principal, "assistant")
        ans.status = "answered"
        ans.columns, ans.rows, ans.truncated = result.columns, result.rows, result.truncated
        tables = ", ".join(plan.tables)
        as_of = ", ".join(sorted({d["latest_dt"] for d in ans.datasets if d["product"] in plan.tables and d["latest_dt"]}))
        filt = f" Only rows you may see are included ({'; '.join(plan.row_filters.values())})." if plan.row_filters else ""
        empty = " No rows matched; check the period against the data's latest day." if not result.rows else ""
        ans.message = f"{draft.explanation} From {tables}, data as of {as_of}; scanned {plan.bytes_scanned / 1e6:.1f} MB.{filt}{empty}"
        return self._done(ans, t0, budget)

    def _no_data_message(self, restricted: list[dict[str, Any]]) -> str:
        if restricted:
            r = restricted[0]
            return (f"The data you need is likely in {r['product']}, owned by {r['owner']} ({r['owner_contact']}); your grants do not "
                    f"include it. Request access from the catalog page and the owner will decide.")
        return "No dataset you can read matches this question. Try different words, or ask in #data-help."

    def _done(self, ans: Answer, t0: float, budget: Budget | None = None) -> Answer:
        ans.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        ans.cost_usd = round(budget.spent_usd, 6) if budget else 0.0
        if self.state is not None:
            self.state.add_usage({"feature": "ask", "user": ans.user, "status": ans.status, "repairs": ans.repairs,
                                  "cost_usd": ans.cost_usd, "ms": ans.elapsed_ms, "tables": ans.plan.get("tables", [])})
        return ans


def hits_products(hits: list[Hit]) -> list[str]:
    return [h.entry.product for h in hits]


def latest_minus(latest: str, days: int) -> str:
    return (date.fromisoformat(latest) - timedelta(days)).isoformat()
