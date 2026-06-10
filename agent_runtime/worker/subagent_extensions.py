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
from agent_runtime.core.result_formatters import ResultFormatter, ResultFormatterRegistry
from agent_runtime.core.tool_helpers import emit_tool_finish, emit_tool_start
from agent_runtime.core.tool_helpers import ToolOutput
from agent_runtime.registry.skill_registry import AgentManifest


PromptContextFn = Callable[[AgentManifest], dict[str, Any] | None]
ValidateEnvironmentFn = Callable[[AgentManifest], None]
CapabilityResolverFn = Callable[[AgentManifest], dict[str, Any] | None]


@dataclass
class ToolPolicyBinding:
    tool_name: str
    capability: str = ""
    policy_path: str = ""
    audit_name: str = ""


@dataclass
class SubagentExtension:
    module_name: str
    tools: list[Any] = field(default_factory=list)
    tool_policies: dict[str, ToolPolicyBinding] = field(default_factory=dict)
    validators: list[ValidateEnvironmentFn] = field(default_factory=list)
    prompt_contexts: list[PromptContextFn] = field(default_factory=list)
    result_formatters: list[ResultFormatter] = field(default_factory=list)
    capability_resolvers: list[CapabilityResolverFn] = field(default_factory=list)


class SubagentExtensionAPI:
    """Small registration surface exposed to subagent-owned extensions."""

    def __init__(self, *, manifest: AgentManifest) -> None:
        self.manifest = manifest
        self._tools: list[Any] = []
        self._validators: list[ValidateEnvironmentFn] = []
        self._prompt_contexts: list[PromptContextFn] = []
        self._result_formatters: list[ResultFormatter] = []
        self._capability_resolvers: list[CapabilityResolverFn] = []
        self._tool_policies: dict[str, ToolPolicyBinding] = {}

    def tool(
        self,
        tool_obj: Any | None = None,
        fn: Callable[..., Any] | None = None,
        *,
        name: str = "",
        description: str = "",
        capability: str = "",
        policy_path: str = "",
        audit_name: str = "",
    ) -> Any:
        """Register a subagent-local tool.

        Existing SDK FunctionTool objects are accepted as-is for current
        extensions. Raw callables are wrapped with SDK function_tool and get
        standard tool_start/tool_result/tool_end events automatically.
        """
        if tool_obj is None and fn is None:
            def decorator(inner: Callable[..., Any]) -> Any:
                self.tool(
                    inner,
                    name=name,
                    description=description,
                    capability=capability,
                    policy_path=policy_path,
                    audit_name=audit_name,
                )
                return inner

            return decorator
        if isinstance(tool_obj, str):
            name = tool_obj
            tool_obj = fn
        if tool_obj is None:
            raise TypeError("tool expects a callable or FunctionTool.")
        tool = _prepare_tool(tool_obj, name=name, description=description)
        _validate_tool(tool)
        tool_name = _tool_name(tool)
        binding = self._build_tool_policy_binding(
            tool_name=tool_name,
            capability=capability,
            policy_path=policy_path,
            audit_name=audit_name,
        )
        setattr(tool, "_agentweave_tool_policy", binding)
        self._tools.append(tool)
        self._tool_policies[tool_name] = binding
        return tool_obj

    def validate_environment(self, fn: ValidateEnvironmentFn) -> None:
        if not callable(fn):
            raise TypeError("validate_environment expects a callable.")
        self._validators.append(fn)

    def prompt_context(self, fn: PromptContextFn) -> None:
        if not callable(fn):
            raise TypeError("prompt_context expects a callable.")
        self._prompt_contexts.append(fn)

    def result_formatter(self, formatter: ResultFormatter) -> None:
        if not getattr(formatter, "artifact_type", ""):
            raise ValueError("result_formatter expects formatter.artifact_type.")
        if not callable(getattr(formatter, "format", None)):
            raise TypeError("result_formatter expects a formatter with format(payload).")
        self._result_formatters.append(formatter)

    def capability_resolver(self, fn: CapabilityResolverFn) -> None:
        if not callable(fn):
            raise TypeError("capability_resolver expects a callable.")
        self._capability_resolvers.append(fn)

    def build(self, *, module_name: str) -> SubagentExtension:
        return SubagentExtension(
            module_name=module_name,
            tools=list(self._tools),
            tool_policies=dict(self._tool_policies),
            validators=list(self._validators),
            prompt_contexts=list(self._prompt_contexts),
            result_formatters=list(self._result_formatters),
            capability_resolvers=list(self._capability_resolvers),
        )

    def _build_tool_policy_binding(
        self,
        *,
        tool_name: str,
        capability: str = "",
        policy_path: str = "",
        audit_name: str = "",
    ) -> ToolPolicyBinding:
        clean_capability = str(capability or "").strip()
        clean_policy_path = str(policy_path or "").strip()
        clean_audit_name = str(audit_name or "").strip() or tool_name
        if not clean_capability:
            raise ValueError(
                f"Subagent `{self.manifest.name}` tool `{tool_name}` must declare "
                "a capability."
            )
        if clean_capability not in self.manifest.capabilities:
            raise ValueError(
                f"Subagent `{self.manifest.name}` tool `{tool_name}` declares unknown "
                f"capability `{clean_capability}`."
            )
        if clean_policy_path:
            _policy_at_path(self.manifest.policies, clean_policy_path)
        return ToolPolicyBinding(
            tool_name=tool_name,
            capability=clean_capability,
            policy_path=clean_policy_path,
            audit_name=clean_audit_name,
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


def register_extension_result_formatters(
    manifests: list[AgentManifest],
    registry: ResultFormatterRegistry,
) -> None:
    for manifest in manifests:
        extension = load_subagent_extension(manifest)
        if extension is None:
            continue
        for formatter in extension.result_formatters:
            registry.register(formatter)


def resolve_extension_capabilities(manifest: AgentManifest) -> dict[str, Any]:
    extension = load_subagent_extension(manifest)
    if extension is None:
        return {}
    payload: dict[str, Any] = {}
    for fn in extension.capability_resolvers:
        resolved = fn(manifest)
        if isinstance(resolved, dict):
            payload.update(resolved)
    return payload


def resolve_tool_audit_metadata(
    *,
    manifest: AgentManifest,
    tool_name: str,
) -> dict[str, Any]:
    extension = load_subagent_extension(manifest)
    binding = None
    if extension is not None:
        binding = extension.tool_policies.get(tool_name)
    if binding is None:
        raise ValueError(
            f"Subagent `{manifest.name}` tool `{tool_name}` is missing policy metadata."
        )
    policy_snapshot: Any = {}
    if binding.policy_path:
        policy_snapshot = _policy_at_path(manifest.policies, binding.policy_path)
    return {
        "subagent": manifest.name,
        "tool": tool_name,
        "capability": binding.capability,
        "policy_path": binding.policy_path,
        "policy_snapshot": _jsonable_policy(policy_snapshot),
        "audit_name": binding.audit_name or tool_name,
        "scoped": True,
    }


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


def _tool_name(tool_obj: Any) -> str:
    name = getattr(tool_obj, "name", "") or getattr(tool_obj, "__name__", "")
    if not str(name or "").strip():
        raise ValueError(f"Registered subagent tool is missing a name: {tool_obj!r}")
    return str(name)


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


def _policy_at_path(policies: dict[str, Any], path: str) -> Any:
    current: Any = policies
    for part in [item for item in path.split(".") if item]:
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Unknown policy path: {path}")
        current = current[part]
    return current


def _jsonable_policy(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)
    return value
