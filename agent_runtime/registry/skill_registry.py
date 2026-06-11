"""Skill discovery for skills/*/SKILL.md."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.common import file_signature, xml_escape
from agent_runtime.registry.manifest_models import Skill
from agent_runtime.registry.manifest_parser import (
    as_str_list,
    manifest_paths,
    read_markdown_manifest,
    read_yaml_manifest,
)


class SkillRegistry:
    """Discover real skills only from skills/*/SKILL.md."""

    def __init__(self, *, skills_root: Path) -> None:
        self.skills_root = skills_root
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[Skill] | None = None

    def discover(self) -> list[Skill]:
        paths = manifest_paths(self.skills_root, "SKILL")
        signature = file_signature(paths)
        if self._cache is not None and self._cache_signature == signature:
            return list(self._cache)
        if not paths:
            self._cache = []
            self._cache_signature = signature
            return []
        manifests: list[Skill] = []
        for path in paths:
            if path.suffix == ".md":
                manifests.append(
                    read_markdown_manifest(
                        path,
                        manifest_cls=Skill,
                        kind="skill",
                    )
                )
            else:
                manifests.append(
                    read_yaml_manifest(
                        path,
                        manifest_cls=Skill,
                        kind="skill",
                    )
                )
        self._cache = manifests
        self._cache_signature = signature
        return list(manifests)

    def invalidate(self) -> None:
        self._cache = None
        self._cache_signature = None

    def get(self, name: str) -> Skill:
        for skill in self.discover():
            if skill.name == name:
                return skill
        raise ValueError(f"Unknown skill: {name}")

    def format_catalog_for_prompt(self, names: list[str] | None = None) -> str:
        skills = self.discover()
        if names is not None:
            allowed = set(names)
            skills = [skill for skill in skills if skill.name in allowed]
        if not skills:
            return "<skills_catalog></skills_catalog>"
        lines = ["<skills_catalog>"]
        for skill in skills:
            hints = skill.metadata.get("activation_hints", skill.routing_hints)
            activation_hints = ", ".join(as_str_list(hints))
            lines.append(
                f'  <skill name="{xml_escape(skill.name)}" '
                f'description="{xml_escape(skill.description)}" '
                f'activation_hints="{xml_escape(activation_hints)}" />'
            )
        lines.append("</skills_catalog>")
        return "\n".join(lines)


__all__ = ["SkillRegistry"]
