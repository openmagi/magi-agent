"""The CLI runner must request SSE streaming.

Without streaming, a model returns its whole reply as a single final event; the
event bridge keeps that final text on the transcript channel only (a redaction
boundary — see tests/test_event_bridge.py), so the public stream the CLI renders
gets no text and the user sees nothing. Streaming makes the model emit partial
token deltas, which DO flow on the public stream.
"""

from __future__ import annotations

import pytest

from magi_agent.cli.real_runner import CliModelRunner


class _RecordingRunner:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def run_async(self, **kwargs: object):
        self.kwargs = dict(kwargs)
        return
        yield  # pragma: no cover - makes this an async generator


class _FakeSessionService:
    async def get_session(self, **_kwargs: object) -> object:
        return object()  # session exists -> no create needed

    async def create_session(self, **_kwargs: object) -> object:  # pragma: no cover
        return object()


@pytest.mark.asyncio
async def test_cli_runner_requests_sse_streaming() -> None:
    from google.adk.agents.run_config import StreamingMode

    inner = _RecordingRunner()
    runner = CliModelRunner(
        runner=inner,
        agent=object(),
        session_service=_FakeSessionService(),
        app_name="magi-cli",
    )

    async for _ in runner.run_async(user_id="u", session_id="s", new_message=None):
        pass

    run_config = inner.kwargs.get("run_config")
    assert run_config is not None, "CLI runner must pass a run_config"
    assert run_config.streaming_mode == StreamingMode.SSE


@pytest.mark.asyncio
async def test_cli_runner_preserves_explicit_run_config() -> None:
    inner = _RecordingRunner()
    runner = CliModelRunner(
        runner=inner,
        agent=object(),
        session_service=_FakeSessionService(),
        app_name="magi-cli",
    )
    sentinel = object()

    async for _ in runner.run_async(
        user_id="u", session_id="s", new_message=None, run_config=sentinel
    ):
        pass

    assert inner.kwargs.get("run_config") is sentinel
