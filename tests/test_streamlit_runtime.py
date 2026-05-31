import types

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
