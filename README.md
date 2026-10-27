# marketplace-data-mesh-ai

A self-service data platform for a car marketplace, built as a data mesh on AWS:

* Domain teams publish data products under contracts.
* The platform builds, checks, secures and documents those products.
* AI is built into finding data, asking questions of it, diagnosing failed runs and reviewing changes. It is also
  built into the engineers' own tools: a Claude Code hook, skills and an MCP server.

It runs locally against a simulated marketplace. The AWS stack (S3, Glue, Athena, Lake Formation, MWAA) is
generated from the contracts and validated, but it has never been applied to an account. The numbers below come
from that simulation and a simulated model.

## What it does

| | How | Measured (`mesh eval`, simulated world, 17 live incidents) |
| --- | --- | --- |
| Bad data never published | write-audit-publish: staging, contract quality checks, then publish | 21 bad partitions stopped, **0 published** |
| Failed runs go to the team that can fix them | evidence → rules → classifier → model explanation with verified citations | right team **100%** vs **56.4%** for a plain alert to the failed job's owner; right cause 100% |
| Breaking changes stopped before merge | contract diff, column lineage of every consumer, dry run on the last 3 partitions | harmful blocked 100%, safe blocked 0; the changes behind the simulated incidents: **9 of 9 blocked** |
| Questions in plain words, English or German | model drafts SQL, the query engine checks it against the person's grants, rows never go to the model | right 100% in-sample; **0 rows shown that the person may not see**, including with a misbehaving model and planted instructions |
| Dataset search | BM25 over contracts with a German/English glossary | right product in top 3: 100%, first: 75% |
| Cost | Athena pricing on bytes scanned; per-purpose limits | $0.0018 of model per question |

**Blind held-out sets** were written from a guide by someone who had not seen the code, and run once
([heldout/README.md](heldout/README.md)):

* Questions: answered right 47.8%, refused correctly 57.1%, **0 leaks**.
* Changes: 100% as expected, 0 safe changes blocked.
* Incidents: right team 79.2% vs 58.3% for a plain alert; right cause 58.3%.

The gap between in-sample and blind is the honest estimate.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/mesh run --small          # simulate, train, evaluate, gate (under a minute); `mesh run` for the full world
.venv/bin/mesh ask --user anna.analyst "Wie viele Anfragen gab es gestern in Deutschland?"
.venv/bin/mesh failures && .venv/bin/mesh diagnose <run id>
.venv/bin/pytest -q -m "not db"     # add MESH_TEST_DATABASE_URL for the API tests
```

The console needs PostgreSQL:

```bash
export MESH_DATABASE_URL=postgresql://mesh:mesh@localhost:5432/mesh   # a role without SUPERUSER
.venv/bin/mesh demo                  # seeds demo keys and starts the API on :8080
cd web && npm ci && npm run dev      # http://localhost:5173, sign in as one of the demo people
```

## The console

* **Catalog.** A transit map of the mesh: each domain a line, each product a station, lineage the track. Below it,
  search, and what is fresh, late or failing today.
* **Dataset.** The columns you may see, how many are hidden, your row filter, lineage, runs and quality checks,
  and a form to request access.
* **Ask.** The answer, the SQL, the datasets and days it came from. A refusal names who can grant access.
* **Failed runs.** The diagnosis next to who a plain alert would have paged, with the cited evidence marked.
* **Review a change.** Edit a contract or transform and see the verdict, the consumers it breaks and who must
  approve.
* **Access.** Requests to your team, and your own requests. Decisions are final and audited.
* **Evaluation.** The numbers above, including the blind first runs.

## AI in engineering work

* `.claude/settings.json`: after Claude Code edits `products/`, a hook lints the contract and runs the review. It
  sends the blocking findings back, so they are fixed in the same session.
* `.claude/skills/`: new-data-product, change-a-contract, investigate-a-failed-run.
* `.mcp.json`: `mesh mcp`, with 9 tools (search, describe, query, ask, check a contract, review a change, failed
  runs, diagnose, lineage). Every tool runs under the configured person's grants.
* CI posts `mesh review --base origin/main` on every pull request that touches `products/`.

## Where things are

| Path | What |
| --- | --- |
| `products/` | the contracts and transforms (listings, dealers, leads, pricing) |
| `config/` | access policy, platform limits, glossary, triage routing, review rules, release gate, models |
| `src/mesh/` | contracts, lineage, access, query engine, lake, pipeline, DAG and Terraform rendering, CLI |
| `src/mesh/ai/` | catalog, asker, guard, triage, reviewer |
| `src/mesh/llm/` | model router (Bedrock, Anthropic API, simulated), versioned prompts, schemas |
| `src/mesh/aws/` | Glue, Lake Formation and Athena request builders |
| `src/mesh/service/` | FastAPI and PostgreSQL: keys, access requests, feedback, audit |
| `src/mesh/sim/`, `src/mesh/eval/` | the simulated marketplace, the evaluation, the gate, held-out runs |
| `infra/terraform/` | the AWS stack; `products.auto.tfvars.json` is generated |
| `dags/mesh_daily.py` | the generated Airflow DAG |
| `web/` | the console (React, TanStack Query, Framer Motion) |
| `docs/` | [design](docs/DESIGN.md), [decisions](docs/adr/), [runbook](docs/RUNBOOK.md) |

## Limits

* The data and the model are simulated. A real model on real logs has not been measured.
* Terraform validates, but it has never been applied. There is no AWS account behind this repository.
* The pipeline runner reads and writes the local lake. The S3/Athena adapter that MWAA would use is not built.
* Sign-in uses API keys. Single sign-on is not built.
