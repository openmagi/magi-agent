"""PR3 — memory file manifest (frontmatter + mtime, newest-first).

A *manifest* is a lightweight index of the workspace ``memory/`` tree: one entry
per ``*.md`` file carrying the file's frontmatter metadata (``name`` /
``description`` / ``type``), its modification time, and a ``stale`` marker for
entries older than one day.  It is the in-context catalogue handed to the
optional cheap-model re-ranker (:mod:`magi_agent.cli.memory_recall_rerank`) so the
selector can reason about candidate documents by their declared purpose rather
than raw body text alone.

GOVERNANCE INVARIANTS
---------------------
* The manifest is metadata-only and side-effect free: it READS files and never
  writes.  It is only ever built when the re-rank gate is ON (default OFF), so
  the default recall path pays no scan cost.
* ``SOUL.md`` is NEVER included — the agent must never reach the operator soul
  file (mirrors ``conformance.SOUL_FILENAME``).
* Fail-soft: any filesystem / decode error on a single file skips that file; a
  missing/unreadable directory yields ``[]``.  This helper must never raise into
  the recall path.
* Frontmatter parsing is a tiny dependency-free reader (no PyYAML): it accepts a
  leading ``---`` … ``---`` fence of ``key: value`` lines.  Only ``name`` /
  ``description`` / ``type`` are read; everything else is ignored.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from magi_agent.memory.conformance import SOUL_FILENAME

__all__ = [
    "MemoryManifestEntry",
    "build_memory_manifest",
]

#: Default cap on the number of manifest entries (newest-first).  Bounds the
#: in-context catalogue handed to the re-ranker.
_DEFAULT_CAP = 200

#: An entry whose mtime is older than this many seconds is marked ``stale``.
_STALENESS_SECONDS = 24 * 3600

#: Only these frontmatter keys are read; everything else is ignored.
_MANIFEST_KEYS = ("name", "description", "type")

#: Max chars kept from any single frontmatter value (untrusted file content).
_MAX_VALUE_CHARS = 200


@dataclass(frozen=True, slots=True)
class MemoryManifestEntry:
    """One memory file's manifest row.

    ``path`` is relative to the scanned ``memory_dir`` (posix).  ``name`` /
    ``description`` / ``type`` come from the file's frontmatter (empty string
    when absent).  ``mtime`` is the file modification epoch seconds; ``stale`` is
    ``True`` when the file is older than one day.
    """

    path: str
    name: str
    description: str
    type: str
    mtime: float
    stale: bool


def build_memory_manifest(
    memory_dir: "Path | str",
    *,
    cap: int = _DEFAULT_CAP,
    now: float | None = None,
) -> list[MemoryManifestEntry]:
    """Scan ``memory_dir`` for ``*.md`` files and return manifest entries.

    Entries are sorted newest-first by mtime and capped at ``cap``.  ``SOUL.md``
    is always excluded.  Returns ``[]`` for a missing/unreadable directory.

    Args:
        memory_dir: The workspace ``memory/`` directory (NOT the workspace root).
        cap: Maximum number of entries to return (newest-first).
        now: Reference epoch seconds for staleness (defaults to wall clock);
            injectable for deterministic tests.
    """
    root = Path(memory_dir)
    reference = time.time() if now is None else now
    try:
        if not root.is_dir():
            return []
        candidates = list(root.rglob("*.md"))
    except OSError:
        return []

    entries: list[MemoryManifestEntry] = []
    for path in candidates:
        try:
            if not path.is_file():
                continue
            if path.name == SOUL_FILENAME:
                continue
            stat = path.stat()
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.name
            frontmatter = _read_frontmatter(path)
            entries.append(
                MemoryManifestEntry(
                    path=rel,
                    name=frontmatter.get("name", ""),
                    description=frontmatter.get("description", ""),
                    type=frontmatter.get("type", ""),
                    mtime=stat.st_mtime,
                    stale=(reference - stat.st_mtime) > _STALENESS_SECONDS,
                )
            )
        except OSError:
            continue

    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries[: max(int(cap), 0)]


def _read_frontmatter(path: Path) -> dict[str, str]:
    """Return the ``name`` / ``description`` / ``type`` frontmatter, if any.

    Reads only the small leading region of the file (the first ``---`` fence).
    Dependency-free: a minimal ``key: value`` reader, NOT a full YAML parser.
    Any read/decode error yields an empty mapping (fail-soft).
    """
    try:
        # Only the head of the file can hold frontmatter; cap the read so a huge
        # body never costs a full load just to find (or miss) the fence.
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(8192)
    except OSError:
        return {}

    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in _MANIFEST_KEYS and key not in result:
            cleaned = value.strip().strip("'\"")
            result[key] = cleaned[:_MAX_VALUE_CHARS]
    return result
