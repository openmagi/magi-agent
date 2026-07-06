"""B8: the hosted SSE dashboard chat must deliver the governed turn's answer
LIVE (token-by-token), not as a single end-of-turn blob.

When ``MAGI_HOSTED_GOVERNED_TURN_ENABLED`` is ON the selected-full-toolhost SSE
route (`/v1/chat/stream`) drains an ``asyncio.Queue`` populated by the 1-arg
``public_event_sink``. The governed serving branch collects the engine event
stream through ``collect_engine_to_boundary_result``, which historically
``drain()``-ed the stream opaquely and NEVER called ``public_event_sink`` during
the turn. Net effect before the fix: the live queue stayed empty, so the only
text the client received was the single post-turn blob the route synthesises
from the completed response JSON's ``content`` (``live_text_emitted`` False) --
one ``text_delta`` frame carrying the entire answer, with NO token streaming.

This test drives the REAL flag-ON FastAPI SSE route with a real ``google.adk``
Runner fronting a fake streaming ``BaseLlm`` that emits the answer as several
partial chunks. With the fix the client receives one ``text_delta`` frame PER
chunk (live parity with the legacy path); without the fix it receives the answer
as exactly one blob frame. Both assertions therefore pin the defect: the answer
is present either way, but only the fix delivers it as multiple live frames.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

import magi_agent.transport.gate5b_serving as serving_mod
from magi_agent.runtime.hosted_runtime import HostedRuntime
from magi_agent.shadow.hosted_session_substrate import (
    reset_durable_hosted_session_service,
)
from magi_agent.shadow.session_service_registry import (
    reset_default_session_service_registry,
)
from magi_agent.transport.active_turn import ACTIVE_TURNS
from tests.test_gate5b_serving_seed_on_empty import _valid_agent_hosted_runtime
from tests.test_streaming_chat_route import (
    _auth_headers,
    _data_lines,
    _make_app,
    _selected_runtime,
)

_ANSWER_CHUNKS = ("The ", "project ", "codename ", "is ", "MULTIVERSE.")
_ANSWER = "".join(_ANSWER_CHUNKS)


class _StreamingLlm(BaseLlm):
    """Emits the answer as several partial ``LlmResponse`` chunks, then a final
    non-partial aggregate -- the shape a streaming model produces so the ADK
    Runner surfaces one partial text event per chunk."""

    def __init__(self, chunks: tuple[str, ...]) -> None:
        super().__init__(model="fake")
        self._chunks = tuple(chunks)

    async def generate_content_async(self, llm_request: object, stream: bool = False):  # noqa: ANN201
        for chunk in self._chunks:
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=chunk)]),
                partial=True,
            )
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text="".join(self._chunks))]
            ),
            turn_complete=True,
        )


def _wire_governed_real_runner(monkeypatch: Any) -> None:
    """Point the flag-ON governed serving branch at a real ADK Runner fronting
    the streaming fake LLM, with its own real in-memory session service (so the
    fake primitives' session double is never touched)."""
    from google.adk import sessions as adk_sessions

    def fake_model(**kwargs: object) -> object:
        return _StreamingLlm(_ANSWER_CHUNKS)

    monkeypatch.setattr(serving_mod, "_gate1a_correlated_model_or_label", fake_model)

    def real_build_hosted_runtime(**kwargs: object) -> HostedRuntime:
        return _valid_agent_hosted_runtime(
            model=kwargs["model"],
            session_service=adk_sessions.InMemorySessionService(),
            sink=[],
        )

    monkeypatch.setattr(serving_mod, "build_hosted_runtime", real_build_hosted_runtime)


def test_governed_sse_delivers_answer_as_live_text_deltas(
    monkeypatch: Any, tmp_path: Path
) -> None:
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")
    _wire_governed_real_runner(monkeypatch)

    runtime = _selected_runtime(tmp_path, full_toolhost=True)
    client = TestClient(_make_app(runtime=runtime))
    try:
        response = client.post(
            "/v1/chat/stream",
            headers=_auth_headers(),
            json={
                "sessionId": "s-governed-live",
                "turnId": "t-governed-live",
                "messages": [
                    {"role": "user", "content": "What is the project codename?"}
                ],
            },
        )
        assert response.status_code == 200, response.text
        payloads = _data_lines(response.text)
        text_deltas = [p for p in payloads if p.get("type") == "text_delta"]
        joined = "".join(str(p.get("delta", "")) for p in text_deltas)

        # The answer must reach the client (true both before and after the fix).
        assert "MULTIVERSE." in joined, response.text
        # ...and it must arrive as MULTIPLE live frames, not one end-of-turn blob.
        # Before the fix the only text frame is the single content-blob fallback
        # (exactly one text_delta carrying the whole answer); the fix streams one
        # frame per model chunk.
        assert len(text_deltas) >= 2, (
            "governed SSE must stream text_delta frames live, not as one blob; "
            f"saw {len(text_deltas)} text_delta frame(s): {text_deltas}"
        )
        # Terminal frame is a successful completion.
        assert payloads[-1]["type"] == "turn_result"
        assert payloads[-1]["terminal"] == "completed"
    finally:
        ACTIVE_TURNS._turns.clear()
        reset_default_session_service_registry()
        reset_durable_hosted_session_service()
