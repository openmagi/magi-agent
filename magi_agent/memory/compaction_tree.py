"""5-level persistent compaction tree + ROOT.md synthesis (PR-A).

This module ports the proven legacy TS ``CompactionEngine`` design into the
Hipocampus memory subsystem.  It is the missing *write* half of the tree: the
gated append-only writes (``MEMORY.md``/``USER.md``) and the size-cap
:mod:`magi_agent.memory.compactor` already exist, and the read-only adapter
(:mod:`magi_agent.memory.adapters.hipocampus_readonly`) already *reads*
``memory/ROOT.md`` and ``memory/daily/*.md`` — but **nothing ever wrote the
daily/weekly/monthly tiers or generated ROOT.md**.  ``CompactionTree`` fills
that gap.

The tree
--------
Five persistent levels under a workspace ``memory/`` dir::

    raw daily   memory/daily/YYYY-MM-DD.md   (input layer; appended each turn)
        |  (summarize in place when over daily_threshold lines)
    weekly      memory/weekly/YYYY-Www.md    (completed ISO weeks rolled up)
        |  (summarize when over weekly_threshold lines)
    monthly     memory/monthly/YYYY-MM.md    (completed months rolled up)
        |  (summarize when over monthly_threshold lines)
    ROOT        memory/ROOT.md               (synthesized, capped at root_max_tokens)

GOVERNANCE INVARIANT
--------------------
The flag gates *activation*, never *capability*: when
``MemoryRuntimeConfig.compaction_enabled`` is True the tree ACTUALLY builds;
when False ``run()`` is an inert no-op returning a ``skipped`` result.  PR-A
delivers the engine only — PR-B wires the trigger (session hook / cron) and
flips the default ON.  Nothing here auto-triggers.

Reuse (no second copies)
------------------------
* Secret redaction before every write reuses ``_redact_for_write`` from
  :mod:`magi_agent.memory.adapters.local_file_writable` (the proven
  secret-scanner used by the gated writable provider).
* Within-file size-capping (dedup + oldest-drop) reuses
  :func:`magi_agent.memory.compactor.consolidate`.
* Tier thresholds / cooldown / root cap are read from the real resolved
  :class:`magi_agent.memory.config.MemoryRuntimeConfig` — no new tunables.

Determinism / safety
--------------------
* The rollup logic never calls :func:`datetime.now` — callers inject ``today``
  (and an optional ``clock`` for the cooldown timestamp), so tests are
  date-stable and hermetic.
* ``Summarizer`` is an injectable Protocol; on ANY summarizer error it
  **fails open** to a deterministic truncation — ``run()`` never raises.
* All writes are confined under ``memory/``; a path that escapes is skipped,
  never written.  ``run()`` never crashes the caller.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from magi_agent.memory.adapters.local_file_writable import _redact_for_write
from magi_agent.memory.compactor import consolidate
from magi_agent.memory.config import MemoryRuntimeConfig

#: ROOT token estimate heuristic.  We approximate tokens as ``chars / 4`` (a
#: common rule of thumb for English/markdown), so the char budget for ROOT.md
#: is ``root_max_tokens * _CHARS_PER_TOKEN``.  Documented + used in one place so
#: the heuristic is easy to audit/tune.
_CHARS_PER_TOKEN = 4

#: Canonical ROOT.md sections (order is stable for diff-friendliness).
_ROOT_SECTIONS = (
    "## Active Context (recent ~7 days)",
    "## Recent Patterns",
    "## Historical Summary",
    "## Topics Index",
)

_STATE_FILENAME = ".compaction-state.json"
_DAILY_NAME_RE_LEN = len("YYYY-MM-DD")  # 10


@runtime_checkable
class Summarizer(Protocol):
    """Injectable LLM-summarization seam.

    Implementations turn a (possibly long) tier text into a shorter summary.
    PR-A tests inject a deterministic fake; production wires a real model in a
    later PR.  Implementations MAY raise — :class:`CompactionTree` catches every
    exception and falls back to a deterministic truncation (fail-open), so a
    summarizer outage never blocks the tree or crashes the turn loop.
    """

    def summarize(self, text: str) -> str:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class CompactionTreeResult:
    """Outcome of a :meth:`CompactionTree.run` pass.

    Attributes:
        ran: True iff the tree actually executed (not gated-off / not cooled).
        skipped_reason: Why the run was a no-op (``"disabled"``, ``"cooldown"``,
            ``"missing_memory_dir"``), or ``None`` when it ran.
        tiers_compacted: Tier names that produced at least one written file
            (subset of ``{"daily", "weekly", "monthly", "root"}``).
        files_written: Workspace-relative paths written this pass.
        summarized_count: How many tier files were LLM-summarized (over their
            threshold).  Fail-open truncations count here too.
        summarizer_failures: How many summarizer calls raised and fell back to
            deterministic truncation.
    """

    ran: bool
    skipped_reason: str | None = None
    tiers_compacted: tuple[str, ...] = field(default=())
    files_written: tuple[str, ...] = field(default=())
    summarized_count: int = 0
    summarizer_failures: int = 0


# ---------------------------------------------------------------------------
# Raw daily flush helper (the tree's input layer — PR-B calls this each turn)
# ---------------------------------------------------------------------------


def append_daily_entry(memory_dir: Path, entry: str, *, today: date) -> Path | None:
    """Append a raw ``entry`` to ``memory/daily/YYYY-MM-DD.md`` for ``today``.

    Pure, path-safe file IO — this is the tree's *input* layer.  The entry is
    redacted before write (same scanner the rest of the tree uses), so even the
    raw log never persists a secret.  Returns the written file path, or ``None``
    when the resolved target escapes ``memory_dir`` (defensive — ``today``
    formats to a fixed safe basename, so this should not happen in practice).

    Args:
        memory_dir: The workspace ``memory/`` directory (created if absent).
        entry: Raw text to append (a turn summary, an event line, etc.).
        today: The date whose daily file receives the entry (injected — never
            read from the system clock here).
    """
    daily_dir = memory_dir / "daily"
    target = daily_dir / f"{today.isoformat()}.md"
    if not _is_within(target, memory_dir):
        return None
    daily_dir.mkdir(parents=True, exist_ok=True)
    safe = _redact_for_write(entry)
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    target.write_text(existing + safe + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# CompactionTree
# ---------------------------------------------------------------------------


class CompactionTree:
    """5-level persistent compaction tree over a workspace ``memory/`` dir.

    Construct with the workspace ``memory/`` directory, the resolved
    :class:`MemoryRuntimeConfig`, and an injectable ``summarizer``.  Optionally
    inject a ``clock`` (defaults to ``datetime.now(timezone.utc)``) used ONLY
    for the cooldown timestamp — the rollup bucketing uses the ``today`` passed
    to :meth:`run`, never a clock.
    """

    def __init__(
        self,
        memory_dir: Path,
        config: MemoryRuntimeConfig,
        *,
        summarizer: Summarizer | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.memory_dir = Path(memory_dir)
        self.config = config
        self.summarizer = summarizer
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- public API ---------------------------------------------------------

    def run(self, *, today: date, force: bool = False) -> CompactionTreeResult:
        """Build the tree for ``today``.

        Gating + cooldown short-circuit before any IO.  Everything after is
        wrapped so a single bad file never crashes the caller; on an unexpected
        error the partial result gathered so far is returned.

        Args:
            today: Reference date for "completed week/month" bucketing and the
                ROOT "recent ~7 days" window.  Injected for hermetic tests.
            force: When True, ignore the cooldown (but NOT the disabled gate).
        """
        if not self.config.compaction_enabled:
            return CompactionTreeResult(ran=False, skipped_reason="disabled")

        if not self.memory_dir.is_dir():
            # No memory yet → nothing to roll up.  Not an error.
            return CompactionTreeResult(ran=False, skipped_reason="missing_memory_dir")

        if not force and self._within_cooldown():
            return CompactionTreeResult(ran=False, skipped_reason="cooldown")

        tiers: list[str] = []
        written: list[str] = []
        summarized = 0
        failures = 0
        try:
            d_w, d_s, d_f = self._compact_daily(today)
            w_w, w_s, w_f = self._roll_weekly(today)
            m_w, m_s, m_f = self._roll_monthly(today)
            r_w, r_s, r_f = self._synthesize_root(today)

            for tier, paths in (
                ("daily", d_w),
                ("weekly", w_w),
                ("monthly", m_w),
                ("root", r_w),
            ):
                if paths:
                    tiers.append(tier)
                    written.extend(paths)
            summarized = d_s + w_s + m_s + r_s
            failures = d_f + w_f + m_f + r_f
        except Exception:
            # Fail-soft: never propagate into the caller.  Persist the cooldown
            # stamp anyway so a hard-looping caller still respects the window.
            self._stamp_run()
            return CompactionTreeResult(
                ran=True,
                tiers_compacted=tuple(tiers),
                files_written=tuple(written),
                summarized_count=summarized,
                summarizer_failures=failures,
            )

        self._stamp_run()
        return CompactionTreeResult(
            ran=True,
            tiers_compacted=tuple(tiers),
            files_written=tuple(written),
            summarized_count=summarized,
            summarizer_failures=failures,
        )

    # -- tier: daily --------------------------------------------------------

    def _compact_daily(self, today: date) -> tuple[list[str], int, int]:
        """Summarize raw daily files in place when over ``daily_threshold``.

        Today's file is left untouched (it is still being appended to); only
        completed prior days are summarized.
        """
        written: list[str] = []
        summarized = 0
        failures = 0
        for path in self._tier_files("daily"):
            day = _parse_daily_date(path.name)
            if day is None or day >= today:
                continue  # skip the still-open current day (and any future)
            text = _read(path)
            new_text, did, failed = self._maybe_summarize(text, self.config.daily_threshold)
            if did:
                if self._write(path, new_text):
                    written.append(self._rel(path))
                    summarized += 1
                    failures += int(failed)
        return written, summarized, failures

    # -- tier: weekly -------------------------------------------------------

    def _roll_weekly(self, today: date) -> tuple[list[str], int, int]:
        """Roll completed-ISO-week daily files into ``memory/weekly/YYYY-Www.md``."""
        written: list[str] = []
        summarized = 0
        failures = 0
        current_week = _iso_week_key(today)

        buckets: dict[str, list[Path]] = {}
        for path in self._tier_files("daily"):
            day = _parse_daily_date(path.name)
            if day is None:
                continue
            wk = _iso_week_key(day)
            if wk >= current_week:
                continue  # the current (incomplete) week is not rolled up yet
            buckets.setdefault(wk, []).append(path)

        for wk, paths in sorted(buckets.items()):
            combined = self._combine(paths, header=f"# Week {wk}")
            new_text, did, failed = self._maybe_summarize(
                combined, self.config.weekly_threshold
            )
            target = self.memory_dir / "weekly" / f"{wk}.md"
            if self._write(target, new_text):
                written.append(self._rel(target))
                if did:
                    summarized += 1
                    failures += int(failed)
        return written, summarized, failures

    # -- tier: monthly ------------------------------------------------------

    def _roll_monthly(self, today: date) -> tuple[list[str], int, int]:
        """Roll completed-month weekly files into ``memory/monthly/YYYY-MM.md``."""
        written: list[str] = []
        summarized = 0
        failures = 0
        current_month = f"{today.year:04d}-{today.month:02d}"

        buckets: dict[str, list[Path]] = {}
        for path in self._tier_files("weekly"):
            month = _weekly_month_key(path.name)
            if month is None or month >= current_month:
                continue  # the current (incomplete) month is not rolled up yet
            buckets.setdefault(month, []).append(path)

        for month, paths in sorted(buckets.items()):
            combined = self._combine(paths, header=f"# Month {month}")
            new_text, did, failed = self._maybe_summarize(
                combined, self.config.monthly_threshold
            )
            target = self.memory_dir / "monthly" / f"{month}.md"
            if self._write(target, new_text):
                written.append(self._rel(target))
                if did:
                    summarized += 1
                    failures += int(failed)
        return written, summarized, failures

    # -- ROOT synthesis -----------------------------------------------------

    def _synthesize_root(self, today: date) -> tuple[list[str], int, int]:
        """Synthesize ``memory/ROOT.md`` from the tiers (the key missing piece).

        Builds the four canonical sections, redacts, and hard-caps the whole
        document at ``root_max_tokens`` (approximated as chars via
        ``_CHARS_PER_TOKEN``).  Always writes when ANY tier content exists.
        """
        recent = self._recent_daily_text(today)
        weekly_text = self._combine(self._tier_files("weekly"), header=None)
        monthly_text = self._combine(self._tier_files("monthly"), header=None)
        topics = self._topics_index()

        # Empty memory → nothing to synthesize, no ROOT write.
        if not any((recent.strip(), weekly_text.strip(), monthly_text.strip(), topics.strip())):
            return [], 0, 0

        char_cap = self.config.root_max_tokens * _CHARS_PER_TOKEN
        document = _build_root_document(
            active_context=recent,
            recent_patterns=weekly_text,
            historical=monthly_text,
            topics_index=topics,
            char_cap=char_cap,
        )
        target = self.memory_dir / "ROOT.md"
        if self._write(target, document):
            return [self._rel(target)], 0, 0
        return [], 0, 0

    def _recent_daily_text(self, today: date) -> str:
        """Concatenate raw daily files within the recent ~7-day window."""
        cutoff = today - timedelta(days=7)
        parts: list[str] = []
        for path in self._tier_files("daily"):
            day = _parse_daily_date(path.name)
            if day is None or day < cutoff or day > today:
                continue
            text = _read(path).strip()
            if text:
                parts.append(f"### {path.stem}\n{text}")
        return "\n\n".join(parts)

    def _topics_index(self) -> str:
        """A stable bullet list of every tier file currently on disk."""
        lines: list[str] = []
        for tier in ("monthly", "weekly", "daily"):
            for path in self._tier_files(tier):
                lines.append(f"- {tier}: {path.stem}")
        return "\n".join(lines)

    # -- shared helpers -----------------------------------------------------

    def _maybe_summarize(self, text: str, threshold: int) -> tuple[str, bool, bool]:
        """Summarize ``text`` when it exceeds ``threshold`` *lines*.

        Returns ``(text, summarized, failed_over_to_truncation)``.  When under
        threshold the text is returned unchanged.  When over, the injected
        summarizer is tried; on ANY error (or no summarizer wired) it falls open
        to a deterministic truncation — never raising.
        """
        if _line_count(text) <= threshold:
            return text, False, False

        if self.summarizer is not None:
            try:
                summary = self.summarizer.summarize(text)
                if isinstance(summary, str) and summary.strip():
                    return summary, True, False
                # Empty/garbage summary → treat as a soft failure (truncate).
            except Exception:
                return _truncate_lines(text, threshold), True, True
        # No summarizer or empty output → deterministic truncation (still a
        # "summarized" event so the tier shrinks; not a hard failure unless an
        # exception was raised).
        return _truncate_lines(text, threshold), True, False

    def _combine(self, paths: list[Path], *, header: str | None) -> str:
        parts: list[str] = []
        if header:
            parts.append(header)
        for path in paths:
            text = _read(path).strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    def _tier_files(self, tier: str) -> list[Path]:
        directory = self.memory_dir / tier
        if not directory.is_dir():
            return []
        files = [
            p
            for p in directory.glob("*.md")
            if p.is_file() and _is_within(p, self.memory_dir)
        ]
        return sorted(files, key=lambda p: p.name)

    def _write(self, target: Path, text: str) -> bool:
        """Redact, size-cap, and write ``text`` to ``target`` if path-safe."""
        if not _is_within(target, self.memory_dir):
            return False
        safe = _redact_for_write(text)
        # Reuse the deterministic dedup/oldest-drop consolidator as a final
        # within-file size guard (generous cap — summarization already bounds
        # tier size; this just dedups repeated rollups).
        capped = consolidate(safe, max_bytes=_DEFAULT_FILE_CAP_BYTES).text
        body = capped if capped.strip() else safe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
        return True

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.memory_dir).as_posix()
        except ValueError:
            return path.name

    # -- cooldown -----------------------------------------------------------

    def _state_path(self) -> Path:
        return self.memory_dir / _STATE_FILENAME

    def _within_cooldown(self) -> bool:
        last = self._read_last_run()
        if last is None:
            return False
        elapsed = self._clock() - last
        return elapsed < timedelta(hours=self.config.cooldown_hours)

    def _read_last_run(self) -> datetime | None:
        path = self._state_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("last_compaction_run")
            if not isinstance(raw, str):
                return None
            parsed = datetime.fromisoformat(raw)
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _stamp_run(self) -> None:
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self._state_path().write_text(
                json.dumps({"last_compaction_run": self._clock().isoformat()}),
                encoding="utf-8",
            )
        except Exception:
            # Cooldown bookkeeping is best-effort; never crash the caller.
            pass


# Generous per-tier-file byte cap.  Summarization is the primary size control;
# this is a last-resort guard so a pathological rollup can't write an unbounded
# file.  Kept large so normal summaries are never truncated mid-fact.
_DEFAULT_FILE_CAP_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


def _build_root_document(
    *,
    active_context: str,
    recent_patterns: str,
    historical: str,
    topics_index: str,
    char_cap: int,
) -> str:
    """Assemble the four canonical ROOT sections and hard-cap the whole doc."""
    blocks = [
        (_ROOT_SECTIONS[0], active_context),
        (_ROOT_SECTIONS[1], recent_patterns),
        (_ROOT_SECTIONS[2], historical),
        (_ROOT_SECTIONS[3], topics_index),
    ]
    rendered: list[str] = ["# Memory Root (synthesized)"]
    for heading, body in blocks:
        rendered.append(heading)
        rendered.append(body.strip() if body.strip() else "_(none)_")
    document = "\n\n".join(rendered) + "\n"
    return _cap_chars(document, char_cap)


def _cap_chars(text: str, char_cap: int) -> str:
    if char_cap <= 0:
        return ""
    if len(text) <= char_cap:
        return text
    return text[:char_cap].rstrip() + "\n"


def _line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _truncate_lines(text: str, max_lines: int) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        kept.append(line)
        if len(kept) >= max_lines:
            break
    return "\n".join(kept)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _is_within(target: Path, root: Path) -> bool:
    """True iff ``target`` resolves to a location under ``root``."""
    try:
        root_resolved = root.resolve(strict=False)
        target_resolved = target.resolve(strict=False)
        target_resolved.relative_to(root_resolved)
    except (ValueError, OSError):
        return False
    return True


def _parse_daily_date(name: str) -> date | None:
    stem = name[:-3] if name.endswith(".md") else name
    if len(stem) != _DAILY_NAME_RE_LEN:
        return None
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def _iso_week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _weekly_month_key(name: str) -> str | None:
    """Derive ``YYYY-MM`` for a ``YYYY-Www.md`` weekly file via its Monday.

    A week belongs to the month of its Monday (ISO week 1's Monday); this keeps
    every week in exactly one monthly bucket without ambiguity.
    """
    stem = name[:-3] if name.endswith(".md") else name
    if "-W" not in stem:
        return None
    year_part, _, week_part = stem.partition("-W")
    try:
        year = int(year_part)
        week = int(week_part)
        monday = date.fromisocalendar(year, week, 1)
    except (ValueError, TypeError):
        return None
    return f"{monday.year:04d}-{monday.month:02d}"


__all__ = [
    "CompactionTree",
    "CompactionTreeResult",
    "Summarizer",
    "append_daily_entry",
]
