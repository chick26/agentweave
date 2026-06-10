"""Worker subagent lifecycle, tool assembly, and result normalization."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    SQLiteSession,
)
from agents.tool import FunctionTool
from agents.tool_context import ToolContext
from pydantic import BaseModel, Field, ValidationError, model_validator

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.worker.subagent_extensions import (
    build_extension_prompt_context,
    build_extension_tools,
)
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.core.runtime_utils import (
    build_model,
    get_current_time_payload,
    json_dumps,
    make_async_client,
    to_jsonable,
)
from agent_runtime.registry.skill_registry import AgentManifest, AgentRegistry, SkillRegistry

# Plan/execute workers normally need two tool calls plus final output.
# Keep retry headroom while allowing each manifest or env var to override it.
WORKER_MAX_TURNS = int(os.getenv("WORKER_MAX_TURNS", "8"))
WORKER_TIMEOUT_SECONDS = float(os.getenv("WORKER_TIMEOUT_SECONDS", "120"))


def _subagent_enabled(manifest: AgentManifest) -> bool:
    env_key = f"{_subagent_env_prefix(manifest.name)}_ENABLED"
    enabled_value = os.getenv(env_key, "1").strip().lower()
    return enabled_value not in {"0", "false", "no", "off"}


class SubagentResult(BaseModel):
    answer: str = ""
    subagent: str = ""
    trace: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_extras(cls, data: Any) -> Any:
        if isinstance(data, dict):
            known_fields = {"answer", "subagent", "trace", "error", "artifacts", "extras"}
            extras = data.get("extras") or {}
            if not isinstance(extras, dict):
                extras = {}
            else:
                extras = dict(extras)
            for k, v in list(data.items()):
                if k not in known_fields:
                    extras[k] = data.pop(k)
            data["extras"] = extras
        return data


class SubagentToolInput(BaseModel):
    task: str = Field(
        description=(
            "Self-contained task for the worker agent, including resolved time "
            "ranges, business intent, and the user's original filter terms. "
            "Do not add inferred schema fields or enum values as facts."
        )
    )


class SubagentRunner:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        skill_registry: SkillRegistry | None = None,
        memory_manager: MemoryManager | None = None,
        result_store: Any | None = None,
        root: Path,
    ) -> None:
        self.registry = registry
        self.skill_registry = skill_registry
        self.memory_manager = memory_manager
        self.result_store = result_store
        self.root = root

    async def run_subagent(
        self,
        *,
        subagent_name: str,
        task: str,
        orchestrator_context: RuntimeContext,
    ) -> SubagentResult:
        manifest = self.registry.get(subagent_name)
        if manifest.execution.mode != "worker":
            return SubagentResult(
                answer=f"Subagent `{subagent_name}` is not configured for worker execution.",
                subagent=subagent_name,
                error=f"Unsupported execution mode: {manifest.execution.mode}",
            )

        profile = orchestrator_context.model_profile
        max_turns = self._resolve_max_turns(manifest)
        timeout_seconds = self._resolve_timeout_seconds(manifest)
        run_id = f"{subagent_name}-{uuid.uuid4().hex}"
        start_payload = {
            "stage": "worker_start",
            "skill": subagent_name,
            "subagent": subagent_name,
            "model": profile.model_name,
            "max_turns": max_turns,
            "timeout_seconds": timeout_seconds,
            "task": task,
        }
        orchestrator_context.emit_payload(
            kind=EventKind.SUBAGENT_DISPATCH,
            run_id=run_id,
            parent_run_id=orchestrator_context.run_id,
            payload=start_payload,
        )

        run_ctx = orchestrator_context.child(
            run_id=run_id,
            runtime_root=self.root,
            active_subagent=subagent_name,
            state={},
        )
        if run_ctx.result_store is None:
            run_ctx.result_store = self.result_store
        run_ctx.agent_registry = self.registry
        run_ctx.skill_registry = self.skill_registry
        memory_events: list[dict[str, Any]] = []

        def log_callback(log_entry: dict[str, Any]) -> None:
            run_ctx.emit_payload(
                kind="model_call",
                payload=to_jsonable(log_entry),
            )

        agent = self.build_worker_agent(
            manifest=manifest,
            profile=profile,
            prompt_query=task,
            timezone_name=orchestrator_context.timezone_name,
            memory_events=memory_events,
            log_callback=log_callback,
        )
        for payload in memory_events:
            orchestrator_context.emit_payload(
                kind=EventKind.MEMORY_READ,
                run_id=run_id,
                payload=payload,
            )

        try:
            result = await asyncio.wait_for(
                Runner.run(
                    agent,
                    task,
                    context=run_ctx,
                    session=SQLiteSession(run_id, ":memory:"),
                    max_turns=max_turns,
                ),
                timeout=timeout_seconds,
            )
            subagent_result = _coerce_subagent_result(result.final_output, subagent_name)
        except asyncio.TimeoutError:
            subagent_result = SubagentResult(
                subagent=subagent_name,
                trace=_run_payloads(run_ctx),
                error=f"worker_timeout: Worker timed out after {timeout_seconds:g}s.",
            )
        except Exception as exc:
            subagent_result = SubagentResult(
                answer=f"执行 subagent `{subagent_name}` 失败：{type(exc).__name__}: {exc}",
                subagent=subagent_name,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not subagent_result.subagent:
            subagent_result.subagent = subagent_name
        if not subagent_result.trace:
            subagent_result.trace = _run_payloads(run_ctx)
        complete_payload = {
            "stage": "worker_complete",
            "skill": subagent_name,
            "subagent": subagent_name,
            "result": _model_dump(subagent_result),
        }
        orchestrator_context.emit_payload(
            kind=EventKind.SUBAGENT_COMPLETE,
            run_id=run_id,
            parent_run_id=orchestrator_context.run_id,
            payload=complete_payload,
        )
        return subagent_result

    def build_worker_agent(
        self,
        *,
        manifest: AgentManifest,
        profile: Any,
        prompt_query: str = "",
        timezone_name: str = "Asia/Hong_Kong",
        memory_events: list[dict[str, Any]] | None = None,
        log_callback: Any | None = None,
    ) -> Agent[RuntimeContext]:
        model = (
            build_model(
                profile=profile,
                log_callback=log_callback,
                title=f"{manifest.name} Worker 编排模型调用",
                kind="subagent_orchestration_model",
            )
            if log_callback is not None
            else OpenAIChatCompletionsModel(
                model=profile.model_name,
                openai_client=make_async_client(profile),
            )
        )
        return Agent[RuntimeContext](
            name=f"{manifest.name}-worker",
            instructions=self._build_worker_prompt(
                manifest,
                query=prompt_query,
                timezone_name=timezone_name,
                memory_events=memory_events,
            ),
            model=model,
            model_settings=ModelSettings(
                max_tokens=profile.max_tokens,
                tool_choice="auto",
                extra_body=self._get_extra_body(manifest, profile),
            ),
            tools=self._build_subagent_tools(manifest),
        )

    def build_worker_agent_tool(
        self,
        *,
        manifest: AgentManifest,
        profile: Any,
    ) -> FunctionTool:
        """Expose a subagent as an SDK agent-as-tool while preserving isolation.

        The SDK's native Agent.as_tool() currently inherits the parent run
        context. Our worker tools require a fresh RuntimeContext per invocation, so
        this keeps the SDK tool metadata/origin and swaps in the existing
        isolated runner for the actual call.
        """
        worker_agent = self.build_worker_agent(manifest=manifest, profile=profile)
        tool = worker_agent.as_tool(
            tool_name=manifest.name,
            tool_description=self._build_worker_agent_tool_description(manifest),
            parameters=SubagentToolInput,
            input_builder=_build_subagent_input,
            custom_output_extractor=_extract_worker_agent_tool_output,
            max_turns=self._resolve_max_turns(manifest),
            session=None,
        )

        async def invoke_tool(ctx: ToolContext[Any], input_json: str) -> str:
            task_input = _parse_subagent_tool_input(input_json)
            parent_context = getattr(ctx, "context", None)
            if not isinstance(parent_context, RuntimeContext):
                raise TypeError(
                    f"Subagent tool `{manifest.name}` requires RuntimeContext."
                )
            result = await self.run_subagent(
                subagent_name=manifest.name,
                task=task_input.task,
                orchestrator_context=parent_context,
            )
            return json_dumps(_subagent_tool_payload(result))

        tool.on_invoke_tool = invoke_tool
        return tool

    def _get_extra_body(self, manifest: AgentManifest, profile: Any) -> dict[str, Any]:
        return getattr(profile, "extra_body", {}) or {}

    def _resolve_max_turns(self, manifest: AgentManifest) -> int:
        default = manifest.execution.max_turns or WORKER_MAX_TURNS
        return _env_int(f"{_subagent_env_prefix(manifest.name)}_MAX_TURNS", default)

    def _resolve_timeout_seconds(self, manifest: AgentManifest) -> float:
        default = manifest.execution.timeout_seconds or WORKER_TIMEOUT_SECONDS
        return _env_float(f"{_subagent_env_prefix(manifest.name)}_TIMEOUT_SECONDS", default)

    def _build_worker_agent_tool_description(self, manifest: AgentManifest) -> str:
        lines = [
            f"Run the `{manifest.name}` subagent as an isolated SDK agent tool.",
            manifest.description,
            "",
            "Use this when the user task matches this delegated capability.",
            "Provide a self-contained task with resolved dates, business intent, and user-provided filter terms; do not invent schema fields or enum values.",
        ]
        if manifest.routing_hints:
            lines.append(f"Route when: {', '.join(manifest.routing_hints)}")
        return "\n".join(line for line in lines if line)

    def _build_worker_prompt(
        self,
        manifest: AgentManifest,
        *,
        query: str = "",
        timezone_name: str = "Asia/Hong_Kong",
        memory_events: list[dict[str, Any]] | None = None,
    ) -> str:
        template = manifest.body.strip() or manifest.description
        context = {
            "current_time": json_dumps(get_current_time_payload(timezone_name)),
            "memory": self.memory_manager.build_skill_context(
                manifest,
                query=query,
                retrieval_events=memory_events,
            )
            if self.memory_manager is not None
            else "",
        }
        context.update(self._load_prompt_context(manifest))
        for key, value in context.items():
            template = template.replace(f"{{{key}}}", str(value or ""))
        return template

    def _load_prompt_context(self, manifest: AgentManifest) -> dict[str, Any]:
        return build_extension_prompt_context(manifest)

    def _build_subagent_tools(self, manifest: AgentManifest) -> list[Any]:
        if not _subagent_enabled(manifest):
            return []
        if manifest.tools:
            raise ValueError(
                f"Subagent `{manifest.name}` declares legacy tools. "
                "Use extension.py register(api) instead of AGENT.yaml tools."
            )
        tools_by_name: dict[str, Any] = {}
        for tool in build_extension_tools(manifest):
            tools_by_name[_tool_name(tool)] = tool
        return list(tools_by_name.values())


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", "")
    if name:
        return str(name)
    name = getattr(tool, "__name__", "")
    if name:
        return str(name)
    raise ValueError(f"Registered subagent tool is missing a name: {tool!r}")


def _coerce_subagent_result(value: Any, subagent_name: str) -> SubagentResult:
    if isinstance(value, SubagentResult):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            payload = json.loads(_extract_json_text(text))
        except json.JSONDecodeError:
            return SubagentResult(answer=text, subagent=subagent_name)
        if isinstance(payload, dict):
            return _validate_subagent_payload(payload, subagent_name, raw_output=text)
    if isinstance(value, dict):
        return _validate_subagent_payload(value, subagent_name, raw_output=json_dumps(value))
    return SubagentResult(answer=str(value), subagent=subagent_name)


def _validate_subagent_payload(
    payload: dict[str, Any],
    subagent_name: str,
    *,
    raw_output: str,
) -> SubagentResult:
    payload = _normalize_subagent_payload(payload)
    try:
        return SubagentResult.model_validate(payload)
    except ValidationError as exc:
        return SubagentResult(
            subagent=subagent_name,
            trace=[
                {
                    "stage": "invalid_subagent_output",
                    "error": str(exc),
                    "raw_output": raw_output[:2000],
                }
            ],
            error=f"invalid_subagent_output: {exc.errors()}",
        )


def _normalize_subagent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    trace = payload.get("trace")
    if not isinstance(trace, list):
        return payload
    normalized_trace = [
        item if isinstance(item, dict) else {"stage": "note", "message": str(item)}
        for item in trace
    ]
    return {**payload, "trace": normalized_trace}


def _run_payloads(context: RuntimeContext) -> list[dict[str, Any]]:
    return [
        event.get("payload", {})
        for event in context.events
        if event.get("run_id") == context.run_id
    ]


def _parse_subagent_tool_input(input_json: str) -> SubagentToolInput:
    try:
        payload = json.loads(input_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON input for subagent tool: {exc}") from exc
    if isinstance(payload, dict) and "task" not in payload and "input" in payload:
        payload = {**payload, "task": payload["input"]}
    return SubagentToolInput.model_validate(payload)


def _build_subagent_input(options: dict[str, Any]) -> str:
    params = options.get("params", {})
    if isinstance(params, SubagentToolInput):
        return params.task
    if isinstance(params, dict):
        return str(params.get("task") or params.get("input") or "")
    return str(params)


async def _extract_worker_agent_tool_output(value: Any) -> str:
    final_output = getattr(value, "final_output", value)
    result = _coerce_subagent_result(final_output, "")
    return json_dumps(_subagent_tool_payload(result))


def _subagent_tool_payload(result: SubagentResult) -> dict[str, Any]:
    payload = {
        "answer": result.answer,
        "error": result.error,
        "subagent": result.subagent,
        "artifacts": result.artifacts,
        "extras": result.extras,
    }
    return payload


def _subagent_env_prefix(subagent_name: str) -> str:
    chars = [
        char.upper() if char.isalnum() else "_"
        for char in subagent_name
    ]
    collapsed = "_".join(part for part in "".join(chars).split("_") if part)
    return f"SUBAGENT_{collapsed}"



def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _extract_json_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _model_dump(value: BaseModel) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()
