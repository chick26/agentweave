from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

from openai import OpenAI

from agent_runtime.common import env_bool


@dataclass(frozen=True)
class EmbeddingProfile:
    base_url: str
    model_name: str
    api_key: str
    enabled: bool = True


class EmbeddingClient:
    """Synchronous OpenAI-compatible embedding client for memory retrieval."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        self._client = OpenAI(
            base_url=profile.base_url,
            api_key=profile.api_key,
            timeout=float(os.getenv("EMBEDDING_CLIENT_TIMEOUT", "2")),
            max_retries=int(os.getenv("EMBEDDING_CLIENT_MAX_RETRIES", "0")),
        )

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.profile.enabled:
            return []
        clean_texts = [str(text) for text in texts if str(text).strip()]
        if not clean_texts:
            return []
        response = self._client.embeddings.create(
            model=self.profile.model_name,
            input=clean_texts,
        )
        ordered = sorted(response.data, key=lambda item: int(item.index))
        return [[float(value) for value in item.embedding] for item in ordered]


def load_embedding_profile(
    *,
    base_url: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    enabled: bool | None = None,
) -> EmbeddingProfile:
    return EmbeddingProfile(
        base_url=base_url
        or os.getenv("EMBEDDING_BASE_URL", "http://localhost:8002/v1"),
        model_name=model_name
        or os.getenv("EMBEDDING_MODEL", "openai-compatible-embedding-model"),
        api_key=api_key or os.getenv("OPENAI_API_KEY", "not-needed"),
        enabled=env_bool("MEMORY_EMBEDDING_ENABLED", True)
        if enabled is None
        else enabled,
    )
