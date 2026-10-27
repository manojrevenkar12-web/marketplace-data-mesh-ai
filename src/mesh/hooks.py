"""Claude Code hook: after the assistant edits a data contract or transform, check it like CI would.

Registered in ``.claude/settings.json`` as a PostToolUse hook on Edit and Write. Claude Code sends the tool call
as JSON on stdin; for a file under ``products/`` the hook lints the contract and runs the change review
without a model (fast, deterministic). Exit code 2 sends the blocking findings back to the assistant so it
fixes them in the same session; anything else lets the edit stand. Engineers get the review's answer while
they are still editing, instead of on the pull request.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mesh import config


def _old_text(path: Path) -> str | None:
    rel = path.resolve().relative_to(config.repo_root().resolve())
    try:
        return subprocess.run(["git", "show", f"HEAD:{rel.as_posix()}"], capture_output=True, text=True, check=True,
                              cwd=config.repo_root()).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def check(event: dict[str, Any]) -> tuple[int, str]:
    from mesh.access import AccessPolicy
    from mesh.ai.review import FileChange, Reviewer
    from mesh.contracts import Registry
    from mesh.lake import Lake

    raw = (event.get("tool_input") or {}).get("file_path") or ""
    if not raw:
        return 0, ""
    path = Path(raw)
    root = config.products_dir().resolve()
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return 0, ""
    if path.suffix not in (".yaml", ".sql") or len(rel.parts) != 2:
        return 0, ""
    base = Registry.load()  # the files on disk already contain the edit; the base comes from git HEAD
    old = _old_text(path)
    new = path.read_text() if path.exists() else None
    if old is not None:
        base = _with_old(base, path, old)
    lake_root = config.data_dir() / "lake"
    reviewer = Reviewer(base, AccessPolicy(), Lake(lake_root) if lake_root.exists() else None, router=None)
    rel_repo = path.resolve().relative_to(config.repo_root().resolve()).as_posix()
    review = asyncio.run(reviewer.review([FileChange(rel_repo, old, new)], use_model=False))
    blocking = [f for f in review.findings if f.severity == "block"]
    if not blocking:
        return 0, ""
    lines = [f"The data product check blocks this edit ({review.verdict}):"]
    lines += [f"- {f.product}{'.' + f.column if f.column else ''}: {f.detail}" for f in blocking]
    lines.append(f"Safe path: {review.suggested_path}")
    return 2, "\n".join(lines)


def _with_old(base: Any, path: Path, old: str) -> Any:
    from mesh.contracts import parse

    if path.suffix == ".yaml":
        try:
            c = parse(old)
        except Exception:
            return base
        return base.with_contract(c, base.sql.get(c.product))
    product = next((c.product for c in base if c.transform and Path(c.transform).stem == path.stem and c.domain == path.parent.name), None)
    return base.with_contract(base[product], old) if product else base


def main() -> None:  # pragma: no cover - process entry point
    event = json.loads(sys.stdin.read() or "{}")
    code, message = check(event)
    if message:
        print(message, file=sys.stderr)
    sys.exit(code)


if __name__ == "__main__":  # pragma: no cover
    main()
