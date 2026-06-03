from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agent_runtime.core.preset_questions import (
    format_welcome_message,
    generate_preset_question_result,
)

HOOK_PASS = 0
HOOK_BLOCK = 1
HOOK_INJECT = 2
DEFAULT_HOOK_EVENTS = {"SessionStart", "PreToolUse", "PostToolUse"}


@dataclass(frozen=True)
class HookResult:
    exit_code: int = HOOK_PASS
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class SessionStartContext:
    skills_root: Path
    base_url: str
    model_name: str
    api_key: str
    subagents_root: Path | None = None
    questions_per_domain: int = 2
    memory_context: str = ""
    subagent_names: list[str] | None = None
    welcome_mode: str = "providers"
    welcome_provider_module: str = ""
    preset_question_groups: list[dict[str, Any]] = field(default_factory=list)


class HookHandler(Protocol):
    event_name: str

    def run(self, context: Any) -> HookResult: ...


class PresetQuestionsSessionStartHook:
    event_name = "SessionStart"

    def run(self, context: SessionStartContext) -> HookResult:
        result = generate_preset_question_result(
            skills_root=context.skills_root,
            subagents_root=context.subagents_root,
            base_url=context.base_url,
            model_name=context.model_name,
            api_key=context.api_key,
            questions_per_domain=context.questions_per_domain,
            subagent_names=context.subagent_names,
            welcome_mode=context.welcome_mode,
            welcome_provider_module=context.welcome_provider_module,
            preset_question_groups=context.preset_question_groups,
        )
        message = format_welcome_message(result.groups, result.domains)
        if context.memory_context:
            message = f"{message}\n\n我会参考已保存的项目记忆和会话摘要。"
        return HookResult(
            message=message,
            payload={
                "source": result.source,
                "groups": [group.__dict__ for group in result.groups],
                "domains": result.domains or [],
                "raw_output": result.raw_output,
            },
            error=result.error,
        )


class HookRunner:
    def __init__(
        self,
        handlers: list[HookHandler] | dict[str, list[HookHandler]] | None = None,
    ) -> None:
        if handlers is None:
            handlers = [PresetQuestionsSessionStartHook()]
        self.handlers = _normalize_handlers(handlers)

    def run(self, event_name: str, context: Any) -> HookResult:
        handlers = self.handlers.get(event_name, [])
        if not handlers and event_name in DEFAULT_HOOK_EVENTS:
            return HookResult()
        if not handlers:
            return HookResult(error=f"Unsupported hook event: {event_name}")
        for handler in handlers:
            try:
                result = handler.run(context)
            except Exception as exc:
                return _hook_error_result(event_name, exc)
            if result.exit_code in (HOOK_BLOCK, HOOK_INJECT):
                return result
        return result if handlers else HookResult()


def run_hooks(
    event_name: str,
    payload: Any,
    *,
    handlers: list[HookHandler] | dict[str, list[HookHandler]] | None = None,
) -> HookResult:
    return HookRunner(handlers=handlers).run(event_name, payload)


def _normalize_handlers(
    handlers: list[HookHandler] | dict[str, list[HookHandler]],
) -> dict[str, list[HookHandler]]:
    if isinstance(handlers, dict):
        return {event_name: list(items) for event_name, items in handlers.items()}
    grouped: dict[str, list[HookHandler]] = {}
    for handler in handlers:
        grouped.setdefault(handler.event_name, []).append(handler)
    return grouped


def _hook_error_result(event_name: str, exc: Exception) -> HookResult:
    if event_name == "SessionStart":
        return HookResult(
            message=_fallback_welcome(),
            payload={"source": "fallback"},
            error=f"{type(exc).__name__}: {exc}",
        )
    return HookResult(
        payload={"source": "hook_error"},
        error=f"{type(exc).__name__}: {exc}",
    )


def _fallback_welcome() -> str:
    return "你好，我可以回答已接入能力范围内的问题。"
