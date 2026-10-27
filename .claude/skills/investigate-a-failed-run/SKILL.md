---
name: investigate-a-failed-run
description: Find out why a data pipeline run failed and who has to act; use when an Airflow/MWAA task fails or a dataset is late.
---

# Investigate a failed run

1. Call `failed_runs` (optionally with the product or day) and pick the run.
2. Call `diagnose_run`. It returns the cause, the cited evidence, the team that has to act and the next steps.
   Read the evidence it cites; do not repeat a conclusion the evidence does not show.
3. If the cause is upstream (`upstream_contract_change`, `late_drop`, `schema_drift`, `type_drift`), tell the
   team in `route_to` with the evidence lines. Do not patch your own transform around another team's change.
4. If it is your own change (`code_change`), revert or fix it and run `review_change` on the fix; the dry run
   must pass on the recent partitions.
5. If it is the platform (`access_denied`, `resource_exhausted`, `transient_infra`), follow the runbook step
   and tell #data-platform.
6. When `decided_by` is `uncertain`, say so: describe what the log shows and what you checked, and ask.
