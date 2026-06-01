from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from agent_runtime.common import load_env_file, utc_now_iso
from agent_runtime.core.session_ops import (
    fork_sqlite_session,
    replace_sqlite_session_items,
)
from agent_runtime.storage.diagnostic_store import DiagnosticStore
from agent_runtime.storage.session_templates import SessionTemplateStore
from agent_runtime.ui.streamlit.chat import render_chat_history, stream_text
from agent_runtime.ui.streamlit.diagnostics import (
    render_execution_runs,
    render_model_logs,
)
from agent_runtime.ui.streamlit.events import (
    event_payload,
    extract_detail,
    format_trace_for_storage,
    should_show_live_event,
    STAGE_CONFIG,
)
from agent_runtime.ui.streamlit.export import (
    build_session_html,
    build_session_markdown,
)
from agent_runtime.ui.streamlit.results import RuntimeConfig, render_result_runs
from agent_runtime.ui.streamlit.resources import (
    format_reload_summary,
    reload_summary_changed,
)
from agent_runtime.ui.streamlit.sidebar import render_sidebar
from agent_runtime.ui.streamlit.styles import inject_styles


ROOT = Path(__file__).resolve().parents[3]
load_env_file(ROOT / ".env")
SESSION_DB_PATH = ROOT / ".streamlit_agent_sessions.sqlite"
SESSION_TEMPLATE_DB_PATH = ROOT / "agent_session_templates.sqlite"
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

st.set_page_config(page_title="AgentWeave", layout="wide")
inject_styles()
st.title("AgentWeave")


sidebar_config = render_sidebar(
    base_url_default=BASE_URL,
    model_name_default=MODEL_NAME,
    max_output_tokens_default=MAX_OUTPUT_TOKENS,
    sql_base_url_default=SQL_BASE_URL,
    sql_model_name_default=SQL_MODEL_NAME,
    sql_max_output_tokens_default=SQL_MAX_OUTPUT_TOKENS,
    embedding_base_url_default=EMBEDDING_BASE_URL,
    embedding_model_name_default=EMBEDDING_MODEL_NAME,
    api_key_default=API_KEY,
)
max_turns = sidebar_config.max_turns
memory_enabled = sidebar_config.memory_enabled
clear_memory_requested = sidebar_config.clear_memory_requested
reload_resources_requested = sidebar_config.reload_resources_requested
fork_session_requested = sidebar_config.fork_session_requested
base_url = sidebar_config.base_url
model_name = sidebar_config.model_name
max_output_tokens = sidebar_config.max_output_tokens
sql_base_url = sidebar_config.sql_base_url
sql_model_name = sidebar_config.sql_model_name
sql_max_output_tokens = sidebar_config.sql_max_output_tokens
embedding_base_url = sidebar_config.embedding_base_url
embedding_model_name = sidebar_config.embedding_model_name
api_key = sidebar_config.api_key


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
def get_session_template_store() -> SessionTemplateStore:
    return SessionTemplateStore(SESSION_TEMPLATE_DB_PATH)


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
    from agent_runtime.core.orchestrator import AgentRuntime
    from agent_runtime.core.settings import load_database_backend

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


def get_configured_runtime():
    return get_runtime(
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


if clear_memory_requested:
    try:
        get_configured_runtime().clear_memory()
        st.toast("记忆库已清空。")
    except ImportError as exc:
        st.error(f"缺少运行时依赖，无法清空记忆：`{exc}`")


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
    try:
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
    except ImportError:
        return "你好，我可以回答已接入数据领域的问数问题。"


if reload_resources_requested:
    try:
        summary = get_configured_runtime().reload_resources()
        message = format_reload_summary(summary)
        if reload_summary_changed(summary):
            get_initial_assistant_message.clear()
            st.toast(f"资源已重载：{message}")
        else:
            st.toast(f"资源已检查：{message}")
        st.session_state.setdefault("event_runs", []).append(
            {
                "run_id": f"resource-{uuid.uuid4().hex[:16]}",
                "label": "Reload Resources",
                "question": "Reload Resources",
                "events": [
                    {
                        "kind": "resources_reloaded",
                        "timestamp": utc_now_iso(),
                        "run_id": st.session_state.get("session_id", "streamlit"),
                        "payload": {
                            "stage": "resources_reloaded",
                            "summary": summary,
                            "message": message,
                        },
                    }
                ],
            }
        )
    except ImportError as exc:
        st.error(f"缺少运行时依赖，无法重载资源：`{exc}`")
    except Exception as exc:
        st.error(f"资源重载失败：`{type(exc).__name__}: {exc}`")


if "session_id" not in st.session_state:
    st.session_state.session_id = f"streamlit-{uuid.uuid4()}"

if fork_session_requested:
    source_session_id = st.session_state.session_id
    target_session_id = f"streamlit-{uuid.uuid4()}"
    try:
        copied_items = asyncio.run(
            fork_sqlite_session(
                db_path=SESSION_DB_PATH,
                source_session_id=source_session_id,
                target_session_id=target_session_id,
            )
        )
        st.session_state.session_id = target_session_id
        st.session_state.setdefault("event_runs", []).append(
            {
                "run_id": f"session-{uuid.uuid4().hex[:16]}",
                "label": "Fork Session",
                "question": "Fork Session",
                "events": [
                    {
                        "kind": "session_forked",
                        "timestamp": utc_now_iso(),
                        "run_id": target_session_id,
                        "payload": {
                            "stage": "session_forked",
                            "source_session_id": source_session_id,
                            "target_session_id": target_session_id,
                            "copied_items": copied_items,
                        },
                    }
                ],
            }
        )
        st.toast(f"已分叉到新会话：{target_session_id}")
    except Exception as exc:
        st.error(f"会话分叉失败：`{type(exc).__name__}: {exc}`")

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


def _append_local_event_run(
    *,
    label: str,
    event: dict[str, Any],
) -> None:
    st.session_state.setdefault("event_runs", []).append(
        {
            "run_id": str(event.get("run_id") or f"local-{uuid.uuid4().hex[:16]}"),
            "label": label,
            "question": label,
            "events": [event],
        }
    )


with st.sidebar:
    st.divider()
    st.caption("会话模板")
    template_name = st.text_input(
        "模板名称",
        value="",
        placeholder="例如：IDC 月度巡检",
        key="session_template_name",
    )
    if st.button(
        "Save Template",
        help="把当前可见对话保存为可复用模板。",
        use_container_width=True,
    ):
        try:
            template_will_overwrite = get_session_template_store().template_exists(template_name)
            template_id = get_session_template_store().save_template(
                name=template_name,
                messages=st.session_state.messages,
            )
            event = {
                "kind": "session_template_saved",
                "timestamp": utc_now_iso(),
                "run_id": f"template-{uuid.uuid4().hex[:16]}",
                "payload": {
                    "stage": "session_template_saved",
                    "template_id": template_id,
                    "template_name": template_name.strip(),
                    "message_count": len(st.session_state.messages),
                },
            }
            _append_local_event_run(label="Save Template", event=event)
            action = "已覆盖" if template_will_overwrite else "已保存"
            st.toast(f"模板{action}：{template_name.strip()}")
        except ValueError as exc:
            st.warning(str(exc))
        except Exception as exc:
            st.error(f"保存模板失败：`{type(exc).__name__}: {exc}`")

    templates = get_session_template_store().list_templates()
    if templates:
        selected_template_id = st.selectbox(
            "选择模板",
            options=[template.id for template in templates],
            format_func=lambda template_id: next(
                template.name for template in templates if template.id == template_id
            ),
            key="selected_session_template",
        )
        start_column, delete_column = st.columns(2, gap="small")
        if start_column.button(
            "Start",
            help="用模板内容启动一个新会话。",
            use_container_width=True,
        ):
            try:
                template = get_session_template_store().get_template(selected_template_id)
                target_session_id = f"streamlit-{uuid.uuid4()}"
                copied_items = asyncio.run(
                    replace_sqlite_session_items(
                        db_path=SESSION_DB_PATH,
                        session_id=target_session_id,
                        items=template.messages,
                    )
                )
                st.session_state.session_id = target_session_id
                st.session_state.messages = list(template.messages)
                st.session_state.model_log_runs = []
                st.session_state.event_runs = []
                event = {
                    "kind": "session_template_started",
                    "timestamp": utc_now_iso(),
                    "run_id": target_session_id,
                    "payload": {
                        "stage": "session_template_started",
                        "template_id": template.id,
                        "template_name": template.name,
                        "target_session_id": target_session_id,
                        "message_count": copied_items,
                    },
                }
                _append_local_event_run(label="Start From Template", event=event)
                st.toast(f"已从模板启动：{template.name}")
                st.rerun()
            except Exception as exc:
                st.error(f"启动模板失败：`{type(exc).__name__}: {exc}`")
        if delete_column.button(
            "Delete",
            help="删除选中的会话模板。",
            use_container_width=True,
        ):
            get_session_template_store().delete_template(selected_template_id)
            st.toast("模板已删除。")
            st.rerun()
    else:
        st.caption("暂无模板。")

session_markdown = build_session_markdown(
    session_id=st.session_state.session_id,
    messages=st.session_state.messages,
    event_runs=st.session_state.event_runs,
)
session_html = build_session_html(
    session_id=st.session_state.session_id,
    messages=st.session_state.messages,
    event_runs=st.session_state.event_runs,
)
with st.sidebar:
    st.divider()
    st.caption("会话导出")
    export_column, html_export_column = st.columns(2, gap="small")
    export_column.download_button(
        "Markdown",
        data=session_markdown,
        file_name=f"{st.session_state.session_id}.md",
        mime="text/markdown",
        use_container_width=True,
    )
    html_export_column.download_button(
        "HTML",
        data=session_html,
        file_name=f"{st.session_state.session_id}.html",
        mime="text/html",
        use_container_width=True,
    )


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
        if selected_run.get("events"):
            return {
                "run_id": run_id,
                "session_id": st.session_state.get("session_id", ""),
                "question": selected_run.get("question", ""),
                "answer": "",
                "trace_summary": "",
                "model_logs": selected_run.get("logs", []),
                "events": selected_run.get("events", []),
                "status": "completed",
                "error": "",
            }
        st.warning(f"诊断库中找不到 run_id：`{run_id}`。")
        return None


# ── Main rendering ───────────────────────────────────────────────────

chat_tab, logs_tab, execution_tab, results_tab = st.tabs(
    ["对话", "Model Calls", "执行过程", "Results"]
)

with chat_tab:
    render_chat_history(st.session_state.messages)

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
            visible_event_keys: set[tuple[str, str, str]] = set()

            def on_event(event: dict[str, Any]) -> None:
                """Called in real-time as each tool step completes."""
                live_events.append(event)
                payload = event_payload(event)
                stage = payload.get("stage", "")
                # Only show key stages, skip noise
                if not should_show_live_event(event, visible_event_keys):
                    return

                step_counter[0] += 1
                icon, label = STAGE_CONFIG.get(stage, ("⚙️", event.get("title", stage)))
                detail = extract_detail(event)

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
    render_model_logs(
        runs=st.session_state.model_log_runs,
        load_diagnostic_run=_load_diagnostic_run,
        format_run_option=_format_run_option,
    )
with execution_tab:
    render_execution_runs(
        runs=st.session_state.event_runs,
        load_diagnostic_run=_load_diagnostic_run,
        format_run_option=_format_run_option,
    )
with results_tab:
    render_result_runs(
        runs=st.session_state.event_runs,
        runtime_config=RuntimeConfig(
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
        ),
        get_runtime=get_runtime,
        format_run_option=_format_run_option,
    )
