from agent_runtime.core.context import BaseContext, OrchestratorContext, RunContext
from agent_runtime.core.events import EventKind
from agent_runtime.storage.database import CsvSQLiteBackend


def _backend(tmp_path):
    csv_path = tmp_path / "demo.csv"
    csv_path.write_text("value\n1\n", encoding="utf-8")
    return CsvSQLiteBackend({"demo": csv_path})


def test_orchestrator_context_uses_base_event_bus(tmp_path):
    seen = []
    context = OrchestratorContext(
        session_id="session-1",
        backend=_backend(tmp_path),
        model_profiles={},
        event_callback=seen.append,
    )

    context.emit_payload(
        kind=EventKind.AGENT_START,
        run_id=context.session_id,
        payload={"stage": "agent_start"},
    )

    assert isinstance(context, BaseContext)
    assert context.events == seen
    assert context.events[0]["run_id"] == "session-1"


def test_run_context_keeps_worker_state_and_subagent_trace(tmp_path):
    context = RunContext(
        run_id="worker-1",
        backend=_backend(tmp_path),
        model_profiles={},
        active_domain="demo",
        active_table="demo",
    )

    context.emit_subagent_trace({"stage": "execute"})

    assert isinstance(context, BaseContext)
    assert context.active_domain == "demo"
    assert context.events[0]["kind"] == "subagent_trace"
    assert context.events[0]["run_id"] == "worker-1"
