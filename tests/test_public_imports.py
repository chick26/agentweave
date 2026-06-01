import importlib
import sys

import pytest


def test_agent_runtime_package_level_api_still_exports_runtime() -> None:
    from agent_runtime import AgentRuntime
    from agent_runtime.core.orchestrator import AgentRuntime as CoreAgentRuntime

    assert AgentRuntime is CoreAgentRuntime


def test_agent_runtime_package_level_api_exports_common_types() -> None:
    from agent_runtime import (
        AgentRegistry,
        CsvSQLiteBackend,
        EventBus,
        EventKind,
        MemoryManager,
        OrchestratorContext,
        ResultStore,
        RunContext,
        SkillRegistry,
        SqlDatabaseBackend,
    )

    assert AgentRegistry.__name__ == "AgentRegistry"
    assert CsvSQLiteBackend.__name__ == "CsvSQLiteBackend"
    assert EventBus.__name__ == "EventBus"
    assert EventKind.AGENT_START.value == "agent_start"
    assert MemoryManager.__name__ == "MemoryManager"
    assert OrchestratorContext.__name__ == "OrchestratorContext"
    assert ResultStore.__name__ == "ResultStore"
    assert RunContext.__name__ == "RunContext"
    assert SkillRegistry.__name__ == "SkillRegistry"
    assert SqlDatabaseBackend.__name__ == "SqlDatabaseBackend"


def test_new_layered_public_paths_are_available() -> None:
    from agent_runtime.core.context import RunContext
    from agent_runtime.core.hooks import HookRunner
    from agent_runtime.core.preset_questions import PresetQuestionResult
    from agent_runtime.core.result_events import extract_result_metadata
    from agent_runtime.core.skill_runner import SubagentRunner
    from agent_runtime.memory.memory_manager import MemoryManager
    from agent_runtime.registry.skill_registry import AgentRegistry
    from agent_runtime.storage.database import CsvSQLiteBackend

    assert RunContext.__name__ == "RunContext"
    assert HookRunner.__name__ == "HookRunner"
    assert PresetQuestionResult.__name__ == "PresetQuestionResult"
    assert extract_result_metadata.__name__ == "extract_result_metadata"
    assert SubagentRunner.__name__ == "SubagentRunner"
    assert CsvSQLiteBackend.__name__ == "CsvSQLiteBackend"
    assert MemoryManager.__name__ == "MemoryManager"
    assert AgentRegistry.__name__ == "AgentRegistry"


@pytest.mark.parametrize(
    "shim_name",
    [
        "compressor",
        "context",
        "database",
        "diagnostic_store",
        "embeddings",
        "hooks",
        "memory_manager",
        "memory_store",
        "model_profiles",
        "orchestrator",
        "preset_questions",
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
