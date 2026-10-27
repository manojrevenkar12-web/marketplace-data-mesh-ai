# 0005: A local lake, shaped like the AWS one

Status: accepted

## Context

The target is S3, Glue, Athena, Lake Formation and MWAA. Developing and testing against an AWS account is slow and
costs money, and it makes the evaluation hard to reproduce.

## Decision

* The lake is Parquet partitions on disk, read through DuckDB views. The query engine uses Athena's dialect
  (sqlglot) and prices scans at Athena's $5 per TB.
* AWS calls (Glue tables, Lake Formation grants and filters, Athena queries) are built by request builders, and
  tested with botocore's Stubber against the real API models.
* Terraform for the whole stack passes `terraform validate` in CI. It covers S3, KMS, Glue, Lake Formation, IAM,
  Athena workgroups, VPC, MWAA, ECS Fargate for the API, RDS and CloudWatch.

## Consequences

* Everything runs on a laptop and in CI in minutes, and every number is reproducible.
* The S3/Athena adapter for the pipeline runner is not built. Neither the stack nor the grants have been applied
  to a real account. These are the first things to do with one.
