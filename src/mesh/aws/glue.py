"""Glue Data Catalog tables from contracts: one database per domain, one table per product, Parquet in the
domain's bucket, partitioned by dt. Partitions are registered after a partition passes its quality checks
(the publish step of write-audit-publish)."""

from __future__ import annotations

from typing import Any

from mesh.contracts import Contract

GLUE_TYPES = {
    "string": "string",
    "int": "int",
    "bigint": "bigint",
    "double": "double",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp",
}


def database_name(domain: str) -> str:
    return f"mesh_{domain}"


def bucket(prefix: str, domain: str) -> str:
    return f"{prefix}-{domain}"


def location(prefix: str, c: Contract) -> str:
    return f"s3://{bucket(prefix, c.domain)}/{c.name}/"


def table_input(c: Contract, prefix: str) -> dict[str, Any]:
    """TableInput for glue.create_table / update_table."""
    return {
        "Name": c.name,
        "Description": " ".join(c.description.split())[:2048],
        "Owner": c.owner,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "classification": "parquet",
            "mesh.contract_version": str(c.version),
            "mesh.owner": c.owner,
            "parquet.compression": "ZSTD",
            "projection.enabled": "false",
        },
        "PartitionKeys": [{"Name": c.partition, "Type": "date", "Comment": "partition: the business day"}],
        "StorageDescriptor": {
            "Columns": [
                {
                    "Name": col.name,
                    "Type": GLUE_TYPES[col.type],
                    "Comment": (col.description or "")[:255],
                    "Parameters": {"mesh.classification": col.classification},
                }
                for col in c.columns
            ],
            "Location": location(prefix, c),
            "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
            "SerdeInfo": {"SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"},
            "Compressed": True,
        },
    }


def create_table_request(c: Contract, prefix: str, catalog_id: str | None = None) -> dict[str, Any]:
    req: dict[str, Any] = {"DatabaseName": database_name(c.domain), "TableInput": table_input(c, prefix)}
    if catalog_id:
        req["CatalogId"] = catalog_id
    return req


def publish_partition_request(c: Contract, prefix: str, dt: str) -> dict[str, Any]:
    sd = table_input(c, prefix)["StorageDescriptor"]
    return {
        "DatabaseName": database_name(c.domain),
        "TableName": c.name,
        "PartitionInputList": [
            {"Values": [dt], "StorageDescriptor": {**sd, "Location": f"{location(prefix, c)}{c.partition}={dt}/"}}
        ],
    }
