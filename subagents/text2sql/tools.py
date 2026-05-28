from __future__ import annotations

import os
from typing import Any

from agents import RunContextWrapper, function_tool

from agent_runtime.common import columns_from_rows
from agent_runtime.context import RunContext
from agent_runtime.database import validate_readonly_sql
from agent_runtime.runtime_utils import (
    call_chat_model,
    extract_sql,
    get_current_time_payload,
    json_dumps,
    make_async_client,
)
from agent_runtime.skill_registry import AgentRegistry
from subagents.text2sql.domain_registry import Text2SQLDomainRegistry
from subagents.text2sql.planning import (
    build_sql_plan_from_parts,
    sql_plan_to_prompt,
    validate_sql_uses_selected_schema,
)
from subagents.text2sql.prompts import SQL_GENERATION_PROMPT


SQL_RESULT_SAMPLE_ROWS = int(os.getenv("SQL_RESULT_SAMPLE_ROWS", "50"))
SQL_RESULT_STORE_MAX_ROWS = int(os.getenv("SQL_RESULT_STORE_MAX_ROWS", "1000"))
SQL_RESULT_CELL_MAX_CHARS = int(os.getenv("SQL_RESULT_CELL_MAX_CHARS", "300"))


@function_tool
async def get_current_time(
    ctx: RunContextWrapper[RunContext],
    timezone_name: str = "",
) -> str:
    """Resolve current date and time for relative-time SQL filters.

    Use when the Text2SQL task contains today, yesterday, recent, current,
    this week, this month, or a similar relative time phrase that has not
    already been resolved by the orchestrator.

    Args:
        timezone_name: Optional IANA timezone name. Empty means application default.
    """
    run_ctx = ctx.context
    requested_timezone = timezone_name.strip() or run_ctx.timezone_name
    try:
        output = get_current_time_payload(requested_timezone)
    except ValueError as exc:
        output = {"timezone": requested_timezone, "error": str(exc)}
    run_ctx.emit_subagent_trace(
        {
            "stage": "current_time",
            "title": "获取当前时间",
            "input": {"timezone_name": timezone_name or "(default)"},
            "output": output,
        }
    )
    return json_dumps(output)


@function_tool
async def plan_sql_query(
    ctx: RunContextWrapper[RunContext],
    question: str,
    domain_name: str,
    value_queries: list[str] | None = None,
    correction_context: str = "",
) -> str:
    """Plan and generate one validated read-only SQL query for a data domain.

    Use this before execute_sql. Pick domain_name from the injected <domains>
    context. Pass concrete entity/status/city/room/resource text as
    value_queries so the planner can link real values before SQL generation.
    If a prior execute_sql attempt failed, pass the error in correction_context
    and plan once more.

    Args:
        question: User question with resolved dates and business intent.
        domain_name: Text2SQL domain selected from <domains>.
        value_queries: Concrete value snippets that should be linked against the domain.
        correction_context: Error or correction note from one failed execution.
    """
    run_ctx = ctx.context
    registry = _domain_registry_from_context(ctx)
    try:
        domain = registry.get_domain(domain_name)
    except Exception as exc:
        return json_dumps(
            {
                "domain": "",
                "sql_plan": {},
                "sql": "",
                "linked_values": [],
                "validation_error": "",
                "error": f"Invalid domain name: '{domain_name}'. {exc}",
                "available_domains": [item.name for item in registry.list_domains()],
            }
        )

    schema_text = _activate_domain_context(run_ctx, domain)
    linked_values: list[dict[str, Any]] = []
    for raw_query in value_queries or []:
        query = str(raw_query).strip()
        if query:
            linked_values.extend(_search_value_candidates(run_ctx, query))
    linked_values.sort(key=lambda item: -int(item.get("count") or 0))
    linked_values = linked_values[:40]

    selected_columns = run_ctx.backend.get_columns(run_ctx.active_table)
    plan = build_sql_plan_from_parts(
        question=question,
        domain=domain,
        schema_text=schema_text,
        selected_columns=selected_columns,
        linked_values=linked_values,
        constraints=correction_context,
    )
    run_ctx.emit_subagent_trace(
        {
            "stage": "sql_plan",
            "title": "构建 SQLPlan",
            "input": {
                "question": question,
                "domain_name": domain_name,
                "value_queries": value_queries or [],
                "correction_context": correction_context,
            },
            "output": plan.model_dump(),
        }
    )
    generated = await _generate_sql_from_plan(
        run_ctx=run_ctx,
        question=question,
        schema_text=schema_text,
        selected_columns=selected_columns,
        plan_text=sql_plan_to_prompt(plan),
    )
    return json_dumps(
        {
            "domain": domain.name,
            "sql_plan": plan.model_dump(),
            "sql": generated["sql"],
            "linked_values": linked_values,
            "validation_error": generated["validation_error"],
            "error": "",
        }
    )


@function_tool
async def execute_sql(
    ctx: RunContextWrapper[RunContext],
    sql: str,
) -> str:
    """Execute one validated read-only SQL statement and return result pointer or error.

    Use only SQL produced for the active domain. If execution fails, inspect the
    error and schema, then regenerate SQL at most once. The full result is
    stored outside the worker context. The tool returns result_id, row_count,
    columns, sample_rows, sample_size, and truncated for summarization.

    Args:
        sql: SQL statement to execute.
    """
    run_ctx = ctx.context
    try:
        if run_ctx.active_table:
            validate_sql_uses_selected_schema(
                sql,
                selected_columns=run_ctx.backend.get_columns(run_ctx.active_table),
                allowed_tables=[run_ctx.active_table],
            )
        fetched_rows = run_ctx.backend.execute_sql(
            sql,
            max_rows=SQL_RESULT_STORE_MAX_ROWS + 1,
        )
        output = _build_execute_output(
            run_ctx=run_ctx,
            sql=sql,
            rows=fetched_rows[:SQL_RESULT_STORE_MAX_ROWS],
            store_truncated=len(fetched_rows) > SQL_RESULT_STORE_MAX_ROWS,
        )
    except Exception as exc:
        output = {
            "sql": sql,
            "result_id": "",
            "row_count": 0,
            "columns": [],
            "sample_rows": [],
            "sample_size": 0,
            "truncated": False,
            "store_truncated": False,
            "sample_max_rows": SQL_RESULT_SAMPLE_ROWS,
            "store_max_rows": SQL_RESULT_STORE_MAX_ROWS,
            "error": str(exc),
        }
    run_ctx.emit_subagent_trace(
        {
            "stage": "execute",
            "title": "执行查询",
            "input": sql,
            "output": output,
        }
    )
    return json_dumps(output)


def _activate_domain_context(run_ctx: RunContext, domain: Any) -> str:
    run_ctx.active_domain = domain.name
    run_ctx.active_table = domain.table
    run_ctx.active_text_fields = list(domain.text_fields)
    run_ctx.active_field_descriptions = dict(domain.field_descriptions)
    schema_text = run_ctx.backend.get_schema_for_prompt(
        domain.table,
        domain.field_descriptions,
    )
    run_ctx.emit_subagent_trace(
        {
            "stage": "activation",
            "title": f"Domain: {domain.name}",
            "input": {"domain_name": domain.name},
            "output": {
                "name": domain.name,
                "description": domain.description,
                "table": domain.table,
            },
        }
    )
    return schema_text


def _search_value_candidates(
    run_ctx: RunContext,
    query: str,
    field_list: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected_columns = set(run_ctx.backend.get_columns(run_ctx.active_table))
    fields = [
        field for field in list(field_list or run_ctx.active_text_fields)
        if field in selected_columns
    ]
    results: list[dict[str, Any]] = []
    for field_name in fields:
        for value, count in run_ctx.backend.search_distinct_values(
            run_ctx.active_table,
            field_name,
            query,
            limit=10,
        ):
            results.append({"field": field_name, "value": value, "count": count, "query": query})
    results.sort(key=lambda item: -item["count"])
    results = results[:20]
    run_ctx.emit_subagent_trace(
        {
            "stage": "search_values",
            "title": f"搜索候选值: {query}",
            "input": {
                "query": query,
                "fields": ",".join(fields) if field_list else "(all)",
            },
            "output": results,
        }
    )
    return results


async def _generate_sql_from_plan(
    *,
    run_ctx: RunContext,
    question: str,
    schema_text: str,
    selected_columns: list[str],
    plan_text: str,
) -> dict[str, str]:
    messages = [
        {
            "role": "system",
            "content": SQL_GENERATION_PROMPT.format(dialect=run_ctx.backend.dialect),
        },
        {
            "role": "user",
            "content": (
                f"{schema_text}\n\n"
                f"<sql_plan>\n{plan_text}\n</sql_plan>\n\n"
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
    profile = run_ctx.model_profiles["sql_worker"]
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
            allowed_tables=[run_ctx.active_table],
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


def _build_execute_output(
    *,
    run_ctx: RunContext,
    sql: str,
    rows: list[dict[str, Any]],
    store_truncated: bool = False,
) -> dict[str, Any]:
    columns = columns_from_rows(rows)
    result_id = ""
    result_store = getattr(run_ctx, "result_store", None)
    if result_store is not None:
        result_id = result_store.create_result(
            run_id=run_ctx.run_id,
            domain=run_ctx.active_domain,
            sql=sql,
            rows=rows,
        )
    sample_rows = _compact_rows_for_tool(rows[:SQL_RESULT_SAMPLE_ROWS])
    return {
        "sql": sql,
        "result_id": result_id,
        "row_count": len(rows),
        "columns": columns,
        "sample_rows": sample_rows,
        "sample_size": len(sample_rows),
        "truncated": store_truncated or len(rows) > len(sample_rows),
        "store_truncated": store_truncated,
        "sample_max_rows": SQL_RESULT_SAMPLE_ROWS,
        "store_max_rows": SQL_RESULT_STORE_MAX_ROWS,
        "error": None,
    }


def _compact_rows_for_tool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            str(key): _compact_cell(value)
            for key, value in row.items()
        }
        for row in rows
    ]


def _compact_cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if len(text) <= SQL_RESULT_CELL_MAX_CHARS:
        return value
    return f"{text[:SQL_RESULT_CELL_MAX_CHARS].rstrip()}...[truncated {len(text)} chars]"

def _registry_from_context(ctx: RunContextWrapper[RunContext]) -> AgentRegistry:
    registry = getattr(ctx.context, "agent_registry", None)
    if isinstance(registry, AgentRegistry):
        return registry
    raise RuntimeError("RunContext is missing AgentRegistry")


def _domain_registry_from_context(ctx: RunContextWrapper[RunContext]) -> Text2SQLDomainRegistry:
    registry = _registry_from_context(ctx)
    return Text2SQLDomainRegistry.from_agent(registry.get("text2sql"))
