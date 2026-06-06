"""Tests for magi_agent.recipes.best_of_n — general Best-of-N recipe.

All tests are hermetic: fake runner_fn, no network, no real model calls.
"""
from __future__ import annotations

import os

import pytest

from magi_agent.recipes.best_of_n import (
    BestOfNConfig,
    BestOfNResult,
    ConsensusMode,
    run_best_of_n,
)
from magi_agent.tools.manifest import Budget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fake_runner(answers: list[str]):
    """Returns a stub that yields answers in sequence (cycling)."""
    call_count = 0

    def runner(task, *, workspace_root: str, seed: int, **kw: object) -> str:
        nonlocal call_count
        ans = answers[call_count % len(answers)]
        call_count += 1
        return ans

    return runner


# ---------------------------------------------------------------------------
# PR 1 — core recipe tests
# ---------------------------------------------------------------------------


class TestN1SingleAnswer:
    def test_returns_correct_value(self) -> None:
        runner = make_fake_runner(["42"])
        cfg = BestOfNConfig(n=1, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.value == "42"

    def test_n_attempted_is_1(self) -> None:
        runner = make_fake_runner(["42"])
        cfg = BestOfNConfig(n=1, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.n_attempted == 1

    def test_n_successful_is_1(self) -> None:
        runner = make_fake_runner(["42"])
        cfg = BestOfNConfig(n=1, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.n_successful == 1

    def test_result_is_best_of_n_result(self) -> None:
        runner = make_fake_runner(["hello"])
        cfg = BestOfNConfig(n=1, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert isinstance(result, BestOfNResult)


class TestN3PluralityConsensus:
    def test_selects_majority_answer(self) -> None:
        runner = make_fake_runner(["42", "43", "42"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.value == "42"

    def test_agreement_count_reflects_majority(self) -> None:
        runner = make_fake_runner(["42", "43", "42"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.agreement_count == 2

    def test_n_attempted_is_3(self) -> None:
        runner = make_fake_runner(["42", "43", "42"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.n_attempted == 3

    def test_n_successful_is_3(self) -> None:
        runner = make_fake_runner(["42", "43", "42"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.n_successful == 3

    def test_samples_tuple_preserved(self) -> None:
        runner = make_fake_runner(["42", "43", "42"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert len(result.samples) == 3

    def test_confidence_is_fraction_of_n_attempted(self) -> None:
        runner = make_fake_runner(["42", "43", "42"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        # 2 agreed out of 3 attempted
        assert abs(result.confidence - 2 / 3) < 1e-9


class TestFailedSampleOutvoted:
    """A runner that raises on one sample is treated as a failed sample."""

    def _make_flaky_runner(self) -> object:
        call_count = 0

        def flaky_runner(task: object, *, workspace_root: str, seed: int, **kw: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ConnectionError("mcp timeout")
            return "42"

        return flaky_runner

    def test_returns_winner_despite_failure(self) -> None:
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(self._make_flaky_runner(), task="q", config=cfg)
        assert result.value == "42"

    def test_n_successful_is_2(self) -> None:
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(self._make_flaky_runner(), task="q", config=cfg)
        assert result.n_successful == 2

    def test_n_attempted_is_3(self) -> None:
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(self._make_flaky_runner(), task="q", config=cfg)
        assert result.n_attempted == 3


class TestDefaultOff:
    """When enabled=False and MAGI_BEST_OF_N_ENABLED is not set, n=1 pass-through."""

    def test_default_off_degrades_to_n1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["a", "b", "c"])
        cfg = BestOfNConfig(n=5, enabled=False)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.n_attempted == 1

    def test_env_var_activates_feature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_BEST_OF_N_ENABLED", "1")
        runner = make_fake_runner(["x", "y", "z"])
        cfg = BestOfNConfig(n=3, enabled=False)
        result = run_best_of_n(runner, task="q", config=cfg)
        # env var overrides enabled=False, so all 3 samples are attempted
        assert result.n_attempted == 3

    def test_explicit_enabled_true_bypasses_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["a", "b", "a"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.n_attempted == 3


class TestBudgetCapsN:
    def test_budget_max_calls_caps_n(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["42"] * 10)
        cfg = BestOfNConfig(n=5, enabled=True)
        budget = Budget(max_calls_per_turn=2)
        result = run_best_of_n(runner, task="q", config=cfg, budget=budget)
        assert result.n_attempted == 2

    def test_budget_none_does_not_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["x"] * 10)
        cfg = BestOfNConfig(n=4, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg, budget=None)
        assert result.n_attempted == 4

    def test_budget_larger_than_n_does_not_expand_n(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["x"] * 10)
        cfg = BestOfNConfig(n=2, enabled=True)
        budget = Budget(max_calls_per_turn=10)
        result = run_best_of_n(runner, task="q", config=cfg, budget=budget)
        assert result.n_attempted == 2


class TestNormalizationCollapsesFormattingVariants:
    def test_currency_comma_formatting_agrees(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """$1,234 and 1234 should agree after normalization."""
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["$1,234", "1234", "1234"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.agreement_count >= 2

    def test_whitespace_variants_agree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["  42  ", "42", "42"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.agreement_count >= 2

    def test_case_variants_agree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["Paris", "paris", "paris"])
        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.agreement_count >= 2


class TestAllFailedReturnsEmpty:
    def test_returns_empty_value(self) -> None:
        def crash_runner(task: object, **kw: object) -> str:
            raise RuntimeError("always fails")

        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(crash_runner, task="q", config=cfg)
        assert result.value == ""

    def test_n_successful_is_0(self) -> None:
        def crash_runner(task: object, **kw: object) -> str:
            raise RuntimeError("always fails")

        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(crash_runner, task="q", config=cfg)
        assert result.n_successful == 0

    def test_confidence_is_0(self) -> None:
        def crash_runner(task: object, **kw: object) -> str:
            raise RuntimeError("always fails")

        cfg = BestOfNConfig(n=3, enabled=True)
        result = run_best_of_n(crash_runner, task="q", config=cfg)
        assert result.confidence == 0.0


class TestConsensusModes:
    def test_first_valid_returns_first_non_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["", "alpha", "beta"])
        cfg = BestOfNConfig(n=3, enabled=True, consensus_mode=ConsensusMode.FIRST_VALID)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.value == "alpha"

    def test_plurality_is_default_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        cfg = BestOfNConfig(n=1, enabled=True)
        assert cfg.consensus_mode == ConsensusMode.PLURALITY

    def test_consensus_mode_recorded_on_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_BEST_OF_N_ENABLED", raising=False)
        runner = make_fake_runner(["42"])
        cfg = BestOfNConfig(n=1, enabled=True, consensus_mode=ConsensusMode.FIRST_VALID)
        result = run_best_of_n(runner, task="q", config=cfg)
        assert result.consensus_mode == ConsensusMode.FIRST_VALID


class TestBestOfNConfigValidation:
    def test_n_must_be_at_least_1(self) -> None:
        with pytest.raises(Exception):
            BestOfNConfig(n=0, enabled=True)

    def test_n_must_not_exceed_16(self) -> None:
        with pytest.raises(Exception):
            BestOfNConfig(n=17, enabled=True)

    def test_valid_n_16_accepted(self) -> None:
        cfg = BestOfNConfig(n=16, enabled=True)
        assert cfg.n == 16

    def test_default_enabled_is_false(self) -> None:
        cfg = BestOfNConfig()
        assert cfg.enabled is False

    def test_default_n_is_1(self) -> None:
        cfg = BestOfNConfig()
        assert cfg.n == 1

    def test_config_is_frozen(self) -> None:
        cfg = BestOfNConfig(n=3, enabled=True)
        with pytest.raises(Exception):
            cfg.n = 5  # type: ignore[misc]


class TestSeedProgression:
    """Each rollout receives a distinct seed derived from base_seed + i."""

    def test_seeds_are_distinct(self) -> None:
        seen_seeds: list[int] = []

        def seed_recorder(task: object, *, workspace_root: str, seed: int, **kw: object) -> str:
            seen_seeds.append(seed)
            return "x"

        cfg = BestOfNConfig(n=4, enabled=True, base_seed=100)
        run_best_of_n(seed_recorder, task="q", config=cfg)
        assert seen_seeds == [100, 101, 102, 103]


class TestWorkspaceIsolation:
    """Each rollout receives a distinct, non-empty workspace_root path."""

    def test_workspaces_are_distinct(self) -> None:
        seen_workspaces: list[str] = []

        def ws_recorder(task: object, *, workspace_root: str, seed: int, **kw: object) -> str:
            seen_workspaces.append(workspace_root)
            return "x"

        cfg = BestOfNConfig(n=3, enabled=True)
        run_best_of_n(ws_recorder, task="q", config=cfg)
        assert len(set(seen_workspaces)) == 3

    def test_workspace_is_non_empty_string(self) -> None:
        seen: list[str] = []

        def ws_recorder(task: object, *, workspace_root: str, seed: int, **kw: object) -> str:
            seen.append(workspace_root)
            return "x"

        cfg = BestOfNConfig(n=1, enabled=True)
        run_best_of_n(ws_recorder, task="q", config=cfg)
        assert seen[0] != ""


class TestImportBoundary:
    """best_of_n must not import from magi_agent.benchmarks."""

    def test_no_benchmark_import(self) -> None:
        import importlib
        import sys

        # Force re-import to inspect the module graph cleanly
        mod_name = "magi_agent.recipes.best_of_n"
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
        else:
            mod = importlib.import_module(mod_name)

        # Walk the module's __dict__ for any imported benchmark submodule
        for attr_name, attr_val in vars(mod).items():
            if hasattr(attr_val, "__module__"):
                assert "benchmarks" not in (attr_val.__module__ or ""), (
                    f"best_of_n imported benchmark symbol: {attr_name} "
                    f"from {attr_val.__module__}"
                )

    def test_module_docstring_present(self) -> None:
        import magi_agent.recipes.best_of_n as bon

        assert bon.__doc__ is not None
        assert len(bon.__doc__) > 10


class TestRunnerKwargsForwarding:
    def test_extra_runner_kwargs_are_forwarded(self) -> None:
        received_kwargs: dict[str, object] = {}

        def recording_runner(
            task: object, *, workspace_root: str, seed: int, **kw: object
        ) -> str:
            received_kwargs.update(kw)
            return "done"

        cfg = BestOfNConfig(n=1, enabled=True)
        run_best_of_n(
            recording_runner,
            task="q",
            config=cfg,
            runner_kwargs={"model": "claude-opus-4-7"},
        )
        assert received_kwargs.get("model") == "claude-opus-4-7"
