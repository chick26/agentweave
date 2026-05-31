from __future__ import annotations

import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_runtime.common import utc_now_iso


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    namespace: str
    key: str
    content: str
    tags: list[str]
    source: str
    created_at: str
    updated_at: str
    expires_at: str | None = None


@dataclass(frozen=True)
class MemoryVector:
    memory_id: str
    namespace: str
    embedding_model: str
    content_hash: str
    vector: list[float]
    updated_at: str


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._fts_enabled = self._init_schema()

    @property
    def fts_enabled(self) -> bool:
        return self._fts_enabled

    def write(
        self,
        namespace: str,
        key: str,
        content: str,
        tags: list[str] | None = None,
        source: str = "manual",
        expires_in_seconds: int | None = None,
    ) -> str:
        now = utc_now_iso("seconds")
        expires_at = None
        if expires_in_seconds is not None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
        memory_id = self._existing_id(namespace, key) or uuid.uuid4().hex
        created_at = self._existing_created_at(namespace, key) or now
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO memories (
                    id, namespace, key, content, tags, source,
                    created_at, updated_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    content = excluded.content,
                    tags = excluded.tags,
                    source = excluded.source,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    memory_id,
                    namespace,
                    key,
                    content,
                    json.dumps(tags or [], ensure_ascii=False),
                    source,
                    created_at,
                    now,
                    expires_at,
                ),
            )
            self._sync_fts(memory_id, content)
        return memory_id

    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        self._expire_old_records()
        namespaces = namespaces or []
        if self._fts_enabled and query.strip():
            try:
                records = self._search_fts(query, namespaces, limit)
                if records:
                    return records
            except sqlite3.Error:
                pass
        return self._search_like(query, namespaces, limit)

    def upsert_vector(
        self,
        *,
        memory_id: str,
        namespace: str,
        embedding_model: str,
        content_hash: str,
        vector: list[float],
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO memory_vectors (
                    memory_id, namespace, embedding_model, content_hash,
                    vector_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id, embedding_model) DO UPDATE SET
                    namespace = excluded.namespace,
                    content_hash = excluded.content_hash,
                    vector_json = excluded.vector_json,
                    updated_at = excluded.updated_at
                """,
                (
                    memory_id,
                    namespace,
                    embedding_model,
                    content_hash,
                    json.dumps(vector),
                    utc_now_iso("seconds"),
                ),
            )

    def load_vectors(
        self,
        *,
        embedding_model: str,
        namespaces: list[str] | None = None,
    ) -> list[MemoryVector]:
        self._expire_old_records()
        namespace_sql, params = _namespace_filter_sql(namespaces or [], alias="v")
        cursor = self._connection.execute(
            f"""
            SELECT v.*
            FROM memory_vectors v
            JOIN memories m ON m.id = v.memory_id
            WHERE v.embedding_model = ?
            {namespace_sql}
            """,
            [embedding_model, *params],
        )
        return [_row_to_vector(row) for row in cursor.fetchall()]

    def search_vectors(
        self,
        query_vector: list[float],
        *,
        embedding_model: str,
        namespaces: list[str] | None = None,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        if not query_vector:
            return []
        namespace_sql, params = _namespace_filter_sql(namespaces or [], alias="v")
        cursor = self._connection.execute(
            f"""
            SELECT m.*, v.vector_json
            FROM memory_vectors v
            JOIN memories m ON m.id = v.memory_id
            WHERE v.embedding_model = ?
            {namespace_sql}
            """,
            [embedding_model, *params],
        )
        scored: list[tuple[float, MemoryRecord]] = []
        for row in cursor.fetchall():
            try:
                vector = [float(value) for value in json.loads(row["vector_json"])]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            scored.append((_cosine_similarity(query_vector, vector), _row_to_record(row)))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def forget(self, namespace: str, key: str) -> None:
        memory_id = self._existing_id(namespace, key)
        with self._connection:
            self._connection.execute(
                "DELETE FROM memories WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            if self._fts_enabled and memory_id:
                self._connection.execute(
                    "DELETE FROM memories_fts WHERE id = ?",
                    (memory_id,),
                )
            if memory_id:
                self._connection.execute(
                    "DELETE FROM memory_vectors WHERE memory_id = ?",
                    (memory_id,),
                )

    def clear(self) -> None:
        """Delete every memory record and its retrieval indexes."""
        with self._connection:
            self._connection.execute("DELETE FROM memories")
            self._connection.execute("DELETE FROM memory_vectors")
            if self._fts_enabled:
                self._connection.execute("DELETE FROM memories_fts")

    def load_namespace(self, namespace: str) -> list[MemoryRecord]:
        self._expire_old_records()
        cursor = self._connection.execute(
            """
            SELECT * FROM memories
            WHERE namespace = ?
            ORDER BY updated_at DESC
            """,
            (namespace,),
        )
        return [_row_to_record(row) for row in cursor.fetchall()]

    def expire_session(self, session_id: str) -> None:
        namespace = f"session:{session_id}"
        with self._connection:
            self._connection.execute(
                "DELETE FROM memories WHERE namespace = ?",
                (namespace,),
            )
            if self._fts_enabled:
                self._connection.execute(
                    """
                    DELETE FROM memories_fts
                    WHERE id NOT IN (SELECT id FROM memories)
                    """
                )
            self._connection.execute(
                "DELETE FROM memory_vectors WHERE memory_id NOT IN (SELECT id FROM memories)"
            )

    def _init_schema(self) -> bool:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    source TEXT DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    UNIQUE(namespace, key)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace)"
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    memory_id TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(memory_id, embedding_model)
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_vectors_namespace
                ON memory_vectors(namespace, embedding_model)
                """
            )
        try:
            with self._connection:
                self._connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                    USING fts5(id UNINDEXED, content)
                    """
                )
            return True
        except sqlite3.Error:
            return False

    def _sync_fts(self, memory_id: str, content: str) -> None:
        if not self._fts_enabled:
            return
        self._connection.execute(
            "DELETE FROM memories_fts WHERE id = ?",
            (memory_id,),
        )
        self._connection.execute(
            "INSERT INTO memories_fts(id, content) VALUES (?, ?)",
            (memory_id, content),
        )

    def _search_fts(
        self,
        query: str,
        namespaces: list[str],
        limit: int,
    ) -> list[MemoryRecord]:
        namespace_sql, params = _namespace_filter_sql(namespaces, alias="m")
        sql = f"""
            SELECT m.*
            FROM memories_fts f
            JOIN memories m ON m.id = f.id
            WHERE memories_fts MATCH ?
            {namespace_sql}
            ORDER BY rank
            LIMIT ?
        """
        cursor = self._connection.execute(sql, [query, *params, limit])
        return [_row_to_record(row) for row in cursor.fetchall()]

    def _search_like(
        self,
        query: str,
        namespaces: list[str],
        limit: int,
    ) -> list[MemoryRecord]:
        namespace_sql, params = _namespace_filter_sql(namespaces)
        sql = f"""
            SELECT *
            FROM memories
            WHERE content LIKE ?
            {namespace_sql}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        cursor = self._connection.execute(sql, [f"%{query}%", *params, limit])
        return [_row_to_record(row) for row in cursor.fetchall()]

    def _existing_id(self, namespace: str, key: str) -> str | None:
        cursor = self._connection.execute(
            "SELECT id FROM memories WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        row = cursor.fetchone()
        return str(row["id"]) if row else None

    def _existing_created_at(self, namespace: str, key: str) -> str | None:
        cursor = self._connection.execute(
            "SELECT created_at FROM memories WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        row = cursor.fetchone()
        return str(row["created_at"]) if row else None

    def _expire_old_records(self) -> None:
        now = utc_now_iso("seconds")
        with self._connection:
            self._connection.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            if self._fts_enabled:
                self._connection.execute(
                    "DELETE FROM memories_fts WHERE id NOT IN (SELECT id FROM memories)"
                )
            self._connection.execute(
                "DELETE FROM memory_vectors WHERE memory_id NOT IN (SELECT id FROM memories)"
            )


def _namespace_filter_sql(
    namespaces: list[str],
    alias: str = "",
) -> tuple[str, list[str]]:
    if not namespaces:
        return "", []
    column = f"{alias}.namespace" if alias else "namespace"
    placeholders = ", ".join("?" for _ in namespaces)
    return f"AND {column} IN ({placeholders})", namespaces


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    try:
        tags = json.loads(row["tags"] or "[]")
    except json.JSONDecodeError:
        tags = []
    if not isinstance(tags, list):
        tags = []
    return MemoryRecord(
        id=str(row["id"]),
        namespace=str(row["namespace"]),
        key=str(row["key"]),
        content=str(row["content"]),
        tags=[str(tag) for tag in tags],
        source=str(row["source"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        expires_at=str(row["expires_at"]) if row["expires_at"] else None,
    )


def _row_to_vector(row: sqlite3.Row) -> MemoryVector:
    try:
        vector = [float(value) for value in json.loads(row["vector_json"])]
    except (TypeError, ValueError, json.JSONDecodeError):
        vector = []
    return MemoryVector(
        memory_id=str(row["memory_id"]),
        namespace=str(row["namespace"]),
        embedding_model=str(row["embedding_model"]),
        content_hash=str(row["content_hash"]),
        vector=vector,
        updated_at=str(row["updated_at"]),
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)
