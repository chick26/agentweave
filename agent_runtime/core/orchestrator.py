"""Compatibility import for the runtime facade.

New code should import from :mod:`agent_runtime.core.runtime`.
"""

from agent_runtime.core.runtime import AgentRunResult, AgentRuntime

__all__ = ["AgentRunResult", "AgentRuntime"]
