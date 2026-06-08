"""Model profile definitions loaded from AgentWeave environment variables."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Any


DEFAULT_OUTPUT_TOKEN_CAP = 32768
CONTEXT_WINDOW_OUTPUT_RESERVE = 1024


@dataclass(frozen=True)
class ModelProfile:
    role: str
    base_url: str
    model_name: str
    api_key: str
    max_tokens: int
    context_window: int = 32768
    extra_body: dict[str, Any] = field(default_factory=dict)


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
    def _resolve_key(*env_var_names: str) -> str:
        for name in env_var_names:
            if specific_key := os.getenv(name):
                return specific_key
        if api_key and api_key != "not-needed":
            return api_key
        return os.getenv("OPENAI_API_KEY", "not-needed")

    orch_key = _resolve_key("ORCHESTRATOR_API_KEY", "QWEN36_API_KEY")
    exec_key = _resolve_key("EXECUTOR_API_KEY")
    emb_key = _resolve_key("EMBEDDING_API_KEY")

    orchestrator_context_window = int(
        os.getenv("ORCHESTRATOR_CONTEXT_WINDOW") or os.getenv("QWEN36_CONTEXT_WINDOW") or "32768"
    )
    executor_context_window = int(os.getenv("EXECUTOR_CONTEXT_WINDOW", "32768"))

    orchestrator_requested_tokens = (
        orchestrator_max_tokens
        if orchestrator_max_tokens is not None
        else os.getenv("ORCHESTRATOR_MAX_TOKENS") or os.getenv("QWEN36_MAX_TOKENS") or "8192"
    )
    executor_requested_tokens = (
        sql_max_tokens
        if sql_max_tokens is not None
        else os.getenv("EXECUTOR_MAX_TOKENS", "2048")
    )

    return {
        "orchestrator": ModelProfile(
            role="orchestrator",
            base_url=orchestrator_base_url
            or os.getenv("ORCHESTRATOR_BASE_URL")
            or os.getenv("QWEN36_BASE_URL", "http://localhost:8000/v1"),
            model_name=orchestrator_model
            or os.getenv("ORCHESTRATOR_MODEL")
            or os.getenv("QWEN36_MODEL", "qwen3.6-27b"),
            api_key=orch_key,
            max_tokens=normalize_output_tokens(
                orchestrator_requested_tokens,
                default=8192,
                context_window=orchestrator_context_window,
                cap=int(os.getenv("ORCHESTRATOR_MAX_OUTPUT_TOKENS_CAP", str(DEFAULT_OUTPUT_TOKEN_CAP))),
            ),
            context_window=orchestrator_context_window,
            extra_body=_load_extra_body("ORCHESTRATOR", legacy_prefix="QWEN36"),
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
            max_tokens=normalize_output_tokens(
                executor_requested_tokens,
                default=2048,
                context_window=executor_context_window,
                cap=int(os.getenv("EXECUTOR_MAX_OUTPUT_TOKENS_CAP", str(DEFAULT_OUTPUT_TOKEN_CAP))),
            ),
            context_window=executor_context_window,
            extra_body=_load_extra_body("EXECUTOR"),
        ),
        "embedding": ModelProfile(
            role="embedding",
            base_url=os.getenv("EMBEDDING_BASE_URL", "http://localhost:8002/v1"),
            model_name=os.getenv("EMBEDDING_MODEL", "openai-compatible-embedding-model"),
            api_key=emb_key,
            max_tokens=0,
            context_window=int(os.getenv("EMBEDDING_CONTEXT_WINDOW", "8192")),
            extra_body=_load_extra_body("EMBEDDING"),
        ),
    }


def normalize_output_tokens(
    value: Any,
    *,
    default: int,
    context_window: int,
    cap: int = DEFAULT_OUTPUT_TOKEN_CAP,
) -> int:
    """Normalize a generation-token budget so it cannot consume the full context window."""

    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = int(default)
    if requested <= 0:
        requested = int(default)
    context_limit = max(1, int(context_window) - CONTEXT_WINDOW_OUTPUT_RESERVE)
    return max(1, min(requested, int(cap), context_limit))


def _load_extra_body(prefix: str, *, legacy_prefix: str = "") -> dict[str, Any]:
    raw = os.getenv(f"{prefix}_EXTRA_BODY_JSON", "").strip()
    if not raw and legacy_prefix:
        raw = os.getenv(f"{legacy_prefix}_EXTRA_BODY_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{prefix}_EXTRA_BODY_JSON must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{prefix}_EXTRA_BODY_JSON must be a JSON object.")
    return payload
