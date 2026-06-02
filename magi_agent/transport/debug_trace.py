"""Debug endpoint exposing the current turn's execution trace.

Gated by ``MAGI_EXECUTION_TRACE=1``. Returns structural metadata only --
no secrets, no conversation content.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from magi_agent.telemetry.trace_context import get_trace, trace_enabled

router = APIRouter()


@router.get("/v1/debug/trace")
async def debug_trace_endpoint() -> JSONResponse:
    """Return the current turn's execution trace as JSON.

    * **404** -- tracing not enabled (``MAGI_EXECUTION_TRACE`` is not truthy).
    * **204** -- tracing enabled but no trace is active for this context.
    * **200** -- active trace returned.
    """
    if not trace_enabled():
        return JSONResponse({"error": "tracing not enabled"}, status_code=404)

    trace = get_trace()
    if trace is None:
        return JSONResponse({"entries": [], "summary": "no active trace"}, status_code=204)

    return JSONResponse({
        "turn_id": trace.turn_id,
        "entries": trace.to_json(),
        "summary": trace.summary(),
        "duration_breakdown": trace.duration_breakdown(),
    })
