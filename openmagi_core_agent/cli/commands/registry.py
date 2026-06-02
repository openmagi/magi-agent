"""Command registry + dispatcher for the Magi CLI (Stream D, PR-D1).

This module is the seam every CLI surface (TUI / headless) goes through to
discover and run slash-commands. It imports ONLY from
``openmagi_core_agent.cli.contracts`` (the frozen interface surface) plus the
standard library — no ``textual`` / ``google-adk`` / model deps — so importing
it is cheap and side-effect-free.

Three responsibilities live here:

1. ``CommandRegistryImpl`` — structurally satisfies the ``CommandRegistry``
   Protocol. Holds commands + an optional per-command availability predicate.
2. ``get_registry(cwd)`` — a per-cwd memoizing factory. The registry is built
   ONCE per working directory and cached; it is NEVER built at import time (so
   importing this module touches no event loop and runs no discovery).
3. ``dispatch(...)`` — routes a looked-up command to the right execution path
   (prompt / local / widget) and enforces the "widgets are interactive-only"
   structural rule for headless.

Design decisions (documented for D2/E/F):
- **First-wins on duplicate name.** Registering a second command with an
  already-known name is a no-op for ``lookup``; richer precedence handling is
  Stream D2's concern. First-wins keeps D1 deterministic and dependency-free.
- **Unknown command -> ``Skip()``.** ``dispatch`` never crashes the surface on
  an unknown name; it returns ``Skip()`` (and the caller may render a hint).
- **Headless widget -> ``PermissionError``.** Widgets are TUI-only by design;
  reaching one in headless is a structural error, surfaced as
  ``PermissionError`` so it is impossible to silently no-op an interactive flow.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass

from openmagi_core_agent.cli.contracts import (
    Command,
    CommandContext,
    CommandRegistry,
    CommandSurface,
    ContentBlock,
    LocalCommand,
    LocalResult,
    PromptCommand,
    Skip,
    WidgetCommand,
)

__all__ = [
    "CommandRegistryImpl",
    "get_registry",
    "set_registry_builder",
    "dispatch",
]

# Predicate deciding whether a command is currently available given context.
IsEnabled = Callable[[CommandContext], bool]


def _always_true(_ctx: CommandContext) -> bool:
    return True


@dataclass
class _Entry:
    """A registered command plus its live availability predicate."""

    command: Command
    is_enabled: IsEnabled = _always_true


def _surface_permits(requested: CommandSurface, command: CommandSurface) -> bool:
    """Mask rule: command ``command`` is visible for ``requested`` surface when
    the requested mode the caller asks for is one the command supports.

    Callers pass single-mode masks (TUI = ``(tui=True, headless=False)`` or
    headless = ``(tui=False, headless=True)``); the OR below also handles a
    ``(tui=True, headless=True)`` "both" mask correctly.
    """

    return (requested.tui and command.tui) or (requested.headless and command.headless)


class CommandRegistryImpl(CommandRegistry):
    """Concrete ``CommandRegistry``.

    Structurally satisfies the ``CommandRegistry`` Protocol (``lookup`` +
    ``list_for``). The mask filter AND the availability predicate are
    re-evaluated on EVERY ``list_for`` call — we never cache the filtered list —
    so a login / state change between two calls changes the visible command set
    live.
    """

    def __init__(self) -> None:
        # Insertion-ordered (dict preserves order); first-wins on duplicates.
        self._entries: dict[str, _Entry] = {}

    def register(
        self,
        command: Command,
        *,
        is_enabled: IsEnabled | None = None,
    ) -> None:
        """Register ``command`` with an optional availability predicate.

        First-wins: if a command with the same name is already registered, this
        call is a no-op (D2 owns richer precedence).
        """

        if command.name in self._entries:
            return
        self._entries[command.name] = _Entry(
            command=command,
            is_enabled=is_enabled or _always_true,
        )

    def lookup(self, name: str) -> Command | None:
        entry = self._entries.get(name)
        return entry.command if entry is not None else None

    def list_for(
        self,
        surface: CommandSurface,
        ctx: CommandContext | None = None,
    ) -> list[Command]:
        """Return commands visible for ``surface`` whose predicate is True NOW.

        The extra optional ``ctx`` keeps this structurally compatible with the
        ``CommandRegistry`` Protocol (which only mandates ``list_for(surface)``);
        when ``ctx is None`` we build a throwaway ``CommandContext`` so the
        predicate still runs. Re-evaluated every call — no caching.
        """

        eval_ctx = ctx if ctx is not None else CommandContext(cwd=os.getcwd())
        out: list[Command] = []
        for entry in self._entries.values():
            if not _surface_permits(surface, entry.command.surface):
                continue
            if not entry.is_enabled(eval_ctx):
                continue
            out.append(entry.command)
        return out


# ---------------------------------------------------------------------------
# Per-cwd memoized construction
# ---------------------------------------------------------------------------
# The builder seam: D2 swaps this for real command discovery. Default builds an
# empty registry. Kept overridable so D2/tests inject commands without import
# side effects.
RegistryBuilder = Callable[[str], CommandRegistryImpl]


def _default_builder(_cwd: str) -> CommandRegistryImpl:
    return CommandRegistryImpl()


# Module-level mutable state, populated lazily by get_registry(). Building at
# import time is forbidden (it could touch an event loop / run discovery); we
# only ever construct inside get_registry().
_REGISTRY_CACHE: dict[str, CommandRegistryImpl] = {}
_BUILDER: RegistryBuilder = _default_builder


def set_registry_builder(builder: RegistryBuilder) -> None:
    """Override the per-cwd builder (D2 wires real discovery; tests inject).

    Clears the cache so subsequent ``get_registry`` calls use the new builder.
    """

    global _BUILDER
    _BUILDER = builder
    _REGISTRY_CACHE.clear()


def get_registry(cwd: str) -> CommandRegistryImpl:
    """Return the registry for ``cwd``, building it once and caching per cwd.

    Per-cwd memoization rationale: command availability (which commands exist,
    project-local commands, etc.) is a function of the working directory, so we
    key the cache on cwd. The same cwd always yields the same instance; a
    different cwd yields a distinct one. Construction is lazy (never at import).
    """

    cached = _REGISTRY_CACHE.get(cwd)
    if cached is not None:
        return cached
    built = _BUILDER(cwd)
    _REGISTRY_CACHE[cwd] = built
    return built


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
async def dispatch(
    registry: CommandRegistry,
    name: str,
    args: object,
    ctx: CommandContext,
    *,
    surface: CommandSurface,
) -> list[ContentBlock] | LocalResult | object:
    """Route ``name`` to its command and execute it for ``surface``.

    Returns:
        - ``list[ContentBlock]`` for a ``PromptCommand`` (caller expands into a
          turn),
        - a ``LocalResult`` (``Text|Compact|Skip``) for a ``LocalCommand``,
        - the widget's structured ``on_done`` result for a ``WidgetCommand``
          (TUI only),
        - ``Skip()`` if ``name`` is unknown (never crashes the surface).

    Raises:
        ``PermissionError`` if a ``WidgetCommand`` is dispatched in headless
        mode (``surface.headless and not surface.tui``) — widgets are
        interactive-only and must be structurally unreachable in headless.
    """

    command = registry.lookup(name)
    if command is None:
        # Unknown command: don't crash; the caller may surface a hint.
        return Skip()

    if isinstance(command, PromptCommand):
        return await command.build_prompt(args, ctx)

    if isinstance(command, LocalCommand):
        return await command.call(args, ctx)

    if isinstance(command, WidgetCommand):
        return await _dispatch_widget(command, args, ctx, surface=surface)

    # Unreachable given the Command union, but keep dispatch total.
    return Skip()


async def _dispatch_widget(
    command: WidgetCommand,
    args: object,
    ctx: CommandContext,
    *,
    surface: CommandSurface,
) -> object:
    """Validate + invoke a widget command with a deadlock-defensive on_done.

    Headless rejection is structural: widgets are interactive-only.

    Full mounting is Stream E. Here we only wire a minimal, deadlock-defensive
    ``on_done`` over an ``asyncio.Future``: a ``done_was_called`` guard ensures
    the FIRST call resolves the Future and any subsequent calls are ignored
    (double-resolution would raise ``InvalidStateError``). The guard also means
    a widget that never calls ``on_done`` cannot leave a Future awaited
    forever in this lean path — we return whatever the (immediate) coroutine
    produced rather than blocking on the Future indefinitely.
    """

    # Headless mode = headless True and tui False. Reject structurally.
    if surface.headless and not surface.tui:
        raise PermissionError(
            f"widget command '{command.name}' is interactive-only "
            "and cannot run in headless mode"
        )

    loop = asyncio.get_running_loop()
    done_future: asyncio.Future[object] = loop.create_future()
    # Mutable single-cell guard so the nested callback can flip it.
    state = {"done_was_called": False}

    def on_done(
        result: object,
        *,
        display: object = None,
        should_query: bool = False,
        meta_messages: list | None = None,
        next_input: str | None = None,
        submit_next_input: bool = False,
    ) -> None:
        # Stream E consumes display/should_query/meta_messages/next_input/
        # submit_next_input for real mounting. D1 only needs single-resolution.
        _ = (display, should_query, meta_messages, next_input, submit_next_input)
        if state["done_was_called"]:
            # Guard against double-resolution (a buggy widget calling twice).
            return
        state["done_was_called"] = True
        if not done_future.done():
            done_future.set_result(result)

    # Invoke the widget. Stream E will mount a real view; here we drive the
    # callback path so the on_done guard is exercised end-to-end.
    await command.call(on_done, ctx, args)

    if done_future.done():
        return done_future.result()
    # Widget returned without resolving on_done (e.g. a deferred/mounted view).
    # We don't block forever — return Skip() so the surface stays responsive.
    # Stream E's real mount awaits the Future under a managed lifecycle.
    return Skip()
