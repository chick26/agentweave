"""Tests for Streamlit app module loading behavior."""

import types

from streamlit.testing.v1 import AppTest

from agent_runtime.ui import streamlit as streamlit_ui


def test_run_app_reloads_streamlit_app_module(monkeypatch) -> None:
    calls = []
    dummy_module = types.ModuleType("agent_runtime.ui.streamlit.app")

    monkeypatch.setitem(
        streamlit_ui.sys.modules,
        "agent_runtime.ui.streamlit.app",
        dummy_module,
    )
    monkeypatch.setattr(
        streamlit_ui.importlib,
        "reload",
        lambda module: calls.append(("reload", module.__name__)),
    )

    streamlit_ui.run_app()

    assert calls == [("reload", "agent_runtime.ui.streamlit.app")]


def test_run_app_imports_streamlit_app_module_when_missing(monkeypatch) -> None:
    calls = []
    monkeypatch.delitem(
        streamlit_ui.sys.modules,
        "agent_runtime.ui.streamlit.app",
        raising=False,
    )
    monkeypatch.setattr(
        streamlit_ui.importlib,
        "import_module",
        lambda name: calls.append(("import", name)),
    )

    streamlit_ui.run_app()

    assert calls == [("import", "agent_runtime.ui.streamlit.app")]


def test_streamlit_memory_toggle_starts_fresh_session_scope(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_CLIENT_TIMEOUT", "0.1")
    monkeypatch.setenv("OPENAI_CLIENT_MAX_RETRIES", "0")
    app = AppTest.from_file("app.py", default_timeout=10)
    app.session_state["active_bot_id"] = "data_analyst"
    app.session_state["active_memory_enabled"] = True
    app.session_state["session_id"] = "streamlit-data_analyst-mem-existing"
    app.session_state["messages"] = [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    app.session_state["model_log_runs"] = [{"run_id": "old"}]
    app.session_state["event_runs"] = [{"run_id": "old"}]

    app.run(timeout=10)
    app.toggle[0].set_value(False)
    app.run(timeout=10)

    assert not app.exception
    assert app.session_state["active_memory_enabled"] is False
    assert "-nomem-" in app.session_state["session_id"]
    assert app.session_state["session_id"] != "streamlit-data_analyst-mem-existing"
    assert len(app.session_state["messages"]) == 1
    assert app.session_state["model_log_runs"] == []
    assert app.session_state["event_runs"] == []
