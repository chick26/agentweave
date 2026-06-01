from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agent_runtime.core.preset_questions import (
    format_welcome_message,
    generate_preset_question_result,
)


@dataclass(frozen=True)
class HookResult:
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
    def __init__(self, handlers: list[HookHandler] | None = None) -> None:
        self.handlers = (
            list(handlers) if handlers is not None else [PresetQuestionsSessionStartHook()]
        )

    def run(self, event_name: str, context: Any) -> HookResult:
        for handler in self.handlers:
            if handler.event_name != event_name:
                continue
            try:
                return handler.run(context)
            except Exception as exc:
                return HookResult(
                    message=_fallback_welcome(),
                    payload={"source": "fallback"},
                    error=f"{type(exc).__name__}: {exc}",
                )
        return HookResult(error=f"Unsupported hook event: {event_name}")


def _fallback_welcome() -> str:
    return "你好，我可以回答已接入数据领域的问数问题。"
