"""Bundled slash-commands for the Magi CLI (Stream D, PR-D3 / P1.1).

These are first-party commands shipped with the magi-agent package. They live
in tier 1 of the discovery precedence stack (``bundled``) and therefore are
NEVER shadowed by project ``.claude/commands/*.md`` files or skills.

Currently exposes:
- ``InitCommand`` (``/init``) — guided AGENTS.md setup.
- ``ReviewCommand`` (``/review``) — review changes (commit|branch|pr, defaults
  to uncommitted); tagged ``subtask=True`` (intended to run in a child runner).

Template files live in ``magi_agent/cli/commands/templates/``. Each template
is read once at module import time (via ``importlib.resources``) and captured
as a class-level constant so ``build_prompt`` performs no I/O.

Design notes
------------
- ``${path}`` in a template is substituted with ``ctx.cwd`` at ``build_prompt``
  time. This is the only substitution done for bundled commands; richer
  argument substitution lives in ``MarkdownPromptCommand`` (P1.2).
- ``description``, ``subtask`` are stored as instance fields on the subclasses
  for forward compatibility with autocomplete / execution wiring (later PRs).
  We do NOT add them to the frozen ``contracts.PromptCommand`` base dataclass
  to avoid touching the shared interface surface.
- ``surface = CommandSurface(tui=True, headless=True)`` — bundled commands work
  in both surfaces.
"""

from __future__ import annotations

import importlib.resources as _resources
from dataclasses import dataclass, field

from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    ContentBlock,
    PromptCommand,
)

__all__ = [
    "BUNDLED_SURFACE",
    "InitCommand",
    "ReviewCommand",
    "bundled_commands",
]

BUNDLED_SURFACE = CommandSurface(tui=True, headless=True)


def _load_template(filename: str) -> str:
    """Read a template file from this package's ``templates/`` directory.

    Uses ``importlib.resources`` so the template is found correctly whether the
    package is installed as a wheel or run from source.
    """
    pkg = _resources.files("magi_agent.cli.commands") / "templates" / filename
    return pkg.read_text(encoding="utf-8")


# Load templates once at module import (no I/O at build_prompt time).
_INIT_TEMPLATE: str = _load_template("initialize.txt")
_REVIEW_TEMPLATE: str = _load_template("review.txt")


@dataclass
class InitCommand(PromptCommand):
    """``/init`` — guided AGENTS.md setup.

    Returns the ``initialize.txt`` template with ``${path}`` replaced by
    ``ctx.cwd``.
    """

    description: str = "guided AGENTS.md setup"
    subtask: bool = False

    async def build_prompt(  # type: ignore[override]
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = args
        text = _INIT_TEMPLATE.replace("${path}", ctx.cwd)
        return [ContentBlock(type="text", text=text)]


@dataclass
class ReviewCommand(PromptCommand):
    """``/review`` — review changes [commit|branch|pr], defaults to uncommitted.

    ``subtask=True`` marks this command as intended to run in a child runner.
    The execution wiring for subtask is a later PR; the field is the data model.
    """

    description: str = "review changes [commit|branch|pr], defaults to uncommitted"
    subtask: bool = True

    async def build_prompt(  # type: ignore[override]
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = args
        text = _REVIEW_TEMPLATE.replace("${path}", ctx.cwd)
        return [ContentBlock(type="text", text=text)]


def bundled_commands() -> list[PromptCommand]:
    """Return fresh instances of all bundled commands.

    A factory (not module-level singletons) so tests / multiple registries each
    get independent instances with no shared mutable state.
    """
    return [
        InitCommand(name="init", surface=BUNDLED_SURFACE),
        ReviewCommand(name="review", surface=BUNDLED_SURFACE),
    ]
