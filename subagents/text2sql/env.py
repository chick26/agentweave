from __future__ import annotations

import json
import os
from pathlib import Path

from agent_runtime.registry.skill_registry import AgentManifest
from agent_runtime.storage.database import CsvSQLiteBackend, DatabaseBackend, SqlDatabaseBackend


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


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path


def connect_prepared_backend(*, root: Path) -> DatabaseBackend:
    """Connect to a Text2SQL database environment that was prepared before runtime."""
    return load_database_backend(root)


def manifest_csv_tables(*, root: Path, manifest: AgentManifest) -> dict[str, Path]:
    """Resolve local CSV files declared by this subagent manifest.

    Used by environment preparation scripts only. Runtime does not call this
    automatically.
    """
    data_root = _data_root(root=root, manifest=manifest)
    return {
        str(table): _resolve_data_path(data_root, str(filename))
        for table, filename in manifest.data.tables.items()
    }


def _data_root(*, root: Path, manifest: AgentManifest) -> Path:
    if not manifest.data.roots:
        raise ValueError("Text2SQL manifest data.roots is required.")
    data_root = Path(manifest.data.roots[0]).expanduser()
    if data_root.is_absolute():
        return data_root
    return root / data_root


def _resolve_data_path(data_root: Path, filename: str) -> Path:
    path = Path(filename).expanduser()
    if path.is_absolute():
        return path
    return data_root / path
