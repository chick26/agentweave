from agent_runtime.core.events import EventBus, EventKind, RuntimeEvent, make_event


def test_event_bus_emits_sequenced_dict_events() -> None:
    seen = []
    bus = EventBus(events=[], callback=seen.append)

    first = bus.emit(kind=EventKind.AGENT_START, run_id="run-1", payload={"stage": "start"})
    second = bus.emit(kind="worker_run", run_id="worker-1", payload={"stage": "worker_start"})

    assert first["kind"] == "agent_start"
    assert first["sequence"] == 1
    assert second["kind"] == "worker_run"
    assert second["sequence"] == 2
    assert seen == [first, second]


def test_runtime_event_and_make_event_keep_wire_shape() -> None:
    event = RuntimeEvent(
        kind=EventKind.ERROR,
        run_id="run-1",
        payload={"stage": "failed"},
        parent_run_id="parent",
        sequence=7,
        error="boom",
    ).to_dict()

    assert event["kind"] == "error"
    assert event["run_id"] == "run-1"
    assert event["parent_run_id"] == "parent"
    assert event["sequence"] == 7
    assert event["error"] == "boom"
    assert event["payload"] == {"stage": "failed"}

    legacy = make_event(kind="subagent_trace", run_id="run-2", payload={"stage": "execute"})
    assert legacy["kind"] == "subagent_trace"
    assert legacy["payload"]["stage"] == "execute"

