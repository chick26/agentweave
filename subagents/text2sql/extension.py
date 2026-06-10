"""Text2SQL subagent extension that registers SQL tools and readiness checks."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel

from agent_runtime.subagent_api import (
    SubagentContext,
    SubagentToolContext,
    ToolOutput,
    subagent_context,
    tool,
    tool_finish,
    tool_start,
    ResultArtifactSpec,
)
from agent_runtime.shared.common import columns_from_rows
from agent_runtime.shared.database import DatabaseBackend, SqlDatabaseBackend
from agent_runtime.shared.manifest import AgentManifest
from subagents.text2sql.core.domain_catalog import (
    Text2SQLDomainCatalog,
    business_metrics_to_prompt,
    domain_schema_payload,
)
from subagents.text2sql.core.sql_generation import generate_sql
from subagents.text2sql.core.runtime_state import Text2SQLRunStateManager
from subagents.text2sql.core.sql_safety import (
    validate_sql_uses_selected_schema,
)


SQL_RESULT_SAMPLE_ROWS = int(os.getenv("SQL_RESULT_SAMPLE_ROWS", "50"))
SQL_RESULT_STORE_MAX_ROWS = int(os.getenv("SQL_RESULT_STORE_MAX_ROWS", "1000"))
SQL_RESULT_CELL_MAX_CHARS = int(os.getenv("SQL_RESULT_CELL_MAX_CHARS", "300"))
TEXT2SQL_DATABASE_ENVIRONMENT_ERROR = (
    "Text2SQL database environment is not prepared. Follow docs/environment/text2sql.md, "
    "prepare or connect the database, then restart runtime before calling Text2SQL tools."
)
_backend_cache: DatabaseBackend | None = None
_backend_cache_key: tuple[str, str] | None = None


def register(api: Any) -> None:
    api.result_formatter(SqlResultFormatter())
    api.tool(list_domains)
    api.tool(get_domain_schema)
    api.tool(search_domain_values)
    api.tool(generate_readonly_sql)
    api.tool(execute_sql)
    api.validate_environment(validate_environment)
    api.prompt_context(build_prompt_context)


class SqlResultFormatter:
    artifact_type = "sql_result"

    def format(self, payload: dict[str, Any]) -> ResultArtifactSpec:
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        sample_rows = (
            payload.get("sample_rows")
            if isinstance(payload.get("sample_rows"), list)
            else rows[:SQL_RESULT_SAMPLE_ROWS]
        )
        domain = str(payload.get("domain") or "")
        sql = str(payload.get("sql") or "")
        columns = payload.get("columns") if isinstance(payload.get("columns"), list) else columns_from_rows(rows)
        return ResultArtifactSpec(
            artifact_type=self.artifact_type,
            title=str(payload.get("title") or f"SQL Result: {domain or 'query'}"),
            source=str(payload.get("source") or "execute_sql"),
            rows=rows,
            columns=columns,
            preview_rows=sample_rows,
            metadata={
                "domain": domain,
                "sql": sql,
                "sample_max_rows": int(payload.get("sample_max_rows") or SQL_RESULT_SAMPLE_ROWS),
                "store_max_rows": int(payload.get("store_max_rows") or SQL_RESULT_STORE_MAX_ROWS),
                "truncated": bool(payload.get("truncated")),
                "store_truncated": bool(payload.get("store_truncated")),
            },
            row_count=int(payload.get("row_count") or len(rows)),
            row_count_is_exact=bool(payload.get("row_count_is_exact", True)),
        )


def validate_environment(_manifest: AgentManifest) -> None:
    _connect_backend()


def build_prompt_context(manifest: AgentManifest) -> dict[str, str]:
    return {
        "domains": Text2SQLDomainCatalog.from_agent(manifest).format_domains_for_prompt(),
    }


class LinkedValueInput(BaseModel):
    field: str
    value: str
    count: int | None = None
    source: str = "search_domain_values"
    query: str = ""

@tool
async def list_domains(ctx: SubagentToolContext) -> str:
    """List Text2SQL query domains available to this subagent.

    Use this when the injected <domains> context is insufficient or ambiguous.
    It returns only lightweight table/domain summaries, not full schemas.
    """
    run_ctx = subagent_context(ctx)
    tool_start(
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
    run_ctx.trace(
        stage="list_domains",
        title="列出数据域",
        input={},
        output=payload,
    )
    tool_output = ToolOutput(
        llm_content=payload,
        ui_content=payload,
        metadata={"tool_name": "list_domains", "error": payload.get("error") or ""},
    )
    tool_finish(
        run_ctx,
        tool_name="list_domains",
        output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@tool
async def get_domain_schema(
    ctx: SubagentToolContext,
    domain_name: str,
) -> str:
    """Load the real database schema for one Text2SQL domain.

    Always call this before generating SQL. The tool activates the selected
    domain/table in the runtime context and returns schema, columns, text fields,
    business metrics, and domain notes.

    Args:
        domain_name: Domain name selected from <domains> or list_domains.
    """
    run_ctx = subagent_context(ctx)
    tool_start(
        run_ctx,
        tool_name="get_domain_schema",
        input_payload={"domain_name": domain_name},
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        active = _state_manager(run_ctx).activate_domain(domain)
        payload = domain_schema_payload(
            domain=domain,
            schema_text=active.schema_text,
            columns=active.columns,
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
    run_ctx.trace(
        stage="schema",
        title=f"加载 Domain Schema: {domain_name}",
        input={"domain_name": domain_name},
        output=payload,
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
    tool_finish(
        run_ctx,
        tool_name="get_domain_schema",
        output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@tool
async def search_domain_values(
    ctx: SubagentToolContext,
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
    run_ctx = subagent_context(ctx)
    input_payload = {"domain_name": domain_name, "query": query, "fields": fields or []}
    tool_start(
        run_ctx,
        tool_name="search_domain_values",
        input_payload=input_payload,
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        state = _state_manager(run_ctx)
        state.ensure_domain(domain)
        linked_values = _search_value_candidates(state, query, fields)
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
    tool_finish(
        run_ctx,
        tool_name="search_domain_values",
        output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@tool
async def generate_readonly_sql(
    ctx: SubagentToolContext,
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
    run_ctx = subagent_context(ctx)
    linked_value_payloads = _linked_values_to_payload(linked_values)
    input_payload = {
        "question": question,
        "domain_name": domain_name,
        "linked_values": linked_value_payloads,
        "constraints": constraints,
    }
    tool_start(
        run_ctx,
        tool_name="generate_readonly_sql",
        input_payload=input_payload,
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        state = _state_manager(run_ctx)
        active = state.activate_domain(domain)
        generated = await generate_sql(
            ctx=run_ctx,
            question=question,
            domain=domain,
            schema_text=active.schema_text,
            selected_columns=active.columns,
            linked_values=linked_value_payloads,
            dialect=state.dialect,
            require_schema_validation=_schema_validation_required(run_ctx),
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
    tool_finish(
        run_ctx,
        tool_name="generate_readonly_sql",
        output=tool_output,
        status="failed" if tool_output.metadata.get("error") else "completed",
    )
    return tool_output.to_llm_json()


@tool
async def execute_sql(
    ctx: SubagentToolContext,
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
    run_ctx = subagent_context(ctx)
    tool_start(
        run_ctx,
        tool_name="execute_sql",
        input_payload={"domain_name": domain_name, "sql": sql},
    )
    try:
        domain = _domain_catalog_from_context(ctx).get_domain(domain_name)
        state = _state_manager(run_ctx)
        active = state.ensure_domain(domain)
        if _schema_validation_required(run_ctx):
            validate_sql_uses_selected_schema(
                sql,
                selected_columns=active.columns,
                allowed_tables=[active.table],
            )
        fetched_rows = state.backend.execute_sql(
            sql,
            max_rows=SQL_RESULT_STORE_MAX_ROWS + 1,
        )
        output = _build_execute_output(
            run_ctx=run_ctx,
            domain_name=active.domain,
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
    run_ctx.trace(
        stage="execute",
        title="执行查询",
        input={"domain_name": domain_name, "sql": sql},
        output=output,
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
    tool_finish(
        run_ctx,
        tool_name="execute_sql",
        output=tool_output,
        status=status,
    )
    return tool_output.to_llm_json()


def _state_manager(run_ctx: SubagentContext) -> Text2SQLRunStateManager:
    return Text2SQLRunStateManager.from_context(
        run_ctx,
        backend_factory=_connect_backend,
    )


def _search_value_candidates(
    state: Text2SQLRunStateManager,
    query: str,
    field_list: list[str] | None = None,
) -> list[dict[str, Any]]:
    active = state.active_domain()
    fields = state.search_fields(field_list)
    results: list[dict[str, Any]] = []
    for field_name in fields:
        for value, count in state.backend.search_distinct_values(
            active.table,
            field_name,
            query,
            limit=10,
        ):
            results.append({"field": field_name, "value": value, "count": count, "query": query})
    results.sort(key=lambda item: -item["count"])
    results = results[:20]
    state.run_ctx.trace(
        stage="search_values",
        title=f"搜索候选值: {query}",
        input={
            "query": query,
            "fields": ",".join(fields) if field_list else "(all)",
        },
        output=results,
    )
    return results


def _build_execute_output(
    *,
    run_ctx: SubagentContext,
    domain_name: str,
    sql: str,
    rows: list[dict[str, Any]],
    store_truncated: bool = False,
) -> dict[str, Any]:
    columns = columns_from_rows(rows)
    sample_rows = _compact_rows_for_tool(rows[:SQL_RESULT_SAMPLE_ROWS])
    stored_row_count = len(rows)
    has_more = bool(store_truncated)
    row_count_is_exact = not has_more
    artifact = run_ctx.store_artifact(
        artifact_type="sql_result",
        tool_name="execute_sql",
        payload={
            "domain": domain_name,
            "sql": sql,
            "rows": rows,
            "columns": columns,
            "sample_rows": sample_rows,
            "row_count": stored_row_count,
            "row_count_is_exact": row_count_is_exact,
            "truncated": store_truncated or len(rows) > len(sample_rows),
            "store_truncated": store_truncated,
            "sample_max_rows": SQL_RESULT_SAMPLE_ROWS,
            "store_max_rows": SQL_RESULT_STORE_MAX_ROWS,
        },
    )
    return {
        "sql": sql,
        "result_id": artifact.get("result_id", ""),
        "row_count": stored_row_count,
        "stored_row_count": stored_row_count,
        "columns": columns,
        "sample_rows": sample_rows,
        "sample_size": len(sample_rows),
        "truncated": store_truncated or len(rows) > len(sample_rows),
        "store_truncated": store_truncated,
        "has_more": has_more,
        "row_count_is_exact": row_count_is_exact,
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


def _connect_backend() -> DatabaseBackend:
    global _backend_cache, _backend_cache_key
    backend_kind = os.getenv("TEXT2SQL_BACKEND", "").strip().lower()
    database_url = os.getenv("TEXT2SQL_DATABASE_URL", "").strip()
    cache_key = (backend_kind, database_url)
    if _backend_cache is not None and _backend_cache_key == cache_key:
        return _backend_cache
    if database_url and backend_kind in {"", "sqlite"}:
        _backend_cache = SqlDatabaseBackend(database_url)
        _backend_cache_key = cache_key
        return _backend_cache
    if backend_kind and backend_kind != "sqlite":
        raise RuntimeError("Text2SQL runtime supports only TEXT2SQL_BACKEND=sqlite.")
    if backend_kind == "sqlite":
        raise RuntimeError("TEXT2SQL_DATABASE_URL is required when TEXT2SQL_BACKEND=sqlite.")
    raise RuntimeError(TEXT2SQL_DATABASE_ENVIRONMENT_ERROR)


def _schema_validation_required(run_ctx: SubagentContext) -> bool:
    db_policy = run_ctx.policies.get("db", {})
    if not isinstance(db_policy, dict):
        return True
    return bool(db_policy.get("require_schema_validation", True))


def _domain_catalog_from_context(ctx: SubagentToolContext) -> Text2SQLDomainCatalog:
    return Text2SQLDomainCatalog.from_agent(subagent_context(ctx).manifest)
