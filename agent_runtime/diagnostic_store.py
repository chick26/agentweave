from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_runtime.runtime_utils import to_jsonable


class DiagnosticStore:
    """Persist UI-facing run diagnostics for later debugging and optimization."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=5,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._lock = threading.RLock()
        self._init_schema()

    def record_run(
        self,
        *,
        run_id: str,
        session_id: str,
        question: str,
        answer: str,
        trace_summary: str = "",
        model_logs: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        status: str = "completed",
        error: str = "",
    ) -> None:
        model_logs = model_logs or []
        events = events or []
        run_issues: list[dict[str, Any]] = []
        started_at = str(started_at or "")
        completed_at = str(completed_at or "")
        if not started_at:
            run_issues.append({"scope": "run", "issue": "missing_run_started_at"})
        if not completed_at:
            run_issues.append({"scope": "run", "issue": "missing_run_completed_at"})
        duration_ms = _duration_ms(started_at, completed_at)
        if started_at and completed_at and duration_ms is None:
            run_issues.append({"scope": "run", "issue": "invalid_run_duration"})
        model_rows, model_issues = _prepare_model_call_rows(run_id, model_logs)
        event_rows, event_issues = _prepare_event_rows(run_id, events)
        run_issues.extend(model_issues)
        run_issues.extend(event_issues)
        prompt_tokens = _sum_known(row["prompt_tokens"] for row in model_rows)
        completion_tokens = _sum_known(row["completion_tokens"] for row in model_rows)
        total_tokens = _sum_known(row["total_tokens"] for row in model_rows)
        with self._lock, self._connection:
            self._delete_run(run_id)
            self._connection.execute(
                """
                INSERT INTO agent_run_logs (
                    run_id, session_id, question, answer, trace_summary,
                    status, error, started_at, completed_at,
                    duration_ms, model_call_count, total_prompt_tokens,
                    total_completion_tokens, total_tokens, diagnostic_issues_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session_id,
                    question,
                    answer,
                    trace_summary,
                    status,
                    error,
                    started_at,
                    completed_at,
                    duration_ms,
                    len(model_rows),
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    _json_dumps(run_issues),
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO agent_run_model_calls (
                    run_id, call_index, kind, title, model,
                    created_at, completed_at, payload_json,
                    duration_ms, prompt_tokens, completion_tokens, total_tokens,
                    message_count, tool_count, has_error, diagnostic_issue
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (_model_call_insert_tuple(row) for row in model_rows),
            )
            self._connection.executemany(
                """
                INSERT INTO agent_run_events (
                    run_id, event_index, kind, stage, event_run_id,
                    created_at, payload_json, diagnostic_issue
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_event_insert_tuple(row) for row in event_rows),
            )

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT run_id, session_id, question, status, error,
                       started_at, completed_at, duration_ms,
                       model_call_count, total_prompt_tokens,
                       total_completion_tokens, total_tokens,
                       diagnostic_issues_json
                FROM agent_run_logs
                ORDER BY completed_at DESC, run_id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._connection.execute(
                """
                SELECT run_id, session_id, question, answer, trace_summary,
                       status, error, started_at, completed_at,
                       duration_ms, model_call_count, total_prompt_tokens,
                       total_completion_tokens, total_tokens,
                       diagnostic_issues_json
                FROM agent_run_logs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Unknown diagnostic run: {run_id}")
            model_logs = self._connection.execute(
                """
                SELECT call_index, kind, title, model, created_at, completed_at,
                       duration_ms, prompt_tokens, completion_tokens, total_tokens,
                       message_count, tool_count, has_error, diagnostic_issue,
                       payload_json
                FROM agent_run_model_calls
                WHERE run_id = ?
                ORDER BY call_index
                """,
                (run_id,),
            ).fetchall()
            events = self._connection.execute(
                """
                SELECT event_index, kind, stage, event_run_id, created_at,
                       diagnostic_issue, payload_json
                FROM agent_run_events
                WHERE run_id = ?
                ORDER BY event_index
                """,
                (run_id,),
            ).fetchall()
        run_payload = dict(run)
        run_payload["diagnostic_issues"] = _load_issues(
            str(run_payload.get("diagnostic_issues_json") or "")
        )
        model_calls = _order_items_by_canonical_time(
            [_model_call_from_row(row) for row in model_logs],
            index_key="call_index",
        )
        event_items = _order_items_by_canonical_time(
            [_event_from_row(row) for row in events],
            index_key="event_index",
        )
        read_issues = _read_time_issues(model_calls, event_items)
        if read_issues:
            run_payload["diagnostic_issues"].extend(read_issues)
        run_payload["model_calls"] = model_calls
        run_payload["events"] = event_items
        run_payload["timeline"] = _timeline_items(model_calls, event_items)
        run_payload["missing_time_items"] = _missing_time_items(model_calls, event_items)
        run_payload["summary"] = _summarize_run(run_payload, model_calls, event_items)
        return run_payload

    def _init_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_run_logs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    trace_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    duration_ms INTEGER,
                    model_call_count INTEGER,
                    total_prompt_tokens INTEGER,
                    total_completion_tokens INTEGER,
                    total_tokens INTEGER,
                    diagnostic_issues_json TEXT
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_run_model_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    call_index INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    duration_ms INTEGER,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    message_count INTEGER,
                    tool_count INTEGER,
                    has_error INTEGER,
                    diagnostic_issue TEXT,
                    UNIQUE(run_id, call_index),
                    FOREIGN KEY (run_id) REFERENCES agent_run_logs(run_id)
                        ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    event_run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    diagnostic_issue TEXT,
                    UNIQUE(run_id, event_index),
                    FOREIGN KEY (run_id) REFERENCES agent_run_logs(run_id)
                        ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_run_logs_session_completed
                ON agent_run_logs(session_id, completed_at)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_run_model_calls_run
                ON agent_run_model_calls(run_id, call_index)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_run_events_run
                ON agent_run_events(run_id, event_index)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_run_events_kind_stage
                ON agent_run_events(kind, stage)
                """
            )
            _ensure_columns(
                self._connection,
                "agent_run_logs",
                {
                    "duration_ms": "INTEGER",
                    "model_call_count": "INTEGER",
                    "total_prompt_tokens": "INTEGER",
                    "total_completion_tokens": "INTEGER",
                    "total_tokens": "INTEGER",
                    "diagnostic_issues_json": "TEXT",
                },
            )
            _ensure_columns(
                self._connection,
                "agent_run_model_calls",
                {
                    "duration_ms": "INTEGER",
                    "prompt_tokens": "INTEGER",
                    "completion_tokens": "INTEGER",
                    "total_tokens": "INTEGER",
                    "message_count": "INTEGER",
                    "tool_count": "INTEGER",
                    "has_error": "INTEGER",
                    "diagnostic_issue": "TEXT",
                },
            )
            _ensure_columns(
                self._connection,
                "agent_run_events",
                {
                    "diagnostic_issue": "TEXT",
                },
            )

    def _delete_run(self, run_id: str) -> None:
        self._connection.execute(
            "DELETE FROM agent_run_events WHERE run_id = ?",
            (run_id,),
        )
        self._connection.execute(
            "DELETE FROM agent_run_model_calls WHERE run_id = ?",
            (run_id,),
        )
        self._connection.execute(
            "DELETE FROM agent_run_logs WHERE run_id = ?",
            (run_id,),
        )


def _prepare_model_call_rows(
    run_id: str,
    model_logs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for original_index, log in enumerate(model_logs):
        row_issues: list[str] = []
        created_at = str(log.get("created_at") or "")
        completed_at = str(log.get("completed_at") or "")
        if not created_at:
            row_issues.append("missing_model_created_at")
        elif _parse_timestamp(created_at) is None:
            row_issues.append("invalid_model_created_at")
        if not completed_at:
            row_issues.append("missing_model_completed_at")
        elif _parse_timestamp(completed_at) is None:
            row_issues.append("invalid_model_completed_at")

        duration_ms = _duration_ms(created_at, completed_at)
        if created_at and completed_at and duration_ms is None:
            row_issues.append("invalid_model_duration")

        usage = log.get("usage")
        if not isinstance(usage, dict):
            row_issues.append("missing_model_usage")
            prompt_tokens = None
            completion_tokens = None
            total_tokens = None
        else:
            prompt_tokens = _optional_int(usage.get("prompt_tokens"))
            completion_tokens = _optional_int(usage.get("completion_tokens"))
            total_tokens = _optional_int(usage.get("total_tokens"))

        request = log.get("request")
        if not isinstance(request, dict):
            row_issues.append("missing_model_request")
            message_count = None
            tool_count = None
        else:
            messages = request.get("messages")
            tools = request.get("tools")
            message_count = len(messages) if isinstance(messages, list) else None
            tool_count = len(tools) if isinstance(tools, list) else None

        row = {
            "run_id": run_id,
            "original_index": original_index,
            "kind": str(log.get("kind") or ""),
            "title": str(log.get("title") or ""),
            "model": str(log.get("model") or ""),
            "created_at": created_at,
            "completed_at": completed_at,
            "payload_json": _json_dumps(log),
            "duration_ms": duration_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "message_count": message_count,
            "tool_count": tool_count,
            "has_error": 1 if log.get("error") else 0,
            "diagnostic_issue": ";".join(row_issues),
        }
        rows.append(row)
        for issue in row_issues:
            issues.append(
                {
                    "scope": "model_call",
                    "original_index": original_index,
                    "issue": issue,
                }
            )

    timestamped = [
        row for row in rows
        if row["created_at"] and _parse_timestamp(str(row["created_at"])) is not None
    ]
    without_time = [row for row in rows if row not in timestamped]
    timestamped.sort(key=lambda row: _parse_timestamp(str(row["created_at"])) or datetime.min)
    ordered = timestamped + without_time
    for call_index, row in enumerate(ordered):
        row["call_index"] = call_index
    return ordered, issues


def _prepare_event_rows(
    run_id: str,
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        row = _event_row(run_id, index, event)
        rows.append(row)
        if row["diagnostic_issue"]:
            issues.append(
                {
                    "scope": "event",
                    "event_index": index,
                    "issue": row["diagnostic_issue"],
                }
            )
    return rows, issues


def _model_call_insert_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["run_id"],
        row["call_index"],
        row["kind"],
        row["title"],
        row["model"],
        row["created_at"],
        row["completed_at"],
        row["payload_json"],
        row["duration_ms"],
        row["prompt_tokens"],
        row["completion_tokens"],
        row["total_tokens"],
        row["message_count"],
        row["tool_count"],
        row["has_error"],
        row["diagnostic_issue"],
    )


def _event_insert_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["run_id"],
        row["event_index"],
        row["kind"],
        row["stage"],
        row["event_run_id"],
        row["created_at"],
        row["payload_json"],
        row["diagnostic_issue"],
    )


def _event_row(run_id: str, index: int, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    payload = payload if isinstance(payload, dict) else {}
    created_at = str(event.get("timestamp") or "")
    issues: list[str] = []
    if not created_at:
        issues.append("missing_event_timestamp")
    elif _parse_timestamp(created_at) is None:
        issues.append("invalid_event_timestamp")
    return {
        "run_id": run_id,
        "event_index": index,
        "kind": str(event.get("kind") or ""),
        "stage": str(payload.get("stage") or ""),
        "event_run_id": str(event.get("run_id") or ""),
        "created_at": created_at,
        "payload_json": _json_dumps(event),
        "diagnostic_issue": ";".join(issues),
    }


def _model_call_from_row(row: sqlite3.Row) -> dict[str, Any]:
    diagnostic_issue = row["diagnostic_issue"] or ""
    if not diagnostic_issue:
        issues: list[str] = []
        if not row["created_at"]:
            issues.append("missing_model_created_at")
        if not row["completed_at"]:
            issues.append("missing_model_completed_at")
        if (
            row["prompt_tokens"] is None
            and row["completion_tokens"] is None
            and row["total_tokens"] is None
        ):
            issues.append("missing_model_usage")
        if row["message_count"] is None:
            issues.append("missing_model_request")
        diagnostic_issue = ";".join(issues)
    return {
        "call_index": row["call_index"],
        "kind": row["kind"],
        "title": row["title"],
        "model": row["model"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "duration_ms": row["duration_ms"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "message_count": row["message_count"],
        "tool_count": row["tool_count"],
        "has_error": bool(row["has_error"]),
        "diagnostic_issue": diagnostic_issue,
        "payload": json.loads(row["payload_json"]),
    }


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    diagnostic_issue = row["diagnostic_issue"] or ""
    if not diagnostic_issue and not row["created_at"]:
        diagnostic_issue = "missing_event_timestamp"
    return {
        "event_index": row["event_index"],
        "kind": row["kind"],
        "stage": row["stage"],
        "event_run_id": row["event_run_id"],
        "created_at": row["created_at"],
        "diagnostic_issue": diagnostic_issue,
        "payload": json.loads(row["payload_json"]),
    }


def _timeline_items(
    model_calls: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for call in model_calls:
        if _has_canonical_time(call):
            items.append({"type": "model_call", "created_at": call["created_at"], "item": call})
    for event in events:
        if _has_canonical_time(event):
            items.append({"type": "event", "created_at": event["created_at"], "item": event})
    items.sort(key=lambda item: _parse_timestamp(str(item["created_at"])) or datetime.min)
    return items


def _missing_time_items(
    model_calls: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for call in model_calls:
        if not _has_canonical_time(call):
            missing.append({"type": "model_call", "item": call})
    for event in events:
        if not _has_canonical_time(event):
            missing.append({"type": "event", "item": event})
    return missing


def _order_items_by_canonical_time(
    items: list[dict[str, Any]],
    *,
    index_key: str,
) -> list[dict[str, Any]]:
    timestamped = [item for item in items if _has_canonical_time(item)]
    missing = [item for item in items if item not in timestamped]
    timestamped.sort(key=lambda item: _parse_timestamp(str(item["created_at"])) or datetime.min)
    missing.sort(key=lambda item: int(item.get(index_key) or 0))
    return timestamped + missing


def _summarize_run(
    run: dict[str, Any],
    model_calls: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    result_ids: list[str] = []
    execute_count = 0
    for event in events:
        if event.get("stage") == "execute":
            execute_count += 1
        payload = event.get("payload")
        if isinstance(payload, dict):
            event_payload = payload.get("payload")
            if isinstance(event_payload, dict):
                output = event_payload.get("output")
                if isinstance(output, dict) and output.get("result_id"):
                    result_ids.append(str(output["result_id"]))
    return {
        "duration_ms": run.get("duration_ms"),
        "model_call_count": run.get("model_call_count"),
        "total_prompt_tokens": run.get("total_prompt_tokens"),
        "total_completion_tokens": run.get("total_completion_tokens"),
        "total_tokens": run.get("total_tokens"),
        "event_count": len(events),
        "worker_run_count": sum(1 for event in events if event.get("kind") == "worker_run"),
        "execute_count": execute_count,
        "result_ids": result_ids,
        "diagnostic_issue_count": sum(
            1 for issue in run.get("diagnostic_issues", [])
            if isinstance(issue, dict) and issue.get("issue")
        ),
        "missing_time_count": len(_missing_time_items(model_calls, events)),
    }


def _read_time_issues(
    model_calls: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for call in model_calls:
        if call.get("diagnostic_issue"):
            continue
        if not call.get("created_at"):
            issues.append(
                {
                    "scope": "model_call",
                    "call_index": call.get("call_index"),
                    "issue": "missing_model_created_at",
                }
            )
    for event in events:
        if event.get("diagnostic_issue"):
            continue
        if not event.get("created_at"):
            issues.append(
                {
                    "scope": "event",
                    "event_index": event.get("event_index"),
                    "issue": "missing_event_timestamp",
                }
            )
    return issues


def _has_canonical_time(item: dict[str, Any]) -> bool:
    created_at = str(item.get("created_at") or "")
    issue = str(item.get("diagnostic_issue") or "")
    if not created_at:
        return False
    blocking_issues = {
        "missing_model_created_at",
        "invalid_model_created_at",
        "missing_event_timestamp",
        "invalid_event_timestamp",
    }
    if any(item in blocking_issues for item in issue.split(";")):
        return False
    return _parse_timestamp(created_at) is not None


def _load_issues(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return [{"scope": "run", "issue": "invalid_diagnostic_issues_json"}]
    if not isinstance(payload, list):
        return [{"scope": "run", "issue": "invalid_diagnostic_issues_json"}]
    return [item for item in payload if isinstance(item, dict)]


def _sum_known(values: Any) -> int | None:
    total = 0
    seen = False
    for value in values:
        if value is None:
            continue
        total += int(value)
        seen = True
    return total if seen else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _duration_ms(start: str, end: str) -> int | None:
    parsed_start = _parse_timestamp(start)
    parsed_end = _parse_timestamp(end)
    if parsed_start is None or parsed_end is None:
        return None
    return int((parsed_end - parsed_start).total_seconds() * 1000)


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ensure_columns(
    connection: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _json_dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False)
