from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent_runtime.server.service import AgentService, AgentServiceConfig


def create_app(
    *,
    host: str = "127.0.0.1",
    token: str = "",
    root: Path | None = None,
    service: AgentService | None = None,
) -> FastAPI:
    if not _is_loopback_host(host) and not token:
        raise ValueError("AGENTWEAVE_SERVER_TOKEN is required when binding non-localhost hosts.")
    if service is None:
        service = AgentService(config=AgentServiceConfig.from_env(root=root))

    app = FastAPI(title="AgentWeave API", version="0.1.0")
    app.state.service = service
    app.state.token = token
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins_from_env(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    def require_auth(authorization: str = Header(default="")) -> None:
        expected_token = str(app.state.token or "")
        if expected_token and authorization != f"Bearer {expected_token}":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="unauthorized",
            )

    def current_service() -> AgentService:
        return app.state.service

    @app.get("/health", dependencies=[Depends(require_auth)])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/sessions", dependencies=[Depends(require_auth)])
    def create_session(
        payload: dict[str, Any] | None = Body(default=None),
        service: AgentService = Depends(current_service),
    ) -> dict[str, Any]:
        payload = _dict_or_empty(payload)
        return service.create_session(
            session_id=str(payload.get("session_id") or ""),
            metadata=_dict_or_empty(payload.get("metadata")),
        )

    @app.post(
        "/sessions/{session_id}/runs",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_auth)],
    )
    def create_run(
        session_id: str,
        payload: dict[str, Any] | None = Body(default=None),
        service: AgentService = Depends(current_service),
    ) -> dict[str, Any]:
        payload = _dict_or_empty(payload)
        return service.create_run(
            session_id=session_id,
            message=str(payload.get("message") or ""),
            max_turns=int(payload.get("max_turns") or 10),
            metadata=_dict_or_empty(payload.get("metadata")),
        )

    @app.get("/runs/{run_id}/events", dependencies=[Depends(require_auth)])
    def run_events(
        run_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        service: AgentService = Depends(current_service),
    ) -> StreamingResponse:
        start_after = after_sequence or _last_event_id(request)
        return StreamingResponse(
            (
                _sse_frame(event)
                for event in service.iter_sse_events(run_id, after_sequence=start_after)
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/runs/{run_id}", dependencies=[Depends(require_auth)])
    def get_run(
        run_id: str,
        service: AgentService = Depends(current_service),
    ) -> dict[str, Any]:
        try:
            return service.get_run(run_id)
        except KeyError as exc:
            raise _not_found(exc) from exc

    @app.get("/results/{result_id}.csv", dependencies=[Depends(require_auth)])
    def export_result_csv(
        result_id: str,
        service: AgentService = Depends(current_service),
    ) -> Response:
        try:
            return Response(
                content=service.export_result_csv(result_id),
                media_type="text/csv; charset=utf-8",
            )
        except KeyError as exc:
            raise _not_found(exc) from exc

    @app.get("/results/{result_id}", dependencies=[Depends(require_auth)])
    def get_result(
        result_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=1),
        service: AgentService = Depends(current_service),
    ) -> dict[str, Any]:
        try:
            return service.get_result_page(result_id, page=page, page_size=page_size)
        except KeyError as exc:
            raise _not_found(exc) from exc

    @app.get("/diagnostics/{run_id}", dependencies=[Depends(require_auth)])
    def get_diagnostic(
        run_id: str,
        service: AgentService = Depends(current_service),
    ) -> dict[str, Any]:
        try:
            return service.get_diagnostic(run_id)
        except KeyError as exc:
            raise _not_found(exc) from exc

    @app.post("/resources/reload", dependencies=[Depends(require_auth)])
    def reload_resources(
        payload: dict[str, Any] | None = Body(default=None),
        service: AgentService = Depends(current_service),
    ) -> dict[str, Any]:
        payload = _dict_or_empty(payload)
        return service.reload_resources(reason=str(payload.get("reason") or "manual"))

    @app.exception_handler(ValueError)
    def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Bad Request", "message": str(exc)},
        )

    @app.exception_handler(HTTPException)
    def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": _status_phrase(exc.status_code), "message": message},
        )

    return app


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str = "",
    root: Path | None = None,
    service: AgentService | None = None,
) -> FastAPI:
    return create_app(host=host, token=token, root=root, service=service)


def _sse_frame(event: dict[str, Any]) -> bytes:
    event_type = str(event.get("type") or "message")
    if event_type == "keepalive":
        return b":keepalive\n\n"
    event_id = str(event.get("sequence") or "")
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n".encode("utf-8")


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _cors_origins_from_env() -> list[str]:
    raw = os.getenv("AGENTWEAVE_CORS_ORIGINS", "").strip()
    if not raw:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    return [item.strip() for item in raw.split(",") if item.strip()]


def _last_event_id(request: Request) -> int:
    value = request.headers.get("Last-Event-ID", "")
    return int(value) if value.isdigit() else 0


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _status_phrase(status_code: int) -> str:
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "Unauthorized"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "Not Found"
    if status_code == status.HTTP_400_BAD_REQUEST:
        return "Bad Request"
    return "HTTP Error"
