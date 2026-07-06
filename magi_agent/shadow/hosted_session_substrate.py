"""Durable hosted ADK session substrate (PR-3).

The hosted reuse lease registry (``session_service_registry``) historically
handed out a fresh, process-memory ``InMemorySessionService`` on every miss, so
hosted conversation state was lost on pod restart, image bump, LRU eviction or
TTL expiry. This module resolves a PROCESS-SINGLETON ``SqliteSessionService``
backed by a file on the PVC (``<MAGI_STATE_DIR>/adk_sessions.db``) that persists
sessions AND events across all of those, keyed per-bot per-session by the ADK
session id.

Wiring model:
* ``durable_hosted_session_factory`` returns a factory that always yields the
  same singleton durable service (or ``None`` when the substrate is disabled or
  unconstructible, so the caller keeps today's in-memory behavior).
* The lease registry fronts that singleton for single-flight per (bot, session);
  a busy-overlap uses a DISTINCT fresh in-memory fallback so two turns never
  mutate one durable session concurrently.

Fully fail-open: any import/construction error logs at debug and returns
``None``. A serving turn must never fail because of the durable substrate.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


# Generous fetch bound so a very long session can never blow the prompt; the
# model call itself remains the hard bound. Read by the boundary run_config.
DEFAULT_NUM_RECENT_EVENTS = 200


_singleton_lock = threading.Lock()
_singleton_service: object | None = None
_singleton_path: str | None = None
_singleton_failed_path: str | None = None


def _build_sqlite_session_service(db_path: str) -> object | None:
    """Construct a ``SqliteSessionService`` at ``db_path`` (fail-open)."""
    try:
        from pathlib import Path

        from google.adk.sessions.sqlite_session_service import SqliteSessionService

        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        return SqliteSessionService(db_path)
    except Exception:
        logger.debug("hosted durable session service construction failed", exc_info=True)
        return None


def get_durable_hosted_session_service(db_path: str) -> object | None:
    """Return the process-singleton durable session service for ``db_path``.

    Built once per path. A prior construction failure for the same path is
    remembered so repeated turns do not re-attempt (and re-log) construction on
    the hot path; a different path resets that memo.
    """
    global _singleton_service, _singleton_path, _singleton_failed_path
    with _singleton_lock:
        if _singleton_service is not None and _singleton_path == db_path:
            return _singleton_service
        if _singleton_failed_path == db_path and _singleton_service is None:
            return None
        service = _build_sqlite_session_service(db_path)
        if service is None:
            _singleton_failed_path = db_path
            return None
        _singleton_service = service
        _singleton_path = db_path
        _singleton_failed_path = None
        return service


def durable_hosted_session_factory() -> Callable[[], object] | None:
    """Return a zero-arg factory yielding the durable singleton, or ``None``.

    ``None`` means the durable substrate is disabled or unconstructible; the
    caller then keeps the in-memory registry behavior. When non-``None`` the
    returned factory yields the SAME singleton on every call, so the lease
    registry stores one shared durable service across every (bot, session) key.
    """
    try:
        from magi_agent.config.env import (
            hosted_session_db_path,
            is_hosted_session_db_enabled,
        )

        if not is_hosted_session_db_enabled():
            return None
        db_path = str(hosted_session_db_path())
    except Exception:
        logger.debug("hosted durable session substrate gate failed", exc_info=True)
        return None

    service = get_durable_hosted_session_service(db_path)
    if service is None:
        return None
    return lambda: service


def reset_durable_hosted_session_service() -> None:
    """Drop the process-singleton (tests / explicit lifecycle only)."""
    global _singleton_service, _singleton_path, _singleton_failed_path
    with _singleton_lock:
        _singleton_service = None
        _singleton_path = None
        _singleton_failed_path = None


__all__ = [
    "DEFAULT_NUM_RECENT_EVENTS",
    "durable_hosted_session_factory",
    "get_durable_hosted_session_service",
    "reset_durable_hosted_session_service",
]
