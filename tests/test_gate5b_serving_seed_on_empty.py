"""U4 (B2): seed-on-empty for the governed hosted path (the #1364-reopen guard).

Before U4 the flag-ON serving branch mapped ``sanitized_recent_history`` into
``TurnContext.initial_messages`` UNCONDITIONALLY. The driver prepends a rendered
resume prefix whenever ``initial_messages`` is non-empty. After U3 the governed
branch fronts the DURABLE session service, so the ADK Runner ALSO loads the
persisted prior events for the same session. Turn 2 therefore got the history
TWICE: once as persisted session events, once as an inline resume prefix. That
re-opens the #1364 continuity-bug family (duplicated context).

U4 computes an ``include_history`` verdict at the serving seam: probe the SAME
service instance and identity triple the governed runner will use, then apply
``resolve_include_history``. The verdict is threaded into the mapper so inline
history is suppressed once the durable session already holds events. The
conditional lives at the serving seam ONLY; the driver's contract (non-empty
``initial_messages`` => render prefix) is untouched.

Coverage split (why two tiers):

* Test (a) drives the REAL flag-ON FastAPI serving path with a real
  ``google.adk`` Runner. It asserts the seam computes the verdict correctly from
  the DURABLE event count read under the LEGACY identity triple: ``True`` (seed)
  on the empty first turn, ``False`` (suppress) on the reused turn once the
  durable session holds events. This is the exact identity-match guard the
  design warns about: probing under the wrong identity would read 0 and always
  seed, re-opening #1364.
* Test (b) proves at the real-Runner level that the verdict actually removes the
  duplication in the MODEL-VISIBLE contents: with a session already holding the
  prior turns, suppressed ``initial_messages`` yields the history exactly once
  (session events only, no resume prefix), whereas the un-suppressed mapping
  would render it a second time inline.

The resume-prefix mechanism is gated on the full-toolhost route
(``generation_request.py`` only fills ``sanitizedRecentHistory`` when
``full_toolhost_ready``; the readonly route embeds context in the current-turn
text instead), so the model-visible tier is driven at the runtime seam with a
controlled ``TurnContext`` rather than by standing up a full-toolhost bundle.

Test-double note: ``build_hosted_runtime`` sets the ADK Agent ``name`` equal to
the Runner ``app_name``. The governed identity (U2) is the hyphenated legacy
string, which real ADK rejects as an Agent name (it must be a valid identifier)
while it is a valid Runner/session ``app_name`` (session keying is
Runner-level). The real-Runner helpers below therefore keep the exact legacy
SESSION identity (Runner ``app_name`` / ``user_id`` / ``session_id``, so the U4
probe and the runner read the same rows) but give the Agent a valid identifier
name. See the unit report for the underlying ``build_hosted_runtime`` defect
this exposed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

import magi_agent.transport.gate5b_serving as serving_mod
from magi_agent.adk_bridge.wire_profile import HOSTED_PROFILE
from magi_agent.app import create_app
from magi_agent.engine.driver import MagiEngineDriver
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.hosted_runtime import (
    GATE5B_SHADOW_APP_NAME,
    GATE5B_SHADOW_USER_ID,
    HostedRuntime,
    _HOSTED_NOOP_GATE,
)
from magi_agent.runtime.session_ownership import seeded_history_message_count
from magi_agent.runtime.turn_context import TurnContext
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    load_gate5b4c3_live_adk_primitives,
)
from magi_agent.shadow.hosted_session_substrate import (
    reset_durable_hosted_session_service,
)
from magi_agent.shadow.session_service_registry import (
    reset_default_session_service_registry,
)
from magi_agent.transport.hosted_turn_context import hosted_request_to_turn_context
from tests.test_chat_routes_hosted_governed_turn import (
    _canary_headers,
    _make_canary_runtime,
)

# ASCII head of the driver's resume-prefix marker
# ("[Resumed conversation ... prior turns for context]"). Matching on the ASCII
# prefix avoids embedding the marker's em-dash in this file.
_RESUME_MARKER = "[Resumed conversation"


@pytest.fixture(autouse=True)
def _reset_session_state() -> Any:
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()
    yield
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()


class _CapturingLlm(BaseLlm):
    """Records each call's request contents, returns a canned reply."""

    def __init__(self, sink: list[list[object]]) -> None:
        super().__init__(model="fake")
        self._sink = sink

    async def generate_content_async(self, llm_request: object, stream: bool = False):  # noqa: ANN201
        contents = list(getattr(llm_request, "contents", None) or [])
        self._sink.append(contents)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="Understood.")])
        )


def _all_texts(contents: list[object]) -> list[str]:
    texts: list[str] = []
    for content in contents:
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


def _valid_agent_hosted_runtime(*, model: object, session_service: object, sink: list) -> HostedRuntime:
    """A faithful HostedRuntime with a real ADK Runner under the legacy SESSION
    identity but a valid-identifier Agent name (real ADK rejects the hyphenated
    app_name as an Agent name; it is fine as a Runner app_name)."""
    primitives = load_gate5b4c3_live_adk_primitives()
    agent = primitives.Agent(
        name="openmagi_gate5b4c3_shadow_generation_agent",
        description="test hosted governed agent",
        model=model,
        instruction="you are a test agent",
        tools=[],
        generate_content_config=primitives.GenerateContentConfig(),
    )
    runner = primitives.Runner(
        app_name=GATE5B_SHADOW_APP_NAME,
        agent=agent,
        session_service=session_service,
        auto_create_session=True,
    )
    engine = MagiEngineDriver(
        runner=runner,
        wire_profile=HOSTED_PROFILE,
        user_id=GATE5B_SHADOW_USER_ID,
    )
    return HostedRuntime(engine=engine, gate=_HOSTED_NOOP_GATE)


async def _drive_turn(session_service: object, ctx: TurnContext, sink: list) -> None:
    runtime = _valid_agent_hosted_runtime(
        model=_CapturingLlm(sink), session_service=session_service, sink=sink
    )
    async for _event in run_governed_turn(ctx, runtime=runtime):
        pass


# ---------------------------------------------------------------------------
# (a) serving seam: the include_history verdict from the durable event count
# ---------------------------------------------------------------------------


def _install_serving_seam_spies(monkeypatch) -> dict[str, list]:
    records: dict[str, list] = {"include_history": [], "identity": [], "llm": []}

    def fake_model(**kwargs: object) -> object:
        return _CapturingLlm(records["llm"])

    monkeypatch.setattr(serving_mod, "_gate1a_correlated_model_or_label", fake_model)

    def real_build_hosted_runtime(**kwargs: object) -> HostedRuntime:
        records["identity"].append(
            {
                "app_name": kwargs.get("app_name"),
                "user_id": kwargs.get("user_id"),
                "session_service": kwargs.get("session_service"),
            }
        )
        return _valid_agent_hosted_runtime(
            model=kwargs["model"],
            session_service=kwargs["session_service"],
            sink=records["llm"],
        )

    monkeypatch.setattr(serving_mod, "build_hosted_runtime", real_build_hosted_runtime)

    real_mapper = hosted_request_to_turn_context

    def spy_mapper(generation: object, **kwargs: object) -> TurnContext:
        records["include_history"].append(kwargs.get("include_history", "<absent>"))
        return real_mapper(generation, **kwargs)

    monkeypatch.setattr(serving_mod, "hosted_request_to_turn_context", spy_mapper)
    return records


def _post(runtime: object, *, digest: str, session_id: str) -> Any:
    return TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers(digest),
        json={
            "messages": [{"role": "user", "content": "My project codename is BLUEFIN."}],
            "sessionId": session_id,
        },
    )


def test_serving_seam_suppresses_history_once_durable_session_has_events(
    monkeypatch, tmp_path: Any
) -> None:
    """Flag-ON serving path, durable session reuse. The seam probes the durable
    event count under the LEGACY identity and threads the verdict into the
    mapper: seed on the empty first turn, SUPPRESS on the reused turn once the
    durable session holds events. Probing under the wrong identity would read 0
    and seed both turns (the #1364 re-open)."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    records = _install_serving_seam_spies(monkeypatch)
    runtime = _make_canary_runtime(tmp_path)

    resp1 = _post(runtime, digest="a" * 64, session_id="sess-seam")
    assert resp1.status_code == 200, resp1.json()
    resp2 = _post(runtime, digest="b" * 64, session_id="sess-seam")
    assert resp2.status_code == 200, resp2.json()

    # Turn 1 empty durable session -> seed; turn 2 reused-with-events -> suppress.
    assert records["include_history"] == [True, False], records["include_history"]

    # The probe and the runner used the SAME legacy identity (else the probe
    # reads a different row than the runner writes and always seeds).
    assert records["identity"], "build_hosted_runtime was never called"
    for ident in records["identity"]:
        assert ident["app_name"] == GATE5B_SHADOW_APP_NAME
        assert ident["user_id"] == GATE5B_SHADOW_USER_ID


# ---------------------------------------------------------------------------
# (b) real-Runner: a suppressed initial_messages yields history exactly once
# ---------------------------------------------------------------------------


def _session_id() -> str:
    return "gate5b4c3-shadow-seedcheck"


def test_suppressed_initial_messages_yields_history_once(monkeypatch) -> None:
    """A session already holding the prior turns: with ``initial_messages``
    suppressed (U4 on a reused session), the model sees the prior exchange
    exactly once (session events) and NO resume prefix. This is the fixed
    (non-double-seeded) shape."""
    from google.adk import sessions as adk_sessions

    svc = adk_sessions.InMemorySessionService()
    sink: list = []
    sid = _session_id()

    async def scenario() -> None:
        # Turn 1 establishes the fact into the session.
        await _drive_turn(
            svc,
            TurnContext(
                prompt="My project codename is BLUEFIN.", session_id=sid, turn_id="t1"
            ),
            sink,
        )
        # Turn 2 with history SUPPRESSED (initial_messages empty).
        await _drive_turn(
            svc,
            TurnContext(
                prompt="What is my project codename?",
                session_id=sid,
                turn_id="t2",
                initial_messages=(),
            ),
            sink,
        )

    asyncio.run(scenario())

    turn2_texts = _all_texts(sink[-1])
    joined = "\n".join(turn2_texts)
    assert _RESUME_MARKER not in joined, f"resume prefix must be absent: {turn2_texts}"
    bluefin_hits = sum(text.count("BLUEFIN") for text in turn2_texts)
    assert bluefin_hits == 1, f"history must appear exactly once, saw {bluefin_hits}: {turn2_texts}"


def test_unsuppressed_initial_messages_double_seeds_history(monkeypatch) -> None:
    """The same populated session, but with ``initial_messages`` mapped (the
    pre-U4 unconditional behavior): the model sees the prior exchange TWICE
    (session events + inline resume prefix). This is exactly what U4 prevents,
    and it pins the resume-prefix marker the suppression removes."""
    from google.adk import sessions as adk_sessions

    svc = adk_sessions.InMemorySessionService()
    sink: list = []
    sid = _session_id()

    async def scenario() -> None:
        await _drive_turn(
            svc,
            TurnContext(
                prompt="My project codename is BLUEFIN.", session_id=sid, turn_id="t1"
            ),
            sink,
        )
        await _drive_turn(
            svc,
            TurnContext(
                prompt="What is my project codename?",
                session_id=sid,
                turn_id="t2",
                initial_messages=(
                    {"role": "user", "content": "My project codename is BLUEFIN."},
                    {"role": "assistant", "content": "Understood."},
                ),
            ),
            sink,
        )

    asyncio.run(scenario())

    turn2_texts = _all_texts(sink[-1])
    joined = "\n".join(turn2_texts)
    assert _RESUME_MARKER in joined, f"unsuppressed history must render the prefix: {turn2_texts}"
    bluefin_hits = sum(text.count("BLUEFIN") for text in turn2_texts)
    assert bluefin_hits >= 2, f"unsuppressed history double-seeds, saw {bluefin_hits}: {turn2_texts}"


# ---------------------------------------------------------------------------
# (c) mapper unit: include_history gates the inline history, default byte-identical
# ---------------------------------------------------------------------------


def _fake_generation() -> object:
    return SimpleNamespace(
        turn=SimpleNamespace(
            sanitized_current_turn_text="What is my project codename?",
            sanitized_recent_history=[
                SimpleNamespace(role="user", sanitized_text="My codename is BLUEFIN."),
                SimpleNamespace(role="assistant", sanitized_text="Understood."),
            ],
            turn_id="turn-1",
        ),
        model_routing=SimpleNamespace(
            provider_label="google", model_label="gemini-3.5-flash"
        ),
        selection=SimpleNamespace(session_key_digest="sha256:" + "a" * 64),
        request_id_digest="sha256:" + "b" * 64,
    )


def test_mapper_suppresses_history_when_include_history_false() -> None:
    ctx = hosted_request_to_turn_context(_fake_generation(), include_history=False)
    assert ctx.initial_messages == ()


def test_mapper_default_maps_history_byte_identical() -> None:
    generation = _fake_generation()
    expected = tuple(
        {"role": msg.role, "content": msg.sanitized_text}
        for msg in generation.turn.sanitized_recent_history
    )
    # Default (no kwarg) and explicit include_history=True are identical and both
    # reproduce today's mapping exactly (byte-identical for other callers/tests).
    assert hosted_request_to_turn_context(generation).initial_messages == expected
    assert (
        hosted_request_to_turn_context(generation, include_history=True).initial_messages
        == expected
    )


def test_seeded_history_message_count_counts_valid_history() -> None:
    runner_input = SimpleNamespace(
        sanitized_recent_history=[
            {"role": "user", "content": "My codename is BLUEFIN."},
            {"role": "assistant", "content": "Understood."},
            {"role": "system", "content": "ignored"},
        ]
    )
    assert seeded_history_message_count(runner_input) == 2
