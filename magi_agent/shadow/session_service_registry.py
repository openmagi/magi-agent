"""Process-scope session-service registry for hosted turn reuse (08-PR5).

Hosted chat turns historically built a fresh ADK ``InMemorySessionService``
per request, so multiturn continuity arrived only as re-serialized sanitized
history on every turn. Behind the default-OFF ``MAGI_HOSTED_SESSION_REUSE``
flag, the live runner boundary instead acquires its session service from this
registry so turns with the same session key share ADK session state and the
re-sent history degrades to a seed-on-miss.

Isolation model (multitenant-first)
-----------------------------------
* The registry key is the full ``(bot_id_digest, session_id)`` tuple. Two
  distinct bots — or two distinct sessions of one bot — always map to distinct
  session-service objects; there is no shared fallback bucket, and empty key
  parts are rejected with ``ValueError`` instead of collapsing into one.
* Eviction (LRU cap, idle TTL, explicit :meth:`SessionServiceRegistry.evict`)
  drops the stored object reference entirely. A later get for the same key
  always builds a fresh service via the caller's factory, so evicted/expired
  conversation state can never resurrect.

Bounded memory
--------------
* ``max_entries`` LRU cap — inserting past the cap evicts the least recently
  used entry synchronously (never blocks, never raises).
* Idle TTL — entries idle longer than ``ttl_seconds`` are dropped on the next
  registry access. The sweep walks the LRU front and stops at the first live
  entry, which is sufficient because the TTL is access-based: access order
  equals idle order.

Concurrency assumption
----------------------
The hosted serving path awaits the live runner boundary on a single asyncio
event loop, so registry calls are naturally serialized in production. The
boundary's sync ``invoke()`` wrapper may, however, run from worker threads
(``asyncio.run`` per call), so all bookkeeping is guarded by a plain
``threading.Lock`` held only across dict operations and the (trivial,
constructor-only) factory call — never across an ``await``. That is correct
for both models and uncontended on the single-loop path.

Per-key single-flight (busy-fallback)
-------------------------------------
The per-bot concurrency cap is not per-session, so two overlapping turns can
arrive for the SAME key from different threads/loops. Handing both the same
``InMemorySessionService`` would mutate one ADK session concurrently. The
boundary therefore acquires through :meth:`SessionServiceRegistry.try_acquire`,
which marks the entry busy for the duration of the turn; an overlapping
same-key acquire gets a FRESH, never-registered fallback service
(``reused=False`` — it seeds history exactly like a miss, behavior-identical
to the flag-OFF path). Miss entries are provisional until the caller releases
them with ``seeded=True`` after the runner has definitely seeded the ADK
service; pre-seed failure releases discard the exact provisional entry so the
next same-key turn reseeds sanitized history. :meth:`SessionServiceRegistry.release`
is identity-checked and idempotent, so a stale lease (entry evicted or expired
mid-turn, or a busy-fallback service) can never unmark another in-flight
turn — which also keeps LRU/TTL eviction of a busy entry safe.

Scope: the registry is process-local by design — hosted workers are per-bot
pods, so cross-worker sharing and cross-restart persistence are explicitly out
of scope for v1 (08-hosted-path open decision #3 resolved to the simple
bounded in-process store over the SQLite-backed workspace session service).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from typing import TypeAlias


SessionServiceKey: TypeAlias = tuple[str, str]
"""Registry key: ``(bot_id_digest, session_id)`` — both parts non-empty."""

SessionServiceFactory: TypeAlias = Callable[[], object]

DEFAULT_MAX_ENTRIES = 64
DEFAULT_TTL_SECONDS = 1800.0


@dataclass
class _RegistryEntry:
    """Mutable per-key record: stored service + LRU/TTL + busy bookkeeping."""

    service: object
    last_used_at: float
    in_use: bool = False
    seeded: bool = True


class SessionServiceRegistry:
    """Bounded (LRU + idle-TTL) get-or-create store for session services."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("session registry max_entries must be >= 1")
        if ttl_seconds <= 0:
            raise ValueError("session registry ttl_seconds must be > 0")
        self._max_entries = int(max_entries)
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        # key -> _RegistryEntry; ordered least-recently-used first.
        self._entries: OrderedDict[SessionServiceKey, _RegistryEntry] = OrderedDict()

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def get_or_create(
        self,
        key: SessionServiceKey,
        factory: SessionServiceFactory,
    ) -> tuple[object, bool]:
        """Return ``(service, reused)`` for ``key``, building on miss.

        ``reused`` is True only when a live (non-expired) entry for the exact
        key existed. The factory runs under the registry lock so concurrent
        callers of the same key observe exactly one instance; factories must
        therefore stay trivial constructors.

        Note: this legacy accessor does not participate in busy-marking. Turn
        execution paths that may overlap on the same key must use
        :meth:`try_acquire` / :meth:`release` instead.
        """
        validated_key = _validated_key(key)
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            entry = self._entries.get(validated_key)
            if entry is not None:
                entry.last_used_at = now
                self._entries.move_to_end(validated_key)
                return entry.service, True
            service = factory()
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
            self._entries[validated_key] = _RegistryEntry(
                service=service,
                last_used_at=now,
            )
            return service, False

    def try_acquire(
        self,
        key: SessionServiceKey,
        factory: SessionServiceFactory,
    ) -> tuple[object, bool]:
        """Single-flight ``get_or_create``: mark ``key`` busy for this turn.

        Exactly one in-flight turn holds a key at a time:

        * miss — build via ``factory``, register provisionally, mark busy,
          ``reused=False``;
        * hit (idle) — mark busy, ``reused=True``;
        * hit (busy) — **busy-fallback**: return a FRESH service built via
          ``factory`` that is never registered (``reused=False``). The
          overlapping turn seeds history exactly like a miss, and its later
          :meth:`release` is an identity-checked no-op.

        Callers must pair every ``try_acquire`` with a
        ``release(key, service, seeded=...)`` in a ``finally`` once the turn
        stops using the service. Misses stay provisional until released with
        ``seeded=True``; pre-seed failure paths release with ``seeded=False``
        and discard the exact provisional service so the next turn reseeds.
        """
        validated_key = _validated_key(key)
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            entry = self._entries.get(validated_key)
            if entry is not None:
                if not entry.seeded and not entry.in_use:
                    self._entries.pop(validated_key, None)
                    entry = None
            if entry is not None:
                entry.last_used_at = now
                self._entries.move_to_end(validated_key)
                if entry.in_use:
                    return factory(), False
                entry.in_use = True
                return entry.service, True
            service = factory()
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
            self._entries[validated_key] = _RegistryEntry(
                service=service,
                last_used_at=now,
                in_use=True,
                seeded=False,
            )
            return service, False

    def release(
        self,
        key: SessionServiceKey,
        service: object,
        *,
        seeded: bool = True,
    ) -> bool:
        """Clear the busy mark set by :meth:`try_acquire` for ``key``.

        Identity-checked and idempotent: only the exact ``service`` instance
        currently registered for ``key`` clears the mark, so a stale lease
        (entry evicted/expired mid-turn) or a busy-fallback service can never
        unmark a different in-flight turn. Safe to call from ``finally`` on
        every path; returns True only when a registered busy lease matched.

        Miss-created entries are provisional. ``seeded=True`` promotes the
        entry to reusable; ``seeded=False`` discards an unseeded provisional
        entry so the next same-key turn behaves like a miss and reseeds
        sanitized history.
        """
        validated_key = _validated_key(key)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(validated_key)
            if entry is None or entry.service is not service or not entry.in_use:
                return False
            if not seeded and not entry.seeded:
                self._entries.pop(validated_key, None)
                return True
            entry.seeded = True
            entry.in_use = False
            entry.last_used_at = now
            self._entries.move_to_end(validated_key)
            return True

    def evict(self, key: SessionServiceKey) -> bool:
        """Drop ``key`` from the registry; True when an entry was removed."""
        validated_key = _validated_key(key)
        with self._lock:
            return self._entries.pop(validated_key, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _purge_expired_locked(self, now: float) -> None:
        """Drop expired entries from the LRU front (caller holds the lock)."""
        while self._entries:
            oldest_key = next(iter(self._entries))
            last_used = self._entries[oldest_key].last_used_at
            if now - last_used <= self._ttl_seconds:
                break
            self._entries.popitem(last=False)


def _validated_key(key: SessionServiceKey) -> SessionServiceKey:
    bot_id_digest, session_id = key
    if not isinstance(bot_id_digest, str) or not bot_id_digest.strip():
        raise ValueError("session registry key requires a non-empty bot id digest")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session registry key requires a non-empty session id")
    return (bot_id_digest, session_id)


_default_registry: SessionServiceRegistry | None = None
_default_registry_lock = threading.Lock()


def default_session_service_registry() -> SessionServiceRegistry:
    """Process-default registry, built lazily from env-tunable caps.

    Caps are read once at first use (``MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES``
    / ``MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS``); use
    :func:`reset_default_session_service_registry` to rebuild after changing
    them (tests / explicit lifecycle only).
    """
    global _default_registry
    with _default_registry_lock:
        if _default_registry is None:
            # Imported lazily: config <-> shadow is a function-level dependency
            # by convention in this package (avoids import cycles).
            from magi_agent.config.env import (
                hosted_session_reuse_max_entries,
                hosted_session_reuse_ttl_seconds,
            )

            _default_registry = SessionServiceRegistry(
                max_entries=hosted_session_reuse_max_entries(),
                ttl_seconds=hosted_session_reuse_ttl_seconds(),
            )
        return _default_registry


def reset_default_session_service_registry() -> None:
    """Drop the process-default registry so the next use rebuilds it."""
    global _default_registry
    with _default_registry_lock:
        _default_registry = None


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "SessionServiceFactory",
    "SessionServiceKey",
    "SessionServiceRegistry",
    "default_session_service_registry",
    "reset_default_session_service_registry",
]
