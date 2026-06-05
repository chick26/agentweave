"""Markdown document loading utilities for the RAG subagent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnowledgeDocument:
    source: str
    page: int
    text: str


def load_markdown_documents(paths: list[Path]) -> list[KnowledgeDocument]:
    documents: list[KnowledgeDocument] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            documents.append(
                KnowledgeDocument(
                    source=path.name,
                    page=0,
                    text=text,
                )
            )
    return documents
