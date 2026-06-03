from __future__ import annotations

import math
from typing import Any, Protocol

from agent_runtime.memory.embeddings import EmbeddingClient, load_embedding_profile
from subagents.rag.scripts.pdf_loader import PdfDocument


class EmbeddingClientProtocol(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def search_documents(
    *,
    query: str,
    documents: list[PdfDocument],
    top_k: int,
    embedding_client: EmbeddingClientProtocol | None = None,
) -> list[dict[str, Any]]:
    chunks = _chunk_documents(documents)
    if not chunks:
        return []
    client = embedding_client or EmbeddingClient(load_embedding_profile())
    texts = [query, *[chunk["text"] for chunk in chunks]]
    vectors = client.embed_texts(texts)
    if len(vectors) != len(texts):
        raise ValueError("Embedding service returned an unexpected number of vectors.")
    query_vector = vectors[0]
    chunk_vectors = vectors[1:]
    scored = [
        (
            _cosine_similarity(query_vector, vector),
            chunk,
        )
        for chunk, vector in zip(chunks, chunk_vectors)
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            **chunk,
            "score": round(float(score), 6),
        }
        for score, chunk in scored[: max(1, top_k)]
    ]


def _chunk_documents(
    documents: list[PdfDocument],
    *,
    chunk_chars: int = 1200,
    overlap: int = 150,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for document in documents:
        text = " ".join(document.text.split())
        if not text:
            continue
        start = 0
        chunk_index = 0
        while start < len(text):
            end = min(len(text), start + chunk_chars)
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    {
                        "source": document.source,
                        "page": document.page,
                        "chunk_id": f"{document.source}#p{document.page}:c{chunk_index}",
                        "text": chunk_text,
                    }
                )
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
            chunk_index += 1
    return chunks


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
