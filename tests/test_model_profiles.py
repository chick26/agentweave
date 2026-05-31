from agent_runtime.core.model_profiles import load_model_profiles
from agent_runtime.memory.embeddings import load_embedding_profile


def test_model_profiles_use_env_overrides(monkeypatch):
    monkeypatch.setenv("QWEN36_BASE_URL", "http://orchestrator/v1")
    monkeypatch.setenv("QWEN36_MODEL", "orch")
    monkeypatch.setenv("QWEN36_CONTEXT_WINDOW", "65536")
    monkeypatch.setenv("QWEN32_BASE_URL", "http://worker/v1")
    monkeypatch.setenv("QWEN32_MODEL", "worker")
    monkeypatch.setenv("QWEN32_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("QWEN_VL_BASE_URL", "http://vision/v1")
    monkeypatch.setenv("QWEN_VL_MODEL", "vision")

    profiles = load_model_profiles(api_key="key")

    assert profiles["orchestrator"].base_url == "http://orchestrator/v1"
    assert profiles["orchestrator"].model_name == "orch"
    assert profiles["orchestrator"].context_window == 65536
    assert profiles["sql_worker"].base_url == "http://worker/v1"
    assert profiles["sql_worker"].model_name == "worker"
    assert profiles["sql_worker"].context_window == 32768
    assert profiles["vision_worker"].base_url == "http://vision/v1"
    assert profiles["vision_worker"].model_name == "vision"
    assert profiles["sql_worker"].api_key == "key"


def test_embedding_profile_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://embedding/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-model")
    monkeypatch.setenv("MEMORY_EMBEDDING_ENABLED", "0")

    profile = load_embedding_profile(api_key="key")

    assert profile.base_url == "http://embedding/v1"
    assert profile.model_name == "embedding-model"
    assert profile.api_key == "key"
    assert profile.enabled is False
