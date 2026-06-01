"""Public package API for the AgentWeave runtime."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentRuntime",
    "AgentRegistry",
    "CsvSQLiteBackend",
    "EventBus",
    "EventKind",
    "MemoryManager",
    "OrchestratorContext",
    "ResultStore",
    "RunContext",
    "SkillRegistry",
    "SqlDatabaseBackend",
]


_LAZY_EXPORTS = {
    "AgentRuntime": ("agent_runtime.core.orchestrator", "AgentRuntime"),
    "AgentRegistry": ("agent_runtime.registry.skill_registry", "AgentRegistry"),
    "CsvSQLiteBackend": ("agent_runtime.storage.database", "CsvSQLiteBackend"),
    "EventBus": ("agent_runtime.core.events", "EventBus"),
    "EventKind": ("agent_runtime.core.events", "EventKind"),
    "MemoryManager": ("agent_runtime.memory.memory_manager", "MemoryManager"),
    "OrchestratorContext": ("agent_runtime.core.context", "OrchestratorContext"),
    "ResultStore": ("agent_runtime.storage.result_store", "ResultStore"),
    "RunContext": ("agent_runtime.core.context", "RunContext"),
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
