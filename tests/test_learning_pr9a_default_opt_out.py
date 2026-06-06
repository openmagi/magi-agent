"""PR9a — Learning Layer flipped to default-ON (layered opt-out).

Covers the config/gate/readiness layer ONLY (no runtime wiring — that's PR9b):

1. ``LearningConfig`` shape + opt-out defaults.
2. ``resolve_learning_config`` precedence: override > env > opt-out default.
3. Master ``enabled=false`` ⇒ byte-identical OFF (dashboard not mounted,
   reflection disabled no-op, telemetry no emission) — mirrors the existing OFF
   assertions but triggered by master-off rather than unset env.
4. Default (nothing set) ⇒ safe tier ON; opt-in tier still OFF.
5. Authority tier still default-OFF; frozen ``Literal[False]`` flags unchanged.
6. Reflect-tier readiness ready by default; authority-tier not-ready by default.
"""

from __future__ import annotations

import asyncio
from typing import get_args

import pytest

from magi_agent.learning.config import (
    ENV_DASHBOARD,
    ENV_INJECTION,
    ENV_LABELER,
    ENV_LIVE,
    ENV_MASTER,
    ENV_REFLECTION,
    ENV_TELEMETRY,
    LearningConfig,
    resolve_learning_config,
)

_ALL_ENV = (
    ENV_MASTER,
    ENV_REFLECTION,
    ENV_DASHBOARD,
    ENV_TELEMETRY,
    ENV_LABELER,
    ENV_INJECTION,
    ENV_LIVE,
    "MAGI_LEARNING_REFLECTION_INTERVAL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with all MAGI_LEARNING_* env vars UNSET."""
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 1. LearningConfig shape + opt-out defaults
# ---------------------------------------------------------------------------


class TestLearningConfigDefaults:
    def test_safe_tier_on_by_default(self) -> None:
        cfg = LearningConfig()
        assert cfg.enabled is True
        assert cfg.reflection_enabled is True
        assert cfg.dashboard_enabled is True
        assert cfg.telemetry_enabled is True
        assert cfg.labeler == "deterministic"

    def test_opt_in_tier_off_by_default(self) -> None:
        cfg = LearningConfig()
        assert cfg.injection_enabled is False
        assert cfg.live_enabled is False

    def test_reflection_interval_default_24h(self) -> None:
        assert LearningConfig().reflection_interval_hours == 24

    def test_is_frozen(self) -> None:
        cfg = LearningConfig()
        with pytest.raises(Exception):
            cfg.enabled = False  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            LearningConfig.model_validate({"unknownField": True})

    def test_camelcase_aliases(self) -> None:
        cfg = LearningConfig.model_validate(
            {"reflectionEnabled": False, "injectionEnabled": True}
        )
        assert cfg.reflection_enabled is False
        assert cfg.injection_enabled is True

    def test_effective_helpers_default(self) -> None:
        cfg = LearningConfig()
        assert cfg.reflection_effective is True
        assert cfg.dashboard_effective is True
        assert cfg.telemetry_effective is True
        assert cfg.injection_effective is False
        assert cfg.live_effective is False
        assert cfg.llm_labeler_effective is False

    def test_master_off_forces_all_effective_off(self) -> None:
        cfg = LearningConfig(enabled=False, injection_enabled=True, live_enabled=True)
        assert cfg.reflection_effective is False
        assert cfg.dashboard_effective is False
        assert cfg.telemetry_effective is False
        assert cfg.injection_effective is False
        assert cfg.live_effective is False
        assert cfg.llm_labeler_effective is False


# ---------------------------------------------------------------------------
# 2. resolve_learning_config precedence
# ---------------------------------------------------------------------------


class TestResolutionPrecedence:
    def test_empty_env_uses_opt_out_defaults(self) -> None:
        cfg = resolve_learning_config(env={})
        assert cfg.enabled is True
        assert cfg.reflection_enabled is True
        assert cfg.dashboard_enabled is True
        assert cfg.telemetry_enabled is True
        assert cfg.labeler == "deterministic"
        assert cfg.injection_enabled is False
        assert cfg.live_enabled is False

    def test_env_forces_safe_tier_off(self) -> None:
        cfg = resolve_learning_config(env={ENV_REFLECTION: "false"})
        assert cfg.reflection_enabled is False

    def test_env_forces_opt_in_tier_on(self) -> None:
        cfg = resolve_learning_config(
            env={ENV_INJECTION: "1", ENV_LIVE: "true"}
        )
        assert cfg.injection_enabled is True
        assert cfg.live_enabled is True

    def test_master_env_off(self) -> None:
        cfg = resolve_learning_config(env={ENV_MASTER: "off"})
        assert cfg.enabled is False

    def test_explicit_override_beats_env(self) -> None:
        cfg = resolve_learning_config(
            env={ENV_REFLECTION: "false"},
            overrides={"reflection_enabled": True},
        )
        assert cfg.reflection_enabled is True

    def test_explicit_override_beats_default(self) -> None:
        cfg = resolve_learning_config(overrides={"injection_enabled": True})
        assert cfg.injection_enabled is True

    def test_override_camelcase_key(self) -> None:
        cfg = resolve_learning_config(overrides={"liveEnabled": True})
        assert cfg.live_enabled is True

    def test_labeler_env_override(self) -> None:
        assert resolve_learning_config(env={ENV_LABELER: "llm"}).labeler == "llm"
        assert (
            resolve_learning_config(env={ENV_LABELER: "deterministic"}).labeler
            == "deterministic"
        )

    def test_labeler_unknown_token_falls_back_to_deterministic(self) -> None:
        cfg = resolve_learning_config(env={ENV_LABELER: "garbage"})
        assert cfg.labeler == "deterministic"

    def test_interval_env_override(self) -> None:
        cfg = resolve_learning_config(env={"MAGI_LEARNING_REFLECTION_INTERVAL": "6"})
        assert cfg.reflection_interval_hours == 6

    def test_interval_invalid_env_falls_back(self) -> None:
        cfg = resolve_learning_config(env={"MAGI_LEARNING_REFLECTION_INTERVAL": "-3"})
        assert cfg.reflection_interval_hours == 24

    @pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE", "On"])
    def test_truthy_tokens(self, truthy: str) -> None:
        assert resolve_learning_config(env={ENV_INJECTION: truthy}).injection_enabled

    @pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "", "FALSE"])
    def test_falsy_tokens_force_safe_tier_off(self, falsy: str) -> None:
        # A present-but-falsy value forces the field OFF (it is "explicitly set").
        cfg = resolve_learning_config(env={ENV_DASHBOARD: falsy})
        assert cfg.dashboard_enabled is False


# ---------------------------------------------------------------------------
# 3. Master enabled=false ⇒ byte-identical OFF
# ---------------------------------------------------------------------------


class TestMasterOffByteIdenticalOff:
    def test_dashboard_not_mounted_when_master_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_MASTER, "false")
        from magi_agent.transport.learning_dashboard import (
            learning_dashboard_enabled,
        )

        assert learning_dashboard_enabled() is False

    def test_reflection_disabled_noop_when_master_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_MASTER, "false")
        from magi_agent.harness.learning_executor import (
            _reflection_enabled,
            run_reflection,
        )
        from magi_agent.learning.candidates import LocalFakeTranscriptSource

        assert _reflection_enabled() is False
        source = LocalFakeTranscriptSource(traces=())
        result = asyncio.run(run_reflection(source=source))
        assert result.status == "disabled"
        assert result.candidates == ()
        assert result.counters["traces_read"] == 0
        assert result.counters["candidates_produced"] == 0

    def test_telemetry_no_emission_when_master_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_MASTER, "false")
        from magi_agent.learning import telemetry as tel

        sink: list = []
        ev = tel.emit_learning_reflection_event(
            tenant_id="tenant-a",
            candidates_produced=3,
            items_proposed=2,
            items_activated=1,
            sink=sink.append,
        )
        assert ev is None
        assert sink == []

    def test_reflection_disabled_when_reflection_gate_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Master ON but the specific safe-tier gate explicitly off.
        monkeypatch.setenv(ENV_REFLECTION, "false")
        from magi_agent.harness.learning_executor import _reflection_enabled

        assert _reflection_enabled() is False


# ---------------------------------------------------------------------------
# 4. Default (nothing set) ⇒ safe tier ON
# ---------------------------------------------------------------------------


class TestSafeTierOnByDefault:
    def test_reflection_enabled_by_default(self) -> None:
        from magi_agent.harness.learning_executor import _reflection_enabled

        assert _reflection_enabled() is True

    def test_dashboard_mounts_by_default(self) -> None:
        from magi_agent.transport.learning_dashboard import (
            learning_dashboard_enabled,
        )

        assert learning_dashboard_enabled() is True

    def test_telemetry_on_by_default(self) -> None:
        from magi_agent.learning import telemetry as tel

        sink: list = []
        ev = tel.emit_learning_reflection_event(
            tenant_id="tenant-a",
            candidates_produced=1,
            items_proposed=1,
            items_activated=0,
            sink=sink.append,
        )
        assert ev is not None
        assert len(sink) == 1

    def test_reflection_runs_ok_by_default(self) -> None:
        from magi_agent.harness.learning_executor import run_reflection
        from magi_agent.learning.candidates import LocalFakeTranscriptSource

        source = LocalFakeTranscriptSource(traces=())
        result = asyncio.run(run_reflection(source=source))
        assert result.status == "ok"

    def test_labeler_deterministic_by_default(self) -> None:
        assert resolve_learning_config(env={}).labeler == "deterministic"

    def test_injection_and_live_off_by_default(self) -> None:
        cfg = resolve_learning_config(env={})
        assert cfg.injection_enabled is False
        assert cfg.live_enabled is False


# ---------------------------------------------------------------------------
# 5. Authority tier still default-OFF; frozen Literal[False] unchanged
# ---------------------------------------------------------------------------


class TestAuthorityTierStaysOptIn:
    def test_llm_labeler_requires_opt_in(self) -> None:
        assert resolve_learning_config(env={}).llm_labeler_effective is False
        assert (
            resolve_learning_config(env={ENV_LABELER: "llm"}).llm_labeler_effective
            is True
        )

    def test_live_env_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.gates.learning_live_readiness import _live_env_enabled

        assert _live_env_enabled() is False

    def test_live_env_requires_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_LIVE, "1")
        from magi_agent.gates.learning_live_readiness import _live_env_enabled

        assert _live_env_enabled() is True

    def test_live_master_off_forces_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_LIVE, "1")
        monkeypatch.setenv(ENV_MASTER, "false")
        from magi_agent.gates.learning_live_readiness import _live_env_enabled

        assert _live_env_enabled() is False

    def test_frozen_live_authority_flag_unchanged(self) -> None:
        from magi_agent.gates.learning_live_readiness import (
            LearningLiveReadinessConfig,
        )

        field = LearningLiveReadinessConfig.model_fields["live_authority_allowed"]
        assert get_args(field.annotation) == (False,)
        # Forging True is coerced to False.
        cfg = LearningLiveReadinessConfig.model_validate(
            {"liveAuthorityAllowed": True}
        )
        assert cfg.live_authority_allowed is False

    def test_frozen_reflect_authority_flag_unchanged(self) -> None:
        from magi_agent.gates.learning_readiness import LearningReadinessConfig

        field = LearningReadinessConfig.model_fields["reflect_authority"]
        assert get_args(field.annotation) == (False,)
        cfg = LearningReadinessConfig.model_validate({"reflectAuthority": True})
        assert cfg.reflect_authority is False

    def test_frozen_executor_authority_flags_unchanged(self) -> None:
        from magi_agent.harness.learning_executor import LearningReflectionConfig

        for name in (
            "llm_attached",
            "production_write_enabled",
            "real_transcript_source_attached",
        ):
            field = LearningReflectionConfig.model_fields[name]
            assert get_args(field.annotation) == (False,)

    def test_enabling_safe_tier_does_not_flip_frozen_flags(self) -> None:
        # Safe tier ON (default) must NEVER promote any frozen attestation flag.
        from magi_agent.harness.learning_executor import LearningReflectionConfig

        cfg = LearningReflectionConfig(enabled=True)
        assert cfg.llm_attached is False
        assert cfg.production_write_enabled is False
        assert cfg.real_transcript_source_attached is False


# ---------------------------------------------------------------------------
# 6. Readiness split — reflect tier default-ready, authority tier not-ready
# ---------------------------------------------------------------------------


class TestReadinessSplit:
    def test_reflect_tier_ready_by_default(self) -> None:
        from magi_agent.gates.learning_readiness import (
            resolve_learning_reflect_tier_mode,
        )

        assert resolve_learning_reflect_tier_mode() == "reflect"

    def test_reflect_tier_disabled_when_master_off(self) -> None:
        from magi_agent.gates.learning_readiness import (
            resolve_learning_reflect_tier_mode,
        )

        cfg = resolve_learning_config(env={ENV_MASTER: "false"})
        assert resolve_learning_reflect_tier_mode(cfg) == "disabled"

    def test_reflect_tier_disabled_when_reflection_off(self) -> None:
        from magi_agent.gates.learning_readiness import (
            resolve_learning_reflect_tier_mode,
        )

        cfg = resolve_learning_config(env={ENV_REFLECTION: "false"})
        assert resolve_learning_reflect_tier_mode(cfg) == "disabled"

    def test_reflect_tier_mode_literal(self) -> None:
        from magi_agent.gates.learning_readiness import LearningReflectTierMode

        assert set(get_args(LearningReflectTierMode)) == {"disabled", "reflect"}

    def test_authority_tier_not_ready_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Authority tier (live readiness) resolves disabled by default — opt-in
        # only, and even when opted in it starts in shadow, never live, without
        # the full canary ladder.
        from magi_agent.gates.learning_live_readiness import (
            LearningLiveReadinessConfig,
            resolve_learning_live_execution_mode,
        )

        cfg = LearningLiveReadinessConfig()
        mode = resolve_learning_live_execution_mode(
            cfg, bot_id="bot-1", user_id="user-1"
        )
        assert mode == "disabled"

    def test_authority_tier_disabled_without_env_optin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.gates.learning_live_readiness import (
            LearningLiveReadinessConfig,
            learning_live_readiness_health_metadata,
        )

        # No MAGI_LEARNING_LIVE_ENABLED set ⇒ env gate disabled ⇒ disabled mode.
        cfg = LearningLiveReadinessConfig(enabled=True, shadowModeEnabled=True)
        meta = learning_live_readiness_health_metadata(
            cfg, bot_id="bot-1", user_id="user-1"
        )
        assert meta["executionMode"] == "disabled"
        assert meta["readinessReady"] is False


def test_reflect_tier_mode_rejects_bad_arg() -> None:
    from magi_agent.gates.learning_readiness import (
        resolve_learning_reflect_tier_mode,
    )

    with pytest.raises(TypeError):
        resolve_learning_reflect_tier_mode("not-a-config")  # type: ignore[arg-type]
