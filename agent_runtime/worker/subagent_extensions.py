"""Subagent extension registration API and loader."""

from __future__ import annotations

import importlib
import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Callable

from agents import function_tool
from agents.tool import FunctionTool

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.tool_helpers import emit_tool_finish, emit_tool_start
from agent_runtime.core.tool_protocol import ToolOutput
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

    def tool(
        self,
        tool_obj: Any | None = None,
        fn: Callable[..., Any] | None = None,
        *,
        name: str = "",
        description: str = "",
    ) -> Any:
        """Register a subagent-local tool.

        Existing SDK FunctionTool objects are accepted as-is for current
        extensions. Raw callables are wrapped with SDK function_tool and get
        standard tool_start/tool_result/tool_end events automatically.
        """
        if tool_obj is None and fn is None:
            def decorator(inner: Callable[..., Any]) -> Any:
                self.tool(inner, name=name, description=description)
                return inner

            return decorator
        if isinstance(tool_obj, str):
            name = tool_obj
            tool_obj = fn
        if tool_obj is None:
            raise TypeError("tool expects a callable or FunctionTool.")
        tool = _prepare_tool(tool_obj, name=name, description=description)
        _validate_tool(tool)
        self._tools.append(tool)
        return tool_obj

    def validate_environment(self, fn: ValidateEnvironmentFn) -> None:
        if not callable(fn):
            raise TypeError("validate_environment expects a callable.")
        self._validators.append(fn)

    def prompt_context(self, fn: PromptContextFn) -> None:
        if not callable(fn):
            raise TypeError("prompt_context expects a callable.")
        self._prompt_contexts.append(fn)

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
    if (manifest.location.parent / "extension.py").exists():
        module = _load_extension_from_manifest_path(
            manifest=manifest,
            module_name=module_name,
        )
    else:
        module = importlib.import_module(module_name)
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


def clear_subagent_extension_cache() -> None:
    for module_name in list(_extension_cache):
        sys.modules.pop(module_name, None)
    _extension_cache.clear()
    importlib.invalidate_caches()


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
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = module_name.rpartition(".")[0]
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    sys.modules[module_name] = module
    try:
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _validate_tool(tool_obj: Any) -> None:
    name = getattr(tool_obj, "name", "") or getattr(tool_obj, "__name__", "")
    if not str(name or "").strip():
        raise ValueError(f"Registered subagent tool is missing a name: {tool_obj!r}")


def _prepare_tool(tool_obj: Any, *, name: str = "", description: str = "") -> Any:
    if isinstance(tool_obj, FunctionTool):
        return tool_obj
    if not callable(tool_obj):
        raise TypeError("tool expects a callable or FunctionTool.")
    tool = function_tool(tool_obj)
    if name:
        tool.name = name
    if description:
        tool.description = description
    return _with_standard_tool_events(tool)


def _with_standard_tool_events(tool: FunctionTool) -> FunctionTool:
    original_invoke = tool.on_invoke_tool

    async def invoke(ctx: Any, input_json: str) -> Any:
        run_ctx = getattr(ctx, "context", None)
        if isinstance(run_ctx, RuntimeContext):
            emit_tool_start(
                run_ctx,
                tool_name=tool.name,
                input_payload=_json_object(input_json),
            )
        try:
            result = await original_invoke(ctx, input_json)
        except Exception as exc:
            if isinstance(run_ctx, RuntimeContext):
                output = ToolOutput(
                    llm_content={"error": f"{type(exc).__name__}: {exc}"},
                    ui_content={"error": f"{type(exc).__name__}: {exc}"},
                    metadata={"tool_name": tool.name, "error": f"{type(exc).__name__}: {exc}"},
                )
                emit_tool_finish(
                    run_ctx,
                    tool_name=tool.name,
                    tool_output=output,
                    status="failed",
                )
            raise
        if isinstance(run_ctx, RuntimeContext):
            payload = _tool_result_payload(result)
            output = ToolOutput(
                llm_content=payload,
                ui_content=payload,
                metadata={
                    "tool_name": tool.name,
                    "error": _payload_error(payload),
                },
            )
            emit_tool_finish(
                run_ctx,
                tool_name=tool.name,
                tool_output=output,
                status="failed" if output.metadata.get("error") else "completed",
            )
        return result

    tool.on_invoke_tool = invoke
    return tool


def _json_object(input_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(input_json)
    except json.JSONDecodeError:
        return {"raw": input_json}
    return payload if isinstance(payload, dict) else {"value": payload}


def _tool_result_payload(result: Any) -> Any:
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    return result


def _payload_error(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("error") or "")
    return ""
