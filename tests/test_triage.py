"""The failure triage names the cause, the evidence and the team that has to act; a model can explain but
not overrule a confident rule or cite evidence that is not there."""

from __future__ import annotations

import json

from mesh.ai.triage import Triage, TriageClassifier

from .conftest import router_with


def truth(world):  # type: ignore[no-untyped-def]
    return [json.loads(x) for x in (world / "truth" / "incidents.jsonl").read_text().splitlines()]


async def test_failed_and_blocked_runs_are_routed_to_the_team_that_can_fix_them(world, platform):
    right = routed = naive = n = 0
    for inc in truth(world):
        for run_id in inc["affected_runs"]:
            d = await platform.triage.diagnose(run_id)
            n += 1
            right += d.category == inc["kind"]
            routed += d.route_to == inc["owner"]
            naive += d.naive_route == inc["owner"]
            assert set(d.cited) <= {e.id for e in d.evidence}
    assert n >= 10
    assert right / n >= 0.9 and routed / n >= 0.9
    assert routed > naive  # a plain "job failed" alert pages the failed job's owner


async def test_a_blocked_run_points_at_the_root_failure(world, platform):
    blocked = next(r for i in truth(world) for r in i["affected_runs"] if platform.state.run(r).status == "upstream_failed")
    d = await platform.triage.diagnose(blocked)
    assert d.root_run != d.run_id and any("blocked by" in s for s in d.next_steps)
    assert "upstream runs" in d.sources_consulted


async def test_a_misbehaving_model_does_not_change_the_diagnosis(world, platform):
    run_id = truth(world)[0]["affected_runs"][0]
    honest = await platform.triage.diagnose(run_id)
    for faults in ({"hallucinate": 1.0}, {"flip": 1.0}, {"outage": 1.0}, {"invalid": 1.0}):
        t = Triage(platform.state, platform.versions, router_with(faults), platform.triage.classifier)
        d = await t.diagnose(run_id)
        assert d.category == honest.category and d.route_to == honest.route_to
        assert "E99" not in d.cited


def test_the_classifier_learns_from_resolved_tickets(world, platform):
    clf = TriageClassifier.load(world / "models" / "triage.pkl")
    assert clf.model is not None and len(clf.model.classes_) >= 3
    tickets = [json.loads(x) for x in (world / "history" / "tickets.jsonl").read_text().splitlines()]
    hits = total = 0
    for t in tickets:
        run = platform.state.run(t["run_id"])
        if run is None or run.status == "upstream_failed":
            continue
        total += 1
        hits += clf.predict(TriageClassifier.text(run, platform.state.log(run.id)))[0][0] == t["root_cause"]
    assert total and hits / total >= 0.7  # tickets are mislabelled now and then; the classifier should not memorise that
