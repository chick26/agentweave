"""Aggregate registry resources for UI and prompt-facing capability views."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.common import file_signature
from agent_runtime.worker.subagent_extensions import clear_subagent_extension_cache
from agent_runtime.registry.bot_registry import BotConfig, BotRegistry
from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.registry.manifest_models import AgentManifest, Skill
from agent_runtime.registry.skill_registry import SkillRegistry


@dataclass(frozen=True)
class ResourceSnapshot:
    skills: list[Skill] = field(default_factory=list)
    subagents: list[AgentManifest] = field(default_factory=list)
    bots: list[BotConfig] = field(default_factory=list)
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
        bot_registry: BotRegistry | None = None,
    ) -> None:
        self.root = root
        self.skill_registry = skill_registry
        self.agent_registry = agent_registry
        self.bot_registry = bot_registry or BotRegistry(
            bots_root=root / "bots",
            agent_registry=agent_registry,
            skill_registry=skill_registry,
        )
        self._snapshot: ResourceSnapshot | None = None

    def discover(self) -> ResourceSnapshot:
        signature = self._signature()
        if self._snapshot is not None and self._snapshot.signature == signature:
            return self._snapshot
        project_rules, project_rules_source = self._load_project_rules()
        self._snapshot = ResourceSnapshot(
            skills=self.skill_registry.discover(),
            subagents=self.agent_registry.discover(),
            bots=self.bot_registry.discover(),
            project_rules=project_rules,
            project_rules_source=project_rules_source,
            signature=signature,
        )
        return self._snapshot

    def reload(self) -> dict[str, Any]:
        before = self._snapshot or self.discover()
        self.skill_registry.invalidate()
        self.agent_registry.invalidate()
        self.bot_registry.invalidate()
        clear_subagent_extension_cache()
        self._snapshot = None
        after = self.discover()
        return {
            "skills": _names_changed([item.name for item in before.skills], [item.name for item in after.skills]),
            "subagents": _names_changed(
                [item.name for item in before.subagents],
                [item.name for item in after.subagents],
            ),
            "bots": _names_changed([item.id for item in before.bots], [item.id for item in after.bots]),
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
        return self.format_for_bot(self.bot_registry.get("default"))

    def format_for_bot(self, bot: BotConfig) -> str:
        return "\n\n".join(
            [
                self.agent_registry.format_routing_for_prompt(names=bot.subagents),
                self.skill_registry.format_catalog_for_prompt(names=bot.skills),
            ]
        )

    def capabilities_payload(self) -> dict[str, Any]:
        snapshot = self.discover()
        return {
            "subagents": [
                _subagent_payload(item)
                for item in snapshot.subagents
            ],
            "skills": [
                {
                    "name": item.name,
                    "description": item.description,
                    "activation_hints": list(
                        item.metadata.get("activation_hints", item.routing_hints)
                    ),
                }
                for item in snapshot.skills
            ],
        }

    def bot_payload(self, bot_id: str) -> dict[str, Any]:
        bot = self.bot_registry.get(bot_id)
        subagents_by_name = {item.name: item for item in self.agent_registry.discover()}
        skills_by_name = {item.name: item for item in self.skill_registry.discover()}
        return {
            **bot.summary(),
            "instructions": bot.instructions,
            "welcome": {
                "message": bot.welcome.message,
                "preset": bot.welcome.preset,
                "prompt": bot.welcome.prompt,
            },
            "resolved": {
                "subagents": [
                    _subagent_payload(item)
                    for name in bot.subagents
                    if (item := subagents_by_name.get(name)) is not None
                ],
                "skills": [
                    {
                        "name": item.name,
                        "description": item.description,
                        "activation_hints": list(
                            item.metadata.get("activation_hints", item.routing_hints)
                        ),
                    }
                    for name in bot.skills
                    if (item := skills_by_name.get(name)) is not None
                ],
            },
        }

    def get_project_rules(self) -> tuple[str, str]:
        snapshot = self.discover()
        return snapshot.project_rules, snapshot.project_rules_source

    def _signature(self) -> tuple[tuple[str, int, int], ...]:
        paths = [
            *list((self.root / "skills").glob("*/SKILL.*")),
            *list((self.root / "subagents").glob("*/AGENT.*")),
            *list((self.root / "subagents").glob("*/domain_catalog.yaml")),
            *list((self.root / "bots").glob("*/BOT.*")),
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
        return [self.root / "AGENTS.md"]


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
    return sorted(
        path
        for path, _mtime, _size in signature
        if path.endswith("/domain_catalog.yaml")
    )


def _subagent_payload(item: AgentManifest) -> dict[str, Any]:
    return {
        "name": item.name,
        "description": item.description,
        "routing_hints": list(item.routing_hints),
        "execution_mode": item.execution.mode,
        "capabilities": list(item.capabilities),
        "policies": dict(item.policies),
        "output_contract": {
            "format": item.output_contract.format,
            "required_fields": list(item.output_contract.required_fields),
            "artifact_types": list(item.output_contract.artifact_types),
            **dict(item.output_contract.metadata),
        },
    }
