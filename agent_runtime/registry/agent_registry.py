"""Discovery and prompt routing for delegated subagents."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.common import file_signature, xml_escape
from agent_runtime.registry.manifest_models import AgentManifest
from agent_runtime.registry.manifest_parser import (
    read_yaml_manifest,
    subagent_manifest_paths,
    subagent_signature_paths,
)


class AgentRegistry:
    """Discover delegated subagents from the strict subagents/* package contract."""

    def __init__(self, *, subagents_root: Path) -> None:
        self.subagents_root = subagents_root
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[AgentManifest] | None = None

    def discover(self) -> list[AgentManifest]:
        paths = subagent_manifest_paths(self.subagents_root)
        signature = file_signature(subagent_signature_paths(self.subagents_root))
        if self._cache is not None and self._cache_signature == signature:
            return list(self._cache)
        if not paths:
            self._cache = []
            self._cache_signature = signature
            return []
        manifests: list[AgentManifest] = []
        for path in paths:
            manifests.append(
                read_yaml_manifest(
                    path,
                    manifest_cls=AgentManifest,
                    kind="subagent",
                )
            )
        self._cache = manifests
        self._cache_signature = signature
        return list(manifests)

    def invalidate(self) -> None:
        self._cache = None
        self._cache_signature = None

    def get(self, name: str) -> AgentManifest:
        for manifest in self.discover():
            if manifest.name == name:
                return manifest
        raise ValueError(f"Unknown subagent: {name}")

    def format_routing_for_prompt(self, names: list[str] | None = None) -> str:
        subagents = self.discover()
        if names is not None:
            allowed = set(names)
            subagents = [subagent for subagent in subagents if subagent.name in allowed]
        if not subagents:
            return "<subagents_routing></subagents_routing>"
        lines = ["<subagents_routing>"]
        for subagent in subagents:
            mode_desc = (
                "isolated subagent"
                if subagent.execution.mode == "worker"
                else subagent.execution.mode
            )
            routing_hints = ", ".join(subagent.routing_hints)
            lines.append(
                f'  <subagent name="{xml_escape(subagent.name)}" '
                f'execution_mode="{xml_escape(mode_desc)}" '
                f'description="{xml_escape(subagent.description)}" '
                f'route_when="{xml_escape(routing_hints)}" />'
            )
        lines.append("</subagents_routing>")
        return "\n".join(lines)


__all__ = ["AgentRegistry"]
