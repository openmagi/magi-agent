"""Tests for the GA-live readiness gate (Track 19 PR4).

Covers the TDD-specified scenarios:
1. flag-OFF → not live (gate_disabled)
2. flag-ON + shadow stage only → shadow, not fleet-live
3. flag-ON + canary bot 186bf3d7 + minimal constraints met → canary-live
4. flag-ON + gate < 5 → not fleet-live
5. flag-ON + gate >= 5 + promotion confirmed → fleet-live
6. unknown/default → fail-closed (not live)
7. liveExecutionAllowed is Literal[False] (forged env cannot grant live)
8. telemetry helper emits a log_record dict with expected fields
"""
from __future__ import annotations

import hashlib

import pytest

from magi_agent.gates.ga_live_readiness import (
    GaLiveExecutionMode,
    GaLiveReadinessConfig,
    ga_live_readiness_health_metadata,
    resolve_ga_live_execution_mode,
)
from magi_agent.gates.ga_live_readiness import emit_ga_live_telemetry_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANARY_BOT_ID = "186bf3d7"
_CANARY_BOT_DIGEST = "sha256:" + hashlib.sha256(_CANARY_BOT_ID.encode()).hexdigest()
_OTHER_BOT_DIGEST = "sha256:" + hashlib.sha256("other-bot-id".encode()).hexdigest()
_OWNER_DIGEST = "sha256:" + hashlib.sha256("owner-user-id".encode()).hexdigest()


def _config(**kwargs: object) -> GaLiveReadinessConfig:
    """Build a GaLiveReadinessConfig with safe defaults that would be
    *shadow-ready* if enabled, kill-switch off, and scope matched."""
    defaults: dict[str, object] = {
        "enabled": True,
        "killSwitchEnabled": False,
        "shadowModeEnabled": True,
        "selectedBotDigest": _CANARY_BOT_DIGEST,
        "selectedOwnerUserIdDigest": _OWNER_DIGEST,
        "environment": "production",
        "environmentAllowlist": ("production",),
        "promotedGate": 0,
        "canaryPromotionConfirmed": False,
    }
    defaults.update(kwargs)
    return GaLiveReadinessConfig.model_validate(defaults)


# ---------------------------------------------------------------------------
# 1. Flag-OFF → disabled
# ---------------------------------------------------------------------------

def test_flag_off_returns_disabled() -> None:
    """With flag disabled, mode is 'disabled' and live is not allowed."""
    config = _config(enabled=False)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "disabled"
    assert meta["liveExecutionAllowed"] is False
    assert meta["readinessReady"] is False
    assert "gate_disabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# 2. Flag-ON + shadow stage → shadow, not fleet-live
# ---------------------------------------------------------------------------

def test_flag_on_shadow_stage_only_not_fleet_live() -> None:
    """With flag ON and shadow stage (promotedGate=0, not confirmed), mode is shadow."""
    config = _config(promotedGate=0, canaryPromotionConfirmed=False)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "shadow"
    assert meta["liveExecutionAllowed"] is False
    assert meta["readinessReady"] is True


def test_resolve_shadow_mode() -> None:
    config = _config(promotedGate=0, canaryPromotionConfirmed=False)
    mode = resolve_ga_live_execution_mode(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert mode == "shadow"


# ---------------------------------------------------------------------------
# 3. Flag-ON + canary bot 186bf3d7 + gate>=5 + confirmed → canary-live
# ---------------------------------------------------------------------------

def test_canary_bot_with_gate5_and_confirmed_is_live() -> None:
    """Canary bot 186bf3d7 with promotedGate>=5 and confirmed → live."""
    config = _config(
        selectedBotDigest=_CANARY_BOT_DIGEST,
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "live"
    assert meta["liveExecutionAllowed"] is True
    assert meta["selectedScopeMatched"] is True
    assert meta["reasonCodes"] == ["selected_canary_live_ready"]


def test_canary_bot_without_confirmation_is_not_live() -> None:
    """Canary bot with gate>=5 but canaryPromotionConfirmed=False → shadow only."""
    config = _config(
        selectedBotDigest=_CANARY_BOT_DIGEST,
        promotedGate=5,
        canaryPromotionConfirmed=False,
    )
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "shadow"
    assert meta["liveExecutionAllowed"] is False


# ---------------------------------------------------------------------------
# 4. Flag-ON + gate < 5 → not fleet-live
# ---------------------------------------------------------------------------

def test_gate_below_5_is_not_fleet_live() -> None:
    """promotedGate=4 (below _CANARY_LIVE_GATE=5) → shadow even when confirmed."""
    config = _config(promotedGate=4, canaryPromotionConfirmed=True)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "shadow"
    assert meta["liveExecutionAllowed"] is False


def test_gate_zero_is_shadow_not_live() -> None:
    config = _config(promotedGate=0, canaryPromotionConfirmed=True)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "shadow"
    assert meta["liveExecutionAllowed"] is False


# ---------------------------------------------------------------------------
# 5. Flag-ON + gate >= 5 → fleet-live (when all other conditions met)
# ---------------------------------------------------------------------------

def test_gate_5_and_confirmed_is_live() -> None:
    config = _config(promotedGate=5, canaryPromotionConfirmed=True)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "live"
    assert meta["liveExecutionAllowed"] is True


def test_gate_above_5_is_also_live() -> None:
    config = _config(promotedGate=7, canaryPromotionConfirmed=True)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["executionMode"] == "live"
    assert meta["liveExecutionAllowed"] is True


# ---------------------------------------------------------------------------
# 6. Unknown / default → fail-closed (disabled or blocked)
# ---------------------------------------------------------------------------

def test_unknown_default_config_fails_closed() -> None:
    """Default-constructed config (enabled=False) is fail-closed."""
    config = GaLiveReadinessConfig()
    meta = ga_live_readiness_health_metadata(
        config, bot_id="some-bot", user_id="some-user"
    )
    assert meta["liveExecutionAllowed"] is False
    assert meta["executionMode"] in {"disabled", "blocked"}


def test_missing_scope_digest_fails_closed() -> None:
    """Malformed digest → blocked (fail-closed, not live).

    Both selectedBotDigest and selectedOwnerUserIdDigest default to empty string,
    which fails the digest format check and triggers malformed_selected_scope.
    """
    config = GaLiveReadinessConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "shadowModeEnabled": True,
            # selectedBotDigest and selectedOwnerUserIdDigest both default to empty string
            "environment": "production",
            "environmentAllowlist": ("production",),
        }
    )
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["liveExecutionAllowed"] is False
    assert meta["executionMode"] in {"disabled", "blocked"}
    assert "malformed_selected_scope" in meta["reasonCodes"]


def test_kill_switch_fails_closed() -> None:
    """Kill switch active → blocked."""
    config = _config(killSwitchEnabled=True, promotedGate=5, canaryPromotionConfirmed=True)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["liveExecutionAllowed"] is False
    assert "kill_switch_enabled" in meta["reasonCodes"]


def test_shadow_disabled_fails_closed() -> None:
    """shadowModeEnabled=False → blocked (shadow is prerequisite)."""
    config = _config(shadowModeEnabled=False)
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["liveExecutionAllowed"] is False
    assert "shadow_mode_disabled" in meta["reasonCodes"]


def test_non_selected_bot_fails_closed() -> None:
    """Bot not in selected scope → blocked."""
    config = _config(
        selectedBotDigest=_OTHER_BOT_DIGEST,
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["liveExecutionAllowed"] is False
    assert "bot_not_selected" in meta["reasonCodes"]


def test_non_selected_owner_fails_closed() -> None:
    """Owner user_id not in selected scope → blocked."""
    wrong_owner_digest = "sha256:" + hashlib.sha256("wrong-owner-id".encode()).hexdigest()
    config = _config(
        selectedOwnerUserIdDigest=wrong_owner_digest,
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["liveExecutionAllowed"] is False
    assert "owner_not_selected" in meta["reasonCodes"]


def test_environment_not_allowlisted_fails_closed() -> None:
    """Environment not in allowlist → blocked."""
    config = _config(
        environment="production",
        environmentAllowlist=("staging",),  # production not in list
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["liveExecutionAllowed"] is False
    assert "environment_not_allowlisted" in meta["reasonCodes"]


def test_invalid_environment_fails_closed() -> None:
    """Unknown environment not in safe-environments allowlist → blocked."""
    config = _config(
        environment="unknown-env",
        environmentAllowlist=("unknown-env",),
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert meta["liveExecutionAllowed"] is False
    assert "invalid_environment" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# 7. liveExecutionAllowed is Literal[False] — forged env cannot grant live
# ---------------------------------------------------------------------------

def test_live_execution_allowed_locked_to_false_in_config() -> None:
    """Config field liveExecutionAllowed is locked to Literal[False] regardless of input."""
    # Attempt to forge liveExecutionAllowed=True in the config model
    config = GaLiveReadinessConfig.model_validate(
        {"liveExecutionAllowed": True}  # type: ignore[dict-item]
    )
    assert config.live_execution_allowed is False


def test_live_execution_allowed_serializes_as_false() -> None:
    config = GaLiveReadinessConfig()
    dumped = config.model_dump(by_alias=True)
    assert dumped["liveExecutionAllowed"] is False


# ---------------------------------------------------------------------------
# 8. Counter requirements surfaced in health metadata
# ---------------------------------------------------------------------------

def test_health_metadata_exposes_counter_requirements() -> None:
    """counterRequirements must be present so ops dashboards know what to expect."""
    config = _config()
    meta = ga_live_readiness_health_metadata(
        config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id"
    )
    assert "counterRequirements" in meta
    counters = meta["counterRequirements"]
    assert isinstance(counters, list)
    assert len(counters) >= 3
    # Must include the key operational counters
    assert "gatedCalls" in counters
    assert "completionVerifierRepairs" in counters


# ---------------------------------------------------------------------------
# 9. Telemetry helper emits a log_record dict
# ---------------------------------------------------------------------------

def test_emit_ga_live_telemetry_record_returns_dict() -> None:
    """emit_ga_live_telemetry_record returns a log_record dict with expected keys."""
    record = emit_ga_live_telemetry_record(
        event="gated_call",
        decision="allow",
        bot_id=_CANARY_BOT_ID,
        execution_mode="live",
    )
    assert isinstance(record, dict)
    assert record["level"] == "info"
    assert "ga_live" in record["message"]
    assert record.get("event") == "gated_call"
    assert record.get("decision") == "allow"
    assert record.get("botId") == _CANARY_BOT_ID
    assert record.get("executionMode") == "live"


def test_emit_ga_live_telemetry_record_block() -> None:
    record = emit_ga_live_telemetry_record(
        event="gated_call",
        decision="deny",
        bot_id="some-bot",
        execution_mode="shadow",
    )
    assert record["event"] == "gated_call"
    assert record["decision"] == "deny"
    assert record["executionMode"] == "shadow"


def test_emit_ga_live_telemetry_record_completion_verifier_repair() -> None:
    record = emit_ga_live_telemetry_record(
        event="completion_verifier_repair",
        decision="repair",
        bot_id="some-bot",
        execution_mode="live",
        detail="missing_artifact_receipt",
    )
    assert record["event"] == "completion_verifier_repair"
    assert record.get("detail") == "missing_artifact_receipt"


# ---------------------------------------------------------------------------
# 10. resolve_ga_live_execution_mode convenience function
# ---------------------------------------------------------------------------

def test_resolve_mode_disabled() -> None:
    config = _config(enabled=False)
    assert (
        resolve_ga_live_execution_mode(config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id")
        == "disabled"
    )


def test_resolve_mode_live() -> None:
    config = _config(promotedGate=5, canaryPromotionConfirmed=True)
    assert (
        resolve_ga_live_execution_mode(config, bot_id=_CANARY_BOT_ID, user_id="owner-user-id")
        == "live"
    )


# ---------------------------------------------------------------------------
# 11. Canary live gate constant is 5
# ---------------------------------------------------------------------------

def test_canary_live_gate_is_5() -> None:
    from magi_agent.gates.ga_live_readiness import _CANARY_LIVE_GATE
    assert _CANARY_LIVE_GATE == 5


# ---------------------------------------------------------------------------
# 12. general_automation_live_enabled reuse (flag source of truth)
# ---------------------------------------------------------------------------

def test_ga_live_enabled_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_GA_LIVE_ENABLED absent → False (default OFF)."""
    monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
    from magi_agent.config.env import general_automation_live_enabled
    assert general_automation_live_enabled() is False


def test_ga_live_enabled_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    from magi_agent.config.env import general_automation_live_enabled
    assert general_automation_live_enabled() is True


def test_ga_live_enabled_flag_truthy_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.config.env import general_automation_live_enabled
    for val in ("true", "yes", "on", "1"):
        monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", val)
        assert general_automation_live_enabled() is True, f"Expected True for {val!r}"
