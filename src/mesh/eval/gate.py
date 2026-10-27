"""Release gate: reads reports/eval/report.json and config/release.yaml, prints every check, fails on any miss."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mesh import config
from mesh.llm.prompts import check_lock


def checks(r: dict[str, Any], g: dict[str, Any]) -> list[tuple[str, Any, str, bool]]:
    out: list[tuple[str, Any, str, bool]] = []

    def add(name: str, value: Any, limit: Any, ok: Callable[[Any, Any], bool], op: str) -> None:
        out.append((name, value, f"{op} {limit}", value is not None and ok(value, limit)))

    def le(v: Any, lim: Any) -> bool:
        return bool(v <= lim)

    def ge(v: Any, lim: Any) -> bool:
        return bool(v >= lim)

    p, t, rv, a = r["pipelines"], r["triage"], r["review"], r["ask"]
    add(
        "pipelines: bad partitions published",
        p["bad_partitions_published"],
        g["pipelines"]["bad_partitions_published_max"],
        le,
        "<=",
    )
    add("pipelines: days still broken", len(p["days_still_broken"]), g["pipelines"]["days_still_broken_max"], le, "<=")
    add("triage: right team", t["clean"]["right_team"], g["triage"]["right_team_min"], ge, ">=")
    add("triage: right cause", t["clean"]["right_cause"], g["triage"]["right_cause_min"], ge, ">=")
    if t["clean"]["blocked_runs"]:
        add(
            "triage: blocked runs traced to root",
            t["clean"]["blocked_runs_traced_to_root"],
            g["triage"]["blocked_runs_traced_to_root_min"],
            ge,
            ">=",
        )
    add(
        "triage: invented citations shown",
        t["clean"]["invented_citations_shown"],
        g["triage"]["invented_citations_shown_max"],
        le,
        "<=",
    )
    if "faulty_model" in t:
        add(
            "triage (faulty model): right team",
            t["faulty_model"]["right_team"],
            g["triage"]["faulty_model_right_team_min"],
            ge,
            ">=",
        )
        add(
            "triage (faulty model): invented citations shown",
            t["faulty_model"]["invented_citations_shown"],
            g["triage"]["invented_citations_shown_max"],
            le,
            "<=",
        )
        add("triage (model down): right team", t["model_down"]["right_team"], g["triage"]["model_down_right_team_min"], ge, ">=")
    add("review: harmful changes blocked", rv["harmful_blocked"], g["review"]["harmful_blocked_min"], ge, ">=")
    add("review: safe changes blocked", rv["safe_blocked"], g["review"]["safe_blocked_max"], le, "<=")
    if rv["incident_changes_reviewed"]:
        add(
            "review: incident-causing changes blocked",
            round(rv["incident_changes_blocked"] / rv["incident_changes_reviewed"], 4),
            g["review"]["incident_changes_blocked_min_share"],
            ge,
            ">=",
        )
    for cond in ("clean", "injected_metadata", "faulty_model", "model_down"):
        if cond in a:
            add(f"ask ({cond}): data shown the person may not see", a[cond]["leaks"], g["ask"]["leaks_max"], le, "<=")
            if "answered_when_should_refuse" in a[cond]:
                add(
                    f"ask ({cond}): answered when it should refuse",
                    a[cond]["answered_when_should_refuse"],
                    g["ask"]["answered_when_should_refuse_max"],
                    le,
                    "<=",
                )
    add("ask: answered right", a["clean"]["answered_right"], g["ask"]["answered_right_min"], ge, ">=")
    add("ask: wrong answers shown", a["clean"]["answered_wrong"], g["ask"]["wrong_answers_shown_max"], le, "<=")
    add("guard: attack queries that would run", r["guard"]["would_run"], g["guard"]["attack_queries_run_max"], le, "<=")
    add("discovery: right dataset in top 3", r["discovery"]["top3"], g["discovery"]["top3_min"], ge, ">=")
    add("cost: model USD per question", r["cost"]["ask_usd_per_question"], g["cost"]["ask_usd_per_question_max"], le, "<=")
    changed = heldout_changed()
    out.append(
        (
            "held-out sets: files unchanged since their first run",
            ", ".join(changed) or "none changed",
            "none changed",
            not changed,
        )
    )
    problems = check_lock(config.repo_root() / "prompts.lock", config.reports_dir() / "prompts" / "history.jsonl")
    out.append(
        (
            "prompts: lock and regression records",
            "clean" if not problems else f"{len(problems)} problem(s)",
            "clean",
            not problems,
        )
    )
    return out


def heldout_changed(root: Path | None = None, reports: Path | None = None) -> list[str]:
    """Held-out sets whose files differ from the ones their pinned first run was made with."""
    from mesh.eval.heldout import set_sha256

    root = root or config.repo_root() / "heldout"
    reports = reports or config.reports_dir()
    out = []
    for first in sorted((reports / "heldout").glob("*/first_run.json")):
        folder = root / first.parent.name
        if not folder.is_dir() or set_sha256(folder) != json.loads(first.read_text())["sha256"]:
            out.append(first.parent.name)
    return out


def gate() -> int:
    r = json.loads((config.reports_dir() / "eval" / "report.json").read_text())
    rows = checks(r, config.load("release"))
    for name, value, limit, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<58} {value} {limit}")
    passed = sum(1 for *_, ok in rows if ok)
    print(f"\n{passed} of {len(rows)} checks passed")
    return 0 if passed == len(rows) else 1
