from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime.common import utc_now_iso
from agent_runtime.database import DatabaseBackend
from agent_runtime.model_profiles import ModelProfile


def make_event(
    *,
    kind: str,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "timestamp": utc_now_iso(),
        "run_id": run_id,
        "payload": payload,
    }


@dataclass
class OrchestratorContext:
    session_id: str
    backend: DatabaseBackend
    model_profiles: dict[str, ModelProfile]
    result_store: Any | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    event_callback: Callable[[dict[str, Any]], None] | None = None
    timezone_name: str = "Asia/Hong_Kong"

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.event_callback is not None:
            self.event_callback(event)

    def emit_payload(
        self,
        *,
        kind: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> None:
        self.emit(make_event(kind=kind, run_id=run_id, payload=payload))


@dataclass
class RunContext:
    run_id: str
    backend: DatabaseBackend
    model_profiles: dict[str, ModelProfile]
    result_store: Any | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    event_callback: Callable[[dict[str, Any]], None] | None = None
    timezone_name: str = "Asia/Hong_Kong"
    active_domain: str = ""
    active_table: str = ""
    active_text_fields: list[str] = field(default_factory=list)
    active_field_descriptions: dict[str, str] = field(default_factory=dict)
    agent_registry: Any | None = None
    skill_registry: Any | None = None

    def emit_payload(self, *, kind: str, payload: dict[str, Any]) -> None:
        event = make_event(kind=kind, run_id=self.run_id, payload=payload)
        self.events.append(event)
        if self.event_callback is not None:
            self.event_callback(event)

    def emit_subagent_trace(self, payload: dict[str, Any]) -> None:
        self.emit_payload(kind="subagent_trace", payload=payload)
