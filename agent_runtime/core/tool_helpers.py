"""Shared tool event helpers for subagent tools.

Subagent tools.py modules should import these instead of duplicating
the emit_tool_start / emit_tool_finish boilerplate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.runtime_utils import json_dumps


@dataclass(frozen=True)
class ToolOutput:
    """Dual-channel tool output for model and UI/event consumers."""

    llm_content: Any
    ui_content: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_llm_json(self) -> str:
        if isinstance(self.llm_content, str):
            return self.llm_content
        return json_dumps(self.llm_content)


def emit_tool_start(
    run_ctx: RuntimeContext,
    *,
    tool_name: str,
    input_payload: dict[str, Any],
) -> None:
    audit = _tool_audit_metadata(run_ctx, tool_name)
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_START,
        payload={
            "stage": "tool_call_start",
            "tool_name": tool_name,
            **audit,
            "input": input_payload,
        },
    )


def emit_tool_finish(
    run_ctx: RuntimeContext,
    *,
    tool_name: str,
    tool_output: ToolOutput,
    status: str = "completed",
) -> None:
    error = str(tool_output.metadata.get("error") or "")
    audit = _tool_audit_metadata(run_ctx, tool_name)
    metadata = {
        **audit,
        **tool_output.metadata,
    }
    run_ctx.emit_payload(
        kind=EventKind.TOOL_RESULT,
        payload={
            "stage": "tool_result",
            "tool_name": tool_name,
            **audit,
            "status": status,
            "ui_content": tool_output.ui_content,
            "metadata": metadata,
            "error": error,
        },
        error=error,
    )
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_END,
        payload={
            "stage": "tool_call_end",
            "tool_name": tool_name,
            **audit,
            "status": status,
            "error": error,
        },
        error=error,
    )


def _tool_audit_metadata(run_ctx: RuntimeContext, tool_name: str) -> dict[str, Any]:
    subagent_name = str(run_ctx.active_subagent or "")
    if not subagent_name or run_ctx.agent_registry is None:
        return {}
    manifest = run_ctx.agent_registry.get(subagent_name)
    from agent_runtime.worker.subagent_extensions import resolve_tool_audit_metadata

    return resolve_tool_audit_metadata(manifest=manifest, tool_name=tool_name)
