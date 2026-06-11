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
from agent_runtime.shared.embeddings import EmbeddingClient
from agent_runtime.shared.manifest import AgentManifest
from agent_runtime.shared.models import resolve_manifest_embedding_profile
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

    def typed_state(self, namespace: str, factory: Callable[[], T]) -> T:
        return self._ctx.get_typed_state(namespace, factory)

    @property
    def manifest(self) -> AgentManifest:
        registry = self._ctx.agent_registry
        if registry is None:
            raise RuntimeError("Subagent manifest registry is unavailable.")
        return registry.get(self._ctx.active_subagent)

    @property
    def policies(self) -> dict[str, Any]:
        return dict(self.manifest.policies)

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

    async def call_model(
        self,
        *,
        messages: list[dict[str, Any]],
        title: str,
        kind: str,
        max_tokens: int | None = None,
        model_name: str | None = None,
    ) -> str:
        profile = self._ctx.model_profile
        resolved_model_name = str(model_name or profile.model_name)
        return await call_chat_model(
            client=make_async_client(profile),
            model_name=resolved_model_name,
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
            resolve_manifest_embedding_profile(manifest or self.manifest),
        )

    def store_artifact(
        self,
        *,
        artifact_type: str,
        payload: dict[str, Any],
        tool_name: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        artifact_store = self._ctx.artifact_store
        if artifact_store is None:
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
        result_id = artifact_store.create_artifact(
            run_id=self._ctx.run_id,
            artifact=artifact,
            session_id=self._ctx.session_id,
            bot_id=self._ctx.bot_id,
        )
        result = artifact_store.get_metadata(
            result_id,
            run_id=self._ctx.run_id,
            session_id=self._ctx.session_id,
            bot_id=self._ctx.bot_id,
        )
        if tool_name:
            self._emit_result_created(
                tool_name=tool_name,
                ui_content=result,
                metadata=result.get("metadata", {}),
                result=result,
            )
        return result

    def get_artifact(self, result_id: str) -> dict[str, Any]:
        """Read artifact metadata within the current session/bot scope."""

        artifact_store = self._ctx.artifact_store
        if artifact_store is None:
            raise RuntimeError("ArtifactStore is unavailable.")
        return artifact_store.get_artifact(
            result_id,
            session_id=self._ctx.session_id,
            bot_id=self._ctx.bot_id,
        )

    def get_artifact_page(
        self,
        result_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read a paged row preview within the current session/bot scope."""

        artifact_store = self._ctx.artifact_store
        if artifact_store is None:
            raise RuntimeError("ArtifactStore is unavailable.")
        return artifact_store.get_artifact_page(
            result_id,
            offset=offset,
            limit=limit,
            session_id=self._ctx.session_id,
            bot_id=self._ctx.bot_id,
        )

    def _emit_result_created(
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
    _emit_tool_finish(
        subagent_context(ctx)._runtime_context,
        tool_name=tool_name,
        tool_output=output,
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
