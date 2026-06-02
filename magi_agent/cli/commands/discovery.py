"""Command discovery + precedence merge for the Magi CLI (Stream D, PR-D2).

``discover_commands(cwd)`` produces the deduped, precedence-ordered list of
``Command`` objects the registry is built from. ``build_registry(cwd)`` wires
that list into a ``CommandRegistryImpl``; ``install_discovery()`` makes
``build_registry`` the default per-cwd builder for ``get_registry``.

Importing this module is cheap and side-effect-free: it touches NO event loop,
runs NO filesystem scan, and (critically) does NOT call
``set_registry_builder`` at import time. The caller invokes
``install_discovery()`` explicitly when it wants discovery wired in.

Precedence (design §5)
----------------------
Sources are merged in this order; the FIRST occurrence of a name WINS (later
sources with the same name are *shadowed*/dropped)::

    1. bundled
    2. builtin-plugin
    3. skill-dir (.claude/skills / Magi equiv)
    4. workflow
    5. plugin
    6. plugin-skills
    7. builtins (builtin_commands())

For D2, on-disk sources 1/2/4/5/6 are forward-compatible seams (empty by
default, injectable for tests). Two sources are *real*:

- **Source 3 (skill-dir):** markdown command discovery. We scan
  ``<cwd>/.claude/commands/*.md`` and load each file as a ``PromptCommand``
  whose ``name`` is the file stem and whose ``build_prompt`` returns the file's
  text as a single ``ContentBlock``. We place markdown commands at the
  *skill-dir* tier (3) — project-local command files are conceptually the
  project's skill directory, and putting them ABOVE builtins (7) lets a project
  intentionally shadow a builtin name, while keeping them BELOW true bundled /
  builtin-plugin commands.
- **Source 7 (builtins):** always included via ``builtin_commands()``.

The dedup/shadow is made *explicit* here (we don't merely lean on the
registry's first-wins ``register``): ``_merge_dedup`` builds the deduped list by
precedence so the shadowing is itself testable independent of the registry.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from magi_agent.cli.commands.builtins import builtin_commands
from magi_agent.cli.commands.registry import CommandRegistryImpl
from magi_agent.cli.commands.registry import (
    set_registry_builder as _set_registry_builder,
)
from magi_agent.cli.contracts import (
    Command,
    CommandContext,
    CommandSurface,
    ContentBlock,
    PromptCommand,
)

__all__ = [
    "MarkdownPromptCommand",
    "DiscoverySources",
    "discover_commands",
    "markdown_commands",
    "build_registry",
    "install_discovery",
]

# Markdown project commands are usable in both surfaces (they expand into a
# model prompt; nothing surface-specific about that).
_MARKDOWN_SURFACE = CommandSurface(tui=True, headless=True)

# Default relative dir scanned for project markdown commands (Claude-compatible).
_MARKDOWN_COMMANDS_DIR = os.path.join(".claude", "commands")


@dataclass
class MarkdownPromptCommand(PromptCommand):
    """A ``PromptCommand`` whose prompt is the verbatim text of a markdown file.

    ``name`` = file stem; ``build_prompt`` returns the file text as one
    ``ContentBlock``. The text is captured at discovery time (read once) so
    ``build_prompt`` performs no I/O.
    """

    text: str = ""

    async def build_prompt(  # type: ignore[override]
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = (args, ctx)
        return [ContentBlock(type="text", text=self.text)]


@dataclass
class DiscoverySources:
    """Injectable, forward-compatible source buckets in precedence order.

    Each list is a tier from the precedence order. ``skill_dir`` is populated
    from on-disk markdown by ``discover_commands``; the rest default empty and
    exist as seams so D2 stays forward-compatible (Streams E/F / future PRs fill
    bundled / workflow / plugin tiers without changing the merge logic). Tests
    inject these directly to exercise shadowing deterministically.
    """

    bundled: list[Command] = field(default_factory=list)
    builtin_plugin: list[Command] = field(default_factory=list)
    skill_dir: list[Command] = field(default_factory=list)
    workflow: list[Command] = field(default_factory=list)
    plugin: list[Command] = field(default_factory=list)
    plugin_skills: list[Command] = field(default_factory=list)
    builtins: list[Command] = field(default_factory=list)

    def ordered(self) -> list[list[Command]]:
        """Return the source buckets in precedence order (highest first)."""

        return [
            self.bundled,
            self.builtin_plugin,
            self.skill_dir,
            self.workflow,
            self.plugin,
            self.plugin_skills,
            self.builtins,
        ]


def _merge_dedup(sources: Sequence[Sequence[Command]]) -> list[Command]:
    """Merge ``sources`` (already in precedence order) deduping by name.

    Iterates highest-precedence source first; the FIRST command seen for a name
    wins and any later same-named command is shadowed (dropped). The shadow is
    explicit here so it is testable independent of the registry's own
    first-wins ``register``.
    """

    seen: set[str] = set()
    merged: list[Command] = []
    for bucket in sources:
        for command in bucket:
            if command.name in seen:
                continue  # shadowed by a higher-precedence source
            seen.add(command.name)
            merged.append(command)
    return merged


def markdown_commands(cwd: str) -> list[Command]:
    """Load ``<cwd>/.claude/commands/*.md`` as ``MarkdownPromptCommand`` objects.

    Returns an empty list if the dir is absent. Files are sorted by name for a
    deterministic order. Each file's text is read once and captured on the
    command (``build_prompt`` does no I/O).
    """

    commands_dir = os.path.join(cwd, _MARKDOWN_COMMANDS_DIR)
    if not os.path.isdir(commands_dir):
        return []
    out: list[Command] = []
    for entry in sorted(os.listdir(commands_dir)):
        if not entry.endswith(".md"):
            continue
        path = os.path.join(commands_dir, entry)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        name = entry[: -len(".md")]
        out.append(
            MarkdownPromptCommand(name=name, surface=_MARKDOWN_SURFACE, text=text)
        )
    return out


def discover_commands(
    cwd: str, *, sources: DiscoverySources | None = None
) -> list[Command]:
    """Discover all commands for ``cwd`` as a deduped, precedence-ordered list.

    Composes the precedence sources (§5) and merges them with explicit shadow
    semantics. On-disk markdown commands populate the *skill-dir* tier; builtins
    are always appended at the lowest tier. Pass ``sources`` to inject other
    tiers (tests / future PRs); whatever the caller injects is merged at its
    tier — but markdown discovery + builtins are filled in if those tiers are
    left empty.
    """

    src = sources if sources is not None else DiscoverySources()
    # Fill the two *real* tiers when the caller did not pre-populate them.
    # Compute into LOCALS — never mutate the caller-supplied ``sources``. A
    # caller may reuse one ``DiscoverySources`` instance across different cwds;
    # writing ``skill_dir``/``builtins`` back onto ``src`` would cache the first
    # cwd's scan and make the second call silently return the first cwd's
    # commands. The ordered bucket list is built inline from ``src``'s other
    # tiers + these locals so precedence (§5) is unchanged.
    skill = list(src.skill_dir) if src.skill_dir else list(markdown_commands(cwd))
    builtins_list = list(src.builtins) if src.builtins else list(builtin_commands())
    ordered = [
        src.bundled,
        src.builtin_plugin,
        skill,
        src.workflow,
        src.plugin,
        src.plugin_skills,
        builtins_list,
    ]
    return _merge_dedup(ordered)


def build_registry(cwd: str) -> CommandRegistryImpl:
    """Build a ``CommandRegistryImpl`` from ``discover_commands(cwd)``.

    Registers the already-deduped discovered commands. Registry ``register`` is
    also first-wins, so this composes safely even though the list is already
    deduped (belt-and-suspenders; the explicit merge is the canonical shadow).
    """

    registry = CommandRegistryImpl()
    for command in discover_commands(cwd):
        registry.register(command)
    return registry


def install_discovery() -> None:
    """Wire ``build_registry`` as the default per-cwd builder for ``get_registry``.

    Explicit, NOT done at import time: importing this module must have no side
    effects (no builder swap, no cache clear). The caller invokes this once at
    startup when it wants real discovery; ``set_registry_builder`` clears the
    per-cwd cache so subsequent ``get_registry`` calls use discovery.
    """

    _set_registry_builder(build_registry)
