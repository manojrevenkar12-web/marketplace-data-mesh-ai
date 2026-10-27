"""The platform API behind the console and the hosted MCP server.

Everything a person does goes through their own principal: the catalog shows what they may read, queries and
the assistant run under their grants, run logs are shown only for products they can read. Access requests go
to the product's owning team, and an approved request becomes a time-limited grant immediately (here and, via
``mesh infra render`` of approved requests, in Lake Formation). Every access decision and every question is
written to an append-only audit table.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from mesh import config
from mesh.access import Grant
from mesh.ai.review import FileChange
from mesh.lineage import downstream, upstream
from mesh.platform import Platform
from mesh.query import QueryRejected
from mesh.service import db
from mesh.service.auth import Caller, caller


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class QueryIn(BaseModel):
    sql: str = Field(min_length=6, max_length=20000)


class ReviewFile(BaseModel):
    path: str = Field(pattern=r"^products/[a-z_]+/[a-z_]+\.(yaml|sql)$")
    new: str | None = Field(default=None, max_length=100000)


class ReviewIn(BaseModel):
    files: list[ReviewFile] = Field(min_length=1, max_length=10)


class AccessRequestIn(BaseModel):
    product: str
    sensitivities: list[Literal["public", "internal", "confidential", "pii"]] = Field(default=["public", "internal"])
    reason: str = Field(min_length=10, max_length=1000)
    days: int = Field(default=90, ge=1, le=365)


class DecisionIn(BaseModel):
    decision: Literal["approve", "deny"]
    note: str | None = Field(default=None, max_length=1000)


class FeedbackIn(BaseModel):
    feature: Literal["ask", "triage", "review"]
    ref: str = Field(max_length=500)
    helpful: bool
    comment: str | None = Field(default=None, max_length=1000)


def _j(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, int | float | str | bool) or v is None:
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


async def audit(app: FastAPI, user: str, action: str, obj: str, detail: dict[str, Any] | None = None) -> None:
    async with db.tx(app.state.pool) as conn:
        await conn.execute(
            "INSERT INTO audit(user_id, action, object, detail) VALUES (%s, %s, %s, %s)",
            (user, action, obj, json.dumps(detail or {}, default=str)),
        )


async def refresh_grants(app: FastAPI) -> None:
    """Approved, unexpired access requests become grants on top of config/access.yaml."""
    platform: Platform = app.state.platform
    async with db.tx(app.state.pool) as conn:
        rows = await (
            await conn.execute(
                "SELECT id, principal, product, sensitivities, expires_on FROM access_requests WHERE status = 'approved' AND expires_on >= %s",
                (platform.today,),
            )
        ).fetchall()
        marker = await (
            await conn.execute("SELECT count(*) AS n, max(decided_at) AS m FROM access_requests WHERE status <> 'pending'")
        ).fetchone()
    for p in platform.policy.principals.values():
        p.grants = [g for g in p.grants if not g.source.startswith("request:")]
    for r in rows:
        platform.policy.add_grant(
            r["principal"],
            Grant(
                None, tuple(r["sensitivities"]), products=(r["product"],), source=f"request:{r['id']}", expires=r["expires_on"]
            ),
        )
    app.state.grants_marker = (marker["n"], marker["m"]) if marker else None


async def current_grants(request: Request) -> None:
    app = request.app
    async with db.tx(app.state.pool) as conn:
        m = await (
            await conn.execute("SELECT count(*) AS n, max(decided_at) AS m FROM access_requests WHERE status <> 'pending'")
        ).fetchone()
    if m and (m["n"], m["m"]) != app.state.grants_marker:
        await refresh_grants(app)


def create_app(platform: Platform | None = None, pool: db.Pool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        own_pool = pool is None
        app.state.platform = platform or Platform.load()
        app.state.pool = pool or await db.open_pool(db.database_url())
        await db.migrate(app.state.pool)
        await refresh_grants(app)
        yield
        if own_pool:
            await app.state.pool.close()

    app = FastAPI(title="Marketplace data platform", version="0.1.0", lifespan=lifespan)
    origins = [o for o in os.environ.get("MESH_CORS_ORIGINS", "").split(",") if o]
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type"]
        )

    def P(request: Request) -> Platform:
        return request.app.state.platform

    def can_read(p: Platform, who: Caller, product: str) -> bool:
        return product in p.registry and p.policy.table_access(who.principal, p.registry[product], p.today).visible

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> dict[str, Any]:
        async with db.tx(request.app.state.pool) as conn:
            await conn.execute("SELECT 1")
        p = P(request)
        return {"status": "ready", "products": len(p.registry.contracts), "today": p.today.isoformat()}

    @app.get("/v1/me")
    async def me(who: Caller = Depends(caller), _: None = Depends(current_grants)) -> dict[str, Any]:
        return asdict(who)

    @app.get("/v1/catalog")
    async def catalog(
        request: Request, q: str = "", who: Caller = Depends(caller), _: None = Depends(current_grants)
    ) -> dict[str, Any]:
        p = P(request)
        if q.strip():
            hits, restricted = p.catalog.search(q, who.principal, k=12)
            entries = [h.entry for h in hits] + [h.entry for h in restricted]
        else:
            entries = p.catalog.entries(who.principal)
        return {"today": p.today.isoformat(), "datasets": [_entry(e) for e in entries]}

    @app.get("/v1/datasets/{product}")
    async def dataset(
        product: str, request: Request, who: Caller = Depends(caller), _: None = Depends(current_grants)
    ) -> dict[str, Any]:
        p = P(request)
        if product not in p.registry:
            raise HTTPException(404, f"no dataset {product}")
        e = p.catalog.entry(product, who.principal)
        c = p.registry[product]
        out = _entry(e)
        out.update(
            {
                "version": c.version,
                "sla_hours": c.sla.freshness_hours,
                "upstream": upstream(product, p.registry),
                "downstream": downstream(product, p.registry),
                "quality_checks": [q.id for q in c.quality],
                "owned": product in who.owns,
            }
        )
        if e.visible:
            runs = [r for r in p.state.runs(product)][-14:]
            out["runs"] = [
                {"id": r.id, "dt": r.dt, "status": r.status, "rows": r.rows, "error_type": r.error_type} for r in reversed(runs)
            ]
            latest = e.latest_dt
            out["quality_results"] = [asdict(q) for q in p.state.quality(product, latest)][-len(c.quality) :] if latest else []
        return out

    @app.get("/v1/contracts/{product}")
    async def contract_files(product: str, request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        """The contract and transform files of a product as they are in the repository, to start a proposed change from."""
        p = P(request)
        if product not in p.registry:
            raise HTTPException(404, f"no dataset {product}")
        domain, name = product.split(".", 1)
        out = []
        for suffix in ("yaml", "sql"):
            rel = f"products/{domain}/{name}.{suffix}"
            path = config.repo_root() / rel
            if path.exists():
                out.append({"path": rel, "text": path.read_text()})
        return {"product": product, "files": out}

    @app.post("/v1/ask")
    async def ask(
        body: AskIn, request: Request, who: Caller = Depends(caller), _: None = Depends(current_grants)
    ) -> dict[str, Any]:
        a = await P(request).asker.ask(who.user, body.question)
        await audit(
            request.app, who.user, "ask", a.status, {"question": body.question, "sql": a.sql, "tables": a.plan.get("tables", [])}
        )
        return a.as_dict()

    @app.post("/v1/query/plan")
    async def plan(
        body: QueryIn, request: Request, who: Caller = Depends(caller), _: None = Depends(current_grants)
    ) -> dict[str, Any]:
        return P(request).engine.plan(body.sql, who.principal, "person").summary()

    @app.post("/v1/query")
    async def query(
        body: QueryIn, request: Request, who: Caller = Depends(caller), _: None = Depends(current_grants)
    ) -> dict[str, Any]:
        p = P(request)
        try:
            r = await asyncio.to_thread(p.engine.run, body.sql, who.principal, "person")
        except QueryRejected as e:
            await audit(request.app, who.user, "query_rejected", ",".join(e.plan.tables) or "-", {"problems": e.plan.problems})
            raise HTTPException(422, e.plan.summary()) from e
        await audit(request.app, who.user, "query", ",".join(r.plan.tables), {"bytes": r.plan.bytes_scanned})
        return {
            "columns": r.columns,
            "rows": [[_j(v) for v in row] for row in r.rows],
            "truncated": r.truncated,
            "plan": r.plan.summary(),
        }

    @app.get("/v1/runs")
    async def runs(
        request: Request,
        status: Literal["failed", "latest"] = "failed",
        product: str | None = None,
        who: Caller = Depends(caller),
    ) -> list[dict[str, Any]]:
        """Failed runs (every attempt that did not publish, with whether the day was fixed later), or the latest run per day."""
        p = P(request)
        latest = p.state.latest_runs()
        source = [r for r in p.state.runs() if not r.ok] if status == "failed" else list(latest.values())
        out = []
        for r in source:
            if (product and r.product != product) or not can_read(p, who, r.product):
                continue
            final = latest.get((r.product, r.dt))
            out.append(
                {
                    "id": r.id,
                    "product": r.product,
                    "dt": r.dt,
                    "status": r.status,
                    "step": r.step,
                    "error_type": r.error_type,
                    "error": (r.error or "")[:300],
                    "owner": p.registry[r.product].owner if r.product in p.registry else "",
                    "fixed": bool(final and final.ok),
                }
            )
        out.sort(key=lambda x: (x["dt"], x["id"]), reverse=True)
        return out[:300]

    @app.get("/v1/runs/{run_id}")
    async def run(run_id: str, request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        p = P(request)
        r = p.state.run(run_id)
        if r is None or not can_read(p, who, r.product):
            raise HTTPException(404, f"no run {run_id}")
        return {**asdict(r), "log": p.state.log(run_id)}

    @app.get("/v1/runs/{run_id}/diagnosis")
    async def diagnosis(run_id: str, request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        p = P(request)
        r = p.state.run(run_id)
        if r is None or not can_read(p, who, r.product):
            raise HTTPException(404, f"no run {run_id}")
        d = await p.triage.diagnose(run_id)
        return d.as_dict()

    @app.post("/v1/reviews")
    async def review(body: ReviewIn, request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        p = P(request)
        changes = []
        for f in body.files:
            old_path = config.repo_root() / f.path
            changes.append(FileChange(f.path, old_path.read_text() if old_path.exists() else None, f.new))
        r = await p.reviewer.review(changes)
        await audit(request.app, who.user, "review", ",".join(r.products) or "-", {"verdict": r.verdict})
        return r.as_dict()

    @app.post("/v1/access-requests", status_code=201)
    async def request_access(body: AccessRequestIn, request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        p = P(request)
        if body.product not in p.registry:
            raise HTTPException(404, f"no dataset {body.product}")
        owner = p.registry[body.product].owner
        async with db.tx(request.app.state.pool) as conn:
            try:
                row = await (
                    await conn.execute(
                        "INSERT INTO access_requests(user_id, principal, product, sensitivities, reason, days, owner) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, status, created_at",
                        (who.user, who.principal, body.product, body.sensitivities, body.reason, body.days, owner),
                    )
                ).fetchone()
            except Exception as e:
                if "access_requests_one_pending" in str(e):
                    raise HTTPException(409, "you already have a pending request for this dataset") from e
                raise
        await audit(
            request.app, who.user, "access_requested", body.product, {"sensitivities": body.sensitivities, "days": body.days}
        )
        assert row is not None
        return {"id": row["id"], "status": row["status"], "owner": owner}

    @app.get("/v1/access-requests")
    async def access_requests(request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        async with db.tx(request.app.state.pool) as conn:
            mine = await (
                await conn.execute("SELECT * FROM access_requests WHERE user_id = %s ORDER BY id DESC", (who.user,))
            ).fetchall()
            to_decide = await (
                await conn.execute(
                    "SELECT * FROM access_requests WHERE owner = %s ORDER BY status = 'pending' DESC, id DESC", (who.principal,)
                )
            ).fetchall()
        return {
            "mine": [{k: _j(v) if k != "sensitivities" else v for k, v in r.items()} for r in mine],
            "to_decide": [{k: _j(v) if k != "sensitivities" else v for k, v in r.items()} for r in to_decide],
        }

    @app.post("/v1/access-requests/{request_id}/decision")
    async def decide(request_id: int, body: DecisionIn, request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        p = P(request)
        async with db.tx(request.app.state.pool) as conn:
            row = await (await conn.execute("SELECT * FROM access_requests WHERE id = %s FOR UPDATE", (request_id,))).fetchone()
            if row is None:
                raise HTTPException(404, "no such request")
            if row["owner"] != who.principal:
                raise HTTPException(403, f"only {row['owner']} decides access to {row['product']}")
            if row["status"] != "pending":
                raise HTTPException(409, f"request already {row['status']}")
            status = "approved" if body.decision == "approve" else "denied"
            expires = p.today + timedelta(int(row["days"])) if status == "approved" else None
            await conn.execute(
                "UPDATE access_requests SET status = %s, decided_by = %s, decided_at = now(), expires_on = %s, note = %s WHERE id = %s",
                (status, who.user, expires, body.note, request_id),
            )
        await audit(
            request.app,
            who.user,
            f"access_{status}",
            row["product"],
            {"request": request_id, "for": row["user_id"], "expires": expires},
        )
        await refresh_grants(request.app)
        return {"id": request_id, "status": status, "expires_on": expires.isoformat() if expires else None}

    @app.post("/v1/feedback", status_code=201)
    async def feedback(body: FeedbackIn, request: Request, who: Caller = Depends(caller)) -> dict[str, str]:
        async with db.tx(request.app.state.pool) as conn:
            await conn.execute(
                "INSERT INTO feedback(user_id, feature, ref, helpful, comment) VALUES (%s,%s,%s,%s,%s)",
                (who.user, body.feature, body.ref, body.helpful, body.comment),
            )
        return {"status": "recorded"}

    @app.get("/v1/usage")
    async def usage(request: Request, who: Caller = Depends(caller)) -> dict[str, Any]:
        p = P(request)
        rows = p.state.usage()
        by_feature = Counter(r.get("feature", "?").split(":")[0] for r in rows)
        ask_status = Counter(r.get("status") for r in rows if r.get("feature") == "ask")
        async with db.tx(request.app.state.pool) as conn:
            fb = await (
                await conn.execute(
                    "SELECT feature, count(*) AS n, avg(helpful::int)::float AS helpful FROM feedback GROUP BY feature"
                )
            ).fetchall()
        return {
            "events": len(rows),
            "by_feature": dict(by_feature),
            "ask_status": dict(ask_status),
            "feedback": {r["feature"]: {"n": r["n"], "helpful": round(r["helpful"] or 0, 3)} for r in fb},
        }

    @app.get("/v1/evaluation")
    async def evaluation(who: Caller = Depends(caller)) -> dict[str, Any]:
        path = config.reports_dir() / "eval" / "report.json"
        if not path.exists():
            raise HTTPException(404, "no evaluation report yet; run mesh eval")
        report = json.loads(path.read_text())
        heldout = []
        for first in sorted((config.reports_dir() / "heldout").glob("*/first_run.json")):
            h = json.loads(first.read_text())
            heldout.append({k: h.get(k) for k in ("set", "kind", "status", "ran_at", "headline")})
        report["heldout"] = heldout
        return report

    @app.get("/v1/glossary")
    async def glossary(who: Caller = Depends(caller)) -> list[dict[str, Any]]:
        return list(config.load("glossary").get("terms", []))

    app.state.started = time.time()
    return app


def _entry(e: Any) -> dict[str, Any]:
    d = asdict(e)
    d.pop("flagged_text", None)
    return d


__all__ = ["create_app", "date"]
