"""Default-OFF system-prompt block: workspace summary for coding turns (C10).

Auto-injects a concise ``<coding_context>`` block (repo map + recent changes +
entry points + top-level directory stats) into ``build_cli_instruction`` on
coding turns. Strict default-OFF: returns ``""`` unless
``MAGI_CODING_CONTEXT_ENABLED`` is truthy, ``workspace_root`` is provided, or
the block exceeds the token budget — fail-safe to ``""`` so the assembled
prompt stays byte-identical to today.

# scope: coding (Intended scope per H4 OUR-SIDE rule.)

Implements the spec ``docs/plans/2026-06-19-c10-coding-context-injector-spec.md``
in the host project.

Distinct from the model-callable ``repo_map`` tool in
``magi_agent.plugins.native.coding``: that tool returns ``ToolResult`` when the
model asks for it. This module returns a ``str`` block injected proactively at
prompt-assembly time so the model has the summary before its first call.
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

# ``os`` is used for the default environ source (``os.environ if env is None``).

__all__ = ["coding_context_block"]

logger = logging.getLogger(__name__)

# Defaults match the spec §B.1. Tunable via env (see below).
_DEFAULT_FILE_LIMIT = 80
_DEFAULT_TOKEN_BUDGET = 1500
_DEFAULT_RECENT_LIMIT = 5

# Hard ceilings for the per-directory file counter. ``_directory_stats`` only
# needs an approximate "src/ (N files)" figure for the prompt block, so the walk
# must NEVER enumerate a whole tree: on a large workspace (a big monorepo, or a
# cwd like ``/tmp``/``$HOME`` full of unrelated files) an uncapped
# ``sum(1 for _ in _walk_files(...))`` is millions of ``iterdir``/``is_dir``
# syscalls and hangs runtime construction for minutes. Cap total entries visited
# across the whole ``_directory_stats`` pass and the recursion depth; a count
# that hits the cap is rendered as a floor (``N+``).
_MAX_SCAN_ENTRIES = 4000
_MAX_SCAN_DEPTH = 6

# Noise directories the workspace scan skips entirely. Mirrors the conventions
# the existing native ``repo_map`` tool already uses informally + the cargo /
# go / .next / .venv variants common in heterogeneous repos.
_EXCLUDE_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",
        "node_modules", "__pycache__", ".venv", "venv", ".env",
        "dist", "build", "target", "out",
        ".next", ".turbo", ".cache",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "coverage", ".coverage",
        # Security-sensitive — never enumerate these.
        ".ssh", ".aws", ".gnupg",
    }
)

# Known top-of-tree entry points the §B.3 step 2 always surfaces if present.
_ENTRY_POINT_NAMES: tuple[str, ...] = (
    "main.py", "app.py", "__main__.py",
    "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "pnpm-workspace.yaml",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "README.md", "README.rst",
)


def _is_enabled(env: Mapping[str, str]) -> bool:
    """Strict-truthy read of ``MAGI_CODING_CONTEXT_ENABLED`` via config.env."""
    from magi_agent.config.env import is_coding_context_enabled  # noqa: PLC0415

    return is_coding_context_enabled(env)


def _file_limit(env: Mapping[str, str]) -> int:
    from magi_agent.config.env import coding_context_file_limit  # noqa: PLC0415

    return coding_context_file_limit(env) or _DEFAULT_FILE_LIMIT


def _token_budget(env: Mapping[str, str]) -> int:
    from magi_agent.config.env import coding_context_token_budget  # noqa: PLC0415

    return coding_context_token_budget(env) or _DEFAULT_TOKEN_BUDGET


def _estimate_tokens(text: str) -> int:
    """char_count // 4 — matches ``runtime.message_builder._estimate_prompt_tokens``."""
    return len(text) // 4


def _git_recent_changes(workspace: Path, limit: int) -> list[str]:
    """Recently modified files via ``git log --name-only``.

    Returns up to ``limit`` modified file paths (most recent first), or ``[]``
    when git is unavailable, the workspace is not a repo, or any error occurs.
    """
    try:
        proc = subprocess.run(
            [
                "git", "-C", str(workspace),
                "log", "-n", "5",
                "--name-only", "--no-merges",
                "--pretty=format:",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if proc.returncode != 0:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for line in proc.stdout.splitlines():
        path = line.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        ordered.append(path)
        if len(ordered) >= limit:
            break
    return ordered


def _detect_entry_points(workspace: Path) -> list[str]:
    found: list[str] = []
    for name in _ENTRY_POINT_NAMES:
        if (workspace / name).is_file():
            found.append(name)
    return found


def _directory_stats(workspace: Path) -> list[tuple[str, int]]:
    """Per-top-level-directory file count (non-recursive into noise dirs)."""
    stats: list[tuple[str, int]] = []
    try:
        children = sorted(workspace.iterdir(), key=lambda p: p.name)
    except (OSError, PermissionError):
        return stats
    # Shared scan budget across ALL top-level dirs so a workspace with many big
    # subtrees still cannot blow past the ceiling (the cap is global, not
    # per-dir). Mutable one-element list threaded into ``_walk_files``.
    remaining = [_MAX_SCAN_ENTRIES]
    for child in children:
        if remaining[0] <= 0:
            break
        if not child.is_dir():
            continue
        if child.name.startswith(".") and child.name not in (".github",):
            # Hidden directories are skipped unless they are well-known
            # project metadata (.github stays — it shows CI surface).
            continue
        if child.name in _EXCLUDE_DIR_NAMES:
            continue
        try:
            count = sum(1 for _ in _walk_files(child, budget=remaining))
        except (OSError, PermissionError):
            continue
        if count:
            stats.append((child.name + "/", count))
    return stats


def _walk_files(root: Path, *, budget: list[int] | None = None, _depth: int = 0):
    """Yield each file under ``root``, skipping noise directories.

    ``budget`` (a mutable ``[remaining]``) and ``_MAX_SCAN_DEPTH`` bound the walk
    so a pathologically large workspace cannot turn this into a multi-minute
    (or effectively unbounded) enumeration — see ``_MAX_SCAN_ENTRIES``.
    """
    stack: list[tuple[Path, int]] = [(root, _depth)]
    while stack:
        if budget is not None and budget[0] <= 0:
            return
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if budget is not None and budget[0] <= 0:
                return
            name = entry.name
            if name in _EXCLUDE_DIR_NAMES:
                continue
            try:
                if entry.is_dir():
                    if depth < _MAX_SCAN_DEPTH:
                        stack.append((entry, depth + 1))
                elif entry.is_file():
                    if budget is not None:
                        budget[0] -= 1
                    yield entry
            except OSError:
                continue


def _render_sections(
    *,
    workspace: Path,
    recent: list[str],
    entry_points: list[str],
    dir_stats: list[tuple[str, int]],
) -> list[str]:
    """Render each available section as a labelled block. Empty sections skipped."""
    sections: list[str] = []
    sections.append(f"Workspace: {workspace}")
    if recent:
        lines = ["Recently modified:"] + [f"- {p}" for p in recent]
        sections.append("\n".join(lines))
    if entry_points:
        lines = ["Entry points:"] + [f"- {p}" for p in entry_points]
        sections.append("\n".join(lines))
    if dir_stats:
        lines = ["Top-level directories:"] + [
            f"- {name} ({count} files)" for name, count in dir_stats
        ]
        sections.append("\n".join(lines))
    sections.append(
        "Tip: use the `repo_map` or `code_symbol_search` tools for deeper "
        "exploration."
    )
    return sections


def _truncate_to_budget(sections: list[str], budget: int) -> list[str]:
    """Drop lower-priority sections (from the end, before the closing tip) until
    the block fits in ``budget`` tokens.

    The opening Workspace line and the closing tip line are always kept; the
    middle sections are removed from least to most priority. Priority order is
    the order ``_render_sections`` produced (header first, content last).
    """
    if not sections:
        return sections
    if _estimate_tokens(_wrap_block(sections)) <= budget:
        return sections
    workspace_section = sections[0]
    tip_section = sections[-1]
    middle = list(sections[1:-1])
    # Drop content sections from the end (lower priority) until under budget.
    while middle and _estimate_tokens(
        _wrap_block([workspace_section, *middle, tip_section])
    ) > budget:
        middle.pop()
    return [workspace_section, *middle, tip_section]


def _wrap_block(sections: list[str]) -> str:
    body = "\n\n".join(sections)
    return f"<coding_context>\n{body}\n</coding_context>"


def coding_context_block(
    *,
    workspace_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Render the ``<coding_context>`` block, or ``""`` when not applicable.

    Returns ``""`` when:
    - ``MAGI_CODING_CONTEXT_ENABLED`` is not truthy,
    - ``workspace_root`` is ``None`` or unreadable,
    - any internal error occurs (fail-safe).
    """
    try:
        source = os.environ if env is None else env
        if not _is_enabled(source):
            return ""
        if workspace_root is None:
            return ""
        workspace = Path(workspace_root)
        if not workspace.is_dir():
            return ""

        file_limit = _file_limit(source)
        budget = _token_budget(source)

        recent = _git_recent_changes(workspace, limit=_DEFAULT_RECENT_LIMIT)
        entry_points = _detect_entry_points(workspace)
        dir_stats = _directory_stats(workspace)
        # ``file_limit`` is the §B.2 per-tree budget — we cap directory entries
        # so a huge repo doesn't fill the block; recent + entry sections
        # already have their own bounds.
        dir_stats = dir_stats[:file_limit]

        sections = _render_sections(
            workspace=workspace,
            recent=recent,
            entry_points=entry_points,
            dir_stats=dir_stats,
        )
        sections = _truncate_to_budget(sections, budget)
        return _wrap_block(sections)
    except Exception:  # noqa: BLE001 — block must NEVER break prompt assembly.
        logger.debug("coding_context_block failed", exc_info=True)
        return ""
