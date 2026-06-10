"""SQLite result store for large tool outputs and CSV export."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_runtime.common import columns_from_rows, utc_now_iso
from agent_runtime.core.result_formatters import ResultArtifactSpec


class ResultStore:
    """SQLite-backed store for row-shaped result artifacts.

    Worker tools return compact pointers and previews to the model. Full rows
    live here for UI pagination, SSE result chips, diagnostics, and CSV export.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def create_result(
        self,
        *,
        run_id: str,
        session_id: str = "",
        bot_id: str = "",
        domain: str,
        sql: str,
        rows: list[dict[str, Any]],
    ) -> str:
        """Create a SQL result artifact using the standard artifact store."""
        return self.create_artifact(
            run_id=run_id,
            artifact=ResultArtifactSpec(
                artifact_type="sql_result",
                title="SQL Result",
                source="execute_sql",
                rows=rows,
                columns=columns_from_rows(rows),
                preview_rows=rows[:5],
                metadata={
                    "domain": domain,
                    "sql": sql,
                },
                row_count=len(rows),
                row_count_is_exact=True,
            ),
            session_id=session_id,
            bot_id=bot_id,
        )

    def create_artifact(
        self,
        *,
        run_id: str,
        artifact: ResultArtifactSpec,
        session_id: str = "",
        bot_id: str = "",
    ) -> str:
        self._opportunistic_cleanup()
        normalized = artifact.normalized()
        result_id = f"res_{uuid.uuid4().hex[:16]}"
        created_at = utc_now_iso()
        metadata = dict(normalized.metadata)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO result_artifacts (
                    id, run_id, artifact_type, source, title,
                    columns_json, metadata_json, row_count, row_count_is_exact,
                    session_id, bot_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    run_id,
                    normalized.artifact_type,
                    normalized.source,
                    normalized.title,
                    json.dumps(normalized.columns, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False, default=str),
                    normalized.row_count,
                    1 if normalized.row_count_is_exact else 0,
                    session_id,
                    bot_id,
                    created_at,
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO result_artifact_rows (result_id, row_index, row_json)
                VALUES (?, ?, ?)
                """,
                (
                    (
                        result_id,
                        index,
                        json.dumps(row, ensure_ascii=False, default=str),
                    )
                    for index, row in enumerate(normalized.rows)
                ),
            )
        return result_id

    def cleanup(
        self,
        *,
        max_age_hours: float | None = None,
        max_results: int | None = None,
    ) -> int:
        deleted = 0
        with self._lock, self._connection:
            if max_age_hours is not None:
                cutoff = _utc_now_iso_from_age(max_age_hours)
                rows = self._connection.execute(
                    "SELECT id FROM result_artifacts WHERE created_at < ?",
                    (cutoff,),
                ).fetchall()
                deleted += self._delete_results([str(row["id"]) for row in rows])
            if max_results is not None and max_results >= 0:
                rows = self._connection.execute(
                    """
                    SELECT id FROM result_artifacts
                    ORDER BY created_at DESC, id DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (int(max_results),),
                ).fetchall()
                deleted += self._delete_results([str(row["id"]) for row in rows])
        return deleted

    def get_metadata(
        self,
        result_id: str,
        *,
        run_id: str = "",
        session_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT
                    id, run_id, artifact_type, source, title,
                    columns_json, metadata_json, row_count, row_count_is_exact,
                    session_id, bot_id, created_at
                FROM result_artifacts
                WHERE id = ?
                """,
                (result_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown result_id: {result_id}")
        _assert_scope(row, run_id=run_id, session_id=session_id, bot_id=bot_id)
        preview_rows = self._get_page_rows(result_id, offset=0, limit=5)
        metadata = json.loads(row["metadata_json"] or "{}")
        artifact = ResultArtifactSpec(
            artifact_type=row["artifact_type"],
            title=row["title"],
            source=row["source"],
            rows=[],
            columns=json.loads(row["columns_json"] or "[]"),
            preview_rows=preview_rows,
            metadata=metadata,
            row_count=row["row_count"],
            row_count_is_exact=bool(row["row_count_is_exact"]),
        )
        summary = artifact.to_summary(
            result_id=result_id,
            created_at=row["created_at"],
            stored_count=row["row_count"],
        )
        summary.update(
            {
                "run_id": row["run_id"],
                "session_id": row["session_id"],
                "bot_id": row["bot_id"],
            }
        )
        return summary

    def get_artifact(
        self,
        result_id: str,
        *,
        run_id: str = "",
        session_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        return self.get_metadata(
            result_id,
            run_id=run_id,
            session_id=session_id,
            bot_id=bot_id,
        )

    def get_artifact_page(
        self,
        result_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        run_id: str = "",
        session_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        metadata = self.get_metadata(
            result_id,
            run_id=run_id,
            session_id=session_id,
            bot_id=bot_id,
        )
        rows = self._get_page_rows(result_id, offset=offset, limit=limit)
        return {
            **metadata,
            "offset": max(0, int(offset)),
            "limit": max(1, int(limit)),
            "rows": rows,
        }

    def get_page(
        self,
        result_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        run_id: str = "",
        session_id: str = "",
        bot_id: str = "",
    ) -> list[dict[str, Any]]:
        self.get_metadata(
            result_id,
            run_id=run_id,
            session_id=session_id,
            bot_id=bot_id,
        )
        return self._get_page_rows(result_id, offset=offset, limit=limit)

    def _get_page_rows(
        self,
        result_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        offset = max(0, int(offset))
        limit = max(1, int(limit))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT row_json
                FROM result_artifact_rows
                WHERE result_id = ?
                ORDER BY row_index
                LIMIT ? OFFSET ?
                """,
                (result_id, limit, offset),
            ).fetchall()
        return [json.loads(row["row_json"]) for row in rows]

    def export_csv(
        self,
        result_id: str,
        *,
        run_id: str = "",
        session_id: str = "",
        bot_id: str = "",
    ) -> bytes:
        metadata = self.get_metadata(
            result_id,
            run_id=run_id,
            session_id=session_id,
            bot_id=bot_id,
        )
        preview = metadata.get("preview") if isinstance(metadata.get("preview"), dict) else {}
        columns = list(preview.get("columns") or [])
        metrics = metadata.get("metrics") if isinstance(metadata.get("metrics"), dict) else {}
        rows = self._get_page_rows(
            result_id,
            offset=0,
            limit=max(1, int(metrics.get("stored_count") or 0)),
        )
        if not columns:
            columns = columns_from_rows(rows)

        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue().encode("utf-8-sig")

    def _init_schema(self) -> None:
        with self._lock, self._connection:
            self._drop_legacy_query_result_tables()
            self._drop_incompatible_artifact_schema()
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS result_artifacts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    columns_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    row_count_is_exact INTEGER NOT NULL DEFAULT 1,
                    session_id TEXT NOT NULL DEFAULT '',
                    bot_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS result_artifact_rows (
                    result_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    row_json TEXT NOT NULL,
                    PRIMARY KEY (result_id, row_index),
                    FOREIGN KEY (result_id) REFERENCES result_artifacts(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_result_artifact_rows_result_id
                ON result_artifact_rows(result_id, row_index)
                """
            )

    def _drop_legacy_query_result_tables(self) -> None:
        self._connection.execute("DROP TABLE IF EXISTS query_result_rows")
        self._connection.execute("DROP TABLE IF EXISTS query_results")

    def _drop_incompatible_artifact_schema(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(result_artifacts)").fetchall()
        }
        if not columns:
            return
        expected = {
            "id",
            "run_id",
            "artifact_type",
            "source",
            "title",
            "columns_json",
            "metadata_json",
            "row_count",
            "row_count_is_exact",
            "session_id",
            "bot_id",
            "created_at",
        }
        if columns != expected:
            self._connection.execute("DROP TABLE IF EXISTS result_artifact_rows")
            self._connection.execute("DROP TABLE IF EXISTS result_artifacts")

    def _delete_results(self, result_ids: list[str]) -> int:
        if not result_ids:
            return 0
        self._connection.executemany(
            "DELETE FROM result_artifact_rows WHERE result_id = ?",
            ((result_id,) for result_id in result_ids),
        )
        self._connection.executemany(
            "DELETE FROM result_artifacts WHERE id = ?",
            ((result_id,) for result_id in result_ids),
        )
        return len(result_ids)

    def _opportunistic_cleanup(self) -> None:
        raw_ttl = os.environ.get("SQL_RESULT_TTL_HOURS", "").strip()
        if not raw_ttl:
            return
        try:
            ttl_hours = float(raw_ttl)
        except ValueError:
            return
        if ttl_hours > 0:
            self.cleanup(max_age_hours=ttl_hours)


def _utc_now_iso_from_age(max_age_hours: float) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=float(max_age_hours))
    return cutoff.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _assert_scope(
    row: sqlite3.Row,
    *,
    run_id: str = "",
    session_id: str = "",
    bot_id: str = "",
) -> None:
    expected = {
        "run_id": run_id,
        "session_id": session_id,
        "bot_id": bot_id,
    }
    if not any(str(value or "") for value in expected.values()):
        raise KeyError("Result access requires run_id, session_id, or bot_id scope.")
    for field, value in expected.items():
        clean_value = str(value or "")
        if clean_value and str(row[field] or "") != clean_value:
            raise KeyError(f"Result access denied for {field}.")
