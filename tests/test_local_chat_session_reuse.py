"""The local ADK chat SSE seam reuses a session service per channel.

Second local surface (``_local_adk_chat_sse``) must thread the same
process-level ``session_service_factory`` so turn-to-turn history persists here
too, keyed by ``sessionId`` and sharing the registry with /v1/chat/stream.

See docs/plans/2026-07-06-local-serve-session-continuity-fix-design.md.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.transport.local_session_registry import (
    reset_local_session_service_registry,
)


class _FakeEngine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")


async def _drain(gen) -> str:  # noqa: ANN001
    return "".join([chunk async for chunk in gen])


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_local_session_service_registry()
    yield
    reset_local_session_service_registry()


@pytest.mark.asyncio
async def test_local_chat_sse_threads_session_service_factory(
    tmp_path, monkeypatch
) -> None:
    import magi_agent.cli.wiring as wiring
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    captured: dict[str, object] = {}

    def _fake_build(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(engine=_FakeEngine(), gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )

    runtime = SimpleNamespace(config=SimpleNamespace(model="anthropic/claude"))
    out = await _drain(
        chat_mod._local_adk_chat_sse(
            runtime, {"sessionId": "chan-x", "turnId": "chan-x:t"}, "hello"
        )
    )
    assert out.rstrip().endswith("data: [DONE]")

    factory = captured.get("session_service_factory")
    assert callable(factory)
    # Same channel -> same service; the factory keys by the payload sessionId.
    assert factory("magi-cli") is factory("magi-cli")
