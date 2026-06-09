# tests/benchmarks/taubench/test_reliability_integration.py
"""End-to-end seam test: a write recorded by a tau_env tool callable becomes
visible to the L2 verify_final check in run_episode via the SHARED WriteLedger.

This exercises the real cross-file dataflow (tau_env records -> episode reads)
that agent.py wires together, rather than each lever in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from magi_agent.benchmarks.taubench.episode import EpisodeState, run_episode
from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig, WriteLedger
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
class _IntegrationEnv:
    """A write tool that errors, plus respond handling that STOPs after one respond."""

    wiki: str = "POLICY"
    tools_info: tuple = (
        {"type": "function", "function": {"name": "book_reservation", "description": "d",
         "parameters": {"type": "object",
            "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]}}},
    )
    steps: list = field(default_factory=list)
    _responds: int = 0

    def reset(self, task_index):
        return FakeResp("hello (user)", 0.0, False)

    def step(self, action):
        self.steps.append(action)
        if action.name == "book_reservation":
            return FakeResp("Error: no such flight", 0.0, False)  # failed write
        # respond -> user-sim STOPs after the first real respond
        self._responds += 1
        return FakeResp("###STOP###", 1.0, True)


class _Event:
    def __init__(self, text):
        self.content = _Content(text)


class _Content:
    def __init__(self, text):
        self.parts = [_Part(text)]


class _Part:
    def __init__(self, text):
        self.text = text


def _text_of(content) -> str:
    parts = getattr(content, "parts", None) or []
    return "".join(getattr(p, "text", "") or "" for p in parts)


def test_failed_write_recorded_by_tool_drives_l2_nudge_in_loop() -> None:
    env = _IntegrationEnv()
    state = EpisodeState()
    ledger = WriteLedger()
    cfg = ReliabilityConfig(verify_before_final=True)

    # The tool callables (tau_env) record write outcomes into the SHARED ledger.
    callables = build_env_tool_callables(
        env, state=state, action_factory=FakeAction, reliability=cfg, ledger=ledger
    )

    seen: list[str] = []
    calls = {"n": 0}
    texts = ["Your reservation is booked! Reservation ID X", "ok"]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                seen.append(_text_of(kw["new_message"]))
                idx = calls["n"]
                calls["n"] += 1
                if idx == 0:
                    # turn 1: the agent calls the write tool, which errors
                    await callables["book_reservation"]({"user_id": "u1"}, None)
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=6,
        reliability=cfg, ledger=ledger,
    )

    # The tool callable recorded the failed write into the shared ledger...
    assert ledger.last_write_errored() is True
    # ...so L2 read it and nudged: the unsupported success claim was NOT routed
    # as a respond, and the nudge reached the agent as the next observation.
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert "Your reservation is booked! Reservation ID X" not in respond_contents
    assert respond_contents[0] == "ok"
    assert any("Re-check the tool results" in m for m in seen)
    assert result.done is True
