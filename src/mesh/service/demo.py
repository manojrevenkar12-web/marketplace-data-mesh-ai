"""Seed the service database for the console: one API key per user in config/access.yaml (``demo-<user>``),
and a few access requests so the owner's inbox is not empty."""

from __future__ import annotations

from typing import Any

from mesh.access import AccessPolicy
from mesh.service import db
from mesh.service.auth import create_key


async def seed(pool: db.Pool | None = None) -> dict[str, Any]:
    own = pool is None
    pool = pool or await db.open_pool(db.database_url())
    await db.migrate(pool)
    users = AccessPolicy().users
    keys = {u: await create_key(pool, u, key=f"demo-{u}") for u in users}
    requests = [
        (
            "max.partner",
            "partner-reporting",
            "listings.active_listings_daily",
            ["public", "internal"],
            "Monthly stock report for our market-data partner, aggregated by country and make.",
            90,
            "team-listings",
        ),
        (
            "anna.analyst",
            "analysts-marketplace",
            "dealers.dealers",
            ["confidential"],
            "VAT ids to reconcile dealer invoices with finance for the Q3 close.",
            30,
            "team-dealers",
        ),
        (
            "piet.pricing",
            "team-pricing",
            "leads.leads",
            ["pii"],
            "Need buyer contact to survey buyers about price ratings.",
            14,
            "team-leads",
        ),
    ]
    async with db.tx(pool) as conn:
        for user, principal, product, sens, reason, days, owner in requests:
            await conn.execute(
                "INSERT INTO access_requests(user_id, principal, product, sensitivities, reason, days, owner) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (user, principal, product, sens, reason, days, owner),
            )
    if own:
        await pool.close()
    return {"keys": keys, "requests": len(requests)}
