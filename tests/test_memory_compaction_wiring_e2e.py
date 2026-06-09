"""PR-B E2E — flags ON => transcript flush → compaction → ROOT → next-session recall.

This is the load-bearing test for PR-B's governance invariant: when the
activation flags are turned ON the whole memory loop ACTUALLY works end-to-end,
proven without a real model (deterministic fake summarizer + fixed dates):

  1. simulate a couple of turns via the turn-end hook → memory/daily/*.md written
  2. trigger compaction → memory/ROOT.md generated
  3. project the snapshot for the NEXT session → the injected <memory-context>
     contains the synthesized ROOT content

The gating counterpart (master OFF => everything inert) is at the bottom.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from magi_agent.memory.compaction_tree import CompactionTree
from magi_agent.memory.config import MemoryRuntimeConfig
from magi_agent.memory.prompt_projection import (
    MAGI_MEMORY_PROJECTION_ENABLED_ENV,
    project_memory_snapshot,
)
from magi_agent.runtime import memory_turn_hook
from magi_agent.runtime.memory_turn_hook import (
    record_turn,
    reset_session_compaction_state,
)


class _FakeSummarizer:
    """Deterministic, model-free summarizer (prefixes a stable marker)."""

    def summarize(self, text: str) -> str:
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        return f"SUMMARY:: {first.strip()}"


@pytest.fixture(autouse=True)
def _clear_session_state() -> None:
    reset_session_compaction_state()
    yield
    reset_session_compaction_state()


def _flags_on() -> MemoryRuntimeConfig:
    return MemoryRuntimeConfig(
        masterEnabled=True,
        writeEnabled=True,
        compactionEnabled=True,
        projectionEnabled=True,
        recallEnabled=True,
        # tiny thresholds so even a couple of turns roll up deterministically
        dailyThreshold=1,
        cooldownHours=24,
    )


def test_flags_on_full_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _flags_on()
    today = date(2026, 6, 8)
    summarizer = _FakeSummarizer()

    # 1. Two turns through the wired turn-end hook → daily file written.
    record_turn(
        workspace_root=tmp_path,
        session_id="day-session",
        turn_id="t1",
        user_text="refactor the billing module and add tests",
        assistant_text="Refactored billing.py, added 3 tests, all green.",
        used_tool=True,
        config=cfg,
        today=today,
        summarizer=summarizer,
    )
    record_turn(
        workspace_root=tmp_path,
        session_id="day-session",
        turn_id="t2",
        user_text="now wire the webhook signature check",
        assistant_text="Added HMAC verification to the webhook handler.",
        used_tool=True,
        config=cfg,
        today=today,
        summarizer=summarizer,
    )

    daily = sorted((tmp_path / "memory" / "daily").glob("*.md"))
    assert [p.name for p in daily] == ["2026-06-08.md"]
    daily_body = daily[0].read_text(encoding="utf-8")
    assert "billing module" in daily_body
    assert "webhook signature" in daily_body

    # 2. Trigger compaction explicitly (force past cooldown) → ROOT.md generated.
    #    Use a fixed clock so the run is hermetic.
    tree = CompactionTree(
        tmp_path / "memory",
        cfg,
        summarizer=summarizer,
        clock=lambda: datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
    )
    result = tree.run(today=today, force=True)
    assert result.ran is True
    root = tmp_path / "memory" / "ROOT.md"
    assert root.is_file()
    root_text = root.read_text(encoding="utf-8")
    # ROOT synthesizes the recent-daily window, so the day's content is present.
    assert "billing module" in root_text or "webhook signature" in root_text

    # 3. NEXT session projection → injected memory-context includes ROOT content.
    monkeypatch.setenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, "1")
    projection = project_memory_snapshot(workspace_root=tmp_path)
    assert projection.enabled is True
    assert "memory/ROOT.md" in projection.files_loaded
    assert "billing module" in projection.snapshot_block or (
        "webhook signature" in projection.snapshot_block
    )


def test_record_turn_hook_generates_root_through_wired_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The HOOK's own compaction trigger (not an out-of-band CompactionTree)
    must generate ROOT.md.  We drive ROOT generation THROUGH ``record_turn``
    with ``compaction_enabled=True``: the wired ``_maybe_run_compaction`` calls
    ``run(force=False)``, so this also proves the path clears the engine's
    cooldown (fresh workspace => no prior stamp) and rolls up real daily content.

    Seeds enough prior-day daily content (over the tiny daily_threshold) so the
    in-place daily summarization fires through the injected fake summarizer, and
    pins the clock for hermetic cooldown bookkeeping.
    """
    cfg = _flags_on()
    today = date(2026, 6, 8)
    summarizer = _FakeSummarizer()

    # Seed a COMPLETED prior day's raw daily log with enough lines that the
    # wired compaction (daily_threshold=1) actually summarizes + rolls it into
    # ROOT — proving real work, not just a touch.
    daily_dir = tmp_path / "memory" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "2026-06-07.md").write_text(
        "- [turn seed-a] prior work on the auth migration\n"
        "- [turn seed-b] prior work on the rate limiter\n",
        encoding="utf-8",
    )

    # Patch the hook's CompactionTree so the wired ``run(force=False)`` uses a
    # FIXED clock (hermetic cooldown), while everything else stays the real path.
    real_tree = memory_turn_hook.CompactionTree

    class _ClockedTree(real_tree):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            kwargs.setdefault(
                "clock", lambda: datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
            )
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(memory_turn_hook, "CompactionTree", _ClockedTree)

    # One turn through the wired hook: flushes today's daily AND fires the
    # once-per-session compaction trigger (run(force=False)) — no out-of-band
    # CompactionTree, no force=True.
    record_turn(
        workspace_root=tmp_path,
        session_id="hook-session",
        turn_id="t1",
        user_text="finish the auth migration and the rate limiter rollout",
        assistant_text="Completed auth migration; rate limiter shipped.",
        used_tool=True,
        config=cfg,
        today=today,
        summarizer=summarizer,
    )

    # The HOOK (not the test) generated ROOT.md from the rolled-up daily content.
    root = tmp_path / "memory" / "ROOT.md"
    assert root.is_file(), "wired record_turn trigger must generate ROOT.md"
    root_text = root.read_text(encoding="utf-8")
    assert "auth migration" in root_text or "rate limiter" in root_text
    # The fake summarizer's marker proves the daily tier was actually summarized
    # (real rollup work), not merely concatenated.
    assert "SUMMARY::" in root_text


def test_master_off_keeps_everything_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Master OFF => sub-flags follow it OFF (the PR-B default).
    cfg = MemoryRuntimeConfig(masterEnabled=False)
    assert cfg.write_enabled is False
    assert cfg.compaction_enabled is False

    track: list[str] = []
    real_tree = memory_turn_hook.CompactionTree

    class _Tracking(real_tree):  # type: ignore[misc, valid-type]
        def run(self, *, today, force=False):  # noqa: ANN001, ANN202
            track.append("ran")
            return super().run(today=today, force=force)

    monkeypatch.setattr(memory_turn_hook, "CompactionTree", _Tracking)

    record_turn(
        workspace_root=tmp_path,
        session_id="s",
        turn_id="t1",
        user_text="substantial prompt about the deploy pipeline and rollout",
        assistant_text="a long substantial assistant reply about the rollout " * 3,
        used_tool=True,
        config=cfg,
        today=date(2026, 6, 8),
    )

    # No daily file, no compaction run.
    assert not (tmp_path / "memory" / "daily").exists()
    assert track == []

    # Projection gate also off => no injection even if a ROOT existed.
    monkeypatch.delenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, raising=False)
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "ROOT.md").write_text("ROOT_LEAK", encoding="utf-8")
    projection = project_memory_snapshot(workspace_root=tmp_path)
    assert projection.enabled is False
    assert projection.snapshot_block == ""
