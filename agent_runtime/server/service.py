"""Service layer that bridges HTTP requests to the AgentWeave runtime."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from agent_runtime.common import agentweave_data_dir, utc_now_iso
from agent_runtime.core.runtime import AgentRuntime
from agent_runtime.core.result_events import extract_result_metadata
from agent_runtime.core.runtime_utils import to_jsonable
from agent_runtime.storage.diagnostic_store import DiagnosticStore


@dataclass(frozen=True)
class AgentServiceConfig:
    root: Path
    base_url: str
    model_name: str
    api_key: str
    session_db_path: Path
    max_tokens: int = 4096
    embedding_base_url: str | None = None
    embedding_model_name: str | None = None
    memory_enabled: bool | None = None

    @classmethod
    def from_env(cls, root: Path | None = None) -> "AgentServiceConfig":
        import os

        resolved_root = (root or Path.cwd()).resolve()
        data_dir = agentweave_data_dir(resolved_root)
        return cls(
            root=resolved_root,
            base_url=os.getenv("CHAT_BASE_URL", "http://localhost:8000/v1"),
            model_name=os.getenv("CHAT_MODEL", "qwen3.6-27b"),
            api_key=os.getenv("CHAT_API_KEY") or os.getenv("OPENAI_API_KEY", "not-needed"),
            session_db_path=data_dir / "server_sessions.sqlite",
            max_tokens=int(os.getenv("CHAT_MAX_TOKENS", "8192")),
            embedding_base_url=os.getenv("EMBEDDING_BASE_URL") or None,
            embedding_model_name=os.getenv("EMBEDDING_MODEL") or None,
            memory_enabled=_optional_bool(os.getenv("MEMORY_ENABLED")),
        )


@dataclass
class RunRecord:
    run_id: str
    session_id: str
    question: str
    bot_id: str = "default"
    status: str = "queued"
    answer: str = ""
    result_ids: list[str] = field(default_factory=list)
    diagnostic_run_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: str = ""
    created_monotonic: float = field(default_factory=time.monotonic)
    completed_monotonic: float = 0.0
    error: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    model_logs: list[dict[str, Any]] = field(default_factory=list)
    model_deltas: list[dict[str, Any]] = field(default_factory=list)
    sse_events: list[dict[str, Any]] = field(default_factory=list)
    condition: threading.Condition = field(default_factory=threading.Condition)
    next_sequence: int = 1

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        with self.condition:
            event = dict(event)
            event["sequence"] = self.next_sequence
            self.next_sequence += 1
            self.sse_events.append(event)
            self.condition.notify_all()
            return event


class AgentService:
    def __init__(
        self,
        *,
        config: AgentServiceConfig,
        runtime: Any | None = None,
        diagnostic_store: DiagnosticStore | None = None,
        runtime_factory: Callable[[AgentServiceConfig], Any] | None = None,
    ) -> None:
        self.config = config
        self.runtime = runtime or (runtime_factory or _build_runtime)(config)
        self.diagnostic_store = diagnostic_store or DiagnosticStore(config.session_db_path)
        self._runs: dict[str, RunRecord] = {}
        self._sessions: dict[str, str] = {}
        self._lock = threading.RLock()
        self._max_runs = _env_int("AGENTWEAVE_RUN_CACHE_MAX", 1000)
        self._run_ttl_seconds = _env_float("AGENTWEAVE_RUN_CACHE_TTL_SECONDS", 6 * 60 * 60)

    def create_session(
        self,
        *,
        session_id: str = "",
        bot_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = session_id.strip() if session_id else f"web-{uuid.uuid4().hex[:12]}"
        resolved_bot_id = (bot_id or "default").strip() or "default"
        bot = self.get_bot(resolved_bot_id)
        result = self.runtime.run_session_start_hook(
            session_id=session_id,
            bot_id=resolved_bot_id,
        )
        with self._lock:
            self._sessions[session_id] = resolved_bot_id
        return {
            "session_id": session_id,
            "bot_id": resolved_bot_id,
            "bot": {
                "id": bot["id"],
                "name": bot["name"],
                "description": bot.get("description", ""),
            },
            "message": result.message,
            "capabilities": {
                "streaming": True,
                "results": True,
                "diagnostics": True,
                "resource_reload": True,
            },
        }

    def create_run(
        self,
        *,
        session_id: str,
        message: str,
        max_turns: int = 10,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("message is required.")
        bot_id = self._bot_id_for_session(session_id)
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        record = RunRecord(
            run_id=run_id,
            session_id=session_id,
            question=message,
            bot_id=bot_id,
        )
        with self._lock:
            self._prune_runs_locked()
            self._runs[run_id] = record
        worker = threading.Thread(
            target=self._execute_run,
            args=(record, max(1, int(max_turns))),
            name=f"agentweave-run-{run_id}",
            daemon=True,
        )
        worker.start()
        return {
            "run_id": run_id,
            "session_id": session_id,
            "bot_id": bot_id,
            "status": "queued",
            "events_url": f"/runs/{run_id}/events",
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        record = self._get_record(run_id)
        with record.condition:
            return {
                "run_id": record.run_id,
                "session_id": record.session_id,
                "bot_id": record.bot_id,
                "status": record.status,
                "question": record.question,
                "answer": record.answer,
                "result_ids": list(record.result_ids),
                "diagnostic_run_id": record.diagnostic_run_id,
                "created_at": record.created_at,
                "completed_at": record.completed_at,
                "error": record.error,
            }

    def iter_sse_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> Iterator[dict[str, Any]]:
        record = self._get_record(run_id)
        index = max(0, int(after_sequence))
        while True:
            with record.condition:
                while index >= len(record.sse_events) and record.status not in {
                    "completed",
                    "failed",
                }:
                    timed_out = not record.condition.wait(timeout=30)
                    if timed_out and index >= len(record.sse_events) and record.status not in {
                        "completed",
                        "failed",
                    }:
                        event = _keepalive_sse_event(record)
                        break
                else:
                    event = None
                if event is not None:
                    pass
                elif index < len(record.sse_events):
                    event = record.sse_events[index]
                    index += 1
                else:
                    if record.status in {"completed", "failed"}:
                        return
                    continue
            yield dict(event)

    def wait_for_run(
        self,
        run_id: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        record = self._get_record(run_id)
        with record.condition:
            if record.status not in {"completed", "failed"}:
                record.condition.wait(timeout=timeout)
                if record.status in {"completed", "failed"}:
                    return self.get_run(run_id)
        return self.get_run(run_id)

    def get_result_page(
        self,
        result_id: str,
        *,
        page: int = 1,
        page_size: int = 100,
        run_id: str = "",
        session_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = min(1000, max(1, int(page_size)))
        offset = (page - 1) * page_size
        artifact = self.runtime.services.artifact_store.get_artifact_page(
            result_id,
            offset=offset,
            limit=page_size,
            run_id=run_id,
            session_id=session_id,
            bot_id=bot_id,
        )
        metrics = artifact.get("metrics") if isinstance(artifact.get("metrics"), dict) else {}
        total_rows = int(metrics.get("stored_count") or metrics.get("row_count") or 0)
        rows = artifact.get("rows") if isinstance(artifact.get("rows"), list) else []
        return {
            **artifact,
            "result_id": result_id,
            "page": {
                "number": page,
                "size": page_size,
                "offset": offset,
                "total_rows": total_rows,
                "has_more": offset + len(rows) < total_rows,
            },
            "download_url": f"/results/{result_id}.csv",
        }

    def export_result_csv(
        self,
        result_id: str,
        *,
        run_id: str = "",
        session_id: str = "",
        bot_id: str = "",
    ) -> bytes:
        return self.runtime.services.artifact_store.export_csv(
            result_id,
            run_id=run_id,
            session_id=session_id,
            bot_id=bot_id,
        )

    def get_diagnostic(self, run_id: str) -> dict[str, Any]:
        return self.diagnostic_store.get_run(run_id)

    def list_capabilities(self) -> dict[str, Any]:
        return self.runtime.list_capabilities()

    def list_bots(self) -> list[dict[str, Any]]:
        return self.runtime.list_bots()

    def get_bot(self, bot_id: str) -> dict[str, Any]:
        return self.runtime.get_bot(bot_id or "default")

    def reload_resources(self, *, reason: str = "manual") -> dict[str, Any]:
        summary = self.runtime.reload_resources()
        message = " ".join(f"{key}={value}" for key, value in summary.items())
        return {
            "reloaded": True,
            "message": message,
            "event": {
                "kind": "resources_reloaded",
                "payload": {
                    "stage": "resources_reloaded",
                    "reason": reason,
                    "summary": summary,
                },
            },
        }

    def _execute_run(self, record: RunRecord, max_turns: int) -> None:
        started_at = utc_now_iso()
        with record.condition:
            record.status = "running"
            record.condition.notify_all()

        def on_event(event: dict[str, Any]) -> None:
            normalized = to_jsonable(event)
            with record.condition:
                record.events.append(normalized)
            record.publish(_runtime_sse_event(record, normalized))
            result_event = _result_created_sse_event(record, normalized)
            if result_event is not None:
                record.publish(result_event)

        def on_model_delta(payload: dict[str, Any]) -> None:
            if not isinstance(payload, dict) or not payload.get("delta"):
                return
            normalized = to_jsonable(payload)
            with record.condition:
                record.model_deltas.append(normalized)
            record.publish(
                {
                    "type": "model_delta",
                    "run_id": record.run_id,
                    "timestamp": utc_now_iso(),
                    "payload": normalized,
                }
            )

        try:
            response = asyncio.run(
                self.runtime.ask(
                    record.question,
                    record.session_id,
                    event_callback=on_event,
                    model_delta_callback=on_model_delta,
                    max_turns=max_turns,
                    bot_id=record.bot_id,
                )
            )
            completed_at = utc_now_iso()
            answer = str(response.get("final_output") or "")
            events = [to_jsonable(event) for event in response.get("events", [])]
            model_logs = [to_jsonable(log) for log in response.get("model_logs", [])]
            results = extract_result_metadata(events)
            result_ids = [
                str(item["result_id"])
                for item in results
                if item.get("result_id")
            ]
            with record.condition:
                record.answer = answer
                record.events = events
                record.model_logs = model_logs
                record.result_ids = result_ids
                record.diagnostic_run_id = record.run_id
                record.completed_at = completed_at
                record.completed_monotonic = time.monotonic()
                record.condition.notify_all()
            self.diagnostic_store.record_run(
                run_id=record.run_id,
                session_id=record.session_id,
                question=record.question,
                answer=answer,
                model_logs=model_logs,
                events=events,
                started_at=started_at,
                completed_at=completed_at,
                status="completed",
            )
            record.publish(
                {
                    "type": "run_complete",
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "bot_id": record.bot_id,
                    "timestamp": completed_at,
                    "answer": answer,
                    "result_ids": result_ids,
                    "results": results,
                    "diagnostic_run_id": record.run_id,
                }
            )
            with record.condition:
                record.status = "completed"
                record.condition.notify_all()
            self._prune_runs()
        except Exception as exc:
            completed_at = utc_now_iso()
            error = f"{type(exc).__name__}: {exc}"
            with record.condition:
                record.error = error
                record.diagnostic_run_id = record.run_id
                record.completed_at = completed_at
                record.completed_monotonic = time.monotonic()
                record.condition.notify_all()
                events_snapshot = list(record.events)
            self.diagnostic_store.record_run(
                run_id=record.run_id,
                session_id=record.session_id,
                question=record.question,
                answer="",
                events=events_snapshot,
                started_at=started_at,
                completed_at=completed_at,
                status="failed",
                error=error,
            )
            record.publish(
                {
                    "type": "run_error",
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "bot_id": record.bot_id,
                    "timestamp": completed_at,
                    "error": type(exc).__name__,
                    "message": error,
                    "diagnostic_run_id": record.run_id,
                }
            )
            with record.condition:
                record.status = "failed"
                record.condition.notify_all()
            self._prune_runs()

    def _get_record(self, run_id: str) -> RunRecord:
        with self._lock:
            self._prune_runs_locked()
            record = self._runs.get(run_id)
        if record is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        return record

    def _bot_id_for_session(self, session_id: str) -> str:
        with self._lock:
            bot_id = self._sessions.get(session_id)
            if bot_id is None:
                bot_id = "default"
                self._sessions[session_id] = bot_id
        return bot_id

    def _prune_runs(self) -> None:
        with self._lock:
            self._prune_runs_locked()

    def _prune_runs_locked(self) -> None:
        now = time.monotonic()
        expired = [
            run_id
            for run_id, record in self._runs.items()
            if _is_evictable(record, now, self._run_ttl_seconds)
        ]
        for run_id in expired:
            self._runs.pop(run_id, None)

        overflow = max(0, len(self._runs) - max(1, self._max_runs))
        if overflow == 0:
            return
        candidates = sorted(
            (
                record
                for record in self._runs.values()
                if record.status in {"completed", "failed"}
            ),
            key=lambda item: item.completed_monotonic or item.created_monotonic,
        )
        for record in candidates[:overflow]:
            self._runs.pop(record.run_id, None)


def _build_runtime(config: AgentServiceConfig) -> AgentRuntime:
    return AgentRuntime(
        base_url=config.base_url,
        model_name=config.model_name,
        api_key=config.api_key,
        session_db_path=config.session_db_path,
        max_tokens=config.max_tokens,
        embedding_base_url=config.embedding_base_url,
        embedding_model_name=config.embedding_model_name,
        memory_enabled=config.memory_enabled,
    )


def _runtime_sse_event(record: RunRecord, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "runtime_event",
        "run_id": record.run_id,
        "timestamp": str(event.get("timestamp") or utc_now_iso()),
        "payload": {
            "kind": str(event.get("kind") or ""),
            "payload": event.get("payload") if isinstance(event.get("payload"), dict) else {},
            "error": str(event.get("error") or ""),
        },
    }


def _keepalive_sse_event(record: RunRecord) -> dict[str, Any]:
    return {
        "type": "keepalive",
        "run_id": record.run_id,
        "sequence": 0,
        "timestamp": utc_now_iso(),
    }


def _result_created_sse_event(
    record: RunRecord,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    if event.get("kind") != "result_created":
        return None
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    if result is None:
        result = payload.get("ui_content") if isinstance(payload.get("ui_content"), dict) else payload
    result_id = result.get("result_id")
    if not result_id:
        return None
    return {
        "type": "result_created",
        "run_id": record.run_id,
        "timestamp": str(event.get("timestamp") or utc_now_iso()),
        "result_id": str(result_id),
        "result": result,
    }


def _optional_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_evictable(record: RunRecord, now: float, ttl_seconds: float) -> bool:
    if record.status not in {"completed", "failed"}:
        return False
    if ttl_seconds <= 0:
        return False
    completed_at = (
        record.completed_monotonic
        if record.completed_monotonic != 0
        else record.created_monotonic
    )
    return now - completed_at > ttl_seconds


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
