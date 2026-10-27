"""Athena as the query engine on AWS. The platform's own plan runs first (grants, cost, partition filter) so a
person or an assistant gets a precise reason before anything is billed; then the SQL goes to the principal's
workgroup, whose BytesScannedCutoffPerQuery and Lake Formation enforce the same limits again."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from mesh.query import Plan, QueryEngine, QueryRejected


class AthenaEngine:
    def __init__(
        self,
        engine: QueryEngine,
        client: Any,
        workgroup_of: Callable[[str, str], str],
        output: str,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 0.5,
        timeout_seconds: float = 120,
    ):
        self.engine = engine
        self.client = client
        self.workgroup_of = workgroup_of
        self.output = output
        self.sleep = sleep
        self.poll = poll_seconds
        self.timeout = timeout_seconds

    def run(
        self, sql: str, principal: str, purpose: str = "person", max_rows: int = 1000
    ) -> tuple[Plan, list[str], list[list[str]]]:
        plan = self.engine.plan(sql, principal, purpose)  # type: ignore[arg-type]
        if not plan.ok:
            raise QueryRejected(plan)
        qid = self.client.start_query_execution(
            QueryString=sql,
            WorkGroup=self.workgroup_of(principal, purpose),
            ResultConfiguration={"OutputLocation": self.output},
        )["QueryExecutionId"]
        waited = 0.0
        while True:
            st = self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            if st["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            if waited >= self.timeout:
                self.client.stop_query_execution(QueryExecutionId=qid)
                raise TimeoutError(f"Athena query {qid} did not finish in {self.timeout}s")
            self.sleep(self.poll)
            waited += self.poll
        if st["State"] != "SUCCEEDED":
            plan.problems.append(f"Athena {st['State']}: {st.get('StateChangeReason', '')}")
            raise QueryRejected(plan)
        res = self.client.get_query_results(QueryExecutionId=qid, MaxResults=min(max_rows + 1, 1000))
        rows = [[d.get("VarCharValue", "") for d in r["Data"]] for r in res["ResultSet"]["Rows"]]
        return plan, rows[0] if rows else [], rows[1:]
