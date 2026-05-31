from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    role: str
    base_url: str
    model_name: str
    api_key: str
    max_tokens: int
    context_window: int = 32768


def load_model_profiles(
    *,
    orchestrator_base_url: str | None = None,
    orchestrator_model: str | None = None,
    orchestrator_max_tokens: int | None = None,
    sql_base_url: str | None = None,
    sql_model: str | None = None,
    sql_max_tokens: int | None = None,
    api_key: str | None = None,
) -> dict[str, ModelProfile]:
    key = api_key or os.getenv("OPENAI_API_KEY", "not-needed")
    return {
        "orchestrator": ModelProfile(
            role="orchestrator",
            base_url=orchestrator_base_url
            or os.getenv("QWEN36_BASE_URL", "http://localhost:8000/v1"),
            model_name=orchestrator_model
            or os.getenv("QWEN36_MODEL", "openai-compatible-chat-model"),
            api_key=key,
            max_tokens=orchestrator_max_tokens
            or int(os.getenv("QWEN36_MAX_TOKENS", "8192")),
            context_window=int(os.getenv("QWEN36_CONTEXT_WINDOW", "32768")),
        ),
        "sql_worker": ModelProfile(
            role="sql_worker",
            base_url=sql_base_url
            or os.getenv("QWEN32_BASE_URL", "http://localhost:8001/v1"),
            model_name=sql_model
            or os.getenv("QWEN32_MODEL", "openai-compatible-sql-model"),
            api_key=key,
            max_tokens=sql_max_tokens or int(os.getenv("QWEN32_MAX_TOKENS", "2048")),
            context_window=int(os.getenv("QWEN32_CONTEXT_WINDOW", "32768")),
        ),
        "vision_worker": ModelProfile(
            role="vision_worker",
            base_url=os.getenv("QWEN_VL_BASE_URL", "http://localhost:8003/v1"),
            model_name=os.getenv("QWEN_VL_MODEL", "openai-compatible-vision-model"),
            api_key=key,
            max_tokens=int(os.getenv("QWEN_VL_MAX_TOKENS", "4096")),
            context_window=int(os.getenv("QWEN_VL_CONTEXT_WINDOW", "32768")),
        ),
    }
