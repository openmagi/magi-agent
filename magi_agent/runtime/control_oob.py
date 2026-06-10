"""Out-of-band (OOB) approval resolve for the durable control queue (doc 09 PR-5 / A7).

PR-4 gave the control queue an append-only JSONL backend
(:class:`magi_agent.runtime.durable_control_store.DurableControlRequestStore`)
so a pending approval survives a process restart. This module adds the *resolve*
seam on top of that backend: a pending approval written by the in-turn CLI gate
in one process can be approved / denied by a **separate** process — a human
approving later via a channel, the gateway daemon, or the dashboard — without
that resolving process being the one that opened the request.

The mechanism is deliberately thin and stateless: each call opens a *fresh*
durable store over the same JSONL path (replaying the log to recover the pending
queue), performs the resolve (which appends a terminal snapshot to the log), and
returns. The originating process picks the decision up on its next
:meth:`DurableControlRequestStore.refresh_from_log`.

Concurrency: a multi-process gateway can have several OOB writers (and the
in-turn gate) appending to the same log at once. The base store flagged in its
module docstring that multi-process writers need an external lock. We add a
minimal advisory file lock (``fcntl`` where available, with a portable best-effort
fallback) held for the read-resolve-append critical section so concurrent OOB
writes serialize and never tear / interleave JSONL lines.

This seam is dormant by default. Exposing it to external callers is gated by
``MAGI_CONTROL_STORE_OOB_RESOLVE`` (see
:func:`magi_agent.config.env.control_store_oob_resolve_enabled`); the functions
here are pure backend helpers that the gated transport route (or a CLI/gateway
caller) invokes.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

from magi_agent.runtime.control import (
    ControlRequestDecision,
    ControlRequestRecord,
    ControlRequestStoreResult,
)
from magi_agent.runtime.durable_control_store import DurableControlRequestStore

try:  # POSIX advisory locking — available on the deploy target.
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover — Windows / restricted runtimes
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False


def resolve_pending(
    path: str | os.PathLike[str],
    request_id: str,
    *,
    decision: ControlRequestDecision,
    now: int | float,
    feedback: str | None = None,
    updated_input: object | None = None,
    answer: str | None = None,
) -> ControlRequestStoreResult:
    """Resolve a pending durable approval from outside the originating process.

    Opens a fresh :class:`DurableControlRequestStore` over ``path``, replays the
    log to recover the pending queue, then resolves ``request_id`` with
    ``decision`` (appending a terminal snapshot to the log). The whole
    read-resolve-append runs under a file lock so concurrent OOB writers
    serialize.

    Raises :class:`KeyError` if the request id is unknown (no silent allow) and
    :class:`ValueError` if a terminal request is re-resolved to a different
    decision. A repeat of the *same* decision is idempotent (``duplicate=True``)
    — the resolve-once contract of the underlying store.
    """
    with _file_lock(path):
        store = DurableControlRequestStore(path=path)
        return store.resolve_request(
            request_id,
            decision=decision,
            now=now,
            feedback=feedback,
            updated_input=updated_input,
            answer=answer,
        )


def list_pending(
    path: str | os.PathLike[str],
    *,
    session_key: str | None = None,
) -> tuple[ControlRequestRecord, ...]:
    """List pending requests in the durable queue, optionally scoped by session.

    Read-only: opens a fresh store, replays the log, and returns the recovered
    pending records (those an external approver can still act on). When
    ``session_key`` is given, only requests for that session are returned so a
    channel / dashboard never sees another session's queue.
    """
    with _file_lock(path):
        store = DurableControlRequestStore(path=path)
        pending = store.pending_requests
    if session_key is None:
        return pending
    return tuple(record for record in pending if record.session_key == session_key)


@contextlib.contextmanager
def _file_lock(path: str | os.PathLike[str]) -> Iterator[None]:
    """Hold an advisory exclusive lock for the durable-log critical section.

    Uses a sidecar ``<path>.lock`` file so the lock survives the log being
    created lazily. Prefers POSIX ``fcntl`` advisory locking; falls back to a
    best-effort ``O_CREAT|O_EXCL`` spin where ``fcntl`` is unavailable. The lock
    is advisory between cooperating writers (all OOB writers + the durable store
    go through this seam), which is sufficient to keep appended JSONL lines from
    interleaving.
    """
    lock_path = Path(path).with_name(Path(path).name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if _HAVE_FCNTL:
        handle = open(lock_path, "a+")  # noqa: SIM115 — released in finally
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        return

    # Portable fallback: exclusive-create spin lock.
    deadline = time.monotonic() + 10.0
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() > deadline:  # pragma: no cover — contention guard
                raise TimeoutError("control OOB lock timed out") from None
            time.sleep(0.01)
            continue
        try:
            os.close(fd)
            yield
        finally:
            with contextlib.suppress(OSError):
                os.unlink(str(lock_path))
        return
