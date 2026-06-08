"""Session-local todo state separated from durable memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TodoStatus = Literal["pending", "in_progress", "completed"]


@dataclass(frozen=True)
class TodoItem:
    content: str
    status: TodoStatus


class TodoState:
    """In-memory working state for the current runtime process."""

    def __init__(self) -> None:
        self._todos_by_session: dict[str, list[TodoItem]] = {}

    def update(self, session_id: str, items: list[TodoItem]) -> list[TodoItem]:
        normalized = [_normalize_todo(item) for item in items if item.content.strip()]
        in_progress_count = sum(1 for item in normalized if item.status == "in_progress")
        if in_progress_count > 1:
            raise ValueError("Only one todo item can be in_progress.")
        self._todos_by_session[session_id] = normalized
        return list(normalized)

    def get(self, session_id: str) -> list[TodoItem]:
        return list(self._todos_by_session.get(session_id, []))

    def format_context(self, session_id: str) -> str:
        todos = self.get(session_id)
        if not todos:
            return ""
        lines = ["[todo_working_memory]"]
        for item in todos:
            lines.append(f"- [{item.status}] {item.content}")
        return "\n".join(lines)


def _normalize_todo(item: TodoItem) -> TodoItem:
    content = item.content.strip()
    if item.status not in {"pending", "in_progress", "completed"}:
        raise ValueError(f"Unsupported todo status: {item.status}")
    return TodoItem(content=content, status=item.status)
