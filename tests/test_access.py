"""Lake Formation tag-based access, evaluated the way Lake Formation evaluates it."""

from __future__ import annotations

from datetime import date, timedelta

from mesh.access import AccessPolicy, Grant
from mesh.contracts import Registry


def test_columns_follow_domain_and_sensitivity_tags():
    reg, pol = Registry.load(), AccessPolicy()
    leads = reg["leads.leads"]
    analyst = pol.table_access("analysts-marketplace", leads)
    assert "buyer_email" in analyst.hidden and "channel" in analyst.columns and analyst.columns[-1] == "dt"
    owner = pol.table_access("team-leads", leads)
    assert owner.hidden == () and owner.row_filter is None
    partner = pol.table_access("partner-reporting", reg["dealers.dealers"])
    assert not partner.visible and partner.columns == ()
    listings_partner = pol.table_access("partner-reporting", reg["listings.listing_events"])
    assert set(listings_partner.hidden) == {"event_id", "listing_id", "dealer_id", "event_ts"}


def test_row_filters_apply_only_when_every_matching_grant_is_filtered():
    reg, pol = Registry.load(), AccessPolicy()
    success = pol.table_access("dealer-success-de", reg["leads.leads"])
    assert success.row_filter == "(country = 'DE')" and "buyer_email" in success.columns
    assert (
        pol.table_access("dealer-success-de", reg["listings.listing_events"]).row_filter is None
    )  # unfiltered grant on listings
    pol.add_grant("dealer-success-de", Grant(None, ("public",)))  # an extra unfiltered grant lifts the filter
    assert pol.table_access("dealer-success-de", reg["leads.leads"]).row_filter is None


def test_access_request_grants_expire():
    reg, pol = Registry.load(), AccessPolicy()
    pol.add_grant(
        "partner-reporting",
        Grant(None, ("public",), products=("dealers.dealers",), source="request:7", expires=date(2026, 9, 30)),
    )
    assert pol.table_access("partner-reporting", reg["dealers.dealers"], on=date(2026, 9, 1)).columns == ("country", "dt")
    assert not pol.table_access("partner-reporting", reg["dealers.dealers"], on=date(2026, 9, 30) + timedelta(1)).visible
    assert pol.principal_of("anna.analyst") == "analysts-marketplace"
