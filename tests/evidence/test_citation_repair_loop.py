"""Driver-level end-to-end tests for the Wave 4b source-citation repair loop.

The unit suite (``tests/evidence/test_citation_gate.py``) exercises the pure gate
functions and the overlay/audit helpers in isolation. NOTHING there drives the
real ``MagiEngineDriver`` pre-final ``while True`` repair loop, so the P0
defect (a successful citation repair on a no-coding-gate turn dropping its
re-generated CITED tokens at the ``pre_final_gate is None`` break) had no
coverage. These tests drive the real loop with a scripted adapter that plays a
fresh generation per ``run_turn`` call (primary, then each repair round), a real
``SessionSourceRegistry`` stub, and the real deterministic citation gate.

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
# Harness doubles (mirror tests/cli/test_engine_research_soft_notice.py)
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
# P0: successful citation repair on an assembly=None turn keeps the cited answer
# ---------------------------------------------------------------------------


def test_successful_citation_repair_delivers_cited_answer(monkeypatch) -> None:
    """THE P0 regression test.

    A no-coding-gate turn (``runner_policy_assembly=None``) streams an uncited
    high-risk answer. The citation gate blocks and drives an attribution repair.
    The scripted model re-emits the SAME claim WITH a valid [src_1] marker (a
    live response_clear precedes the replacement, as the real transport does).
    The repaired CITED tokens MUST reach the consumer, the final answer MUST
    equal the cited version, and the CitationVerdict record MUST read ``cited``
    with ``repairAttempts == 1``. Fails before the buffer-flush fix (the cited
    tokens are dropped at the ``pre_final_gate is None`` break, leaving a blank
    answer), passes after.
    """
    _repair_env(monkeypatch, max_attempts="2")
    collector = _CaptureCollector(registry=_registry((_source_record(),)))
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
    driver = MagiEngineDriver(runner=runner, runner_policy_assembly=None)

    items = _drive(driver, prompt="When was Tesla founded")

    terminal = items[-1]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    assert terminal.error is None

    # The cited re-emitted tokens actually reached the consumer.
    assert any("[src_1]" in delta for delta in _token_deltas(items))
    # The final answer the consumer sees equals the cited version, not blank and
    # not the stale uncited answer.
    assert _client_answer(items) == _CITED

    # Exactly one verdict record, and it agrees with the delivered answer.
    records = _citation_records(collector)
    assert len(records) == 1
    fields = records[0].fields
    assert fields["verdict"] == "cited"
    assert fields["repairAttempts"] == 1
    assert fields["failOpen"] is False


def test_repair_generation_call_count(monkeypatch) -> None:
    """The successful path runs exactly one repair generation (primary + 1)."""
    _repair_env(monkeypatch, max_attempts="2")
    collector = _CaptureCollector(registry=_registry((_source_record(),)))
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
    driver = MagiEngineDriver(runner=runner, runner_policy_assembly=None)

    _drive(driver, prompt="When was Tesla founded")
    assert runner.call_count == 2


# ---------------------------------------------------------------------------
# Fail-open after budget exhaustion
# ---------------------------------------------------------------------------


def test_fail_open_after_budget_keeps_answer_and_appends_notice(monkeypatch) -> None:
    """The model never cites across the whole budget, so the gate fails open.

    The turn MUST complete, the deterministic hedge notice MUST be appended, the
    verdict record MUST carry ``failOpen: true``, and the answer MUST NOT be
    dropped or left blank (a live response_clear from the last repair round
    blanked the UI, so the last attempt is restored before the notice).
    """
    _repair_env(monkeypatch, max_attempts="1")
    collector = _CaptureCollector(registry=_registry((_source_record(),)))
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
    driver = MagiEngineDriver(runner=runner, runner_policy_assembly=None)

    items = _drive(driver, prompt="When was Tesla founded")

    terminal = items[-1]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed

    # Fail-open status surfaced and the deterministic hedge notice appended.
    fail_open_status = [
        item
        for item in items
        if isinstance(item, RuntimeEvent)
        and item.payload.get("type") == "source_citation_fail_open"
    ]
    assert len(fail_open_status) == 1
    assert any(
        "Contains unverified figures" in delta for delta in _token_deltas(items)
    )

    # The answer is not dropped or blank: the restored attempt precedes the notice.
    answer = _client_answer(items)
    assert _UNCITED in answer
    assert answer.strip()

    records = _citation_records(collector)
    assert len(records) == 1
    assert records[0].fields["failOpen"] is True


def test_shared_budget_bounds_repair_generations(monkeypatch) -> None:
    """The shared ``repair_attempts`` budget bounds the loop.

    With ``max_attempts = 2`` and a model that never cites, the loop drives at
    most two repair generations before failing open (primary + 2 repairs = 3
    generations total), so the shared repair budget composes as a single ceiling
    rather than compounding per repair family.
    """
    _repair_env(monkeypatch, max_attempts="2")
    collector = _CaptureCollector(registry=_registry((_source_record(),)))
    never_cites = [
        {"type": "response_clear"},
        {"type": "text_delta", "delta": _UNCITED},
    ]
    runner = _ScriptedRunner(
        generations=[
            [{"type": "text_delta", "delta": _UNCITED}],
            list(never_cites),
            list(never_cites),
            # A fourth generation would indicate the budget was exceeded.
            list(never_cites),
        ],
        collector=collector,
    )
    driver = MagiEngineDriver(runner=runner, runner_policy_assembly=None)

    items = _drive(driver, prompt="When was Tesla founded")

    terminal = items[-1]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal == Terminal.completed
    # 1 primary + 2 repair rounds; never a 4th generation.
    assert runner.call_count == 3
    assert collector.records
    assert _citation_records(collector)[0].fields["failOpen"] is True
