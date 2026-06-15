"""Regression tests for the live turn_id mismatch between the collector and gate.

Root cause 1 (live-instrumented): the CLI tool wrapper records tool results into
the shared ``LocalToolEvidenceCollector`` keyed on the ADK ``invocation_id``
(e.g. ``"e-fbb68880-..."``), while the engine's pre-final gate queries
``_collect_evidence`` with the engine's static turn id (the default
``"cli-turn"`` from ``_turn_identity``). The two never match, so EVERY evidence
record the collector recorded is invisible to the gate and a clean source-read
turn still blocks.

The fix reconciles them: the engine captures the ADK ``invocation_id`` observed
on the live event stream and folds the records recorded under that id into
``_collect_evidence`` — without changing the engine's own ``turn_id`` (which
threads through every emitted event and must stay byte-identical for the
coding/hosted paths).
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.tool_runtime import wrap_cli_adk_tools_with_evidence_collector
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus


class _FakeAdkTool:
    name = "GitDiff"

    def __init__(self) -> None:
        self.func = self._func

    async def _func(
        self,
        arguments: dict[str, object],
        tool_context: object,
    ) -> dict[str, object]:
        return {
            "status": "ok",
            "metadata": {
                "toolName": "GitDiff",
                "toolCallId": "call-diff",
                "evidenceRefs": ["evidence:git-diff"],
                "validatorRefs": ["verifier:dev-coding:test-evidence"],
                "toolExecutionReceipt": {
                    "receiptId": "receipt:local-git-diff",
                    "toolName": "GitDiff",
                    "status": "success",
                },
            },
        }


class _FakeInvocationContext:
    """Mirrors the live ADK tool context: invocation_id is the ADK turn id."""

    invocation_id = "e-fbb68880-live"
    function_call = {"id": "call-diff", "name": "GitDiff"}


def _record_via_live_wrapper(collector: LocalToolEvidenceCollector) -> None:
    """Record a tool result the way the live CLI wrapper does (ADK invocation id)."""
    tool = _FakeAdkTool()
    wrapped = wrap_cli_adk_tools_with_evidence_collector(
        [tool],
        collector=collector,
        session_id="cli-session",
    )
    asyncio.run(wrapped[0].func({"diffRef": "x"}, _FakeInvocationContext()))


def test_collect_evidence_sees_records_under_observed_adk_invocation_id() -> None:
    """RED→GREEN: gate's _collect_evidence must see records recorded under the
    ADK invocation id even though the engine's turn id is the static default."""
    collector = LocalToolEvidenceCollector()
    _record_via_live_wrapper(collector)

    # The collector recorded under the ADK invocation id, NOT "cli-turn".
    assert collector.collect_for_turn("cli-turn") == ()
    assert collector.collect_for_turn("e-fbb68880-live") != ()

    engine = MagiEngineDriver(
        runner=object(),
        evidence_collector=collector.collect_for_turn,
    )
    # Simulate the engine having observed the ADK invocation id on the live
    # event stream this turn (what _drive captures from each adk_event).
    engine._note_observed_invocation_id("e-fbb68880-live")

    # The gate queries with the engine's static turn id; reconciliation must
    # still surface the records the collector stored under the ADK id.
    records = engine._collect_evidence("cli-turn")
    assert records, "expected gate to see the live-recorded evidence records"

    bus = execute_pre_final_verifier_bus(
        required_evidence=("evidence:git-diff",),
        required_validators=("verifier:dev-coding:test-evidence",),
        observed_public_refs=(),
        evidence_records=records,
    )
    assert bus["decision"] == "pass"


def test_collect_evidence_matching_turn_id_unchanged_no_observed_ids() -> None:
    """Regression guard: with no observed invocation ids (the coding/hosted
    tests' shape, where recorded turn id == queried turn id), behaviour is
    byte-identical to before — the matching records are returned exactly once."""
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="cli-session",
        turn_id="turn-match",
        tool_call_id="call-1",
        tool_name="GitDiff",
        result={
            "status": "ok",
            "metadata": {
                "toolName": "GitDiff",
                "evidenceRefs": ["evidence:git-diff"],
            },
        },
    )
    engine = MagiEngineDriver(
        runner=object(),
        evidence_collector=collector.collect_for_turn,
    )
    # No observed invocation ids noted: pure pass-through to collect_for_turn.
    direct = collector.collect_for_turn("turn-match")
    via_engine = engine._collect_evidence("turn-match")
    assert via_engine == direct
    assert len(via_engine) == len(direct)


def test_collect_evidence_no_double_count_when_observed_equals_turn_id() -> None:
    """If the observed ADK id happens to equal the engine turn id, records must
    not be double-counted (the union is deduped by record identity)."""
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="cli-session",
        turn_id="same-id",
        tool_call_id="call-1",
        tool_name="GitDiff",
        result={
            "status": "ok",
            "metadata": {
                "toolName": "GitDiff",
                "evidenceRefs": ["evidence:git-diff"],
            },
        },
    )
    engine = MagiEngineDriver(
        runner=object(),
        evidence_collector=collector.collect_for_turn,
    )
    engine._note_observed_invocation_id("same-id")
    baseline = collector.collect_for_turn("same-id")
    via_engine = engine._collect_evidence("same-id")
    assert len(via_engine) == len(baseline)


def test_collect_evidence_no_collector_returns_empty() -> None:
    """No evidence_collector wired -> empty, regardless of observed ids."""
    engine = MagiEngineDriver(runner=object())
    engine._note_observed_invocation_id("e-anything")
    assert engine._collect_evidence("cli-turn") == ()
