"""A small simulated marketplace built once per test session in a temporary directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def small_config() -> dict[str, Any]:
    cfg = yaml.safe_load((ROOT / "config" / "world.yaml").read_text())
    cfg.update(
        start="2026-07-01", live_from="2026-07-18", end="2026-08-10", dealers=60, new_listings_per_day=120, burn_in_days=45
    )
    cfg["incidents"].update(rate_history=0.55, rate_live=0.55)
    return cfg


@pytest.fixture(scope="session")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from mesh.ai.triage import train_from_tickets
    from mesh.sim.world import World
    from mesh.state import State

    data = tmp_path_factory.mktemp("mesh") / "data"
    World(data, small_config()).build()
    clf, _ = train_from_tickets(State(data / "state"), data / "history" / "tickets.jsonl")
    clf.save(data / "models" / "triage.pkl")
    return data


async def nosleep(_: float) -> None:
    return None


def router_with(faults: dict[str, float] | None = None, provider: Any = None):  # type: ignore[no-untyped-def]
    from mesh.llm.router import ModelRouter
    from mesh.llm.simulated import SimulatedProvider

    return ModelRouter({"simulated": provider or SimulatedProvider(faults or {})}, sleep=nosleep)


@pytest.fixture(scope="session")
def platform(world: Path):  # type: ignore[no-untyped-def]
    from mesh.platform import Platform

    return Platform.load(world, router=router_with())
