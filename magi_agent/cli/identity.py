"""Identity + project-context loading for the local ``magi`` CLI agent.

Two distinct scopes, deliberately kept separate:

* **Self identity** — who the agent IS. Read only from the magi-owned ``.magi``
  namespace: ``~/.magi/`` (global) and ``<cwd>/.magi/`` (project override).
  This is the agent's own space; a working repository's root files never define
  the agent's identity.
* **Project context** — the repository the agent is working IN. Read from
  repo-root ``AGENTS.md`` / ``CLAUDE.md`` (the cross-tool convention files) and
  surfaced as project context, NOT identity, so a project's description can
  never overwrite the agent's selfhood.
"""
from __future__ import annotations

import os
from typing import Mapping

# Magi-owned self-identity files, read from the ``.magi`` namespace only.
_SELF_IDENTITY_FILES: tuple[tuple[str, str], ...] = (("SOUL.md", "soul"),)

# Repo-root project-context files (other tools' / cross-tool conventions).
# Order = render order under the PROJECT CONTEXT header.
_PROJECT_CONTEXT_FILES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")


def load_identity(workspace_root: str) -> Mapping[str, str]:
    identity: dict[str, str] = {}

    # Self identity: ~/.magi (global) then <cwd>/.magi (project). Project wins
    # via last assignment.
    self_dirs = (
        os.path.join(os.path.expanduser("~"), ".magi"),
        os.path.join(workspace_root, ".magi"),
    )
    for filename, key in _SELF_IDENTITY_FILES:
        for directory in self_dirs:
            content = _read_optional(os.path.join(directory, filename))
            if content:
                identity[key] = content

    # Project context: repo-root convention files, combined under sub-headers.
    project_blocks: list[str] = []
    for filename in _PROJECT_CONTEXT_FILES:
        content = _read_optional(os.path.join(workspace_root, filename))
        if content:
            project_blocks.append(f"## {filename}\n\n{content}")
    if project_blocks:
        identity["project_context"] = "\n\n".join(project_blocks)

    return identity


def _read_optional(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


__all__ = ["load_identity"]
