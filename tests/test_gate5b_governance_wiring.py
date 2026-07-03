"""Tests for the gate5b-governance wiring (MAGI_GATE5B_GOVERNANCE_ENABLED).

Covers the three guarantees the wiring must provide:

1. Flag OFF (and unset) is byte-identical to today:
   * ``build_gate5b_control_plane_plugins`` returns ``[]`` so the gate5b Runner
     gets NO ``plugins`` kwarg.
   * the pre-final grounding gate is inert (returns ``None``; never blocks).
   * the live runner boundary builds its Runner with NO ``plugins`` kwarg.

2. Flag ON runs the control plane on the gate5b runner:
   * ``build_gate5b_control_plane_plugins`` returns the SAME
     ``_ExtendedControlPlanePlugin`` the cli/engine runner uses.
   * the live runner boundary forwards that plugin into the ADK Runner.

3. Flag ON blocks on a missing-evidence / ungrounded answer:
   * an answer asserting a specific value absent from the turn's evidence corpus
     is classified ``ungrounded_guess`` so the serving path blocks it.

No real LLM, no real ADK Runner — fake primitives + the deterministic grounding
detector only. Every test isolates ``MAGI_GATE5B_GOVERNANCE_ENABLED`` via
``monkeypatch`` so it never leaks across the suite.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundary,
)
from magi_agent.transport.gate5b_governance import (
    build_gate5b_control_plane_plugins,
    corpus_from_public_events,
    gate5b_governance_enabled,
    gate5b_pre_final_grounding_status,
)

# Reuse the boundary test's request payload + fake ADK primitives so this test
# drives the SAME live-runner path the serving boundary uses.
from tests.test_gate5b4c3_live_runner_boundary import (  # noqa: PLC0415
    _FakeRunner,
    _enabled_config,
    _fake_primitives,
    _request,
)


_ENV = "MAGI_GATE5B_GOVERNANCE_ENABLED"


# ---------------------------------------------------------------------------
# 1. Flag OFF — inert, byte-identical
# ---------------------------------------------------------------------------


def test_flag_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert gate5b_governance_enabled() is False
    monkeypatch.setenv(_ENV, "1")
    assert gate5b_governance_enabled() is True
    monkeypatch.setenv(_ENV, "off")
    assert gate5b_governance_enabled() is False


def test_off_builds_no_control_plane_plugins(monkeypatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert build_gate5b_control_plane_plugins() == []


def test_off_grounding_status_is_inert(monkeypatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    # Even a blatant ungrounded guess returns None when the master flag is OFF.
    status = gate5b_pre_final_grounding_status(
        final_text="the view count is 776665",
        public_events=[{"type": "tool_end", "output_preview": "no number here at all"}],
    )
    assert status is None


def test_off_boundary_runner_gets_no_plugins_kwarg(monkeypatch) -> None:
    """Flag OFF: the caller passes control_plane_plugins=() -> no plugins kwarg."""
    monkeypatch.delenv(_ENV, raising=False)
    primitives = _fake_primitives()
    boundary = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(),
        # control_plane_plugins defaults to () — exactly what the OFF caller passes.
    )
    result = asyncio.run(boundary.invoke_async(_request(), config=_enabled_config()))
    assert result.status == "completed"
    # The Runner construction must NOT carry a ``plugins`` kwarg when none were
    # supplied — byte-identical to the pre-governance boundary.
    assert "plugins" not in _FakeRunner.created_kwargs


# ---------------------------------------------------------------------------
# 2. Flag ON — control plane reaches the gate5b runner
# ---------------------------------------------------------------------------


def test_on_builds_extended_control_plane_plugin(monkeypatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    plugins = build_gate5b_control_plane_plugins()
    assert len(plugins) == 1
    # MUST be the SAME plugin type the local-CLI runner (cli.real_runner) attaches
    # via build_default_plugin — proving one shared control-plane path, no drift.
    from magi_agent.adk_bridge.control_plane import _ExtendedControlPlanePlugin

    assert isinstance(plugins[0], _ExtendedControlPlanePlugin)


def test_on_boundary_runner_receives_control_plane_plugin(monkeypatch) -> None:
    """Flag ON: the control-plane plugin is forwarded into the ADK Runner."""
    monkeypatch.setenv(_ENV, "1")
    plugins = build_gate5b_control_plane_plugins()
    assert plugins, "governance ON must yield a control-plane plugin"

    primitives = _fake_primitives()
    boundary = Gate5B4C3LiveRunnerBoundary(
        lambda: primitives,
        adk_tools=(),
        control_plane_plugins=plugins,
    )
    result = asyncio.run(boundary.invoke_async(_request(), config=_enabled_config()))
    assert result.status == "completed"
    # The control plane reached the gate5b runner exactly as it reaches the
    # cli/engine runner (ADK App/Runner plugins).
    forwarded = _FakeRunner.created_kwargs.get("plugins")
    assert forwarded == plugins


# ---------------------------------------------------------------------------
# 3. Flag ON — pre-final grounding blocks an ungrounded guess
# ---------------------------------------------------------------------------


def test_on_blocks_ungrounded_specific_value(monkeypatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    # The answer asserts 776,665 but the collected evidence corpus mentions only
    # a different figure -> ungrounded guess -> block.
    status = gate5b_pre_final_grounding_status(
        final_text="The channel has exactly 776665 subscribers.",
        public_events=[
            {"type": "tool_end", "output_preview": "page listed 12,000 followers"},
            {"type": "text_delta", "delta": "Let me check the page."},
        ],
    )
    assert status == "ungrounded_guess"


def test_on_passes_when_value_supported_by_corpus(monkeypatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    # Same specific value, but now it IS present in the evidence corpus -> grounded.
    status = gate5b_pre_final_grounding_status(
        final_text="The channel has exactly 776665 subscribers.",
        public_events=[
            {"type": "tool_end", "output_preview": "subscriber total: 776,665"},
        ],
    )
    assert status == "grounded"


def test_on_does_not_block_answer_without_specific_value(monkeypatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    # No specific numeric/identifier value to ground (the G4 boundary) -> grounded
    # so ordinary chat is never blocked by the guard.
    status = gate5b_pre_final_grounding_status(
        final_text="I summarized the document for you.",
        public_events=[{"type": "tool_end", "output_preview": "some collected content"}],
    )
    assert status == "grounded"


def test_on_no_corpus_does_not_block(monkeypatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    # No reachable evidence corpus at this seam -> the guard cannot contradict the
    # answer, so it does not block (the model may answer from its own knowledge).
    status = gate5b_pre_final_grounding_status(
        final_text="The value is 776665.",
        public_events=[],
    )
    assert status is None


def test_on_empty_answer_does_not_block(monkeypatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    status = gate5b_pre_final_grounding_status(
        final_text="",
        public_events=[{"type": "tool_end", "output_preview": "evidence"}],
    )
    assert status is None


def test_on_answer_cannot_ground_itself(monkeypatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    # The specific value appears ONLY in the model's own answer text_delta, not in
    # any tool-evidence event. The guard must NOT count the answer as its own
    # supporting corpus -> there is no tool corpus -> it does not falsely "ground".
    status = gate5b_pre_final_grounding_status(
        final_text="The total is 776665.",
        public_events=[
            {"type": "text_delta", "delta": "The total is 776665."},
        ],
    )
    # No tool-evidence corpus reachable -> None (cannot contradict, does not block).
    assert status is None


# ---------------------------------------------------------------------------
# Corpus harvesting from public events
# ---------------------------------------------------------------------------


def test_corpus_harvests_tool_evidence_only_excludes_answer_text() -> None:
    corpus = corpus_from_public_events(
        [
            {"type": "text_delta", "delta": "answer text"},  # model output: excluded
            {"type": "tool_end", "output_preview": "tool output preview"},
            {"type": "tool_progress", "message": "running grep", "label": "Grep"},
            {"type": "tool_end", "receiptRefs": ["result:sha256:" + "a" * 64]},
            {"type": "noise"},
            "not-a-mapping",
        ]
    )
    # The model's own answer text is NOT corpus.
    assert "answer text" not in corpus
    # Tool-evidence content IS corpus.
    assert "tool output preview" in corpus
    assert "running grep" in corpus
    assert "Grep" in corpus
    assert "result:sha256:" + "a" * 64 in corpus


def test_corpus_dedups_and_drops_blanks() -> None:
    corpus = corpus_from_public_events(
        [
            {"type": "tool_end", "output_preview": "dup"},
            {"type": "tool_end", "output_preview": "dup"},
            {"type": "tool_end", "output_preview": "   "},
        ]
    )
    assert corpus == ("dup",)


# ---------------------------------------------------------------------------
# End-to-end: the full /v1/chat/completions live-canary HTTP path
# ---------------------------------------------------------------------------
#
# Drives ``run_gate5b_user_visible_chat_response`` -> ``_run_live_chat_runner``
# through a fully-configured runtime, with the live runner boundary STUBBED so
# the test controls (a) the tool-evidence event emitted on the public sink and
# (b) the final answer text. This proves the wiring END-TO-END: the same gate5b
# serving turn blocks an ungrounded answer with the flag ON and is byte-identical
# with the flag OFF. Fake everything; no real ADK, no real model.

from fastapi.testclient import TestClient  # noqa: E402

from magi_agent.app import create_app  # noqa: E402
from magi_agent.config.models import PythonRuntimeAuthorityConfig  # noqa: E402
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (  # noqa: E402
    Gate5B4C3LiveRunnerBoundaryResult,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (  # noqa: E402
    Gate5B4C3ShadowGenerationConfig as _ShadowGenConfig,
    Gate5B4C3ShadowGenerationDiagnostic as _ShadowGenDiagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (  # noqa: E402
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.transport import chat_routes as _chat_routes_module  # noqa: E402
from magi_agent.transport.chat import Gate5BUserVisibleChatRouteConfig  # noqa: E402
from magi_agent.transport.shadow_generations import (  # noqa: E402
    Gate5B4C3ShadowGenerationRouteConfig,
)
from tests.test_chat_route_contract import (  # noqa: E402,PLC0415
    _fake_primitives as _contract_fake_primitives,
    _sha256,
    make_runtime,
)


def _live_canary_runtime(tmp_path) -> object:
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_contract_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=_ShadowGenConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 5,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    return runtime


def _install_stub_boundary(
    monkeypatch,
    *,
    answer_text: str,
    tool_preview: str,
) -> None:
    """Stub the live runner boundary: emit ONE tool-evidence event on the sink
    (so the grounding corpus has the tool ``output_preview``) and return a
    completed result whose internal output is ``answer_text``."""

    diagnostic = _ShadowGenDiagnostic(
        accepted=True,
        status="accepted",
        reason="accepted",
        shadowGenerationId="shadow_gen_governance",
        provider="google",
        model="gemini-3.5-flash",
        routingSource="per_turn_injected",
    )

    async def _stub_boundary(*_args: object, **kwargs: object) -> object:
        sink = kwargs.get("public_event_sink")
        if sink is not None:
            sink({"type": "tool_end", "output_preview": tool_preview})
        return Gate5B4C3LiveRunnerBoundaryResult(
            diagnostic=diagnostic.model_dump(by_alias=True, mode="python", warnings=False),
            status="completed",
            reason="runner_completed",
            adkInvoked=True,
            runnerAttempted=True,
            modelCallViaAdkRunnerAttempted=True,
            eventCount=1,
            routingSource="per_turn_injected",
            selectedProvider="google",
            selectedModel="gemini-3.5-flash",
            outputTextInternal=answer_text,
            usageInternal=None,
        )

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_gate5b4c3_live_runner_boundary_async",
        _stub_boundary,
    )


def _post_canary(runtime) -> object:
    return TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": "sha256:" + "8" * 64,
        },
        json={"messages": [{"role": "user", "content": "How many subscribers?"}]},
    )


def test_e2e_flag_on_blocks_ungrounded_answer(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv(_ENV, "1")
    # Answer asserts 776,665; the only tool evidence mentions a different figure.
    _install_stub_boundary(
        monkeypatch,
        answer_text="The channel has 776665 subscribers.",
        tool_preview="the page showed about 12,000 subscribers",
    )
    response = _post_canary(_live_canary_runtime(tmp_path))

    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "python_error"
    assert body["reason"] == "gate5b_governance_ungrounded_answer"
    assert body["responseAuthority"] == "typescript"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    # The ungrounded draft is NOT emitted as a python-authority answer.
    assert "choices" not in body


def test_e2e_flag_on_allows_grounded_answer(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv(_ENV, "1")
    # Same value, now SUPPORTED by the tool evidence -> grounded -> emitted.
    _install_stub_boundary(
        monkeypatch,
        answer_text="The channel has 776665 subscribers.",
        tool_preview="subscriber count reported as 776,665",
    )
    response = _post_canary(_live_canary_runtime(tmp_path))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["responseAuthority"] == "python"
    assert body["choices"][0]["message"]["content"] == "The channel has 776665 subscribers."


def test_e2e_flag_off_emits_same_ungrounded_answer(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.delenv(_ENV, raising=False)
    # The SAME ungrounded answer that blocks with the flag ON: with the flag OFF
    # the gate5b serving path emits it unchanged (byte-identical behavior).
    _install_stub_boundary(
        monkeypatch,
        answer_text="The channel has 776665 subscribers.",
        tool_preview="the page showed about 12,000 subscribers",
    )
    response = _post_canary(_live_canary_runtime(tmp_path))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["responseAuthority"] == "python"
    assert body["choices"][0]["message"]["content"] == "The channel has 776665 subscribers."
