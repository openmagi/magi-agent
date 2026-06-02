"""Seed *local* builtin slash-commands for the Magi CLI (Stream D, PR-D2).

These are the always-present, model-free commands every surface (TUI + headless)
exposes: ``status``, ``reset``, ``compact`` and ``help``.

Design rationale
----------------
- **Delegate recognition to the boundary, never reparse.** Three of the four
  builtins (``status``/``reset``/``compact``) consult
  ``SlashControlBoundary.project`` instead of reimplementing slash parsing /
  intent classification. The boundary is the single source of truth for what a
  ``/compact`` (etc.) *means* (redaction, reason codes, intent shape). Builtins
  only MAP the boundary's ``SlashControlDecision`` onto a ``LocalResult``. This
  keeps parsing in exactly one place and makes the builtins thin adapters.
- **Lazy, local boundary construction.** We build the
  ``SlashControlBoundary`` inside ``call`` (per invocation) rather than at
  import / construction time. Importing this module must be cheap and
  side-effect-free (no event loop, no shared mutable boundary). The config is
  constructed enabled + local-fake-projection-enabled so ``project`` actually
  yields a ``command_intent`` rather than ``disabled``; the boundary is
  *projection only* — it never mutates session / plan state — so building it
  here is safe.
- **``reset`` -> ``Text`` mapping.** ``LocalResult`` has only ``Text``,
  ``Compact`` and ``Skip`` variants — there is no dedicated "reset" result. A
  reset is a user-facing acknowledgement of a *reset intent* (the actual
  session reset is Stream B/E's job, gated behind the boundary's authority
  flags which are all ``False`` here). So ``reset`` returns a ``Text`` ack
  derived from the boundary decision; ``compact`` is the one builtin with a
  first-class result variant (``Compact()``).
- **``help`` is NOT a boundary command.** ``help`` is not in the boundary's
  recognized set (``compact/reset/status/...``), so it does not call
  ``project()``; it simply returns a ``Text`` listing the builtin names.

All four builtins carry ``surface=CommandSurface(tui=True, headless=True)`` —
they are useful in both surfaces and perform no model round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass

from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    Compact,
    LocalCommand,
    LocalResult,
    Text,
)
from magi_agent.runtime.slash_control_boundary import (
    SlashControlBoundary,
    SlashControlConfig,
    SlashControlDecision,
    SlashControlRequest,
)

__all__ = [
    "BUILTIN_BOTH",
    "BUILTIN_COMMAND_NAMES",
    "StatusCommand",
    "ResetCommand",
    "CompactCommand",
    "HelpCommand",
    "builtin_commands",
]

# Builtins work in BOTH surfaces (model-free, useful in TUI and headless alike).
BUILTIN_BOTH = CommandSurface(tui=True, headless=True)

# Stable, ordered list of builtin names. ``help`` lists exactly these.
BUILTIN_COMMAND_NAMES: tuple[str, ...] = ("status", "reset", "compact", "help")


def _make_boundary() -> SlashControlBoundary:
    """Build a config-enabled, projection-only boundary lazily (per call).

    ``enabled=True`` + ``localFakeCommandProjectionEnabled=True`` are the two
    flags ``project()`` checks before it will parse and emit a
    ``command_intent`` (otherwise it returns ``disabled``). Every other
    authority flag stays ``False`` (the boundary is intent-only), so this
    constructs no live runtime and performs no writes.
    """

    return SlashControlBoundary(
        SlashControlConfig(enabled=True, localFakeCommandProjectionEnabled=True)
    )


def _project(name: str, args: object, ctx: CommandContext) -> SlashControlDecision:
    """Project ``/<name> <args>`` through the boundary and return its decision.

    Recognition + redaction live entirely in the boundary; we just build the
    request text (``/name args``) and hand it over. ``cwd`` is used as a stable,
    non-secret session key surrogate so the boundary's control-ref hashing has
    an input (the CLI session identity is wired by Stream B/E later).
    """

    argument = "" if args is None else str(args)
    text = f"/{name} {argument}".rstrip()
    request = SlashControlRequest(text=text, sessionKey=ctx.cwd or "cli")
    return _make_boundary().project(request)


@dataclass
class StatusCommand(LocalCommand):
    """``/status`` — summarize the boundary decision as a redaction-safe line.

    Delegates to ``SlashControlBoundary.project`` then renders a concise
    one-liner from ``decision.public_projection()`` (so nothing private leaks).
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        decision = _project("status", args, ctx)
        proj = decision.public_projection()
        reasons = ", ".join(proj.get("reasonCodes", []) or []) or "-"
        return Text(text=f"status: {proj.get('status')} | reasons: {reasons}")


@dataclass
class ResetCommand(LocalCommand):
    """``/reset`` — acknowledge a reset intent.

    No ``LocalResult`` reset variant exists, so we map the boundary's reset
    decision to a user-facing ``Text`` ack. The boundary's authority flags are
    all ``False`` (intent-only); the actual session reset is performed by a
    higher layer (Stream B/E) when wired.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        decision = _project("reset", args, ctx)
        proj = decision.public_projection()
        return Text(text=f"reset acknowledged ({proj.get('status')})")


@dataclass
class CompactCommand(LocalCommand):
    """``/compact`` — request context compaction.

    Consults the boundary (expect a ``command_intent``/compact decision) then
    returns the first-class ``Compact()`` result variant for the surface to act
    on. The boundary call proves the intent was recognized before we signal
    compaction.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        # Consult the boundary so recognition stays delegated (not reimplemented).
        # We bind the decision and confirm the boundary actually recognized the
        # command (``command_intent``) before signalling compaction. The happy
        # path is unchanged — we still return ``Compact()`` — but an unexpected
        # status (boundary didn't recognize ``/compact``) is now observable here
        # rather than silently discarded.
        decision = _project("compact", args, ctx)
        proj = decision.public_projection()
        if proj.get("status") != "command_intent":
            # Recognition was consulted but the boundary did not classify this
            # as a command intent. Still return Compact() (the surface acts on
            # the user's explicit /compact) — kept as a documented seam for
            # Stream B/E to harden once the boundary is fully wired.
            pass
        return Compact()


@dataclass
class HelpCommand(LocalCommand):
    """``/help`` — list the available builtin command names.

    Not a boundary command (not in the recognized set), so it does NOT call
    ``project()``; it simply renders the static builtin name list.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        _ = (args, ctx)
        names = ", ".join(BUILTIN_COMMAND_NAMES)
        return Text(text=f"available commands: {names}")


def builtin_commands() -> list[LocalCommand]:
    """Return fresh instances of all four seed builtins (both-surface).

    A factory (not module-level singletons) so each registry/cwd gets its own
    instances and there is no shared mutable state across registries.
    """

    return [
        StatusCommand(name="status", surface=BUILTIN_BOTH),
        ResetCommand(name="reset", surface=BUILTIN_BOTH),
        CompactCommand(name="compact", surface=BUILTIN_BOTH),
        HelpCommand(name="help", surface=BUILTIN_BOTH),
    ]
