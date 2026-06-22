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
from pathlib import Path
from typing import Optional

import typer
from typer.core import TyperGroup

from magi_agent.cli.headless import run_headless
from magi_agent.cli.wiring import (
    build_headless_runtime,
    build_tui_app,
    local_runner_policy_routing_enabled_from_env,
)
from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults

__all__ = ["app", "main", "resolve_headless_permission_mode"]


def _anthropic_default_model_id() -> str:
    """Pull anthropic's default model id from the single ``ModelCatalog`` (E-1)
    so help strings never drift from ``cli.providers._DEFAULT_MODEL``.
    """
    from magi_agent.models.catalog import ModelCatalog  # noqa: PLC0415

    return ModelCatalog.builtin().default_model_for("anthropic").model


_DEFAULT_ANTHROPIC_MODEL = _anthropic_default_model_id()
_MODEL_OPTION_HELP = (
    f"{_DEFAULT_ANTHROPIC_MODEL} model override; provider default when unset."
)

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
    smart_approve = "smartApprove"


class AgentMode(str, Enum):
    plan = "plan"
    act = "act"


def _normalize_runtime_profile(runtime_profile: str | None) -> str:
    return (runtime_profile or "").strip().lower()


class _ResumeArgs:
    """Duck-typed args object for ``session_log.prepare_resume`` (PR-04-PR2).

    ``prepare_resume`` reads ``resume`` / ``continue_`` / ``cwd`` attributes; this
    tiny carrier adapts the typer options without importing argparse.
    """

    def __init__(
        self, *, resume: str | None, continue_: bool, cwd: str
    ) -> None:
        self.resume = resume
        self.continue_ = continue_
        self.cwd = cwd


def _resume_enabled() -> bool:
    """Whether ``--resume``/``--continue`` rehydration is armed (stage-1 OFF)."""

    from magi_agent.config.env import cli_resume_enabled  # noqa: PLC0415

    return cli_resume_enabled(os.environ)


def resolve_headless_permission_mode(
    *, permission_mode: str, flag_is_default: bool, runtime_profile: str | None
) -> str:
    """Resolve the headless permission mode.

    Local CLI/TUI installs default to ``bypassPermissions`` when the operator
    omits ``--permission-mode``. An explicit ``--permission-mode`` always wins,
    including an explicit ``default``.
    """
    del runtime_profile  # Reserved for compatibility with existing callers.
    if flag_is_default and permission_mode == "default":
        return "bypassPermissions"
    return permission_mode


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
        help=(
            "Permission mode: default | acceptEdits | bypassPermissions | "
            "smartApprove."
        ),
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
        help=_MODEL_OPTION_HELP,
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
    # when no explicit subcommand is given). ``ctx`` is used for
    # get_parameter_source in both the headless and TUI branches below.

    runtime_profile = _normalize_runtime_profile(os.environ.get("MAGI_RUNTIME_PROFILE"))
    if runtime_profile == "eval":
        from magi_agent.runtime.local_defaults import apply_local_eval_runtime_defaults  # noqa: PLC0415

        apply_local_eval_runtime_defaults(os.environ)
    elif runtime_profile == "lab":
        from magi_agent.runtime.local_defaults import apply_lab_runtime_defaults  # noqa: PLC0415

        apply_lab_runtime_defaults(os.environ)
    else:
        apply_local_full_runtime_defaults(os.environ)
    runner_policy_routing_enabled = local_runner_policy_routing_enabled_from_env()

    # ------------------------------------------------------------------ #
    # Mode selection                                                       #
    # ------------------------------------------------------------------ #
    is_non_interactive = bool(prompt) or print_flag or (not sys.stdin.isatty())

    # Normalize output format string.
    output_str = output.value  # e.g. "text", "json", "stream-json"

    # PR-04-PR2: --resume / --continue now rehydrate prior conversation context.
    # When MAGI_CLI_RESUME_ENABLED is on, resolve a ResumeContext from the
    # on-disk JSONL transcript (session_log) and thread its reconstructed
    # ``initial_messages`` into the engine so the model replays the earlier
    # conversation. Gate OFF (stage-1 default) OR no transcript -> empty context,
    # so --resume/--continue degrade to the legacy "id only" behavior with no
    # error (byte-identical for a fresh session).
    resume_messages: list[dict[str, str]] = []
    if (resume or continue_) and _resume_enabled():
        from magi_agent.cli.session_log import prepare_resume  # noqa: PLC0415

        resume_ctx = prepare_resume(
            _ResumeArgs(resume=resume, continue_=continue_, cwd=os.getcwd())
        )
        resume_messages = list(resume_ctx.initial_messages or [])

    if is_non_interactive:
        # -------------------------------------------------------------- #
        # Headless branch                                                  #
        # -------------------------------------------------------------- #
        permission_mode_source = ctx.get_parameter_source("permission_mode")
        headless_permission_mode = resolve_headless_permission_mode(
            permission_mode=permission_mode.value,
            flag_is_default=(
                permission_mode is PermMode.default
                and getattr(permission_mode_source, "name", None) == "DEFAULT"
            ),
            runtime_profile=runtime_profile,
        )
        rt = build_headless_runtime(
            permission_mode=headless_permission_mode,  # type: ignore[arg-type]
            session_id=resume or "cli-session",
            model=model,
            mode=mode.value,  # type: ignore[arg-type]
            runner_policy_routing_enabled=runner_policy_routing_enabled,
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
                permission_mode=headless_permission_mode,  # type: ignore[arg-type]
                session_id=rt.session_log.path.stem
                if hasattr(rt.session_log, "path")
                else (resume or "cli-session"),
                stream=None,  # default: sys.stdout
                input_stream=inbound,  # type: ignore[arg-type]
                mcp_servers=rt.mcp_servers,
                session_log=rt.session_log,
                initial_messages=resume_messages,
            )
        )
        raise typer.Exit(code=exit_code)

    else:
        # -------------------------------------------------------------- #
        # Interactive TUI branch                                           #
        # All textual imports happen lazily inside build_tui_app.         #
        # -------------------------------------------------------------- #
        # The interactive TUI defaults to bypassPermissions when the flag is
        # omitted: a local interactive session has a human watching, and the
        # per-tool confirm modal (including Bash) proved too noisy as a default.
        # Explicit --permission-mode values, including default/acceptEdits, are
        # honored as-is. Headless (-p) keeps its own resolution.
        permission_mode_source = ctx.get_parameter_source("permission_mode")
        tui_permission_mode = (
            "bypassPermissions"
            if (
                permission_mode is PermMode.default
                and getattr(permission_mode_source, "name", None) == "DEFAULT"
            )
            else permission_mode.value
        )
        tui = build_tui_app(
            cwd=os.getcwd(),
            permission_mode=tui_permission_mode,  # type: ignore[arg-type]
            session_id=resume or "cli-session",
            model=model,
            mode=mode.value,  # type: ignore[arg-type]
            runner_policy_routing_enabled=runner_policy_routing_enabled,
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
    """Run environment diagnostics.

    Reports the four things a local ``magi`` run needs: a resolvable provider
    config, the ``litellm`` dependency, a readable config file (if present), and
    a writable working directory, followed by optional-integration status. This
    command is informational and always exits 0; read the lines for problems.
    """
    _ = ctx
    from magi_agent.cli import providers as _providers  # noqa: PLC0415

    # 1. Provider configuration (env key or ~/.magi/config.toml).
    config = _providers.resolve_provider_config()
    if config is None:
        hints = sorted(
            {name for keys in _providers._PROVIDER_ENV_KEYS.values() for name in keys}
        )
        typer.echo(
            "provider: NONE — set one of "
            + ", ".join(hints)
            + f", or create {_providers._config_path()}",
            err=False,
        )
    else:
        typer.echo(
            f"provider: OK ({config.provider}, model={config.model})", err=False
        )

    # 2. litellm dependency (required to build the real model runner).
    try:
        import litellm  # noqa: F401, PLC0415

        typer.echo("litellm: OK", err=False)
    except Exception:  # noqa: BLE001 — any import failure means it is unusable
        typer.echo(
            "litellm: MISSING — install the cli extra "
            "(`uv sync --extra cli` from source, or `pip install litellm`)",
            err=False,
        )

    # 3. Config file readability (flagged only if it exists but cannot be read).
    cfg_path = _providers._config_path()
    if cfg_path.exists():
        if os.access(cfg_path, os.R_OK):
            typer.echo(f"config file: OK ({cfg_path})", err=False)
        else:
            typer.echo(f"config file: UNREADABLE ({cfg_path})", err=False)
    else:
        typer.echo(
            f"config file: none ({cfg_path}) — using environment variables",
            err=False,
        )

    # 4. Working directory writability.
    cwd = os.getcwd()
    if os.access(cwd, os.W_OK):
        typer.echo(f"workspace: OK (writable: {cwd})", err=False)
    else:
        typer.echo(f"workspace: NOT WRITABLE ({cwd})", err=False)

    # Optional integration status.
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
# `magi pack` — pack authoring (Pack B1)
# ---------------------------------------------------------------------------

pack_app = typer.Typer(
    name="pack",
    help="Author and manage user packs.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@pack_app.callback(invoke_without_command=True)
def pack_root(ctx: typer.Context) -> None:
    """Author and manage user packs."""
    if ctx.invoked_subcommand is None:
        typer.echo("magi pack: use `magi pack new <type> <name>`.", err=False)


@pack_app.command("new")
def pack_new(
    ptype: str = typer.Argument(
        ...,
        metavar="TYPE",
        help=(
            "One of: tool, callback, validator, harness, control_plane, "
            "evidence_producer, recipe, connector."
        ),
    ),
    name: str = typer.Argument(..., help="Human name for the primitive (e.g. my-check)."),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest",
        help="Packs root to scaffold into. Default: <cwd>/.magi/packs.",
    ),
) -> None:
    """Scaffold a ready-to-load user pack (pack.toml + impl stub + smoke test)."""
    from magi_agent.packs.scaffold import scaffold_pack  # noqa: PLC0415

    dest_root = dest if dest is not None else Path.cwd() / ".magi" / "packs"
    try:
        result = scaffold_pack(ptype, name, dest_root)
    except ValueError as exc:
        typer.echo(f"magi pack new: {exc}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"pack created: {result.pack_dir}")
    typer.echo(f"  ref:        {result.ref}")
    typer.echo(f"  manifest:   {result.pack_toml}")
    if result.impl_path is not None:
        typer.echo(f"  impl:       {result.impl_path}")
    if result.spec_path is not None:
        typer.echo(f"  spec:       {result.spec_path}")
    typer.echo(f"  smoke test: {result.test_path}")
    typer.echo(
        "next: edit the impl, then verify it loads with "
        f"`pytest {result.test_path}` — packs under <cwd>/.magi/packs are "
        "discovered automatically (no PYTHONPATH needed)."
    )


app.add_typer(pack_app, name="pack")


# ---------------------------------------------------------------------------
# `magi gateway` — always-on daemon (Track F)
# ---------------------------------------------------------------------------

gateway_app = typer.Typer(
    name="gateway",
    help="Always-on gateway daemon (cron + live channels). Default OFF.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@gateway_app.callback(invoke_without_command=True)
def gateway_root(ctx: typer.Context) -> None:
    """Manage the always-on gateway daemon."""
    if ctx.invoked_subcommand is None:
        typer.echo(
            "magi gateway: use start | install | uninstall | status.", err=False
        )


@gateway_app.command("status")
def gateway_status() -> None:
    """Show whether the gateway daemon is enabled (env gate) — no side effects."""
    from magi_agent.gateway.daemon import is_gateway_daemon_enabled  # noqa: PLC0415

    if is_gateway_daemon_enabled():
        typer.echo(
            "gateway daemon: enabled (MAGI_GATEWAY_DAEMON_ENABLED is set). "
            "Each watcher still respects its own gate."
        )
    else:
        typer.echo(
            "gateway daemon: disabled — set MAGI_GATEWAY_DAEMON_ENABLED=1 to "
            "enable always-on (each watcher also needs its own gate)."
        )


@gateway_app.command("start")
def gateway_start(
    once: bool = typer.Option(
        False,
        "--once",
        help="Run a single scheduler tick and exit (legacy behavior).",
    ),
) -> None:
    """Run the gateway daemon (gated). Default: supervise until SIGINT/SIGTERM.

    Gate OFF → prints status and exits. With the gate ON, the default mode
    supervises the first-party watcher set via ``GatewayDaemon.run`` (each
    watcher still respects its own gate, e.g. the cron watcher's
    ``MAGI_SCHEDULER_EXECUTOR_ENABLED``). ``--once`` keeps the legacy
    single-tick behavior. Channel watchers still require explicit
    provider/client wiring and are not constructed by this local CLI.
    """
    import asyncio  # noqa: PLC0415
    import contextlib  # noqa: PLC0415
    import signal  # noqa: PLC0415

    from magi_agent.gateway.daemon import (  # noqa: PLC0415
        is_gateway_daemon_enabled,
    )

    if not is_gateway_daemon_enabled():
        typer.echo(
            "gateway daemon: disabled (not enabled). Set "
            "MAGI_GATEWAY_DAEMON_ENABLED=1 to start always-on."
        )
        return

    if once:
        _gateway_run_once()
        return

    from magi_agent.gateway.daemon import build_default_gateway_daemon  # noqa: PLC0415

    daemon = build_default_gateway_daemon()

    async def _main() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)
        typer.echo(
            "gateway daemon: supervising watchers (Ctrl-C or SIGTERM to stop). "
            "Each watcher still respects its own gate."
        )
        await daemon.run(stop_event=stop_event)
        typer.echo("gateway daemon: stopped.")

    asyncio.run(_main())


def _gateway_run_once() -> None:
    """Legacy single scheduler tick (the pre-daemon `gateway start` body)."""
    from magi_agent.gateway.watchers import (  # noqa: PLC0415
        build_local_scheduler_cron_driver,
        is_scheduler_executor_enabled,
    )

    if not is_scheduler_executor_enabled():
        typer.echo(
            "gateway daemon: enabled. scheduler_cron disabled "
            "(MAGI_SCHEDULER_EXECUTOR_ENABLED is not set). Channel watchers "
            "require explicit provider wiring."
        )
        return
    driver = build_local_scheduler_cron_driver()
    result = driver.run_once()
    typer.echo(
        "gateway daemon: enabled. "
        f"scheduler_cron: {_scheduler_once_summary(result)}. "
        "Channel watchers require explicit provider wiring."
    )


def _scheduler_once_summary(result: object) -> str:
    tick_result = getattr(result, "tick_result")
    executions = tuple(getattr(result, "executions", ()))
    modes = sorted({str(getattr(execution, "mode", "unknown")) for execution in executions})
    mode = ",".join(modes) if modes else "none"
    runner_invoked = any(
        bool(getattr(execution, "runner_invoked", False)) for execution in executions
    )
    return (
        f"{tick_result.status} "
        f"fired={len(tick_result.fired_job_ids)} "
        f"skipped={len(tick_result.skipped_job_ids)} "
        f"executions={len(executions)} "
        f"mode={mode} "
        f"runnerInvoked={str(runner_invoked).lower()}"
    )


@gateway_app.command("install")
def gateway_install(
    target_path: Path = typer.Option(
        ...,
        "--target-path",
        help="Where to write the generated unit/plist (no system dir is touched).",
    ),
    manager: Optional[str] = typer.Option(
        None,
        "--manager",
        help="systemd | launchd. Auto-detected from the platform when unset.",
    ),
    exec_path: str = typer.Option(
        "magi",
        "--exec-path",
        help="Path to the magi executable used in ExecStart / ProgramArguments.",
    ),
) -> None:
    """Generate + write an OS service file to --target-path (default-off).

    Does NOT run systemctl/launchctl and does NOT set the env gate — installing
    alone keeps the daemon a no-op until MAGI_GATEWAY_DAEMON_ENABLED is set.
    """
    from magi_agent.gateway.service_install import (  # noqa: PLC0415
        DEFAULT_LAUNCHD_LABEL,
        ServiceManager,
        detect_service_manager,
        install_service,
    )

    if manager is None:
        mgr = detect_service_manager()
    else:
        try:
            mgr = ServiceManager(manager.lower())
        except ValueError:
            typer.echo(f"unknown --manager: {manager!r}", err=True)
            raise typer.Exit(code=2)

    if mgr is ServiceManager.SYSTEMD:
        written = install_service(
            manager=mgr,
            target_path=target_path,
            exec_start=f"{exec_path} gateway start",
        )
    elif mgr is ServiceManager.LAUNCHD:
        written = install_service(
            manager=mgr,
            target_path=target_path,
            program_arguments=[exec_path, "gateway", "start"],
            label=DEFAULT_LAUNCHD_LABEL,
        )
    else:
        typer.echo(
            "unsupported platform for service install (need systemd or launchd).",
            err=True,
        )
        raise typer.Exit(code=2)

    typer.echo(f"gateway service written: {written}")


@gateway_app.command("uninstall")
def gateway_uninstall(
    target_path: Path = typer.Option(
        ...,
        "--target-path",
        help="The unit/plist path to remove (no system dir is touched).",
    ),
) -> None:
    """Remove the service file at --target-path. Does not run systemctl/launchctl."""
    from magi_agent.gateway.service_install import uninstall_service  # noqa: PLC0415

    removed = uninstall_service(target_path=target_path)
    if removed:
        typer.echo(f"gateway service removed: {target_path}")
    else:
        typer.echo(f"gateway service not present: {target_path}")


app.add_typer(gateway_app, name="gateway")


# ---------------------------------------------------------------------------
# computer-use subcommand
# ---------------------------------------------------------------------------

computer_use_app = typer.Typer(
    name="computer-use",
    help="Manage the macOS computer-use tool.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@computer_use_app.callback(invoke_without_command=True)
def computer_use_root(ctx: typer.Context) -> None:
    """Manage the macOS computer-use tool."""
    if ctx.invoked_subcommand is None:
        typer.echo("magi computer-use: use `magi computer-use install`.", err=False)


@computer_use_app.command("install")
def computer_use_install() -> None:
    """Download, verify, and install the pinned cua-driver binary."""
    import sys  # noqa: PLC0415

    from magi_agent.computer.autonomous.install_runner import host_arch, install  # noqa: PLC0415
    from magi_agent.computer.autonomous.installer import (  # noqa: PLC0415
        CUA_DRIVER_VERSION,
        InstallError,
        gatekeeper_note,
    )

    arch = host_arch()
    typer.echo(f"Installing cua-driver v{CUA_DRIVER_VERSION} ({arch})…")
    try:
        report = install()
    except InstallError as exc:
        typer.echo(f"install failed: {exc}", err=True)
        sys.exit(1)
    typer.echo(f"  app:    {report.app_path}")
    typer.echo(f"  binary: {report.binary_symlink}")
    typer.echo(f"  sha256: {report.sha256}")
    typer.echo("")
    typer.echo("Next: grant TCC to the CuaDriver daemon (not your terminal):")
    typer.echo("  cua-driver permissions grant")
    typer.echo(gatekeeper_note())


app.add_typer(computer_use_app, name="computer-use")


# ---------------------------------------------------------------------------
# LegalBench evaluation subcommand
# ---------------------------------------------------------------------------

@app.command()
def legalbench(
    data_root: Path = typer.Option(
        Path("data/legalbench"),
        "--data-root",
        help="Root directory containing per-task subdirs (train.tsv/test.tsv/base_prompt.txt).",
    ),
    manifest: Path = typer.Option(
        Path("data/legalbench/manifest.v1.json"),
        "--manifest",
        help="Path to the JSON manifest listing {task_id, reasoning_type} entries.",
    ),
    max_tasks: Optional[int] = typer.Option(
        None,
        "--max-tasks",
        help="Evaluate only the first N tasks from the manifest.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help=_MODEL_OPTION_HELP,
    ),
    ablation: bool = typer.Option(
        False,
        "--ablation/--no-ablation",
        help=(
            "Also run per-checkpoint ablation (marginal lift per checkpoint). "
            "Cost is roughly (1 + N_checkpoints) * total_instances. Off by default."
        ),
    ),
) -> None:
    """Run the LegalBench harness evaluation (requires MAGI_LEGAL_HARNESS_ENABLED=1).

    Evaluates the harness (all checkpoints enabled) and a baseline (all off)
    against the curated manifest subset, then prints harness, baseline, and lift
    as JSON to stdout.

    With --ablation: also runs per-checkpoint marginal-lift measurement and
    includes an "ablation" key in the JSON output (keyed by checkpoint name).

    Requires MAGI_LEGAL_HARNESS_ENABLED=1 to run. Without a configured provider
    (ANTHROPIC_API_KEY / OPENAI_API_KEY / etc.) the command exits with an error.
    """
    import json as _json  # noqa: PLC0415 - deferred (cold-start discipline)

    from magi_agent.benchmarks.legal_eval import lift as _lift  # noqa: PLC0415
    from magi_agent.benchmarks.legalbench.cli import (  # noqa: PLC0415
        GateDisabledError,
        ensure_enabled,
        run_checkpoint_ablation,
        run_eval,
    )
    from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

    # Pre-check so the gate error surfaces before provider-resolution I/O; run_eval also enforces this.
    try:
        ensure_enabled()
    except GateDisabledError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    # Resolve provider config (respects MAGI_PROVIDER / ANTHROPIC_API_KEY / etc.)
    provider_cfg = resolve_provider_config(model_override=model)
    if provider_cfg is None:
        typer.echo(
            "No provider configured. Set ANTHROPIC_API_KEY (or OPENAI_API_KEY / "
            "GEMINI_API_KEY) to run the live harness.",
            err=True,
        )
        raise typer.Exit(code=1)

    import importlib.util  # noqa: PLC0415
    if importlib.util.find_spec("litellm") is None:
        typer.echo("litellm is required: pip install 'magi-agent[providers]'", err=True)
        raise typer.Exit(code=1)

    def _real_complete(prompt: str) -> str:
        """Single-turn completion via litellm.completion (no tools).

        Wire: litellm.completion(model=provider_cfg.litellm_model,
        api_key=provider_cfg.api_key, messages=[{role:user, content:prompt}])
        -> response.choices[0].message.content

        provider_cfg.litellm_model is built by ProviderConfig.litellm_model
        (magi_agent/cli/providers.py:75) as "<litellm_prefix>/<model>".
        The same litellm dependency is already used by
        magi_agent/cli/real_runner.py:_build_litellm_model().
        """
        try:
            import litellm  # noqa: PLC0415
        except ImportError as exc:
            raise NotImplementedError(
                "Wire to: litellm.completion(model=provider_cfg.litellm_model, "
                "api_key=provider_cfg.api_key, messages=[...]). "
                "Install with: pip install 'magi-agent[providers]'"
            ) from exc
        response = litellm.completion(
            model=provider_cfg.litellm_model,
            api_key=provider_cfg.api_key,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    harness_report, baseline_report = run_eval(
        data_root=data_root,
        manifest_path=manifest,
        complete=_real_complete,
        max_tasks=max_tasks,
    )
    lift_report = _lift(harness=harness_report, baseline=baseline_report)
    output_dict: dict = {
        "harness": harness_report.model_dump(),
        "baseline": baseline_report.model_dump(),
        "lift": lift_report.model_dump(),
    }
    if ablation:
        ablation_result = run_checkpoint_ablation(
            data_root=data_root,
            manifest_path=manifest,
            complete=_real_complete,
            max_tasks=max_tasks,
        )
        output_dict["ablation"] = {k: v.model_dump() for k, v in ablation_result.items()}
    typer.echo(_json.dumps(output_dict, indent=2))


# ---------------------------------------------------------------------------
# Console-script entry
# ---------------------------------------------------------------------------

def main() -> None:
    """Console-script entry point (registered in pyproject.toml later)."""
    from magi_agent.ops.otel_noise import silence_otel_detach_noise

    silence_otel_detach_noise()
    # Install-default-on memory: overlay ~/.magi/config.toml[memory] on the
    # install defaults ({enabled, prefer_local_search}) and setdefault the
    # matching MAGI_MEMORY_* env vars so the env-reading runtime gates see them.
    # Runs ONLY from this real CLI entrypoint (never during library/test imports);
    # the code-level default (resolve_memory_config(env={}) → master False) is
    # unchanged. Fail-soft. See magi_agent/cli/memory_bootstrap.py.
    #
    # Gate by runtime profile, mirroring apply_local_full_runtime_defaults: the
    # lean/opt-out profiles (safe|minimal|off|conservative|eval) must NOT inherit
    # install-default-on memory — they leave it at the code default (off) unless
    # config/env explicitly enables it. This keeps the eval measurement profile
    # free of per-turn file IO + the <memory-recall> prompt block.
    from magi_agent.runtime.local_defaults import (  # noqa: PLC0415
        local_full_runtime_defaults_enabled,
    )

    if local_full_runtime_defaults_enabled(os.environ):
        # File-driven install profile (e.g. Homebrew-seeded ~/.magi/profile.env):
        # setdefault MAGI_* flags BEFORE the memory bootstrap so a profile that
        # sets MAGI_RUNTIME_PROFILE/memory flags is honoured. No file => no-op
        # (pip installs stay at code defaults). Explicit env still wins.
        from magi_agent.cli.install_profile_bootstrap import (
            apply_install_profile_bootstrap,
        )

        apply_install_profile_bootstrap(os.environ)
        from magi_agent.cli.memory_bootstrap import apply_memory_config_bootstrap

        apply_memory_config_bootstrap(os.environ)
    app()
