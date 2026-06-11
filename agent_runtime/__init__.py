"""Public package API for the AgentWeave runtime."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentRuntime",
    "AgentRegistry",
    "BotConfig",
    "BotRegistry",
    "CsvSQLiteBackend",
    "EventBus",
    "EventKind",
    "MemoryManager",
    "ArtifactStore",
    "RuntimeContext",
    "SkillRegistry",
    "SqlDatabaseBackend",
]


_LAZY_EXPORTS = {
    "AgentRuntime": ("agent_runtime.core.runtime", "AgentRuntime"),
    "AgentRegistry": ("agent_runtime.registry.agent_registry", "AgentRegistry"),
    "BotConfig": ("agent_runtime.registry.bot_registry", "BotConfig"),
    "BotRegistry": ("agent_runtime.registry.bot_registry", "BotRegistry"),
    "CsvSQLiteBackend": ("agent_runtime.storage.database", "CsvSQLiteBackend"),
    "EventBus": ("agent_runtime.core.events", "EventBus"),
    "EventKind": ("agent_runtime.core.events", "EventKind"),
    "MemoryManager": ("agent_runtime.memory.memory_manager", "MemoryManager"),
    "ArtifactStore": ("agent_runtime.storage.artifact_store", "ArtifactStore"),
    "RuntimeContext": ("agent_runtime.core.context", "RuntimeContext"),
    "SkillRegistry": ("agent_runtime.registry.skill_registry", "SkillRegistry"),
    "SqlDatabaseBackend": ("agent_runtime.storage.database", "SqlDatabaseBackend"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_name, attribute_name = _LAZY_EXPORTS[name]
        module = __import__(module_name, fromlist=[attribute_name])
        value = getattr(module, attribute_name)
        globals()[name] = value
        return value

    raise AttributeError(f"module 'agent_runtime' has no attribute {name!r}")
