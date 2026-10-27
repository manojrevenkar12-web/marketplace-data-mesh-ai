# 0004: Write, audit, then publish

Status: accepted

## Context

Most damage from bad data happens after it is published: dashboards, models and partner reports read it before
anyone notices.

## Decision

* Every run writes its partition to staging and runs the contract's quality checks there.
* It publishes only when every blocking check passes.
* A failed day stays in staging, quarantined. Consumers keep reading the last good partition, and freshness
  reports the product as late.
* Volume checks compare against the last published days, not against staged ones.

## Consequences

* In the simulation, 21 bad partitions were stopped and 0 were published.
* A late product is visible and explained. A wrong product is not published at all.
* A blocking check that is too strict delays data. Thresholds are part of the contract and change through review.
