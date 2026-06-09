# magi_agent/benchmarks/taubench/scorer.py
"""Pure τ-bench scorer. No tau_bench import, no model/provider calls."""
from __future__ import annotations

from math import comb

from pydantic import BaseModel, ConfigDict

_FROZEN = ConfigDict(frozen=True, extra="forbid")


def pass_hat_k(successes_per_task: list[int], *, trials: int, k: int) -> float:
    if k > trials or trials <= 0 or not successes_per_task:
        return 0.0
    denom = comb(trials, k)
    per_task = [comb(c, k) / denom for c in successes_per_task]
    return sum(per_task) / len(per_task)


class TauReport(BaseModel):
    model_config = _FROZEN
    trials: int
    pass_hat_k: dict[int, float]
    avg_reward: float


def score(*, successes_per_task: list[int], trials: int, rewards: list[float]) -> TauReport:
    phk = {k: pass_hat_k(successes_per_task, trials=trials, k=k) for k in range(1, trials + 1)}
    avg = sum(rewards) / len(rewards) if rewards else 0.0
    return TauReport(trials=trials, pass_hat_k=phk, avg_reward=avg)
