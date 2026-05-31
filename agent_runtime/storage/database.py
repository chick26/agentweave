from __future__ import annotations

import csv
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote, urlparse

from agent_runtime.common import quote_identifier, validate_identifier


@dataclass(frozen=True)
class ColumnInfo:
    """Schema metadata used by prompt construction and backend validation."""

    name: str
    type: str


@runtime_checkable
class DatabaseBackend(Protocol):
    """Unified read-only interface for database-backed question answering."""

    @property
    def dialect(self) -> str:
        """Return the SQL dialect name used in prompts."""
        ...

    def get_columns(self, table: str) -> list[str]:
        """Return the column names for the given table."""
        ...

    def get_column_info(self, table: str) -> list[ColumnInfo]:
        """Return typed column metadata for the given table."""
        ...

    def search_distinct_values(
        self,
        table: str,
        column: str,
        keyword: str,
        limit: int = 20,
    ) -> list[tuple[str, int]]:
        """Search for distinct values in a column that contain the keyword.

        Returns a list of (value, count) tuples, ordered by count descending.
        """
        ...

    def execute_sql(self, sql: str, max_rows: int = 100) -> list[dict[str, Any]]:
        """Execute a single read-only SQL query and return result rows as dicts."""
        ...

    def get_schema_for_prompt(
        self,
        table: str,
        field_descriptions: dict[str, str],
    ) -> str:
        """Return a human-readable schema description for LLM prompts."""
        ...


class CsvSQLiteBackend:
    """SQLite backend loaded from one or more CSV files for local demos."""

    def __init__(self, tables: dict[str, Path | str]) -> None:
        """Initialize the backend with a mapping of table names to CSV paths."""
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._columns: dict[str, list[ColumnInfo]] = {}
        for table_name, csv_path in tables.items():
            self._load_csv(Path(csv_path), table_name)

    @property
    def dialect(self) -> str:
        return "SQLite"

    def _load_csv(self, csv_path: Path, table_name: str) -> None:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]
            columns = list(reader.fieldnames or [])

        column_info = [
            ColumnInfo(name=col, type=_infer_column_type(col, [row.get(col, "") for row in rows]))
            for col in columns
        ]
        self._columns[table_name] = column_info

        quoted_columns = ", ".join(
            f"{quote_identifier(col.name)} {_sqlite_storage_type(col.type)}"
            for col in column_info
        )
        self._connection.execute(
            f"CREATE TABLE {quote_identifier(table_name)} ({quoted_columns})"
        )

        placeholders = ", ".join("?" for _ in columns)
        insert_sql = (
            f"INSERT INTO {quote_identifier(table_name)} "
            f"({', '.join(quote_identifier(col) for col in columns)}) "
            f"VALUES ({placeholders})"
        )
        self._connection.executemany(
            insert_sql,
            (
                [
                    _coerce_csv_value(row.get(col.name, ""), col.type)
                    for col in column_info
                ]
                for row in rows
            ),
        )
        self._connection.commit()

    def get_columns(self, table: str) -> list[str]:
        return [col.name for col in self.get_column_info(table)]

    def get_column_info(self, table: str) -> list[ColumnInfo]:
        validate_identifier(table)
        if table not in self._columns:
            raise ValueError(f"Unknown table: {table}")
        return list(self._columns[table])

    def search_distinct_values(
        self,
        table: str,
        column: str,
        keyword: str,
        limit: int = 20,
    ) -> list[tuple[str, int]]:
        _validate_table_column(self, table, column)
        sql = (
            f"SELECT {quote_identifier(column)}, COUNT(*) AS cnt "
            f"FROM {quote_identifier(table)} "
            f"WHERE CAST({quote_identifier(column)} AS TEXT) LIKE ? "
            f"GROUP BY {quote_identifier(column)} "
            f"ORDER BY cnt DESC "
            f"LIMIT ?"
        )
        pattern = f"%{keyword}%"
        with self._lock:
            cursor = self._connection.execute(sql, (pattern, limit))
            return [(str(row[0]), row[1]) for row in cursor.fetchall() if row[0] is not None]

    def execute_sql(self, sql: str, max_rows: int = 100) -> list[dict[str, Any]]:
        clean_sql = _normalize_sql(sql)
        validate_readonly_sql(clean_sql)
        with self._lock:
            try:
                cursor = self._connection.execute(clean_sql)
                rows = [dict(row) for row in cursor.fetchmany(max_rows)]
            except sqlite3.Error as exc:
                raise ValueError(str(exc)) from exc
        return rows

    def get_schema_for_prompt(
        self,
        table: str,
        field_descriptions: dict[str, str],
    ) -> str:
        return _format_schema_for_prompt(table, self.get_column_info(table), field_descriptions)


class SQLiteBackend(CsvSQLiteBackend):
    """Backward-compatible alias for the CSV-backed SQLite demo backend."""


class SqlDatabaseBackend:
    """Read-only SQL backend for real database tables.

    The current implementation supports SQLite connection strings such as:
    - sqlite:////absolute/path/to/database.db
    - sqlite:///relative/path/to/database.db
    - sqlite:///:memory:

    The class keeps the same DatabaseBackend surface as CsvSQLiteBackend, so it
    can later be wrapped by an MCP server without moving query logic again.
    """

    def __init__(self, connection_string: str, dialect: str = "SQLite") -> None:
        self._connection = _connect_sqlite(connection_string)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._dialect = dialect

    @property
    def dialect(self) -> str:
        return self._dialect

    def get_columns(self, table: str) -> list[str]:
        return [col.name for col in self.get_column_info(table)]

    def get_column_info(self, table: str) -> list[ColumnInfo]:
        validate_identifier(table)
        with self._lock:
            cursor = self._connection.execute(
                f"PRAGMA table_info({quote_identifier(table)})"
            )
            rows = cursor.fetchall()
        if not rows:
            raise ValueError(f"Unknown table: {table}")
        return [
            ColumnInfo(name=row["name"], type=_normalize_type_name(row["type"], row["name"]))
            for row in rows
        ]

    def search_distinct_values(
        self,
        table: str,
        column: str,
        keyword: str,
        limit: int = 20,
    ) -> list[tuple[str, int]]:
        _validate_table_column(self, table, column)
        sql = (
            f"SELECT {quote_identifier(column)}, COUNT(*) AS cnt "
            f"FROM {quote_identifier(table)} "
            f"WHERE CAST({quote_identifier(column)} AS TEXT) LIKE ? "
            f"GROUP BY {quote_identifier(column)} "
            f"ORDER BY cnt DESC "
            f"LIMIT ?"
        )
        pattern = f"%{keyword}%"
        with self._lock:
            cursor = self._connection.execute(sql, (pattern, limit))
            return [(str(row[0]), row[1]) for row in cursor.fetchall() if row[0] is not None]

    def execute_sql(self, sql: str, max_rows: int = 100) -> list[dict[str, Any]]:
        clean_sql = _normalize_sql(sql)
        validate_readonly_sql(clean_sql)
        with self._lock:
            try:
                cursor = self._connection.execute(clean_sql)
                rows = [dict(row) for row in cursor.fetchmany(max_rows)]
            except sqlite3.Error as exc:
                raise ValueError(str(exc)) from exc
        return rows

    def get_schema_for_prompt(
        self,
        table: str,
        field_descriptions: dict[str, str],
    ) -> str:
        return _format_schema_for_prompt(table, self.get_column_info(table), field_descriptions)


def validate_readonly_sql(sql: str) -> None:
    """Validate that SQL is a single read-only SELECT/WITH statement."""
    clean_sql = _normalize_sql(sql)
    if not clean_sql:
        raise ValueError("SQL is empty")
    _ensure_single_statement(clean_sql)
    sql_without_comments = _strip_sql_comments(clean_sql).strip()
    first_keyword = _first_sql_keyword(sql_without_comments)
    if first_keyword not in {"select", "with"}:
        raise ValueError("Only read-only SELECT/WITH SQL is allowed")

    lowered = _mask_quoted_strings(sql_without_comments).lower()
    blocked = {
        "alter", "attach", "create", "delete", "detach", "drop", "insert",
        "pragma", "replace", "update", "vacuum",
    }
    for keyword in blocked:
        if re.search(rf"\b{keyword}\b", lowered):
            raise ValueError(f"Unsafe SQL keyword is not allowed: {keyword.upper()}")


def _format_schema_for_prompt(
    table: str,
    columns: list[ColumnInfo],
    field_descriptions: dict[str, str],
) -> str:
    lines = [f"表名: {table}", "字段:"]
    for col in columns:
        desc = field_descriptions.get(col.name, col.name)
        lines.append(f"- {col.name} ({col.type}): {desc}")
    return "\n".join(lines)


def _validate_table_column(backend: DatabaseBackend, table: str, column: str) -> None:
    validate_identifier(table)
    validate_identifier(column)
    columns = set(backend.get_columns(table))
    if column not in columns:
        raise ValueError(f"Unknown column: {table}.{column}")


def _normalize_sql(sql: str) -> str:
    clean_sql = sql.strip()
    clean_sql = re.sub(r"^```(?:sql)?", "", clean_sql, flags=re.IGNORECASE).strip()
    clean_sql = re.sub(r"```$", "", clean_sql).strip()
    return clean_sql.strip()


def _ensure_single_statement(sql: str) -> None:
    semicolon_positions = _semicolon_positions_outside_strings(sql)
    if not semicolon_positions:
        return
    last_semicolon = semicolon_positions[-1]
    if sql[last_semicolon + 1 :].strip():
        raise ValueError("Only one SQL statement is allowed")
    if len(semicolon_positions) > 1:
        raise ValueError("Only one SQL statement is allowed")


def _semicolon_positions_outside_strings(sql: str) -> list[int]:
    positions: list[int] = []
    quote: str | None = None
    idx = 0
    while idx < len(sql):
        char = sql[idx]
        if quote:
            if char == quote:
                if idx + 1 < len(sql) and sql[idx + 1] == quote:
                    idx += 2
                    continue
                quote = None
            idx += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == ";":
            positions.append(idx)
        idx += 1
    return positions


def _strip_sql_comments(sql: str) -> str:
    without_line_comments = re.sub(r"--[^\n\r]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", without_line_comments, flags=re.DOTALL)


def _first_sql_keyword(sql: str) -> str:
    match = re.match(r"\s*([A-Za-z]+)\b", sql)
    return match.group(1).lower() if match else ""


def _mask_quoted_strings(sql: str) -> str:
    result: list[str] = []
    quote: str | None = None
    idx = 0
    while idx < len(sql):
        char = sql[idx]
        if quote:
            if char == quote:
                if idx + 1 < len(sql) and sql[idx + 1] == quote:
                    idx += 2
                    continue
                quote = None
            idx += 1
            continue
        if char in {"'", '"'}:
            quote = char
            idx += 1
            continue
        result.append(char)
        idx += 1
    return "".join(result)


def _infer_column_type(column_name: str, values: list[str]) -> str:
    non_empty = [value.strip() for value in values if value is not None and value.strip()]
    if not non_empty:
        return "text"
    if _looks_like_datetime_column(column_name):
        return "datetime"
    if all(_is_int(value) for value in non_empty):
        return "integer"
    if all(_is_float(value) for value in non_empty):
        return "real"
    return "text"


def _looks_like_datetime_column(column_name: str) -> bool:
    lowered = column_name.lower()
    return (
        lowered.endswith("_time")
        or lowered.endswith("_date")
        or "date" in lowered
        or lowered in {"reserved_field"}
    )


def _is_int(value: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+", value.strip()))


def _is_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _coerce_csv_value(value: str | None, column_type: str) -> Any:
    if value is None or value.strip() == "":
        return None
    if column_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if column_type == "real":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return value


def _sqlite_storage_type(column_type: str) -> str:
    if column_type == "integer":
        return "INTEGER"
    if column_type == "real":
        return "REAL"
    return "TEXT"


def _normalize_type_name(raw_type: str, column_name: str) -> str:
    lowered = (raw_type or "").lower()
    if _looks_like_datetime_column(column_name):
        return "datetime"
    if any(token in lowered for token in ("int", "bool")):
        return "integer"
    if any(token in lowered for token in ("real", "float", "double", "decimal", "numeric")):
        return "real"
    if any(token in lowered for token in ("date", "time")):
        return "datetime"
    return "text"


def _connect_sqlite(connection_string: str) -> sqlite3.Connection:
    parsed = urlparse(connection_string)
    if parsed.scheme != "sqlite":
        raise ValueError(
            "Only sqlite:// connection strings are supported by SqlDatabaseBackend"
        )
    if connection_string == "sqlite:///:memory:":
        return sqlite3.connect(":memory:", check_same_thread=False)

    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raise ValueError("SQLite connection strings must use a local file path")

    if connection_string.startswith("sqlite:////"):
        db_path = Path("/" + unquote(connection_string.removeprefix("sqlite:////")))
    elif connection_string.startswith("sqlite:///"):
        db_path = Path(unquote(connection_string.removeprefix("sqlite:///")))
    else:
        raise ValueError("SQLite connection string is missing a database path")
    return sqlite3.connect(db_path, check_same_thread=False)
