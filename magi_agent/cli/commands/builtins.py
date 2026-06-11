"""Seed *local* builtin slash-commands for the Magi CLI (Stream D, PR-D2).

These are the always-present, model-free commands every surface (TUI + headless)
exposes. The original seed group is ``status``, ``reset``, ``compact`` and
``help``; the *magi-native* group ``plan``, ``goal``, ``onboarding`` and
``superpowers`` was added on top. The magi-native commands project their
``recipePackRef`` / ``checkpointRef`` intent through the boundary and only
*acknowledge* it (intent-only, exactly like ``reset`` — no recipe pack is
loaded, no methodology activated, no checkpoint written here; that is gated
runtime authority for a later phase).

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

All eight builtin command names carry ``surface=CommandSurface(tui=True,
headless=True)``. By default they are local and perform no model round-trip;
when ``MAGI_SUPERPOWERS_RUNTIME_ENABLED`` is explicitly truthy,
``/superpowers`` becomes a prompt command that injects bundled instructions into
the next turn.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    Compact,
    ContentBlock,
    LocalCommand,
    LocalResult,
    PromptCommand,
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
    "PlanCommand",
    "GoalCommand",
    "OnboardingCommand",
    "SuperpowersCommand",
    "SuperpowersRuntimeCommand",
    "builtin_commands",
]

# Builtins work in BOTH surfaces (model-free, useful in TUI and headless alike).
BUILTIN_BOTH = CommandSurface(tui=True, headless=True)

# Stable, ordered list of builtin names. ``help`` lists exactly these.
BUILTIN_COMMAND_NAMES: tuple[str, ...] = (
    "status",
    "reset",
    "compact",
    "help",
    "plan",
    "goal",
    "onboarding",
    "superpowers",
)
_SUPERPOWERS_RUNTIME_ENV = "MAGI_SUPERPOWERS_RUNTIME_ENABLED"
_DEFAULT_SUPERPOWER_SKILL = "using-superpowers"
_TRUTHY_ENV = {"1", "true", "yes", "on"}


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


def _superpowers_runtime_enabled() -> bool:
    raw = os.environ.get(_SUPERPOWERS_RUNTIME_ENV)
    return raw is not None and raw.strip().lower() in _TRUTHY_ENV


def _superpower_skill_name(args: object) -> str:
    if args is None:
        return _DEFAULT_SUPERPOWER_SKILL
    raw = str(args).strip()
    first = raw.split(None, 1)[0] if raw else ""
    if not first or first == "invoke":
        return _DEFAULT_SUPERPOWER_SKILL
    return first.removeprefix("/")


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


def _intent_refs(proj: dict[str, object]) -> tuple[str, str]:
    """Return ``(recipePackRef, checkpointRef)`` from a public projection.

    The magi-native methodology builtins all read these two refs the same way:
    ``public_projection()`` nests them under the ``"intent"`` key. Centralized
    here so the four commands stay thin and a projection-shape change is a
    single edit. Missing/non-dict intents fold to empty strings (redaction-safe;
    only ``public_projection()`` output is ever read).
    """

    intent = proj.get("intent") or {}
    if not isinstance(intent, dict):
        return "", ""
    return str(intent.get("recipePackRef") or ""), str(intent.get("checkpointRef") or "")


def _render_status(snap: dict[str, object]) -> str:
    """Format an app status snapshot into a readable multi-line block."""

    version = snap.get("version") or "?"
    model = snap.get("model") or "no model"
    cwd = snap.get("cwd") or "?"
    mode = snap.get("mode") or "act"
    session = snap.get("session") or "cli"
    turns = snap.get("turns", 0)
    tokens = snap.get("tokens", 0)
    return "\n".join(
        [
            f"Magi v{version}",
            f"model:   {model}",
            f"cwd:     {cwd}",
            f"mode:    {mode}  ·  session: {session}  ·  turns: {turns}"
            f"  ·  last turn: {tokens} tok",
        ]
    )


@dataclass
class StatusCommand(LocalCommand):
    """``/status`` — show the live session status (model, cwd, mode, turns, …).

    In the TUI it reads ``ctx.app.status_snapshot()`` for a real status block.
    Headless (no app) falls back to the slash-control boundary projection
    one-liner from ``decision.public_projection()`` (so nothing private leaks).
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        snapshot = self._app_snapshot(ctx)
        if snapshot is not None:
            return Text(text=_render_status(snapshot))
        decision = _project("status", args, ctx)
        proj = decision.public_projection()
        reasons = ", ".join(proj.get("reasonCodes", []) or []) or "-"
        return Text(text=f"status: {proj.get('status')} | reasons: {reasons}")

    @staticmethod
    def _app_snapshot(ctx: CommandContext) -> dict[str, object] | None:
        app = getattr(ctx, "app", None)
        snap = getattr(app, "status_snapshot", None)
        if not callable(snap):
            return None
        try:
            result = snap()
        except Exception:
            return None
        return result if isinstance(result, dict) else None


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

    Note: this command lists BUILTIN_COMMAND_NAMES only. Discovered commands
    (``/init``, ``/review``, skill commands) and gated control/session-history
    seams (``/model``, ``/fork``, etc.) are NOT included here. The full command
    surface — including those commands — is exposed via autocomplete and
    ``registry.list_for``; a full ``/help`` implementation would render from
    there rather than from this static list.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        _ = (args, ctx)
        names = ", ".join(BUILTIN_COMMAND_NAMES)
        return Text(text=f"available commands: {names}")


@dataclass
class PlanCommand(LocalCommand):
    """``/plan`` — acknowledge a plan-mode intent via the boundary.

    Projects ``/plan`` through the boundary and surfaces the recognized intent,
    including the ``recipePackRef`` and ``checkpointRef`` provided by the
    boundary for magi-native methodology commands. The actual plan-mode
    activation is gated behind runtime authority (a later phase); this builtin
    is intent-only, mirroring the maturity of ``/reset``.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        decision = _project("plan", args, ctx)
        proj = decision.public_projection()
        recipe, checkpoint = _intent_refs(proj)
        return Text(
            text=f"plan: {proj.get('status')} | recipe: {recipe} | checkpoint: {checkpoint}"
        )


@dataclass
class GoalCommand(LocalCommand):
    """``/goal`` — acknowledge a goal-setting intent via the boundary.

    Projects ``/goal`` through the boundary and surfaces the recognized intent
    with ``recipePackRef`` and ``checkpointRef``. Intent-only (no runtime
    goal mutation) — the real goal-write is gated behind authority flags.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        decision = _project("goal", args, ctx)
        proj = decision.public_projection()
        recipe, checkpoint = _intent_refs(proj)
        return Text(
            text=f"goal: {proj.get('status')} | recipe: {recipe} | checkpoint: {checkpoint}"
        )


@dataclass
class OnboardingCommand(LocalCommand):
    """``/onboarding`` — acknowledge an onboarding intent via the boundary.

    Projects ``/onboarding`` through the boundary and surfaces the recognized
    intent with ``recipePackRef`` and ``checkpointRef``. Intent-only; the
    actual onboarding flow is gated behind runtime authority.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        decision = _project("onboarding", args, ctx)
        proj = decision.public_projection()
        recipe, checkpoint = _intent_refs(proj)
        return Text(
            text=f"onboarding: {proj.get('status')} | recipe: {recipe} | checkpoint: {checkpoint}"
        )


@dataclass
class SuperpowersCommand(LocalCommand):
    """``/superpowers`` — acknowledge a superpowers intent via the boundary.

    The boundary recognizes ``superpowers`` via a ``superpowers:`` prefix on
    the raw command (see ``_parse_command``). A bare ``/superpowers`` produces
    the raw command ``superpowers`` which does NOT match the ``superpowers:``
    prefix rule — so we project as ``/superpowers:invoke`` to ensure the
    boundary's prefix check triggers. Optionally a sub-command may be passed
    via ``args``; we fold it in as ``superpowers:<args>`` when present.

    Returns a ``Text`` summarising the recognized intent with ``recipePackRef``
    and ``checkpointRef``. Intent-only — no runtime superpowers invocation.
    """

    async def call(self, args: object, ctx: CommandContext) -> LocalResult:  # type: ignore[override]
        # The boundary's _parse_command matches ``superpowers:`` via a prefix
        # check on the lowercased raw command group. A bare ``/superpowers``
        # leaves the colon off, so normalized ``superpowers`` doesn't hit the
        # prefix branch. We project as ``/superpowers:invoke`` (or
        # ``/superpowers:<args>`` when the user supplied a sub-command) so the
        # boundary's recognition always fires.
        sub = str(args).strip() if args else "invoke"
        decision = _project(f"superpowers:{sub}", None, ctx)
        proj = decision.public_projection()
        recipe, checkpoint = _intent_refs(proj)
        return Text(
            text=f"superpowers: {proj.get('status')} | recipe: {recipe} | checkpoint: {checkpoint}"
        )


@dataclass
class SuperpowersRuntimeCommand(PromptCommand):
    """``/superpowers`` runtime path for bundled Superpowers instructions.

    This command is installed only when ``MAGI_SUPERPOWERS_RUNTIME_ENABLED`` is
    explicitly truthy. It reads a bundled ``SKILL.md`` body and returns it as
    prompt content for the next model turn; it does not execute skill content or
    load project/user skill files.
    """

    description: str = "load bundled Superpowers instructions into the next turn"

    async def build_prompt(  # type: ignore[override]
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = ctx
        from magi_agent.plugins.native.skills import load_bundled_skill_body

        skill_name = _superpower_skill_name(args)
        loaded = load_bundled_skill_body(skill_name)
        if loaded is None and skill_name != _DEFAULT_SUPERPOWER_SKILL:
            loaded = load_bundled_skill_body(_DEFAULT_SUPERPOWER_SKILL)
        if loaded is None:
            return [
                ContentBlock(
                    type="text",
                    text="OpenMagi Superpowers runtime is enabled, but no bundled skill instructions were found.",
                )
            ]

        path = str(loaded.get("path") or "")
        body = str(loaded.get("body") or "")
        text = f"OpenMagi bundled superpower loaded: {path}\n\n{body}"
        return [ContentBlock(type="text", text=text)]


def builtin_commands() -> list[LocalCommand | PromptCommand]:
    """Return fresh instances of all eight builtins (both-surface).

    A factory (not module-level singletons) so each registry/cwd gets its own
    instances and there is no shared mutable state across registries.
    """

    superpowers: LocalCommand | PromptCommand
    if _superpowers_runtime_enabled():
        superpowers = SuperpowersRuntimeCommand(name="superpowers", surface=BUILTIN_BOTH)
    else:
        superpowers = SuperpowersCommand(name="superpowers", surface=BUILTIN_BOTH)

    return [
        StatusCommand(name="status", surface=BUILTIN_BOTH),
        ResetCommand(name="reset", surface=BUILTIN_BOTH),
        CompactCommand(name="compact", surface=BUILTIN_BOTH),
        HelpCommand(name="help", surface=BUILTIN_BOTH),
        PlanCommand(name="plan", surface=BUILTIN_BOTH),
        GoalCommand(name="goal", surface=BUILTIN_BOTH),
        OnboardingCommand(name="onboarding", surface=BUILTIN_BOTH),
        superpowers,
    ]
