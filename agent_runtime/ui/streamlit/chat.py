from __future__ import annotations

from typing import Any, Iterable

import streamlit as st


def stream_text(text: str) -> Iterable[str]:
    for chunk in text.splitlines(keepends=True):
        yield chunk


def render_chat_history(messages: list[dict[str, Any]]) -> None:
    for message in messages:
        with st.chat_message(message["role"]):
            trace_summary = message.get("trace_summary", "")
            if trace_summary:
                with st.expander("🔧 执行过程", expanded=False):
                    st.markdown(trace_summary)
            st.markdown(message["content"])
