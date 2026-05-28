from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Literal

from agents import (
    Agent,
    ModelSettings,
    RunContextWrapper,
    Runner,
    SQLiteSession,
    function_tool,
    set_tracing_disabled,
)

from agent_runtime.compressor import ContextCompressor
from agent_runtime.context import OrchestratorContext
from agent_runtime.database import CsvSQLiteBackend, DatabaseBackend
from agent_runtime.embeddings import EmbeddingClient, load_embedding_profile
from agent_runtime.hooks import HookResult, HookRunner, SessionStartContext
from agent_runtime.memory_manager import MemoryManager, TodoItem
from agent_runtime.memory_store import MemoryStore
from agent_runtime.prompts import (
    MEMORY_POLICY_SECTION,
    MEMORY_ROLE_POLICY,
    MEMORY_TOOL_POLICY,
    SYSTEM_PROMPT,
)
from agent_runtime.result_store import ResultStore
from agent_runtime.runtime_utils import (
    build_model,
    get_current_time_payload,
    json_dumps,
    to_jsonable,
)
from agent_runtime.settings import build_model_profiles
from agent_runtime.skill_registry import AgentRegistry, SkillRegistry
from agent_runtime.skill_runner import SubagentRunner
from pydantic import BaseModel

from agent_runtime.common import env_bool


class TodoToolItem(BaseModel):
    content: str
    status: Literal["pending", "in_progress", "completed"]


class AgentRuntime:
    """General orchestrator runtime with manifest-driven subagents."""

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str,
        session_db_path: Path,
        tables: dict[str, Path | str] | None = None,
        backend: DatabaseBackend | None = None,
        max_tokens: int = 4096,
        sql_base_url: str | None = None,
        sql_model_name: str | None = None,
        sql_max_tokens: int = 2048,
        embedding_base_url: str | None = None,
        embedding_model_name: str | None = None,
        memory_enabled: bool | None = None,
        timezone_name: str | None = None,
    ) -> None:
        set_tracing_disabled(True)
        if backend is None:
            if tables is None:
                raise ValueError("Either backend or tables must be provided.")
            backend = CsvSQLiteBackend(tables)
        self.backend = backend
        self.session_db_path = session_db_path
        session_root = session_db_path.resolve().parent
        self.root = (
            session_root
            if (session_root / "skills").exists() or (session_root / "subagents").exists()
            else Path.cwd().resolve()
        )
        self.timezone_name = timezone_name or os.getenv("TEXT2SQL_TIMEZONE", "Asia/Hong_Kong")
        self.model_profiles = build_model_profiles(
            base_url=base_url,
            model_name=model_name,
            api_key=api_key,
            max_tokens=max_tokens,
            sql_base_url=sql_base_url or base_url,
            sql_model_name=sql_model_name or model_name,
            sql_max_tokens=sql_max_tokens,
        )
        self.skill_registry = SkillRegistry(skills_root=self.root / "skills")
        self.agent_registry = AgentRegistry(subagents_root=self.root / "subagents")
        self.memory_store = MemoryStore(self.root / "agent_memory.sqlite")
        self.memory_enabled = (
            env_bool("MEMORY_ENABLED", True)
            if memory_enabled is None
            else bool(memory_enabled)
        )
        self.embedding_profile = load_embedding_profile(
            base_url=embedding_base_url,
            model_name=embedding_model_name,
            api_key=api_key,
        )
        self.memory_manager = MemoryManager(
            self.memory_store,
            embedding_client=EmbeddingClient(self.embedding_profile),
            enabled=self.memory_enabled,
        )
        self.result_store = ResultStore(self.root / "agent_results.sqlite")
        self.subagent_runner = SubagentRunner(
            registry=self.agent_registry,
            skill_registry=self.skill_registry,
            memory_manager=self.memory_manager,
            result_store=self.result_store,
            root=self.root,
        )
        orchestrator_profile = self.model_profiles["orchestrator"]
        self.compressor = ContextCompressor(
            context_window=orchestrator_profile.context_window,
            reserved_output_tokens=orchestrator_profile.max_tokens,
            model_name=orchestrator_profile.model_name,
        )
        self.hook_runner = HookRunner()

    async def ask(
        self,
        user_input: str,
        session_id: str,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        max_turns: int = 10,
    ) -> dict[str, Any]:
        local_model_logs: list[dict[str, Any]] = []
        def log_callback(log_entry: dict[str, Any]) -> None:
            local_model_logs.append(to_jsonable(log_entry))

        context = OrchestratorContext(
            session_id=session_id,
            backend=self.backend,
            model_profiles=self.model_profiles,
            result_store=self.result_store,
            event_callback=event_callback,
            timezone_name=self.timezone_name,
        )
        session = SQLiteSession(session_id, str(self.session_db_path))
        prior_messages = await session.get_items()
        compressed_messages = await self.compressor.compress(
            prior_messages,
            session_id=session_id,
            memory_manager=self.memory_manager,
            model_profile=self.model_profiles["sql_worker"],
        )
        if compressed_messages != prior_messages:
            await session.clear_session()
            await session.add_items(compressed_messages)

        profile = self.model_profiles["orchestrator"]
        agent = Agent[OrchestratorContext](
            name="Agent Orchestrator",
            instructions=self._build_instructions(
                session_id,
                current_query=user_input,
                context=context,
            ),
            model=build_model(
                profile=profile,
                log_callback=log_callback,
                title="编排模型调用",
                kind="orchestration_model",
            ),
            model_settings=ModelSettings(max_tokens=profile.max_tokens),
            tools=self._build_tools(),
        )

        result = await Runner.run(
            agent,
            user_input,
            context=context,
            session=session,
            max_turns=max_turns,
        )
        model_logs = list(local_model_logs)
        model_logs.extend(
            event["payload"]
            for event in context.events
            if event.get("kind") == "model_call" and isinstance(event.get("payload"), dict)
        )
        return {
            "final_output": result.final_output,
            "events": list(context.events),
            "subagent_trace": _subagent_trace(context.events),
            "model_logs": model_logs,
            "worker_runs": [
                event for event in context.events if event.get("kind") == "worker_run"
            ],
            "todo_events": [
                event for event in context.events if event.get("kind") == "todo_event"
            ],
        }

    def _build_instructions(
        self,
        session_id: str = "",
        *,
        current_query: str = "",
        context: OrchestratorContext | None = None,
    ) -> str:
        parts = [
            SYSTEM_PROMPT.format(
                skills_section=self._build_skills_section(),
                memory_role_policy=MEMORY_ROLE_POLICY if self.memory_enabled else "",
                memory_tool_policy=MEMORY_TOOL_POLICY if self.memory_enabled else "",
                memory_policy_section=MEMORY_POLICY_SECTION if self.memory_enabled else "",
            )
        ]
        user_memory = _read_optional_path(Path(os.getenv("AGENT_USER_MEMORY_PATH", "~/.agent/USER.md")).expanduser())
        project_rules = _read_optional_path(
            Path(os.getenv("AGENT_PROJECT_RULES_PATH", str(self.root / "PROJECT.md"))).expanduser()
        )
        retrieval_events: list[dict[str, Any]] = []
        memory_context = ""
        if session_id:
            memory_context = self.memory_manager.build_orchestrator_context(
                session_id,
                current_query=current_query,
                retrieval_events=retrieval_events,
            )
        if context is not None:
            for payload in retrieval_events:
                context.emit_payload(
                    kind="memory_event",
                    run_id=context.session_id,
                    payload=payload,
                )
        if user_memory:
            parts.append(f"用户偏好:\n{user_memory}")
        if project_rules:
            parts.append(f"项目规则:\n{project_rules}")
        if memory_context:
            parts.append(f"<memory_context>\n{memory_context}\n</memory_context>")
        return "\n\n".join(parts)

    def _build_skills_section(self) -> str:
        return "\n\n".join(
            [
                self.agent_registry.format_routing_for_prompt(),
                self.skill_registry.format_catalog_for_prompt(),
            ]
        )

    def _build_subagent_agent_tools(self) -> list[Any]:
        tools = []
        for manifest in self.agent_registry.discover():
            if manifest.execution.mode != "worker":
                continue
            model_role = self.subagent_runner._resolve_model_role(manifest)
            if not model_role:
                continue
            profile = self.model_profiles[model_role]
            tools.append(
                self.subagent_runner.build_worker_agent_tool(
                    manifest=manifest,
                    profile=profile,
                )
            )
        return tools

    def _build_memory_context(self, session_id: str) -> str:
        return self.memory_manager.build_orchestrator_context(session_id)

    def clear_memory(self) -> None:
        self.memory_manager.clear()

    def run_session_start_hook(
        self,
        *,
        session_id: str,
        base_url: str,
        model_name: str,
        api_key: str,
        questions_per_domain: int,
    ) -> HookResult:
        return self.hook_runner.run(
            "SessionStart",
            SessionStartContext(
                skills_root=self.root / "skills",
                subagents_root=self.root / "subagents",
                base_url=base_url,
                model_name=model_name,
                api_key=api_key,
                questions_per_domain=questions_per_domain,
                memory_context=self.memory_manager.build_orchestrator_context(session_id),
            ),
        )

    def _build_tools(self) -> list[Any]:
        runtime = self

        @function_tool
        async def get_current_time(timezone_name: str = "") -> str:
            """Resolve the current date and time before handling relative-time questions.

            Use this when the user says today, yesterday, this week, this month,
            recent, current, now, or a similar relative time phrase. Pass an
            explicit IANA timezone only when the user requests one; otherwise
            leave timezone_name empty and the application default is used.

            Args:
                timezone_name: Optional IANA timezone name. Empty means application default.
            """
            requested_timezone = timezone_name.strip() or runtime.timezone_name
            try:
                output = get_current_time_payload(requested_timezone)
            except ValueError as exc:
                output = {"timezone": requested_timezone, "error": str(exc)}
            return json_dumps(output)

        @function_tool
        async def memory_search(
            ctx: RunContextWrapper[OrchestratorContext],
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
            namespace_list = [item.strip() for item in namespaces.split(",") if item.strip()]
            result = runtime.memory_manager.retrieve(query, namespace_list, limit=limit)
            records = result.records
            ctx.context.emit_payload(
                kind="memory_event",
                run_id=ctx.context.session_id,
                payload={
                    "stage": "memory_search",
                    "query": query,
                    "namespaces": namespace_list,
                    "count": len(records),
                    "strategy": result.strategy,
                    "embedding_fallback": result.fallback,
                    "error": result.error,
                },
            )
            return json_dumps([record.__dict__ for record in records])

        @function_tool
        async def memory_write(
            ctx: RunContextWrapper[OrchestratorContext],
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
            tag_list = [item.strip() for item in tags.split(",") if item.strip()]
            runtime.memory_manager.write(
                namespace=namespace,
                key=key,
                content=content,
                tags=tag_list,
                source="agent",
            )
            ctx.context.emit_payload(
                kind="memory_event",
                run_id=ctx.context.session_id,
                payload={
                    "stage": "memory_write",
                    "namespace": namespace,
                    "key": key,
                    "tags": tag_list,
                },
            )
            return json_dumps({"ok": True, "namespace": namespace, "key": key})

        @function_tool
        async def load_skill(
            ctx: RunContextWrapper[OrchestratorContext],
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
            try:
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
                        skill.name for skill in runtime.skill_registry.discover()
                    ],
                }
            ctx.context.emit_payload(
                kind="skill_event",
                run_id=ctx.context.session_id,
                payload={
                    "stage": "load_skill",
                    "skill": skill_name,
                    "found": "error" not in payload,
                },
            )
            return json_dumps(payload)

        @function_tool
        async def update_todo(
            ctx: RunContextWrapper[OrchestratorContext],
            items: list[TodoToolItem],
        ) -> str:
            """Update session-local working todos.

            Use this for multi-step work planning and progress tracking inside
            the current session. Todos are short-lived working memory and are
            not written into durable user/project/skill memory.

            Args:
                items: Todo items with content and status: pending, in_progress, or completed.
            """
            todos = [
                TodoItem(content=item.content, status=item.status)
                for item in items
            ]
            try:
                updated = runtime.memory_manager.update_todo(ctx.context.session_id, todos)
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
                kind="todo_event",
                run_id=ctx.context.session_id,
                payload=payload,
            )
            return json_dumps(payload)

        tools = [
            get_current_time,
            load_skill,
            update_todo,
            *runtime._build_subagent_agent_tools(),
        ]
        if runtime.memory_enabled:
            tools[1:1] = [memory_search, memory_write]
        return tools


def _subagent_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace = []
    for event in events:
        if event.get("kind") == "subagent_trace":
            payload = event.get("payload")
            if isinstance(payload, dict):
                trace.append(payload)
    return trace


def _read_optional_path(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()
