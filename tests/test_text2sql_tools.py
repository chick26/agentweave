"""Tests for Text2SQL extension tools and SQL execution flow."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from agents.tool_context import ToolContext
from agent_runtime.core.context import RuntimeContext
from agent_runtime.storage.database import DatabaseBackend, SqlDatabaseBackend
from agent_runtime.core.model_profiles import ModelProfile
from agent_runtime.storage.artifact_store import ArtifactStore
from agent_runtime.registry.agent_registry import AgentRegistry
from agent_runtime.subagent_api import subagent_context
from subagents.text2sql import extension as tools
from subagents.text2sql.core import sql_generation
from subagents.text2sql.core.runtime_state import (
    TEXT2SQL_STATE_NAMESPACE,
    Text2SQLState,
)


def _model_profile() -> ModelProfile:
    return ModelProfile(
        base_url="http://example.test/v1",
        model_name="chat",
        api_key="not-needed",
        max_tokens=128,
    )


def _sqlite_backend(
    tmp_path: Path,
    table: str,
    columns: list[tuple[str, str]],
    rows: list[tuple[object, ...]],
) -> SqlDatabaseBackend:
    db_path = tmp_path / f"{table}.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        column_sql = ", ".join(f"{name} {kind}" for name, kind in columns)
        connection.execute(f"CREATE TABLE {table} ({column_sql})")
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return SqlDatabaseBackend(f"sqlite:///{db_path}")


def _install_text2sql_backend(run_ctx: RuntimeContext, backend: DatabaseBackend) -> None:
    state = subagent_context(run_ctx).typed_state(
        TEXT2SQL_STATE_NAMESPACE,
        Text2SQLState,
    )
    state.backend = backend


class _PolicyRegistry:
    def __init__(self, *, policies: dict):
        self._registry = AgentRegistry(subagents_root=Path("subagents"))
        self._policies = policies

    def get(self, name: str):
        manifest = self._registry.get(name)
        return replace(manifest, policies=self._policies)


def test_text2sql_extension_connects_prepared_backend_from_env(tmp_path, monkeypatch):
    db_path = tmp_path / "text2sql.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE resources (machine_room TEXT)")
    connection.execute("INSERT INTO resources VALUES ('403')")
    connection.commit()
    connection.close()
    monkeypatch.setenv("TEXT2SQL_BACKEND", "sqlite")
    monkeypatch.setenv("TEXT2SQL_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(tools, "_backend_cache", None)
    monkeypatch.setattr(tools, "_backend_cache_key", None)

    backend = tools._connect_backend()

    assert backend.get_columns("resources") == ["machine_room"]


def test_compact_rows_for_tool_limits_cell_text(monkeypatch):
    monkeypatch.setattr(tools, "SQL_RESULT_CELL_MAX_CHARS", 10)

    rows = tools._compact_rows_for_tool(
        [{"name": "NCP", "detail": "x" * 30, "count": 3}]
    )

    assert rows == [
        {
            "name": "NCP",
            "detail": "xxxxxxxxxx...[truncated 30 chars]",
            "count": 3,
        }
    ]


def test_execute_sql_returns_result_pointer_and_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SQL_RESULT_SAMPLE_ROWS", 1)
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    run_ctx = RuntimeContext(
        run_id="run-1",
        model_profile=_model_profile(),
        artifact_store=store,
    )

    output = tools._build_execute_output(
        run_ctx=subagent_context(run_ctx),
        domain_name="sea_cable_faults",
        sql="SELECT sea_cable_no FROM sea_cable_faults",
        rows=[
            {"sea_cable_no": "NCP"},
            {"sea_cable_no": "APG"},
        ],
    )

    assert output["result_id"].startswith("res_")
    assert output["row_count"] == 2
    assert output["stored_row_count"] == 2
    assert output["has_more"] is False
    assert output["row_count_is_exact"] is True
    assert output["columns"] == ["sea_cable_no"]
    assert output["sample_rows"] == [{"sea_cable_no": "NCP"}]
    assert output["sample_size"] == 1
    assert output["truncated"] is True
    assert "rows" not in output
    assert store.get_artifact_page(
        output["result_id"],
        offset=0,
        limit=10,
        run_id="run-1",
    )["rows"] == [
        {"sea_cable_no": "NCP"},
        {"sea_cable_no": "APG"},
    ]


def test_execute_sql_emits_result_created_ui_event(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SQL_RESULT_SAMPLE_ROWS", 1)
    backend = _sqlite_backend(
        tmp_path,
        "sea_cable_faults",
        [("sea_cable_no", "TEXT")],
        [("NCP",), ("APG",)],
    )
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    run_ctx = RuntimeContext(
        run_id="execute-run",
        model_profile=_model_profile(),
        artifact_store=store,
        agent_registry=_PolicyRegistry(
            policies={
                "db": {
                    "readonly": True,
                    "max_rows": 1000,
                    "sample_rows": 1,
                    "require_schema_validation": True,
                }
            }
        ),
        active_subagent="text2sql",
    )
    _install_text2sql_backend(run_ctx, backend)

    output = asyncio.run(
        tools.execute_sql.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="execute_sql",
                tool_call_id="call_execute",
                tool_arguments=json.dumps(
                    {
                        "domain_name": "sea_cable_faults",
                        "sql": "SELECT sea_cable_no FROM sea_cable_faults",
                    }
                ),
            ),
            json.dumps(
                {
                    "domain_name": "sea_cable_faults",
                    "sql": "SELECT sea_cable_no FROM sea_cable_faults",
                }
            ),
        )
    )
    payload = json.loads(output)
    result_events = [event for event in run_ctx.events if event["kind"] == "result_created"]
    tool_events = [
        event for event in run_ctx.events
        if event["kind"] in {"tool_call_start", "tool_result", "tool_call_end"}
    ]

    assert payload["result_id"].startswith("res_")
    assert payload["sample_rows"] == [{"sea_cable_no": "NCP"}]
    assert result_events
    assert result_events[0]["payload"]["ui_content"]["result_id"] == payload["result_id"]
    assert result_events[0]["payload"]["ui_content"]["metrics"]["row_count"] == 2
    assert result_events[0]["payload"]["ui_content"]["metrics"]["stored_count"] == 2
    assert result_events[0]["payload"]["ui_content"]["metadata"]["sql"] == "SELECT sea_cable_no FROM sea_cable_faults"
    assert [event["kind"] for event in tool_events] == [
        "tool_call_start",
        "tool_result",
        "tool_call_end",
    ]
    assert tool_events[0]["payload"]["tool_name"] == "execute_sql"
    assert tool_events[0]["payload"]["capability"] == "db.readonly"
    assert tool_events[0]["payload"]["policy_path"] == "db"
    assert tool_events[0]["payload"]["policy_snapshot"]["max_rows"] == 1000
    assert tool_events[1]["payload"]["metadata"]["audit_name"] == "text2sql.execute_sql"
    assert tool_events[-1]["payload"]["status"] == "completed"


def test_get_domain_schema_requires_prepared_database_environment(monkeypatch):
    monkeypatch.delenv("TEXT2SQL_BACKEND", raising=False)
    monkeypatch.delenv("TEXT2SQL_DATABASE_URL", raising=False)
    monkeypatch.setattr(tools, "_backend_cache", None)
    monkeypatch.setattr(tools, "_backend_cache_key", None)
    run_ctx = RuntimeContext(
        run_id="schema-missing-db-run",
        model_profile=_model_profile(),
        agent_registry=AgentRegistry(subagents_root=Path("subagents")),
        active_subagent="text2sql",
    )

    output = asyncio.run(
        tools.get_domain_schema.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="get_domain_schema",
                tool_call_id="call_schema",
                tool_arguments=json.dumps(
                    {
                        "domain_name": "idc_resources",
                    }
                ),
            ),
            json.dumps(
                {
                    "domain_name": "idc_resources",
                }
            ),
        )
    )
    payload = json.loads(output)

    assert "database environment is not prepared" in payload["error"]
    assert "docs/environment/text2sql.md" in payload["error"]


def test_execute_sql_emits_failed_tool_lifecycle(tmp_path):
    backend = _sqlite_backend(
        tmp_path,
        "sea_cable_faults",
        [("sea_cable_no", "TEXT")],
        [("NCP",)],
    )
    run_ctx = RuntimeContext(
        run_id="execute-failed-run",
        model_profile=_model_profile(),
        artifact_store=ArtifactStore(tmp_path / "agent_artifacts.sqlite"),
        agent_registry=AgentRegistry(subagents_root=Path("subagents")),
        active_subagent="text2sql",
    )
    _install_text2sql_backend(run_ctx, backend)

    output = asyncio.run(
        tools.execute_sql.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="execute_sql",
                tool_call_id="call_execute",
                tool_arguments=json.dumps(
                    {
                        "domain_name": "sea_cable_faults",
                        "sql": "SELECT missing FROM sea_cable_faults",
                    }
                ),
            ),
            json.dumps(
                {
                    "domain_name": "sea_cable_faults",
                    "sql": "SELECT missing FROM sea_cable_faults",
                }
            ),
        )
    )
    payload = json.loads(output)
    tool_events = [
        event for event in run_ctx.events
        if event["kind"] in {"tool_call_start", "tool_result", "tool_call_end"}
    ]

    assert payload["error"]
    assert [event["kind"] for event in tool_events] == [
        "tool_call_start",
        "tool_result",
        "tool_call_end",
    ]
    assert tool_events[1]["payload"]["status"] == "failed"
    assert tool_events[1]["error"]


def test_execute_sql_uses_manifest_policy_for_row_limits(tmp_path):
    backend = _sqlite_backend(
        tmp_path,
        "sea_cable_faults",
        [("sea_cable_no", "TEXT")],
        [("NCP",), ("APG",), ("SJC",)],
    )
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    run_ctx = RuntimeContext(
        run_id="execute-truncated-run",
        model_profile=_model_profile(),
        artifact_store=store,
        agent_registry=_PolicyRegistry(
            policies={
                "db": {
                    "readonly": True,
                    "max_rows": 2,
                    "sample_rows": 1,
                    "require_schema_validation": True,
                }
            }
        ),
        active_subagent="text2sql",
    )
    _install_text2sql_backend(run_ctx, backend)

    output = asyncio.run(
        tools.execute_sql.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="execute_sql",
                tool_call_id="call_execute",
                tool_arguments=json.dumps(
                    {
                        "domain_name": "sea_cable_faults",
                        "sql": "SELECT sea_cable_no FROM sea_cable_faults",
                    }
                ),
            ),
            json.dumps(
                {
                    "domain_name": "sea_cable_faults",
                    "sql": "SELECT sea_cable_no FROM sea_cable_faults",
                }
            ),
        )
    )
    payload = json.loads(output)

    assert payload["row_count"] == 2
    assert payload["stored_row_count"] == 2
    assert payload["store_truncated"] is True
    assert payload["has_more"] is True
    assert payload["row_count_is_exact"] is False
    assert payload["sample_max_rows"] == 1
    assert payload["store_max_rows"] == 2
    assert store.get_metadata(
        payload["result_id"],
        run_id="execute-truncated-run",
    )["metrics"]["row_count"] == 2
    assert store.get_metadata(
        payload["result_id"],
        run_id="execute-truncated-run",
    )["metrics"]["count_is_exact"] is False
    assert store.get_artifact_page(
        payload["result_id"],
        offset=0,
        limit=10,
        run_id="execute-truncated-run",
    )["rows"] == [
        {"sea_cable_no": "NCP"},
        {"sea_cable_no": "APG"},
    ]


def test_explicit_schema_value_and_sql_generation_steps(tmp_path, monkeypatch):
    monkeypatch.delenv("TEXT2SQL_SQL_MODEL", raising=False)
    backend = _sqlite_backend(
        tmp_path,
        "resources",
        [("machine_room", "TEXT"), ("cabinet_business_status", "TEXT")],
        [("403", "Available")],
    )
    run_ctx = RuntimeContext(
        run_id="explicit-text2sql-run",
        model_profile=_model_profile(),
        agent_registry=AgentRegistry(subagents_root=Path("subagents")),
        active_subagent="text2sql",
    )
    _install_text2sql_backend(run_ctx, backend)
    model_calls = []

    async def fake_call_model(self, **kwargs):
        model_calls.append(kwargs)
        return (
            "SELECT COUNT(*) AS count FROM resources "
            "WHERE machine_room = '403' AND cabinet_business_status = 'Available'"
        )

    monkeypatch.setattr(
        "agent_runtime.subagent_api.SubagentContext.call_model",
        fake_call_model,
    )

    schema_output = asyncio.run(
        tools.get_domain_schema.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="get_domain_schema",
                tool_call_id="call_schema",
                tool_arguments=json.dumps(
                    {
                        "domain_name": "idc_resources",
                    }
                ),
            ),
            json.dumps(
                {
                    "domain_name": "idc_resources",
                }
            ),
        )
    )
    value_output = asyncio.run(
        tools.search_domain_values.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="search_domain_values",
                tool_call_id="call_values",
                tool_arguments=json.dumps(
                    {
                        "domain_name": "idc_resources",
                        "query": "403",
                        "fields": ["machine_room"],
                    }
                ),
            ),
            json.dumps(
                {
                    "domain_name": "idc_resources",
                    "query": "403",
                    "fields": ["machine_room"],
                }
            ),
        )
    )
    linked_values = json.loads(value_output)["linked_values"]
    output = asyncio.run(
        tools.generate_readonly_sql.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="generate_readonly_sql",
                tool_call_id="call_generate",
                tool_arguments=json.dumps(
                    {
                        "question": "403机房有多少可用机柜？",
                        "domain_name": "idc_resources",
                        "linked_values": linked_values,
                    }
                ),
            ),
            json.dumps(
                {
                    "question": "403机房有多少可用机柜？",
                    "domain_name": "idc_resources",
                    "linked_values": linked_values,
                }
            ),
        )
    )
    schema_payload = json.loads(schema_output)
    payload = json.loads(output)
    stages = [event["payload"]["stage"] for event in run_ctx.events]

    assert schema_payload["domain"] == "idc_resources"
    assert schema_payload["table"] == "resources"
    assert "machine_room" in schema_payload["columns"]
    assert payload["domain"] == "idc_resources"
    assert payload["linked_values"][0]["field"] == "machine_room"
    metrics = {
        metric["name"]: metric
        for metric in payload["business_metrics"]
    }
    assert metrics["available_cabinet_count"]["filters"] == {
        "cabinet_business_status": "Available"
    }
    assert payload["sql"].startswith("SELECT COUNT(*)")
    assert payload["validation_error"] == ""
    assert model_calls[0]["model_name"] == "qwen3-32b"
    assert {"activation", "schema", "search_values", "sql_extract"} <= set(stages)
    tool_events = [
        event for event in run_ctx.events
        if event["kind"] in {"tool_call_start", "tool_result", "tool_call_end"}
        and event["payload"].get("tool_name") == "generate_readonly_sql"
    ]
    assert [event["kind"] for event in tool_events] == [
        "tool_call_start",
        "tool_result",
        "tool_call_end",
    ]


def test_text2sql_sql_generation_model_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_SQL_MODEL", "qwen3-32b-fast")

    assert sql_generation.sql_generation_model_name() == "qwen3-32b-fast"
