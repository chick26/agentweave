"""Streamlit UI composition for local AgentWeave debugging sessions."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from agent_runtime.common import agentweave_data_dir, load_local_env_files, utc_now_iso
from agent_runtime.storage.diagnostic_store import DiagnosticStore
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
from agent_runtime.ui.streamlit.results import RuntimeConfig, render_result_runs
from agent_runtime.ui.streamlit.resources import (
    format_reload_summary,
    reload_summary_changed,
)
from agent_runtime.ui.streamlit.sidebar import render_sidebar
from agent_runtime.ui.streamlit.styles import inject_styles


ROOT = Path(__file__).resolve().parents[3]
load_local_env_files(ROOT)
DATA_DIR = agentweave_data_dir(ROOT)
SESSION_DB_PATH = DATA_DIR / "streamlit_sessions.sqlite"
TEXT2SQL_AGENT_ROOT = ROOT / "subagents" / "text2sql"

# -- Model defaults (hidden from UI) ----------------------------------
BASE_URL = os.getenv("ORCHESTRATOR_BASE_URL") or os.getenv("QWEN36_BASE_URL", "http://localhost:8000/v1")
MODEL_NAME = os.getenv("ORCHESTRATOR_MODEL") or os.getenv("QWEN36_MODEL", "qwen3.6-27b")
MAX_OUTPUT_TOKENS = int(os.getenv("ORCHESTRATOR_MAX_TOKENS", "8192"))
SQL_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://localhost:8001/v1")
SQL_MODEL_NAME = os.getenv("EXECUTOR_MODEL", "qwen3-32b")
SQL_MAX_OUTPUT_TOKENS = int(os.getenv("EXECUTOR_MAX_TOKENS", "2048"))
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://localhost:8002/v1")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "openai-compatible-embedding-model")
API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")

st.set_page_config(page_title="AgentWeave", layout="wide")
inject_styles()
st.title("AgentWeave")


@st.cache_data(show_spinner=False)
def get_bot_options() -> list[dict[str, str]]:
    from agent_runtime.registry.bot_registry import BotRegistry
    from agent_runtime.registry.skill_registry import AgentRegistry, SkillRegistry

    registry = BotRegistry(
        bots_root=ROOT / "bots",
        agent_registry=AgentRegistry(subagents_root=ROOT / "subagents"),
        skill_registry=SkillRegistry(skills_root=ROOT / "skills"),
    )
    return [
        {
            "id": item["id"],
            "name": item.get("name", item["id"]),
            "description": item.get("description", ""),
        }
        for item in registry.list_summaries()
    ]


sidebar_config = render_sidebar(
    bot_options=get_bot_options(),
    bot_id_default=st.session_state.get("active_bot_id", "data_analyst"),
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
selected_bot_id = sidebar_config.bot_id
memory_enabled = sidebar_config.memory_enabled
clear_memory_requested = sidebar_config.clear_memory_requested
reload_resources_requested = sidebar_config.reload_resources_requested
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
    paths = [domain_configs_root / "domain_catalog.yaml"]
    return tuple(
        (
            str(path.relative_to(domain_configs_root)),
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in sorted(paths)
        if path.exists()
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
    from agent_runtime.core.orchestrator import AgentRuntime

    return AgentRuntime(
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


@st.cache_data(show_spinner="正在生成欢迎消息...", ttl=3600)
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
    bot_id: str,
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
            bot_id=bot_id,
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
    st.session_state.session_id = f"streamlit-{selected_bot_id}-{uuid.uuid4()}"

if st.session_state.get("active_bot_id") != selected_bot_id:
    st.session_state.active_bot_id = selected_bot_id
    st.session_state.session_id = f"streamlit-{selected_bot_id}-{uuid.uuid4()}"
    st.session_state.pop("messages", None)
    st.session_state.model_log_runs = []
    st.session_state.event_runs = []
    st.session_state.pop("initial_message_signature", None)

domains_root = TEXT2SQL_AGENT_ROOT
domains_signature = get_domains_signature(domains_root)
initial_message_signature = (
    selected_bot_id,
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
        bot_id=selected_bot_id,
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
        bot_id=selected_bot_id,
        domains_signature=domains_signature,
    )
    st.session_state.messages[0]["content"] = initial_assistant_message
    st.session_state.initial_message_signature = initial_message_signature
if "model_log_runs" not in st.session_state:
    st.session_state.model_log_runs = []
if "event_runs" not in st.session_state:
    st.session_state.event_runs = []


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
                        bot_id=selected_bot_id,
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
