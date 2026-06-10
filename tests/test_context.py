"""Tests for unified runtime context event emission and child isolation."""

from dataclasses import dataclass

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.model_profiles import ModelProfile


def _model_profile() -> ModelProfile:
    return ModelProfile(
        base_url="http://example.test/v1",
        model_name="chat",
        api_key="not-needed",
        max_tokens=128,
    )


@dataclass
class DemoTypedState:
    value: str = ""


def test_runtime_context_emits_events_through_shared_bus():
    seen = []
    context = RuntimeContext(
        run_id="session-1",
        session_id="session-1",
        model_profile=_model_profile(),
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
        model_profile=_model_profile(),
        result_store=object(),
        timezone_name="UTC",
        runtime_root=tmp_path,
        state={"tenant": "demo"},
    )

    child = parent.child(
        run_id="worker-1",
        active_subagent="text2sql",
        state={"worker_flag": "demo"},
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
    assert child.state["worker_flag"] == "demo"
    assert parent.events is child.events
    assert parent.events[0]["kind"] == "subagent_trace"
    assert parent.events[0]["run_id"] == "worker-1"


def test_runtime_context_reuses_typed_state_within_namespace():
    context = RuntimeContext(
        run_id="typed-state-run",
        model_profile=_model_profile(),
    )

    first = context.get_typed_state("demo", DemoTypedState)
    first.value = "kept"
    second = context.get_typed_state("demo", DemoTypedState)

    assert second is first
    assert second.value == "kept"


def test_runtime_context_isolates_typed_state_namespaces():
    context = RuntimeContext(
        run_id="typed-state-run",
        model_profile=_model_profile(),
    )

    first = context.get_typed_state("demo", DemoTypedState)
    second = context.get_typed_state("other", DemoTypedState)

    assert second is not first


def test_child_context_does_not_inherit_parent_typed_state():
    parent = RuntimeContext(
        run_id="parent-run",
        model_profile=_model_profile(),
    )
    parent_state = parent.get_typed_state("demo", DemoTypedState)
    parent_state.value = "parent"

    child = parent.child(run_id="child-run")
    child_state = child.get_typed_state("demo", DemoTypedState)

    assert child_state is not parent_state
    assert child_state.value == ""
