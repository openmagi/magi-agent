"""Per-session input history + draft stash for the Magi TUI (PR1.2 / PR1.3).

``InputHistory`` is a bounded ring of submitted prompts, navigated by ↑/↓,
persisted one JSON object per line. ``DraftStash`` (PR1.3) is a light
recency+count restore for abandoned drafts. Both reuse the ``~/.magi`` root
(``MAGI_CLI_SESSION_DIR`` override) that ``session_log.py`` establishes, under a
``tui/`` subdir so they never collide with session transcripts.

Pure stdlib (json + pathlib). No textual/rich/adk imports, so the module is
import-clean and unit-testable without an event loop.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from magi_agent.cli.session_log import _session_root

__all__ = [
    "InputHistory",
    "DraftStash",
    "history_path",
    "draft_path",
]

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Default cap on retained history entries (newest-wins ring).
DEFAULT_MAX_ENTRIES = 500

# Default cap on retained DISTINCT drafts (frecency dict is keyed by text, so
# this bounds the in-memory entry count; the on-disk JSONL is compacted down to
# this many distinct entries when it crosses 2*cap appended lines).
DEFAULT_MAX_DRAFTS = 200

# Minimum draft length worth stashing (01 §5.3).
MIN_DRAFT_LEN = 20


def _safe_session(session_id: str) -> str:
    # Ordering is intentional: the unsafe-char regex runs FIRST (it preserves
    # ``.`` so legit dotted ids survive), which guarantees no NEW ``..``
    # sequence can be synthesized after the subsequent ``replace("..", "-")``.
    # Do not reorder these two steps.
    cleaned = _UNSAFE.sub("-", session_id).replace("..", "-").lstrip(".-")
    return cleaned or "session"


def _tui_dir() -> Path:
    return _session_root() / "tui"


def history_path(session_id: str) -> Path:
    """JSONL path for a session's input history."""

    return _tui_dir() / f"history-{_safe_session(session_id)}.jsonl"


def draft_path(session_id: str) -> Path:
    """JSONL path for a session's draft stash (PR1.3)."""

    return _tui_dir() / f"drafts-{_safe_session(session_id)}.jsonl"


class InputHistory:
    """Bounded ring of submitted prompts, ↑/↓-navigable, persisted as JSONL.

    Navigation model (mirrors a shell): ``prev(current)`` walks toward older
    entries, stashing ``current`` (the live draft) on the FIRST step so
    ``next()`` can restore it after the user scrolls past the newest entry.
    Clamps at the oldest entry (``prev`` returns it repeatedly); ``next()`` past
    the bottom returns ``None``.

    Persistence is opt-in: pass an explicit ``path`` (JSONL on disk) or
    ``path=Ellipsis`` to derive the per-session path via :func:`history_path`.
    The default ``path=None`` is IN-MEMORY only (no disk read/write) so pure
    unit tests and ephemeral hosts never touch ``~/.magi``.
    """

    _DERIVE = object()  # sentinel: derive the on-disk path from session_id

    def __init__(
        self,
        *,
        session_id: str,
        path: Path | None = _DERIVE,  # type: ignore[assignment]
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._session_id = session_id
        self._max = max(1, int(max_entries))
        if path is InputHistory._DERIVE:
            # Default (app construction): derive the per-session on-disk path.
            path = history_path(session_id)
        # ``path=None`` stays None: in-memory only (no persistence).
        self._path: Path | None = path
        self._entries: list[str] = self._load()
        # Cursor: None = at the live draft (bottom); else index into _entries.
        self._cursor: int | None = None
        self._draft: str = ""

    # -- persistence -----------------------------------------------------
    def _load(self) -> list[str]:
        if self._path is None or not self._path.exists():
            return []
        out: list[str] = []
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = obj.get("text") if isinstance(obj, dict) else None
                    if isinstance(text, str) and text:
                        out.append(text)
        except OSError:
            return []
        return out[-self._max :]

    def _append_disk(self, text: str) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(
                self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
        except OSError:
            return
        # Opportunistic compaction: appends grow the JSONL forever (entries are
        # only trimmed on the NEXT _load). When the file crosses 2*_max lines,
        # rewrite it down to the most-recent _max entries so a long-lived
        # session can't grow the file without bound. Same graceful-degradation
        # discipline as above: a failed compaction must never crash the TUI.
        self._compact_disk()

    def _compact_disk(self) -> None:
        if self._path is None:
            return
        threshold = 2 * self._max
        try:
            # Cheap line-count guard before doing any read-trim-rewrite work.
            with open(self._path, "r", encoding="utf-8") as fh:
                line_count = sum(1 for _ in fh)
            if line_count <= threshold:
                return
            # Re-read and keep only the most-recent _max valid entries.
            entries: list[str] = []
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    val = obj.get("text") if isinstance(obj, dict) else None
                    if isinstance(val, str) and val:
                        entries.append(val)
            entries = entries[-self._max :]
            tmp = self._path.with_name(self._path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for val in entries:
                    fh.write(
                        json.dumps({"text": val}, ensure_ascii=False) + "\n"
                    )
            os.replace(tmp, self._path)
        except OSError:
            return

    # -- public API ------------------------------------------------------
    def add(self, text: str) -> None:
        """Record a submitted prompt; resets navigation to the bottom."""

        text = text.rstrip("\n")
        if not text.strip():
            return
        if self._entries and self._entries[-1] == text:
            # consecutive duplicate: don't grow, just reset cursor
            self._cursor = None
            self._draft = ""
            return
        self._entries.append(text)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max :]
        self._cursor = None
        self._draft = ""
        self._append_disk(text)

    def prev(self, current: str) -> str | None:
        """Step to an older entry; return it (clamps at the oldest)."""

        if not self._entries:
            return None
        if self._cursor is None:
            # leaving the live draft: stash it so next() can restore it
            self._draft = current
            self._cursor = len(self._entries) - 1
        elif self._cursor > 0:
            self._cursor -= 1
        # at index 0 we clamp (stay)
        return self._entries[self._cursor]

    def next(self) -> str | None:
        """Step toward newer entries; restore the stashed draft past the end."""

        if self._cursor is None:
            return None
        if self._cursor < len(self._entries) - 1:
            self._cursor += 1
            return self._entries[self._cursor]
        # past the newest entry -> back to the live draft
        self._cursor = None
        return self._draft


class DraftStash:
    """Light recency+count restore for abandoned drafts (01 §5.3).

    Saves drafts of at least :data:`MIN_DRAFT_LEN` chars; ``recent(limit)`` ranks
    by a deliberately simple frecency score = save count, ties broken by most-
    recent save (YAGNI — not a decayed frecency engine). Persisted as JSONL
    (one ``{"text": ...}`` object per line), so re-saving the same text appends a
    line that bumps its in-memory count + recency on the next bump/reload.

    Persistence mirrors :class:`InputHistory`: pass an explicit ``path`` (JSONL
    on disk) or ``path=Ellipsis`` to derive the per-session path via
    :func:`draft_path`. The default ``path=None`` is IN-MEMORY only (no disk
    read/write) so pure unit tests and ephemeral hosts never touch ``~/.magi``.
    On-disk files use the same 0o700 dir / 0o600 file perms and corrupt-line
    tolerance as ``InputHistory``; a failed read/write degrades gracefully and
    never crashes the TUI.
    """

    _DERIVE = object()  # sentinel: derive the on-disk path from session_id

    def __init__(
        self,
        *,
        session_id: str,
        path: Path | None = _DERIVE,  # type: ignore[assignment]
        max_drafts: int = DEFAULT_MAX_DRAFTS,
    ) -> None:
        self._session_id = session_id
        self._max = max(1, int(max_drafts))
        if path is DraftStash._DERIVE:
            # Default (app construction): derive the per-session on-disk path.
            path = draft_path(session_id)
        # ``path=None`` stays None: in-memory only (no persistence).
        self._path: Path | None = path
        # text -> (count, last_seq). _seq is a monotonic recency tiebreaker.
        self._entries: dict[str, tuple[int, int]] = {}
        self._seq = 0
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = obj.get("text") if isinstance(obj, dict) else None
                    if isinstance(text, str) and len(text) >= MIN_DRAFT_LEN:
                        self._bump(text)
        except OSError:
            return

    def _bump(self, text: str) -> None:
        self._seq += 1
        count, _ = self._entries.get(text, (0, 0))
        self._entries[text] = (count + 1, self._seq)

    def _append_disk(self, text: str) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(
                self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
        except OSError:
            return
        # Opportunistic compaction (mirrors InputHistory._append_disk): re-saving
        # the same draft appends a line each time, so the JSONL grows without
        # bound even though the in-memory dict dedups. When the file crosses
        # 2*_max lines, rewrite it down to the current distinct entries
        # (most-recent-ranked) so _load() cost stays bounded. Same
        # graceful-degradation discipline: a failed compaction never crashes.
        self._compact_disk()

    def _compact_disk(self) -> None:
        if self._path is None:
            return
        threshold = 2 * self._max
        try:
            # Cheap line-count guard before any read-trim-rewrite work.
            with open(self._path, "r", encoding="utf-8") as fh:
                line_count = sum(1 for _ in fh)
            if line_count <= threshold:
                return
            # Keep the most-relevant _max distinct entries, but WRITE them
            # oldest-relevant LAST so a fresh _load() (which assigns a monotonic
            # _seq per line in file order) reconstructs the same recency
            # ranking. recent() returns newest-first, so reverse it for write.
            entries = list(reversed(self.recent(limit=self._max)))
            tmp = self._path.with_name(self._path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for val in entries:
                    fh.write(
                        json.dumps({"text": val}, ensure_ascii=False) + "\n"
                    )
            os.replace(tmp, self._path)
        except OSError:
            return

    # -- public API ------------------------------------------------------
    def save(self, text: str) -> bool:
        """Stash ``text`` if it is at least :data:`MIN_DRAFT_LEN` chars.

        Returns ``True`` only when the draft was actually stored, ``False`` when
        dropped as too-short/blank. The caller (ctrl+s) uses this to avoid
        clearing the buffer on a dropped sub-threshold draft (no silent loss).
        """

        text = text.rstrip("\n")
        if len(text) < MIN_DRAFT_LEN:
            return False
        self._bump(text)
        self._append_disk(text)
        return True

    def recent(self, limit: int = 10) -> list[str]:
        """Most relevant drafts: by save count, then recency. Newest first."""

        ranked = sorted(
            self._entries.items(),
            key=lambda kv: (kv[1][0], kv[1][1]),
            reverse=True,
        )
        return [text for text, _ in ranked[: max(0, int(limit))]]
