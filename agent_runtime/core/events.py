from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agent_runtime.common import utc_now_iso


class EventKind(str, Enum):
    SESSION_START = "session_start"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TOOL_RESULT = "tool_result"
    SUBAGENT_DISPATCH = "subagent_dispatch"
    SUBAGENT_COMPLETE = "subagent_complete"
    WORKER_RUN = "worker_run"
    SUBAGENT_TRACE = "subagent_trace"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    MEMORY_EVENT = "memory_event"
    CONTEXT_COMPRESSED = "context_compressed"
    MODEL_CALL = "model_call"
    TODO_EVENT = "todo_event"
    SKILL_EVENT = "skill_event"
    RESULT_CREATED = "result_created"
    RESOURCES_RELOADED = "resources_reloaded"
    SESSION_FORKED = "session_forked"
    SESSION_TEMPLATE_STARTED = "session_template_started"
    SESSION_TEMPLATE_SAVED = "session_template_saved"
    ERROR = "error"


_LEGACY_KIND_MAP = {
    "memory_search": EventKind.MEMORY_READ,
    "memory_write": EventKind.MEMORY_WRITE,
}


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str | EventKind
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now_iso)
    parent_run_id: str = ""
    sequence: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": _kind_value(self.kind),
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "payload": self.payload,
            "sequence": self.sequence,
        }
        if self.parent_run_id:
            data["parent_run_id"] = self.parent_run_id
        if self.error:
            data["error"] = self.error
        return data


class EventBus:
    def __init__(
        self,
        *,
        events: list[dict[str, Any]] | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.callback = callback
        self._sequence = _max_sequence(self.events)

    def emit(
        self,
        *,
        kind: str | EventKind,
        run_id: str,
        payload: dict[str, Any] | None = None,
        parent_run_id: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        self._sequence += 1
        event = RuntimeEvent(
            kind=_normalize_kind(kind),
            run_id=run_id,
            payload=payload or {},
            parent_run_id=parent_run_id,
            sequence=self._sequence,
            error=error,
        ).to_dict()
        self.events.append(event)
        if self.callback is not None:
            self.callback(event)
        return event

    def emit_event(self, event: RuntimeEvent | dict[str, Any]) -> dict[str, Any]:
        if isinstance(event, RuntimeEvent):
            self._sequence += 1
            event_dict = RuntimeEvent(
                kind=event.kind,
                run_id=event.run_id,
                payload=event.payload,
                timestamp=event.timestamp,
                parent_run_id=event.parent_run_id,
                sequence=event.sequence or self._sequence,
                error=event.error,
            ).to_dict()
        else:
            self._sequence += 1
            event_dict = _normalize_event_dict(event, self._sequence)
        self.events.append(event_dict)
        if self.callback is not None:
            self.callback(event_dict)
        return event_dict


def make_event(
    *,
    kind: str | EventKind,
    run_id: str,
    payload: dict[str, Any],
    parent_run_id: str = "",
    error: str = "",
) -> dict[str, Any]:
    return RuntimeEvent(
        kind=_normalize_kind(kind),
        run_id=run_id,
        payload=payload,
        parent_run_id=parent_run_id,
        error=error,
    ).to_dict()


def _normalize_event_dict(event: dict[str, Any], sequence: int) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return RuntimeEvent(
        kind=_normalize_kind(str(event.get("kind") or "")),
        run_id=str(event.get("run_id") or ""),
        payload=payload,
        timestamp=str(event.get("timestamp") or utc_now_iso()),
        parent_run_id=str(event.get("parent_run_id") or ""),
        sequence=int(event.get("sequence") or sequence),
        error=str(event.get("error") or ""),
    ).to_dict()


def _normalize_kind(kind: str | EventKind) -> str:
    kind_value = _kind_value(kind)
    mapped = _LEGACY_KIND_MAP.get(kind_value)
    return _kind_value(mapped) if mapped else kind_value


def _kind_value(kind: str | EventKind) -> str:
    return kind.value if isinstance(kind, EventKind) else str(kind)


def _max_sequence(events: list[dict[str, Any]]) -> int:
    values = []
    for event in events:
        try:
            values.append(int(event.get("sequence") or 0))
        except (TypeError, ValueError):
            continue
    return max(values, default=0)
