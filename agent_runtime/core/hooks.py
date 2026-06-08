"""Hook runner primitives for runtime extension points."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_HOOK_EVENTS = {"SessionStart"}


@dataclass(frozen=True)
class HookResult:
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class HookHandler(Protocol):
    event_name: str

    def run(self, context: Any) -> HookResult: ...


class HookRunner:
    def __init__(
        self,
        handlers: list[HookHandler] | dict[str, list[HookHandler]] | None = None,
    ) -> None:
        self.handlers = _normalize_handlers(handlers or [])

    def run(self, event_name: str, context: Any) -> HookResult:
        handlers = self.handlers.get(event_name, [])
        if not handlers and event_name in DEFAULT_HOOK_EVENTS:
            return HookResult()
        if not handlers:
            return HookResult(error=f"Unsupported hook event: {event_name}")
        results: list[HookResult] = []
        for handler in handlers:
            try:
                result = handler.run(context)
            except Exception as exc:
                return _hook_error_result(event_name, exc)
            results.append(result)
        return _aggregate_hook_results(results)


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
    return HookResult(
        payload={"source": "hook_error"},
        error=f"{type(exc).__name__}: {exc}",
    )


def _aggregate_hook_results(results: list[HookResult]) -> HookResult:
    if not results:
        return HookResult()
    if len(results) == 1:
        return results[0]
    messages = [result.message for result in results if result.message]
    errors = [result.error for result in results if result.error]
    payload: dict[str, Any] = {}
    handler_results: list[dict[str, Any]] = []
    for result in results:
        payload.update(result.payload)
        handler_results.append(
            {
                "message": result.message,
                "payload": result.payload,
                "error": result.error,
            }
        )
    payload["handler_results"] = handler_results
    return HookResult(
        message="\n\n".join(messages),
        payload=payload,
        error="; ".join(errors),
    )
