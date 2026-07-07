"""Engine wiring for the WS6 PR6b fact-grounding / contract evidence hedge.

Design: WS6 deterministic-verification activation, PR6b (enable fact_grounding +
final_output_gate, hedge-not-refuse).

These tests drive the real ``MagiEngineDriver`` pre-final gate. With
``MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED`` ON, an in-scope research/contract
recipe whose pre-final gate would BLOCK on a non-empty missing validator set
(e.g. a fact-grounding ``guess``, or the satisfier-less ``citation_support`` on
``openmagi.research``) is converted from the hard
``pre_final_evidence_gate_blocked`` terminal into a SOFT appended
``evidence_hedge_applied`` status event plus a trailing ``text_delta`` suffix,
and a normal ``Terminal.completed``. The already-streamed answer is kept and
never retracted. With the flag OFF the existing hard-refuse behavior is
byte-identical.

The §1a CRITICAL CORRECTION is the load-bearing case: the soft branch fires on
``decision == "block"`` with ANY non-empty missing set, NOT only on
``fact_grounding`` (``citation_support`` has no live satisfier).
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.runtime.events import RuntimeEvent

_HEDGE_FLAG = "MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED"
_VERIFY_FLAG = "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED"
_SOFT_BLOCK_FLAG = "MAGI_RESEARCH_GOVERNANCE_SOFT_BLOCK_ENABLED"

# An ungrounded specific numeric value (not present in the corpus below) -> guess.
_GUESS_ANSWER = "The channel has exactly 424242 subscribers."
# A grounded numeric value (present in the corpus) -> grounded, clears fact_grounding.
_GROUNDED_ANSWER = "It has 776665 subscribers."


class _NoopRunner:
    async def run_async(self, **kwargs: object) -> AsyncIterator[object]:
        if False:
            yield kwargs


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


class _TextEmittingAdapter:
    def __init__(self, *, runner: object, num_recent_events: int | None = None) -> None:
        self.runner = runner

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        text = getattr(self.runner, "final_text", "")
        if text:
            yield {"type": "text_delta", "delta": text}


class _StatefulRepairAdapter:
    """First run_turn = initial turn (an edit tool_start + the answer text);
    every subsequent run_turn = a repair attempt that streams one token."""

    def __init__(self, *, runner: object, num_recent_events: int | None = None) -> None:
        self.runner = runner
        self._calls = 0

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        self._calls += 1
        if self._calls == 1:
            yield {"type": "tool_start", "name": "Edit", "id": "edit-1"}
            yield {"type": "text_delta", "delta": getattr(self.runner, "final_text", "")}
        else:
            yield {"type": "text_delta", "delta": "repair attempt token"}


class _PassthroughBridge:
    def __init__(self, *, live_compatible: bool) -> None:
        self.live_compatible = live_compatible

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> object:
        del turn_id
        if isinstance(adk_event, Mapping):
            return type("Projection", (), {"agent_events": [dict(adk_event)]})()
        return type("Projection", (), {"agent_events": []})()


def _engine_deps(adapter_cls: type = _TextEmittingAdapter) -> dict[str, object]:
    return {
        "types": _FakeTypes,
        "OpenMagiEventBridge": _PassthroughBridge,
        "OpenMagiRunnerAdapter": adapter_cls,
        "RunnerTurnInput": _CapturedRunnerInput,
        "sanitize_agent_event": lambda event: event,
    }


class _TextRunner(_NoopRunner):
    def __init__(self, *, final_text: str) -> None:
        self.final_text = final_text


def _fact_grounding_only_assembly() -> RunnerPolicyAssembly:
    # A contract recipe requiring ONLY the satisfiable label fact_grounding (NOT
    # openmagi.research): a grounded answer clears it (pass), an ungrounded guess
    # leaves it missing (block). Research scope holds because fact_grounding is a
    # research-contract validator label.
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.research",),
        evidenceRequirements=(),
        requiredValidators=("fact_grounding",),
        missingEvidenceAction="insufficient_evidence",
        repairPolicy={"action": "insufficient_evidence", "source": "recipe-materializer"},
        taskProfile={"taskType": "research"},
    )


def _openmagi_research_assembly() -> RunnerPolicyAssembly:
    # openmagi.research: citation_support has no live satisfier (ALWAYS unmet) and
    # fact_grounding is also unmet on a guess, so the gate blocks.
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.research",),
        evidenceRequirements=(),
        requiredValidators=("citation_support", "fact_grounding"),
        missingEvidenceAction="insufficient_evidence",
        repairPolicy={"action": "insufficient_evidence", "source": "recipe-materializer"},
        taskProfile={"taskType": "research"},
    )


def _coding_research_repair_assembly() -> RunnerPolicyAssembly:
    # Research scope (fact_grounding label) AND a coding repair driver
    # (dev-coding pack + repair_required action) so a real repair loop runs and
    # buffers/suppresses the live answer (MINOR-2 precondition).
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.dev-coding",),
        evidenceRequirements=(),
        requiredValidators=("fact_grounding",),
        missingEvidenceAction="repair_required",
        repairPolicy={
            "action": "repair_required",
            "maxAttempts": 1,
            "source": "recipe-materializer",
        },
        taskProfile={"taskType": "coding"},
    )


def _source_record(preview: str = "The channel reported 776,665 subscribers.") -> dict[str, object]:
    return {
        "type": "SourceInspection",
        "status": "ok",
        "observedAt": 1000.0,
        "source": {"kind": "tool_trace", "toolName": "WebFetch"},
        "preview": preview,
    }


def _drive(
    driver: MagiEngineDriver,
    *,
    prompt: str,
    turn_id: str = "t",
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


def _answer_deltas(items: list[object]) -> list[str]:
    deltas: list[str] = []
    for item in items:
        if (
            isinstance(item, RuntimeEvent)
            and item.type == "token"
            and item.payload.get("type") == "text_delta"
        ):
            delta = item.payload.get("delta")
            if isinstance(delta, str):
                deltas.append(delta)
    return deltas


def _hedge_events(items: list[object]) -> list[RuntimeEvent]:
    return [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "evidence_hedge_applied"
    ]


def _hedge_env(monkeypatch, adapter_cls: type = _TextEmittingAdapter) -> None:
    # Hermetic: clear any shell-exported WS6 flags, then set only what the test
    # needs. The lab profile exports these, so an un-cleared shell pollutes the run.
    for flag in (_HEDGE_FLAG, _VERIFY_FLAG, _SOFT_BLOCK_FLAG):
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", lambda: _engine_deps(adapter_cls))


def test_guess_hedges_not_refuses(monkeypatch) -> None:
    _hedge_env(monkeypatch)
    monkeypatch.setenv(_VERIFY_FLAG, "1")
    monkeypatch.setenv(_HEDGE_FLAG, "1")
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_GUESS_ANSWER),
        runner_policy_assembly=_fact_grounding_only_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and report the subscriber count")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error is None

    hedges = _hedge_events(items)
    assert len(hedges) == 1
    payload = hedges[0].payload
    assert payload["verdict"] == "guess"
    assert tuple(payload["missingValidators"]) == ("fact_grounding",)
    assert payload["hedgeApplied"] is True

    # A trailing hedge-notice text_delta follows the status event (a SUFFIX).
    hedge_index = items.index(hedges[0])
    trailing = [
        item
        for item in items[hedge_index:]
        if isinstance(item, RuntimeEvent)
        and item.type == "token"
        and item.payload.get("type") == "text_delta"
    ]
    assert trailing, "a trailing hedge text_delta must follow the status event"

    # The answer-body deltas (before the hedge) are byte-identical to the no-hedge
    # run -> only a suffix was added.
    monkeypatch.delenv(_HEDGE_FLAG, raising=False)
    baseline_driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_GUESS_ANSWER),
        runner_policy_assembly=_fact_grounding_only_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )
    baseline_items = _drive(
        baseline_driver, prompt="research the channel and report the subscriber count"
    )
    assert _answer_deltas(items)[: len(_answer_deltas(baseline_items))] == _answer_deltas(
        baseline_items
    )


def test_citation_support_class_block_hedges_not_refuses(monkeypatch) -> None:
    # §1a load-bearing case: openmagi.research blocks on the satisfier-less
    # citation_support regardless of grounding; the soft branch must fire on the
    # FULL non-empty missing set, not only fact_grounding.
    _hedge_env(monkeypatch)
    monkeypatch.setenv(_VERIFY_FLAG, "1")
    monkeypatch.setenv(_HEDGE_FLAG, "1")
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_GUESS_ANSWER),
        runner_policy_assembly=_openmagi_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    hedges = _hedge_events(items)
    assert len(hedges) == 1
    assert "citation_support" in hedges[0].payload["missingValidators"]


def test_citation_support_class_block_off_mirror_hard_refuses(monkeypatch) -> None:
    # The OFF mirror: with the hedge flag absent the existing hard refuse is
    # preserved (matches test_fact_grounding_gate_wiring's pin).
    _hedge_env(monkeypatch)
    monkeypatch.setenv(_VERIFY_FLAG, "1")
    # hedge + soft-block flags stay unset (cleared by _hedge_env).
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_GROUNDED_ANSWER),
        runner_policy_assembly=_openmagi_research_assembly(),
        evidence_collector=lambda turn_id: (
            _source_record("The channel reported 776,665 subscribers."),
        )
        if turn_id == "t"
        else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert _hedge_events(items) == []
    # A grounded answer clears fact_grounding, leaving citation_support missing.
    gate_status = [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.type == "status"
        and item.payload.get("type") == "pre_final_evidence_gate"
    ]
    assert gate_status
    assert "citation_support" in gate_status[-1].payload["missingValidators"]


def test_grounded_answer_pass_byte_identical(monkeypatch) -> None:
    # A grounded answer clears fact_grounding -> decision=pass -> the soft branch
    # is never reached, so the hedge flag is a no-op. The frame stream with the
    # hedge flag ON is byte-identical to the lab baseline (hedge unset).
    def _run(hedge_on: bool) -> list[object]:
        _hedge_env(monkeypatch)
        monkeypatch.setenv(_VERIFY_FLAG, "1")
        if hedge_on:
            monkeypatch.setenv(_HEDGE_FLAG, "1")
        driver = MagiEngineDriver(
            runner=_TextRunner(final_text=_GROUNDED_ANSWER),
            runner_policy_assembly=_fact_grounding_only_assembly(),
            evidence_collector=lambda turn_id: (
                _source_record("The channel reported 776,665 subscribers."),
            )
            if turn_id == "t"
            else (),
        )
        return _drive(driver, prompt="research the channel and report the subscriber count")

    hedge_items = _run(hedge_on=True)
    baseline_items = _run(hedge_on=False)

    # Both pass with no hedge event, byte-identical answer deltas.
    assert _hedge_events(hedge_items) == []
    assert _hedge_events(baseline_items) == []
    assert _answer_deltas(hedge_items) == _answer_deltas(baseline_items)
    assert isinstance(hedge_items[-1], EngineResult)
    assert hedge_items[-1].terminal == Terminal.completed
    assert isinstance(baseline_items[-1], EngineResult)
    assert baseline_items[-1].terminal == Terminal.completed

    # Registry-default OFF (every WS6 flag unset, incl. verification): the
    # ungrounded satisfier is inert -> no hedge event at all.
    _hedge_env(monkeypatch)
    registry_driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_GROUNDED_ANSWER),
        runner_policy_assembly=_fact_grounding_only_assembly(),
        evidence_collector=lambda turn_id: (
            _source_record("The channel reported 776,665 subscribers."),
        )
        if turn_id == "t"
        else (),
    )
    registry_items = _drive(
        registry_driver, prompt="research the channel and report the subscriber count"
    )
    assert _hedge_events(registry_items) == []


def test_hedge_value_redacted(monkeypatch) -> None:
    # An extractedValue containing a path/secret marker must be OMITTED-or-DIGESTED
    # (never the raw value), the soft branch must NOT crash, and it must NOT fall
    # back to the hard refuse.
    _hedge_env(monkeypatch)
    monkeypatch.setenv(_VERIFY_FLAG, "1")
    monkeypatch.setenv(_HEDGE_FLAG, "1")

    from magi_agent.evidence import claim_grounding as claim_grounding_module
    from magi_agent.evidence.claim_grounding import FactGroundingVerdict

    secret_value = "/Users/kevin/.ssh/id_rsa"

    def _guess_with_secret(self, *, final_text: str, evidence_records):  # noqa: ANN001
        del final_text, evidence_records
        return FactGroundingVerdict(
            status="guess",
            reason_code="specific_value_unsupported_by_corpus",
            extracted_value=secret_value,
            satisfied_label=None,
        )

    monkeypatch.setattr(
        claim_grounding_module.FactGroundingEvidenceProducer,
        "evaluate",
        _guess_with_secret,
    )

    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_GUESS_ANSWER),
        runner_policy_assembly=_fact_grounding_only_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and report the subscriber count")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error != "pre_final_evidence_gate_blocked"

    hedges = _hedge_events(items)
    assert len(hedges) == 1
    emitted = hedges[0].payload.get("extractedValue")
    # Either omitted, or digested (ref:<sha>) -- but NEVER the raw secret path.
    assert emitted != secret_value
    assert secret_value not in str(emitted)
    if emitted is not None:
        assert str(emitted).startswith("ref:")


def test_repair_buffer_empty_precondition(monkeypatch) -> None:
    # MINOR-2: the soft branch fires ONLY when repair_token_buffer is empty. When
    # the coding repair loop buffered then suppressed/discarded the live answer,
    # the suffix invariant fails, so the soft resolver MUST be skipped and the
    # existing suppress + hard-terminal path runs unchanged.
    _hedge_env(monkeypatch, adapter_cls=_StatefulRepairAdapter)
    monkeypatch.setenv(_HEDGE_FLAG, "1")  # soft WOULD fire if not suppressed
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "1")

    soft_calls: list[object] = []
    original = MagiEngineDriver._apply_soft_verification_consequence

    def _spy(self, **kwargs):  # noqa: ANN001
        soft_calls.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(
        MagiEngineDriver, "_apply_soft_verification_consequence", _spy
    )

    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="draft answer"),
        runner_policy_assembly=_coding_research_repair_assembly(),
        evidence_collector=lambda turn_id: (),
    )

    items = _drive(driver, prompt="fix the bug in the code file")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    # The soft resolver was guarded out entirely on the suppressed turn.
    assert soft_calls == []
    assert _hedge_events(items) == []
    # The repair loop DID suppress a buffered answer (proves the precondition).
    suppressed = [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.type == "status"
        and item.payload.get("type") == "coding_repair_output_suppressed"
    ]
    assert suppressed, "the repair loop must have suppressed the buffered answer"


def test_status_payload_no_reserved_delta_keys(monkeypatch) -> None:
    # MINOR-1: the evidence_hedge_applied status payload must not carry a
    # text/content/delta key or the transport would leak it into the answer body.
    _hedge_env(monkeypatch)
    monkeypatch.setenv(_VERIFY_FLAG, "1")
    monkeypatch.setenv(_HEDGE_FLAG, "1")
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_GUESS_ANSWER),
        runner_policy_assembly=_fact_grounding_only_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and report the subscriber count")
    hedges = _hedge_events(items)
    assert hedges
    for event in hedges:
        assert set(event.payload) & {"text", "content", "delta"} == set()
