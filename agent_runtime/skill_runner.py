from __future__ import annotations

import asyncio
import importlib
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
from pydantic import BaseModel, Field, ValidationError

from agent_runtime.context import OrchestratorContext, RunContext
from agent_runtime.memory_manager import MemoryManager
from agent_runtime.runtime_utils import build_model, json_dumps, make_async_client, to_jsonable
from agent_runtime.skill_registry import AgentManifest, AgentRegistry, SkillRegistry

# Plan/execute workers normally need two tool calls plus final output.
# Keep retry headroom while allowing each manifest or env var to override it.
WORKER_MAX_TURNS = int(os.getenv("WORKER_MAX_TURNS", "8"))
WORKER_TIMEOUT_SECONDS = float(os.getenv("WORKER_TIMEOUT_SECONDS", "120"))


_loaded_modules: dict[str, Any] = {}
_loaded_context_modules: dict[str, Any] = {}


def _load_subagent_module(manifest: AgentManifest) -> Any | None:
    env_key = f"{_subagent_env_prefix(manifest.name)}_ENABLED"
    enabled_value = os.getenv(env_key, "1").strip().lower()
    if enabled_value in {"0", "false", "no", "off"}:
        return None
    module_name = manifest.execution.tool_module.strip()
    if not module_name:
        if manifest.tools:
            raise ValueError(
                f"Subagent `{manifest.name}` declares tools but execution.tool_module is missing."
            )
        return None
    if module_name in _loaded_modules:
        return _loaded_modules[module_name]
    module = importlib.import_module(module_name)
    _loaded_modules[module_name] = module
    return module


def _load_subagent_context_module(manifest: AgentManifest) -> Any | None:
    module_name = manifest.execution.context_module.strip()
    if not module_name:
        return None
    if module_name in _loaded_context_modules:
        return _loaded_context_modules[module_name]
    module = importlib.import_module(module_name)
    _loaded_context_modules[module_name] = module
    return module


class SubagentResult(BaseModel):
    answer: str = ""
    skill: str = ""
    subagent: str = ""
    domain: str = ""
    sql: str = ""
    result_id: str = ""
    row_count: int = 0
    truncated: bool = False
    rows: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""


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
        orchestrator_context: OrchestratorContext,
    ) -> SubagentResult:
        manifest = self.registry.get(subagent_name)
        if manifest.execution.mode != "worker":
            return SubagentResult(
                answer=f"Subagent `{subagent_name}` is not configured for worker execution.",
                skill=subagent_name,
                subagent=subagent_name,
                error=f"Unsupported execution mode: {manifest.execution.mode}",
            )

        model_role = self._resolve_model_role(manifest)
        if not model_role:
            return SubagentResult(
                answer=f"Subagent `{subagent_name}` is missing execution.model_role.",
                skill=subagent_name,
                subagent=subagent_name,
                error="Missing model role",
            )
        profile = orchestrator_context.model_profiles[model_role]
        max_turns = self._resolve_max_turns(manifest)
        timeout_seconds = self._resolve_timeout_seconds(manifest)
        run_id = f"{subagent_name}-{uuid.uuid4().hex}"
        orchestrator_context.emit_payload(
            kind="worker_run",
            run_id=run_id,
            payload={
                "stage": "worker_start",
                "skill": subagent_name,
                "subagent": subagent_name,
                "model_role": model_role,
                "model": profile.model_name,
                "max_turns": max_turns,
                "timeout_seconds": timeout_seconds,
                "task": task,
            },
        )

        run_ctx = RunContext(
            run_id=run_id,
            backend=orchestrator_context.backend,
            model_profiles=orchestrator_context.model_profiles,
            result_store=orchestrator_context.result_store or self.result_store,
            event_callback=orchestrator_context.emit,
            timezone_name=orchestrator_context.timezone_name,
            agent_registry=self.registry,
            skill_registry=self.skill_registry,
        )
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
            memory_events=memory_events,
            log_callback=log_callback,
        )
        for payload in memory_events:
            orchestrator_context.emit_payload(
                kind="memory_event",
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
            skill_result = _coerce_subagent_result(result.final_output, subagent_name)
        except asyncio.TimeoutError:
            skill_result = _fallback_result_from_trace(
                run_ctx=run_ctx,
                skill_name=subagent_name,
                error=(
                    f"Worker timed out after {timeout_seconds:g}s; "
                    "returning the latest tool result."
                ),
            )
        except Exception as exc:
            skill_result = SubagentResult(
                answer=f"执行 subagent `{subagent_name}` 失败：{type(exc).__name__}: {exc}",
                skill=subagent_name,
                subagent=subagent_name,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not skill_result.subagent:
            skill_result.subagent = skill_result.skill or subagent_name
        if not skill_result.skill:
            skill_result.skill = skill_result.subagent
        if not skill_result.trace:
            skill_result.trace = [event.get("payload", {}) for event in run_ctx.events]
        orchestrator_context.emit_payload(
            kind="worker_run",
            run_id=run_id,
            payload={
                "stage": "worker_complete",
                "skill": subagent_name,
                "subagent": subagent_name,
                "result": _model_dump(skill_result),
            },
        )
        return skill_result

    async def run_skill(
        self,
        *,
        skill_name: str,
        task: str,
        orchestrator_context: OrchestratorContext,
    ) -> SubagentResult:
        """Compatibility alias; worker execution is now subagent-only."""
        return await self.run_subagent(
            subagent_name=skill_name,
            task=task,
            orchestrator_context=orchestrator_context,
        )

    def build_worker_agent(
        self,
        *,
        manifest: AgentManifest,
        profile: Any,
        prompt_query: str = "",
        memory_events: list[dict[str, Any]] | None = None,
        log_callback: Any | None = None,
    ) -> Agent[RunContext]:
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
        return Agent[RunContext](
            name=f"{manifest.name}-worker",
            instructions=self._build_worker_prompt(
                manifest,
                query=prompt_query,
                memory_events=memory_events,
            ),
            model=model,
            model_settings=ModelSettings(
                max_tokens=profile.max_tokens,
                tool_choice="auto",
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
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
        context. Our worker tools require a fresh RunContext per invocation, so
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
            if not isinstance(parent_context, OrchestratorContext):
                raise TypeError(
                    f"Subagent tool `{manifest.name}` requires OrchestratorContext."
                )
            result = await self.run_subagent(
                subagent_name=manifest.name,
                task=task_input.task,
                orchestrator_context=parent_context,
            )
            return result.answer or json_dumps(_model_dump(result))

        tool.on_invoke_tool = invoke_tool
        return tool

    def _resolve_model_role(self, manifest: AgentManifest) -> str:
        model_role = manifest.execution.model_role
        if manifest.name == "text2sql":
            model_role = os.getenv("TEXT2SQL_WORKER_MODEL_ROLE", model_role)
        model_role = os.getenv(f"{_subagent_env_prefix(manifest.name)}_MODEL_ROLE", model_role)
        return model_role

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
        memory_events: list[dict[str, Any]] | None = None,
    ) -> str:
        template = manifest.body.strip() or manifest.description
        context = {
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
        module = _load_subagent_context_module(manifest)
        if module is None or not hasattr(module, "build_prompt_context"):
            return {}
        context = module.build_prompt_context(manifest)
        return context if isinstance(context, dict) else {}

    def _build_subagent_tools(self, manifest: AgentManifest) -> list[Any]:
        module = _load_subagent_module(manifest)
        if module is None:
            return []
        return [
            getattr(module, name)
            for name in manifest.tools
            if hasattr(module, name)
        ]


def _coerce_subagent_result(value: Any, subagent_name: str) -> SubagentResult:
    if isinstance(value, SubagentResult):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            payload = json.loads(_extract_json_text(text))
        except json.JSONDecodeError:
            return SubagentResult(answer=text, skill=subagent_name, subagent=subagent_name)
        if isinstance(payload, dict):
            return _validate_subagent_payload(payload, subagent_name, raw_output=text)
    if isinstance(value, dict):
        return _validate_subagent_payload(value, subagent_name, raw_output=json_dumps(value))
    return SubagentResult(answer=str(value), skill=subagent_name, subagent=subagent_name)


def _validate_subagent_payload(
    payload: dict[str, Any],
    subagent_name: str,
    *,
    raw_output: str,
) -> SubagentResult:
    try:
        return SubagentResult.model_validate(payload)
    except ValidationError as exc:
        return SubagentResult(
            skill=subagent_name,
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


def _fallback_result_from_trace(
    *,
    run_ctx: RunContext,
    skill_name: str,
    error: str,
) -> SubagentResult:
    execute_payload = _last_trace_payload(run_ctx.events, "execute")
    if execute_payload is None:
        return SubagentResult(
            skill=skill_name,
            subagent=skill_name,
            domain=run_ctx.active_domain,
            trace=[event.get("payload", {}) for event in run_ctx.events],
            error=f"worker_timeout: {error}",
        )
    output = execute_payload.get("output")
    if not isinstance(output, dict):
        output = {}
    rows = output.get("sample_rows", output.get("rows", []))
    if not isinstance(rows, list):
        rows = []
    sql = str(output.get("sql") or execute_payload.get("input") or "")
    result_id = str(output.get("result_id") or "")
    return SubagentResult(
        skill=skill_name,
        subagent=skill_name,
        domain=run_ctx.active_domain,
        sql=sql,
        result_id=result_id,
        row_count=int(output.get("row_count") or len(rows)),
        truncated=bool(output.get("truncated")),
        rows=rows,
        trace=[event.get("payload", {}) for event in run_ctx.events],
        error=f"worker_timeout: {error}",
    )


def _last_trace_payload(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("kind") != "subagent_trace":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("stage") == stage:
            return payload
    return None


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
    return result.answer or json_dumps(_model_dump(result))


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
