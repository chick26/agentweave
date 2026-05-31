from __future__ import annotations

from typing import Any


STAGE_CONFIG = {
    "current_time": ("🕒", "获取当前时间"),
    "worker_start": ("🧩", "启动 Worker"),
    "worker_complete": ("✅", "Worker 完成"),
    "load_skill": ("📚", "加载 Skill"),
    "memory_search": ("🧠", "检索记忆"),
    "memory_retrieval": ("🧠", "注入记忆"),
    "activation": ("🔌", "激活 Domain"),
    "search_values": ("🔍", "搜索候选值"),
    "sql_plan": ("🧭", "构建 SQLPlan"),
    "sql_prompt": ("📝", "构建提示词"),
    "sql_model_output": ("🤖", "SQL 模型推理"),
    "sql_extract": ("✂️", "提取 SQL"),
    "execute": ("▶️", "执行查询"),
    "tool_call_start": ("🛠️", "调用工具"),
    "tool_call_end": ("✅", "工具完成"),
    "tool_result": ("📦", "工具结果"),
    "resources_reloaded": ("🔄", "重载资源"),
    "session_forked": ("🌿", "分叉会话"),
    "session_template_started": ("📋", "从模板启动"),
    "session_template_saved": ("💾", "保存模板"),
    "todo_update": ("☑️", "更新 Todo"),
}


VISIBLE_STAGES = {
    "current_time",
    "worker_start",
    "worker_complete",
    "load_skill",
    "memory_search",
    "memory_retrieval",
    "activation",
    "search_values",
    "sql_plan",
    "todo_update",
    "sql_extract",
    "execute",
    "tool_call_start",
    "tool_call_end",
    "tool_result",
    "resources_reloaded",
    "session_forked",
    "session_template_started",
    "session_template_saved",
}

LIVE_VISIBLE_STAGES = VISIBLE_STAGES - {"tool_result", "tool_call_end"}


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return event


def should_show_live_event(
    event: dict[str, Any],
    seen_keys: set[tuple[str, str, str]] | None = None,
) -> bool:
    payload = event_payload(event)
    stage = str(payload.get("stage") or "")
    if stage not in LIVE_VISIBLE_STAGES:
        return False
    if event.get("kind") == "worker_run" and stage == "worker_start":
        return False

    key = _event_visibility_key(event)
    if seen_keys is not None:
        if key in seen_keys:
            return False
        seen_keys.add(key)
    return True


def extract_detail(event: dict[str, Any]) -> str:
    event = event_payload(event)
    stage = event.get("stage", "")
    output = event.get("output")
    inp = event.get("input")

    if stage == "activation":
        if isinstance(output, dict):
            return f"`{output.get('name', '')}`"

    elif stage == "worker_start":
        skill = event.get("skill", "")
        detail = f"`{skill}`" if skill else ""
        task = str(event.get("task") or "")
        if task:
            if len(task) > 60:
                task = task[:57] + "..."
            detail += f" · {task}"
        return detail

    elif stage == "worker_complete":
        subagent = event.get("subagent") or event.get("skill") or ""
        result = event.get("result")
        detail = f"`{subagent}`" if subagent else ""
        if isinstance(result, dict):
            domain = result.get("domain")
            row_count = result.get("stored_row_count", result.get("row_count"))
            if domain:
                detail += f" · domain=`{domain}`"
            if row_count is not None:
                suffix = "+" if result.get("has_more") or result.get("store_truncated") else ""
                detail += f" · rows={row_count}{suffix}"
        return detail

    elif stage == "load_skill":
        skill = event.get("skill", "")
        found = "已加载" if event.get("found") else "未找到"
        return f"`{skill}` · {found}" if skill else found

    elif stage in {"memory_search", "memory_retrieval"}:
        strategy = event.get("strategy", "")
        count = int(event.get("count") or 0)
        namespaces = event.get("namespaces", [])
        labels = ",".join(str(item) for item in namespaces) if isinstance(namespaces, list) else ""
        detail = f"`{strategy}` 命中 {count} 条"
        if labels:
            detail += f" · `{labels}`"
        if event.get("embedding_fallback"):
            detail += " · fallback"
        if event.get("error"):
            detail += " · 发生降级"
        return detail

    elif stage == "current_time":
        if isinstance(output, dict):
            if output.get("error"):
                return f"❌ {output['error']}"
            return f"`{output.get('iso', '')}`"

    elif stage == "search_values":
        if isinstance(inp, dict):
            query = inp.get("query", "")
            if isinstance(output, list) and output:
                n = len(output)
                top = output[0].get("value", "")
                if len(top) > 30:
                    top = top[:27] + "..."
                if n == 1:
                    return f'`{query}` → `{top}`'
                return f'`{query}` → {n} 个候选值'
            return f'`{query}` → 无匹配'

    elif stage == "todo_update":
        if event.get("error"):
            return f"❌ {event['error']}"
        items = event.get("items", [])
        if isinstance(items, list):
            in_progress = [
                item.get("content", "")
                for item in items
                if isinstance(item, dict) and item.get("status") == "in_progress"
            ]
            if in_progress:
                return f"`{in_progress[0]}`"
            return f"{len(items)} 项"

    elif stage == "sql_plan":
        if isinstance(output, dict):
            linked = output.get("linked_values", [])
            metrics = output.get("business_metrics", [])
            parts = []
            if isinstance(metrics, list) and metrics:
                parts.append(f"{len(metrics)} 个业务口径")
            if isinstance(linked, list) and linked:
                parts.append(f"{len(linked)} 个候选值")
            return " · ".join(parts)

    elif stage == "sql_extract":
        if isinstance(output, dict):
            validation_error = output.get("validation_error")
            if validation_error:
                return f"❌ {validation_error}"
            output = output.get("sql", "")
        if isinstance(output, str) and output.strip():
            sql = output.strip().replace("\n", " ")
            if len(sql) > 80:
                sql = sql[:77] + "..."
            return f"`{sql}`"

    elif stage == "execute":
        if isinstance(output, dict):
            error = output.get("error")
            if error:
                return f"❌ {error}"
            rows = output.get("sample_rows", output.get("rows", []))
            row_count = int(output.get("stored_row_count") or output.get("row_count") or 0)
            result_id = output.get("result_id", "")
            suffix = "+" if output.get("has_more") or output.get("store_truncated") else ""
            if isinstance(rows, list) and rows:
                if len(rows) == 1 and len(rows[0]) <= 3:
                    parts = [f"{k}=**{v}**" for k, v in rows[0].items()]
                    detail = "✅ " + ", ".join(parts)
                else:
                    detail = f"✅ {row_count}{suffix} 行结果，样例 {len(rows)} 行"
            else:
                detail = f"✅ {row_count}{suffix} 行结果"
            if result_id:
                detail += f" · `{result_id}`"
            return detail

    elif stage in {"tool_call_start", "tool_call_end", "tool_result"}:
        tool_name = event.get("tool_name", "")
        detail = f"`{tool_name}`" if tool_name else ""
        status = event.get("status")
        if status:
            detail += f" · {status}"
        error = event.get("error")
        if error:
            detail += f" · ❌ {error}"
        result_id = event.get("result_id")
        if result_id:
            detail += f" · `{result_id}`"
        row_count = event.get("stored_row_count", event.get("row_count"))
        if row_count is not None:
            suffix = "+" if event.get("has_more") or event.get("store_truncated") else ""
            detail += f" · rows={row_count}{suffix}"
        return detail

    elif stage == "resources_reloaded":
        return str(event.get("message") or "")

    elif stage == "session_forked":
        source = event.get("source_session_id", "")
        target = event.get("target_session_id", "")
        copied = event.get("copied_items", 0)
        return f"`{source}` → `{target}` · {copied} items"

    elif stage in {"session_template_started", "session_template_saved"}:
        template = event.get("template_name", "")
        message_count = event.get("message_count", 0)
        detail = f"`{template}`" if template else ""
        if message_count:
            detail += f" · {message_count} messages"
        return detail

    return ""


def format_event_line(event: dict[str, Any]) -> str:
    event = event_payload(event)
    stage = event.get("stage", "")
    icon, label = STAGE_CONFIG.get(stage, ("⚙️", event.get("title", stage)))
    detail = extract_detail(event)
    if detail:
        return f"{icon} {label} — {detail}"
    return f"{icon} {label}"


def format_trace_for_storage(events: list[dict[str, Any]]) -> str:
    if not events:
        return ""
    lines = []
    seen_keys: set[tuple[str, str, str]] = set()
    for event in events:
        if should_show_live_event(event, seen_keys):
            lines.append(format_event_line(event))
    return "\n".join(lines)


def _event_visibility_key(event: dict[str, Any]) -> tuple[str, str, str]:
    payload = event_payload(event)
    stage = str(payload.get("stage") or "")
    subject = str(
        payload.get("tool_name")
        or payload.get("subagent")
        or payload.get("skill")
        or payload.get("template_id")
        or ""
    )
    detail = str(
        payload.get("result_id")
        or payload.get("target_session_id")
        or payload.get("source_session_id")
        or payload.get("input")
        or payload.get("query")
        or ""
    )
    return stage, subject, detail
