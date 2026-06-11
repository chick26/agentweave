"""Main AgentWeave runtime facade and SDK agent construction."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from agents import Agent, ModelSettings, Runner, set_tracing_disabled

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.hooks import HookResult
from agent_runtime.hooks.session_start import SessionStartContext
from agent_runtime.core.prompts import (
    MEMORY_POLICY_SECTION,
    MEMORY_ROLE_POLICY,
    MEMORY_TOOL_POLICY,
    SYSTEM_PROMPT,
)
from agent_runtime.core.result_mapper import map_agent_result
from agent_runtime.core.services import RuntimeServices
from agent_runtime.core.runtime_utils import (
    build_model,
    get_current_time_payload,
    json_dumps,
    to_jsonable,
)
from agent_runtime.worker.subagent_extensions import validate_extension_environment
from agent_runtime.core.tool_factory import build_runtime_tools
from agent_runtime.registry.bot_registry import BotConfig
from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.registry.skill_registry import SkillRegistry

from agent_runtime.common import env_bool


class AgentRuntime:
    """General orchestrator runtime with manifest-driven subagents."""

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str,
        session_db_path: Path,
        max_tokens: int = 4096,
        embedding_base_url: str | None = None,
        embedding_model_name: str | None = None,
        memory_enabled: bool | None = None,
        timezone_name: str | None = None,
        validate_subagents: bool | None = None,
    ) -> None:
        set_tracing_disabled(True)
        self.services = RuntimeServices.build(
            base_url=base_url,
            model_name=model_name,
            api_key=api_key,
            session_db_path=session_db_path,
            max_tokens=max_tokens,
            embedding_base_url=embedding_base_url,
            embedding_model_name=embedding_model_name,
            memory_enabled=memory_enabled,
            timezone_name=timezone_name,
        )
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

        bot = self.services.bot_registry.get(bot_id)
        if self.validate_subagents:
            self._validate_bot_subagents_readiness(bot.id)
        context = self._build_run_context(
            session_id=session_id,
            bot=bot,
            event_callback=event_callback,
        )
        context.emit_payload(
            kind=EventKind.AGENT_START,
            payload={"stage": "agent_start", "user_input": user_input},
        )
        session = await self._prepare_session(session_id=session_id, context=context)
        agent = self._build_orchestrator_agent(
            session_id=session_id,
            user_input=user_input,
            context=context,
            bot=bot,
            log_callback=log_callback,
        )

        try:
            result = await self._run_orchestrator_agent(
                agent=agent,
                user_input=user_input,
                context=context,
                session=session,
                max_turns=max_turns,
                model_delta_callback=model_delta_callback,
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
        model_logs = _collect_model_logs(context, local_model_logs)
        context.emit_payload(
            kind=EventKind.AGENT_END,
            payload={"stage": "agent_end", "status": "completed"},
        )
        return map_agent_result(
            final_output=result.final_output,
            events=list(context.events),
            model_logs=model_logs,
        )

    def _build_run_context(
        self,
        *,
        session_id: str,
        bot: BotConfig,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> RuntimeContext:
        return RuntimeContext(
            run_id=session_id,
            session_id=session_id,
            bot_id=bot.id,
            model_profile=self.services.model_profile,
            artifact_store=self.services.artifact_store,
            result_formatters=self.services.result_formatters,
            event_callback=event_callback,
            timezone_name=self.services.timezone_name,
            runtime_root=self.services.root,
            agent_registry=self.services.agent_registry,
            skill_registry=self.services.skill_registry,
        )

    async def _prepare_session(self, *, session_id: str, context: RuntimeContext) -> Any:
        return await self.services.session_manager.prepare(
            session_id=session_id,
            context=context,
            model_profile=self.services.model_profile,
        )

    def _build_orchestrator_agent(
        self,
        *,
        session_id: str,
        user_input: str,
        context: RuntimeContext,
        bot: BotConfig,
        log_callback: Callable[[dict[str, Any]], None],
    ) -> Agent[RuntimeContext]:
        return Agent[RuntimeContext](
            name="Agent Orchestrator",
            instructions=self._build_instructions(
                session_id,
                current_query=user_input,
                context=context,
                bot=bot,
            ),
            model=build_model(
                profile=self.services.model_profile,
                log_callback=log_callback,
                title="编排模型调用",
                kind="orchestration_model",
            ),
            model_settings=ModelSettings(max_tokens=self.services.model_profile.max_tokens),
            tools=self._build_tools(bot=bot),
        )

    async def _run_orchestrator_agent(
        self,
        *,
        agent: Agent[RuntimeContext],
        user_input: str,
        context: RuntimeContext,
        session: Any,
        max_turns: int,
        model_delta_callback: Callable[[dict[str, Any]], None] | None,
    ) -> Any:
        if model_delta_callback is None:
            return await Runner.run(
                agent,
                user_input,
                context=context,
                session=session,
                max_turns=max_turns,
            )
        result = Runner.run_streamed(
            agent,
            user_input,
            context=context,
            session=session,
            max_turns=max_turns,
        )
        async for stream_event in result.stream_events():
            if delta := _model_text_delta(stream_event):
                model_delta_callback(
                    {
                        "kind": "orchestration_model",
                        "stage": "model_delta",
                        "title": "编排模型调用",
                        "model": self.services.model_profile.model_name,
                        "delta": delta,
                    }
                )
        return result

    def _build_instructions(
        self,
        session_id: str = "",
        *,
        current_query: str = "",
        context: RuntimeContext | None = None,
        bot: BotConfig | None = None,
    ) -> str:
        bot = bot or self.services.bot_registry.get("default")
        parts = [
            SYSTEM_PROMPT.format(
                skills_section=self._build_skills_section(bot=bot),
                memory_role_policy=MEMORY_ROLE_POLICY if self.services.memory_enabled else "",
                memory_tool_policy=MEMORY_TOOL_POLICY if self.services.memory_enabled else "",
                memory_policy_section=MEMORY_POLICY_SECTION if self.services.memory_enabled else "",
            )
        ]
        parts.append(
            "<current_time>\n"
            f"{json_dumps(get_current_time_payload(self.services.timezone_name))}\n"
            "</current_time>"
        )
        user_memory = _read_optional_path(Path(os.getenv("AGENT_USER_MEMORY_PATH", "~/.agent/USER.md")).expanduser())
        project_rules, _project_rules_source = self.services.resource_loader.get_project_rules()
        retrieval_events: list[dict[str, Any]] = []
        memory_context = ""
        if session_id:
            memory_context = self.services.memory_manager.build_orchestrator_context(
                session_id,
                current_query=current_query,
                retrieval_events=retrieval_events,
            )
            todo_context = self.services.todo_state.format_context(session_id)
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
        return self.services.resource_loader.format_for_bot(bot or self.services.bot_registry.get("default"))

    def _build_memory_context(self, session_id: str) -> str:
        return self.services.memory_manager.build_orchestrator_context(session_id)

    def clear_memory(self) -> None:
        self.services.memory_manager.clear()

    def reload_resources(self) -> dict[str, Any]:
        summary = self.services.resource_loader.reload()
        self.services.rebuild_result_formatters()
        return summary

    def list_capabilities(self) -> dict[str, Any]:
        return self.services.resource_loader.capabilities_payload()

    def list_bots(self) -> list[dict[str, Any]]:
        return self.services.bot_registry.list_summaries()

    def get_bot(self, bot_id: str) -> dict[str, Any]:
        return self.services.resource_loader.bot_payload(bot_id)

    def _validate_bot_subagents_readiness(self, bot_id: str) -> None:
        resolved_bot = self.services.bot_registry.get(bot_id)
        if resolved_bot.id in self._validated_bot_ids:
            return
        manifests = {manifest.name: manifest for manifest in self.services.agent_registry.discover()}
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
        bot = self.services.bot_registry.get(bot_id)
        return self.services.hook_runner.run(
            "SessionStart",
            SessionStartContext(
                welcome_message=bot.welcome.message,
                welcome_preset=bot.welcome.preset,
                welcome_prompt=bot.welcome.prompt,
                welcome_model_base_url=self.services.model_profile.base_url,
                welcome_model_name=self.services.model_profile.model_name,
                welcome_model_api_key=self.services.model_profile.api_key,
                memory_context=(
                    self.services.memory_manager.build_orchestrator_context(session_id)
                    if self.services.memory_enabled
                    else ""
                ),
                subagents=_ordered_subagents(self.services.agent_registry, bot.subagents),
                skills=_ordered_skills(self.services.skill_registry, bot.skills),
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


def _collect_model_logs(
    context: RuntimeContext,
    local_model_logs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    model_logs = list(local_model_logs)
    model_logs.extend(
        event["payload"]
        for event in context.events
        if event.get("kind") == "model_call" and isinstance(event.get("payload"), dict)
    )
    return model_logs


def _model_text_delta(stream_event: Any) -> str:
    if getattr(stream_event, "type", "") != "raw_response_event":
        return ""
    data = getattr(stream_event, "data", None)
    if getattr(data, "type", "") != "response.output_text.delta":
        return ""
    delta = getattr(data, "delta", "")
    return delta if isinstance(delta, str) else ""
