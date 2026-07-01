"""Engine wiring for the WS6 PR6a soft research-governance notice.

Design: WS6 deterministic-verification activation, PR6a (research-governance
promote to soft ``local_block_intent``).

These tests drive the real ``MagiEngineDriver`` pre-final gate over the
``openmagi.research`` recipe (whose ``citation_support`` validator has NO live
satisfier, so it is ALWAYS unmet and the gate decision is ``block``). With the
new ``MAGI_RESEARCH_GOVERNANCE_SOFT_BLOCK_ENABLED`` flag ON, the hard
``pre_final_evidence_gate_blocked`` terminal is converted into a SOFT appended
notice (a ``research_governance_notice`` status event plus a trailing
``text_delta`` suffix) and a normal ``Terminal.completed``. The answer is kept
and never retracted. With the flag OFF the existing hard-refuse behavior is
byte-identical.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import AsyncIterator

import magi_agent.cli.engine as engine_module
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.runtime.events import RuntimeEvent

_FLAG = "MAGI_RESEARCH_GOVERNANCE_SOFT_BLOCK_ENABLED"
_URL_ANSWER = "According to https://example.com/report the launch shipped in 2026."


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


def _research_assembly() -> RunnerPolicyAssembly:
    # openmagi.research: citation_support has no satisfier (always missing) and
    # fact_grounding is also missing when its flag is OFF, so the gate blocks.
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


def _non_research_assembly() -> RunnerPolicyAssembly:
    # An office-automation recipe: the gate applies and blocks on the unmet
    # (non-research) preview_before_write validator, but it is NOT research
    # scope, so the soft research notice must NOT fire.
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=("openmagi.office-automation",),
        evidenceRequirements=(),
        requiredValidators=("preview_before_write",),
        missingEvidenceAction="insufficient_evidence",
        repairPolicy={"action": "insufficient_evidence", "source": "recipe-materializer"},
        taskProfile={"taskType": "office"},
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


def _notice_events(items: list[object]) -> list[RuntimeEvent]:
    return [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "research_governance_notice"
    ]


def _common_env(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    monkeypatch.setattr(engine_module, "_lazy_engine_deps", _engine_deps)


def test_off_path_byte_identical_registry_default(monkeypatch) -> None:
    # Registry-default OFF (both flags unset): the research gate blocks on the
    # unsatisfiable citation_support exactly as today (hard refuse, no notice).
    monkeypatch.delenv(_FLAG, raising=False)
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    _common_env(monkeypatch)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert _notice_events(items) == []


def test_off_path_byte_identical_lab_baseline(monkeypatch) -> None:
    # Lab baseline: MAGI_FACT_GROUNDING_VERIFICATION_ENABLED=1 already set but
    # the new soft flag is unset -> existing hard refuse preserved (no notice).
    monkeypatch.delenv(_FLAG, raising=False)
    monkeypatch.setenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "1")
    _common_env(monkeypatch)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert _notice_events(items) == []


def test_soft_block_emits_notice_and_keeps_answer(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    _common_env(monkeypatch)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error is None

    notices = _notice_events(items)
    assert len(notices) == 1
    payload = notices[0].payload
    assert payload["mode"] == "local_block_intent"
    assert "output_link_not_in_source_ledger" in payload["reasonCodes"]
    assert payload["citedWithoutSource"]
    assert payload["noticeAppended"] is True

    # Notice text rides on a dedicated trailing text_delta AFTER the answer.
    notice_index = items.index(notices[0])
    trailing_deltas = [
        item
        for item in items[notice_index:]
        if isinstance(item, RuntimeEvent)
        and item.type == "token"
        and item.payload.get("type") == "text_delta"
    ]
    assert trailing_deltas, "a trailing notice text_delta must follow the status event"

    # The answer-body deltas (everything before the notice) are byte-identical
    # to a no-flag run: only a suffix was added.
    monkeypatch.delenv(_FLAG, raising=False)
    baseline_driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )
    baseline_items = _drive(baseline_driver, prompt="research the launch and cite sources")
    assert _answer_deltas(items)[: len(_answer_deltas(baseline_items))] == _answer_deltas(
        baseline_items
    )


def test_grounded_answer_still_blocked_on_citation_support_gets_soft_notice(monkeypatch) -> None:
    # The CRITICAL CORRECTION (design 1a): even a grounded answer (no URL, no
    # factual-claim miss) is still blocked on the unsatisfiable citation_support;
    # the soft branch must fire on the FULL non-empty missing set, not only on
    # fact_grounding. With MAGI_FACT_GROUNDING_VERIFICATION_ENABLED=1 a grounded
    # numeric answer clears fact_grounding, leaving citation_support missing.
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.setenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", "1")
    _common_env(monkeypatch)
    records = (_source_record("The channel reported 776,665 subscribers in May."),)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text="It has 776665 subscribers."),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the channel and report the subscriber count")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    notices = _notice_events(items)
    assert len(notices) == 1
    assert "citation_support" in notices[0].payload["missingValidators"]


def test_hex_turn_id_and_raw_refs_still_soft_notice_not_swallowed_refuse(monkeypatch) -> None:
    # CRITICAL (design 3.3 / 3.6): a digit/hex turn_id (fails _SAFE_GATE_ID_RE)
    # plus a raw-URL answer must still yield the SOFT outcome, proving the live
    # path normalizes the turn_id and builds src_N cited refs BEFORE constructing
    # the request -- NOT a swallowed ValueError degrading to the hard refuse.
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    _common_env(monkeypatch)
    hex_turn = "9f3a1b2c4d5e"
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == hex_turn else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources", turn_id=hex_turn)
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error != "pre_final_evidence_gate_blocked"
    notices = _notice_events(items)
    assert len(notices) == 1
    assert "output_link_not_in_source_ledger" in notices[0].payload["reasonCodes"]


def test_non_research_recipe_not_scoped(monkeypatch) -> None:
    # A non-research recipe with the flag ON yields no research_governance_notice.
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    _common_env(monkeypatch)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_non_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="prepare the quarterly report document")
    terminal = items[-1]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert _notice_events(items) == []


def test_resolver_failure_fails_open(monkeypatch) -> None:
    # If the research final-gate evaluator raises, the soft branch fails open to
    # the existing behavior (hard refuse), never a crash and never a notice.
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    _common_env(monkeypatch)

    import magi_agent.research.live_research_final_gate as live_gate

    def _boom(**kwargs: object) -> object:
        raise RuntimeError("evaluator fault")

    monkeypatch.setattr(live_gate, "evaluate_live_research_final_gate", _boom)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources")
    terminal = items[-1]

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.error
    assert terminal.error == "pre_final_evidence_gate_blocked"
    assert _notice_events(items) == []
    # The answer still emitted (it was on the wire before the gate ran).
    assert _URL_ANSWER in "".join(_answer_deltas(items))


def test_status_payload_no_reserved_delta_keys(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    _common_env(monkeypatch)
    driver = MagiEngineDriver(
        runner=_TextRunner(final_text=_URL_ANSWER),
        runner_policy_assembly=_research_assembly(),
        evidence_collector=lambda turn_id: (_source_record(),) if turn_id == "t" else (),
    )

    items = _drive(driver, prompt="research the launch and cite sources")
    ws6_status = [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.type == "status"
        and item.payload.get("type") == "research_governance_notice"
    ]
    assert ws6_status
    for event in ws6_status:
        assert set(event.payload) & {"text", "content", "delta"} == set()
