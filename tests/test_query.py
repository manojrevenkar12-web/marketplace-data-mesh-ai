"""Queries are checked before they run: grants, row filters, read-only SQL, partition filters and cost."""

from __future__ import annotations

import pytest

from mesh.query import QueryRejected


def last(platform, product: str) -> str:  # type: ignore[no-untyped-def]
    return platform.lake.partitions(product)[-1]


def test_hidden_columns_are_refused_and_stars_expand_to_readable_columns(platform):
    dt = last(platform, "leads.leads")
    plan = platform.engine.plan(f"SELECT buyer_email FROM leads.leads WHERE dt = DATE '{dt}'", "analysts-marketplace")
    assert not plan.ok and plan.denied[0].column == "buyer_email" and "pii" in plan.denied[0].reason
    cte = platform.engine.plan(
        f"WITH x AS (SELECT contact_email FROM dealers.dealers WHERE dt = DATE '{dt}') SELECT * FROM x", "analysts-marketplace"
    )
    assert not cte.ok and cte.denied[0].column == "contact_email"
    r = platform.engine.run(f"SELECT * FROM leads.leads WHERE dt = DATE '{dt}' LIMIT 3", "analysts-marketplace")
    assert "buyer_email" not in r.columns and "buyer_phone" not in r.columns and "channel" in r.columns


def test_row_filters_hold_whatever_the_sql(platform):
    dt = last(platform, "leads.leads")
    sql = f"SELECT country, count(*) AS n FROM leads.leads WHERE dt = DATE '{dt}' AND (country <> 'DE' OR 1 = 1) GROUP BY country"
    rows = platform.engine.run(sql, "dealer-success-de").rows
    assert [r[0] for r in rows] == ["DE"]
    everyone = platform.engine.run(sql, "team-leads").rows
    assert len(everyone) > 1


@pytest.mark.parametrize(
    ("sql", "problem"),
    [
        ("DROP TABLE leads.leads", "only SELECT"),
        ("INSERT INTO leads.leads SELECT * FROM leads.leads", "only SELECT"),
        ("SELECT * FROM read_parquet('/etc/passwd')", "table functions"),
        ("SELECT 1; SELECT 2", "exactly one statement"),
        ("SELECT * FROM information_schema.tables", "unknown table"),
        ("SELEC country FROM leads.leads", "does not parse"),
    ],
)
def test_only_single_read_only_queries_over_catalog_tables_run(platform, sql, problem):
    plan = platform.engine.plan(sql, "team-leads")
    assert not plan.ok and any(problem in p for p in plan.problems)
    with pytest.raises(QueryRejected):
        platform.engine.run(sql, "team-leads")


def test_partition_pruning_cost_estimate_and_required_partition_filter(platform):
    parts = platform.lake.partitions("listings.listing_events")
    assert len(parts) > 31
    unfiltered = platform.engine.plan("SELECT count(*) FROM listings.listing_events", "analysts-marketplace")
    assert not unfiltered.ok and any("add a filter on dt" in p for p in unfiltered.problems)
    week = platform.engine.plan(
        f"SELECT make, avg(price_eur) FROM listings.listing_events WHERE dt BETWEEN DATE '{parts[-7]}' AND DATE '{parts[-1]}' GROUP BY make",
        "analysts-marketplace",
    )
    day = platform.engine.plan(
        f"SELECT make, avg(price_eur) FROM listings.listing_events WHERE dt = DATE '{parts[-1]}' GROUP BY make",
        "analysts-marketplace",
    )
    assert week.ok and week.partitions["listings.listing_events"][0] == 7 and day.partitions["listings.listing_events"][0] == 1
    assert 0 < day.bytes_scanned < week.bytes_scanned
    wide = platform.engine.plan(f"SELECT * FROM listings.listing_events WHERE dt = DATE '{parts[-1]}'", "analysts-marketplace")
    assert wide.bytes_scanned > day.bytes_scanned  # more columns, more bytes


def test_cost_limit_refuses_large_scans(platform, monkeypatch):
    from mesh import config

    settings = config.platform()
    monkeypatch.setitem(settings["query"]["bytes_limit"], "assistant", 1000)
    dt = last(platform, "leads.leads")
    plan = platform.engine.plan(f"SELECT * FROM leads.leads WHERE dt = DATE '{dt}'", "team-leads", "assistant")
    assert not plan.ok and any("over the" in p and "limit" in p for p in plan.problems)
