from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime.storage.database import DatabaseBackend
from agent_runtime.core.model_profiles import ModelProfile
from agent_runtime.core.events import EventBus, EventKind, RuntimeEvent


@dataclass(kw_only=True)
class BaseContext:
    backend: DatabaseBackend
    model_profiles: dict[str, ModelProfile]
    result_store: Any | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    event_callback: Callable[[dict[str, Any]], None] | None = None
    timezone_name: str = "Asia/Hong_Kong"
    event_bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        self.event_bus = EventBus(events=self.events, callback=self.event_callback)


@dataclass
class OrchestratorContext(BaseContext):
    session_id: str

    def emit(self, event: RuntimeEvent | dict[str, Any]) -> None:
        self.event_bus.emit_event(event)

    def emit_payload(
        self,
        *,
        kind: str | EventKind,
        run_id: str,
        payload: dict[str, Any],
        parent_run_id: str = "",
        error: str = "",
    ) -> None:
        self.event_bus.emit(
            kind=kind,
            run_id=run_id,
            payload=payload,
            parent_run_id=parent_run_id,
            error=error,
        )


@dataclass
class RunContext(BaseContext):
    run_id: str
    active_domain: str = ""
    active_table: str = ""
    active_text_fields: list[str] = field(default_factory=list)
    active_field_descriptions: dict[str, str] = field(default_factory=dict)
    agent_registry: Any | None = None
    skill_registry: Any | None = None

    def emit_payload(
        self,
        *,
        kind: str | EventKind,
        payload: dict[str, Any],
        parent_run_id: str = "",
        error: str = "",
    ) -> None:
        self.event_bus.emit(
            kind=kind,
            run_id=self.run_id,
            payload=payload,
            parent_run_id=parent_run_id,
            error=error,
        )

    def emit_subagent_trace(self, payload: dict[str, Any]) -> None:
        self.emit_payload(kind=EventKind.SUBAGENT_TRACE, payload=payload)
