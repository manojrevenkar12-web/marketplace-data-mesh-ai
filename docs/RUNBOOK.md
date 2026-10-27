# Runbook

Every CloudWatch alarm and every triage category links to a section here. A section says what you see, what to
check first, and what to do. Commands are the platform's CLI. Run them from a checkout with the platform's
read role. In production, `mesh` talks to Athena and Glue instead of the local lake.

## failed-run

**Alarm:** `<name>-pipeline-runs-failed`. A data product run failed, or its partition was quarantined.

1. Open the run in the console (Failed runs), or run `mesh diagnose <run id>`. The diagnosis gives the cause, the
   team that has to act, and the evidence it was decided on.
2. If the diagnosis says another team has to act, page that team, not the product's owner. A plain "job failed"
   alert goes to the failed product's owner. In the evaluation that was the wrong team 44% of the time: most such
   failures start upstream or in the platform.
3. If the run was `upstream_failed`, look at its root run. Fixing the root re-runs the day for every consumer
   (the DAG's sensors wait on the upstream's success marker).
4. If the decision was `uncertain`, a person has to read the evidence. Tag the ticket with the category you find.
   The classifier is retrained on resolved tickets (`mesh train`).

Nothing is published while a run is failing. Consumers keep reading the last good partition. Freshness says so.

## late-product

**Alarm:** `<name>-products-late`. A product missed its freshness SLA (`sla.freshness_hours` in its contract).

1. Run `mesh freshness` to see which products are late and since when.
2. If the product's own run failed, go to [failed-run](#failed-run).
3. If no run started, the upstream is late. Follow `upstream` in `mesh freshness` to the first late product.
4. If a source product is late with no failed run, the source system did not deliver. See [late-drop](#late-drop).

## late-drop

The source system's file for the day did not arrive (`LandingFileNotFound`). The source system's team acts.

1. Ask the source team when the drop will land. The contract's `source.system` names the system, and
   `owner_contact` names the channel.
2. When it lands, re-run the day. Its consumers follow on their own.
3. If it will not arrive, agree with the consumers whether to publish an empty day. That is a contract decision:
   record it in the product's change log.

## schema-drift

The source sent a field under a new name, or dropped one (`ContractViolation: unknown column` or
`missing required column`). The source system's team acts.

1. The run's log names the field. Compare it with the contract in `products/<domain>/<name>.yaml`.
2. Ask the source team to restore the field and redeliver the day.
3. If the change is intended, it needs a contract change through review (the change-a-contract skill): add the new
   column, deprecate the old one, and let consumers migrate within `deprecation_days`.

## type-drift

The source sent values of the wrong type (`TypeCastError`). The source system's team acts. Ask them to fix the
export and redeliver. Never loosen the contract's type to let a bad day through.

## quality

A blocking quality check failed (`QualityCheckFailed`), and the partition is quarantined in staging. Typical
causes: a null spike, duplicates, or a volume drop. The product's owner acts.

1. The run's quality results name the check and the observed value (Dataset page, Quality checks).
2. Null spike: usually a tracking or producer bug. Find it, redeliver, re-run.
3. Duplicates: usually a replay at the source. Deduplicate at the source, not in the transform.
4. Volume drop: check for a tracking outage before publishing anything. A real drop in demand looks gradual. A
   tracking outage looks like a cliff.
5. Do not lower a threshold to publish a bad day. Threshold changes go through review like any contract change.

## contract-break

An input's contract changed in a way this product cannot read (`upstream_contract_change`). The upstream team
acts.

1. The diagnosis cites the contract version that changed and when it was published.
2. The upstream team reverts, or re-publishes the column as deprecated. Then the consumers migrate.
3. Review blocks such changes before merge. If one got through, find out how: a direct push, or a review run
   with `--no-model` and an override.

## code-change

The product's own transform changed just before the failure. The product's owner acts. Revert the change, or fix
it. Review runs the new SQL on the last 3 partitions, and its dry-run error is usually the same one the run hit.

## access-denied

The pipeline role lost a Lake Formation or S3 permission (`AccessDenied`). The platform team acts.

1. Look at the last Terraform apply in `infra/terraform` (CI keeps the plan).
2. Run `mesh infra render --check`. If the grants drifted from the policy in `config/access.yaml`, re-apply.
3. Lake Formation tags are additive, so removing a tag from a column can hide it from the pipeline role too.

## resources

The worker ran out of memory (`ResourceExhausted`). The platform team acts. Re-run on a larger worker. Then read the query plan. A transform that scans every partition usually
has a missing `dt` filter.

## transient

AWS throttled or failed briefly beyond the retries (`ServiceError`, marked retryable). Re-run. If it repeats on the same day,
raise the task's retries or spread the schedule. Do not raise them platform-wide.

## assistant-rejections

**Alarm:** `<name>-assistant-rejected-queries`. Many assistant queries were refused for asking beyond the user's
grants.

This is the signature of instructions planted in metadata: a column or dataset description that tells the
assistant to add a restricted column. The query engine refuses those queries, so nothing leaks. But the
description is still poisoning answers.

1. The catalog removes instruction-like sentences from descriptions before a model sees them, and review blocks new
   ones. A sentence that got past both is the likely cause.
2. In the evaluation, one planted sentence is not caught by the guard; it is kept in the test world on purpose.
   Look for descriptions that mention columns the dataset's readers cannot see.
3. Fix the description through review, and add the phrasing to the guard's patterns (`mesh.ai.guard`) with a test.

## cost

**Alarm:** `<name>-athena-<purpose>-scanned`. A workgroup scanned more than usual.

1. Each purpose (person, assistant, service) has a per-query bytes limit in `config/platform.yaml`. The same
   limit is the workgroup's `bytes_scanned_cutoff_per_query`, so a single query cannot exceed it.
2. Many queries just under the limit point to a dashboard or a loop. Find the principal in CloudTrail or in the
   API's audit table.
3. Queries over more than `require_partition_filter_over` partitions without a `dt` filter are rejected before
   they run.

## terraform

Terraform in `infra/terraform` is generated in part: `products.auto.tfvars.json` comes from the contracts and
grants.

1. Never edit `products.auto.tfvars.json` by hand. Change the contract or `config/access.yaml`, then run
   `mesh infra render`.
2. CI runs `terraform fmt -check` and `terraform validate`. The stack has been validated, not applied: this
   repository has never run against an AWS account.
