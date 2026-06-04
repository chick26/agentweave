from types import SimpleNamespace

from agent_runtime.core.manifest_models import (
    resolve_manifest_embedding_profile,
    resolve_manifest_llm_profile,
)
from agent_runtime.core.model_profiles import ModelProfile
from agent_runtime.core.model_profiles import load_model_profiles
from agent_runtime.memory.embeddings import load_embedding_profile


def test_model_profiles_use_env_overrides(monkeypatch):
    monkeypatch.setenv("QWEN36_BASE_URL", "http://orchestrator/v1")
    monkeypatch.setenv("QWEN36_MODEL", "orch")
    monkeypatch.setenv("QWEN36_CONTEXT_WINDOW", "65536")
    monkeypatch.setenv("EXECUTOR_BASE_URL", "http://executor/v1")
    monkeypatch.setenv("EXECUTOR_MODEL", "executor")
    monkeypatch.setenv("EXECUTOR_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://embedding/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding")

    profiles = load_model_profiles(api_key="key")

    assert profiles["orchestrator"].base_url == "http://orchestrator/v1"
    assert profiles["orchestrator"].model_name == "orch"
    assert profiles["orchestrator"].context_window == 65536
    assert profiles["executor"].base_url == "http://executor/v1"
    assert profiles["executor"].model_name == "executor"
    assert profiles["executor"].context_window == 32768
    assert profiles["embedding"].base_url == "http://embedding/v1"
    assert profiles["embedding"].model_name == "embedding"
    assert profiles["executor"].api_key == "key"


def test_embedding_profile_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://embedding/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-model")
    monkeypatch.setenv("MEMORY_EMBEDDING_ENABLED", "0")

    profile = load_embedding_profile(api_key="key")

    assert profile.base_url == "http://embedding/v1"
    assert profile.model_name == "embedding-model"
    assert profile.api_key == "key"
    assert profile.enabled is False


def test_model_profiles_granular_api_keys(monkeypatch):
    monkeypatch.setenv("QWEN36_API_KEY", "orch-key")
    monkeypatch.setenv("EXECUTOR_API_KEY", "exec-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "emb-key")

    profiles = load_model_profiles(api_key="fallback-key")

    assert profiles["orchestrator"].api_key == "orch-key"
    assert profiles["executor"].api_key == "exec-key"
    assert profiles["embedding"].api_key == "emb-key"

    # Also test load_embedding_profile directly
    emb_profile = load_embedding_profile(api_key="fallback-key")
    assert emb_profile.api_key == "emb-key"


def test_model_profiles_granular_api_key_fallbacks(monkeypatch):
    # Case 1: no specific envs, should use explicit api_key arg if valid
    profiles = load_model_profiles(api_key="explicit-key")
    assert profiles["orchestrator"].api_key == "explicit-key"
    assert profiles["executor"].api_key == "explicit-key"
    assert profiles["embedding"].api_key == "explicit-key"

    emb_profile = load_embedding_profile(api_key="explicit-key")
    assert emb_profile.api_key == "explicit-key"

    # Case 2: api_key is "not-needed" or None, should fallback to OPENAI_API_KEY env
    monkeypatch.setenv("OPENAI_API_KEY", "global-openai-key")
    profiles_fallback = load_model_profiles(api_key="not-needed")
    assert profiles_fallback["orchestrator"].api_key == "global-openai-key"
    assert profiles_fallback["executor"].api_key == "global-openai-key"
    assert profiles_fallback["embedding"].api_key == "global-openai-key"

    emb_profile_fallback = load_embedding_profile(api_key="not-needed")
    assert emb_profile_fallback.api_key == "global-openai-key"

    # Case 3: nothing set, should default to "not-needed"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    profiles_default = load_model_profiles()
    assert profiles_default["orchestrator"].api_key == "not-needed"
    assert profiles_default["executor"].api_key == "not-needed"
    assert profiles_default["embedding"].api_key == "not-needed"


def test_manifest_llm_profile_resolves_runtime_role_with_overrides():
    manifest = SimpleNamespace(
        model=SimpleNamespace(
            llm_role="executor",
            llm="manifest-chat",
            llm_base_url="",
        )
    )
    profiles = {
        "executor": ModelProfile(
            role="executor",
            base_url="http://executor/v1",
            model_name="executor-chat",
            api_key="exec-key",
            max_tokens=2048,
            context_window=32768,
        )
    }

    profile = resolve_manifest_llm_profile(
        manifest,
        model_profiles=profiles,
        default_role="orchestrator",
    )

    assert profile.role == "executor"
    assert profile.base_url == "http://executor/v1"
    assert profile.model_name == "manifest-chat"
    assert profile.api_key == "exec-key"


def test_manifest_embedding_profile_resolves_runtime_role():
    manifest = SimpleNamespace(
        model=SimpleNamespace(
            embedding_role="embedding",
            embedding="",
            embedding_base_url="",
        )
    )
    profiles = {
        "embedding": ModelProfile(
            role="embedding",
            base_url="http://embedding/v1",
            model_name="qwen3-embedding-4b",
            api_key="emb-key",
            max_tokens=0,
            context_window=8192,
        )
    }

    profile = resolve_manifest_embedding_profile(
        manifest,
        model_profiles=profiles,
    )

    assert profile.base_url == "http://embedding/v1"
    assert profile.model_name == "qwen3-embedding-4b"
    assert profile.api_key == "emb-key"
