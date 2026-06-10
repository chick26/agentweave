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
from agent_runtime.worker.subagent_extensions import (
    load_subagent_extension,
    resolve_extension_capabilities,
)
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.memory.memory_store import MemoryStore
from agent_runtime.registry.skill_registry import AgentRegistry


def _registry() -> AgentRegistry:
    return AgentRegistry(subagents_root=Path("subagents"))


def _model_profile(
    *,
    base_url: str = "http://example.test/v1",
    model_name: str = "chat",
    extra_body: dict | None = None,
) -> ModelProfile:
    return ModelProfile(
        base_url=base_url,
        model_name=model_name,
        api_key="not-needed",
        max_tokens=128,
        extra_body=extra_body or {},
    )


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
        model_profile=_model_profile(model_name="chat"),
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
    assert "model_role" not in dispatch_payload
    assert dispatch_payload["model"] == "chat"


def test_tool_registry_enable_disable(monkeypatch):
    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    manifest = registry.get("text2sql")

    assert {tool.name for tool in runner._build_subagent_tools(manifest)} == {
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
        "from agent_runtime.subagent_api import ResultArtifactSpec, tool\n\n"
        "class DemoFormatter:\n"
        "    artifact_type = 'demo_artifact'\n"
        "    def format(self, payload):\n"
        "        return ResultArtifactSpec(artifact_type=self.artifact_type, rows=[payload])\n\n"
        "@tool\n"
        "async def extension_tool(value: str) -> str:\n"
        "    return value\n\n"
        "def build_prompt_context(manifest):\n"
        "    return {'extra_context': 'from extension'}\n\n"
        "def resolve_capabilities(manifest):\n"
        "    return {'capabilities': manifest.capabilities, 'policy': manifest.policies.get('demo', {})}\n\n"
        "def register(api):\n"
        "    api.tool(extension_tool, capability='demo.capability')\n"
        "    api.prompt_context(build_prompt_context)\n"
        "    api.result_formatter(DemoFormatter())\n"
        "    api.capability_resolver(resolve_capabilities)\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: extension_worker\n"
        "description: Extension worker subagent.\n"
        "execution:\n"
        "  mode: worker\n"
        "extension:\n"
        "  module: subagents.extension_worker.extension\n"
        "capabilities:\n"
        "  - demo.capability\n"
        "policies:\n"
        "  demo:\n"
        "    enabled: true\n",
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
    extension = load_subagent_extension(manifest)
    assert extension is not None
    assert extension.result_formatters[0].artifact_type == "demo_artifact"
    assert resolve_extension_capabilities(manifest) == {
        "capabilities": ["demo.capability"],
        "policy": {"enabled": True},
    }


def test_extension_tool_policy_metadata_is_registered(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "policy_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text(
        "async def scoped_tool(value: str) -> dict:\n"
        "    return {'value': value, 'error': ''}\n\n"
        "def register(api):\n"
        "    api.tool(scoped_tool, capability='demo.query', policy_path='demo', audit_name='demo.audit')\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: policy_worker\n"
        "description: Policy worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "extension:\n"
        "  module: subagents.policy_worker.extension\n"
        "capabilities:\n"
        "  - demo.query\n"
        "policies:\n"
        "  demo:\n"
        "    max_rows: 2\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Policy worker prompt.\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = AgentRegistry(subagents_root=subagents_root)
    manifest = registry.get("policy_worker")

    extension = load_subagent_extension(manifest)

    assert extension.tool_policies["scoped_tool"].capability == "demo.query"
    assert extension.tool_policies["scoped_tool"].policy_path == "demo"
    assert extension.tool_policies["scoped_tool"].audit_name == "demo.audit"
    runner = SubagentRunner(registry=registry, root=tmp_path)
    tool = runner._build_subagent_tools(manifest)[0]
    context = RuntimeContext(
        run_id="policy-run",
        session_id="policy-session",
        model_profile=_model_profile(),
        agent_registry=registry,
        active_subagent="policy_worker",
    )

    asyncio.run(
        tool.on_invoke_tool(
            ToolContext(
                context=context,
                tool_name="scoped_tool",
                tool_call_id="call_scoped",
                tool_arguments=json.dumps({"value": "hello"}),
            ),
            json.dumps({"value": "hello"}),
        )
    )

    start_payload = context.events[0]["payload"]
    result_payload = context.events[1]["payload"]
    assert start_payload["capability"] == "demo.query"
    assert start_payload["policy_path"] == "demo"
    assert start_payload["policy_snapshot"] == {"max_rows": 2}
    assert result_payload["metadata"]["audit_name"] == "demo.audit"


def test_extension_tool_policy_rejects_unknown_capability(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "bad_capability_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text(
        "async def scoped_tool() -> dict:\n"
        "    return {'error': ''}\n\n"
        "def register(api):\n"
        "    api.tool(scoped_tool, capability='missing.capability')\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: bad_capability_worker\n"
        "description: Bad worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "extension:\n"
        "  module: subagents.bad_capability_worker.extension\n"
        "capabilities:\n"
        "  - demo.query\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Bad worker prompt.\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ValueError, match="unknown capability"):
        load_subagent_extension(AgentRegistry(subagents_root=subagents_root).get("bad_capability_worker"))


def test_extension_tool_policy_requires_capability(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "missing_capability_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text(
        "async def missing_capability_tool() -> dict:\n"
        "    return {'error': ''}\n\n"
        "def register(api):\n"
        "    api.tool(missing_capability_tool)\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: missing_capability_worker\n"
        "description: Missing capability worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "extension:\n"
        "  module: subagents.missing_capability_worker.extension\n"
        "capabilities:\n"
        "  - demo.query\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Missing capability prompt.\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ValueError, match="must declare a capability"):
        load_subagent_extension(
            AgentRegistry(subagents_root=subagents_root).get("missing_capability_worker")
        )


def test_extension_tool_policy_rejects_unknown_policy_path(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "bad_policy_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text(
        "async def scoped_tool() -> dict:\n"
        "    return {'error': ''}\n\n"
        "def register(api):\n"
        "    api.tool(scoped_tool, capability='demo.query', policy_path='missing')\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: bad_policy_worker\n"
        "description: Bad policy worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "extension:\n"
        "  module: subagents.bad_policy_worker.extension\n"
        "capabilities:\n"
        "  - demo.query\n"
        "policies:\n"
        "  demo:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (subagent_dir / "prompt.md").write_text("Bad policy worker prompt.\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ValueError, match="Unknown policy path"):
        load_subagent_extension(AgentRegistry(subagents_root=subagents_root).get("bad_policy_worker"))


def test_raw_extension_tool_gets_standard_events(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "raw_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text(
        "async def echo(value: str) -> dict:\n"
        "    return {'echo': value, 'error': ''}\n\n"
        "def register(api):\n"
        "    api.tool(echo, capability='demo.echo')\n",
        encoding="utf-8",
    )
    (subagent_dir / "AGENT.yaml").write_text(
        "name: raw_worker\n"
        "description: Raw callable worker.\n"
        "execution:\n"
        "  mode: worker\n"
        "extension:\n"
        "  module: subagents.raw_worker.extension\n"
        "capabilities:\n"
        "  - demo.echo\n",
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
        model_profile=_model_profile(),
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


def test_worker_uses_single_runtime_model_profile(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "model_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "extension.py").write_text("def register(api):\n    return None\n", encoding="utf-8")
    (subagent_dir / "AGENT.yaml").write_text(
        "name: model_worker\n"
        "description: Model override worker.\n"
        "execution:\n"
        "  mode: worker\n"
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
        model_profile=_model_profile(
            base_url="http://chat/v1",
            model_name="chat-model",
            extra_body={"top_p": 0.9},
        ),
    )

    asyncio.run(
        runner.run_subagent(
            subagent_name="model_worker",
            task="hello",
            orchestrator_context=context,
        )
    )

    assert str(captured["agent"].model.model) == "chat-model"
    assert captured["agent"].model_settings.extra_body == {"top_p": 0.9}


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
        model_profile=_model_profile(),
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
