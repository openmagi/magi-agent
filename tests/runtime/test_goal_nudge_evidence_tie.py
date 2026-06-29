"""WS6 PR6c — goal_nudge completion tie (TDD).

Pins the CORRECTED PR6c scope (see the WS6 design doc, section "PR6c"):

- The ``goal_nudge.goal_is_met`` evidence gate is PRE-EXISTING and unchanged.
  PR6c does NOT add a flag around it and does NOT re-gate it.
- The goal_nudge CONTINUE path is TERMINAL-FREE: when ``goal_is_met`` returns
  ``False`` the engine emits a ``goal_nudge`` status event and ``continue``s the
  primary turn loop to RE-INVOKE the model. PR6c must NOT insert a terminal and
  must NOT append a trailing ``text_delta`` suffix on this path (the model is
  about to be re-driven).
- PR6c's ONLY change: when the WS6 hedge flag
  ``MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED`` is ON, ENRICH the existing
  ``goal_nudge`` status payload with transport-safe evidence-reason fields
  (``missingValidators`` / ``requirementLabels`` / ``reasonCodes``) so the
  client sees WHY the turn continued. The payload must still carry NO reserved
  ``text``/``content``/``delta`` key (MINOR-1 transport collision guard).

PR6c has NO runtime effect until WS3 enables goal_nudge + sets
``required_evidence``; these tests drive the engine with an explicit
``GoalNudge`` to exercise the (otherwise dormant) enrichment.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.goal_nudge import (
    GoalNudge,
    goal_is_met,
    goal_nudge_evidence_reasons,
)


# ---------------------------------------------------------------------------
# Hermetic env: clear shell MAGI_* leakage + isolate cwd/HOME so engine runs
# never pollute the tracked ``memory/`` tree.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    for key in list(os.environ):
        if key.startswith("MAGI_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))


# ---------------------------------------------------------------------------
# Minimal fake ADK harness (mirrors tests/cli/test_engine_goal_nudge.py).
# ---------------------------------------------------------------------------


@dataclass
class FakeRunnerCall:
    invocation_id: str
    new_message_text: str


class _FakeADKEvent:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict


class _FakeADKStream:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self._index = 0

    def __aiter__(self) -> "_FakeADKStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return _FakeADKEvent(event)

    async def aclose(self) -> None:
        pass


class FakeRunner:
    def __init__(self, *, events_per_call: list[list[dict[str, Any]]] | None = None) -> None:
        self.calls: list[FakeRunnerCall] = []
        self._events_per_call: list[list[dict[str, Any]]] = events_per_call or []
        self._call_index = 0
        self.agent = None

    def _events_for_this_call(self) -> list[dict[str, Any]]:
        if self._call_index < len(self._events_per_call):
            events = self._events_per_call[self._call_index]
        else:
            events = []
        self._call_index += 1
        return events

    def run_async(
        self,
        *,
        user_id: str,
        session_id: str,
        invocation_id: str,
        new_message: Any,
    ) -> AsyncIterator[Any]:
        parts = getattr(new_message, "parts", None) or []
        text = ""
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                text = t
                break
        self.calls.append(FakeRunnerCall(invocation_id=invocation_id, new_message_text=text))
        return _FakeADKStream(self._events_for_this_call())


class _FakeBridgeResult:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict

    @property
    def agent_events(self) -> list[dict[str, Any]]:
        if not self._event_dict:
            return []
        return [self._event_dict]


class FakeEventBridge:
    def project_adk_event(self, adk_event: object, *, turn_id: str) -> Any:
        return _FakeBridgeResult(getattr(adk_event, "_event_dict", {}))


def _fake_sanitize(d: dict[str, Any]) -> dict[str, Any] | None:
    return d if d else None


class FakeRunnerTurnInputCls:
    def __new__(cls, **kwargs: Any) -> "FakeRunnerTurnInputCls":  # type: ignore[misc]
        obj = object.__new__(cls)
        for k, v in kwargs.items():
            setattr(obj, k, v)
        return obj


class FakeRunnerAdapter:
    def __init__(self, *, runner: FakeRunner) -> None:
        self.runner = runner

    def run_turn(self, runner_input: Any) -> AsyncIterator[Any]:
        return self.runner.run_async(
            user_id=getattr(runner_input, "userId", "cli"),
            session_id=getattr(runner_input, "sessionId", "s"),
            invocation_id=getattr(runner_input, "invocationId", "t"),
            new_message=getattr(runner_input, "newMessage", None),
        )


class _FakeContent:
    def __init__(self, *, role: str, parts: list) -> None:
        self.role = role
        self.parts = parts


class _FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class _FakeTypes:
    Content = _FakeContent
    Part = _FakePart


def _patch_lazy_deps(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    import magi_agent.cli.engine as engine_mod

    fake_deps = {
        "types": _FakeTypes(),
        "OpenMagiEventBridge": lambda **kwargs: FakeEventBridge(),
        "OpenMagiRunnerAdapter": lambda **kwargs: FakeRunnerAdapter(runner=runner),
        "RunnerTurnInput": FakeRunnerTurnInputCls,
        "sanitize_agent_event": _fake_sanitize,
    }
    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", lambda: fake_deps)


def _make_driver(
    runner: FakeRunner,
    *,
    goal_nudge: GoalNudge,
    evidence_collector: Any | None = None,
) -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=runner,
        max_event_count=4096,
        user_id="cli",
        goal_nudge=goal_nudge,
        evidence_collector=evidence_collector,
    )


def _run_drive(driver: MagiEngineDriver, *, prompt: str = "do the thing") -> list[Any]:
    async def _collect() -> list[Any]:
        cancel = asyncio.Event()
        items: list[Any] = []
        async for item in driver.run_turn_stream(
            runtime=None,
            turn_input={"prompt": prompt, "session_id": "test-session", "turn_id": "test-turn"},
            cancel=cancel,
        ):
            items.append(item)
        return items

    return asyncio.run(_collect())


def _goal_nudge_status_events(items: list[Any]) -> list[RuntimeEvent]:
    return [
        i
        for i in items
        if isinstance(i, RuntimeEvent)
        and i.type == "status"
        and isinstance(i.payload, dict)
        and i.payload.get("type") == "goal_nudge"
    ]


_SOURCE_RECORD = {
    "type": "SourceInspection",
    "sourceRef": "web:example.com",
    "evidenceRef": "ev:0001:evidence_record",
}


# ---------------------------------------------------------------------------
# 1. The existing evidence gate is unchanged (pure, no engine, no WS6 flag).
# ---------------------------------------------------------------------------


def test_existing_goal_nudge_evidence_gate_unchanged() -> None:
    """required_evidence declared but no opened sources -> goal_is_met False.

    Holds WITHOUT any WS6 flag (the hermetic fixture cleared every MAGI_*);
    PR6c does not regress / re-gate the pre-existing gate.
    """
    nudge = GoalNudge(goal="research done", required_evidence=("source_ledger",))
    assert goal_is_met(nudge, evidence_records=[]) is False


def test_completion_allowed_when_grounded() -> None:
    """A SourceInspection record satisfies source_ledger -> goal_is_met True."""
    nudge = GoalNudge(goal="research done", required_evidence=("source_ledger",))
    assert goal_is_met(nudge, evidence_records=[_SOURCE_RECORD]) is True


def test_goal_nudge_evidence_reasons_derives_missing_validators() -> None:
    """The PR6c helper projects the gate decision into transport-safe fields."""
    nudge = GoalNudge(goal="research done", required_evidence=("source_ledger",))
    reasons = goal_nudge_evidence_reasons(nudge, evidence_records=[])
    assert reasons.requirement_labels == ("source_ledger",)
    assert reasons.missing_validators == ("source_ledger",)
    assert "missing_required_evidence:source_ledger" in reasons.reason_codes
    # Grounded -> no missing validators.
    grounded = goal_nudge_evidence_reasons(nudge, evidence_records=[_SOURCE_RECORD])
    assert grounded.missing_validators == ()


# ---------------------------------------------------------------------------
# 2. Control-flow pin: the continue path RE-INVOKES, never terminates.
# ---------------------------------------------------------------------------


def test_goal_nudge_continue_reinvokes_not_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """goal_nudge enabled + required_evidence unmet -> re-invoke, NO terminal.

    Asserts a SECOND run_async invocation occurs (the model is re-driven) and
    that no Terminal.error/Terminal.completed was yielded at the nudge point
    (only the normal end-of-turn terminal appears last, and it is completed).
    """
    runner = FakeRunner(events_per_call=[[], []])
    _patch_lazy_deps(monkeypatch, runner)

    def empty_collector(turn_id: str) -> list[object]:
        return []

    nudge = GoalNudge(
        goal="research done",
        mode="goal",
        max_nudges=3,
        required_evidence=("source_ledger",),
    )
    driver = _make_driver(runner, goal_nudge=nudge, evidence_collector=empty_collector)
    items = _run_drive(driver)

    # The model was RE-INVOKED (initial + one nudge).
    assert len(runner.calls) == 2

    # A goal_nudge status event was emitted at the continue point.
    nudge_events = _goal_nudge_status_events(items)
    assert len(nudge_events) == 1

    # No terminal yielded at the nudge point: the ONLY terminal-bearing item is
    # the last one, and it is Terminal.completed (NOT error).
    terminal_items = [i for i in items if getattr(i, "terminal", None) is not None]
    assert len(terminal_items) == 1
    assert terminal_items[-1] is items[-1]
    assert items[-1].terminal == Terminal.completed


def test_uncertain_does_not_false_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambiguous goal + insufficient evidence does not auto-close (re-invokes)."""
    max_n = 2
    runner = FakeRunner(events_per_call=[[] for _ in range(max_n + 2)])
    _patch_lazy_deps(monkeypatch, runner)

    def empty_collector(turn_id: str) -> list[object]:
        return []

    nudge = GoalNudge(
        goal="is the answer X or Y?",
        mode="grind",
        max_nudges=max_n,
        required_evidence=("source_ledger",),
    )
    driver = _make_driver(runner, goal_nudge=nudge, evidence_collector=empty_collector)
    items = _run_drive(driver)

    # Insufficient evidence never auto-completes on the first stop: it re-invokes
    # to the nudge cap (initial + max_n nudges), never a single false-complete.
    assert len(runner.calls) == max_n + 1
    assert items[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# 3. Enrichment: WS6 hedge flag ON -> status payload carries evidence reasons,
#    WITHOUT any reserved transport key and WITHOUT a trailing text suffix.
# ---------------------------------------------------------------------------


def test_goal_nudge_status_enriched_with_evidence_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED", "1")
    runner = FakeRunner(events_per_call=[[], []])
    _patch_lazy_deps(monkeypatch, runner)

    def empty_collector(turn_id: str) -> list[object]:
        return []

    nudge = GoalNudge(
        goal="research done",
        mode="goal",
        max_nudges=3,
        required_evidence=("source_ledger",),
    )
    driver = _make_driver(runner, goal_nudge=nudge, evidence_collector=empty_collector)
    items = _run_drive(driver)

    nudge_events = _goal_nudge_status_events(items)
    assert len(nudge_events) == 1
    payload = nudge_events[0].payload

    # Enriched with the WS6 evidence-reason fields.
    assert payload["missingValidators"] == ["source_ledger"]
    assert payload["requirementLabels"] == ["source_ledger"]
    assert "missing_required_evidence:source_ledger" in payload["reasonCodes"]

    # MINOR-1 transport collision guard: no reserved delta key on the payload.
    assert set(payload) & {"text", "content", "delta"} == set()

    # Terminal-free: the continue path re-invokes (no terminal at nudge point).
    assert len(runner.calls) == 2
    assert items[-1].terminal == Terminal.completed

    # No trailing text_delta SUFFIX was appended on this path (the model is
    # about to be re-driven; a suffix would be premature).
    text_delta_events = [
        i
        for i in items
        if isinstance(i, RuntimeEvent) and i.type == "text_delta"
    ]
    assert text_delta_events == []


def test_goal_nudge_status_not_enriched_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hedge flag OFF -> the goal_nudge payload keeps ONLY its base keys."""
    runner = FakeRunner(events_per_call=[[], []])
    _patch_lazy_deps(monkeypatch, runner)

    def empty_collector(turn_id: str) -> list[object]:
        return []

    nudge = GoalNudge(
        goal="research done",
        mode="goal",
        max_nudges=3,
        required_evidence=("source_ledger",),
    )
    driver = _make_driver(runner, goal_nudge=nudge, evidence_collector=empty_collector)
    items = _run_drive(driver)

    nudge_events = _goal_nudge_status_events(items)
    assert len(nudge_events) == 1
    payload = nudge_events[0].payload
    assert set(payload) == {"type", "mode", "nudge", "max"}
