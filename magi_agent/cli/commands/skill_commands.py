"""Skill → command bridge for the Magi CLI (Stream D, PR-D3 / P1.3).

Exposes ``skill_commands(cwd) -> list[Command]``, which discovers ``SKILL.md``
files in the standard skill scan locations and converts each into a
``SkillPromptCommand`` (a ``PromptCommand`` subclass).

Scan locations (mirrors ``magi_agent.plugins.native.skills._skill_candidates``):
1. ``<cwd>/skills/**``        (project skills directory)
2. ``<cwd>/.magi/skills/**``  (magi-local hidden skills directory)
3. ``<cwd>/docs/superpowers/**`` (superpowers doc skills)
4. ``magi_agent/skills/bundled/**`` (package-bundled skills, via importlib.resources)

For each ``SKILL.md`` found:
- Frontmatter ``name`` key → command name (fallback: parent directory name).
- Frontmatter ``description`` key → ``description`` field.
- Body (frontmatter stripped) → returned as one ``ContentBlock`` from
  ``build_prompt``.

The ``SkillPromptCommand`` is placed in the ``plugin_skills`` tier (tier 6) of
the discovery precedence stack, so a project ``.claude/commands/<name>.md``
(tier 3) with the same name will shadow the skill command. Bundled commands
(tier 1) also win over skills.

Design notes
------------
- We reuse ``_parse_frontmatter`` from ``discovery.py`` rather than duplicating
  the parser. Both live in the same package so the internal import is stable.
- At most 50 skills are loaded per on-disk location (mirrors the existing limit
  in ``plugins.native.skills``). Bundled skills use the same 50-cap.
- ``build_prompt`` performs no I/O (body captured at discovery time).
- ``agent``, ``model``, ``subtask`` from SKILL.md frontmatter are currently
  stored but not wired to execution (same data-model-only convention as P1.2).
"""

from __future__ import annotations

import importlib.resources as _resources
from dataclasses import dataclass, field
from pathlib import Path

from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    ContentBlock,
    PromptCommand,
)

__all__ = [
    "SKILL_SURFACE",
    "SkillPromptCommand",
    "skill_commands",
]

SKILL_SURFACE = CommandSurface(tui=True, headless=True)

_MAX_SKILLS_PER_LOCATION = 50


@dataclass
class SkillPromptCommand(PromptCommand):
    """A ``PromptCommand`` backed by a ``SKILL.md`` file.

    ``name``        — skill name from frontmatter or directory name.
    ``description`` — from frontmatter ``description`` key.
    ``body``        — skill body text (frontmatter stripped); returned verbatim
                      from ``build_prompt`` as one ``ContentBlock``.

    ``agent``, ``model``, ``subtask`` are parsed and stored for forward
    compatibility; execution wiring is a later PR.
    """

    description: str = ""
    agent: str = ""
    model: str = ""
    subtask: bool = False
    body: str = ""
    hints: list[str] = field(default_factory=list)

    async def build_prompt(  # type: ignore[override]
        self, args: object, ctx: CommandContext
    ) -> list[ContentBlock]:
        _ = (args, ctx)
        return [ContentBlock(type="text", text=self.body)]


def _parse_skill_md(path: Path, fallback_name: str) -> SkillPromptCommand | None:
    """Parse a ``SKILL.md`` file and return a ``SkillPromptCommand`` or None.

    Uses the internal ``_parse_frontmatter`` helper from ``discovery`` to avoid
    duplicating the frontmatter parser.
    """
    # Lazy import to keep module-level import cheap and avoid circular deps.
    from magi_agent.cli.commands.discovery import _parse_frontmatter

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    meta, body = _parse_frontmatter(raw)

    name = str(meta.get("name", "")).strip() or fallback_name
    description = str(meta.get("description", "")).strip()
    agent = str(meta.get("agent", "")).strip()
    model = str(meta.get("model", "")).strip()
    subtask = bool(meta.get("subtask", False))

    return SkillPromptCommand(
        name=name,
        surface=SKILL_SURFACE,
        description=description,
        agent=agent,
        model=model,
        subtask=subtask,
        body=body,
    )


def _scan_dir(base: Path) -> list[SkillPromptCommand]:
    """Scan ``base`` recursively for ``SKILL.md`` files (up to the cap).

    Returns skills sorted by the SKILL.md path for deterministic ordering.
    """
    if not base.exists() or not base.is_dir():
        return []
    skills: list[SkillPromptCommand] = []
    for skill_md in sorted(base.rglob("SKILL.md"))[:_MAX_SKILLS_PER_LOCATION]:
        fallback = skill_md.parent.name
        cmd = _parse_skill_md(skill_md, fallback)
        if cmd is not None:
            skills.append(cmd)
    return skills


def _bundled_skill_commands() -> list[SkillPromptCommand]:
    """Discover skills from the ``magi_agent/skills/bundled`` package tree.

    Uses ``importlib.resources`` so this works whether the package is installed
    as a wheel or run from source.
    """
    try:
        bundled_root = _resources.files("magi_agent").joinpath("skills").joinpath("bundled")
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        return []

    # importlib.resources Traversable — convert to Path if possible for rglob.
    try:
        bundled_path = Path(str(bundled_root))
    except Exception:
        return []

    return _scan_dir(bundled_path)


def skill_commands(cwd: str) -> list[SkillPromptCommand]:
    """Discover SKILL.md files in the standard scan locations for ``cwd``.

    Merges commands from bundled skills and on-disk locations in the following
    order (bundled first, then on-disk by location):
    1. ``magi_agent/skills/bundled/**``
    2. ``<cwd>/skills/**``
    3. ``<cwd>/.magi/skills/**``
    4. ``<cwd>/docs/superpowers/**``

    Duplicate skill names are NOT deduplicated here — the discovery layer's
    ``_merge_dedup`` handles that at the ``plugin_skills`` tier level. (In
    practice, duplicate names within a tier are rare and the first-seen wins
    in the registry.)
    """
    root = Path(cwd)
    out: list[SkillPromptCommand] = []

    # Bundled skills (package-bundled, always available).
    out.extend(_bundled_skill_commands())

    # On-disk locations.
    for subdir in (
        root / "skills",
        root / ".magi" / "skills",
        root / "docs" / "superpowers",
    ):
        out.extend(_scan_dir(subdir))

    return out
