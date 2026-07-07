"""Consolidated session-ownership primitives for governed hosted turns (WS-A).

Continuity (who owns the ADK session service across turns, and who decides
whether to seed sanitized history into a turn's prompt) was historically fixed
twice: once inside the legacy live runner boundary and once at the local serve
route. This module is the single home for the hosted half of that decision so
the next continuity bug is fixed in exactly one place.

The four primitives here were moved verbatim (semantics unchanged) out of
``magi_agent/shadow/gate5b4c3_live_runner_boundary.py``:

* :func:`probe_session_event_count` (read-only, fully fail-open ``None``),
* :func:`resolve_include_history` (seed-on-empty verdict),
* :func:`seeded_history_message_count` (observability count),
* :func:`acquire_hosted_session_lease` plus :class:`HostedSessionLease` (the
  single-flight lease chokepoint over the process-scope registry).

Ownership contract: a shared or durable substrate (the hosted durable SQLite
singleton) requires the ``try_acquire`` single-flight lease so two overlapping
same-session turns never mutate one session concurrently; a per-key in-memory
substrate on a single-operator surface may instead use ``get_or_create``. The
hosted path uses the lease below; the local route keeps ``get_or_create`` by
design (single operator, per-key services).

Layering note: this is a ``runtime`` module that imports from ``shadow``
(``session_service_registry``, ``hosted_session_substrate``). Those modules
import only ``config.env`` lazily and never import ``runtime``, so no cycle is
introduced. The reuse flag and the durable factory are imported function-level
(the boundary's established convention) to keep import-time edges minimal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import inspect

from magi_agent.shadow.session_service_registry import (
    SessionServiceRegistry,
    default_session_service_registry,
)

__all__ = [
    "HostedSessionLease",
    "acquire_hosted_session_lease",
    "probe_session_event_count",
    "resolve_include_history",
    "seeded_history_message_count",
]


async def probe_session_event_count(
    session_service: object,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
) -> int | None:
    """Return the ADK session's event count, or ``None`` when undeterminable.

    Read-only probe used for continuity observability (PR-1) and the
    seed-on-empty safety net (PR-2). Fully fail-open: a service without an
    async ``get_session`` (e.g. a bare test fake) or any error returns
    ``None`` so callers fall back to today's registry-verdict behavior.
    """
    get_session = getattr(session_service, "get_session", None)
    if get_session is None:
        return None
    try:
        session = get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if inspect.isawaitable(session):
            session = await session
    except Exception:
        return None
    if session is None:
        return 0
    events = getattr(session, "events", None)
    if events is None:
        return None
    try:
        return len(events)
    except Exception:
        return None


def resolve_include_history(
    *,
    session_reused: bool,
    session_event_count: int | None,
) -> bool:
    """Decide whether to seed sanitized history into this turn's prompt.

    Emptiness probe (PR-2): seed whenever the durable ADK session holds zero
    events, regardless of the registry reuse verdict. This is the unconditional
    safety net: a reused-but-empty session (eviction / restart / hollow hit /
    busy-fallback) still seeds the client echo instead of going in blind, while
    a genuinely populated reused session is not double-seeded.

    When the event count is undeterminable (``None`` -> bare service or a probe
    error), fall back to today's registry verdict so flag-OFF and local paths
    stay byte-identical (a fresh service always probes 0 anyway -> seed).
    """
    if session_event_count is None:
        return not session_reused
    return session_event_count == 0


def seeded_history_message_count(runner_input: object) -> int:
    """Count the sanitized history messages that the prompt builder seeds.

    Mirrors the exact filter the boundary applies (valid user/assistant items
    with non-empty content) so the observability count matches what the model
    actually received. Fully defensive: any odd shape counts as zero.
    """
    history = getattr(runner_input, "sanitized_recent_history", ()) or ()
    count = 0
    for item in history:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            count += 1
    return count


@dataclass
class HostedSessionLease:
    """A single-flight lease on a hosted session service for one turn.

    ``service`` is the ADK session service the turn must run against, ``reused``
    reports whether an existing live session was reused (registry hit), and
    :meth:`release` clears the busy mark set by
    :meth:`SessionServiceRegistry.try_acquire`, promoting a provisional miss
    entry to reusable when ``seeded`` is True or discarding it when False.
    """

    service: object
    reused: bool
    _release: Callable[[bool], None] = field(repr=False)

    def release(self, seeded: bool) -> None:
        """Release this turn's lease. ``seeded`` records whether the ADK session
        was actually seeded (a runner event was observed); an unseeded
        provisional miss entry is discarded so the next same-key turn reseeds."""
        self._release(seeded)


def acquire_hosted_session_lease(
    *,
    bot_id_digest: str,
    session_id: str,
    session_key_digest: str,
    in_memory_factory: Callable[[], object],
    registry: SessionServiceRegistry | None = None,
) -> HostedSessionLease | None:
    """Acquire (or, behind the reuse flag, build fresh) the turn's session service.

    Returns ``None`` to signal "no registry participation": the caller must
    build a fresh in-memory service itself and perform no release. ``None`` is
    returned on the two historical bypass paths, byte-identical to the legacy
    boundary:

    * reuse flag OFF (default) -> historical fresh-instance-per-turn behavior,
      no registry interaction at all;
    * empty ``session_key_digest`` -> the session id would fall back to the
      per-request-unique request digest, so a registry entry could never be
      reused; registering one would only churn the LRU and evict this bot's
      live sessions.

    When a lease IS returned it comes from the process-scope registry keyed by
    the full ``(bot_id_digest, session_id)`` pair via
    :meth:`SessionServiceRegistry.try_acquire`, which marks the key busy for the
    turn's duration; an overlapping same-key turn gets a fresh, unregistered
    fallback service (single-flight). With the durable SQLite substrate active
    the miss factory returns a process-singleton durable service and the
    busy-fallback uses a DISTINCT fresh in-memory service so two turns never
    mutate one durable session concurrently.
    """
    # Lazy import: runtime -> config is a function-level dependency by
    # convention in this package (avoids import cycles).
    from magi_agent.config.env import is_hosted_session_reuse_enabled

    if not is_hosted_session_reuse_enabled():
        return None
    if not session_key_digest:
        return None
    if registry is None:
        registry = default_session_service_registry()
    session_key = (bot_id_digest, session_id)
    # Durable substrate (PR-3): when MAGI_HOSTED_SESSION_DB is ON and the
    # SqliteSessionService is constructible, the lease registry fronts a
    # process-singleton durable service so sessions/events survive restart,
    # image bump, LRU eviction and TTL. A busy-overlap gets a DISTINCT fresh
    # in-memory fallback so two turns never mutate one durable session
    # concurrently. Fail-open: None keeps today's in-memory behavior.
    from magi_agent.shadow.hosted_session_substrate import (
        durable_hosted_session_factory,
    )

    durable_factory = durable_hosted_session_factory()
    if durable_factory is not None:
        session_service, session_reused = registry.try_acquire(
            session_key,
            durable_factory,
            fallback_factory=in_memory_factory,
        )
    else:
        session_service, session_reused = registry.try_acquire(
            session_key,
            in_memory_factory,
        )

    bound_registry = registry

    def _release(seeded: bool) -> None:
        bound_registry.release(session_key, session_service, seeded=seeded)

    return HostedSessionLease(
        service=session_service,
        reused=session_reused,
        _release=_release,
    )
