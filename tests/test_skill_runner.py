import asyncio
import json
from pathlib import Path

import pytest

from agent_runtime.core.context import OrchestratorContext, RunContext
from agent_runtime.core.model_profiles import ModelProfile
from agent_runtime.core.runtime_utils import LoggingOpenAIChatCompletionsModel
from agent_runtime.core.skill_runner import (
    SubagentResult,
    SubagentRunner,
    WORKER_MAX_TURNS,
    _coerce_subagent_result,
    _subagent_tool_payload,
)
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.memory.memory_store import MemoryStore
from agent_runtime.registry.skill_registry import AgentRegistry
from agent_runtime.storage.database import CsvSQLiteBackend


def _registry() -> AgentRegistry:
    return AgentRegistry(subagents_root=Path("subagents"))


def test_text2sql_worker_uses_sdk_runner_with_isolated_context(tmp_path, monkeypatch):
    csv_path = tmp_path / "resources.csv"
    csv_path.write_text(
        "machine_room,cabinet_business_status\n"
        "403,Available\n"
        "403,Sold\n",
        encoding="utf-8",
    )
    backend = CsvSQLiteBackend({"resources": csv_path})
    captured = {}

    class FakeRunResult:
        final_output = {
            "answer": "ok",
            "skill": "text2sql",
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

    monkeypatch.setattr("agent_runtime.core.skill_runner.Runner.run", fake_runner_run)

    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    context = OrchestratorContext(
        session_id="test",
        backend=backend,
        model_profiles={
            "orchestrator": ModelProfile(
                role="orchestrator",
                base_url="http://example.test/orchestrator/v1",
                model_name="orchestrator",
                api_key="not-needed",
                max_tokens=128,
            ),
            "sql_worker": ModelProfile(
                role="sql_worker",
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
            task="403机房有多少可用机柜？",
            orchestrator_context=context,
        )
    )

    assert result.answer == "ok"
    assert result.domain == "idc_resources"
    assert captured["input"] == "403机房有多少可用机柜？"
    assert captured["max_turns"] == WORKER_MAX_TURNS
    assert isinstance(captured["context"], RunContext)
    assert captured["context"].run_id.startswith("text2sql-")
    assert captured["context"].backend is backend
    assert isinstance(captured["agent"].model, LoggingOpenAIChatCompletionsModel)
    assert {tool.name for tool in captured["agent"].tools} == {
        "get_current_time",
        "plan_sql_query",
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


def test_tool_registry_enable_disable(monkeypatch):
    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    manifest = registry.get("text2sql")

    assert manifest.execution.tool_module == "subagents.text2sql.tools"
    assert manifest.execution.context_module == "subagents.text2sql.domain_registry"
    assert {tool.name for tool in runner._build_subagent_tools(manifest)} == {
        "get_current_time",
        "plan_sql_query",
        "execute_sql",
    }

    monkeypatch.setenv("SUBAGENT_TEXT2SQL_ENABLED", "0")

    assert runner._build_subagent_tools(manifest) == []


def test_worker_subagent_can_load_tools_from_manifest_without_code_registration(tmp_path, monkeypatch):
    tools_module = tmp_path / "fake_subagent_tools.py"
    tools_module.write_text(
        "from agents import function_tool\n\n"
        "@function_tool\n"
        "async def echo_tool(value: str) -> str:\n"
        "    return value\n",
        encoding="utf-8",
    )
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "fake_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "AGENT.md").write_text(
        "---\n"
        "name: fake_worker\n"
        "description: Fake worker subagent.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "  tool_module: fake_subagent_tools\n"
        "  max_turns: 3\n"
        "  timeout_seconds: 7.5\n"
        "tools:\n"
        "  - echo_tool\n"
        "---\n"
        "Fake worker prompt.\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = AgentRegistry(subagents_root=subagents_root)
    manifest = registry.get("fake_worker")
    runner = SubagentRunner(registry=registry, root=tmp_path)

    tools = runner._build_subagent_tools(manifest)
    assert [tool.name for tool in tools] == ["echo_tool"]
    assert runner._resolve_max_turns(manifest) == 3
    assert runner._resolve_timeout_seconds(manifest) == 7.5

    profile = ModelProfile(
        role="orchestrator",
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        max_tokens=128,
    )
    agent_tool = runner.build_worker_agent_tool(manifest=manifest, profile=profile)
    assert agent_tool.name == "fake_worker"
    assert getattr(agent_tool, "_is_agent_tool") is True
    assert set(agent_tool.params_json_schema["properties"]) == {"task"}


def test_generic_subagent_env_model_role_override(monkeypatch):
    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    manifest = registry.get("text2sql")

    monkeypatch.setenv("TEXT2SQL_WORKER_MODEL_ROLE", "sql_worker")
    assert runner._resolve_model_role(manifest) == "sql_worker"

    monkeypatch.setenv("SUBAGENT_TEXT2SQL_MODEL_ROLE", "orchestrator")
    assert runner._resolve_model_role(manifest) == "orchestrator"


def test_missing_tool_module_for_declared_tools_raises_clear_error(tmp_path):
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "broken_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "AGENT.md").write_text(
        "---\n"
        "name: broken_worker\n"
        "description: Broken worker subagent.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "tools:\n"
        "  - missing_tool\n"
        "---\n"
        "Broken worker prompt.\n",
        encoding="utf-8",
    )

    registry = AgentRegistry(subagents_root=subagents_root)
    runner = SubagentRunner(registry=registry, root=tmp_path)

    with pytest.raises(ValueError, match="execution.tool_module is missing"):
        runner._build_subagent_tools(registry.get("broken_worker"))


def test_declared_missing_tool_raises_clear_error(tmp_path, monkeypatch):
    tools_module = tmp_path / "partial_tools.py"
    tools_module.write_text(
        "from agents import function_tool\n\n"
        "@function_tool\n"
        "async def existing_tool(value: str) -> str:\n"
        "    return value\n",
        encoding="utf-8",
    )
    subagents_root = tmp_path / "subagents"
    subagent_dir = subagents_root / "broken_worker"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "AGENT.md").write_text(
        "---\n"
        "name: broken_worker\n"
        "description: Broken worker subagent.\n"
        "execution:\n"
        "  mode: worker\n"
        "  model_role: orchestrator\n"
        "  tool_module: partial_tools\n"
        "tools:\n"
        "  - existing_tool\n"
        "  - missing_tool\n"
        "---\n"
        "Broken worker prompt.\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    registry = AgentRegistry(subagents_root=subagents_root)
    runner = SubagentRunner(registry=registry, root=tmp_path)

    with pytest.raises(ValueError, match="declares missing tools"):
        runner._build_subagent_tools(registry.get("broken_worker"))


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
            "skill": "text2sql",
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


def test_text2sql_worker_timeout_returns_latest_execute_result(tmp_path, monkeypatch):
    csv_path = tmp_path / "sea_cable_faults.csv"
    csv_path.write_text(
        "sea_cable_no,pop_fault_seg\n"
        "NCP,S1\n",
        encoding="utf-8",
    )
    backend = CsvSQLiteBackend({"sea_cable_faults": csv_path})

    async def fake_runner_run(agent, input, **kwargs):
        run_ctx = kwargs["context"]
        run_ctx.active_domain = "sea_cable_faults"
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

    monkeypatch.setattr("agent_runtime.core.skill_runner.Runner.run", fake_runner_run)
    monkeypatch.setattr("agent_runtime.core.skill_runner.WORKER_TIMEOUT_SECONDS", 0.01)
    registry = _registry()
    runner = SubagentRunner(registry=registry, root=Path("."))
    context = OrchestratorContext(
        session_id="test",
        backend=backend,
        model_profiles={
            "orchestrator": ModelProfile(
                role="orchestrator",
                base_url="http://example.test/orchestrator/v1",
                model_name="orchestrator",
                api_key="not-needed",
                max_tokens=128,
            ),
            "sql_worker": ModelProfile(
                role="sql_worker",
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

    assert result.domain == "sea_cable_faults"
    assert result.sql == "SELECT * FROM sea_cable_faults"
    assert result.result_id == "res_timeout"
    assert result.row_count == 1
    assert result.rows == [{"sea_cable_no": "NCP", "pop_fault_seg": "S1"}]
    assert result.answer == ""
    assert result.error.startswith("worker_timeout:")


def test_subagent_result_coercion_maps_subagent_field():
    result = _coerce_subagent_result(
        {
            "answer": "ok",
            "skill": "text2sql",
            "subagent": "text2sql",
            "domain": "idc_resources",
            "trace": [],
        },
        "text2sql",
    )

    assert result.domain == "idc_resources"
    assert result.skill == "text2sql"


def test_subagent_tool_payload_keeps_structure_when_answer_is_present():
    payload = _subagent_tool_payload(
        SubagentResult(
            answer="查询完成。",
            subagent="text2sql",
            domain="idc_resources",
            sql="SELECT 1",
            result_id="res_123",
            row_count=1,
            truncated=False,
            rows=[{"count": 1}],
            error="",
        )
    )

    assert json.loads(json.dumps(payload, ensure_ascii=False)) == {
        "answer": "查询完成。",
        "error": "",
        "subagent": "text2sql",
        "domain": "idc_resources",
        "sql": "SELECT 1",
        "result_id": "res_123",
        "row_count": 1,
        "truncated": False,
        "sample_rows": [{"count": 1}],
    }
