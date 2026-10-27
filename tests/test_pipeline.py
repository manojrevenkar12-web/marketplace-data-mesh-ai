"""The simulated marketplace and the pipelines: every injected incident shows up as failed runs, quarantined
partitions never reach the lake, and partitions are processed under the contract in force on their day."""

from __future__ import annotations

import json

from mesh.contracts import Registry
from mesh.history import Versions
from mesh.state import State


def incidents(world):  # type: ignore[no-untyped-def]
    return [json.loads(x) for x in (world / "truth" / "incidents.jsonl").read_text().splitlines()]


def test_every_first_attempt_failure_comes_from_an_injected_incident(world):
    state = State(world / "state")
    attributed = {r for i in incidents(world) for r in i["affected_runs"]}
    first_failures = [r for r in state.runs() if not r.ok and r.id.endswith(":1")]
    assert first_failures and all(r.id in attributed for r in first_failures)
    assert {i["kind"] for i in incidents(world)} >= {"late_drop", "schema_drift"} or len(incidents(world)) >= 8


def test_incidents_are_fixed_and_re_run(world):
    state = State(world / "state")
    latest = state.latest_runs()
    last_day = max(dt for _, dt in latest)
    broken = [k for k, r in latest.items() if not r.ok and k[1] != last_day]
    assert broken == []


def test_quarantined_partitions_are_not_published_and_failures_are_logged(world, platform):
    state = State(world / "state")
    q = [r for r in state.runs() if r.status == "quality_failed"]
    assert q
    for r in q:
        later_ok = [x for x in state.runs(r.product, r.dt) if x.ok and x.id > r.id]
        if not later_ok:
            assert not platform.lake.has(r.product, r.dt)
        assert any("QualityCheckFailed" in line for line in state.log(r.id))


def test_partitions_use_the_contract_in_force_on_their_day(tmp_path):
    reg = Registry.load()
    v = Versions(State(tmp_path), reg)
    v.bootstrap("2026-07-01")
    text = reg.paths["leads.leads"].read_text().replace("version: 2", "version: 3").replace("name: channel", "name: contact_channel")
    text = text.replace("column: channel", "column: contact_channel")
    v.publish(text, None, "2026-08-01", "team-leads", "rename channel", published="2026-08-01T15:00:00Z")
    assert v.registry_at("2026-07-31")["leads.leads"].column("channel") is not None
    assert v.registry_at("2026-08-01")["leads.leads"].column("contact_channel") is not None
    assert [c.kind for _, cs in v.changes("leads.leads", "2026-07-25", "2026-08-02") for c in cs] == ["column_renamed"]
    assert v.changes("leads.leads", "2026-07-25", "2026-08-02", published_before="2026-08-01T10:00:00Z") == []
