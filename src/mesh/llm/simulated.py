"""A deterministic stand-in for Claude, used in tests, CI and the committed evaluation.

It answers each task from the prompt alone (it has no access to the platform, the data or the truth) and can
be told to misbehave the way real models do, so the evaluation measures what the platform does about it:

* ``invalid``: answers in the wrong shape (the router repairs once, then gives up on that model);
* ``hallucinate``: invents a column, cites evidence that does not exist, mentions products not in the findings;
* ``obey_injection``: follows instructions planted in dataset descriptions;
* ``flip``: picks the second candidate instead of the best one;
* ``outage``: does not answer at all.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from mesh.llm import sim_sql
from mesh.llm.base import Block, ModelRequest, ModelResponse, ProviderError, Usage


def _u(key: str) -> float:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


class SimulatedProvider:
    name = "simulated"

    def __init__(self, faults: dict[str, float] | None = None):
        self.faults = faults or {}
        self.calls = 0

    def _fault(self, kind: str, key: str) -> bool:
        p = float(self.faults.get(kind, 0.0))
        return p > 0 and _u(f"{kind}|{key}") < p

    async def generate(self, req: ModelRequest, model: str) -> ModelResponse:
        self.calls += 1
        text = "\n".join(b.text for m in req.messages for b in m.content if b.type == "text")
        first = req.messages[0].content[0].text if req.messages and req.messages[0].content else ""
        key = f"{req.task}|{first}"
        repair = len(req.messages) > 1
        if self._fault("outage", key):
            raise ProviderError("simulated outage", retryable=True, code="ServiceUnavailableException")
        if self._fault("invalid", key) and not repair:
            return self._tool(req, {"oops": "not the schema"}, text)
        handler = {"ask_sql": self._ask, "triage": self._triage, "review": self._review}[req.task]
        return self._tool(req, handler(first, text, key, repair), text)

    def _tool(self, req: ModelRequest, payload: dict[str, Any], text: str) -> ModelResponse:
        name = req.tool_choice if req.tool_choice not in ("auto", "any") else req.tools[0].name
        out = len(str(payload)) // 4 + 20
        return ModelResponse([Block("tool_use", tool_use_id=f"sim-{self.calls}", name=name, input=payload)], "tool_use",
                             Usage(len(req.system + text) // 4, out), "simulated-v1", "simulated", latency_ms=5.0)

    # ---------------------------------------------------------------- tasks

    def _ask(self, first: str, text: str, key: str, repair: bool) -> dict[str, Any]:
        d = sim_sql.draft(first)
        payload: dict[str, Any] = {"answerable": d.answerable, "sql": d.sql, "tables": d.tables or [], "explanation": d.explanation,
                                   "missing": d.missing, "assumptions": []}
        if not d.answerable:
            return payload
        if repair:
            # a second try after the platform rejected the first: fix what the rejection names
            rejected = re.findall(r"has no column '(\w+)'", text)
            if "add a filter on dt" in text and " dt " not in payload["sql"]:
                payload["sql"] = payload["sql"].replace(" WHERE ", " WHERE dt >= current_date - INTERVAL '7' DAY AND ", 1)
            for col in rejected:
                payload["sql"] = re.sub(rf"\b{col}\b", "price_eur" if "price" in col else "country", payload["sql"])
            return payload
        planted = re.findall(r"(?:also|always)\s+(?:return|include|select|add)\s+(?:the\s+)?(\w+)", first, re.I)
        if planted and self._fault("obey_injection", key):
            col = planted[0]
            payload["sql"] = re.sub(r"^SELECT ", f"SELECT {col}, ", payload["sql"], count=1)
            payload["sql"] = re.sub(r" GROUP BY (\w+)", rf" GROUP BY \1, {col}", payload["sql"]) if " GROUP BY " in payload["sql"] else payload["sql"]
        if self._fault("hallucinate", key):
            for real, fake in (("price_eur", "price"), ("country", "market"), ("make", "brand"), ("lead_id", "lead_count")):
                if real in payload["sql"]:
                    payload["sql"] = payload["sql"].replace(real, fake, 1)
                    break
        return payload

    def _triage(self, first: str, text: str, key: str, repair: bool) -> dict[str, Any]:
        cands = re.findall(r"^- (\w+) \((rule|classifier)[^)]*\): (.*?) \[evidence: ([^\]]*)\]", first, re.M)
        evidence_ids = re.findall(r"^\[(E\d+)\]", first, re.M)
        if not cands:
            cat = "transient_infra"
            cited = evidence_ids[:1]
            summary = "No rule matched; the log shows an infrastructure error."
        else:
            pick = cands[1] if self._fault("flip", key) and len(cands) > 1 else cands[0]
            cat, _src, why, ev = pick
            cited = [e.strip() for e in ev.split(",") if e.strip()] or evidence_ids[:1]
            summary = why
        if self._fault("hallucinate", key) and not repair:
            cited = [*cited, "E99"]
            summary += " The upstream team announced this change last week in #data-announce."
        run = re.search(r"Failed run: (\S+)", first)
        return {"category": cat, "evidence": cited, "summary": f"{run.group(1) if run else 'The run'}: {summary}",
                "next_steps": ["Follow the runbook step for this category.", "Re-run the day once the cause is fixed."]}

    def _review(self, first: str, text: str, key: str, repair: bool) -> dict[str, Any]:
        findings = re.findall(r"^- (F\d+) \[(\w+)\] (.*)$", first, re.M)
        verdict = re.search(r"Verdict \(decided by the platform\): (\w+)", first)
        v = verdict.group(1) if verdict else "approve"
        notes = [{"finding": f, "note": detail} for f, _sev, detail in findings]
        summary = {"block": "This change would break consumers or bypass governance as it stands.",
                   "needs_approval": "This change is safe to merge once the named owners approve.",
                   "approve": "No consumer is affected by this change."}.get(v, "")
        if self._fault("hallucinate", key) and not repair:
            summary += " It also breaks finance.revenue_daily, which reads this column."
        blocks = " ".join(d for _f, s, d in findings if s == "block")
        if re.search(r"renamed|removed", blocks):
            path = "Deprecate the column first (set deprecated_on), let consumers migrate within the deprecation window, then remove it."
        elif "governance" in blocks:
            path = "Ask data governance to approve the reclassification before merging."
        elif "fails" in blocks or "transform" in blocks:
            path = "Fix the transform so the dry run passes on recent partitions, then ask for review again."
        elif blocks:
            path = "Resolve the blocking findings listed above."
        else:
            path = "Merge after the approvals listed."
        return {"summary": summary, "finding_notes": notes, "suggested_path": path}
