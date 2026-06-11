"""Tests for RAG indexing, summary metadata, and extension tools."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from agent_runtime.core.context import RuntimeContext
from agent_runtime.core.model_profiles import ModelProfile
from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.storage.artifact_store import ArtifactStore
from subagents.rag import extension as rag_tools
from subagents.rag.core.markdown_loader import KnowledgeDocument
from agentweave_prepare import rag as prepare_index
from agentweave_prepare.rag import prepare_rag_index
from subagents.rag.core.retrieval import (
    build_knowledge_index,
    generate_kb_description,
    knowledge_index_summary,
    search_documents,
    search_knowledge_index,
    write_knowledge_index,
)


class FakeEmbeddingClient:
    def embed_texts(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([1.0, 0.0] if "target" in lowered else [0.0, 1.0])
        return vectors


def _model_profile() -> ModelProfile:
    return ModelProfile(
        base_url="http://example.test/v1",
        model_name="chat",
        api_key="not-needed",
        max_tokens=128,
    )


def test_rag_manifest_declares_local_markdown_data():
    registry = AgentRegistry(subagents_root=Path("subagents"))
    manifest = registry.get("rag")

    assert manifest.location.name == "AGENT.yaml"
    assert manifest.body.startswith("你是 RAG Knowledge Subagent")
    assert manifest.extension.module == "subagents.rag.extension"
    assert manifest.tools == []
    assert manifest.execution.mode == "worker"


def test_rag_knowledge_paths_use_project_examples_dir(tmp_path):
    md_path = tmp_path / "data" / "examples" / "rag" / "demo.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("# Demo\nknowledge", encoding="utf-8")

    paths = prepare_index._knowledge_paths(runtime_root=tmp_path)

    assert paths == [md_path]


def test_rag_search_documents_returns_top_k_chunks_with_sources():
    documents = [
        KnowledgeDocument(
            source="knowledge.md",
            page=1,
            text="general background",
        ),
        KnowledgeDocument(
            source="knowledge.md",
            page=2,
            text="target answer appears in this page",
        ),
    ]

    chunks = search_documents(
        query="target",
        documents=documents,
        top_k=1,
        embedding_client=FakeEmbeddingClient(),
    )

    assert len(chunks) == 1
    assert chunks[0]["page"] == 2
    assert chunks[0]["source"] == "knowledge.md"
    assert chunks[0]["score"] == 1.0
    assert "target answer" in chunks[0]["text"]


def test_rag_prepared_index_searches_without_embedding_all_chunks():
    documents = [
        KnowledgeDocument(source="knowledge.md", page=1, text="general background"),
        KnowledgeDocument(source="knowledge.md", page=2, text="target answer appears"),
    ]
    index = build_knowledge_index(
        documents=documents,
        embedding_client=FakeEmbeddingClient(),
    )

    chunks = search_knowledge_index(
        query="target",
        index=index,
        top_k=1,
        embedding_client=FakeEmbeddingClient(),
    )

    assert len(index["chunks"]) == 2
    assert len(index["vectors"]) == 2
    assert chunks[0]["page"] == 2
    assert chunks[0]["match_method"] == "prepared_index"


def test_rag_overview_query_is_not_keyword_short_circuited():
    documents = [
        KnowledgeDocument(source="knowledge.md", page=1, text="general background"),
        KnowledgeDocument(source="knowledge.md", page=2, text="target answer appears"),
    ]

    chunks = search_documents(
        query="总结",
        documents=documents,
        top_k=1,
        embedding_client=FakeEmbeddingClient(),
    )

    assert chunks[0]["match_method"] == "embedding"
    assert chunks[0]["page"] == 1


def test_rag_index_writes_v2_summary_fields_with_callable():
    calls = []

    def fake_summary(prompt):
        calls.append(prompt)
        if "分段摘要" in prompt:
            return "知识库介绍目标答案资料。"
        return "目标资料摘要。"

    documents = [
        KnowledgeDocument(source="knowledge.md", page=1, text="target answer appears"),
    ]

    index = build_knowledge_index(
        documents=documents,
        embedding_client=FakeEmbeddingClient(),
        summary_callable=fake_summary,
    )

    assert index["version"] == 2
    assert index["section_summaries"] == ["目标资料摘要。"]
    assert index["kb_description"] == "知识库介绍目标答案资料。"
    assert index["source_overview"][0]["source"] == "knowledge.md"
    assert len(calls) == 2


def test_rag_index_without_summary_callable_still_builds_v2():
    index = build_knowledge_index(
        documents=[KnowledgeDocument(source="knowledge.md", page=1, text="target")],
        embedding_client=FakeEmbeddingClient(),
    )

    assert index["version"] == 2
    assert index["section_summaries"] == []
    assert "1 个 Markdown 文档" in index["kb_description"]


def test_rag_kb_description_summary_prompt_is_truncated(monkeypatch):
    calls = []

    def fake_summary(prompt):
        calls.append(prompt)
        return "有预算的知识库总览。"

    monkeypatch.setenv("RAG_KB_DESCRIPTION_PROMPT_CHARS", "1200")

    description = generate_kb_description(
        documents=[KnowledgeDocument(source="knowledge.md", page=1, text="target")],
        chunks=[{"source": "knowledge.md", "page": 1, "text": "target"}],
        section_summaries=[f"第 {index} 段摘要：" + ("x" * 200) for index in range(100)],
        summary_callable=fake_summary,
    )

    assert description == "有预算的知识库总览。"
    assert calls
    assert len(calls[0]) < 1800
    assert "内容已按摘要输入预算截断" in calls[0]


def test_rag_summary_failure_is_not_silently_fallbacked():
    def failing_summary(_prompt):
        raise RuntimeError("context window exceeded")

    with pytest.raises(RuntimeError, match="context window exceeded"):
        build_knowledge_index(
            documents=[KnowledgeDocument(source="knowledge.md", page=1, text="target")],
            embedding_client=FakeEmbeddingClient(),
            summary_callable=failing_summary,
        )


def test_rag_summary_extra_body_uses_profile_config():
    profile = SimpleNamespace(
        extra_body={"chat_template_kwargs": {"enable_thinking": False}}
    )

    assert prepare_index._summary_extra_body(profile) == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_rag_summary_output_strips_thinking_blocks():
    cleaned = prepare_index._clean_summary_output(
        "<think>这里是推理过程，不应写入索引。</think>\n\n知识库主要介绍目标资料。"
    )

    assert cleaned == "知识库主要介绍目标资料。"


def test_rag_legacy_v1_index_summary_is_compatible():
    summary = knowledge_index_summary(
        {
            "version": 1,
            "chunks": [
                {
                    "source": "knowledge.md",
                    "page": 1,
                    "chunk_id": "knowledge.md#p1:c0",
                    "text": "legacy target answer",
                }
            ],
            "vectors": [[1.0, 0.0]],
        }
    )

    assert summary["version"] == 1
    assert summary["kb_description"] == ""
    assert summary["source_overview"][0]["source"] == "knowledge.md"


def test_rag_retrieval_has_no_agent_runtime_dependency():
    text = Path("subagents/rag/core/retrieval.py").read_text(encoding="utf-8")

    assert "agent_runtime" not in text


def test_rag_tool_returns_clear_error_when_index_not_prepared(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_INDEX_PATH", raising=False)
    registry = AgentRegistry(subagents_root=Path("subagents"))
    run_ctx = RuntimeContext(
        run_id="rag-run",
        runtime_root=tmp_path,
        active_subagent="rag",
        model_profile=_model_profile(),
        agent_registry=registry,
    )

    output = asyncio.run(
        rag_tools.search_knowledge_base.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="search_knowledge_base",
                tool_call_id="call_rag",
                tool_arguments=json.dumps({"query": "anything", "top_k": 3}),
            ),
            json.dumps({"query": "anything", "top_k": 3}),
        )
    )
    payload = json.loads(output)
    tool_events = [
        event for event in run_ctx.events
        if event["kind"] in {"tool_call_start", "tool_result", "tool_call_end"}
    ]

    assert "agentweave-prepare-rag" in payload["error"]
    assert payload["chunks"] == []
    assert [event["kind"] for event in tool_events] == [
        "tool_call_start",
        "tool_result",
        "tool_call_end",
    ]
    assert tool_events[-1]["payload"]["status"] == "failed"


def test_rag_tool_searches_prepared_index(tmp_path, monkeypatch):
    index_path = tmp_path / "rag_index.json"
    write_knowledge_index(
        {
            "version": 1,
            "chunks": [
                {
                    "source": "knowledge.md",
                    "page": 1,
                    "chunk_id": "knowledge.md#p1:c0",
                    "text": "target answer",
                }
            ],
            "vectors": [[1.0, 0.0]],
        },
        index_path,
    )
    monkeypatch.setenv("RAG_INDEX_PATH", str(index_path))
    monkeypatch.setattr(
        "agent_runtime.subagent_api.SubagentContext.embedding_client",
        lambda self: FakeEmbeddingClient(),
    )
    artifact_store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    run_ctx = RuntimeContext(
        run_id="rag-index-run",
        runtime_root=tmp_path,
        active_subagent="rag",
        model_profile=_model_profile(),
        agent_registry=AgentRegistry(subagents_root=Path("subagents")),
        artifact_store=artifact_store,
    )

    output = asyncio.run(
        rag_tools.search_knowledge_base.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="search_knowledge_base",
                tool_call_id="call_rag",
                tool_arguments=json.dumps({"query": "target", "top_k": 1}),
            ),
            json.dumps({"query": "target", "top_k": 1}),
        )
    )
    payload = json.loads(output)

    assert payload["count"] == 1
    assert payload["result_id"].startswith("res_")
    assert payload["chunks"][0]["chunk_id"] == "knowledge.md#p1:c0"
    assert payload["chunks"][0]["match_method"] == "prepared_index"
    metadata = artifact_store.get_metadata(payload["result_id"], run_id="rag-index-run")
    assert metadata["artifact_type"] == "rag_chunks"
    assert metadata["metadata"]["query"] == "target"
    assert artifact_store.get_artifact_page(
        payload["result_id"],
        offset=0,
        limit=10,
        run_id="rag-index-run",
    )["rows"][0]["chunk_id"] == "knowledge.md#p1:c0"


def test_rag_summary_tool_returns_prepared_index_summary(tmp_path, monkeypatch):
    index_path = tmp_path / "rag_index.json"
    write_knowledge_index(
        {
            "version": 2,
            "kb_description": "这是知识库总览。",
            "section_summaries": ["第一部分摘要。"],
            "source_overview": [
                {
                    "source": "knowledge.md",
                    "pages": [1],
                    "chunk_count": 1,
                    "preview": "target answer",
                }
            ],
            "chunks": [
                {
                    "source": "knowledge.md",
                    "page": 1,
                    "chunk_id": "knowledge.md#p1:c0",
                    "text": "target answer",
                }
            ],
            "vectors": [[1.0, 0.0]],
        },
        index_path,
    )
    monkeypatch.setenv("RAG_INDEX_PATH", str(index_path))
    run_ctx = RuntimeContext(
        run_id="rag-summary-run",
        runtime_root=tmp_path,
        active_subagent="rag",
        model_profile=_model_profile(),
        agent_registry=AgentRegistry(subagents_root=Path("subagents")),
    )

    output = asyncio.run(
        rag_tools.get_knowledge_base_summary.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="get_knowledge_base_summary",
                tool_call_id="call_rag_summary",
                tool_arguments=json.dumps({}),
            ),
            json.dumps({}),
        )
    )
    payload = json.loads(output)

    assert payload["summary"] == "这是知识库总览。"
    assert payload["section_summaries"] == ["第一部分摘要。"]
    assert payload["source_overview"][0]["source"] == "knowledge.md"
    assert run_ctx.events[-1]["payload"]["status"] == "completed"


def test_prepare_rag_index_reuses_existing_index_without_overwrite(tmp_path):
    output = tmp_path / ".agentweave" / "rag_index.json"
    write_knowledge_index(
        {"version": 1, "chunks": [], "vectors": []},
        output,
    )

    prepared = prepare_rag_index(root=tmp_path, output=output, overwrite=False)

    assert prepared == output
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "version": 1,
        "chunks": [],
        "vectors": [],
    }


def test_rag_toolkit_emits_successful_search(tmp_path, monkeypatch):
    index_path = tmp_path / "rag_index.json"
    write_knowledge_index(
        {
            "version": 1,
            "chunks": [
                {
                    "source": "knowledge.md",
                    "page": 1,
                    "chunk_id": "knowledge.md#p1:c0",
                    "text": "target answer",
                }
            ],
            "vectors": [[1.0, 0.0]],
        },
        index_path,
    )
    monkeypatch.setenv("RAG_INDEX_PATH", str(index_path))
    monkeypatch.setattr(
        "agent_runtime.subagent_api.SubagentContext.embedding_client",
        lambda self: FakeEmbeddingClient(),
    )
    run_ctx = RuntimeContext(
        run_id="rag-success-run",
        runtime_root=Path("."),
        active_subagent="rag",
        model_profile=_model_profile(),
        agent_registry=AgentRegistry(subagents_root=Path("subagents")),
    )

    output = asyncio.run(
        rag_tools.search_knowledge_base.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="search_knowledge_base",
                tool_call_id="call_rag",
                tool_arguments=json.dumps({"query": "target", "top_k": 1}),
            ),
            json.dumps({"query": "target", "top_k": 1}),
        )
    )
    payload = json.loads(output)

    assert payload["count"] == 1
    assert payload["chunks"][0]["chunk_id"] == "knowledge.md#p1:c0"
    assert run_ctx.events[-1]["payload"]["status"] == "completed"
