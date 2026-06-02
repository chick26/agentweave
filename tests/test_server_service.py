from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from agent_runtime.server.service import AgentService, AgentServiceConfig
from agent_runtime.storage.diagnostic_store import DiagnosticStore
from agent_runtime.storage.result_store import ResultStore


class FakeRuntime:
    def __init__(self, result_store: ResultStore) -> None:
        self.result_store = result_store

    def run_session_start_hook(self, **kwargs):
        return SimpleNamespace(message=f"welcome:{kwargs['session_id']}")

    async def ask(
        self,
        question,
        session_id,
        event_callback=None,
        model_delta_callback=None,
        max_turns=10,
    ):
        events = [
            {
                "kind": "subagent_trace",
                "timestamp": "2026-06-01T10:00:01Z",
                "run_id": session_id,
                "payload": {"stage": "plan", "title": "规划查询"},
                "sequence": 1,
            },
            {
                "kind": "result_created",
                "timestamp": "2026-06-01T10:00:02Z",
                "run_id": session_id,
                "payload": {
                    "ui_content": {
                        "result_id": "res_fake",
                        "sample_rows": [{"count": 1}],
                        "row_count": 1,
                        "has_more": False,
                    }
                },
                "sequence": 2,
            },
        ]
        for event in events:
            if event_callback is not None:
                event_callback(event)
        if model_delta_callback is not None:
            model_delta_callback(
                {
                    "kind": "orchestration_model",
                    "stage": "model_delta",
                    "title": "编排模型调用",
                    "model": "chat",
                    "delta": "当前",
                }
            )
            model_delta_callback(
                {
                    "kind": "orchestration_model",
                    "stage": "model_delta",
                    "title": "编排模型调用",
                    "model": "chat",
                    "delta": "答案",
                }
            )
        return {
            "final_output": f"answer:{question}",
            "events": events,
            "model_logs": [],
        }

    def reload_resources(self):
        return {"skills": 1, "subagents": 1, "domains": 2}


def _service(tmp_path: Path) -> AgentService:
    config = AgentServiceConfig(
        root=tmp_path,
        base_url="http://example.test/v1",
        model_name="chat",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )
    return AgentService(
        config=config,
        runtime=FakeRuntime(ResultStore(tmp_path / "results.sqlite")),
        diagnostic_store=DiagnosticStore(tmp_path / "diagnostics.sqlite"),
    )


def _service_with_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentService:
    monkeypatch.setenv("AGENTWEAVE_RUN_CACHE_MAX", "1")
    monkeypatch.setenv("AGENTWEAVE_RUN_CACHE_TTL_SECONDS", "9999")
    return _service(tmp_path)


def test_service_creates_session_with_capabilities(tmp_path: Path) -> None:
    service = _service(tmp_path)

    session = service.create_session(session_id="web-test")

    assert session["session_id"] == "web-test"
    assert session["message"] == "welcome:web-test"
    assert session["capabilities"] == {
        "streaming": True,
        "results": True,
        "diagnostics": True,
        "resource_reload": True,
    }


def test_service_run_streams_runtime_result_and_complete_events(tmp_path: Path) -> None:
    service = _service(tmp_path)

    created = service.create_run(session_id="web-test", message="查 403")
    events = list(service.iter_sse_events(created["run_id"]))
    run = service.get_run(created["run_id"])

    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5, 6]
    assert [event["type"] for event in events] == [
        "runtime_event",
        "runtime_event",
        "result_created",
        "model_delta",
        "model_delta",
        "run_complete",
    ]
    assert [
        event["payload"]["delta"]
        for event in events
        if event["type"] == "model_delta"
    ] == [
        "当前",
        "答案",
    ]
    assert events[3]["payload"]["kind"] == "orchestration_model"
    assert events[-1]["answer"] == "answer:查 403"
    assert run["status"] == "completed"
    assert run["result_ids"] == ["res_fake"]
    assert service.get_diagnostic(created["run_id"])["summary"]["event_count"] == 2


def test_service_sse_after_sequence_resumes_model_delta(tmp_path: Path) -> None:
    service = _service(tmp_path)

    created = service.create_run(session_id="web-test", message="查 403")
    all_events = list(service.iter_sse_events(created["run_id"]))
    resumed = list(service.iter_sse_events(created["run_id"], after_sequence=3))

    assert [event["type"] for event in all_events[3:]] == [
        "model_delta",
        "model_delta",
        "run_complete",
    ]
    assert resumed == all_events[3:]


def test_service_result_page_and_csv_export(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result_id = service.runtime.result_store.create_result(
        run_id="run-1",
        domain="idc_resources",
        sql="SELECT 1 AS count",
        rows=[{"count": 1}, {"count": 2}],
    )

    page = service.get_result_page(result_id, page=1, page_size=1)
    csv_data = service.export_result_csv(result_id)

    assert page["result_id"] == result_id
    assert page["rows"] == [{"count": 1}]
    assert page["has_more"] is True
    assert "count" in csv_data.decode("utf-8-sig")


def test_service_reload_resources_returns_ui_event(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.reload_resources(reason="test")

    assert result["reloaded"] is True
    assert result["event"]["kind"] == "resources_reloaded"
    assert result["event"]["payload"]["summary"]["domains"] == 2


def test_service_prunes_completed_runs_by_max_cache_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service_with_limits(tmp_path, monkeypatch)

    first = service.create_run(session_id="web-test", message="first")
    list(service.iter_sse_events(first["run_id"]))
    second = service.create_run(session_id="web-test", message="second")
    list(service.iter_sse_events(second["run_id"]))

    with pytest.raises(KeyError):
        service.get_run(first["run_id"])
    assert service.get_run(second["run_id"])["status"] == "completed"


def test_service_prunes_completed_runs_by_ttl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTWEAVE_RUN_CACHE_MAX", "1000")
    monkeypatch.setenv("AGENTWEAVE_RUN_CACHE_TTL_SECONDS", "0.001")
    service = _service(tmp_path)

    created = service.create_run(session_id="web-test", message="ttl")
    list(service.iter_sse_events(created["run_id"]))
    service._runs[created["run_id"]].completed_monotonic = time.monotonic() - 10
    service.create_run(session_id="web-test", message="trigger prune")

    with pytest.raises(KeyError):
        service.get_run(created["run_id"])
