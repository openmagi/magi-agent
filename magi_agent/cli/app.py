"""Typer CLI entrypoint for Magi (PR-F1, Stream F).

This module defines the Typer application that serves as the ``magi`` command.
It is intentionally thin: all composition happens in ``cli.wiring`` and all
turn execution is delegated to ``cli.headless.run_headless`` (headless path) or
``cli.wiring.build_tui_app(...).run()`` (interactive TUI path).

Mode selection
--------------
Non-interactive (headless) when ANY of:
- A ``[prompt]`` positional argument is provided.
- ``--print / -p`` flag is set.
- ``sys.stdin.isatty()`` returns ``False`` (stdin is piped/redirected).

Interactive (TUI) otherwise: no prompt, no ``-p``, stdin is a tty.

Sub-commands
------------
``config`` and ``mcp`` are minimal stubs. ``doctor`` and ``auth`` expose
lightweight status checks.

Cold-start discipline
---------------------
All ``textual`` / ``cli.tui`` imports are deferred to the interactive branch
(inside the callback, never at module top). ``typer`` is imported at top level
here (this module IS the Typer surface). ``cli.headless`` and ``cli.wiring``
imports at top level are safe (both are documented import-clean).
"""

from __future__ import annotations

import asyncio
import os
import sys
from enum import Enum
from typing import Optional

import typer
from typer.core import TyperGroup

from magi_agent.cli.headless import run_headless
from magi_agent.cli.wiring import build_headless_runtime, build_tui_app

__all__ = ["app", "main"]

# ---------------------------------------------------------------------------
# Default-command group
# ---------------------------------------------------------------------------
#
# Problem: Typer's root ``@app.callback(invoke_without_command=True)`` with a
# positional ``[prompt]`` argument SHADOWS the subcommands — Click feeds the
# subcommand token (``config``) into the positional ``prompt`` so the callback
# runs the agent with ``prompt="config"`` and the subcommand never fires.
#
# Fix (mirrors Claude Code's two-tier dispatcher, see
# docs/architecture/claude-code-cli/01-entrypoint-arg-parsing.md §3/§4): the
# *agent is the default command*; ``config``/``doctor``/``mcp``/``auth`` are
# siblings. We use a custom Click group that, when the first token is NOT a
# registered subcommand (and not ``--help``-style help), routes the whole argv
# to the default ``agent`` command. Known subcommands resolve normally.

_DEFAULT_COMMAND = "agent"


class DefaultCommandGroup(TyperGroup):
    """A Click/Typer group that falls back to a default command for unknown tokens.

    Dispatch rules:
    - First token is a registered subcommand name → invoke that subcommand.
    - No tokens at all → invoke the default ``agent`` command (bare ``magi``;
      the agent then decides TUI vs headless based on tty/stdin).
    - First token is anything else (a bare prompt, or an option like ``-p``)
      → prepend the default command name so the args route to ``agent``.

    The group help (``magi --help`` / ``magi -h``) is preserved: those tokens
    are handled by Click before ``resolve_command`` falls through.
    """

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:
        # Route to the default command when there is no explicit subcommand.
        # We must NOT swallow help requests for the group itself.
        if not args:
            args = [_DEFAULT_COMMAND]
        else:
            first = args[0]
            # Help for the group: let Click handle it normally.
            if first not in ("-h", "--help"):
                if first not in self.commands:
                    # Unknown token (bare prompt or an agent flag/option) →
                    # prepend the default command so the rest routes to it.
                    args = [_DEFAULT_COMMAND, *args]
        return super().parse_args(ctx, args)


# ---------------------------------------------------------------------------
# Typer app (uses the default-command group)
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="magi",
    help="Magi CLI — autonomous agent interface.",
    add_completion=False,
    no_args_is_help=False,
    cls=DefaultCommandGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)


# ---------------------------------------------------------------------------
# Output format + permission mode enums (so Typer shows valid choices)
# ---------------------------------------------------------------------------

class OutputFormat(str, Enum):
    text = "text"
    json = "json"
    stream_json = "stream-json"


class PermMode(str, Enum):
    default = "default"
    accept_edits = "acceptEdits"
    bypass = "bypassPermissions"


class AgentMode(str, Enum):
    plan = "plan"
    act = "act"


def _composio_status_line(prefix: str) -> str:
    from magi_agent.composio.config import resolve_composio_config
    from magi_agent.composio.health import composio_health_metadata

    metadata = composio_health_metadata(resolve_composio_config(os.environ))
    state = "active" if metadata["active"] else "inactive"
    reason = metadata.get("disabledReason")
    next_action = metadata.get("nextAction")
    parts = [f"{prefix}: {state}"]
    if reason:
        parts.append(f"reason={reason}")
    if next_action:
        parts.append(str(next_action))
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# The default "agent" command (a real sibling command, routed to by default)
# ---------------------------------------------------------------------------

@app.command(_DEFAULT_COMMAND)
def agent(
    ctx: typer.Context,
    prompt: Optional[str] = typer.Argument(None, help="Prompt to send to the agent."),
    print_flag: bool = typer.Option(
        False, "--print", "-p",
        help="Print response and exit (non-interactive headless mode).",
    ),
    output: OutputFormat = typer.Option(
        OutputFormat.text,
        "--output",
        help="Output format for headless mode.",
    ),
    include_partial_messages: bool = typer.Option(
        False,
        "--include-partial-messages",
        help="Include partial streaming events in stream-json output.",
    ),
    permission_mode: PermMode = typer.Option(
        PermMode.default,
        "--permission-mode",
        help="Permission mode: default | acceptEdits | bypassPermissions.",
    ),
    resume: Optional[str] = typer.Option(
        None,
        "--resume",
        help="Resume a session by id.",
    ),
    continue_: bool = typer.Option(
        False,
        "--continue/--no-continue",
        help="Continue the most-recent session.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model to use (reserved; not yet fully wired).",
    ),
    mode: AgentMode = typer.Option(
        AgentMode.act,
        "--mode",
        help="Agent mode: plan (read-only tools) | act (full tools).",
    ),
) -> None:
    """Run the Magi agent (default command).

    With a prompt or in non-interactive mode: headless NDJSON/text output.
    Without a prompt and with an interactive terminal: launches the TUI.
    """

    # ``agent`` is the default command (routed to by ``DefaultCommandGroup``
    # when no explicit subcommand is given). ``ctx`` is accepted for parity
    # with the other commands and possible future use.
    _ = ctx

    # ------------------------------------------------------------------ #
    # Mode selection                                                       #
    # ------------------------------------------------------------------ #
    is_non_interactive = bool(prompt) or print_flag or (not sys.stdin.isatty())

    # Normalize output format string.
    output_str = output.value  # e.g. "text", "json", "stream-json"

    # NOTE: --resume / --continue only thread a session id today; true session
    # rehydration (replaying initial_messages into the engine) is a v1.1 follow-up
    # — the engine still stubs initial_messages (engine._drive: ``_ = initial_messages``),
    # so wiring resume here would be hollow.
    _ = continue_  # accepted; resume rehydration deferred to v1.1

    if is_non_interactive:
        # -------------------------------------------------------------- #
        # Headless branch                                                  #
        # -------------------------------------------------------------- #
        rt = build_headless_runtime(
            permission_mode=permission_mode.value,  # type: ignore[arg-type]
            session_id=resume or "cli-session",
            model=model,
            mode=mode.value,  # type: ignore[arg-type]
        )

        # Resolve prompt: explicit arg, else read from stdin (which then can't
        # double as the inbound control channel).
        effective_prompt: str
        inbound: object | None = None
        if prompt:
            effective_prompt = prompt
            # With an explicit prompt arg, stdin is free to act as the inbound
            # NDJSON control channel for stream-json (permission answers, cancel).
            if output_str == "stream-json":
                inbound = sys.stdin
        else:
            effective_prompt = sys.stdin.read()

        exit_code = asyncio.run(
            run_headless(
                effective_prompt,
                output=output_str,  # type: ignore[arg-type]
                include_partial=include_partial_messages,
                gate=rt.gate,
                commands=rt.commands,
                driver=rt.engine,
                permission_mode=permission_mode.value,  # type: ignore[arg-type]
                session_id=rt.session_log.path.stem
                if hasattr(rt.session_log, "path")
                else (resume or "cli-session"),
                stream=None,  # default: sys.stdout
                input_stream=inbound,  # type: ignore[arg-type]
                mcp_servers=rt.mcp_servers,
            )
        )
        raise typer.Exit(code=exit_code)

    else:
        # -------------------------------------------------------------- #
        # Interactive TUI branch                                           #
        # All textual imports happen lazily inside build_tui_app.         #
        # -------------------------------------------------------------- #
        tui = build_tui_app(
            permission_mode=permission_mode.value,  # type: ignore[arg-type]
            session_id=resume or "cli-session",
            model=model,
            mode=mode.value,  # type: ignore[arg-type]
        )
        tui.run()


# ---------------------------------------------------------------------------
# Stub sub-commands
# ---------------------------------------------------------------------------

@app.command()
def config(
    ctx: typer.Context,
) -> None:
    """Manage Magi configuration (stub — not yet implemented)."""
    typer.echo("magi config: not yet implemented.", err=False)


@app.command()
def doctor(
    ctx: typer.Context,
) -> None:
    """Run environment diagnostics."""
    _ = ctx
    typer.echo(_composio_status_line("Composio"), err=False)


@app.command()
def mcp(
    ctx: typer.Context,
) -> None:
    """Manage MCP server connections (stub — not yet implemented)."""
    typer.echo("magi mcp: not yet implemented.", err=False)


auth_app = typer.Typer(
    name="auth",
    help="Manage authentication.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@auth_app.callback(invoke_without_command=True)
def auth_root(ctx: typer.Context) -> None:
    """Manage authentication."""
    if ctx.invoked_subcommand is None:
        typer.echo("magi auth: use `magi auth composio status`.", err=False)


@auth_app.command("composio")
def auth_composio(
    action: str = typer.Argument("status", help="Status action. Only `status` is supported."),
) -> None:
    """Show Composio authentication status."""
    if action != "status":
        typer.echo("magi auth composio: only `status` is supported.", err=True)
        raise typer.Exit(code=2)
    typer.echo(_composio_status_line("Composio auth"), err=False)


app.add_typer(auth_app, name="auth")


# ---------------------------------------------------------------------------
# Console-script entry
# ---------------------------------------------------------------------------

def main() -> None:
    """Console-script entry point (registered in pyproject.toml later)."""
    app()
