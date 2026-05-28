from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from agent_runtime.embeddings import EmbeddingClient
from agent_runtime.memory_store import MemoryRecord, MemoryStore
from agent_runtime.skill_registry import ManifestBase

TodoStatus = Literal["pending", "in_progress", "completed"]


@dataclass(frozen=True)
class TodoItem:
    content: str
    status: TodoStatus


@dataclass(frozen=True)
class MemorySearchResult:
    records: list[MemoryRecord]
    strategy: str
    namespaces: list[str]
    fallback: bool = False
    error: str = ""


class MemoryManager:
    """Coordinates durable memory, session summaries, and session-local todos."""

    def __init__(
        self,
        memory_store: MemoryStore,
        embedding_client: EmbeddingClient | Any | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self.store = memory_store
        self.embedding_client = embedding_client
        self.enabled = enabled
        self._todos_by_session: dict[str, list[TodoItem]] = {}

    def build_orchestrator_context(
        self,
        session_id: str,
        current_query: str = "",
        retrieval_events: list[dict[str, Any]] | None = None,
    ) -> str:
        parts: list[str] = []
        if self.enabled:
            for namespace in ["project", "user"]:
                result = self.retrieve(current_query, [namespace], limit=5)
                if result.records:
                    parts.append(_format_records(namespace, result.records))
                _append_retrieval_event(retrieval_events, result, source="orchestrator_context")
            session_records = self.store.load_namespace(f"session:{session_id}")
            if session_records:
                parts.append(f"[session_summary]\n{session_records[0].content}")
        todo_context = self.build_todo_context(session_id)
        if todo_context:
            parts.append(todo_context)
        return "\n\n".join(parts)

    def build_skill_context(
        self,
        manifest: ManifestBase,
        query: str = "",
        retrieval_events: list[dict[str, Any]] | None = None,
    ) -> str:
        if not self.enabled:
            return ""
        sections: list[str] = []
        for namespace in manifest.memory.namespaces:
            result = self.retrieve(query, [namespace], limit=5)
            if result.records:
                sections.append(_format_records(namespace, result.records))
            _append_retrieval_event(retrieval_events, result, source="worker_context")
        return "\n\n".join(sections)

    def write_session_summary(self, session_id: str, summary: str) -> None:
        if not self.enabled:
            return
        self.store.write(
            namespace=f"session:{session_id}",
            key="conversation_summary",
            content=summary,
            source="compressor",
            expires_in_seconds=86400,
        )

    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        return self.retrieve(query, namespaces, limit).records

    def retrieve(
        self,
        query: str,
        namespaces: list[str] | None = None,
        limit: int = 10,
    ) -> MemorySearchResult:
        namespace_list = list(namespaces or [])
        if not self.enabled:
            return MemorySearchResult(
                records=[],
                strategy="disabled",
                namespaces=namespace_list,
            )
        if not query.strip():
            return MemorySearchResult(
                records=_load_recent(self.store, namespace_list, limit),
                strategy="recent_fallback",
                namespaces=namespace_list,
            )

        fallback = False
        error = ""
        profile = getattr(self.embedding_client, "profile", None)
        if self.embedding_client is not None and getattr(profile, "enabled", True):
            try:
                self._backfill_embeddings(namespace_list)
                query_vectors = self.embedding_client.embed_texts([query])
                records = self.store.search_vectors(
                    query_vectors[0] if query_vectors else [],
                    embedding_model=str(profile.model_name),
                    namespaces=namespace_list,
                    limit=limit,
                )
                if records:
                    return MemorySearchResult(
                        records=records,
                        strategy="vector",
                        namespaces=namespace_list,
                    )
                fallback = True
            except Exception as exc:
                fallback = True
                error = f"{type(exc).__name__}: {exc}"

        return MemorySearchResult(
            records=self.store.search(query, namespace_list, limit),
            strategy="lexical_fallback",
            namespaces=namespace_list,
            fallback=fallback,
            error=error,
        )

    def write(
        self,
        namespace: str,
        key: str,
        content: str,
        tags: list[str] | None = None,
        source: str = "agent",
        expires_in_seconds: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        memory_id = self.store.write(
            namespace=namespace,
            key=key,
            content=content,
            tags=tags,
            source=source,
            expires_in_seconds=expires_in_seconds,
        )
        self._upsert_embedding(
            memory_id=memory_id,
            namespace=namespace,
            content=content,
        )

    def load_namespace(self, namespace: str) -> list[MemoryRecord]:
        return self.store.load_namespace(namespace)

    def clear(self) -> None:
        self.store.clear()

    def update_todo(self, session_id: str, items: list[TodoItem]) -> list[TodoItem]:
        normalized = [_normalize_todo(item) for item in items if item.content.strip()]
        in_progress_count = sum(1 for item in normalized if item.status == "in_progress")
        if in_progress_count > 1:
            raise ValueError("Only one todo item can be in_progress.")
        self._todos_by_session[session_id] = normalized
        return list(normalized)

    def get_todos(self, session_id: str) -> list[TodoItem]:
        return list(self._todos_by_session.get(session_id, []))

    def build_todo_context(self, session_id: str) -> str:
        todos = self.get_todos(session_id)
        if not todos:
            return ""
        lines = ["[todo_working_memory]"]
        for item in todos:
            lines.append(f"- [{item.status}] {item.content}")
        return "\n".join(lines)

    def _backfill_embeddings(self, namespaces: list[str]) -> None:
        profile = getattr(self.embedding_client, "profile", None)
        if self.embedding_client is None or profile is None:
            return
        records = _load_all(self.store, namespaces)
        vectors = {
            vector.memory_id: vector
            for vector in self.store.load_vectors(
                embedding_model=str(profile.model_name),
                namespaces=namespaces,
            )
        }
        stale = [
            record
            for record in records
            if not record.namespace.startswith("session:")
            and (
                record.id not in vectors
                or vectors[record.id].content_hash != _content_hash(record.content)
            )
        ]
        if not stale:
            return
        embeddings = self.embedding_client.embed_texts([record.content for record in stale])
        for record, vector in zip(stale, embeddings):
            self.store.upsert_vector(
                memory_id=record.id,
                namespace=record.namespace,
                embedding_model=str(profile.model_name),
                content_hash=_content_hash(record.content),
                vector=vector,
            )

    def _upsert_embedding(self, *, memory_id: str, namespace: str, content: str) -> None:
        profile = getattr(self.embedding_client, "profile", None)
        if (
            self.embedding_client is None
            or profile is None
            or not getattr(profile, "enabled", True)
            or namespace.startswith("session:")
        ):
            return
        try:
            embeddings = self.embedding_client.embed_texts([content])
            if embeddings:
                self.store.upsert_vector(
                    memory_id=memory_id,
                    namespace=namespace,
                    embedding_model=str(profile.model_name),
                    content_hash=_content_hash(content),
                    vector=embeddings[0],
                )
        except Exception:
            return


def _normalize_todo(item: TodoItem) -> TodoItem:
    content = item.content.strip()
    if item.status not in {"pending", "in_progress", "completed"}:
        raise ValueError(f"Unsupported todo status: {item.status}")
    return TodoItem(content=content, status=item.status)


def _format_records(namespace: str, records: list[MemoryRecord]) -> str:
    lines = [f"[{namespace}]"]
    for record in records:
        lines.append(f"- {record.key}: {record.content}")
    return "\n".join(lines)


def _append_retrieval_event(
    events: list[dict[str, Any]] | None,
    result: MemorySearchResult,
    *,
    source: str,
) -> None:
    if events is None:
        return
    events.append(
        {
            "stage": "memory_retrieval",
            "source": source,
            "strategy": result.strategy,
            "namespaces": result.namespaces,
            "count": len(result.records),
            "records": [_memory_record_event(record) for record in result.records],
            "embedding_fallback": result.fallback,
            "error": result.error,
        }
    )


def _memory_record_event(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "namespace": record.namespace,
        "key": record.key,
        "content": _preview_text(record.content, limit=240),
        "tags": record.tags,
        "source": record.source,
        "updated_at": record.updated_at,
    }


def _preview_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}...[truncated {len(text)} chars]"


def _load_recent(store: MemoryStore, namespaces: list[str], limit: int) -> list[MemoryRecord]:
    records = _load_all(store, namespaces)
    records.sort(key=lambda record: record.updated_at, reverse=True)
    return records[:limit]


def _load_all(store: MemoryStore, namespaces: list[str]) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for namespace in namespaces:
        records.extend(store.load_namespace(namespace))
    return records


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
