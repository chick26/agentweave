"""Result artifact formatting contracts and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_runtime.common import columns_from_rows


@dataclass(frozen=True)
class ResultArtifactSpec:
    """Normalized artifact payload ready for ResultStore persistence."""

    artifact_type: str
    title: str = ""
    source: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    preview_rows: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    row_count: int | None = None
    row_count_is_exact: bool = True

    def normalized(self) -> "ResultArtifactSpec":
        rows = _dict_rows(self.rows)
        preview_rows = _dict_rows(self.preview_rows) if self.preview_rows else rows[:5]
        columns = list(self.columns or columns_from_rows(rows or preview_rows))
        row_count = int(self.row_count) if self.row_count is not None else len(rows)
        return ResultArtifactSpec(
            artifact_type=str(self.artifact_type or "generic_artifact"),
            title=str(self.title or self.artifact_type or "Result Artifact"),
            source=str(self.source or ""),
            rows=rows,
            columns=columns,
            preview_rows=preview_rows,
            metadata=dict(self.metadata or {}),
            row_count=max(0, row_count),
            row_count_is_exact=bool(self.row_count_is_exact),
        )

    def to_summary(
        self,
        *,
        result_id: str,
        created_at: str = "",
        stored_count: int | None = None,
    ) -> dict[str, Any]:
        normalized = self.normalized()
        stored_count = (
            int(stored_count)
            if stored_count is not None
            else len(normalized.rows)
        )
        return {
            "result_id": str(result_id),
            "artifact_type": normalized.artifact_type,
            "title": normalized.title,
            "source": normalized.source,
            "preview": {
                "kind": "rows",
                "columns": normalized.columns,
                "rows": normalized.preview_rows,
            },
            "metrics": {
                "row_count": normalized.row_count,
                "stored_count": max(0, stored_count),
                "count_is_exact": normalized.row_count_is_exact,
                "truncated": not normalized.row_count_is_exact,
            },
            "metadata": normalized.metadata,
            "created_at": created_at,
        }


class ResultFormatter(Protocol):
    """Subagent/core-owned adapter from business payload to result artifact."""

    artifact_type: str

    def format(self, payload: dict[str, Any]) -> ResultArtifactSpec:
        """Return a normalized artifact spec for one tool/subagent payload."""


class ResultFormatterRegistry:
    """Lookup table for artifact formatters registered by core and extensions."""

    def __init__(self) -> None:
        self._formatters: dict[str, ResultFormatter] = {}
        self.register(GenericArtifactFormatter())
        self.register(MemoryRecordsFormatter())

    def register(self, formatter: ResultFormatter) -> None:
        artifact_type = str(getattr(formatter, "artifact_type", "") or "").strip()
        if not artifact_type:
            raise ValueError("ResultFormatter must define artifact_type.")
        self._formatters[artifact_type] = formatter

    def has(self, artifact_type: str) -> bool:
        return artifact_type in self._formatters

    def get(self, artifact_type: str) -> ResultFormatter:
        return self._formatters.get(artifact_type) or self._formatters["generic_artifact"]

    def format(self, artifact_type: str, payload: dict[str, Any]) -> ResultArtifactSpec:
        return self.get(artifact_type).format(payload).normalized()

    def copy(self) -> "ResultFormatterRegistry":
        registry = ResultFormatterRegistry()
        registry._formatters = dict(self._formatters)
        return registry


class GenericArtifactFormatter:
    artifact_type = "generic_artifact"

    def format(self, payload: dict[str, Any]) -> ResultArtifactSpec:
        rows = _dict_rows(payload.get("rows", []))
        preview_rows = _dict_rows(payload.get("preview_rows", payload.get("sample_rows", [])))
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return ResultArtifactSpec(
            artifact_type=str(payload.get("artifact_type") or self.artifact_type),
            title=str(payload.get("title") or "Result Artifact"),
            source=str(payload.get("source") or payload.get("tool_name") or ""),
            rows=rows,
            preview_rows=preview_rows,
            columns=payload.get("columns") if isinstance(payload.get("columns"), list) else [],
            metadata={**metadata},
            row_count=_optional_int(payload.get("row_count")),
            row_count_is_exact=bool(payload.get("row_count_is_exact", True)),
        )


class MemoryRecordsFormatter:
    artifact_type = "memory_records"

    def format(self, payload: dict[str, Any]) -> ResultArtifactSpec:
        records = payload.get("records") if isinstance(payload.get("records"), list) else []
        rows = [
            {
                "namespace": record.get("namespace", ""),
                "key": record.get("key", ""),
                "content": record.get("content", ""),
                "tags": record.get("tags", []),
                "source": record.get("source", ""),
                "updated_at": record.get("updated_at", ""),
            }
            for record in records
            if isinstance(record, dict)
        ]
        return ResultArtifactSpec(
            artifact_type=self.artifact_type,
            title=str(payload.get("title") or "Memory Search Results"),
            source=str(payload.get("source") or "memory_search"),
            rows=rows,
            columns=["namespace", "key", "content", "tags", "source", "updated_at"],
            preview_rows=rows[:5],
            metadata={
                "query": str(payload.get("query") or ""),
                "namespaces": payload.get("namespaces") if isinstance(payload.get("namespaces"), list) else [],
                "strategy": str(payload.get("strategy") or ""),
                "embedding_fallback": bool(payload.get("embedding_fallback")),
                "count": int(payload.get("count") or len(rows)),
            },
            row_count=len(rows),
            row_count_is_exact=True,
        )


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append(dict(item))
        else:
            rows.append({"value": item})
    return rows


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
