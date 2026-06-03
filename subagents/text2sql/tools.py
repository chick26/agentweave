from __future__ import annotations

import os
from typing import Any

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel

from agent_runtime.common import columns_from_rows
from agent_runtime.core.context import RunContext
from agent_runtime.core.events import EventKind
from agent_runtime.core.tool_protocol import ToolOutput
from agent_runtime.storage.database import DatabaseBackend
from agent_runtime.core.runtime_utils import (
    get_current_time_payload,
)
from agent_runtime.registry.skill_registry import AgentRegistry
from subagents.text2sql.scripts.domain_catalog import (
    Text2SQLDomainCatalog,
    business_metrics_to_prompt,
    domain_schema_payload,
)
from subagents.text2sql.scripts.sql_generation import generate_sql
from subagents.text2sql.scripts.sql_safety import (
    validate_sql_uses_selected_schema,
)


SQL_RESULT_SAMPLE_ROWS = int(os.getenv("SQL_RESULT_SAMPLE_ROWS", "50"))
SQL_RESULT_STORE_MAX_ROWS = int(os.getenv("SQL_RESULT_STORE_MAX_ROWS", "1000"))
SQL_RESULT_CELL_MAX_CHARS = int(os.getenv("SQL_RESULT_CELL_MAX_CHARS", "300"))
TEXT2SQL_DATABASE_ENVIRONMENT_ERROR = (
    "Text2SQL database environment is not prepared. Follow "
    "subagents/text2sql/ENVIRONMENT.md, prepare or connect the database, "
    "then restart runtime before calling Text2SQL tools."
)


class LinkedValueInput(BaseModel):
    field: str
    value: str
    count: int | None = None
    source: str = "search_domain_values"
    query: str = ""


def _emit_tool_start(
    run_ctx: RunContext,
    *,
    tool_name: str,
    input_payload: dict[str, Any],
) -> None:
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_START,
        payload={
            "stage": "tool_call_start",
            "tool_name": tool_name,
            "input": input_payload,
        },
    )


def _emit_tool_finish(
    run_ctx: RunContext,
    *,
    tool_name: str,
    tool_output: ToolOutput,
    status: str = "completed",
) -> None:
    error = str(tool_output.metadata.get("error") or "")
    run_ctx.emit_payload(
        kind=EventKind.TOOL_RESULT,
        payload={
            "stage": "tool_result",
            "tool_name": tool_name,
            "status": status,
            "ui_content": tool_output.ui_content,
            "metadata": tool_output.metadata,
            "error": error,
        },
        error=error,
    )
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_END,
        payload={
            "stage": "tool_call_end",
            "tool_name": tool_name,
            "status": status,
            "error": error,
        },
        error=error,
    )


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
    _emit_tool_start(
        run_ctx,
        tool_name="get_current_time",
        input_payload={"timezone_name": timezone_name},
    )
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
    tool_output = ToolOutput(
        llm_content=output,
        ui_content=output,
        metadata={
            "tool_name": "get_current_time",
            "error": output.get("error", "") if isinstance(output, dict) else "",
        },
    )
    _emit_tool_finish(
        run_ctx,
        tool_name="get_current_time",
        tool_output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@function_tool
async def list_domains(ctx: RunContextWrapper[RunContext]) -> str:
    """List Text2SQL query domains available to this subagent.

    Use this when the injected <domains> context is insufficient or ambiguous.
    It returns only lightweight table/domain summaries, not full schemas.
    """
    run_ctx = ctx.context
    _emit_tool_start(
        run_ctx,
        tool_name="list_domains",
        input_payload={},
    )
    try:
        domains = [
            {
                "name": domain.name,
                "description": domain.description,
                "table": domain.table,
            }
            for domain in _domain_catalog_from_context(ctx).list_domains()
        ]
        payload = {"domains": domains, "error": ""}
    except Exception as exc:
        payload = {
            "domains": [],
            "error": str(exc),
        }
    run_ctx.emit_subagent_trace(
        {
            "stage": "list_domains",
            "title": "列出数据域",
            "input": {},
            "output": payload,
        }
    )
    tool_output = ToolOutput(
        llm_content=payload,
        ui_content=payload,
        metadata={"tool_name": "list_domains", "error": payload.get("error") or ""},
    )
    _emit_tool_finish(
        run_ctx,
        tool_name="list_domains",
        tool_output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@function_tool
async def get_domain_schema(
    ctx: RunContextWrapper[RunContext],
    domain_name: str,
) -> str:
    """Load the real database schema for one Text2SQL domain.

    Always call this before generating SQL. The tool activates the selected
    domain/table in RunContext and returns schema, columns, text fields,
    business metrics, and domain notes.

    Args:
        domain_name: Domain name selected from <domains> or list_domains.
    """
    run_ctx = ctx.context
    _emit_tool_start(
        run_ctx,
        tool_name="get_domain_schema",
        input_payload={"domain_name": domain_name},
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        schema_text = _activate_domain_context(run_ctx, domain)
        selected_columns = _require_backend(run_ctx).get_columns(domain.table)
        payload = domain_schema_payload(
            domain=domain,
            schema_text=schema_text,
            columns=selected_columns,
        )
    except Exception as exc:
        payload = {
            "domain": domain_name,
            "description": "",
            "table": "",
            "schema": "",
            "columns": [],
            "text_fields": [],
            "field_descriptions": {},
            "business_metrics": [],
            "notes": "",
            "error": str(exc),
        }
    run_ctx.emit_subagent_trace(
        {
            "stage": "schema",
            "title": f"加载 Domain Schema: {domain_name}",
            "input": {"domain_name": domain_name},
            "output": payload,
        }
    )
    tool_output = ToolOutput(
        llm_content=payload,
        ui_content=payload,
        metadata={
            "tool_name": "get_domain_schema",
            "domain": payload.get("domain") or domain_name,
            "table": payload.get("table") or "",
            "error": payload.get("error") or "",
        },
    )
    _emit_tool_finish(
        run_ctx,
        tool_name="get_domain_schema",
        tool_output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@function_tool
async def search_domain_values(
    ctx: RunContextWrapper[RunContext],
    domain_name: str,
    query: str,
    fields: list[str] | None = None,
) -> str:
    """Search real text values in a domain before generating SQL filters.

    Use this when the user mentions concrete names, cities, statuses, rooms,
    resource IDs, sea cable numbers, or similar text filters.

    Args:
        domain_name: Domain name selected from <domains> or list_domains.
        query: User-provided literal snippet to link against real data values.
        fields: Optional field list. Empty means the domain's text_fields.
    """
    run_ctx = ctx.context
    input_payload = {"domain_name": domain_name, "query": query, "fields": fields or []}
    _emit_tool_start(
        run_ctx,
        tool_name="search_domain_values",
        input_payload=input_payload,
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        if run_ctx.active_domain != domain.name or run_ctx.active_table != domain.table:
            _activate_domain_context(run_ctx, domain)
        linked_values = _search_value_candidates(run_ctx, query, fields)
        payload = {
            "domain": domain.name,
            "query": query,
            "linked_values": linked_values,
            "error": "",
        }
    except Exception as exc:
        payload = {
            "domain": domain_name,
            "query": query,
            "linked_values": [],
            "error": str(exc),
        }
    tool_output = ToolOutput(
        llm_content=payload,
        ui_content=payload,
        metadata={
            "tool_name": "search_domain_values",
            "domain": payload.get("domain") or domain_name,
            "error": payload.get("error") or "",
        },
    )
    _emit_tool_finish(
        run_ctx,
        tool_name="search_domain_values",
        tool_output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@function_tool
async def generate_readonly_sql(
    ctx: RunContextWrapper[RunContext],
    question: str,
    domain_name: str,
    linked_values: list[LinkedValueInput] | None = None,
    constraints: str = "",
) -> str:
    """Generate one validated read-only SQL statement for an activated domain.

    Call get_domain_schema before this tool. Pass linked_values returned by
    search_domain_values when text filters are involved. If execute_sql failed,
    pass the error as constraints and retry at most once.

    Args:
        question: User question with resolved dates and business intent.
        domain_name: Domain name selected from <domains> or list_domains.
        linked_values: Optional linked values returned by search_domain_values.
        constraints: Optional correction note from one failed execution.
    """
    run_ctx = ctx.context
    linked_value_payloads = _linked_values_to_payload(linked_values)
    input_payload = {
        "question": question,
        "domain_name": domain_name,
        "linked_values": linked_value_payloads,
        "constraints": constraints,
    }
    _emit_tool_start(
        run_ctx,
        tool_name="generate_readonly_sql",
        input_payload=input_payload,
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        schema_text = _activate_domain_context(run_ctx, domain)
        selected_columns = _require_backend(run_ctx).get_columns(domain.table)
        generated = await generate_sql(
            run_ctx=run_ctx,
            question=question,
            domain=domain,
            schema_text=schema_text,
            selected_columns=selected_columns,
            linked_values=linked_value_payloads,
            constraints=constraints,
        )
        payload = {
            "domain": domain.name,
            "table": domain.table,
            "sql": generated["sql"],
            "linked_values": linked_value_payloads,
            "business_metrics": business_metrics_to_prompt(domain.business_metrics),
            "validation_error": generated["validation_error"],
            "error": "",
        }
    except Exception as exc:
        payload = {
            "domain": domain_name,
            "table": "",
            "sql": "",
            "linked_values": linked_value_payloads,
            "business_metrics": [],
            "validation_error": "",
            "error": str(exc),
        }
    tool_output = ToolOutput(
        llm_content=payload,
        ui_content=payload,
        metadata={
            "tool_name": "generate_readonly_sql",
            "domain": payload.get("domain") or domain_name,
            "error": payload.get("error") or payload.get("validation_error") or "",
        },
    )
    _emit_tool_finish(
        run_ctx,
        tool_name="generate_readonly_sql",
        tool_output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@function_tool
async def execute_sql(
    ctx: RunContextWrapper[RunContext],
    domain_name: str,
    sql: str,
) -> str:
    """Execute one validated read-only SQL statement and return result pointer or error.

    Use only SQL produced for the active domain. If execution fails, inspect the
    error and schema, then regenerate SQL at most once. The full result is
    stored outside the worker context. The tool returns result_id, row_count,
    stored_row_count, has_more, columns, sample_rows, sample_size, and truncated
    for summarization.

    Args:
        domain_name: Domain name used for schema validation.
        sql: SQL statement to execute.
    """
    run_ctx = ctx.context
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_START,
        payload={
            "stage": "tool_call_start",
            "tool_name": "execute_sql",
            "input": {"domain_name": domain_name, "sql": sql},
        },
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        if run_ctx.active_domain != domain.name or run_ctx.active_table != domain.table:
            _activate_domain_context(run_ctx, domain)
        validate_sql_uses_selected_schema(
            sql,
            selected_columns=_require_backend(run_ctx).get_columns(domain.table),
            allowed_tables=[domain.table],
        )
        fetched_rows = _require_backend(run_ctx).execute_sql(
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
            "stored_row_count": 0,
            "columns": [],
            "sample_rows": [],
            "sample_size": 0,
            "truncated": False,
            "store_truncated": False,
            "has_more": False,
            "row_count_is_exact": True,
            "sample_max_rows": SQL_RESULT_SAMPLE_ROWS,
            "store_max_rows": SQL_RESULT_STORE_MAX_ROWS,
            "error": str(exc),
        }
    status = "failed" if output.get("error") else "completed"
    run_ctx.emit_subagent_trace(
        {
            "stage": "execute",
            "title": "执行查询",
            "input": {"domain_name": domain_name, "sql": sql},
            "output": output,
        }
    )
    tool_output = ToolOutput(
        llm_content=output,
        ui_content=_build_execute_ui_content(output),
        metadata={
            "tool_name": "execute_sql",
            "result_id": output.get("result_id") or "",
            "row_count": output.get("row_count") or 0,
            "stored_row_count": output.get("stored_row_count") or 0,
            "has_more": bool(output.get("has_more")),
            "error": output.get("error") or "",
        },
    )
    run_ctx.emit_payload(
        kind=EventKind.TOOL_RESULT,
        payload={
            "stage": "tool_result",
            "tool_name": "execute_sql",
            "status": status,
            "result_id": output.get("result_id") or "",
            "row_count": output.get("row_count") or 0,
            "stored_row_count": output.get("stored_row_count") or 0,
            "has_more": bool(output.get("has_more")),
            "ui_content": tool_output.ui_content,
            "metadata": tool_output.metadata,
            "error": output.get("error") or "",
        },
        error=output.get("error") or "",
    )
    if output.get("result_id"):
        run_ctx.emit_payload(
            kind=EventKind.RESULT_CREATED,
            payload={
                "stage": "result_created",
                "tool_name": "execute_sql",
                "ui_content": tool_output.ui_content,
                "metadata": tool_output.metadata,
            },
        )
    run_ctx.emit_payload(
        kind=EventKind.TOOL_CALL_END,
        payload={
            "stage": "tool_call_end",
            "tool_name": "execute_sql",
            "status": status,
            "result_id": output.get("result_id") or "",
            "row_count": output.get("row_count") or 0,
            "stored_row_count": output.get("stored_row_count") or 0,
            "has_more": bool(output.get("has_more")),
            "error": output.get("error") or "",
        },
        error=output.get("error") or "",
    )
    return tool_output.to_llm_json()


def _activate_domain_context(run_ctx: RunContext, domain: Any) -> str:
    backend = _require_backend(run_ctx)
    run_ctx.active_domain = domain.name
    run_ctx.active_table = domain.table
    run_ctx.active_text_fields = list(domain.text_fields)
    run_ctx.active_field_descriptions = dict(domain.field_descriptions)
    schema_text = backend.get_schema_for_prompt(
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
    backend = _require_backend(run_ctx)
    selected_columns = set(backend.get_columns(run_ctx.active_table))
    fields = [
        field for field in list(field_list or run_ctx.active_text_fields)
        if field in selected_columns
    ]
    results: list[dict[str, Any]] = []
    for field_name in fields:
        for value, count in backend.search_distinct_values(
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
    stored_row_count = len(rows)
    has_more = bool(store_truncated)
    return {
        "sql": sql,
        "result_id": result_id,
        "row_count": stored_row_count,
        "stored_row_count": stored_row_count,
        "columns": columns,
        "sample_rows": sample_rows,
        "sample_size": len(sample_rows),
        "truncated": store_truncated or len(rows) > len(sample_rows),
        "store_truncated": store_truncated,
        "has_more": has_more,
        "row_count_is_exact": not has_more,
        "sample_max_rows": SQL_RESULT_SAMPLE_ROWS,
        "store_max_rows": SQL_RESULT_STORE_MAX_ROWS,
        "error": None,
    }


def _build_execute_ui_content(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_id": str(output.get("result_id") or ""),
        "row_count": int(output.get("row_count") or 0),
        "stored_row_count": int(output.get("stored_row_count") or output.get("row_count") or 0),
        "columns": output.get("columns") if isinstance(output.get("columns"), list) else [],
        "sample_rows": output.get("sample_rows")
        if isinstance(output.get("sample_rows"), list)
        else [],
        "sample_size": int(output.get("sample_size") or 0),
        "truncated": bool(output.get("truncated")),
        "store_truncated": bool(output.get("store_truncated")),
        "has_more": bool(output.get("has_more") or output.get("store_truncated")),
        "row_count_is_exact": bool(output.get("row_count_is_exact", not output.get("store_truncated"))),
        "sample_max_rows": int(output.get("sample_max_rows") or SQL_RESULT_SAMPLE_ROWS),
        "store_max_rows": int(output.get("store_max_rows") or SQL_RESULT_STORE_MAX_ROWS),
        "sql": str(output.get("sql") or ""),
        "error": str(output.get("error") or ""),
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


def _linked_values_to_payload(
    linked_values: list[LinkedValueInput] | None,
) -> list[dict[str, Any]]:
    if not linked_values:
        return []
    return [item.model_dump() for item in linked_values]


def _require_backend(run_ctx: RunContext) -> DatabaseBackend:
    backend = getattr(run_ctx, "backend", None)
    if backend is None:
        raise RuntimeError(TEXT2SQL_DATABASE_ENVIRONMENT_ERROR)
    return backend


def _registry_from_context(ctx: RunContextWrapper[RunContext]) -> AgentRegistry:
    registry = getattr(ctx.context, "agent_registry", None)
    if isinstance(registry, AgentRegistry):
        return registry
    raise RuntimeError("RunContext is missing AgentRegistry")


def _domain_catalog_from_context(ctx: RunContextWrapper[RunContext]) -> Text2SQLDomainCatalog:
    registry = _registry_from_context(ctx)
    return Text2SQLDomainCatalog.from_agent(registry.get("text2sql"))
