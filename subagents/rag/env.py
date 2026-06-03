from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.registry.skill_registry import AgentManifest
from subagents.rag.scripts.pdf_loader import load_pdf_documents
from subagents.rag.scripts.retrieval import search_documents


def search_local_knowledge_base(
    *,
    query: str,
    top_k: int,
    root: Path | None,
    manifest: AgentManifest | None,
) -> dict[str, Any]:
    runtime_root = root or Path.cwd()
    pdf_paths = _pdf_paths(runtime_root=runtime_root, manifest=manifest)
    if not pdf_paths:
        return {
            "query": query,
            "chunks": [],
            "count": 0,
            "error": "No PDF files found for RAG knowledge base.",
        }
    documents = load_pdf_documents(pdf_paths)
    if not documents:
        return {
            "query": query,
            "chunks": [],
            "count": 0,
            "error": "No extractable text found in RAG PDF files.",
        }
    chunks = search_documents(query=query, documents=documents, top_k=top_k)
    return {
        "query": query,
        "chunks": chunks,
        "count": len(chunks),
        "error": "",
    }


def _pdf_paths(*, runtime_root: Path, manifest: AgentManifest | None) -> list[Path]:
    roots = (
        manifest.data.roots
        if manifest is not None and manifest.data.roots
        else ["subagents/rag/data"]
    )
    globs = manifest.data.globs if manifest is not None and manifest.data.globs else ["*.pdf"]
    paths: list[Path] = []
    for raw_root in roots:
        base = Path(raw_root)
        if not base.is_absolute():
            base = runtime_root / base
        for pattern in globs:
            paths.extend(path for path in base.glob(pattern) if path.is_file())
    return sorted(set(paths), key=lambda path: str(path))
