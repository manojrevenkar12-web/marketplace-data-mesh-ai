"""Review of a change to a data product before it merges: what breaks, for whom, and the safe way to do it.

Runs on pull requests that touch ``products/`` (GitHub Actions), in the editor (Claude Code hook and the MCP
tool ``impact_of_change``), and in the console. The platform decides the verdict from the contracts, column
lineage, access grants and a dry run of a changed transform on recent partitions; a model writes the comment.
The comment may only mention products, columns and teams that are in the findings, or the platform's own
wording is used.

Most incidents that hurt consumers are a contract or transform change nobody checked against its readers;
the evaluation counts how many of the simulated ones this review would have stopped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import sqlglot

from mesh import config
from mesh.access import AccessPolicy
from mesh.ai.guard import screen
from mesh.contracts import Change, Contract, Registry, diff, lint, parse, render_sql
from mesh.lake import Lake, connect, query_arrow, register_view
from mesh.lineage import readers_of
from mesh.llm.base import Block, Message, ModelRequest, ModelResponse
from mesh.llm.prompts import load
from mesh.llm.router import Budget, ModelRouter, Unavailable
from mesh.llm.schemas import RECORD_REVIEW, ReviewDraft, Verdict


@dataclass
class FileChange:
    path: str  # repository path, e.g. products/leads/leads.yaml
    old: str | None
    new: str | None


@dataclass
class Finding:
    id: str
    severity: str  # block | needs_approval | info
    kind: str
    product: str
    column: str | None
    detail: str
    consumers: list[str] = field(default_factory=list)
    approvers: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Review:
    verdict: Verdict
    products: list[str]
    findings: list[Finding]
    summary: str
    notes: dict[str, str]
    suggested_path: str
    comment_markdown: str
    model: str | None = None
    model_problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse(resp: ModelResponse) -> ReviewDraft:
    return ReviewDraft.model_validate(resp.tool_input("record_review"))


class Reviewer:
    def __init__(
        self, base: Registry, policy: AccessPolicy, lake: Lake | None, router: ModelRouter | None, today: date | None = None
    ):
        self.base = base
        self.policy = policy
        self.lake = lake
        self.router = router
        self.today = today or date.today()
        self.cfg = config.load("review")

    # ---------------------------------------------------------------- entry points

    async def review(self, changes: list[FileChange], use_model: bool = True) -> Review:
        new_reg, touched, removed = self._apply(changes)
        findings: list[Finding] = []

        def add(severity: str, kind: str, product: str, column: str | None, detail: str, **kw: Any) -> None:
            findings.append(Finding(f"F{len(findings) + 1}", severity, kind, product, column, detail, **kw))

        for product in removed:
            consumers = self.base.consumers(product)
            add(
                "block" if consumers else "needs_approval",
                "product_removed",
                product,
                None,
                f"{product} is deleted" + (f" while {', '.join(consumers)} read it" if consumers else ""),
                consumers=consumers,
                approvers=[self.base[product].owner],
            )
        for product in sorted(touched):
            new = new_reg[product]
            for p in lint(new, new_reg, None, new_reg.sql.get(product)):
                add("block" if p.severity == "error" else "info", f"lint:{p.rule}", product, None, p.message)
            if product not in self.base:
                add("info", "product_added", product, None, f"new {new.kind} product owned by {new.owner}")
                self._check_text(new, add)
                continue
            old = self.base[product]
            for ch in diff(old, new, self.base.sql.get(product), new_reg.sql.get(product)):
                self._judge(ch, old, new, new_reg, add)
            if (
                any(f.kind in ("column_removed", "column_renamed", "type_changed") and f.product == product for f in findings)
                and new.version <= old.version
            ):
                add(
                    "block",
                    "version_not_bumped",
                    product,
                    None,
                    f"a breaking change needs a new major version (still v{new.version})",
                )
            self._check_text(new, add)
        # consumers checked against the changed inputs (a reclassified column, a removed one, a tighter SLA)
        for product in sorted(touched):
            for consumer in new_reg.consumers(product):
                if consumer in touched:
                    continue
                for p in lint(new_reg[consumer], new_reg, None, new_reg.sql.get(consumer)):
                    if p.severity == "error":
                        add(
                            "block",
                            f"downstream:{p.rule}",
                            consumer,
                            None,
                            p.message,
                            consumers=[consumer],
                            approvers=[new_reg[consumer].owner],
                        )
        verdict: Verdict = (
            "block"
            if any(f.severity == "block" for f in findings)
            else ("needs_approval" if any(f.severity == "needs_approval" for f in findings) else "approve")
        )
        review = Review(verdict, sorted(touched | set(removed)), findings, "", {}, "", "")
        if use_model and self.router is not None:
            await self._write(review, changes)
        if not review.summary:
            review.summary = _default_summary(review)
            review.suggested_path = _default_path(review)
        review.comment_markdown = render_markdown(review)
        return review

    def _apply(self, changes: list[FileChange]) -> tuple[Registry, set[str], list[str]]:
        reg = self.base
        touched: set[str] = set()
        removed: list[str] = []
        sql_updates: dict[str, str] = {}
        for ch in changes:
            if ch.path.endswith(".sql") and ch.new is not None:
                sql_updates[Path(ch.path).stem] = ch.new
        for ch in changes:
            if not ch.path.endswith(".yaml"):
                continue
            if ch.new is None and ch.old is not None:
                removed.append(parse(ch.old).product)
                continue
            assert ch.new is not None
            c = parse(ch.new)
            sql = sql_updates.pop(Path(c.transform).stem, None) if c.transform else None
            reg = reg.with_contract(c, sql if sql is not None else self.base.sql.get(c.product))
            touched.add(c.product)
        for stem, sql in sql_updates.items():  # a transform changed without its contract
            product = next((c.product for c in reg if c.transform and Path(c.transform).stem == stem), None)
            if product:
                reg = reg.with_contract(reg[product], sql)
                touched.add(product)
        return reg, touched, removed

    # ---------------------------------------------------------------- rules

    def _judge(self, ch: Change, old: Contract, new: Contract, new_reg: Registry, add: Any) -> None:
        p = ch.product
        readers = readers_of(p, ch.column, self.base) if ch.column else []
        owners = sorted({self.base[r].owner for r in readers})
        window = int(config.platform()["contracts"]["deprecation_days"])
        if ch.kind == "column_removed":
            dep = old.column(ch.column or "")
            ripe = bool(dep and dep.deprecated_on and (self.today - dep.deprecated_on).days >= window)
            if ripe and not readers:
                add("info", ch.kind, p, ch.column, f"{ch.detail} after its deprecation window; nobody reads it")
            else:
                why = (f"read by {', '.join(readers)}" if readers else "") + (
                    "" if ripe else (", " if readers else "") + f"not deprecated for {window} days first"
                )
                add("block", ch.kind, p, ch.column, f"{ch.detail}: {why}", consumers=readers, approvers=owners)
        elif ch.kind == "column_renamed":
            add(
                "block",
                ch.kind,
                p,
                ch.column,
                f"{ch.detail}"
                + (
                    f": {', '.join(readers)} read '{ch.old}'" if readers else ": existing queries and dashboards use the old name"
                ),
                consumers=readers,
                approvers=owners,
                data={"old": ch.old, "new": ch.new},
            )
        elif ch.kind == "type_changed":
            add(
                "block",
                ch.kind,
                p,
                ch.column,
                f"{ch.detail} is not a widening" + (f"; read by {', '.join(readers)}" if readers else ""),
                consumers=readers,
                approvers=owners,
            )
        elif ch.kind == "type_widened":
            add("info", ch.kind, p, ch.column, f"{ch.detail} (widening: consumers keep working)")
        elif ch.kind == "became_optional":
            add(
                "needs_approval" if readers else "info",
                ch.kind,
                p,
                ch.column,
                f"{ch.detail}" + (f"; {', '.join(readers)} may assume it is always set" if readers else ""),
                consumers=readers,
                approvers=owners,
            )
        elif ch.kind == "classification_lowered":
            add(
                "block",
                ch.kind,
                p,
                ch.column,
                f"{ch.detail}: more people would see it; data governance must approve, not a code review",
                approvers=list(self.cfg["governance_approvers"]),
            )
        elif ch.kind == "classification_raised":
            losing = self._losing_access(old, new, ch.column or "")
            add(
                "needs_approval",
                ch.kind,
                p,
                ch.column,
                f"{ch.detail}; loses access: {', '.join(losing) or 'nobody'}",
                approvers=[old.owner],
                data={"losing": losing},
            )
        elif ch.kind in ("quality_removed", "quality_loosened"):
            add("needs_approval", ch.kind, p, ch.column, ch.detail, consumers=self.base.consumers(p), approvers=[old.owner])
        elif ch.kind == "sla_loosened":
            tight = [c for c in self.base.consumers(p) if self.base[c].sla.freshness_hours < new.sla.freshness_hours]
            add(
                "block" if tight else "needs_approval",
                ch.kind,
                p,
                None,
                ch.detail + (f"; {', '.join(tight)} promise fresher data than this input would" if tight else ""),
                consumers=tight,
                approvers=sorted({self.base[c].owner for c in tight}) or [old.owner],
            )
        elif ch.kind == "partition_changed":
            add("block", ch.kind, p, None, ch.detail, consumers=self.base.consumers(p))
        elif ch.kind == "product_deprecated":
            add(
                "needs_approval",
                ch.kind,
                p,
                None,
                ch.detail,
                consumers=self.base.consumers(p),
                approvers=sorted({self.base[c].owner for c in self.base.consumers(p)}),
            )
        elif ch.kind == "transform_changed":
            self._dry_run(p, new_reg, add)
        elif ch.kind in (
            "column_added",
            "became_required",
            "quality_added",
            "sla_tightened",
            "column_deprecated",
            "inputs_changed",
        ):
            add("info", ch.kind, p, ch.column, ch.detail)

    def _losing_access(self, old: Contract, new: Contract, column: str) -> list[str]:
        out = []
        for name, pr in self.policy.principals.items():
            if pr.kind == "service":
                continue
            before = column in self.policy.table_access(name, old).columns
            after = column in self.policy.table_access(name, new).columns
            if before and not after:
                out.append(name)
        return out

    def _check_text(self, c: Contract, add: Any) -> None:
        flagged = screen(c.description).flagged + [f for col in c.columns for f in screen(col.description).flagged]
        if flagged:
            add(
                "block",
                "instructions_in_metadata",
                c.product,
                None,
                f'descriptions contain text aimed at AI assistants: "{flagged[0][:120]}"; descriptions are shown to assistants as data',
            )

    def _dry_run(self, product: str, reg: Registry, add: Any) -> None:
        """Run the new transform on the last few published days and compare with what is published."""
        if self.lake is None:
            add("needs_approval", "transform_changed", product, None, "transform changed; no lake to dry-run against here")
            return
        c = reg[product]
        days = self.lake.partitions(product)[-int(self.cfg["dry_run_days"]) :]
        if not days:
            add("info", "transform_changed", product, None, "transform changed; no published partitions to compare with yet")
            return
        results = []
        for dt in days:
            con = connect()
            for i in c.inputs:
                register_view(con, self.lake, reg[i], until_dt=dt)
            try:
                sql = sqlglot.transpile(render_sql(reg.sql[product], dt), read="athena", write="duckdb")[0]
                new_rows = query_arrow(con, sql).num_rows
            except Exception as e:  # the error is the finding
                add("block", "dry_run_failed", product, None, f"the new transform fails on {dt}: {str(e).splitlines()[0][:220]}")
                return
            old_rows = self.lake.row_count(product, dt)
            results.append((dt, old_rows, new_rows))
        warn = float(self.cfg["dry_run_row_change_warn"])
        changed = [(d, o, n) for d, o, n in results if o and abs(n - o) / o > warn]
        detail = "; ".join(f"{d}: {o} -> {n} rows" for d, o, n in results)
        if changed:
            add(
                "needs_approval",
                "dry_run_rows_changed",
                product,
                None,
                f"the new transform changes row counts by more than {warn:.0%}: {detail}",
                consumers=self.base.consumers(product),
                approvers=sorted({self.base[x].owner for x in self.base.consumers(product)}) or [c.owner],
                data={"days": results},
            )
        else:
            add(
                "info",
                "dry_run_ok",
                product,
                None,
                f"the new transform ran on {len(results)} recent days: {detail}",
                data={"days": results},
            )

    # ---------------------------------------------------------------- comment

    async def _write(self, review: Review, changes: list[FileChange]) -> None:
        assert self.router is not None
        prompt = load("review")
        findings = (
            "\n".join(
                f"- {f.id} [{f.severity}] {f.product}{'.' + f.column if f.column else ''}: {f.detail}" for f in review.findings
            )
            or "(none)"
        )
        change = ", ".join(c.path for c in changes)
        user = prompt.render_user(change=change, verdict=review.verdict, findings=findings)
        req = ModelRequest(
            task="review",
            system=prompt.system,
            messages=[Message("user", [Block.t(user)])],
            tools=[RECORD_REVIEW],
            tool_choice="record_review",
            max_tokens=1200,
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            data_scope="internal",
        )
        try:
            routed = await self.router.call(
                req, _parse, Budget(limit_usd=float(self.router.routing.get("max_cost_usd_per_request", 0.2)))
            )
        except Unavailable as e:
            review.model_problems.append(f"model unavailable: {e}")
            return
        d = routed.value
        review.model = routed.model.key
        allowed = (
            {f.product for f in review.findings} | {c for f in review.findings for c in f.consumers} | set(self.base.contracts)
        )
        text = " ".join([d.summary, d.suggested_path, *(n.note for n in d.finding_notes)])
        named = set(re.findall(r"\b([a-z_]+\.[a-z_]+)\b", text))
        invented = sorted(
            n
            for n in named
            if n not in allowed and n.split(".")[0] in self.policy.tags["domain"] + ["finance", "marketing", "sales"]
        )
        unknown_ids = [n.finding for n in d.finding_notes if n.finding not in {f.id for f in review.findings}]
        if invented or unknown_ids:
            review.model_problems.append(f"comment mentioned {', '.join(invented + unknown_ids)}, which are not in the findings")
            return
        review.summary, review.suggested_path = d.summary, d.suggested_path
        review.notes = {n.finding: n.note for n in d.finding_notes}


def _default_summary(r: Review) -> str:
    blocks = [f for f in r.findings if f.severity == "block"]
    if blocks:
        return f"{len(blocks)} finding(s) block this change: " + "; ".join(f.detail for f in blocks[:3])
    approvals = [f for f in r.findings if f.severity == "needs_approval"]
    if approvals:
        return "Safe to merge after approval by " + ", ".join(
            sorted({a for f in approvals for a in f.approvers}) or ["the owner"]
        )
    return "No consumer is affected."


def _default_path(r: Review) -> str:
    kinds = {f.kind for f in r.findings if f.severity == "block"}
    if kinds & {"column_removed", "column_renamed"}:
        return (
            "Add the new column next to the old one, mark the old one deprecated_on today, tell the consumers, and remove it after "
            f"{config.platform()['contracts']['deprecation_days']} days."
        )
    if "dry_run_failed" in kinds:
        return "Fix the transform so the dry run passes on the recent partitions."
    if "classification_lowered" in kinds:
        return "Ask data governance to approve the reclassification before merging."
    return "Address the blocking findings, or merge once the listed approvers agree."


def render_markdown(r: Review) -> str:
    icon = {"block": "Blocked", "needs_approval": "Needs approval", "approve": "Approved"}[r.verdict]
    lines = [f"### Data product review: {icon}", "", r.summary, ""]
    if r.findings:
        lines += ["| | Finding | Product | Consumers | Approvers |", "| --- | --- | --- | --- | --- |"]
        for f in r.findings:
            note = r.notes.get(f.id, "")
            extra = f" {note}" if note and f.detail not in note and note not in f.detail else ""
            lines.append(
                f"| {f.severity} | {f.detail}{extra} | `{f.product}`"
                f"{'.' + f.column if f.column else ''} | {', '.join(f.consumers) or '-'} | {', '.join(f.approvers) or '-'} |"
            )
        lines.append("")
    if r.suggested_path:
        lines += [f"**Safe path:** {r.suggested_path}", ""]
    lines.append(
        "_Verdict decided by the platform from contracts, lineage, grants and a dry run; wording by the assistant._"
        if r.model
        else "_Verdict and wording from the platform's checks._"
    )
    return "\n".join(lines)
