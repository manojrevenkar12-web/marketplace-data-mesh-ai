"""Render the Terraform inputs from the contracts and the grants, so infrastructure follows the products.

``mesh infra render`` writes infra/terraform/products.auto.tfvars.json; Terraform creates the Glue tables,
LF-tag assignments, tag-policy grants, data cells filters and Athena workgroups from it with for_each. CI
fails when the committed file differs from what the contracts produce (``mesh infra render --check``).
"""

from __future__ import annotations

import json
from typing import Any

from mesh import config
from mesh.access import AccessPolicy
from mesh.aws.glue import GLUE_TYPES
from mesh.contracts import Registry


def render(registry: Registry, policy: AccessPolicy) -> dict[str, Any]:
    tables = {}
    for c in sorted(registry, key=lambda x: x.product):
        tables[c.product] = {
            "domain": c.domain,
            "name": c.name,
            "description": " ".join(c.description.split())[:2048],
            "owner": c.owner,
            "version": c.version,
            "partition": c.partition,
            "columns": [
                {
                    "name": col.name,
                    "type": GLUE_TYPES[col.type],
                    "comment": (col.description or "")[:255],
                    "sensitivity": col.classification,
                }
                for col in c.columns
            ],
        }
    grants, filters = [], []
    for name, p in sorted(policy.principals.items()):
        for i, g in enumerate(p.grants):
            domains = list(g.domains) if g.domains is not None else list(policy.tags["domain"])
            sens = list(g.sensitivities) if g.sensitivities is not None else list(policy.tags["sensitivity"])
            if g.row_filter:
                for c in sorted(registry, key=lambda x: x.product):
                    if c.domain in domains and policy.table_access(name, c).row_filter:
                        filters.append(
                            {
                                "key": f"{name}-{c.domain}-{c.name}-{i}",
                                "principal": name,
                                "domain": c.domain,
                                "table": c.name,
                                "filter": g.row_filter,
                                "columns": [col.name for col in c.columns if col.classification in sens] + [c.partition],
                            }
                        )
                continue
            grants.append({"key": f"{name}-{i}", "principal": name, "domains": domains, "sensitivities": sens})
    q = config.platform()["query"]
    return {
        "domains": list(policy.tags["domain"]),
        "lf_tags": policy.tags,
        "tables": tables,
        "principals": {n: {"description": p.description, "kind": p.kind} for n, p in sorted(policy.principals.items())},
        "tag_grants": grants,
        "cell_filters": filters,
        "athena_bytes_limit": {k: int(v) for k, v in q["bytes_limit"].items()},
    }


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
