from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml

from agent_runtime.common import file_signature, split_frontmatter, xml_escape


@dataclass(frozen=True)
class ManifestExecution:
    mode: str = "inline"
    worker_profile: str = ""
    model_role: str = ""
    tool_module: str = ""
    context_module: str = ""
    max_turns: int | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class ManifestMemory:
    namespaces: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ManifestDomains:
    root: str = ""


@dataclass(frozen=True)
class ManifestBase:
    name: str
    description: str
    location: Path
    kind: str
    body: str = ""
    execution: ManifestExecution = field(default_factory=ManifestExecution)
    tools: list[str] = field(default_factory=list)
    memory: ManifestMemory = field(default_factory=ManifestMemory)
    domains: ManifestDomains = field(default_factory=ManifestDomains)
    routing_hints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Skill(ManifestBase):
    """A loadable skill document under skills/*/SKILL.md.

    Skills are method cards or reusable workflow resources. They are not
    delegated worker agents and are not exposed as top-level agent tools.
    """


@dataclass(frozen=True)
class AgentManifest(ManifestBase):
    """A delegated subagent manifest under subagents/*/AGENT.md."""


ManifestT = TypeVar("ManifestT", bound=ManifestBase)


class SkillRegistry:
    """Discover real skills only from skills/*/SKILL.md."""

    def __init__(self, *, skills_root: Path) -> None:
        self.skills_root = skills_root
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[Skill] | None = None

    def discover(self) -> list[Skill]:
        paths = _manifest_paths(self.skills_root, "SKILL")
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
                    _read_markdown_manifest(
                        path,
                        manifest_cls=Skill,
                        kind="skill",
                    )
                )
            else:
                manifests.append(
                    _read_yaml_manifest(
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

    def format_catalog_for_prompt(self) -> str:
        skills = self.discover()
        if not skills:
            return "<skills_catalog></skills_catalog>"
        lines = ["<skills_catalog>"]
        for skill in skills:
            hints = skill.metadata.get("activation_hints", skill.routing_hints)
            activation_hints = ", ".join(_as_str_list(hints))
            lines.append(
                f'  <skill name="{xml_escape(skill.name)}" '
                f'description="{xml_escape(skill.description)}" '
                f'activation_hints="{xml_escape(activation_hints)}" />'
            )
        lines.append("</skills_catalog>")
        return "\n".join(lines)


class AgentRegistry:
    """Discover delegated subagents only from subagents/*/AGENT.md."""

    def __init__(self, *, subagents_root: Path) -> None:
        self.subagents_root = subagents_root
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[AgentManifest] | None = None

    def discover(self) -> list[AgentManifest]:
        paths = _manifest_paths(self.subagents_root, "AGENT")
        signature = file_signature(paths)
        if self._cache is not None and self._cache_signature == signature:
            return list(self._cache)
        if not paths:
            self._cache = []
            self._cache_signature = signature
            return []
        manifests: list[AgentManifest] = []
        for path in paths:
            if path.suffix == ".md":
                manifests.append(
                    _read_markdown_manifest(
                        path,
                        manifest_cls=AgentManifest,
                        kind="subagent",
                    )
                )
            else:
                manifests.append(
                    _read_yaml_manifest(
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

    def format_routing_for_prompt(self) -> str:
        subagents = self.discover()
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


SkillExecution = ManifestExecution
SkillMemory = ManifestMemory
SkillDomains = ManifestDomains


def _read_yaml_manifest(
    path: Path,
    *,
    manifest_cls: type[ManifestT],
    kind: str,
) -> ManifestT:
    metadata = _read_yaml_file(path)
    return _build_manifest_from_metadata(
        metadata=metadata,
        location=path,
        body="",
        default_name=path.parent.name,
        manifest_cls=manifest_cls,
        kind=kind,
    )


def _read_markdown_manifest(
    path: Path,
    *,
    manifest_cls: type[ManifestT],
    kind: str,
) -> ManifestT:
    text = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(text)
    return _build_manifest_from_metadata(
        metadata=metadata,
        location=path,
        body=body.strip(),
        default_name=path.parent.name,
        manifest_cls=manifest_cls,
        kind=kind,
    )


def _build_manifest_from_metadata(
    *,
    metadata: dict[str, Any],
    location: Path,
    body: str,
    default_name: str,
    manifest_cls: type[ManifestT],
    kind: str,
) -> ManifestT:
    execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
    memory = metadata.get("memory") if isinstance(metadata.get("memory"), dict) else {}
    domains = metadata.get("domains") if isinstance(metadata.get("domains"), dict) else {}
    execution_mode = str(execution.get("mode", "inline"))
    model_role = str(execution.get("model_role", ""))
    if kind == "subagent" and execution_mode == "worker" and not model_role:
        raise ValueError(f"Worker subagent {location} is missing execution.model_role")
    return manifest_cls(
        name=str(metadata.get("name") or default_name),
        description=str(metadata.get("description", "")),
        location=location,
        kind=kind,
        body=body,
        execution=ManifestExecution(
            mode=execution_mode,
            worker_profile=str(execution.get("worker_profile", "")),
            model_role=model_role,
            tool_module=str(execution.get("tool_module", "")),
            context_module=str(execution.get("context_module", "")),
            max_turns=_optional_int(execution.get("max_turns")),
            timeout_seconds=_optional_float(execution.get("timeout_seconds")),
        ),
        tools=_as_str_list(metadata.get("tools", [])),
        memory=ManifestMemory(namespaces=_as_str_list(memory.get("namespaces", []))),
        domains=ManifestDomains(root=str(domains.get("root", ""))),
        routing_hints=_as_str_list(metadata.get("routing_hints", [])),
        metadata=metadata,
    )


def _read_yaml_file(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML manifest {path}: expected a mapping")
    return data if isinstance(data, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _manifest_paths(root: Path, basename: str) -> list[Path]:
    if not root.exists():
        return []
    paths: list[Path] = []
    for item_dir in sorted(root.iterdir()):
        if not item_dir.is_dir():
            continue
        md_path = item_dir / f"{basename}.md"
        yaml_path = item_dir / f"{basename}.yaml"
        if md_path.exists():
            paths.append(md_path)
        elif yaml_path.exists():
            paths.append(yaml_path)
    return paths
