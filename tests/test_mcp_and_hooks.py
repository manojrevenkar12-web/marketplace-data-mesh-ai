"""The platform as MCP tools for coding assistants, and the Claude Code hook that checks contract edits."""

from __future__ import annotations

import json

from mcp import Client

from mesh import hooks
from mesh.mcp_server import build_server


def payload(result):  # type: ignore[no-untyped-def]
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


async def test_tools_act_as_the_engineer_and_respect_their_grants(platform):
    async with Client(build_server(platform, "anna.analyst")) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert {
            "search_datasets",
            "describe_dataset",
            "run_query",
            "ask",
            "check_contract",
            "review_change",
            "diagnose_run",
            "lineage",
        } <= names
        found = payload(await c.call_tool("search_datasets", {"query": "Händler Paket"}))
        assert found["datasets"][0]["product"] == "dealers.dealers"
        desc = payload(await c.call_tool("describe_dataset", {"product": "dealers.dealers"}))
        assert "contact_email" not in {col["name"] for col in desc["columns"]} and desc["hidden_columns"] == 3
        dt = platform.lake.partitions("leads.leads")[-1]
        denied = payload(await c.call_tool("run_query", {"sql": f"SELECT buyer_email FROM leads.leads WHERE dt = DATE '{dt}'"}))
        assert denied["ok"] is False and denied["plan"]["denied"][0]["column"] == "buyer_email"
        rows = payload(
            await c.call_tool(
                "run_query", {"sql": f"SELECT channel, count(*) AS n FROM leads.leads WHERE dt = DATE '{dt}' GROUP BY channel"}
            )
        )
        assert rows["ok"] and {r[0] for r in rows["rows"]} <= {"email", "call", "chat"}
    async with Client(build_server(platform, "max.partner")) as c:
        found = payload(await c.call_tool("search_datasets", {"query": "dealer tier"}))
        assert "dealers.dealers" in {r["product"] for r in found["restricted"]}


async def test_contract_and_change_tools(platform):
    async with Client(build_server(platform, "lukas.leads")) as c:
        text = platform.registry.paths["leads.leads"].read_text()
        ok = payload(await c.call_tool("check_contract", {"contract_yaml": text}))
        assert ok["ok"] and ok["problems"] == []
        bad = payload(await c.call_tool("check_contract", {"contract_yaml": text.replace("type: timestamp", "type: datetime")}))
        assert not bad["ok"] and bad["problems"][0]["rule"] == "type"
        review = payload(
            await c.call_tool(
                "review_change",
                {
                    "files": [
                        {"path": "products/leads/leads.yaml", "new": text.replace("name: dealer_id", "name: seller_dealer_id")}
                    ]
                },
            )
        )
        assert review["verdict"] == "block" and "leads.dealer_lead_performance" in review["comment_markdown"]
        failed = payload(await c.call_tool("failed_runs", {}))
        if failed:
            d = payload(await c.call_tool("diagnose_run", {"run_id": failed[0]["id"]}))
            assert d["route_to"] and d["category"]
        lin = payload(await c.call_tool("lineage", {"product": "listings.listing_events", "column": "mileage_km"}))
        assert lin["column_readers"] == ["pricing.price_ratings"]


def test_the_edit_hook_blocks_a_breaking_contract_edit(platform, tmp_path, monkeypatch):
    from mesh import config

    real_config = config.config_dir()
    products = tmp_path / "products"
    for src in config.products_dir().glob("*/*"):
        dst = products / src.parent.name / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text())
    target = products / "listings" / "listing_events.yaml"
    old = target.read_text()
    target.write_text(old.replace("name: mileage_km", "name: mileage"))
    monkeypatch.setenv("MESH_PRODUCTS_DIR", str(products))
    monkeypatch.setenv("MESH_ROOT", str(tmp_path))
    monkeypatch.setattr(hooks, "_old_text", lambda path: old if path.name == "listing_events.yaml" else None)
    (tmp_path / "config").symlink_to(real_config)
    config.load.cache_clear()
    code, message = hooks.check({"tool_name": "Edit", "tool_input": {"file_path": str(target)}})
    assert code == 2 and "renamed" in message and "pricing.price_ratings" in message
    target.write_text(old.replace("Every change to a car listing", "Every change to a listed car"))
    assert hooks.check({"tool_name": "Edit", "tool_input": {"file_path": str(target)}}) == (0, "")
    assert hooks.check({"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "README.md")}}) == (0, "")
    config.load.cache_clear()
