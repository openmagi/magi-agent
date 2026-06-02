"""PR-A3 contract-hardening tests.

Covers the purely-additive seams added in A3:
- new contract types (``TurnInput``, ``ContentBlock``, ``EmitFn``, ``WidgetDone``)
- ``EngineResult.session_id`` / ``.turn_id`` defaulted fields
- engine ``harness_state`` passthrough + terminal-envelope session/turn ids
- the ``gate=`` DI seam threaded through ``run_headless`` (no behavior change)
- the async ``aclose()`` cleanup fix (prompt single-flight release + ADK iterator
  close on early/mid-stream cancel)

Style: sync tests driving async via ``asyncio.run(...)`` (matches A1/A2).
"""

from __future__ import annotations

import asyncio
import inspect
import io
from collections.abc import Awaitable

import pytest

from magi_agent.cli.contracts import (
    ContentBlock,
    EmitFn,
    EngineResult,
    NullPermissionGate,
    RuntimeEvent,
    Terminal,
    TurnInput,
    WidgetDone,
)
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import StubEngineDriver, drain, run_headless

# Heavy ADK imports allowed in the TEST module (not in engine.py).
from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402


# ---------------------------------------------------------------------------
# Shared mock-runner helpers (mirrors test_engine.py)
# ---------------------------------------------------------------------------
def _text_event(text: str, *, partial: bool = True) -> Event:
    return Event(
        author="model",
        partial=partial,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def _call_event(name: str, args: dict, call_id: str) -> Event:
    return Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name=name, args=args, id=call_id)
                )
            ],
        ),
    )


class MockRunner:
    def __init__(self, events: list[Event]) -> None:
        self._events = events

    async def run_async(self, **_kwargs: object):
        for event in self._events:
            yield event


def _capture_runner_input(monkeypatch: pytest.MonkeyPatch) -> list:
    """Monkeypatch ``_lazy_engine_deps`` to wrap ``RunnerTurnInput`` so we can
    capture the instance the driver constructs (and assert ``harness_state``).

    Returns a list that will hold the single captured RunnerTurnInput.
    """
    import magi_agent.cli.engine as engine_mod

    real_deps = engine_mod._lazy_engine_deps()
    real_cls = real_deps["RunnerTurnInput"]
    captured: list = []

    def _wrapped(*args: object, **kwargs: object):
        instance = real_cls(*args, **kwargs)
        captured.append(instance)
        return instance

    def _fake_deps() -> dict:
        deps = dict(real_deps)
        deps["RunnerTurnInput"] = _wrapped
        return deps

    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", _fake_deps)
    return captured


class GatedRunner:
    def __init__(self, before: list[Event], gate: asyncio.Event, after: list[Event]):
        self._before = before
        self._gate = gate
        self._after = after

    async def run_async(self, **_kwargs: object):
        for event in self._before:
            yield event
        await self._gate.wait()
        for event in self._after:
            yield event


# ---------------------------------------------------------------------------
# 1. New contract types: import + shape
# ---------------------------------------------------------------------------
def test_turn_input_defaults() -> None:
    ti = TurnInput()
    assert ti.prompt == ""
    assert ti.session_id == "cli-session"
    assert ti.turn_id == "cli-turn"
    assert ti.initial_messages == []
    assert ti.harness_state is None
    # default_factory: separate instances do not share the list.
    other = TurnInput()
    ti.initial_messages.append("x")
    assert other.initial_messages == []


def test_turn_input_overrides() -> None:
    sentinel = object()
    ti = TurnInput(
        prompt="hi",
        session_id="s",
        turn_id="t",
        initial_messages=[{"role": "user"}],
        harness_state=sentinel,
    )
    assert ti.prompt == "hi"
    assert ti.session_id == "s"
    assert ti.turn_id == "t"
    assert ti.initial_messages == [{"role": "user"}]
    assert ti.harness_state is sentinel


def test_content_block_defaults() -> None:
    cb = ContentBlock()
    assert cb.type == "text"
    assert cb.text == ""
    cb2 = ContentBlock(type="image", text="caption")
    assert cb2.type == "image"
    assert cb2.text == "caption"


def test_emit_fn_is_usable_alias() -> None:
    # EmitFn is Callable[[RuntimeEvent], Awaitable[None]]; a conforming async
    # function should be assignable to it and callable.
    received: list[RuntimeEvent] = []

    async def emit(event: RuntimeEvent) -> None:
        received.append(event)

    fn: EmitFn = emit
    ev = RuntimeEvent(type="status", payload={"k": "v"}, turn_id="t")
    coro = fn(ev)
    assert isinstance(coro, Awaitable)
    asyncio.run(coro)
    assert received == [ev]


def test_widget_done_protocol_accepts_five_kwargs() -> None:
    calls: list[dict] = []

    def on_done(
        result: object,
        *,
        display: object = None,
        should_query: bool = False,
        meta_messages: list | None = None,
        next_input: str | None = None,
        submit_next_input: bool = False,
    ) -> None:
        calls.append(
            {
                "result": result,
                "display": display,
                "should_query": should_query,
                "meta_messages": meta_messages,
                "next_input": next_input,
                "submit_next_input": submit_next_input,
            }
        )

    # WidgetDone is runtime_checkable.
    assert isinstance(on_done, WidgetDone)
    typed: WidgetDone = on_done
    typed(
        "res",
        display="d",
        should_query=True,
        meta_messages=[{"m": 1}],
        next_input="next",
        submit_next_input=True,
    )
    assert calls == [
        {
            "result": "res",
            "display": "d",
            "should_query": True,
            "meta_messages": [{"m": 1}],
            "next_input": "next",
            "submit_next_input": True,
        }
    ]


# ---------------------------------------------------------------------------
# 2. EngineResult additive fields
# ---------------------------------------------------------------------------
def test_engine_result_session_turn_fields() -> None:
    r = EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")
    assert r.session_id == "s"
    assert r.turn_id == "t"


def test_engine_result_session_turn_default_none() -> None:
    r = EngineResult(terminal=Terminal.completed)
    assert r.session_id is None
    assert r.turn_id is None


def test_engine_result_legacy_construction_still_works() -> None:
    # Existing keyword construction (terminal/usage/cost_usd/error) unchanged.
    r = EngineResult(
        terminal=Terminal.error,
        usage={"input_tokens": 1},
        cost_usd=0.5,
        error="boom",
    )
    assert r.terminal is Terminal.error
    assert r.usage == {"input_tokens": 1}
    assert r.cost_usd == 0.5
    assert r.error == "boom"
    assert r.session_id is None
    assert r.turn_id is None


# ---------------------------------------------------------------------------
# 3. engine: harness_state passthrough
# ---------------------------------------------------------------------------
def test_turn_input_threads_harness_state_into_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_runner_input(monkeypatch)
    sentinel = {"resumed": True}
    runner = MockRunner([_text_event("ok")])
    driver = MagiEngineDriver(runner=runner)
    ti = TurnInput(
        prompt="go", session_id="s-hs", turn_id="t-hs", harness_state=sentinel
    )

    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, ti, cancel=asyncio.Event()))
    )
    assert terminal.terminal is Terminal.completed
    assert len(captured) == 1
    # The driver threaded TurnInput.harness_state into RunnerTurnInput
    # (constructed via the `harnessState` alias; read back via the field name).
    assert captured[0].harness_state is sentinel


def test_plain_dict_yields_harness_state_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_runner_input(monkeypatch)
    runner = MockRunner([_text_event("ok")])
    driver = MagiEngineDriver(runner=runner)

    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                {"prompt": "go", "session_id": "s-d", "turn_id": "t-d"},
                cancel=asyncio.Event(),
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    assert len(captured) == 1
    # A plain dict without harness_state => None, identical to pre-A3 behavior.
    assert captured[0].harness_state is None


# ---------------------------------------------------------------------------
# 4. engine: terminal EngineResult carries session_id/turn_id
# ---------------------------------------------------------------------------
def test_completed_terminal_carries_session_and_turn() -> None:
    runner = MockRunner([_text_event("ok")])
    driver = MagiEngineDriver(runner=runner)
    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                {"prompt": "go", "session_id": "s-c", "turn_id": "t-c"},
                cancel=asyncio.Event(),
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    assert terminal.session_id == "s-c"
    assert terminal.turn_id == "t-c"


def test_aborted_terminal_carries_session_and_turn() -> None:
    gate = asyncio.Event()  # never set => runner blocks
    runner = GatedRunner(
        before=[_call_event("Bash", {"cmd": "sleep"}, "o-1")],
        gate=gate,
        after=[],
    )
    driver = MagiEngineDriver(runner=runner)

    async def scenario() -> EngineResult:
        cancel = asyncio.Event()
        gen = driver.run_turn_stream(
            None,
            {"prompt": "go", "session_id": "s-ab", "turn_id": "t-ab"},
            cancel=cancel,
        )
        it = gen.__aiter__()
        await it.__anext__()  # first event => turn active
        cancel.set()
        terminal: EngineResult | None = None
        async for item in it:
            if isinstance(item, EngineResult):
                terminal = item
                break
        await gen.aclose()
        assert terminal is not None
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.terminal is Terminal.aborted
    assert terminal.session_id == "s-ab"
    assert terminal.turn_id == "t-ab"


class _RaisingRunner:
    def __init__(self, before: list[Event], exc: Exception) -> None:
        self._before = before
        self._exc = exc

    async def run_async(self, **_kwargs: object):
        for event in self._before:
            yield event
        raise self._exc


def test_error_terminal_carries_session_and_turn() -> None:
    runner = _RaisingRunner(
        before=[_call_event("Bash", {"cmd": "boom"}, "e-1")],
        exc=RuntimeError("kaboom"),
    )
    driver = MagiEngineDriver(runner=runner)
    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                {"prompt": "go", "session_id": "s-e", "turn_id": "t-e"},
                cancel=asyncio.Event(),
            )
        )
    )
    assert terminal.terminal is Terminal.error
    assert terminal.session_id == "s-e"
    assert terminal.turn_id == "t-e"


def test_single_flight_reject_terminal_carries_session_and_turn() -> None:
    gate = asyncio.Event()
    runner = GatedRunner(before=[_text_event("first")], gate=gate, after=[])
    driver = MagiEngineDriver(runner=runner)

    async def scenario() -> EngineResult:
        gen1 = driver.run_turn_stream(
            None,
            {"prompt": "go", "session_id": "dup", "turn_id": "t1"},
            cancel=asyncio.Event(),
        )
        it1 = gen1.__aiter__()
        await it1.__anext__()  # turn active
        gen2 = driver.run_turn_stream(
            None,
            {"prompt": "go", "session_id": "dup", "turn_id": "t2"},
            cancel=asyncio.Event(),
        )
        _ev2, terminal2 = await drain(gen2)
        gate.set()
        async for item in it1:
            if isinstance(item, EngineResult):
                break
        await gen1.aclose()
        return terminal2

    terminal2 = asyncio.run(scenario())
    assert terminal2.terminal is Terminal.aborted
    assert terminal2.error == "active_session_turn"
    assert terminal2.session_id == "dup"
    assert terminal2.turn_id == "t2"


# ---------------------------------------------------------------------------
# 5. gate= seam accepted everywhere + threaded through run_headless
# ---------------------------------------------------------------------------
def test_gate_kwarg_accepted_by_magi_and_stub_drivers() -> None:
    # Both drivers' run_turn_stream accept a `gate` keyword.
    for cls in (MagiEngineDriver, StubEngineDriver):
        sig = inspect.signature(cls.run_turn_stream)
        assert "gate" in sig.parameters
        assert sig.parameters["gate"].kind is inspect.Parameter.KEYWORD_ONLY

    # Functionally: passing gate= does not change a real driver's output.
    runner = MockRunner([_text_event("ok")])
    driver = MagiEngineDriver(runner=runner)
    _ev, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                {"prompt": "go", "session_id": "s-g", "turn_id": "t-g"},
                cancel=asyncio.Event(),
                gate=NullPermissionGate(allow_in_test=True),
            )
        )
    )
    assert terminal.terminal is Terminal.completed


def test_run_headless_threads_gate_without_changing_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")

    def _run(gate: object | None) -> str:
        buffer = io.StringIO()
        code = asyncio.run(
            run_headless(
                "hi",
                output="text",
                driver=StubEngineDriver(text="answer"),
                gate=gate,  # type: ignore[arg-type]
                stream=buffer,
            )
        )
        assert code == 0
        return buffer.getvalue()

    # With an explicit gate and with the implicit default, output is identical.
    with_gate = _run(NullPermissionGate())
    default = _run(None)
    assert with_gate == default == "answer\n"


# ---------------------------------------------------------------------------
# 6. async aclose() fix: prompt single-flight release + ADK iterator close
# ---------------------------------------------------------------------------
def test_early_aclose_releases_single_flight_and_closes_iterator() -> None:
    gate = asyncio.Event()  # never set => runner blocks mid-stream
    runner = GatedRunner(
        before=[_text_event("first")],
        gate=gate,
        after=[_text_event("second")],
    )
    driver = MagiEngineDriver(runner=runner)

    async def scenario() -> EngineResult:
        gen = driver.run_turn_stream(
            None,
            {"prompt": "go", "session_id": "s-aclose", "turn_id": "t1"},
            cancel=asyncio.Event(),
        )
        it = gen.__aiter__()
        await it.__anext__()  # consume "first" => turn active, blocked on gate
        # Early aclose mid-stream — must propagate into _drive's finally and
        # release the single-flight slot promptly (not deferred to GC).
        await gen.aclose()

        # Registry slot must be free right after aclose(): a subsequent same-
        # session turn is admitted (it would be rejected if the slot leaked).
        runner2 = MockRunner([_text_event("again")])
        driver._runner = runner2  # reuse same driver+registry, new runner
        _ev, terminal2 = await drain(
            driver.run_turn_stream(
                None,
                {"prompt": "go", "session_id": "s-aclose", "turn_id": "t2"},
                cancel=asyncio.Event(),
            )
        )
        return terminal2

    terminal2 = asyncio.run(scenario())
    # Re-admitted (NOT active_session_turn) => the slot was released by aclose.
    assert terminal2.terminal is Terminal.completed
    assert terminal2.error is None
