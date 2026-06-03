from __future__ import annotations

from typing import Any

from agents import RunContextWrapper, function_tool

from agent_runtime.core.context import RunContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.tool_protocol import ToolOutput
from subagents.rag.env import search_local_knowledge_base


def _emit_tool_start(
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


def _emit_tool_finish(
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


@function_tool
async def search_knowledge_base(
    ctx: RunContextWrapper[RunContext],
    query: str,
    top_k: int = 5,
) -> str:
    """Search the local PDF knowledge base and return cited chunks.

    Args:
        query: User question or search query.
        top_k: Maximum number of chunks to return.
    """
    run_ctx = ctx.context
    top_k = min(10, max(1, int(top_k or 5)))
    input_payload = {"query": query, "top_k": top_k}
    _emit_tool_start(run_ctx, tool_name="search_knowledge_base", input_payload=input_payload)
    try:
        manifest = run_ctx.agent_registry.get("rag") if run_ctx.agent_registry else None
        output = search_local_knowledge_base(
            query=query,
            top_k=top_k,
            root=run_ctx.runtime_root,
            manifest=manifest,
        )
    except Exception as exc:
        output = {
            "query": query,
            "chunks": [],
            "count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    run_ctx.emit_subagent_trace(
        {
            "stage": "rag_search",
            "title": "检索知识库",
            "input": input_payload,
            "output": output,
        }
    )
    tool_output = ToolOutput(
        llm_content=output,
        ui_content=output,
        metadata={
            "tool_name": "search_knowledge_base",
            "count": output.get("count", 0),
            "error": output.get("error", ""),
        },
    )
    _emit_tool_finish(
        run_ctx,
        tool_name="search_knowledge_base",
        tool_output=tool_output,
        status="failed" if output.get("error") else "completed",
    )
    return tool_output.to_llm_json()
