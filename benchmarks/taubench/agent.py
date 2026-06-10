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
from benchmarks.taubench.tau_env import build_env_function_tools


def build_magi_tau_agent(*, runner_factory: Callable[..., Any]) -> Any:
    """Construct a tau_bench Agent subclass bound to a magi runner_factory.

    runner_factory(*, instruction, tools) -> object with .run_async(...).
    In production this wraps build_cli_model_runner; tests inject a fake.

    tau_bench.agents.base.Agent, tau_bench.types.Action, RESPOND_ACTION_NAME,
    and SolveResult are imported lazily here so the module can be imported
    without tau_bench being installed.

    Expected tau_bench API (confirm against cloned repo at live time):
    - Agent.solve(env, task_index, max_num_steps=30) -> SolveResult
    - Action(name: str, kwargs: dict)
    - RESPOND_ACTION_NAME: str  (== "respond")
    - SolveResult(reward, info, messages, total_cost)
    """
    from tau_bench.agents.base import Agent  # noqa: PLC0415
    from tau_bench.types import RESPOND_ACTION_NAME, Action, SolveResult  # noqa: PLC0415

    class MagiTauAgent(Agent):
        def solve(
            self,
            env: Any,
            task_index: int | None = None,
            max_num_steps: int = 30,
        ) -> Any:
            state = EpisodeState()
            # tools share `state`; run_episode uses the SAME state so
            # tool-step done/reward and respond-step done/reward are both
            # visible to one object.
            tools = build_env_function_tools(env, state=state, action_factory=Action)
            result = run_episode(
                env,
                task_index if task_index is not None else 0,
                state=state,
                runner_factory=runner_factory,  # (*, instruction, tools) -> runner
                action_factory=Action,
                respond_action_name=RESPOND_ACTION_NAME,
                max_steps=max_num_steps,
                instruction=env.wiki,
                tools=tools,
            )
            return SolveResult(
                reward=result.reward,
                info={"turns": result.turns, "infra_error": result.infra_error},
                messages=[],
                total_cost=0.0,
            )

    return MagiTauAgent()


__all__ = ["build_magi_tau_agent"]
