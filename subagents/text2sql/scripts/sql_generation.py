from __future__ import annotations

from typing import Any

from agent_runtime.core.context import RunContext
from agent_runtime.core.runtime_utils import (
    call_chat_model,
    json_dumps,
    make_async_client,
)
import re
from agent_runtime.storage.database import validate_readonly_sql
from subagents.text2sql.scripts.domain_catalog import DomainConfig, business_metrics_to_prompt
from subagents.text2sql.scripts.prompts import SQL_GENERATION_PROMPT
from subagents.text2sql.scripts.sql_safety import validate_sql_uses_selected_schema


async def generate_sql(
    *,
    run_ctx: RunContext,
    question: str,
    domain: DomainConfig,
    schema_text: str,
    selected_columns: list[str],
    linked_values: list[dict[str, Any]],
    constraints: str = "",
) -> dict[str, str]:
    payload = {
        "question": question.strip(),
        "domain": domain.name,
        "table": domain.table,
        "selected_columns": list(selected_columns),
        "linked_values": linked_values,
        "business_metrics": business_metrics_to_prompt(domain.business_metrics),
        "notes": domain.notes,
        "constraints": constraints.strip(),
    }
    messages = [
        {
            "role": "system",
            "content": SQL_GENERATION_PROMPT.format(dialect=_require_backend_dialect(run_ctx)),
        },
        {
            "role": "user",
            "content": (
                f"{schema_text}\n\n"
                f"<sql_context>\n{json_dumps(payload)}\n</sql_context>\n\n"
                f"用户问题:\n{question}"
            ),
        },
    ]
    run_ctx.emit_subagent_trace(
        {
            "stage": "sql_prompt",
            "title": "构建 SQL 提示词",
            "input": messages,
            "output": None,
        }
    )
    profile = run_ctx.model_profiles["executor"]
    raw_output = await call_chat_model(
        client=make_async_client(profile),
        model_name=profile.model_name,
        max_tokens=profile.max_tokens,
        messages=messages,
        title="SQL 生成模型调用",
        kind="sql_model",
        log_callback=lambda log: run_ctx.emit_payload(kind="model_call", payload=log),
    )
    run_ctx.emit_subagent_trace(
        {
            "stage": "sql_model_output",
            "title": "SQL 模型推理",
            "input": None,
            "output": raw_output,
        }
    )
    sql = extract_sql(raw_output)
    validation_errors: list[str] = []
    try:
        validate_readonly_sql(sql)
    except ValueError as exc:
        validation_errors.append(str(exc))
    try:
        validate_sql_uses_selected_schema(
            sql,
            selected_columns=selected_columns,
            allowed_tables=[domain.table],
        )
    except ValueError as exc:
        validation_errors.append(str(exc))
    validation_error = "; ".join(validation_errors)
    run_ctx.emit_subagent_trace(
        {
            "stage": "sql_extract",
            "title": "提取 SQL",
            "input": raw_output,
            "output": {"sql": sql, "validation_error": validation_error},
        }
    )
    return {
        "sql": sql,
        "raw_output": raw_output,
        "validation_error": validation_error,
    }


def _require_backend_dialect(run_ctx: RunContext) -> str:
    backend = getattr(run_ctx, "backend", None)
    return str(getattr(backend, "dialect", "SQL"))


def extract_sql(content: str) -> str:
    stripped = content.strip()
    fenced_sql = _extract_fenced_sql(stripped)
    if fenced_sql:
        return _normalize_sql_statement(fenced_sql)
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```sql").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
        return _normalize_sql_statement(stripped)
    match = re.search(r"\b(select|with)\b.+", stripped, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return stripped
    sql = match.group(0).strip()
    return _normalize_sql_statement(sql)


def _extract_fenced_sql(content: str) -> str:
    matches = re.findall(
        r"```(?:sql|sqlite)?\s*(.*?)```",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for candidate in reversed(matches):
        if re.search(r"\b(select|with)\b", candidate, flags=re.IGNORECASE):
            return candidate.strip()
    return ""


def _normalize_sql_statement(sql: str) -> str:
    stripped = sql.strip()
    if ";" in stripped:
        stripped = stripped.split(";", 1)[0].strip()
    kept: list[str] = []
    for line in stripped.splitlines():
        clean = line.strip()
        if not clean:
            continue
        kept.append(clean)
    return " ".join(kept)
