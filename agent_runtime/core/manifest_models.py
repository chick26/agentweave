"""Resolve manifest model roles into concrete LLM and embedding profiles."""

from __future__ import annotations

from agent_runtime.core.model_profiles import ModelProfile, load_model_profiles
from agent_runtime.memory.embeddings import EmbeddingProfile, load_embedding_profile
from agent_runtime.registry.skill_registry import ManifestBase


def resolve_manifest_llm_profile(
    manifest: ManifestBase,
    *,
    model_profiles: dict[str, ModelProfile] | None = None,
    default_role: str = "orchestrator",
    api_key: str | None = None,
    max_tokens: int | None = None,
) -> ModelProfile:
    """Resolve a manifest-local LLM declaration against runtime model roles."""
    role = manifest.model.llm_role or default_role
    base = resolve_model_role_profile(role, model_profiles=model_profiles)
    return ModelProfile(
        role=role,
        base_url=manifest.model.llm_base_url or base.base_url,
        model_name=manifest.model.llm or base.model_name,
        api_key=api_key or base.api_key,
        max_tokens=max_tokens or base.max_tokens,
        context_window=base.context_window,
    )


def resolve_manifest_embedding_profile(
    manifest: ManifestBase | None,
    *,
    model_profiles: dict[str, ModelProfile] | None = None,
) -> EmbeddingProfile:
    """Resolve a manifest-local embedding declaration against runtime model roles."""
    model = manifest.model if manifest is not None else None
    role_profile = None
    if model is not None and model.embedding_role:
        role_profile = resolve_model_role_profile(
            model.embedding_role,
            model_profiles=model_profiles,
        )
    return load_embedding_profile(
        base_url=(
            model.embedding_base_url
            if model is not None and model.embedding_base_url
            else role_profile.base_url if role_profile is not None else None
        ),
        model_name=(
            model.embedding
            if model is not None and model.embedding
            else role_profile.model_name if role_profile is not None else None
        ),
        api_key=role_profile.api_key if role_profile is not None else None,
    )


def resolve_model_role_profile(
    role: str,
    *,
    model_profiles: dict[str, ModelProfile] | None = None,
) -> ModelProfile:
    if not role:
        raise ValueError("Model role is required.")
    if model_profiles is not None and role in model_profiles:
        return model_profiles[role]
    if model_profiles:
        raise ValueError(f"Unknown model role: {role}")
    profiles = load_model_profiles()
    if role not in profiles:
        raise ValueError(f"Unknown model role: {role}")
    return profiles[role]
