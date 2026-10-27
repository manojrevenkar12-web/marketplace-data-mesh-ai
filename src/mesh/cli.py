"""mesh: the platform's command line.

mesh sim [--small]              simulate the marketplace (data/landing, data/lake, data/state, data/truth)
mesh train                      train the triage classifier on resolved on-call tickets
mesh lint                       check every contract against the platform rules
mesh review [--base REF]        review changed products/ files against a git ref (exit 1 when blocked)
mesh ask --user U "question"    answer a question as user U
mesh query --user U "SQL"       run SQL as user U, with the platform's checks
mesh failures | diagnose RUN    failed runs; why one failed
mesh freshness                  products against their SLA
mesh dags render [--check]      generate the Airflow DAG from the contracts
mesh infra render [--check]     generate the Terraform inputs from the contracts and grants
mesh mcp [--http]               the MCP server for coding assistants
mesh serve | demo [--seed-only] the API (and the console's demo database)
mesh eval [--quick] | gate      evaluate, then apply the release gate
mesh prompts lock|check         pin prompt versions to their regression runs
mesh heldout run SET            run a blind held-out set (its first run is kept)
mesh run                        sim, train, eval, gate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mesh import config


def _json(x: Any) -> None:
    print(json.dumps(x, indent=2, default=str, ensure_ascii=False))


def _platform() -> Any:
    from mesh.platform import Platform

    return Platform.load()


def cmd_sim(a: argparse.Namespace) -> int:
    import yaml

    from mesh.sim.world import World

    cfg = yaml.safe_load((config.config_dir() / "world.yaml").read_text())
    if a.small:
        cfg.update(start="2026-07-01", live_from="2026-07-20", end="2026-08-10", dealers=60, new_listings_per_day=120)
    _json(World(cfg=cfg).build())
    return 0


def cmd_train(_: argparse.Namespace) -> int:
    from mesh.ai.triage import train_from_tickets
    from mesh.state import State

    data = config.data_dir()
    clf, n = train_from_tickets(State(data / "state"), data / "history" / "tickets.jsonl")
    clf.save(data / "models" / "triage.pkl")
    _json({"tickets": n, "classes": list(clf.model.classes_) if clf.model is not None else []})
    return 0


def cmd_lint(_: argparse.Namespace) -> int:
    from mesh.contracts import Registry, lint_all

    problems = lint_all(Registry.load())
    for p in problems:
        print(f"{p.severity.upper():5} {p.product}: [{p.rule}] {p.message}")
    print(f"{len(problems)} problem(s)")
    return 1 if any(p.severity == "error" for p in problems) else 0


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=config.repo_root()).stdout


def cmd_review(a: argparse.Namespace) -> int:
    from mesh.access import AccessPolicy
    from mesh.ai.review import FileChange, Reviewer
    from mesh.contracts import Registry, parse
    from mesh.lake import Lake
    from mesh.llm.factory import make_router

    names = [
        n for n in _git("diff", "--name-only", f"{a.base}...HEAD", "--", "products").split() if n.endswith((".yaml", ".sql"))
    ]
    changes = []
    for n in names:
        try:
            old: str | None = _git("show", f"{a.base}:{n}")
        except subprocess.CalledProcessError:
            old = None
        path = config.repo_root() / n
        changes.append(FileChange(n, old, path.read_text() if path.exists() else None))
    if not changes:
        print("No data product changes.")
        return 0
    base = Registry.load()  # the working tree; rebuild the base from the old files
    for ch in changes:
        if ch.path.endswith(".yaml") and ch.old is not None:
            c = parse(ch.old)
            base = base.with_contract(c, base.sql.get(c.product))
        elif ch.path.endswith(".sql") and ch.old is not None:
            stem = Path(ch.path).stem
            product = next((c.product for c in base if c.transform and Path(c.transform).stem == stem), None)
            if product:
                base = base.with_contract(base[product], ch.old)
    lake_root = config.data_dir() / "lake"
    reviewer = Reviewer(
        base, AccessPolicy(), Lake(lake_root) if lake_root.exists() else None, None if a.no_model else make_router()
    )
    r = asyncio.run(reviewer.review(changes, use_model=not a.no_model))
    if a.format == "json":
        _json(r.as_dict())
    else:
        print(r.comment_markdown)
    return 1 if r.verdict == "block" else 0


def cmd_ask(a: argparse.Namespace) -> int:
    p = _platform()
    ans = asyncio.run(p.asker.ask(a.user, a.question))
    _json(ans.as_dict())
    return 0 if ans.status == "answered" else 1


def cmd_query(a: argparse.Namespace) -> int:
    from mesh.query import QueryRejected

    p = _platform()
    try:
        r = p.engine.run(a.sql, p.policy.principal_of(a.user), "person")
    except QueryRejected as e:
        _json(e.plan.summary())
        return 1
    _json({"columns": r.columns, "rows": r.rows, "plan": r.plan.summary()})
    return 0


def cmd_failures(_: argparse.Namespace) -> int:
    p = _platform()
    rows = [asdict(r) for r in p.state.latest_runs().values() if not r.ok]
    _json([{k: r[k] for k in ("id", "product", "dt", "status", "error_type")} for r in rows])
    return 0


def cmd_diagnose(a: argparse.Namespace) -> int:
    p = _platform()
    _json(asyncio.run(p.triage.diagnose(a.run_id)).as_dict())
    return 0


def cmd_freshness(_: argparse.Namespace) -> int:
    from mesh.monitor import freshness

    _json(freshness(_platform().catalog))
    return 0


def _render_check(path: Path, text: str, check: bool) -> int:
    if check:
        if not path.exists() or path.read_text() != text:
            print(f"{path.relative_to(config.repo_root())} is stale; run the render command and commit it")
            return 1
        print(f"{path.relative_to(config.repo_root())} is up to date")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path.relative_to(config.repo_root())}")
    return 0


def cmd_dags(a: argparse.Namespace) -> int:
    from mesh.contracts import Registry
    from mesh.dags import render

    return _render_check(config.repo_root() / "dags" / "mesh_daily.py", render(Registry.load()), a.check)


def cmd_infra(a: argparse.Namespace) -> int:
    from mesh.access import AccessPolicy
    from mesh.aws.terraform import dumps, render
    from mesh.contracts import Registry

    return _render_check(
        config.repo_root() / "infra" / "terraform" / "products.auto.tfvars.json",
        dumps(render(Registry.load(), AccessPolicy())),
        a.check,
    )


def cmd_mcp(a: argparse.Namespace) -> int:
    from mesh.mcp_server import main

    main(http=a.http, port=a.port)
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("mesh.service.app:create_app", factory=True, host=a.host, port=a.port)
    return 0


def cmd_demo(a: argparse.Namespace) -> int:
    from mesh.service.demo import seed

    _json(asyncio.run(seed()))
    if a.seed_only:
        return 0
    return cmd_serve(a)


def cmd_eval(a: argparse.Namespace) -> int:
    from mesh.eval.run import run_all

    report = asyncio.run(run_all(quick=a.quick))
    print((config.reports_dir() / "eval" / "SUMMARY.md").read_text())
    return 0 if report else 1


def cmd_gate(_: argparse.Namespace) -> int:
    from mesh.eval.gate import gate

    return gate()


def cmd_prompts(a: argparse.Namespace) -> int:
    from mesh.llm.prompts import check_lock, lock_entries

    lock = config.repo_root() / "prompts.lock"
    history = config.reports_dir() / "prompts" / "history.jsonl"
    if a.action == "lock":
        lock.write_text(json.dumps(lock_entries(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {lock.name}")
        return 0
    problems = check_lock(lock, history)
    for p in problems:
        print("FAIL", p)
    print("prompts: clean" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_heldout(a: argparse.Namespace) -> int:
    from mesh.eval.heldout import run_set

    _json(asyncio.run(run_set(a.set)))
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    for step in (cmd_sim, cmd_train, cmd_eval, cmd_gate):
        if step(a):
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mesh", description="Marketplace data mesh platform")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sim")
    s.add_argument("--small", action="store_true")
    s.set_defaults(fn=cmd_sim)
    sub.add_parser("train").set_defaults(fn=cmd_train)
    sub.add_parser("lint").set_defaults(fn=cmd_lint)
    r = sub.add_parser("review")
    r.add_argument("--base", default="origin/main")
    r.add_argument("--format", choices=["markdown", "json"], default="markdown")
    r.add_argument("--no-model", action="store_true")
    r.set_defaults(fn=cmd_review)
    q = sub.add_parser("ask")
    q.add_argument("--user", required=True)
    q.add_argument("question")
    q.set_defaults(fn=cmd_ask)
    qq = sub.add_parser("query")
    qq.add_argument("--user", required=True)
    qq.add_argument("sql")
    qq.set_defaults(fn=cmd_query)
    sub.add_parser("failures").set_defaults(fn=cmd_failures)
    d = sub.add_parser("diagnose")
    d.add_argument("run_id")
    d.set_defaults(fn=cmd_diagnose)
    sub.add_parser("freshness").set_defaults(fn=cmd_freshness)
    for name, fn in (("dags", cmd_dags), ("infra", cmd_infra)):
        x = sub.add_parser(name)
        x.add_argument("action", choices=["render"])
        x.add_argument("--check", action="store_true")
        x.set_defaults(fn=fn)
    m = sub.add_parser("mcp")
    m.add_argument("--http", action="store_true")
    m.add_argument("--port", type=int, default=8765)
    m.set_defaults(fn=cmd_mcp)
    for name, fn in (("serve", cmd_serve), ("demo", cmd_demo)):
        x = sub.add_parser(name)
        x.add_argument("--host", default="127.0.0.1")
        x.add_argument("--port", type=int, default=8080)
        if name == "demo":
            x.add_argument("--seed-only", action="store_true")
        x.set_defaults(fn=fn)
    e = sub.add_parser("eval")
    e.add_argument("--quick", action="store_true")
    e.set_defaults(fn=cmd_eval)
    sub.add_parser("gate").set_defaults(fn=cmd_gate)
    pr = sub.add_parser("prompts")
    pr.add_argument("action", choices=["lock", "check"])
    pr.set_defaults(fn=cmd_prompts)
    h = sub.add_parser("heldout")
    h.add_argument("action", choices=["run"])
    h.add_argument("set")
    h.set_defaults(fn=cmd_heldout)
    rr = sub.add_parser("run")
    rr.add_argument("--small", action="store_true")
    rr.add_argument("--quick", action="store_true")
    rr.set_defaults(fn=cmd_run)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
