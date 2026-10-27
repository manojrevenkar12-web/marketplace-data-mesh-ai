"""The lake: Parquet partitions per product, written to a staging area first and published after quality checks.

Locally the lake is a directory laid out like the S3 buckets (``<domain>/<product>/dt=YYYY-MM-DD/``), and
DuckDB reads it the way Athena reads the Glue tables. On AWS the same layout lives in one bucket per domain
and the publish step is a Glue partition registration after an S3 copy.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from mesh import config
from mesh.contracts import Contract

ARROW_TYPES: dict[str, pa.DataType] = {
    "string": pa.string(),
    "int": pa.int32(),
    "bigint": pa.int64(),
    "double": pa.float64(),
    "boolean": pa.bool_(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
}


class Lake:
    def __init__(self, root: Path | None = None):
        self.root = root or (config.data_dir() / "lake")
        self.staging_root = self.root / config.platform()["lake"]["staging"]

    def table_dir(self, product: str) -> Path:
        domain, name = product.split(".", 1)
        return self.root / domain / name

    def partition_dir(self, product: str, dt: str, staged: bool = False) -> Path:
        domain, name = product.split(".", 1)
        base = self.staging_root if staged else self.root
        return base / domain / name / f"dt={dt}"

    def partitions(self, product: str) -> list[str]:
        d = self.table_dir(product)
        if not d.exists():
            return []
        return sorted(p.name[3:] for p in d.iterdir() if p.name.startswith("dt=") and any(p.glob("*.parquet")))

    def has(self, product: str, dt: str) -> bool:
        return (self.partition_dir(product, dt) / "part-0.parquet").exists()

    def stage(self, product: str, dt: str, table: pa.Table) -> Path:
        d = self.partition_dir(product, dt, staged=True)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        path = d / "part-0.parquet"
        pq.write_table(table, path, compression="zstd")
        return path

    def publish(self, product: str, dt: str) -> None:
        src = self.partition_dir(product, dt, staged=True)
        dst = self.partition_dir(product, dt)
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

    def discard(self, product: str, dt: str) -> None:
        d = self.partition_dir(product, dt, staged=True)
        if d.exists():
            shutil.rmtree(d)

    def unpublish(self, product: str, dt: str) -> None:
        d = self.partition_dir(product, dt)
        if d.exists():
            shutil.rmtree(d)

    def staged_path(self, product: str, dt: str) -> Path:
        return self.partition_dir(product, dt, staged=True) / "part-0.parquet"

    def glob(self, product: str) -> str:
        return str(self.table_dir(product) / "dt=*" / "*.parquet")

    def partition_bytes(self, product: str) -> dict[str, dict[str, int]]:
        """dt -> column -> compressed bytes, from Parquet footers (what Athena would scan per column)."""
        out: dict[str, dict[str, int]] = {}
        for dt in self.partitions(product):
            meta = pq.ParquetFile(self.partition_dir(product, dt) / "part-0.parquet").metadata
            cols: dict[str, int] = {}
            for rg in range(meta.num_row_groups):
                g = meta.row_group(rg)
                for i in range(g.num_columns):
                    c = g.column(i)
                    cols[c.path_in_schema] = cols.get(c.path_in_schema, 0) + c.total_compressed_size
            out[dt] = cols
        return out

    def row_count(self, product: str, dt: str, staged: bool = False) -> int:
        path = self.partition_dir(product, dt, staged) / "part-0.parquet"
        return pq.ParquetFile(path).metadata.num_rows if path.exists() else 0


def arrow_schema(c: Contract) -> pa.Schema:
    return pa.schema([pa.field(col.name, ARROW_TYPES[col.type]) for col in c.columns])


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("SET threads TO 2")
    con.execute("SET TimeZone = 'UTC'")
    return con


def register_view(con: duckdb.DuckDBPyConnection, lake: Lake, c: Contract, columns: list[str] | None = None,
                  row_filter: str | None = None, until_dt: str | None = None) -> None:
    """A view named ``<domain>.<product>`` over the published partitions, restricted to some columns and rows."""
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {c.domain}")
    cols = columns or [*c.column_names, c.partition]
    select = ", ".join(_q(x) for x in cols)
    if lake.partitions(c.product):
        src = f"read_parquet('{lake.glob(c.product)}', hive_partitioning = true, hive_types = {{'{c.partition}': DATE}}, union_by_name = true)"
    else:  # no data yet: an empty relation with the contract's schema
        from mesh.lineage import SQL_TYPES

        typed = ", ".join(f"CAST(NULL AS {SQL_TYPES[t]}) AS {_q(n)}" for n, t in c.schema().items())
        src = f"(SELECT {typed} WHERE FALSE)"
    where = []
    if row_filter:
        where.append(f"({row_filter})")
    if until_dt:
        where.append(f"{c.partition} <= DATE '{until_dt}'")
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    con.execute(f"CREATE OR REPLACE VIEW {c.domain}.{_q(c.name)} AS SELECT {select} FROM {src}{clause}")


def query_arrow(con: duckdb.DuckDBPyConnection, sql: str) -> pa.Table:
    """Run a query and return an Arrow table (DuckDB 2 calls it to_arrow_table, 1.x calls it arrow)."""
    rel = con.sql(sql)
    fetch = getattr(rel, "to_arrow_table", None) or rel.arrow
    return fetch()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
