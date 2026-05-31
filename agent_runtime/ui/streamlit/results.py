from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st

from agent_runtime.result_events import extract_result_metadata
from agent_runtime.ui.streamlit.formatting import format_result_option


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    model_name: str
    api_key: str
    max_tokens: int
    sql_base_url: str
    sql_model_name: str
    sql_max_tokens: int
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
        sql_base_url=runtime_config.sql_base_url,
        sql_model_name=runtime_config.sql_model_name,
        sql_max_tokens=runtime_config.sql_max_tokens,
        embedding_base_url=runtime_config.embedding_base_url,
        embedding_model_name=runtime_config.embedding_model_name,
        memory_enabled=runtime_config.memory_enabled,
    )
    try:
        metadata = runtime.result_store.get_metadata(selected_result_id)
    except KeyError:
        st.error("Result Store 中找不到这个 result_id，可能是本地结果库被清理或运行配置已切换。")
        st.json(next(item for item in results if item["result_id"] == selected_result_id))
        return

    selected_event_metadata = next(
        (item for item in results if item["result_id"] == selected_result_id),
        {},
    )
    stored_row_count = int(metadata["row_count"])
    has_more = bool(selected_event_metadata.get("has_more") or selected_event_metadata.get("store_truncated"))
    row_label = f"{stored_row_count}+" if has_more else str(stored_row_count)
    st.markdown(
        f"**result_id** `{metadata['result_id']}` · "
        f"**domain** `{metadata['domain'] or '-'}` · "
        f"**stored rows** `{row_label}` · "
        f"**created** `{metadata['created_at']}`"
    )
    st.code(metadata["sql"], language="sql")

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
        rows = runtime.result_store.get_page(
            selected_result_id,
            offset=offset,
            limit=int(page_size),
        )
        total_label = f"{row_count}+ 行已存储" if has_more else f"{row_count} 行"
        st.caption(f"显示第 {offset + 1} - {offset + len(rows)} 行，共 {total_label}。")
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.download_button(
        "下载已存储 CSV",
        data=runtime.result_store.export_csv(selected_result_id),
        file_name=f"{selected_result_id}.csv",
        mime="text/csv",
        use_container_width=True,
    )
