"""Workspace file provider for ``@``-mention autocomplete (gap: identity-and-polish).

A small, pure-stdlib :class:`~magi_agent.cli.tui.autocomplete.CompletionProvider`
that lazily walks the workspace root and returns repo-relative POSIX paths. The
router (in :mod:`~magi_agent.cli.tui.autocomplete`) fuzzy-ranks and caps the
result set, so this provider returns its full (bounded) universe and lets the
router rank.

Design constraints (so a huge / hostile tree cannot stall or crash the
autocomplete worker):

* ``os.scandir`` (not ``rglob``) for a cheap iterative walk.
* directory excludes applied BEFORE descent (``.git``, ``node_modules``,
  ``__pycache__``, ``.venv``, ``dist``, ``build``, and any dot-directory).
* a hard file-count cap so the walk stops early on a giant tree.
* symlinked directories are NOT followed (no traversal cycles).
* per-directory ``PermissionError`` / ``OSError`` are swallowed (skip the dir,
  keep going) so one unreadable directory cannot crash the ``@`` worker.

This module is import-cheap (stdlib only); it is nonetheless lazy-imported by
``cli.wiring.build_tui_app`` to keep the wiring cold-start contract explicit.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

__all__ = ["WorkspaceFileProvider", "DEFAULT_FILE_CAP", "DEFAULT_EXCLUDE_DIRS"]

#: Hard cap on how many files the walk collects before stopping. A bounded scan
#: keeps the autocomplete worker responsive on a large repository.
DEFAULT_FILE_CAP = 2000

#: Directory names skipped entirely (never descended into). Dot-directories are
#: excluded separately by the leading-dot rule in :meth:`_iter_files`.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
)


class WorkspaceFileProvider:
    """Lazily list repo-relative files under ``root`` for ``@`` autocomplete."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        file_cap: int = DEFAULT_FILE_CAP,
        exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    ) -> None:
        self._root = os.fspath(root)
        self._file_cap = file_cap
        self._exclude_dirs = exclude_dirs

    # The router passes the post-trigger fragment; we ignore it and return the
    # full bounded universe (the router fuzzy-ranks + caps). Kept in the
    # signature to satisfy the CompletionProvider protocol.
    def candidates(self, fragment: str) -> Sequence[str]:
        _ = fragment
        return list(self._iter_files())

    def _iter_files(self) -> list[str]:
        out: list[str] = []
        # Iterative DFS over directories. ``stack`` holds absolute dir paths.
        stack: list[str] = [self._root]
        root = self._root
        while stack and len(out) < self._file_cap:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except (PermissionError, OSError):
                # Unreadable directory: skip it, keep walking the rest.
                continue
            for entry in entries:
                if len(out) >= self._file_cap:
                    break
                name = entry.name
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    # Exclude noise + dot-directories BEFORE descending.
                    if name.startswith(".") or name in self._exclude_dirs:
                        continue
                    stack.append(entry.path)
                    continue
                try:
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if not is_file:
                    continue
                rel = os.path.relpath(entry.path, root)
                out.append(rel.replace(os.sep, "/"))
        return out
