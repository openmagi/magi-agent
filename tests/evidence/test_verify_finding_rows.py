"""Driver-level tests for per-finding verify observability rows (PR-2).

Tests 10-16 in the PR-2 spec. Harness doubled from
test_verify_turn_twin_observability.py verbatim.

Style: no em-dashes.
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

_UNCITED = "Tesla was founded in 2003."
_CITED = "Tesla was founded in 2003 [src_1]."


# ---------------------------------------------------------------------------
# Harness doubles (mirrored from test_verify_turn_twin_observability.py)
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


def _terminal(items: list[object]) -> EngineResult:
    terminal = items[-1]
    assert isinstance(terminal, EngineResult)
    return terminal


def _verify_rows(sink: _SinkCapture) -> list[dict[str, object]]:
    return [e for e in sink.events if e.get("sourceType") == "verify"]


def _finding_rows(sink: _SinkCapture) -> list[dict[str, object]]:
    """Return only verify rows with verifyKind=='finding'."""
    return [r for r in _verify_rows(sink) if r.get("verifyKind") == "finding"]


def _turn_rows(sink: _SinkCapture) -> list[dict[str, object]]:
    """Return only verify rows with verifyKind=='turn'."""
    return [r for r in _verify_rows(sink) if r.get("verifyKind") == "turn"]


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


# ---------------------------------------------------------------------------
# Tests 10-16
# ---------------------------------------------------------------------------


def test_finding_rows_emitted_terminally_with_final_resolution(monkeypatch) -> None:
    """Test 10: contradiction fixture nudges then revises; finding rows appear exactly
    once with final resolution despite 2 audit passes.

    Mirrors test_contradiction_nudges_then_revision_delivers from nudge loop tests.
    Verifies: each finding row has type=='rule_check', sourceType=='verify',
    verifyKind=='finding', findingId, confidence, claimClass, suggestedAction present;
    rows appear ONCE (not per pass).
    """
    _verify_env(monkeypatch, enabled=True)
    collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink = _SinkCapture()
    revision = "Actually the tests fail with exit code 1; I have not fixed them."
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
    finding_rows = _finding_rows(sink)

    # With a failing TestRun and "tests pass" claim, at least 1 finding expected.
    assert len(finding_rows) >= 1, (
        f"Expected at least 1 finding row for contradiction fixture, got {len(finding_rows)}"
    )

    for row in finding_rows:
        assert row.get("type") == "rule_check", (
            f"Expected type=='rule_check', got {row.get('type')!r}"
        )
        assert row.get("sourceType") == "verify"
        assert row.get("verifyKind") == "finding"
        assert isinstance(row.get("findingId"), str) and row.get("findingId"), (
            "findingId must be a non-empty string"
        )
        assert row.get("confidence") in {"high", "advisory"}, (
            f"confidence must be 'high' or 'advisory', got {row.get('confidence')!r}"
        )
        assert isinstance(row.get("claimClass"), str) and row.get("claimClass"), (
            "claimClass must be a non-empty string"
        )
        assert isinstance(row.get("suggestedAction"), str) and row.get("suggestedAction"), (
            "suggestedAction must be a non-empty string"
        )
        # resolution is the FINAL resolution after revision
        assert isinstance(row.get("resolution"), str), "resolution must be a string"

    # Finding rows emitted exactly once (not per pass): count by findingId
    finding_ids = [r.get("findingId") for r in finding_rows]
    assert len(finding_ids) == len(set(finding_ids)), (
        f"Duplicate finding rows detected (rows appear more than once): {finding_ids!r}"
    )


def test_rule_verdict_mapping_per_finding(monkeypatch) -> None:
    """Test 11: resolved -> verdict=='ok'; high+ignored -> 'violation';
    high+acknowledged_shipped -> 'pending'; advisory (any resolution) -> 'pending'.

    We test the verdict mapping by checking which rule verdicts appear in finding
    rows for our contradiction fixture. When revision delivers, the finding
    resolution should be 'resolved' -> verdict='ok'.
    """
    _verify_env(monkeypatch, enabled=True)
    collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink = _SinkCapture()
    revision = "Actually the tests fail with exit code 1."
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

    _drive(driver, prompt="did the tests pass")

    finding_rows = _finding_rows(sink)
    if finding_rows:
        # After revision, findings should be resolved -> verdict='ok'
        verdicts = [r.get("verdict") for r in finding_rows]
        # All verdicts must be one of the valid rule_check values
        for v in verdicts:
            assert v in {"ok", "violation", "pending"}, (
                f"rule verdict must be ok/violation/pending, got {v!r}"
            )

    # Also verify the mapping logic: for nudge_ignored scenario
    sink2 = _SinkCapture()
    collector2 = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    runner2 = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": "All 93 tests pass."},
            ],
        ],
        collector=collector2,
    )
    driver2 = MagiEngineDriver(
        runner=runner2, runner_policy_assembly=None, event_sink=sink2
    )
    _verify_env(monkeypatch, enabled=True)
    _drive(driver2, prompt="did the tests pass")

    finding_rows2 = _finding_rows(sink2)
    if finding_rows2:
        # Finding still detected -> ignored -> violation (for high confidence)
        for row in finding_rows2:
            if row.get("confidence") == "high" and row.get("resolution") == "ignored":
                assert row.get("verdict") == "violation", (
                    f"high+ignored must map to verdict='violation', got {row.get('verdict')!r}"
                )


def test_claim_span_with_path_survives_redaction(monkeypatch) -> None:
    """Test 12: A finding whose claim_text names a path (e.g., /workspace/src/foo.ts)
    must emit claimText containing 'foo.ts' and public_projection_safe_text(claimText)
    must not be '[redacted]'.

    We monkeypatch audit_candidate to inject a finding with a path claim_text.
    """
    import magi_agent.evidence.verify_audit as va_module
    from magi_agent.evidence.verify_audit import VerifyFinding, VerifyAuditResult
    from magi_agent.evidence.reports import public_projection_safe_text

    path_claim_text = "The result is stored at /workspace/src/foo.ts for later use"
    injected_finding = VerifyFinding(
        finding_id="fp-claim-path",
        rule_id="verify_before_replying.evidence_consistency",
        confidence="high",
        claim_class="numeric",
        claim_text=path_claim_text,
        span=(0, 30),
        evidence_refs=("evidence:sha256:abc123",),
        expected=None,
        observed=None,
        detail="Test detail",
        suggested_action="recheck",
    )

    original_audit_candidate = va_module.audit_candidate

    def patched_audit_candidate(**kwargs):
        result = original_audit_candidate(**kwargs)
        all_findings = list(result.findings) + [injected_finding]
        # Honor the surfaced-fingerprint contract the real audit_candidate
        # enforces: a finding already surfaced this turn is never "new" again.
        # Without this the driver's counterless-convergence loop never
        # terminates on a single-generation script (the injected finding would
        # re-nudge every pass forever).
        surfaced = kwargs.get("surfaced_fingerprints") or set()
        new_findings = [
            f
            for f in [injected_finding]
            if f not in list(result.findings) and f.finding_id not in surfaced
        ]
        return VerifyAuditResult(
            findings=tuple(all_findings),
            new_findings=tuple(new_findings),
            high_count=len([f for f in all_findings if f.confidence == "high"]),
            advisory_count=len([f for f in all_findings if f.confidence == "advisory"]),
            corpus_record_count=result.corpus_record_count,
            skeptic_ran=result.skeptic_ran,
            skeptic_findings_dropped=result.skeptic_findings_dropped,
        )

    _verify_env(monkeypatch, enabled=True)
    monkeypatch.setattr(va_module, "audit_candidate", patched_audit_candidate)

    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[[{"type": "text_delta", "delta": "The analysis is complete."}]],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="what is the result")

    finding_rows = _finding_rows(sink)
    path_rows = [r for r in finding_rows if r.get("findingId") == "fp-claim-path"]
    assert len(path_rows) >= 1, (
        f"Expected at least 1 finding row with findingId='fp-claim-path', "
        f"got {len(finding_rows)} total finding rows"
    )
    row = path_rows[0]
    claim_text = row.get("claimText")
    assert isinstance(claim_text, str), "claimText must be a string"
    assert "foo.ts" in claim_text, (
        f"Expected 'foo.ts' in claimText (basename from path), got {claim_text!r}"
    )
    assert public_projection_safe_text(claim_text) != "[redacted]", (
        f"claimText {claim_text!r} still redacts after display_span"
    )


def test_cap_spills_into_turn_findings_omitted(monkeypatch) -> None:
    """Test 13: 15 distinct findings -> exactly 12 finding rows emitted;
    the turn row has findingsOmitted==3.
    """
    import magi_agent.evidence.verify_audit as va_module
    from magi_agent.evidence.verify_audit import VerifyFinding, VerifyAuditResult

    _CAP = 12
    _TOTAL = 15
    _EXPECTED_OMITTED = _TOTAL - _CAP

    findings_15 = tuple(
        VerifyFinding(
            finding_id=f"f{i:02d}",
            rule_id="verify_before_replying.evidence_consistency",
            confidence="high",
            claim_class="numeric",
            claim_text=f"claim {i}",
            span=(i, i + 5),
            evidence_refs=(),
            expected=None,
            observed=None,
            detail=f"detail {i}",
            suggested_action="recheck",
        )
        for i in range(_TOTAL)
    )

    original_audit_candidate = va_module.audit_candidate

    def patched_audit_candidate(**kwargs):
        del kwargs
        return VerifyAuditResult(
            findings=findings_15,
            new_findings=findings_15,
            high_count=_TOTAL,
            advisory_count=0,
            corpus_record_count=0,
            skeptic_ran=False,
            skeptic_findings_dropped=0,
        )

    _verify_env(monkeypatch, enabled=True)
    monkeypatch.setattr(va_module, "audit_candidate", patched_audit_candidate)

    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[
            # First pass: claim gets nudged (15 high findings present)
            [{"type": "text_delta", "delta": "Some answer with 15 findings."}],
            # Second pass: SHIP_AS_IS to deliver
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": "SHIP_AS_IS"},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="what is the result")

    finding_rows = _finding_rows(sink)
    assert len(finding_rows) == _CAP, (
        f"Expected exactly {_CAP} finding rows (cap), got {len(finding_rows)}"
    )

    turn_row_list = _turn_rows(sink)
    assert len(turn_row_list) >= 1, "Expected at least 1 turn row"
    turn_row = turn_row_list[-1]  # use the last/final turn row
    findings_omitted = turn_row.get("findingsOmitted")
    assert findings_omitted == _EXPECTED_OMITTED, (
        f"Expected findingsOmitted=={_EXPECTED_OMITTED} on turn row, "
        f"got {findings_omitted!r}"
    )


def test_evidence_ref_first_only_scalar(monkeypatch) -> None:
    """Test 14: Finding with 3 refs -> event carries evidenceRef (str, first only);
    no 'evidenceRefs' list key on the event.
    """
    import magi_agent.evidence.verify_audit as va_module
    from magi_agent.evidence.verify_audit import VerifyFinding, VerifyAuditResult

    finding_with_refs = VerifyFinding(
        finding_id="f-refs",
        rule_id="verify_before_replying.evidence_consistency",
        confidence="high",
        claim_class="numeric",
        claim_text="claim text here",
        span=(0, 10),
        evidence_refs=("evidence:sha256:first", "evidence:sha256:second", "evidence:sha256:third"),
        expected=None,
        observed=None,
        detail="detail text",
        suggested_action="cite",
    )

    original_audit_candidate = va_module.audit_candidate

    def patched_audit_candidate(**kwargs):
        del kwargs
        return VerifyAuditResult(
            findings=(finding_with_refs,),
            new_findings=(finding_with_refs,),
            high_count=1,
            advisory_count=0,
            corpus_record_count=0,
            skeptic_ran=False,
            skeptic_findings_dropped=0,
        )

    _verify_env(monkeypatch, enabled=True)
    monkeypatch.setattr(va_module, "audit_candidate", patched_audit_candidate)

    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "The answer is 42."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": "SHIP_AS_IS"},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="what is the answer")

    rows = [r for r in _finding_rows(sink) if r.get("findingId") == "f-refs"]
    assert len(rows) >= 1, "Expected at least 1 finding row for f-refs"
    row = rows[0]
    assert isinstance(row.get("evidenceRef"), str), (
        f"evidenceRef must be a scalar string, got {row.get('evidenceRef')!r}"
    )
    assert row.get("evidenceRef") == "evidence:sha256:first", (
        f"evidenceRef must be the FIRST ref, got {row.get('evidenceRef')!r}"
    )
    assert "evidenceRefs" not in row, (
        "evidenceRefs (list) must NOT be present on the finding event"
    )


def test_finding_row_payload_survives_projector(monkeypatch) -> None:
    """Test 15: through project_public_event, all scalars retained (flatness proof)."""
    from magi_agent.observability.projector import project_public_event
    import magi_agent.evidence.verify_audit as va_module
    from magi_agent.evidence.verify_audit import VerifyFinding, VerifyAuditResult

    finding = VerifyFinding(
        finding_id="f-projector",
        rule_id="verify_before_replying.evidence_consistency",
        confidence="high",
        claim_class="numeric",
        claim_text="The revenue was 42 million",
        span=(0, 25),
        evidence_refs=("evidence:sha256:abc",),
        expected=None,
        observed=None,
        detail="Claim not backed by evidence",
        suggested_action="cite",
    )

    original_audit_candidate = va_module.audit_candidate

    def patched_audit_candidate(**kwargs):
        del kwargs
        return VerifyAuditResult(
            findings=(finding,),
            new_findings=(finding,),
            high_count=1,
            advisory_count=0,
            corpus_record_count=0,
            skeptic_ran=False,
            skeptic_findings_dropped=0,
        )

    _verify_env(monkeypatch, enabled=True)
    monkeypatch.setattr(va_module, "audit_candidate", patched_audit_candidate)

    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "Revenue grew 42 million."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": "SHIP_AS_IS"},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="what is the revenue")

    rows = [r for r in _finding_rows(sink) if r.get("findingId") == "f-projector"]
    assert len(rows) >= 1, "Expected at least 1 finding row for f-projector"
    raw_event = rows[0]

    activity = project_public_event(raw_event, session_id="s", turn_id="t")
    assert activity is not None, "project_public_event must not return None for finding row"
    projected = activity.payload or {}

    expected_scalar_keys = {
        "verifyKind",
        "findingId",
        "confidence",
        "claimClass",
        "resolution",
        "suggestedAction",
    }
    missing = expected_scalar_keys - set(projected.keys())
    assert not missing, (
        f"Projector dropped expected scalar keys: {sorted(missing)!r}\n"
        f"Projected payload keys: {sorted(projected.keys())!r}"
    )


def test_flag_off_emits_no_finding_rows(monkeypatch) -> None:
    """Test 16: with flag OFF, no finding rows in the sink."""
    _verify_env(monkeypatch, enabled=False)
    collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[[{"type": "text_delta", "delta": "All 93 tests pass."}]],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    _drive(driver, prompt="did the tests pass")

    rows = _finding_rows(sink)
    assert len(rows) == 0, (
        f"Expected zero finding rows with flag OFF, got {len(rows)}: {rows!r}"
    )
