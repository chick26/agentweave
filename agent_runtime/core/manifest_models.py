"""Resolve manifest-local embedding declarations."""

from __future__ import annotations

from agent_runtime.memory.embeddings import EmbeddingProfile, load_embedding_profile
from agent_runtime.registry.skill_registry import ManifestBase


def resolve_manifest_embedding_profile(
    manifest: ManifestBase | None,
) -> EmbeddingProfile:
    """Resolve a manifest-local embedding declaration against embedding defaults."""
    model = manifest.model if manifest is not None else None
    return load_embedding_profile(
        base_url=(
            model.embedding_base_url
            if model is not None and model.embedding_base_url
            else None
        ),
        model_name=(
            model.embedding
            if model is not None and model.embedding
            else None
        ),
    )
