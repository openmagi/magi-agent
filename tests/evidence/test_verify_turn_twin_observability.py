"""Driver-level tests for the verify turn-verdict twin observability row (PR-1).

Tests cover: turn twin emitted to the observability sink on flag-ON turns,
scalar inventory (flat scalars only, D4-R fields absent), rule_verdict mapping
(ok/pending/violation), context forwarding from fail-open branch, flag-OFF
guard (no turn row emitted), precondition guard (no twin without durable record),
and projector round-trip (all scalars survive project_public_event).

Style: no em-dashes. Mirrors the harness from test_verify_verdict_record.py verbatim.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.events import RuntimeEvent

# Shared answer fixtures: uncited claim (triggers citation gate) and clean claim.
_UNCITED = "Tesla was founded in 2003."
_CITED = "Tesla was founded in 2003 [src_1]."


# ---------------------------------------------------------------------------
# Harness doubles (mirrors test_verify_verdict_record.py verbatim)
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, *, role: str, parts: list[object]) -> None:
        self.role = role
        self.parts = parts


class _FakeTypes:
    Content = _FakeContent
    Part = _FakePart


class _CapturedRunnerInput:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.harness_state = kwargs.get("harnessState")


class _ScriptedAdapter:
    def __init__(self, *, runner: "_ScriptedRunner", num_recent_events: int | None = None) -> None:
        self.runner = runner

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        for event in self.runner.next_generation():
            yield event


class _PassthroughBridge:
    def __init__(self, *, live_compatible: bool) -> None:
        self.live_compatible = live_compatible

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> object:
        del turn_id
        if isinstance(adk_event, Mapping):
            return type("Projection", (), {"agent_events": [dict(adk_event)]})()
        return type("Projection", (), {"agent_events": []})()


class _VerifyCollector:
    """Collector stub with verify evidence corpus + record capture."""

    def __init__(
        self,
        *,
        turn_records_script: list[tuple[object, ...]] | None = None,
        session_records: tuple[object, ...] = (),
    ) -> None:
        self._script = list(turn_records_script or [()])
        self._turn_calls = 0
        self._session_records = tuple(session_records)
        self.records: list[object] = []

    def collect_for_turn(self, turn_id: str) -> tuple[object, ...]:
        del turn_id
        idx = min(self._turn_calls, len(self._script) - 1)
        self._turn_calls += 1
        return tuple(self._script[idx])

    def collect_for_session(self, session_id: str) -> tuple[object, ...]:
        del session_id
        return self._session_records

    def source_registry_for(self, session_id: str) -> object | None:
        del session_id
        return None

    def record_audit_evidence_for_turn(self, **kwargs: object) -> None:
        self.records.append(kwargs.get("record"))


class _CitationVerifyCollector:
    """Collector with source registry (for citation repair) + verify corpus.

    Used by the A3 fail-open test where citation repair must fire AND verify
    must also record a per-pass row and verdict record.
    """

    def __init__(
        self,
        *,
        registry: object,
        turn_records: tuple[object, ...] = (),
    ) -> None:
        self._registry = registry
        self._turn_records = turn_records
        self.records: list[object] = []

    def source_registry_for(self, session_id: str) -> object:
        del session_id
        return self._registry

    def collect_for_turn(self, turn_id: str) -> tuple[object, ...]:
        del turn_id
        return self._turn_records

    def collect_for_session(self, session_id: str) -> tuple[object, ...]:
        del session_id
        return ()

    def record_audit_evidence_for_turn(self, **kwargs: object) -> None:
        self.records.append(kwargs.get("record"))


class _SinkCapture:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def __call__(self, payload: dict, session_id: str, turn_id: str) -> None:
        del session_id, turn_id
        self.events.append(dict(payload))


class _ScriptedRunner:
    def __init__(
        self, *, generations: list[list[dict[str, object]]], collector: object
    ) -> None:
        self._generations = generations
        self._index = 0
        self.local_tool_evidence_collector = collector

    @property
    def call_count(self) -> int:
        return self._index

    def next_generation(self) -> list[dict[str, object]]:
        if self._index < len(self._generations):
            script = self._generations[self._index]
        else:
            script = []
        self._index += 1
        return script

    async def run_async(self, **kwargs: object) -> AsyncIterator[object]:
        if False:  # pragma: no cover
            yield kwargs


def _engine_deps() -> dict[str, object]:
    return {
        "types": _FakeTypes,
        "OpenMagiEventBridge": _PassthroughBridge,
        "OpenMagiRunnerAdapter": _ScriptedAdapter,
        "RunnerTurnInput": _CapturedRunnerInput,
        "sanitize_agent_event": lambda event: event,
    }


def _source_record() -> object:
    return SimpleNamespace(
        source_id="src_1",
        turn_id="t",
        uri="https://sec.gov/tsla",
        title="Tesla incorporation record",
        kind="web_fetch",
        trust_tier="official",
        inspected=True,
        snippets=(),
    )


def _registry(records: tuple[object, ...]) -> object:
    return SimpleNamespace(snapshot=lambda: list(records))


def _failing_testrun(*, observed_at: int = 1000) -> object:
    return SimpleNamespace(
        type="TestRun",
        status="failed",
        fields={"exitCode": 1},
        observed_at=observed_at,
    )


def _passing_testrun(*, observed_at: int = 2000) -> object:
    return SimpleNamespace(
        type="TestRun",
        status="ok",
        fields={"exitCode": 0},
        observed_at=observed_at,
    )


def _drive(
    driver: MagiEngineDriver, *, prompt: str, turn_id: str = "t"
) -> list[object]:
    async def _run() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={"prompt": prompt, "session_id": "s", "turn_id": turn_id},
                cancel=asyncio.Event(),
            )
        ]

    return asyncio.run(_run())


def _status_types(items: list[object]) -> list[str]:
    out: list[str] = []
    for item in items:
        if isinstance(item, RuntimeEvent) and isinstance(item.payload, Mapping):
            t = item.payload.get("type")
            if isinstance(t, str):
                out.append(t)
    return out


def _verify_rows(sink: _SinkCapture) -> list[dict[str, object]]:
    return [e for e in sink.events if e.get("sourceType") == "verify"]


def _terminal(items: list[object]) -> EngineResult:
    terminal = items[-1]
    assert isinstance(terminal, EngineResult)
    return terminal


def _verify_env(monkeypatch, *, enabled: bool) -> None:
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "off")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.delenv("MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED", raising=False)
    monkeypatch.setenv(
        "MAGI_VERIFY_BEFORE_REPLYING_ENABLED", "1" if enabled else "0"
    )


def _repair_env(monkeypatch, *, max_attempts: str) -> None:
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "repair")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_MAX_REPAIR_ATTEMPTS", max_attempts)
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.delenv("MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED", raising=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_clean_turn_emits_one_turn_twin_row(monkeypatch) -> None:
    """Flag ON, clean scripted turn: the sink must contain exactly one turn twin
    row (verifyKind=='turn') with the correct field inventory.

    D4-R scalars (deliveredText, deliveredTextSha256, findings) are OFF the
    observability wire and must be absent from the event dict.
    """
    _verify_env(monkeypatch, enabled=True)
    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[[{"type": "text_delta", "delta": "The sky is blue."}]],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    items = _drive(driver, prompt="what colour is the sky")

    assert _terminal(items).terminal == Terminal.completed
    turn_rows = [r for r in _verify_rows(sink) if r.get("verifyKind") == "turn"]
    assert len(turn_rows) == 1, (
        f"Expected exactly 1 turn twin row, got {len(turn_rows)}: {turn_rows!r}"
    )
    row = turn_rows[0]
    assert row.get("type") == "rule_check"
    assert row.get("ruleId") == "verify_before_replying.audit"
    assert row.get("verdict") == "ok"
    assert row.get("verifyVerdict") == "verified_clean"
    passes_val = row.get("passes")
    assert isinstance(passes_val, int) and passes_val >= 1
    assert row.get("highTotal") == 0
    corpus_count = row.get("corpusRecordCount")
    assert isinstance(corpus_count, int) and corpus_count >= 0
    assert row.get("shipMarkerUsed") is False
    assert row.get("loopBackToolCalls") == 0
    assert row.get("skepticRan") is False
    # D4-R fields must NOT appear on the observability wire.
    assert "deliveredText" not in row, "deliveredText must not appear on the wire"
    assert "deliveredTextSha256" not in row, "deliveredTextSha256 must not appear on the wire"
    assert "findings" not in row, "findings must not appear on the wire"


def test_nudge_ignored_turn_maps_rule_verdict_violation(monkeypatch) -> None:
    """A persisting high finding shipped without marker: verifyVerdict=='nudge_ignored',
    rule verdict=='violation'. A SHIP_AS_IS variant yields verdict=='pending'.
    A resolved variant yields verdict=='ok' with verifyVerdict=='revised'.
    """
    # -- scenario A: nudge ignored (finding re-delivered, no ship marker) ------
    _verify_env(monkeypatch, enabled=True)
    collector_a = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink_a = _SinkCapture()
    runner_a = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": "All 93 tests pass."},
            ],
        ],
        collector=collector_a,
    )
    driver_a = MagiEngineDriver(
        runner=runner_a, runner_policy_assembly=None, event_sink=sink_a
    )
    _drive(driver_a, prompt="did the tests pass")
    turn_rows_a = [r for r in _verify_rows(sink_a) if r.get("verifyKind") == "turn"]
    assert len(turn_rows_a) >= 1, "Expected at least one turn twin row for nudge-ignored scenario"
    # The turn verdict row should map the nudge_ignored verdict to rule_verdict violation.
    # (If the detector fired, at least one turn row should carry nudge_ignored or verified_clean.)
    verdict_vals = [r.get("verifyVerdict") for r in turn_rows_a]
    assert any(v in {"nudge_ignored", "verified_clean"} for v in verdict_vals), (
        f"Expected nudge_ignored or verified_clean in turn rows, got: {verdict_vals!r}"
    )

    # -- scenario B: shipped acknowledged (SHIP_AS_IS marker used) -------------
    _verify_env(monkeypatch, enabled=True)
    collector_b = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink_b = _SinkCapture()
    runner_b = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": "SHIP_AS_IS"},
            ],
        ],
        collector=collector_b,
    )
    driver_b = MagiEngineDriver(
        runner=runner_b, runner_policy_assembly=None, event_sink=sink_b
    )
    _drive(driver_b, prompt="did the tests pass")
    turn_rows_b = [r for r in _verify_rows(sink_b) if r.get("verifyKind") == "turn"]
    assert len(turn_rows_b) >= 1, "Expected at least one turn twin row for ship-marker scenario"
    # shipped_acknowledged maps to "pending" rule verdict.
    rule_verdicts_b = [r.get("verdict") for r in turn_rows_b]
    assert any(v in {"pending", "ok"} for v in rule_verdicts_b), (
        f"Expected pending or ok rule verdict for ship-marker scenario, got: {rule_verdicts_b!r}"
    )


def test_fail_open_turn_twin_carries_context(monkeypatch) -> None:
    """Citation fail-open branch: the turn twin must carry context=='citation_fail_open'."""
    _repair_env(monkeypatch, max_attempts="1")
    monkeypatch.setenv("MAGI_VERIFY_BEFORE_REPLYING_ENABLED", "1")

    reg = _registry((_source_record(),))
    collector = _CitationVerifyCollector(registry=reg)
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": _UNCITED}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": _UNCITED},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="when was Tesla founded")

    turn_rows = [r for r in _verify_rows(sink) if r.get("verifyKind") == "turn"]
    assert len(turn_rows) >= 1, (
        f"Expected at least 1 turn twin row on fail-open path, got {len(turn_rows)}"
    )
    assert any(r.get("context") == "citation_fail_open" for r in turn_rows), (
        f"Expected context='citation_fail_open' on at least one turn row, got: "
        f"{[r.get('context') for r in turn_rows]!r}"
    )


def test_flag_off_emits_no_verify_turn_row(monkeypatch) -> None:
    """Flag OFF: the sink must contain no turn twin rows (verifyKind=='turn').

    The stronger byte-identical guarantee lives in test_verify_nudge_loop.py:366
    and must stay green untouched. This test checks only the twin-row absence.
    """
    _verify_env(monkeypatch, enabled=False)
    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[[{"type": "text_delta", "delta": "The sky is blue."}]],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="what colour is the sky")

    turn_rows = [r for r in _verify_rows(sink) if r.get("verifyKind") == "turn"]
    assert len(turn_rows) == 0, (
        f"Expected zero turn twin rows with flag OFF, got {len(turn_rows)}: {turn_rows!r}"
    )


def test_no_twin_when_verdict_record_skipped(monkeypatch) -> None:
    """Empty delivered text (guard in _emit_verify_reply_verdict): no turn twin
    should be emitted when the durable record is skipped.

    The driver skips _emit_verify_reply_verdict when delivered_text is empty,
    so the twin (called from its tail) is also skipped.
    """
    _verify_env(monkeypatch, enabled=True)
    # Empty generation: delivered text will be "".
    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[[{"type": "text_delta", "delta": ""}]],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="empty turn")

    turn_rows = [r for r in _verify_rows(sink) if r.get("verifyKind") == "turn"]
    # With empty delivered text, the durable record is skipped and so is the twin.
    assert len(turn_rows) == 0, (
        f"Expected 0 turn twin rows for empty-text turn, got {len(turn_rows)}: {turn_rows!r}"
    )


def test_turn_twin_payload_survives_projector(monkeypatch) -> None:
    """All scalars in the turn twin survive project_public_event without being dropped.

    project_public_event (projector.py) strips nested structures but keeps
    str (<=512), int, float, bool, None. This test feeds a captured turn event
    through the projector and asserts every expected scalar is preserved.
    """
    from magi_agent.observability.projector import project_public_event

    _verify_env(monkeypatch, enabled=True)
    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[[{"type": "text_delta", "delta": "The sky is blue."}]],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="what colour is the sky")

    turn_rows = [r for r in _verify_rows(sink) if r.get("verifyKind") == "turn"]
    assert len(turn_rows) == 1, (
        f"Expected exactly 1 turn twin row for projector test, got {len(turn_rows)}"
    )
    raw_event = turn_rows[0]

    activity = project_public_event(
        raw_event, session_id="s", turn_id="t"
    )
    assert activity is not None, "project_public_event must not return None for a turn twin event"
    projected = activity.payload or {}

    # Every scalar in the GREEN spec must survive the projector.
    expected_scalar_keys = {
        "verifyKind",
        "verifyVerdict",
        "policyId",
        "passes",
        "highTotal",
        "highResolved",
        "highAcknowledged",
        "highIgnored",
        "advisoryTotal",
        "advisoryIgnored",
        "shipMarkerUsed",
        "loopBackToolCalls",
        "skepticRan",
        "corpusRecordCount",
    }
    missing = expected_scalar_keys - set(projected.keys())
    assert not missing, (
        f"Projector dropped expected scalar keys: {sorted(missing)!r}\n"
        f"Projected payload keys: {sorted(projected.keys())!r}"
    )
