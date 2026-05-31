from __future__ import annotations

from typing import Any


def format_reload_summary(summary: dict[str, Any]) -> str:
    parts = []
    for key, label in (
        ("skills", "Skills"),
        ("subagents", "Subagents"),
        ("domains", "Domains"),
    ):
        item = summary.get(key)
        if isinstance(item, dict):
            if item.get("changed"):
                added = item.get("added") or []
                removed = item.get("removed") or []
                detail = []
                if added:
                    detail.append(f"+{len(added)}")
                if removed:
                    detail.append(f"-{len(removed)}")
                parts.append(f"{label} changed ({', '.join(detail) or 'updated'})")
            else:
                parts.append(f"{label} unchanged")
    if summary.get("project_rules"):
        source = summary.get("project_rules_source") or "project rules"
        parts.append(f"Project rules changed: {source}")
    else:
        parts.append("Project rules unchanged")
    return " · ".join(parts)


def reload_summary_changed(summary: dict[str, Any]) -> bool:
    for key in ("skills", "subagents", "domains"):
        item = summary.get(key)
        if isinstance(item, dict) and item.get("changed"):
            return True
    return bool(summary.get("project_rules"))
