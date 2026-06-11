# benchmarks/taubench/agent.py
"""MagiTauAgent: drives magi's real runner as a tau-bench agent.

tau_bench is imported ONLY lazily inside build_magi_tau_agent so that
``import benchmarks.taubench.agent`` succeeds without tau_bench
installed.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchmarks.taubench.episode import EpisodeState, run_episode
from benchmarks.taubench.reliability import ReliabilityConfig
from benchmarks.taubench.tau_env import build_env_function_tools


def build_magi_tau_agent(
    *,
    runner_factory: Callable[..., Any],
    reliability: ReliabilityConfig | None = None,
) -> Any:
    """Construct a tau_bench Agent subclass bound to a magi runner_factory.

    runner_factory(*, instruction, tools) -> object with .run_async(...).
    reliability: optional ReliabilityConfig enabling the v2 driver-boundary
    levers (default None = all levers off). One WriteLedger is created per
    solve() and shared between the tool builders (L1/L3) and the episode loop
    (L2) so all levers see the same write history.
    """
    from tau_bench.agents.base import Agent  # noqa: PLC0415
    from tau_bench.types import RESPOND_ACTION_NAME, Action, SolveResult  # noqa: PLC0415

    from benchmarks.taubench.reliability import WriteLedger  # noqa: PLC0415

    class MagiTauAgent(Agent):
        def solve(
            self,
            env: Any,
            task_index: int | None = None,
            max_num_steps: int = 30,
        ) -> Any:
            state = EpisodeState()
            ledger = WriteLedger()
            tools = build_env_function_tools(
                env,
                state=state,
                action_factory=Action,
                reliability=reliability,
                ledger=ledger,
            )
            result = run_episode(
                env,
                task_index if task_index is not None else 0,
                state=state,
                runner_factory=runner_factory,
                action_factory=Action,
                respond_action_name=RESPOND_ACTION_NAME,
                max_steps=max_num_steps,
                instruction=env.wiki,
                tools=tools,
                reliability=reliability,
                ledger=ledger,
            )
            return SolveResult(
                reward=result.reward,
                info={"turns": result.turns, "infra_error": result.infra_error},
                messages=[],
                total_cost=0.0,
            )

    return MagiTauAgent()


__all__ = ["build_magi_tau_agent"]
