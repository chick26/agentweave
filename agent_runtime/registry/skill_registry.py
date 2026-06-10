"""Discovery and validation for skills and subagent manifests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml

from agent_runtime.common import file_signature, split_frontmatter, xml_escape


@dataclass(frozen=True)
class ManifestExecution:
    mode: str = "inline"
    max_turns: int | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class ManifestMemory:
    namespaces: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ManifestDomains:
    root: str = ""
    file: str = ""


@dataclass(frozen=True)
class ManifestModel:
    llm: str = ""
    llm_base_url: str = ""
    embedding: str = ""
    embedding_base_url: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManifestExtension:
    module: str = ""


@dataclass(frozen=True)
class ManifestOutputContract:
    format: str = ""
    required_fields: list[str] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuggestedQuestion:
    text: str
    reason: str = ""


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
    model: ManifestModel = field(default_factory=ManifestModel)
    extension: ManifestExtension = field(default_factory=ManifestExtension)
    capabilities: list[str] = field(default_factory=list)
    policies: dict[str, Any] = field(default_factory=dict)
    output_contract: ManifestOutputContract = field(default_factory=ManifestOutputContract)
    routing_hints: list[str] = field(default_factory=list)
    suggested_questions: list[SuggestedQuestion] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Skill(ManifestBase):
    """A loadable skill document under skills/*/SKILL.md.

    Skills are method cards or reusable workflow resources. They are not
    delegated worker agents and are not exposed as top-level agent tools.
    """


@dataclass(frozen=True)
class AgentManifest(ManifestBase):
    """A delegated subagent manifest under subagents/*/AGENT.yaml."""


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
            activation_hints = ", ".join(_as_str_list(hints))
            lines.append(
                f'  <skill name="{xml_escape(skill.name)}" '
                f'description="{xml_escape(skill.description)}" '
                f'activation_hints="{xml_escape(activation_hints)}" />'
            )
        lines.append("</skills_catalog>")
        return "\n".join(lines)


class AgentRegistry:
    """Discover delegated subagents from the strict subagents/* package contract."""

    def __init__(self, *, subagents_root: Path) -> None:
        self.subagents_root = subagents_root
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache: list[AgentManifest] | None = None

    def discover(self) -> list[AgentManifest]:
        paths = _subagent_manifest_paths(self.subagents_root)
        signature = file_signature(_subagent_signature_paths(self.subagents_root))
        if self._cache is not None and self._cache_signature == signature:
            return list(self._cache)
        if not paths:
            self._cache = []
            self._cache_signature = signature
            return []
        manifests: list[AgentManifest] = []
        for path in paths:
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


SkillExecution = ManifestExecution
SkillMemory = ManifestMemory
SkillDomains = ManifestDomains
SkillModel = ManifestModel
SkillExtension = ManifestExtension
SkillOutputContract = ManifestOutputContract


def _read_yaml_manifest(
    path: Path,
    *,
    manifest_cls: type[ManifestT],
    kind: str,
) -> ManifestT:
    metadata = _read_yaml_file(path)
    body = ""
    prompt_path = path.parent / "prompt.md"
    if kind == "subagent" and prompt_path.exists():
        body = prompt_path.read_text(encoding="utf-8").strip()
    manifest = _build_manifest_from_metadata(
        metadata=metadata,
        location=path,
        body=body,
        default_name=path.parent.name,
        manifest_cls=manifest_cls,
        kind=kind,
    )
    if kind == "subagent":
        _validate_subagent_contract(manifest)
    return manifest


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
    model = metadata.get("model") if isinstance(metadata.get("model"), dict) else {}
    extension = metadata.get("extension") if isinstance(metadata.get("extension"), dict) else {}
    policies = metadata.get("policies", {})
    output_contract = metadata.get("output_contract", {})
    execution_mode = str(execution.get("mode", "inline"))
    return manifest_cls(
        name=str(metadata.get("name") or default_name),
        description=str(metadata.get("description", "")),
        location=location,
        kind=kind,
        body=body,
        execution=ManifestExecution(
            mode=execution_mode,
            max_turns=_optional_int(execution.get("max_turns")),
            timeout_seconds=_optional_float(execution.get("timeout_seconds")),
        ),
        tools=_as_str_list(metadata.get("tools", [])),
        memory=ManifestMemory(namespaces=_as_str_list(memory.get("namespaces", []))),
        domains=ManifestDomains(
            root=str(domains.get("root", "")),
            file=str(domains.get("file", "")),
        ),
        model=ManifestModel(
            llm=str(model.get("llm", "")),
            llm_base_url=str(model.get("llm_base_url", "")),
            embedding=str(model.get("embedding", "")),
            embedding_base_url=str(model.get("embedding_base_url", "")),
            extra_body=_as_dict(model.get("extra_body", {})),
        ),
        extension=ManifestExtension(
            module=str(extension.get("module", "")),
        ),
        capabilities=_as_str_list(metadata.get("capabilities", [])),
        policies=_as_dict_strict(policies, field_name="policies", location=location),
        output_contract=_as_output_contract(
            output_contract,
            location=location,
        ),
        routing_hints=_as_str_list(metadata.get("routing_hints", [])),
        suggested_questions=_as_suggested_questions(
            metadata.get("suggested_questions", metadata.get("presets", []))
        ),
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


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_dict_strict(value: Any, *, field_name: str, location: Path) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid manifest {location}: `{field_name}` must be a mapping.")
    return dict(value)


def _as_output_contract(value: Any, *, location: Path) -> ManifestOutputContract:
    if value in (None, ""):
        return ManifestOutputContract()
    if not isinstance(value, dict):
        raise ValueError(
            f"Invalid manifest {location}: `output_contract` must be a mapping."
        )
    reserved = {"format", "required_fields", "artifact_types"}
    return ManifestOutputContract(
        format=str(value.get("format", "")),
        required_fields=_as_str_list(value.get("required_fields", [])),
        artifact_types=_as_str_list(value.get("artifact_types", [])),
        metadata={key: item for key, item in value.items() if key not in reserved},
    )


def _as_suggested_questions(value: Any) -> list[SuggestedQuestion]:
    if not isinstance(value, list):
        return []
    questions: list[SuggestedQuestion] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                questions.append(SuggestedQuestion(text=text))
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        questions.append(
            SuggestedQuestion(
                text=text,
                reason=str(item.get("reason") or "").strip(),
            )
        )
    return questions


def _validate_subagent_contract(manifest: ManifestBase) -> None:
    root = manifest.location.parent
    if manifest.location.name != "AGENT.yaml":
        raise ValueError(f"Subagent `{manifest.name}` must use AGENT.yaml.")
    if not manifest.body.strip():
        raise ValueError(f"Subagent `{manifest.name}` must provide prompt.md.")
    if manifest.execution.mode != "worker":
        raise ValueError(f"Subagent `{manifest.name}` must set execution.mode: worker.")
    execution = manifest.metadata.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    if "model_role" in execution:
        raise ValueError(
            f"Subagent `{manifest.name}` must not use execution.model_role."
        )
    if "tool_module" in execution or "context_module" in execution or "worker_profile" in execution:
        raise ValueError(
            f"Subagent `{manifest.name}` must use convention paths; "
            "execution.tool_module/context_module/worker_profile are not allowed."
        )
    if manifest.tools:
        raise ValueError(
            f"Subagent `{manifest.name}` declares legacy tools. "
            "Use extension.py register(api) instead of AGENT.yaml tools."
        )
    model = manifest.metadata.get("model")
    model = model if isinstance(model, dict) else {}
    if "llm_role" in model or "embedding_role" in model:
        raise ValueError(
            f"Subagent `{manifest.name}` must not use model role fields."
        )


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
        yaml_path = item_dir / f"{basename}.yaml"
        yml_path = item_dir / f"{basename}.yml"
        md_path = item_dir / f"{basename}.md"
        if yaml_path.exists():
            paths.append(yaml_path)
        elif yml_path.exists():
            paths.append(yml_path)
        elif md_path.exists():
            paths.append(md_path)
    return paths


def _subagent_manifest_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    paths: list[Path] = []
    for item_dir in sorted(root.iterdir()):
        if not item_dir.is_dir():
            continue
        yaml_path = item_dir / "AGENT.yaml"
        if yaml_path.exists():
            paths.append(yaml_path)
    return paths


def _subagent_signature_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    paths: list[Path] = []
    for item_dir in sorted(root.iterdir()):
        if not item_dir.is_dir():
            continue
        for filename in [
            "AGENT.yaml",
            "prompt.md",
            "extension.py",
            "ENVIRONMENT.md",
            "domain_catalog.yaml",
        ]:
            path = item_dir / filename
            if path.exists():
                paths.append(path)
    return paths
