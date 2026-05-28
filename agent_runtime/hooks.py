from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.preset_questions import (
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


class HookRunner:
    def run(self, event_name: str, context: Any) -> HookResult:
        if event_name != "SessionStart":
            return HookResult(error=f"Unsupported hook event: {event_name}")
        try:
            return self._run_session_start(context)
        except Exception as exc:
            return HookResult(
                message=_fallback_welcome(),
                payload={"source": "fallback"},
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_session_start(self, context: SessionStartContext) -> HookResult:
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


def _fallback_welcome() -> str:
    return "你好，我可以回答已接入数据领域的问数问题。"
