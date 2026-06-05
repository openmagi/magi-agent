from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .runtime.openmagi_runtime import OpenMagiRuntime
from .transport.chat import register_chat_routes
from .transport.streaming_chat_route import register_streaming_chat_routes
from .transport.dashboard import register_dashboard_routes
from .transport.health import health_payload, healthz_payload
from .transport.shadow_generations import register_shadow_generation_routes
from .transport.plugins import register_plugin_admin_routes
from .transport.shadow_invocations import register_shadow_invocation_routes
from .transport.debug_trace import router as debug_trace_router
from .transport.learning_dashboard import register_learning_dashboard_routes
from .transport.tools import register_tool_admin_routes


def create_app(runtime: OpenMagiRuntime) -> FastAPI:
    app = FastAPI(title="Open Magi Agent", version=runtime.config.build.version)

    @app.get("/health")
    def health() -> dict[str, object]:
        return health_payload(runtime)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        payload = healthz_payload(runtime)
        return JSONResponse(content=payload, status_code=200 if payload.get("ok") else 503)

    register_chat_routes(app, runtime)
    register_streaming_chat_routes(app, runtime)
    register_shadow_invocation_routes(app, runtime)
    register_shadow_generation_routes(app, runtime)
    register_tool_admin_routes(app, runtime)
    register_plugin_admin_routes(app, runtime)
    register_dashboard_routes(app, runtime)
    # Default-OFF: mounts only when MAGI_LEARNING_DASHBOARD_ENABLED is truthy,
    # leaving the default app surface byte-identical.
    register_learning_dashboard_routes(app, runtime)
    app.include_router(debug_trace_router)

    return app
