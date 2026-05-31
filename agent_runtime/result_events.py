from __future__ import annotations

from typing import Any

from agent_runtime.common import coerce_bool


def extract_result_metadata(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract result-store metadata from subagent execute trace events."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("kind") == "result_created":
            ui_content = event.get("payload", {}).get("ui_content")
            if not isinstance(ui_content, dict):
                continue
            result_id = ui_content.get("result_id")
            if not result_id or result_id in seen:
                continue
            seen.add(str(result_id))
            results.append(
                {
                    "result_id": str(result_id),
                    "row_count": int(ui_content.get("row_count") or 0),
                    "stored_row_count": int(
                        ui_content.get("stored_row_count")
                        or ui_content.get("row_count")
                        or 0
                    ),
                    "columns": ui_content.get("columns")
                    if isinstance(ui_content.get("columns"), list)
                    else [],
                    "sample_rows": ui_content.get("sample_rows")
                    if isinstance(ui_content.get("sample_rows"), list)
                    else [],
                    "sample_size": int(ui_content.get("sample_size") or 0),
                    "truncated": coerce_bool(ui_content.get("truncated")),
                    "store_truncated": coerce_bool(ui_content.get("store_truncated")),
                    "has_more": coerce_bool(
                        ui_content.get("has_more") or ui_content.get("store_truncated")
                    ),
                    "row_count_is_exact": coerce_bool(
                        ui_content.get("row_count_is_exact", not ui_content.get("store_truncated"))
                    ),
                    "sql": str(ui_content.get("sql") or ""),
                }
            )
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        if not isinstance(payload, dict) or payload.get("stage") != "execute":
            continue
        output = payload.get("output")
        if not isinstance(output, dict):
            continue
        result_id = output.get("result_id")
        if not result_id or result_id in seen:
            continue
        seen.add(str(result_id))
        results.append(
            {
                "result_id": str(result_id),
                "row_count": int(output.get("row_count") or 0),
                "stored_row_count": int(output.get("stored_row_count") or output.get("row_count") or 0),
                "columns": output.get("columns") if isinstance(output.get("columns"), list) else [],
                "sample_rows": output.get("sample_rows")
                if isinstance(output.get("sample_rows"), list)
                else [],
                "sample_size": int(output.get("sample_size") or 0),
                "truncated": coerce_bool(output.get("truncated")),
                "store_truncated": coerce_bool(output.get("store_truncated")),
                "has_more": coerce_bool(output.get("has_more") or output.get("store_truncated")),
                "row_count_is_exact": coerce_bool(
                    output.get("row_count_is_exact", not output.get("store_truncated"))
                ),
                "sql": str(output.get("sql") or payload.get("input") or ""),
            }
        )
    return results
