from __future__ import annotations

from typing import Any

from agent_runtime.common import coerce_bool


def extract_result_metadata(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract result-store metadata from subagent execute trace events or tool results."""
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

        # 3. Check legacy/fallback "execute" subagent trace for backward compatibility
        if payload.get("stage") == "execute":
            output = payload.get("output")
            if isinstance(output, dict):
                result_id = output.get("result_id")
                if result_id and result_id not in seen:
                    seen.add(str(result_id))
                    sql = output.get("sql") or payload.get("input") or ""
                    parsed = _parse_result_dict(output, result_id)
                    if sql and not parsed.get("sql"):
                        parsed["sql"] = str(sql)
                    results.append(parsed)
            continue

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
