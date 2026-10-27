"""The pipelines the platform runs for every data product, every day.

Source products are ingested from the operational system's daily drop in the landing zone and checked
against their contract; derived products run their SQL transform (written for Athena, executed locally by
DuckDB after sqlglot translates it) over the published partitions of their inputs. Either way the result is
staged, checked and only then published (write-audit-publish). Each run leaves a record and a log, which is
what the on-call engineer and the failure triage read.

The same steps are what the generated Airflow DAGs call on MWAA (``mesh.dags``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.json as pajson
import sqlglot

from mesh import config
from mesh.contracts import Contract, Registry, render_sql
from mesh.history import Versions
from mesh.lake import Lake, arrow_schema, connect, query_arrow, register_view
from mesh.quality import run_checks
from mesh.state import Run, State


class StepError(Exception):
    def __init__(self, kind: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable


# A fault hook lets the simulation break the environment (permissions, capacity, S3) the way it breaks in
# production. It returns an error to raise for (product, dt, step, attempt), or None.
FaultHook = Callable[[str, str, str, int], StepError | None]


@dataclass
class Log:
    dt: str
    product: str
    offset_hours: float = 0
    lines: list[str] = field(default_factory=list)
    _t: datetime | None = None

    def __post_init__(self) -> None:
        # the daily DAG runs at 02:00 UTC the day after; re-runs happen later (when on-call re-triggers them)
        self._t = datetime.fromisoformat(self.dt) + timedelta(days=1, hours=2 + self.offset_hours)

    def __call__(self, level: str, task: str, msg: str, seconds: float = 1.0) -> None:
        assert self._t is not None
        self._t += timedelta(seconds=seconds)
        self.lines.append(f"{self._t.isoformat(timespec='seconds')}Z {level:<5} [{self.product}.{task}] {msg}")

    @property
    def now(self) -> str:
        assert self._t is not None
        return self._t.isoformat(timespec="seconds") + "Z"


class Runner:
    def __init__(self, lake: Lake, state: State, versions: Versions, landing: Path | None = None,
                 faults: FaultHook | None = None, retries: int = 2):
        self.lake = lake
        self.state = state
        self.versions = versions
        self.landing = landing or (config.data_dir() / "landing")
        self.faults = faults
        self.retries = retries

    def run_day(self, dt: str) -> list[Run]:
        registry = self.versions.registry_at(dt)
        return [self.run_product(p, dt, registry) for p in registry.order()]

    def run_product(self, product: str, dt: str, registry: Registry) -> Run:
        c = registry[product]
        self._seq = getattr(self, "_seq", {})
        n = self._seq[(product, dt)] = self._seq.get((product, dt), len(self.state.runs(product, dt))) + 1
        run_id = f"{product}:{dt}:{n}"
        log = Log(dt, product, offset_hours=0 if n == 1 else 30 + n)
        started = log.now
        log("INFO", "start", f"run {product} for dt={dt} under contract v{c.version}")
        # inputs must be published for this day before a derived product runs
        missing = [i for i in c.inputs if not self.lake.has(i, dt)]
        if missing:
            for i in missing:
                last = self.state.runs(i, dt)
                why = f" (last run: {last[-1].status})" if last else " (no run yet)"
                log("ERROR", "sensor", f"input {i} has no published partition for dt={dt}{why}")
            log("ERROR", "sensor", f"UpstreamNotReady: {', '.join(missing)} not published; {product} not run")
            return self._record(Run(run_id, product, dt, "upstream_failed", started, log.now, step="sensor",
                                    error_type="UpstreamNotReady", error=f"inputs not published: {', '.join(missing)}",
                                    contract_version=c.version, blocked_by=missing), log)
        attempt = 0
        step = "ingest" if c.kind == "source" else "transform"
        while True:
            attempt += 1
            try:
                self._fault(product, dt, step, attempt)
                table = self._ingest(c, dt, log) if c.kind == "source" else self._transform(c, registry, dt, log)
                break
            except StepError as e:
                log("ERROR", step, f"{e.kind}: {e.message}")
                if e.retryable and attempt <= self.retries:
                    log("WARN", step, f"attempt {attempt} failed; retrying in {30 * attempt}s", seconds=30 * attempt)
                    continue
                return self._record(Run(run_id, product, dt, "failed", started, log.now, attempt, step,
                                        e.kind, e.message, contract_version=c.version), log)
        staged = self.lake.stage(product, dt, table)
        log("INFO", step, f"staged {table.num_rows} rows to {self.lake.partition_dir(product, dt, staged=True).relative_to(self.lake.root.parent)}")
        try:
            self._fault(product, dt, "quality", attempt)
        except StepError as e:
            log("ERROR", "quality", f"{e.kind}: {e.message}")
            self.lake.discard(product, dt)
            return self._record(Run(run_id, product, dt, "failed", started, log.now, attempt, "quality",
                                    e.kind, e.message, rows=table.num_rows, contract_version=c.version), log)
        last_ok = {r.dt: r.rows for r in self.state.runs(product) if r.ok and r.dt < dt}
        previous = [last_ok[d] for d in sorted(last_ok)]
        results = run_checks(c, dt, staged, self.lake, previous)
        self.state.add_quality(results)
        failed = [q for q in results if not q.passed]
        for q in results:
            log("INFO" if q.passed else ("ERROR" if q.severity == "error" else "WARN"), "quality",
                f"{'PASS' if q.passed else 'FAIL'} {q.check} ({q.severity}): {q.detail}", seconds=0.2)
        blocking = [q for q in failed if q.severity == "error"]
        if blocking:
            self.lake.discard(product, dt)
            msg = "; ".join(f"{q.check}: {q.detail}" for q in blocking)
            log("ERROR", "quality", f"QualityCheckFailed: partition dt={dt} quarantined, not published ({len(blocking)} blocking checks)")
            return self._record(Run(run_id, product, dt, "quality_failed", started, log.now, attempt, "quality",
                                    "QualityCheckFailed", msg, rows=table.num_rows, contract_version=c.version), log)
        self.lake.publish(product, dt)
        log("INFO", "publish", f"published dt={dt} ({table.num_rows} rows); registered partition in the catalog")
        return self._record(Run(run_id, product, dt, "success", started, log.now, attempt, "publish",
                                rows=table.num_rows, contract_version=c.version), log)

    # ---------------------------------------------------------------- steps

    def _fault(self, product: str, dt: str, step: str, attempt: int) -> None:
        if self.faults:
            err = self.faults(product, dt, step, attempt)
            if err is not None:
                raise err

    def _ingest(self, c: Contract, dt: str, log: Log) -> pa.Table:
        assert c.source is not None
        path = self.landing / c.source.system / c.name / f"{dt}.jsonl"
        log("INFO", "ingest", f"reading s3://mesh-landing/{c.source.system}/{c.name}/{dt}.jsonl")
        if not path.exists():
            raise StepError("LandingFileNotFound", f"s3://mesh-landing/{c.source.system}/{c.name}/{dt}.jsonl does not exist; "
                                                   f"{c.source.system} has not delivered the drop for {dt}")
        try:
            raw = pajson.read_json(path, read_options=pajson.ReadOptions(block_size=1 << 24),
                                   parse_options=pajson.ParseOptions(unexpected_field_behavior="infer"))
        except pa.ArrowInvalid as e:
            raise StepError("MalformedInput", str(e).splitlines()[0]) from e
        log("INFO", "ingest", f"read {raw.num_rows} records with fields {sorted(raw.column_names)}", seconds=3)
        fields = set(raw.column_names)
        declared = set(c.column_names)
        unexpected = sorted(fields - declared)
        missing_required = [col.name for col in c.columns if col.required and col.name not in fields]
        if unexpected:
            log("WARN", "ingest", f"ignoring fields not in contract v{c.version}: {unexpected}")
        if missing_required:
            raise StepError("ContractViolation", f"required column(s) {missing_required} missing from {c.source.system} drop; "
                                                 f"fields not in contract v{c.version}: {unexpected}")
        arrays = []
        schema = arrow_schema(c)
        for f in schema:
            if f.name not in fields:
                log("WARN", "ingest", f"optional column '{f.name}' absent; filled with nulls")
                arrays.append(pa.nulls(raw.num_rows, f.type))
                continue
            col = raw.column(f.name)
            try:
                arrays.append(col if col.type == f.type else pc.cast(col, f.type))
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
                sample = next((str(v) for v in col.to_pylist() if v is not None and not _castable(v, f.type)), "?")
                raise StepError("TypeCastError", f"column '{f.name}' ({col.type}) cannot be read as {c.columns[schema.get_field_index(f.name)].type}: "
                                                 f"value '{sample}'; {str(e).splitlines()[0]}") from e
        return pa.Table.from_arrays(arrays, schema=schema)

    def _transform(self, c: Contract, registry: Registry, dt: str, log: Log) -> pa.Table:
        sql = registry.sql.get(c.product)
        if sql is None:
            raise StepError("MissingTransform", f"no transform for {c.product}")
        con = connect()
        for i in c.inputs:
            register_view(con, self.lake, registry[i], until_dt=dt)
        rendered = render_sql(sql, dt)
        try:
            duck = sqlglot.transpile(rendered, read="athena", write="duckdb")[0]
        except sqlglot.errors.ParseError as e:
            raise StepError("SqlSyntaxError", str(e).splitlines()[0]) from e
        log("INFO", "transform", f"running {c.transform} over {', '.join(c.inputs)}")
        t0 = time.perf_counter()
        try:
            table = query_arrow(con, duck)
        except Exception as e:  # DuckDB raises its own error classes; the message is what matters
            msg = str(e).splitlines()[0]
            kind = msg.split(":", 1)[0].replace(" ", "") if ":" in msg else type(e).__name__
            raise StepError(kind or "QueryError", msg) from e
        log("INFO", "transform", f"query finished in {time.perf_counter() - t0:.1f}s, {table.num_rows} rows", seconds=12)
        schema = arrow_schema(c)
        missing = [f.name for f in schema if f.name not in table.column_names]
        if missing:
            raise StepError("ContractViolation", f"transform output lacks contract column(s) {missing}")
        try:
            return pa.Table.from_arrays([pc.cast(table.column(f.name), f.type) for f in schema], schema=schema)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
            raise StepError("TypeCastError", f"transform output does not match contract types: {str(e).splitlines()[0]}") from e

    def _record(self, run: Run, log: Log) -> Run:
        if run.status != "success":
            log("ERROR", "end", f"run {run.status}: {run.error_type}")
        else:
            log("INFO", "end", "run succeeded")
        self.state.add_run(run, log.lines)
        return run


def _castable(v: object, t: pa.DataType) -> bool:
    try:
        pc.cast(pa.array([v]), t)
        return True
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
        return False


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
