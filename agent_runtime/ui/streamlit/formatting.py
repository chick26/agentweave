from __future__ import annotations

from typing import Any


def format_count(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def format_result_option(result_id: str, results: list[dict[str, Any]]) -> str:
    for item in results:
        if item["result_id"] == result_id:
            row_count = item.get("stored_row_count", item.get("row_count", 0))
            suffix = "+" if item.get("has_more") or item.get("store_truncated") else ""
            return f"{result_id} · {row_count}{suffix} stored rows"
    return result_id
