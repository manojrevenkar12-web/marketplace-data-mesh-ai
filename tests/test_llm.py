"""Model calls: request shapes against the real AWS service models, routing, fallback, breaker, budget, repair, privacy."""

from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

from mesh.llm.anthropic_api import to_messages_api
from mesh.llm.base import Block, InvalidOutput, Message, ModelRequest, ModelResponse, ProviderError, Usage
from mesh.llm.bedrock import BedrockProvider, from_converse, to_converse
from mesh.llm.prompts import all_prompts, check_lock, load, render
from mesh.llm.router import Budget, BudgetExceeded, ModelRouter, Unavailable
from mesh.llm.schemas import RECORD_DIAGNOSIS, RECORD_QUERY

SETTINGS: dict[str, Any] = {
    "models": {
        "haiku-eu": {
            "provider": "bedrock",
            "model_id": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
            "region_group": "eu",
            "privacy": "in_region",
            "price_in": 1.0,
            "price_out": 5.0,
        },
        "sonnet-eu": {
            "provider": "bedrock",
            "model_id": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "region_group": "eu",
            "privacy": "in_region",
            "price_in": 3.0,
            "price_out": 15.0,
        },
        "sonnet-direct": {
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-5",
            "region_group": "vendor",
            "privacy": "vendor_api",
            "price_in": 3.0,
            "price_out": 15.0,
        },
    },
    "tasks": {"triage": ["haiku-eu", "sonnet-eu", "sonnet-direct"]},
    "data_policies": {"pii": ["eu"], "internal": ["eu", "vendor"]},
    "routing": {
        "attempts_per_model": 2,
        "backoff_seconds": [0, 0],
        "timeout_seconds": 5,
        "breaker_failures": 2,
        "breaker_cooldown_seconds": 60,
        "repair_attempts": 1,
        "max_cost_usd_per_invoice": 1.0,
    },
}


def req(scope: str = "pii") -> ModelRequest:
    return ModelRequest(
        task="triage",
        system="sys",
        messages=[Message("user", [Block.t("hello")])],
        tools=[RECORD_QUERY, RECORD_DIAGNOSIS],
        tool_choice="record_diagnosis",
        data_scope=scope,
        prompt_id="triage",
        prompt_version=1,
    )


class Scripted:
    """A provider that answers from a script: exceptions are raised, responses returned."""

    def __init__(self, name: str, script: list[Any]):
        self.name, self.script, self.models = name, list(script), []

    async def generate(self, r: ModelRequest, model: str) -> ModelResponse:
        self.models.append(model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(payload: dict[str, Any] | None = None) -> ModelResponse:
    return ModelResponse(
        [Block("tool_use", tool_use_id="t1", name="record_diagnosis", input=payload or {"category": "late_drop", "evidence": ["E1"], "summary": "s", "next_steps": []})],
        "tool_use",
        Usage(1000, 200),
        "m",
        "x",
    )


def parse(r: ModelResponse) -> dict[str, Any]:
    p = r.tool_input("record_diagnosis")
    if "category" not in p:
        raise InvalidOutput("category missing")
    return p


async def nosleep(_: float) -> None:
    return None


def test_converse_request_matches_the_bedrock_service_model():
    client = boto3.client("bedrock-runtime", region_name="eu-central-1", aws_access_key_id="x", aws_secret_access_key="y")
    params = to_converse(req(), "eu.anthropic.claude-haiku-4-5-20251001-v1:0", guardrail={"id": "gr-1", "version": "3"})
    assert params["toolConfig"]["toolChoice"] == {"tool": {"name": "record_diagnosis"}}
    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "t1", "name": "record_diagnosis", "input": {"category": "late_drop", "evidence": []}}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 900, "outputTokens": 40, "totalTokens": 940},
        "metrics": {"latencyMs": 812},
    }
    with Stubber(client) as stub:
        stub.add_response("converse", response, params)  # Stubber validates both against the service model
        out = client.converse(**params)
    r = from_converse(out, "m", 1.0)
    assert r.tool_input("record_diagnosis") == {"category": "late_drop", "evidence": []}
    assert r.usage == Usage(900, 40) and r.latency_ms == 812


async def test_bedrock_errors_are_classified_for_retry():
    client = boto3.client("bedrock-runtime", region_name="eu-central-1", aws_access_key_id="x", aws_secret_access_key="y")
    p = BedrockProvider(client=client)
    with Stubber(client) as stub:
        stub.add_client_error("converse", service_error_code="ThrottlingException", http_status_code=429)
        stub.add_client_error("converse", service_error_code="ValidationException", http_status_code=400)
        with pytest.raises(ProviderError) as e1:
            await p.generate(req(), "eu.anthropic.claude-haiku-4-5-20251001-v1:0")
        with pytest.raises(ProviderError) as e2:
            await p.generate(req(), "eu.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert e1.value.retryable and not e2.value.retryable


def test_anthropic_request_forces_the_recording_tool():
    p = to_messages_api(req(), "claude-sonnet-4-5")
    assert p["tool_choice"] == {"type": "tool", "name": "record_diagnosis"}
    assert [t["name"] for t in p["tools"]] == ["record_query", "record_diagnosis"]


async def test_router_falls_back_and_personal_data_stays_in_the_eu():
    bedrock = Scripted("bedrock", [ProviderError("down", code="ServiceUnavailableException")] * 2 + [ok()])
    vendor = Scripted("anthropic", [ok()])
    router = ModelRouter({"bedrock": bedrock, "anthropic": vendor}, SETTINGS, sleep=nosleep)
    out = await router.call(req("pii"), parse)
    assert out.model.key == "sonnet-eu" and bedrock.models[-1].startswith("eu.anthropic.claude-sonnet")
    assert vendor.models == []  # personal data may not leave the EU
    assert [m.key for m in router.chain("triage", "internal")] == ["haiku-eu", "sonnet-eu", "sonnet-direct"]
    assert [m.key for m in router.chain("triage", "unknown")] == []


async def test_router_repairs_invalid_output_once_then_gives_up_on_that_model():
    bad = ModelResponse(
        [Block("tool_use", tool_use_id="t9", name="record_diagnosis", input={"oops": 1})], "tool_use", Usage(10, 10), "m", "x"
    )
    p = Scripted("bedrock", [bad, ok()])
    router = ModelRouter({"bedrock": p}, SETTINGS, sleep=nosleep)
    out = await router.call(req(), parse)
    assert out.attempts == 2 and out.value["category"] == "late_drop"
    p2 = Scripted("bedrock", [bad, bad, bad, bad, bad, bad])
    with pytest.raises(Unavailable):
        await ModelRouter({"bedrock": p2}, SETTINGS, sleep=nosleep).call(req(), parse)


async def test_circuit_breaker_skips_a_failing_model_for_the_cooldown():
    now = [0.0]
    p = Scripted("bedrock", [ProviderError("x")] * 2 + [ok()] + [ok()])
    router = ModelRouter({"bedrock": p}, SETTINGS, clock=lambda: now[0], sleep=nosleep)
    await router.call(req(), parse)  # haiku fails twice: breaker opens; sonnet answers
    assert router.breakers["haiku-eu"].open_until == 60.0
    p.models.clear()
    await router.call(req(), parse)
    assert all("sonnet" in m for m in p.models)


async def test_budget_stops_calls_before_they_are_made():
    p = Scripted("bedrock", [ok()])
    router = ModelRouter({"bedrock": p}, SETTINGS, sleep=nosleep)
    with pytest.raises(BudgetExceeded):
        await router.call(req(), parse, Budget(limit_usd=0.0000001))
    assert p.models == []


def test_prompts_render_and_are_locked_to_their_regression_runs(tmp_path):
    p = load("ask_sql")
    assert p.variables() == {"question", "latest_dt", "glossary", "datasets"}
    with pytest.raises(KeyError):
        p.render_user(question="x")
    assert render("a {{x}} b", x=1) == "a 1 b"
    lock = tmp_path / "prompts.lock"
    history = tmp_path / "history.jsonl"
    lock.write_text(json.dumps({q.id: {"version": q.version, "sha256": q.sha256} for q in all_prompts()}))
    history.write_text("".join(json.dumps({"prompt_id": q.id, "version": q.version, "sha256": q.sha256}) + "\n" for q in all_prompts()))
    assert check_lock(lock, history) == []
    changed = json.loads(lock.read_text())
    changed["ask_sql"]["sha256"] = "0" * 64
    lock.write_text(json.dumps(changed))
    assert any("raise the version" in x for x in check_lock(lock, history))
    history.write_text("")
    assert any("no regression run" in x for x in check_lock(lock, history))
