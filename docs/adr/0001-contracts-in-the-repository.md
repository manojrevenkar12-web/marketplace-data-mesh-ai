# 0001: Data contracts live in the repository and generate everything else

Status: accepted

## Context

A data product needs a schema, an owner, a freshness promise, quality checks, column sensitivities and its inputs.
Keeping these in a catalog UI, in Glue and in Airflow code separately means they drift. Nobody notices the drift
until a consumer breaks.

## Decision

* One YAML contract per product (`products/<domain>/<name>.yaml`), plus a SQL transform for derived products.
* Contracts are versioned by effective date.
* The Airflow DAG, the Terraform inputs for Glue and Lake Formation, and the catalog are generated from the
  contracts.
* Generated files are committed, and CI fails when they differ from what the contracts produce.
* Changes go through review like code. The reviewer reads the diff and the column lineage of every consumer.

## Consequences

* A rename or type change is visible in a pull request, with the consumers it breaks named.
* Domain teams need to edit YAML and SQL. The new-data-product skill and the Claude Code hook make that quicker,
  and they check it as they go.
* The catalog cannot say more than the contracts do. Thin descriptions mean thin search results.
