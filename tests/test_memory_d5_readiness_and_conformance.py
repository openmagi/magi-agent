"""D5 — Writable-memory readiness ladder + writable-provider conformance.

TDD tests covering:

A. Readiness ladder: gate-disabled / env-off → disabled (not blocked)
B. Readiness ladder: kill-switch → blocked
C. Readiness ladder: shadow path (all gates open, no canary)
D. Readiness ladder: live path (canary promoted)
E. Readiness ladder: env allowlist + digest scope selection
F. Readiness ladder: live_execution_allowed Literal[False] lock
G. Kill-switch env var override
H. Conformance extension: passing writable provider passes all 6 invariants
I. Conformance extension: soul_not_agent_writable invariant (SOUL.md absent)
J. Conformance extension: read_only_default invariant (gated_write tier)
K. Conformance extension: path_safe_redacted_bounded invariant
L. Conformance extension: projection invariant
M. Conformance extension: soul_operator_path_separate invariant
N. Conformance extension: check_local_file_memory_provider_conformance passes
O. Canary constant reference: _CANARY_LIVE_GATE == 5
P. Governed env gates listed in health metadata
Q. Safety invariants listed in health metadata
R. Readiness enables nothing (default-OFF smoke)
"""
from __future__ import annotations

import hashlib
import os

import pytest
from pydantic import ValidationError

from magi_agent.gates.memory_write_readiness import (
    _CANARY_LIVE_GATE,
    MAGI_MEMORY_PROJECTION_ENABLED_ENV,
    MAGI_MEMORY_WRITE_ENABLED_ENV,
    MAGI_SOUL_WRITE_ENABLED_ENV,
    MemoryWriteReadinessConfig,
    memory_write_readiness_health_metadata,
    resolve_memory_write_execution_mode,
)
from magi_agent.memory.conformance import (
    WritableProviderConformanceReport,
    WritableProviderInvariantResult,
    check_local_file_memory_provider_conformance,
    check_writable_provider_conformance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _shadow_config(
    bot_id: str = "bot-a",
    user_id: str = "user-a",
    environment: str = "local",
) -> MemoryWriteReadinessConfig:
    """Return a config with all gates open for shadow (no canary)."""
    return MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(bot_id),
        selectedOwnerUserIdDigest=_sha256(user_id),
        environment=environment,
        environmentAllowlist=(environment,),
    )


def _live_config(
    bot_id: str = "bot-a",
    user_id: str = "user-a",
    environment: str = "local",
) -> MemoryWriteReadinessConfig:
    """Return a config fully promoted to live."""
    return MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256(bot_id),
        selectedOwnerUserIdDigest=_sha256(user_id),
        environment=environment,
        environmentAllowlist=(environment,),
        promotedGate=_CANARY_LIVE_GATE,
        canaryPromotionConfirmed=True,
    )


# ---------------------------------------------------------------------------
# A. Gate-disabled / env-off → disabled (not blocked)
# ---------------------------------------------------------------------------


def test_gate_disabled_returns_disabled_status() -> None:
    """When enabled=False the mode is 'disabled', not 'blocked'."""
    config = MemoryWriteReadinessConfig()
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot", user_id="user"
    )
    assert meta["executionMode"] == "disabled"
    assert meta["status"] == "disabled"
    assert "gate_disabled" in meta["reasonCodes"]


def test_env_gate_off_returns_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the env gate is off (even with enabled=True) the mode is 'disabled'."""
    monkeypatch.delenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(enabled=True)
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot", user_id="user"
    )
    # env gate is off → disabled (not blocked)
    assert meta["executionMode"] == "disabled"
    assert meta["status"] == "disabled"


def test_env_gate_off_status_is_disabled_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env_gate_disabled reason → status must be 'disabled', never 'blocked'."""
    monkeypatch.delenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=True,  # additional blocking reason
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot", user_id="user"
    )
    assert meta["status"] == "disabled"
    assert meta["executionMode"] == "disabled"


# ---------------------------------------------------------------------------
# B. Kill-switch → blocked
# ---------------------------------------------------------------------------


def test_kill_switch_enabled_returns_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """When kill switch is enabled (config) and all other gates open, status is 'blocked'."""
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=True,  # kill switch on
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("bot-x"),
        selectedOwnerUserIdDigest=_sha256("user-x"),
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-x", user_id="user-x"
    )
    assert meta["status"] == "blocked"
    assert "kill_switch_enabled" in meta["reasonCodes"]


def test_kill_switch_env_var_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED=1 triggers kill_switch even if config has it off."""
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", "1")
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,  # config says off — env overrides
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("bot-x"),
        selectedOwnerUserIdDigest=_sha256("user-x"),
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-x", user_id="user-x"
    )
    assert meta["status"] == "blocked"
    assert "kill_switch_enabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# C. Shadow path
# ---------------------------------------------------------------------------


def test_shadow_mode_returns_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = _shadow_config()
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["executionMode"] == "shadow"
    assert meta["status"] == "shadow"
    assert meta["readinessReady"] is True
    assert meta["liveExecutionAllowed"] is False
    assert meta["reasonCodes"] == ["selected_shadow_ready"]


def test_shadow_mode_selected_scope_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = _shadow_config(bot_id="bot-b", user_id="user-b")
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-b", user_id="user-b"
    )
    assert meta["selectedScopeMatched"] is True


def test_shadow_mode_wrong_bot_returns_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = _shadow_config(bot_id="bot-a")
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-WRONG", user_id="user-a"
    )
    assert meta["executionMode"] == "disabled"
    assert meta["status"] == "blocked"
    assert "bot_not_selected" in meta["reasonCodes"]


def test_shadow_mode_disabled_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=False,  # shadow off
        selectedBotDigest=_sha256("bot-a"),
        selectedOwnerUserIdDigest=_sha256("user-a"),
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["status"] == "blocked"
    assert "shadow_mode_disabled" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# D. Live path
# ---------------------------------------------------------------------------


def test_live_mode_returns_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = _live_config()
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["executionMode"] == "live"
    assert meta["status"] == "live"
    assert meta["readinessReady"] is True
    assert meta["liveExecutionAllowed"] is True
    assert meta["reasonCodes"] == ["selected_canary_live_ready"]


def test_live_requires_canary_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("bot-a"),
        selectedOwnerUserIdDigest=_sha256("user-a"),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=_CANARY_LIVE_GATE,
        canaryPromotionConfirmed=False,  # not confirmed
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["executionMode"] == "shadow"  # stays shadow without confirmation


def test_live_requires_gate_reaching_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("bot-a"),
        selectedOwnerUserIdDigest=_sha256("user-a"),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=_CANARY_LIVE_GATE - 1,  # one gate short
        canaryPromotionConfirmed=True,
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["executionMode"] == "shadow"


# ---------------------------------------------------------------------------
# E. Env allowlist + digest scope selection
# ---------------------------------------------------------------------------


def test_environment_not_allowlisted_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("bot-a"),
        selectedOwnerUserIdDigest=_sha256("user-a"),
        environment="local",
        environmentAllowlist=("staging",),  # local not in allowlist
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["status"] == "blocked"
    assert "environment_not_allowlisted" in meta["reasonCodes"]


def test_invalid_environment_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("bot-a"),
        selectedOwnerUserIdDigest=_sha256("user-a"),
        environment="unknown-env",  # not in SAFE_ENVIRONMENTS
        environmentAllowlist=("unknown-env",),
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["status"] == "blocked"
    assert "invalid_environment" in meta["reasonCodes"]


def test_malformed_digest_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest="not-a-valid-digest",  # malformed
        selectedOwnerUserIdDigest=_sha256("user-a"),
        environment="local",
        environmentAllowlist=("local",),
    )
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert meta["status"] == "blocked"
    assert "malformed_selected_scope" in meta["reasonCodes"]


# ---------------------------------------------------------------------------
# F. live_execution_allowed Literal[False] lock
# ---------------------------------------------------------------------------


def test_live_execution_allowed_is_literal_false_in_config() -> None:
    """live_execution_allowed in the config is always False regardless of input."""
    config = MemoryWriteReadinessConfig(liveExecutionAllowed=True)  # type: ignore[call-arg]
    assert config.live_execution_allowed is False


def test_live_execution_allowed_serializes_false() -> None:
    config = MemoryWriteReadinessConfig()
    dumped = config.model_dump(by_alias=True)
    assert dumped["liveExecutionAllowed"] is False


def test_live_execution_allowed_in_metadata_only_true_at_live_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    shadow_config = _shadow_config()
    shadow_meta = memory_write_readiness_health_metadata(
        shadow_config, bot_id="bot-a", user_id="user-a"
    )
    assert shadow_meta["liveExecutionAllowed"] is False

    live_config = _live_config()
    live_meta = memory_write_readiness_health_metadata(
        live_config, bot_id="bot-a", user_id="user-a"
    )
    assert live_meta["liveExecutionAllowed"] is True


# ---------------------------------------------------------------------------
# G. Kill-switch env var
# ---------------------------------------------------------------------------


def test_kill_switch_env_disabled_no_effect_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)
    config = _shadow_config()
    mode = resolve_memory_write_execution_mode(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert mode == "shadow"


def test_kill_switch_env_blocks_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", "true")
    config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,  # config off; env override
        shadowModeEnabled=True,
        selectedBotDigest=_sha256("bot-a"),
        selectedOwnerUserIdDigest=_sha256("user-a"),
        environment="local",
        environmentAllowlist=("local",),
    )
    mode = resolve_memory_write_execution_mode(
        config, bot_id="bot-a", user_id="user-a"
    )
    assert mode == "disabled"


# ---------------------------------------------------------------------------
# H. Conformance: passing writable provider passes all 6 invariants
# ---------------------------------------------------------------------------


def test_conforming_writable_provider_passes_all_invariants() -> None:
    report = check_writable_provider_conformance(
        provider_id="local-file-memory-writable",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md", "USER.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.all_invariants_pass is True
    assert report.invariant_result.failing_invariants == ()
    assert report.soul_in_agent_allowlist is False


# ---------------------------------------------------------------------------
# I. soul_not_agent_writable: SOUL.md must NOT be in agent allowlist
# ---------------------------------------------------------------------------


def test_soul_in_agent_allowlist_fails_invariants() -> None:
    report = check_writable_provider_conformance(
        provider_id="bad-provider",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md", "USER.md", "SOUL.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.soul_in_agent_allowlist is True
    assert report.invariant_result.soul_not_agent_writable is False
    assert "soul_not_agent_writable" in report.invariant_result.failing_invariants
    assert report.invariant_result.all_invariants_pass is False


def test_soul_absent_passes_soul_not_agent_writable() -> None:
    report = check_writable_provider_conformance(
        provider_id="good-provider",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md", "USER.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.soul_not_agent_writable is True
    assert "soul_not_agent_writable" not in report.invariant_result.failing_invariants


# ---------------------------------------------------------------------------
# J. read_only_default invariant
# ---------------------------------------------------------------------------


def test_wrong_write_tier_fails_read_only_default() -> None:
    report = check_writable_provider_conformance(
        provider_id="bad-tier",
        write_tier="unrestricted_write",  # not "gated_write"
        allowed_write_files=frozenset({"MEMORY.md", "USER.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.read_only_default is False
    assert "read_only_default" in report.invariant_result.failing_invariants


# ---------------------------------------------------------------------------
# K. path_safe_redacted_bounded invariant
# ---------------------------------------------------------------------------


def test_missing_redaction_fails_invariant() -> None:
    report = check_writable_provider_conformance(
        provider_id="no-redact",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md"}),
        has_declarative_filter=True,
        has_redaction=False,  # no redaction
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.path_safe_redacted_bounded is False
    assert "path_safe_redacted_bounded" in report.invariant_result.failing_invariants


def test_missing_byte_bound_fails_invariant() -> None:
    report = check_writable_provider_conformance(
        provider_id="no-bound",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=False,  # no byte bound
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.path_safe_redacted_bounded is False
    assert "path_safe_redacted_bounded" in report.invariant_result.failing_invariants


def test_missing_path_safety_fails_invariant() -> None:
    report = check_writable_provider_conformance(
        provider_id="no-path-safety",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=False,  # no path safety
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.path_safe_redacted_bounded is False
    assert "path_safe_redacted_bounded" in report.invariant_result.failing_invariants


# ---------------------------------------------------------------------------
# L. Projection invariant
# ---------------------------------------------------------------------------


def test_projection_not_default_off_fails_invariant() -> None:
    report = check_writable_provider_conformance(
        provider_id="bad-projection",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=False,  # not default off
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.projection_cache_safe_incognito_respecting is False
    assert (
        "projection_cache_safe_incognito_respecting"
        in report.invariant_result.failing_invariants
    )


def test_projection_incognito_not_blocked_fails_invariant() -> None:
    report = check_writable_provider_conformance(
        provider_id="bad-incognito",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=True,
        projection_default_off=True,
        projection_incognito_blocked=False,  # incognito not blocked
    )
    assert report.invariant_result.projection_cache_safe_incognito_respecting is False


# ---------------------------------------------------------------------------
# M. soul_operator_path_separate invariant
# ---------------------------------------------------------------------------


def test_no_operator_soul_gate_fails_soul_operator_path_separate() -> None:
    report = check_writable_provider_conformance(
        provider_id="no-operator-gate",
        write_tier="gated_write",
        allowed_write_files=frozenset({"MEMORY.md"}),
        has_declarative_filter=True,
        has_redaction=True,
        has_write_byte_bound=True,
        has_path_safety=True,
        has_operator_soul_gate=False,  # no operator gate
        projection_default_off=True,
        projection_incognito_blocked=True,
    )
    assert report.invariant_result.soul_operator_path_separate is False
    assert "soul_operator_path_separate" in report.invariant_result.failing_invariants


# ---------------------------------------------------------------------------
# N. check_local_file_memory_provider_conformance passes
# ---------------------------------------------------------------------------


def test_local_file_memory_provider_passes_all_invariants() -> None:
    """The canonical D1 LocalFileMemoryProvider satisfies all D1–D4 invariants."""
    report = check_local_file_memory_provider_conformance()
    assert isinstance(report, WritableProviderConformanceReport)
    assert report.invariant_result.all_invariants_pass is True
    assert report.invariant_result.failing_invariants == ()
    assert report.soul_in_agent_allowlist is False
    assert report.write_tier == "gated_write"


def test_local_file_memory_provider_soul_not_in_allowlist() -> None:
    report = check_local_file_memory_provider_conformance()
    # The tuple of allowed write files must not include SOUL.md
    assert "SOUL.md" not in report.allowed_write_files
    assert "MEMORY.md" in report.allowed_write_files
    assert "USER.md" in report.allowed_write_files


# ---------------------------------------------------------------------------
# O. Canary constant reference: _CANARY_LIVE_GATE == 5
# ---------------------------------------------------------------------------


def test_canary_live_gate_constant_is_five() -> None:
    """_CANARY_LIVE_GATE must equal 5, mirroring the precedent gates."""
    assert _CANARY_LIVE_GATE == 5


def test_health_metadata_exposes_canary_live_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    config = MemoryWriteReadinessConfig(enabled=True)
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot", user_id="user"
    )
    assert meta["canaryLiveGate"] == _CANARY_LIVE_GATE


# ---------------------------------------------------------------------------
# P. Governed env gates listed in health metadata
# ---------------------------------------------------------------------------


def test_governed_env_gates_in_metadata() -> None:
    config = MemoryWriteReadinessConfig()
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot", user_id="user"
    )
    gates = meta["governedEnvGates"]
    assert MAGI_MEMORY_WRITE_ENABLED_ENV in gates
    assert MAGI_MEMORY_PROJECTION_ENABLED_ENV in gates
    assert MAGI_SOUL_WRITE_ENABLED_ENV in gates


def test_governed_env_gates_are_the_three_d_surfaces() -> None:
    assert MAGI_MEMORY_WRITE_ENABLED_ENV == "MAGI_MEMORY_WRITE_ENABLED"
    assert MAGI_MEMORY_PROJECTION_ENABLED_ENV == "MAGI_MEMORY_PROJECTION_ENABLED"
    assert MAGI_SOUL_WRITE_ENABLED_ENV == "MAGI_SOUL_WRITE_ENABLED"


# ---------------------------------------------------------------------------
# Q. Safety invariants listed in health metadata
# ---------------------------------------------------------------------------


def test_safety_invariants_asserted_in_metadata() -> None:
    config = MemoryWriteReadinessConfig()
    meta = memory_write_readiness_health_metadata(
        config, bot_id="bot", user_id="user"
    )
    invariants = meta["safetyInvariantsAsserted"]
    assert "read_only_default" in invariants
    assert "declarative_only_filter" in invariants
    assert "path_safe_redacted_bounded" in invariants
    assert "soul_not_agent_writable" in invariants
    assert "soul_operator_path_separate" in invariants
    assert "projection_cache_safe_incognito_respecting" in invariants


# ---------------------------------------------------------------------------
# R. Readiness enables nothing (default-OFF smoke)
# ---------------------------------------------------------------------------


def test_readiness_gate_enables_nothing_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default config: all D-surface gates are still off after importing readiness."""
    monkeypatch.delenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_PROJECTION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_SOUL_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED", raising=False)

    # Importing the readiness module and building a default config must NOT
    # open any of the D-surface gates.
    config = MemoryWriteReadinessConfig()
    assert config.enabled is False
    assert config.live_execution_allowed is False

    mode = resolve_memory_write_execution_mode(
        config, bot_id="bot", user_id="user"
    )
    assert mode == "disabled"

    # D-surface env gates are still off
    assert os.environ.get("MAGI_MEMORY_WRITE_ENABLED", "0") not in {"1", "true", "yes", "on"}
    assert os.environ.get("MAGI_MEMORY_PROJECTION_ENABLED", "0") not in {"1", "true", "yes", "on"}
    assert os.environ.get("MAGI_SOUL_WRITE_ENABLED", "0") not in {"1", "true", "yes", "on"}


def test_conformance_check_enables_nothing() -> None:
    """Calling check_local_file_memory_provider_conformance must enable nothing."""
    report = check_local_file_memory_provider_conformance()
    # The report itself carries no live authority flags
    assert report.invariant_result.all_invariants_pass is True
    # Conformance is purely metadata — no env mutations, no I/O
    assert os.environ.get("MAGI_MEMORY_WRITE_ENABLED", "") == ""
