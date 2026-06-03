from __future__ import annotations

from typing import Any

from agent_runtime.registry.skill_registry import AgentManifest
from subagents.text2sql.scripts.domain_catalog import build_prompt_context as _build_prompt_context


def build_prompt_context(manifest: AgentManifest) -> dict[str, Any]:
    return _build_prompt_context(manifest)
