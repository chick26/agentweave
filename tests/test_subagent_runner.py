"""Tests for worker subagent assembly and result normalization."""

import asyncio
import json
from pathlib import Path

import pytest
from agents.tool_context import ToolContext

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.model_profiles import ModelProfile
from agent_runtime.core.runtime_utils import LoggingOpenAIChatCompletionsModel
from agent_runtime.worker.subagent_runner import (
    SubagentResult,
    SubagentRunner,
    WORKER_MAX_TURNS,
    _coerce_subagent_result,
    _subagent_tool_payload,
)
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.memory.memory_store import MemoryStore
from agent_runtime.registry.skill_registry import AgentRegistry


def _registry() -> AgentRegistry:
    return AgentRegistry(subagents_root=Path("subagents"))


def test_text2sql_worker_uses_sdk_runner_with_isolated_context(tmp_path, monkeypatch):
    captured = {}

    class FakeRunResult:
        final_output = {
            "answer": "ok",
            "subagent": "text2sql",
            "domain": "idc_resources",
            "sql": "SELECT 1",
            "rows": [{"value": 1}],
            "trace": [],
            "error": "",
        }

    async def fake_runner_run(agent, input, **kwargs):
        captured["agent"] = agent
        captured["input"] = input
        captured["context"] = kwargs["context"]
        captured["session"] = kwargs["session"]
        captured["max_turns"] = kwargs["max_turns"]
        return FakeRunResult()

    monkeypatch.setattr("agent_runtime.worker.subagent_runner.Runner.run", fake_runner_run)

    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    context = RuntimeContext(
        run_id="test",
        session_id="test",
        model_profiles={
            "orchestrator": ModelProfile(
                role="orchestrator",
                base_url="http://example.test/orchestrator/v1",
                model_name="orchestrator",
                api_key="not-needed",
                max_tokens=128,
            ),
            "executor": ModelProfile(
                role="executor",
                base_url="http://example.test/v1",
                model_name="sql",
                api_key="not-needed",
                max_tokens=128,
            ),
        },
        state={"tenant": "root"},
    )

    result = asyncio.run(
        runner.run_subagent(
            subagent_name="text2sql",
            task="403机房有多少可用机柜？",
            orchestrator_context=context,
        )
    )

    assert result.answer == "ok"
    assert result.extras["domain"] == "idc_resources"
    assert captured["input"] == "403机房有多少可用机柜？"
    assert captured["max_turns"] == WORKER_MAX_TURNS
    assert isinstance(captured["context"], RuntimeContext)
    assert captured["context"].run_id.startswith("text2sql-")
    assert captured["context"].state["tenant"] == "root"
    captured["context"].state["tenant"] = "worker"
    assert context.state["tenant"] == "root"
    assert isinstance(captured["agent"].model, LoggingOpenAIChatCompletionsModel)
    assert {tool.name for tool in captured["agent"].tools} == {
        "get_current_time",
        "list_domains",
        "get_domain_schema",
        "search_domain_values",
        "generate_readonly_sql",
        "execute_sql",
    }
    assert [
        event["payload"]["stage"]
        for event in context.events
        if event["kind"] in {"subagent_dispatch", "subagent_complete"}
    ] == [
        "worker_start",
        "worker_complete",
    ]
    dispatch_payload = next(
        event["payload"]
        for event in context.events
        if event["kind"] == "subagent_dispatch"
    )
    assert dispatch_payload["model_role"] == "orchestrator"
    assert dispatch_payload["model"] == "orchestrator"


def test_tool_registry_enable_disable(monkeypatch):
    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    manifest = registry.get("text2sql")

    assert {tool.name for tool in runner._build_subagent_tools(manifest)} == {
        "get_current_time",
        "list_domains",
        "get_domain_schema",
        "search_domain_values",
        "generate_readonly_sql",
        "execute_sql",
    }

    monkeypatch.setenv("SUBAGENT_TEXT2SQL_ENABLED", "0")

    assert runner._build_subagent_tools(manifest) == []


def test_subagent_manifest_tools_field_is_rejected(tmp_path):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "fake_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "AGENT.yaml").write_text(
        "name: fake_worker\n"
        "description: Fake worker subagent.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "  max_turns: 3\n"
        "  timeout_seconds: 7.5\n"
        "tools:\n"
        "  - echo_tool\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Fake worker prompt.\n", encoding="utf-8")
    registry = AgentRegistry(subagents_root=subagents_root)

    with pytest.raises(ValueError, match="declares legacy tools"):
        registry.discover()


def test_worker_subagent_loads_extension_tools_from_public_subagent_api(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "extension_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text(
        "from agent_runtime.subagent_api import tool\n\n"
        "@tool\n"
        "async def extension_tool(value: str) -> str:\n"
        "    return value\n\n"
        "def build_prompt_context(manifest):\n"
        "    return {'extra_context': 'from extension'}\n\n"
        "def register(api):\n"
        "    api.tool(extension_tool)\n"
        "    api.prompt_context(build_prompt_context)\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: extension_worker\n"
        "description: Extension worker subagent.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "extension:\n"
        "  module: subagents.extension_worker.extension\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text(
        "Extension worker prompt: {extra_context}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = AgentRegistry(subagents_root=subagents_root)
    runner = SubagentRunner(registry=registry, root=tmp_path)
    manifest = registry.get("extension_worker")

    tools = runner._build_subagent_tools(manifest)

    assert [tool.name for tool in tools] == ["extension_tool"]
    assert "from extension" in runner._build_worker_prompt(manifest)


def test_raw_extension_tool_gets_standard_events(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "raw_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text(
        "async def echo(value: str) -> dict:\n"
        "    return {'echo': value, 'error': ''}\n\n"
        "def register(api):\n"
        "    api.tool(echo)\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: raw_worker\n"
        "description: Raw callable worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "extension:\n"
        "  module: subagents.raw_worker.extension\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Raw worker prompt.\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = AgentRegistry(subagents_root=subagents_root)
    runner = SubagentRunner(registry=registry, root=tmp_path)
    tool = runner._build_subagent_tools(registry.get("raw_worker"))[0]
    context = RuntimeContext(
        run_id="raw-run",
        session_id="raw-session",
        model_profiles={
            "orchestrator": ModelProfile(
                role="orchestrator",
                base_url="http://example.test/v1",
                model_name="orchestrator",
                api_key="not-needed",
                max_tokens=128,
            )
        },
    )

    output = asyncio.run(
        tool.on_invoke_tool(
            ToolContext(
                context=context,
                tool_name="echo",
                tool_call_id="call_echo",
                tool_arguments=json.dumps({"value": "hello"}),
            ),
            json.dumps({"value": "hello"}),
        )
    )

    assert output == {"echo": "hello", "error": ""}
    assert [event["kind"] for event in context.events] == [
        "tool_call_start",
        "tool_result",
        "tool_call_end",
    ]
    assert context.events[1]["payload"]["metadata"]["tool_name"] == "echo"


def test_subagent_model_role_defaults_to_manifest_and_allows_env_override(monkeypatch):
    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    manifest = registry.get("text2sql")

    assert runner.resolve_model_role(manifest) == "orchestrator"

    monkeypatch.setenv("SUBAGENT_TEXT2SQL_MODEL_ROLE", "executor")
    assert runner.resolve_model_role(manifest) == "executor"


def test_worker_uses_manifest_model_overrides(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "model_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text("def register(api):\n    return None\n", encoding="utf-8")
    (subagent_dir / "AGENT.yaml").write_text(
        "name: model_worker\n"
        "description: Model override worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: executor\n"
        "model:\n"
        "  llm: manifest-chat\n"
        "  extra_body:\n"
        "    temperature: 0\n"
        "extension:\n"
        "  module: subagents.model_worker.extension\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Model worker prompt.\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    captured = {}

    class FakeRunResult:
        final_output = {"answer": "ok", "subagent": "model_worker", "trace": [], "error": ""}

    async def fake_runner_run(agent, input, **kwargs):
        captured["agent"] = agent
        return FakeRunResult()

    monkeypatch.setattr("agent_runtime.worker.subagent_runner.Runner.run", fake_runner_run)
    registry = AgentRegistry(subagents_root=subagents_root)
    runner = SubagentRunner(registry=registry, root=tmp_path)
    context = RuntimeContext(
        run_id="test",
        session_id="test",
        model_profiles={
            "executor": ModelProfile(
                role="executor",
                base_url="http://executor/v1",
                model_name="executor-chat",
                api_key="not-needed",
                max_tokens=128,
                extra_body={"top_p": 0.9},
            )
        },
    )

    asyncio.run(
        runner.run_subagent(
            subagent_name="model_worker",
            task="hello",
            orchestrator_context=context,
        )
    )

    assert str(captured["agent"].model.model) == "manifest-chat"
    assert captured["agent"].model_settings.extra_body == {
        "top_p": 0.9,
        "temperature": 0,
    }


def test_worker_prompt_template_replaces_domains_and_memory(tmp_path):
    registry = _registry()
    memory_store = MemoryStore(tmp_path / "agent_memory.sqlite")
    memory_manager = MemoryManager(memory_store)
    memory_store.write("project", "answer_style", "回答时保留 SQL 口径。")
    runner = SubagentRunner(
        registry=registry,
        memory_manager=memory_manager,
        root=Path("."),
    )

    prompt = runner._build_worker_prompt(registry.get("text2sql"))

    assert "{domains}" not in prompt
    assert "{memory}" not in prompt
    assert "<domains>" in prompt
    assert "idc_resources" in prompt
    assert "回答时保留 SQL 口径。" in prompt
    assert "domain_hint" not in prompt


def test_worker_prompt_can_use_extension_context(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "context_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "AGENT.yaml").write_text(
        "name: context_worker\n"
        "description: Context worker subagent.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "extension:\n"
        "  module: subagents.context_worker.extension\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Context says: {context_value}\n", encoding="utf-8")
    (subagent_dir / "extension.py").write_text(
        "def build_prompt_context(manifest):\n"
        "    return {'context_value': manifest.name}\n\n"
        "def register(api):\n"
        "    api.prompt_context(build_prompt_context)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = AgentRegistry(subagents_root=subagents_root)
    runner = SubagentRunner(registry=registry, root=tmp_path)

    prompt = runner._build_worker_prompt(registry.get("context_worker"))

    assert prompt == "Context says: context_worker"


def test_worker_reads_only_declared_skill_memory(tmp_path):
    registry = _registry()
    memory_store = MemoryStore(tmp_path / "agent_memory.sqlite")
    memory_manager = MemoryManager(memory_store)
    memory_manager.write("project", "metric_rule", "保留项目级 SQL 口径。")
    memory_manager.write("skill:text2sql", "search_rule", "实体值必须先搜索候选。")
    memory_manager.write("user", "private_pref", "不要注入 worker。")
    runner = SubagentRunner(
        registry=registry,
        memory_manager=memory_manager,
        root=Path("."),
    )

    prompt = runner._build_worker_prompt(registry.get("text2sql"))

    assert "保留项目级 SQL 口径。" in prompt
    assert "实体值必须先搜索候选。" in prompt
    assert "不要注入 worker。" not in prompt


def test_subagent_result_coercion_normalizes_string_trace_items():
    result = _coerce_subagent_result(
        {
            "answer": "403机房没有可用机柜。",
            "subagent": "text2sql",
            "domain": "idc_resources",
            "sql": "SELECT COUNT(*) AS count FROM resources",
            "rows": [{"count": 0}],
            "trace": [
                "activate_domain: idc_resources",
                "execute_sql: COUNT -> 0",
            ],
            "error": "",
        },
        "text2sql",
    )

    assert result.answer == "403机房没有可用机柜。"
    assert result.error == ""
    assert result.trace == [
        {"stage": "note", "message": "activate_domain: idc_resources"},
        {"stage": "note", "message": "execute_sql: COUNT -> 0"},
    ]


def test_text2sql_worker_timeout_returns_standard_error(tmp_path, monkeypatch):
    async def fake_runner_run(agent, input, **kwargs):
        run_ctx = kwargs["context"]
        run_ctx.state["active_domain"] = "sea_cable_faults"
        run_ctx.emit_subagent_trace(
            {
                "stage": "execute",
                "title": "执行查询",
                "input": "SELECT * FROM sea_cable_faults",
                "output": {
                    "sql": "SELECT * FROM sea_cable_faults",
                    "result_id": "res_timeout",
                    "row_count": 1,
                    "columns": ["sea_cable_no", "pop_fault_seg"],
                    "sample_rows": [{"sea_cable_no": "NCP", "pop_fault_seg": "S1"}],
                    "sample_size": 1,
                    "truncated": False,
                    "error": None,
                },
            }
        )
        await asyncio.sleep(1)

    monkeypatch.setattr("agent_runtime.worker.subagent_runner.Runner.run", fake_runner_run)
    monkeypatch.setattr("agent_runtime.worker.subagent_runner.WORKER_TIMEOUT_SECONDS", 0.01)
    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    context = RuntimeContext(
        run_id="test",
        session_id="test",
        model_profiles={
            "orchestrator": ModelProfile(
                role="orchestrator",
                base_url="http://example.test/orchestrator/v1",
                model_name="orchestrator",
                api_key="not-needed",
                max_tokens=128,
            ),
            "executor": ModelProfile(
                role="executor",
                base_url="http://example.test/v1",
                model_name="sql",
                api_key="not-needed",
                max_tokens=128,
            ),
        },
    )

    result = asyncio.run(
        runner.run_subagent(
            subagent_name="text2sql",
            task="查 NCP",
            orchestrator_context=context,
        )
    )

    assert result.answer == ""
    assert result.error.startswith("worker_timeout:")
    assert result.artifacts == []
    assert result.trace[-1]["stage"] == "execute"


def test_subagent_result_coercion_keeps_unknown_fields_in_extras():
    result = _coerce_subagent_result(
        {
            "answer": "ok",
            "subagent": "text2sql",
            "domain": "idc_resources",
            "trace": [],
        },
        "text2sql",
    )

    assert result.extras["domain"] == "idc_resources"
    assert result.subagent == "text2sql"


def test_subagent_tool_payload_keeps_structure_when_answer_is_present():
    payload = _subagent_tool_payload(
        SubagentResult(
            answer="查询完成。",
            subagent="text2sql",
            artifacts=[
                {
                    "type": "sql_result",
                    "result_id": "res_123",
                    "preview": [{"count": 1}],
                    "metadata": {
                        "domain": "idc_resources",
                        "sql": "SELECT 1",
                        "row_count": 1,
                        "truncated": False,
                    },
                }
            ],
            error="",
        )
    )

    assert json.loads(json.dumps(payload, ensure_ascii=False)) == {
        "answer": "查询完成。",
        "error": "",
        "subagent": "text2sql",
        "artifacts": [
            {
                "type": "sql_result",
                "result_id": "res_123",
                "preview": [{"count": 1}],
                "metadata": {
                    "domain": "idc_resources",
                    "sql": "SELECT 1",
                    "row_count": 1,
                    "truncated": False,
                },
            }
        ],
        "extras": {},
    }
