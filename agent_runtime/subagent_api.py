"""Stable public API surface for AgentWeave subagent extensions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TypeAlias, TypeVar

from agents import RunContextWrapper, function_tool

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.result_formatters import (
    ResultArtifactSpec,
    ResultFormatter,
    ResultFormatterRegistry,
)
from agent_runtime.core.runtime_utils import call_chat_model, make_async_client
from agent_runtime.core.tool_helpers import emit_tool_finish as _emit_tool_finish
from agent_runtime.core.tool_helpers import emit_tool_start as _emit_tool_start
from agent_runtime.core.tool_helpers import ToolOutput
from agent_runtime.shared.embeddings import EmbeddingClient, EmbeddingProfile
from agent_runtime.shared.manifest import AgentManifest
from agent_runtime.shared.models import ModelProfile, resolve_manifest_embedding_profile
from agent_runtime.worker.subagent_extensions import SubagentExtensionAPI
from agent_runtime.worker.subagent_extensions import load_subagent_extension


SubagentToolContext: TypeAlias = RunContextWrapper[Any]
tool = function_tool
T = TypeVar("T")


class SubagentContext:
    """Stable facade over the internal runtime context exposed to subagents."""

    def __init__(self, runtime_context: RuntimeContext) -> None:
        self._ctx = runtime_context

    @property
    def run_id(self) -> str:
        return self._ctx.run_id

    @property
    def runtime_root(self) -> Path:
        return self._ctx.runtime_root or Path.cwd()

    @property
    def timezone_name(self) -> str:
        return self._ctx.timezone_name

    @property
    def cache(self) -> dict[str, Any]:
        return self._ctx.state

    def typed_state(self, namespace: str, factory: Callable[[], T]) -> T:
        return self._ctx.get_typed_state(namespace, factory)

    @property
    def manifest(self) -> AgentManifest:
        registry = self._ctx.agent_registry
        if registry is None:
            raise RuntimeError("Subagent manifest registry is unavailable.")
        return registry.get(self._ctx.active_subagent)

    @property
    def capabilities(self) -> list[str]:
        return list(self.manifest.capabilities)

    @property
    def policies(self) -> dict[str, Any]:
        return dict(self.manifest.policies)

    @property
    def output_contract(self) -> dict[str, Any]:
        contract = self.manifest.output_contract
        return {
            "format": contract.format,
            "required_fields": list(contract.required_fields),
            "artifact_types": list(contract.artifact_types),
            **dict(contract.metadata),
        }

    def trace(
        self,
        *,
        stage: str,
        title: str = "",
        input: Any = None,
        output: Any = None,
        **extra: Any,
    ) -> None:
        payload = {
            "stage": stage,
            "title": title or stage,
            "input": input,
            "output": output,
            **extra,
        }
        self._ctx.emit_subagent_trace(payload)

    def emit_tool_result(
        self,
        *,
        tool_name: str,
        output: ToolOutput,
        status: str = "completed",
    ) -> None:
        _emit_tool_finish(
            self._ctx,
            tool_name=tool_name,
            tool_output=output,
            status=status,
        )

    def model_profile(self) -> ModelProfile:
        return self._ctx.model_profile

    async def call_model(
        self,
        *,
        messages: list[dict[str, Any]],
        title: str,
        kind: str,
        max_tokens: int | None = None,
    ) -> str:
        profile = self.model_profile()
        return await call_chat_model(
            client=make_async_client(profile),
            model_name=profile.model_name,
            max_tokens=max_tokens or profile.max_tokens,
            messages=messages,
            title=title,
            kind=kind,
            log_callback=lambda log: self._ctx.emit_payload(
                kind=EventKind.MODEL_CALL,
                payload=log,
            ),
        )

    def embedding_client(self, manifest: AgentManifest | None = None) -> EmbeddingClient:
        return EmbeddingClient(
            self.embedding_profile(manifest=manifest),
        )

    def embedding_profile(self, manifest: AgentManifest | None = None) -> EmbeddingProfile:
        return resolve_manifest_embedding_profile(
            manifest or self.manifest,
        )

    def store_result(
        self,
        *,
        domain: str,
        sql: str,
        rows: list[dict[str, Any]],
        tool_name: str = "",
        ui_content: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        result_store = self._ctx.result_store
        if result_store is None:
            return ""
        result_id = result_store.create_result(
            run_id=self._ctx.run_id,
            session_id=self._ctx.session_id,
            bot_id=self._ctx.bot_id,
            domain=domain,
            sql=sql,
            rows=rows,
        )
        if tool_name:
            self.result_created(
                tool_name=tool_name,
                ui_content=ui_content or {"result_id": result_id},
                metadata=metadata or {"result_id": result_id},
            )
        return result_id

    def store_artifact(
        self,
        *,
        artifact_type: str,
        payload: dict[str, Any],
        tool_name: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        result_store = self._ctx.result_store
        if result_store is None:
            return {}
        registry = self._ctx.result_formatters
        if registry is None:
            registry = ResultFormatterRegistry()
            try:
                extension = load_subagent_extension(self.manifest)
            except Exception:
                extension = None
            if extension is not None:
                for formatter in extension.result_formatters:
                    registry.register(formatter)
        formatter_payload = {
            **dict(payload),
            "artifact_type": artifact_type,
            "tool_name": tool_name,
            "source": source or tool_name or self._ctx.active_subagent,
            "subagent": self._ctx.active_subagent,
        }
        artifact = registry.format(artifact_type, formatter_payload)
        result_id = result_store.create_artifact(
            run_id=self._ctx.run_id,
            artifact=artifact,
            session_id=self._ctx.session_id,
            bot_id=self._ctx.bot_id,
        )
        result = result_store.get_metadata(
            result_id,
            run_id=self._ctx.run_id,
            session_id=self._ctx.session_id,
            bot_id=self._ctx.bot_id,
        )
        if tool_name:
            self.result_created(
                tool_name=tool_name,
                ui_content=result,
                metadata=result.get("metadata", {}),
                result=result,
            )
        return result

    def result_created(
        self,
        *,
        tool_name: str,
        ui_content: dict[str, Any],
        metadata: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> None:
        self._ctx.emit_payload(
            kind=EventKind.RESULT_CREATED,
            payload={
                "stage": "result_created",
                "tool_name": tool_name,
                "ui_content": ui_content,
                "metadata": metadata,
                "result": result or ui_content,
            },
        )

    @property
    def _runtime_context(self) -> RuntimeContext:
        """Internal escape hatch for framework adapters, not subagent code."""

        return self._ctx


def subagent_context(ctx: SubagentToolContext | RuntimeContext | SubagentContext) -> SubagentContext:
    if isinstance(ctx, SubagentContext):
        return ctx
    if isinstance(ctx, RuntimeContext):
        return SubagentContext(ctx)
    runtime_context = getattr(ctx, "context", None)
    if isinstance(runtime_context, RuntimeContext):
        return SubagentContext(runtime_context)
    raise TypeError("Subagent tool requires an AgentWeave runtime context.")


def tool_start(
    ctx: SubagentContext | SubagentToolContext,
    *,
    tool_name: str,
    input_payload: dict[str, Any],
) -> None:
    _emit_tool_start(
        subagent_context(ctx)._runtime_context,
        tool_name=tool_name,
        input_payload=input_payload,
    )


def tool_finish(
    ctx: SubagentContext | SubagentToolContext,
    *,
    tool_name: str,
    output: ToolOutput,
    status: str = "completed",
) -> None:
    subagent_context(ctx).emit_tool_result(
        tool_name=tool_name,
        output=output,
        status=status,
    )


__all__ = [
    "SubagentContext",
    "SubagentExtensionAPI",
    "SubagentToolContext",
    "ResultArtifactSpec",
    "ResultFormatter",
    "ToolOutput",
    "subagent_context",
    "tool",
    "tool_finish",
    "tool_start",
]
