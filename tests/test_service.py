"""The API over PostgreSQL: who you are decides what you see, access requests go to the owning team and take
effect when approved, and the audit trail cannot be edited.

Needs a database the test may wipe: MESH_TEST_DATABASE_URL=postgresql://mesh:mesh@localhost:5432/mesh_test
"""

from __future__ import annotations

import os

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.db
URL = os.environ.get("MESH_TEST_DATABASE_URL")
if not URL:
    pytest.skip("MESH_TEST_DATABASE_URL not set", allow_module_level=True)


@pytest.fixture(scope="module")
async def app(platform):  # type: ignore[no-untyped-def]
    from mesh.service import db
    from mesh.service.app import create_app
    from mesh.service.demo import seed

    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    pool = await db.open_pool(URL)
    await db.migrate(pool)
    await seed(pool)
    application = create_app(platform, pool)
    async with application.router.lifespan_context(application):
        yield application
    for pr in platform.policy.principals.values():  # leave the shared platform as other tests expect it
        pr.grants = [g for g in pr.grants if not g.source.startswith("request:")]
    await pool.close()


def client(app, user: str | None = None) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    headers = {"authorization": f"Bearer demo-{user}"} if user else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)


async def test_keys_identify_users_and_the_catalog_follows_their_grants(app):
    async with client(app) as c:
        assert (await c.get("/v1/me")).status_code == 401
    async with client(app, "max.partner") as c:
        me = (await c.get("/v1/me")).json()
        assert me["principal"] == "partner-reporting" and me["owns"] == []
        cat = (await c.get("/v1/catalog")).json()["datasets"]
        visible = {d["product"] for d in cat if d["visible"]}
        assert visible == {"listings.listing_events", "listings.active_listings_daily"}
        dealers = next(d for d in cat if d["product"] == "dealers.dealers")
        assert dealers["columns"] == [] and dealers["latest_dt"] is None
        assert (await c.get("/v1/runs", params={"product": "dealers.dealers"})).json() == []
    async with client(app, "lena.listings") as c:
        assert set((await c.get("/v1/me")).json()["owns"]) == {"listings.active_listings_daily", "listings.listing_events"}


async def test_queries_run_under_the_callers_grants_and_are_audited(app, platform):
    dt = platform.lake.partitions("leads.leads")[-1]
    async with client(app, "anna.analyst") as c:
        r = await c.post("/v1/query", json={"sql": f"SELECT buyer_email FROM leads.leads WHERE dt = DATE '{dt}'"})
        assert r.status_code == 422 and r.json()["detail"]["denied"][0]["column"] == "buyer_email"
        ok = await c.post(
            "/v1/query", json={"sql": f"SELECT channel, count(*) AS n FROM leads.leads WHERE dt = DATE '{dt}' GROUP BY channel"}
        )
        assert ok.status_code == 200 and ok.json()["rows"]
        a = (await c.post("/v1/ask", json={"question": "How many leads per channel yesterday?"})).json()
        assert a["status"] == "answered"
    with psycopg.connect(URL, autocommit=True) as conn:
        actions = [r[0] for r in conn.execute("SELECT action FROM audit WHERE user_id = 'anna.analyst' ORDER BY id")]
        assert actions == ["query_rejected", "query", "ask"]
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM audit")


async def test_an_approved_access_request_becomes_a_time_limited_grant(app, platform):
    async with client(app, "max.partner") as partner:
        denied = (await partner.post("/v1/ask", json={"question": "How many dealers per tier?"})).json()
        assert denied["status"] == "denied"
        r = await partner.post(
            "/v1/access-requests",
            json={"product": "dealers.dealers", "reason": "Dealer counts per tier for the partner report.", "days": 30},
        )
        assert r.status_code == 201 and r.json()["owner"] == "team-dealers"
        again = await partner.post(
            "/v1/access-requests",
            json={"product": "dealers.dealers", "reason": "Same request a second time, please.", "days": 30},
        )
        assert again.status_code == 409
        rid = r.json()["id"]
    async with client(app, "lukas.leads") as wrong_owner:
        assert (await wrong_owner.post(f"/v1/access-requests/{rid}/decision", json={"decision": "approve"})).status_code == 403
    async with client(app, "dana.dealers") as owner:
        inbox = (await owner.get("/v1/access-requests")).json()["to_decide"]
        assert any(x["id"] == rid and x["status"] == "pending" for x in inbox)
        d = (
            await owner.post(f"/v1/access-requests/{rid}/decision", json={"decision": "approve", "note": "ok for the report"})
        ).json()
        assert d["status"] == "approved" and d["expires_on"]
        assert (await owner.post(f"/v1/access-requests/{rid}/decision", json={"decision": "deny"})).status_code == 409
    async with client(app, "max.partner") as partner:
        ds = (await partner.get("/v1/datasets/dealers.dealers")).json()
        assert ds["visible"] and {c["name"] for c in ds["columns"]} == {
            "dealer_id",
            "dealer_name",
            "country",
            "city",
            "tier",
            "onboarded_on",
        }
        a = (await partner.post("/v1/ask", json={"question": "How many dealers per tier?"})).json()
        assert a["status"] == "answered" and a["rows"]
    with psycopg.connect(URL, autocommit=True) as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE access_requests SET status = 'denied' WHERE id = %s", (rid,))


async def test_failed_runs_their_diagnosis_and_reviews(app, platform):
    async with client(app, "piet.pricing") as c:
        failed = (await c.get("/v1/runs")).json()
        assert failed and all(r["status"] != "success" for r in failed)
        d = (await c.get(f"/v1/runs/{failed[0]['id']}/diagnosis")).json()
        assert d["category"] and d["route_to"] and d["evidence"]
        log = (await c.get(f"/v1/runs/{failed[0]['id']}")).json()["log"]
        assert any("ERROR" in line for line in log)
        text = platform.registry.paths["listings.listing_events"].read_text()
        r = (
            await c.post(
                "/v1/reviews",
                json={
                    "files": [
                        {
                            "path": "products/listings/listing_events.yaml",
                            "new": text.replace("name: mileage_km", "name: mileage"),
                        }
                    ]
                },
            )
        ).json()
        assert r["verdict"] == "block"
        bad = await c.post("/v1/reviews", json={"files": [{"path": "../etc/passwd", "new": "x"}]})
        assert bad.status_code == 422
        assert (
            await c.post("/v1/feedback", json={"feature": "triage", "ref": failed[0]["id"], "helpful": True})
        ).status_code == 201
        usage = (await c.get("/v1/usage")).json()
        assert usage["feedback"]["triage"]["n"] == 1


async def test_contract_files_and_the_evaluation_with_its_blind_first_runs(app):
    async with client(app, "dana.dealers") as c:
        files = (await c.get("/v1/contracts/leads.dealer_lead_performance")).json()["files"]
        assert [f["path"] for f in files] == [
            "products/leads/dealer_lead_performance.yaml",
            "products/leads/dealer_lead_performance.sql",
        ]
        assert "dealer_id" in files[0]["text"]
        assert (await c.get("/v1/contracts/nope.nope")).status_code == 404
        ev = await c.get("/v1/evaluation")
        if ev.status_code == 404:
            pytest.skip("no evaluation report in this checkout")
        sets = {h["set"]: h for h in ev.json()["heldout"]}
        assert {"questions-1", "changes-1", "incidents-1"} <= set(sets)
        assert all(h["headline"] and h["status"] for h in sets.values())
