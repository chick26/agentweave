"""Tests for persisted result pages, metadata extraction, and CSV export."""

from agent_runtime.core.result_events import extract_result_metadata
from agent_runtime.core.result_formatters import ResultArtifactSpec
from agent_runtime.storage.artifact_store import ArtifactStore


def test_artifact_store_create_page_export(tmp_path):
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    rows = [
        {"sea_cable_no": "NCP", "city": "Hong Kong"},
        {"sea_cable_no": "APG", "city": "Singapore"},
    ]
    result_id = store.create_artifact(
        run_id="run-1",
        session_id="session-1",
        bot_id="data_analyst",
        artifact=_sql_artifact(
            domain="sea_cable_faults",
            sql="SELECT sea_cable_no, city FROM sea_cable_faults",
            rows=rows,
        ),
    )

    metadata = store.get_metadata(
        result_id,
        run_id="run-1",
        session_id="session-1",
        bot_id="data_analyst",
    )
    assert metadata["result_id"] == result_id
    assert metadata["run_id"] == "run-1"
    assert metadata["metadata"]["domain"] == "sea_cable_faults"
    assert metadata["metadata"]["sql"] == "SELECT sea_cable_no, city FROM sea_cable_faults"
    assert metadata["preview"]["columns"] == ["sea_cable_no", "city"]
    assert metadata["metrics"]["row_count"] == 2
    assert metadata["metrics"]["stored_count"] == 2

    assert store.get_artifact_page(
        result_id,
        offset=1,
        limit=1,
        session_id="session-1",
    )["rows"] == [
        {"sea_cable_no": "APG", "city": "Singapore"}
    ]
    csv_text = store.export_csv(result_id, bot_id="data_analyst").decode("utf-8-sig")
    assert "sea_cable_no,city" in csv_text
    assert "NCP,Hong Kong" in csv_text
    assert "APG,Singapore" in csv_text


def test_artifact_store_cleanup_by_max_results_and_age(tmp_path):
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    old_id = store.create_artifact(
        run_id="run-old",
        session_id="session-old",
        bot_id="bot",
        artifact=_sql_artifact(sql="SELECT 1", rows=[{"value": 1}]),
    )
    keep_id = store.create_artifact(
        run_id="run-keep",
        session_id="session-keep",
        bot_id="bot",
        artifact=_sql_artifact(sql="SELECT 2", rows=[{"value": 2}]),
    )
    drop_id = store.create_artifact(
        run_id="run-drop",
        session_id="session-drop",
        bot_id="bot",
        artifact=_sql_artifact(sql="SELECT 3", rows=[{"value": 3}]),
    )
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE result_artifacts SET created_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00.000Z", old_id),
        )
        store._connection.execute(
            "UPDATE result_artifacts SET created_at = ? WHERE id = ?",
            ("2999-01-01T00:00:00.000Z", keep_id),
        )
        store._connection.execute(
            "UPDATE result_artifacts SET created_at = ? WHERE id = ?",
            ("2999-01-02T00:00:00.000Z", drop_id),
        )
    assert store.cleanup(max_age_hours=1) == 1
    assert store.cleanup(max_results=1) == 1

    assert store.get_metadata(drop_id, run_id="run-drop")["result_id"] == drop_id
    for result_id, run_id in ((old_id, "run-old"), (keep_id, "run-keep")):
        try:
            store.get_metadata(result_id, run_id=run_id)
        except KeyError:
            pass
        else:
            raise AssertionError(f"Expected {result_id} to be cleaned")


def test_artifact_store_scope_guard_allows_matching_scope(tmp_path):
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    result_id = store.create_artifact(
        run_id="run-1",
        session_id="session-1",
        bot_id="data_analyst",
        artifact=_sql_artifact(sql="SELECT 1", rows=[{"value": 1}]),
    )

    metadata = store.get_metadata(
        result_id,
        run_id="run-1",
        session_id="session-1",
        bot_id="data_analyst",
    )

    assert metadata["session_id"] == "session-1"
    assert metadata["bot_id"] == "data_analyst"
    assert store.get_artifact_page(result_id, session_id="session-1")["rows"] == [
        {"value": 1}
    ]
    assert "value" in store.export_csv(result_id, bot_id="data_analyst").decode("utf-8-sig")


def test_artifact_store_scope_guard_rejects_mismatched_scope(tmp_path):
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    result_id = store.create_artifact(
        run_id="run-1",
        session_id="session-1",
        bot_id="data_analyst",
        artifact=_sql_artifact(sql="SELECT 1", rows=[{"value": 1}]),
    )

    try:
        store.get_metadata(result_id, session_id="session-2")
    except KeyError:
        pass
    else:
        raise AssertionError("Expected mismatched session scope to be rejected")

    try:
        store.get_metadata(result_id)
    except KeyError:
        pass
    else:
        raise AssertionError("Expected missing scope to be rejected")


def test_artifact_store_preserves_generic_preview_envelope(tmp_path):
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    result_id = store.create_artifact(
        run_id="run-1",
        session_id="session-1",
        bot_id="data_analyst",
        artifact=ResultArtifactSpec(
            artifact_type="chart_spec",
            title="Chart Spec",
            source="build_chart_spec",
            rows=[],
            preview={
                "kind": "chart_spec",
                "spec": {
                    "mark": "bar",
                    "encoding": {
                        "x": {"field": "city"},
                        "y": {"field": "fault_count"},
                    },
                },
            },
            metadata={"source_result_id": "res_source"},
            row_count=0,
        ),
    )

    metadata = store.get_metadata(result_id, session_id="session-1")

    assert metadata["artifact_type"] == "chart_spec"
    assert metadata["preview"]["kind"] == "chart_spec"
    assert metadata["preview"]["spec"]["mark"] == "bar"
    assert metadata["metadata"]["source_result_id"] == "res_source"


def test_artifact_store_reports_actual_stored_count_for_truncated_artifacts(tmp_path):
    store = ArtifactStore(tmp_path / "agent_artifacts.sqlite")
    result_id = store.create_artifact(
        run_id="run-1",
        session_id="session-1",
        artifact=ResultArtifactSpec(
            artifact_type="sql_result",
            title="Truncated SQL Result",
            source="execute_sql",
            rows=[{"value": 1}, {"value": 2}],
            row_count=10,
            row_count_is_exact=False,
        ),
    )

    metadata = store.get_metadata(result_id, session_id="session-1")

    assert metadata["metrics"]["row_count"] == 10
    assert metadata["metrics"]["stored_count"] == 2
    assert metadata["metrics"]["count_is_exact"] is False
    assert metadata["metrics"]["truncated"] is True


def test_extract_result_metadata_from_result_created_event():
    events = [
        {
            "kind": "result_created",
            "payload": {
                "result": {
                    "result_id": "res_123",
                    "artifact_type": "sql_result",
                    "title": "SQL Result",
                    "source": "execute_sql",
                    "run_id": "run-1",
                    "session_id": "session-1",
                    "bot_id": "data_analyst",
                    "preview": {
                        "kind": "rows",
                        "columns": ["value"],
                        "rows": [{"value": 1}],
                    },
                    "metrics": {
                        "row_count": 1,
                        "stored_count": 1,
                        "count_is_exact": True,
                        "truncated": False,
                    },
                    "metadata": {"sql": "SELECT 1"},
                    "created_at": "2026-01-01T00:00:00.000Z",
                },
            },
        }
    ]

    assert extract_result_metadata(events) == [
        {
            "result_id": "res_123",
            "artifact_type": "sql_result",
            "title": "SQL Result",
            "source": "execute_sql",
            "run_id": "run-1",
            "session_id": "session-1",
            "bot_id": "data_analyst",
            "preview": {
                "kind": "rows",
                "columns": ["value"],
                "rows": [{"value": 1}],
            },
            "metrics": {
                "row_count": 1,
                "stored_count": 1,
                "count_is_exact": True,
                "truncated": False,
            },
            "metadata": {"sql": "SELECT 1"},
            "created_at": "2026-01-01T00:00:00.000Z",
        }
    ]


def _sql_artifact(
    *,
    domain: str = "demo",
    sql: str,
    rows: list[dict[str, object]],
) -> ResultArtifactSpec:
    return ResultArtifactSpec(
        artifact_type="sql_result",
        title="SQL Result",
        source="execute_sql",
        rows=rows,
        columns=list(rows[0].keys()) if rows else [],
        preview_rows=rows[:5],
        metadata={
            "domain": domain,
            "sql": sql,
        },
        row_count=len(rows),
        row_count_is_exact=True,
    )
