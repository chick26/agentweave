from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.runtime_utils import json_dumps


@dataclass(frozen=True)
class ToolOutput:
    """Dual-channel tool output.

    llm_content is the compact payload returned to the model. ui_content is a
    richer representation emitted through runtime events for UI surfaces.
    """

    llm_content: Any
    ui_content: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_llm_json(self) -> str:
        if isinstance(self.llm_content, str):
            return self.llm_content
        return json_dumps(self.llm_content)

