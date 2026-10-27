# 0002: Tag-based access, checked before a query runs

Status: accepted

## Context

There are dozens of consumer groups (analysts, domain teams, dealer success in one country, an external partner)
and hundreds of columns. Granting table by table does not scale. Granting broadly leaks personal data.

## Decision

* Lake Formation tag-based access control, emulated locally with the same semantics.
* Every table has a `domain` tag. Every column has a `sensitivity` tag (its contract classification).
* Grants are tag expressions, and they add up. A row filter applies only if every matching grant carries one.
* Access requests are time-limited grants that the owning team approves.
* The query engine checks a query against the grants before it runs. A hidden column the query names is refused.
  `*` expands to the readable columns.
* Row filters are applied through views.
* The same policy (`config/access.yaml`) renders LF-tags, grants and data cells filters for Terraform.

## Consequences

* A new column is protected from the moment its contract gives it a classification.
* Lowering a classification is a governance decision. Review routes it to approval.
* The local engine and Lake Formation must agree. That is tested at the level of generated grant entries; it has
  not been tested against a live account.
