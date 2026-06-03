from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent_runtime.core.runtime_utils import get_current_time_payload
from subagents.text2sql.scripts.sql_generation import extract_sql
from subagents.text2sql.env import load_csv_tables, load_database_backend
from agent_runtime.storage.database import (
    CsvSQLiteBackend,
    SqlDatabaseBackend,
    _coerce_csv_value,
    validate_readonly_sql,
)
from subagents.text2sql.env import manifest_csv_tables


def test_csv_backend_infers_types_and_executes_numeric_queries(tmp_path):
    csv_path = tmp_path / "metrics.csv"
    csv_path.write_text(
        "name,fault_duration,rate,pop_fault_time\n"
        "NCP,10.5,0.12,2024-01-01\n"
        "NCP,4.5,0.08,2024-01-02\n"
        "AAG,3.0,0.05,2024-01-03\n",
        encoding="utf-8",
    )
    backend = CsvSQLiteBackend({"metrics": csv_path})

    schema = backend.get_schema_for_prompt(
        "metrics",
        {
            "name": "名称",
            "fault_duration": "故障持续时长",
            "rate": "影响率",
            "pop_fault_time": "故障时间",
        },
    )
    assert "fault_duration (real)" in schema
    assert "rate (real)" in schema
    assert "pop_fault_time (datetime)" in schema

    rows = backend.execute_sql(
        "SELECT name, AVG(fault_duration) AS avg_duration "
        "FROM metrics GROUP BY name ORDER BY avg_duration DESC"
    )
    assert rows[0]["name"] == "NCP"
    assert rows[0]["avg_duration"] == pytest.approx(7.5)


def test_csv_backend_coerces_invalid_numeric_cells_to_null(tmp_path):
    assert _coerce_csv_value("N/A", "integer") is None
    assert _coerce_csv_value("bad", "real") is None
    assert _coerce_csv_value("2", "integer") == 2
    assert _coerce_csv_value("2.5", "real") == 2.5


def test_csv_backend_searches_known_columns_only(tmp_path):
    csv_path = tmp_path / "resources.csv"
    csv_path.write_text("room,status\n403,Available\n404,Sold\n", encoding="utf-8")
    backend = CsvSQLiteBackend({"resources": csv_path})

    assert sorted(backend.search_distinct_values("resources", "room", "40")) == [
        ("403", 1),
        ("404", 1),
    ]
    with pytest.raises(ValueError, match="Unknown column"):
        backend.search_distinct_values("resources", "missing", "40")
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        backend.search_distinct_values("resources;DROP", "room", "40")


def test_readonly_sql_validation_blocks_mutation_and_multi_statement():
    validate_readonly_sql("SELECT * FROM resources")
    validate_readonly_sql("WITH rows AS (SELECT * FROM resources) SELECT * FROM rows")

    blocked_sql = [
        "DELETE FROM resources",
        "SELECT * FROM resources; DROP TABLE resources",
        "PRAGMA table_info(resources)",
        "WITH deleted AS (DELETE FROM resources RETURNING *) SELECT * FROM deleted",
    ]
    for sql in blocked_sql:
        with pytest.raises(ValueError):
            validate_readonly_sql(sql)


def test_sql_database_backend_reads_sqlite_file(tmp_path):
    db_path = tmp_path / "demo.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE resources (room TEXT, available_count INTEGER, updated_time TEXT)"
    )
    connection.executemany(
        "INSERT INTO resources VALUES (?, ?, ?)",
        [
            ("403", 12, "2024-01-01"),
            ("404", 5, "2024-01-02"),
        ],
    )
    connection.commit()
    connection.close()

    backend = SqlDatabaseBackend(f"sqlite:////{db_path.as_posix().lstrip('/')}")

    schema = backend.get_schema_for_prompt(
        "resources",
        {"room": "机房", "available_count": "可用数量", "updated_time": "更新时间"},
    )
    assert "available_count (integer)" in schema
    assert "updated_time (datetime)" in schema
    assert sorted(backend.search_distinct_values("resources", "room", "40")) == [
        ("403", 1),
        ("404", 1),
    ]
    assert backend.execute_sql("SELECT SUM(available_count) AS total FROM resources") == [
        {"total": 17}
    ]


def test_csv_table_config_can_be_overridden_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXT2SQL_BACKEND", "csv")
    monkeypatch.setenv("TEXT2SQL_TABLES_JSON", '{"demo": "demo.csv"}')
    assert load_csv_tables(tmp_path) == {"demo": tmp_path / "demo.csv"}


def test_database_backend_requires_explicit_text2sql_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("TEXT2SQL_BACKEND", raising=False)
    monkeypatch.delenv("TEXT2SQL_TABLES_JSON", raising=False)
    monkeypatch.delenv("TEXT2SQL_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="database environment is not prepared"):
        load_database_backend(tmp_path)


def test_csv_backend_requires_explicit_table_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXT2SQL_BACKEND", "csv")
    monkeypatch.delenv("TEXT2SQL_TABLES_JSON", raising=False)

    with pytest.raises(RuntimeError, match="TEXT2SQL_TABLES_JSON is required"):
        load_database_backend(tmp_path)


def test_text2sql_env_can_use_subagent_manifest_data(tmp_path, monkeypatch):
    monkeypatch.delenv("TEXT2SQL_TABLES_JSON", raising=False)
    csv_path = tmp_path / "subagents" / "text2sql" / "data" / "resources.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("room\n403\n", encoding="utf-8")
    manifest = SimpleNamespace(
        data=SimpleNamespace(
            roots=["subagents/text2sql/data"],
            tables={"resources": "resources.csv"},
        )
    )

    assert manifest_csv_tables(root=tmp_path, manifest=manifest) == {"resources": csv_path}


def test_current_time_payload_uses_requested_timezone():
    payload = get_current_time_payload(
        "Asia/Hong_Kong",
        now=datetime(2026, 5, 14, 2, 30, tzinfo=timezone.utc),
    )

    assert payload["timezone"] == "Asia/Hong_Kong"
    assert payload["date"] == "2026-05-14"
    assert payload["time"] == "10:30:00"
    assert payload["iso"].endswith("+08:00")


def test_extract_sql_prefers_fenced_sql_block_over_analysis_text():
    content = """
### 分析过程
使用 `SELECT COUNT(*) FROM resources WHERE ...` 结构。

### SQL
```sql
SELECT COUNT(*)
FROM resources
WHERE machine_room = '403'
  AND cabinet_business_status = 'Available';
```
"""

    assert extract_sql(content) == (
        "SELECT COUNT(*) FROM resources WHERE machine_room = '403' "
        "AND cabinet_business_status = 'Available'"
    )


def test_extract_sql_keeps_case_when_and_with_queries():
    case_sql = """
```sql
SELECT CASE
  WHEN status = 'active' THEN 1
  ELSE 0
END AS is_active
FROM resources;
```
"""
    with_sql = """
分析:
WITH rows AS (
  SELECT *
  FROM resources
)
SELECT COUNT(*) AS total
FROM rows;
"""

    assert "WHEN status = 'active' THEN 1" in extract_sql(case_sql)
    assert extract_sql(with_sql).startswith("WITH rows AS")
    assert "SELECT COUNT(*) AS total" in extract_sql(with_sql)
