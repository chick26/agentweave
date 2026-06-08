"""Session preparation and compression helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import SQLiteSession

from agent_runtime.core.events import EventKind


class SessionManager:
    def __init__(self, *, session_db_path: Path, compressor: Any, memory_manager: Any) -> None:
        self.session_db_path = session_db_path
        self.compressor = compressor
        self.memory_manager = memory_manager

    async def prepare(
        self,
        *,
        session_id: str,
        context: Any,
        model_profile: Any,
    ) -> SQLiteSession:
        session = SQLiteSession(session_id, str(self.session_db_path))
        prior_messages = await session.get_items()
        compressed_messages = await self.compressor.compress(
            prior_messages,
            session_id=session_id,
            memory_manager=self.memory_manager,
            model_profile=model_profile,
        )
        if compressed_messages != prior_messages:
            await session.clear_session()
            await session.add_items(compressed_messages)
            context.emit_payload(
                kind=EventKind.CONTEXT_COMPRESSED,
                run_id=session_id,
                payload={
                    "stage": "context_compressed",
                    "before_count": len(prior_messages),
                    "after_count": len(compressed_messages),
                },
            )
        return session
