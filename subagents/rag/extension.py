"""RAG subagent extension that registers search, summary, and readiness checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from agent_runtime.core.context import RunContext
from agent_runtime.core.manifest_models import resolve_manifest_embedding_profile
from agent_runtime.core.tool_helpers import emit_tool_finish, emit_tool_start
from agent_runtime.core.tool_protocol import ToolOutput
from agent_runtime.memory.embeddings import EmbeddingClient
from agent_runtime.registry.skill_registry import AgentManifest
from subagents.rag.core.retrieval import (
    knowledge_index_summary,
    load_knowledge_index,
    search_knowledge_index,
)


RAG_ENVIRONMENT_ERROR = (
    "RAG knowledge base is not prepared. Run `uv run agentweave-prepare-rag`, "
    "then restart runtime."
)


def register(api: Any) -> None:
    api.tool(search_knowledge_base)
    api.tool(get_knowledge_base_summary)
    api.validate_environment(validate_environment)


def validate_environment(_manifest: AgentManifest) -> None:
    _prepared_index_path()


@function_tool
async def search_knowledge_base(
    ctx: RunContextWrapper[RunContext],
    query: str,
    top_k: int = 5,
) -> str:
    """Search the prepared local Markdown knowledge base and return cited chunks."""
    run_ctx = ctx.context
    top_k = min(10, max(1, int(top_k or 5)))
    input_payload = {"query": query, "top_k": top_k}
    emit_tool_start(run_ctx, tool_name="search_knowledge_base", input_payload=input_payload)
    try:
        manifest = _active_manifest(run_ctx)
        index = load_knowledge_index(_prepared_index_path())
        chunks = search_knowledge_index(
            query=query,
            index=index,
            top_k=top_k,
            embedding_client=EmbeddingClient(
                resolve_manifest_embedding_profile(
                    manifest,
                    model_profiles=run_ctx.model_profiles,
                )
            ),
        )
        output = {
            "query": query,
            "chunks": chunks,
            "count": len(chunks),
            "error": "",
        }
    except Exception as exc:
        output = {
            "query": query,
            "chunks": [],
            "count": 0,
            "error": f"{type(exc).__name__}: {exc}" if str(exc) else RAG_ENVIRONMENT_ERROR,
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
    emit_tool_finish(
        run_ctx,
        tool_name="search_knowledge_base",
        tool_output=tool_output,
        status="failed" if output.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@function_tool
async def get_knowledge_base_summary(ctx: RunContextWrapper[RunContext]) -> str:
    """Return the prepared Markdown knowledge base summary."""
    run_ctx = ctx.context
    input_payload: dict[str, Any] = {}
    emit_tool_start(
        run_ctx,
        tool_name="get_knowledge_base_summary",
        input_payload=input_payload,
    )
    try:
        index = load_knowledge_index(_prepared_index_path())
        summary = knowledge_index_summary(index)
        output = {
            "summary": summary["kb_description"],
            "section_summaries": summary["section_summaries"],
            "source_overview": summary["source_overview"],
            "version": summary["version"],
            "error": "",
        }
    except Exception as exc:
        output = {
            "summary": "",
            "section_summaries": [],
            "source_overview": [],
            "error": f"{type(exc).__name__}: {exc}" if str(exc) else RAG_ENVIRONMENT_ERROR,
        }
    run_ctx.emit_subagent_trace(
        {
            "stage": "rag_summary",
            "title": "读取知识库摘要",
            "input": input_payload,
            "output": output,
        }
    )
    tool_output = ToolOutput(
        llm_content=output,
        ui_content=output,
        metadata={
            "tool_name": "get_knowledge_base_summary",
            "error": output.get("error", ""),
        },
    )
    emit_tool_finish(
        run_ctx,
        tool_name="get_knowledge_base_summary",
        tool_output=tool_output,
        status="failed" if output.get("error") else "completed",
    )
    return tool_output.to_llm_json()


def _active_manifest(run_ctx: RunContext) -> AgentManifest:
    if run_ctx.agent_registry is None:
        raise ValueError("RunContext is missing agent_registry.")
    return run_ctx.agent_registry.get(run_ctx.active_subagent or "rag")


def _prepared_index_path() -> Path:
    value = os.getenv("RAG_INDEX_PATH", "").strip()
    if not value:
        raise RuntimeError(RAG_ENVIRONMENT_ERROR)
    path = Path(value).expanduser()
    if not path.exists():
        raise RuntimeError(f"{RAG_ENVIRONMENT_ERROR} Missing index: {path}")
    return path
