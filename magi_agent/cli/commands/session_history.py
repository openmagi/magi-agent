"""Session-history slash-command seams for the Magi CLI (Stream D, PR5).

These five commands (``/fork``, ``/undo``, ``/redo``, ``/share``,
``/unshare``) are **protocol seams — default-off**. They exist and route
through clean typed Protocols, but are HIDDEN from ``list_for`` until a real
controller is wired onto ``ctx.runtime``. This matches magi's "scaffold +
gate, activate later" paradigm.

Why deferred
------------
- ``/fork`` (new session from a message): the real substrate is the
  ``cli/session_log.py`` DAG (``append`` supports ``parent_uuid`` forking),
  but no session manager wires it today.  ``runtime/fork_runner.py`` is a
  prompt-cache parallel-child primitive — NOT session branching.
- ``/undo``, ``/redo``: require a per-message revert/checkpoint subsystem that
  does not exist (no message-level snapshot in ``runtime/``).
- ``/share``, ``/unshare``: session sharing produces a public URL, which is a
  **hosted (Clawy Pro)** concern, outside the OSS runtime.  The OSS layer ships
  only the Protocol seam and a hidden command; the hosted layer wires the real
  provider.

No live fork/revert/share logic lives here — the Protocols define the contract
that a future controller must satisfy; the commands call through them when wired
and return ``Skip()`` when not.

Design mirrors ``control.py``
------------------------------
- Each runtime controller is an OPTIONAL attribute on ``ctx.runtime``:
  ``session_forker``, ``session_revert``, ``session_share``. When
  ``ctx.runtime`` is ``None`` or lacks the attribute (or the attribute does not
  satisfy the Protocol), the lookup helper returns ``None`` and the command
  returns ``Skip()``.
- ``isinstance``-checks against ``runtime_checkable`` Protocols guard the
  lookup so an object lacking the expected methods is rejected.
- ``is_enabled`` predicates have nuanced (dynamic) conditions for
  ``/undo``, ``/redo``, and ``/unshare``:
    - ``/undo`` is visible only when ``can_undo()`` is True.
    - ``/redo`` is visible only when ``can_redo()`` is True.
    - ``/unshare`` is visible only when ``shared_url()`` is not None (session
      is currently shared).
  This mirrors opencode's enable/disable semantics (commands are context-
  sensitive, not merely controller-presence-gated).
- All output is concise and redaction-safe: only refs/URLs returned by the
  controllers are surfaced, never internal paths or secrets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    LocalCommand,
    LocalResult,
    Skip,
    Text,
)

__all__ = [
    # Protocols
    "SessionForker",
    "SessionRevert",
    "SessionShareProvider",
    # Lookup helpers
    "_session_forker",
    "_session_revert",
    "_session_share",
    # Commands
    "ForkCommand",
    "UndoCommand",
    "RedoCommand",
    "ShareCommand",
    "UnshareCommand",
    # Registration helpers
    "session_history_commands",
    "register_session_history_commands",
]

# Both surfaces: these commands work in TUI AND headless (LocalCommand, no model
# round-trip, no interactive widget needed).
_HISTORY_BOTH = CommandSurface(tui=True, headless=True)


# ---------------------------------------------------------------------------
# Runtime-controller Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionForker(Protocol):
    """Controller Protocol for branching a new session from a message node.

    The real substrate is the ``cli/session_log.py`` DAG — ``append`` already
    supports ``parent_uuid`` forking, but no session manager wires it yet.
    A future session manager wires a concrete implementation onto
    ``ctx.runtime.session_forker``; until then the command returns ``Skip()``.
    """

    def fork_from(self, message_ref: str | None) -> str:
        """Fork a new session from ``message_ref`` (or the current head if None).

        Returns the new session ref.
        """
        ...


@runtime_checkable
class SessionRevert(Protocol):
    """Controller Protocol for per-message undo/redo.

    A future revert/checkpoint subsystem wires a concrete implementation onto
    ``ctx.runtime.session_revert``; until then the commands return ``Skip()``.
    """

    def can_undo(self) -> bool:
        """Return True if there is a step available to undo."""
        ...

    def undo(self) -> bool:
        """Undo one step; return True on success."""
        ...

    def can_redo(self) -> bool:
        """Return True if there is a step available to redo."""
        ...

    def redo(self) -> bool:
        """Redo one step; return True on success."""
        ...


@runtime_checkable
class SessionShareProvider(Protocol):
    """Controller Protocol for hosted session sharing.

    The hosted (Clawy Pro) layer wires a concrete implementation onto
    ``ctx.runtime.session_share``; the OSS runtime ships no implementation
    (``/share`` and ``/unshare`` are permanently hidden without it).
    """

    def shared_url(self) -> str | None:
        """Return the current share URL, or None if the session is not shared."""
        ...

    def share(self) -> str:
        """Publish the session and return the share URL."""
        ...

    def unshare(self) -> None:
        """Revoke the shared session URL."""
        ...


# ---------------------------------------------------------------------------
# Lookup helpers (default-off)
# ---------------------------------------------------------------------------


def _session_forker(ctx: CommandContext) -> SessionForker | None:
    """Return the ``SessionForker`` on ``ctx.runtime``, or ``None``.

    Returns ``None`` when ``ctx.runtime`` is ``None``, lacks the attribute, or
    the attribute does not satisfy the ``SessionForker`` Protocol.
    """
    if ctx.runtime is None:
        return None
    forker = getattr(ctx.runtime, "session_forker", None)
    if forker is None or not isinstance(forker, SessionForker):
        return None
    return forker


def _session_revert(ctx: CommandContext) -> SessionRevert | None:
    """Return the ``SessionRevert`` on ``ctx.runtime``, or ``None``."""
    if ctx.runtime is None:
        return None
    revert = getattr(ctx.runtime, "session_revert", None)
    if revert is None or not isinstance(revert, SessionRevert):
        return None
    return revert


def _session_share(ctx: CommandContext) -> SessionShareProvider | None:
    """Return the ``SessionShareProvider`` on ``ctx.runtime``, or ``None``."""
    if ctx.runtime is None:
        return None
    share = getattr(ctx.runtime, "session_share", None)
    if share is None or not isinstance(share, SessionShareProvider):
        return None
    return share


# ---------------------------------------------------------------------------
# LocalCommand implementations
# ---------------------------------------------------------------------------


@dataclass
class ForkCommand(LocalCommand):
    """``/fork [message-ref]`` — branch a new session from the given message.

    Optional ``message-ref`` arg → fork from that message node.
    No arg → fork from the current session head.
    Returns the new session ref from ``fork_from()``.
    No ``SessionForker`` wired → ``Skip()`` (command is hidden from
    ``list_for`` anyway when no forker is present).
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        forker = _session_forker(ctx)
        if forker is None:
            return Skip()
        arg = str(args).strip() if args else ""
        ref = forker.fork_from(arg or None)
        return Text(text=f"forked session: {ref}")


@dataclass
class UndoCommand(LocalCommand):
    """``/undo`` — undo the last step via the runtime revert controller.

    No ``SessionRevert`` wired → ``Skip()``.
    Controller present but ``can_undo()`` is False → command is hidden from
    ``list_for``; calling it directly still returns a ``Text`` ack (the
    controller decides what to report for a no-op undo).
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        _ = args
        revert = _session_revert(ctx)
        if revert is None:
            return Skip()
        result = revert.undo()
        return Text(text=f"undo: {'ok' if result else 'nothing to undo'}")


@dataclass
class RedoCommand(LocalCommand):
    """``/redo`` — redo the last undone step via the runtime revert controller.

    No ``SessionRevert`` wired → ``Skip()``.
    Controller present but ``can_redo()`` is False → command is hidden from
    ``list_for``; calling it directly still returns a ``Text`` ack.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        _ = args
        revert = _session_revert(ctx)
        if revert is None:
            return Skip()
        result = revert.redo()
        return Text(text=f"redo: {'ok' if result else 'nothing to redo'}")


@dataclass
class ShareCommand(LocalCommand):
    """``/share`` — publish the session and return the share URL.

    Returns ``Text("session shared: <url>")`` where ``<url>`` is the
    provider-returned share link (a user-facing URL, not a secret).
    No ``SessionShareProvider`` wired → ``Skip()``.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        _ = args
        provider = _session_share(ctx)
        if provider is None:
            return Skip()
        url = provider.share()
        return Text(text=f"session shared: {url}")


@dataclass
class UnshareCommand(LocalCommand):
    """``/unshare`` — revoke the current session share URL.

    Only visible from ``list_for`` when the session is currently shared
    (``shared_url()`` is not None). Returns ``Text("session unshared")``.
    No ``SessionShareProvider`` wired → ``Skip()``.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        _ = args
        provider = _session_share(ctx)
        if provider is None:
            return Skip()
        provider.unshare()
        return Text(text="session unshared")


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------


def _session_history_specs() -> (
    list[tuple[LocalCommand, Callable[[CommandContext], bool]]]
):
    """Single source of truth: each session-history command + its ``is_enabled`` predicate.

    Returns FRESH command instances on every call (no shared mutable state).
    The five predicates are written as distinct tuple-literal lambdas, each
    closing over its OWN module-level helper — NOT built in a loop over a
    shared variable, so there is no late-binding closure-capture bug.

    Predicate nuances (matching opencode's enable/disable semantics):
    - ``/fork``: visible iff a ``SessionForker`` is present.
    - ``/undo``: visible iff a ``SessionRevert`` is present AND ``can_undo()``
      is True (context-sensitive: only shown when there is something to undo).
    - ``/redo``: visible iff a ``SessionRevert`` is present AND ``can_redo()``
      is True.
    - ``/share``: visible iff a ``SessionShareProvider`` is present.
    - ``/unshare``: visible iff a ``SessionShareProvider`` is present AND
      ``shared_url()`` is not None (only shown when the session is shared).
    """

    return [
        (
            ForkCommand(name="fork", surface=_HISTORY_BOTH),
            lambda ctx: _session_forker(ctx) is not None,
        ),
        (
            UndoCommand(name="undo", surface=_HISTORY_BOTH),
            lambda ctx: (
                (r := _session_revert(ctx)) is not None and r.can_undo()
            ),
        ),
        (
            RedoCommand(name="redo", surface=_HISTORY_BOTH),
            lambda ctx: (
                (r := _session_revert(ctx)) is not None and r.can_redo()
            ),
        ),
        (
            ShareCommand(name="share", surface=_HISTORY_BOTH),
            lambda ctx: _session_share(ctx) is not None,
        ),
        (
            UnshareCommand(name="unshare", surface=_HISTORY_BOTH),
            lambda ctx: (
                (p := _session_share(ctx)) is not None
                and p.shared_url() is not None
            ),
        ),
    ]


def session_history_commands() -> list[LocalCommand]:
    """Return fresh instances of all five session-history commands.

    A factory (not module-level singletons) so each registry gets its own
    instances with no shared mutable state. Derived from
    :func:`_session_history_specs` (the single definition site).
    """

    return [command for command, _ in _session_history_specs()]


def register_session_history_commands(registry: object) -> None:
    """Register the five session-history commands onto ``registry`` with gated predicates.

    Each command is registered with an ``is_enabled`` predicate that returns
    ``True`` only when the matching controller is present (and for ``/undo``,
    ``/redo``, ``/unshare`` — only when the dynamic condition is met). Result:

    - ``list_for`` EXCLUDES the command when not applicable (default-off,
      hidden from the user).
    - ``lookup`` INCLUDES the command regardless (dispatch stays total + safe:
      the command itself returns ``Skip()`` when called without a controller).

    ``registry`` is typed as ``object`` to avoid a hard import of
    ``CommandRegistryImpl`` (keeps this module import-cheap), but it must expose
    a ``register(command, *, is_enabled)`` method compatible with
    ``CommandRegistryImpl.register``.
    """

    for command, is_enabled in _session_history_specs():
        registry.register(command, is_enabled=is_enabled)
