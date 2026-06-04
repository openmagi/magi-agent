"""LIVE error-recovery tests for the genuine run-invocation retry seam (PR12).

The recovery half of PR12 was originally wired at ADK's
``on_model_error_callback`` — a *substitute-the-response* seam, not a *retry*
seam: returning a content-less ``LlmResponse`` there ENDS the turn (ADK treats
it as the final step) and no second model call happens. So a 429 there would
sleep the Retry-After delay and then silently terminate the turn with an empty
response — no actual retry.

These tests drive the ACTUAL run-invocation seam — ``MagiEngineDriver`` consuming
``adapter.run_turn(runner_input)`` (a real ``OpenMagiRunnerAdapter`` calling the
fake runner's ``run_async``) — and prove the genuine retry:

* (a) the run is actually RE-INVOKED: a fake runner that raises 429 ONCE then
  succeeds is called a SECOND time (the model is genuinely re-invoked);
* (b) the Retry-After delay was honored (the parsed 1.5s, not blind backoff);
* (c) a terminal error is NOT retried (single invocation, terminal error);
* (d) budget exhaustion stops the retry (bounded re-invocations);
* (e) flag OFF (``recovery=None``) attaches no retry wrapper (single invocation,
  error surfaced).

Each test would FAIL if the retry wrapper were removed (the 2nd invocation /
honored delay / bounded count assertions vanish). This package drives async via
``asyncio.run(...)`` (no ``pytest-asyncio``), matching the sibling engine tests.
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import EngineRecoveryPolicy, MagiEngineDriver
from magi_agent.cli.headless import drain
from magi_agent.runtime.error_recovery import ErrorRecoveryConfig, RecoveryEngine

# Heavy ADK imports allowed in the TEST module (not in engine.py).
from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402


def _text_event(text: str, *, partial: bool = True) -> Event:
    return Event(
        author="model",
        partial=partial,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def _text_stream(text: str) -> list[Event]:
    """A streaming text turn: a partial delta (which the bridge projects as a
    ``text_delta``) followed by the final non-partial event."""
    return [_text_event(text, partial=True), _text_event(text, partial=False)]


def _turn_input(session_id: str, turn_id: str = "turn-1", prompt: str = "go") -> dict:
    return {"prompt": prompt, "session_id": session_id, "turn_id": turn_id}


class _Error429(Exception):
    """A realistic 429 carrying a retry-after-ms hint via .response.headers,
    like provider SDK errors (parsed by ErrorClassifier into 1.5s)."""

    def __init__(self) -> None:
        super().__init__("429 too many requests rate_limit")
        self.response = type("Resp", (), {"headers": {"retry-after-ms": "1500"}})()


class _RetryOnceRunner:
    """``run_async`` raises a 429 on the FIRST invocation, succeeds on the 2nd.

    Records every invocation so the test can assert the run was genuinely
    re-invoked (a real second model call). Matches the ``run_async`` signature
    that ``OpenMagiRunnerAdapter`` calls.
    """

    def __init__(self, success_events: list[Event]) -> None:
        self.invocations = 0
        self._success_events = success_events

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        if self.invocations == 1:
            raise _Error429()
        for event in self._success_events:
            yield event
        if False:  # pragma: no cover - generator type hint
            yield None


class _AlwaysFailRunner:
    """``run_async`` raises the given error on EVERY invocation."""

    def __init__(self, error: Exception) -> None:
        self.invocations = 0
        self._error = error

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        raise self._error
        if False:  # pragma: no cover - generator type hint
            yield None


def _recovery_policy(max_attempts: int = 3) -> EngineRecoveryPolicy:
    config = ErrorRecoveryConfig(
        recovery_enabled=True,
        max_recovery_attempts=max_attempts,
        rate_limit_base_delay_seconds=99,  # prove Retry-After wins over backoff
    )
    return EngineRecoveryPolicy(engine=RecoveryEngine(config), max_attempts=max_attempts)


def _patch_sleep(slept: list[float]):
    """Patch the rate-limit strategy's asyncio.sleep so the test never blocks
    and records the honored delay. Returns a (restore) callable."""
    import magi_agent.runtime.error_recovery.strategies.rate_limit as rl

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    original = rl.asyncio.sleep
    rl.asyncio.sleep = fake_sleep  # type: ignore[assignment]

    def restore() -> None:
        rl.asyncio.sleep = original  # type: ignore[assignment]

    return restore


def test_live_recovery_reinvokes_run_after_429_and_honors_retry_after() -> None:
    # (a) genuine 2nd invocation + (b) Retry-After honored.
    runner = _RetryOnceRunner(_text_stream("recovered output"))
    driver = MagiEngineDriver(runner=runner, recovery=_recovery_policy(max_attempts=3))
    cancel = asyncio.Event()
    slept: list[float] = []
    restore = _patch_sleep(slept)
    try:
        events, terminal = asyncio.run(
            drain(driver.run_turn_stream(None, _turn_input("s-live-retry"), cancel=cancel))
        )
    finally:
        restore()

    # The model was genuinely RE-INVOKED: run_async called twice.
    assert runner.invocations == 2
    # Retry-After-ms=1500 honored (1.5s), NOT the 99s base backoff.
    assert slept == [1.5]
    # The 2nd (successful) invocation's output reached the consumer and the turn
    # completed normally.
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.completed
    assert any("recovered output" in str(e.payload) for e in events)


def test_live_recovery_terminal_error_not_retried() -> None:
    # (c) a terminal (non-classified-retryable) error is NOT retried.
    runner = _AlwaysFailRunner(ValueError("some unrecognized internal failure"))
    driver = MagiEngineDriver(runner=runner, recovery=_recovery_policy(max_attempts=3))
    cancel = asyncio.Event()
    slept: list[float] = []
    restore = _patch_sleep(slept)
    try:
        _events, terminal = asyncio.run(
            drain(driver.run_turn_stream(None, _turn_input("s-live-terminal"), cancel=cancel))
        )
    finally:
        restore()

    # Single invocation: terminal error is not retried.
    assert runner.invocations == 1
    assert slept == []
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.error


def test_live_recovery_budget_exhausted_stops() -> None:
    # (d) budget exhaustion bounds the re-invocations: a runner that ALWAYS 429s
    # is invoked 1 (initial) + max_attempts (retries) times, then stops.
    runner = _AlwaysFailRunner(_Error429())
    driver = MagiEngineDriver(runner=runner, recovery=_recovery_policy(max_attempts=2))
    cancel = asyncio.Event()
    slept: list[float] = []
    restore = _patch_sleep(slept)
    try:
        _events, terminal = asyncio.run(
            drain(driver.run_turn_stream(None, _turn_input("s-live-budget"), cancel=cancel))
        )
    finally:
        restore()

    # initial + 2 retries = 3 invocations; bounded (not infinite).
    assert runner.invocations == 3
    assert slept == [1.5, 1.5]
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.error


def test_live_recovery_flag_off_no_retry_wrapper() -> None:
    # (e) recovery=None -> no retry wrapper -> a 429 is surfaced after a SINGLE
    # invocation (byte-for-byte identical to pre-PR12 streaming).
    runner = _AlwaysFailRunner(_Error429())
    driver = MagiEngineDriver(runner=runner, recovery=None)
    cancel = asyncio.Event()
    slept: list[float] = []
    restore = _patch_sleep(slept)
    try:
        _events, terminal = asyncio.run(
            drain(driver.run_turn_stream(None, _turn_input("s-live-off"), cancel=cancel))
        )
    finally:
        restore()

    assert runner.invocations == 1
    assert slept == []
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.error


def test_live_recovery_does_not_replay_after_partial_output() -> None:
    # Safety: if the run yields an event THEN raises a 429, we must NOT replay
    # (would double-emit). The error is surfaced; no retry; single invocation.
    class _EmitThenFailRunner:
        def __init__(self) -> None:
            self.invocations = 0

        async def run_async(self, **_kwargs: object):
            self.invocations += 1
            yield _text_event("partial output")
            raise _Error429()

    runner = _EmitThenFailRunner()
    driver = MagiEngineDriver(runner=runner, recovery=_recovery_policy(max_attempts=3))
    cancel = asyncio.Event()
    slept: list[float] = []
    restore = _patch_sleep(slept)
    try:
        events, terminal = asyncio.run(
            drain(
                driver.run_turn_stream(
                    None, _turn_input("s-live-partial"), cancel=cancel
                )
            )
        )
    finally:
        restore()

    # Only the initial invocation; no replay after output was streamed.
    assert runner.invocations == 1
    assert slept == []
    assert any("partial output" in str(e.payload) for e in events)
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.error
