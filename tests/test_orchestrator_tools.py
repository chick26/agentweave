import asyncio
import json
from pathlib import Path

from agents.tool_context import ToolContext

from agent_runtime.core.context import OrchestratorContext, RunContext
from agent_runtime.storage.database import CsvSQLiteBackend
from agent_runtime.memory.memory_manager import TodoItem
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.prompts import SYSTEM_PROMPT
from agent_runtime.core.settings import load_database_backend


def test_orchestrator_exposes_only_runtime_tools():
    runtime = AgentRuntime(
        backend=load_database_backend(Path(".")),
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=Path("/tmp/test_orchestrator_tools.sqlite"),
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
    )

    tool_names = {tool.name for tool in runtime._build_tools()}

    assert {
        "get_current_time",
        "memory_search",
        "memory_write",
        "load_skill",
        "update_todo",
        "text2sql",
    } <= tool_names
    assert "run_skill" not in tool_names
    assert "data_analysis" not in tool_names
    assert "search_values" not in tool_names
    assert "generate_sql" not in tool_names
    assert "execute_sql" not in tool_names

    text2sql = next(tool for tool in runtime._build_tools() if tool.name == "text2sql")
    assert set(text2sql.params_json_schema["properties"]) == {"task"}
    assert getattr(text2sql, "_is_agent_tool") is True
    assert getattr(text2sql, "_agent_instance").name == "text2sql-worker"
    assert "domain_hint" not in text2sql.description


def test_orchestrator_hides_memory_surface_when_disabled(tmp_path):
    runtime = AgentRuntime(
        backend=load_database_backend(Path(".")),
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
        memory_enabled=False,
    )
    runtime.memory_store.write("project", "metric_rule", "不要注入这条记忆。")

    tool_names = {tool.name for tool in runtime._build_tools()}
    instructions = runtime._build_instructions("abc")

    assert "memory_search" not in tool_names
    assert "memory_write" not in tool_names
    assert "memory_search" not in instructions
    assert "<memory_policy>" not in instructions
    assert "不要注入这条记忆。" not in instructions


def test_runtime_clear_memory_clears_persisted_memory(tmp_path):
    runtime = AgentRuntime(
        backend=load_database_backend(Path(".")),
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
    )
    memory_id = runtime.memory_store.write("project", "metric_rule", "按柜数统计。")
    runtime.memory_store.upsert_vector(
        memory_id=memory_id,
        namespace="project",
        embedding_model="fake",
        content_hash="hash",
        vector=[1.0, 0.0],
    )

    runtime.clear_memory()

    assert runtime.memory_store.load_namespace("project") == []
    assert runtime.memory_store.load_vectors(embedding_model="fake") == []


def test_skill_agent_tool_invocation_creates_isolated_worker_contexts(tmp_path, monkeypatch):
    csv_path = tmp_path / "resources.csv"
    csv_path.write_text(
        "machine_room,cabinet_business_status\n"
        "403,Available\n",
        encoding="utf-8",
    )
    backend = CsvSQLiteBackend({"resources": csv_path})
    runtime = AgentRuntime(
        backend=backend,
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
    )
    captured = {"contexts": [], "sessions": [], "inputs": []}

    class FakeRunResult:
        final_output = {
            "answer": "ok",
            "skill": "text2sql",
            "domain": "idc_resources",
            "sql": "SELECT 1",
            "rows": [],
            "trace": [],
            "error": "",
        }

    async def fake_runner_run(agent, input, **kwargs):
        captured["inputs"].append(input)
        captured["contexts"].append(kwargs["context"])
        captured["sessions"].append(kwargs["session"])
        return FakeRunResult()

    monkeypatch.setattr("agent_runtime.core.skill_runner.Runner.run", fake_runner_run)

    tool = next(tool for tool in runtime._build_tools() if tool.name == "text2sql")
    orchestrator_context = OrchestratorContext(
        session_id="abc",
        backend=backend,
        model_profiles=runtime.model_profiles,
        result_store=runtime.result_store,
    )

    first_output = asyncio.run(
        tool.on_invoke_tool(
            ToolContext(
                context=orchestrator_context,
                tool_name="text2sql",
                tool_call_id="call_1",
                tool_arguments=json.dumps({"task": "first task"}),
            ),
            json.dumps({"task": "first task"}),
        )
    )
    second_output = asyncio.run(
        tool.on_invoke_tool(
            ToolContext(
                context=orchestrator_context,
                tool_name="text2sql",
                tool_call_id="call_2",
                tool_arguments=json.dumps({"task": "second task"}),
            ),
            json.dumps({"task": "second task"}),
        )
    )

    assert json.loads(first_output) == {
        "answer": "ok",
        "error": "",
        "subagent": "text2sql",
        "domain": "idc_resources",
        "sql": "SELECT 1",
        "result_id": "",
        "row_count": 0,
        "truncated": False,
        "sample_rows": [],
    }
    assert json.loads(second_output)["answer"] == "ok"
    assert captured["inputs"] == ["first task", "second task"]
    assert all(isinstance(ctx, RunContext) for ctx in captured["contexts"])
    assert captured["contexts"][0] is not captured["contexts"][1]
    assert captured["contexts"][0].run_id != captured["contexts"][1].run_id
    assert captured["sessions"][0] is not captured["sessions"][1]


def test_load_skill_returns_skill_body(tmp_path):
    runtime = AgentRuntime(
        backend=load_database_backend(Path(".")),
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
    )
    tool = next(tool for tool in runtime._build_tools() if tool.name == "load_skill")
    orchestrator_context = OrchestratorContext(
        session_id="abc",
        backend=runtime.backend,
        model_profiles=runtime.model_profiles,
        result_store=runtime.result_store,
    )

    output = asyncio.run(
        tool.on_invoke_tool(
            ToolContext(
                context=orchestrator_context,
                tool_name="load_skill",
                tool_call_id="call_load_skill",
                tool_arguments=json.dumps({"skill_name": "data_analysis"}),
            ),
            json.dumps({"skill_name": "data_analysis"}),
        )
    )
    payload = json.loads(output)

    assert payload["name"] == "data_analysis"
    assert "Data Analysis Skill" in payload["body"]
    assert any(event["kind"] == "skill_event" for event in orchestrator_context.events)
    tool_events = [
        event for event in orchestrator_context.events
        if event["kind"] in {"tool_call_start", "tool_result", "tool_call_end"}
    ]
    assert [event["kind"] for event in tool_events] == [
        "tool_call_start",
        "tool_result",
        "tool_call_end",
    ]
    assert tool_events[-1]["payload"]["tool_name"] == "load_skill"


def test_prompt_no_hardcoded_skills():
    assert "text2sql" not in SYSTEM_PROMPT
    assert "IDC" not in SYSTEM_PROMPT
    assert "海缆" not in SYSTEM_PROMPT


def test_prompt_keeps_delegation_compact():
    assert "任务描述必须自包含" in SYSTEM_PROMPT
    assert "只传递用户原文和你已确认的事实" in SYSTEM_PROMPT
    assert "专业查询或数据处理默认委派" in SYSTEM_PROMPT
    assert "不能看到或调用 subagent 的内部工具" not in SYSTEM_PROMPT
    assert "<subagent_delegation>" not in SYSTEM_PROMPT


def test_memory_injection(tmp_path):
    runtime = AgentRuntime(
        backend=load_database_backend(Path(".")),
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
    )
    runtime.memory_manager.write("project", "metric_rule", "可用资源默认按柜数统计。")
    runtime.memory_manager.write(
        "session:abc",
        "conversation_summary",
        "用户刚刚确认查询香港资源。",
        source="compressor",
    )

    memory_context = runtime._build_memory_context("abc")
    instructions = runtime._build_instructions("abc")

    assert "[project]" in memory_context
    assert "可用资源默认按柜数统计。" in memory_context
    assert "[session_summary]" in memory_context
    assert "用户刚刚确认查询香港资源。" in instructions


def test_todo_context_injected_into_instructions(tmp_path):
    runtime = AgentRuntime(
        backend=load_database_backend(Path(".")),
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
    )
    runtime.memory_manager.update_todo(
        "abc",
        [
            TodoItem("执行 Text2SQL 查询", "in_progress")
        ],
    )

    instructions = runtime._build_instructions("abc")

    assert "[todo_working_memory]" in instructions
    assert "[in_progress] 执行 Text2SQL 查询" in instructions


def test_skills_section_includes_execution_mode():
    runtime = AgentRuntime(
        backend=load_database_backend(Path(".")),
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=Path("/tmp/test_orchestrator_tools_skills.sqlite"),
        sql_base_url="http://example.test/sql/v1",
        sql_model_name="sql",
    )
    skills_section = runtime._build_skills_section()
    assert "<subagents_routing>" in skills_section
    assert 'name="text2sql"' in skills_section
    assert 'execution_mode="isolated subagent"' in skills_section
    assert "<skills_catalog>" in skills_section
    assert 'name="data_analysis"' in skills_section
