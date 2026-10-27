"""Blind held-out sets, written by someone who has not seen the code, run once, the first run kept.

* ``questions``: business questions with the analyst's gold SQL and the person asking (``questions.yaml``);
* ``incidents``: failed runs described the way on-call sees them, with the true cause (``incidents.yaml``);
* ``changes``: edits to data contracts with the verdict a data governance lead would give (``changes.yaml``).

``reports/heldout/<set>/first_run.json`` records the first run with the sha256 of the set's files and is never
rewritten; later runs go to ``latest.json``. A set whose files changed after its first run is refused.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from mesh import config
from mesh.ai.review import FileChange
from mesh.ai.triage import Triage
from mesh.contracts import Registry, parse
from mesh.history import Versions
from mesh.platform import Platform
from mesh.state import Run, State


class SetChanged(RuntimeError):
    pass


def set_sha256(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file() and p.name != "STATUS"):
        h.update(f.relative_to(folder).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


async def run_set(
    name: str, root: Path | None = None, reports: Path | None = None, platform: Platform | None = None
) -> dict[str, Any]:
    folder = (root or config.repo_root() / "heldout") / name
    out_dir = (reports or config.reports_dir()) / "heldout" / name
    first = out_dir / "first_run.json"
    sha = set_sha256(folder)
    if first.exists() and json.loads(first.read_text())["sha256"] != sha:
        raise SetChanged(f"{name}: files changed since the first run")
    platform = platform or Platform.load()
    if (folder / "questions.yaml").exists():
        result = await _questions(folder, platform)
    elif (folder / "incidents.yaml").exists():
        result = await _incidents(folder, platform)
    elif (folder / "changes.yaml").exists():
        result = await _changes(folder, platform)
    else:
        raise ValueError(f"{name}: no questions.yaml, incidents.yaml or changes.yaml")
    status = (folder / "STATUS").read_text().strip() if (folder / "STATUS").exists() else "blind"
    record = {
        "set": name,
        "status": status,
        "sha256": sha,
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_mode": config.model_mode(),
        **result,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ("latest.json" if first.exists() else "first_run.json")).write_text(
        json.dumps(record, indent=2, default=str) + "\n"
    )
    return record


# ---------------------------------------------------------------- questions


async def _questions(folder: Path, platform: Platform) -> dict[str, Any]:
    from mesh.eval.run import _gold, _norm

    qs = yaml.safe_load((folder / "questions.yaml").read_text())["questions"]
    right = wrong = not_answered = refused_right = should_refuse = leaks = 0
    rows = []
    for q in qs:
        principal = platform.policy.principal_of(q["user"])
        a = await platform.asker.ask(q["user"], q["question"])
        if a.status == "answered":
            for t in a.plan.get("tables", []):
                ta = platform.policy.table_access(principal, platform.registry[t], platform.today)
                leaks += sum(1 for c in a.plan.get("columns", {}).get(t, []) if c not in ta.columns)
        if q["expect"] == "refuse":
            ok = not (a.status == "answered" and a.rows)
            refused_right += ok
            should_refuse += not ok
            verdict = "refused" if ok else "answered_but_should_refuse"
        elif a.status != "answered":
            not_answered += 1
            verdict = f"not answered ({a.status})"
        else:
            try:
                gold = _gold(platform, _resolve(q["gold_sql"], platform), principal)
            except Exception as e:  # a gold query the platform cannot run is reported, not fixed
                rows.append({"id": q["id"], "verdict": f"gold query failed: {str(e)[:120]}"})
                continue
            good = _norm(a.rows) == gold
            right += good
            wrong += not good
            verdict = "right" if good else "wrong"
        rows.append({"id": q["id"], "user": q["user"], "status": a.status, "verdict": verdict, "sql": a.sql})
    n_ans = sum(1 for q in qs if q["expect"] == "answer")
    n_ref = len(qs) - n_ans

    def pct(x: int, n: int) -> float | None:
        return round(x / n, 4) if n else None

    headline = (
        f"{len(qs)} questions; answered right {_p(pct(right, n_ans))}, wrong answers shown {wrong}, not answered {not_answered}; "
        f"refused correctly {_p(pct(refused_right, n_ref))}; data shown the person may not see: {leaks}."
    )
    return {
        "kind": "questions",
        "questions": len(qs),
        "answered_right": pct(right, n_ans),
        "wrong_answers_shown": wrong,
        "not_answered": not_answered,
        "refused_right": pct(refused_right, n_ref),
        "answered_when_should_refuse": should_refuse,
        "leaks": leaks,
        "rows": rows,
        "headline": headline,
    }


def _resolve(sql: str, platform: Platform) -> str:
    """Gold SQL may say {latest} or {latest-N} for the latest day with data, as the writer could not know it."""
    import re

    latest = max(p for c in platform.registry for p in platform.lake.partitions(c.product)[-1:])

    def sub(m: re.Match[str]) -> str:
        n = int(m.group(1) or 0)
        from datetime import date

        return (date.fromisoformat(latest) - timedelta(n)).isoformat()

    return re.sub(r"\{latest(?:-(\d+))?\}", sub, sql)


# ---------------------------------------------------------------- incidents


async def _incidents(folder: Path, platform: Platform) -> dict[str, Any]:
    cases = yaml.safe_load((folder / "incidents.yaml").read_text())["incidents"]
    right = routed = naive = 0
    rows = []
    for case in cases:
        tmp = folder.parent / ".scratch" / folder.name / case["id"]
        state = State(tmp)
        state.reset()
        versions = Versions(state, platform.registry)
        versions.bootstrap("2026-01-01")
        dt = case.get("dt", "2026-09-10")
        for ch in case.get("recent_changes", []) or []:
            _apply_change(versions, platform.registry, ch, dt)
        started = f"{(datetime.fromisoformat(dt) + timedelta(days=1)).date().isoformat()}T02:00:01Z"
        for up in case.get("upstream", []) or []:
            r = Run(
                f"{up['product']}:{dt}:1",
                up["product"],
                dt,
                up["status"],
                started,
                started,
                step=up.get("step", "ingest"),
                error_type=up.get("error_type"),
                error=up.get("error"),
            )
            state.add_run(r, up.get("log", []))
        run = Run(
            f"{case['product']}:{dt}:1",
            case["product"],
            dt,
            case["status"],
            started,
            started,
            step=case.get("step", "transform"),
            error_type=case.get("error_type"),
            error=case.get("error"),
            blocked_by=case.get("blocked_by", []) or [],
        )
        state.add_run(run, case.get("log", []))
        t = Triage(state, versions, platform.router, platform.triage.classifier)
        d = await t.diagnose(run.id)
        right += d.category == case["expected_category"]
        routed += d.route_to == case["expected_team"]
        naive += d.naive_route == case["expected_team"]
        rows.append(
            {
                "id": case["id"],
                "expected": case["expected_category"],
                "got": d.category,
                "team": case["expected_team"],
                "routed": d.route_to,
                "by": d.decided_by,
            }
        )
    n = len(cases)
    headline = (
        f"{n} incidents; right cause {_p(right / n if n else None)}, right team {_p(routed / n if n else None)} "
        f"(a plain alert to the failed job's owner: {_p(naive / n if n else None)})."
    )
    return {
        "kind": "incidents",
        "incidents": n,
        "right_cause": round(right / n, 4) if n else None,
        "right_team": round(routed / n, 4) if n else None,
        "naive_alert_right_team": round(naive / n, 4) if n else None,
        "rows": rows,
        "headline": headline,
    }


def _apply_change(versions: Versions, registry: Registry, ch: dict[str, Any], dt: str) -> None:
    product = ch["product"]
    base = registry[product]
    text = registry.paths[product].read_text()
    sql = registry.sql.get(product)
    for old, new in (ch.get("rename_columns") or {}).items():
        text = text.replace(f"name: {old}", f"name: {new}").replace(f"column: {old}", f"column: {new}")
    if ch.get("sql_replace"):
        assert sql is not None
        for old, new in ch["sql_replace"].items():
            sql = sql.replace(old, new)
    text = text.replace(f"version: {base.version}", f"version: {base.version + 1}", 1)
    effective = (datetime.fromisoformat(dt) - timedelta(days=int(ch.get("days_before", 1)))).date().isoformat()
    versions.publish(
        text, sql, effective, ch.get("author", base.owner), ch.get("note", "change"), published=f"{effective}T15:00:00Z"
    )


# ---------------------------------------------------------------- changes


async def _changes(folder: Path, platform: Platform) -> dict[str, Any]:
    cases = yaml.safe_load((folder / "changes.yaml").read_text())["changes"]
    rows = []
    exact = harmful = harmful_blocked = safe = safe_blocked = 0
    for case in cases:
        product = case["product"]
        path = platform.registry.paths[product]
        old = path.read_text()
        new = old
        for edit in case["edits"]:
            if edit["find"] not in new:
                rows.append({"id": case["id"], "verdict": "edit does not apply", "find": edit["find"][:80]})
                new = None  # type: ignore[assignment]
                break
            new = new.replace(edit["find"], edit["replace"], 1)
        if new is None:
            continue
        r = await platform.reviewer.review([FileChange(f"products/{path.parent.name}/{path.name}", old, new)])
        exp = case["expected_verdict"]
        exact += r.verdict == exp
        if exp == "block":
            harmful += 1
            harmful_blocked += r.verdict == "block"
        if exp == "approve":
            safe += 1
            safe_blocked += r.verdict == "block"
        rows.append(
            {
                "id": case["id"],
                "expected": exp,
                "got": r.verdict,
                "findings": [f.kind for f in r.findings if f.severity != "info"],
            }
        )
    n = len([x for x in rows if "got" in x])
    headline = (
        f"{n} changes; verdict as expected {_p(exact / n if n else None)}; harmful blocked {_p(harmful_blocked / harmful if harmful else None)}; "
        f"safe changes blocked by mistake {safe_blocked} of {safe}."
    )
    return {
        "kind": "changes",
        "changes": n,
        "exact_verdict": round(exact / n, 4) if n else None,
        "harmful_blocked": round(harmful_blocked / harmful, 4) if harmful else None,
        "safe_blocked": safe_blocked,
        "rows": rows,
        "headline": headline,
        "parse_check": [parse(platform.registry.paths[c["product"]].read_text()).product for c in cases[:1]],
    }


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"
