"""Lake Formation tag-based access control from config/access.yaml and the contracts.

The same objects the local query engine evaluates become LF-tags on databases, tables and columns, tag-policy
grants per principal, and data cells filters for row filters. ``plan_requests`` returns the API calls in
order; Terraform (infra/terraform/lakeformation.tf) manages the same resources declaratively from the tfvars
that ``mesh.aws.terraform`` renders. Tests check both against the evaluation the platform uses.
"""

from __future__ import annotations

from typing import Any

from mesh.access import AccessPolicy
from mesh.aws.glue import database_name
from mesh.contracts import Registry


def lf_tag_requests(policy: AccessPolicy) -> list[dict[str, Any]]:
    return [{"TagKey": k, "TagValues": list(v)} for k, v in policy.tags.items()]


def tag_assignments(registry: Registry) -> list[dict[str, Any]]:
    """add_lf_tags_to_resource requests: domain on each table, sensitivity on each column."""
    out = []
    for c in registry:
        db = database_name(c.domain)
        out.append(
            {
                "Resource": {"Table": {"DatabaseName": db, "Name": c.name}},
                "LFTags": [{"TagKey": "domain", "TagValues": [c.domain]}],
            }
        )
        by_cls: dict[str, list[str]] = {}
        for col in c.columns:
            by_cls.setdefault(col.classification, []).append(col.name)
        for cls, cols in sorted(by_cls.items()):
            out.append(
                {
                    "Resource": {"TableWithColumns": {"DatabaseName": db, "Name": c.name, "ColumnNames": cols}},
                    "LFTags": [{"TagKey": "sensitivity", "TagValues": [cls]}],
                }
            )
        # the partition column is readable by anyone who can read any column of the table
        out.append(
            {
                "Resource": {"TableWithColumns": {"DatabaseName": db, "Name": c.name, "ColumnNames": [c.partition]}},
                "LFTags": [{"TagKey": "sensitivity", "TagValues": ["public"]}],
            }
        )
    return out


def grant_entries(
    policy: AccessPolicy, registry: Registry, role_arn: dict[str, str], account_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(batch_grant_permissions entries, create_data_cells_filter requests)."""
    entries: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    for name, p in sorted(policy.principals.items()):
        arn = role_arn[name]
        for i, g in enumerate(p.grants):
            domains = list(g.domains) if g.domains is not None else list(policy.tags["domain"])
            sens = list(g.sensitivities) if g.sensitivities is not None else list(policy.tags["sensitivity"])
            if g.row_filter:
                # a row filter is a data cells filter per matching table that has the filtered columns
                for c in registry:
                    if c.domain not in domains:
                        continue
                    ta = policy.table_access(name, c)
                    if not ta.row_filter:
                        continue
                    cols = [col.name for col in c.columns if col.classification in sens]
                    fname = f"{name}-{c.domain}-{c.name}-{i}"[:255]
                    filters.append(
                        {
                            "TableData": {
                                "TableCatalogId": account_id,
                                "DatabaseName": database_name(c.domain),
                                "TableName": c.name,
                                "Name": fname,
                                "RowFilter": {"FilterExpression": g.row_filter},
                                "ColumnNames": [*cols, c.partition],
                            }
                        }
                    )
                    entries.append(
                        {
                            "Id": f"{name}-{i}-{c.domain}-{c.name}"[:255],
                            "Principal": {"DataLakePrincipalIdentifier": arn},
                            "Resource": {
                                "DataCellsFilter": {
                                    "TableCatalogId": account_id,
                                    "DatabaseName": database_name(c.domain),
                                    "TableName": c.name,
                                    "Name": fname,
                                }
                            },
                            "Permissions": ["SELECT"],
                        }
                    )
                continue
            entries.append(
                {
                    "Id": f"{name}-{i}"[:255],
                    "Principal": {"DataLakePrincipalIdentifier": arn},
                    "Resource": {
                        "LFTagPolicy": {
                            "ResourceType": "TABLE",
                            "Expression": [
                                {"TagKey": "domain", "TagValues": domains},
                                {"TagKey": "sensitivity", "TagValues": sens},
                            ],
                        }
                    },
                    "Permissions": ["SELECT", "DESCRIBE"],
                }
            )
    return entries, filters
