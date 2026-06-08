"""Main AgentWeave runtime facade and SDK agent construction."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from agents import set_tracing_disabled

from agent_runtime.core.agent_factory import build_orchestrator_agent
from agent_runtime.core.compressor import ContextCompressor
from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.memory.embeddings import EmbeddingClient, load_embedding_profile
from agent_runtime.core.hooks import HookResult, HookRunner
from agent_runtime.hooks.session_start import (
    SessionStartContext,
    build_default_session_start_hooks,
)
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.memory.todo_state import TodoState
from agent_runtime.memory.memory_store import MemoryStore
from agent_runtime.core.prompts import (
    MEMORY_POLICY_SECTION,
    MEMORY_ROLE_POLICY,
    MEMORY_TOOL_POLICY,
    SYSTEM_PROMPT,
)
from agent_runtime.core.result_mapper import AgentRunResult, map_agent_result
from agent_runtime.core.run_executor import run_orchestrator_agent
from agent_runtime.storage.result_store import ResultStore
from agent_runtime.core.runtime_utils import to_jsonable
from agent_runtime.core.session_manager import SessionManager
from agent_runtime.core.model_profiles import load_model_profiles
from agent_runtime.worker.subagent_extensions import validate_extension_environment
from agent_runtime.core.tool_factory import build_runtime_tools
from agent_runtime.registry.bot_registry import BotConfig, BotRegistry
from agent_runtime.registry.resources import ResourceLoader
from agent_runtime.registry.skill_registry import AgentRegistry, SkillRegistry
from agent_runtime.worker.subagent_runner import SubagentRunner

from agent_runtime.common import agentweave_data_dir, env_bool


class AgentRuntime:
    """General orchestrator runtime with manifest-driven subagents."""

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str,
        session_db_path: Path,
        max_tokens: int = 4096,
        sql_base_url: str | None = None,
        sql_model_name: str | None = None,
        sql_max_tokens: int = 2048,
        embedding_base_url: str | None = None,
        embedding_model_name: str | None = None,
        memory_enabled: bool | None = None,
        timezone_name: str | None = None,
        validate_subagents: bool | None = None,
    ) -> None:
        set_tracing_disabled(True)
        self.session_db_path = session_db_path
        self.session_db_path.parent.mkdir(parents=True, exist_ok=True)
        session_root = session_db_path.resolve().parent
        if session_root.name == ".agentweave":
            session_root = session_root.parent
        self.root = (
            session_root
            if (session_root / "skills").exists() or (session_root / "subagents").exists()
            else Path.cwd().resolve()
        )
        self.data_dir = agentweave_data_dir(self.root)
        self.skill_registry = SkillRegistry(skills_root=self.root / "skills")
        self.agent_registry = AgentRegistry(subagents_root=self.root / "subagents")
        self.timezone_name = timezone_name or os.getenv("AGENTWEAVE_TIMEZONE", "Asia/Hong_Kong")
        self.model_profiles = load_model_profiles(
            orchestrator_base_url=base_url,
            orchestrator_model=model_name,
            orchestrator_max_tokens=max_tokens,
            api_key=api_key,
            sql_base_url=sql_base_url,
            sql_model=sql_model_name,
            sql_max_tokens=sql_max_tokens,
        )
        self.bot_registry = BotRegistry(
            bots_root=self.root / "bots",
            agent_registry=self.agent_registry,
            skill_registry=self.skill_registry,
        )
        self.resource_loader = ResourceLoader(
            root=self.root,
            skill_registry=self.skill_registry,
            agent_registry=self.agent_registry,
            bot_registry=self.bot_registry,
        )
        self.memory_store = MemoryStore(self.data_dir / "agent_memory.sqlite")
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
        self.todo_state = TodoState()
        self.result_store = ResultStore(self.data_dir / "agent_results.sqlite")
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
        self.session_manager = SessionManager(
            session_db_path=self.session_db_path,
            compressor=self.compressor,
            memory_manager=self.memory_manager,
        )
        self.hook_runner = HookRunner(handlers=build_default_session_start_hooks())
        self.validate_subagents = (
            env_bool("AGENTWEAVE_VALIDATE_SUBAGENTS", False)
            if validate_subagents is None
            else bool(validate_subagents)
        )
        self._validated_bot_ids: set[str] = set()

    async def ask(
        self,
        user_input: str,
        session_id: str,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        model_delta_callback: Callable[[dict[str, Any]], None] | None = None,
        max_turns: int = 10,
        bot_id: str = "default",
    ) -> dict[str, Any]:
        local_model_logs: list[dict[str, Any]] = []
        def log_callback(log_entry: dict[str, Any]) -> None:
            local_model_logs.append(to_jsonable(log_entry))

        bot = self.bot_registry.get(bot_id)
        if self.validate_subagents:
            self._validate_bot_subagents_readiness(bot.id)
        context = RuntimeContext(
            run_id=session_id,
            session_id=session_id,
            model_profiles=self.model_profiles,
            result_store=self.result_store,
            event_callback=event_callback,
            timezone_name=self.timezone_name,
            runtime_root=self.root,
            agent_registry=self.agent_registry,
            skill_registry=self.skill_registry,
        )
        context.emit_payload(
            kind=EventKind.AGENT_START,
            payload={"stage": "agent_start", "user_input": user_input},
        )
        session = await self.session_manager.prepare(
            session_id=session_id,
            context=context,
            model_profile=self.model_profiles["executor"],
        )

        profile = self.model_profiles["orchestrator"]
        agent = build_orchestrator_agent(
            instructions=self._build_instructions(
                session_id,
                current_query=user_input,
                context=context,
                bot=bot,
            ),
            profile=profile,
            tools=self._build_tools(bot=bot),
            log_callback=log_callback,
        )

        try:
            result = await run_orchestrator_agent(
                agent=agent,
                user_input=user_input,
                context=context,
                session=session,
                max_turns=max_turns,
                model_delta_callback=model_delta_callback,
                model_name=profile.model_name,
            )
        except Exception as exc:
            context.emit_payload(
                kind=EventKind.ERROR,
                payload={"stage": "agent_error", "error_type": type(exc).__name__},
                error=f"{type(exc).__name__}: {exc}",
            )
            context.emit_payload(
                kind=EventKind.AGENT_END,
                payload={"stage": "agent_end", "status": "failed"},
            )
            raise
        model_logs = list(local_model_logs)
        model_logs.extend(
            event["payload"]
            for event in context.events
            if event.get("kind") == "model_call" and isinstance(event.get("payload"), dict)
        )
        context.emit_payload(
            kind=EventKind.AGENT_END,
            payload={"stage": "agent_end", "status": "completed"},
        )
        return map_agent_result(
            final_output=result.final_output,
            events=list(context.events),
            model_logs=model_logs,
        )

    def _build_instructions(
        self,
        session_id: str = "",
        *,
        current_query: str = "",
        context: RuntimeContext | None = None,
        bot: BotConfig | None = None,
    ) -> str:
        bot = bot or self.bot_registry.get("default")
        parts = [
            SYSTEM_PROMPT.format(
                skills_section=self._build_skills_section(bot=bot),
                memory_role_policy=MEMORY_ROLE_POLICY if self.memory_enabled else "",
                memory_tool_policy=MEMORY_TOOL_POLICY if self.memory_enabled else "",
                memory_policy_section=MEMORY_POLICY_SECTION if self.memory_enabled else "",
            )
        ]
        user_memory = _read_optional_path(Path(os.getenv("AGENT_USER_MEMORY_PATH", "~/.agent/USER.md")).expanduser())
        project_rules, _project_rules_source = self.resource_loader.get_project_rules()
        retrieval_events: list[dict[str, Any]] = []
        memory_context = ""
        if session_id:
            memory_context = self.memory_manager.build_orchestrator_context(
                session_id,
                current_query=current_query,
                retrieval_events=retrieval_events,
            )
            todo_context = self.todo_state.format_context(session_id)
            if todo_context:
                memory_context = (
                    f"{memory_context}\n\n{todo_context}" if memory_context else todo_context
                )
        if context is not None:
            for payload in retrieval_events:
                context.emit_payload(
                    kind=EventKind.MEMORY_READ,
                    run_id=context.session_id,
                    payload=payload,
                )
        if user_memory:
            parts.append(f"用户偏好:\n{user_memory}")
        if project_rules:
            parts.append(f"项目规则:\n{project_rules}")
        if bot.instructions:
            parts.append(f"Bot 指令:\n{bot.instructions}")
        if memory_context:
            parts.append(f"<memory_context>\n{memory_context}\n</memory_context>")
        return "\n\n".join(parts)

    def _build_skills_section(self, *, bot: BotConfig | None = None) -> str:
        return self.resource_loader.format_for_bot(bot or self.bot_registry.get("default"))

    def _build_memory_context(self, session_id: str) -> str:
        return self.memory_manager.build_orchestrator_context(session_id)

    def clear_memory(self) -> None:
        self.memory_manager.clear()

    def reload_resources(self) -> dict[str, Any]:
        return self.resource_loader.reload()

    def list_capabilities(self) -> dict[str, Any]:
        return self.resource_loader.capabilities_payload()

    def list_bots(self) -> list[dict[str, Any]]:
        return self.bot_registry.list_summaries()

    def get_bot(self, bot_id: str) -> dict[str, Any]:
        return self.resource_loader.bot_payload(bot_id)

    def _validate_bot_subagents_readiness(self, bot_id: str) -> None:
        resolved_bot = self.bot_registry.get(bot_id)
        if resolved_bot.id in self._validated_bot_ids:
            return
        manifests = {manifest.name: manifest for manifest in self.agent_registry.discover()}
        for subagent_name in resolved_bot.subagents:
            manifest = manifests.get(subagent_name)
            if manifest is None:
                continue
            try:
                validate_extension_environment(manifest)
            except Exception as exc:
                raise RuntimeError(
                    f"Subagent `{subagent_name}` readiness check failed: {exc}"
                ) from exc
        self._validated_bot_ids.add(resolved_bot.id)

    def run_session_start_hook(
        self,
        *,
        session_id: str,
        bot_id: str = "default",
    ) -> HookResult:
        bot = self.bot_registry.get(bot_id)
        profile = self.model_profiles["orchestrator"]
        return self.hook_runner.run(
            "SessionStart",
            SessionStartContext(
                welcome_message=bot.welcome.message,
                welcome_preset=bot.welcome.preset,
                welcome_prompt=bot.welcome.prompt,
                welcome_model_base_url=profile.base_url,
                welcome_model_name=profile.model_name,
                welcome_model_api_key=profile.api_key,
                memory_context=self.memory_manager.build_orchestrator_context(session_id),
                subagents=_ordered_subagents(self.agent_registry, bot.subagents),
                skills=_ordered_skills(self.skill_registry, bot.skills),
            ),
        )

    def _build_tools(self, *, bot: BotConfig | None = None) -> list[Any]:
        return build_runtime_tools(self, bot=bot)


def _read_optional_path(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _ordered_subagents(registry: AgentRegistry, names: list[str]) -> list[Any]:
    items = {item.name: item for item in registry.discover()}
    return [item for name in names if (item := items.get(name)) is not None]


def _ordered_skills(registry: SkillRegistry, names: list[str]) -> list[Any]:
    items = {item.name: item for item in registry.discover()}
    return [item for name in names if (item := items.get(name)) is not None]
