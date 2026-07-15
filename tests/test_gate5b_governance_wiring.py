"""Tests for the gate5b-governance wiring (MAGI_GATE5B_GOVERNANCE_ENABLED).

Covers the three guarantees the wiring must provide:

1. Flag OFF (and unset) is byte-identical to today:
   * ``build_gate5b_control_plane_plugins`` returns ``[]`` so the gate5b Runner
     gets NO ``plugins`` kwarg.
   * the pre-final grounding gate is inert (returns ``None``; never blocks).
   * the governed runner build (``build_hosted_runtime``) omits the ``plugins``
     kwarg.

2. Flag ON runs the control plane on the gate5b runner:
   * ``build_gate5b_control_plane_plugins`` returns the SAME
     ``_ExtendedControlPlanePlugin`` the cli/engine runner uses.
   * ``build_hosted_runtime`` forwards that plugin into the ADK Runner.

3. Flag ON blocks on a missing-evidence / ungrounded answer:
   * an answer asserting a specific value absent from the turn's evidence corpus
     is classified ``ungrounded_guess`` so the serving path blocks it.

No real LLM, no real ADK Runner — fake primitives + the deterministic grounding
detector only. Every test isolates ``MAGI_GATE5B_GOVERNANCE_ENABLED`` via
``monkeypatch`` so it never leaks across the suite.
"""

from __future__ import annotations

import pytest

from magi_agent.runtime.hosted_runtime import build_hosted_runtime
from magi_agent.transport.gate5b_governance import (
    build_gate5b_control_plane_plugins,
    corpus_from_public_events,
    gate5b_governance_enabled,
    gate5b_pre_final_grounding_status,
)

# P5-M1b: the legacy Gate5B4C3LiveRunnerBoundary engine was retired. The
# governed serving path forwards ``control_plane_plugins`` into the ADK Runner
# via ``build_hosted_runtime`` (the same place the legacy boundary constructed
# its Runner), so the plugin-wiring assertions below drive that seam with a fake
# Runner that records its construction kwargs.
from tests.support.gate5b_governance_fakes import (  # noqa: PLC0415
    build_plugin_recording_primitives,
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
    """Flag OFF: control_plane_plugins=() -> the governed Runner gets no plugins kwarg."""
    monkeypatch.delenv(_ENV, raising=False)
    primitives, RunnerClass = build_plugin_recording_primitives()
    build_hosted_runtime(
        adk_primitives_loader=lambda: primitives,
        adk_tools=(),
        model="gemini-3.5-flash",
        instruction="be helpful",
        generate_content_config=primitives.GenerateContentConfig(),
        # control_plane_plugins defaults to () — exactly what the OFF caller passes.
    )
    # The Runner construction must NOT carry a ``plugins`` kwarg when none were
    # supplied — byte-identical to the pre-governance / pre-M1b Runner build.
    assert "plugins" not in RunnerClass.created_kwargs


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
    """Flag ON: the control-plane plugin is forwarded into the governed ADK Runner."""
    monkeypatch.setenv(_ENV, "1")
    plugins = build_gate5b_control_plane_plugins()
    assert plugins, "governance ON must yield a control-plane plugin"

    primitives, RunnerClass = build_plugin_recording_primitives()
    build_hosted_runtime(
        adk_primitives_loader=lambda: primitives,
        adk_tools=(),
        model="gemini-3.5-flash",
        instruction="be helpful",
        generate_content_config=primitives.GenerateContentConfig(),
        control_plane_plugins=plugins,
    )
    # The control plane reaches the governed runner exactly as it reaches the
    # cli/engine runner (ADK App/Runner plugins).
    forwarded = RunnerClass.created_kwargs.get("plugins")
    assert forwarded == list(plugins)


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
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (  # noqa: E402
    Gate5B4C3ShadowGenerationConfig as _ShadowGenConfig,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (  # noqa: E402
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.transport.chat import Gate5BUserVisibleChatRouteConfig  # noqa: E402
from magi_agent.transport.shadow_generations import (  # noqa: E402
    Gate5B4C3ShadowGenerationRouteConfig,
)
from tests.support.governed_turn_fakes import install_governed_turn  # noqa: E402
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


def _install_governed_answer(
    monkeypatch,
    *,
    answer_text: str,
    tool_preview: str,
) -> None:
    """Drive a deterministic governed turn: emit ONE tool-evidence event (so the
    grounding corpus has the tool ``output_preview``) followed by the answer as a
    ``text_delta``. The REAL ``collect_engine_to_boundary_result`` tees the tool
    event to the route's public sink and aggregates the text_delta into
    ``outputTextInternal`` -- exactly the two signals the pre-final grounding gate
    reads. (P5-M1b: this replaces the stub of the retired legacy boundary; the
    grounding-gate assertions are unchanged.)"""

    install_governed_turn(
        monkeypatch,
        events=[
            {"type": "tool_end", "output_preview": tool_preview},
            {"type": "text_delta", "delta": answer_text},
        ],
        terminal="completed",
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
    _install_governed_answer(
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
    _install_governed_answer(
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
    _install_governed_answer(
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
