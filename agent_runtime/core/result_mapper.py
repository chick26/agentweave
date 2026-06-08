"""Map runtime events and SDK results to public API payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentRunResult:
    final_output: Any
    events: list[dict[str, Any]] = field(default_factory=list)
    subagent_trace: list[dict[str, Any]] = field(default_factory=list)
    model_logs: list[dict[str, Any]] = field(default_factory=list)
    worker_runs: list[dict[str, Any]] = field(default_factory=list)
    todo_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_output": self.final_output,
            "events": self.events,
            "subagent_trace": self.subagent_trace,
            "model_logs": self.model_logs,
            "worker_runs": self.worker_runs,
            "todo_events": self.todo_events,
        }


def map_agent_result(
    *,
    final_output: Any,
    events: list[dict[str, Any]],
    model_logs: list[dict[str, Any]],
) -> dict[str, Any]:
    return AgentRunResult(
        final_output=final_output,
        events=list(events),
        subagent_trace=subagent_trace(events),
        model_logs=model_logs,
        worker_runs=[
            event
            for event in events
            if event.get("kind") in {"subagent_dispatch", "subagent_complete", "worker_run"}
        ],
        todo_events=[event for event in events if event.get("kind") == "todo_event"],
    ).to_dict()


def subagent_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace = []
    for event in events:
        if event.get("kind") == "subagent_trace":
            payload = event.get("payload")
            if isinstance(payload, dict):
                trace.append(payload)
    return trace
