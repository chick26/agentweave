from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime.common import utc_now_iso


@dataclass(frozen=True)
class SessionTemplate:
    id: str
    name: str
    messages: list[dict[str, Any]]
    created_at: str
    updated_at: str


class SessionTemplateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def save_template(self, *, name: str, messages: list[dict[str, Any]]) -> str:
        template_name = name.strip()
        if not template_name:
            raise ValueError("Template name is required.")
        cleaned_messages = _clean_messages(messages)
        if not cleaned_messages:
            raise ValueError("Template must contain at least one message.")

        template_id = _template_id(template_name)
        now = utc_now_iso()
        payload = json.dumps(cleaned_messages, ensure_ascii=False, default=str)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT created_at FROM session_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            self._connection.execute(
                """
                INSERT OR REPLACE INTO session_templates (
                    id, name, messages_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (template_id, template_name, payload, created_at, now),
            )
        return template_id

    def template_exists(self, name: str) -> bool:
        template_name = name.strip()
        if not template_name:
            return False
        template_id = _template_id(template_name)
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM session_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
        return row is not None

    def list_templates(self) -> list[SessionTemplate]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, name, messages_json, created_at, updated_at
                FROM session_templates
                ORDER BY updated_at DESC, name ASC
                """
            ).fetchall()
        return [_row_to_template(row) for row in rows]

    def get_template(self, template_id: str) -> SessionTemplate:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, name, messages_json, created_at, updated_at
                FROM session_templates
                WHERE id = ?
                """,
                (template_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown session template: {template_id}")
        return _row_to_template(row)

    def delete_template(self, template_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM session_templates WHERE id = ?",
                (template_id,),
            )

    def _init_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS session_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )


def _template_id(name: str) -> str:
    return "tpl_" + "_".join(name.strip().lower().split())


def _clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role and content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def _row_to_template(row: sqlite3.Row) -> SessionTemplate:
    return SessionTemplate(
        id=str(row["id"]),
        name=str(row["name"]),
        messages=json.loads(str(row["messages_json"] or "[]")),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
