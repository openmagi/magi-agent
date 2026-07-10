"""Transport-agnostic slash-to-skill resolver for hosted and CLI surfaces.

Resolves a leading ``/skill-name ...`` chat message to an installed skill's
SKILL.md body (or a miss), reusing the SkillLoader candidate scan so
slash-activation and SkillLoader can never disagree about which skills exist.

This module is pure library with no callers; it has no transport-layer imports.
Layering: the SkillLoader candidate scan and body readers live in
``plugins.native.skills`` and are reused here via function-scoped (lazy) imports
so that no top-level ``runtime -> plugins`` package edge is introduced; the
frontmatter parser is a minimal self-contained copy (see ``_parse_frontmatter``)
kept local so that ``runtime`` takes no dependency on ``cli``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Mirror of ``plugins.native.skills._LEGACY_WORKSPACE_SKILL_PREFIX``, kept local
# so path-prefix checks need no import of the plugins layer. The scan and body
# readers themselves are imported lazily at their call sites.
_LEGACY_WORKSPACE_SKILL_PREFIX = "legacy-workspace/skills"

__all__ = [
    "RESERVED_COMMAND_NAMES",
    "SkillSlashActivation",
    "SkillSlashMiss",
    "parse_slash",
    "resolve_skill_slash",
]

# Reserved names that can never be overridden by an installed skill.
# Mirrors dashboard C12 reserved builtins plus CLI builtins.
RESERVED_COMMAND_NAMES: frozenset[str] = frozenset(
    {"reset", "status", "compact", "help", "init", "review"}
)

# Prefix on custom skill directory names (custom-<slug>) as synced from the
# dashboard; the SKILL.md frontmatter name is the clean slug without it.
_CUSTOM_PREFIX = "custom-"

# Regex to collapse consecutive hyphens for normalisation.
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")


@dataclass(frozen=True)
class SkillSlashActivation:
    """Successful resolution of a ``/skill-name`` invocation."""

    skill_name: str
    """Canonical resolved name (frontmatter ``name`` field, or dir name)."""

    invoked_token: str
    """Token the user typed after the ``/``."""

    source_path: str
    """Workspace-relative SKILL.md path (e.g. ``skills/foo/SKILL.md``)."""

    source: str
    """One of ``"workspace"`` | ``"bundled"`` | ``"legacy_workspace"``."""

    body: str
    """Frontmatter-stripped body, capped at ``max_body_chars``."""

    truncated: bool
    """``True`` when the body was cut to fit ``max_body_chars``."""

    residual_text: str
    """User text after the command token (empty string when none)."""


@dataclass(frozen=True)
class SkillSlashMiss:
    """No installed skill matched the invoked token."""

    invoked_token: str
    residual_text: str
    near_matches: tuple[str, ...]
    """Up to 3 candidate display names that are close to the invoked token."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_slash(text: str) -> tuple[str, str] | None:
    """Return ``(token, residual)`` when ``text`` starts with ``/token``.

    Returns ``None`` when the text does not start with ``/`` or the token is
    empty.  Semantics match ``cli/headless._parse_slash``: strip the leading
    ``/``, split on whitespace once.
    """
    if not text.startswith("/"):
        return None
    body = text[1:]  # strip leading "/"
    parts = body.split(None, 1)
    token = parts[0] if parts else ""
    residual = parts[1] if len(parts) > 1 else ""
    if not token:
        return None
    return token, residual


def resolve_skill_slash(
    text: str,
    *,
    workspace_root: Path,
    max_body_chars: int,
    reserved_names: frozenset[str] = RESERVED_COMMAND_NAMES,
) -> SkillSlashActivation | SkillSlashMiss | None:
    """Resolve a ``/skill-name ...`` message to an installed skill.

    Returns:
    - ``SkillSlashActivation`` when a skill is found.
    - ``SkillSlashMiss`` when the text starts with ``/`` but no skill matches.
    - ``None`` when the text does not start with ``/``, the token is empty, or
      the token is in ``reserved_names``.

    The candidate set is exactly ``_skill_candidates(workspace_root)`` so this
    surface and SkillLoader always agree about which skills are installed (P4).
    """
    parsed = parse_slash(text)
    if parsed is None:
        return None
    token, residual = parsed
    token_lower = token.lower()
    if token_lower in reserved_names:
        return None

    # Lazy import keeps the SkillLoader scan as the single source of truth (P4)
    # without a top-level runtime -> plugins package edge.
    from magi_agent.plugins.native.skills import _skill_candidates  # noqa: PLC0415

    candidates = _skill_candidates(workspace_root)
    token_norm = _normalize(token)

    # Build the display name set for near-match scoring (populated lazily as
    # we walk; we need them for the miss path).
    display_names: list[str] = []

    # Walk the resolution ladder. We iterate candidates in scan order and
    # track the best (lowest-rung) match for a deterministic first-match result.
    best: _Match | None = None

    for relative in candidates:
        # Determine canonical dir name (the leaf directory containing SKILL.md).
        dir_name = _dir_name_from_relative(relative)
        if dir_name is None:
            continue
        dir_norm = _normalize(dir_name)

        # Rung 1: directory name == token (case-insensitive, NFC normalized).
        if dir_norm == token_norm:
            if best is None or best.rung > 1:
                best = _Match(rung=1, relative=relative, dir_name=dir_name, fm_name=None)
            if best.rung == 1:
                # First rung-1 match wins across bases; we can break early only if
                # we already have rung 1 and this candidate is for the same base
                # or a later one - but since best is set and rung matches, we keep
                # scanning to respect first-hit-wins within the same rung by
                # candidate order (first candidate in scan order already stored).
                pass
            continue  # display_names populated below after rung determination

        # Rungs 2 and 3 require frontmatter - read it lazily.
        fm = _read_frontmatter_name(workspace_root, relative)
        fm_norm = _normalize(fm) if fm else None

        # Rung 2: frontmatter name == token.
        if fm_norm is not None and fm_norm == token_norm:
            if best is None or best.rung > 2:
                best = _Match(rung=2, relative=relative, dir_name=dir_name, fm_name=fm)
            continue

        # Rung 3: either direction of one leading "custom-" stripped.
        #   a) dir name with one "custom-" stripped == token
        #   b) token with one "custom-" stripped == dir name OR frontmatter name
        dir_stripped_norm = _strip_custom_once_norm(dir_name)
        token_stripped_norm = _strip_custom_once_norm(token)
        match3 = False
        if dir_stripped_norm is not None and dir_stripped_norm == token_norm:
            match3 = True
        if token_stripped_norm is not None and (
            token_stripped_norm == dir_norm
            or (fm_norm is not None and token_stripped_norm == fm_norm)
        ):
            match3 = True
        if match3:
            if best is None or best.rung > 3:
                best = _Match(rung=3, relative=relative, dir_name=dir_name, fm_name=fm)

        # Accumulate display names for near-match scoring regardless of ladder result.
        # (We add below after candidate fully processed.)

    # Populate display_names for near-match scoring (second pass if we need it).
    # For efficiency, only build if best is None (miss path).
    if best is None:
        for relative in candidates:
            dn = _dir_name_from_relative(relative)
            if dn:
                display_names.append(dn)
            fm = _read_frontmatter_name(workspace_root, relative)
            if fm:
                display_names.append(fm)
        near = _near_matches(token, display_names)
        return SkillSlashMiss(
            invoked_token=token,
            residual_text=residual,
            near_matches=tuple(near),
        )

    # Read the body via the existing helpers (inherits 64k cap + traversal checks).
    body_dict = _read_body(workspace_root, best.relative)
    if body_dict is None:
        # File not readable; treat as miss.
        for relative in candidates:
            dn = _dir_name_from_relative(relative)
            if dn:
                display_names.append(dn)
            fm = _read_frontmatter_name(workspace_root, relative)
            if fm:
                display_names.append(fm)
        near = _near_matches(token, display_names)
        return SkillSlashMiss(
            invoked_token=token,
            residual_text=residual,
            near_matches=tuple(near),
        )

    raw_body: str = str(body_dict.get("body", ""))
    source: str = str(body_dict.get("source", "workspace"))

    # Strip frontmatter from body (the loader does not strip it for us).
    _, body_stripped = _parse_frontmatter_lazy(raw_body)

    truncated = False
    if len(body_stripped) > max_body_chars:
        body_stripped = body_stripped[:max_body_chars]
        truncated = True

    # Canonical skill name: frontmatter name if available, else dir name.
    skill_name = best.fm_name if best.fm_name else best.dir_name

    return SkillSlashActivation(
        skill_name=skill_name,
        invoked_token=token,
        source_path=best.relative,
        source=source,
        body=body_stripped,
        truncated=truncated,
        residual_text=residual,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _Match:
    rung: int  # 1, 2, or 3
    relative: str  # candidate relative path
    dir_name: str  # dir name of the skill
    fm_name: str | None  # frontmatter name if already read


def _normalize(s: str) -> str:
    """NFC normalize, lowercase, collapse consecutive hyphens."""
    s = unicodedata.normalize("NFC", s).lower()
    s = _MULTI_HYPHEN_RE.sub("-", s)
    return s


def _dir_name_from_relative(relative: str) -> str | None:
    """Extract the skill directory name from a candidate relative path."""
    # Candidate paths look like:
    #   "skills/custom-foo/SKILL.md"                 (workspace base)
    #   "bundled/superpowers/bar/SKILL.md"            (bundled)
    #   "legacy-workspace/skills/baz/SKILL.md"        (legacy)
    if not relative.endswith("/SKILL.md"):
        return None
    parent = relative[: -len("/SKILL.md")]
    return Path(parent).name or None


def _strip_custom_once_norm(s: str) -> str | None:
    """Return the normalised string with one leading ``custom-`` stripped, or None."""
    s_norm = _normalize(s)
    prefix = "custom-"
    if s_norm.startswith(prefix):
        return s_norm[len(prefix):]
    return None


def _read_frontmatter_name(workspace_root: Path, relative: str) -> str | None:
    """Read just the ``name`` field from a SKILL.md frontmatter. No caching."""
    try:
        if relative.startswith("bundled/"):
            from importlib import resources as _res
            skills_root = _res.files("magi_agent").joinpath("skills")
            resource = skills_root.joinpath(*relative.split("/"))
            if not resource.is_file():
                return None
            raw = resource.read_text(encoding="utf-8")
        elif relative.startswith(f"{_LEGACY_WORKSPACE_SKILL_PREFIX}/"):
            inner = relative[len(f"{_LEGACY_WORKSPACE_SKILL_PREFIX}/"):]
            base = workspace_root.parent / "skills"
            if not base.is_dir():
                return None
            path = (base / inner).resolve()
            raw = path.read_text(encoding="utf-8")
        else:
            path = (workspace_root / relative).resolve()
            raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None

    meta, _ = _parse_frontmatter_lazy(raw)
    name = str(meta.get("name", "")).strip()
    return name if name else None


_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\n---\r?\n?", re.DOTALL)


def _parse_frontmatter_lazy(text: str) -> tuple[dict[str, object], str]:
    """Minimal frontmatter parser.

    Self-contained mirror of ``cli.commands.discovery._parse_frontmatter`` kept
    local so ``runtime`` takes no dependency on ``cli``. Returns ``(meta, body)``
    where ``meta`` holds simple top-level scalar keys and ``body`` is the text
    with the frontmatter block stripped. Byte-identical behaviour to the CLI
    parser for the keys this resolver reads (``name``).
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text

    fm_content = match.group(1)
    body = text[match.end():]

    meta: dict[str, object] = {}
    for line in fm_content.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.lower() == "true":
            meta[key] = True
        elif value.lower() == "false":
            meta[key] = False
        else:
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            meta[key] = value

    return meta, body


def _read_body(workspace_root: Path, relative: str) -> dict[str, object] | None:
    """Read skill body via the appropriate loader helper.

    The body readers live in ``plugins.native.skills``; they are imported lazily
    here so no top-level ``runtime -> plugins`` package edge is introduced while
    still reusing the exact SkillLoader read path (path-traversal and
    protected-path checks included).
    """
    from magi_agent.plugins.native.skills import (  # noqa: PLC0415
        _read_bundled_skill_body,
        _read_legacy_workspace_skill_body,
        _read_workspace_skill_body,
    )

    if relative.startswith("bundled/"):
        return _read_bundled_skill_body(relative)
    elif relative.startswith(f"{_LEGACY_WORKSPACE_SKILL_PREFIX}/"):
        return _read_legacy_workspace_skill_body(workspace_root, relative)
    else:
        return _read_workspace_skill_body(workspace_root, relative)


# ---------------------------------------------------------------------------
# Near-match scoring (Levenshtein <= 2 or prefix match)
# ---------------------------------------------------------------------------

_MAX_NEAR_MATCHES = 3


def _near_matches(token: str, display_names: list[str]) -> list[str]:
    """Return up to 3 near matches (prefix or Levenshtein distance <= 2)."""
    token_norm = _normalize(token)
    seen: set[str] = set()
    results: list[str] = []
    for name in display_names:
        if not name or name in seen:
            continue
        name_norm = _normalize(name)
        if name_norm == token_norm:
            # Exact match would not be a miss; skip.
            continue
        if name_norm.startswith(token_norm) or token_norm.startswith(name_norm):
            seen.add(name)
            results.append(name)
        elif _levenshtein(token_norm, name_norm) <= 2:
            seen.add(name)
            results.append(name)
        if len(results) >= _MAX_NEAR_MATCHES:
            break
    return results[:_MAX_NEAR_MATCHES]


def _levenshtein(a: str, b: str) -> int:
    """Compute the Levenshtein edit distance between two strings.

    Classic DP implementation; no external dependencies.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la

    # Use two rows to keep memory O(min(la, lb)).
    if la < lb:
        a, b = b, a
        la, lb = lb, la

    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,       # insertion
                prev[j] + 1,           # deletion
                prev[j - 1] + cost,    # substitution
            )
        prev, curr = curr, prev
    return prev[lb]
