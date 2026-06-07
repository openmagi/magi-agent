"""Runtime-control slash-command seams for the Magi CLI (Stream D, PR4).

These four commands (``/model``, ``/agent``, ``/mcp``, ``/new``) are
**protocol seams — default-off**. They exist and route through clean typed
Protocols, but are HIDDEN from ``list_for`` until a real controller is wired
onto ``ctx.runtime``. This matches magi's "scaffold + gate, activate later"
paradigm: the commands are always findable via ``lookup`` (dispatch stays
total + safe), but only VISIBLE once the corresponding runtime attribute is
present.

No live switching logic lives here — the Protocols define the contract that
a future controller must satisfy; the commands call through them when wired
and return ``Skip()`` when not.

Design
------
- Each runtime controller is an OPTIONAL attribute on ``ctx.runtime``:
  ``model_selector``, ``agent_selector``, ``mcp_controller``,
  ``session_lifecycle``. When ``ctx.runtime`` is ``None`` or lacks the
  attribute, the helper returns ``None`` and the command returns ``Skip()``.
- ``isinstance``-checks against ``runtime_checkable`` Protocols guard the
  lookup so an object lacking the expected methods is rejected (note:
  ``runtime_checkable`` checks method *presence* only, not signatures).
- ``is_enabled`` predicates passed to ``register_control_commands`` mirror
  the lookup helpers, so visibility is live and re-evaluated every
  ``list_for`` call.
- All output is concise and redaction-safe: only ids/names are surfaced, never
  paths or secrets (the protocols are designed to return only those).
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
    "ModelSelector",
    "AgentSelector",
    "McpController",
    "SessionLifecycle",
    # Lookup helpers
    "_model_selector",
    "_agent_selector",
    "_mcp_controller",
    "_session_lifecycle",
    # Commands
    "ModelCommand",
    "AgentCommand",
    "McpCommand",
    "NewSessionCommand",
    # Registration helpers
    "control_commands",
    "register_control_commands",
]

# Both surfaces: these commands work in TUI AND headless (LocalCommand, no model
# round-trip, no interactive widget needed).
_CONTROL_BOTH = CommandSurface(tui=True, headless=True)


# ---------------------------------------------------------------------------
# Runtime-controller Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelSelector(Protocol):
    """Controller Protocol for live model switching.

    A future runner/engine wires a concrete implementation onto
    ``ctx.runtime.model_selector``; until then the attribute is absent and all
    model commands return ``Skip()``.
    """

    def list_models(self) -> list[str]:
        """Return the ids of all selectable models."""
        ...

    def current_model(self) -> str | None:
        """Return the currently active model id, or ``None`` if unknown."""
        ...

    def select_model(self, model_id: str) -> None:
        """Switch the active model to ``model_id``."""
        ...


@runtime_checkable
class AgentSelector(Protocol):
    """Controller Protocol for live agent switching.

    A future agent orchestrator wires a concrete implementation onto
    ``ctx.runtime.agent_selector``.
    """

    def list_agents(self) -> list[str]:
        """Return the ids of all selectable agents."""
        ...

    def current_agent(self) -> str | None:
        """Return the currently active agent id, or ``None`` if unknown."""
        ...

    def select_agent(self, agent_id: str) -> None:
        """Switch the active agent to ``agent_id``."""
        ...


@runtime_checkable
class McpController(Protocol):
    """Controller Protocol for MCP server toggling.

    A future MCP manager wires a concrete implementation onto
    ``ctx.runtime.mcp_controller``.
    """

    def list_servers(self) -> list[tuple[str, bool]]:
        """Return ``[(name, enabled), ...]`` for all known MCP servers."""
        ...

    def toggle_server(self, name: str) -> bool:
        """Toggle the named server's enabled state; return the new state."""
        ...


@runtime_checkable
class SessionLifecycle(Protocol):
    """Controller Protocol for creating new sessions.

    A future session manager wires a concrete implementation onto
    ``ctx.runtime.session_lifecycle``.
    """

    def new_session(self) -> str:
        """Create a new session and return its id/ref."""
        ...


# ---------------------------------------------------------------------------
# Lookup helpers (default-off)
# ---------------------------------------------------------------------------


def _model_selector(ctx: CommandContext) -> ModelSelector | None:
    """Return the ``ModelSelector`` on ``ctx.runtime``, or ``None``.

    Returns ``None`` when ``ctx.runtime`` is ``None``, lacks the attribute, or
    the attribute does not satisfy the ``ModelSelector`` Protocol.
    """
    if ctx.runtime is None:
        return None
    sel = getattr(ctx.runtime, "model_selector", None)
    if sel is None or not isinstance(sel, ModelSelector):
        return None
    return sel


def _agent_selector(ctx: CommandContext) -> AgentSelector | None:
    """Return the ``AgentSelector`` on ``ctx.runtime``, or ``None``."""
    if ctx.runtime is None:
        return None
    sel = getattr(ctx.runtime, "agent_selector", None)
    if sel is None or not isinstance(sel, AgentSelector):
        return None
    return sel


def _mcp_controller(ctx: CommandContext) -> McpController | None:
    """Return the ``McpController`` on ``ctx.runtime``, or ``None``."""
    if ctx.runtime is None:
        return None
    ctrl = getattr(ctx.runtime, "mcp_controller", None)
    if ctrl is None or not isinstance(ctrl, McpController):
        return None
    return ctrl


def _session_lifecycle(ctx: CommandContext) -> SessionLifecycle | None:
    """Return the ``SessionLifecycle`` on ``ctx.runtime``, or ``None``."""
    if ctx.runtime is None:
        return None
    lc = getattr(ctx.runtime, "session_lifecycle", None)
    if lc is None or not isinstance(lc, SessionLifecycle):
        return None
    return lc


# ---------------------------------------------------------------------------
# LocalCommand implementations
# ---------------------------------------------------------------------------


@dataclass
class ModelCommand(LocalCommand):
    """``/model [model-id]`` — list or switch the active model.

    No arg → list ``current_model()`` and available ``list_models()``.
    With an arg → call ``select_model(arg)`` and confirm selection.
    No ``ModelSelector`` wired → ``Skip()`` (safe no-op; command is hidden
    from ``list_for`` anyway when no selector is present).
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        sel = _model_selector(ctx)
        if sel is None:
            return Skip()
        arg = str(args).strip() if args else ""
        if arg:
            sel.select_model(arg)
            return Text(text=f"model: selected {arg}")
        current = sel.current_model() or "(none)"
        available = ", ".join(sel.list_models()) or "(none)"
        return Text(text=f"model: current={current} available=[{available}]")


@dataclass
class AgentCommand(LocalCommand):
    """``/agent [agent-id]`` — list or switch the active agent.

    No arg → list ``current_agent()`` and available ``list_agents()``.
    With an arg → call ``select_agent(arg)`` and confirm selection.
    No ``AgentSelector`` wired → ``Skip()``.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        sel = _agent_selector(ctx)
        if sel is None:
            return Skip()
        arg = str(args).strip() if args else ""
        if arg:
            sel.select_agent(arg)
            return Text(text=f"agent: selected {arg}")
        current = sel.current_agent() or "(none)"
        available = ", ".join(sel.list_agents()) or "(none)"
        return Text(text=f"agent: current={current} available=[{available}]")


@dataclass
class McpCommand(LocalCommand):
    """``/mcp [server-name]`` — list or toggle MCP servers.

    No arg → list all servers from ``list_servers()`` with enabled state.
    With a server-name arg → toggle it via ``toggle_server(arg)`` and confirm.
    No ``McpController`` wired → ``Skip()``.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        ctrl = _mcp_controller(ctx)
        if ctrl is None:
            return Skip()
        arg = str(args).strip() if args else ""
        if arg:
            new_state = ctrl.toggle_server(arg)
            state_str = "enabled" if new_state else "disabled"
            return Text(text=f"mcp: {arg} is now {state_str}")
        servers = ctrl.list_servers()
        if not servers:
            return Text(text="mcp: no servers configured")
        parts = [f"{name}={'on' if enabled else 'off'}" for name, enabled in servers]
        return Text(text="mcp: " + " ".join(parts))


@dataclass
class NewSessionCommand(LocalCommand):
    """``/new`` — create a new session via the runtime lifecycle controller.

    Returns the new session ref from ``new_session()``. No ``SessionLifecycle``
    wired → ``Skip()``.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        _ = args
        lc = _session_lifecycle(ctx)
        if lc is None:
            return Skip()
        ref = lc.new_session()
        return Text(text=f"new session: {ref}")


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------


def _control_specs() -> list[tuple[LocalCommand, Callable[[CommandContext], bool]]]:
    """Single source of truth: each control command + its ``is_enabled`` predicate.

    Returns FRESH command instances on every call (no shared mutable state).
    The four predicates are written as distinct tuple-literal lambdas, each
    closing over its OWN module-level helper — they are NOT built in a loop over
    a shared variable, so there is no late-binding closure-capture bug. Both
    ``control_commands()`` and ``register_control_commands`` consume this list so
    the name↔class↔predicate mapping lives in exactly one place.
    """

    return [
        (
            ModelCommand(name="model", surface=_CONTROL_BOTH),
            lambda ctx: _model_selector(ctx) is not None,
        ),
        (
            AgentCommand(name="agent", surface=_CONTROL_BOTH),
            lambda ctx: _agent_selector(ctx) is not None,
        ),
        (
            McpCommand(name="mcp", surface=_CONTROL_BOTH),
            lambda ctx: _mcp_controller(ctx) is not None,
        ),
        (
            NewSessionCommand(name="new", surface=_CONTROL_BOTH),
            lambda ctx: _session_lifecycle(ctx) is not None,
        ),
    ]


def control_commands() -> list[LocalCommand]:
    """Return fresh instances of all four runtime-control commands.

    A factory (not module-level singletons) so each registry gets its own
    instances with no shared mutable state. Derived from :func:`_control_specs`
    (the single definition site).
    """

    return [command for command, _ in _control_specs()]


def register_control_commands(registry: object) -> None:
    """Register the four control commands onto ``registry`` with gated predicates.

    Each command is registered with an ``is_enabled`` predicate that returns
    ``True`` only when its controller is present on ``ctx.runtime``. The result:

    - ``list_for`` EXCLUDES the command when no controller is wired (default-off,
      hidden from the user).
    - ``lookup`` INCLUDES the command regardless (dispatch stays total + safe:
      the command itself returns ``Skip()`` when called without a controller).

    ``registry`` is typed as ``object`` to avoid a hard import of
    ``CommandRegistryImpl`` (keeps this module import-cheap), but it must expose
    a ``register(command, *, is_enabled)`` method compatible with
    ``CommandRegistryImpl.register``.
    """

    for command, is_enabled in _control_specs():
        registry.register(command, is_enabled=is_enabled)
