"""Result-store browsing components for Streamlit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st

from agent_runtime.core.result_events import extract_result_metadata
from agent_runtime.ui.streamlit.formatting import format_result_option


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    model_name: str
    api_key: str
    max_tokens: int
    embedding_base_url: str
    embedding_model_name: str
    memory_enabled: bool


def render_result_runs(
    *,
    runs: list[dict[str, Any]],
    runtime_config: RuntimeConfig,
    get_runtime: Callable[..., Any],
    format_run_option: Callable[[dict[str, Any]], str],
) -> None:
    if not runs:
        st.info("还没有查询结果。发起一次问数后，这里会显示 Result Store 中的分页预览和 CSV 下载。")
        return

    newest_first = list(reversed(runs))
    selected_idx = st.selectbox(
        "选择一次对话",
        options=list(range(len(newest_first))),
        format_func=lambda idx: format_run_option(newest_first[idx]),
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
        format_func=lambda result_id: format_result_option(result_id, results),
        key="selected_result_id",
    )
    runtime = get_runtime(
        base_url=runtime_config.base_url,
        model_name=runtime_config.model_name,
        api_key=runtime_config.api_key,
        max_tokens=runtime_config.max_tokens,
        embedding_base_url=runtime_config.embedding_base_url,
        embedding_model_name=runtime_config.embedding_model_name,
        memory_enabled=runtime_config.memory_enabled,
    )
    selected_event_metadata = next(
        (item for item in results if item["result_id"] == selected_result_id),
        {},
    )
    scope = {
        "run_id": str(selected_event_metadata.get("run_id") or selected_run.get("run_id") or ""),
        "session_id": str(selected_event_metadata.get("session_id") or selected_run.get("session_id") or ""),
        "bot_id": str(selected_event_metadata.get("bot_id") or selected_run.get("bot_id") or ""),
    }
    try:
        metadata = runtime.result_store.get_metadata(selected_result_id, **scope)
    except KeyError:
        st.error("Result Store 无法读取这个 result_id，可能是结果已清理、运行配置已切换，或事件缺少访问 scope。")
        st.json(next(item for item in results if item["result_id"] == selected_result_id))
        return

    artifact_type = str(metadata.get("artifact_type") or "artifact")
    title = str(metadata.get("title") or artifact_type)
    metrics = metadata.get("metrics") if isinstance(metadata.get("metrics"), dict) else {}
    event_metrics = (
        selected_event_metadata.get("metrics")
        if isinstance(selected_event_metadata.get("metrics"), dict)
        else {}
    )
    artifact_metadata = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    stored_row_count = int(metrics.get("stored_count") or metrics.get("row_count") or 0)
    has_more = bool(event_metrics.get("truncated") or metrics.get("truncated"))
    row_label = f"{stored_row_count}+" if has_more else str(stored_row_count)
    st.markdown(
        f"**result_id** `{metadata['result_id']}` · "
        f"**type** `{artifact_type}` · "
        f"**title** `{title}` · "
        f"**stored rows** `{row_label}` · "
        f"**created** `{metadata['created_at']}`"
    )
    if artifact_metadata.get("domain"):
        st.caption(f"domain: `{artifact_metadata['domain']}`")
    if artifact_metadata.get("sql"):
        st.code(str(artifact_metadata["sql"]), language="sql")
    elif artifact_metadata:
        st.json(artifact_metadata)

    row_count = stored_row_count
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
        page_payload = runtime.result_store.get_artifact_page(
            selected_result_id,
            offset=offset,
            limit=int(page_size),
            **scope,
        )
        rows = page_payload.get("rows") if isinstance(page_payload.get("rows"), list) else []
        total_label = f"{row_count}+ 行已存储" if has_more else f"{row_count} 行"
        st.caption(f"显示第 {offset + 1} - {offset + len(rows)} 行，共 {total_label}。")
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.download_button(
        "下载已存储 CSV",
        data=runtime.result_store.export_csv(selected_result_id, **scope),
        file_name=f"{selected_result_id}.csv",
        mime="text/csv",
        use_container_width=True,
    )
