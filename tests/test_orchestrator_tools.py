"""Tests for orchestrator tools, subagent readiness, and SQL workflows."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from agent_runtime.core.context import RuntimeContext
from agent_runtime.memory.todo_state import TodoItem
from agent_runtime.core.runtime import AgentRuntime
from agent_runtime.core.prompts import SYSTEM_PROMPT


def test_runtime_local_sqlite_stores_live_under_agentweave(tmp_path):
    (tmp_path / "subagents").mkdir()
    (tmp_path / "skills").mkdir()
    session_path = tmp_path / ".agentweave" / "streamlit_sessions.sqlite"

    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=session_path,
        memory_enabled=False,
    )

    assert runtime.root == tmp_path
    assert runtime.memory_store.path == tmp_path / ".agentweave" / "agent_memory.sqlite"
    assert runtime.result_store.path == tmp_path / ".agentweave" / "agent_results.sqlite"


def test_runtime_readiness_checks_only_selected_bot_subagents(tmp_path, monkeypatch):
    subagents_root = tmp_path / "subagents"
    bots_root = tmp_path / "bots"
    healthy_dir = subagents_root / "healthy"
    broken_dir = subagents_root / "broken"
    healthy_dir.mkdir(parents=True)
    broken_dir.mkdir(parents=True)
    (healthy_dir / "extension.py").write_text(
        "def validate(manifest):\n"
        "    return None\n\n"
        "def register(api):\n"
        "    api.validate_environment(validate)\n",
        encoding="utf-8",
    )
    (broken_dir / "extension.py").write_text(
        "def validate(manifest):\n"
        "    raise RuntimeError('missing env')\n\n"
        "def register(api):\n"
        "    api.validate_environment(validate)\n",
        encoding="utf-8",
    )
    for name in ("healthy", "broken"):
        (subagents_root / name / "AGENT.yaml").write_text(
            f"name: {name}\n"
            f"description: {name} worker\n"
            "execution:\n"
            "  mode: worker\n"
            "extension:\n"
            f"  module: subagents.{name}.extension\n",
            encoding="utf-8",
        )
        (subagents_root / name / "prompt.md").write_text("Prompt\n", encoding="utf-8")
    bot_dir = bots_root / "healthy_bot"
    bot_dir.mkdir(parents=True)
    (bot_dir / "BOT.yaml").write_text(
        "id: healthy_bot\n"
        "name: Healthy Bot\n"
        "subagents:\n"
        "  - healthy\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        validate_subagents=True,
    )

    runtime._validate_bot_subagents_readiness("healthy_bot")

    with pytest.raises(RuntimeError, match="broken.*missing env"):
        runtime._validate_bot_subagents_readiness("default")


def test_orchestrator_exposes_only_runtime_tools():
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=Path("/tmp/test_orchestrator_tools.sqlite"),
    )

    tool_names = {tool.name for tool in runtime._build_tools()}

    assert {
        "memory_search",
        "memory_write",
        "load_skill",
        "text2sql",
    } <= tool_names
    assert "run_skill" not in tool_names
    assert "data_analysis" not in tool_names
    assert "search_values" not in tool_names
    assert "generate_sql" not in tool_names
    assert "execute_sql" not in tool_names
    assert "update_todo" not in tool_names
    assert "get_current_time" not in tool_names

    text2sql = next(tool for tool in runtime._build_tools() if tool.name == "text2sql")
    assert set(text2sql.params_json_schema["properties"]) == {"task"}
    assert getattr(text2sql, "_is_agent_tool") is True
    assert getattr(text2sql, "_agent_instance").name == "text2sql-worker"
    assert "domain_hint" not in text2sql.description


def test_todo_tool_is_opt_in(monkeypatch):
    monkeypatch.setenv("AGENTWEAVE_ENABLE_TODO_TOOL", "1")
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=Path("/tmp/test_orchestrator_todo_tool.sqlite"),
        memory_enabled=False,
    )

    tool_names = {tool.name for tool in runtime._build_tools()}

    assert "update_todo" in tool_names


def test_memory_search_creates_memory_records_artifact(tmp_path):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
        memory_enabled=True,
    )
    runtime.memory_manager.write(
        namespace="project",
        key="timezone",
        content="Use Asia/Hong_Kong for runtime examples.",
        tags=["time"],
    )
    tool = next(tool for tool in runtime._build_tools() if tool.name == "memory_search")
    context = RuntimeContext(
        run_id="memory-run",
        session_id="memory-session",
        model_profile=runtime.model_profile,
        result_store=runtime.result_store,
        result_formatters=runtime.result_formatters,
    )

    output = asyncio.run(
        tool.on_invoke_tool(
            ToolContext(
                context=context,
                tool_name="memory_search",
                tool_call_id="call_memory",
                tool_arguments=json.dumps(
                    {"query": "", "namespaces": "project", "limit": 5}
                ),
            ),
            json.dumps({"query": "", "namespaces": "project", "limit": 5}),
        )
    )
    records = json.loads(output)
    result_events = [event for event in context.events if event["kind"] == "result_created"]

    assert records[0]["key"] == "timezone"
    assert result_events
    result = result_events[0]["payload"]["result"]
    assert result["artifact_type"] == "memory_records"
    assert runtime.result_store.get_page(
        result["result_id"],
        offset=0,
        limit=10,
        run_id="memory-run",
    )[0]["key"] == "timezone"


def test_orchestrator_hides_memory_surface_when_disabled(tmp_path):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
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


def test_runtime_injects_current_time_without_time_tool(tmp_path):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )

    instructions = runtime._build_instructions("abc")

    assert "get_current_time" not in {tool.name for tool in runtime._build_tools()}
    assert "<current_time>" in instructions
    assert '"timezone": "Asia/Hong_Kong"' in instructions


def test_runtime_clear_memory_clears_persisted_memory(tmp_path):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
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
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )
    captured = {"contexts": [], "sessions": [], "inputs": []}

    class FakeRunResult:
        final_output = {
            "answer": "ok",
            "subagent": "text2sql",
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

    monkeypatch.setattr("agent_runtime.worker.subagent_runner.Runner.run", fake_runner_run)

    tool = next(tool for tool in runtime._build_tools() if tool.name == "text2sql")
    orchestrator_context = RuntimeContext(
        run_id="abc",
        session_id="abc",
        model_profile=runtime.model_profile,
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
        "artifacts": [],
        "extras": {
            "domain": "idc_resources",
            "sql": "SELECT 1",
            "rows": [],
        },
    }
    assert json.loads(second_output)["answer"] == "ok"
    assert captured["inputs"] == ["first task", "second task"]
    assert all(isinstance(ctx, RuntimeContext) for ctx in captured["contexts"])
    assert captured["contexts"][0] is not captured["contexts"][1]
    assert captured["contexts"][0].run_id != captured["contexts"][1].run_id
    assert captured["sessions"][0] is not captured["sessions"][1]


def test_load_skill_returns_skill_body(tmp_path):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )
    tool = next(tool for tool in runtime._build_tools() if tool.name == "load_skill")
    orchestrator_context = RuntimeContext(
        run_id="abc",
        session_id="abc",
        model_profile=runtime.model_profile,
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
    assert "只传递用户原文、注入的当前时间和你已确认的事实" in SYSTEM_PROMPT
    assert "专业查询或数据处理默认委派" in SYSTEM_PROMPT
    assert "不能看到或调用 subagent 的内部工具" not in SYSTEM_PROMPT
    assert "<subagent_delegation>" not in SYSTEM_PROMPT


def test_memory_injection(tmp_path):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
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
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )
    runtime.todo_state.update(
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
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=Path("/tmp/test_orchestrator_tools_skills.sqlite"),
    )
    skills_section = runtime._build_skills_section()
    assert "<subagents_routing>" in skills_section
    assert 'name="text2sql"' in skills_section
    assert 'execution_mode="isolated subagent"' in skills_section
    assert "<skills_catalog>" in skills_section
    assert 'name="data_analysis"' in skills_section


def test_runtime_scopes_tools_prompt_and_load_skill_by_bot(tmp_path):
    (tmp_path / "data.csv").write_text("value\n1\n", encoding="utf-8")
    for name in ("data_analysis", "extra_skill"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\n{name} body",
            encoding="utf-8",
        )
    for name in ("text2sql", "api_call"):
        subagent_dir = tmp_path / "subagents" / name
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "AGENT.yaml").write_text(
            f"name: {name}\n"
            f"description: {name}\n"
            "execution:\n"
            "  mode: worker\n",
            encoding="utf-8",
        )
        (subagent_dir / "prompt.md").write_text(f"{name} prompt", encoding="utf-8")
    bot_dir = tmp_path / "bots" / "data_analyst"
    bot_dir.mkdir(parents=True)
    (bot_dir / "BOT.yaml").write_text(
        "id: data_analyst\n"
        "name: 数据分析机器人\n"
        "instructions: 只做数据分析。\n"
        "subagents:\n"
        "  - text2sql\n"
        "skills:\n"
        "  - data_analysis\n",
        encoding="utf-8",
    )
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )
    bot = runtime.bot_registry.get("data_analyst")

    tool_names = {tool.name for tool in runtime._build_tools(bot=bot)}
    instructions = runtime._build_instructions("abc", bot=bot)
    section = runtime._build_skills_section(bot=bot)

    assert "text2sql" in tool_names
    assert "api_call" not in tool_names
    assert 'name="text2sql"' in section
    assert 'name="api_call"' not in section
    assert 'name="data_analysis"' in section
    assert 'name="extra_skill"' not in section
    assert "只做数据分析。" in instructions

    load_skill = next(tool for tool in runtime._build_tools(bot=bot) if tool.name == "load_skill")
    context = RuntimeContext(
        run_id="abc",
        session_id="abc",
        model_profile=runtime.model_profile,
    )
    output = asyncio.run(
        load_skill.on_invoke_tool(
            ToolContext(
                context=context,
                tool_name="load_skill",
                tool_call_id="call_1",
                tool_arguments=json.dumps({"skill_name": "extra_skill"}),
            ),
            json.dumps({"skill_name": "extra_skill"}),
        )
    )

    payload = json.loads(output)
    assert "not enabled for bot `data_analyst`" in payload["error"]
    assert payload["available_skills"] == ["data_analysis"]


def test_runtime_ask_without_model_delta_uses_non_streaming_runner(tmp_path, monkeypatch):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )
    calls = {"run": 0, "run_streamed": 0}

    class FakeRunResult:
        final_output = "done"

    async def fake_run(*args, **kwargs):
        calls["run"] += 1
        return FakeRunResult()

    def fake_run_streamed(*args, **kwargs):
        calls["run_streamed"] += 1
        raise AssertionError("run_streamed should not be used")

    monkeypatch.setattr("agent_runtime.core.runtime.Runner.run", fake_run)
    monkeypatch.setattr("agent_runtime.core.runtime.Runner.run_streamed", fake_run_streamed)

    result = asyncio.run(runtime.ask("hello", "session-plain"))

    assert result["final_output"] == "done"
    assert calls == {"run": 1, "run_streamed": 0}


def test_runtime_ask_streams_only_output_text_delta(tmp_path, monkeypatch):
    runtime = AgentRuntime(
        base_url="http://example.test/v1",
        model_name="orchestrator",
        api_key="not-needed",
        session_db_path=tmp_path / "sessions.sqlite",
    )
    deltas: list[str] = []

    class FakeStreamedResult:
        final_output = "当前答案"

        async def stream_events(self):
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="当前"),
            )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.reasoning_text.delta", delta="内部"),
            )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.function_call_arguments.delta", delta="{}"),
            )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="答案"),
            )
            yield SimpleNamespace(type="run_item_stream_event", data=None)

    async def fake_run(*args, **kwargs):
        raise AssertionError("run should not be used")

    def fake_run_streamed(*args, **kwargs):
        return FakeStreamedResult()

    monkeypatch.setattr("agent_runtime.core.runtime.Runner.run", fake_run)
    monkeypatch.setattr("agent_runtime.core.runtime.Runner.run_streamed", fake_run_streamed)

    result = asyncio.run(
        runtime.ask(
            "hello",
            "session-stream",
            model_delta_callback=lambda payload: deltas.append(payload["delta"]),
        )
    )

    assert deltas == ["当前", "答案"]
    assert result["final_output"] == "当前答案"
