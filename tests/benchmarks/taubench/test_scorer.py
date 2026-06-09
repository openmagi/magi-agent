# tests/benchmarks/taubench/test_scorer.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.taubench.scorer import pass_hat_k, score


def test_pass_hat_1_is_average_success_rate() -> None:
    # 2 tasks, 4 trials each: task A 4/4, task B 2/4 -> pass^1 = (1.0+0.5)/2 = 0.75
    assert pass_hat_k([4, 2], trials=4, k=1) == pytest.approx(0.75)


def test_pass_hat_k_uses_combinatorics() -> None:
    # task with 2 successes of 4 trials: C(2,2)/C(4,2) = 1/6 at k=2
    assert pass_hat_k([2], trials=4, k=2) == pytest.approx(1 / 6)


def test_pass_hat_k_zero_when_successes_below_k() -> None:
    assert pass_hat_k([1], trials=4, k=2) == 0.0


def test_score_reports_pass_hat_1_to_k_and_avg_reward() -> None:
    report = score(successes_per_task=[4, 2], trials=4, rewards=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0])
    assert report.pass_hat_k[1] == pytest.approx(0.75)
    assert report.pass_hat_k[4] == pytest.approx((1.0 + 0.0) / 2)  # only task A all-4
    assert report.avg_reward == pytest.approx(6 / 8)
    assert report.trials == 4


def test_pass_hat_k_zero_when_k_exceeds_trials() -> None:
    assert pass_hat_k([4], trials=4, k=5) == 0.0


def test_pass_hat_k_clamps_successes_above_trials() -> None:
    # defensive: a miscount c>trials must not yield pass^k > 1.0
    assert pass_hat_k([5], trials=4, k=1) == 1.0


def test_pass_hat_k_raises_on_nonpositive_trials() -> None:
    with pytest.raises(ValueError):
        pass_hat_k([1], trials=0, k=1)


def test_score_avg_reward_zero_for_empty_rewards() -> None:
    report = score(successes_per_task=[2], trials=4, rewards=[])
    assert report.avg_reward == 0.0
