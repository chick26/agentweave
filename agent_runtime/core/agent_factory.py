"""Factory helpers for SDK Agent construction."""

from __future__ import annotations

from typing import Any

from agents import Agent, ModelSettings

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.runtime_utils import build_model


def build_orchestrator_agent(
    *,
    instructions: str,
    profile: Any,
    tools: list[Any],
    log_callback: Any,
) -> Agent[RuntimeContext]:
    return Agent[RuntimeContext](
        name="Agent Orchestrator",
        instructions=instructions,
        model=build_model(
            profile=profile,
            log_callback=log_callback,
            title="编排模型调用",
            kind="orchestration_model",
        ),
        model_settings=ModelSettings(max_tokens=profile.max_tokens),
        tools=tools,
    )
