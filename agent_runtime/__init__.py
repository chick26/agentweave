"""General-purpose agent runtime with delegated subagents."""

from __future__ import annotations

from typing import Any

__all__ = ["AgentRuntime"]


def __getattr__(name: str) -> Any:
    if name == "AgentRuntime":
        from agent_runtime.core.orchestrator import AgentRuntime

        return AgentRuntime
    raise AttributeError(f"module 'agent_runtime' has no attribute {name!r}")
