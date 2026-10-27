"""Platform state: pipeline runs and their logs, quality results, contract versions, usage of AI features.

Kept as append-only JSON lines under ``data/state`` so a run of the platform is reproducible and easy to
inspect. On AWS the same records go to DynamoDB (runs, contract versions) and CloudWatch Logs (run logs);
the service keeps its own records (access requests, audit, feedback) in PostgreSQL.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from mesh import config

RunStatus = Literal["success", "failed", "quality_failed", "upstream_failed"]


@dataclass
class Run:
    id: str
    product: str
    dt: str
    status: RunStatus
    started: str
    ended: str
    attempt: int = 1
    step: str = ""  # ingest | transform | quality | publish
    error_type: str | None = None
    error: str | None = None
    rows: int = 0
    contract_version: int = 0
    blocked_by: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "success"


@dataclass
class QualityResult:
    product: str
    dt: str
    check: str
    severity: str
    passed: bool
    observed: Any
    detail: str


@dataclass
class ContractVersion:
    product: str
    version: int
    effective: str  # first dt this version applies to
    contract: str  # YAML text
    sql: str | None
    author: str
    note: str
    published: str = ""  # when the version was merged (UTC); a diagnosis only sees versions published before the run


class State:
    def __init__(self, root: Path | None = None):
        self.root = root or (config.data_dir() / "state")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)
        self._lock = threading.Lock()

    # ---- generic
    def _append(self, name: str, row: dict[str, Any]) -> None:
        with self._lock, (self.root / f"{name}.jsonl").open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def _read(self, name: str) -> list[dict[str, Any]]:
        p = self.root / f"{name}.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def reset(self) -> None:
        for p in self.root.glob("*.jsonl"):
            p.unlink()
        for p in (self.root / "logs").glob("*.log"):
            p.unlink()

    # ---- runs
    def add_run(self, run: Run, log: list[str]) -> None:
        self._append("runs", asdict(run))
        (self.root / "logs" / f"{run.id}.log").write_text("\n".join(log) + "\n")

    def runs(self, product: str | None = None, dt: str | None = None) -> list[Run]:
        out = [Run(**r) for r in self._read("runs")]
        return [r for r in out if (product is None or r.product == product) and (dt is None or r.dt == dt)]

    def run(self, run_id: str) -> Run | None:
        return next((r for r in self.runs() if r.id == run_id), None)

    def latest_runs(self) -> dict[tuple[str, str], Run]:
        """The last attempt for every (product, dt)."""
        out: dict[tuple[str, str], Run] = {}
        for r in self.runs():
            out[(r.product, r.dt)] = r
        return out

    def log(self, run_id: str) -> list[str]:
        p = self.root / "logs" / f"{run_id}.log"
        return p.read_text().splitlines() if p.exists() else []

    # ---- quality
    def add_quality(self, results: list[QualityResult]) -> None:
        for q in results:
            self._append("quality", asdict(q))

    def quality(self, product: str | None = None, dt: str | None = None) -> list[QualityResult]:
        out = [QualityResult(**r) for r in self._read("quality")]
        return [q for q in out if (product is None or q.product == product) and (dt is None or q.dt == dt)]

    # ---- contract versions
    def add_contract_version(self, v: ContractVersion) -> None:
        self._append("contracts", asdict(v))

    def contract_versions(self, product: str | None = None) -> list[ContractVersion]:
        out = [ContractVersion(**r) for r in self._read("contracts")]
        return [v for v in out if product is None or v.product == product]

    # ---- usage of AI features (who used what, with what outcome): the adoption numbers come from here
    def add_usage(self, row: dict[str, Any]) -> None:
        self._append("usage", row)

    def usage(self) -> list[dict[str, Any]]:
        return self._read("usage")
