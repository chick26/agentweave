"""Tests for public import aliases and package surface compatibility."""

import importlib
import sys

import pytest


def test_agent_runtime_package_level_api_still_exports_runtime() -> None:
    from agent_runtime import AgentRuntime
    from agent_runtime.core.runtime import AgentRuntime as CoreAgentRuntime

    assert AgentRuntime is CoreAgentRuntime


def test_agent_runtime_package_level_api_exports_common_types() -> None:
    from agent_runtime import (
        AgentRegistry,
        BotRegistry,
        CsvSQLiteBackend,
        EventBus,
        EventKind,
        MemoryManager,
        ResultStore,
        RuntimeContext,
        SkillRegistry,
        SqlDatabaseBackend,
    )

    assert AgentRegistry.__name__ == "AgentRegistry"
    assert BotRegistry.__name__ == "BotRegistry"
    assert CsvSQLiteBackend.__name__ == "CsvSQLiteBackend"
    assert EventBus.__name__ == "EventBus"
    assert EventKind.AGENT_START.value == "agent_start"
    assert MemoryManager.__name__ == "MemoryManager"
    assert ResultStore.__name__ == "ResultStore"
    assert RuntimeContext.__name__ == "RuntimeContext"
    assert SkillRegistry.__name__ == "SkillRegistry"
    assert SqlDatabaseBackend.__name__ == "SqlDatabaseBackend"


def test_new_layered_public_paths_are_available() -> None:
    from agent_runtime.core.context import RuntimeContext
    from agent_runtime.core.hooks import HookRunner
    from agent_runtime.core.result_events import extract_result_metadata
    from agent_runtime.hooks.session_start import SessionStartContext
    from agent_runtime.memory.memory_manager import MemoryManager
    from agent_runtime.registry.skill_registry import AgentRegistry
    from agent_runtime.registry.bot_registry import BotRegistry
    from agent_runtime.storage.database import CsvSQLiteBackend
    from agent_runtime.worker.subagent_runner import SubagentRunner

    assert RuntimeContext.__name__ == "RuntimeContext"
    assert HookRunner.__name__ == "HookRunner"
    assert SessionStartContext.__name__ == "SessionStartContext"
    assert extract_result_metadata.__name__ == "extract_result_metadata"
    assert SubagentRunner.__name__ == "SubagentRunner"
    assert CsvSQLiteBackend.__name__ == "CsvSQLiteBackend"
    assert MemoryManager.__name__ == "MemoryManager"
    assert AgentRegistry.__name__ == "AgentRegistry"
    assert BotRegistry.__name__ == "BotRegistry"


def test_subagent_api_public_surface_is_available() -> None:
    from agent_runtime.subagent_api import (
        SubagentContext,
        SubagentExtensionAPI,
        SubagentToolContext,
        ToolOutput,
        subagent_context,
        tool,
    )

    assert SubagentContext.__name__ == "SubagentContext"
    assert SubagentExtensionAPI.__name__ == "SubagentExtensionAPI"
    assert SubagentToolContext is not None
    assert ToolOutput.__name__ == "ToolOutput"
    assert callable(subagent_context)
    assert callable(tool)


@pytest.mark.parametrize(
    "module_name",
    [
        "agent_runtime.core.subagent_runner",
        "agent_runtime.core.subagent_extensions",
    ],
)
def test_legacy_subagent_core_shims_are_removed(module_name: str) -> None:
    sys.modules.pop(module_name, None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_shared_facade_public_surface_is_available() -> None:
    from agent_runtime.shared.common import columns_from_rows, xml_escape
    from agent_runtime.shared.database import CsvSQLiteBackend, validate_readonly_sql
    from agent_runtime.shared.embeddings import EmbeddingClient
    from agent_runtime.shared.manifest import AgentManifest, load_subagent_manifest
    from agent_runtime.shared.models import ModelProfile, call_chat_model

    assert columns_from_rows([{"a": 1}]) == ["a"]
    assert xml_escape("<x>") == "&lt;x&gt;"
    assert CsvSQLiteBackend.__name__ == "CsvSQLiteBackend"
    assert validate_readonly_sql("select 1") is None
    assert EmbeddingClient.__name__ == "EmbeddingClient"
    assert AgentManifest.__name__ == "AgentManifest"
    assert callable(load_subagent_manifest)
    assert ModelProfile.__name__ == "ModelProfile"
    assert callable(call_chat_model)


@pytest.mark.parametrize(
    "shim_name",
    [
        "compressor",
        "context",
        "database",
        "diagnostic_store",
        "embeddings",
        "memory_manager",
        "memory_store",
        "model_profiles",
        "orchestrator",
        "prompts",
        "result_events",
        "result_store",
        "runtime_utils",
        "settings",
        "skill_runner",
        "skill_registry",
        "token_counter",
    ],
)
def test_removed_top_level_shim_modules_are_not_importable(shim_name: str) -> None:
    module_name = f"agent_runtime.{shim_name}"
    sys.modules.pop(module_name, None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
