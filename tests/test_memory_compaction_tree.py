"""PR-A — 5-level persistent compaction tree + ROOT.md synthesis.

Hermetic + deterministic: every test injects a fake summarizer and fixed dates
(``today``), and a fixed ``clock`` for cooldown.  No real model, no system
clock inside the rollup logic, no network.

Coverage:
  * daily → weekly → monthly rollup with synthetic dated files
  * threshold-triggered summarization (fake summarizer invoked ONLY over thresh)
  * cooldown skip vs force
  * ROOT.md synthesis: canonical sections + ``root_max_tokens`` char cap
  * fail-open when the summarizer raises (never propagates)
  * redaction of secrets before write
  * ``append_daily_entry`` appends + is path-safe
  * gating (compaction_enabled=False → inert no-op)
  * empty / missing memory dir → no crash
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from magi_agent.memory.compaction_tree import (
    CompactionTree,
    CompactionTreeResult,
    Summarizer,
    append_daily_entry,
    _CHARS_PER_TOKEN,
    _ROOT_SECTIONS,
)
from magi_agent.memory.config import MemoryRuntimeConfig


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _RecordingSummarizer:
    """Deterministic fake summarizer; records every call for assertions."""

    def __init__(self, marker: str = "[SUMMARY]") -> None:
        self.marker = marker
        self.calls: list[str] = []

    def summarize(self, text: str) -> str:
        self.calls.append(text)
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        return f"{self.marker} {first.strip()}"


class _RaisingSummarizer:
    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, text: str) -> str:
        self.calls += 1
        raise RuntimeError("model timeout")


def _config(**overrides) -> MemoryRuntimeConfig:
    base: dict[str, object] = {
        "compactionEnabled": True,
        "dailyThreshold": 5,
        "weeklyThreshold": 8,
        "monthlyThreshold": 12,
        "rootMaxTokens": 200,
        "cooldownHours": 24,
    }
    base.update(overrides)
    return MemoryRuntimeConfig(**base)


def _write_daily(memory: Path, day: str, lines: int = 1, text: str | None = None) -> Path:
    daily = memory / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    path = daily / f"{day}.md"
    if text is None:
        text = "\n".join(f"- entry {day} line {i}" for i in range(lines))
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _fixed_clock(dt: datetime):
    return lambda: dt


FIXED_NOW = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Gating (capability vs activation invariant)
# ---------------------------------------------------------------------------


def test_disabled_is_inert_noop(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-05-01", lines=50)
    tree = CompactionTree(memory, _config(compactionEnabled=False))
    result = tree.run(today=date(2026, 6, 8))
    assert isinstance(result, CompactionTreeResult)
    assert result.ran is False
    assert result.skipped_reason == "disabled"
    # No tier files, no ROOT, no state written.
    assert not (memory / "ROOT.md").exists()
    assert not (memory / "weekly").exists()
    assert not (memory / ".compaction-state.json").exists()


def test_enabled_actually_builds(tmp_path: Path) -> None:
    """Same inputs, flag ON → the tree ACTUALLY builds (the invariant)."""
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-08", lines=2)  # today → ROOT active context
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True
    assert (memory / "ROOT.md").is_file()
    assert "root" in result.tiers_compacted


# ---------------------------------------------------------------------------
# Empty / missing memory dir → no crash
# ---------------------------------------------------------------------------


def test_missing_memory_dir_no_crash(tmp_path: Path) -> None:
    tree = CompactionTree(tmp_path / "memory", _config())
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is False
    assert result.skipped_reason == "missing_memory_dir"


def test_empty_memory_dir_no_root(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    tree = CompactionTree(memory, _config())
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True
    assert not (memory / "ROOT.md").exists()
    assert result.tiers_compacted == ()


# ---------------------------------------------------------------------------
# Rollup: daily → weekly → monthly
# ---------------------------------------------------------------------------


def test_daily_rolls_into_completed_week(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # ISO week 2026-W18 (late Apr/early May): Mon 2026-04-27 .. Sun 2026-05-03.
    _write_daily(memory, "2026-04-27", lines=2)
    _write_daily(memory, "2026-04-28", lines=2)
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    # today is well past that week → the week is "completed".
    tree.run(today=date(2026, 6, 8))
    weekly = memory / "weekly" / "2026-W18.md"
    assert weekly.is_file()
    content = weekly.read_text(encoding="utf-8")
    assert "2026-04-27" in content


def test_current_week_not_rolled(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # today 2026-06-08 is in ISO week 2026-W24; its own daily must NOT roll up.
    _write_daily(memory, "2026-06-08", lines=2)
    tree = CompactionTree(memory, _config())
    tree.run(today=date(2026, 6, 8))
    assert not (memory / "weekly" / "2026-W24.md").exists()


def test_weekly_rolls_into_completed_month(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    weekly = memory / "weekly"
    weekly.mkdir(parents=True)
    # Week 2026-W10's Monday is 2026-03-02 → month 2026-03.
    (weekly / "2026-W10.md").write_text("- old weekly summary\n", encoding="utf-8")
    tree = CompactionTree(memory, _config())
    tree.run(today=date(2026, 6, 8))
    monthly = memory / "monthly" / "2026-03.md"
    assert monthly.is_file()
    assert "old weekly summary" in monthly.read_text(encoding="utf-8")


def test_current_month_weekly_not_rolled(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    weekly = memory / "weekly"
    weekly.mkdir(parents=True)
    # Week 2026-W23 Monday is 2026-06-01 → month 2026-06 == current month.
    (weekly / "2026-W23.md").write_text("- june weekly\n", encoding="utf-8")
    tree = CompactionTree(memory, _config())
    tree.run(today=date(2026, 6, 8))
    assert not (memory / "monthly" / "2026-06.md").exists()


# ---------------------------------------------------------------------------
# Threshold-triggered summarization
# ---------------------------------------------------------------------------


def test_summarizer_not_called_under_threshold(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # 2 lines < dailyThreshold(5); a completed prior day.
    _write_daily(memory, "2026-05-01", lines=2)
    summ = _RecordingSummarizer()
    tree = CompactionTree(memory, _config(), summarizer=summ)
    result = tree.run(today=date(2026, 6, 8))
    # daily under threshold → not summarized at the daily tier.
    # (weekly/monthly rollups of this small file are also under their thresholds.)
    assert summ.calls == []
    assert result.summarized_count == 0


def test_summarizer_called_over_threshold(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # 10 lines > dailyThreshold(5); a completed prior day.
    _write_daily(memory, "2026-05-01", lines=10)
    summ = _RecordingSummarizer()
    tree = CompactionTree(memory, _config(), summarizer=summ)
    result = tree.run(today=date(2026, 6, 8))
    assert summ.calls, "summarizer must be invoked when over threshold"
    assert result.summarized_count >= 1
    daily_after = (memory / "daily" / "2026-05-01.md").read_text(encoding="utf-8")
    assert "[SUMMARY]" in daily_after


def test_today_file_not_summarized(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-08", lines=20)  # huge, but it's TODAY
    summ = _RecordingSummarizer()
    tree = CompactionTree(memory, _config(), summarizer=summ)
    tree.run(today=date(2026, 6, 8))
    # today's raw file is still open → never summarized in place.
    today_text = (memory / "daily" / "2026-06-08.md").read_text(encoding="utf-8")
    assert "[SUMMARY]" not in today_text


# ---------------------------------------------------------------------------
# Fail-open when summarizer raises
# ---------------------------------------------------------------------------


def test_summarizer_raises_fails_open(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-05-01", lines=10)
    summ = _RaisingSummarizer()
    tree = CompactionTree(memory, _config(), summarizer=summ)
    # Must NOT raise.
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True
    assert summ.calls >= 1
    assert result.summarizer_failures >= 1
    # The daily file is truncated (deterministic fallback), still written.
    daily_after = (memory / "daily" / "2026-05-01.md").read_text(encoding="utf-8")
    assert daily_after.strip()  # non-empty
    # Truncated to the threshold line count (5).
    assert len([ln for ln in daily_after.splitlines() if ln.strip()]) <= 5


def test_no_summarizer_falls_back_to_truncation(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-05-01", lines=10)
    tree = CompactionTree(memory, _config(), summarizer=None)
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True
    daily_after = (memory / "daily" / "2026-05-01.md").read_text(encoding="utf-8")
    assert len([ln for ln in daily_after.splitlines() if ln.strip()]) <= 5


# ---------------------------------------------------------------------------
# I2 — redact-before-summarize: the summarizer must never receive raw secrets
# ---------------------------------------------------------------------------


def test_summarizer_input_is_redacted(tmp_path: Path) -> None:
    """The text handed to the summarizer must be scrubbed of secrets (I2).

    A daily file over threshold containing a secret token is fed to the tree.
    We assert that EVERY recorded summarizer input has the secret redacted —
    closing the exfiltration surface of sending raw tier text to an LLM.
    """
    memory = tmp_path / "memory"
    secret = "sk-live-supersecretkey1234567890"
    lines = "\n".join(
        [f"- entry {i}" for i in range(8)] + [f"- api key is {secret}"]
    )
    _write_daily(memory, "2026-05-01", text=lines)
    summ = _RecordingSummarizer()
    tree = CompactionTree(memory, _config(), summarizer=summ)

    tree.run(today=date(2026, 6, 8))

    assert summ.calls, "summarizer must be invoked when over threshold"
    for recorded in summ.calls:
        assert secret not in recorded, "raw secret leaked into summarizer input"
    # And the persisted tier file is redacted too (defense-in-depth on write).
    daily_after = (memory / "daily" / "2026-05-01.md").read_text(encoding="utf-8")
    assert secret not in daily_after


# ---------------------------------------------------------------------------
# ROOT.md synthesis: canonical sections + cap
# ---------------------------------------------------------------------------


def test_root_has_canonical_sections(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-08", lines=2)
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    root = (memory / "ROOT.md").read_text(encoding="utf-8")
    for section in _ROOT_SECTIONS:
        assert section in root, f"missing canonical section: {section}"


def test_root_respects_token_cap(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # Big recent-window content; tiny token cap.
    _write_daily(memory, "2026-06-08", lines=200)
    cfg = _config(rootMaxTokens=20)  # 20 * 4 = 80 chars cap
    tree = CompactionTree(memory, cfg, summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    root = (memory / "ROOT.md").read_text(encoding="utf-8")
    assert len(root) <= cfg.root_max_tokens * _CHARS_PER_TOKEN + 1  # +1 for trailing \n


def test_root_active_context_only_recent_window(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-08", lines=1, text="- recent today fact")
    _write_daily(memory, "2026-01-01", lines=1, text="- ancient fact")
    tree = CompactionTree(memory, _config(rootMaxTokens=4000), summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    root = (memory / "ROOT.md").read_text(encoding="utf-8")
    # The recent file is in the active-context window; the ancient one is not
    # (it rolls into older tiers / topics index instead).
    assert "recent today fact" in root


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


def test_cooldown_skips_within_window(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    # Last run 1 hour ago; cooldown 24h → skip.
    state = {"last_compaction_run": datetime(2026, 6, 8, 11, 0, tzinfo=timezone.utc).isoformat()}
    (memory / ".compaction-state.json").write_text(json.dumps(state), encoding="utf-8")
    _write_daily(memory, "2026-06-08", lines=2)
    tree = CompactionTree(
        memory, _config(), summarizer=_RecordingSummarizer(), clock=_fixed_clock(FIXED_NOW)
    )
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is False
    assert result.skipped_reason == "cooldown"
    assert not (memory / "ROOT.md").exists()


def test_force_ignores_cooldown(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    state = {"last_compaction_run": datetime(2026, 6, 8, 11, 0, tzinfo=timezone.utc).isoformat()}
    (memory / ".compaction-state.json").write_text(json.dumps(state), encoding="utf-8")
    _write_daily(memory, "2026-06-08", lines=2)
    tree = CompactionTree(
        memory, _config(), summarizer=_RecordingSummarizer(), clock=_fixed_clock(FIXED_NOW)
    )
    result = tree.run(today=date(2026, 6, 8), force=True)
    assert result.ran is True
    assert (memory / "ROOT.md").is_file()


def test_run_stamps_state(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-08", lines=2)
    tree = CompactionTree(
        memory, _config(), summarizer=_RecordingSummarizer(), clock=_fixed_clock(FIXED_NOW)
    )
    tree.run(today=date(2026, 6, 8))
    state = json.loads((memory / ".compaction-state.json").read_text(encoding="utf-8"))
    assert state["last_compaction_run"] == FIXED_NOW.isoformat()


def test_cooldown_past_window_runs(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    # Last run 48h before FIXED_NOW; cooldown 24h → should run.
    old = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    (memory / ".compaction-state.json").write_text(
        json.dumps({"last_compaction_run": old.isoformat()}), encoding="utf-8"
    )
    _write_daily(memory, "2026-06-08", lines=2)
    tree = CompactionTree(
        memory, _config(), summarizer=_RecordingSummarizer(), clock=_fixed_clock(FIXED_NOW)
    )
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True


# ---------------------------------------------------------------------------
# Redaction before write
# ---------------------------------------------------------------------------


def test_secrets_redacted_before_write(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    secret_line = "- API_KEY=sk-live-abcdef0123456789 leaked here"
    _write_daily(memory, "2026-06-08", lines=1, text=secret_line)
    tree = CompactionTree(memory, _config(rootMaxTokens=4000), summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    root = (memory / "ROOT.md").read_text(encoding="utf-8")
    assert "sk-live-abcdef0123456789" not in root
    assert "[redacted]" in root


def test_append_daily_redacts(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    path = append_daily_entry(
        memory, "Bearer abcdef0123456789ABCDEF token", today=date(2026, 6, 8)
    )
    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert "abcdef0123456789ABCDEF" not in content


# ---------------------------------------------------------------------------
# append_daily_entry
# ---------------------------------------------------------------------------


def test_append_daily_creates_and_appends(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    p1 = append_daily_entry(memory, "first fact", today=date(2026, 6, 8))
    p2 = append_daily_entry(memory, "second fact", today=date(2026, 6, 8))
    assert p1 == p2 == memory / "daily" / "2026-06-08.md"
    content = p1.read_text(encoding="utf-8")
    assert "first fact" in content
    assert "second fact" in content
    # Appended, not overwritten.
    assert content.index("first fact") < content.index("second fact")


def test_append_daily_path_safe(tmp_path: Path) -> None:
    """A normal date always produces a safe basename confined under memory/."""
    memory = tmp_path / "memory"
    path = append_daily_entry(memory, "x", today=date(2026, 6, 8))
    assert path is not None
    assert path.resolve().is_relative_to(memory.resolve())


# ---------------------------------------------------------------------------
# Fail-soft: a bad tier file does not crash the run
# ---------------------------------------------------------------------------


def test_run_never_raises_on_garbage_files(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    daily = memory / "daily"
    daily.mkdir(parents=True)
    # A non-date-named md file in the daily dir must be ignored, not crash.
    (daily / "notes.md").write_text("- stray\n", encoding="utf-8")
    (daily / "2026-06-08.md").write_text("- ok\n", encoding="utf-8")
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    result = tree.run(today=date(2026, 6, 8))
    assert result.ran is True


# ---------------------------------------------------------------------------
# Multi-period rollup: distinct weeks + correct monthly folding
# ---------------------------------------------------------------------------


def test_two_completed_weeks_produce_two_weekly_files(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # Two distinct completed ISO weeks, both well before today.
    # 2026-W18: Mon 2026-04-27 .. Sun 2026-05-03.
    _write_daily(memory, "2026-04-27", lines=2)
    _write_daily(memory, "2026-04-28", lines=2)
    # 2026-W19: Mon 2026-05-04 .. Sun 2026-05-10.
    _write_daily(memory, "2026-05-04", lines=2)
    _write_daily(memory, "2026-05-05", lines=2)
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    weekly_dir = memory / "weekly"
    week_files = sorted(p.name for p in weekly_dir.glob("*.md"))
    assert week_files == ["2026-W18.md", "2026-W19.md"]
    w18 = (weekly_dir / "2026-W18.md").read_text(encoding="utf-8")
    w19 = (weekly_dir / "2026-W19.md").read_text(encoding="utf-8")
    assert "2026-04-27" in w18 and "2026-04-28" in w18
    assert "2026-05-04" in w19 and "2026-05-05" in w19


def test_multiple_weeks_fold_into_single_monthly_bucket(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    weekly = memory / "weekly"
    weekly.mkdir(parents=True)
    # Three weeks whose Mondays all fall in 2026-03:
    #   2026-W10 Mon 2026-03-02, 2026-W11 Mon 2026-03-09, 2026-W12 Mon 2026-03-16.
    (weekly / "2026-W10.md").write_text("- w10 summary\n", encoding="utf-8")
    (weekly / "2026-W11.md").write_text("- w11 summary\n", encoding="utf-8")
    (weekly / "2026-W12.md").write_text("- w12 summary\n", encoding="utf-8")
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    monthly_dir = memory / "monthly"
    month_files = sorted(p.name for p in monthly_dir.glob("*.md"))
    assert month_files == ["2026-03.md"]  # all folded into one bucket
    march = (monthly_dir / "2026-03.md").read_text(encoding="utf-8")
    assert "w10 summary" in march
    assert "w11 summary" in march
    assert "w12 summary" in march


# ---------------------------------------------------------------------------
# ISO year-rollover correctness (week bucketing across the year boundary)
# ---------------------------------------------------------------------------


def test_iso_week_year_rollover_late_december(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # 2025-12-29 is a Monday belonging to ISO week 2026-W01.
    _write_daily(memory, "2025-12-29", lines=2)
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    assert (memory / "weekly" / "2026-W01.md").is_file()
    # And NOT a (wrong) 2025-W## bucket for that date.
    assert not (memory / "weekly" / "2025-W52.md").exists()
    assert not (memory / "weekly" / "2025-W53.md").exists()


def test_iso_week_year_rollover_early_january(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    # 2027-01-03 is a Sunday belonging to ISO week 2026-W53.
    _write_daily(memory, "2027-01-03", lines=2)
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    # today is in 2027 so the late-2026 ISO week is completed.
    tree.run(today=date(2027, 3, 1))
    assert (memory / "weekly" / "2026-W53.md").is_file()
    assert not (memory / "weekly" / "2027-W01.md").exists()


# ---------------------------------------------------------------------------
# consolidate byte-cap path vs structure preservation under the cap
# ---------------------------------------------------------------------------


def test_oversized_tier_triggers_byte_cap_guard(tmp_path: Path) -> None:
    from magi_agent.memory.compaction_tree import _DEFAULT_FILE_CAP_BYTES

    memory = tmp_path / "memory"
    # A completed prior day whose body EXCEEDS the 256KB cap, made of UNIQUE flat
    # bullets so dedup alone cannot shrink it — the oldest-drop byte guard must
    # fire when this rolls up through ``_write`` into the weekly tier.  The
    # weekly threshold is set absurdly high so summarization never runs and the
    # full oversized text reaches ``_write``.
    big = "\n".join(f"- [note] unique fact number {i} padding xyz" for i in range(20000))
    assert len(big.encode("utf-8")) > _DEFAULT_FILE_CAP_BYTES
    # 2026-04-27 is in completed week 2026-W18 (before today).
    _write_daily(memory, "2026-04-27", text=big)
    tree = CompactionTree(
        memory, _config(weeklyThreshold=10_000_000), summarizer=None
    )
    tree.run(today=date(2026, 6, 8))
    written = (memory / "weekly" / "2026-W18.md").read_text(encoding="utf-8")
    # Size guard fired → final weekly file is within the cap.
    assert len(written.encode("utf-8")) <= _DEFAULT_FILE_CAP_BYTES


def test_structured_content_under_cap_preserved(tmp_path: Path) -> None:
    """Under the cap, structured markdown is written verbatim (no dedup flatten).

    A recurring ``- standup done`` bullet across two days must NOT collapse the
    structure (headers / blank-line separators) now that consolidate no longer
    runs under-cap.
    """
    memory = tmp_path / "memory"
    # Two completed days in the same ISO week (2026-W18), each with a duplicate
    # bullet that would be deduped if consolidate ran on the structured rollup.
    _write_daily(
        memory,
        "2026-04-27",
        text="- standup done\n- shipped feature A",
    )
    _write_daily(
        memory,
        "2026-04-28",
        text="- standup done\n- shipped feature B",
    )
    # High thresholds so no summarization fires; we want the raw combined text.
    tree = CompactionTree(
        memory, _config(weeklyThreshold=10_000), summarizer=None
    )
    tree.run(today=date(2026, 6, 8))
    weekly = (memory / "weekly" / "2026-W18.md").read_text(encoding="utf-8")
    # Structure intact: the header is present and both days' content survive.
    assert "# Week 2026-W18" in weekly
    assert "shipped feature A" in weekly and "shipped feature B" in weekly
    # The duplicate bullet is NOT deduped — it appears for BOTH days.
    assert weekly.count("- standup done") == 2
    # Blank-line separators between blocks are preserved.
    assert "\n\n" in weekly


# ---------------------------------------------------------------------------
# Atomic write: no leftover temp file remains
# ---------------------------------------------------------------------------


def _temp_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.compact.tmp")]


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    _write_daily(memory, "2026-06-08", lines=2)
    tree = CompactionTree(memory, _config(), summarizer=_RecordingSummarizer())
    tree.run(today=date(2026, 6, 8))
    root = memory / "ROOT.md"
    assert root.is_file()
    # The atomic tmp file is renamed into place — nothing left behind.
    assert _temp_files(memory) == []
    # Content is the real (correct) ROOT document.
    assert "# Memory Root (synthesized)" in root.read_text(encoding="utf-8")


def test_append_daily_entry_leaves_no_temp_file(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    append_daily_entry(memory, "first fact", today=date(2026, 6, 8))
    append_daily_entry(memory, "second fact", today=date(2026, 6, 8))
    daily = memory / "daily" / "2026-06-08.md"
    assert daily.is_file()
    assert _temp_files(memory) == []
    content = daily.read_text(encoding="utf-8")
    assert "first fact" in content and "second fact" in content
