from __future__ import annotations

from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class SidebarConfig:
    max_turns: int
    memory_enabled: bool
    clear_memory_requested: bool
    reload_resources_requested: bool
    fork_session_requested: bool
    base_url: str
    model_name: str
    max_output_tokens: int
    sql_base_url: str
    sql_model_name: str
    sql_max_output_tokens: int
    embedding_base_url: str
    embedding_model_name: str
    api_key: str


def render_sidebar(
    *,
    base_url_default: str,
    model_name_default: str,
    max_output_tokens_default: int,
    sql_base_url_default: str,
    sql_model_name_default: str,
    sql_max_output_tokens_default: int,
    embedding_base_url_default: str,
    embedding_model_name_default: str,
    api_key_default: str,
) -> SidebarConfig:
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

        reload_resources_requested = st.button(
            "Reload Resources",
            help="重新发现 AGENTS.md、PROJECT.md、Skills、Subagents 和 Domain 配置。",
            use_container_width=True,
        )
        fork_session_requested = st.button(
            "Fork Session",
            help="从当前会话历史复制出一个新 session，后续提问从分叉点继续。",
            use_container_width=True,
        )

        st.divider()
        with st.expander("模型与连接", expanded=False):
            st.caption("编排模型")
            base_url = st.text_input("Base URL", value=base_url_default, key="orchestration_base_url").strip() or base_url_default
            model_name = st.text_input("模型名称", value=model_name_default, key="orchestration_model_name").strip() or model_name_default
            max_output_tokens = st.number_input(
                "最大输出 tokens",
                min_value=256,
                max_value=262144,
                value=max_output_tokens_default,
                step=256,
                key="orchestration_max_output_tokens",
            )

            st.divider()
            st.caption("SQL 生成")
            sql_base_url = st.text_input("SQL Base URL", value=sql_base_url_default, key="sql_base_url").strip() or sql_base_url_default
            sql_model_name = st.text_input("SQL 模型名称", value=sql_model_name_default, key="sql_model_name").strip() or sql_model_name_default
            sql_max_output_tokens = st.number_input(
                "SQL 最大输出 tokens",
                min_value=256,
                max_value=32768,
                value=sql_max_output_tokens_default,
                step=256,
                key="sql_max_output_tokens",
            )

            st.divider()
            st.caption("Memory Embedding")
            embedding_base_url = st.text_input(
                "Embedding Base URL",
                value=embedding_base_url_default,
                key="embedding_base_url",
            ).strip() or embedding_base_url_default
            embedding_model_name = st.text_input(
                "Embedding 模型名称",
                value=embedding_model_name_default,
                key="embedding_model_name",
            ).strip() or embedding_model_name_default
            api_key = st.text_input(
                "API Key",
                value=api_key_default,
                type="password",
                help="会同时用于编排模型、SQL 模型和 Embedding 模型；如果服务不校验，可保持默认值。",
                key="api_key",
            ) or api_key_default

    return SidebarConfig(
        max_turns=int(max_turns),
        memory_enabled=bool(memory_enabled),
        clear_memory_requested=bool(clear_memory_requested),
        reload_resources_requested=bool(reload_resources_requested),
        fork_session_requested=bool(fork_session_requested),
        base_url=base_url,
        model_name=model_name,
        max_output_tokens=int(max_output_tokens),
        sql_base_url=sql_base_url,
        sql_model_name=sql_model_name,
        sql_max_output_tokens=int(sql_max_output_tokens),
        embedding_base_url=embedding_base_url,
        embedding_model_name=embedding_model_name,
        api_key=api_key,
    )
