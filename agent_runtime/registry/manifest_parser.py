"""Manifest file parsing and contract validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml

from agent_runtime.common import split_frontmatter
from agent_runtime.registry.manifest_models import (
    AgentManifest,
    ManifestBase,
    ManifestDomains,
    ManifestExecution,
    ManifestExtension,
    ManifestMemory,
    ManifestModel,
    ManifestOutputContract,
    Skill,
    SuggestedQuestion,
)


ManifestT = TypeVar("ManifestT", bound=ManifestBase)


def read_yaml_manifest(
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
    manifest = build_manifest_from_metadata(
        metadata=metadata,
        location=path,
        body=body,
        default_name=path.parent.name,
        manifest_cls=manifest_cls,
        kind=kind,
    )
    if kind == "subagent":
        validate_subagent_contract(manifest)
    return manifest


def read_markdown_manifest(
    path: Path,
    *,
    manifest_cls: type[ManifestT],
    kind: str,
) -> ManifestT:
    text = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(text)
    return build_manifest_from_metadata(
        metadata=metadata,
        location=path,
        body=body.strip(),
        default_name=path.parent.name,
        manifest_cls=manifest_cls,
        kind=kind,
    )


def build_manifest_from_metadata(
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


def manifest_paths(root: Path, basename: str) -> list[Path]:
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


def subagent_manifest_paths(root: Path) -> list[Path]:
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


def subagent_signature_paths(root: Path) -> list[Path]:
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


def as_str_list(value: Any) -> list[str]:
    return _as_str_list(value)


def validate_subagent_contract(manifest: ManifestBase) -> None:
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


__all__ = [
    "as_str_list",
    "build_manifest_from_metadata",
    "manifest_paths",
    "read_markdown_manifest",
    "read_yaml_manifest",
    "subagent_manifest_paths",
    "subagent_signature_paths",
    "validate_subagent_contract",
]
