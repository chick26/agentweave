"""Shared tool event helpers for subagent tools.

Subagent tools.py modules should import these instead of duplicating
the emit_tool_start / emit_tool_finish boilerplate.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.core.context import RunContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.tool_protocol import ToolOutput


def emit_tool_start(
    run_ctx: RunContext,
    *,
    tool_name: str,
    input_payload: dict[str, Any],
) -> None:
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_START,
        payload={
            "stage": "tool_call_start",
            "tool_name": tool_name,
            "input": input_payload,
        },
    )


def emit_tool_finish(
    run_ctx: RunContext,
    *,
    tool_name: str,
    tool_output: ToolOutput,
    status: str = "completed",
) -> None:
    error = str(tool_output.metadata.get("error") or "")
    run_ctx.emit_payload(
        kind=EventKind.TOOL_RESULT,
        payload={
            "stage": "tool_result",
            "tool_name": tool_name,
            "status": status,
            "ui_content": tool_output.ui_content,
            "metadata": tool_output.metadata,
            "error": error,
        },
        error=error,
    )
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_END,
        payload={
            "stage": "tool_call_end",
            "tool_name": tool_name,
            "status": status,
            "error": error,
        },
        error=error,
    )
