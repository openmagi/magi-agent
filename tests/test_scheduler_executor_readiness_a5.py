"""A5 — Scheduler-executor readiness ladder + canary + ops health/metrics + oc-cron guard.

TDD: tests are written first, implementations follow.

Coverage:
1. Readiness gate pass/fail for each reason code (disabled, kill_switch, shadow_mode,
   malformed_scope, bot_not_selected, owner_not_selected, invalid_environment,
   env_not_allowlisted, shadow_ready, canary_live_ready).
2. env_gate_disabled short-circuit (MAGI_SCHEDULER_EXECUTOR_ENABLED default off).
3. Literal[False] authority flag: live_execution_allowed locked to False.
4. resolve_scheduler_execution_mode convenience function.
5. Canary registration in api_canary_ladder — gate5 slug and readiness package.
6. Health projection — disabled / shadow / live + tick summary counts.
7. Metrics counters — correct labels, units, counter requirements list.
8. oc-cron transition guard — reject double-active, accept single-active, accept both-off.
"""
from __future__ import annotations

import hashlib
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


_BOT_ID = "186bf3d7-7d00-4c8b-86c9-c1734c66a1e4"
_USER_ID = "user-owner-0000-0000-000000000000"
_BOT_DIGEST = _sha256(_BOT_ID)
_USER_DIGEST = _sha256(_USER_ID)

_VALID_CONFIG_KWARGS = dict(
    enabled=True,
    killSwitchEnabled=False,
    shadowModeEnabled=True,
    selectedBotDigest=_BOT_DIGEST,
    selectedOwnerUserIdDigest=_USER_DIGEST,
    environment="local",
    environmentAllowlist=["local"],
    promotedGate=0,
    canaryPromotionConfirmed=False,
)


# ---------------------------------------------------------------------------
# 1. Config model — authority flag locked
# ---------------------------------------------------------------------------

class TestSchedulerExecutorReadinessConfig:
    def test_default_values_are_safe_off(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
        )
        cfg = SchedulerExecutorReadinessConfig()
        assert cfg.enabled is False
        assert cfg.kill_switch_enabled is True
        assert cfg.shadow_mode_enabled is False
        assert cfg.live_execution_allowed is False

    def test_live_execution_allowed_locked_false_even_if_truthy_value_supplied(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
        )
        # Any value is coerced to False — authority is gate-derived.
        cfg = SchedulerExecutorReadinessConfig(liveExecutionAllowed=True)  # type: ignore[arg-type]
        assert cfg.live_execution_allowed is False

    def test_environment_allowlist_coerced_from_comma_string(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
        )
        cfg = SchedulerExecutorReadinessConfig(environmentAllowlist="local,staging")
        assert cfg.environment_allowlist == ("local", "staging")

    def test_environment_allowlist_coerced_from_none(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
        )
        cfg = SchedulerExecutorReadinessConfig(environmentAllowlist=None)
        assert cfg.environment_allowlist == ()

    def test_frozen_model_rejects_mutation(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
        )
        cfg = SchedulerExecutorReadinessConfig()
        with pytest.raises(Exception):
            cfg.enabled = True  # type: ignore[misc]

    def test_promoted_gate_bounded_0_to_9(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
        )
        cfg = SchedulerExecutorReadinessConfig(promotedGate=5)
        assert cfg.promoted_gate == 5

    def test_promoted_gate_rejects_negative(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
        )
        with pytest.raises(Exception):
            SchedulerExecutorReadinessConfig(promotedGate=-1)


# ---------------------------------------------------------------------------
# 2. Reason codes + health metadata
# ---------------------------------------------------------------------------

class TestReadinessHealthMetadata:
    def _make(self, monkeypatch: pytest.MonkeyPatch | None = None, **overrides: object):
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
            scheduler_executor_readiness_health_metadata,
        )
        kwargs = {**_VALID_CONFIG_KWARGS, **overrides}
        cfg = SchedulerExecutorReadinessConfig(**kwargs)  # type: ignore[arg-type]
        if monkeypatch is not None:
            monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        return scheduler_executor_readiness_health_metadata(
            cfg, bot_id=_BOT_ID, user_id=_USER_ID
        )

    # 2a. gate_disabled
    def test_gate_disabled_when_enabled_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        meta = self._make(monkeypatch, enabled=False)
        assert meta["executionMode"] == "disabled"
        assert meta["status"] == "disabled"
        assert meta["readinessReady"] is False
        assert meta["liveExecutionAllowed"] is False
        assert "gate_disabled" in meta["reasonCodes"]

    # 2b-extra. env gate off + kill switch on → status "disabled" (not "blocked")
    def test_env_gate_off_and_kill_switch_on_yields_disabled_not_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
            scheduler_executor_readiness_health_metadata,
        )
        # kill switch explicitly on so _reason_codes returns a tuple with BOTH
        # env_gate_disabled AND kill_switch_enabled — the old code fell to "blocked".
        cfg = SchedulerExecutorReadinessConfig(
            **{**_VALID_CONFIG_KWARGS, "killSwitchEnabled": True}
        )  # type: ignore[arg-type]
        meta = scheduler_executor_readiness_health_metadata(
            cfg, bot_id=_BOT_ID, user_id=_USER_ID
        )
        assert meta["status"] == "disabled", (
            f"expected 'disabled' but got {meta['status']!r}; "
            f"reason codes: {meta['reasonCodes']}"
        )
        assert meta["executionMode"] == "disabled"
        assert "env_gate_disabled" in meta["reasonCodes"]
        assert "kill_switch_enabled" in meta["reasonCodes"]

    # 2b. env_gate_disabled (MAGI_SCHEDULER_EXECUTOR_ENABLED not set / off)
    def test_env_gate_disabled_when_env_var_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
            scheduler_executor_readiness_health_metadata,
        )
        cfg = SchedulerExecutorReadinessConfig(**_VALID_CONFIG_KWARGS)  # type: ignore[arg-type]
        meta = scheduler_executor_readiness_health_metadata(
            cfg, bot_id=_BOT_ID, user_id=_USER_ID
        )
        assert meta["executionMode"] == "disabled"
        assert "env_gate_disabled" in meta["reasonCodes"]

    def test_env_gate_enabled_when_env_var_is_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make()
        # Should progress beyond env_gate_disabled (shadow_ready given valid config)
        assert "env_gate_disabled" not in meta["reasonCodes"]

    # 2c. kill_switch_enabled — env-isolated via monkeypatch
    def test_kill_switch_blocks_to_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Env-isolated: force MAGI_SCHEDULER_EXECUTOR_ENABLED=1 so the env gate
        # is not a confound; the kill-switch alone should cause the mode to be disabled.
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make(monkeypatch, killSwitchEnabled=True)
        assert meta["executionMode"] == "disabled"
        assert "kill_switch_enabled" in meta["reasonCodes"]

    # 2d. shadow_mode_disabled
    def test_shadow_mode_disabled_blocks(self) -> None:
        meta = self._make(shadowModeEnabled=False)
        assert meta["executionMode"] == "disabled"
        assert "shadow_mode_disabled" in meta["reasonCodes"]

    # 2e. malformed_selected_scope
    def test_malformed_scope_if_bot_digest_empty(self) -> None:
        meta = self._make(selectedBotDigest="")
        assert "malformed_selected_scope" in meta["reasonCodes"]

    def test_malformed_scope_if_user_digest_not_sha256(self) -> None:
        meta = self._make(selectedOwnerUserIdDigest="not-a-digest")
        assert "malformed_selected_scope" in meta["reasonCodes"]

    # 2f. bot_not_selected
    def test_bot_not_selected_when_digest_mismatch(self) -> None:
        meta = self._make(selectedBotDigest=_sha256("other-bot"))
        assert "bot_not_selected" in meta["reasonCodes"]

    # 2g. owner_not_selected
    def test_owner_not_selected_when_digest_mismatch(self) -> None:
        meta = self._make(selectedOwnerUserIdDigest=_sha256("other-user"))
        assert "owner_not_selected" in meta["reasonCodes"]

    # 2h. invalid_environment
    def test_invalid_environment_blocked(self) -> None:
        meta = self._make(environment="forbidden-env")
        assert "invalid_environment" in meta["reasonCodes"]

    # 2i. environment_not_allowlisted
    def test_environment_not_allowlisted_blocks(self) -> None:
        meta = self._make(environment="local", environmentAllowlist=["staging"])
        assert "environment_not_allowlisted" in meta["reasonCodes"]

    # 2j. shadow_ready (full pass, no canary yet)
    def test_shadow_ready_when_all_clear_and_no_canary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make(promotedGate=0, canaryPromotionConfirmed=False)
        assert meta["executionMode"] == "shadow"
        assert meta["status"] == "shadow"
        assert meta["readinessReady"] is True
        assert meta["liveExecutionAllowed"] is False
        assert meta["reasonCodes"] == ["selected_shadow_ready"]

    # 2k. canary_live_ready (gate >= 5 AND confirmed)
    def test_canary_live_ready_when_promoted_gate_5_and_confirmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make(promotedGate=5, canaryPromotionConfirmed=True)
        assert meta["executionMode"] == "live"
        assert meta["status"] == "live"
        assert meta["liveExecutionAllowed"] is True
        assert meta["reasonCodes"] == ["selected_canary_live_ready"]

    def test_not_live_if_promoted_gate_4_even_if_confirmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make(promotedGate=4, canaryPromotionConfirmed=True)
        assert meta["executionMode"] == "shadow"
        assert meta["liveExecutionAllowed"] is False

    def test_not_live_if_gate_5_but_not_confirmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make(promotedGate=5, canaryPromotionConfirmed=False)
        assert meta["executionMode"] == "shadow"

    # 2l. Metadata fields
    def test_metadata_contains_expected_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make()
        assert "enabled" in meta
        assert "envGateEnabled" in meta
        assert "status" in meta
        assert "executionMode" in meta
        assert "readinessReady" in meta
        assert "selectedScopeMatched" in meta
        assert "promotedGate" in meta
        assert "canaryLiveGate" in meta
        assert "canaryPromotionConfirmed" in meta
        assert "liveExecutionAllowed" in meta
        assert "counterRequirements" in meta
        assert "reasonCodes" in meta

    def test_counter_requirements_surface_expected_scheduler_counters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        meta = self._make(promotedGate=5, canaryPromotionConfirmed=True)
        counters = meta["counterRequirements"]
        assert "fired" in counters
        assert "suppressed_silent" in counters
        assert "skipped" in counters
        assert "timed_out" in counters
        assert "lease_rejected" in counters


# ---------------------------------------------------------------------------
# 3. resolve_scheduler_execution_mode convenience
# ---------------------------------------------------------------------------

class TestResolveSchedulerExecutionMode:
    def test_returns_disabled_when_gate_disabled(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
            resolve_scheduler_execution_mode,
        )
        cfg = SchedulerExecutorReadinessConfig()
        mode = resolve_scheduler_execution_mode(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert mode == "disabled"

    def test_returns_shadow_for_valid_shadow_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
            resolve_scheduler_execution_mode,
        )
        cfg = SchedulerExecutorReadinessConfig(**_VALID_CONFIG_KWARGS)  # type: ignore[arg-type]
        mode = resolve_scheduler_execution_mode(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert mode == "shadow"

    def test_returns_live_for_full_canary_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
            resolve_scheduler_execution_mode,
        )
        cfg = SchedulerExecutorReadinessConfig(
            **{**_VALID_CONFIG_KWARGS, "promotedGate": 5, "canaryPromotionConfirmed": True}
        )  # type: ignore[arg-type]
        mode = resolve_scheduler_execution_mode(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert mode == "live"


# ---------------------------------------------------------------------------
# 4. Canary ladder — gate5 registration
# ---------------------------------------------------------------------------

class TestCanaryLadderGate5SchedulerRegistration:
    def test_gate5_slug_is_scheduler_cron_mission(self) -> None:
        from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

        registry = build_canary_gate_registry()
        gate5 = registry.by_id(5)
        assert gate5.slug == "gate5_scheduler_cron_mission"

    def test_gate5_default_off(self) -> None:
        from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

        registry = build_canary_gate_registry()
        gate5 = registry.by_id(5)
        assert gate5.default_off is True

    def test_gate5_readiness_package_present(self) -> None:
        from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

        registry = build_canary_gate_registry()
        gate5 = registry.by_id(5)
        pkg = gate5.readiness_package
        assert pkg is not None

    def test_gate5_readiness_package_has_scheduler_blockers(self) -> None:
        from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

        registry = build_canary_gate_registry()
        gate5 = registry.by_id(5)
        pkg = gate5.readiness_package
        assert pkg is not None
        blockers_text = " ".join(pkg.implementation_blockers).lower()
        assert "scheduler" in blockers_text or "cron" in blockers_text or "mission" in blockers_text

    def test_gate5_activation_env_references_scheduler_env_var(self) -> None:
        from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

        registry = build_canary_gate_registry()
        gate5 = registry.by_id(5)
        pkg = gate5.readiness_package
        assert pkg is not None
        env_text = " ".join(pkg.activation_env)
        assert "OPENMAGI_CANARY_GATE=5" in env_text

    def test_gate5_stop_conditions_include_double_fire_guard(self) -> None:
        from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

        registry = build_canary_gate_registry()
        gate5 = registry.by_id(5)
        pkg = gate5.readiness_package
        assert pkg is not None
        stops_text = " ".join(pkg.stop_conditions).lower()
        # Should mention protection against double fire / missing cleanup
        assert "double" in stops_text or "cleanup" in stops_text or "production cron" in stops_text

    def test_scheduler_executor_readiness_canary_live_gate_matches_gate5(self) -> None:
        """The _CANARY_LIVE_GATE constant in the readiness module must equal 5."""
        import importlib
        mod = importlib.import_module("magi_agent.gates.scheduler_executor_readiness")
        assert mod._CANARY_LIVE_GATE == 5  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 5. Ops health projection
# ---------------------------------------------------------------------------

class TestSchedulerExecutorHealthProjection:
    def test_default_runtime_ops_health_metadata_unchanged(self) -> None:
        """default_runtime_ops_health_metadata must still return the existing fields."""
        from magi_agent.ops.health import default_runtime_ops_health_metadata

        meta = default_runtime_ops_health_metadata()
        assert meta["schemaVersion"] == "openmagi.ops.health.v1"
        assert meta["enabled"] is False

    def test_scheduler_executor_health_disabled_by_default(self) -> None:
        from magi_agent.ops.health import scheduler_executor_health_projection

        proj = scheduler_executor_health_projection()
        assert proj["executorEnabled"] is False
        assert proj["shadowEnabled"] is False
        assert proj["status"] == "disabled"

    def test_scheduler_executor_health_shadow_when_env_enabled_shadow_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "1")
        from magi_agent.ops.health import scheduler_executor_health_projection

        proj = scheduler_executor_health_projection()
        assert proj["executorEnabled"] is True
        assert proj["shadowEnabled"] is True
        assert proj["status"] == "shadow"

    def test_scheduler_executor_health_shadow_when_executor_on_without_readiness_live(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "0")
        from magi_agent.ops.health import scheduler_executor_health_projection

        proj = scheduler_executor_health_projection()
        assert proj["executorEnabled"] is True
        assert proj["shadowEnabled"] is True
        assert proj["status"] == "shadow"
        assert proj["liveExecutionAllowed"] is False

    def test_scheduler_executor_health_live_requires_readiness_live(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "0")
        from magi_agent.ops.health import scheduler_executor_health_projection

        proj = scheduler_executor_health_projection(readiness_execution_mode="live")
        assert proj["executorEnabled"] is True
        assert proj["shadowEnabled"] is False
        assert proj["status"] == "live"
        assert proj["liveExecutionAllowed"] is True

    def test_scheduler_executor_health_kill_switch_forces_shadow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "0")
        monkeypatch.setenv("MAGI_SCHEDULER_KILL_SWITCH_ENABLED", "1")
        from magi_agent.ops.health import scheduler_executor_health_projection

        proj = scheduler_executor_health_projection()
        assert proj["executorEnabled"] is True
        assert proj["killSwitchEnabled"] is True
        assert proj["shadowEnabled"] is True
        assert proj["status"] == "shadow"

    def test_scheduler_executor_health_with_tick_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "1")
        from magi_agent.ops.health import scheduler_executor_health_projection

        tick_summary = {
            "lastTickUtcIso": "2026-06-03T00:00:00+00:00",
            "fired": 3,
            "suppressed_silent": 1,
            "skipped": 2,
        }
        proj = scheduler_executor_health_projection(tick_summary=tick_summary)
        assert proj["lastTickUtcIso"] == "2026-06-03T00:00:00+00:00"
        assert proj["fired"] == 3
        assert proj["suppressed_silent"] == 1
        assert proj["skipped"] == 2

    def test_scheduler_executor_health_no_tick_summary_omits_tick_fields(self) -> None:
        from magi_agent.ops.health import scheduler_executor_health_projection

        proj = scheduler_executor_health_projection()
        assert "lastTickUtcIso" not in proj
        assert "fired" not in proj

    def test_scheduler_health_merged_into_default_ops_health(self) -> None:
        from magi_agent.ops.health import (
            default_runtime_ops_health_metadata,
            scheduler_executor_health_projection,
        )

        base = default_runtime_ops_health_metadata()
        sched = scheduler_executor_health_projection()
        # The two can be merged without key collision on 'enabled'
        # (scheduler health uses its own keys)
        assert "executorEnabled" in sched
        assert "schemaVersion" in base

    def test_shadow_garbage_value_matches_harness_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """health.py shadow resolution must agree with JobExecutionConfig.from_env()
        for a garbage MAGI_SCHEDULER_SHADOW value (e.g. 'xyz')."""
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "xyz")
        from magi_agent.ops.health import scheduler_executor_health_projection
        from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

        proj = scheduler_executor_health_projection()
        cfg = JobExecutionConfig.from_env()
        # Both must agree: garbage values fail safe to shadow=True.
        assert proj["shadowEnabled"] == cfg.shadow
        assert cfg.shadow is True

    def test_ops_health_module_exports_all(self) -> None:
        """ops/health.py must declare __all__ with the two public functions."""
        import magi_agent.ops.health as health_mod

        assert hasattr(health_mod, "__all__")
        assert "default_runtime_ops_health_metadata" in health_mod.__all__
        assert "scheduler_executor_health_projection" in health_mod.__all__

    def test_healthz_payload_surfaces_scheduler_executor_projection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "0")
        monkeypatch.setenv("MAGI_SCHEDULER_KILL_SWITCH_ENABLED", "1")
        from magi_agent.config.models import BuildInfo, RuntimeConfig
        from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
        from magi_agent.transport.health import healthz_payload

        runtime = OpenMagiRuntime(
            config=RuntimeConfig(
                bot_id="bot-test",
                user_id="user-test",
                gateway_token="gateway-token",
                api_proxy_url="http://api-proxy.local",
                chat_proxy_url="http://chat-proxy.local",
                redis_url="redis://redis.local:6379/0",
                model="gpt-5.2",
                build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
            )
        )

        runtime_ops = healthz_payload(runtime)["runtimeOperations"]
        assert isinstance(runtime_ops, dict)
        scheduler = runtime_ops["schedulerExecutor"]
        assert isinstance(scheduler, dict)
        assert scheduler["killSwitchEnabled"] is True
        assert scheduler["shadowEnabled"] is True
        assert scheduler["status"] == "shadow"


# ---------------------------------------------------------------------------
# 6. Metrics counters
# ---------------------------------------------------------------------------

class TestSchedulerExecutorMetrics:
    def test_scheduler_outcome_counter_labels_defined(self) -> None:
        from magi_agent.ops.scheduler_metrics import SCHEDULER_OUTCOME_COUNTER_LABELS

        assert "fired" in SCHEDULER_OUTCOME_COUNTER_LABELS
        assert "suppressed_silent" in SCHEDULER_OUTCOME_COUNTER_LABELS
        assert "skipped" in SCHEDULER_OUTCOME_COUNTER_LABELS
        assert "timed_out" in SCHEDULER_OUTCOME_COUNTER_LABELS
        assert "lease_rejected" in SCHEDULER_OUTCOME_COUNTER_LABELS

    def test_build_scheduler_outcome_counters_returns_zero_snapshot(self) -> None:
        from magi_agent.ops.scheduler_metrics import build_scheduler_outcome_counters

        snapshot = build_scheduler_outcome_counters()
        assert snapshot["fired"] == 0
        assert snapshot["suppressed_silent"] == 0
        assert snapshot["skipped"] == 0
        assert snapshot["timed_out"] == 0
        assert snapshot["lease_rejected"] == 0

    def test_increment_counter_updates_value(self) -> None:
        from magi_agent.ops.scheduler_metrics import (
            build_scheduler_outcome_counters,
            increment_scheduler_counter,
        )

        counts = build_scheduler_outcome_counters()
        counts = increment_scheduler_counter(counts, "fired", delta=3)
        assert counts["fired"] == 3
        assert counts["skipped"] == 0

    def test_increment_counter_unknown_label_raises(self) -> None:
        from magi_agent.ops.scheduler_metrics import (
            build_scheduler_outcome_counters,
            increment_scheduler_counter,
        )

        counts = build_scheduler_outcome_counters()
        with pytest.raises((KeyError, ValueError)):
            increment_scheduler_counter(counts, "unknown_label")

    def test_scheduler_metrics_to_runtime_metric_records_returns_records(self) -> None:
        from magi_agent.ops.scheduler_metrics import (
            build_scheduler_outcome_counters,
            increment_scheduler_counter,
            scheduler_counts_to_metric_records,
        )

        _TRACE = "sha256:" + "a" * 64
        _POLICY = "sha256:" + "b" * 64

        counts = build_scheduler_outcome_counters()
        counts = increment_scheduler_counter(counts, "fired", delta=2)
        counts = increment_scheduler_counter(counts, "timed_out", delta=1)
        records = scheduler_counts_to_metric_records(
            counts, trace_digest=_TRACE, policy_snapshot_digest=_POLICY
        )
        # Should produce a record per non-zero counter.
        # Metric names use the ops. prefix required by RuntimeMetricRecord's
        # SAFE_METRIC_RE validator (must match ^ops\.[a-z][a-z0-9_.-]{0,80}$).
        metric_names = [r.metric_name for r in records]
        assert "ops.scheduler.outcome.fired" in metric_names
        assert "ops.scheduler.outcome.timed_out" in metric_names

    def test_scheduler_metrics_unit_is_count(self) -> None:
        from magi_agent.ops.scheduler_metrics import (
            build_scheduler_outcome_counters,
            increment_scheduler_counter,
            scheduler_counts_to_metric_records,
        )

        _TRACE = "sha256:" + "a" * 64
        _POLICY = "sha256:" + "b" * 64

        counts = build_scheduler_outcome_counters()
        counts = increment_scheduler_counter(counts, "fired")
        records = scheduler_counts_to_metric_records(
            counts, trace_digest=_TRACE, policy_snapshot_digest=_POLICY
        )
        for rec in records:
            assert rec.unit == "count"


# ---------------------------------------------------------------------------
# 7. oc-cron transition guard
# ---------------------------------------------------------------------------

class TestOcCronTransitionGuard:
    def test_guard_accepts_both_off(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard,
        )

        result = check_oc_cron_transition_guard(
            oss_scheduler_enabled=False,
            oc_cron_active=False,
        )
        assert result["safe"] is True
        assert result["conflict"] is False

    def test_guard_accepts_oss_only_active(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard,
        )

        result = check_oc_cron_transition_guard(
            oss_scheduler_enabled=True,
            oc_cron_active=False,
        )
        assert result["safe"] is True
        assert result["conflict"] is False

    def test_guard_accepts_oc_cron_only_active(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard,
        )

        result = check_oc_cron_transition_guard(
            oss_scheduler_enabled=False,
            oc_cron_active=True,
        )
        assert result["safe"] is True
        assert result["conflict"] is False

    def test_guard_rejects_double_active(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard,
        )

        result = check_oc_cron_transition_guard(
            oss_scheduler_enabled=True,
            oc_cron_active=True,
        )
        assert result["safe"] is False
        assert result["conflict"] is True
        assert "double" in result["reason"].lower() or "both" in result["reason"].lower()

    def test_guard_from_env_both_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_OC_CRON_ACTIVE", raising=False)
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard_from_env,
        )

        result = check_oc_cron_transition_guard_from_env()
        assert result["safe"] is True

    def test_guard_from_env_oss_on_oc_cron_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.delenv("MAGI_OC_CRON_ACTIVE", raising=False)
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard_from_env,
        )

        result = check_oc_cron_transition_guard_from_env()
        assert result["safe"] is True

    def test_guard_from_env_conflict_when_both_envs_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_OC_CRON_ACTIVE", "1")
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard_from_env,
        )

        result = check_oc_cron_transition_guard_from_env()
        assert result["safe"] is False
        assert result["conflict"] is True

    def test_guard_result_includes_end_state_note(self) -> None:
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard,
        )

        result = check_oc_cron_transition_guard(
            oss_scheduler_enabled=True,
            oc_cron_active=True,
        )
        # end-state doc: oc-cron → OSS replacement is mentioned
        assert "endState" in result or "end_state" in result or "note" in result


# ---------------------------------------------------------------------------
# 8. Import purity — no forbidden imports
# ---------------------------------------------------------------------------

class TestSchedulerExecutorReadinessImportPurity:
    def test_readiness_module_does_not_import_urllib(self) -> None:
        import ast, pathlib

        src = (
            pathlib.Path(__file__).parent.parent
            / "magi_agent"
            / "gates"
            / "scheduler_executor_readiness.py"
        ).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("urllib"), (
                        f"Forbidden import: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("urllib"), (
                    f"Forbidden import from: {module}"
                )

    def test_scheduler_metrics_module_does_not_import_network(self) -> None:
        import ast, pathlib

        src = (
            pathlib.Path(__file__).parent.parent
            / "magi_agent"
            / "ops"
            / "scheduler_metrics.py"
        ).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("urllib", "socket", "http", "requests")), (
                        f"Forbidden import: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(("urllib", "socket", "http", "requests")), (
                    f"Forbidden import from: {module}"
                )


# ---------------------------------------------------------------------------
# G2.6 — Health uses JobExecutionConfig.from_env() (single source of truth)
# ---------------------------------------------------------------------------

class TestHealthUsesFromEnv:
    """Verify health.py delegates to JobExecutionConfig.from_env() so that the
    health surface and the execution config cannot diverge on shadow resolution.
    """

    def test_health_agrees_with_from_env_shadow_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "1")
        from magi_agent.ops.health import scheduler_executor_health_projection
        from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

        proj = scheduler_executor_health_projection()
        cfg = JobExecutionConfig.from_env()
        assert proj["executorEnabled"] == cfg.executor_enabled
        assert proj["shadowEnabled"] == (cfg.shadow if cfg.executor_enabled else False)
        assert proj["status"] == "shadow"

    def test_health_agrees_with_from_env_shadow_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "0")
        from magi_agent.ops.health import scheduler_executor_health_projection
        from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

        proj = scheduler_executor_health_projection()
        cfg = JobExecutionConfig.from_env()
        assert proj["executorEnabled"] == cfg.executor_enabled
        assert cfg.executor_enabled is True
        assert cfg.shadow is False
        assert proj["shadowEnabled"] is True
        assert proj["status"] == "shadow"
        assert proj["liveExecutionAllowed"] is False

    def test_health_agrees_with_from_env_garbage_shadow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """health.py shadow resolution must agree with JobExecutionConfig.from_env()
        for a garbage MAGI_SCHEDULER_SHADOW value — both fail safe to shadow.
        """
        monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
        monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "xyz")
        from magi_agent.ops.health import scheduler_executor_health_projection
        from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

        proj = scheduler_executor_health_projection()
        cfg = JobExecutionConfig.from_env()
        # Both must agree: garbage values fail safe to shadow=True.
        assert proj["shadowEnabled"] == cfg.shadow
        assert cfg.shadow is True

    def test_kill_switch_blocks_to_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """test_kill_switch_blocks_to_disabled must be env-isolated via monkeypatch
        so it does not depend on ambient MAGI_SCHEDULER_EXECUTOR_ENABLED.
        """
        monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
        from magi_agent.gates.scheduler_executor_readiness import (
            SchedulerExecutorReadinessConfig,
            scheduler_executor_readiness_health_metadata,
        )
        # kill switch explicitly on with env gate off → disabled (not blocked)
        cfg = SchedulerExecutorReadinessConfig(
            **{**_VALID_CONFIG_KWARGS, "killSwitchEnabled": True}
        )  # type: ignore[arg-type]
        meta = scheduler_executor_readiness_health_metadata(
            cfg, bot_id=_BOT_ID, user_id=_USER_ID
        )
        assert meta["executionMode"] == "disabled"
        assert "kill_switch_enabled" in meta["reasonCodes"]
