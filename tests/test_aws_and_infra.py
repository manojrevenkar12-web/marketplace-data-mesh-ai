"""AWS requests are validated against the botocore service models; Terraform inputs, Lake Formation grants and
the DAG are generated from the same contracts and grants the platform evaluates locally."""

from __future__ import annotations

import json
from pathlib import Path

import boto3
import hcl2
import pytest
from botocore.stub import ANY, Stubber

from mesh import config
from mesh.access import AccessPolicy
from mesh.aws import glue, lakeformation
from mesh.aws.athena import AthenaEngine
from mesh.aws.terraform import dumps, render
from mesh.contracts import Registry
from mesh.dags import render as render_dag
from mesh.query import QueryRejected

ROOT = Path(__file__).resolve().parents[1]
KW = {"region_name": "eu-central-1", "aws_access_key_id": "x", "aws_secret_access_key": "y"}


def test_glue_requests_match_the_service_model():
    reg = Registry.load()
    client = boto3.client("glue", **KW)
    with Stubber(client) as stub:
        for c in reg:
            req = glue.create_table_request(c, "mesh-lake")
            stub.add_response("create_table", {}, req)
            client.create_table(**req)
        part = glue.publish_partition_request(reg["leads.leads"], "mesh-lake", "2026-09-01")
        stub.add_response("batch_create_partition", {"Errors": []}, part)
        client.batch_create_partition(**part)
    cols = glue.table_input(reg["leads.leads"], "mesh-lake")["StorageDescriptor"]["Columns"]
    assert {c["Name"]: c["Parameters"]["mesh.classification"] for c in cols}["buyer_email"] == "pii"


def test_lake_formation_requests_match_the_service_model_and_the_policy():
    reg, pol = Registry.load(), AccessPolicy()
    arns = {n: f"arn:aws:iam::123456789012:role/mesh-{n}" for n in pol.principals}
    entries, filters = lakeformation.grant_entries(pol, reg, arns, "123456789012")
    client = boto3.client("lakeformation", **KW)
    with Stubber(client) as stub:
        for t in lakeformation.lf_tag_requests(pol):
            stub.add_response("create_lf_tag", {}, t)
            client.create_lf_tag(**t)
        for a in lakeformation.tag_assignments(reg):
            stub.add_response("add_lf_tags_to_resource", {"Failures": []}, a)
            client.add_lf_tags_to_resource(**a)
        for f in filters:
            stub.add_response("create_data_cells_filter", {}, f)
            client.create_data_cells_filter(**f)
        stub.add_response("batch_grant_permissions", {"Failures": []}, {"Entries": entries})
        client.batch_grant_permissions(Entries=entries)
    filtered = {(f["TableData"]["DatabaseName"], f["TableData"]["TableName"]) for f in filters}
    assert filtered == {("mesh_dealers", "dealers"), ("mesh_leads", "leads"), ("mesh_leads", "dealer_lead_performance")}


def _lf_columns(tfvars: dict, principal: str, product: str) -> set[str]:
    """What Lake Formation would let ``principal`` read, evaluated from the rendered Terraform inputs."""
    t = tfvars["tables"][product]
    cols: set[str] = set()
    for g in tfvars["tag_grants"]:
        if g["principal"] == principal and t["domain"] in g["domains"]:
            cols |= {c["name"] for c in t["columns"] if c["sensitivity"] in g["sensitivities"]}
    for f in tfvars["cell_filters"]:
        if f["principal"] == principal and f["domain"] == t["domain"] and f["table"] == t["name"]:
            cols |= set(f["columns"]) - {t["partition"]}
    return cols


def test_terraform_inputs_grant_exactly_what_the_query_engine_allows():
    reg, pol = Registry.load(), AccessPolicy()
    tfvars = render(reg, pol)
    for principal in pol.principals:
        for c in reg:
            ta = pol.table_access(principal, c)
            assert _lf_columns(tfvars, principal, c.product) == set(ta.columns) - {c.partition}, (principal, c.product)
            has_filter = any(
                f["principal"] == principal and f["table"] == c.name and f["domain"] == c.domain for f in tfvars["cell_filters"]
            )
            assert has_filter == (ta.row_filter is not None), (principal, c.product)


def test_generated_files_are_up_to_date():
    reg = Registry.load()
    assert (ROOT / "infra" / "terraform" / "products.auto.tfvars.json").read_text() == dumps(render(reg, AccessPolicy())), (
        "run mesh infra render"
    )
    assert (ROOT / "dags" / "mesh_daily.py").read_text() == render_dag(reg), "run mesh dags render"


def test_terraform_parses_and_declares_every_generated_variable():
    variables: set[str] = set()
    resources: set[str] = set()
    for f in sorted((ROOT / "infra" / "terraform").glob("*.tf")):
        with f.open() as fh:
            doc = hcl2.load(fh)
        for v in doc.get("variable", []):
            variables |= {k.strip('"') for k in v}
        for r in doc.get("resource", []):
            resources |= {k.strip('"') for k in r}
    tfvars = json.loads((ROOT / "infra" / "terraform" / "products.auto.tfvars.json").read_text())
    assert set(tfvars) <= variables
    assert {
        "aws_lakeformation_permissions",
        "aws_lakeformation_data_cells_filter",
        "aws_glue_catalog_table",
        "aws_athena_workgroup",
        "aws_mwaa_environment",
        "aws_lakeformation_resource_lf_tags",
    } <= resources


def test_athena_runs_only_checked_queries(platform):
    client = boto3.client("athena", **KW)
    dt = platform.lake.partitions("leads.leads")[-1]
    sql = f"SELECT channel, count(*) AS n FROM leads.leads WHERE dt = DATE '{dt}' GROUP BY channel"
    eng = AthenaEngine(
        platform.engine, client, lambda p, purpose: f"mesh-{purpose}", "s3://mesh-athena-results/person/", sleep=lambda s: None
    )
    with Stubber(client) as stub:
        stub.add_response(
            "start_query_execution",
            {"QueryExecutionId": "q1"},
            {"QueryString": sql, "WorkGroup": "mesh-person", "ResultConfiguration": {"OutputLocation": ANY}},
        )
        stub.add_response("get_query_execution", {"QueryExecution": {"Status": {"State": "RUNNING"}}}, {"QueryExecutionId": "q1"})
        stub.add_response(
            "get_query_execution", {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}, {"QueryExecutionId": "q1"}
        )
        stub.add_response(
            "get_query_results",
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": "channel"}, {"VarCharValue": "n"}]},
                        {"Data": [{"VarCharValue": "email"}, {"VarCharValue": "10"}]},
                    ]
                }
            },
            {"QueryExecutionId": "q1", "MaxResults": ANY},
        )
        plan, header, rows = eng.run(sql, "analysts-marketplace")
        assert header == ["channel", "n"] and rows == [["email", "10"]] and plan.ok
    with pytest.raises(QueryRejected):  # never reaches Athena: no stubbed call is consumed
        eng.run(f"SELECT buyer_email FROM leads.leads WHERE dt = DATE '{dt}'", "analysts-marketplace")


def test_the_generated_dag_follows_the_inputs():
    pytest.importorskip("airflow")
    try:
        from airflow.dag_processing.dagbag import DagBag  # Airflow 3.1 and later
    except ImportError:
        from airflow.models.dagbag import DagBag

    bag = DagBag(dag_folder=str(ROOT / "dags"))
    assert not bag.import_errors
    dag = bag.dags["mesh_daily"]
    assert dag.get_task("pricing__price_ratings").upstream_task_ids == {"listings__listing_events"}


def test_platform_settings_are_read_from_config():
    assert config.platform()["query"]["price_usd_per_tb"] == 5.0
