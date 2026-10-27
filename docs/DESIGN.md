# Design

## The problem

A car marketplace has four domain teams (listings, dealers, leads, pricing), and each owns data that the others
need. A central data team cannot keep up with every request. Every domain building its own pipelines without
shared rules is no better: columns are renamed without warning, personal data turns up in places nobody approved,
and a failed job pages the wrong team.

This platform is the shared part of a data mesh. Domains publish **data products** under a contract. The
platform builds, checks, publishes, secures and documents them. AI sits where people spend time:

* finding data;
* asking questions of it;
* working out why a run failed;
* reviewing a change before it breaks a consumer.

## Pieces

```
products/<domain>/<name>.yaml (+ .sql)        the contract and transform, owned by the domain team
        │
        ├── mesh lint / review ──────────────── rules over contracts, lineage and grants (CI and Claude Code hook)
        ├── mesh dags render ─────────────────── dags/mesh_daily.py (Airflow on MWAA)
        ├── mesh infra render ────────────────── infra/terraform/products.auto.tfvars.json (Glue, Lake Formation)
        │
landing/ ─► ingest ─► staging ─► quality ─► publish (lake/<domain>/<name>/dt=…)   write-audit-publish
                                     │
                                     └─ fail: quarantined, consumers keep the last good day
        │
QueryEngine (one read-only SELECT, grants, row filters, partition pruning, bytes limits)
        │
        ├── Catalog (BM25 over descriptions, columns and a German/English glossary; restricted hits named)
        ├── Asker (model drafts SQL, engine checks it, rows never go to the model)
        ├── Triage (evidence, rules, classifier, model explanation with verified citations)
        ├── Reviewer (diff, consumers via column lineage, dry run on recent partitions, approvals)
        │
        ├── FastAPI service + PostgreSQL (keys, access requests, feedback, append-only audit)
        ├── console (React)                    ├── MCP server (9 tools for coding assistants)
        └── Claude Code hook and skills
```

## Decisions that matter

**Contracts are the source of truth.** Everything is generated from them: the Airflow DAG, the Glue tables and
Lake Formation tags, the catalog, and the review's idea of what a consumer reads. Generated files are checked in,
and CI fails if they drift (`--check`). See [ADR 0001](adr/0001-contracts-in-the-repository.md).

**Access is tag-based and checked before a query runs.**

* Each table carries its domain tag.
* Each column carries a sensitivity tag (public, internal, confidential, pii).
* Grants are tag expressions, and they add up. A row filter applies only if every grant that matches is filtered.

Locally the query engine enforces this. On AWS, the same policy renders Lake Formation LF-tags, grants and data
cells filters. The engine refuses a query that names a hidden column, instead of returning a result with the
column dropped. See [ADR 0002](adr/0002-tag-based-access.md).

**Models read; rules and the engine decide.**

* The asker's model writes SQL, but the query engine decides whether it runs. Rows never go back to the model.
* The triage's rules decide the category and the team when they match. The model writes the explanation, and a
  citation that is not in the evidence is removed.
* The reviewer's verdict comes from the diff and the lineage. The model only writes the comment, and a comment
  that names a column or product that does not exist is dropped.

A model that misbehaves, or is down, changes the wording, not the outcome. The evaluation runs every feature with
a misbehaving model and with no model. See [ADR 0003](adr/0003-models-read-rules-decide.md).

**Write-audit-publish.** A run writes to staging, checks quality there, and publishes only if every blocking check
passes. A bad day is quarantined, and consumers keep reading the last good partition. See
[ADR 0004](adr/0004-write-audit-publish.md).

**Local first, AWS-shaped.**

* The lake is Parquet on disk with DuckDB views.
* Athena, Glue and Lake Formation are reached through request builders. Those are tested with botocore's Stubber
  against the real API models.
* The Terraform stack validates.

None of it has been applied to an AWS account. See [ADR 0005](adr/0005-local-lake-aws-shaped.md).

**Truth stays in the simulator.** `mesh.sim` writes what really happened (`data/truth`). Only `mesh.sim` and
`mesh.eval` may read it, and `tests/test_isolation.py` enforces that. Held-out sets are written blind and run
once. See [ADR 0006](adr/0006-evaluation-and-held-out-sets.md).

## Routing a failure to the team that acts

A failed run belongs to a product, but the cause is often somewhere else:

* a source system that changed a field;
* an upstream contract change;
* a lost permission.

The triage collects evidence first:

* the run record and its log;
* upstream runs on the same day;
* contract and transform versions published in the last 7 days (only versions published before the run);
* quality results.

Rules map the evidence to a category, and `config/triage.yaml` maps each category to who acts:

* the source system's owner;
* the product's owner;
* the upstream product's owner;
* the platform team.

When no rule matches, a classifier trained on resolved on-call tickets proposes a category. Below 0.55 the
diagnosis says "uncertain" instead of guessing.

## Where AI runs in engineering work

Engineers change contracts in their editor, often with a coding assistant. The platform meets them there:

* **Claude Code hook** (`.claude/settings.json`). After an edit to `products/`, it lints the contract and runs
  the review without a model. Blocking findings go back to the assistant (exit 2) in the same session.
* **Skills** (`.claude/skills`): new-data-product, change-a-contract and investigate-a-failed-run. Each one
  states the platform's rules and the commands to check them.
* **MCP server** (`mesh mcp`). Search, describe, query, ask, check a contract, review a change, failed runs,
  diagnose, lineage. Every tool runs as the configured user, under their grants.
* **Pull requests.** CI runs `mesh review --base origin/main` and posts the review as a comment.

## What is not here

* The lake adapter for S3/Athena in the pipeline runner. MWAA would run the same steps against S3, but only the
  local lake is implemented.
* A real model in the evaluation. The model layer supports Bedrock and the Anthropic API. The numbers come from
  a simulated model, which is deterministic and free.
* Single sign-on. The service uses API keys that are hashed in PostgreSQL. The console's demo keys are seeded by
  `mesh demo`.
