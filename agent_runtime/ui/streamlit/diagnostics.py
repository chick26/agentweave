from __future__ import annotations

import json
from typing import Any, Callable

import streamlit as st

from agent_runtime.common import xml_escape
from agent_runtime.ui.streamlit.events import event_payload, format_event_line
from agent_runtime.ui.streamlit.formatting import format_count


def render_model_logs(
    *,
    runs: list[dict[str, Any]],
    load_diagnostic_run: Callable[[dict[str, Any]], dict[str, Any] | None],
    format_run_option: Callable[[dict[str, Any]], str],
) -> None:
    if not runs:
        st.info("还没有模型日志。发起一次问数后，这里会显示给模型的实际输入、工具定义、模型输出和 token 用量。")
        return

    st.caption("本页显示当前 Streamlit 会话缓存；完整诊断日志会同步写入本地 SQLite，便于后续离线分析。")
    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: format_run_option(newest_first[idx]),
        key="selected_model_log_run",
    )
    selected_run = newest_first[selected_idx]
    diagnostic_run = load_diagnostic_run(selected_run)
    if diagnostic_run is None:
        return
    logs = diagnostic_run.get("model_calls", [])

    if not isinstance(logs, list) or not logs:
        st.warning("这次对话没有捕获到模型调用日志。")
        return

    canonical = [call for call in logs if isinstance(call, dict) and _has_canonical_time(call)]
    missing_time = [call for call in logs if isinstance(call, dict) and not _has_canonical_time(call)]

    for idx, call in enumerate(canonical, start=1):
        _render_model_call(call, idx, expanded=idx == 1)

    if missing_time:
        with st.expander(f"时间缺失 · {len(missing_time)}", expanded=False):
            for idx, call in enumerate(missing_time, start=1):
                _render_model_call(call, idx, expanded=False)


def render_event_runs(
    *,
    runs: list[dict[str, Any]],
    kind: str,
    empty_message: str,
    load_diagnostic_run: Callable[[dict[str, Any]], dict[str, Any] | None],
    format_run_option: Callable[[dict[str, Any]], str],
) -> None:
    if not runs:
        st.info(empty_message)
        return
    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: format_run_option(newest_first[idx]),
        key=f"selected_{kind}_run",
    )
    selected_run = newest_first[selected_idx]
    diagnostic_run = load_diagnostic_run(selected_run)
    if diagnostic_run is None:
        return
    events = [
        event for event in diagnostic_run.get("events", [])
        if isinstance(event, dict) and event.get("kind") == kind
    ]
    if not events:
        st.warning("这次对话没有对应事件。")
        return
    for idx, event in enumerate(events, start=1):
        payload = event_payload(event.get("payload", {}))
        title = payload.get("title") or payload.get("stage") or kind
        with st.expander(f"{idx}. {title}", expanded=idx == 1):
            st.json(event, expanded=False)


def render_execution_runs(
    *,
    runs: list[dict[str, Any]],
    load_diagnostic_run: Callable[[dict[str, Any]], dict[str, Any] | None],
    format_run_option: Callable[[dict[str, Any]], str],
) -> None:
    if not runs:
        st.info("还没有执行过程。发起一次对话后，这里会显示 Skill、Subagent、Memory 和 Todo 事件。")
        return
    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: format_run_option(newest_first[idx]),
        key="selected_execution_run",
    )
    selected_run = newest_first[selected_idx]
    diagnostic_run = load_diagnostic_run(selected_run)
    if diagnostic_run is None:
        return
    _render_diagnostic_overview(diagnostic_run)
    with st.expander("时间线", expanded=True):
        _render_timeline(diagnostic_run)

    event_groups = [
        ("Skill Events", ("skill_event",)),
        ("Subagent Runs", ("subagent_dispatch", "subagent_complete", "worker_run")),
        ("Subagent Trace", ("subagent_trace",)),
        ("Tool Events", ("tool_call_start", "tool_result", "tool_call_end")),
        ("Memory Events", ("memory_event", "memory_read", "memory_write")),
        ("Resource Events", ("resources_reloaded",)),
        (
            "Session Events",
            (
                "session_forked",
                "session_template_started",
                "session_template_saved",
            ),
        ),
        ("Todo Events", ("todo_event",)),
    ]
    for title, kinds in event_groups:
        events = [
            event for event in diagnostic_run.get("events", [])
            if isinstance(event, dict) and event.get("kind") in kinds
        ]
        with st.expander(f"{title} · {len(events)}", expanded=bool(events) and "worker_run" in kinds):
            if not events:
                st.caption("这次对话没有对应事件。")
                continue
            for idx, event in enumerate(events, start=1):
                raw_event = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if event.get("kind") in {"memory_event", "memory_read", "memory_write"}:
                    with st.container(border=True):
                        st.markdown(f"**{idx}. Memory Retrieval**")
                        _render_memory_event(raw_event, event)
                else:
                    st.markdown(f"{idx}. {format_event_line(raw_event)}")
                    if event.get("diagnostic_issue"):
                        st.warning(f"诊断数据缺失：`{event['diagnostic_issue']}`")
                    if event.get("kind") == "subagent_trace":
                        payload = event_payload(raw_event)
                        if payload.get("stage") == "sql_plan":
                            _render_sql_plan_event(payload)
                    st.json(event, expanded=False)


def _format_duration_ms(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric < 1000:
        return f"{numeric:.0f} ms"
    return f"{numeric / 1000:.2f} s"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metric_card(label: str, value: str, detail: str = "", tone: str = "neutral") -> str:
    class_name = f"aw-metric-card {tone}".strip()
    detail_html = (
        f'<div class="aw-metric-detail">{xml_escape(detail)}</div>'
        if detail
        else ""
    )
    return (
        f'<div class="{class_name}">'
        f'<div class="aw-metric-label">{xml_escape(label)}</div>'
        f'<div class="aw-metric-value">{xml_escape(value)}</div>'
        f"{detail_html}"
        "</div>"
    )


def _issue_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value:
        return [str(value)]
    return []


def _has_canonical_time(item: dict[str, Any]) -> bool:
    issues = _issue_list(item.get("diagnostic_issue"))
    return not any("missing_" in issue and "time" in issue for issue in issues)


def _model_call_header(call: dict[str, Any], position: int) -> str:
    title = str(call.get("title") or call.get("kind") or "模型调用")
    parts = [
        f"{position}. {title}",
        str(call.get("model") or "-"),
        _format_duration_ms(call.get("duration_ms")),
        f"tokens={format_count(call.get('total_tokens'))}",
        f"messages={format_count(call.get('message_count'))}",
        f"tools={format_count(call.get('tool_count'))}",
    ]
    if not _has_canonical_time(call):
        parts.append("time=missing")
    if call.get("diagnostic_issue"):
        parts.append(f"issue={call['diagnostic_issue']}")
    return " · ".join(parts)


def _render_model_call(call: dict[str, Any], position: int, expanded: bool = False) -> None:
    with st.expander(_model_call_header(call, position), expanded=expanded):
        created_at = call.get("created_at") or "-"
        completed_at = call.get("completed_at") or "-"
        st.markdown(
            f"`created_at={created_at}` · `completed_at={completed_at}`  \n"
            f"`prompt={format_count(call.get('prompt_tokens'))}` · "
            f"`completion={format_count(call.get('completion_tokens'))}` · "
            f"`total={format_count(call.get('total_tokens'))}`"
        )
        st.markdown(
            f"`messages={format_count(call.get('message_count'))}` · "
            f"`tools={format_count(call.get('tool_count'))}` · "
            f"`duration={_format_duration_ms(call.get('duration_ms'))}`"
        )
        if call.get("diagnostic_issue"):
            st.warning(f"诊断数据缺失：`{call['diagnostic_issue']}`")
        detail_tabs = _model_call_detail_tabs(call)
        tabs = st.tabs([label for label, _ in detail_tabs])
        for tab, (_, key) in zip(tabs, detail_tabs):
            with tab:
                if key == "raw":
                    st.json(call, expanded=False)
                else:
                    st.json(call[key], expanded=False)


def _model_call_detail_tabs(call: dict[str, Any]) -> list[tuple[str, str]]:
    tabs: list[tuple[str, str]] = []
    if call.get("request"):
        tabs.append(("实际输入", "request"))
    if call.get("response"):
        tabs.append(("实际输出", "response"))
    tabs.append(("完整 JSON", "raw"))
    return tabs


def _render_diagnostic_overview(diagnostic_run: dict[str, Any]) -> None:
    summary = diagnostic_run.get("summary") if isinstance(diagnostic_run.get("summary"), dict) else {}
    issue_count = _optional_int(summary.get("diagnostic_issue_count")) or 0
    missing_time_count = _optional_int(summary.get("missing_time_count")) or 0
    execute_count = _optional_int(summary.get("execute_count")) or 0

    cards = [
        _metric_card("总耗时", _format_duration_ms(summary.get("duration_ms")), "端到端执行时间", "primary"),
        _metric_card(
            "模型调用",
            format_count(summary.get("model_call_count")),
            f"事件 {format_count(summary.get('event_count'))}",
            "ok",
        ),
        _metric_card("总 tokens", format_count(summary.get("total_tokens")), "prompt + completion"),
        _metric_card(
            "诊断问题",
            format_count(summary.get("diagnostic_issue_count")),
            "字段缺失或时间线不完整" if issue_count else "诊断字段完整",
            "warn" if issue_count else "ok",
        ),
        _metric_card("Prompt tokens", format_count(summary.get("total_prompt_tokens")), "输入侧消耗"),
        _metric_card("Completion tokens", format_count(summary.get("total_completion_tokens")), "输出侧消耗"),
        _metric_card(
            "SQL execute",
            format_count(summary.get("execute_count")),
            "执行过查询" if execute_count else "未触发 SQL",
            "primary" if execute_count else "neutral",
        ),
        _metric_card(
            "缺时间项",
            format_count(summary.get("missing_time_count")),
            "影响时间线排序" if missing_time_count else "时间戳完整",
            "warn" if missing_time_count else "ok",
        ),
    ]

    result_ids = summary.get("result_ids") if isinstance(summary.get("result_ids"), list) else []
    result_html = ""
    if result_ids:
        chips = "".join(
            f'<span class="aw-chip">{xml_escape(str(item))}</span>'
            for item in result_ids
        )
        result_html = (
            '<div class="aw-result-strip">'
            '<div class="aw-result-label">Result IDs</div>'
            f'<div class="aw-result-chips">{chips}</div>'
            '</div>'
        )

    st.markdown(
        '<section class="aw-diagnostic-overview">'
        '<div class="aw-overview-header">'
        '<div class="aw-overview-title">诊断总览</div>'
        '<div class="aw-overview-subtitle">Model calls · Memory · SQL · Result Store</div>'
        '</div>'
        f'<div class="aw-metric-grid">{"".join(cards)}</div>'
        f'{result_html}'
        '</section>',
        unsafe_allow_html=True,
    )

    issues = diagnostic_run.get("diagnostic_issues")
    if isinstance(issues, list) and issues:
        with st.expander(f"诊断问题 · {len(issues)}", expanded=False):
            st.json(issues, expanded=False)


def _render_timeline(diagnostic_run: dict[str, Any]) -> None:
    timeline = diagnostic_run.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        st.caption("没有具备 canonical 时间戳的时间线条目。")
    else:
        for index, entry in enumerate(timeline, start=1):
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict):
                continue
            created_at = str(entry.get("created_at") or item.get("created_at") or "")
            if entry.get("type") == "model_call":
                st.markdown(
                    f"{index}. `{created_at}` · 🤖 "
                    f"{item.get('title') or item.get('kind') or '模型调用'} · "
                    f"{item.get('model') or '-'} · "
                    f"{_format_duration_ms(item.get('duration_ms'))} · "
                    f"tokens={format_count(item.get('total_tokens'))}"
                )
            else:
                raw_event = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                st.markdown(f"{index}. `{created_at}` · {format_event_line(raw_event)}")

    missing = diagnostic_run.get("missing_time_items")
    if isinstance(missing, list) and missing:
        with st.expander(f"时间缺失 · {len(missing)}", expanded=False):
            for item in missing:
                payload = item.get("item") if isinstance(item, dict) else None
                if not isinstance(payload, dict):
                    continue
                if item.get("type") == "model_call":
                    st.markdown(
                        "- 🤖 "
                        f"{payload.get('title') or payload.get('kind') or '模型调用'} · "
                        f"`{payload.get('diagnostic_issue') or 'missing_time'}`"
                    )
                else:
                    raw_event = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
                    st.markdown(
                        "- "
                        f"{format_event_line(raw_event)} · "
                        f"`{payload.get('diagnostic_issue') or 'missing_time'}`"
                    )


def _render_memory_event(raw_event: dict[str, Any], event: dict[str, Any]) -> None:
    payload = event_payload(raw_event)
    st.markdown(f"{format_event_line(payload)}")
    if event.get("diagnostic_issue"):
        st.warning(f"诊断数据缺失：`{event['diagnostic_issue']}`")

    source = payload.get("source", "")
    strategy = payload.get("strategy", "")
    namespaces = payload.get("namespaces", [])
    count = payload.get("count", 0)
    embedding_fallback = payload.get("embedding_fallback", False)
    error = payload.get("error", "")
    records = payload.get("records", [])

    st.markdown(
        "检索信息："
        f"`source={source or '-'}` · "
        f"`strategy={strategy or '-'}` · "
        f"`namespaces={','.join(str(item) for item in namespaces) if isinstance(namespaces, list) else namespaces or '-'}` · "
        f"`hits={count}` · "
        f"`embedding_fallback={bool(embedding_fallback)}`"
    )
    if error:
        st.error(str(error))

    if isinstance(records, list) and records:
        st.dataframe(
            [
                {
                    "namespace": item.get("namespace", ""),
                    "key": item.get("key", ""),
                    "content": item.get("content", ""),
                    "source": item.get("source", ""),
                    "updated_at": item.get("updated_at", ""),
                }
                for item in records
                if isinstance(item, dict)
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("没有命中记忆记录。")

    st.json(event, expanded=False)


def _render_sql_plan_event(payload: dict[str, Any]) -> None:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    if not output:
        return
    metrics = output.get("business_metrics", [])
    linked_values = output.get("linked_values", [])

    summary = []
    if isinstance(metrics, list) and metrics:
        summary.append(f"业务口径 `{len(metrics)}` 个")
    if isinstance(linked_values, list) and linked_values:
        summary.append(f"候选值 `{len(linked_values)}` 个")
    if summary:
        st.markdown(" · ".join(summary))

    if isinstance(metrics, list) and metrics:
        st.markdown("**可用业务口径**")
        st.dataframe(
            [
                {
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                    "phrases": ", ".join(str(v) for v in item.get("phrases", []))
                    if isinstance(item.get("phrases"), list)
                    else "",
                    "aggregation": item.get("aggregation", ""),
                    "unit": item.get("unit", ""),
                    "filters": json.dumps(item.get("filters", {}), ensure_ascii=False),
                    "source": item.get("source", ""),
                }
                for item in metrics
                if isinstance(item, dict)
            ],
            use_container_width=True,
            hide_index=True,
        )
