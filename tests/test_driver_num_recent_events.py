"""TDD -- B5 fix: num_recent_events bound threads adapter -> driver -> hosted_runtime.

RED phase: all tests below fail BEFORE the fix because:
  - ``OpenMagiRunnerAdapter.__init__`` has no ``num_recent_events`` param (TypeError).
  - ``MagiEngineDriver.__init__`` has no ``num_recent_events`` param (TypeError).
  - ``build_hosted_runtime`` has no ``num_recent_events`` param (TypeError).

Three test groups:
  1. Adapter-level: num_recent_events=200 -> RunConfig carries
     GetSessionConfig(num_recent_events=200).
  2. Adapter-level: num_recent_events=None (default) -> no get_session_config
     (byte-identical to pre-fix behavior).
  3. build_hosted_runtime: num_recent_events=200 -> runner receives the bound
     after a full governed turn.
"""

from __future__ import annotations

import asyncio

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from magi_agent.adk_bridge.runner_adapter import OpenMagiRunnerAdapter, RunnerTurnInput
from magi_agent.runtime.hosted_runtime import build_hosted_runtime
from tests.support.engine_fakes import text_event
from tests.support.gate5b4c3_fakes import _FakeGenerateContentConfig, make_primitives


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _RecordingRunner:
    """Records all ``run_async`` kwargs; yields a well-formed final text event."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_async(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        yield text_event("ok", partial=False, turn_complete=True)


def _make_loader(runner: object) -> object:
    primitives = make_primitives(runner)

    def _loader() -> object:
        return primitives

    return _loader


def _minimal_turn_input() -> RunnerTurnInput:
    return RunnerTurnInput(
        userId="u-test",
        sessionId="s-test",
        turnId="t-test",
        invocationId="t-test",
        newMessage=types.Content(role="user", parts=[types.Part(text="ping")]),
        harnessState=None,
    )


# ---------------------------------------------------------------------------
# Group 1: OpenMagiRunnerAdapter -- bound path (num_recent_events=200)
# ---------------------------------------------------------------------------


class TestAdapterNumRecentEventsBound:
    """Adapter stores num_recent_events and injects GetSessionConfig into RunConfig."""

    def test_adapter_accepts_num_recent_events_param(self) -> None:
        """Constructor must accept ``num_recent_events`` without TypeError."""
        runner = _RecordingRunner()
        # RED: __init__ has no num_recent_events param -> TypeError
        adapter = OpenMagiRunnerAdapter(runner=runner, num_recent_events=200)
        assert adapter is not None

    def test_run_config_carries_get_session_config_num_recent_events(self) -> None:
        """RunConfig forwarded to runner has GetSessionConfig(num_recent_events=200)."""
        runner = _RecordingRunner()
        adapter = OpenMagiRunnerAdapter(runner=runner, num_recent_events=200)
        turn_input = _minimal_turn_input()
        asyncio.run(adapter.collect_events(turn_input))
        assert runner.calls, "runner.run_async was not called"
        call = runner.calls[0]
        assert "run_config" in call, "run_config not forwarded to runner"
        rc = call["run_config"]
        assert isinstance(rc, RunConfig)
        assert rc.streaming_mode == StreamingMode.SSE
        gsc = rc.get_session_config
        assert gsc is not None, "get_session_config must not be None when bound=200"
        assert isinstance(gsc, GetSessionConfig)
        assert gsc.num_recent_events == 200

    def test_anti_side_channel_allowlist_unchanged(self) -> None:
        """run_config must still NOT appear in ADK_RUNNER_KWARG_ALLOWLIST."""
        from magi_agent.adk_bridge.runner_adapter import ADK_RUNNER_KWARG_ALLOWLIST

        assert "run_config" not in ADK_RUNNER_KWARG_ALLOWLIST


# ---------------------------------------------------------------------------
# Group 2: OpenMagiRunnerAdapter -- None path (byte-identical)
# ---------------------------------------------------------------------------


class TestAdapterNumRecentEventsNone:
    """Default and explicit-None paths must not add get_session_config."""

    def test_default_none_no_get_session_config(self) -> None:
        """Omitting num_recent_events leaves RunConfig without get_session_config."""
        runner = _RecordingRunner()
        adapter = OpenMagiRunnerAdapter(runner=runner)  # no num_recent_events
        turn_input = _minimal_turn_input()
        asyncio.run(adapter.collect_events(turn_input))
        assert runner.calls, "runner.run_async was not called"
        call = runner.calls[0]
        if "run_config" in call:
            rc = call["run_config"]
            assert isinstance(rc, RunConfig)
            assert rc.get_session_config is None, (
                "None path must NOT set get_session_config on RunConfig"
            )

    def test_explicit_none_no_get_session_config(self) -> None:
        """Passing num_recent_events=None explicitly is also a no-op."""
        runner = _RecordingRunner()
        adapter = OpenMagiRunnerAdapter(runner=runner, num_recent_events=None)
        turn_input = _minimal_turn_input()
        asyncio.run(adapter.collect_events(turn_input))
        call = runner.calls[0]
        if "run_config" in call:
            rc = call["run_config"]
            assert rc.get_session_config is None


# ---------------------------------------------------------------------------
# Group 3: build_hosted_runtime -- threads num_recent_events to the runner
# ---------------------------------------------------------------------------


class TestBuildHostedRuntimeNumRecentEvents:
    """build_hosted_runtime(num_recent_events=N) wires N end-to-end to the ADK runner."""

    def test_build_accepts_num_recent_events_param(self) -> None:
        """build_hosted_runtime must accept num_recent_events without TypeError."""
        runner = _RecordingRunner()
        loader = _make_loader(runner)
        # RED: function has no num_recent_events param -> TypeError
        rt = build_hosted_runtime(
            adk_primitives_loader=loader,
            model="gemini-2.0-flash",
            instruction="test",
            generate_content_config=_FakeGenerateContentConfig(),
            num_recent_events=200,
        )
        assert rt is not None

    def test_runner_receives_bound_after_full_turn(self) -> None:
        """Runner sees get_session_config.num_recent_events==200 after a governed turn."""
        from magi_agent.runtime.governed_turn import run_governed_turn
        from magi_agent.runtime.turn_context import TurnContext

        runner = _RecordingRunner()
        loader = _make_loader(runner)
        rt = build_hosted_runtime(
            adk_primitives_loader=loader,
            model="gemini-2.0-flash",
            instruction="test",
            generate_content_config=_FakeGenerateContentConfig(),
            num_recent_events=200,
        )
        ctx = TurnContext(
            prompt="hi", session_id="s-hosted-bound", turn_id="t-hosted-bound"
        )

        async def _run() -> list:
            return [e async for e in run_governed_turn(ctx, runtime=rt)]

        asyncio.run(_run())
        assert runner.calls, "runner.run_async was not called"
        first = runner.calls[0]
        assert "run_config" in first, "run_config not forwarded after full turn"
        gsc = first["run_config"].get_session_config
        assert gsc is not None, "get_session_config is None; bound not threaded"
        assert gsc.num_recent_events == 200

    def test_default_none_runner_has_no_bound(self) -> None:
        """build_hosted_runtime() without num_recent_events leaves no get_session_config."""
        from magi_agent.runtime.governed_turn import run_governed_turn
        from magi_agent.runtime.turn_context import TurnContext

        runner = _RecordingRunner()
        loader = _make_loader(runner)
        rt = build_hosted_runtime(
            adk_primitives_loader=loader,
            model="gemini-2.0-flash",
            instruction="test",
            generate_content_config=_FakeGenerateContentConfig(),
        )
        ctx = TurnContext(
            prompt="hi", session_id="s-hosted-none", turn_id="t-hosted-none"
        )

        async def _run() -> list:
            return [e async for e in run_governed_turn(ctx, runtime=rt)]

        asyncio.run(_run())
        for call in runner.calls:
            if "run_config" in call:
                assert call["run_config"].get_session_config is None, (
                    "None default must not set get_session_config"
                )
