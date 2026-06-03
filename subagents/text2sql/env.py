from __future__ import annotations

from pathlib import Path

from agent_runtime.core.settings import load_database_backend
from agent_runtime.registry.skill_registry import AgentManifest
from agent_runtime.storage.database import DatabaseBackend


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
