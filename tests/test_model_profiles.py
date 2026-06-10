"""Tests for chat and embedding profile resolution."""

from types import SimpleNamespace

import pytest

from agent_runtime.core.manifest_models import resolve_manifest_embedding_profile
from agent_runtime.core.model_profiles import load_model_profile
from agent_runtime.memory.embeddings import load_embedding_profile


def test_chat_model_profile_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "http://chat/v1")
    monkeypatch.setenv("CHAT_MODEL", "chat-model")
    monkeypatch.setenv("CHAT_CONTEXT_WINDOW", "65536")
    monkeypatch.setenv("CHAT_API_KEY", "chat-key")

    profile = load_model_profile(api_key="fallback-key")

    assert profile.base_url == "http://chat/v1"
    assert profile.model_name == "chat-model"
    assert profile.context_window == 65536
    assert profile.api_key == "chat-key"
    assert profile.extra_body == {}


def test_load_model_profile_accepts_explicit_api_key(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "http://chat/v1")

    profile = load_model_profile(api_key="key")

    assert profile.base_url == "http://chat/v1"
    assert profile.api_key == "key"


def test_chat_model_profile_clamps_output_tokens_below_context_window(monkeypatch):
    monkeypatch.setenv("CHAT_MAX_TOKENS", "262144")
    monkeypatch.setenv("CHAT_CONTEXT_WINDOW", "32768")

    profile = load_model_profile(api_key="key")

    assert profile.max_tokens == 31744


def test_chat_model_profile_clamps_explicit_output_tokens(monkeypatch):
    monkeypatch.setenv("CHAT_CONTEXT_WINDOW", "262144")

    profile = load_model_profile(max_tokens=262144, api_key="key")

    assert profile.max_tokens == 32768


def test_embedding_profile_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://embedding/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-model")
    monkeypatch.setenv("MEMORY_EMBEDDING_ENABLED", "0")

    profile = load_embedding_profile(api_key="key")

    assert profile.base_url == "http://embedding/v1"
    assert profile.model_name == "embedding-model"
    assert profile.api_key == "key"
    assert profile.enabled is False


def test_chat_and_embedding_api_key_fallbacks(monkeypatch):
    profile = load_model_profile(api_key="explicit-key")
    assert profile.api_key == "explicit-key"

    emb_profile = load_embedding_profile(api_key="explicit-key")
    assert emb_profile.api_key == "explicit-key"

    monkeypatch.setenv("OPENAI_API_KEY", "global-openai-key")
    fallback_profile = load_model_profile(api_key="not-needed")
    assert fallback_profile.api_key == "global-openai-key"

    emb_profile_fallback = load_embedding_profile(api_key="not-needed")
    assert emb_profile_fallback.api_key == "global-openai-key"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    default_profile = load_model_profile()
    assert default_profile.api_key == "not-needed"


def test_chat_model_profile_loads_extra_body_json(monkeypatch):
    monkeypatch.setenv(
        "CHAT_EXTRA_BODY_JSON",
        '{"chat_template_kwargs": {"enable_thinking": false}}',
    )

    profile = load_model_profile()

    assert profile.extra_body == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_chat_model_profile_rejects_invalid_extra_body_json(monkeypatch):
    monkeypatch.setenv("CHAT_EXTRA_BODY_JSON", "[1, 2, 3]")

    with pytest.raises(ValueError, match="CHAT_EXTRA_BODY_JSON must be a JSON object"):
        load_model_profile()


def test_manifest_embedding_profile_uses_direct_manifest_overrides(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "emb-key")
    manifest = SimpleNamespace(
        model=SimpleNamespace(
            embedding="manifest-embedding",
            embedding_base_url="http://manifest-embedding/v1",
        )
    )

    profile = resolve_manifest_embedding_profile(manifest)

    assert profile.base_url == "http://manifest-embedding/v1"
    assert profile.model_name == "manifest-embedding"
    assert profile.api_key == "emb-key"
