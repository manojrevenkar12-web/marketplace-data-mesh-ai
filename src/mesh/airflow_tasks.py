"""What the generated DAG's tasks call on the Airflow workers: run one product for one day with the platform's
runner, publish run metrics (CloudWatch embedded metric format), and fail the task when the run did not
publish, so Airflow's retries and alerting apply."""

from __future__ import annotations

from mesh import config
from mesh.contracts import Registry
from mesh.history import Versions
from mesh.lake import Lake
from mesh.obs import metrics
from mesh.pipeline import Runner
from mesh.state import State


class RunFailed(RuntimeError):
    pass


def run_product(product: str, ds: str) -> str:
    data = config.data_dir()
    state = State(data / "state")
    versions = Versions(state, Registry.load())
    versions.bootstrap(ds)
    runner = Runner(Lake(data / "lake"), state, versions, data / "landing", retries=0)  # Airflow retries the task
    run = runner.run_product(product, ds, versions.registry_at(ds))
    metrics.emit({"product": product, "status": run.status}, RunsFailed=0 if run.ok else 1, Rows=run.rows)
    if not run.ok:
        raise RunFailed(f"{run.id} {run.status}: {run.error_type}: {run.error}")
    return run.id
