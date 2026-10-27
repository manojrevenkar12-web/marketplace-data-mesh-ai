"""One object that holds the platform for a process: contracts, grants, lake, state, catalog, query engine,
model router and the AI features built on them. The CLI, the MCP server, the service and the evaluation all
start from ``Platform.load``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from mesh import config
from mesh.access import AccessPolicy
from mesh.ai.ask import Asker
from mesh.ai.catalog import Catalog
from mesh.ai.review import Reviewer
from mesh.ai.triage import Triage, TriageClassifier
from mesh.contracts import Registry
from mesh.history import Versions
from mesh.lake import Lake
from mesh.llm.factory import make_router
from mesh.llm.router import ModelRouter
from mesh.query import QueryEngine
from mesh.state import State


@dataclass
class Platform:
    registry: Registry
    policy: AccessPolicy
    lake: Lake
    state: State
    versions: Versions
    catalog: Catalog
    engine: QueryEngine
    router: ModelRouter
    asker: Asker
    triage: Triage
    reviewer: Reviewer
    today: date

    @classmethod
    def load(
        cls,
        data: Path | None = None,
        router: ModelRouter | None = None,
        today: date | None = None,
        registry: Registry | None = None,
        policy: AccessPolicy | None = None,
        models: Path | None = None,
    ) -> Platform:
        data = data or config.data_dir()
        registry = registry or Registry.load()
        policy = policy or AccessPolicy()
        lake = Lake(data / "lake")
        state = State(data / "state")
        versions = Versions(state, registry)
        if today is None:
            # the platform's "today" is the day after the newest partition: what a person sees the morning after
            latest = max((p for c in registry for p in lake.partitions(c.product)[-1:]), default=None)
            today = date.fromisoformat(latest) + timedelta(1) if latest else date.today()
        router = router or make_router()
        catalog = Catalog(registry, policy, lake, state, today=today)
        engine = QueryEngine(registry, policy, lake, today=today)
        clf = TriageClassifier.load((models or data / "models") / "triage.pkl")
        return cls(
            registry=registry,
            policy=policy,
            lake=lake,
            state=state,
            versions=versions,
            catalog=catalog,
            engine=engine,
            router=router,
            asker=Asker(catalog, engine, router, state),
            triage=Triage(state, versions, router, clf),
            reviewer=Reviewer(registry, policy, lake, router, today),
            today=today,
        )
