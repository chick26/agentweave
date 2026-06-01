from __future__ import annotations

from pathlib import Path

from agents import SQLiteSession


async def fork_sqlite_session(
    *,
    db_path: Path,
    source_session_id: str,
    target_session_id: str,
) -> int:
    source = SQLiteSession(source_session_id, str(db_path))
    target = SQLiteSession(target_session_id, str(db_path))
    items = await source.get_items()
    if not items:
        raise ValueError(f"Source session has no items: {source_session_id}")
    await target.clear_session()
    await target.add_items(items)
    return len(items)


async def replace_sqlite_session_items(
    *,
    db_path: Path,
    session_id: str,
    items: list[dict],
) -> int:
    session = SQLiteSession(session_id, str(db_path))
    await session.clear_session()
    if items:
        await session.add_items(items)
    return len(items)
