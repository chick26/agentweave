"""Discovery and parsing for bot capability manifests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_runtime.common import file_signature
from agent_runtime.registry.skill_registry import AgentRegistry, SkillRegistry


@dataclass(frozen=True)
class BotWelcome:
    mode: str = "providers"
    provider_module: str = ""
    preset_questions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class BotConfig:
    id: str
    name: str
    description: str
    location: Path
    instructions: str = ""
    subagents: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    welcome: BotWelcome = field(default_factory=BotWelcome)
    metadata: dict[str, Any] = field(default_factory=dict)
    generated: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "subagents": list(self.subagents),
            "skills": list(self.skills),
            "generated": self.generated,
        }


class BotRegistry:
    """Discover backend bot configs from bots/*/BOT.yaml."""

    def __init__(
        self,
        *,
        bots_root: Path,
        agent_registry: AgentRegistry,
        skill_registry: SkillRegistry,
    ) -> None:
        self.bots_root = bots_root
        self.agent_registry = agent_registry
        self.skill_registry = skill_registry
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[BotConfig] | None = None

    def discover(self) -> list[BotConfig]:
        paths = _bot_paths(self.bots_root)
        signature = file_signature(paths)
        if self._cache is not None and self._cache_signature == signature:
            return list(self._cache)
        bots = [_read_bot(path) for path in paths]
        if not any(bot.id == "default" for bot in bots):
            bots.insert(0, self._default_bot())
        self._validate(bots)
        self._cache = bots
        self._cache_signature = signature
        return list(bots)

    def invalidate(self) -> None:
        self._cache = None
        self._cache_signature = None

    def get(self, bot_id: str = "") -> BotConfig:
        resolved_id = (bot_id or "default").strip() or "default"
        for bot in self.discover():
            if bot.id == resolved_id:
                return bot
        raise ValueError(f"Unknown bot: {resolved_id}")

    def list_summaries(self) -> list[dict[str, Any]]:
        return [bot.summary() for bot in self.discover()]

    def _default_bot(self) -> BotConfig:
        subagents = [item.name for item in self.agent_registry.discover()]
        skills = [item.name for item in self.skill_registry.discover()]
        return BotConfig(
            id="default",
            name="Default Bot",
            description="默认机器人，兼容当前全部已接入能力。",
            location=self.bots_root / "__generated_default__",
            instructions="",
            subagents=subagents,
            skills=skills,
            generated=True,
        )

    def _validate(self, bots: list[BotConfig]) -> None:
        ids = [bot.id for bot in bots]
        duplicates = sorted({bot_id for bot_id in ids if ids.count(bot_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate bot id(s): {', '.join(duplicates)}")
        available_subagents = {item.name for item in self.agent_registry.discover()}
        available_skills = {item.name for item in self.skill_registry.discover()}
        for bot in bots:
            missing_subagents = sorted(set(bot.subagents) - available_subagents)
            missing_skills = sorted(set(bot.skills) - available_skills)
            if missing_subagents or missing_skills:
                parts = []
                if missing_subagents:
                    parts.append(f"subagents={', '.join(missing_subagents)}")
                if missing_skills:
                    parts.append(f"skills={', '.join(missing_skills)}")
                raise ValueError(f"Bot `{bot.id}` references unknown resources: {'; '.join(parts)}")


def _bot_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [
            *root.glob("*/BOT.yaml"),
            *root.glob("*/BOT.yml"),
        ],
        key=lambda path: str(path),
    )


def _read_bot(path: Path) -> BotConfig:
    try:
        metadata = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid bot config {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid bot config {path}: expected a mapping")
    bot_id = str(metadata.get("id") or path.parent.name).strip()
    if not bot_id:
        raise ValueError(f"Invalid bot config {path}: id is required")
    welcome = metadata.get("welcome") if isinstance(metadata.get("welcome"), dict) else {}
    return BotConfig(
        id=bot_id,
        name=str(metadata.get("name") or bot_id),
        description=str(metadata.get("description") or ""),
        location=path,
        instructions=str(metadata.get("instructions") or ""),
        subagents=_as_str_list(metadata.get("subagents", [])),
        skills=_as_str_list(metadata.get("skills", [])),
        welcome=BotWelcome(
            mode=str(welcome.get("mode") or "providers"),
            provider_module=str(welcome.get("provider_module") or ""),
            preset_questions=_preset_question_groups(welcome.get("preset_questions", [])),
        ),
        metadata=metadata,
    )


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _preset_question_groups(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    groups: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        questions = _as_str_list(item.get("questions", []))
        domain_name = str(item.get("domain_name") or item.get("name") or "").strip()
        title = str(item.get("title") or domain_name).strip()
        if not domain_name or not questions:
            continue
        groups.append(
            {
                "domain_name": domain_name,
                "title": title or domain_name,
                "questions": questions,
            }
        )
    return groups
