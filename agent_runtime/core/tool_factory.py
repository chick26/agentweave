"""Runtime tool factory for orchestrator-owned tools."""

from __future__ import annotations

from typing import Any, Literal

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel

from agent_runtime.common import env_bool
from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.runtime_utils import get_current_time_payload
from agent_runtime.core.tool_protocol import ToolOutput
from agent_runtime.memory.todo_state import TodoItem


class TodoToolItem(BaseModel):
    content: str
    status: Literal["pending", "in_progress", "completed"]


def build_runtime_tools(runtime: Any, *, bot: Any | None = None) -> list[Any]:
    """Build orchestrator tools without making AgentRuntime own tool internals."""

    bot = bot or runtime.bot_registry.get("default")
    allowed_skills = set(bot.skills)

    @function_tool
    async def get_current_time(
        ctx: RunContextWrapper[RuntimeContext],
        timezone_name: str = "",
    ) -> str:
        """Resolve the current date and time before handling relative-time questions.

        Use this when the user says today, yesterday, this week, this month,
        recent, current, now, or a similar relative time phrase. Pass an
        explicit IANA timezone only when the user requests one; otherwise
        leave timezone_name empty and the application default is used.

        Args:
            timezone_name: Optional IANA timezone name. Empty means application default.
        """
        emit_tool_start(
            ctx.context,
            tool_name="get_current_time",
            input_payload={"timezone_name": timezone_name},
        )
        requested_timezone = timezone_name.strip() or runtime.timezone_name
        try:
            output = get_current_time_payload(requested_timezone)
        except ValueError as exc:
            output = {"timezone": requested_timezone, "error": str(exc)}
        tool_output = ToolOutput(
            llm_content=output,
            ui_content=output,
            metadata={
                "tool_name": "get_current_time",
                "error": output.get("error", "") if isinstance(output, dict) else "",
            },
        )
        emit_tool_finish(
            ctx.context,
            tool_name="get_current_time",
            output=tool_output,
            status="failed" if tool_output.metadata.get("error") else "completed",
        )
        return tool_output.to_llm_json()

    @function_tool
    async def memory_search(
        ctx: RunContextWrapper[RuntimeContext],
        query: str,
        namespaces: str = "",
        limit: int = 5,
    ) -> str:
        """Search durable project/user/session memory for relevant prior context.

        Use this only when the current answer depends on remembered
        preferences, project rules, prior decisions, or the user explicitly
        asks about what was remembered. Prefer narrow namespaces when known.

        Args:
            query: Natural-language search query.
            namespaces: Optional comma-separated namespaces such as user, project, skill:text2sql.
            limit: Maximum number of memory records to return.
        """
        emit_tool_start(
            ctx.context,
            tool_name="memory_search",
            input_payload={
                "query": query,
                "namespaces": namespaces,
                "limit": limit,
            },
        )
        namespace_list = [item.strip() for item in namespaces.split(",") if item.strip()]
        result = runtime.memory_manager.retrieve(query, namespace_list, limit=limit)
        records = result.records
        memory_payload = {
            "stage": "memory_search",
            "query": query,
            "namespaces": namespace_list,
            "count": len(records),
            "strategy": result.strategy,
            "embedding_fallback": result.fallback,
            "error": result.error,
        }
        ctx.context.emit_payload(
            kind=EventKind.MEMORY_READ,
            run_id=ctx.context.session_id,
            payload=memory_payload,
        )
        output = [record.__dict__ for record in records]
        tool_output = ToolOutput(
            llm_content=output,
            ui_content={**memory_payload, "records": output},
            metadata={
                "tool_name": "memory_search",
                "count": len(records),
                "error": result.error,
            },
        )
        emit_tool_finish(
            ctx.context,
            tool_name="memory_search",
            output=tool_output,
            status="failed" if result.error else "completed",
        )
        return tool_output.to_llm_json()

    @function_tool
    async def memory_write(
        ctx: RunContextWrapper[RuntimeContext],
        namespace: str,
        key: str,
        content: str,
        tags: str = "",
    ) -> str:
        """Write stable, reusable memory.

        Use this only for durable facts, user preferences, or project rules
        that are likely to help future sessions. Do not store credentials,
        private secrets, raw query results, transient errors, or one-off
        intermediate reasoning.

        Args:
            namespace: Memory namespace, for example user, project, or skill:text2sql.
            key: Stable concise key for upsert behavior.
            content: Memory content as a short factual sentence or rule.
            tags: Optional comma-separated tags.
        """
        emit_tool_start(
            ctx.context,
            tool_name="memory_write",
            input_payload={"namespace": namespace, "key": key, "tags": tags},
        )
        tag_list = [item.strip() for item in tags.split(",") if item.strip()]
        runtime.memory_manager.write(
            namespace=namespace,
            key=key,
            content=content,
            tags=tag_list,
            source="agent",
        )
        ctx.context.emit_payload(
            kind=EventKind.MEMORY_WRITE,
            run_id=ctx.context.session_id,
            payload={
                "stage": "memory_write",
                "namespace": namespace,
                "key": key,
                "tags": tag_list,
            },
        )
        output = {"ok": True, "namespace": namespace, "key": key}
        tool_output = ToolOutput(
            llm_content=output,
            ui_content={**output, "tags": tag_list},
            metadata={"tool_name": "memory_write", "error": ""},
        )
        emit_tool_finish(ctx.context, tool_name="memory_write", output=tool_output)
        return tool_output.to_llm_json()

    @function_tool
    async def load_skill(
        ctx: RunContextWrapper[RuntimeContext],
        skill_name: str,
    ) -> str:
        """Load a real skill document from skills/*/SKILL.md.

        Skills are method cards or reusable workflows. They are not
        subagent tools. Use this only after consulting skills_catalog and
        before applying a skill's guidance in the orchestrator response or
        subagent task handoff.

        Args:
            skill_name: Skill name from skills_catalog, for example data_analysis.
        """
        emit_tool_start(
            ctx.context,
            tool_name="load_skill",
            input_payload={"skill_name": skill_name},
        )
        try:
            if skill_name not in allowed_skills:
                raise ValueError(f"Skill `{skill_name}` is not enabled for bot `{bot.id}`.")
            skill = runtime.skill_registry.get(skill_name)
            payload = {
                "name": skill.name,
                "description": skill.description,
                "body": skill.body,
                "metadata": skill.metadata,
            }
        except ValueError as exc:
            payload = {
                "error": str(exc),
                "available_skills": [
                    skill.name
                    for skill in runtime.skill_registry.discover()
                    if skill.name in allowed_skills
                ],
            }
        ctx.context.emit_payload(
            kind=EventKind.SKILL_EVENT,
            run_id=ctx.context.session_id,
            payload={
                "stage": "load_skill",
                "skill": skill_name,
                "found": "error" not in payload,
            },
        )
        tool_output = ToolOutput(
            llm_content=payload,
            ui_content=payload,
            metadata={
                "tool_name": "load_skill",
                "skill": skill_name,
                "error": payload.get("error", ""),
            },
        )
        emit_tool_finish(
            ctx.context,
            tool_name="load_skill",
            output=tool_output,
            status="failed" if payload.get("error") else "completed",
        )
        return tool_output.to_llm_json()

    @function_tool
    async def update_todo(
        ctx: RunContextWrapper[RuntimeContext],
        items: list[TodoToolItem],
    ) -> str:
        """Update session-local working todos.

        Use this for multi-step work planning and progress tracking inside
        the current session. Todos are short-lived working memory and are
        not written into durable user/project/skill memory.

        Args:
            items: Todo items with content and status: pending, in_progress, or completed.
        """
        emit_tool_start(
            ctx.context,
            tool_name="update_todo",
            input_payload={"items": [item.model_dump() for item in items]},
        )
        todos = [TodoItem(content=item.content, status=item.status) for item in items]
        try:
            updated = runtime.todo_state.update(ctx.context.session_id, todos)
            payload = {
                "stage": "todo_update",
                "items": [item.__dict__ for item in updated],
            }
        except ValueError as exc:
            payload = {
                "stage": "todo_update",
                "error": str(exc),
            }
        ctx.context.emit_payload(
            kind=EventKind.TODO_EVENT,
            run_id=ctx.context.session_id,
            payload=payload,
        )
        tool_output = ToolOutput(
            llm_content=payload,
            ui_content=payload,
            metadata={
                "tool_name": "update_todo",
                "error": payload.get("error", ""),
            },
        )
        emit_tool_finish(
            ctx.context,
            tool_name="update_todo",
            output=tool_output,
            status="failed" if payload.get("error") else "completed",
        )
        return tool_output.to_llm_json()

    tools = [
        get_current_time,
        load_skill,
        *_build_subagent_agent_tools(runtime, bot=bot),
    ]
    if runtime.memory_enabled:
        tools[1:1] = [memory_search, memory_write]
    if env_bool("AGENTWEAVE_ENABLE_TODO_TOOL", False):
        tools.append(update_todo)
    return tools


def emit_tool_start(
    context: RuntimeContext,
    *,
    tool_name: str,
    input_payload: dict[str, Any],
) -> None:
    context.emit_payload(
        kind=EventKind.TOOL_CALL_START,
        run_id=context.session_id,
        payload={
            "stage": "tool_call_start",
            "tool_name": tool_name,
            "input": input_payload,
        },
    )


def emit_tool_finish(
    context: RuntimeContext,
    *,
    tool_name: str,
    output: ToolOutput,
    status: str = "completed",
) -> None:
    error = str(output.metadata.get("error") or "")
    payload = {
        "stage": "tool_result",
        "tool_name": tool_name,
        "status": status,
        "ui_content": output.ui_content,
        "metadata": output.metadata,
        "error": error,
    }
    context.emit_payload(
        kind=EventKind.TOOL_RESULT,
        run_id=context.session_id,
        payload=payload,
        error=error,
    )
    context.emit_payload(
        kind=EventKind.TOOL_CALL_END,
        run_id=context.session_id,
        payload={
            "stage": "tool_call_end",
            "tool_name": tool_name,
            "status": status,
            "error": error,
        },
        error=error,
    )


def _build_subagent_agent_tools(runtime: Any, *, bot: Any) -> list[Any]:
    tools = []
    allowed = set(bot.subagents)
    for manifest in runtime.agent_registry.discover():
        if manifest.name not in allowed:
            continue
        if manifest.execution.mode != "worker":
            continue
        profile = runtime.subagent_runner.resolve_worker_profile(
            manifest,
            model_profiles=runtime.model_profiles,
        )
        tools.append(
            runtime.subagent_runner.build_worker_agent_tool(
                manifest=manifest,
                profile=profile,
            )
        )
    return tools
