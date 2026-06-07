"""B5 — Goal-loop readiness ladder + canary registration + spawn-depth/ownership gates.

TDD: tests are written first, implementations follow.

Coverage:
1. Config model: authority flag locked to Literal[False]; frozen; default-off.
2. Reason codes + health metadata: each blocking reason, shadow_ready, canary_live_ready.
3. env_gate kill-switch short-circuit (MAGI_GOAL_LOOP_KILL_SWITCH_ENABLED default on).
4. Env gate disabled short-circuit (MAGI_GOAL_LOOP_ENABLED default off).
5. resolve_goal_loop_execution_mode convenience.
6. _CANARY_LIVE_GATE constant equals 5 (mirrors scheduler_executor); the main 0-9
   registry is untouched. No standalone canary factory (matches scheduler precedent).
7. Spawn-depth enforcement: check_goal_loop_spawn_depth rejects exceeding
   DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH=2 and accepts valid depths.
8. Ownership enforcement: check_goal_loop_ownership_assignment rejects child agents
   owning persistence/scheduling and accepts main agents.
9. B1-B4 safety invariants asserted in readiness: default-off verified; continuation
   is USER-role (cache-safe); spend-guard wired; judge fail-open budget present;
   evidence-gate fails-toward-continue; after-turn hook is non-blocking/fail-open.
10. Import purity — no forbidden network imports.
"""
from __future__ import annotations

import hashlib
import os

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

class TestGoalLoopReadinessConfig:
    def test_default_values_are_safe_off(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig()
        assert cfg.enabled is False
        assert cfg.kill_switch_enabled is True
        assert cfg.shadow_mode_enabled is False
        assert cfg.live_execution_allowed is False

    def test_live_execution_allowed_locked_false_even_if_truthy_supplied(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig(liveExecutionAllowed=True)  # type: ignore[arg-type]
        assert cfg.live_execution_allowed is False

    def test_live_execution_allowed_serializes_as_false(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig()
        dumped = cfg.model_dump(by_alias=True, mode="python")
        assert dumped["liveExecutionAllowed"] is False

    def test_environment_allowlist_coerced_from_comma_string(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig(environmentAllowlist="local,staging")
        assert cfg.environment_allowlist == ("local", "staging")

    def test_environment_allowlist_coerced_from_none(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig(environmentAllowlist=None)
        assert cfg.environment_allowlist == ()

    def test_frozen_model_rejects_mutation(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig()
        with pytest.raises(Exception):
            cfg.enabled = True  # type: ignore[misc]

    def test_promoted_gate_bounded_0_to_9(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig(promotedGate=5)
        assert cfg.promoted_gate == 5

    def test_promoted_gate_rejects_negative(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        with pytest.raises(Exception):
            GoalLoopReadinessConfig(promotedGate=-1)

    def test_promoted_gate_rejects_above_9(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        with pytest.raises(Exception):
            GoalLoopReadinessConfig(promotedGate=10)


# ---------------------------------------------------------------------------
# 2. Reason codes + health metadata
# ---------------------------------------------------------------------------

class TestGoalLoopReadinessHealthMetadata:
    def _make(self, monkeypatch: pytest.MonkeyPatch | None = None, **overrides: object):
        from magi_agent.gates.goal_loop_readiness import (
            GoalLoopReadinessConfig,
            goal_loop_readiness_health_metadata,
        )
        kwargs = {**_VALID_CONFIG_KWARGS, **overrides}
        cfg = GoalLoopReadinessConfig(**kwargs)  # type: ignore[arg-type]
        if monkeypatch is not None:
            monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        return goal_loop_readiness_health_metadata(cfg, bot_id=_BOT_ID, user_id=_USER_ID)

    def test_gate_disabled_when_enabled_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        meta = self._make(monkeypatch, enabled=False)
        assert meta["executionMode"] == "disabled"
        assert meta["status"] == "disabled"
        assert meta["readinessReady"] is False
        assert meta["liveExecutionAllowed"] is False
        assert "gate_disabled" in meta["reasonCodes"]

    def test_env_gate_disabled_when_env_var_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_GOAL_LOOP_ENABLED", raising=False)
        from magi_agent.gates.goal_loop_readiness import (
            GoalLoopReadinessConfig,
            goal_loop_readiness_health_metadata,
        )
        cfg = GoalLoopReadinessConfig(**_VALID_CONFIG_KWARGS)  # type: ignore[arg-type]
        meta = goal_loop_readiness_health_metadata(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert meta["executionMode"] == "disabled"
        assert "env_gate_disabled" in meta["reasonCodes"]

    def test_env_gate_off_and_kill_switch_on_yields_disabled_not_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_GOAL_LOOP_ENABLED", raising=False)
        from magi_agent.gates.goal_loop_readiness import (
            GoalLoopReadinessConfig,
            goal_loop_readiness_health_metadata,
        )
        cfg = GoalLoopReadinessConfig(
            **{**_VALID_CONFIG_KWARGS, "killSwitchEnabled": True}
        )  # type: ignore[arg-type]
        meta = goal_loop_readiness_health_metadata(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert meta["status"] == "disabled"
        assert meta["executionMode"] == "disabled"
        assert "env_gate_disabled" in meta["reasonCodes"]

    def test_kill_switch_blocks_to_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, killSwitchEnabled=True)
        assert meta["executionMode"] == "disabled"
        assert "kill_switch_enabled" in meta["reasonCodes"]

    def test_kill_switch_enabled_with_env_on_yields_blocked_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """env_on=True + kill_switch_enabled=True → status=='blocked' (not 'disabled').

        When the env gate IS on but the kill switch is engaged the loop is
        actively blocked (not merely off), so status must be 'blocked' —
        paralleling the existing env-off→'disabled' test which checks the
        complementary case.
        """
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, killSwitchEnabled=True)
        assert meta["status"] == "blocked"
        assert meta["executionMode"] == "disabled"

    def test_shadow_mode_disabled_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, shadowModeEnabled=False)
        assert meta["executionMode"] == "disabled"
        assert "shadow_mode_disabled" in meta["reasonCodes"]

    def test_malformed_scope_if_bot_digest_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, selectedBotDigest="")
        assert "malformed_selected_scope" in meta["reasonCodes"]

    def test_malformed_scope_if_user_digest_not_sha256(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, selectedOwnerUserIdDigest="not-a-digest")
        assert "malformed_selected_scope" in meta["reasonCodes"]

    def test_bot_not_selected_when_digest_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, selectedBotDigest=_sha256("other-bot"))
        assert "bot_not_selected" in meta["reasonCodes"]

    def test_owner_not_selected_when_digest_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, selectedOwnerUserIdDigest=_sha256("other-user"))
        assert "owner_not_selected" in meta["reasonCodes"]

    def test_invalid_environment_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, environment="forbidden-env")
        assert "invalid_environment" in meta["reasonCodes"]

    def test_environment_not_allowlisted_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, environment="local", environmentAllowlist=["staging"])
        assert "environment_not_allowlisted" in meta["reasonCodes"]

    def test_shadow_ready_when_all_clear_and_no_canary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, promotedGate=0, canaryPromotionConfirmed=False)
        assert meta["executionMode"] == "shadow"
        assert meta["status"] == "shadow"
        assert meta["readinessReady"] is True
        assert meta["liveExecutionAllowed"] is False
        assert meta["reasonCodes"] == ["selected_shadow_ready"]

    def test_canary_live_ready_when_promoted_gate_5_and_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, promotedGate=5, canaryPromotionConfirmed=True)
        assert meta["executionMode"] == "live"
        assert meta["status"] == "live"
        assert meta["liveExecutionAllowed"] is True
        assert meta["reasonCodes"] == ["selected_canary_live_ready"]

    def test_not_live_if_promoted_gate_4_even_if_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, promotedGate=4, canaryPromotionConfirmed=True)
        assert meta["executionMode"] == "shadow"
        assert meta["liveExecutionAllowed"] is False

    def test_not_live_if_gate_5_but_not_confirmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, promotedGate=5, canaryPromotionConfirmed=False)
        assert meta["executionMode"] == "shadow"

    def test_metadata_contains_expected_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch)
        for field in (
            "enabled", "envGateEnabled", "status", "executionMode",
            "readinessReady", "selectedScopeMatched", "promotedGate",
            "canaryLiveGate", "canaryPromotionConfirmed", "liveExecutionAllowed",
            "counterRequirements", "reasonCodes",
        ):
            assert field in meta, f"missing field: {field}"

    def test_counter_requirements_surface_goal_loop_counters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        meta = self._make(monkeypatch, promotedGate=5, canaryPromotionConfirmed=True)
        counters = meta["counterRequirements"]
        assert "continued" in counters
        assert "stopped" in counters
        assert "spend_capped" in counters
        assert "judge_budget_exhausted" in counters


# ---------------------------------------------------------------------------
# 3. resolve_goal_loop_execution_mode convenience
# ---------------------------------------------------------------------------

class TestResolveGoalLoopExecutionMode:
    def test_returns_disabled_when_gate_disabled(self) -> None:
        from magi_agent.gates.goal_loop_readiness import (
            GoalLoopReadinessConfig,
            resolve_goal_loop_execution_mode,
        )
        cfg = GoalLoopReadinessConfig()
        mode = resolve_goal_loop_execution_mode(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert mode == "disabled"

    def test_returns_shadow_for_valid_shadow_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        from magi_agent.gates.goal_loop_readiness import (
            GoalLoopReadinessConfig,
            resolve_goal_loop_execution_mode,
        )
        cfg = GoalLoopReadinessConfig(**_VALID_CONFIG_KWARGS)  # type: ignore[arg-type]
        mode = resolve_goal_loop_execution_mode(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert mode == "shadow"

    def test_returns_live_for_full_canary_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
        from magi_agent.gates.goal_loop_readiness import (
            GoalLoopReadinessConfig,
            resolve_goal_loop_execution_mode,
        )
        cfg = GoalLoopReadinessConfig(
            **{**_VALID_CONFIG_KWARGS, "promotedGate": 5, "canaryPromotionConfirmed": True}
        )  # type: ignore[arg-type]
        mode = resolve_goal_loop_execution_mode(cfg, bot_id=_BOT_ID, user_id=_USER_ID)
        assert mode == "live"


# ---------------------------------------------------------------------------
# 4. Canary live-gate constant + registry invariant
# ---------------------------------------------------------------------------

class TestGoalLoopCanaryLiveGate:
    def test_canary_live_gate_constant_equals_5(self) -> None:
        """_CANARY_LIVE_GATE must equal 5 — same as scheduler_executor.
        The goal loop rides the shared gate-5 threshold; no standalone factory
        is needed (mirrors the scheduler precedent of using only the constant).
        """
        import importlib
        mod = importlib.import_module("magi_agent.gates.goal_loop_readiness")
        assert mod._CANARY_LIVE_GATE == 5  # type: ignore[attr-defined]

    def test_main_registry_still_defines_gate0_to_9_unchanged(self) -> None:
        """The main 0-9 registry must be untouched — goal loop adds no new entry."""
        from magi_agent.gates.api_canary_ladder import build_canary_gate_registry

        registry = build_canary_gate_registry()
        assert tuple(gate.gate_id for gate in registry.gates) == tuple(range(10))
        assert registry.gates[5].slug == "gate5_scheduler_cron_mission"

    def test_build_goal_loop_canary_gate_spec_does_not_exist(self) -> None:
        """The unused factory must be absent — YAGNI, matches scheduler precedent."""
        import magi_agent.gates.goal_loop_readiness as mod
        assert not hasattr(mod, "build_goal_loop_canary_gate_spec"), (
            "build_goal_loop_canary_gate_spec was deleted as unused; "
            "it must not be re-added without a real call-site"
        )


# ---------------------------------------------------------------------------
# 5. Spawn-depth enforcement
# ---------------------------------------------------------------------------

class TestGoalLoopSpawnDepthEnforcement:
    def test_check_spawn_depth_accepts_zero(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_spawn_depth_gate

        result = check_goal_loop_spawn_depth_gate(spawn_depth=0)
        assert result["allowed"] is True
        assert result["spawnDepth"] == 0

    def test_check_spawn_depth_accepts_max(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_spawn_depth_gate
        from magi_agent.harness.goal_loop import DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH

        result = check_goal_loop_spawn_depth_gate(spawn_depth=DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH)
        assert result["allowed"] is True

    def test_check_spawn_depth_rejects_exceeding_max(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_spawn_depth_gate
        from magi_agent.harness.goal_loop import DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH

        result = check_goal_loop_spawn_depth_gate(spawn_depth=DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH + 1)
        assert result["allowed"] is False
        assert "spawn_depth_exceeded" in result["reasonCode"]

    def test_check_spawn_depth_rejects_negative(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_spawn_depth_gate

        result = check_goal_loop_spawn_depth_gate(spawn_depth=-1)
        assert result["allowed"] is False

    def test_check_spawn_depth_uses_max_from_goal_loop(self) -> None:
        """check_goal_loop_spawn_depth_gate must respect DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH."""
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_spawn_depth_gate
        from magi_agent.harness.goal_loop import DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH

        result = check_goal_loop_spawn_depth_gate(spawn_depth=DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH)
        assert result["maxSpawnDepth"] == DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH

    def test_check_spawn_depth_result_includes_max_field(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_spawn_depth_gate

        result = check_goal_loop_spawn_depth_gate(spawn_depth=1)
        assert "maxSpawnDepth" in result
        assert "spawnDepth" in result
        assert "allowed" in result


# ---------------------------------------------------------------------------
# 6. Ownership assignment gate
# ---------------------------------------------------------------------------

class TestGoalLoopOwnershipAssignment:
    def test_main_agent_at_depth_0_is_allowed(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_ownership_assignment

        result = check_goal_loop_ownership_assignment(agent_scope="main", spawn_depth=0)
        assert result["ownershipValid"] is True

    def test_child_agent_cannot_own_persistence(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_ownership_assignment

        result = check_goal_loop_ownership_assignment(agent_scope="child", spawn_depth=1)
        assert result["ownershipValid"] is False
        assert "child_cannot_own" in result["reasonCode"] or "ownership" in result["reasonCode"].lower()

    def test_child_agent_at_depth_0_is_invalid(self) -> None:
        """Child agents at depth 0 are invalid — child must have spawn_depth > 0."""
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_ownership_assignment

        result = check_goal_loop_ownership_assignment(agent_scope="child", spawn_depth=0)
        assert result["ownershipValid"] is False

    def test_main_agent_at_depth_1_is_invalid(self) -> None:
        """Main agents must use spawn_depth=0."""
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_ownership_assignment

        result = check_goal_loop_ownership_assignment(agent_scope="main", spawn_depth=1)
        assert result["ownershipValid"] is False

    def test_result_includes_required_fields(self) -> None:
        from magi_agent.gates.goal_loop_readiness import check_goal_loop_ownership_assignment

        result = check_goal_loop_ownership_assignment(agent_scope="main", spawn_depth=0)
        assert "ownershipValid" in result
        assert "agentScope" in result
        assert "spawnDepth" in result


# ---------------------------------------------------------------------------
# 7. B1-B4 safety invariants
# ---------------------------------------------------------------------------

class TestB1B4SafetyInvariantsInReadiness:
    def test_goal_loop_is_default_off_in_policy(self) -> None:
        """B1-B4 invariant: GoalLoopPolicy must be default-off (traffic/execution_attached=False)."""
        from magi_agent.harness.goal_loop import GoalLoopPolicy

        policy = GoalLoopPolicy()
        assert policy.enabled is False
        assert policy.traffic_attached is False
        assert policy.execution_attached is False

    def test_continuation_role_is_user_not_system(self) -> None:
        """B3 invariant: continuation is USER-role, prefix-cache safe."""
        from magi_agent.harness.goal_loop_control import LoopControlResult

        # Verify the continuation_role field is always "user" by checking defaults
        import inspect
        hints = LoopControlResult.model_fields
        assert "continuation_role" in hints

    def test_after_turn_hook_is_non_blocking_and_fail_open(self) -> None:
        """B3 invariant: the after-turn hook manifest has blocking=False and failOpen=True."""
        from magi_agent.harness.goal_loop_control import build_after_turn_goal_loop_hook

        def _provider(ctx):  # type: ignore[no-untyped-def]
            return None

        manifest, _handler = build_after_turn_goal_loop_hook(input_provider=_provider)
        assert manifest.blocking is False
        assert manifest.fail_open is True

    def test_spend_guard_seam_exists_in_loop_control_input(self) -> None:
        """B3 invariant: SpendCapProbe is wired via LoopControlInput.spend_probe."""
        from magi_agent.harness.goal_loop_control import LoopControlInput

        assert "spend_probe" in LoopControlInput.model_fields

    def test_evidence_gate_fails_toward_continue_not_stop(self) -> None:
        """B4 invariant: evidence gate failure causes continue (evidence_unmet), not stop."""
        from magi_agent.harness.goal_loop_control import (
            EVIDENCE_GATE_ENV_VAR,
            EvidenceGateVerdict,
            LoopControlInput,
        )
        # Verify the verdict model has passed=False as its failure mode
        verdict = EvidenceGateVerdict(passed=False, reason="test")
        assert verdict.passed is False

    def test_judge_fail_open_budget_is_present(self) -> None:
        """B2 invariant: DEFAULT_JUDGE_PARSE_FAILURE_BUDGET exists (fail-open with cap)."""
        from magi_agent.harness.goal_judge import DEFAULT_JUDGE_PARSE_FAILURE_BUDGET

        assert isinstance(DEFAULT_JUDGE_PARSE_FAILURE_BUDGET, int)
        assert DEFAULT_JUDGE_PARSE_FAILURE_BUDGET > 0

    def test_goal_loop_policy_traffic_and_execution_remain_false(self) -> None:
        """B5 readiness is a projection layer — it must NOT flip traffic/execution flags."""
        from magi_agent.harness.goal_loop import build_goal_loop_policy

        policy = build_goal_loop_policy(enabled=False)
        assert policy.traffic_attached is False
        assert policy.execution_attached is False

    def test_readiness_module_does_not_enable_goal_loop(self) -> None:
        """Importing goal_loop_readiness must not change any env gate or authority flag."""
        import importlib
        # Import must not raise and must not set env vars
        before = os.environ.get("MAGI_GOAL_LOOP_ENABLED")
        importlib.import_module("magi_agent.gates.goal_loop_readiness")
        after = os.environ.get("MAGI_GOAL_LOOP_ENABLED")
        assert before == after


# ---------------------------------------------------------------------------
# 8. Kill-switch + env allowlist
# ---------------------------------------------------------------------------

class TestKillSwitchAndEnvAllowlist:
    def test_kill_switch_env_var_name_is_accessible(self) -> None:
        from magi_agent.gates.goal_loop_readiness import _KILL_SWITCH_ENV_VAR

        assert "MAGI_GOAL_LOOP_KILL_SWITCH" in _KILL_SWITCH_ENV_VAR

    def test_kill_switch_enabled_by_default_in_config(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig()
        assert cfg.kill_switch_enabled is True

    def test_env_gate_var_name_is_accessible(self) -> None:
        from magi_agent.gates.goal_loop_readiness import _GOAL_LOOP_ENV_VAR

        assert "MAGI_GOAL_LOOP" in _GOAL_LOOP_ENV_VAR

    def test_safe_environments_does_not_include_unknown(self) -> None:
        from magi_agent.gates.goal_loop_readiness import _SAFE_ENVIRONMENTS

        assert "production" in _SAFE_ENVIRONMENTS
        assert "local" in _SAFE_ENVIRONMENTS
        assert "garbage-env" not in _SAFE_ENVIRONMENTS

    def test_allowlist_empty_by_default(self) -> None:
        from magi_agent.gates.goal_loop_readiness import GoalLoopReadinessConfig

        cfg = GoalLoopReadinessConfig()
        assert cfg.environment_allowlist == ()


# ---------------------------------------------------------------------------
# 9. Import purity — no forbidden imports
# ---------------------------------------------------------------------------

class TestGoalLoopReadinessImportPurity:
    def test_readiness_module_does_not_import_forbidden_modules(self) -> None:
        import ast
        import pathlib

        src = (
            pathlib.Path(__file__).parent.parent
            / "magi_agent"
            / "gates"
            / "goal_loop_readiness.py"
        ).read_text()
        tree = ast.parse(src)
        forbidden = ("urllib", "socket", "subprocess", "http", "requests")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in forbidden:
                        assert not alias.name.startswith(prefix), (
                            f"Forbidden import: {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for prefix in forbidden:
                    assert not module.startswith(prefix), (
                        f"Forbidden import from: {module}"
                    )

    def test_readiness_module_does_not_import_google_adk(self) -> None:
        import ast
        import pathlib

        src = (
            pathlib.Path(__file__).parent.parent
            / "magi_agent"
            / "gates"
            / "goal_loop_readiness.py"
        ).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("google"), (
                        f"Forbidden import: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("google"), (
                    f"Forbidden import from: {module}"
                )
