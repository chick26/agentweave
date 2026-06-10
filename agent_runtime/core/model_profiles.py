"""Chat model profile definitions loaded from AgentWeave environment variables."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Any


DEFAULT_OUTPUT_TOKEN_CAP = 32768
CONTEXT_WINDOW_OUTPUT_RESERVE = 1024


@dataclass(frozen=True)
class ModelProfile:
    base_url: str
    model_name: str
    api_key: str
    max_tokens: int
    context_window: int = 32768
    extra_body: dict[str, Any] = field(default_factory=dict)


def load_model_profile(
    *,
    base_url: str | None = None,
    model_name: str | None = None,
    max_tokens: int | None = None,
    api_key: str | None = None,
) -> ModelProfile:
    context_window = int(os.getenv("CHAT_CONTEXT_WINDOW", "32768"))
    requested_tokens = (
        max_tokens if max_tokens is not None else os.getenv("CHAT_MAX_TOKENS", "8192")
    )
    resolved_api_key = (
        os.getenv("CHAT_API_KEY")
        or (api_key if api_key and api_key != "not-needed" else "")
        or os.getenv("OPENAI_API_KEY", "not-needed")
    )
    return ModelProfile(
        base_url=base_url or os.getenv("CHAT_BASE_URL", "http://localhost:8000/v1"),
        model_name=model_name or os.getenv("CHAT_MODEL", "qwen3.6-27b"),
        api_key=resolved_api_key,
        max_tokens=normalize_output_tokens(
            requested_tokens,
            default=8192,
            context_window=context_window,
            cap=int(os.getenv("CHAT_MAX_OUTPUT_TOKENS_CAP", str(DEFAULT_OUTPUT_TOKEN_CAP))),
        ),
        context_window=context_window,
        extra_body=_load_extra_body("CHAT"),
    )


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


def _load_extra_body(prefix: str) -> dict[str, Any]:
    raw = os.getenv(f"{prefix}_EXTRA_BODY_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{prefix}_EXTRA_BODY_JSON must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{prefix}_EXTRA_BODY_JSON must be a JSON object.")
    return payload
