from __future__ import annotations

import json
import os
from pathlib import Path

from agent_runtime.storage.database import CsvSQLiteBackend, DatabaseBackend, SqlDatabaseBackend
from agent_runtime.core.model_profiles import ModelProfile, load_model_profiles


TEXT2SQL_ENVIRONMENT_ERROR = (
    "Text2SQL database environment is not prepared. Follow "
    "subagents/text2sql/ENVIRONMENT.md, prepare the database first, then start runtime."
)


def load_database_backend(root: Path) -> DatabaseBackend:
    backend_kind = os.getenv("TEXT2SQL_BACKEND", "").strip().lower()
    if not backend_kind:
        raise RuntimeError(TEXT2SQL_ENVIRONMENT_ERROR)
    if backend_kind == "csv":
        return CsvSQLiteBackend(load_csv_tables(root))
    if backend_kind == "sqlite":
        database_url = os.getenv("TEXT2SQL_DATABASE_URL")
        if not database_url:
            raise ValueError("TEXT2SQL_DATABASE_URL is required when TEXT2SQL_BACKEND=sqlite")
        return SqlDatabaseBackend(database_url)
    raise ValueError(f"Unsupported TEXT2SQL_BACKEND: {backend_kind}")


def load_csv_tables(root: Path) -> dict[str, Path]:
    raw_config = os.getenv("TEXT2SQL_TABLES_JSON")
    if not raw_config:
        raise RuntimeError(
            "TEXT2SQL_TABLES_JSON is required when TEXT2SQL_BACKEND=csv. "
            "Follow subagents/text2sql/ENVIRONMENT.md to prepare the database."
        )
    tables = json.loads(raw_config)
    if not isinstance(tables, dict):
        raise ValueError("TEXT2SQL_TABLES_JSON must be a JSON object")
    return {
        str(table): _resolve_path(root, str(path))
        for table, path in tables.items()
    }


def build_model_profiles(
    *,
    base_url: str,
    model_name: str,
    api_key: str,
    max_tokens: int,
    sql_base_url: str,
    sql_model_name: str,
    sql_max_tokens: int,
) -> dict[str, ModelProfile]:
    return load_model_profiles(
        orchestrator_base_url=base_url,
        orchestrator_model=model_name,
        orchestrator_max_tokens=max_tokens,
        sql_base_url=sql_base_url,
        sql_model=sql_model_name,
        sql_max_tokens=sql_max_tokens,
        api_key=api_key,
    )


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path
