from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_runtime.server.app import create_app
from tests.test_server_service import _service


def test_server_requires_token_for_non_loopback_host(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="TOKEN is required"):
        create_app(host="0.0.0.0", token="", service=_service(tmp_path))


def test_server_rejects_missing_bearer_token(tmp_path: Path) -> None:
    app = create_app(host="127.0.0.1", token="secret", service=_service(tmp_path))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 401
    assert response.json()["message"] == "unauthorized"


def test_server_accepts_authorized_session_request(tmp_path: Path) -> None:
    app = create_app(host="127.0.0.1", token="secret", service=_service(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/sessions",
        json={"session_id": "web-test"},
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "web-test"
    assert response.json()["message"] == "welcome:web-test"


def test_server_streams_sse_events(tmp_path: Path) -> None:
    app = create_app(host="127.0.0.1", token="secret", service=_service(tmp_path))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    created = client.post(
        "/sessions/web-test/runs",
        json={"message": "查 403"},
        headers=headers,
    ).json()

    with client.stream("GET", created["events_url"], headers=headers) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "event: runtime_event" in body
    assert "event: result_created" in body
    assert "event: model_delta" in body
    assert "event: run_complete" in body
    assert "id: 6" in body


def test_server_formats_keepalive_as_sse_comment() -> None:
    from agent_runtime.server.app import _sse_frame

    assert _sse_frame({"type": "keepalive"}).decode("utf-8") == ":keepalive\n\n"
