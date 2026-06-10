"""Stable manifest helpers shared with subagents and prepare scripts."""

from pathlib import Path

from agent_runtime.registry.skill_registry import (
    AgentManifest,
    AgentRegistry,
    ManifestDomains,
    ManifestExtension,
    ManifestMemory,
    ManifestModel,
    ManifestOutputContract,
)


def load_subagent_manifest(root: Path | str, name: str) -> AgentManifest:
    """Load one subagent manifest from a runtime project root."""

    return AgentRegistry(subagents_root=Path(root) / "subagents").get(name)


__all__ = [
    "AgentManifest",
    "ManifestDomains",
    "ManifestExtension",
    "ManifestMemory",
    "ManifestModel",
    "ManifestOutputContract",
    "load_subagent_manifest",
]
