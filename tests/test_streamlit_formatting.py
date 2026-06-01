from agent_runtime.ui.streamlit.formatting import format_count, format_result_option
from agent_runtime.ui.streamlit.resources import (
    format_reload_summary,
    reload_summary_changed,
)
from agent_runtime.ui.streamlit.results import RuntimeConfig
from agent_runtime.ui.streamlit.sidebar import SidebarConfig
from agent_runtime.ui.streamlit.events import (
    event_payload,
    extract_detail,
    format_event_line,
    format_trace_for_storage,
    should_show_live_event,
)
from agent_runtime.ui.streamlit.export import (
    build_session_html,
    build_session_markdown,
)


def test_streamlit_formatting_helpers_do_not_require_streamlit_runtime() -> None:
    assert format_count(1200) == "1,200"
    assert format_count(None) == "-"
    assert format_result_option(
        "res_1",
        [{"result_id": "res_1", "row_count": 3}],
    ) == "res_1 · 3 stored rows"


def test_streamlit_event_helpers_format_nested_runtime_events() -> None:
    event = {
        "kind": "subagent_trace",
        "payload": {
            "stage": "execute",
            "output": {
                "row_count": 2,
                "sample_rows": [{"count": 2}],
                "result_id": "res_123",
            },
        },
    }

    assert event_payload(event)["stage"] == "execute"
    assert extract_detail(event) == "✅ count=**2** · `res_123`"
    assert format_event_line(event) == "▶️ 执行查询 — ✅ count=**2** · `res_123`"
    assert format_trace_for_storage([event]) == "▶️ 执行查询 — ✅ count=**2** · `res_123`"


def test_streamlit_event_helpers_format_session_template_events() -> None:
    event = {
        "kind": "session_template_started",
        "payload": {
            "stage": "session_template_started",
            "template_name": "IDC 巡检",
            "message_count": 3,
        },
    }

    assert format_event_line(event) == "📋 从模板启动 — `IDC 巡检` · 3 messages"
    assert format_trace_for_storage([event]) == "📋 从模板启动 — `IDC 巡检` · 3 messages"


def test_streamlit_event_trace_skips_non_visible_stages() -> None:
    assert format_trace_for_storage(
        [{"payload": {"stage": "sql_prompt", "title": "prompt"}}]
    ) == ""


def test_streamlit_event_trace_deduplicates_legacy_worker_and_hides_noisy_tool_events() -> None:
    events = [
        {
            "kind": "subagent_dispatch",
            "payload": {"stage": "worker_start", "subagent": "text2sql"},
        },
        {
            "kind": "worker_run",
            "payload": {"stage": "worker_start", "subagent": "text2sql"},
        },
        {
            "kind": "tool_call_start",
            "payload": {"stage": "tool_call_start", "tool_name": "execute_sql"},
        },
        {
            "kind": "tool_result",
            "payload": {"stage": "tool_result", "tool_name": "execute_sql"},
        },
        {
            "kind": "tool_call_end",
            "payload": {"stage": "tool_call_end", "tool_name": "execute_sql"},
        },
    ]

    assert should_show_live_event(events[0], set()) is True
    assert should_show_live_event(events[1], set()) is False
    assert "启动 Worker" in format_trace_for_storage(events)
    assert format_trace_for_storage(events).count("启动 Worker") == 1
    assert "工具结果" not in format_trace_for_storage(events)
    assert "工具完成" not in format_trace_for_storage(events)


def test_resource_reload_summary_reports_changed_sections() -> None:
    summary = {
        "skills": {"changed": True, "added": ["sql"], "removed": []},
        "subagents": {"changed": False},
        "domains": {"changed": False},
        "project_rules": True,
        "project_rules_source": "/tmp/AGENTS.md",
    }

    assert reload_summary_changed(summary) is True
    label = format_reload_summary(summary)
    assert "Skills changed (+1)" in label
    assert "Project rules changed: /tmp/AGENTS.md" in label


def test_session_export_includes_messages_and_events() -> None:
    markdown = build_session_markdown(
        session_id="session-1",
        messages=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ],
        event_runs=[
            {
                "label": "run",
                "events": [
                    {
                        "kind": "tool_result",
                        "payload": {
                            "stage": "tool_result",
                            "tool_name": "execute_sql",
                            "status": "completed",
                        },
                    }
                ],
            }
        ],
    )
    html = build_session_html(
        session_id="session-1",
        messages=[{"role": "user", "content": "<hello>"}],
        event_runs=[],
    )

    assert "Session ID: `session-1`" in markdown
    assert "### User" in markdown
    assert "`tool_result` `tool_result` execute_sql" in markdown
    assert "&lt;hello&gt;" in html
    assert "<h1>AgentWeave Session</h1>" in html
    assert "<li>Session ID: <code>session-1</code></li>" in html


def test_session_html_renders_markdown_structure_and_escapes_content() -> None:
    html = build_session_html(
        session_id="session-1",
        messages=[
            {"role": "user", "content": "请看 `code` 和 <script>alert(1)</script>"},
        ],
        event_runs=[],
    )

    assert "<h1>AgentWeave Session</h1>" in html
    assert "<h2>Conversation</h2>" in html
    assert "<h3>User</h3>" in html
    assert "<code>code</code>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_streamlit_ui_config_dataclasses_are_plain_values() -> None:
    runtime_config = RuntimeConfig(
        base_url="http://orchestrator",
        model_name="model",
        api_key="key",
        max_tokens=1024,
        sql_base_url="http://sql",
        sql_model_name="sql",
        sql_max_tokens=512,
        embedding_base_url="http://embedding",
        embedding_model_name="embedding",
        memory_enabled=True,
    )
    sidebar_config = SidebarConfig(
        max_turns=10,
        memory_enabled=True,
        clear_memory_requested=False,
        reload_resources_requested=False,
        fork_session_requested=False,
        base_url=runtime_config.base_url,
        model_name=runtime_config.model_name,
        max_output_tokens=runtime_config.max_tokens,
        sql_base_url=runtime_config.sql_base_url,
        sql_model_name=runtime_config.sql_model_name,
        sql_max_output_tokens=runtime_config.sql_max_tokens,
        embedding_base_url=runtime_config.embedding_base_url,
        embedding_model_name=runtime_config.embedding_model_name,
        api_key=runtime_config.api_key,
    )

    assert sidebar_config.base_url == runtime_config.base_url
    assert sidebar_config.sql_max_output_tokens == runtime_config.sql_max_tokens
