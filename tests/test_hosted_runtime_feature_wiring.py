"""U9 (P1-1): engine-feature wiring parity for the governed hosted path.

Audit-then-wire unit. The legacy gate5b4c3 boundary wires ONE driver-shaped
engine feature that the governed ``build_hosted_runtime`` path drops:
**output continuation** (truncated-output auto-continue). Legacy activates it
for ``selected_full_toolhost`` requests from env
(``gate5b4c3:834-836`` -> ``_output_continuation_config_from_env`` ->
``MAGI_OUTPUT_CONTINUATION_ENABLED``, which is profile-aware default-ON via
``config/env.py:552`` + ``_truthy.runtime_feature_enabled``). On the canary
(full profile, ``selected_full_toolhost``) the legacy path continues long
answers that hit ``maxOutputTokens``; the governed driver, built with
``output_continuation=None`` by default (``hosted_runtime`` /
``driver.py`` default), does not. That is a user-visible regression: long
answers truncate mid-sentence under the flip.

The other two features named in the P1-1 brief are NON-gaps and are LOCKED
here so a future edit cannot silently ``build_*`` them onto the hosted path:

* ``empty_response_recovery`` -- the legacy boundary loop never constructs the
  driver's ``EmptyResponseRecoveryConfig`` (grep: zero references in
  ``gate5b4c3_live_runner_boundary.py``). It is a CLI/headless-only driver
  config (``cli/wiring.py:566``, strict default-OFF
  ``MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED``). Wiring the default-OFF builder to
  the hosted path would be inert AND would not replicate any legacy hosted
  behavior, so it stays ``None``.
* ``goal_loop_judge_factory`` -- CLI-composer-only (``cli/wiring.py:605``,
  fires only when PR-B publishes a ``GoalLoopPolicy`` on the per-turn
  ContextVar). The legacy boundary never wires it either. Stays ``None``.

NOTE (new finding, reported separately): the legacy boundary's
``_run_no_tool_finalizer`` (``gate5b4c3:1222-1234``, always-on for
``selected_full_toolhost`` when the turn ends with no visible text) is a real
empty-response behavior the governed path lacks. It is NOT a driver-ctor param
(unlike output continuation) and replicating it needs a governed-native
finalizer, so it is flagged for a dedicated unit and deliberately NOT wired
here.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.engine.engine_recovery import build_output_continuation_config
from magi_agent.runtime.hosted_runtime import build_hosted_runtime
from magi_agent.runtime.output_continuation import OutputContinuationConfig
from tests.support.engine_fakes import MockRunner, text_event
from tests.support.gate5b4c3_fakes import _FakeGenerateContentConfig, make_primitives


def _make_loader(runner: object) -> object:
    primitives = make_primitives(runner)

    def _loader() -> object:
        return primitives

    return _loader


def _build(**overrides: object) -> object:
    runner = MockRunner([text_event("ok", partial=True, turn_complete=True)])
    kwargs: dict[str, object] = {
        "adk_primitives_loader": _make_loader(runner),
        "adk_tools": (),
        "model": "fake-model",
        "instruction": "test",
        "generate_content_config": _FakeGenerateContentConfig(),
    }
    kwargs.update(overrides)
    return build_hosted_runtime(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. output_continuation is forwarded to the driver (the WIRE)
# ---------------------------------------------------------------------------


def test_output_continuation_forwarded_to_driver() -> None:
    """build_hosted_runtime(output_continuation=cfg) reaches the engine driver."""
    cfg = OutputContinuationConfig(enabled=True, max_continuations=3)
    rt = _build(output_continuation=cfg)
    assert rt.engine._output_continuation is cfg


def test_output_continuation_defaults_to_none_byte_identical() -> None:
    """Default (no output_continuation) leaves the driver's off path unchanged."""
    rt = _build()
    assert rt.engine._output_continuation is None


# ---------------------------------------------------------------------------
# 2. serving-seam resolution mirrors the legacy activation condition
#    (selected_full_toolhost AND MAGI_OUTPUT_CONTINUATION_ENABLED)
# ---------------------------------------------------------------------------


def _generation(tools_policy: str) -> object:
    return SimpleNamespace(recipe_profile=SimpleNamespace(tools_policy=tools_policy))


def test_serving_resolves_config_for_full_toolhost_default_on(monkeypatch) -> None:
    """selected_full_toolhost + profile default-ON env -> a real config.

    Mirrors legacy ``_output_continuation_config_from_env() if
    selected_full_toolhost else None`` (gate5b4c3:834-836).
    """
    from magi_agent.transport.gate5b_serving import _resolve_output_continuation_config

    # Clean env: flag unset -> runtime_feature_enabled default-ON (non-safe).
    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    resolved = _resolve_output_continuation_config(_generation("selected_full_toolhost"))
    assert isinstance(resolved, OutputContinuationConfig)
    assert resolved.enabled is True
    assert resolved.max_continuations == 4


def test_serving_returns_none_for_non_full_toolhost(monkeypatch) -> None:
    """Non-full-toolhost routes never get output continuation (legacy parity)."""
    from magi_agent.transport.gate5b_serving import _resolve_output_continuation_config

    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    assert _resolve_output_continuation_config(_generation("shadow_readonly")) is None
    assert _resolve_output_continuation_config(_generation("disabled")) is None


def test_serving_returns_none_when_flag_disabled(monkeypatch) -> None:
    """Explicit MAGI_OUTPUT_CONTINUATION_ENABLED=0 suppresses even full-toolhost."""
    from magi_agent.transport.gate5b_serving import _resolve_output_continuation_config

    monkeypatch.setenv("MAGI_OUTPUT_CONTINUATION_ENABLED", "0")
    assert _resolve_output_continuation_config(_generation("selected_full_toolhost")) is None


def test_serving_returns_none_under_safe_profile(monkeypatch) -> None:
    """MAGI_RUNTIME_PROFILE=safe flips the profile default OFF (legacy parity)."""
    from magi_agent.transport.gate5b_serving import _resolve_output_continuation_config

    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    assert _resolve_output_continuation_config(_generation("selected_full_toolhost")) is None


def test_serving_resolution_equals_legacy_builder(monkeypatch) -> None:
    """The serving resolver reuses the exact env-read the legacy path used.

    Legacy ``_output_continuation_config_from_env`` and the shared
    ``build_output_continuation_config`` both parse ``os.environ`` the same way;
    when full-toolhost the resolver must return a config equal to the shared
    builder's output (same max_continuations).
    """
    from magi_agent.transport.gate5b_serving import _resolve_output_continuation_config

    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("MAGI_MAX_OUTPUT_CONTINUATIONS", "7")
    resolved = _resolve_output_continuation_config(_generation("selected_full_toolhost"))
    reference = build_output_continuation_config()
    assert isinstance(resolved, OutputContinuationConfig)
    assert reference is not None
    assert resolved.max_continuations == reference.max_continuations == 7


# ---------------------------------------------------------------------------
# 3. NON-gap locks: empty_response_recovery / goal_loop_judge_factory stay None
# ---------------------------------------------------------------------------


def test_hosted_driver_has_no_empty_response_recovery() -> None:
    """The governed hosted driver never wires empty_response_recovery.

    Legacy hosted never constructed the driver config; wiring it would be inert
    (default-OFF) and would not replicate the legacy ``_run_no_tool_finalizer``.
    """
    rt = _build()
    assert rt.engine._empty_response_recovery is None


def test_hosted_driver_has_no_goal_loop_judge_factory() -> None:
    """The governed hosted driver never wires goal_loop_judge_factory (CLI-only)."""
    rt = _build()
    assert rt.engine._goal_loop_judge_factory is None


# ---------------------------------------------------------------------------
# 4. serving integration: the resolved config reaches build_hosted_runtime
#    over the REAL flag-ON serving path (fake model, real Runner).
# ---------------------------------------------------------------------------


def test_flag_on_serving_forwards_output_continuation_gated_on_toolhost(
    monkeypatch, tmp_path: Any
) -> None:
    """Drive the real flag-ON serving path and assert the call site forwards the
    resolved output-continuation config into ``build_hosted_runtime``, honoring
    the ``selected_full_toolhost`` gate under the default-ON env.

    Reuses the seed-on-empty harness (real ADK Runner, valid-identifier Agent
    name, ``_CapturingLlm``) which is the proven working driver for the flag-ON
    serving path; this test layers an ``output_continuation`` capture on top.
    """
    from fastapi.testclient import TestClient

    import magi_agent.transport.gate5b_serving as serving_mod
    from magi_agent.app import create_app
    from magi_agent.runtime.hosted_runtime import HostedRuntime
    from magi_agent.shadow.hosted_session_substrate import (
        reset_durable_hosted_session_service,
    )
    from magi_agent.shadow.session_service_registry import (
        reset_default_session_service_registry,
    )
    from tests.test_chat_routes_hosted_governed_turn import (
        _canary_headers,
        _make_canary_runtime,
    )
    from tests.test_gate5b_serving_seed_on_empty import (
        _CapturingLlm,
        _valid_agent_hosted_runtime,
    )

    reset_default_session_service_registry()
    reset_durable_hosted_session_service()

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    # Default-ON output continuation (canary/full profile): flag unset.
    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)

    llm_sink: list = []
    monkeypatch.setattr(
        serving_mod,
        "_gate1a_correlated_model_or_label",
        lambda **_kw: _CapturingLlm(llm_sink),
    )

    captured: list[object | None] = []

    def _capturing_build(**kwargs: object) -> HostedRuntime:
        captured.append(kwargs.get("output_continuation"))
        return _valid_agent_hosted_runtime(
            model=kwargs["model"],
            session_service=kwargs["session_service"],
            sink=llm_sink,
        )

    # Capture the resolver verdict independently so we assert the call site
    # forwarded EXACTLY that verdict (robust to the canary's resolved
    # tools_policy).
    resolver_verdicts: list[object | None] = []
    real_resolver = serving_mod._resolve_output_continuation_config

    def _spy_resolver(generation: object) -> object | None:
        verdict = real_resolver(generation)
        resolver_verdicts.append(verdict)
        return verdict

    monkeypatch.setattr(serving_mod, "build_hosted_runtime", _capturing_build)
    monkeypatch.setattr(
        serving_mod, "_resolve_output_continuation_config", _spy_resolver
    )

    runtime = _make_canary_runtime(tmp_path)
    resp = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("c" * 64),
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "sessionId": "sess-oc",
        },
    )
    assert resp.status_code == 200, resp.json()
    assert captured, "build_hosted_runtime was never called on the flag-ON path"
    assert resolver_verdicts, "resolver was never consulted"
    # The call site forwarded EXACTLY the resolver's verdict for this generation.
    assert captured[0] is resolver_verdicts[0]
    # And the verdict itself honors the gate: a config iff full-toolhost.
    verdict = resolver_verdicts[0]
    if verdict is not None:
        assert isinstance(verdict, OutputContinuationConfig)
        assert verdict.enabled is True

    reset_default_session_service_registry()
    reset_durable_hosted_session_service()
