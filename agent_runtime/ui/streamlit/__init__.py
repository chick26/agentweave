"""Streamlit UI adapter."""

from __future__ import annotations

import importlib
import sys


def run_app() -> None:
    module_name = "agent_runtime.ui.streamlit.app"
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    else:
        importlib.import_module(module_name)
