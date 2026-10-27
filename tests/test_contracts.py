"""Contracts: the repository's products pass the platform rules, and the rules catch what they are for."""

from __future__ import annotations

from datetime import date

import pytest

from mesh.contracts import Registry, diff, lint, lint_all, parse
from mesh.lineage import column_lineage, readers_of, source_columns


@pytest.fixture(scope="module")
def reg() -> Registry:
    return Registry.load()


def text(reg: Registry, product: str) -> str:
    return reg.paths[product].read_text()


def test_the_repository_contracts_pass_and_order_inputs_first(reg):
    assert lint_all(reg) == []
    order = reg.order()
    assert order.index("listings.listing_events") < order.index("pricing.price_ratings")
    assert order.index("dealers.dealers") < order.index("leads.dealer_lead_performance")
    assert reg.consumers("listings.listing_events") == ["leads.dealer_lead_performance", "listings.active_listings_daily", "pricing.price_ratings"]


@pytest.mark.parametrize(
    ("product", "old", "new", "rule"),
    [
        ("leads.leads", "type: timestamp", "type: datetime", "type"),
        ("leads.leads", "freshness_hours: 30", "freshness_hours: 40", None),
        ("leads.dealer_lead_performance", "freshness_hours: 34", "freshness_hours: 20", "sla"),
        ("leads.leads", "{check: not_null, column: lead_id", "{check: not_null, column: lead_key", "quality"),
        ("pricing.price_ratings", "inputs: [listings.listing_events]", "inputs: [listings.active_listings_daily]", "transform"),
    ],
)
def test_lint_catches_contract_mistakes(reg, product, old, new, rule):
    c = parse(text(reg, product).replace(old, new, 1))
    problems = lint(c, reg.with_contract(c))
    if rule is None:
        assert not [p for p in problems if p.severity == "error"]
    else:
        assert rule in {p.rule for p in problems if p.severity == "error"}


def test_a_derived_column_cannot_be_classified_below_its_source(reg):
    dealers = parse(text(reg, "dealers.dealers").replace('classification: internal, description: "Subscription', 'classification: confidential, description: "Subscription'))
    r2 = reg.with_contract(dealers)
    problems = lint(r2["leads.dealer_lead_performance"], r2)
    assert any(p.rule == "classification" and "'tier'" in p.message for p in problems)


def test_transform_lineage_resolves_columns_through_ctes_and_aliases(reg):
    sql = reg.sql["pricing.price_ratings"].replace("{{ dt }}", "2026-09-01")
    read = {c for p, c in source_columns(sql, reg) if p == "listings.listing_events"}
    assert {"mileage_km", "first_registration_year", "price_eur", "make", "model"} <= read
    assert "dealer_id" not in read
    assert readers_of("listings.listing_events", "mileage_km", reg) == ["pricing.price_ratings"]
    assert column_lineage("leads.dealer_lead_performance", reg)["tier"] == {("dealers.dealers", "tier")}


def test_diff_classifies_changes(reg):
    old = reg["listings.listing_events"]
    renamed = parse(text(reg, "listings.listing_events").replace("name: mileage_km", "name: mileage"))
    assert [(c.kind, c.old, c.new) for c in diff(old, renamed) if c.kind == "column_renamed"] == [("column_renamed", "mileage_km", "mileage")]
    widened = parse(text(reg, "listings.listing_events").replace("name: mileage_km, type: int", "name: mileage_km, type: bigint"))
    assert [c.kind for c in diff(old, widened)] == ["type_widened"]
    lowered = parse(text(reg, "leads.leads").replace("name: buyer_email, type: string, required: false, classification: pii",
                                                         "name: buyer_email, type: string, required: false, classification: internal"))
    assert "classification_lowered" in {c.kind for c in diff(reg["leads.leads"], lowered)}
    loosened = parse(text(reg, "leads.leads").replace("max: 0.6", "max: 0.9"))
    assert "quality_loosened" in {c.kind for c in diff(reg["leads.leads"], loosened)}


def test_deprecated_on_parses_as_a_date(reg):
    c = parse(text(reg, "dealers.dealers").replace("description: \"VAT identification number\"}", "description: \"VAT identification number\", deprecated_on: 2026-08-01}"))
    assert c.column("vat_id").deprecated_on == date(2026, 8, 1)  # type: ignore[union-attr]
