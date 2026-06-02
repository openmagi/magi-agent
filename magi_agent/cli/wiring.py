"""Composition root for the Magi CLI (PR-F1, Stream F).

This is the ONLY place where the landed Streams (A/B/C/D/E) meet. Two public
functions build the complete dependency graph for each surface:

``build_headless_runtime(...)``
    Constructs the headless dependency set: engine (A), permission gate (C),
    command registry (D), session log (B). MUST NOT import ``cli.tui.*``,
    ``cli.render.*``, ``textual``, or ``rich`` at module top or inside the
    function. This function is the cold-start-clean path.

``build_tui_app(...)``
    Constructs everything ``build_headless_runtime`` does PLUS the
    ``ToolRendererRegistry`` and returns a constructed ``MagiTuiApp``. All
    ``textual`` / ``rich`` / ``cli.tui`` / ``cli.render`` imports are LAZY
    (inside the function body) so importing ``cli.wiring`` does NOT pull
    those in for the headless/version paths.

Cold-start discipline
---------------------
``import magi_agent.cli.wiring`` must succeed without importing
``textual``, ``rich``, ``google-adk``, or ``google-genai`` (all of those are
lazy, exactly as ``cli.engine`` and ``cli.session_log`` already guarantee).
Importing ``cli.wiring`` is therefore safe on any cold path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Light, import-clean imports only at module top.
# cli.engine / cli.permissions / cli.session_log / cli.commands are all
# already documented as import-clean (no textual / google-adk at top level).
# ---------------------------------------------------------------------------
from magi_agent.cli.commands import (
    build_registry,
    install_discovery,
)
from magi_agent.cli.contracts import CommandRegistry
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.permissions import PermissionMode, RulesPermissionGate
from magi_agent.cli.session_log import SessionLog
from magi_agent.composio.config import resolve_composio_config
from magi_agent.composio.mcp import (
    ComposioToolsetBundle,
    attach_composio_toolsets_to_runner,
    build_composio_toolset_bundle,
)

__all__ = [
    "HeadlessRuntime",
    "build_headless_runtime",
    "build_tui_app",
]

# Guard so `install_discovery()` is called at most once per process.
_discovery_installed = False


def _ensure_discovery() -> None:
    global _discovery_installed
    if not _discovery_installed:
        install_discovery()
        _discovery_installed = True


@dataclass
class HeadlessRuntime:
    """Dependency set for the headless path.

    Attributes
    ----------
    engine:
        The ADK-backed :class:`MagiEngineDriver` (or an injected test stub).
    gate:
        The :class:`RulesPermissionGate` wired with the chosen permission mode.
    commands:
        A :class:`CommandRegistry` built from the discovered project commands
        + builtins for ``cwd``.
    session_log:
        An open :class:`SessionLog` scoped to ``(session_id, cwd)``.
    composio:
        Optional Composio MCP toolset bundle, inactive when not configured or
        when optional packages are unavailable.
    mcp_servers:
        Labels for active MCP servers surfaced in protocol metadata.
    """

    engine: MagiEngineDriver
    gate: RulesPermissionGate
    commands: CommandRegistry
    session_log: SessionLog
    composio: ComposioToolsetBundle
    mcp_servers: tuple[str, ...] = ()


def build_headless_runtime(
    *,
    cwd: str | os.PathLike[str] | None = None,
    permission_mode: PermissionMode = "default",
    session_id: str = "cli-session",
    runner: object | None = None,
    model: str | None = None,
) -> HeadlessRuntime:
    """Construct the complete headless dependency set.

    Parameters
    ----------
    cwd:
        Working directory for command discovery + session-log path scoping.
        Defaults to ``os.getcwd()``.
    permission_mode:
        ``"default"`` | ``"acceptEdits"`` | ``"bypassPermissions"``.
    session_id:
        Engine + session-log session id.
    runner:
        Optional explicit ADK runner for ``MagiEngineDriver``. Useful for
        tests (inject a mock) or future production callers that pre-build the
        runner before calling here.
    model:
        Reserved for future model-selection wiring; accepted but not yet
        forwarded (no Stream F model plumbing yet).

    Returns
    -------
    HeadlessRuntime
        A small dataclass holding the four constructed dependencies.

    Cold-start guarantee
    --------------------
    This function MUST NOT import ``textual`` / ``rich`` / ``cli.tui`` /
    ``cli.render``. All those are TUI-only; the headless path is cold-clean.
    """

    _ = model  # reserved seam, not yet wired

    effective_cwd = str(cwd) if cwd is not None else os.getcwd()
    effective_runner = runner if runner is not None else _build_default_runner(model=model)
    composio_config = resolve_composio_config(os.environ)
    composio_bundle = build_composio_toolset_bundle(composio_config)
    composio_attached = attach_composio_toolsets_to_runner(
        effective_runner,
        composio_bundle,
    )
    mcp_servers = (
        (composio_bundle.mcp_server_label,)
        if composio_bundle.active and composio_attached
        else ()
    )

    # (A) Engine — MagiEngineDriver lazy-imports ADK only when a turn is
    #     iterated; construction is free/cheap.
    engine = MagiEngineDriver(runner=effective_runner)

    # (C) Permission gate — RulesPermissionGate with no sinks (headless
    #     ``default`` path will fall back to deny on ask; the HeadlessSink
    #     wiring is a later PR).
    gate = RulesPermissionGate()

    # (D) Command registry — install discovery once (idempotent), then build
    #     the per-cwd registry.
    _ensure_discovery()
    commands = build_registry(effective_cwd)

    # (B) Session log — scoped to (session_id, cwd); never written until the
    #     first ``append`` call (lazy file creation).
    session_log = SessionLog(session_id=session_id, cwd=effective_cwd)

    return HeadlessRuntime(
        engine=engine,
        gate=gate,
        commands=commands,
        session_log=session_log,
        composio=composio_bundle,
        mcp_servers=mcp_servers,
    )


def _build_default_runner(*, model: str | None = None) -> object:
    from magi_agent.cli.local_runner import build_local_cli_runner  # noqa: PLC0415

    return build_local_cli_runner(model=model)


def build_tui_app(
    *,
    cwd: str | os.PathLike[str] | None = None,
    permission_mode: PermissionMode = "default",
    session_id: str = "cli-session",
    runner: object | None = None,
    model: str | None = None,
    runtime: object | None = None,
) -> object:
    """Construct and return a fully-wired :class:`MagiTuiApp`.

    All ``textual`` / ``rich`` / ``cli.tui`` / ``cli.render`` imports are
    LAZY (inside this function body) so importing ``cli.wiring`` does NOT
    pull those in for the headless/version paths.

    Parameters
    ----------
    cwd:
        Working directory for command discovery + session-log path scoping.
    permission_mode:
        ``"default"`` | ``"acceptEdits"`` | ``"bypassPermissions"``.
    session_id:
        Session id forwarded to the engine and TUI app.
    runner:
        Optional explicit ADK runner.
    model:
        Reserved for future model-selection wiring.
    runtime:
        Optional runtime object forwarded to ``MagiTuiApp`` (for tests /
        production callers that pre-build a runtime).

    Returns
    -------
    MagiTuiApp
        A constructed TUI app ready to ``.run()``.
    """

    # ------------------------------------------------------------------ #
    # ALL textual / rich / cli.tui / cli.render imports are LAZY here.    #
    # ------------------------------------------------------------------ #
    from magi_agent.cli.tui.app import MagiTuiApp  # noqa: PLC0415
    from magi_agent.cli.tui.tool_render import build_tool_renderers  # noqa: PLC0415

    runtime_runner = getattr(runtime, "runner", None) if runtime is not None else None
    effective_runner = runner if runner is not None else runtime_runner

    # Build the shared headless half (engine / gate / commands / log).
    rt = build_headless_runtime(
        cwd=cwd,
        permission_mode=permission_mode,
        session_id=session_id,
        runner=effective_runner,
        model=model,
    )

    renderers = build_tool_renderers()

    app = MagiTuiApp(
        engine=rt.engine,
        gate=rt.gate,
        commands=rt.commands,
        renderers=renderers,
        runtime=runtime,
        session_id=session_id,
    )

    # FIX 2 (global review): attach the app's TextualSink to the gate so the
    # gate races the TUI sink. build_headless_runtime constructs the gate with
    # an EMPTY ``sinks`` list; without this wiring any tool needing an ``ask``
    # verdict resolves to safe-deny and the ToolUseConfirm modal never appears.
    # Defensive: only when the gate exposes a ``sinks`` list.
    gate_sinks = getattr(rt.gate, "sinks", None)
    if isinstance(gate_sinks, list):
        gate_sinks.append(app.sink)

    return app
