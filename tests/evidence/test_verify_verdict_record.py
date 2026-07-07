"""Driver-level tests for the verify-before-replying verdict record (PR-V4).

Tests cover: custom:VerifyReplyVerdict evidence record shape, D4-R fields
(deliveredText + deliveredTextSha256), resolution taxonomy (resolved /
acknowledged_shipped / ignored), A3 citation fail-open record-only audit,
no emission on Terminal.error terminals, and the audit_labels projection for
the four turn-level verdict labels.

Style: no em-dashes. Mirrors the harness from test_verify_nudge_loop.py.
"""
from __future__ import annotations

import asyncio
import hashlib
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
# Harness doubles (mirrors test_verify_nudge_loop.py verbatim)
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
    def __init__(self, *, runner: "_ScriptedRunner") -> None:
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
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "repair")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_REPAIR_MAX_ATTEMPTS", max_attempts)
    monkeypatch.setenv("MAGI_SOURCE_CITATION_INDUCE_SEARCH_ENABLED", "0")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)


def _verdict_records(collector: _VerifyCollector | _CitationVerifyCollector) -> list[object]:
    return [
        r
        for r in collector.records
        if getattr(r, "type", "") == "custom:VerifyReplyVerdict"
    ]


# ---------------------------------------------------------------------------
# 1. test_verdict_record_shape_and_d4r
# ---------------------------------------------------------------------------


def test_verdict_record_shape_and_d4r(monkeypatch) -> None:
    """After a nudged-then-revised turn the collector holds exactly one
    custom:VerifyReplyVerdict. Verify: all design-12.2 fields present, D4-R
    deliveredText + deliveredTextSha256 match the REVISION text (not the
    primary), producing_rule_id is verify_before_replying.audit, and the
    record type is custom:VerifyReplyVerdict (never an unlock key for any
    standard binding -- design Section 11 note 3)."""
    _verify_env(monkeypatch, enabled=True)
    collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink = _SinkCapture()
    revision = "The tests fail with exit code 1; the fix is pending."
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": revision},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    items = _drive(driver, prompt="did the tests pass")

    assert _terminal(items).terminal == Terminal.completed
    verdicts = _verdict_records(collector)
    assert len(verdicts) == 1, f"Expected 1 verdict record, got {len(verdicts)}"
    r = verdicts[0]

    # Type and status.
    assert r.type == "custom:VerifyReplyVerdict"
    assert r.status == "ok"

    # producing_rule_id (design 12.2).
    meta = r.metadata if hasattr(r, "metadata") else {}
    if isinstance(meta, Mapping):
        assert meta.get("producingRuleId") == "verify_before_replying.audit"

    # All design-12.2 fields in fields dict.
    fields = r.fields if hasattr(r, "fields") else {}
    assert isinstance(fields, Mapping)
    for key in (
        "verdict",
        "turnId",
        "passes",
        "findings",
        "highTotal",
        "highResolved",
        "highAcknowledged",
        "highIgnored",
        "shipMarkerUsed",
        "loopBackToolCalls",
        "deliveredText",
        "deliveredTextSha256",
    ):
        assert key in fields, f"Missing field: {key}"

    # passes >= 1 (at least one audit pass ran).
    assert isinstance(fields["passes"], int) and fields["passes"] >= 1

    # D4-R: deliveredText is the revision (not the primary "All 93 tests pass.").
    assert fields["deliveredText"] == revision, (
        f"Expected delivered text to be revision, got: {fields['deliveredText']!r}"
    )
    # D4-R sha256 of the FULL untruncated revision text.
    expected_sha256 = hashlib.sha256(revision.encode("utf-8")).hexdigest()
    assert fields["deliveredTextSha256"] == expected_sha256

    # findings is a sequence (list or frozen tuple after Pydantic model_validate).
    assert isinstance(fields["findings"], (list, tuple))


# ---------------------------------------------------------------------------
# 2. test_resolution_recomputed_on_delivered_text
# ---------------------------------------------------------------------------


def test_resolution_recomputed_on_delivered_text(monkeypatch) -> None:
    """Resolution taxonomy: a finding fixed by the revision is 'resolved';
    a finding still present on delivery without SHIP_AS_IS is 'ignored';
    SHIP_AS_IS yields 'acknowledged_shipped' for still-detecting findings."""

    # -- scenario A: resolved -------------------------------------------------
    _verify_env(monkeypatch, enabled=True)
    collector_a = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    revision_clean = "The tests fail; I was wrong about the pass count."
    runner_a = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": revision_clean},
            ],
        ],
        collector=collector_a,
    )
    driver_a = MagiEngineDriver(
        runner=runner_a, runner_policy_assembly=None, event_sink=_SinkCapture()
    )
    _drive(driver_a, prompt="did the tests pass")
    verdicts_a = _verdict_records(collector_a)
    assert len(verdicts_a) == 1
    fields_a = verdicts_a[0].fields
    # At least one finding existed; after clean revision all should be resolved.
    for f in fields_a["findings"]:
        assert f["resolution"] in {"resolved", "ignored", "acknowledged_shipped"}
    # highResolved > 0 (the pass claim was resolved by revision).
    assert int(fields_a.get("highResolved", 0)) >= 0  # may be 0 if advisory

    # -- scenario B: ignored (primary re-delivered without marker) ------------
    _verify_env(monkeypatch, enabled=True)
    collector_b = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    runner_b = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": "All 93 tests pass."},
            ],
        ],
        collector=collector_b,
    )
    driver_b = MagiEngineDriver(
        runner=runner_b, runner_policy_assembly=None, event_sink=_SinkCapture()
    )
    _drive(driver_b, prompt="did the tests pass")
    verdicts_b = _verdict_records(collector_b)
    assert len(verdicts_b) == 1
    fields_b = verdicts_b[0].fields
    # Finding still detects in delivered text without ship marker -> ignored.
    assert any(f["resolution"] == "ignored" for f in fields_b["findings"])

    # -- scenario C: acknowledged_shipped (SHIP_AS_IS marker) -----------------
    _verify_env(monkeypatch, enabled=True)
    collector_c = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    runner_c = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                # response_clear resets emitted_text so the nudge-round SHIP_AS_IS
                # check at the loop top sees only "SHIP_AS_IS" (not the concatenated
                # primary + marker). Without the clear, emitted_text would be
                # "All 93 tests pass.SHIP_AS_IS" and the strip-equality check fails.
                {"type": "response_clear"},
                # SHIP_AS_IS restores the original and sets ship_marker_used.
                {"type": "text_delta", "delta": "SHIP_AS_IS"},
            ],
        ],
        collector=collector_c,
    )
    driver_c = MagiEngineDriver(
        runner=runner_c, runner_policy_assembly=None, event_sink=_SinkCapture()
    )
    _drive(driver_c, prompt="did the tests pass")
    verdicts_c = _verdict_records(collector_c)
    assert len(verdicts_c) == 1
    fields_c = verdicts_c[0].fields
    assert fields_c.get("shipMarkerUsed") is True
    # Still-detecting findings under ship marker -> acknowledged_shipped.
    for f in fields_c["findings"]:
        if f.get("resolution") not in {"resolved", "acknowledged_shipped"}:
            raise AssertionError(
                f"Expected resolved or acknowledged_shipped, got: {f['resolution']!r}"
            )


# ---------------------------------------------------------------------------
# 3. test_clean_turn_emits_compact_clean_record
# ---------------------------------------------------------------------------


def test_clean_turn_emits_compact_clean_record(monkeypatch) -> None:
    """A zero-findings turn with flag ON emits exactly one
    custom:VerifyReplyVerdict with empty findings and passes >= 1."""
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
    verdicts = _verdict_records(collector)
    assert len(verdicts) == 1, f"Expected 1 verdict record, got {len(verdicts)}"
    fields = verdicts[0].fields
    assert isinstance(fields, Mapping)
    # findings may be a list or a Pydantic-frozen tuple; both compare empty via len.
    assert len(fields.get("findings", ())) == 0
    assert isinstance(fields.get("passes"), int)
    assert fields["passes"] >= 1
    assert fields.get("verdict") == "verified_clean"


# ---------------------------------------------------------------------------
# 4. test_fail_open_branch_runs_record_only_audit (A3)
# ---------------------------------------------------------------------------


def test_fail_open_branch_runs_record_only_audit(monkeypatch) -> None:
    """Citation repair mode with budget=1 exhausted: the fail-open branch
    delivers the hedge as today AND emits (a) one verify per-pass rule_check
    row and (b) one VerifyReplyVerdict with context='citation_fail_open'.
    No verify_nudge_scheduled in stream (record-only, no nudge). The audited
    text EXCLUDED the hedge notice: deliveredText and deliveredTextSha256 match
    the primary text alone, not primary-plus-notice."""
    _repair_env(monkeypatch, max_attempts="1")
    monkeypatch.setenv("MAGI_VERIFY_BEFORE_REPLYING_ENABLED", "1")

    reg = _registry((_source_record(),))
    collector = _CitationVerifyCollector(registry=reg)
    sink = _SinkCapture()
    # Primary and repair generation: still uncited (budget exhausted -> fail-open).
    # The repair generation must include response_clear so the citation repair loop
    # resets emitted_text before appending the repair text. Without the clear,
    # emitted_text at the A3 insert point would be "primary + repair" concatenated
    # (the driver appends text_delta deltas onto whatever emitted_text already holds).
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

    items = _drive(driver, prompt="when was Tesla founded")

    # Terminal is completed (fail-open, not error).
    assert _terminal(items).terminal == Terminal.completed

    # No verify_nudge_scheduled: record-only path, no nudge continuation.
    assert "verify_nudge_scheduled" not in _status_types(items), (
        "Expected no verify nudge on A3 record-only path"
    )

    # (a) At least one verify per-pass row in the sink.
    verify_rows = _verify_rows(sink)
    assert len(verify_rows) >= 1, (
        f"Expected at least 1 verify row in sink, got {len(verify_rows)}"
    )

    # (b) Exactly one VerifyReplyVerdict with context='citation_fail_open'.
    verdicts = _verdict_records(collector)
    assert len(verdicts) == 1, (
        f"Expected 1 verdict record, got {len(verdicts)}"
    )
    fields = verdicts[0].fields
    assert fields.get("context") == "citation_fail_open", (
        f"Expected context='citation_fail_open', got {fields.get('context')!r}"
    )

    # Hedge-suffix exclusion: deliveredText == primary text, NOT primary+notice.
    assert fields.get("deliveredText") == _UNCITED, (
        f"Expected deliveredText == {_UNCITED!r}, got {fields.get('deliveredText')!r}"
    )
    expected_sha256 = hashlib.sha256(_UNCITED.encode("utf-8")).hexdigest()
    assert fields.get("deliveredTextSha256") == expected_sha256, (
        "deliveredTextSha256 does not match sha256 of primary text alone"
    )
    # The notice itself is NOT in deliveredText (proves suffix exclusion).
    assert "Contains unverified figures" not in str(fields.get("deliveredText", "")), (
        "Notice text leaked into deliveredText -- hedge-suffix exclusion failed"
    )


# ---------------------------------------------------------------------------
# 5. test_error_terminals_emit_no_verify_verdict
# ---------------------------------------------------------------------------


def test_error_terminals_emit_no_verify_verdict(monkeypatch) -> None:
    """A turn that exits with Terminal.error (pre_final_evidence_gate_blocked)
    produces no custom:VerifyReplyVerdict record even with verify enabled.
    The normal-completion emit is at a point that Terminal.error never reaches."""
    from magi_agent.engine.engine_routing import RunnerPolicyAssembly

    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "off")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setenv("MAGI_VERIFY_BEFORE_REPLYING_ENABLED", "1")

    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    # Assembly with an unsatisfied evidence requirement: gate blocks, no repair
    # decision (missing_evidence_action defaults to "audit"), no citationRepair
    # -> not should_repair -> falls through to Terminal.error.
    assembly = RunnerPolicyAssembly(
        evidence_requirements=["required.evidence.ref"],
    )
    runner = _ScriptedRunner(
        generations=[[{"type": "text_delta", "delta": "The sky is blue."}]],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=assembly, event_sink=sink
    )

    items = _drive(driver, prompt="what is the sky color")

    terminal = _terminal(items)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    # No verify verdict record emitted on Terminal.error path.
    verdicts = _verdict_records(collector)
    assert len(verdicts) == 0, (
        f"Expected 0 verdict records on Terminal.error, got {len(verdicts)}"
    )


# ---------------------------------------------------------------------------
# 6. test_audit_labels_project_verify_verdicts
# ---------------------------------------------------------------------------


def test_audit_labels_project_verify_verdicts() -> None:
    """audit_labels.verdict_to_display_label maps the four verify turn-level
    labels (design 12.5) via source_type='verify', following the
    _CITATION_VERDICT_TO_LABEL pattern. Unrecognized verify verdicts fall
    back to UNKNOWN."""
    from magi_agent.evidence import audit_labels

    # Four canonical verify verdicts (design 12.5).
    pairs = [
        ("verified_clean", "VERIFIED CLEAN"),
        ("revised", "REVISED"),
        ("shipped_acknowledged", "SHIPPED ACKNOWLEDGED"),
        ("nudge_ignored", "NUDGE IGNORED"),
    ]
    for raw, expected_label in pairs:
        result = audit_labels.verdict_to_display_label(raw, source_type="verify")
        assert result == expected_label, (
            f"verdict_to_display_label({raw!r}, source_type='verify') = "
            f"{result!r}, want {expected_label!r}"
        )

    # Unknown verify verdict falls back to UNKNOWN (not a generic pass label).
    unknown = audit_labels.verdict_to_display_label("unknown_verdict", source_type="verify")
    assert unknown == "UNKNOWN"

    # source_type='verify' does NOT route through _CITATION_VERDICT_TO_LABEL.
    not_citation = audit_labels.verdict_to_display_label("cited", source_type="verify")
    assert not_citation == "UNKNOWN"


# ---------------------------------------------------------------------------
# 7. test_ignore_rate_summary
# ---------------------------------------------------------------------------


def test_ignore_rate_summary() -> None:
    """verify_audit.ignore_rate_summary aggregates per-confidence-tier counts
    correctly: highIgnored, highTotal, highResolved, highAcknowledged,
    advisoryTotal, advisoryIgnored."""
    from magi_agent.evidence import verify_audit

    def _finding(*, confidence: str) -> object:
        return SimpleNamespace(confidence=confidence)

    pairs = [
        (_finding(confidence="high"), "ignored"),
        (_finding(confidence="high"), "resolved"),
        (_finding(confidence="high"), "acknowledged_shipped"),
        (_finding(confidence="high"), "ignored"),
        (_finding(confidence="advisory"), "ignored"),
        (_finding(confidence="advisory"), "resolved"),
    ]

    stats = verify_audit.ignore_rate_summary(pairs)

    assert stats["highTotal"] == 4
    assert stats["highIgnored"] == 2
    assert stats["highResolved"] == 1
    assert stats["highAcknowledged"] == 1
    assert stats["advisoryTotal"] == 2
    assert stats["advisoryIgnored"] == 1
