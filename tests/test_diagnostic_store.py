from __future__ import annotations

import sqlite3

from agent_runtime.diagnostic_store import DiagnosticStore


def test_diagnostic_store_records_run_details(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite"
    store = DiagnosticStore(db_path)

    store.record_run(
        run_id="run_1",
        session_id="session_1",
        question="403机房有多少可用机柜？",
        answer="0",
        trace_summary="执行 SQL",
        model_logs=[
            {
                "kind": "model_call",
                "title": "编排模型",
                "model": "test-chat-model",
                "created_at": "2026-05-27T00:00:02.000Z",
                "completed_at": "2026-05-27T00:00:03.000Z",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                "request": {"messages": [{"role": "user", "content": "hi"}]},
            }
        ],
        events=[
            {
                "kind": "worker_run",
                "timestamp": "2026-05-27T00:00:00.500Z",
                "run_id": "session_1",
                "payload": {
                    "stage": "worker_start",
                    "subagent": "text2sql",
                },
            },
            {
                "kind": "subagent_trace",
                "timestamp": "2026-05-27T00:00:00.700Z",
                "run_id": "text2sql-1",
                "payload": {
                    "stage": "execute",
                    "output": {"result_id": "res_1", "row_count": 1},
                },
            },
        ],
        started_at="2026-05-27T00:00:00.000Z",
        completed_at="2026-05-27T00:00:01.000Z",
    )

    loaded = store.get_run("run_1")
    assert loaded["question"] == "403机房有多少可用机柜？"
    assert loaded["answer"] == "0"
    assert loaded["duration_ms"] == 1000
    assert loaded["model_call_count"] == 1
    assert loaded["total_prompt_tokens"] == 10
    assert loaded["total_completion_tokens"] == 5
    assert loaded["total_tokens"] == 15
    assert loaded["summary"]["execute_count"] == 1
    assert loaded["model_calls"][0]["model"] == "test-chat-model"
    assert loaded["model_calls"][0]["duration_ms"] == 1000
    assert loaded["model_calls"][0]["prompt_tokens"] == 10
    assert loaded["model_calls"][0]["message_count"] == 1
    assert loaded["model_calls"][0]["tool_count"] is None
    assert loaded["events"][1]["payload"]["payload"]["stage"] == "execute"
    assert loaded["events"][1]["created_at"] == "2026-05-27T00:00:00.700Z"
    assert len(loaded["timeline"]) == 3
    assert loaded["missing_time_items"] == []

    recent = store.recent_runs()
    assert recent[0]["run_id"] == "run_1"

    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT COUNT(*) FROM agent_run_logs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM agent_run_model_calls").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM agent_run_events").fetchone()[0] == 2


def test_diagnostic_store_orders_model_calls_by_canonical_created_at(tmp_path) -> None:
    store = DiagnosticStore(tmp_path / "sessions.sqlite")

    store.record_run(
        run_id="run_1",
        session_id="session_1",
        question="q",
        answer="a",
        model_logs=[
            {
                "kind": "model_call",
                "model": "late",
                "created_at": "2026-05-27T00:00:03.000Z",
                "completed_at": "2026-05-27T00:00:04.000Z",
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                "request": {"messages": [], "tools": []},
            },
            {
                "kind": "model_call",
                "model": "missing_time",
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                "request": {"messages": [], "tools": []},
            },
            {
                "kind": "model_call",
                "model": "early",
                "created_at": "2026-05-27T00:00:01.000Z",
                "completed_at": "2026-05-27T00:00:02.000Z",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "request": {"messages": [], "tools": []},
            },
        ],
        started_at="2026-05-27T00:00:00.000Z",
        completed_at="2026-05-27T00:00:05.000Z",
    )

    loaded = store.get_run("run_1")
    assert [call["model"] for call in loaded["model_calls"]] == [
        "early",
        "late",
        "missing_time",
    ]
    assert [call["call_index"] for call in loaded["model_calls"]] == [0, 1, 2]
    assert [item["item"]["model"] for item in loaded["timeline"]] == ["early", "late"]
    assert loaded["missing_time_items"][0]["item"]["model"] == "missing_time"
    assert "missing_model_created_at" in loaded["model_calls"][2]["diagnostic_issue"]


def test_diagnostic_store_marks_missing_fields_without_fallback(tmp_path) -> None:
    store = DiagnosticStore(tmp_path / "sessions.sqlite")

    store.record_run(
        run_id="run_1",
        session_id="session_1",
        question="q",
        answer="a",
        model_logs=[
            {
                "kind": "model_call",
                "model": "test-chat-model",
                "created_at": "2026-05-27T00:00:01.000Z",
            }
        ],
        events=[
            {
                "kind": "worker_run",
                "created_at": "2026-05-27T00:00:02.000Z",
                "payload": {"stage": "worker_start"},
            }
        ],
        started_at="2026-05-27T00:00:00.000Z",
        completed_at="2026-05-27T00:00:03.000Z",
    )

    loaded = store.get_run("run_1")
    call = loaded["model_calls"][0]
    event = loaded["events"][0]
    assert call["completed_at"] == ""
    assert call["prompt_tokens"] is None
    assert call["message_count"] is None
    assert "missing_model_completed_at" in call["diagnostic_issue"]
    assert "missing_model_usage" in call["diagnostic_issue"]
    assert "missing_model_request" in call["diagnostic_issue"]
    assert event["created_at"] == ""
    assert event["diagnostic_issue"] == "missing_event_timestamp"
    assert loaded["timeline"] == [
        {
            "type": "model_call",
            "created_at": "2026-05-27T00:00:01.000Z",
            "item": call,
        }
    ]


def test_diagnostic_store_migrates_legacy_schema(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE agent_run_logs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            trace_summary TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL
        );
        CREATE TABLE agent_run_model_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            call_index INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, call_index)
        );
        CREATE TABLE agent_run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            kind TEXT NOT NULL,
            stage TEXT NOT NULL,
            event_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, event_index)
        );
        """
    )
    connection.close()

    store = DiagnosticStore(db_path)
    store.record_run(
        run_id="run_1",
        session_id="session_1",
        question="q",
        answer="a",
        model_logs=[],
        events=[],
        started_at="2026-05-27T00:00:00.000Z",
        completed_at="2026-05-27T00:00:01.000Z",
    )

    connection = sqlite3.connect(db_path)
    log_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_run_logs)")}
    model_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_run_model_calls)")}
    event_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_run_events)")}
    assert "diagnostic_issues_json" in log_columns
    assert "duration_ms" in model_columns
    assert "diagnostic_issue" in event_columns


def test_diagnostic_store_marks_legacy_missing_diagnostics_without_json_fallback(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE agent_run_logs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            trace_summary TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL
        );
        CREATE TABLE agent_run_model_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            call_index INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, call_index)
        );
        CREATE TABLE agent_run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            kind TEXT NOT NULL,
            stage TEXT NOT NULL,
            event_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, event_index)
        );
        INSERT INTO agent_run_logs VALUES (
            'legacy_1', 'session_1', 'q', 'a', '', 'completed', '',
            '2026-05-27T00:00:00.000Z', '2026-05-27T00:00:01.000Z'
        );
        INSERT INTO agent_run_model_calls (
            run_id, call_index, kind, title, model, created_at, completed_at, payload_json
        ) VALUES (
            'legacy_1', 0, 'model_call', 'legacy', 'test-chat-model',
            '2026-05-27T00:00:00.100Z', '2026-05-27T00:00:00.200Z',
            '{"usage":{"prompt_tokens":999},"request":{"messages":[1]}}'
        );
        INSERT INTO agent_run_events (
            run_id, event_index, kind, stage, event_run_id, created_at, payload_json
        ) VALUES (
            'legacy_1', 0, 'worker_run', 'worker_start', 'worker_1', '',
            '{"timestamp":"2026-05-27T00:00:00.300Z","payload":{"stage":"worker_start"}}'
        );
        """
    )
    connection.close()

    loaded = DiagnosticStore(db_path).get_run("legacy_1")

    call = loaded["model_calls"][0]
    event = loaded["events"][0]
    assert call["prompt_tokens"] is None
    assert "missing_model_usage" in call["diagnostic_issue"]
    assert "missing_model_request" in call["diagnostic_issue"]
    assert event["created_at"] == ""
    assert event["diagnostic_issue"] == "missing_event_timestamp"
    assert loaded["missing_time_items"][0]["item"] == event


def test_diagnostic_store_overwrites_same_run(tmp_path) -> None:
    store = DiagnosticStore(tmp_path / "sessions.sqlite")

    store.record_run(
        run_id="run_1",
        session_id="session_1",
        question="first",
        answer="old",
        model_logs=[{"kind": "model_call", "model": "old"}],
        events=[{"kind": "worker_run", "payload": {"stage": "worker_start"}}],
    )
    store.record_run(
        run_id="run_1",
        session_id="session_1",
        question="second",
        answer="new",
        model_logs=[],
        events=[],
    )

    loaded = store.get_run("run_1")
    assert loaded["question"] == "second"
    assert loaded["answer"] == "new"
    assert loaded["model_calls"] == []
    assert loaded["events"] == []
