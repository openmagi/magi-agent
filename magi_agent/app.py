from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .learning.bootstrap import LearningBootstrap
from .runtime.openmagi_runtime import OpenMagiRuntime
from .transport.chat import register_chat_routes
from .transport.control_requests import register_control_request_routes
from .transport.streaming_chat_route import register_streaming_chat_routes
from .transport.web_dashboard import register_dashboard_routes
from .transport.health import health_payload, healthz_payload
from .transport.plugins import register_plugin_admin_routes
from .transport.shadow_invocations import register_shadow_invocation_routes
from .transport.debug_trace import router as debug_trace_router
from .transport.learning_dashboard import register_learning_dashboard_routes
from .transport.tools import register_tool_admin_routes
from .transport.app_api import register_app_api_routes
from .transport.customize import register_customize_routes
from .transport.packs_dashboard import register_dashboard_pack_routes
from .transport.credentials import register_credentials_routes
from .transport.integrations import register_integrations_routes
from magi_agent.observability import register_observability, register_session_transcript
from magi_agent.missions.work_queue.board_api import register_work_queue_board
from magi_agent.egress_proxy.config import EgressProxyConfig

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
    # Fail-closed: if the egress proxy is enabled-but-misconfigured, refuse to
    # start. Default-OFF: a no-op when MAGI_EGRESS_PROXY_ENABLED is unset.
    EgressProxyConfig.from_env().validate()

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
            # Serve session-end memory extraction: the local serve path has no
            # per-conversation end / shared session service, so we flush the
            # in-process per-session transcript buffer once on shutdown. Gated
            # (empty + no-op unless MAGI_MEMORY_SESSION_EXTRACT_ENABLED) and
            # fail-soft — it can never block or crash shutdown.
            try:
                from magi_agent.runtime.active_sessions import (  # noqa: PLC0415
                    drain_and_extract,
                )

                await drain_and_extract()
            except Exception:  # noqa: BLE001 - never raise on shutdown
                logger.warning("session-extract shutdown drain failed", exc_info=True)
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
    register_control_request_routes(app, runtime)
    register_shadow_invocation_routes(app, runtime)
    register_tool_admin_routes(app, runtime)
    register_customize_routes(app, runtime)
    # Default-OFF: dashboard pack-builder REST endpoints. Routes register
    # unconditionally but every handler returns 410 unless
    # MAGI_DASHBOARD_PACK_AUTHORING_ENABLED is on AND the deployment is not
    # hosted (self-host only — same model as the user HookBus).
    register_dashboard_pack_routes(app, runtime)
    # Default-OFF vault seam: routes serve unconditionally, but registration
    # returns 503 and persists nothing until MAGI_VAULT_ADMIN_ENABLED + a real
    # vault admin API are wired.
    register_credentials_routes(app, runtime)
    register_integrations_routes(app, runtime)
    register_app_api_routes(app, runtime)
    register_plugin_admin_routes(app, runtime)
    register_dashboard_routes(app, runtime)
    # Default-OFF: mounts only when MAGI_LEARNING_DASHBOARD_ENABLED is truthy,
    # leaving the default app surface byte-identical.
    register_learning_dashboard_routes(app, runtime)
    app.include_router(debug_trace_router)
    # Default-OFF: mounts only when MAGI_OBSERVABILITY_ENABLED is truthy,
    # leaving the default app surface byte-identical.
    register_observability(app, runtime)
    # Default-OFF: installs the per-session JSONL transcript sink only when
    # MAGI_SESSION_TRANSCRIPT_ENABLED is truthy; otherwise registers nothing.
    register_session_transcript(app, runtime)
    # Default-OFF: mounts the read-only work-queue board API only when
    # MAGI_WORK_QUEUE_BOARD_API_ENABLED is truthy; otherwise registers nothing.
    register_work_queue_board(app, runtime)

    return app
