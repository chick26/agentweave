"""Runtime dependency container for AgentWeave."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.common import agentweave_data_dir, env_bool
from agent_runtime.core.compressor import ContextCompressor
from agent_runtime.core.hooks import HookRunner
from agent_runtime.core.model_profiles import ModelProfile, load_model_profile
from agent_runtime.core.result_formatters import ResultFormatterRegistry
from agent_runtime.core.session_manager import SessionManager
from agent_runtime.hooks.session_start import build_default_session_start_hooks
from agent_runtime.memory.embeddings import EmbeddingClient, load_embedding_profile
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.memory.memory_store import MemoryStore
from agent_runtime.memory.todo_state import TodoState
from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.registry.bot_registry import BotRegistry
from agent_runtime.registry.resources import ResourceLoader
from agent_runtime.registry.skill_registry import SkillRegistry
from agent_runtime.storage.artifact_store import ArtifactStore
from agent_runtime.worker.subagent_extensions import register_extension_result_formatters
from agent_runtime.worker.subagent_runner import SubagentRunner


@dataclass
class RuntimeServices:
    """Concrete services that AgentRuntime wires into one run."""

    session_db_path: Path
    root: Path
    data_dir: Path
    skill_registry: SkillRegistry
    agent_registry: AgentRegistry
    timezone_name: str
    model_profile: ModelProfile
    bot_registry: BotRegistry
    resource_loader: ResourceLoader
    memory_store: MemoryStore
    memory_enabled: bool
    embedding_profile: object
    memory_manager: MemoryManager
    todo_state: TodoState
    artifact_store: ArtifactStore
    result_formatters: ResultFormatterRegistry
    subagent_runner: SubagentRunner
    compressor: ContextCompressor
    session_manager: SessionManager
    hook_runner: HookRunner

    @classmethod
    def build(
        cls,
        *,
        base_url: str,
        model_name: str,
        api_key: str,
        session_db_path: Path,
        max_tokens: int = 4096,
        embedding_base_url: str | None = None,
        embedding_model_name: str | None = None,
        memory_enabled: bool | None = None,
        timezone_name: str | None = None,
    ) -> "RuntimeServices":
        session_db_path.parent.mkdir(parents=True, exist_ok=True)
        root = _resolve_runtime_root(session_db_path)
        data_dir = agentweave_data_dir(root)
        skill_registry = SkillRegistry(skills_root=root / "skills")
        agent_registry = AgentRegistry(subagents_root=root / "subagents")
        resolved_timezone_name = timezone_name or os.getenv(
            "AGENTWEAVE_TIMEZONE",
            "Asia/Hong_Kong",
        )
        model_profile = load_model_profile(
            base_url=base_url,
            model_name=model_name,
            max_tokens=max_tokens,
            api_key=api_key,
        )
        bot_registry = BotRegistry(
            bots_root=root / "bots",
            agent_registry=agent_registry,
            skill_registry=skill_registry,
        )
        resource_loader = ResourceLoader(
            root=root,
            skill_registry=skill_registry,
            agent_registry=agent_registry,
            bot_registry=bot_registry,
        )
        memory_store = MemoryStore(data_dir / "agent_memory.sqlite")
        resolved_memory_enabled = (
            env_bool("MEMORY_ENABLED", True)
            if memory_enabled is None
            else bool(memory_enabled)
        )
        embedding_profile = load_embedding_profile(
            base_url=embedding_base_url,
            model_name=embedding_model_name,
            api_key=api_key,
        )
        memory_manager = MemoryManager(
            memory_store,
            embedding_client=EmbeddingClient(embedding_profile),
            enabled=resolved_memory_enabled,
        )
        todo_state = TodoState()
        artifact_store = ArtifactStore(data_dir / "agent_artifacts.sqlite")
        result_formatters = _build_result_formatters(agent_registry)
        subagent_runner = SubagentRunner(
            registry=agent_registry,
            skill_registry=skill_registry,
            memory_manager=memory_manager,
            artifact_store=artifact_store,
            root=root,
        )
        compressor = ContextCompressor(
            context_window=model_profile.context_window,
            reserved_output_tokens=model_profile.max_tokens,
            model_name=model_profile.model_name,
        )
        session_manager = SessionManager(
            session_db_path=session_db_path,
            compressor=compressor,
            memory_manager=memory_manager,
        )
        return cls(
            session_db_path=session_db_path,
            root=root,
            data_dir=data_dir,
            skill_registry=skill_registry,
            agent_registry=agent_registry,
            timezone_name=resolved_timezone_name,
            model_profile=model_profile,
            bot_registry=bot_registry,
            resource_loader=resource_loader,
            memory_store=memory_store,
            memory_enabled=resolved_memory_enabled,
            embedding_profile=embedding_profile,
            memory_manager=memory_manager,
            todo_state=todo_state,
            artifact_store=artifact_store,
            result_formatters=result_formatters,
            subagent_runner=subagent_runner,
            compressor=compressor,
            session_manager=session_manager,
            hook_runner=HookRunner(handlers=build_default_session_start_hooks()),
        )

    def rebuild_result_formatters(self) -> None:
        self.result_formatters = _build_result_formatters(self.agent_registry)


def _resolve_runtime_root(session_db_path: Path) -> Path:
    session_root = session_db_path.resolve().parent
    if session_root.name == ".agentweave":
        session_root = session_root.parent
    if (session_root / "skills").exists() or (session_root / "subagents").exists():
        return session_root
    return Path.cwd().resolve()


def _build_result_formatters(agent_registry: AgentRegistry) -> ResultFormatterRegistry:
    result_formatters = ResultFormatterRegistry()
    register_extension_result_formatters(
        agent_registry.discover(),
        result_formatters,
    )
    return result_formatters


__all__ = ["RuntimeServices"]
