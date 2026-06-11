"""Tests for public import surfaces after breaking cleanup."""

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
        ArtifactStore,
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
    assert ArtifactStore.__name__ == "ArtifactStore"
    assert RuntimeContext.__name__ == "RuntimeContext"
    assert SkillRegistry.__name__ == "SkillRegistry"
    assert SqlDatabaseBackend.__name__ == "SqlDatabaseBackend"


def test_new_layered_public_paths_are_available() -> None:
    from agent_runtime.core.context import RuntimeContext
    from agent_runtime.core.hooks import HookRunner
    from agent_runtime.core.result_events import extract_result_metadata
    from agent_runtime.core.result_formatters import ResultArtifactSpec, ResultFormatterRegistry
    from agent_runtime.hooks.session_start import SessionStartContext
    from agent_runtime.memory.memory_manager import MemoryManager
    from agent_runtime.registry.agent_registry import AgentRegistry as SplitAgentRegistry
    from agent_runtime.registry.manifest_models import AgentManifest as SplitAgentManifest
    from agent_runtime.registry.agent_registry import AgentRegistry
    from agent_runtime.registry.bot_registry import BotRegistry
    from agent_runtime.storage.database import CsvSQLiteBackend
    from agent_runtime.worker.subagent_runner import SubagentRunner

    assert RuntimeContext.__name__ == "RuntimeContext"
    assert HookRunner.__name__ == "HookRunner"
    assert SessionStartContext.__name__ == "SessionStartContext"
    assert extract_result_metadata.__name__ == "extract_result_metadata"
    assert ResultArtifactSpec.__name__ == "ResultArtifactSpec"
    assert ResultFormatterRegistry.__name__ == "ResultFormatterRegistry"
    assert SubagentRunner.__name__ == "SubagentRunner"
    assert CsvSQLiteBackend.__name__ == "CsvSQLiteBackend"
    assert MemoryManager.__name__ == "MemoryManager"
    assert AgentRegistry.__name__ == "AgentRegistry"
    assert SplitAgentRegistry is AgentRegistry
    assert SplitAgentManifest.__name__ == "AgentManifest"
    assert BotRegistry.__name__ == "BotRegistry"


def test_legacy_registry_cross_exports_are_removed() -> None:
    import agent_runtime.registry.skill_registry as skill_registry

    assert hasattr(skill_registry, "SkillRegistry")
    assert not hasattr(skill_registry, "AgentRegistry")
    assert not hasattr(skill_registry, "AgentManifest")
    assert not hasattr(skill_registry, "ManifestBase")


def test_legacy_storage_result_store_module_is_removed() -> None:
    sys.modules.pop("agent_runtime.storage.result_store", None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agent_runtime.storage.result_store")


def test_legacy_sqlite_backend_alias_is_removed() -> None:
    from agent_runtime.storage import database

    assert not hasattr(database, "SQLiteBackend")


def test_subagent_api_public_surface_is_available() -> None:
    from agent_runtime.subagent_api import (
        SubagentContext,
        SubagentExtensionAPI,
        SubagentToolContext,
        ResultArtifactSpec,
        ToolOutput,
        subagent_context,
        tool,
    )

    assert SubagentContext.__name__ == "SubagentContext"
    assert SubagentExtensionAPI.__name__ == "SubagentExtensionAPI"
    assert SubagentToolContext is not None
    assert ResultArtifactSpec.__name__ == "ResultArtifactSpec"
    assert ToolOutput.__name__ == "ToolOutput"
    assert callable(subagent_context)
    assert callable(tool)


def test_subagent_context_surface_is_intentionally_narrow() -> None:
    from agent_runtime.subagent_api import SubagentContext

    expected = {
        "call_model",
        "embedding_client",
        "get_artifact",
        "get_artifact_page",
        "manifest",
        "policies",
        "run_id",
        "runtime_root",
        "store_artifact",
        "timezone_name",
        "trace",
        "typed_state",
    }
    removed = {
        "cache",
        "capabilities",
        "emit_tool_result",
        "embedding_profile",
        "model_profile",
        "output_contract",
        "result_created",
        "store_result",
    }

    for name in expected:
        assert hasattr(SubagentContext, name)
    for name in removed:
        assert not hasattr(SubagentContext, name)


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
        "artifact_store",
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
