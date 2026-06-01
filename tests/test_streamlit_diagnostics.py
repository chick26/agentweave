from agent_runtime.ui.streamlit.diagnostics import _model_call_detail_tabs


def test_model_call_detail_tabs_hide_missing_payloads() -> None:
    assert _model_call_detail_tabs({"model": "qwen"}) == [("完整 JSON", "raw")]


def test_model_call_detail_tabs_keep_available_payloads() -> None:
    assert _model_call_detail_tabs(
        {
            "request": {"messages": []},
            "response": {"choices": []},
        }
    ) == [
        ("实际输入", "request"),
        ("实际输出", "response"),
        ("完整 JSON", "raw"),
    ]


def test_model_call_detail_tabs_do_not_show_empty_response_tab() -> None:
    assert _model_call_detail_tabs({"request": {"messages": []}}) == [
        ("实际输入", "request"),
        ("完整 JSON", "raw"),
    ]
