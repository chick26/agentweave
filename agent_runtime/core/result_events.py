"""Helpers for extracting result-store metadata from runtime events."""

from __future__ import annotations

from typing import Any

from agent_runtime.common import coerce_bool


def extract_result_metadata(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract result-store metadata from generic artifacts or result events."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue

        result = payload.get("result")
        if isinstance(result, dict):
            result_id = result.get("result_id")
            if result_id and result_id not in seen:
                seen.add(str(result_id))
                results.append(_parse_result_dict(result, result_id))
                continue

        # 1. Check result_created event
        if kind == "result_created":
            ui_content = payload.get("ui_content")
            if isinstance(ui_content, dict):
                result_id = ui_content.get("result_id")
                if result_id and result_id not in seen:
                    seen.add(str(result_id))
                    results.append(_parse_result_dict(ui_content, result_id))
            continue

        # 2. Check tool_result event
        if kind == "tool_result":
            metadata = payload.get("metadata") or {}
            ui_content = payload.get("ui_content") or {}
            result_id = metadata.get("result_id") or ui_content.get("result_id")
            if result_id and result_id not in seen:
                seen.add(str(result_id))
                merged = {**ui_content, **metadata}
                results.append(_parse_result_dict(merged, result_id))
            continue

        # 3. Check generic subagent artifacts
        result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        artifacts = result_payload.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                result_id = artifact.get("result_id")
                if result_id and result_id not in seen:
                    seen.add(str(result_id))
                    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
                    preview = artifact.get("preview") if isinstance(artifact.get("preview"), list) else []
                    results.append(_parse_result_dict({**metadata, "sample_rows": preview}, result_id))

    return results


def _parse_result_dict(data: dict[str, Any], result_id: Any) -> dict[str, Any]:
    if isinstance(data.get("preview"), dict) and isinstance(data.get("metrics"), dict):
        return {
            "result_id": str(result_id),
            "artifact_type": str(data.get("artifact_type") or "generic_artifact"),
            "title": str(data.get("title") or ""),
            "source": str(data.get("source") or ""),
            "preview": data.get("preview") or {},
            "metrics": data.get("metrics") or {},
            "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            "created_at": str(data.get("created_at") or ""),
        }

    rows = data.get("sample_rows") or data.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    columns = data.get("columns") if isinstance(data.get("columns"), list) else []
    row_count = int(data.get("row_count") or 0)
    stored_count = int(data.get("stored_row_count") or data.get("row_count") or 0)
    count_is_exact = coerce_bool(
        data.get("row_count_is_exact", not data.get("store_truncated"))
    )
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if data.get("sql") and "sql" not in metadata:
        metadata = {**metadata, "sql": str(data.get("sql") or "")}

    return {
        "result_id": str(result_id),
        "artifact_type": str(data.get("artifact_type") or "sql_result"),
        "title": str(data.get("title") or ""),
        "source": str(data.get("source") or ""),
        "preview": {
            "kind": "rows",
            "columns": columns,
            "rows": data.get("preview_rows") if isinstance(data.get("preview_rows"), list) else rows,
        },
        "metrics": {
            "row_count": row_count,
            "stored_count": stored_count,
            "count_is_exact": count_is_exact,
            "truncated": not count_is_exact,
        },
        "metadata": metadata,
        "created_at": str(data.get("created_at") or ""),
    }
