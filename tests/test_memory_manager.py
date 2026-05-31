from pathlib import Path

import pytest

from agent_runtime.memory.embeddings import EmbeddingProfile
from agent_runtime.memory.memory_manager import MemoryManager, TodoItem
from agent_runtime.memory.memory_store import MemoryStore
from agent_runtime.registry.skill_registry import AgentRegistry


class FakeEmbeddingClient:
    def __init__(self, *, fail: bool = False):
        self.profile = EmbeddingProfile(
            base_url="http://embedding.test/v1",
            model_name="fake-embedding",
            api_key="key",
        )
        self.fail = fail

    def embed_texts(self, texts):
        if self.fail:
            raise RuntimeError("embedding down")
        return [_fake_vector(text) for text in texts]


def test_memory_manager_builds_orchestrator_context(tmp_path):
    manager = MemoryManager(MemoryStore(tmp_path / "agent_memory.sqlite"))
    manager.write("project", "metric_rule", "可用资源默认按柜数统计。")
    manager.write("user", "language", "默认使用中文。")
    manager.write_session_summary("abc", "用户刚刚确认查询香港资源。")

    context = manager.build_orchestrator_context("abc")

    assert "[project]" in context
    assert "可用资源默认按柜数统计。" in context
    assert "[user]" in context
    assert "默认使用中文。" in context
    assert "[session_summary]" in context
    assert "用户刚刚确认查询香港资源。" in context


def test_memory_manager_builds_skill_context(tmp_path):
    manager = MemoryManager(MemoryStore(tmp_path / "agent_memory.sqlite"))
    manager.write("project", "metric_rule", "保留 SQL 口径。")
    manager.write("skill:text2sql", "value_rule", "实体值先搜索候选。")
    manager.write("user", "private_pref", "不要注入 worker。")
    manifest = AgentRegistry(subagents_root=Path("subagents")).get("text2sql")

    context = manager.build_skill_context(manifest)

    assert "保留 SQL 口径。" in context
    assert "实体值先搜索候选。" in context
    assert "不要注入 worker。" not in context


def test_update_todo_validates_single_in_progress(tmp_path):
    manager = MemoryManager(MemoryStore(tmp_path / "agent_memory.sqlite"))

    with pytest.raises(ValueError):
        manager.update_todo(
            "abc",
            [
                TodoItem("第一步", "in_progress"),
                TodoItem("第二步", "in_progress"),
            ],
        )


def test_todo_context_is_session_local(tmp_path):
    manager = MemoryManager(MemoryStore(tmp_path / "agent_memory.sqlite"))
    manager.update_todo(
        "abc",
        [
            TodoItem("确认查询领域", "completed"),
            TodoItem("执行 SQL 查询", "in_progress"),
        ],
    )

    context = manager.build_orchestrator_context("abc")

    assert "[todo_working_memory]" in context
    assert "[completed] 确认查询领域" in context
    assert "[in_progress] 执行 SQL 查询" in context
    assert manager.build_orchestrator_context("other") == ""
    assert manager.load_namespace("project") == []


def test_memory_manager_retrieves_vector_context_and_tracks_events(tmp_path):
    manager = MemoryManager(
        MemoryStore(tmp_path / "agent_memory.sqlite"),
        embedding_client=FakeEmbeddingClient(),
    )
    manager.write("project", "idc_metric", "IDC 可用资源默认按机柜统计。")
    manager.write("project", "fault_metric", "海缆故障默认按 fault_id 统计。")
    events = []

    context = manager.build_orchestrator_context(
        "abc",
        current_query="IDC 机柜资源",
        retrieval_events=events,
    )

    assert "IDC 可用资源默认按机柜统计。" in context
    assert events[0]["strategy"] == "vector"
    assert events[0]["records"][0]["namespace"] == "project"
    assert events[0]["records"][0]["key"] == "idc_metric"
    assert events[0]["records"][0]["content"] == "IDC 可用资源默认按机柜统计。"
    assert manager.store.load_vectors(embedding_model="fake-embedding", namespaces=["project"])


def test_memory_manager_falls_back_when_embedding_is_unavailable(tmp_path):
    manager = MemoryManager(
        MemoryStore(tmp_path / "agent_memory.sqlite"),
        embedding_client=FakeEmbeddingClient(fail=True),
    )
    manager.store.write("project", "language", "回答默认使用中文。")

    result = manager.retrieve("中文", ["project"], limit=3)

    assert [record.key for record in result.records] == ["language"]
    assert result.strategy == "lexical_fallback"
    assert result.fallback is True


def test_memory_manager_disabled_skips_durable_memory_but_keeps_todos(tmp_path):
    store = MemoryStore(tmp_path / "agent_memory.sqlite")
    store.write("project", "metric_rule", "保留项目口径。")
    store.write("session:abc", "summary", "不要注入摘要。")
    manager = MemoryManager(store, enabled=False)
    manager.update_todo("abc", [TodoItem("继续当前任务", "in_progress")])

    result = manager.retrieve("项目口径", ["project"])
    context = manager.build_orchestrator_context("abc", current_query="项目口径")

    assert result.records == []
    assert result.strategy == "disabled"
    assert "保留项目口径。" not in context
    assert "不要注入摘要。" not in context
    assert "[todo_working_memory]" in context


def _fake_vector(text: str) -> list[float]:
    if "IDC" in text or "机柜" in text or "资源" in text:
        return [1.0, 0.0]
    if "海缆" in text or "故障" in text:
        return [0.0, 1.0]
    return [0.5, 0.5]
