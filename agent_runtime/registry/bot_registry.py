"""Discovery and parsing for bot capability manifests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_runtime.common import file_signature
from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.registry.skill_registry import SkillRegistry

DEFAULT_WELCOME_MESSAGE = "你好，我可以回答已接入能力范围内的问题。"
DEFAULT_WELCOME_PROMPT = (
    "你是当前 Bot 的欢迎词生成器。根据已接入的 subagents 和 skills 描述，"
    "生成简短中文 Markdown 欢迎词，并给出用户可以直接提问的示例。"
)


@dataclass(frozen=True)
class BotWelcome:
    message: str = DEFAULT_WELCOME_MESSAGE
    preset: bool = False
    prompt: str = DEFAULT_WELCOME_PROMPT


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
        if not any(bot.id == "default" for bot in bots) and _allow_generated_default_bot():
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
            description="默认机器人，自动聚合当前全部已接入能力。",
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
            message=str(welcome.get("message") or DEFAULT_WELCOME_MESSAGE),
            preset=_as_bool(welcome.get("preset"), default=False),
            prompt=str(welcome.get("prompt") or DEFAULT_WELCOME_PROMPT),
        ),
        metadata=metadata,
    )


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _allow_generated_default_bot() -> bool:
    env_name = os.getenv("AGENTWEAVE_ENV", "").strip().lower()
    if env_name not in {"production", "prod"}:
        return True
    return _as_bool(os.getenv("AGENTWEAVE_ALLOW_GENERATED_DEFAULT_BOT"), default=False)
