"""Control-request REST surface consumed by the restored web dashboard.

The historical dashboard polls ``/v1/control-requests`` and
``/v1/control-events`` and posts approvals to
``/v1/control-requests/{id}/response`` to drive the tool-approval and
plan-approval UI. The local ADK runtime surfaces those interactions inline on
the chat SSE stream (``ask_user`` / ``plan_ready`` frames, answered via
``/v1/chat/control-response``), so there is no server-side control-request
ledger for a local session.

These endpoints exist for parity so the dashboard's background pollers receive
well-formed empty responses instead of 404s (which the client treats as a hard
error). They never expose runtime state and require the same gateway-token
authorization as the chat routes.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.chat_shared import bearer_auth_failed


def _unauthorized(request: Request, runtime: OpenMagiRuntime) -> bool:
    # A-9: constant-time gateway-token check via the shared helper.
    return bearer_auth_failed(request, runtime)


def register_control_request_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/v1/control-requests")
    def control_requests(request: Request) -> JSONResponse:
        if _unauthorized(request, runtime):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"requests": []})

    @app.get("/v1/control-events")
    def control_events(request: Request) -> JSONResponse:
        if _unauthorized(request, runtime):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            last_seq = int(request.query_params.get("lastSeq", "0"))
        except (TypeError, ValueError):
            last_seq = 0
        return JSONResponse({"events": [], "lastSeq": last_seq})

    @app.post("/v1/control-requests/{request_id}/response")
    async def control_request_response(request_id: str, request: Request) -> JSONResponse:
        if _unauthorized(request, runtime):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # No server-side ledger for local sessions; acknowledge so the client
        # resolves its optimistic local copy of the request.
        return JSONResponse({"ok": True, "requestId": request_id, "request": None})
