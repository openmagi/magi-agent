"""Tool-level memory-mode hard enforcement.

Faithful Python port of ``clawy-core-agent/src/util/memoryMode.ts``. A chat
channel may carry a memory mode that restricts whether tools may read/write the
agent's long-term ("protected") memory state:

- ``incognito`` — tools may NOT read OR write protected memory paths.
- ``read_only`` — FileRead/Grep/Glob may NOT read protected memory paths;
  write tools may NOT write protected memory paths.
- ``normal``    — no restriction.

"Protected" memory = the top-level files ``MEMORY.md`` / ``SCRATCHPAD.md`` /
``WORKING.md`` / ``TASK-QUEUE.md`` and anything under ``memory/``.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping

from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.tools._workspace_path_guards import glob_pattern_matches


PROTECTED_TOP_LEVEL_FILES = frozenset(
    {"MEMORY.md", "SCRATCHPAD.md", "WORKING.md", "TASK-QUEUE.md"}
)

# Read-half of the memory-mode guards, single home alongside the write-half
# below. Shared by the core toolhost, the gate5b full toolhost, and the
# gates_policy_default firstparty pack so all three enforce identical extraction.
MEMORY_WRITE_TOOL_NAMES = frozenset({"FileWrite", "FileEdit", "PatchApply"})
MEMORY_READ_TOOL_NAMES = frozenset({"FileRead", "Glob", "Grep"})
PROTECTED_GLOB_SENTINELS = (
    "MEMORY.md",
    "SCRATCHPAD.md",
    "WORKING.md",
    "TASK-QUEUE.md",
    "memory/example.md",
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


def is_long_term_memory_read_disabled(value: MemoryMode | str | None) -> bool:
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


def memory_write_target_paths(
    tool_name: str,
    arguments: Mapping[str, object],
) -> tuple[str, ...]:
    """Return candidate target paths a write-style tool call would mutate.

    Shared by the core toolhost and the gate5b full toolhost so both enforce
    identical extraction. FileWrite/FileEdit/PatchApply (content mode) use the
    ``path`` argument (or its ``filePath`` alias, which the gate5b handlers
    also accept); PatchApply envelope mode parses ``patch``/``diff``.
    """

    paths: list[str] = []
    path_arg = arguments.get("path") or arguments.get("filePath")
    if isinstance(path_arg, str) and path_arg:
        paths.append(path_arg)
    if tool_name == "PatchApply" and not paths:
        patch_text = arguments.get("patch") or arguments.get("diff")
        if isinstance(patch_text, str) and patch_text.strip():
            paths.extend(_patch_envelope_paths(patch_text))
    return tuple(paths)


def _patch_envelope_paths(patch_text: str) -> tuple[str, ...]:
    try:
        from magi_agent.coding.patch_apply import parse_patch_envelope

        files = parse_patch_envelope(patch_text)
    except Exception:
        return ()
    paths: list[str] = []
    for file_op in files:
        if isinstance(getattr(file_op, "path", None), str):
            paths.append(file_op.path)
        move_to = getattr(file_op, "move_to", None)
        if isinstance(move_to, str) and move_to:
            paths.append(move_to)
    return tuple(paths)


def memory_read_target_paths(
    tool_name: str,
    arguments: Mapping[str, object],
) -> tuple[str, ...]:
    """Return candidate paths a read-style tool call would touch.

    FileRead resolves path aliases, Glob resolves the selector, and Grep
    resolves the glob/path selector.
    """

    if tool_name == "FileRead":
        names = ("path", "file", "filePath")
    elif tool_name == "Glob":
        names = ("pattern", "glob")
    elif tool_name == "Grep":
        names = ("glob", "path", "patternGlob")
    else:
        names = ()
    paths: list[str] = []
    for name in names:
        value = arguments.get(name)
        if isinstance(value, str) and value:
            paths.append(value)
    return tuple(paths)


def grep_glob_may_include_protected_memory(arguments: Mapping[str, object]) -> bool:
    raw_glob = (
        arguments.get("glob")
        or arguments.get("path")
        or arguments.get("patternGlob")
        or "**/*"
    )
    if not isinstance(raw_glob, str):
        return True
    pattern = normalize_memory_glob(raw_glob)
    if pattern is None:
        return False
    return any(glob_pattern_matches(path, pattern) for path in PROTECTED_GLOB_SENTINELS)


def normalize_memory_glob(pattern: str) -> str | None:
    text = str(pattern or "*").strip().replace("\\", "/")
    if not text:
        return "*"
    if text.startswith(("/", "~")):
        return None
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return None
    normalized = posixpath.normpath("/".join(parts) or "*")
    return "*" if normalized == "." else normalized


def filter_protected_memory_matches(output: object) -> object:
    if not isinstance(output, Mapping):
        return output
    matches = output.get("matches")
    if not isinstance(matches, list):
        return output
    filtered = [
        match for match in matches if not is_protected_memory_path(_match_path(match))
    ]
    if len(filtered) == len(matches):
        return output
    updated = dict(output)
    updated["matches"] = filtered
    return updated


def _match_path(match: object) -> str | None:
    if isinstance(match, str):
        return match
    if isinstance(match, Mapping):
        path = match.get("path")
        return path if isinstance(path, str) else None
    return None


__all__ = [
    "MEMORY_READ_TOOL_NAMES",
    "MEMORY_WRITE_TOOL_NAMES",
    "PROTECTED_GLOB_SENTINELS",
    "PROTECTED_TOP_LEVEL_FILES",
    "command_may_write_protected_memory",
    "command_mentions_protected_memory",
    "filter_protected_memory_matches",
    "grep_glob_may_include_protected_memory",
    "is_incognito_memory_mode",
    "is_long_term_memory_read_disabled",
    "is_long_term_memory_write_disabled",
    "is_protected_memory_path",
    "memory_read_target_paths",
    "memory_write_target_paths",
    "normalize_memory_glob",
    "normalize_memory_mode",
    "protected_memory_error",
]
