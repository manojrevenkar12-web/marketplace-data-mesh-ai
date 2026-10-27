"""Freshness against each product's SLA, as the daily monitor (and the ProductsLate alarm) sees it."""

from __future__ import annotations

from typing import Any

from mesh.ai.catalog import Catalog
from mesh.obs import metrics


def freshness(catalog: Catalog) -> list[dict[str, Any]]:
    rows = []
    for e in catalog.entries("platform-pipelines"):
        rows.append(
            {"product": e.product, "owner": e.owner, "latest_dt": e.latest_dt, "freshness": e.freshness, "quality": e.quality}
        )
    late = [r for r in rows if r["freshness"] != "fresh"]
    metrics.emit({"check": "freshness"}, ProductsLate=len(late))
    return rows
