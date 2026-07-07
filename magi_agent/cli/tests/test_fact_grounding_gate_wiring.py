"""Engine wiring for semantic grounding verification (live evidence gate).

These tests drive the real ``MagiEngineDriver`` pre-final gate over a research
policy whose only outstanding required-validator is the bare ``fact_grounding``
label, isolating the grounding satisfier's effect on the decision:

* Flag OFF (default): the satisfier is inert; the research gate blocks on the
  missing ``fact_grounding`` validator exactly as it does today (byte-identical).
* Flag ON + fabricated specific value (NOT in the collected corpus): ``guess``
  -> ``fact_grounding`` stays missing -> ``block`` ->
  ``pre_final_evidence_gate_blocked``.
* Flag ON + grounded specific value (present in the corpus): ``grounded`` ->
  ``fact_grounding`` satisfied -> the gate passes.
* Flag ON + a semantic-only answer (no specific value to ground): ``grounded``
  (G4 boundary) -> no false block.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.runtime.events import RuntimeEvent


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
    """Adapter that yields the configured final-answer text as a text_delta."""

    def __init__(self, *, runner: object, num_recent_events: int | None = None) -> None:
        self.runner = runner

    async def run_turn(self, runner_input: object) -> AsyncIterator[object]:
        del runner_input
        text = getattr(self.runner, "final_text", "")
        if text:
            yield {"type": "text_delta", "delta": text}


class _PassthroughBridge:
    def __init__(self, *, live_compatible: bool) -> None:
        self.live_compatible = live_compatible

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> object:
        del turn_id
        if isinstance(adk_event, Mapping):
            return type("Projection", (), {"agent_events": [dict(adk_event)]})()
        return type("Projection", (), {"agent_events": []})()


def _engine_deps() -> dict[str, object]:
    return {
        "types": _FakeTypes,
        "OpenMagiEventBridge": _PassthroughBridge,
        "OpenMagiRunnerAdapter": _TextEmittingAdapter,
        "RunnerTurnInput": _CapturedRunnerInput,
        "sanitize_agent_event": lambda event: event,
    }


class _TextRunner(_NoopRunner):
    def __init__(self, *, final_text: str) -> None:
        self.final_text = final_text


def _research_assembly() -> RunnerPolicyAssembly:
    # Research policy whose ONLY outstanding required-validator is the bare
    # fact_grounding label and with NO evidence requirements, so the gate
    # decision is governed entirely by the grounding satisfier.
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


def _source_record(preview: str) -> dict[str, object]:
    return {
        "type": "SourceInspection",
        "status": "ok",
        "observedAt": 1000.0,
        "source": {"kind": "tool_trace", "toolName": "WebFetch"},
        "preview": preview,
    }


def _drive(driver: MagiEngineDriver, *, prompt: str) -> list[object]:
    async def _run() -> list[object]:
        return [
            item
            async for item in driver.run_turn_stream(
                runtime=object(),
                turn_input={"prompt": prompt, "session_id": "s", "turn_id": "t"},
                cancel=asyncio.Event(),
            )
        ]

    return asyncio.run(_run())


def test_flag_off_research_gate_blocks_on_missing_fact_grounding(monkeypatch) -> None:
    # Byte-identical to today: with the satisfier OFF the bare fact_grounding
    # required-validator is never satisfied, so the research gate blocks.
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    records = (_source_record("The channel reported 776,665 subscribers."),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="It has 776665 subscribers."),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and report the subscriber count")
    terminal = items[-1]
    gate = next(
        item.payload
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "pre_final_evidence_gate"
    )

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert gate["decision"] == "block"
    assert gate["missingValidators"] == ["fact_grounding"]


def test_flag_on_fabricated_value_blocks(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    # Corpus does NOT contain the asserted value -> fabricated -> guess.
    records = (_source_record("The page would not load; the count is unknown."),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="It has exactly 776665 subscribers."),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and report the subscriber count")
    terminal = items[-1]
    gate = next(
        item.payload
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "pre_final_evidence_gate"
    )

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert gate["decision"] == "block"
    assert gate["missingValidators"] == ["fact_grounding"]


def test_flag_on_grounded_value_passes(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    # Corpus contains the asserted value (separator-agnostic) -> grounded.
    records = (_source_record("The channel reported 776,665 subscribers in May."),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="It has 776665 subscribers."),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and report the subscriber count")
    terminal = items[-1]
    gate = next(
        item.payload
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "pre_final_evidence_gate"
    )

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error is None
    assert gate["decision"] == "pass"
    assert gate["missingValidators"] == []
    assert "fact_grounding" in gate["matchedRefs"]


def test_flag_on_semantic_only_answer_passes_no_false_block(monkeypatch) -> None:
    # G4 boundary: a general natural-language answer asserts no specific
    # numeric/identifier value -> grounded -> no false block.
    monkeypatch.setenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    records = (_source_record("Background notes about the topic."),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="The thesis is consistent with the cited reporting."),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the topic and summarize the consensus")
    terminal = items[-1]
    gate = next(
        item.payload
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "pre_final_evidence_gate"
    )

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error is None
    assert gate["decision"] == "pass"
    assert gate["missingValidators"] == []


def test_flag_on_does_not_satisfy_unrelated_missing_validator(monkeypatch) -> None:
    # The satisfier ONLY clears fact_grounding. A grounded answer must NOT clear
    # an unrelated missing validator (e.g. citation_support) — that still blocks.
    monkeypatch.setenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    records = (_source_record("The channel reported 776,665 subscribers."),)
    assembly = RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.research",),
        evidenceRequirements=(),
        requiredValidators=("citation_support", "fact_grounding"),
        missingEvidenceAction="insufficient_evidence",
        repairPolicy={"action": "insufficient_evidence", "source": "recipe-materializer"},
        taskProfile={"taskType": "research"},
    )
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="It has 776665 subscribers."),
        runner_policy_assembly=assembly,
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and cite the subscriber count")
    terminal = items[-1]
    gate = next(
        item.payload
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "pre_final_evidence_gate"
    )

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert gate["decision"] == "block"
    # fact_grounding cleared; citation_support still missing.
    assert gate["missingValidators"] == ["citation_support"]
