# Working on this repository

## Fast feedback

| Command | What | Time |
| --- | --- | --- |
| `.venv/bin/pytest -q -m "not db"` | unit and pipeline tests (builds a small world once) | about a minute |
| `MESH_TEST_DATABASE_URL=postgresql://mesh:mesh@localhost:5432/mesh_test .venv/bin/pytest -q` | adds the API over PostgreSQL | |
| `.venv/bin/ruff check src tests && .venv/bin/mypy` | lint and types | seconds |
| `.venv/bin/mesh lint` | every contract against the platform rules | seconds |
| `.venv/bin/mesh review --base origin/main` | the review CI posts on your pull request | seconds |
| `.venv/bin/mesh dags render --check && .venv/bin/mesh infra render --check` | generated files match the contracts | seconds |
| `.venv/bin/mesh run` | full world, train, eval, release gate | minutes |
| `cd web && npm test && npm run build` | console tests and build | seconds |
| `cd web && npx playwright test` | console end to end (needs the API, a world and `mesh demo --seed-only`) | |

## Invariants (each has a test or a gate check)

* **Truth isolation.** Only `mesh.sim` and `mesh.eval` read `data/truth` or `config/world.yaml`. The platform,
  AI features, service and MCP server never do (`tests/test_isolation.py`).
* **Contracts generate the rest.** Never hand-edit `dags/mesh_daily.py` or
  `infra/terraform/products.auto.tfvars.json`. Change the contract or `config/access.yaml` and re-render. CI runs
  `--check`.
* **Access is decided by the query engine,** before a query runs. A hidden column is refused, never silently
  dropped. Rows never go to a model (`tests/test_ask.py`, `tests/test_query.py`).
* **Models read; rules decide.** No verdict, category, route or grant comes from a model alone (ADR 0003). New AI
  features are evaluated with the faulty and down model conditions.
* **Write-audit-publish.** Nothing is published before its blocking checks pass. Never weaken a check to publish
  a day.
* **Settings live in `config/`,** not in code: limits, thresholds, routing, gate.
* **Prompts are versioned.** Change a prompt's text, bump its version, run `mesh eval`, then `mesh prompts lock`.
  The gate fails on an unlocked prompt.
* **The API's database role is not a superuser** (the service refuses to start as one). The audit table is
  append-only.

## Held-out sets

* `heldout/<set>/` are blind sets. Their first runs are in `reports/heldout/<set>/first_run.json` and are never
  rewritten.
* Never change code to pass a set.
* If you study a set's failures, write `regression` to its `STATUS` file. Then have someone who has not seen the
  code write a new set from the `heldout/WRITING_*.md` guide only.
* The gate fails if a set's files change after its first run.

## Where settings live

* `config/access.yaml`: principals, grants, users.
* `config/platform.yaml`: query limits and freshness.
* `config/triage.yaml`: categories, who acts, runbook.
* `config/review.yaml`: review rules.
* `config/models.yaml`: tasks, model routes, data policies.
* `config/release.yaml`: gate thresholds.
* `config/glossary.yaml`: business terms in English and German.
