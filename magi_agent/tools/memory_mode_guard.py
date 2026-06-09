"""Tool-level memory-mode hard enforcement.

Faithful Python port of ``clawy-core-agent/src/util/memoryMode.ts``. A chat
channel may carry a memory mode that restricts whether tools may read/write the
agent's long-term ("protected") memory state:

- ``incognito`` — tools may NOT read OR write protected memory paths.
- ``read_only`` — tools may read but NOT write protected memory paths.
- ``normal``    — no restriction.

"Protected" memory = the top-level files ``MEMORY.md`` / ``SCRATCHPAD.md`` /
``WORKING.md`` / ``TASK-QUEUE.md`` and anything under ``memory/``.
"""

from __future__ import annotations

import posixpath
import re

from magi_agent.runtime.session_identity import MemoryMode


PROTECTED_TOP_LEVEL_FILES = frozenset(
    {"MEMORY.md", "SCRATCHPAD.md", "WORKING.md", "TASK-QUEUE.md"}
)

_COMMAND_MENTIONS_MEMORY_RE = re.compile(r"\bmemory(?:/|\b)", re.IGNORECASE)
_COMMAND_MENTIONS_FILES_RE = re.compile(
    r"\b(MEMORY\.md|SCRATCHPAD\.md|WORKING\.md|TASK-QUEUE\.md)\b"
)
_COMMAND_MUTATING_BINARY_RE = re.compile(
    r"(^|[\s|;&(])(?:rm|mv|cp|touch|mkdir|rmdir|tee|truncate|sed|perl|python|"
    r"node|ruby|bash|sh|zsh)\b",
    re.IGNORECASE,
)
_COMMAND_REDIRECTION_RE = re.compile(r"(^|[^<])>>?")


def normalize_memory_mode(value: MemoryMode | str | None) -> str:
    """Return ``"normal"`` | ``"read_only"`` | ``"incognito"``."""

    raw = value.value if isinstance(value, MemoryMode) else value
    if raw == MemoryMode.READ_ONLY.value:
        return MemoryMode.READ_ONLY.value
    if raw == MemoryMode.INCOGNITO.value:
        return MemoryMode.INCOGNITO.value
    return MemoryMode.NORMAL.value


def is_incognito_memory_mode(value: MemoryMode | str | None) -> bool:
    return normalize_memory_mode(value) == MemoryMode.INCOGNITO.value


def is_long_term_memory_write_disabled(value: MemoryMode | str | None) -> bool:
    normalized = normalize_memory_mode(value)
    return normalized in (MemoryMode.READ_ONLY.value, MemoryMode.INCOGNITO.value)


def is_protected_memory_path(raw_path: str | None) -> bool:
    if not isinstance(raw_path, str):
        return False
    text = raw_path.strip().replace("\\", "/")
    if not text:
        return False
    normalized = posixpath.normpath(text)
    # Strip a leading ``./`` then any leading ``/`` so absolute-looking and
    # relative paths normalize the same way as the TS reference.
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    if normalized in ("", "."):
        return False
    if normalized == "memory" or normalized.startswith("memory/"):
        return True
    return normalized in PROTECTED_TOP_LEVEL_FILES


def command_mentions_protected_memory(command: str | None) -> bool:
    if not isinstance(command, str) or not command:
        return False
    if _COMMAND_MENTIONS_MEMORY_RE.search(command) is not None:
        return True
    return _COMMAND_MENTIONS_FILES_RE.search(command) is not None


def command_may_write_protected_memory(command: str | None) -> bool:
    if not isinstance(command, str):
        return False
    if not command_mentions_protected_memory(command):
        return False
    if _COMMAND_MUTATING_BINARY_RE.search(command) is not None:
        return True
    return _COMMAND_REDIRECTION_RE.search(command) is not None


def protected_memory_error(path_label: str = "memory state") -> str:
    return f"memory mode blocks access to {path_label}"


__all__ = [
    "PROTECTED_TOP_LEVEL_FILES",
    "command_may_write_protected_memory",
    "command_mentions_protected_memory",
    "is_incognito_memory_mode",
    "is_long_term_memory_write_disabled",
    "is_protected_memory_path",
    "normalize_memory_mode",
    "protected_memory_error",
]
