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
    rows = data.get("sample_rows") or data.get("rows") or []
    if not isinstance(rows, list):
        rows = []

    return {
        "result_id": str(result_id),
        "row_count": int(data.get("row_count") or 0),
        "stored_row_count": int(
            data.get("stored_row_count")
            or data.get("row_count")
            or 0
        ),
        "columns": data.get("columns")
        if isinstance(data.get("columns"), list)
        else [],
        "sample_rows": rows,
        "sample_size": int(data.get("sample_size") or len(rows)),
        "truncated": coerce_bool(data.get("truncated")),
        "store_truncated": coerce_bool(data.get("store_truncated")),
        "has_more": coerce_bool(
            data.get("has_more") or data.get("store_truncated")
        ),
        "row_count_is_exact": coerce_bool(
            data.get("row_count_is_exact", not data.get("store_truncated"))
        ),
        "sql": str(data.get("sql") or ""),
    }
