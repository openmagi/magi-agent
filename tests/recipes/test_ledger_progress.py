"""Tests for the progress ledger — Phase 0b (StallVerdict + detect_stall)
and Phase 2 (ProgressLedgerEntry, ProgressLedgerContract, helpers).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.recipes.ledger_progress import (
    # Phase 0b
    StallKind,
    StallVerdict,
    detect_stall,
    # Phase 2
    ProgressStepVerdict,
    ProgressLedgerEntry,
    ProgressLedgerContract,
    make_progress_ledger_entry,
    make_progress_ledger,
    update_progress_ledger,
    derive_step_verdict,
)


# ---------------------------------------------------------------------------
# detect_stall — happy path (ok)
# ---------------------------------------------------------------------------

def _ok_kwargs() -> dict:
    return dict(
        consecutive_stalled_steps=0,
        stall_threshold=3,
        total_steps_taken=5,
        step_budget=20,
        total_tokens_used=10_000,
        token_budget=400_000,
        total_wall_ms=30_000,
        wall_budget_ms=240_000,
        replan_count=0,
        max_replan_count=2,
    )


class TestDetectStallOk:
    def test_all_within_budget_returns_ok(self) -> None:
        verdict = detect_stall(**_ok_kwargs())
        assert verdict.kind == StallKind.ok

    def test_ok_verdict_is_frozen(self) -> None:
        verdict = detect_stall(**_ok_kwargs())
        with pytest.raises((TypeError, ValidationError)):
            verdict.kind = StallKind.stall_threshold_exceeded  # type: ignore[misc]

    def test_ok_verdict_has_digest(self) -> None:
        verdict = detect_stall(**_ok_kwargs())
        d = verdict.verdict_digest()
        assert d.startswith("sha256:")
        assert len(d) == 71

    def test_ok_default_off_true(self) -> None:
        verdict = detect_stall(**_ok_kwargs())
        assert verdict.default_off is True


# ---------------------------------------------------------------------------
# detect_stall — each non-ok kind
# ---------------------------------------------------------------------------

class TestDetectStallKinds:
    def test_stall_threshold_exceeded(self) -> None:
        kwargs = _ok_kwargs()
        kwargs["consecutive_stalled_steps"] = 3  # == threshold
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.stall_threshold_exceeded

    def test_step_budget_exhausted(self) -> None:
        kwargs = _ok_kwargs()
        kwargs["total_steps_taken"] = 20  # == budget
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.step_budget_exhausted

    def test_token_budget_exhausted(self) -> None:
        kwargs = _ok_kwargs()
        kwargs["total_tokens_used"] = 400_000  # == budget
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.token_budget_exhausted

    def test_wall_budget_exhausted(self) -> None:
        kwargs = _ok_kwargs()
        kwargs["total_wall_ms"] = 240_000  # == budget
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.wall_budget_exhausted

    def test_replan_count_exhausted(self) -> None:
        kwargs = _ok_kwargs()
        kwargs["replan_count"] = 3  # > max_replan_count=2
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.replan_count_exhausted

    def test_wall_priority_over_token(self) -> None:
        """Wall-clock budget fires before token budget."""
        kwargs = _ok_kwargs()
        kwargs["total_wall_ms"] = 240_000
        kwargs["total_tokens_used"] = 400_000
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.wall_budget_exhausted

    def test_token_priority_over_step(self) -> None:
        kwargs = _ok_kwargs()
        kwargs["total_tokens_used"] = 400_000
        kwargs["total_steps_taken"] = 20
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.token_budget_exhausted

    def test_step_priority_over_replan(self) -> None:
        kwargs = _ok_kwargs()
        kwargs["total_steps_taken"] = 20
        kwargs["replan_count"] = 3
        verdict = detect_stall(**kwargs)
        assert verdict.kind == StallKind.step_budget_exhausted


# ---------------------------------------------------------------------------
# StallVerdict digest
# ---------------------------------------------------------------------------

class TestStallVerdictDigest:
    def test_different_kinds_different_digest(self) -> None:
        ok_kwargs = _ok_kwargs()
        ok = detect_stall(**ok_kwargs)
        stall_kwargs = {**ok_kwargs, "consecutive_stalled_steps": 3}
        stall = detect_stall(**stall_kwargs)
        assert ok.verdict_digest() != stall.verdict_digest()

    def test_same_state_same_digest(self) -> None:
        v1 = detect_stall(**_ok_kwargs())
        v2 = detect_stall(**_ok_kwargs())
        assert v1.verdict_digest() == v2.verdict_digest()

    def test_public_projection_contains_digest(self) -> None:
        v = detect_stall(**_ok_kwargs())
        proj = v.public_projection()
        assert proj["verdictDigest"] == v.verdict_digest()


# ---------------------------------------------------------------------------
# StallVerdict construction — consistency validation
# ---------------------------------------------------------------------------

class TestStallVerdictConsistency:
    def test_ok_with_stalled_steps_at_threshold_rejected(self) -> None:
        with pytest.raises(ValidationError, match="consecutive_stalled_steps"):
            StallVerdict(
                kind=StallKind.ok,
                consecutive_stalled_steps=3,
                stall_threshold=3,
                total_steps_taken=0,
                step_budget=20,
                total_tokens_used=0,
                token_budget=400_000,
                total_wall_ms=0,
                wall_budget_ms=240_000,
            )


# ---------------------------------------------------------------------------
# Phase 2 — ProgressStepVerdict + derive_step_verdict
# ---------------------------------------------------------------------------

class TestDeriveStepVerdict:
    def test_contradicted_takes_priority(self) -> None:
        v = derive_step_verdict(
            ("fact-1",),
            ("fact-2",),
            ("fact-x",),
            tokens_used=100,
            per_step_token_budget=20_000,
        )
        assert v == ProgressStepVerdict.contradicted

    def test_budget_exceeded_when_tokens_at_cap(self) -> None:
        v = derive_step_verdict((), (), (), tokens_used=20_000, per_step_token_budget=20_000)
        assert v == ProgressStepVerdict.budget_exceeded

    def test_advancing_when_facts_upgraded(self) -> None:
        v = derive_step_verdict(
            ("fact-1",),
            ("fact-1",),  # upgraded
            (),
            tokens_used=100,
            per_step_token_budget=20_000,
        )
        assert v == ProgressStepVerdict.advancing

    def test_speculative_when_only_facts_added(self) -> None:
        v = derive_step_verdict(
            ("fact-new",),
            (),
            (),
            tokens_used=100,
            per_step_token_budget=20_000,
        )
        assert v == ProgressStepVerdict.speculative

    def test_stalled_when_nothing(self) -> None:
        v = derive_step_verdict((), (), (), tokens_used=0, per_step_token_budget=20_000)
        assert v == ProgressStepVerdict.stalled


# ---------------------------------------------------------------------------
# Phase 2 — ProgressLedgerEntry
# ---------------------------------------------------------------------------

class TestProgressLedgerEntry:
    def test_make_entry_happy(self) -> None:
        entry = make_progress_ledger_entry(
            entry_id="entry:step-1",
            step_id="step:lookup",
            step_verdict=ProgressStepVerdict.advancing,
            facts_added=("fact-orcid",),
            facts_upgraded=("fact-orcid",),
            tokens_used=5_000,
            wall_ms=8_000,
        )
        assert entry.step_verdict == ProgressStepVerdict.advancing
        assert entry.default_off is True
        assert entry.entry_digest.startswith("sha256:")

    def test_entry_digest_validates(self) -> None:
        entry = make_progress_ledger_entry(
            entry_id="entry:step-1",
            step_id="step:lookup",
            step_verdict=ProgressStepVerdict.stalled,
        )
        # Re-validate: must not raise
        from pydantic import TypeAdapter
        ProgressLedgerEntry.model_validate(
            entry.model_dump(by_alias=True, mode="python", warnings=False)
        )

    def test_bad_entry_digest_rejected(self) -> None:
        with pytest.raises(ValidationError, match="entry_digest"):
            ProgressLedgerEntry(
                entry_id="entry:x",
                step_id="step:x",
                step_verdict=ProgressStepVerdict.stalled,
                entry_digest="sha256:" + "a" * 64,
            )

    def test_public_projection_shape(self) -> None:
        entry = make_progress_ledger_entry(
            entry_id="entry:step-1",
            step_id="step:lookup",
            step_verdict=ProgressStepVerdict.advancing,
            facts_added=("f1",),
        )
        proj = entry.public_projection()
        assert proj["entryId"] == "entry:step-1"
        assert proj["stepVerdict"] == "advancing"


# ---------------------------------------------------------------------------
# Phase 2 — ProgressLedgerContract
# ---------------------------------------------------------------------------

def _make_entry(
    step_id: str = "step:a",
    verdict: ProgressStepVerdict = ProgressStepVerdict.stalled,
    tokens: int = 0,
    wall: int = 0,
    n: int = 0,
) -> ProgressLedgerEntry:
    return make_progress_ledger_entry(
        entry_id=f"entry:{step_id}-{n}",
        step_id=step_id,
        step_verdict=verdict,
        tokens_used=tokens,
        wall_ms=wall,
    )


class TestProgressLedgerContract:
    def test_empty_ledger(self) -> None:
        ledger = make_progress_ledger(
            progress_id="prog:1",
            task_ledger_id="task:1",
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        assert ledger.total_steps_taken == 0
        assert ledger.consecutive_stalled_steps == 0
        assert ledger.progress_digest.startswith("sha256:")
        assert ledger.default_off is True

    def test_three_stalled_entries(self) -> None:
        e1 = _make_entry(n=1, verdict=ProgressStepVerdict.stalled)
        e2 = _make_entry(n=2, verdict=ProgressStepVerdict.stalled)
        e3 = _make_entry(n=3, verdict=ProgressStepVerdict.stalled)
        ledger = make_progress_ledger(
            progress_id="prog:2",
            task_ledger_id="task:2",
            entries=(e1, e2, e3),
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        assert ledger.consecutive_stalled_steps == 3
        # current_stall_verdict should fire stall_threshold_exceeded
        verdict = ledger.current_stall_verdict()
        assert verdict.kind == StallKind.stall_threshold_exceeded

    def test_stall_then_advance_resets_consecutive(self) -> None:
        e1 = _make_entry(n=1, verdict=ProgressStepVerdict.stalled)
        e2 = _make_entry(n=2, verdict=ProgressStepVerdict.stalled)
        e3 = _make_entry(n=3, verdict=ProgressStepVerdict.advancing)
        ledger = make_progress_ledger(
            progress_id="prog:3",
            task_ledger_id="task:3",
            entries=(e1, e2, e3),
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        assert ledger.consecutive_stalled_steps == 0

    def test_token_total_computed_correctly(self) -> None:
        e1 = _make_entry(n=1, tokens=5_000)
        e2 = _make_entry(n=2, tokens=8_000)
        ledger = make_progress_ledger(
            progress_id="prog:4",
            task_ledger_id="task:4",
            entries=(e1, e2),
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        assert ledger.total_tokens_used == 13_000

    def test_wall_total_computed_correctly(self) -> None:
        e1 = _make_entry(n=1, wall=10_000)
        e2 = _make_entry(n=2, wall=20_000)
        ledger = make_progress_ledger(
            progress_id="prog:5",
            task_ledger_id="task:5",
            entries=(e1, e2),
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        assert ledger.total_wall_ms == 30_000

    def test_wrong_total_steps_rejected(self) -> None:
        e1 = _make_entry(n=1)
        with pytest.raises(ValidationError, match="total_steps_taken"):
            ProgressLedgerContract(
                progress_id="prog:x",
                task_ledger_id="task:x",
                entries=(e1,),
                consecutive_stalled_steps=0,
                total_steps_taken=99,  # wrong
                total_tokens_used=0,
                total_wall_ms=0,
                stall_threshold=3,
                step_budget=20,
                replan_count=0,
                token_budget=400_000,
                wall_budget_ms=240_000,
                max_replan_count=2,
                progress_digest="sha256:" + "a" * 64,
            )

    def test_token_budget_exceeded_fires_in_verdict(self) -> None:
        e1 = _make_entry(n=1, tokens=400_000)
        ledger = make_progress_ledger(
            progress_id="prog:6",
            task_ledger_id="task:6",
            entries=(e1,),
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        verdict = ledger.current_stall_verdict()
        assert verdict.kind == StallKind.token_budget_exhausted

    def test_update_progress_ledger_appends(self) -> None:
        ledger = make_progress_ledger(
            progress_id="prog:7",
            task_ledger_id="task:7",
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        entry = _make_entry(n=1, verdict=ProgressStepVerdict.advancing)
        updated = update_progress_ledger(ledger, entry)
        assert updated.total_steps_taken == 1
        assert updated.progress_digest != ledger.progress_digest

    def test_update_progress_ledger_increments_replan(self) -> None:
        ledger = make_progress_ledger(
            progress_id="prog:8",
            task_ledger_id="task:8",
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
            replan_count=0,
        )
        entry = _make_entry(n=1)
        updated = update_progress_ledger(ledger, entry, replan_count=1)
        assert updated.replan_count == 1

    def test_public_projection_shape(self) -> None:
        ledger = make_progress_ledger(
            progress_id="prog:9",
            task_ledger_id="task:9",
            stall_threshold=3,
            step_budget=20,
            token_budget=400_000,
            wall_budget_ms=240_000,
        )
        proj = ledger.public_projection()
        assert proj["progressId"] == "prog:9"
        assert proj["defaultOff"] is True
        assert "progressDigest" in proj
