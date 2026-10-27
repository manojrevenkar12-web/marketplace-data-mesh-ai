"""Build the model router for this process: simulated (default) or live Bedrock / Anthropic providers."""

from __future__ import annotations

import os

from mesh import config
from mesh.llm.base import Provider
from mesh.llm.router import ModelRouter
from mesh.llm.simulated import SimulatedProvider


def make_router(mode: str | None = None) -> ModelRouter:
    mode = mode or config.model_mode()
    if mode == "simulated":
        return ModelRouter({"simulated": SimulatedProvider()})
    providers: dict[str, Provider] = {}
    from mesh.llm.bedrock import BedrockProvider

    guardrail = None
    if os.environ.get("MESH_GUARDRAIL_ID"):
        guardrail = {"id": os.environ["MESH_GUARDRAIL_ID"], "version": os.environ.get("MESH_GUARDRAIL_VERSION", "DRAFT")}
    providers["bedrock"] = BedrockProvider(guardrail=guardrail)
    if os.environ.get("ANTHROPIC_API_KEY"):
        from mesh.llm.anthropic_api import AnthropicProvider

        providers["anthropic"] = AnthropicProvider()
    return ModelRouter(providers)
