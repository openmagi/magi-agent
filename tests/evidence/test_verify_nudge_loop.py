"""Driver-level end-to-end tests for the verify-before-replying nudge loop (PR-V3).

These tests drive the REAL ``MagiEngineDriver`` pre-final ``while True`` loop with
a scripted adapter (one generation per ``run_turn`` call: primary, then each nudge
round). The scripted-adapter harness (doubles below) is copied verbatim from
``tests/evidence/test_citation_repair_loop.py`` because that is the harness that
exercises the live loop; the verify-specific collector, the observability sink,
and the evidence-record fixtures are added on top.

Verify is a NUDGE, not a gate: it never blocks, never yields ``Terminal.error``,
never mutates ``repairDecision``. On a clean turn it is byte-identical to the
flag-OFF path except for a single store-side ``rule_check`` row (A4).

Style: no em-dashes (period/comma/colon/parens only), per the citation feature
rule.
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

# The uncited primary answer carries a high-risk claim (a specific figure with a
# date verb) but no [src_N] marker. Sources exist in the registry, so the gate
# raises ``uncited_high_risk`` and plans an ATTRIBUTION repair.
_UNCITED = "Tesla was founded in 2003."
_CITED = "Tesla was founded in 2003 [src_1]."


# ---------------------------------------------------------------------------
# Harness doubles (copied verbatim from tests/evidence/test_citation_repair_loop.py)
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
    """Plays one scripted generation (a list of event dicts) per run_turn call.

    The driver instantiates the adapter ONCE and calls ``run_turn`` for the
    primary generation and again for every repair round, so a per-runner call
    index scripts multi-turn model behavior on the real loop.
    """

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


class _CaptureCollector:
    """Exposes the session registry and captures verdict records."""

    def __init__(self, *, registry: object) -> None:
        self._registry = registry
        self.records: list[object] = []

    def source_registry_for(self, session_id: str) -> object:
        del session_id
        return self._registry

    def record_audit_evidence_for_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        record: object,
        tool_call_id: str,
        producing_rule_id: str,
    ) -> None:
        del session_id, turn_id, tool_name, tool_call_id, producing_rule_id
        self.records.append(record)


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
            # Budget may drive more repair rounds than scripted: an empty
            # generation is a silent model, which the gate re-evaluates.
            script = []
        self._index += 1
        return script

    async def run_async(self, **kwargs: object) -> AsyncIterator[object]:
        if False:  # pragma: no cover - never iterated, compat with adapter API
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


def _repair_env(monkeypatch, *, max_attempts: str) -> None:
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_GATE_MODE", "repair")
    monkeypatch.setenv("MAGI_SOURCE_CITATION_REPAIR_MAX_ATTEMPTS", max_attempts)
    # Force the ATTRIBUTION repair kind (sources exist), never induce-search.
    monkeypatch.setenv("MAGI_SOURCE_CITATION_INDUCE_SEARCH_ENABLED", "0")
    # Isolate the citation loop from the coding repair loop.
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)


def _drive(driver: MagiEngineDriver, *, prompt: str, turn_id: str = "t") -> list[object]:
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


def _token_deltas(items: list[object]) -> list[str]:
    out: list[str] = []
    for item in items:
        if (
            isinstance(item, RuntimeEvent)
            and item.type == "token"
            and isinstance(item.payload, Mapping)
            and item.payload.get("type") == "text_delta"
        ):
            delta = item.payload.get("delta")
            if isinstance(delta, str):
                out.append(delta)
    return out


def _client_answer(items: list[object]) -> str:
    """Reconstruct the answer the consumer sees, replaying the driver's own
    emitted_text semantics: a response_clear resets, a text_delta appends."""
    answer = ""
    for item in items:
        if not isinstance(item, RuntimeEvent) or not isinstance(item.payload, Mapping):
            continue
        kind = item.payload.get("type")
        if kind == "response_clear":
            answer = ""
        elif kind == "text_delta":
            delta = item.payload.get("delta")
            if isinstance(delta, str):
                answer += delta
    return answer


def _citation_records(collector: _CaptureCollector) -> list[object]:
    return [
        record
        for record in collector.records
        if getattr(record, "type", "") == "custom:CitationVerdict"
    ]


# ---------------------------------------------------------------------------
# Verify-specific doubles (evidence collector with turn/session corpus + sink)
# ---------------------------------------------------------------------------


class _VerifyCollector:
    """Collector stub exposing the evidence-corpus read path verify audits.

    ``turn_records_script`` is a list of record tuples returned by successive
    ``collect_for_turn`` calls (one call per verify pass); the last entry is
    sticky so surplus calls repeat it. ``source_registry_for`` returns None so
    the citation member is inert unless a real registry is supplied.
    """

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
    """Captures the store-side observability events the driver emits."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def __call__(self, payload: dict, session_id: str, turn_id: str) -> None:
        del session_id, turn_id
        self.events.append(dict(payload))


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


def _status_types(items: list[object]) -> list[str]:
    out: list[str] = []
    for item in items:
        if isinstance(item, RuntimeEvent) and isinstance(item.payload, Mapping):
            t = item.payload.get("type")
            if isinstance(t, str):
                out.append(t)
    return out


def _verify_status(items: list[object]) -> list[object]:
    return [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and isinstance(item.payload, Mapping)
        and str(item.payload.get("type", "")).startswith("verify")
    ]


def _stream_shape(items: list[object]) -> list[object]:
    shape: list[object] = []
    for item in items:
        if isinstance(item, RuntimeEvent):
            payload = item.payload if isinstance(item.payload, Mapping) else {}
            shape.append((item.type, dict(payload)))
        elif isinstance(item, EngineResult):
            shape.append(("__terminal__", item.terminal, item.error))
    return shape


def _verify_rows(sink: _SinkCapture) -> list[dict[str, object]]:
    return [e for e in sink.events if e.get("sourceType") == "verify"]


def _terminal(items: list[object]) -> EngineResult:
    terminal = items[-1]
    assert isinstance(terminal, EngineResult)
    return terminal


# ---------------------------------------------------------------------------
# 1. Flag OFF is byte-identical to flag ON on a clean, gate-less turn
# ---------------------------------------------------------------------------


def test_flag_off_stream_is_byte_identical(monkeypatch) -> None:
    """A gate-less clean turn streams identically with and without the master
    flag: verify short-circuits at step 1 when OFF and emits only a store-side
    row when ON, so the RuntimeEvent stream is element-wise equal and no
    ``verify_*`` status ever leaks into it."""

    def run(enabled: bool) -> tuple[list[object], _SinkCapture]:
        _verify_env(monkeypatch, enabled=enabled)
        collector = _VerifyCollector(turn_records_script=[()])
        sink = _SinkCapture()
        runner = _ScriptedRunner(
            generations=[[{"type": "text_delta", "delta": "The sky is blue."}]],
            collector=collector,
        )
        driver = MagiEngineDriver(
            runner=runner, runner_policy_assembly=None, event_sink=sink
        )
        return _drive(driver, prompt="what colour is the sky"), sink

    off_items, _off_sink = run(False)
    on_items, _on_sink = run(True)

    assert _stream_shape(off_items) == _stream_shape(on_items)
    assert not _verify_status(off_items)
    assert not _verify_status(on_items)


# ---------------------------------------------------------------------------
# 2. Flag ON clean turn: stream byte-identical except one store-side rule_check
# ---------------------------------------------------------------------------


def test_flag_on_clean_turn_is_byte_identical_except_one_rule_check_row(
    monkeypatch,
) -> None:
    """A4: a gate-less clean turn with the flag ON streams NO new status event
    (no ``pre_final_evidence_gate`` yield) yet records exactly one store-side
    ``rule_check`` row with ``sourceType == 'verify'``."""
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

    assert "pre_final_evidence_gate" not in _status_types(items)
    assert not _verify_status(items)
    assert runner.call_count == 1
    rows = _verify_rows(sink)
    # PR-1: a clean turn now emits 2 verify rows: 1 per-pass row (verifyKind=="pass")
    # + 1 turn twin (verifyKind=="turn"). Both are store-side only (stream unchanged).
    assert len(rows) == 2
    assert all(r["type"] == "rule_check" for r in rows)
    pass_rows = [r for r in rows if r.get("verifyKind") == "pass"]
    turn_rows = [r for r in rows if r.get("verifyKind") == "turn"]
    assert len(pass_rows) == 1
    assert len(turn_rows) == 1
    assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 3. Contradiction nudges once, revision delivers, response_clear buffered
# ---------------------------------------------------------------------------


def test_contradiction_nudges_then_revision_delivers(monkeypatch) -> None:
    """A failing TestRun plus a "tests pass" claim fires a high finding: one
    ``verify_nudge_scheduled`` status, a scripted revision round buffers its
    ``response_clear`` (not streamed live) and replays clear-then-tokens on the
    flush, and the delivered text equals the revision."""
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

    types_seq = _status_types(items)
    assert types_seq.count("verify_nudge_scheduled") == 1
    assert runner.call_count == 2
    # response_clear was buffered and replayed AFTER the nudge, never live.
    nudge_idx = types_seq.index("verify_nudge_scheduled")
    assert "response_clear" in types_seq
    assert types_seq.index("response_clear") > nudge_idx
    assert _client_answer(items) == revision
    assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 4. SHIP_AS_IS delivers the original with no leaked marker / clear
# ---------------------------------------------------------------------------


def test_ship_as_is_delivers_original_without_leaks(monkeypatch) -> None:
    """A SHIP_AS_IS round (response_clear + marker) is buffered then DISCARDED:
    neither the marker text nor a streamed ``response_clear`` reaches the
    consumer, the delivered answer is the original, and the turn completes."""
    _verify_env(monkeypatch, enabled=True)
    original = "All 93 tests pass."
    collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": original}],
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

    items = _drive(driver, prompt="did the tests pass")

    assert not any("SHIP_AS_IS" in delta for delta in _token_deltas(items))
    assert "response_clear" not in _status_types(items)
    assert _client_answer(items) == original
    assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 5. A rephrased but unfixed claim does not re-nudge (A1 keying)
# ---------------------------------------------------------------------------


def test_rephrased_unfixed_claim_does_not_renudge(monkeypatch) -> None:
    """A1 end-to-end: round 2 rephrases the same pass-claim while the SAME
    failing TestRun still indicts it. The evidence_ref-keyed fingerprint is
    already surfaced, so exactly ONE nudge fires across the whole stream and the
    turn delivers."""
    _verify_env(monkeypatch, enabled=True)
    # Same failing record on both passes (sticky single-entry script).
    collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink = _SinkCapture()
    rephrase = "Every one of the 93 tests passes cleanly."
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": rephrase},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    items = _drive(driver, prompt="did the tests pass")

    assert _status_types(items).count("verify_nudge_scheduled") == 1
    assert _client_answer(items) == rephrase
    assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 6. Tool loop-back accumulates evidence and re-audits, then resolves
# ---------------------------------------------------------------------------


def test_tool_loopback_reaudits_and_resolves(monkeypatch) -> None:
    """Round 2 plays tool events (streamed LIVE) plus a new PASSING TestRun into
    the collector plus a revised claim. The second audit pass emits its own
    ``rule_check`` row, sees no contradiction, and the turn delivers."""
    _verify_env(monkeypatch, enabled=True)
    collector = _VerifyCollector(
        turn_records_script=[(_failing_testrun(),), (_passing_testrun(),)]
    )
    sink = _SinkCapture()
    revised = "The 93 tests pass after the fix."
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": "All 93 tests pass."}],
            [
                {"type": "tool_start", "name": "bash", "toolCallId": "c1"},
                {"type": "tool_result", "toolCallId": "c1", "output": "ok"},
                {"type": "response_clear"},
                {"type": "text_delta", "delta": revised},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    items = _drive(driver, prompt="did the tests pass")

    types_seq = _status_types(items)
    # Tool events streamed LIVE during the nudge round (not buffered).
    tool_events = [
        it
        for it in items
        if isinstance(it, RuntimeEvent) and it.type == "tool"
    ]
    assert tool_events
    assert types_seq.count("verify_nudge_scheduled") == 1
    assert _client_answer(items) == revised
    assert _terminal(items).terminal == Terminal.completed
    # PR-1: two audit passes -> 2 per-pass rows + 1 turn twin = 3 verify rows total.
    all_verify = _verify_rows(sink)
    pass_rows = [r for r in all_verify if r.get("verifyKind") == "pass"]
    turn_rows = [r for r in all_verify if r.get("verifyKind") == "turn"]
    assert len(pass_rows) == 2
    assert len(turn_rows) == 1


# ---------------------------------------------------------------------------
# 7. SITE-A: nudge fires on a gate-less turn (payload None every pass)
# ---------------------------------------------------------------------------


def test_site_a_nudge_on_gateless_turn(monkeypatch) -> None:
    """With no assembly and citation off, ``_pre_final_gate_payload`` returns
    None every pass, so the nudge must fire through SITE-A (the None-payload
    exit) with no ``pre_final_evidence_gate`` or coding-repair status present."""
    _verify_env(monkeypatch, enabled=True)
    collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
    sink = _SinkCapture()
    revision = "Correction: the tests fail with exit code 1."
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

    types_seq = _status_types(items)
    assert "verify_nudge_scheduled" in types_seq
    assert "pre_final_evidence_gate" not in types_seq
    assert "coding_repair_retry_scheduled" not in types_seq
    assert "source_citation_repair_scheduled" not in types_seq
    assert _client_answer(items) == revision
    assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 8. Block-path precedence unchanged (citation repair runs first, verify last)
# ---------------------------------------------------------------------------


def test_block_path_precedence_unchanged(monkeypatch) -> None:
    """A citation repair-mode turn: the citation repair runs FIRST
    (``source_citation_repair_scheduled``), verify injects nothing during block
    passes (its member evaluates only after the block clears), the cited answer
    delivers, and the CitationVerdict record still reads ``cited``."""
    _repair_env(monkeypatch, max_attempts="2")
    monkeypatch.setenv("MAGI_VERIFY_BEFORE_REPLYING_ENABLED", "1")
    collector = _CaptureCollector(registry=_registry((_source_record(),)))
    sink = _SinkCapture()
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": _UNCITED}],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": _CITED},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    items = _drive(driver, prompt="When was Tesla founded")

    types_seq = _status_types(items)
    assert "source_citation_repair_scheduled" in types_seq
    assert "verify_nudge_scheduled" not in types_seq
    assert _client_answer(items) == _CITED
    assert _terminal(items).terminal == Terminal.completed
    records = _citation_records(collector)
    assert len(records) == 1
    assert records[0].fields["verdict"] == "cited"


# ---------------------------------------------------------------------------
# 9. Verify never yields Terminal.error and never mutates repairDecision
# ---------------------------------------------------------------------------


def test_verify_never_yields_terminal_error(monkeypatch) -> None:
    """Across the nudged-revision and SHIP_AS_IS variants, no ``Terminal.error``
    terminal is produced and no ``repairDecision`` status originates from
    verify."""
    for revision in (
        "The tests fail with exit code 1.",
        "SHIP_AS_IS",
    ):
        _verify_env(monkeypatch, enabled=True)
        collector = _VerifyCollector(turn_records_script=[(_failing_testrun(),)])
        sink = _SinkCapture()
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

        for item in items:
            if isinstance(item, EngineResult):
                assert item.terminal != Terminal.error
        # No verify pass emits a repairDecision status.
        for item in items:
            if isinstance(item, RuntimeEvent) and isinstance(item.payload, Mapping):
                if item.payload.get("type") == "verify_nudge_scheduled":
                    assert "repairDecision" not in item.payload
        assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 10. An advisory-only finding nudges exactly once (per-signal-class fingerprint)
# ---------------------------------------------------------------------------


def test_advisory_only_finding_nudges_once(monkeypatch) -> None:
    """A sycophancy praise-density finding is advisory. The model ships a
    revision that still trips the same signal class, but the per-signal-class
    fingerprint is already surfaced, so exactly ONE nudge fires."""
    _verify_env(monkeypatch, enabled=True)
    # No collector records needed: sycophancy is pure text.
    collector = _VerifyCollector(turn_records_script=[()])
    sink = _SinkCapture()
    still_praise = "You're absolutely right, great catch. The answer is still 42."
    runner = _ScriptedRunner(
        generations=[
            [
                {
                    "type": "text_delta",
                    "delta": "You're absolutely right, great catch. The answer is 42.",
                }
            ],
            [
                {"type": "response_clear"},
                {"type": "text_delta", "delta": still_praise},
            ],
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(
        runner=runner, runner_policy_assembly=None, event_sink=sink
    )

    items = _drive(driver, prompt="is the answer 42")

    assert _status_types(items).count("verify_nudge_scheduled") == 1
    assert _client_answer(items) == still_praise
    assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 11. U2: execution_claims member fires end-to-end on a failed-spawn record
# ---------------------------------------------------------------------------


def _subagent_spawn(
    *,
    status: str = "error",
    reason: str | None = "child_turn_timeout",
    model: str | None = "opus-4-8",
    provider: str | None = "anthropic",
    persona: str | None = None,
    ref: str = "sp_001",
) -> object:
    """Duck-typed SubagentSpawn evidence record (verified fields shape).

    Mirrors first_party_activity.to_evidence_record: type
    custom:FirstPartySubagentSpawn, camelCase fields with top-level status /
    reason / errorCode and a nested detail{spawnStatus, persona, model,
    provider}. Copied from tests/evidence/test_verify_audit.py to keep this
    driver-level test self-contained.
    """
    detail: dict[str, object] = {
        "spawnStatus": status,
        "persona": persona or "",
        "promptDigest": "",
        "requestedDepth": 0,
        "liveChildRunnerAttached": False,
    }
    if provider is not None:
        detail["provider"] = provider
    if model is not None:
        detail["model"] = model
    ev_status = {"ok": "ok", "error": "failed"}.get(status, "unknown")
    return SimpleNamespace(
        type="custom:FirstPartySubagentSpawn",
        status=ev_status,
        fields={
            "status": status,
            "reason": reason,
            "errorCode": None,
            "evidenceRef": ref,
            "detail": detail,
        },
        observed_at=1000,
    )


class _CapturingScriptedAdapter(_ScriptedAdapter):
    """Scripted adapter that records the newMessage text of every run_turn.

    The nudge continuation is fed to the runner as the next ``newMessage``
    (driver.py builds ``runner_turn_input_cls(newMessage=...)`` with the nudge
    text on the repair round), so capturing the text the adapter receives is the
    driver-level view of the nudge overlay handed back to the model."""

    captured_messages: list[str] = []

    async def run_turn(self, runner_input: object):
        text = ""
        kwargs = getattr(runner_input, "kwargs", None)
        if isinstance(kwargs, Mapping):
            message = kwargs.get("newMessage")
            parts = getattr(message, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str):
                    text += part_text
        type(self).captured_messages.append(text)
        for event in self.runner.next_generation():
            yield event


def _capturing_engine_deps() -> dict[str, object]:
    deps = _engine_deps()
    deps["OpenMagiRunnerAdapter"] = _CapturingScriptedAdapter
    return deps


def test_execution_claims_nudge_fires_end_to_end(monkeypatch) -> None:
    """U2 proof: a candidate presenting a FAILED Opus spawn as a completed
    review, with the failing SubagentSpawn record in the collector corpus,
    drives ``_verify_nudge_check`` to (a) hand the model a nudge overlay
    containing the ``[verify_before_replying.execution_claims]`` finding with the
    human-readable reason, and (b) emit a per-pass verify observability row whose
    findings carry the new rule's claimClass."""
    _CapturingScriptedAdapter.captured_messages = []
    _verify_env(monkeypatch, enabled=True)
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _capturing_engine_deps)

    failed = _subagent_spawn(
        status="error", reason="child_turn_timeout", model="opus-4-8", ref="sp_001"
    )
    collector = _VerifyCollector(turn_records_script=[(failed,)])
    sink = _SinkCapture()
    # Primary presents the failed spawn as a completed review; the revision
    # discloses the timeout so the finding resolves and the turn delivers.
    original = "Opus reviewed this and concluded the design is sound."
    revision = "Correction: the Opus review timed out and never completed."
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": original}],
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

    items = _drive(driver, prompt="did the review pass")

    # (a) Exactly one nudge fired and the continuation handed to the model on the
    #     nudge round carries the execution_claims rule id plus the human reason
    #     and the raw token (auditable).
    assert _status_types(items).count("verify_nudge_scheduled") == 1
    nudge_messages = [
        m
        for m in _CapturingScriptedAdapter.captured_messages
        if "verify_before_replying.execution_claims" in m
    ]
    assert len(nudge_messages) == 1
    overlay = nudge_messages[0]
    assert "turn timeout" in overlay, "human-readable reason renders in the nudge"
    assert "child_turn_timeout" in overlay, "raw reason token renders for audit"

    # (b) A per-pass verify observability row carries the new rule's claimClass.
    pass_rows = [r for r in _verify_rows(sink) if r.get("verifyKind") == "pass"]
    claim_classes = {
        str(finding.get("claimClass"))
        for row in pass_rows
        for finding in row.get("findings", [])
    }
    assert (
        "failed_execution_presented_as_success" in claim_classes
        or "fabricated_execution" in claim_classes
    )
    exec_rule_findings = [
        finding
        for row in pass_rows
        for finding in row.get("findings", [])
        if finding.get("ruleId") == "verify_before_replying.execution_claims"
    ]
    assert exec_rule_findings, "an execution_claims finding row was recorded"

    assert _client_answer(items) == revision
    assert _terminal(items).terminal == Terminal.completed
