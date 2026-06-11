"""Worker subagent agent construction and SDK tool wrapping."""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel
from agents.tool import FunctionTool
from agents.tool_context import ToolContext

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.runtime_utils import (
    build_model,
    get_current_time_payload,
    json_dumps,
    make_async_client,
)
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.registry.manifest_models import AgentManifest
from agent_runtime.worker.result_normalizer import (
    SubagentResult,
    SubagentToolInput,
    build_subagent_input,
    extract_worker_agent_tool_output,
    parse_subagent_tool_input,
    subagent_tool_payload,
)
from agent_runtime.worker.subagent_extensions import (
    build_extension_prompt_context,
    build_extension_tools,
)


RunWorkerFn = Callable[[str, RuntimeContext], Awaitable[SubagentResult]]


class SubagentAgentFactory:
    """Build worker agents, worker prompts, local tools, and agent-as-tool wrappers."""

    def __init__(self, *, memory_manager: MemoryManager | None = None) -> None:
        self.memory_manager = memory_manager

    def build_worker_agent(
        self,
        *,
        manifest: AgentManifest,
        profile: Any,
        prompt_query: str = "",
        timezone_name: str = "Asia/Hong_Kong",
        memory_events: list[dict[str, Any]] | None = None,
        log_callback: Any | None = None,
    ) -> Agent[RuntimeContext]:
        model = (
            build_model(
                profile=profile,
                log_callback=log_callback,
                title=f"{manifest.name} Worker 编排模型调用",
                kind="subagent_orchestration_model",
            )
            if log_callback is not None
            else OpenAIChatCompletionsModel(
                model=profile.model_name,
                openai_client=make_async_client(profile),
            )
        )
        return Agent[RuntimeContext](
            name=f"{manifest.name}-worker",
            instructions=self.build_worker_prompt(
                manifest,
                query=prompt_query,
                timezone_name=timezone_name,
                memory_events=memory_events,
            ),
            model=model,
            model_settings=ModelSettings(
                max_tokens=profile.max_tokens,
                tool_choice="auto",
                extra_body=self.get_extra_body(profile),
            ),
            tools=self.build_subagent_tools(manifest),
        )

    def build_worker_agent_tool(
        self,
        *,
        manifest: AgentManifest,
        profile: Any,
        max_turns: int,
        run_worker: RunWorkerFn,
    ) -> FunctionTool:
        """Expose a subagent as an SDK agent-as-tool while preserving isolation."""

        worker_agent = self.build_worker_agent(manifest=manifest, profile=profile)
        tool = worker_agent.as_tool(
            tool_name=manifest.name,
            tool_description=self.build_worker_agent_tool_description(manifest),
            parameters=SubagentToolInput,
            input_builder=build_subagent_input,
            custom_output_extractor=extract_worker_agent_tool_output,
            max_turns=max_turns,
            session=None,
        )

        async def invoke_tool(ctx: ToolContext[Any], input_json: str) -> str:
            task_input = parse_subagent_tool_input(input_json)
            parent_context = getattr(ctx, "context", None)
            if not isinstance(parent_context, RuntimeContext):
                raise TypeError(
                    f"Subagent tool `{manifest.name}` requires RuntimeContext."
                )
            result = await run_worker(task_input.task, parent_context)
            return json_dumps(subagent_tool_payload(result))

        tool.on_invoke_tool = invoke_tool
        return tool

    def build_worker_agent_tool_description(self, manifest: AgentManifest) -> str:
        lines = [
            f"Run the `{manifest.name}` subagent as an isolated SDK agent tool.",
            manifest.description,
            "",
            "Use this when the user task matches this delegated capability.",
            "Provide a self-contained task with resolved dates, business intent, and user-provided filter terms; do not invent schema fields or enum values.",
        ]
        if manifest.routing_hints:
            lines.append(f"Route when: {', '.join(manifest.routing_hints)}")
        return "\n".join(line for line in lines if line)

    def build_worker_prompt(
        self,
        manifest: AgentManifest,
        *,
        query: str = "",
        timezone_name: str = "Asia/Hong_Kong",
        memory_events: list[dict[str, Any]] | None = None,
    ) -> str:
        template = manifest.body.strip() or manifest.description
        context = {
            "current_time": json_dumps(get_current_time_payload(timezone_name)),
            "memory": self.memory_manager.build_skill_context(
                manifest,
                query=query,
                retrieval_events=memory_events,
            )
            if self.memory_manager is not None
            else "",
        }
        context.update(build_extension_prompt_context(manifest))
        for key, value in context.items():
            template = template.replace(f"{{{key}}}", str(value or ""))
        return template

    def build_subagent_tools(self, manifest: AgentManifest) -> list[Any]:
        if not subagent_enabled(manifest):
            return []
        if manifest.tools:
            raise ValueError(
                f"Subagent `{manifest.name}` declares legacy tools. "
                "Use extension.py register(api) instead of AGENT.yaml tools."
            )
        tools_by_name: dict[str, Any] = {}
        for tool in build_extension_tools(manifest):
            tools_by_name[tool_name(tool)] = tool
        return list(tools_by_name.values())

    def get_extra_body(self, profile: Any) -> dict[str, Any]:
        return getattr(profile, "extra_body", {}) or {}


def subagent_enabled(manifest: AgentManifest) -> bool:
    env_key = f"{subagent_env_prefix(manifest.name)}_ENABLED"
    enabled_value = os.getenv(env_key, "1").strip().lower()
    return enabled_value not in {"0", "false", "no", "off"}


def tool_name(tool: Any) -> str:
    name = getattr(tool, "name", "")
    if name:
        return str(name)
    name = getattr(tool, "__name__", "")
    if name:
        return str(name)
    raise ValueError(f"Registered subagent tool is missing a name: {tool!r}")


def subagent_env_prefix(subagent_name: str) -> str:
    chars = [
        char.upper() if char.isalnum() else "_"
        for char in subagent_name
    ]
    collapsed = "_".join(part for part in "".join(chars).split("_") if part)
    return f"SUBAGENT_{collapsed}"
