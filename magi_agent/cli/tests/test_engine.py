"""Tests for the real ADK-backed MagiEngineDriver (PR-A2).

Style note: this package has no ``pytest-asyncio`` configured, so every test is
a SYNC function that drives async code via ``asyncio.run(...)`` — matching the
A1 ``test_headless.py`` convention.

Fake events / runner construction
---------------------------------
We build a ``MockRunner`` whose ``run_async(**kwargs)`` async-generator yields
REAL ``google.adk.events.Event`` objects (text / function-call / function-
response). ``OpenMagiRunnerAdapter`` calls exactly ``runner.run_async(...)``, so
this exercises the real adapter + the real ``OpenMagiEventBridge`` +  the real
``_sanitize_agent_event`` — i.e. the full A2 path with NO model call. Importing
``google.adk`` inside the TEST module is fine; the IMPORT-CLEANLINESS invariant
only constrains ``cli/engine.py`` at module load (asserted in
``test_engine_module_is_import_clean``).
"""

from __future__ import annotations

import asyncio
import io
import json
import sys

import pytest

from magi_agent.cli.contracts import EngineResult, RuntimeEvent, Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import drain, run_headless

# Heavy ADK imports are allowed in the TEST module (not in engine.py).
from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402


# ---------------------------------------------------------------------------
# Fake-event + mock-runner helpers (real ADK objects)
# ---------------------------------------------------------------------------
def _text_event(
    text: str,
    *,
    partial: bool = True,
    turn_complete: bool = False,
) -> Event:
    return Event(
        author="model",
        partial=partial,
        turn_complete=turn_complete,
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


def _response_event(name: str, response: dict, call_id: str) -> Event:
    return Event(
        author="user",
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name, response=response, id=call_id
                    )
                )
            ],
        ),
    )


class MockRunner:
    """Yields a fixed list of ADK events. Matches the ``run_async`` signature
    the OpenMagiRunnerAdapter calls."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    async def run_async(self, **_kwargs: object):
        for event in self._events:
            yield event


class GatedRunner:
    """Yields ``before`` events, blocks on a gate, then yields ``after``."""

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


def _turn_input(session_id: str, turn_id: str = "turn-1", prompt: str = "go") -> dict:
    return {"prompt": prompt, "session_id": session_id, "turn_id": turn_id}


def _install_transcript_only_text_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate providers whose final aggregate text only lands in transcript entries."""

    import magi_agent.cli.engine as engine_mod

    real_deps = engine_mod._lazy_engine_deps()
    bridge_key = next(key for key in real_deps if key.endswith("EventBridge"))

    class _BridgeWithTranscriptOnlyText:
        def __init__(self, **kwargs: object) -> None:
            self._real = real_deps[bridge_key](**kwargs)  # type: ignore[operator]

        def project_adk_event(self, event: object, *, turn_id: str):
            projection = self._real.project_adk_event(event, turn_id=turn_id)
            if any(
                getattr(entry, "kind", None) == "assistant_text"
                for entry in projection.transcript_entries
            ):
                object.__setattr__(
                    projection,
                    "agent_events",
                    [
                        agent_event
                        for agent_event in projection.agent_events
                        if agent_event.get("type") != "text_delta"
                    ],
                )
            return projection

    def _fake_deps() -> dict:
        deps = dict(real_deps)
        deps[bridge_key] = _BridgeWithTranscriptOnlyText
        return deps

    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", _fake_deps)


# ---------------------------------------------------------------------------
# 1. Turn drains to a terminal result
# ---------------------------------------------------------------------------
def test_turn_drains_to_completed_terminal() -> None:
    runner = MockRunner(
        [
            _text_event("hello "),
            _call_event("Bash", {"cmd": "ls"}, "call-1"),
            _response_event("Bash", {"out": "file.txt"}, "call-1"),
            _text_event("done"),
        ]
    )
    driver = MagiEngineDriver(runner=runner)
    cancel = asyncio.Event()

    events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-complete"), cancel=cancel))
    )

    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.completed
    assert terminal.error is None
    # Every collected item is a RuntimeEvent (no EngineResult leaked into list).
    assert all(isinstance(ev, RuntimeEvent) for ev in events)
    assert not any(isinstance(ev, EngineResult) for ev in events)
    kinds = [(ev.type, ev.payload.get("type")) for ev in events]
    assert ("token", "text_delta") in kinds
    assert ("tool", "tool_start") in kinds
    assert ("tool", "tool_end") in kinds


def test_headless_text_uses_transcript_only_final_aggregate_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    _install_transcript_only_text_bridge(monkeypatch)

    driver = MagiEngineDriver(
        runner=MockRunner(
            [_text_event("aggregate-only final", partial=False, turn_complete=True)]
        )
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless("hi", output="text", driver=driver, stream=buffer)
    )

    assert code == 0
    assert buffer.getvalue() == "aggregate-only final\n"


def test_headless_json_transcript_aggregate_after_partial_does_not_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    _install_transcript_only_text_bridge(monkeypatch)

    driver = MagiEngineDriver(
        runner=MockRunner(
            [
                _text_event("Hello ", partial=True),
                _text_event("Hello world.", partial=False, turn_complete=True),
            ]
        )
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless("hi", output="json", driver=driver, stream=buffer)
    )

    result = json.loads(buffer.getvalue())
    assert code == 0
    assert result["result"] == "Hello world."


def test_stream_json_transcript_only_final_emits_assistant_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    _install_transcript_only_text_bridge(monkeypatch)

    driver = MagiEngineDriver(
        runner=MockRunner(
            [_text_event("stream aggregate", partial=False, turn_complete=True)]
        )
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless("hi", output="stream-json", driver=driver, stream=buffer)
    )

    frames = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assistant_frames = [frame for frame in frames if frame["type"] == "assistant"]
    result_frames = [frame for frame in frames if frame["type"] == "result"]
    assert code == 0
    assert [frame["message"]["content"] for frame in assistant_frames] == [
        "stream aggregate"
    ]
    assert result_frames[-1]["result"] == "stream aggregate"


# ---------------------------------------------------------------------------
# 2. Cancel mid-turn synthesizes orphan tool_results
# ---------------------------------------------------------------------------
def test_cancel_midturn_synthesizes_orphan_tool_result() -> None:
    gate = asyncio.Event()  # never set => runner blocks after the tool_start
    runner = GatedRunner(
        before=[_call_event("Bash", {"cmd": "sleep"}, "orphan-1")],
        gate=gate,
        after=[_call_event("Other", {}, "never")],
    )
    driver = MagiEngineDriver(runner=runner)
    cancel = asyncio.Event()

    async def scenario() -> tuple[list[RuntimeEvent], EngineResult]:
        gen = driver.run_turn_stream(None, _turn_input("s-cancel"), cancel=cancel)
        it = gen.__aiter__()
        collected: list[RuntimeEvent] = []
        # Pull the first event (the orphan tool_start), then cancel.
        first = await it.__anext__()
        collected.append(first)
        cancel.set()
        terminal: EngineResult | None = None
        async for item in it:
            if isinstance(item, EngineResult):
                terminal = item
                break
            collected.append(item)
        await gen.aclose()
        assert terminal is not None
        return collected, terminal

    events, terminal = asyncio.run(scenario())

    assert terminal.terminal is Terminal.aborted
    assert terminal.error == "cancelled"
    # The orphan tool_start...
    assert any(
        ev.type == "tool" and ev.payload.get("type") == "tool_start"
        and ev.payload.get("id") == "orphan-1"
        for ev in events
    )
    # ...gets a synthesized interrupted tool_end with the SAME id.
    synthetic = [
        ev
        for ev in events
        if ev.type == "tool"
        and ev.payload.get("type") == "tool_end"
        and ev.payload.get("id") == "orphan-1"
        and ev.payload.get("interrupted") is True
    ]
    assert len(synthetic) == 1


# ---------------------------------------------------------------------------
# 3. Single-flight rejects concurrent turns
# ---------------------------------------------------------------------------
def test_single_flight_rejects_concurrent_same_session() -> None:
    gate = asyncio.Event()
    runner = GatedRunner(
        before=[_text_event("first")],
        gate=gate,
        after=[_text_event("second")],
    )
    driver = MagiEngineDriver(runner=runner)

    async def scenario() -> tuple[EngineResult, RuntimeEvent, EngineResult]:
        # Turn 1 for session "dup": start and hold mid-stream.
        gen1 = driver.run_turn_stream(
            None, _turn_input("dup", turn_id="t1"), cancel=asyncio.Event()
        )
        it1 = gen1.__aiter__()
        await it1.__anext__()  # consume "first" => turn is active

        # Turn 2, SAME session, concurrently => must be rejected, no run.
        gen2 = driver.run_turn_stream(
            None, _turn_input("dup", turn_id="t2"), cancel=asyncio.Event()
        )
        events2, terminal2 = await drain(gen2)
        assert events2 == []

        # A turn for a DIFFERENT session proceeds normally (separate driver +
        # runner, so it shares no state with the held "dup" turn).
        sep_driver = MagiEngineDriver(runner=MockRunner([_text_event("ok")]))
        gen3 = sep_driver.run_turn_stream(
            None, _turn_input("other", turn_id="t3"), cancel=asyncio.Event()
        )
        events3, terminal3 = await drain(gen3)
        first3 = events3[0]

        # Let turn 1 finish to release cleanly.
        gate.set()
        async for item in it1:
            if isinstance(item, EngineResult):
                break
        await gen1.aclose()

        return terminal2, first3, terminal3

    terminal2, first3, terminal3 = asyncio.run(scenario())

    # Same-session second turn: aborted with active_session_turn, never ran.
    assert terminal2.terminal is Terminal.aborted
    assert terminal2.error == "active_session_turn"
    # Different session proceeded to completion.
    assert isinstance(first3, RuntimeEvent)
    assert terminal3.terminal is Terminal.completed


def test_single_flight_same_driver_allows_distinct_session() -> None:
    """The driver's registry keys on session id, so distinct sessions on the
    SAME driver both run (sequentially here, since one runner instance)."""
    runner = MockRunner([_text_event("a")])
    driver = MagiEngineDriver(runner=runner)

    async def scenario() -> EngineResult:
        _events, terminal = await drain(
            driver.run_turn_stream(None, _turn_input("sess-A"), cancel=asyncio.Event())
        )
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.terminal is Terminal.completed
    # After the first turn released, a second distinct session also completes.
    runner2 = MockRunner([_text_event("b")])
    driver2 = MagiEngineDriver(runner=runner2)

    async def scenario2() -> EngineResult:
        _events, terminal = await drain(
            driver2.run_turn_stream(None, _turn_input("sess-B"), cancel=asyncio.Event())
        )
        return terminal

    assert asyncio.run(scenario2()).terminal is Terminal.completed


def test_release_runs_even_when_aclose_raises() -> None:
    """FIX 3 (global review): a sub-generator whose ``aclose()`` raises must
    still release the single-flight slot, so a subsequent turn for the SAME
    session is NOT rejected as ``active_session_turn``."""

    driver = MagiEngineDriver(runner=MockRunner([_text_event("ok")]))

    async def _scenario() -> EngineResult:
        # Patch _drive to return a generator whose aclose() raises. The
        # run_turn_stream wrapper's finally must still call registry.release().
        async def _exploding_drive(**_kwargs: object):
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.completed,
                usage={},
                cost_usd=0.0,
                error=None,
            )

        class _Wrapper:
            """Wraps a generator and raises in aclose()."""

            def __init__(self, gen):
                self._gen = gen

            def __aiter__(self):
                return self._gen.__aiter__()

            async def aclose(self):
                await self._gen.aclose()
                raise RuntimeError("aclose boom")

        def _patched_drive(**kwargs: object):
            return _Wrapper(_exploding_drive(**kwargs))

        driver._drive = _patched_drive  # type: ignore[assignment]

        # Turn 1: drains; its aclose() raises inside run_turn_stream's finally.
        try:
            await drain(
                driver.run_turn_stream(
                    None, _turn_input("leak-sess", turn_id="t1"), cancel=asyncio.Event()
                )
            )
        except RuntimeError:
            pass  # the boom propagates out of aclose; that's fine for this test

        # Restore a normal _drive for turn 2 (same session).
        del driver._drive  # type: ignore[attr-defined]

        _events, terminal2 = await drain(
            driver.run_turn_stream(
                None, _turn_input("leak-sess", turn_id="t2"), cancel=asyncio.Event()
            )
        )
        return terminal2

    terminal2 = asyncio.run(_scenario())
    # The slot was released despite the aclose() failure -> turn 2 ran normally.
    assert terminal2.error != "active_session_turn"
    assert terminal2.terminal is Terminal.completed


# ---------------------------------------------------------------------------
# 4. _sanitize_agent_event is actually applied
# ---------------------------------------------------------------------------
def test_sanitizer_redacts_private_text_in_emitted_payload() -> None:
    # A text_delta whose content carries a private "raw prompt" marker must be
    # redacted by _sanitize_agent_event to "[redacted-private]".
    runner = MockRunner([_text_event("here is the raw prompt: SECRET")])
    driver = MagiEngineDriver(runner=runner)

    events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-redact"), cancel=asyncio.Event()))
    )
    assert terminal.terminal is Terminal.completed
    token_events = [ev for ev in events if ev.type == "token"]
    assert token_events, "expected a token event"
    assert token_events[0].payload.get("delta") == "[redacted-private]"
    assert "SECRET" not in json.dumps(token_events[0].payload)


def test_sanitizer_drops_thinking_events(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inject a thinking_delta projection; the sanitizer returns None => skipped.
    # We wrap the REAL bridge (via _lazy_engine_deps) so the sanitizer is still
    # the real one being exercised.
    import magi_agent.cli.engine as engine_mod

    runner = MockRunner([_text_event("visible")])
    driver = MagiEngineDriver(runner=runner)

    real_deps = engine_mod._lazy_engine_deps()
    real_bridge = real_deps["OpenMagiEventBridge"](live_compatible=True)  # type: ignore[operator]

    class _BridgeWithThinking:
        def __init__(self, **_kw: object) -> None:
            self._real = real_bridge

        def project_adk_event(self, event: object, *, turn_id: str):
            proj = self._real.project_adk_event(event, turn_id=turn_id)
            # Prepend a thinking event that MUST be dropped by the sanitizer.
            proj.agent_events.insert(0, {"type": "thinking_delta", "delta": "secret"})
            return proj

    def _fake_deps() -> dict:
        deps = dict(real_deps)
        deps["OpenMagiEventBridge"] = _BridgeWithThinking
        return deps

    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", _fake_deps)

    events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-think"), cancel=asyncio.Event()))
    )
    assert terminal.terminal is Terminal.completed
    # No thinking content leaks into any emitted payload.
    assert all(ev.payload.get("type") != "thinking_delta" for ev in events)
    assert all("secret" not in json.dumps(ev.payload) for ev in events)


def test_sanitizer_passes_thinking_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # The OTHER side of the MAGI_STREAM_THINKING gate: with the flag SET, the
    # sanitizer lets a thinking_delta through (redacted), so a thinking event
    # DOES reach the TUI. Mirrors test_sanitizer_drops_thinking_events (flag off)
    # but proves the gate is genuinely a gate, not an unconditional drop.
    import magi_agent.cli.engine as engine_mod

    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")

    runner = MockRunner([_text_event("visible")])
    driver = MagiEngineDriver(runner=runner)

    real_deps = engine_mod._lazy_engine_deps()
    real_bridge = real_deps["OpenMagiEventBridge"](live_compatible=True)  # type: ignore[operator]

    class _BridgeWithThinking:
        def __init__(self, **_kw: object) -> None:
            self._real = real_bridge

        def project_adk_event(self, event: object, *, turn_id: str):
            proj = self._real.project_adk_event(event, turn_id=turn_id)
            proj.agent_events.insert(0, {"type": "thinking_delta", "delta": "just planning"})
            return proj

    def _fake_deps() -> dict:
        deps = dict(real_deps)
        deps["OpenMagiEventBridge"] = _BridgeWithThinking
        return deps

    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", _fake_deps)

    events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-think-on"), cancel=asyncio.Event()))
    )
    assert terminal.terminal is Terminal.completed
    # With the flag ON, the thinking event survives the sanitizer (engine maps
    # thinking_delta -> a "status" RuntimeEvent whose inner payload type stays
    # "thinking_delta") — it is NOT dropped.
    assert any(
        isinstance(ev.payload, dict) and ev.payload.get("type") == "thinking_delta"
        for ev in events
    ), [ev.payload for ev in events]


# ---------------------------------------------------------------------------
# 5. engine.py import-clean (no google.adk / textual at module load)
# ---------------------------------------------------------------------------
def test_engine_module_is_import_clean() -> None:
    # Importing the engine module (already imported above) must not have pulled
    # in google.adk or textual at module-load time. We assert engine.py itself
    # does not name them at top-level by checking a fresh subprocess-free signal:
    # the module's own globals carry no adk/textual references.
    import magi_agent.cli.engine as engine_mod

    src_globals = vars(engine_mod)
    # Lazy helper exists and heavy symbols are NOT module-level names.
    assert "_lazy_engine_deps" in src_globals
    assert "OpenMagiRunnerAdapter" not in src_globals
    assert "OpenMagiEventBridge" not in src_globals
    assert "_sanitize_agent_event" not in src_globals
    # 'types' (google.genai) is also not a module global.
    assert "types" not in src_globals


def test_engine_import_clean_in_fresh_interpreter() -> None:
    # Definitive check: a fresh interpreter importing only engine.py must not
    # have google.adk or textual loaded.
    import subprocess

    code = (
        "import magi_agent.cli.engine, sys;"
        "print(any(m=='textual' or m.startswith('textual.') for m in sys.modules),"
        "any('google.adk' in m for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False False", result.stdout


# ---------------------------------------------------------------------------
# A2 headless end-to-end with the real (mocked) engine
# ---------------------------------------------------------------------------
def test_headless_stream_json_with_magi_driver_emits_one_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    runner = MockRunner([_text_event("answer "), _text_event("text")])
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            driver=MagiEngineDriver(runner=runner),
            stream=buffer,
        )
    )
    assert code == 0
    lines = [line for line in buffer.getvalue().splitlines() if line]
    objs = [json.loads(line) for line in lines]
    # System init first, exactly one result line last.
    assert objs[0]["type"] == "system"
    result_lines = [obj for obj in objs if obj.get("type") == "result"]
    assert len(result_lines) == 1
    assert result_lines[0]["subtype"] == "success"
    assert result_lines[0]["is_error"] is False
    # B1 regression: the real engine emits token text under the `delta` key
    # (text_delta), so the headless projection MUST surface it — not drop it.
    assert result_lines[0]["result"] == "answer text"
    assistant = [obj for obj in objs if obj.get("type") == "assistant"]
    assert any(obj["message"].get("content") for obj in assistant), (
        "real-engine assistant text was dropped by the headless projection"
    )


def test_headless_stream_json_with_final_only_adk_text_emits_assistant_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    runner = MockRunner(
        [_text_event("final-only answer", partial=False, turn_complete=True)]
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=MagiEngineDriver(runner=runner),
            stream=buffer,
        )
    )

    assert code == 0
    objs = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assistant = [obj for obj in objs if obj.get("type") == "assistant"]
    token_events = [
        obj
        for obj in objs
        if obj.get("type") == "stream_event"
        and obj.get("event", {}).get("type") == "token"
    ]
    result = next(obj for obj in objs if obj.get("type") == "result")

    assert assistant
    assert assistant[0]["message"]["content"] == "final-only answer"
    assert token_events
    assert result["result"] == "final-only answer"


def test_headless_text_mode_with_magi_driver_surfaces_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # B1 regression for `text` output mode too.
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    runner = MockRunner([_text_event("real "), _text_event("answer")])
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hi",
            output="text",
            driver=MagiEngineDriver(runner=runner),
            stream=buffer,
        )
    )
    assert code == 0
    assert buffer.getvalue() == "real answer\n"


def test_headless_text_mode_with_final_only_adk_text_surfaces_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    runner = MockRunner(
        [_text_event("final-only text", partial=False, turn_complete=True)]
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless(
            "hi",
            output="text",
            driver=MagiEngineDriver(runner=runner),
            stream=buffer,
        )
    )

    assert code == 0
    assert buffer.getvalue() == "final-only text\n"


def test_headless_text_mode_with_fireworks_kimi_final_aggregate_surfaces_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    runner = MockRunner(
        [_text_event("kimi aggregate text", partial=False, turn_complete=False)]
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless(
            "hi",
            output="text",
            driver=MagiEngineDriver(runner=runner),
            stream=buffer,
        )
    )

    assert code == 0
    assert buffer.getvalue() == "kimi aggregate text\n"


def test_headless_text_mode_dedupes_partials_plus_fireworks_kimi_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    runner = MockRunner(
        [
            _text_event("streamed ", partial=True),
            _text_event("answer", partial=True),
            _text_event(
                "streamed answer",
                partial=False,
                turn_complete=False,
            ),
        ]
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless(
            "hi",
            output="text",
            driver=MagiEngineDriver(runner=runner),
            stream=buffer,
        )
    )

    assert code == 0
    assert buffer.getvalue() == "streamed answer\n"


def test_headless_stream_json_with_fireworks_kimi_final_aggregate_emits_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    runner = MockRunner(
        [_text_event("kimi stream aggregate", partial=False, turn_complete=False)]
    )
    buffer = io.StringIO()

    code = asyncio.run(
        run_headless(
            "hi",
            output="stream-json",
            include_partial=True,
            driver=MagiEngineDriver(runner=runner),
            stream=buffer,
        )
    )

    assert code == 0
    objs = [json.loads(line) for line in buffer.getvalue().splitlines() if line]
    assistant = [obj for obj in objs if obj.get("type") == "assistant"]
    token_events = [
        obj
        for obj in objs
        if obj.get("type") == "stream_event"
        and obj.get("event", {}).get("type") == "token"
    ]
    result = next(obj for obj in objs if obj.get("type") == "result")

    assert assistant
    assert token_events
    assert result["result"] == "kimi stream aggregate"


class _RaisingRunner:
    """Yields a tool_start, then raises mid-turn (a pending tool is orphaned)."""

    def __init__(self, before: list[Event], exc: Exception) -> None:
        self._before = before
        self._exc = exc

    async def run_async(self, **_kwargs: object):
        for event in self._before:
            yield event
        raise self._exc


def test_engine_error_midtool_synthesizes_orphan_tool_result() -> None:
    # C3: a runner failure while a tool_use is pending must still synthesize a
    # balancing tool_end (same hazard as cancel), then yield an error terminal.
    runner = _RaisingRunner(
        before=[_call_event("Bash", {"cmd": "boom"}, "err-1")],
        exc=RuntimeError("kaboom"),
    )
    driver = MagiEngineDriver(runner=runner)

    events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-err"), cancel=asyncio.Event()))
    )

    assert terminal.terminal is Terminal.error
    assert terminal.error and "kaboom" in terminal.error
    synthetic = [
        ev
        for ev in events
        if ev.type == "tool"
        and ev.payload.get("type") == "tool_end"
        and ev.payload.get("id") == "err-1"
        and ev.payload.get("interrupted") is True
    ]
    assert len(synthetic) == 1
