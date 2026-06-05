"""RAG index construction, loading, summary metadata, and semantic search."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable, Protocol

from subagents.rag.core.markdown_loader import KnowledgeDocument


class EmbeddingClientProtocol(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


SummaryCallable = Callable[[str], str]


def build_knowledge_index(
    *,
    documents: list[KnowledgeDocument],
    embedding_client: EmbeddingClientProtocol,
    summary_callable: SummaryCallable | None = None,
) -> dict[str, Any]:
    chunks = _chunk_documents(documents)
    source_overview = _source_overview(documents=documents, chunks=chunks)
    if not chunks:
        return {
            "version": 2,
            "kb_description": "",
            "section_summaries": [],
            "source_overview": source_overview,
            "chunks": [],
            "vectors": [],
        }
    vectors: list[list[float]] = []
    batch_size = max(1, int(os.getenv("RAG_EMBED_BATCH_SIZE", "32")))
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        batch_vectors = embedding_client.embed_texts([str(chunk["text"]) for chunk in batch])
        if len(batch_vectors) != len(batch):
            raise ValueError("Embedding service returned an unexpected number of vectors.")
        vectors.extend(batch_vectors)
    section_summaries = generate_section_summaries(
        chunks=chunks,
        summary_callable=summary_callable,
    )
    kb_description = generate_kb_description(
        documents=documents,
        chunks=chunks,
        section_summaries=section_summaries,
        summary_callable=summary_callable,
    )
    return {
        "version": 2,
        "kb_description": kb_description,
        "section_summaries": section_summaries,
        "source_overview": source_overview,
        "chunks": chunks,
        "vectors": vectors,
    }


def write_knowledge_index(index: dict[str, Any], path: Path) -> Path:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return path


def load_knowledge_index(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid RAG index {path}: expected object")
    chunks = payload.get("chunks")
    vectors = payload.get("vectors")
    if not isinstance(chunks, list) or not isinstance(vectors, list):
        raise ValueError(f"Invalid RAG index {path}: chunks/vectors are required")
    if len(chunks) != len(vectors):
        raise ValueError(f"Invalid RAG index {path}: chunk/vector count mismatch")
    return payload


def search_knowledge_index(
    *,
    query: str,
    index: dict[str, Any],
    top_k: int,
    embedding_client: EmbeddingClientProtocol,
) -> list[dict[str, Any]]:
    chunks = [chunk for chunk in index.get("chunks", []) if isinstance(chunk, dict)]
    vectors = [
        [float(value) for value in vector]
        for vector in index.get("vectors", [])
        if isinstance(vector, list)
    ]
    if not chunks or not vectors:
        return []
    if len(chunks) != len(vectors):
        raise ValueError("RAG index chunks and vectors have different lengths.")
    candidate_indexes = _candidate_indexes(query=query, chunks=chunks)
    query_vectors = embedding_client.embed_texts([query])
    if len(query_vectors) != 1:
        raise ValueError("Embedding service returned an unexpected query vector.")
    query_vector = query_vectors[0]
    scored = [
        (
            _cosine_similarity(query_vector, vectors[index_value]),
            chunks[index_value],
        )
        for index_value in candidate_indexes
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            **chunk,
            "score": round(float(score), 6),
            "match_method": "prepared_index",
        }
        for score, chunk in scored[: max(1, top_k)]
    ]


def search_documents(
    *,
    query: str,
    documents: list[KnowledgeDocument],
    top_k: int,
    embedding_client: EmbeddingClientProtocol,
) -> list[dict[str, Any]]:
    chunks = _chunk_documents(documents)
    if not chunks:
        return []
    chunks = _candidate_chunks(query=query, chunks=chunks)
    texts = [query, *[chunk["text"] for chunk in chunks]]
    vectors = embedding_client.embed_texts(texts)
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
            "match_method": "embedding",
        }
        for score, chunk in scored[: max(1, top_k)]
    ]


def knowledge_index_summary(index: dict[str, Any]) -> dict[str, Any]:
    chunks = [chunk for chunk in index.get("chunks", []) if isinstance(chunk, dict)]
    return {
        "version": int(index.get("version") or 1),
        "kb_description": str(index.get("kb_description") or ""),
        "section_summaries": _as_str_list(index.get("section_summaries", [])),
        "source_overview": _as_source_overview(index.get("source_overview"), chunks),
    }


def generate_section_summaries(
    *,
    chunks: list[dict[str, Any]],
    summary_callable: SummaryCallable | None,
) -> list[str]:
    if summary_callable is None or not chunks:
        return []
    group_size = max(1, int(os.getenv("RAG_SUMMARY_GROUP_CHUNKS", "8")))
    summaries: list[str] = []
    for start in range(0, len(chunks), group_size):
        group = chunks[start:start + group_size]
        prompt = _section_summary_prompt(group)
        summary = summary_callable(prompt).strip()
        if summary:
            summaries.append(summary)
    return summaries


def generate_kb_description(
    *,
    documents: list[KnowledgeDocument],
    chunks: list[dict[str, Any]],
    section_summaries: list[str],
    summary_callable: SummaryCallable | None,
) -> str:
    if summary_callable is not None and section_summaries:
        summary_text = "\n".join(f"- {summary}" for summary in section_summaries)
        summary_text = _truncate_text(
            summary_text,
            max_chars=_int_env("RAG_KB_DESCRIPTION_PROMPT_CHARS", 24000),
        )
        prompt = (
            "请基于以下分段摘要，生成知识库总览。要求：\n"
            "1. 第一句说明知识库主题。\n"
            "2. 第二段用中文概括主要内容和适合回答的问题。\n"
            "3. 不要编造摘要中没有的信息。\n\n"
            f"{summary_text}"
        )
        description = summary_callable(prompt).strip()
        if description:
            return description
    return _fallback_kb_description(documents=documents, chunks=chunks)


def _chunk_documents(
    documents: list[KnowledgeDocument],
    *,
    chunk_chars: int | None = None,
    overlap: int | None = None,
) -> list[dict[str, Any]]:
    chunk_chars = chunk_chars or int(os.getenv("RAG_CHUNK_CHARS", "500"))
    overlap = overlap if overlap is not None else int(os.getenv("RAG_CHUNK_OVERLAP", "50"))
    chunk_chars = max(1, int(chunk_chars))
    overlap = max(0, min(int(overlap), chunk_chars - 1))
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


def _source_overview(
    *,
    documents: list[KnowledgeDocument],
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for document in documents:
        entry = by_source.setdefault(
            document.source,
            {
                "source": document.source,
                "pages": [],
                "chunk_count": 0,
                "preview": "",
            },
        )
        if document.page not in entry["pages"]:
            entry["pages"].append(document.page)
        if not entry["preview"] and document.text.strip():
            entry["preview"] = " ".join(document.text.split())[:200]
    for chunk in chunks:
        source = str(chunk.get("source") or "")
        if source in by_source:
            by_source[source]["chunk_count"] += 1
    overview = list(by_source.values())
    for entry in overview:
        entry["pages"] = sorted(entry["pages"], key=lambda item: str(item))
    overview.sort(key=lambda item: str(item["source"]))
    return overview


def _as_source_overview(value: Any, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    by_source: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        source = str(chunk.get("source") or "")
        if not source:
            continue
        entry = by_source.setdefault(
            source,
            {
                "source": source,
                "pages": [],
                "chunk_count": 0,
                "preview": "",
            },
        )
        page = chunk.get("page")
        if page not in entry["pages"]:
            entry["pages"].append(page)
        entry["chunk_count"] += 1
        if not entry["preview"]:
            entry["preview"] = str(chunk.get("text") or "")[:200]
    overview = list(by_source.values())
    for entry in overview:
        entry["pages"] = sorted(entry["pages"], key=lambda item: str(item))
    overview.sort(key=lambda item: str(item["source"]))
    return overview


def _candidate_chunks(
    *,
    query: str,
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    max_chunks = max(1, int(os.getenv("RAG_MAX_EMBED_CHUNKS", "64")))
    if len(chunks) <= max_chunks:
        return chunks
    scored = [
        (_lexical_score(query, str(chunk.get("text") or "")), index, chunk)
        for index, chunk in enumerate(chunks)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [chunk for score, _index, chunk in scored if score > 0][:max_chunks]
    if selected:
        return selected
    return chunks[:max_chunks]


def _candidate_indexes(
    *,
    query: str,
    chunks: list[dict[str, Any]],
) -> list[int]:
    max_chunks = max(1, int(os.getenv("RAG_MAX_SEARCH_CHUNKS", "128")))
    if len(chunks) <= max_chunks:
        return list(range(len(chunks)))
    scored = [
        (_lexical_score(query, str(chunk.get("text") or "")), index)
        for index, chunk in enumerate(chunks)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [index for score, index in scored if score > 0][:max_chunks]
    if selected:
        return selected
    return list(range(max_chunks))


def _lexical_score(query: str, text: str) -> int:
    query_terms = _query_terms(query)
    if not query_terms:
        return 0
    lowered = text.lower()
    return sum(1 for term in query_terms if term in lowered)


def _query_terms(query: str) -> list[str]:
    lowered = query.lower()
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9_\-]{1,}", lowered)
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    terms = ascii_terms + cjk_terms
    return [term for term in terms if term not in _GENERIC_QUERY_TERMS]


def _section_summary_prompt(chunks: list[dict[str, Any]]) -> str:
    formatted = []
    for chunk in chunks:
        formatted.append(
            "\n".join(
                [
                    f"来源: {chunk.get('source', '')}",
                    f"页码: {chunk.get('page', '')}",
                    f"内容: {chunk.get('text', '')}",
                ]
            )
        )
    prompt = (
        "请总结以下知识库片段。要求：\n"
        "1. 用中文输出 2-4 句。\n"
        "2. 保留关键主题、对象、事件或指标。\n"
        "3. 不要添加片段外的信息。\n\n"
        + "\n\n---\n\n".join(formatted)
    )
    return _truncate_text(
        prompt,
        max_chars=_int_env("RAG_SECTION_SUMMARY_PROMPT_CHARS", 12000),
    )


def _fallback_kb_description(
    *,
    documents: list[KnowledgeDocument],
    chunks: list[dict[str, Any]],
) -> str:
    source_count = len({document.source for document in documents})
    chunk_count = len(chunks)
    if source_count == 0:
        return ""
    return f"本知识库包含 {source_count} 个 Markdown 文档，已切分为 {chunk_count} 个可检索片段。"


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _truncate_text(text: str, *, max_chars: int) -> str:
    max_chars = max(1, int(max_chars))
    if len(text) <= max_chars:
        return text
    suffix = "\n\n[内容已按摘要输入预算截断]"
    keep = max(1, max_chars - len(suffix))
    return text[:keep].rstrip() + suffix


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


_GENERIC_QUERY_TERMS = {
    "document",
    "markdown",
    "文档",
    "文件",
    "资料",
    "知识库",
    "主要",
    "内容",
    "总结",
    "概览",
    "什么",
    "来源",
    "片段",
}


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
