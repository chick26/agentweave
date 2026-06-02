from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/api/http-sse-contract.md"
INTEGRATION = ROOT / "docs/frontend/ts-web-integration.md"
API_CLIENT = ROOT / "docs/frontend/examples/api-client.ts"
SSE_CLIENT = ROOT / "docs/frontend/examples/sse-client.ts"


def test_http_sse_contract_documents_required_endpoints() -> None:
    text = CONTRACT.read_text(encoding="utf-8")

    for endpoint in [
        "POST /sessions",
        "POST /sessions/{session_id}/runs",
        "GET /runs/{run_id}/events",
        "GET /runs/{run_id}",
        "GET /results/{result_id}",
        "GET /diagnostics/{run_id}",
        "POST /resources/reload",
    ]:
        assert endpoint in text


def test_http_sse_contract_documents_stable_sse_events() -> None:
    text = CONTRACT.read_text(encoding="utf-8")

    for event_type in [
        '"runtime_event"',
        '"result_created"',
        '"model_delta"',
        '"run_complete"',
        '"run_error"',
    ]:
        assert event_type in text

    for stable_field in ["type", "run_id", "sequence", "timestamp"]:
        assert stable_field in text


def test_typescript_example_exports_contract_interfaces() -> None:
    text = API_CLIENT.read_text(encoding="utf-8")

    for interface_name in [
        "SessionResponse",
        "RunCreatedResponse",
        "RuntimeEvent",
        "ModelDeltaEvent",
        "RunCompleteEvent",
        "ResultPage",
        "DiagnosticRun",
    ]:
        assert f"export interface {interface_name}" in text

    for method_name in [
        "createSession",
        "createRun",
        "getResult",
        "getDiagnostics",
        "reloadResources",
    ]:
        assert method_name in text


def test_frontend_docs_warn_about_eventsource_auth_limitation() -> None:
    integration = INTEGRATION.read_text(encoding="utf-8")
    sse_client = SSE_CLIENT.read_text(encoding="utf-8")

    assert "EventSource" in integration
    assert "Authorization" in integration
    assert "ReadableStream" in integration
    assert "Authorization" in sse_client
    assert "text/event-stream" in sse_client


def test_contract_documents_sse_event_boundaries() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    integration = INTEGRATION.read_text(encoding="utf-8")

    for text in [contract, integration]:
        assert "model_calls" in text
        assert "GET /diagnostics/{run_id}" in text
        assert "POST /sessions" in text
        assert ":keepalive" in text
        assert "subagent_trace" in text
        assert "tool_result" in text
        assert "context_compressed" in text
        assert "model_delta" in text
        assert "payload.kind" in text
        assert "run_complete.answer" in text
