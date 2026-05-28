from __future__ import annotations

from typing import Any

from agent_runtime.common import coerce_bool


def extract_result_metadata(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract result-store metadata from subagent execute trace events."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
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
                "columns": output.get("columns") if isinstance(output.get("columns"), list) else [],
                "sample_rows": output.get("sample_rows")
                if isinstance(output.get("sample_rows"), list)
                else [],
                "sample_size": int(output.get("sample_size") or 0),
                "truncated": coerce_bool(output.get("truncated")),
                "store_truncated": coerce_bool(output.get("store_truncated")),
                "sql": str(output.get("sql") or payload.get("input") or ""),
            }
        )
    return results
