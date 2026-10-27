"""The platform as tools for AI coding assistants (Claude Code, Cursor, Codex) over the Model Context Protocol.

A data engineer's assistant can search the catalog, read a dataset's contract, lineage and recent runs, run a
query, check a contract while editing it, see what a change would break, and ask why last night's run failed.
Every tool acts as the engineer (``MESH_USER``, or the user of the OAuth token in the hosted server), so the
assistant can never read more than its user may: queries go through the same Lake Formation checks and the
assistant's lower cost limit.

Run locally with ``mesh mcp`` (stdio; registered for Claude Code in ``.mcp.json``) or hosted with
``mesh mcp --http``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from mesh import config
from mesh.ai.review import FileChange
from mesh.contracts import lint, parse
from mesh.lineage import downstream, readers_of, upstream
from mesh.platform import Platform
from mesh.query import QueryRejected


def build_server(platform: Platform, user: str) -> MCPServer:
    principal = platform.policy.principal_of(user)
    server = MCPServer(
        name="mesh",
        instructions=(
            "Tools for the marketplace data platform. Datasets are named <domain>.<product> and partitioned by dt. "
            "Use search_datasets before writing SQL, check_contract after editing products/**/*.yaml, and review_change "
            "before proposing a contract or transform change. Results respect the user's access grants."
        ),
    )

    def usage(tool: str, **extra: Any) -> None:
        platform.state.add_usage({"feature": f"mcp:{tool}", "user": user, **extra})

    @server.tool(
        description="Search the data catalog in plain words (English or German). Returns the datasets you may read, "
        "and the names and owners of matching datasets you may not."
    )
    def search_datasets(query: str, limit: int = 5) -> dict[str, Any]:
        hits, restricted = platform.catalog.search(query, principal, k=limit)
        usage("search_datasets", hits=len(hits))
        return {
            "datasets": [
                {
                    "product": h.entry.product,
                    "description": h.entry.description,
                    "owner": h.entry.owner,
                    "latest_dt": h.entry.latest_dt,
                    "freshness": h.entry.freshness,
                    "quality": h.entry.quality,
                    "score": h.score,
                }
                for h in hits
            ],
            "restricted": [
                {"product": h.entry.product, "owner": h.entry.owner, "owner_contact": h.entry.owner_contact} for h in restricted
            ],
        }

    @server.tool(
        description="Describe one dataset: the columns you may read, contract, freshness, quality, lineage and recent runs."
    )
    def describe_dataset(product: str) -> dict[str, Any]:
        if product not in platform.registry:
            return {"error": f"unknown dataset {product}"}
        e = platform.catalog.entry(product, principal)
        runs = [asdict(r) for r in platform.state.runs(product)[-5:]] if e.visible else []
        c = platform.registry[product]
        usage("describe_dataset", product=product)
        return {
            **asdict(e),
            "version": c.version,
            "sla_freshness_hours": c.sla.freshness_hours,
            "quality_checks": [q.id for q in c.quality],
            "upstream": upstream(product, platform.registry),
            "downstream": downstream(product, platform.registry),
            "recent_runs": runs,
        }

    @server.tool(
        description="Run one read-only Athena SQL query as you. It is checked against your grants and a cost limit first; "
        "rejected queries come back with the reasons and the estimated scan."
    )
    def run_query(sql: str, max_rows: int = 100) -> dict[str, Any]:
        try:
            r = platform.engine.run(sql, principal, "assistant", max_rows=min(max_rows, 500))
        except QueryRejected as e:
            usage("run_query", ok=False)
            return {"ok": False, "plan": e.plan.summary()}
        usage("run_query", ok=True, bytes=r.plan.bytes_scanned)
        return {
            "ok": True,
            "columns": r.columns,
            "rows": [[_j(v) for v in row] for row in r.rows],
            "truncated": r.truncated,
            "plan": r.plan.summary(),
        }

    @server.tool(
        description="Answer a business question with SQL over the datasets you may read; returns the SQL, the rows and the sources."
    )
    async def ask(question: str) -> dict[str, Any]:
        a = await platform.asker.ask(user, question)
        return a.as_dict()

    @server.tool(
        description="Check a data contract (the YAML text of products/<domain>/<product>.yaml) against the platform rules "
        "and the other contracts. Returns problems; an empty list means it passes."
    )
    def check_contract(contract_yaml: str, transform_sql: str | None = None) -> dict[str, Any]:
        try:
            c = parse(contract_yaml)
        except Exception as e:  # malformed YAML or schema
            return {"ok": False, "problems": [{"rule": "parse", "message": str(e).splitlines()[0]}]}
        reg = platform.registry.with_contract(
            c, transform_sql if transform_sql is not None else platform.registry.sql.get(c.product)
        )
        problems = [asdict(p) for p in lint(c, reg, None, reg.sql.get(c.product))]
        usage("check_contract", product=c.product, problems=len(problems))
        return {"ok": not any(p["severity"] == "error" for p in problems), "problems": problems}

    @server.tool(
        description="Review a proposed change before it is merged: give the repository path and the new text of each changed "
        "contract or transform. Returns the verdict (approve, needs_approval, block), the findings and a review comment."
    )
    async def review_change(files: list[dict[str, str]]) -> dict[str, Any]:
        changes = []
        for f in files:
            path = f["path"]
            old_path = config.repo_root() / path
            changes.append(FileChange(path, old_path.read_text() if old_path.exists() else None, f.get("new")))
        r = await platform.reviewer.review(changes)
        usage("review_change", verdict=r.verdict)
        return {"verdict": r.verdict, "findings": [asdict(x) for x in r.findings], "comment_markdown": r.comment_markdown}

    @server.tool(description="List the failed pipeline runs, newest first (optionally for one product or day).")
    def failed_runs(product: str | None = None, dt: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        runs = [
            r
            for r in platform.state.latest_runs().values()
            if not r.ok and (product is None or r.product == product) and (dt is None or r.dt == dt)
        ]
        runs.sort(key=lambda r: (r.dt, r.product), reverse=True)
        return [
            {"id": r.id, "product": r.product, "dt": r.dt, "status": r.status, "error_type": r.error_type} for r in runs[:limit]
        ]

    @server.tool(
        description="Explain why a pipeline run failed: the cause, the evidence, the team that has to act and the next steps."
    )
    async def diagnose_run(run_id: str) -> dict[str, Any]:
        d = await platform.triage.diagnose(run_id)
        return {
            "category": d.category,
            "route_to": d.route_to,
            "decided_by": d.decided_by,
            "confidence": d.confidence,
            "summary": d.summary,
            "next_steps": d.next_steps,
            "root_run": d.root_run,
            "evidence": [asdict(e) for e in d.evidence if e.id in d.cited],
            "runbook": d.runbook,
        }

    @server.tool(description="Lineage of a dataset, or of one column: what it is computed from and what reads it.")
    def lineage(product: str, column: str | None = None) -> dict[str, Any]:
        if product not in platform.registry:
            return {"error": f"unknown dataset {product}"}
        out: dict[str, Any] = {
            "upstream": upstream(product, platform.registry),
            "downstream": downstream(product, platform.registry),
        }
        if column:
            out["column_readers"] = readers_of(product, column, platform.registry)
        return out

    return server


def _j(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


def main(http: bool = False, port: int = 8765) -> None:  # pragma: no cover - process entry point
    platform = Platform.load()
    server = build_server(platform, os.environ.get("MESH_USER", "anna.analyst"))
    if http:
        server.run(transport="streamable-http", port=port)
    else:
        server.run()


__all__ = ["Path", "build_server", "main"]
