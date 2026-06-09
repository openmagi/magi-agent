# tests/benchmarks/taubench/test_tau_env.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from magi_agent.benchmarks.taubench.episode import EpisodeState
from magi_agent.benchmarks.taubench.tau_env import build_env_tool_callables


@dataclass
class FakeAction:
    name: str
    kwargs: dict


@dataclass
class FakeResp:
    observation: str
    reward: float
    done: bool


@dataclass
class FakeEnv:
    tools_info: tuple = (
        {"type": "function", "function": {"name": "get_order", "description": "d",
         "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}},
    )
    steps: list = field(default_factory=list)

    def step(self, action):
        self.steps.append(action)
        return FakeResp(observation=f"order {action.kwargs.get('id')}", reward=0.0, done=False)


def test_tool_callable_routes_to_env_step_and_records_state() -> None:
    env = FakeEnv()
    state = EpisodeState()
    callables = build_env_tool_callables(env, state=state, action_factory=FakeAction)
    assert set(callables) == {"get_order"}
    out = asyncio.run(callables["get_order"]({"id": "A1"}, None))
    assert "order A1" in str(out)
    assert env.steps[0].name == "get_order"
    assert env.steps[0].kwargs == {"id": "A1"}
