import importlib
import sys

import pytest


def test_agent_runtime_package_level_api_still_exports_runtime() -> None:
    from agent_runtime import AgentRuntime
    from agent_runtime.core.orchestrator import AgentRuntime as CoreAgentRuntime

    assert AgentRuntime is CoreAgentRuntime


def test_new_layered_public_paths_are_available() -> None:
    from agent_runtime.core.context import RunContext
    from agent_runtime.memory.memory_manager import MemoryManager
    from agent_runtime.registry.skill_registry import AgentRegistry
    from agent_runtime.storage.database import CsvSQLiteBackend

    assert RunContext.__name__ == "RunContext"
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
        "memory_manager",
        "memory_store",
        "model_profiles",
        "orchestrator",
        "prompts",
        "result_store",
        "runtime_utils",
        "settings",
        "skill_registry",
        "token_counter",
    ],
)
def test_removed_top_level_shim_modules_are_not_importable(shim_name: str) -> None:
    module_name = f"agent_runtime.{shim_name}"
    sys.modules.pop(module_name, None)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
