"""Task 5.1 / 5.2 — gated background memory-review harness (A1).

The harness ports Hermes' periodic BACKGROUND REVIEW into magi's gated model:
after every N turns a (later, live) reviewer re-reads the transcript and the
facts it surfaces are routed through the PR2 write pipeline (declarative filter
+ gated MemoryWriteToolHost). DEFAULT OFF; never reachable without an explicit
config flag AND the env gate.

These tests inject a FAKE reviewer — no live model is ever called.

  (a) disabled (config + env off) → reviewer never called, no writes
  (b) enabled + fake reviewer returning a declarative fact → routed to write_host
  (c) enabled + fake reviewer returning task-state facts → all dropped by filter
  (d) receipt counts (candidates / dropped / written) are correct
  + should_run_review N-turn trigger (Task 5.2)
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_live_write_host(tmp_path: Path):
    """Build a PR2 write-host that resolves to 'live' via the local-dev path.

    Requires the three memory-write env gates set by the caller. Returns a
    MemoryWriteToolHost whose ``_handle`` performs a real append-only write.
    """
    from magi_agent.runtime.memory_write_wiring import build_memory_write_host

    return build_memory_write_host(
        workspace_root=tmp_path,
        bot_id="bot-review",
        user_id="user-review",
    )


def _spy_reviewer(return_value: list[str]):
    """Return (reviewer, calls) where calls records each transcript passed in."""
    calls: list[list[dict]] = []

    def reviewer(transcript: list[dict]) -> list[str]:
        calls.append(transcript)
        return list(return_value)

    return reviewer, calls


# ---------------------------------------------------------------------------
# (a) disabled → reviewer never called, no writes
# ---------------------------------------------------------------------------


def test_review_disabled_config_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.enabled=False → reviewer never called, disabled receipt."""
    monkeypatch.setenv("MAGI_MEMORY_REVIEW_ENABLED", "1")  # env on, config off

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    reviewer, calls = _spy_reviewer(["user prefers Korean responses"])
    host = _make_live_write_host(tmp_path)

    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=False))
    receipt = asyncio.run(
        harness.review(
            [{"role": "user", "content": "hi"}],
            reviewer=reviewer,
            write_host=host,
        )
    )

    assert calls == []  # reviewer never invoked
    assert receipt.status == "disabled"
    assert receipt.candidates == 0
    assert receipt.attempted_writes == 0
    assert receipt.write_receipts == ()


def test_review_disabled_env_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.enabled=True but env gate off → reviewer never called."""
    monkeypatch.delenv("MAGI_MEMORY_REVIEW_ENABLED", raising=False)

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    reviewer, calls = _spy_reviewer(["user prefers Korean responses"])
    host = _make_live_write_host(tmp_path)

    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True))
    receipt = asyncio.run(
        harness.review(
            [{"role": "user", "content": "hi"}],
            reviewer=reviewer,
            write_host=host,
        )
    )

    assert calls == []
    assert receipt.status == "disabled"
    assert receipt.attempted_writes == 0


# ---------------------------------------------------------------------------
# (b) enabled + declarative fact → routed to write_host
# ---------------------------------------------------------------------------


def test_review_enabled_routes_declarative_fact_to_write_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enabled + fact 'user prefers Korean responses' → write_host saw it."""
    monkeypatch.setenv("MAGI_MEMORY_REVIEW_ENABLED", "1")
    # Live write gates so the write actually lands on disk (proves routing).
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_LOCAL_DEV", "1")

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    fact = "user prefers Korean responses"
    reviewer, calls = _spy_reviewer([fact])

    # Record every fact the host is asked to write.
    host = _make_live_write_host(tmp_path)
    seen: list[str] = []
    original_handle = host._handle

    async def _spy_handle(arguments, context):  # type: ignore[no-untyped-def]
        seen.append(str(arguments.get("fact")))
        return await original_handle(arguments, context)

    monkeypatch.setattr(host, "_handle", _spy_handle)

    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True))
    receipt = asyncio.run(
        harness.review(
            [{"role": "user", "content": "please answer in Korean"}],
            reviewer=reviewer,
            write_host=host,
        )
    )

    assert len(calls) == 1  # reviewer called exactly once
    assert seen == [fact]  # the host saw the fact
    assert receipt.status == "reviewed"
    assert receipt.candidates == 1
    assert receipt.dropped_declarative == 0
    assert receipt.attempted_writes == 1
    assert len(receipt.write_receipts) == 1
    # Live mode → a real write occurred.
    written = receipt.write_receipts[0]
    assert written.status == "ok"
    assert written.real_write is True


# ---------------------------------------------------------------------------
# (c) enabled + task-state facts → all dropped by declarative filter
# ---------------------------------------------------------------------------


def test_review_drops_task_state_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task-state candidates are dropped pre-write; zero writes attempted."""
    monkeypatch.setenv("MAGI_MEMORY_REVIEW_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_LOCAL_DEV", "1")

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    reviewer, _calls = _spy_reviewer(["merged PR #123", "commit a1b2c3d4 done"])

    host = _make_live_write_host(tmp_path)
    seen: list[str] = []
    original_handle = host._handle

    async def _spy_handle(arguments, context):  # type: ignore[no-untyped-def]
        seen.append(str(arguments.get("fact")))
        return await original_handle(arguments, context)

    monkeypatch.setattr(host, "_handle", _spy_handle)

    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True))
    receipt = asyncio.run(
        harness.review(
            [{"role": "assistant", "content": "shipped it"}],
            reviewer=reviewer,
            write_host=host,
        )
    )

    assert seen == []  # nothing routed to the host
    assert receipt.status == "reviewed"
    assert receipt.candidates == 2
    assert receipt.dropped_declarative == 2
    assert receipt.attempted_writes == 0
    assert receipt.write_receipts == ()


# ---------------------------------------------------------------------------
# (d) receipt counts are correct for a mixed batch
# ---------------------------------------------------------------------------


def test_review_receipt_counts_mixed_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixed declarative + task-state + empty candidates → exact counts."""
    monkeypatch.setenv("MAGI_MEMORY_REVIEW_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_LOCAL_DEV", "1")

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    candidates = [
        "user prefers Korean responses",  # declarative → written
        "merged PR #123",                  # task-state → dropped
        "user is a solo founder",          # declarative → written
        "   ",                             # empty → dropped (non-declarative)
    ]
    reviewer, _calls = _spy_reviewer(candidates)
    host = _make_live_write_host(tmp_path)

    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True))
    receipt = asyncio.run(
        harness.review(
            [{"role": "user", "content": "context"}],
            reviewer=reviewer,
            write_host=host,
        )
    )

    assert receipt.candidates == 4
    assert receipt.dropped_declarative == 2
    assert receipt.attempted_writes == 2
    assert receipt.written == 2
    assert len(receipt.write_receipts) == 2


def test_review_empty_candidates_yields_zero_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer returns no candidates → reviewed receipt with all-zero counts."""
    monkeypatch.setenv("MAGI_MEMORY_REVIEW_ENABLED", "1")

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    reviewer, calls = _spy_reviewer([])
    host = _make_live_write_host(tmp_path)

    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True))
    receipt = asyncio.run(harness.review([], reviewer=reviewer, write_host=host))

    assert len(calls) == 1
    assert receipt.status == "reviewed"
    assert receipt.candidates == 0
    assert receipt.attempted_writes == 0


# ---------------------------------------------------------------------------
# Error isolation — a raising reviewer / write_host must not crash the review
# or leak the exception into the caller's turn.
# ---------------------------------------------------------------------------


def test_review_isolates_raising_reviewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer that raises is isolated → reviewed receipt, no propagation."""
    monkeypatch.setenv("MAGI_MEMORY_REVIEW_ENABLED", "1")

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    def boom_reviewer(_transcript: list[dict]) -> list[str]:
        raise RuntimeError("reviewer blew up (secret: hunter2)")

    host = _make_live_write_host(tmp_path)
    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True))

    # Must NOT raise.
    receipt = asyncio.run(
        harness.review(
            [{"role": "user", "content": "x"}],
            reviewer=boom_reviewer,
            write_host=host,
        )
    )

    assert receipt.status == "reviewed"
    assert receipt.candidates == 0
    assert receipt.attempted_writes == 0
    assert receipt.write_receipts == ()
    assert "reviewer_exception" in receipt.reason_codes


def test_review_isolates_raising_write_host_per_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write_host._handle that raises for ONE fact is isolated to that fact.

    The raising fact yields a ``blocked`` receipt with a generic reason code;
    the remaining declarative facts are still processed and written.
    """
    monkeypatch.setenv("MAGI_MEMORY_REVIEW_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_LOCAL_DEV", "1")

    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
    )

    bad_fact = "user prefers dark mode"
    good_fact = "user is a solo founder"
    reviewer, _calls = _spy_reviewer([bad_fact, good_fact])

    host = _make_live_write_host(tmp_path)
    original_handle = host._handle

    async def _flaky_handle(arguments, context):  # type: ignore[no-untyped-def]
        if str(arguments.get("fact")) == bad_fact:
            raise RuntimeError("handle exploded (secret: hunter2)")
        return await original_handle(arguments, context)

    monkeypatch.setattr(host, "_handle", _flaky_handle)

    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True))
    receipt = asyncio.run(
        harness.review(
            [{"role": "user", "content": "context"}],
            reviewer=reviewer,
            write_host=host,
        )
    )

    # Both facts are declarative → both attempted; one blocked, one written.
    assert receipt.status == "reviewed"
    assert receipt.candidates == 2
    assert receipt.dropped_declarative == 0
    assert receipt.attempted_writes == 2
    assert receipt.blocked == 1
    assert receipt.written == 1
    assert len(receipt.write_receipts) == 2

    by_status = {r.status: r for r in receipt.write_receipts}
    blocked_receipt = by_status["blocked"]
    assert blocked_receipt.real_write is False
    assert "review_write_exception" in blocked_receipt.reason_codes
    # Generic reason only — the raw exception text (with the secret) must not leak.
    assert all("hunter2" not in code for code in blocked_receipt.reason_codes)


# ---------------------------------------------------------------------------
# Authority pins must remain Literal[False]
# ---------------------------------------------------------------------------


def test_review_config_authority_pins_locked() -> None:
    """Forged authority flags are coerced to False (Literal[False] is force-false).

    C-4 PR-G2 (raise-to-coerce): the introspection-based
    :class:`FalseOnlyAuthorityModel` now coerces forged ``Literal[False]``
    fields to ``False`` uniformly across construct/copy/validate, instead
    of raising a ``ValidationError`` on a forged True via ``model_validate``.

    The end-result invariant is preserved: a caller cannot smuggle a
    forged True through any of construct / copy / validate — the value
    still reads ``False`` in the resulting config.
    """
    from magi_agent.harness.memory_review import MemoryReviewConfig

    # A forged truthy value via model_validate is coerced (raise-to-coerce).
    validated = MemoryReviewConfig.model_validate(
        {
            "enabled": True,
            "backgroundReviewRunnerAttached": True,
            "liveReviewerAttached": True,
            "productionWritesEnabled": True,
        }
    )
    assert validated.background_review_runner_attached is False
    assert validated.live_reviewer_attached is False
    assert validated.production_writes_enabled is False

    # The default config pins are all False, and model_copy cannot lift them.
    config = MemoryReviewConfig(enabled=True)
    assert config.background_review_runner_attached is False
    assert config.live_reviewer_attached is False
    assert config.production_writes_enabled is False

    copied = config.model_copy(update={"backgroundReviewRunnerAttached": True})
    assert copied.background_review_runner_attached is False
    assert copied.live_reviewer_attached is False
    assert copied.production_writes_enabled is False

    constructed = MemoryReviewConfig.model_construct(
        enabled=True,
        background_review_runner_attached=True,
        live_reviewer_attached=True,
        production_writes_enabled=True,
    )
    assert constructed.background_review_runner_attached is False
    assert constructed.live_reviewer_attached is False
    assert constructed.production_writes_enabled is False


def test_review_receipt_fact_preview_is_digest_only() -> None:
    """Write receipts must not echo raw reviewer facts or secrets."""
    from magi_agent.harness.memory_review import _to_write_receipt

    class _Result:
        status = "ok"
        metadata = {"reasonCodes": ("memory_write_live",)}
        output = {"realWrite": True}

    fact = "user api token is sk-test-secret-value"
    receipt = _to_write_receipt(fact, _Result())

    assert receipt.fact_preview.startswith("sha256:")
    assert "sk-test-secret-value" not in receipt.fact_preview
    assert "user api token" not in receipt.fact_preview


# ---------------------------------------------------------------------------
# Task 5.2 — should_run_review N-turn trigger
# ---------------------------------------------------------------------------


def test_should_run_review_off_never_triggers() -> None:
    from magi_agent.harness.memory_review import should_run_review

    for turn in range(0, 40):
        assert (
            should_run_review(turn, interval_turns=10, enabled=False) is False
        )


def test_should_run_review_interval_boundaries() -> None:
    from magi_agent.harness.memory_review import should_run_review

    # turn 0 never triggers (no turns yet)
    assert should_run_review(0, interval_turns=10, enabled=True) is False
    # boundaries
    assert should_run_review(10, interval_turns=10, enabled=True) is True
    assert should_run_review(20, interval_turns=10, enabled=True) is True
    # off-boundary
    assert should_run_review(5, interval_turns=10, enabled=True) is False
    assert should_run_review(11, interval_turns=10, enabled=True) is False
    # different interval
    assert should_run_review(3, interval_turns=3, enabled=True) is True
    assert should_run_review(6, interval_turns=3, enabled=True) is True
    assert should_run_review(4, interval_turns=3, enabled=True) is False
