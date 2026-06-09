"""Tests for magi_agent.recipes.cross_verify — unified cross-verify recipe.

All tests are hermetic: a per-route FAKE child runner is injected via
``child_runner_factory``; no network, no keys, no real model calls.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from magi_agent.recipes.cross_verify import (
    CrossVerifyConfig,
    CrossVerifyResult,
    is_cross_verify_enabled,
    run_cross_verify,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeChildRunner:
    """A local-fake child runner that returns a canned envelope-shaped mapping.

    ``openmagi_local_fake_provider = True`` so the boundary trusts it.  The
    ``answer`` is surfaced as the (sanitised) envelope ``summary``.
    """

    openmagi_local_fake_provider = True

    def __init__(self, answer: str, *, fail: bool = False, status: str = "completed") -> None:
        self.answer = answer
        self.fail = fail
        self.status = status
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("fake child runner forced failure")
        task_id = getattr(request, "task_id", "task")
        return {
            "childExecutionId": f"child-{task_id}",
            "status": self.status,
            "summary": self.answer,
        }


class RecordingFactory:
    """A ``child_runner_factory`` that records which routes it was asked for."""

    def __init__(self, runners: dict[tuple[str, str], FakeChildRunner]) -> None:
        self.runners = runners
        self.requested: list[tuple[str, str]] = []

    def __call__(self, route: tuple[str, str]) -> FakeChildRunner:
        self.requested.append(route)
        return self.runners[route]


def _factory(mapping: dict[tuple[str, str], FakeChildRunner]) -> RecordingFactory:
    return RecordingFactory(mapping)


# ---------------------------------------------------------------------------
# Consensus across models
# ---------------------------------------------------------------------------


class TestConsensusAcrossModels:
    def _setup(self) -> tuple[RecordingFactory, list[tuple[str, str]]]:
        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
        ]
        factory = _factory(
            {
                routes[0]: FakeChildRunner("X"),
                routes[1]: FakeChildRunner("X"),
                routes[2]: FakeChildRunner("Y"),
            }
        )
        return factory, routes

    def test_majority_answer_wins(self) -> None:
        factory, routes = self._setup()
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.consensus == "X"

    def test_all_candidates_listed_with_routes(self) -> None:
        factory, routes = self._setup()
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert len(result.candidates) == 3
        seen = {(c.provider, c.model): c.summary for c in result.candidates}
        assert seen == {
            ("anthropic", "claude"): "X",
            ("openai", "gpt"): "X",
            ("google", "gemini"): "Y",
        }

    def test_counts_are_correct(self) -> None:
        factory, routes = self._setup()
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.models_attempted == 3
        assert result.models_counted == 3
        assert result.agreement_count == 2

    def test_factory_called_once_per_route(self) -> None:
        factory, routes = self._setup()
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        asyncio.run(run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg))
        assert sorted(factory.requested) == sorted(routes)

    def test_result_type(self) -> None:
        factory, routes = self._setup()
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert isinstance(result, CrossVerifyResult)
        assert result.enabled is True


# ---------------------------------------------------------------------------
# Default-OFF gate
# ---------------------------------------------------------------------------


class TestDefaultOff:
    def test_disabled_is_noop_and_factory_not_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_CROSS_VERIFY_ENABLED", raising=False)
        routes = [("anthropic", "claude"), ("openai", "gpt")]
        factory = _factory({r: FakeChildRunner("X") for r in routes})
        cfg = CrossVerifyConfig(enabled=False, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.enabled is False
        assert result.consensus == ""
        assert result.reason_codes == ("cross_verify_disabled",)
        assert factory.requested == []
        assert all(r.calls == 0 for r in factory.runners.values())

    def test_env_var_activates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_CROSS_VERIFY_ENABLED", "1")
        routes = [("anthropic", "claude"), ("openai", "gpt")]
        factory = _factory({r: FakeChildRunner("X") for r in routes})
        cfg = CrossVerifyConfig(enabled=False, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.enabled is True
        assert result.consensus == "X"

    def test_default_config_is_off(self) -> None:
        assert CrossVerifyConfig().enabled is False

    def test_is_cross_verify_enabled_helper(self) -> None:
        assert is_cross_verify_enabled({"MAGI_CROSS_VERIFY_ENABLED": "1"}) is True
        assert is_cross_verify_enabled({}) is False


# ---------------------------------------------------------------------------
# Degraded paths (never raises)
# ---------------------------------------------------------------------------


class TestDegradedPaths:
    def test_one_child_fails_others_still_vote(self) -> None:
        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
        ]
        factory = _factory(
            {
                routes[0]: FakeChildRunner("X"),
                routes[1]: FakeChildRunner("X"),
                routes[2]: FakeChildRunner("Y", fail=True),
            }
        )
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.consensus == "X"
        assert result.models_attempted == 3
        assert result.models_counted == 2
        # The failed child is recorded but not counted.
        failed = [c for c in result.candidates if c.model == "gemini"][0]
        assert failed.counted is False
        assert failed.status == "error"

    def test_all_children_fail_degrades_no_raise(self) -> None:
        routes = [("anthropic", "claude"), ("openai", "gpt")]
        factory = _factory({r: FakeChildRunner("X", fail=True) for r in routes})
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.consensus == ""
        assert result.models_counted == 0
        assert result.reason_codes == ("cross_verify_no_countable_children",)
        assert all(c.counted is False for c in result.candidates)
        # Bug fix #1: all-fail must NOT yield spurious agreement_count / confidence.
        assert result.agreement_count == 0
        assert result.confidence == 0.0

    def test_blocked_child_excluded(self) -> None:
        # A blocked envelope status still produces an "ok" boundary result with a
        # summary; an UNTRUSTED runner instead yields a non-ok child status.
        class UntrustedRunner:
            openmagi_local_fake_provider = False

            async def run_child(self, request: object) -> dict[str, object]:
                return {"status": "completed", "summary": "Z"}

        routes = [("anthropic", "claude"), ("openai", "gpt")]
        factory_map = {
            routes[0]: FakeChildRunner("X"),
            routes[1]: UntrustedRunner(),  # type: ignore[dict-item]
        }

        def factory(route: tuple[str, str]) -> object:
            return factory_map[route]

        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.consensus == "X"
        assert result.models_counted == 1
        blocked = [c for c in result.candidates if c.model == "gpt"][0]
        assert blocked.counted is False
        assert blocked.status == "blocked"

    def test_no_models_degrades(self) -> None:
        cfg = CrossVerifyConfig(enabled=True, models=())
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=lambda r: None, config=cfg)
        )
        assert result.enabled is True
        assert result.consensus == ""
        assert result.reason_codes == ("cross_verify_no_models",)


# ---------------------------------------------------------------------------
# Single-model trivial consensus
# ---------------------------------------------------------------------------


class TestSingleModel:
    def test_single_model_trivial_consensus(self) -> None:
        route = ("anthropic", "claude")
        factory = _factory({route: FakeChildRunner("only-answer")})
        cfg = CrossVerifyConfig(enabled=True, models=(route,))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.consensus == "only-answer"
        assert result.models_attempted == 1
        assert result.models_counted == 1
        assert result.agreement_count == 1


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------


class TestSanitization:
    def test_dirty_summary_is_sanitized(self) -> None:
        route = ("anthropic", "claude")
        dirty = (
            "Safe public answer line.\n"
            "raw_child_transcript: /Users/kevin/private/raw.json\n"
            "Authorization: Bearer sk-live-abcd1234efgh5678\n"
            "/workspace/secret/path"
        )
        factory = _factory({route: FakeChildRunner(dirty)})
        cfg = CrossVerifyConfig(enabled=True, models=(route,))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        rendered = json.dumps(result.model_dump(), sort_keys=True)
        assert "Safe public answer line." in result.consensus
        assert "raw_child_transcript" not in rendered
        assert "/Users/kevin" not in rendered
        assert "/workspace" not in rendered
        assert "sk-live-abcd1234efgh5678" not in rendered
        assert "Bearer sk-live" not in rendered


# ---------------------------------------------------------------------------
# Clamps
# ---------------------------------------------------------------------------


class TestClamps:
    def test_max_models_clamp(self) -> None:
        # 10 distinct routes provided; only 8 (the _MAX_MODELS cap) survive.
        routes = [(f"prov{i}", f"model{i}") for i in range(10)]
        factory = _factory({r: FakeChildRunner("X") for r in routes})
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        # Config-level validator already clamps to 8.
        assert len(cfg.models) == 8
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.models_attempted == 8
        assert len(factory.requested) == 8

    def test_models_deduped(self) -> None:
        routes = [("anthropic", "claude"), ("anthropic", "claude"), ("openai", "gpt")]
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        assert cfg.models == (("anthropic", "claude"), ("openai", "gpt"))

    def test_concurrency_clamp_honored(self) -> None:
        # max_concurrency=1 forces serial execution; result must still be correct.
        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
        ]
        factory = _factory(
            {
                routes[0]: FakeChildRunner("X"),
                routes[1]: FakeChildRunner("X"),
                routes[2]: FakeChildRunner("Y"),
            }
        )
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes), maxConcurrency=1)
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.consensus == "X"
        assert result.models_counted == 3

    def test_max_concurrency_validator_clamps_high(self) -> None:
        with pytest.raises(Exception):
            CrossVerifyConfig(enabled=True, maxConcurrency=99)


# ---------------------------------------------------------------------------
# Bug #1 — all-fail spurious metrics
# ---------------------------------------------------------------------------


class TestAllFailMetrics:
    def test_all_fail_models_voted_is_zero(self) -> None:
        """Confirm models_voted=0 when no countable children exist."""
        routes = [("anthropic", "claude"), ("openai", "gpt")]
        factory = _factory({r: FakeChildRunner("X", fail=True) for r in routes})
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.models_voted == 0
        assert result.agreement_count == 0
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Bug #3 — budget truncation / models_voted
# ---------------------------------------------------------------------------


class TestBudgetModelsVoted:
    def test_budget_cap_makes_models_voted_less_than_models_counted(self) -> None:
        """When Budget.max_calls_per_turn < candidate count, models_voted < models_counted."""
        from magi_agent.tools.manifest import Budget

        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
            ("meta", "llama"),
        ]
        factory = _factory({r: FakeChildRunner("X") for r in routes})
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        # Cap the vote to 2 while there are 4 countable candidates.
        budget = Budget(max_calls_per_turn=2)
        result = asyncio.run(
            run_cross_verify(
                prompt="q",
                child_runner_factory=factory,
                config=cfg,
                budget=budget,
            )
        )
        assert result.models_counted == 4, "all four models produced answers"
        assert result.models_voted < result.models_counted, "budget capped the vote"
        assert result.models_voted == 2, "budget capped to 2"
        # Confidence should be interpretable against models_voted, not models_counted.
        assert result.agreement_count <= result.models_voted
        assert 0.0 <= result.confidence <= 1.0

    def test_no_budget_models_voted_equals_models_counted(self) -> None:
        """Without a Budget, all countable candidates are voted over."""
        routes = [("anthropic", "claude"), ("openai", "gpt"), ("google", "gemini")]
        factory = _factory({r: FakeChildRunner("X") for r in routes})
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.models_voted == result.models_counted


# ---------------------------------------------------------------------------
# Bug #2 — gather fault tolerance
# ---------------------------------------------------------------------------


class TestGatherFaultTolerance:
    def test_one_raiser_excluded_others_still_vote(self) -> None:
        """A child whose run_child() raises must not crash the fan-out."""

        class RaisingRunner:
            openmagi_local_fake_provider = True

            async def run_child(self, request: object) -> dict[str, object]:
                raise RuntimeError("injected task-level raise")

        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
        ]
        factory_map: dict[tuple[str, str], object] = {
            routes[0]: FakeChildRunner("consensus"),
            routes[1]: FakeChildRunner("consensus"),
            routes[2]: RaisingRunner(),
        }

        def factory(route: tuple[str, str]) -> object:
            return factory_map[route]

        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        # No exception must escape.
        assert result.consensus == "consensus"
        assert result.models_attempted == 3
        assert result.models_counted == 2, "only the two successful ones counted"
        raiser_candidate = [c for c in result.candidates if c.model == "gemini"][0]
        assert raiser_candidate.counted is False
        assert raiser_candidate.status == "error"


# ---------------------------------------------------------------------------
# Live-path admission — live-marked runner is admitted via the LIVE branch
# ---------------------------------------------------------------------------


class TestLivePathAdmission:
    """Prove the recipe admits a live-marked (``openmagi_live_provider=True``)
    runner through the boundary's LIVE branch.

    A real model is NOT needed: the "live-marked stand-in" carries the
    ``openmagi_live_provider=True`` marker so the boundary routes it via
    ``_run_live_child``, but its ``run_child`` still returns a canned envelope
    mapping — no network, no keys.  This verifies that ``_run_child`` now
    enables ``liveChildRunnerEnabled=True`` in the ``ChildRunnerConfig`` it
    constructs, so the boundary's live-admission path is reachable from the
    recipe.
    """

    def test_live_marked_runner_admitted_and_counted(self) -> None:
        """A live-marked stand-in runner is admitted and its answer counted."""

        class LiveMarkedStandIn:
            """Fake with the live-provider marker — no real model calls."""

            openmagi_live_provider = True

            def __init__(self, answer: str) -> None:
                self.answer = answer
                self.calls = 0

            async def run_child(self, request: object) -> dict[str, object]:
                self.calls += 1
                task_id = getattr(request, "task_id", "task")
                return {
                    "childExecutionId": f"child-{task_id}",
                    "status": "completed",
                    "summary": self.answer,
                }

        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
        ]
        live_runner_a = LiveMarkedStandIn("live-answer")
        live_runner_b = LiveMarkedStandIn("live-answer")
        factory = _factory(
            {
                routes[0]: live_runner_a,  # type: ignore[dict-item]
                routes[1]: live_runner_b,  # type: ignore[dict-item]
            }
        )
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        # Both live-marked runners must have been called.
        assert live_runner_a.calls == 1
        assert live_runner_b.calls == 1
        # Their answers must be counted in the consensus.
        assert result.models_counted == 2
        assert result.consensus == "live-answer"
        assert result.agreement_count == 2
        # All candidates must be marked counted=True.
        assert all(c.counted is True for c in result.candidates)

    def test_fake_path_still_works_alongside_live_gate_enabled(self) -> None:
        """Enabling liveChildRunnerEnabled must NOT break the existing fake path."""
        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
        ]
        factory = _factory(
            {
                routes[0]: FakeChildRunner("X"),
                routes[1]: FakeChildRunner("X"),
                routes[2]: FakeChildRunner("Y"),
            }
        )
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.consensus == "X"
        assert result.models_counted == 3
        assert result.agreement_count == 2


# ---------------------------------------------------------------------------
# Minor #5/#6 — confidence accuracy + tie-break determinism
# ---------------------------------------------------------------------------


class TestConfidenceAndTieBreak:
    def test_happy_path_confidence_ratio(self) -> None:
        """confidence == agreement_count / models_voted on a clean majority."""
        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
        ]
        factory = _factory(
            {
                routes[0]: FakeChildRunner("X"),
                routes[1]: FakeChildRunner("X"),
                routes[2]: FakeChildRunner("Y"),
            }
        )
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        result = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result.agreement_count == 2
        assert result.models_voted == 3
        expected_confidence = result.agreement_count / result.models_voted
        assert abs(result.confidence - expected_confidence) < 1e-9

    def test_all_distinct_tie_break_is_stable(self) -> None:
        """All distinct answers → first-seen route wins (insertion-order stable)."""
        routes = [
            ("anthropic", "claude"),
            ("openai", "gpt"),
            ("google", "gemini"),
        ]
        factory = _factory(
            {
                routes[0]: FakeChildRunner("alpha"),
                routes[1]: FakeChildRunner("beta"),
                routes[2]: FakeChildRunner("gamma"),
            }
        )
        cfg = CrossVerifyConfig(enabled=True, models=tuple(routes))
        # Run twice; winner must be the same both times.
        result_a = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        result_b = asyncio.run(
            run_cross_verify(prompt="q", child_runner_factory=factory, config=cfg)
        )
        assert result_a.consensus == result_b.consensus, "tie-break is not stable"
        # First-seen wins: "alpha" is the first candidate in insertion order.
        assert result_a.consensus == "alpha"
