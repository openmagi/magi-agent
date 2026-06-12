# tests/benchmarks/taubench/test_agent.py
from __future__ import annotations

import sys
import types
from dataclasses import dataclass

from benchmarks.taubench.reliability import ReliabilityConfig


@dataclass
class FakeAction:
    name: str
    kwargs: dict


def _install_fake_tau_bench(monkeypatch) -> None:
    base = types.ModuleType("tau_bench.agents.base")

    class Agent:  # minimal base
        pass

    base.Agent = Agent

    tb_types = types.ModuleType("tau_bench.types")
    tb_types.RESPOND_ACTION_NAME = "respond"
    tb_types.Action = FakeAction

    @dataclass
    class SolveResult:
        reward: float
        info: dict
        messages: list
        total_cost: float

    tb_types.SolveResult = SolveResult

    monkeypatch.setitem(sys.modules, "tau_bench", types.ModuleType("tau_bench"))
    monkeypatch.setitem(sys.modules, "tau_bench.agents", types.ModuleType("tau_bench.agents"))
    monkeypatch.setitem(sys.modules, "tau_bench.agents.base", base)
    monkeypatch.setitem(sys.modules, "tau_bench.types", tb_types)


def test_solve_threads_config_and_one_shared_ledger(monkeypatch) -> None:
    _install_fake_tau_bench(monkeypatch)
    from benchmarks.taubench import agent as agent_mod
    from benchmarks.taubench.episode import EpisodeResult

    captured: dict[str, object] = {}

    def fake_build_tools(env, *, state, action_factory, reliability=None, ledger=None):
        captured["tools_reliability"] = reliability
        captured["tools_ledger"] = ledger
        return ["TOOL"]

    def fake_run_episode(
        env, task_index, *, state, runner_factory, action_factory,
        respond_action_name, max_steps, instruction=None, tools=None,
        session_id=None, reliability=None, ledger=None,
    ):
        captured["ep_reliability"] = reliability
        captured["ep_ledger"] = ledger
        return EpisodeResult(reward=1.0, done=True, turns=1)

    monkeypatch.setattr(agent_mod, "build_env_function_tools", fake_build_tools)
    monkeypatch.setattr(agent_mod, "run_episode", fake_run_episode)

    @dataclass
    class FakeEnv:
        wiki: str = "POLICY"

    cfg = ReliabilityConfig(arg_validation=True, dup_write_guard=True, verify_before_final=True)
    agent = agent_mod.build_magi_tau_agent(runner_factory=lambda **k: None, reliability=cfg)
    result = agent.solve(FakeEnv(), task_index=0, max_num_steps=5)

    assert result.reward == 1.0
    assert captured["tools_reliability"] is cfg
    assert captured["ep_reliability"] is cfg
    assert captured["tools_ledger"] is not None
    assert captured["tools_ledger"] is captured["ep_ledger"]  # SAME shared ledger


def test_solve_defaults_reliability_off(monkeypatch) -> None:
    _install_fake_tau_bench(monkeypatch)
    from benchmarks.taubench import agent as agent_mod
    from benchmarks.taubench.episode import EpisodeResult

    captured: dict[str, object] = {}

    def fake_build_tools(env, *, state, action_factory, reliability=None, ledger=None):
        captured["reliability"] = reliability
        return []

    def fake_run_episode(env, task_index, **kw):
        captured["ep_reliability"] = kw.get("reliability")
        return EpisodeResult(reward=0.0, done=False, turns=0)

    monkeypatch.setattr(agent_mod, "build_env_function_tools", fake_build_tools)
    monkeypatch.setattr(agent_mod, "run_episode", fake_run_episode)

    @dataclass
    class FakeEnv:
        wiki: str = "POLICY"

    agent = agent_mod.build_magi_tau_agent(runner_factory=lambda **k: None)
    agent.solve(FakeEnv(), task_index=0)
    assert captured["reliability"] is None
    assert captured["ep_reliability"] is None
