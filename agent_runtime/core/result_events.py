"""Helpers for extracting artifact metadata from standard runtime events."""

from __future__ import annotations

from typing import Any


def extract_result_metadata(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract artifact metadata from result_created events."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("kind") != "result_created":
            continue
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

        ui_content = payload.get("ui_content")
        if isinstance(ui_content, dict):
            result_id = ui_content.get("result_id")
            if result_id and result_id not in seen:
                seen.add(str(result_id))
                results.append(_parse_result_dict(ui_content, result_id))

    return results


def _parse_result_dict(data: dict[str, Any], result_id: Any) -> dict[str, Any]:
    return {
        "result_id": str(result_id),
        "artifact_type": str(data.get("artifact_type") or "generic_artifact"),
        "title": str(data.get("title") or ""),
        "source": str(data.get("source") or ""),
        "run_id": str(data.get("run_id") or ""),
        "session_id": str(data.get("session_id") or ""),
        "bot_id": str(data.get("bot_id") or ""),
        "preview": data.get("preview") if isinstance(data.get("preview"), dict) else {},
        "metrics": data.get("metrics") if isinstance(data.get("metrics"), dict) else {},
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        "created_at": str(data.get("created_at") or ""),
    }
