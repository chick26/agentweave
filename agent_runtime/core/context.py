"""Unified runtime context shared by orchestrator and worker subagents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from agent_runtime.core.events import EventBus, EventKind, RuntimeEvent
from agent_runtime.core.model_profiles import ModelProfile


T = TypeVar("T")
TYPED_STATE_KEY = "_typed_state"


@dataclass(kw_only=True)
class RuntimeContext:
    """Single context type for orchestrator and isolated worker runs."""

    run_id: str
    model_profile: ModelProfile
    session_id: str = ""
    bot_id: str = "default"
    parent_run_id: str = ""
    result_store: Any | None = None
    result_formatters: Any | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    event_callback: Callable[[dict[str, Any]], None] | None = None
    timezone_name: str = "Asia/Hong_Kong"
    state: dict[str, Any] = field(default_factory=dict)
    runtime_root: Path | None = None
    active_subagent: str = ""
    agent_registry: Any | None = None
    skill_registry: Any | None = None
    parent: RuntimeContext | None = None
    event_bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        if self.parent is not None:
            self.event_bus = self.parent.event_bus
            self.events = self.parent.events
            if not self.session_id:
                self.session_id = self.parent.session_id
            if self.bot_id == "default":
                self.bot_id = self.parent.bot_id
            if self.result_store is None:
                self.result_store = self.parent.result_store
            if self.result_formatters is None:
                self.result_formatters = self.parent.result_formatters
            if self.runtime_root is None:
                self.runtime_root = self.parent.runtime_root
            if self.agent_registry is None:
                self.agent_registry = self.parent.agent_registry
            if self.skill_registry is None:
                self.skill_registry = self.parent.skill_registry
            if self.timezone_name == "Asia/Hong_Kong":
                self.timezone_name = self.parent.timezone_name
            return
        self.event_bus = EventBus(events=self.events, callback=self.event_callback)
        if not self.session_id:
            self.session_id = self.run_id

    def child(
        self,
        *,
        run_id: str,
        active_subagent: str = "",
        state: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        runtime_root: Path | None = None,
    ) -> RuntimeContext:
        child_state = {
            key: value
            for key, value in self.state.items()
            if key != TYPED_STATE_KEY
        }
        if state:
            child_state.update(
                {
                    key: value
                    for key, value in state.items()
                    if key != TYPED_STATE_KEY
                }
            )
        return RuntimeContext(
            run_id=run_id,
            session_id=self.session_id,
            bot_id=self.bot_id,
            parent_run_id=parent_run_id if parent_run_id is not None else self.run_id,
            model_profile=self.model_profile,
            result_store=self.result_store,
            result_formatters=self.result_formatters,
            timezone_name=self.timezone_name,
            state=child_state,
            runtime_root=runtime_root if runtime_root is not None else self.runtime_root,
            active_subagent=active_subagent,
            agent_registry=self.agent_registry,
            skill_registry=self.skill_registry,
            parent=self,
        )

    def emit(self, event: RuntimeEvent | dict[str, Any]) -> None:
        self.event_bus.emit_event(event)

    def emit_payload(
        self,
        *,
        kind: str | EventKind,
        payload: dict[str, Any],
        run_id: str | None = None,
        parent_run_id: str | None = None,
        error: str = "",
    ) -> None:
        self.event_bus.emit(
            kind=kind,
            run_id=run_id or self.run_id,
            payload=payload,
            parent_run_id=self.parent_run_id if parent_run_id is None else parent_run_id,
            error=error,
        )

    def emit_subagent_trace(self, payload: dict[str, Any]) -> None:
        self.emit_payload(kind=EventKind.SUBAGENT_TRACE, payload=payload)

    def get_typed_state(self, namespace: str, factory: Callable[[], T]) -> T:
        """Return a run-local typed state object for one namespace."""
        clean_namespace = namespace.strip()
        if not clean_namespace:
            raise ValueError("typed state namespace is required.")
        store = self.state.setdefault(TYPED_STATE_KEY, {})
        if not isinstance(store, dict):
            raise TypeError(f"{TYPED_STATE_KEY} must be a dictionary.")
        if clean_namespace not in store:
            store[clean_namespace] = factory()
        return store[clean_namespace]
