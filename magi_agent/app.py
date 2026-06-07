from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .learning.bootstrap import LearningBootstrap
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
from magi_agent.observability import register_observability

logger = logging.getLogger(__name__)


def _build_learning_bootstrap(runtime: OpenMagiRuntime) -> LearningBootstrap | None:
    """Construct the learning bootstrap, fail-open.

    PR9b: on a default install the SAFE reflect tier runs in the background
    (operator's local sessions → deterministic label → eval-gate → ``proposed``
    items for the already-mounted dashboard).  Construction is wrapped so a build
    failure (or master/reflect tier off) never blocks app startup — it just
    yields ``None`` and the layer stays inert.  No prompt injection, no LLM cost,
    no behaviour change; the frozen ``Literal[False]`` authority flags are never
    flipped.
    """
    try:
        return LearningBootstrap(
            app_name="magi",
            user_id=runtime.config.user_id,
        )
    except Exception:  # noqa: BLE001 - fail-open: never block startup
        logger.warning(
            "learning bootstrap construction failed; learning layer inert",
            exc_info=True,
        )
        return None


def create_app(runtime: OpenMagiRuntime) -> FastAPI:
    bootstrap = _build_learning_bootstrap(runtime)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Start the safe reflect tier in the background.  ``start()`` is itself
        # fail-open (a disabled tier / missing session DB / build error leaves it
        # inert), and the extra guard here means a learning failure can NEVER
        # crash app startup or shutdown.
        if bootstrap is not None:
            try:
                await bootstrap.start()
            except Exception:  # noqa: BLE001 - fail-open
                logger.warning(
                    "learning bootstrap start failed; learning layer inert",
                    exc_info=True,
                )
        try:
            yield
        finally:
            if bootstrap is not None:
                try:
                    await bootstrap.stop()
                except Exception:  # noqa: BLE001 - never raise on shutdown
                    logger.warning(
                        "learning bootstrap stop failed", exc_info=True
                    )

    app = FastAPI(
        title="Open Magi Agent",
        version=runtime.config.build.version,
        lifespan=lifespan,
    )

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
    # Default-OFF: mounts only when MAGI_OBSERVABILITY_ENABLED is truthy,
    # leaving the default app surface byte-identical.
    register_observability(app, runtime)

    return app
