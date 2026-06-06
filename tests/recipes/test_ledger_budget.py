"""Tests for LedgerBudgetPolicy (Phase 0a)."""
from __future__ import annotations

import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from magi_agent.recipes.ledger_budget import LedgerBudgetPolicy, default_gaia_policy


# ---------------------------------------------------------------------------
# Construction — happy path
# ---------------------------------------------------------------------------

class TestLedgerBudgetPolicyConstruction:
    def test_minimal_valid_policy(self) -> None:
        policy = LedgerBudgetPolicy(
            step_budget=5,
            token_budget=10_000,
            wall_budget_ms=10_000,
            stall_threshold=2,
            max_replan_count=1,
            per_step_token_budget=1_000,
            per_step_wall_ms=5_000,
        )
        assert policy.step_budget == 5
        assert policy.default_off is True

    def test_frozen_rejects_mutation(self) -> None:
        policy = LedgerBudgetPolicy(
            step_budget=10,
            token_budget=200_000,
            wall_budget_ms=120_000,
            stall_threshold=3,
            max_replan_count=2,
            per_step_token_budget=20_000,
            per_step_wall_ms=30_000,
        )
        with pytest.raises((TypeError, ValidationError)):
            policy.step_budget = 99  # type: ignore[misc]

    def test_default_off_always_true(self) -> None:
        policy = LedgerBudgetPolicy(
            step_budget=10,
            token_budget=200_000,
            wall_budget_ms=120_000,
            stall_threshold=3,
            max_replan_count=2,
            per_step_token_budget=20_000,
            per_step_wall_ms=30_000,
        )
        assert policy.default_off is True


# ---------------------------------------------------------------------------
# Validation — reject bad inputs
# ---------------------------------------------------------------------------

class TestLedgerBudgetPolicyValidation:
    def _valid_kwargs(self) -> dict:
        return {
            "step_budget": 10,
            "token_budget": 200_000,
            "wall_budget_ms": 120_000,
            "stall_threshold": 3,
            "max_replan_count": 2,
            "per_step_token_budget": 20_000,
            "per_step_wall_ms": 30_000,
        }

    def test_per_step_token_exceeds_total_rejected(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["per_step_token_budget"] = kwargs["token_budget"] + 1
        with pytest.raises(ValidationError, match="per_step_token_budget"):
            LedgerBudgetPolicy(**kwargs)

    def test_per_step_wall_exceeds_total_rejected(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["per_step_wall_ms"] = kwargs["wall_budget_ms"] + 1
        with pytest.raises(ValidationError, match="per_step_wall_ms"):
            LedgerBudgetPolicy(**kwargs)

    def test_step_budget_zero_rejected(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["step_budget"] = 0
        with pytest.raises(ValidationError):
            LedgerBudgetPolicy(**kwargs)

    def test_extra_fields_rejected(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["unknown_field"] = "bad"
        with pytest.raises(ValidationError):
            LedgerBudgetPolicy(**kwargs)


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

class TestLedgerBudgetPolicyDigest:
    def _make(self, **overrides: object) -> LedgerBudgetPolicy:
        kwargs: dict = {
            "step_budget": 10,
            "token_budget": 200_000,
            "wall_budget_ms": 120_000,
            "stall_threshold": 3,
            "max_replan_count": 2,
            "per_step_token_budget": 20_000,
            "per_step_wall_ms": 30_000,
        }
        kwargs.update(overrides)
        return LedgerBudgetPolicy(**kwargs)

    def test_digest_format(self) -> None:
        policy = self._make()
        digest = policy.policy_digest()
        assert digest.startswith("sha256:")
        assert len(digest) == 7 + 64

    def test_different_fields_produce_different_digest(self) -> None:
        p1 = self._make(step_budget=10)
        p2 = self._make(step_budget=11)
        assert p1.policy_digest() != p2.policy_digest()

    def test_same_fields_produce_same_digest(self) -> None:
        p1 = self._make()
        p2 = self._make()
        assert p1.policy_digest() == p2.policy_digest()

    def test_public_projection_contains_digest(self) -> None:
        policy = self._make()
        proj = policy.public_projection()
        assert proj["policyDigest"] == policy.policy_digest()
        assert proj["defaultOff"] is True


# ---------------------------------------------------------------------------
# GAIA defaults factory
# ---------------------------------------------------------------------------

class TestDefaultGaiaPolicy:
    def test_level_1_returns_policy(self) -> None:
        policy = default_gaia_policy(1)
        assert isinstance(policy, LedgerBudgetPolicy)
        assert policy.step_budget == 10
        assert policy.wall_budget_ms == 120_000

    def test_level_2_returns_policy(self) -> None:
        policy = default_gaia_policy(2)
        assert policy.step_budget == 15
        assert policy.wall_budget_ms == 240_000

    def test_level_3_returns_policy(self) -> None:
        policy = default_gaia_policy(3)
        assert policy.step_budget == 20
        assert policy.wall_budget_ms == 360_000

    def test_unknown_level_falls_back_to_l2(self) -> None:
        policy = default_gaia_policy(99)
        assert policy.step_budget == 15  # L2 default

    def test_all_levels_pass_validation(self) -> None:
        for level in (1, 2, 3):
            policy = default_gaia_policy(level)
            assert policy.default_off is True
