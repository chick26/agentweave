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


@dataclass(frozen=True)
class RuntimeModelProfiles:
    """Canonical model roles exposed by the backend runtime."""

    orchestrator: ModelProfile
    executor: ModelProfile
    embedding: ModelProfile


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
    def _resolve_key(env_var_name: str) -> str:
        if specific_key := os.getenv(env_var_name):
            return specific_key
        if api_key and api_key != "not-needed":
            return api_key
        return os.getenv("OPENAI_API_KEY", "not-needed")

    orch_key = _resolve_key("QWEN36_API_KEY")
    exec_key = _resolve_key("EXECUTOR_API_KEY")
    emb_key = _resolve_key("EMBEDDING_API_KEY")

    return {
        "orchestrator": ModelProfile(
            role="orchestrator",
            base_url=orchestrator_base_url
            or os.getenv("QWEN36_BASE_URL", "http://localhost:8000/v1"),
            model_name=orchestrator_model
            or os.getenv("QWEN36_MODEL", "qwen3.6-27b"),
            api_key=orch_key,
            max_tokens=orchestrator_max_tokens
            or int(os.getenv("QWEN36_MAX_TOKENS", "8192")),
            context_window=int(os.getenv("QWEN36_CONTEXT_WINDOW", "32768")),
        ),
        "executor": ModelProfile(
            role="executor",
            base_url=sql_base_url
            or os.getenv("EXECUTOR_BASE_URL")
            or "http://localhost:8001/v1",
            model_name=sql_model
            or os.getenv("EXECUTOR_MODEL")
            or "qwen3-32b",
            api_key=exec_key,
            max_tokens=sql_max_tokens
            or int(os.getenv("EXECUTOR_MAX_TOKENS", "2048")),
            context_window=int(os.getenv("EXECUTOR_CONTEXT_WINDOW", "32768")),
        ),
        "embedding": ModelProfile(
            role="embedding",
            base_url=os.getenv("EMBEDDING_BASE_URL", "http://localhost:8002/v1"),
            model_name=os.getenv("EMBEDDING_MODEL", "openai-compatible-embedding-model"),
            api_key=emb_key,
            max_tokens=0,
            context_window=int(os.getenv("EMBEDDING_CONTEXT_WINDOW", "8192")),
        ),
    }
