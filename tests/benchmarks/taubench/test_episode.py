# tests/benchmarks/taubench/test_episode.py
from __future__ import annotations

from dataclasses import dataclass, field

from magi_agent.benchmarks.taubench.episode import EpisodeState, run_episode


# --- fakes (no tau_bench, no ADK) ---
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
    wiki: str = "POLICY"
    tools_info: tuple = ()
    script: list = field(default_factory=list)  # list of FakeResp for each step
    steps: list = field(default_factory=list)
    _i: int = 0

    def reset(self, task_index):
        return FakeResp(observation="hello (user)", reward=0.0, done=False)

    def step(self, action):
        self.steps.append(action)
        resp = self.script[self._i]
        self._i += 1
        return resp


def make_fake_runner_factory(turns_text: list[str], state: EpisodeState, env: FakeEnv):
    """Returns a runner_factory whose runner.run_async yields one turn's text and,
    per turn, performs one env tool call (simulating the agent using a tool)."""
    calls = {"n": 0}

    def factory(*, instruction, tools):
        class _Runner:
            async def run_async(self, *, user_id, session_id, new_message):
                # simulate: agent calls one tool this turn, then emits text
                resp = env.step(FakeAction(name="lookup", kwargs={}))
                state.observe(resp.reward, resp.done)
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(turns_text[idx])
        return _Runner()

    return factory


class _Event:
    def __init__(self, text):
        self.content = _Content(text)


class _Content:
    def __init__(self, text):
        self.parts = [_Part(text)]


class _Part:
    def __init__(self, text):
        self.text = text


def test_run_episode_drives_turns_until_done_and_returns_reward() -> None:
    env = FakeEnv(script=[
        FakeResp("tool-result-1", 0.0, False),   # turn 1 tool call
        FakeResp("user-reply-1", 0.0, False),     # turn 1 respond -> user reply
        FakeResp("tool-result-2", 0.0, False),    # turn 2 tool call
        FakeResp("###STOP###", 1.0, True),         # turn 2 respond -> done, reward 1
    ])
    state = EpisodeState()
    factory = make_fake_runner_factory(["msg to user 1", "msg to user 2"], state, env)
    result = run_episode(
        env, task_index=0,
        state=state,
        runner_factory=factory,
        action_factory=FakeAction,
        respond_action_name="respond",
        max_steps=10,
    )
    assert result.done is True
    assert result.reward == 1.0
    assert result.turns == 2
    # respond actions were sent with the agent's text
    respond_actions = [a for a in env.steps if a.name == "respond"]
    assert respond_actions[0].kwargs["content"] == "msg to user 1"


def test_run_episode_marks_infra_error_when_runner_raises() -> None:
    env = FakeEnv(script=[])

    def boom_factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                raise RuntimeError("provider down")
                yield  # unreachable; makes this an async generator
        return _R()

    result = run_episode(
        env, task_index=0, state=EpisodeState(), runner_factory=boom_factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=5,
    )
    assert result.infra_error is True
    assert result.done is False


def test_run_episode_stops_at_max_steps() -> None:
    env = FakeEnv(script=[FakeResp("reply", 0.0, False)] * 20)
    state = EpisodeState()
    factory = make_fake_runner_factory(["t"] * 20, state, env)
    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory, action_factory=FakeAction,
        respond_action_name="respond", max_steps=3,
    )
    assert result.turns == 3
    assert result.done is False


def test_no_respond_when_tool_finishes_episode() -> None:
    """If a tool call sets done=True mid-turn, no 'respond' action should be sent."""
    env = FakeEnv(script=[FakeResp("done-by-tool", 1.0, True)])  # the tool step finishes it
    state = EpisodeState()

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                resp = env.step(FakeAction(name="finish", kwargs={}))
                state.observe(resp.reward, resp.done)
                yield _Event("done")
        return _R()

    result = run_episode(env, task_index=0, state=state, runner_factory=factory,
                         action_factory=FakeAction, respond_action_name="respond", max_steps=5)
    assert result.done is True
    assert result.reward == 1.0
    assert [a for a in env.steps if a.name == "respond"] == []  # no extra respond
