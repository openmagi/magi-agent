"""Command discovery + precedence merge for the Magi CLI (Stream D, PR-D2/D3).

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

For D3, tier 1 (bundled) and tier 6 (plugin-skills) are now real:
- **Tier 1 (bundled):** ``bundled_commands()`` from ``bundled.py`` — first-party
  commands shipped with the package (``/init``, ``/review``). Cannot be
  shadowed by project or skill files.
- **Tier 3 (skill-dir):** markdown command discovery. We scan
  ``<cwd>/.claude/commands/*.md`` and load each file as a ``PromptCommand``
  whose ``name`` is the file stem. Markdown commands now support optional YAML
  frontmatter (``description``, ``agent``, ``model``, ``subtask``) and argument
  substitution (``$1``..$N``, ``$ARGUMENTS``) at ``build_prompt`` time.
- **Tier 6 (plugin-skills):** ``skill_commands(cwd)`` from ``skill_commands.py``
  — discovers ``SKILL.md`` files in standard skill locations and exposes each as
  a ``PromptCommand``. Shadowed by project skill-dir files (tier 3).
- **Tier 7 (builtins):** always included via ``builtin_commands()``.

The dedup/shadow is made *explicit* here (we don't merely lean on the
registry's first-wins ``register``): ``_merge_dedup`` builds the deduped list
by precedence so the shadowing is itself testable independent of the registry.

P1.2 — Markdown frontmatter + argument substitution
----------------------------------------------------
``MarkdownPromptCommand`` now carries optional metadata fields parsed from YAML
frontmatter (``description``, ``agent``, ``model``, ``subtask``) and a
``hints`` list computed from placeholder tokens in the body (``$1``..$N`` sorted
+ ``$ARGUMENTS`` appended if present). The frontmatter block is stripped from
the captured body text; a file with no frontmatter behaves exactly as before.

Argument substitution at ``build_prompt`` time:
- ``$ARGUMENTS`` → the full raw argument string (``""`` when args is None).
- ``$1``, ``$2``, … ``$N`` → whitespace-split positional tokens (1-indexed;
  missing positionals substitute to ``""``.

Back-compat guarantee: a ``.md`` file with no frontmatter and no placeholders
returns its verbatim text as one ``ContentBlock``, identical to the D2
behaviour.
"""

from __future__ import annotations

import os
import re
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

# Regex to match YAML frontmatter block at the start of a markdown file.
_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\n---\r?\n?", re.DOTALL)

# Regex to detect placeholder tokens for hints computation.
_POSITIONAL_RE = re.compile(r"\$([1-9][0-9]*)")
_ARGUMENTS_TOKEN = "$ARGUMENTS"

# Single-pass substitution regex: matches $ARGUMENTS or $N (N >= 1).
_TOKEN_RE = re.compile(r"\$ARGUMENTS|\$([1-9][0-9]*)")


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse an optional YAML frontmatter block from ``text``.

    Returns ``(meta, body)`` where ``meta`` is a dict of parsed keys and
    ``body`` is the text with the frontmatter block stripped. If there is no
    frontmatter block, ``meta`` is empty and ``body`` equals ``text``.

    We use a minimal hand-written parser to avoid a ``pyyaml`` hard dependency.
    Only simple scalar string/bool values on the top level are needed
    (``description``, ``agent``, ``model``, ``subtask``). Complex YAML is not
    required by the spec.
    """
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        return {}, text

    fm_content = m.group(1)
    body = text[m.end():]

    meta: dict[str, object] = {}
    for line in fm_content.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Coerce known boolean fields.
        if value.lower() == "true":
            meta[key] = True
        elif value.lower() == "false":
            meta[key] = False
        else:
            # Strip optional surrounding quotes.
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            meta[key] = value

    return meta, body


def _compute_hints(text: str) -> list[str]:
    """Compute the placeholder hints list from ``text``.

    Collects distinct ``$1`` .. ``$N`` tokens (sorted numerically), then
    appends ``$ARGUMENTS`` if that literal appears in the text.

    Example: ``"do $2 then $1 using $ARGUMENTS"`` → ``["$1", "$2", "$ARGUMENTS"]``.
    """
    positionals_seen: set[int] = set()
    for m in _POSITIONAL_RE.finditer(text):
        positionals_seen.add(int(m.group(1)))
    hints: list[str] = [f"${n}" for n in sorted(positionals_seen)]
    if _ARGUMENTS_TOKEN in text:
        hints.append(_ARGUMENTS_TOKEN)
    return hints


def _substitute_args(text: str, args: object) -> str:
    """Substitute ``$ARGUMENTS`` and ``$1``..``$N`` placeholders in ``text``.

    ``args`` may be a string or None; ``None`` is treated as ``""``.

    - ``$ARGUMENTS`` → full raw argument string.
    - ``$1`` → first whitespace-split token (empty string if absent).
    - ``$N`` → Nth token (empty string if absent).

    A single left-to-right pass is used (via ``_TOKEN_RE``) so that
    substituted output is never re-scanned. This means ``$1``-like text
    inside the user's argument string is not mistakenly re-substituted.
    """
    arg_str = "" if args is None else str(args)
    tokens = arg_str.split()

    def _repl(m: re.Match) -> str:  # type: ignore[type-arg]
        if m.group(1) is None:  # matched $ARGUMENTS
            return arg_str
        idx = int(m.group(1)) - 1  # 1-indexed -> 0-indexed
        return tokens[idx] if idx < len(tokens) else ""

    return _TOKEN_RE.sub(_repl, text)


@dataclass
class MarkdownPromptCommand(PromptCommand):
    """A ``PromptCommand`` whose prompt is the (possibly arg-substituted) text of
    a markdown file.

    ``name`` = file stem; ``text`` = body text with frontmatter stripped.
    ``build_prompt`` substitutes argument placeholders and returns the result as
    one ``ContentBlock``. The text is captured at discovery time (read once) so
    ``build_prompt`` performs no I/O.

    Optional metadata fields parsed from YAML frontmatter:
    - ``description`` — human-readable description for autocomplete display.
    - ``agent`` — agent hint (parsed, not wired to execution in this PR).
    - ``model`` — model hint (parsed, not wired in this PR).
    - ``subtask`` — whether the command should run in a child runner.

    ``hints`` — list of placeholder tokens found in ``text`` (``$1``..$N`` sorted
    + ``$ARGUMENTS`` if present), computed once at load time.

    Backward compat: a file with no frontmatter and no placeholders behaves
    exactly as before — verbatim text as one ``ContentBlock``.
    """

    text: str = ""
    description: str = ""
    agent: str = ""
    model: str = ""
    subtask: bool = False
    hints: list[str] = field(default_factory=list)

    async def build_prompt(  # type: ignore[override]
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = ctx
        substituted = _substitute_args(self.text, args)
        return [ContentBlock(type="text", text=substituted)]


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

    P1.2: optional YAML frontmatter is parsed and stripped from the body.
    Argument substitution placeholders are collected into ``hints``.
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
            raw = handle.read()
        meta, body = _parse_frontmatter(raw)
        name = entry[: -len(".md")]
        hints = _compute_hints(body)
        out.append(
            MarkdownPromptCommand(
                name=name,
                surface=_MARKDOWN_SURFACE,
                text=body,
                description=str(meta.get("description", "")),
                agent=str(meta.get("agent", "")),
                model=str(meta.get("model", "")),
                subtask=bool(meta.get("subtask", False)),
                hints=hints,
            )
        )
    return out


def discover_commands(
    cwd: str, *, sources: DiscoverySources | None = None
) -> list[Command]:
    """Discover all commands for ``cwd`` as a deduped, precedence-ordered list.

    Composes the precedence sources (§5) and merges them with explicit shadow
    semantics. On-disk markdown commands populate the *skill-dir* tier; bundled
    commands populate tier 1; skill commands populate tier 6; builtins are
    always appended at the lowest tier.

    Pass ``sources`` to inject other tiers (tests / future PRs); whatever the
    caller injects is merged at its tier — but markdown discovery, bundled
    commands, skill commands, and builtins are filled in if those tiers are
    left empty.
    """
    # Lazy imports inside the function body to keep module import cheap and
    # side-effect-free; these modules are only needed when discovery actually
    # runs.
    from magi_agent.cli.commands.bundled import bundled_commands
    from magi_agent.cli.commands.skill_commands import skill_commands

    src = sources if sources is not None else DiscoverySources()
    # Fill the real tiers when the caller did not pre-populate them.
    # Compute into LOCALS — never mutate the caller-supplied ``sources``. A
    # caller may reuse one ``DiscoverySources`` instance across different cwds;
    # writing tiers back onto ``src`` would cache the first cwd's scan and make
    # the second call silently return the first cwd's commands.
    bundled_list = list(src.bundled) if src.bundled else list(bundled_commands())
    skill = list(src.skill_dir) if src.skill_dir else list(markdown_commands(cwd))
    plugin_skills_list = (
        list(src.plugin_skills) if src.plugin_skills else list(skill_commands(cwd))
    )
    builtins_list = list(src.builtins) if src.builtins else list(builtin_commands())
    ordered = [
        bundled_list,
        src.builtin_plugin,
        skill,
        src.workflow,
        src.plugin,
        plugin_skills_list,
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
