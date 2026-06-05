"""Subagent extension registration API and loader."""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime.registry.skill_registry import AgentManifest


PromptContextFn = Callable[[AgentManifest], dict[str, Any] | None]
ValidateEnvironmentFn = Callable[[AgentManifest], None]


@dataclass
class SubagentExtension:
    module_name: str
    tools: list[Any] = field(default_factory=list)
    validators: list[ValidateEnvironmentFn] = field(default_factory=list)
    prompt_contexts: list[PromptContextFn] = field(default_factory=list)


class SubagentExtensionAPI:
    """Small registration surface exposed to subagent-owned extensions."""

    def __init__(self, *, manifest: AgentManifest) -> None:
        self.manifest = manifest
        self._tools: list[Any] = []
        self._validators: list[ValidateEnvironmentFn] = []
        self._prompt_contexts: list[PromptContextFn] = []

    def tool(self, tool_obj: Any) -> None:
        self._tools.append(tool_obj)

    def validate_environment(self, fn: ValidateEnvironmentFn) -> None:
        self._validators.append(fn)

    def prompt_context(self, fn: PromptContextFn) -> None:
        self._prompt_contexts.append(fn)

    def on_event(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("Extension event hooks are reserved for a future runtime.")

    def build(self, *, module_name: str) -> SubagentExtension:
        return SubagentExtension(
            module_name=module_name,
            tools=list(self._tools),
            validators=list(self._validators),
            prompt_contexts=list(self._prompt_contexts),
        )


_extension_cache: dict[str, SubagentExtension] = {}


def load_subagent_extension(manifest: AgentManifest) -> SubagentExtension | None:
    module_name = manifest.extension.module
    if not module_name:
        return None
    if module_name in _extension_cache:
        return _extension_cache[module_name]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        module = _load_extension_from_manifest_path(
            manifest=manifest,
            module_name=module_name,
        )
    register = getattr(module, "register", None)
    if not callable(register):
        raise ValueError(
            f"Subagent `{manifest.name}` extension `{module_name}` must expose register(api)."
        )
    api = SubagentExtensionAPI(manifest=manifest)
    register(api)
    extension = api.build(module_name=module_name)
    _extension_cache[module_name] = extension
    return extension


def build_extension_tools(manifest: AgentManifest) -> list[Any]:
    extension = load_subagent_extension(manifest)
    return list(extension.tools) if extension is not None else []


def build_extension_prompt_context(manifest: AgentManifest) -> dict[str, Any]:
    extension = load_subagent_extension(manifest)
    if extension is None:
        return {}
    context: dict[str, Any] = {}
    for fn in extension.prompt_contexts:
        payload = fn(manifest)
        if isinstance(payload, dict):
            context.update(payload)
    return context


def validate_extension_environment(manifest: AgentManifest) -> None:
    extension = load_subagent_extension(manifest)
    if extension is None:
        return
    for fn in extension.validators:
        fn(manifest)


def _load_extension_from_manifest_path(*, manifest: AgentManifest, module_name: str) -> Any:
    path = manifest.location.parent / "extension.py"
    if not path.exists():
        raise ModuleNotFoundError(module_name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load subagent extension {module_name} from {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
