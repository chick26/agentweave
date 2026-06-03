import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from agents.tool_context import ToolContext

from agent_runtime.core.context import RunContext
from agent_runtime.registry.skill_registry import AgentRegistry
from subagents.rag import env as rag_env
from subagents.rag import tools as rag_tools
from subagents.rag.scripts import pdf_loader
from subagents.rag.scripts.retrieval import search_documents


class FakeEmbeddingClient:
    def embed_texts(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([1.0, 0.0] if "target" in lowered else [0.0, 1.0])
        return vectors


def test_rag_manifest_declares_local_pdf_data():
    registry = AgentRegistry(subagents_root=Path("subagents"))
    manifest = registry.get("rag")

    assert manifest.location.name == "AGENT.yaml"
    assert manifest.body.startswith("你是 RAG Knowledge Subagent")
    assert manifest.runtime_env.setup_module == "subagents.rag.env"
    assert manifest.data.roots == ["subagents/rag/data"]
    assert "search_knowledge_base" in manifest.tools


def test_rag_pdf_paths_use_manifest_data_roots(tmp_path):
    pdf_path = tmp_path / "subagents" / "rag" / "data" / "demo.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_text("fake pdf placeholder", encoding="utf-8")
    manifest = SimpleNamespace(
        data=SimpleNamespace(roots=["subagents/rag/data"], globs=["*.pdf"])
    )

    paths = rag_env._pdf_paths(runtime_root=tmp_path, manifest=manifest)

    assert paths == [pdf_path]


def test_rag_search_documents_returns_top_k_chunks_with_sources():
    documents = [
        pdf_loader.PdfDocument(
            source="knowledge.pdf",
            page=1,
            text="general background",
        ),
        pdf_loader.PdfDocument(
            source="knowledge.pdf",
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
    assert chunks[0]["source"] == "knowledge.pdf"
    assert chunks[0]["score"] == 1.0
    assert "target answer" in chunks[0]["text"]


def test_rag_tool_returns_clear_error_when_no_pdf(tmp_path):
    registry = AgentRegistry(subagents_root=Path("subagents"))
    run_ctx = RunContext(
        run_id="rag-run",
        runtime_root=tmp_path,
        active_subagent="rag",
        backend=None,
        model_profiles={},
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

    assert payload["error"] == "No PDF files found for RAG knowledge base."
    assert payload["chunks"] == []
    assert [event["kind"] for event in tool_events] == [
        "tool_call_start",
        "tool_result",
        "tool_call_end",
    ]
    assert tool_events[-1]["payload"]["status"] == "failed"


def test_rag_tool_emits_successful_search(monkeypatch):
    def fake_search_local_knowledge_base(**kwargs):
        return {
            "query": kwargs["query"],
            "chunks": [
                {
                    "source": "knowledge.pdf",
                    "page": 1,
                    "chunk_id": "knowledge.pdf#p1:c0",
                    "text": "target answer",
                    "score": 1.0,
                }
            ],
            "count": 1,
            "error": "",
        }

    monkeypatch.setattr(rag_tools, "search_local_knowledge_base", fake_search_local_knowledge_base)
    run_ctx = RunContext(
        run_id="rag-success-run",
        runtime_root=Path("."),
        active_subagent="rag",
        backend=None,
        model_profiles={},
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
    assert payload["chunks"][0]["chunk_id"] == "knowledge.pdf#p1:c0"
    assert run_ctx.events[-1]["payload"]["status"] == "completed"
