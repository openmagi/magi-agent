"""Project identity loading for the local ``magi`` CLI agent.

Claude Code / OpenCode load project-level instruction files (``AGENTS.md`` and
friends) into the system prompt so the agent picks up repo conventions. This
module reads those optional files from the CLI ``workspace_root`` (the cwd) and
its ``.magi/`` subdir and shapes them into the ``identity`` mapping that
``build_system_prompt`` renders.
"""
from __future__ import annotations

import os
from typing import Mapping

# Project file name -> identity mapping key understood by build_system_prompt.
# CLAUDE.md is the general project-instructions file, so it maps to ``identity``.
_IDENTITY_FILES: tuple[tuple[str, str], ...] = (
    ("SOUL.md", "soul"),
    ("CLAUDE.md", "identity"),
    ("AGENTS.md", "agents"),
    ("TOOLS.md", "tools"),
)


def load_identity(workspace_root: str) -> Mapping[str, str]:
    identity: dict[str, str] = {}
    # Order matters: ``.magi/`` is searched last so a ``.magi/`` copy overwrites
    # (wins over) the workspace-root copy via last assignment.
    search_dirs = (workspace_root, os.path.join(workspace_root, ".magi"))
    for filename, key in _IDENTITY_FILES:
        for directory in search_dirs:
            content = _read_optional(os.path.join(directory, filename))
            if content:
                identity[key] = content
    return identity


def _read_optional(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


__all__ = ["load_identity"]
