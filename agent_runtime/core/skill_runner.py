"""Compatibility alias for agent_runtime.core.subagent_runner."""

from __future__ import annotations

import sys

from agent_runtime.core import subagent_runner as _subagent_runner

sys.modules[__name__] = _subagent_runner
