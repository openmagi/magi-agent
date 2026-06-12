"""C4.1 — strategy seams on the three memory harnesses (dual-load).

The C4 policy/mechanism split: the compaction denial DECISION, the recall
default namespace/projection policies, and the N-turn review TRIGGER become
injectable strategies. ``None`` (the default everywhere) preserves the exact
legacy behavior — proven by the memory golden oracle staying byte-identical.
Stores, receipts, and authority pins are untouched.
"""
from __future__ import annotations

import asyncio

from magi_agent.harness.memory_compaction import (
    MemoryCompactionHarness,
    MemoryCompactionPolicy,
    MemoryCompactionRequest,
)
from magi_agent.harness.memory_review import MemoryReviewConfig, MemoryReviewHarness


def _req() -> MemoryCompactionRequest:
    return MemoryCompactionRequest.model_validate(
        {
            "providerId": "p",
            "turnId": "t",
            "sourceRefs": ("evidence:s",),
            "evidenceRefs": ("evidence:e",),
        }
    )


def _pol() -> MemoryCompactionPolicy:
    return MemoryCompactionPolicy.model_validate(
        {
            "policyRef": "policy:x",
            "policySnapshotRef": "policy:x@snap",
            "localFakeCompactionAllowed": True,
        }
    )


def test_injected_denial_strategy_decides_compaction() -> None:
    def always_block(request, policy):
        _ = request, policy
        return "blocked", ("custom_strategy_block",)

    harness = MemoryCompactionHarness(
        {"enabled": True, "localFakeAdapterEnabled": True},
        denial_strategy=always_block,
    )
    result = asyncio.run(harness.compact(request=_req(), policy=_pol()))
    assert result.status == "blocked"
    assert result.reason_codes == ("custom_strategy_block",)


def test_default_denial_strategy_unchanged() -> None:
    harness = MemoryCompactionHarness({"enabled": True, "localFakeAdapterEnabled": True})
    result = asyncio.run(harness.compact(request=_req(), policy=_pol()))
    assert result.status == "success"


def test_review_harness_trigger_strategy() -> None:
    calls: list[int] = []

    def every_turn(turn_count: int, *, interval_turns: int, enabled: bool) -> bool:
        _ = interval_turns
        calls.append(turn_count)
        return enabled and turn_count > 0

    harness = MemoryReviewHarness(
        MemoryReviewConfig(enabled=True, intervalTurns=10), trigger=every_turn
    )
    assert harness.should_run(turn_count=3) is True
    assert calls == [3]


def test_review_harness_default_trigger_is_legacy_should_run_review() -> None:
    harness = MemoryReviewHarness(MemoryReviewConfig(enabled=True, intervalTurns=10))
    assert harness.should_run(turn_count=10) is True
    assert harness.should_run(turn_count=3) is False
    # config gate honored through the trigger arguments
    off = MemoryReviewHarness(MemoryReviewConfig(enabled=False, intervalTurns=10))
    assert off.should_run(turn_count=10) is False


def test_recall_harness_default_policies_are_used_when_none(monkeypatch) -> None:
    from magi_agent.harness.memory_recall import MemoryRecallHarness

    projection_sentinel = object()
    namespace_sentinel = object()
    captured: dict = {}

    async def fake_exec(**kwargs):
        captured.update(kwargs)

        class _R:
            status = "disabled"

        return _R()

    import magi_agent.harness.memory_recall as mr

    monkeypatch.setattr(mr, "execute_readonly_memory_recall", fake_exec)
    harness = MemoryRecallHarness(
        {},
        default_namespace_policy=namespace_sentinel,
        default_projection_policy=projection_sentinel,
    )
    asyncio.run(
        harness.recall(
            request={"scope": {"tenantId": "t", "botId": "b", "sessionKey": "s"},
                     "query": "q", "purpose": "answer_user"},
            namespace_policy=None,
            projection_policy=None,
        )
    )
    assert captured["projection_policy"] is projection_sentinel
    assert captured["namespace_policy"] is namespace_sentinel


def test_recall_harness_explicit_policies_take_precedence(monkeypatch) -> None:
    from magi_agent.harness.memory_recall import MemoryRecallHarness

    explicit = object()
    captured: dict = {}

    async def fake_exec(**kwargs):
        captured.update(kwargs)

        class _R:
            status = "disabled"

        return _R()

    import magi_agent.harness.memory_recall as mr

    monkeypatch.setattr(mr, "execute_readonly_memory_recall", fake_exec)
    harness = MemoryRecallHarness(
        {},
        default_namespace_policy=object(),
        default_projection_policy=object(),
    )
    asyncio.run(
        harness.recall(
            request={"scope": {"tenantId": "t", "botId": "b", "sessionKey": "s"},
                     "query": "q", "purpose": "answer_user"},
            namespace_policy=explicit,
            projection_policy=explicit,
        )
    )
    assert captured["namespace_policy"] is explicit
    assert captured["projection_policy"] is explicit
