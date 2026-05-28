import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from agents.tool_context import ToolContext
from agent_runtime.context import RunContext
from agent_runtime.database import CsvSQLiteBackend
from agent_runtime.model_profiles import ModelProfile
from agent_runtime.result_store import ResultStore
from agent_runtime.skill_registry import AgentRegistry
from subagents.text2sql import tools


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
    store = ResultStore(tmp_path / "agent_results.sqlite")
    run_ctx = SimpleNamespace(
        run_id="run-1",
        active_domain="sea_cable_faults",
        result_store=store,
    )

    output = tools._build_execute_output(
        run_ctx=run_ctx,
        sql="SELECT sea_cable_no FROM sea_cable_faults",
        rows=[
            {"sea_cable_no": "NCP"},
            {"sea_cable_no": "APG"},
        ],
    )

    assert output["result_id"].startswith("res_")
    assert output["row_count"] == 2
    assert output["columns"] == ["sea_cable_no"]
    assert output["sample_rows"] == [{"sea_cable_no": "NCP"}]
    assert output["sample_size"] == 1
    assert output["truncated"] is True
    assert "rows" not in output
    assert store.get_page(output["result_id"], offset=0, limit=10) == [
        {"sea_cable_no": "NCP"},
        {"sea_cable_no": "APG"},
    ]


def test_plan_sql_query_runs_internal_domain_and_value_steps(tmp_path, monkeypatch):
    csv_path = tmp_path / "resources.csv"
    csv_path.write_text(
        "machine_room,cabinet_business_status\n"
        "403,Available\n",
        encoding="utf-8",
    )
    backend = CsvSQLiteBackend({"resources": csv_path})
    run_ctx = RunContext(
        run_id="plan-run",
        backend=backend,
        model_profiles={
            "sql_worker": ModelProfile(
                role="sql_worker",
                base_url="http://sql.test/v1",
                model_name="sql",
                api_key="key",
                max_tokens=128,
            )
        },
        agent_registry=AgentRegistry(subagents_root=Path("subagents")),
    )

    async def fake_call_chat_model(**kwargs):
        return (
            "SELECT COUNT(*) AS count FROM resources "
            "WHERE machine_room = '403' AND cabinet_business_status = 'Available'"
        )

    monkeypatch.setattr(tools, "call_chat_model", fake_call_chat_model)

    output = asyncio.run(
        tools.plan_sql_query.on_invoke_tool(
            ToolContext(
                context=run_ctx,
                tool_name="plan_sql_query",
                tool_call_id="call_plan",
                tool_arguments=json.dumps(
                    {
                        "question": "403机房有多少可用机柜？",
                        "domain_name": "idc_resources",
                        "value_queries": ["403", "可用"],
                    }
                ),
            ),
            json.dumps(
                {
                    "question": "403机房有多少可用机柜？",
                    "domain_name": "idc_resources",
                    "value_queries": ["403", "可用"],
                }
            ),
        )
    )
    payload = json.loads(output)
    stages = [event["payload"]["stage"] for event in run_ctx.events]

    assert payload["domain"] == "idc_resources"
    assert payload["sql_plan"]["linked_values"][0]["field"] == "machine_room"
    metrics = {
        metric["name"]: metric
        for metric in payload["sql_plan"]["business_metrics"]
    }
    assert metrics["available_cabinet_count"]["filters"] == {
        "cabinet_business_status": "Available"
    }
    assert "business_metric" not in payload
    assert "confidence" not in payload
    assert "unlinked_values" not in payload
    assert "assumptions" not in payload
    assert payload["sql"].startswith("SELECT COUNT(*)")
    assert payload["validation_error"] == ""
    assert {"activation", "search_values", "sql_plan", "sql_extract"} <= set(stages)
