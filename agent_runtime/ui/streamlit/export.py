from __future__ import annotations

from html import escape
from typing import Any


def build_session_markdown(
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    event_runs: list[dict[str, Any]],
) -> str:
    lines = [f"# AgentWeave Session", "", f"- Session ID: `{session_id}`", ""]
    lines.append("## Conversation")
    lines.append("")
    for message in messages:
        role = str(message.get("role") or "message").title()
        content = str(message.get("content") or "")
        lines.extend([f"### {role}", "", content, ""])

    if event_runs:
        lines.extend(["## Runtime Events", ""])
        for run in event_runs:
            label = str(run.get("label") or run.get("question") or run.get("run_id") or "Run")
            lines.extend([f"### {label}", ""])
            for event in run.get("events") or []:
                if not isinstance(event, dict):
                    continue
                kind = event.get("kind", "")
                payload = event.get("payload")
                stage = payload.get("stage", "") if isinstance(payload, dict) else ""
                summary = _event_summary(payload if isinstance(payload, dict) else event)
                lines.append(f"- `{kind}` `{stage}` {summary}".rstrip())
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_session_html(
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    event_runs: list[dict[str, Any]],
) -> str:
    markdown = build_session_markdown(
        session_id=session_id,
        messages=messages,
        event_runs=event_runs,
    )
    body = "\n".join(
        f"<p>{escape(line)}</p>" if line else ""
        for line in markdown.splitlines()
    )
    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\"><title>AgentWeave Session</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
        "max-width:900px;margin:40px auto;line-height:1.5;padding:0 24px}"
        "p{white-space:pre-wrap}code{background:#f4f4f5;padding:2px 4px;border-radius:4px}"
        "</style></head><body>"
        f"{body}"
        "</body></html>\n"
    )


def _event_summary(event: dict[str, Any]) -> str:
    for key in ("message", "tool_name", "title", "status", "error"):
        value = event.get(key)
        if value:
            return str(value)
    output = event.get("output")
    if isinstance(output, dict):
        result_id = output.get("result_id")
        row_count = output.get("stored_row_count", output.get("row_count"))
        if result_id:
            suffix = "+" if output.get("has_more") or output.get("store_truncated") else ""
            return f"result_id={result_id} stored_rows={row_count}{suffix}"
        if output.get("error"):
            return str(output["error"])
    return ""
