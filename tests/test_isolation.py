"""The platform and its AI features never see the simulation's truth."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "mesh"
ALLOWED = {"sim", "eval"}


def test_only_the_simulation_and_the_evaluation_touch_the_truth():
    offenders = []
    for f in SRC.rglob("*.py"):
        part = f.relative_to(SRC).parts[0]
        if part in ALLOWED or f.name == "cli.py":
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and (node.module.startswith("mesh.sim") or node.module.startswith("mesh.eval")):
                offenders.append(f"{f.relative_to(SRC)} imports {node.module}")
            if isinstance(node, ast.Import) and any(a.name.startswith(("mesh.sim", "mesh.eval")) for a in node.names):
                offenders.append(f"{f.relative_to(SRC)} imports mesh.sim/eval")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in ("truth", "world.yaml"):
                offenders.append(f"{f.relative_to(SRC)} names '{node.value}'")
    assert offenders == []
