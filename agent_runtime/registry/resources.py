from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.common import file_signature
from agent_runtime.registry.skill_registry import AgentManifest, AgentRegistry, Skill, SkillRegistry


@dataclass(frozen=True)
class ResourceSnapshot:
    skills: list[Skill] = field(default_factory=list)
    subagents: list[AgentManifest] = field(default_factory=list)
    project_rules: str = ""
    project_rules_source: str = ""
    signature: tuple[tuple[str, int, int], ...] = field(default_factory=tuple)


class ResourceLoader:
    """Discover prompt-facing AgentWeave resources with reload support."""

    def __init__(
        self,
        *,
        root: Path,
        skill_registry: SkillRegistry,
        agent_registry: AgentRegistry,
    ) -> None:
        self.root = root
        self.skill_registry = skill_registry
        self.agent_registry = agent_registry
        self._snapshot: ResourceSnapshot | None = None

    def discover(self) -> ResourceSnapshot:
        signature = self._signature()
        if self._snapshot is not None and self._snapshot.signature == signature:
            return self._snapshot
        project_rules, project_rules_source = self._load_project_rules()
        self._snapshot = ResourceSnapshot(
            skills=self.skill_registry.discover(),
            subagents=self.agent_registry.discover(),
            project_rules=project_rules,
            project_rules_source=project_rules_source,
            signature=signature,
        )
        return self._snapshot

    def reload(self) -> dict[str, Any]:
        before = self._snapshot or self.discover()
        self.skill_registry.invalidate()
        self.agent_registry.invalidate()
        self._snapshot = None
        after = self.discover()
        return {
            "skills": _names_changed([item.name for item in before.skills], [item.name for item in after.skills]),
            "subagents": _names_changed(
                [item.name for item in before.subagents],
                [item.name for item in after.subagents],
            ),
            "domains": {
                "changed": _domain_files(before.signature) != _domain_files(after.signature),
                "before": _domain_files(before.signature),
                "after": _domain_files(after.signature),
            },
            "project_rules": before.project_rules != after.project_rules
            or before.project_rules_source != after.project_rules_source,
            "project_rules_source": after.project_rules_source,
        }

    def format_for_prompt(self) -> str:
        return "\n\n".join(
            [
                self.agent_registry.format_routing_for_prompt(),
                self.skill_registry.format_catalog_for_prompt(),
            ]
        )

    def get_project_rules(self) -> tuple[str, str]:
        snapshot = self.discover()
        return snapshot.project_rules, snapshot.project_rules_source

    def _signature(self) -> tuple[tuple[str, int, int], ...]:
        paths = [
            *list((self.root / "skills").glob("*/SKILL.*")),
            *list((self.root / "subagents").glob("*/AGENT.*")),
            *list((self.root / "subagents").glob("*/domains/*/DOMAIN.*")),
            *self._project_rule_candidates(),
        ]
        return file_signature([path for path in paths if path.exists()])

    def _load_project_rules(self) -> tuple[str, str]:
        for path in self._project_rule_candidates():
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8", errors="replace").strip(), str(path)
        return "", ""

    def _project_rule_candidates(self) -> list[Path]:
        override = os.getenv("AGENT_PROJECT_RULES_PATH", "").strip()
        if override:
            return [Path(override).expanduser()]
        return [self.root / "AGENTS.md", self.root / "PROJECT.md"]


def _names_changed(before: list[str], after: list[str]) -> dict[str, Any]:
    before_set = set(before)
    after_set = set(after)
    return {
        "changed": before != after,
        "before": before,
        "after": after,
        "added": sorted(after_set - before_set),
        "removed": sorted(before_set - after_set),
    }


def _domain_files(signature: tuple[tuple[str, int, int], ...]) -> list[str]:
    return sorted(path for path, _mtime, _size in signature if "/domains/" in path)
