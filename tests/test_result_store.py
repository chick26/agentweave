from agent_runtime.core.result_events import extract_result_metadata
from agent_runtime.storage.result_store import ResultStore


def test_result_store_create_page_export(tmp_path):
    store = ResultStore(tmp_path / "agent_results.sqlite")
    result_id = store.create_result(
        run_id="run-1",
        domain="sea_cable_faults",
        sql="SELECT sea_cable_no, city FROM sea_cable_faults",
        rows=[
            {"sea_cable_no": "NCP", "city": "Hong Kong"},
            {"sea_cable_no": "APG", "city": "Singapore"},
        ],
    )

    metadata = store.get_metadata(result_id)
    assert metadata["result_id"] == result_id
    assert metadata["run_id"] == "run-1"
    assert metadata["domain"] == "sea_cable_faults"
    assert metadata["columns"] == ["sea_cable_no", "city"]
    assert metadata["row_count"] == 2

    assert store.get_page(result_id, offset=1, limit=1) == [
        {"sea_cable_no": "APG", "city": "Singapore"}
    ]
    csv_text = store.export_csv(result_id).decode("utf-8-sig")
    assert "sea_cable_no,city" in csv_text
    assert "NCP,Hong Kong" in csv_text
    assert "APG,Singapore" in csv_text


def test_result_store_cleanup_by_max_results_and_age(tmp_path):
    store = ResultStore(tmp_path / "agent_results.sqlite")
    old_id = store.create_result(
        run_id="run-old",
        domain="demo",
        sql="SELECT 1",
        rows=[{"value": 1}],
    )
    keep_id = store.create_result(
        run_id="run-keep",
        domain="demo",
        sql="SELECT 2",
        rows=[{"value": 2}],
    )
    drop_id = store.create_result(
        run_id="run-drop",
        domain="demo",
        sql="SELECT 3",
        rows=[{"value": 3}],
    )
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE query_results SET created_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00.000Z", old_id),
        )
        store._connection.execute(
            "UPDATE query_results SET created_at = ? WHERE id = ?",
            ("2999-01-01T00:00:00.000Z", keep_id),
        )
        store._connection.execute(
            "UPDATE query_results SET created_at = ? WHERE id = ?",
            ("2999-01-02T00:00:00.000Z", drop_id),
        )
    assert store.cleanup(max_age_hours=1) == 1
    assert store.cleanup(max_results=1) == 1

    assert store.get_metadata(drop_id)["result_id"] == drop_id
    for result_id in (old_id, keep_id):
        try:
            store.get_metadata(result_id)
        except KeyError:
            pass
        else:
            raise AssertionError(f"Expected {result_id} to be cleaned")


def test_extract_result_metadata_from_trace():
    events = [
        {
            "kind": "subagent_trace",
            "payload": {
                "stage": "execute",
                "input": "SELECT 1",
                "output": {
                    "sql": "SELECT 1",
                    "result_id": "res_123",
                    "row_count": 1,
                    "columns": ["value"],
                    "sample_rows": [{"value": 1}],
                    "sample_size": 1,
                    "truncated": False,
                    "error": None,
                },
            },
        }
    ]

    assert extract_result_metadata(events) == [
        {
            "result_id": "res_123",
            "row_count": 1,
            "stored_row_count": 1,
            "columns": ["value"],
            "sample_rows": [{"value": 1}],
            "sample_size": 1,
            "truncated": False,
            "store_truncated": False,
            "has_more": False,
            "row_count_is_exact": True,
            "sql": "SELECT 1",
        }
    ]
