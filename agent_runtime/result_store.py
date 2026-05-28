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


class ResultStore:
    """SQLite-backed store for full SQL query results.

    Worker tools return only a compact pointer and sample to the model. The full
    rows live here for UI pagination and CSV export.
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
        domain: str,
        sql: str,
        rows: list[dict[str, Any]],
    ) -> str:
        self._opportunistic_cleanup()
        result_id = f"res_{uuid.uuid4().hex[:16]}"
        columns = columns_from_rows(rows)
        created_at = utc_now_iso()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO query_results (
                    id, run_id, domain, sql, columns_json, row_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    run_id,
                    domain,
                    sql,
                    json.dumps(columns, ensure_ascii=False),
                    len(rows),
                    created_at,
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO query_result_rows (result_id, row_index, row_json)
                VALUES (?, ?, ?)
                """,
                (
                    (
                        result_id,
                        index,
                        json.dumps(row, ensure_ascii=False, default=str),
                    )
                    for index, row in enumerate(rows)
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
                    "SELECT id FROM query_results WHERE created_at < ?",
                    (cutoff,),
                ).fetchall()
                deleted += self._delete_results([str(row["id"]) for row in rows])
            if max_results is not None and max_results >= 0:
                rows = self._connection.execute(
                    """
                    SELECT id FROM query_results
                    ORDER BY created_at DESC, id DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (int(max_results),),
                ).fetchall()
                deleted += self._delete_results([str(row["id"]) for row in rows])
        return deleted

    def get_metadata(self, result_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, run_id, domain, sql, columns_json, row_count, created_at
                FROM query_results
                WHERE id = ?
                """,
                (result_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown result_id: {result_id}")
        return {
            "result_id": row["id"],
            "run_id": row["run_id"],
            "domain": row["domain"],
            "sql": row["sql"],
            "columns": json.loads(row["columns_json"] or "[]"),
            "row_count": row["row_count"],
            "created_at": row["created_at"],
        }

    def get_page(
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
                FROM query_result_rows
                WHERE result_id = ?
                ORDER BY row_index
                LIMIT ? OFFSET ?
                """,
                (result_id, limit, offset),
            ).fetchall()
        return [json.loads(row["row_json"]) for row in rows]

    def export_csv(self, result_id: str) -> bytes:
        metadata = self.get_metadata(result_id)
        columns = list(metadata.get("columns") or [])
        rows = self.get_page(result_id, offset=0, limit=max(1, int(metadata["row_count"])))
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
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_results (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    columns_json TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_result_rows (
                    result_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    row_json TEXT NOT NULL,
                    PRIMARY KEY (result_id, row_index),
                    FOREIGN KEY (result_id) REFERENCES query_results(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_query_result_rows_result_id
                ON query_result_rows(result_id, row_index)
                """
            )

    def _delete_results(self, result_ids: list[str]) -> int:
        if not result_ids:
            return 0
        self._connection.executemany(
            "DELETE FROM query_result_rows WHERE result_id = ?",
            ((result_id,) for result_id in result_ids),
        )
        self._connection.executemany(
            "DELETE FROM query_results WHERE id = ?",
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
