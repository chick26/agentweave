import asyncio
from agents import SQLiteSession

from agent_runtime.core.session_ops import (
    fork_sqlite_session,
    replace_sqlite_session_items,
)


def test_fork_sqlite_session_copies_items(tmp_path):
    db_path = tmp_path / "sessions.sqlite"
    source = SQLiteSession("source", str(db_path))
    items = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    asyncio.run(source.add_items(items))

    copied = asyncio.run(
        fork_sqlite_session(
            db_path=db_path,
            source_session_id="source",
            target_session_id="target",
        )
    )
    target = SQLiteSession("target", str(db_path))

    assert copied == 2
    assert asyncio.run(target.get_items()) == asyncio.run(source.get_items())


def test_replace_sqlite_session_items_overwrites_existing_items(tmp_path):
    db_path = tmp_path / "sessions.sqlite"
    session = SQLiteSession("target", str(db_path))
    asyncio.run(session.add_items([{"role": "user", "content": "old"}]))

    count = asyncio.run(
        replace_sqlite_session_items(
            db_path=db_path,
            session_id="target",
            items=[{"role": "user", "content": "new"}],
        )
    )

    assert count == 1
    assert asyncio.run(session.get_items()) == [{"role": "user", "content": "new"}]
