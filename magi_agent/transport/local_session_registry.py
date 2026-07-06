"""Process-level ADK session-service reuse for LOCAL serve chat surfaces.

Local ``magi serve`` rebuilds the whole engine per ``/v1/chat/stream`` request,
and each rebuild historically constructed a fresh, in-memory
``WorkspaceSessionService``. Because ADK turn-to-turn history lives inside that
service instance (its private ``_sessions`` dict), a new instance per turn meant
turn N+1 never saw turn N. This module anchors ONE process-level registry of
per-session ``WorkspaceSessionService`` instances so consecutive turns on the
same channel reuse the same service, and therefore the same ADK ``Session`` with
its accumulated events.

This is the local analogue of the hosted reuse already provided by
``magi_agent.shadow.session_service_registry.default_session_service_registry``
plus ``magi_agent.shadow.hosted_session_substrate``. We reuse the generic
``SessionServiceRegistry`` class (which has no hosted coupling) behind a SEPARATE
module-level singleton so local and hosted entries never share a store, a cap, or
a key domain. The substrate stays the in-memory ``WorkspaceSessionService`` with
``store=None``, so nothing is written to disk in any memory mode (incognito turn
to turn continuity is RAM-only, bounded by idle TTL, LRU pressure, and process
lifetime).

See docs/plans/2026-07-06-local-serve-session-continuity-fix-design.md.
"""

from __future__ import annotations

import threading

from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.shadow.session_service_registry import SessionServiceRegistry

# Generous local defaults (a single operator's dashboard channels). Constants,
# not env knobs, in v1: no new typed flags, no docs/env-reference.md churn.
LOCAL_SESSION_MAX_ENTRIES = 256
LOCAL_SESSION_TTL_SECONDS = 86400.0  # 24h idle

_registry: SessionServiceRegistry | None = None
_registry_lock = threading.Lock()


def local_session_service_registry() -> SessionServiceRegistry:
    """Return the lazily-built process-level local session-service registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = SessionServiceRegistry(
                    max_entries=LOCAL_SESSION_MAX_ENTRIES,
                    ttl_seconds=LOCAL_SESSION_TTL_SECONDS,
                )
    return _registry


def acquire_local_session_service(*, app_name: str, session_id: str) -> object:
    """Return the reused ``WorkspaceSessionService`` for ``(app_name, session_id)``.

    Builds a fresh in-memory service on the first turn of a channel and returns
    the same instance for every subsequent turn (until idle TTL / LRU eviction),
    so ADK session events accumulate across turns. Distinct channels (distinct
    ``session_id``) get distinct services, so history never leaks across
    channels. The reset-aware dashboard session key means a channel reset ("new
    chat") is a new key and therefore starts empty.
    """
    service, _reused = local_session_service_registry().get_or_create(
        (app_name, session_id),
        lambda: WorkspaceSessionService(app_name=app_name),
    )
    return service


def reset_local_session_service_registry() -> None:
    """Drop the process-level registry. Tests only (cross-test isolation)."""
    global _registry
    with _registry_lock:
        _registry = None
