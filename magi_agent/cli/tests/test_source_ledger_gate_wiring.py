"""Engine wiring for the live source-ledger evidence ref (pre-final gate).

These tests drive the real ``MagiEngineDriver`` pre-final gate over a narrow
policy whose only outstanding required-validator is the NAMED public ref
``verifier:research-source-evidence``. They prove the live-turn source ledger is
projected into the engine's harvested public refs so the gate can REQUIRE that
ref:

* Flag OFF (default): the projector is inert; even a turn that read sources does
  not emit the named ref, so the gate blocks on it (byte-identical to today,
  where only ``sha256:`` receipts ever reached the harvest).
* Flag ON + a turn whose collected evidence has >=1 inspected source: the named
  ref ``verifier:research-source-evidence`` is harvested -> the requirement is
  satisfied -> the gate passes.
* Flag ON + a turn whose collected evidence has NO inspected source: the named
  ref is absent -> the gate blocks on it.
* Flag ON must not satisfy an unrelated missing validator.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.runtime.events import RuntimeEvent

_SOURCE_EVIDENCE_REF = "verifier:research-source-evidence"


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
    def __init__(self, *, runner: object) -> None:
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


def _source_grounded_assembly() -> RunnerPolicyAssembly:
    # Narrow policy whose ONLY outstanding required-validator is the NAMED public
    # ref ``verifier:research-source-evidence`` (a satisfiable prefixed ref) and
    # with NO evidence requirements, so the gate decision is governed entirely by
    # the source-ledger projector.
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.source-grounded",),
        evidenceRequirements=(),
        requiredValidators=(_SOURCE_EVIDENCE_REF,),
        missingEvidenceAction="insufficient_evidence",
        repairPolicy={"action": "insufficient_evidence", "source": "recipe-materializer"},
        taskProfile={"taskType": "research"},
    )


def _source_record() -> dict[str, object]:
    return {
        "type": "SourceInspection",
        "status": "ok",
        "observedAt": 1000.0,
        "source": {"kind": "file", "toolName": "FileRead"},
        "preview": "the agent opened a workspace file",
    }


def _non_source_record() -> dict[str, object]:
    # A tool receipt projection with no inspected source — the shape produced
    # for a non-reading tool call (e.g. a plain Bash echo).
    return {
        "schemaVersion": "openmagi.localToolEvidenceReceipt.v1",
        "toolName": "Bash",
        "status": "ok",
        "receiptRefs": [],
        "evidenceRefs": [],
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


def _gate_payload(items: list[object]) -> dict[str, object]:
    return next(
        item.payload
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "pre_final_evidence_gate"
    )


def test_flag_off_source_read_still_blocks_on_named_ref(monkeypatch) -> None:
    # Byte-identical to today: with the projector OFF the named source-evidence
    # ref is never harvested even when sources were read, so the gate blocks.
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    records = (_source_record(),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="Here is what the file says."),
        runner_policy_assembly=_source_grounded_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="read the file and summarize it")
    terminal = items[-1]
    gate = _gate_payload(items)

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert gate["decision"] == "block"
    assert gate["missingValidators"] == [_SOURCE_EVIDENCE_REF]
    assert _SOURCE_EVIDENCE_REF not in gate["matchedRefs"]


def test_flag_on_source_read_passes(monkeypatch) -> None:
    # A turn whose collected evidence has >=1 inspected source emits the named
    # ref into the harvested public refs -> the requirement is satisfied -> pass.
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    records = (_source_record(),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="Here is what the file says."),
        runner_policy_assembly=_source_grounded_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="read the file and summarize it")
    terminal = items[-1]
    gate = _gate_payload(items)

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error is None
    assert gate["decision"] == "pass"
    assert gate["missingValidators"] == []
    assert _SOURCE_EVIDENCE_REF in gate["matchedRefs"]


def test_flag_on_no_source_read_blocks(monkeypatch) -> None:
    # A turn that read NO source does not emit the named ref -> the gate blocks.
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    records = (_non_source_record(),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="I did not open any sources."),
        runner_policy_assembly=_source_grounded_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="answer without reading anything")
    terminal = items[-1]
    gate = _gate_payload(items)

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert gate["decision"] == "block"
    assert gate["missingValidators"] == [_SOURCE_EVIDENCE_REF]
    assert _SOURCE_EVIDENCE_REF not in gate["matchedRefs"]


def test_flag_on_does_not_satisfy_unrelated_missing_validator(monkeypatch) -> None:
    # The projector ONLY clears the named source-evidence ref. A source-reading
    # turn must NOT clear an unrelated missing validator.
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)
    records = (_source_record(),)
    assembly = RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.source-grounded",),
        evidenceRequirements=(),
        requiredValidators=("fact_grounding", _SOURCE_EVIDENCE_REF),
        missingEvidenceAction="insufficient_evidence",
        repairPolicy={"action": "insufficient_evidence", "source": "recipe-materializer"},
        taskProfile={"taskType": "research"},
    )
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="Here is what the file says."),
        runner_policy_assembly=assembly,
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="read the file and ground the answer")
    terminal = items[-1]
    gate = _gate_payload(items)

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert gate["decision"] == "block"
    # source-evidence ref cleared; fact_grounding still missing.
    assert gate["missingValidators"] == ["fact_grounding"]
    assert _SOURCE_EVIDENCE_REF in gate["matchedRefs"]
