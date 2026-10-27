"""Quality checks from the contract, run on a staged partition before it is published (write-audit-publish).

A failed ``error`` check keeps the partition out of the lake, so consumers never read it; a failed ``warn``
check publishes it and records the warning. Volume is compared with the median of the previous published
partitions, so a tracking outage that halves the leads stops at the door.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import duckdb

from mesh import config
from mesh.contracts import Contract, QualityCheck
from mesh.lake import Lake, _q
from mesh.state import QualityResult


def run_checks(
    c: Contract, dt: str, staged: Path, lake: Lake, previous_counts: list[int], con: duckdb.DuckDBPyConnection | None = None
) -> list[QualityResult]:
    con = con or duckdb.connect()
    src = f"read_parquet('{staged}')"
    n = int(con.execute(f"SELECT count(*) FROM {src}").fetchone()[0])  # type: ignore[index]
    out: list[QualityResult] = []
    for q in c.quality:
        passed, observed, detail = _check(con, q, src, n, c, lake, previous_counts)
        out.append(QualityResult(c.product, dt, q.id, q.severity, passed, observed, detail))
    return out


def _check(
    con: duckdb.DuckDBPyConnection, q: QualityCheck, src: str, n: int, c: Contract, lake: Lake, previous_counts: list[int]
) -> tuple[bool, float | int | str | None, str]:
    col = _q(q.column) if q.column else ""
    if q.check == "not_null":
        bad = _one(con, f"SELECT count(*) FROM {src} WHERE {col} IS NULL")
        return bad == 0, bad, f"{bad} of {n} rows have no {q.column}"
    if q.check == "unique":
        dup = _one(con, f"SELECT count(*) - count(DISTINCT {col}) FROM {src}")
        return dup == 0, dup, f"{dup} duplicate {q.column} values"
    if q.check == "accepted_values":
        values = ", ".join("'" + v.replace("'", "''") + "'" for v in (q.values or []))
        bad = _one(con, f"SELECT count(*) FROM {src} WHERE {col} IS NOT NULL AND {col} NOT IN ({values})")
        sample = con.execute(
            f"SELECT DISTINCT {col} FROM {src} WHERE {col} IS NOT NULL AND {col} NOT IN ({values}) LIMIT 3"
        ).fetchall()
        return bad == 0, bad, f"{bad} rows outside {q.values}" + (f", e.g. {[s[0] for s in sample]}" if sample else "")
    if q.check == "range":
        cond = []
        if q.min is not None:
            cond.append(f"{col} < {q.min}")
        if q.max is not None:
            cond.append(f"{col} > {q.max}")
        bad = _one(con, f"SELECT count(*) FROM {src} WHERE {' OR '.join(cond)}")
        return bad == 0, bad, f"{bad} rows outside [{q.min}, {q.max}]"
    if q.check == "null_share":
        share = (_one(con, f"SELECT count(*) FROM {src} WHERE {col} IS NULL") / n) if n else 0.0
        return share <= (q.max or 0), round(share, 4), f"{share:.1%} of rows have no {q.column} (max {q.max:.0%})"
    if q.check == "volume":
        days = config.platform()["quality"]["volume_baseline_days"]
        recent = [x for x in previous_counts[-days:] if x > 0]
        if len(recent) < 3:
            return True, n, f"{n} rows; not enough history for a baseline"
        base = statistics.median(recent)
        ratio = n / base if base else 1.0
        ok = (q.min_ratio or 0) <= ratio <= (q.max_ratio or 1e9)
        return ok, round(ratio, 3), f"{n} rows, {ratio:.2f}x the median of the last {len(recent)} partitions ({base:.0f})"
    if q.check == "referential" and q.ref:
        ref_product, ref_col = q.ref.rsplit(".", 1)
        if not lake.partitions(ref_product):
            return True, 0, f"no published {ref_product} to check against"
        bad = _one(
            con,
            f"SELECT count(*) FROM {src} s WHERE s.{col} IS NOT NULL AND s.{col} NOT IN "
            f"(SELECT {_q(ref_col)} FROM read_parquet('{lake.glob(ref_product)}'))",
        )
        return bad == 0, bad, f"{bad} rows reference an unknown {q.ref}"
    return True, None, "not evaluated"


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0
