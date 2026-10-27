"""The held-out discipline and the release gate: pinned first runs cannot be silently re-made, and the gate fails on
any threshold miss, on a changed held-out set, or on an unlocked prompt."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mesh import config
from mesh.eval import gate as gate_mod
from mesh.eval.heldout import SetChanged, run_set, set_sha256

ROOT = config.repo_root()


def test_every_recorded_first_run_matches_its_sets_files():
    firsts = sorted((ROOT / "reports" / "heldout").glob("*/first_run.json"))
    assert firsts, "no recorded first runs"
    for first in firsts:
        rec = json.loads(first.read_text())
        folder = ROOT / "heldout" / rec["set"]
        assert set_sha256(folder) == rec["sha256"], f"{rec['set']} changed after its first run"
        assert (folder / "STATUS").read_text().strip() in {"blind", "regression"}
        assert rec["headline"]
    assert gate_mod.heldout_changed() == []


def test_the_status_file_is_not_part_of_the_pin(tmp_path: Path):
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "questions.yaml").write_text("questions: []\n")
    (folder / "STATUS").write_text("blind\n")
    before = set_sha256(folder)
    (folder / "STATUS").write_text("regression\n")  # studying a set's failures changes its status, not its pin
    assert set_sha256(folder) == before
    (folder / "questions.yaml").write_text("questions: [x]\n")
    assert set_sha256(folder) != before


async def test_a_changed_set_cannot_be_run_again(tmp_path: Path):
    root, reports = tmp_path / "heldout", tmp_path / "reports"
    shutil.copytree(ROOT / "heldout" / "changes-1", root / "changes-1")
    first = reports / "heldout" / "changes-1" / "first_run.json"
    first.parent.mkdir(parents=True)
    first.write_text(json.dumps({"sha256": set_sha256(root / "changes-1")}))
    with (root / "changes-1" / "changes.yaml").open("a") as f:
        f.write("\n# edited after the first run\n")
    with pytest.raises(SetChanged):
        await run_set("changes-1", root=root, reports=reports, platform=object())  # type: ignore[arg-type]
    assert gate_mod.heldout_changed(root, reports) == ["changes-1"]


def _report() -> dict:
    return json.loads((ROOT / "reports" / "eval" / "report.json").read_text())


def test_the_gate_passes_the_committed_report_and_fails_each_kind_of_miss():
    cfg = config.load("release")
    rows = gate_mod.checks(_report(), cfg)
    assert all(ok for *_, ok in rows), [r for r in rows if not r[3]]

    leak = _report()
    leak["ask"]["faulty_model"]["leaks"] = 1
    harmful = _report()
    harmful["review"]["harmful_blocked"] = 0.95
    routing = _report()
    routing["triage"]["clean"]["right_team"] = 0.5
    for bad, name in ((leak, "ask (faulty_model): data shown"), (harmful, "review: harmful"), (routing, "triage: right team")):
        failed = [r[0] for r in gate_mod.checks(bad, cfg) if not r[3]]
        assert any(f.startswith(name) for f in failed), failed
