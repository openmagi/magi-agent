from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from magi_agent.observability.config import ObservabilityConfig
from magi_agent.observability.core import ObservabilityCore
from magi_agent.observability.runtime_sink import set_active_sink

logger = logging.getLogger(__name__)


def resolve_observability_home() -> Path:
    """The observability home directory: ``MAGI_OBS_HOME`` when set, else the
    hidden home dir under the cwd. Shared so sibling features (e.g. the session
    transcript) write under the same parent without re-deriving the path."""
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    return Path(flag_str("MAGI_OBS_HOME") or (Path.cwd() / ".openmagi"))


def register_observability(app: Any, runtime: Any) -> ObservabilityCore | None:
    """Mount the observability module into a FastAPI app + activate the event
    sink. Fully inert (returns None) when MAGI_OBSERVABILITY_ENABLED is unset."""
    home = resolve_observability_home()
    config = ObservabilityConfig.from_env(home=home)
    if not config.enabled:
        return None

    if getattr(app.state, "observability_core", None) is not None:
        return app.state.observability_core  # idempotent: already registered

    core = ObservabilityCore(config, runtime=runtime)
    if core.router is not None:
        app.include_router(core.router)

    from magi_agent.observability.page import build_page_router

    app.include_router(build_page_router(runtime))

    set_active_sink(core.record_public_event)
    app.state.observability_core = core

    # Opportunistic one-shot prune at startup; the periodic retention loop is
    # started lazily by the core on the first recorded event (in the run loop).
    try:
        core.store.prune(max_events=config.max_events, retention_days=config.retention_days)
    except Exception:
        logger.debug("observability initial prune failed", exc_info=True)

    return core
