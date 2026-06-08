"""Tests for unified runtime context event emission and child isolation."""

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind


def test_runtime_context_emits_events_through_shared_bus():
    seen = []
    context = RuntimeContext(
        run_id="session-1",
        session_id="session-1",
        model_profiles={},
        event_callback=seen.append,
    )

    context.emit_payload(
        kind=EventKind.AGENT_START,
        payload={"stage": "agent_start"},
    )

    assert context.events == seen
    assert context.events[0]["run_id"] == "session-1"


def test_child_context_inherits_runtime_resources_and_shares_events(tmp_path):
    parent = RuntimeContext(
        run_id="session-1",
        session_id="session-1",
        model_profiles={},
        result_store=object(),
        timezone_name="UTC",
        runtime_root=tmp_path,
        state={"tenant": "demo"},
    )

    child = parent.child(
        run_id="worker-1",
        active_subagent="text2sql",
        state={"active_domain": "demo"},
    )
    child.state["tenant"] = "worker-only"
    child.emit_subagent_trace({"stage": "execute"})

    assert child.parent is parent
    assert child.session_id == "session-1"
    assert child.parent_run_id == "session-1"
    assert child.result_store is parent.result_store
    assert child.timezone_name == "UTC"
    assert child.runtime_root == tmp_path
    assert child.active_subagent == "text2sql"
    assert parent.state["tenant"] == "demo"
    assert child.state["active_domain"] == "demo"
    assert parent.events is child.events
    assert parent.events[0]["kind"] == "subagent_trace"
    assert parent.events[0]["run_id"] == "worker-1"
