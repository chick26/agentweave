"""Worker subagent lifecycle and isolation boundary."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from agents import (
    Runner,
    SQLiteSession,
)
from agents.tool import FunctionTool

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.core.runtime_utils import (
    to_jsonable,
)
from agent_runtime.worker.agent_factory import (
    SubagentAgentFactory,
    subagent_env_prefix,
)
from agent_runtime.worker.result_normalizer import (
    SubagentResult,
    SubagentToolInput,
    coerce_subagent_result,
    model_dump,
    run_payloads,
    _coerce_subagent_result,
    _subagent_tool_payload,
)
from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.registry.manifest_models import AgentManifest
from agent_runtime.registry.skill_registry import SkillRegistry

# Plan/execute workers normally need two tool calls plus final output.
# Keep retry headroom while allowing each manifest or env var to override it.
WORKER_MAX_TURNS = int(os.getenv("WORKER_MAX_TURNS", "8"))
WORKER_TIMEOUT_SECONDS = float(os.getenv("WORKER_TIMEOUT_SECONDS", "120"))


class SubagentRunner:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        skill_registry: SkillRegistry | None = None,
        memory_manager: MemoryManager | None = None,
        artifact_store: Any | None = None,
        root: Path,
    ) -> None:
        self.registry = registry
        self.skill_registry = skill_registry
        self.memory_manager = memory_manager
        self.artifact_store = artifact_store
        self.root = root
        self.agent_factory = SubagentAgentFactory(memory_manager=memory_manager)

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
        if run_ctx.artifact_store is None:
            run_ctx.artifact_store = self.artifact_store
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
            subagent_result = coerce_subagent_result(result.final_output, subagent_name)
        except asyncio.TimeoutError:
            subagent_result = SubagentResult(
                subagent=subagent_name,
                trace=run_payloads(run_ctx),
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
            subagent_result.trace = run_payloads(run_ctx)
        complete_payload = {
            "stage": "worker_complete",
            "subagent": subagent_name,
            "result": model_dump(subagent_result),
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
    ) -> Any:
        return self.agent_factory.build_worker_agent(
            manifest=manifest,
            profile=profile,
            prompt_query=prompt_query,
            timezone_name=timezone_name,
            memory_events=memory_events,
            log_callback=log_callback,
        )

    def build_worker_agent_tool(
        self,
        *,
        manifest: AgentManifest,
        profile: Any,
    ) -> FunctionTool:
        async def run_worker(task: str, parent_context: RuntimeContext) -> SubagentResult:
            return await self.run_subagent(
                subagent_name=manifest.name,
                task=task,
                orchestrator_context=parent_context,
            )

        return self.agent_factory.build_worker_agent_tool(
            manifest=manifest,
            profile=profile,
            max_turns=self._resolve_max_turns(manifest),
            run_worker=run_worker,
        )

    def _build_worker_prompt(
        self,
        manifest: AgentManifest,
        *,
        query: str = "",
        timezone_name: str = "Asia/Hong_Kong",
        memory_events: list[dict[str, Any]] | None = None,
    ) -> str:
        return self.agent_factory.build_worker_prompt(
            manifest,
            query=query,
            timezone_name=timezone_name,
            memory_events=memory_events,
        )

    def _build_subagent_tools(self, manifest: AgentManifest) -> list[Any]:
        return self.agent_factory.build_subagent_tools(manifest)

    def _resolve_max_turns(self, manifest: AgentManifest) -> int:
        default = manifest.execution.max_turns or WORKER_MAX_TURNS
        return _env_int(f"{subagent_env_prefix(manifest.name)}_MAX_TURNS", default)

    def _resolve_timeout_seconds(self, manifest: AgentManifest) -> float:
        default = manifest.execution.timeout_seconds or WORKER_TIMEOUT_SECONDS
        return _env_float(f"{subagent_env_prefix(manifest.name)}_TIMEOUT_SECONDS", default)


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
