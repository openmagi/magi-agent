"""The default /v1/chat/stream builder reuses a session service per channel.

End-to-end route wiring test (no model dependency): the LIVE
``_default_engine_builder`` must thread a ``session_service_factory`` into
``build_headless_runtime`` that resolves to the SAME service for repeat turns on
one ``sessionId`` and a DIFFERENT service across channels.

See docs/plans/2026-07-06-local-serve-session-continuity-fix-design.md.
"""

from __future__ import annotations

import magi_agent.cli.wiring as wiring_module
import pytest
from fastapi.testclient import TestClient

from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.transport.local_session_registry import (
    reset_local_session_service_registry,
)

from tests.test_streaming_chat_route import _auth_headers, _make_app


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_local_session_service_registry()
    yield
    reset_local_session_service_registry()


class _StubEngine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
        yield EngineResult(
            terminal=Terminal.completed,
            usage={"input_tokens": 1},
            session_id="s",
            turn_id="t",
        )


class _StubRuntime:
    def __init__(self) -> None:
        self.engine = _StubEngine()
        self.gate = None


def _post(client: TestClient, session_id: str) -> None:
    resp = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": session_id,
            "turnId": f"{session_id}:t",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200


def test_default_builder_reuses_service_per_session(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")

    captured: list = []

    def _capture_build_headless_runtime(*args, **kwargs):
        captured.append(kwargs.get("session_service_factory"))
        return _StubRuntime()

    monkeypatch.setattr(
        wiring_module, "build_headless_runtime", _capture_build_headless_runtime
    )

    client = TestClient(_make_app())  # engine_builder=None -> live default builder

    _post(client, "chan-A")
    _post(client, "chan-A")
    _post(client, "chan-B")

    assert len(captured) == 3
    assert all(f is not None for f in captured)
    svc_a1 = captured[0]("magi-cli")
    svc_a2 = captured[1]("magi-cli")
    svc_b = captured[2]("magi-cli")
    assert svc_a1 is svc_a2  # same channel -> reused
    assert svc_a1 is not svc_b  # distinct channel -> isolated
