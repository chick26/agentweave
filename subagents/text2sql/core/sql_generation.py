"""Text2SQL prompt construction and model-backed SQL generation."""

from __future__ import annotations

import os
import re
from typing import Any

from agent_runtime.shared.database import validate_readonly_sql
from agent_runtime.shared.models import json_dumps
from agent_runtime.subagent_api import SubagentContext
from subagents.text2sql.core.domain_catalog import DomainConfig, business_metrics_to_prompt
from subagents.text2sql.core.prompts import SQL_GENERATION_PROMPT
from subagents.text2sql.core.sql_safety import validate_sql_uses_selected_schema


async def generate_sql(
    *,
    ctx: SubagentContext,
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
            "content": SQL_GENERATION_PROMPT.format(dialect=_require_backend_dialect(ctx)),
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
    ctx.trace(
        stage="sql_prompt",
        title="构建 SQL 提示词",
        input=messages,
        output=None,
    )
    raw_output = await ctx.call_model(
        role="executor",
        messages=messages,
        title="SQL 生成模型调用",
        kind="sql_model",
    )
    ctx.trace(
        stage="sql_model_output",
        title="SQL 模型推理",
        input=None,
        output=raw_output,
    )
    sql = extract_sql(raw_output)
    validation_errors: list[str] = []
    try:
        validate_readonly_sql(sql)
    except ValueError as exc:
        validation_errors.append(str(exc))
    if _strict_schema_validation_enabled():
        try:
            validate_sql_uses_selected_schema(
                sql,
                selected_columns=selected_columns,
                allowed_tables=[domain.table],
            )
        except ValueError as exc:
            validation_errors.append(str(exc))
    validation_error = "; ".join(validation_errors)
    ctx.trace(
        stage="sql_extract",
        title="提取 SQL",
        input=raw_output,
        output={"sql": sql, "validation_error": validation_error},
    )
    return {
        "sql": sql,
        "raw_output": raw_output,
        "validation_error": validation_error,
    }


def _require_backend_dialect(ctx: SubagentContext) -> str:
    backend = ctx.cache.get("database_backend")
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


def _strict_schema_validation_enabled() -> bool:
    return os.getenv("TEXT2SQL_STRICT_SCHEMA_VALIDATION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
