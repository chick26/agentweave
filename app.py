from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from agent_runtime.common import load_env_file, utc_now_iso
from agent_runtime.diagnostic_store import DiagnosticStore
from agent_runtime.result_events import extract_result_metadata


ROOT = Path(__file__).resolve().parent
load_env_file(ROOT / ".env")
SESSION_DB_PATH = ROOT / ".streamlit_agent_sessions.sqlite"
TEXT2SQL_AGENT_ROOT = ROOT / "subagents" / "text2sql"

# -- Model defaults (hidden from UI) ----------------------------------
BASE_URL = os.getenv("QWEN36_BASE_URL", "http://localhost:8000/v1")
MODEL_NAME = os.getenv("QWEN36_MODEL", "openai-compatible-chat-model")
MAX_OUTPUT_TOKENS = 8192
SQL_BASE_URL = os.getenv("QWEN32_BASE_URL", "http://localhost:8001/v1")
SQL_MODEL_NAME = os.getenv("QWEN32_MODEL", "openai-compatible-sql-model")
SQL_MAX_OUTPUT_TOKENS = 2048
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://localhost:8002/v1")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "openai-compatible-embedding-model")
API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")
PRESET_QUESTIONS_PER_DOMAIN = 2

# ── Stage display config ─────────────────────────────────────────────
STAGE_CONFIG = {
    "current_time": ("🕒", "获取当前时间"),
    "worker_start": ("🧩", "启动 Worker"),
    "worker_complete": ("✅", "Worker 完成"),
    "load_skill": ("📚", "加载 Skill"),
    "memory_search": ("🧠", "检索记忆"),
    "memory_retrieval": ("🧠", "注入记忆"),
    "activation": ("🔌", "激活 Domain"),
    "search_values": ("🔍", "搜索候选值"),
    "sql_plan": ("🧭", "构建 SQLPlan"),
    "sql_prompt": ("📝", "构建提示词"),
    "sql_model_output": ("🤖", "SQL 模型推理"),
    "sql_extract": ("✂️", "提取 SQL"),
    "execute": ("▶️", "执行查询"),
    "todo_update": ("☑️", "更新 Todo"),
}


st.set_page_config(page_title="AgentWeave", layout="wide")
st.title("AgentWeave")


with st.sidebar:
    st.subheader("运行控制")
    max_turns = st.slider(
        "最大对话轮数",
        min_value=1,
        max_value=20,
        value=10,
        help="单次会话中允许的最大对话轮数（用户 + 助手各算一轮）。",
        key="max_turns",
    )
    memory_enabled = st.toggle(
        "启用 Memory 能力",
        value=True,
        help="关闭后不注入长期记忆，也不向编排模型暴露 memory_search / memory_write。",
        key="memory_enabled",
    )

    st.caption("清理操作")
    clear_memory_column, clear_session_column = st.columns(2, gap="small")
    clear_memory_requested = clear_memory_column.button(
        "清空记忆",
        help="删除 agent_memory.sqlite 中的记忆、会话摘要和向量索引。",
        use_container_width=True,
    )
    if clear_session_column.button(
        "清空会话",
        help="清空当前页面中的聊天、执行过程和模型日志。",
        use_container_width=True,
    ):
        st.session_state.clear()
        st.rerun()

    st.divider()
    with st.expander("模型与连接", expanded=False):
        st.caption("编排模型")
        base_url = st.text_input(
            "Base URL",
            value=BASE_URL,
            key="orchestration_base_url",
        ).strip() or BASE_URL
        model_name = st.text_input(
            "模型名称",
            value=MODEL_NAME,
            key="orchestration_model_name",
        ).strip() or MODEL_NAME
        max_output_tokens = st.number_input(
            "最大输出 tokens",
            min_value=256,
            max_value=262144,
            value=MAX_OUTPUT_TOKENS,
            step=256,
            key="orchestration_max_output_tokens",
        )

        st.divider()
        st.caption("SQL 生成")
        sql_base_url = st.text_input(
            "SQL Base URL",
            value=SQL_BASE_URL,
            key="sql_base_url",
        ).strip() or SQL_BASE_URL
        sql_model_name = st.text_input(
            "SQL 模型名称",
            value=SQL_MODEL_NAME,
            key="sql_model_name",
        ).strip() or SQL_MODEL_NAME
        sql_max_output_tokens = st.number_input(
            "SQL 最大输出 tokens",
            min_value=256,
            max_value=32768,
            value=SQL_MAX_OUTPUT_TOKENS,
            step=256,
            key="sql_max_output_tokens",
        )

        st.divider()
        st.caption("Memory Embedding")
        embedding_base_url = st.text_input(
            "Embedding Base URL",
            value=EMBEDDING_BASE_URL,
            key="embedding_base_url",
        ).strip() or EMBEDDING_BASE_URL
        embedding_model_name = st.text_input(
            "Embedding 模型名称",
            value=EMBEDDING_MODEL_NAME,
            key="embedding_model_name",
        ).strip() or EMBEDDING_MODEL_NAME
        api_key = st.text_input(
            "API Key",
            value=API_KEY,
            type="password",
            help="会同时用于编排模型、SQL 模型和 Embedding 模型；如果服务不校验，可保持默认值。",
            key="api_key",
        ) or API_KEY


def get_domains_signature(domain_configs_root: Path) -> tuple[tuple[str, int, int], ...]:
    paths = list(domain_configs_root.glob("*/DOMAIN.md"))
    return tuple(
        (
            str(path.relative_to(domain_configs_root)),
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in sorted(paths)
    )


def get_secret_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@st.cache_resource(show_spinner=False)
def get_diagnostic_store() -> DiagnosticStore:
    return DiagnosticStore(SESSION_DB_PATH)


@st.cache_resource(show_spinner=False)
def get_runtime(
    base_url: str,
    model_name: str,
    api_key: str,
    max_tokens: int,
    sql_base_url: str,
    sql_model_name: str,
    sql_max_tokens: int,
    embedding_base_url: str,
    embedding_model_name: str,
    memory_enabled: bool,
):
    from agent_runtime.orchestrator import AgentRuntime
    from agent_runtime.settings import load_database_backend

    return AgentRuntime(
        backend=load_database_backend(ROOT),
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        session_db_path=SESSION_DB_PATH,
        max_tokens=max_tokens,
        sql_base_url=sql_base_url,
        sql_model_name=sql_model_name,
        sql_max_tokens=sql_max_tokens,
        embedding_base_url=embedding_base_url,
        embedding_model_name=embedding_model_name,
        memory_enabled=memory_enabled,
    )


def persist_diagnostic_run(
    *,
    run_id: str,
    session_id: str,
    question: str,
    answer: str,
    trace_summary: str = "",
    model_logs: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    status: str = "completed",
    error: str = "",
) -> None:
    try:
        get_diagnostic_store().record_run(
            run_id=run_id,
            session_id=session_id,
            question=question,
            answer=answer,
            trace_summary=trace_summary,
            model_logs=model_logs,
            events=events,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            error=error,
        )
    except Exception as exc:
        st.warning(f"诊断日志保存失败：{type(exc).__name__}: {exc}")


if clear_memory_requested:
    runtime = get_runtime(
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        max_tokens=int(max_output_tokens),
        sql_base_url=sql_base_url,
        sql_model_name=sql_model_name,
        sql_max_tokens=int(sql_max_output_tokens),
        embedding_base_url=embedding_base_url,
        embedding_model_name=embedding_model_name,
        memory_enabled=memory_enabled,
    )
    runtime.clear_memory()
    st.toast("记忆库已清空。")


@st.cache_data(show_spinner="正在根据 Domain 生成预设问题...", ttl=3600)
def get_initial_assistant_message(
    base_url: str,
    model_name: str,
    max_tokens: int,
    sql_base_url: str,
    sql_model_name: str,
    sql_max_tokens: int,
    embedding_base_url: str,
    embedding_model_name: str,
    memory_enabled: bool,
    api_key: str,
    session_id: str,
    domains_signature: tuple[tuple[str, int, int], ...],
) -> str:
    del domains_signature
    runtime = get_runtime(
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        max_tokens=max_tokens,
        sql_base_url=sql_base_url,
        sql_model_name=sql_model_name,
        sql_max_tokens=sql_max_tokens,
        embedding_base_url=embedding_base_url,
        embedding_model_name=embedding_model_name,
        memory_enabled=memory_enabled,
    )
    result = runtime.run_session_start_hook(
        session_id=session_id,
        base_url=sql_base_url,
        model_name=sql_model_name,
        api_key=api_key,
        questions_per_domain=PRESET_QUESTIONS_PER_DOMAIN,
    )
    return result.message


if "session_id" not in st.session_state:
    st.session_state.session_id = f"streamlit-{uuid.uuid4()}"
domains_root = TEXT2SQL_AGENT_ROOT / "domains"
domains_signature = get_domains_signature(domains_root)
initial_message_signature = (
    base_url,
    model_name,
    max_output_tokens,
    sql_base_url,
    sql_model_name,
    sql_max_output_tokens,
    embedding_base_url,
    embedding_model_name,
    memory_enabled,
    get_secret_fingerprint(api_key),
    domains_signature,
)
if "messages" not in st.session_state:
    initial_assistant_message = get_initial_assistant_message(
        base_url=base_url,
        model_name=model_name,
        max_tokens=max_output_tokens,
        sql_base_url=sql_base_url,
        sql_model_name=sql_model_name,
        sql_max_tokens=sql_max_output_tokens,
        embedding_base_url=embedding_base_url,
        embedding_model_name=embedding_model_name,
        memory_enabled=memory_enabled,
        api_key=api_key,
        session_id=st.session_state.session_id,
        domains_signature=domains_signature,
    )
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": initial_assistant_message,
        }
    ]
    st.session_state.initial_message_signature = initial_message_signature
elif (
    len(st.session_state.messages) == 1
    and st.session_state.messages[0].get("role") == "assistant"
    and st.session_state.get("initial_message_signature") != initial_message_signature
):
    initial_assistant_message = get_initial_assistant_message(
        base_url=base_url,
        model_name=model_name,
        max_tokens=max_output_tokens,
        sql_base_url=sql_base_url,
        sql_model_name=sql_model_name,
        sql_max_tokens=sql_max_output_tokens,
        embedding_base_url=embedding_base_url,
        embedding_model_name=embedding_model_name,
        memory_enabled=memory_enabled,
        api_key=api_key,
        session_id=st.session_state.session_id,
        domains_signature=domains_signature,
    )
    st.session_state.messages[0]["content"] = initial_assistant_message
    st.session_state.initial_message_signature = initial_message_signature
if "model_log_runs" not in st.session_state:
    st.session_state.model_log_runs = []
if "event_runs" not in st.session_state:
    st.session_state.event_runs = []


def stream_text(text: str):
    for chunk in text.splitlines(keepends=True):
        yield chunk


# ── Event formatting helpers ─────────────────────────────────────────

# Only these stages are shown to the user (both real-time and stored)
_VISIBLE_STAGES = {
    "current_time",
    "worker_start",
    "worker_complete",
    "load_skill",
    "memory_search",
    "memory_retrieval",
    "activation",
    "search_values",
    "sql_plan",
    "todo_update",
    "sql_extract",
    "execute",
}


def _event_payload(event: dict) -> dict:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return event


def _extract_detail(event: dict) -> str:
    """Extract key detail from an event for compact display."""
    event = _event_payload(event)
    stage = event.get("stage", "")
    output = event.get("output")
    inp = event.get("input")

    if stage == "activation":
        if isinstance(output, dict):
            return f"`{output.get('name', '')}`"

    elif stage == "worker_start":
        skill = event.get("skill", "")
        detail = f"`{skill}`" if skill else ""
        task = str(event.get("task") or "")
        if task:
            if len(task) > 60:
                task = task[:57] + "..."
            detail += f" · {task}"
        return detail

    elif stage == "worker_complete":
        subagent = event.get("subagent") or event.get("skill") or ""
        result = event.get("result")
        detail = f"`{subagent}`" if subagent else ""
        if isinstance(result, dict):
            domain = result.get("domain")
            row_count = result.get("row_count")
            if domain:
                detail += f" · domain=`{domain}`"
            if row_count is not None:
                detail += f" · rows={row_count}"
        return detail

    elif stage == "load_skill":
        skill = event.get("skill", "")
        found = "已加载" if event.get("found") else "未找到"
        return f"`{skill}` · {found}" if skill else found

    elif stage in {"memory_search", "memory_retrieval"}:
        strategy = event.get("strategy", "")
        count = int(event.get("count") or 0)
        namespaces = event.get("namespaces", [])
        labels = ",".join(str(item) for item in namespaces) if isinstance(namespaces, list) else ""
        detail = f"`{strategy}` 命中 {count} 条"
        if labels:
            detail += f" · `{labels}`"
        if event.get("embedding_fallback"):
            detail += " · fallback"
        if event.get("error"):
            detail += " · 发生降级"
        return detail

    elif stage == "current_time":
        if isinstance(output, dict):
            if output.get("error"):
                return f"❌ {output['error']}"
            return f"`{output.get('iso', '')}`"

    elif stage == "search_values":
        if isinstance(inp, dict):
            query = inp.get("query", "")
            if isinstance(output, list) and output:
                n = len(output)
                top = output[0].get("value", "")
                if len(top) > 30:
                    top = top[:27] + "..."
                if n == 1:
                    return f'`{query}` → `{top}`'
                return f'`{query}` → {n} 个候选值'
            return f'`{query}` → 无匹配'

    elif stage == "todo_update":
        if event.get("error"):
            return f"❌ {event['error']}"
        items = event.get("items", [])
        if isinstance(items, list):
            in_progress = [
                item.get("content", "")
                for item in items
                if isinstance(item, dict) and item.get("status") == "in_progress"
            ]
            if in_progress:
                return f"`{in_progress[0]}`"
            return f"{len(items)} 项"

    elif stage == "sql_plan":
        if isinstance(output, dict):
            linked = output.get("linked_values", [])
            metrics = output.get("business_metrics", [])
            parts = []
            if isinstance(metrics, list) and metrics:
                parts.append(f"{len(metrics)} 个业务口径")
            if isinstance(linked, list) and linked:
                parts.append(f"{len(linked)} 个候选值")
            return " · ".join(parts)

    elif stage == "sql_extract":
        if isinstance(output, dict):
            validation_error = output.get("validation_error")
            if validation_error:
                return f"❌ {validation_error}"
            output = output.get("sql", "")
        if isinstance(output, str) and output.strip():
            sql = output.strip().replace("\n", " ")
            if len(sql) > 80:
                sql = sql[:77] + "..."
            return f"`{sql}`"

    elif stage == "execute":
        if isinstance(output, dict):
            error = output.get("error")
            if error:
                return f"❌ {error}"
            rows = output.get("sample_rows", output.get("rows", []))
            row_count = int(output.get("row_count") or 0)
            result_id = output.get("result_id", "")
            if isinstance(rows, list) and rows:
                # Format result as a readable summary
                if len(rows) == 1 and len(rows[0]) <= 3:
                    # Single-row result: show inline
                    parts = [f"{k}=**{v}**" for k, v in rows[0].items()]
                    detail = "✅ " + ", ".join(parts)
                else:
                    detail = f"✅ {row_count} 行结果，样例 {len(rows)} 行"
            else:
                detail = f"✅ {row_count} 行结果"
            if result_id:
                detail += f" · `{result_id}`"
            return detail

    return ""


def format_event_line(event: dict) -> str:
    """Format an event into a concise display line."""
    event = _event_payload(event)
    stage = event.get("stage", "")
    icon, label = STAGE_CONFIG.get(stage, ("⚙️", event.get("title", stage)))
    detail = _extract_detail(event)
    if detail:
        return f"{icon} {label} — {detail}"
    return f"{icon} {label}"


def format_trace_for_storage(events: list[dict]) -> str:
    """Format events as a compact markdown string for message storage."""
    if not events:
        return ""
    lines = []
    for e in events:
        if _event_payload(e).get("stage") in _VISIBLE_STAGES:
            lines.append(format_event_line(e))
    return "\n".join(lines)


def _format_run_option(run: dict[str, Any]) -> str:
    label = str(run.get("label") or run.get("question") or "")
    run_id = str(run.get("run_id") or "")
    if run_id:
        return f"{label} · {run_id}"
    return label


def _load_diagnostic_run(selected_run: dict[str, Any]) -> dict[str, Any] | None:
    run_id = str(selected_run.get("run_id") or "")
    if not run_id:
        st.warning("这次对话没有诊断 run_id，无法读取规范化诊断日志。")
        return None
    try:
        return get_diagnostic_store().get_run(run_id)
    except KeyError:
        st.warning(f"诊断库中找不到 run_id：`{run_id}`。")
        return None


def _format_duration_ms(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return "-"
    if millis < 1000:
        return f"{millis} ms"
    return f"{millis / 1000:.2f}s"


def _format_count(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _issue_list(value: Any) -> list[str]:
    if not value:
        return []
    return [item for item in str(value).split(";") if item]


def _has_canonical_time(item: dict[str, Any]) -> bool:
    issues = _issue_list(item.get("diagnostic_issue"))
    if any(issue.endswith("_created_at") or issue.endswith("_timestamp") for issue in issues):
        return False
    return bool(item.get("created_at"))


def _model_call_header(call: dict[str, Any], position: int) -> str:
    parts = [
        f"{position}. {call.get('title') or call.get('kind') or '模型调用'}",
        str(call.get("model") or "-"),
        _format_duration_ms(call.get("duration_ms")),
        f"tokens={_format_count(call.get('total_tokens'))}",
        f"messages={_format_count(call.get('message_count'))}",
        f"tools={_format_count(call.get('tool_count'))}",
    ]
    created_at = str(call.get("created_at") or "")
    if created_at:
        parts.append(created_at)
    if call.get("diagnostic_issue"):
        parts.append(f"issue={call['diagnostic_issue']}")
    return " · ".join(parts)


def _render_model_call(call: dict[str, Any], position: int, expanded: bool = False) -> None:
    payload = call.get("payload") if isinstance(call.get("payload"), dict) else {}
    with st.expander(_model_call_header(call, position), expanded=expanded):
        st.markdown(
            "Token 用量："
            f"`prompt={_format_count(call.get('prompt_tokens'))}` · "
            f"`completion={_format_count(call.get('completion_tokens'))}` · "
            f"`total={_format_count(call.get('total_tokens'))}`"
        )
        st.markdown(
            "结构："
            f"`messages={_format_count(call.get('message_count'))}` · "
            f"`tools={_format_count(call.get('tool_count'))}` · "
            f"`duration={_format_duration_ms(call.get('duration_ms'))}`"
        )
        if call.get("diagnostic_issue"):
            st.warning(f"诊断数据缺失：`{call['diagnostic_issue']}`")
        if call.get("has_error"):
            st.error(str(payload.get("error") or "模型调用失败"))

        request_tab, response_tab, raw_tab = st.tabs(["实际输入", "实际输出", "完整 JSON"])
        with request_tab:
            st.json(payload.get("request", {}), expanded=False)
        with response_tab:
            st.json(payload.get("response", {}), expanded=False)
        with raw_tab:
            st.json(call, expanded=False)


def _render_diagnostic_overview(diagnostic_run: dict[str, Any]) -> None:
    summary = diagnostic_run.get("summary") if isinstance(diagnostic_run.get("summary"), dict) else {}
    cols = st.columns(5)
    cols[0].metric("总耗时", _format_duration_ms(summary.get("duration_ms")))
    cols[1].metric("模型调用", _format_count(summary.get("model_call_count")))
    cols[2].metric("总 tokens", _format_count(summary.get("total_tokens")))
    cols[3].metric("事件数", _format_count(summary.get("event_count")))
    cols[4].metric("诊断问题", _format_count(summary.get("diagnostic_issue_count")))

    detail_cols = st.columns(4)
    detail_cols[0].metric("Prompt tokens", _format_count(summary.get("total_prompt_tokens")))
    detail_cols[1].metric("Completion tokens", _format_count(summary.get("total_completion_tokens")))
    detail_cols[2].metric("SQL execute", _format_count(summary.get("execute_count")))
    detail_cols[3].metric("缺时间项", _format_count(summary.get("missing_time_count")))

    result_ids = summary.get("result_ids") if isinstance(summary.get("result_ids"), list) else []
    if result_ids:
        st.caption("Result IDs: " + ", ".join(f"`{item}`" for item in result_ids))

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
                    f"tokens={_format_count(item.get('total_tokens'))}"
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
    payload = _event_payload(raw_event)
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


def render_model_logs() -> None:
    """Render detailed model request/response logs."""
    runs = st.session_state.model_log_runs
    if not runs:
        st.info("还没有模型日志。发起一次问数后，这里会显示给模型的实际输入、工具定义、模型输出和 token 用量。")
        return

    st.caption("本页显示当前 Streamlit 会话缓存；完整诊断日志会同步写入本地 SQLite，便于后续离线分析。")
    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: _format_run_option(newest_first[idx]),
        key="selected_model_log_run",
    )
    selected_run = newest_first[selected_idx]
    diagnostic_run = _load_diagnostic_run(selected_run)
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


def render_event_runs(kind: str, empty_message: str) -> None:
    runs = st.session_state.event_runs
    if not runs:
        st.info(empty_message)
        return
    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: _format_run_option(newest_first[idx]),
        key=f"selected_{kind}_run",
    )
    selected_run = newest_first[selected_idx]
    diagnostic_run = _load_diagnostic_run(selected_run)
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
        payload = _event_payload(event.get("payload", {}))
        title = payload.get("title") or payload.get("stage") or kind
        with st.expander(f"{idx}. {title}", expanded=idx == 1):
            st.json(event, expanded=False)


def render_execution_runs() -> None:
    runs = st.session_state.event_runs
    if not runs:
        st.info("还没有执行过程。发起一次对话后，这里会显示 Skill、Subagent、Memory 和 Todo 事件。")
        return
    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: _format_run_option(newest_first[idx]),
        key="selected_execution_run",
    )
    selected_run = newest_first[selected_idx]
    diagnostic_run = _load_diagnostic_run(selected_run)
    if diagnostic_run is None:
        return
    _render_diagnostic_overview(diagnostic_run)
    with st.expander("时间线", expanded=True):
        _render_timeline(diagnostic_run)

    event_groups = [
        ("Skill Events", "skill_event"),
        ("Subagent Runs", "worker_run"),
        ("Subagent Trace", "subagent_trace"),
        ("Memory Events", "memory_event"),
        ("Todo Events", "todo_event"),
    ]
    for title, kind in event_groups:
        events = [
            event for event in diagnostic_run.get("events", [])
            if isinstance(event, dict) and event.get("kind") == kind
        ]
        with st.expander(f"{title} · {len(events)}", expanded=bool(events) and kind == "worker_run"):
            if not events:
                st.caption("这次对话没有对应事件。")
                continue
            for idx, event in enumerate(events, start=1):
                raw_event = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if kind == "memory_event":
                    with st.container(border=True):
                        st.markdown(f"**{idx}. Memory Retrieval**")
                        _render_memory_event(raw_event, event)
                else:
                    st.markdown(f"{idx}. {format_event_line(raw_event)}")
                    if event.get("diagnostic_issue"):
                        st.warning(f"诊断数据缺失：`{event['diagnostic_issue']}`")
                    if kind == "subagent_trace":
                        payload = _event_payload(raw_event)
                        if payload.get("stage") == "sql_plan":
                            _render_sql_plan_event(payload)
                    st.json(event, expanded=False)


def render_result_runs() -> None:
    runs = st.session_state.event_runs
    if not runs:
        st.info("还没有查询结果。发起一次问数后，这里会显示 Result Store 中的分页预览和 CSV 下载。")
        return

    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: _format_run_option(newest_first[idx]),
        key="selected_result_run",
    )
    selected_run = newest_first[selected_idx]
    results = extract_result_metadata(selected_run.get("events", []))
    if not results:
        st.warning("这次对话没有可预览的 Result Store 结果。")
        return

    selected_result_id = st.selectbox(
        "选择结果",
        options=[item["result_id"] for item in results],
        format_func=lambda result_id: _format_result_option(result_id, results),
        key="selected_result_id",
    )
    runtime = get_runtime(
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        max_tokens=int(max_output_tokens),
        sql_base_url=sql_base_url,
        sql_model_name=sql_model_name,
        sql_max_tokens=int(sql_max_output_tokens),
        embedding_base_url=embedding_base_url,
        embedding_model_name=embedding_model_name,
        memory_enabled=memory_enabled,
    )
    try:
        metadata = runtime.result_store.get_metadata(selected_result_id)
    except KeyError:
        st.error("Result Store 中找不到这个 result_id，可能是本地结果库被清理或运行配置已切换。")
        st.json(next(item for item in results if item["result_id"] == selected_result_id))
        return

    st.markdown(
        f"**result_id** `{metadata['result_id']}` · "
        f"**domain** `{metadata['domain'] or '-'}` · "
        f"**rows** `{metadata['row_count']}` · "
        f"**created** `{metadata['created_at']}`"
    )
    st.code(metadata["sql"], language="sql")

    row_count = int(metadata["row_count"])
    if row_count == 0:
        st.info("这个结果没有数据行。")
    else:
        page_size = st.selectbox(
            "每页行数",
            options=[20, 50, 100, 200],
            index=1,
            key=f"result_page_size_{selected_result_id}",
        )
        max_page = max(1, ((row_count - 1) // int(page_size)) + 1)
        page = st.number_input(
            "页码",
            min_value=1,
            max_value=max_page,
            value=1,
            step=1,
            key=f"result_page_{selected_result_id}",
        )
        offset = (int(page) - 1) * int(page_size)
        rows = runtime.result_store.get_page(
            selected_result_id,
            offset=offset,
            limit=int(page_size),
        )
        st.caption(f"显示第 {offset + 1} - {offset + len(rows)} 行，共 {row_count} 行。")
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.download_button(
        "下载完整 CSV",
        data=runtime.result_store.export_csv(selected_result_id),
        file_name=f"{selected_result_id}.csv",
        mime="text/csv",
        use_container_width=True,
    )


def _format_result_option(result_id: str, results: list[dict[str, Any]]) -> str:
    for item in results:
        if item["result_id"] == result_id:
            return f"{result_id} · {item['row_count']} rows"
    return result_id


# ── Main rendering ───────────────────────────────────────────────────

chat_tab, logs_tab, execution_tab, results_tab = st.tabs(
    ["对话", "Model Calls", "执行过程", "Results"]
)

with chat_tab:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            # Show stored trace summary for past messages
            trace_summary = message.get("trace_summary", "")
            if trace_summary:
                with st.expander("🔧 执行过程", expanded=False):
                    st.markdown(trace_summary)
            st.markdown(message["content"])

prompt = st.chat_input("输入你的问数问题")
if prompt:
    diagnostic_run_id = f"ui-{uuid.uuid4().hex[:16]}"
    diagnostic_started_at = utc_now_iso()
    live_events: list[dict[str, Any]] = []
    with chat_tab:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            # Create a live status container for real-time tool execution display
            status_container = st.status("🧠 思考中...", expanded=True)
            step_counter = [0]

            def on_event(event: dict[str, Any]) -> None:
                """Called in real-time as each tool step completes."""
                live_events.append(event)
                payload = _event_payload(event)
                stage = payload.get("stage", "")
                # Only show key stages, skip noise
                if stage not in _VISIBLE_STAGES:
                    return

                step_counter[0] += 1
                icon, label = STAGE_CONFIG.get(stage, ("⚙️", event.get("title", stage)))
                detail = _extract_detail(event)

                with status_container:
                    if detail:
                        st.markdown(f"{icon} **{label}** — {detail}")
                    else:
                        st.markdown(f"{icon} **{label}**")

                status_container.update(label=f"🧠 {label}...", state="running")

            try:
                runtime = get_runtime(
                    base_url=base_url,
                    model_name=model_name,
                    api_key=api_key,
                    max_tokens=int(max_output_tokens),
                    sql_base_url=sql_base_url,
                    sql_model_name=sql_model_name,
                    sql_max_tokens=int(sql_max_output_tokens),
                    embedding_base_url=embedding_base_url,
                    embedding_model_name=embedding_model_name,
                    memory_enabled=memory_enabled,
                )
                response = asyncio.run(
                    runtime.ask(
                        prompt,
                        st.session_state.session_id,
                        event_callback=on_event,
                        max_turns=max_turns,
                    )
                )
                answer = response["final_output"]
                model_logs = response.get("model_logs", [])
                events = response.get("events", [])

                st.session_state.model_log_runs.append(
                    {
                        "run_id": diagnostic_run_id,
                        "label": f"{prompt[:40]}{'...' if len(prompt) > 40 else ''}",
                        "question": prompt,
                        "logs": model_logs,
                    }
                )
                st.session_state.event_runs.append(
                    {
                        "run_id": diagnostic_run_id,
                        "label": f"{prompt[:40]}{'...' if len(prompt) > 40 else ''}",
                        "question": prompt,
                        "events": events,
                    }
                )

                # Finalize status
                if step_counter[0] > 0:
                    status_container.update(
                        label=f"✅ 完成（{step_counter[0]} 步）",
                        state="complete",
                        expanded=False,
                    )
                else:
                    # No tools were called (pure conversation turn)
                    status_container.update(
                        label="✅ 完成",
                        state="complete",
                        expanded=False,
                    )

                # Stream the final answer
                streamed_answer = st.write_stream(stream_text(answer))
                trace_summary = format_trace_for_storage(events)

                # Store message with trace summary for re-rendering
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": streamed_answer,
                        "trace_summary": trace_summary,
                    }
                )
                persist_diagnostic_run(
                    run_id=diagnostic_run_id,
                    session_id=st.session_state.session_id,
                    question=prompt,
                    answer=streamed_answer,
                    trace_summary=trace_summary,
                    model_logs=model_logs,
                    events=events,
                    started_at=diagnostic_started_at,
                    completed_at=utc_now_iso(),
                )
            except ImportError as exc:
                status_container.update(label="❌ 错误", state="error")
                message = (
                    "缺少 OpenAI Agents SDK。请先安装依赖：`uv sync --dev`，"
                    f"然后重新运行 Streamlit。原始错误：`{exc}`"
                )
                st.error(message)
                st.session_state.messages.append({"role": "assistant", "content": message})
                persist_diagnostic_run(
                    run_id=diagnostic_run_id,
                    session_id=st.session_state.session_id,
                    question=prompt,
                    answer=message,
                    events=live_events,
                    started_at=diagnostic_started_at,
                    completed_at=utc_now_iso(),
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception as exc:
                status_container.update(label="❌ 执行失败", state="error")
                message = f"执行失败：`{type(exc).__name__}: {exc}`"
                st.error(message)
                st.session_state.messages.append({"role": "assistant", "content": message})
                persist_diagnostic_run(
                    run_id=diagnostic_run_id,
                    session_id=st.session_state.session_id,
                    question=prompt,
                    answer=message,
                    events=live_events,
                    started_at=diagnostic_started_at,
                    completed_at=utc_now_iso(),
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )

with logs_tab:
    render_model_logs()
with execution_tab:
    render_execution_runs()
with results_tab:
    render_result_runs()
