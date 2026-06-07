"""C4 — Self-review readiness ladder + telemetry: TDD test suite.

Tests
-----
1.  Default config → gate_disabled reason, disabled status.
2.  Enabled + env gate OFF → env_gate_disabled, status=disabled (not blocked).
3.  Enabled + env ON + kill_switch ON → kill_switch_enabled in reasons, blocked.
4.  Enabled + env ON + kill_switch OFF + shadow disabled → shadow_mode_disabled.
5.  Enabled + env ON + kill_switch OFF + shadow ON + malformed scope → malformed_selected_scope.
6.  Enabled + env ON + kill_switch OFF + shadow ON + wrong bot_id → bot_not_selected.
7.  Enabled + env ON + kill_switch OFF + shadow ON + wrong user_id → owner_not_selected.
8.  Enabled + env ON + kill_switch OFF + shadow ON + env not allowlisted → environment_not_allowlisted.
9.  Enabled + env ON + kill_switch OFF + shadow ON + valid scope, no canary → shadow mode.
10. Enabled + env ON + kill_switch OFF + shadow ON + valid scope + canary → live mode.
11. liveExecutionAllowed locked Literal[False] — forged True coerced to False.
12. liveExecutionAllowed serializes False regardless of internal state.
13. env gate OFF → status="disabled" not "blocked" (exact bug class fixed in A5/B5).
14. env gate OFF + kill_switch ON → still disabled (not blocked).
15. resolve_self_review_execution_mode returns correct literal.
16. Human-approval invariant: non-rule kind → invariant_holds=True regardless of approval_ref.
17. Human-approval invariant: rule kind + approval_ref present → invariant_holds=True.
18. Human-approval invariant: rule kind + no approval_ref → invariant_holds=False.
19. Human-approval invariant: rule kind + empty string approval_ref → invariant_holds=False.
20. Health metadata contains invariantCriteria with 5 expected keys.
21. Health metadata contains counterRequirements with 4 expected counters.
22. Telemetry gate OFF → emit returns None (no-op).
23. Telemetry gate ON → event emitted with correct shape and counters.
24. Telemetry: raw user_id never in event metadata; only digest.
25. Telemetry: activationEnabled=False (enforced by model).
26. selectedScopeMatched=False when gate disabled.
27. selectedScopeMatched=True only when all scope checks pass.
28. environment not in SAFE_ENVIRONMENTS → invalid_environment reason.
29. _CANARY_LIVE_GATE constant is 5 (matches precedents).
30. Config is fully frozen (immutable after construction).
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

import pytest

from magi_agent.gates.self_review_readiness import (
    _CANARY_LIVE_GATE,
    SelfReviewExecutionMode,
    SelfReviewReadinessConfig,
    check_rule_human_approval_invariant,
    emit_self_review_rollout_staging_event,
    resolve_self_review_execution_mode,
    self_review_readiness_health_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOT_ID = "bot-test-123"
USER_ID = "user-test-456"


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _shadow_ready_config() -> SelfReviewReadinessConfig:
    """A config that is one step away from shadow — all blocking reasons cleared."""
    return SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=0,
        canaryPromotionConfirmed=False,
    )


def _live_ready_config() -> SelfReviewReadinessConfig:
    """A config that satisfies all criteria for live promotion."""
    return SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=_CANARY_LIVE_GATE,
        canaryPromotionConfirmed=True,
    )


def _meta(config: SelfReviewReadinessConfig, *, env_on: bool = True) -> dict[str, Any]:
    with _env({"MAGI_SELF_REVIEW_LIVE_ENABLED": "1" if env_on else "0"}):
        return self_review_readiness_health_metadata(
            config, bot_id=BOT_ID, user_id=USER_ID
        )


class _env:
    """Context manager that temporarily sets env vars."""

    def __init__(self, overrides: dict[str, str]) -> None:
        self._overrides = overrides
        self._originals: dict[str, str | None] = {}

    def __enter__(self) -> _env:
        for k, v in self._overrides.items():
            self._originals[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *_: object) -> None:
        for k, orig in self._originals.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig


# ---------------------------------------------------------------------------
# 1. Default config → gate_disabled
# ---------------------------------------------------------------------------


def test_default_config_gate_disabled() -> None:
    """Default config (enabled=False) always resolves to gate_disabled / disabled."""
    config = SelfReviewReadinessConfig()
    meta = _meta(config, env_on=True)
    assert meta["status"] == "disabled"
    assert meta["executionMode"] == "disabled"
    assert meta["readinessReady"] is False
    assert "gate_disabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# 2. Enabled + env gate OFF → env_gate_disabled, status=disabled
# ---------------------------------------------------------------------------


def test_env_gate_off_gives_disabled_not_blocked() -> None:
    """When env gate is OFF and config is enabled, status must be 'disabled' not 'blocked'."""
    # env gate OFF, everything else set up for shadow
    config = _shadow_ready_config()
    meta = _meta(config, env_on=False)
    assert meta["status"] == "disabled", (
        f"Expected 'disabled' not 'blocked' when env gate is off; got {meta['status']!r}. "
        "This is the exact bug class fixed in A5/B5."
    )
    assert meta["executionMode"] == "disabled"
    assert "env_gate_disabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# 3. kill_switch ON → blocked
# ---------------------------------------------------------------------------


def test_kill_switch_on_blocks_with_env_enabled() -> None:
    """Kill switch ON blocks even when env is on and scope matches."""
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=True,  # blocking
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = _meta(config, env_on=True)
    assert meta["status"] == "blocked"
    assert "kill_switch_enabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# 4. Shadow disabled → shadow_mode_disabled
# ---------------------------------------------------------------------------


def test_shadow_mode_disabled_reason() -> None:
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=False,  # shadow not enabled
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = _meta(config, env_on=True)
    assert "shadow_mode_disabled" in meta["reasonCodes"]
    assert meta["status"] == "blocked"


# ---------------------------------------------------------------------------
# 5. Malformed scope
# ---------------------------------------------------------------------------


def test_malformed_scope_reason() -> None:
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest="not-a-valid-digest",  # malformed
        selectedOwnerUserIdDigest="also-not-valid",
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = _meta(config, env_on=True)
    assert "malformed_selected_scope" in meta["reasonCodes"]
    assert meta["status"] == "blocked"


# ---------------------------------------------------------------------------
# 6. Wrong bot_id
# ---------------------------------------------------------------------------


def test_wrong_bot_id_reason() -> None:
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("different-bot"),  # wrong bot
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = _meta(config, env_on=True)
    assert "bot_not_selected" in meta["reasonCodes"]
    assert meta["status"] == "blocked"


# ---------------------------------------------------------------------------
# 7. Wrong user_id
# ---------------------------------------------------------------------------


def test_wrong_user_id_reason() -> None:
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256("different-user"),  # wrong user
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = _meta(config, env_on=True)
    assert "owner_not_selected" in meta["reasonCodes"]
    assert meta["status"] == "blocked"


# ---------------------------------------------------------------------------
# 8. Environment not allowlisted
# ---------------------------------------------------------------------------


def test_environment_not_allowlisted_reason() -> None:
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("staging",),  # local not in allowlist
    )
    meta = _meta(config, env_on=True)
    assert "environment_not_allowlisted" in meta["reasonCodes"]
    assert meta["status"] == "blocked"


# ---------------------------------------------------------------------------
# 9. Valid scope, no canary → shadow
# ---------------------------------------------------------------------------


def test_shadow_mode_resolved() -> None:
    config = _shadow_ready_config()
    meta = _meta(config, env_on=True)
    assert meta["status"] == "shadow"
    assert meta["executionMode"] == "shadow"
    assert meta["readinessReady"] is True
    assert meta["reasonCodes"] == ["selected_shadow_ready"]
    assert meta["liveExecutionAllowed"] is False


# ---------------------------------------------------------------------------
# 10. Canary ready → live
# ---------------------------------------------------------------------------


def test_live_mode_resolved() -> None:
    config = _live_ready_config()
    meta = _meta(config, env_on=True)
    assert meta["status"] == "live"
    assert meta["executionMode"] == "live"
    assert meta["readinessReady"] is True
    assert meta["reasonCodes"] == ["selected_canary_live_ready"]
    assert meta["liveExecutionAllowed"] is True


# ---------------------------------------------------------------------------
# 11. liveExecutionAllowed locked Literal[False]
# ---------------------------------------------------------------------------


def test_live_execution_allowed_locked_false() -> None:
    """Forged True must be coerced to False by the validator."""
    config = SelfReviewReadinessConfig.model_validate(
        {"liveExecutionAllowed": True}  # attempt to forge True
    )
    assert config.live_execution_allowed is False


# ---------------------------------------------------------------------------
# 12. liveExecutionAllowed serializes False
# ---------------------------------------------------------------------------


def test_live_execution_allowed_serializes_false() -> None:
    """Even if somehow a truthy value is stored, serialization returns False."""
    config = _live_ready_config()
    dumped = config.model_dump(by_alias=True)
    assert dumped["liveExecutionAllowed"] is False


# ---------------------------------------------------------------------------
# 13. Env gate OFF → exactly "disabled", never "blocked"
# ---------------------------------------------------------------------------


def test_env_gate_off_only_disabled_status_variants() -> None:
    """All config variants with env gate OFF must return status='disabled'."""
    # Even when kill_switch is also ON and other reasons accumulate,
    # env-gate-off should drive the status to 'disabled'.
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=True,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=_CANARY_LIVE_GATE,
        canaryPromotionConfirmed=True,
    )
    meta = _meta(config, env_on=False)
    assert meta["status"] == "disabled", (
        f"Expected 'disabled' when env gate is off, got {meta['status']!r}"
    )


# ---------------------------------------------------------------------------
# 14. Env gate OFF + kill_switch ON → still disabled
# ---------------------------------------------------------------------------


def test_env_gate_off_and_kill_switch_on_still_disabled() -> None:
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=True,
        shadowModeEnabled=False,
        selectedBotDigest="",
        selectedOwnerUserIdDigest="",
        environment="local",
        environmentAllowlist=(),
    )
    meta = _meta(config, env_on=False)
    assert meta["status"] == "disabled"
    assert meta["executionMode"] == "disabled"


# ---------------------------------------------------------------------------
# 15. resolve_self_review_execution_mode
# ---------------------------------------------------------------------------


def test_resolve_execution_mode_shadow() -> None:
    config = _shadow_ready_config()
    with _env({"MAGI_SELF_REVIEW_LIVE_ENABLED": "1"}):
        mode = resolve_self_review_execution_mode(
            config, bot_id=BOT_ID, user_id=USER_ID
        )
    assert mode == "shadow"


def test_resolve_execution_mode_live() -> None:
    config = _live_ready_config()
    with _env({"MAGI_SELF_REVIEW_LIVE_ENABLED": "1"}):
        mode = resolve_self_review_execution_mode(
            config, bot_id=BOT_ID, user_id=USER_ID
        )
    assert mode == "live"


def test_resolve_execution_mode_disabled() -> None:
    config = SelfReviewReadinessConfig()
    with _env({"MAGI_SELF_REVIEW_LIVE_ENABLED": "1"}):
        mode = resolve_self_review_execution_mode(
            config, bot_id=BOT_ID, user_id=USER_ID
        )
    assert mode == "disabled"


# ---------------------------------------------------------------------------
# 16. Human-approval invariant: non-rule kind
# ---------------------------------------------------------------------------


def test_approval_invariant_non_rule_no_approval_needed() -> None:
    result = check_rule_human_approval_invariant(
        candidate_kind="example",
        approval_ref=None,
    )
    assert result["invariant_holds"] is True
    assert result["kind"] == "example"


def test_approval_invariant_non_rule_any_approval_ref() -> None:
    result = check_rule_human_approval_invariant(
        candidate_kind="eval",
        approval_ref="some-ref",
    )
    assert result["invariant_holds"] is True


# ---------------------------------------------------------------------------
# 17. Human-approval invariant: rule + approval_ref present
# ---------------------------------------------------------------------------


def test_approval_invariant_rule_with_approval_ref() -> None:
    result = check_rule_human_approval_invariant(
        candidate_kind="rule",
        approval_ref="human-approved-12345",
    )
    assert result["invariant_holds"] is True
    assert result["kind"] == "rule"
    assert result["approval_ref"] == "human-approved-12345"


# ---------------------------------------------------------------------------
# 18. Human-approval invariant: rule + no approval_ref
# ---------------------------------------------------------------------------


def test_approval_invariant_rule_without_approval_ref_fails() -> None:
    result = check_rule_human_approval_invariant(
        candidate_kind="rule",
        approval_ref=None,
    )
    assert result["invariant_holds"] is False
    assert result["kind"] == "rule"
    assert result["approval_ref"] is None
    # Reason should mention the policy
    assert "approval_ref" in result["reason"]
    assert "no-direct-mutation" in result["reason"]


# ---------------------------------------------------------------------------
# 19. Human-approval invariant: rule + empty string
# ---------------------------------------------------------------------------


def test_approval_invariant_rule_empty_string_fails() -> None:
    result = check_rule_human_approval_invariant(
        candidate_kind="rule",
        approval_ref="",
    )
    assert result["invariant_holds"] is False


def test_approval_invariant_rule_whitespace_only_fails() -> None:
    result = check_rule_human_approval_invariant(
        candidate_kind="rule",
        approval_ref="   ",
    )
    assert result["invariant_holds"] is False


# ---------------------------------------------------------------------------
# 20. invariantCriteria in metadata
# ---------------------------------------------------------------------------


def test_health_metadata_invariant_criteria() -> None:
    config = _shadow_ready_config()
    meta = _meta(config, env_on=True)
    criteria = meta["invariantCriteria"]
    assert isinstance(criteria, list)
    expected = {
        "c1_fork_cache_untouched",
        "c1_restricted_toolset_enforced",
        "c2_eval_gate_thresholds_not_weakened",
        "c2_rule_no_auto_activate_without_approval_ref",
        "c3_archive_only_snapshot_backed",
    }
    assert set(criteria) == expected, (
        f"invariantCriteria mismatch: got {set(criteria)}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# 21. counterRequirements in metadata
# ---------------------------------------------------------------------------


def test_health_metadata_counter_requirements() -> None:
    config = SelfReviewReadinessConfig()
    meta = _meta(config, env_on=True)
    counters = meta["counterRequirements"]
    assert isinstance(counters, list)
    expected = {
        "candidates_proposed",
        "examples_auto_activated",
        "rules_pending_approval",
        "curator_archived",
    }
    assert set(counters) == expected, (
        f"counterRequirements mismatch: got {set(counters)}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# 22. Telemetry gate OFF → None
# ---------------------------------------------------------------------------


def test_telemetry_gate_off_returns_none() -> None:
    received: list[Any] = []
    # No MAGI_SELF_REVIEW_TELEMETRY_ENABLED in env → default OFF
    env_patch = {"MAGI_SELF_REVIEW_TELEMETRY_ENABLED": "0"}
    with _env(env_patch):
        result = emit_self_review_rollout_staging_event(
            tenant_id="test-tenant",
            bot_id="test-bot",
            execution_mode="shadow",
            promoted_gate=0,
            canary_live_gate=_CANARY_LIVE_GATE,
            user_id_digest=_sha256(USER_ID),
            sink=received.append,
        )
    assert result is None
    assert received == []


# ---------------------------------------------------------------------------
# 23. Telemetry gate ON → event emitted
# ---------------------------------------------------------------------------


def test_telemetry_gate_on_emits_event() -> None:
    received: list[Any] = []
    with _env({"MAGI_SELF_REVIEW_TELEMETRY_ENABLED": "1"}):
        result = emit_self_review_rollout_staging_event(
            tenant_id="test-tenant",
            bot_id="test-bot",
            execution_mode="shadow",
            promoted_gate=0,
            canary_live_gate=_CANARY_LIVE_GATE,
            user_id_digest=_sha256(USER_ID),
            candidates_proposed=5,
            examples_auto_activated=2,
            rules_pending_approval=1,
            curator_archived=3,
            sink=received.append,
        )
    assert result is not None
    assert len(received) == 1
    event = received[0]
    dumped = event.model_dump(by_alias=True, mode="json")
    meta = dumped["metadata"]
    assert meta["executionMode"] == "shadow"
    assert meta["candidatesProposed"] == 5
    assert meta["examplesAutoActivated"] == 2
    assert meta["rulesPendingApproval"] == 1
    assert meta["curatorArchived"] == 3
    assert meta["canaryLiveGate"] == _CANARY_LIVE_GATE


# ---------------------------------------------------------------------------
# 24. Telemetry: raw user_id never in event
# ---------------------------------------------------------------------------


def test_telemetry_no_raw_user_id() -> None:
    """Raw USER_ID must not appear anywhere in the emitted event metadata."""
    received: list[Any] = []
    with _env({"MAGI_SELF_REVIEW_TELEMETRY_ENABLED": "1"}):
        emit_self_review_rollout_staging_event(
            tenant_id="test-tenant",
            bot_id="test-bot",
            execution_mode="live",
            promoted_gate=_CANARY_LIVE_GATE,
            canary_live_gate=_CANARY_LIVE_GATE,
            user_id_digest=_sha256(USER_ID),
            sink=received.append,
        )
    assert received
    dumped = received[0].model_dump(by_alias=True, mode="json")
    # Raw USER_ID must never appear — only the digest
    assert USER_ID not in str(dumped), (
        f"Raw user_id {USER_ID!r} found in telemetry event — PII leak!"
    )
    # The digest must be present
    assert _sha256(USER_ID) in str(dumped)


# ---------------------------------------------------------------------------
# 25. Telemetry: activationEnabled=False
# ---------------------------------------------------------------------------


def test_telemetry_activation_enabled_false() -> None:
    received: list[Any] = []
    with _env({"MAGI_SELF_REVIEW_TELEMETRY_ENABLED": "1"}):
        emit_self_review_rollout_staging_event(
            tenant_id="test-tenant",
            bot_id="test-bot",
            execution_mode="disabled",
            promoted_gate=0,
            canary_live_gate=_CANARY_LIVE_GATE,
            user_id_digest=_sha256(USER_ID),
            sink=received.append,
        )
    assert received
    dumped = received[0].model_dump(by_alias=True, mode="json")
    # activationEnabled field is enforced False by the DeterministicRuntimeEvent model
    assert dumped.get("activationEnabled") is False


# ---------------------------------------------------------------------------
# 26. selectedScopeMatched=False when gate disabled
# ---------------------------------------------------------------------------


def test_selected_scope_matched_false_when_disabled() -> None:
    config = SelfReviewReadinessConfig()  # enabled=False
    meta = _meta(config, env_on=True)
    assert meta["selectedScopeMatched"] is False


# ---------------------------------------------------------------------------
# 27. selectedScopeMatched=True when all checks pass
# ---------------------------------------------------------------------------


def test_selected_scope_matched_true_when_all_pass() -> None:
    config = _shadow_ready_config()
    meta = _meta(config, env_on=True)
    assert meta["selectedScopeMatched"] is True


# ---------------------------------------------------------------------------
# 28. Invalid environment
# ---------------------------------------------------------------------------


def test_invalid_environment_reason() -> None:
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="unknown-env",  # not in SAFE_ENVIRONMENTS
        environmentAllowlist=("unknown-env",),
    )
    meta = _meta(config, env_on=True)
    assert "invalid_environment" in meta["reasonCodes"]
    assert meta["status"] == "blocked"


# ---------------------------------------------------------------------------
# 29. _CANARY_LIVE_GATE == 5
# ---------------------------------------------------------------------------


def test_canary_live_gate_constant() -> None:
    assert _CANARY_LIVE_GATE == 5, (
        f"_CANARY_LIVE_GATE must be 5 (matches precedents); got {_CANARY_LIVE_GATE}"
    )


# ---------------------------------------------------------------------------
# 30. Config is frozen
# ---------------------------------------------------------------------------


def test_config_is_frozen() -> None:
    config = SelfReviewReadinessConfig()
    with pytest.raises((TypeError, Exception)):
        config.enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_promoted_gate_below_canary_stays_shadow() -> None:
    """Gate below _CANARY_LIVE_GATE with canary_confirmed → still shadow."""
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=_CANARY_LIVE_GATE - 1,  # one below threshold
        canaryPromotionConfirmed=True,
    )
    meta = _meta(config, env_on=True)
    assert meta["executionMode"] == "shadow"
    assert meta["liveExecutionAllowed"] is False


def test_promoted_gate_at_canary_but_not_confirmed_stays_shadow() -> None:
    """Gate at threshold but canary_confirmed=False → still shadow."""
    config = SelfReviewReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(BOT_ID),
        selectedOwnerUserIdDigest=_sha256(USER_ID),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=_CANARY_LIVE_GATE,
        canaryPromotionConfirmed=False,  # not confirmed
    )
    meta = _meta(config, env_on=True)
    assert meta["executionMode"] == "shadow"
    assert meta["liveExecutionAllowed"] is False


def test_health_metadata_enabled_gate_fields() -> None:
    """enabled/envGateEnabled/promotedGate/canaryLiveGate are always present."""
    config = SelfReviewReadinessConfig()
    meta = _meta(config, env_on=False)
    assert "enabled" in meta
    assert "envGateEnabled" in meta
    assert "promotedGate" in meta
    assert meta["canaryLiveGate"] == _CANARY_LIVE_GATE
    assert "canaryPromotionConfirmed" in meta


def test_telemetry_counters_default_zero() -> None:
    """When counter kwargs are omitted they default to 0."""
    received: list[Any] = []
    with _env({"MAGI_SELF_REVIEW_TELEMETRY_ENABLED": "1"}):
        emit_self_review_rollout_staging_event(
            tenant_id="local",
            bot_id="bot-default",
            execution_mode="disabled",
            promoted_gate=0,
            canary_live_gate=_CANARY_LIVE_GATE,
            user_id_digest=_sha256("user-default"),
            sink=received.append,
        )
    assert received
    meta = received[0].model_dump(by_alias=True, mode="json")["metadata"]
    assert meta["candidatesProposed"] == 0
    assert meta["examplesAutoActivated"] == 0
    assert meta["rulesPendingApproval"] == 0
    assert meta["curatorArchived"] == 0


def test_env_allowlist_coercion_from_string() -> None:
    """environmentAllowlist can be supplied as a comma-separated string."""
    config = SelfReviewReadinessConfig.model_validate(
        {
            "enabled": True,
            "environmentAllowlist": "local,staging",
        }
    )
    assert "local" in config.environment_allowlist
    assert "staging" in config.environment_allowlist
