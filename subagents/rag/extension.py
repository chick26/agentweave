"""RAG subagent extension that registers search, summary, and readiness checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_runtime.subagent_api import (
    SubagentToolContext,
    ToolOutput,
    subagent_context,
    tool,
    tool_finish,
    tool_start,
)
from agent_runtime.shared.manifest import AgentManifest
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


@tool
async def search_knowledge_base(
    ctx: SubagentToolContext,
    query: str,
    top_k: int = 5,
) -> str:
    """Search the prepared local Markdown knowledge base and return cited chunks."""
    run_ctx = subagent_context(ctx)
    top_k = min(10, max(1, int(top_k or 5)))
    input_payload = {"query": query, "top_k": top_k}
    tool_start(run_ctx, tool_name="search_knowledge_base", input_payload=input_payload)
    try:
        index = load_knowledge_index(_prepared_index_path())
        chunks = search_knowledge_index(
            query=query,
            index=index,
            top_k=top_k,
            embedding_client=run_ctx.embedding_client(),
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
    run_ctx.trace(
        stage="rag_search",
        title="检索知识库",
        input=input_payload,
        output=output,
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
    tool_finish(
        run_ctx,
        tool_name="search_knowledge_base",
        output=tool_output,
        status="failed" if output.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@tool
async def get_knowledge_base_summary(ctx: SubagentToolContext) -> str:
    """Return the prepared Markdown knowledge base summary."""
    run_ctx = subagent_context(ctx)
    input_payload: dict[str, Any] = {}
    tool_start(
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
    run_ctx.trace(
        stage="rag_summary",
        title="读取知识库摘要",
        input=input_payload,
        output=output,
    )
    tool_output = ToolOutput(
        llm_content=output,
        ui_content=output,
        metadata={
            "tool_name": "get_knowledge_base_summary",
            "error": output.get("error", ""),
        },
    )
    tool_finish(
        run_ctx,
        tool_name="get_knowledge_base_summary",
        output=tool_output,
        status="failed" if output.get("error") else "completed",
    )
    return tool_output.to_llm_json()


def _prepared_index_path() -> Path:
    value = os.getenv("RAG_INDEX_PATH", "").strip()
    if not value:
        raise RuntimeError(RAG_ENVIRONMENT_ERROR)
    path = Path(value).expanduser()
    if not path.exists():
        raise RuntimeError(f"{RAG_ENVIRONMENT_ERROR} Missing index: {path}")
    return path
