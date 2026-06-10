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
            async def run_async(self, **kw):
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


from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig, WriteLedger


def _text_of(content) -> str:
    parts = getattr(content, "parts", None) or []
    return "".join(getattr(p, "text", "") or "" for p in parts)


def test_l2_injects_one_nudge_on_unsupported_success_claim() -> None:
    env = FakeEnv(script=[
        FakeResp("user-reply-after-nudge", 0.0, False),  # respond after the 2nd turn
        FakeResp("###STOP###", 1.0, True),                # respond after the 3rd turn -> done
    ])
    state = EpisodeState()
    led = WriteLedger()
    seen: list[str] = []
    calls = {"n": 0}
    texts = ["Your reservation is booked! Reservation ID HATHAT", "ok, fixing now", "done"]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                seen.append(_text_of(kw["new_message"]))
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=6,
        reliability=ReliabilityConfig(verify_before_final=True), ledger=led,
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    # The unsupported success claim was NOT routed as a respond — the nudge replaced it.
    assert "Your reservation is booked! Reservation ID HATHAT" not in respond_contents
    assert respond_contents[0] == "ok, fixing now"
    # The nudge was delivered to the agent as the next observation.
    assert any("Re-check the tool results" in m for m in seen)
    assert result.done is True


def test_l2_silent_when_write_succeeded() -> None:
    env = FakeEnv(script=[FakeResp("###STOP###", 1.0, True)])
    state = EpisodeState()
    led = WriteLedger()
    led.record("book_reservation", {"x": 1}, ok=True)
    calls = {"n": 0}
    texts = ["Your reservation is booked. Reservation ID R1."]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=5,
        reliability=ReliabilityConfig(verify_before_final=True), ledger=led,
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert respond_contents == ["Your reservation is booked. Reservation ID R1."]
    assert result.done is True


def test_l4_injects_completion_review_on_refusal_conclusion() -> None:
    env = FakeEnv(script=[
        FakeResp("user-reply", 0.0, False),
        FakeResp("###STOP###", 1.0, True),
    ])
    state = EpisodeState()
    seen: list[str] = []
    calls = {"n": 0}
    texts = ["I'm sorry, I'm unable to provide that compensation.", "ok done", "bye"]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                seen.append(_text_of(kw["new_message"]))
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=6,
        reliability=ReliabilityConfig(completion_review=True),
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert "I'm sorry, I'm unable to provide that compensation." not in respond_contents
    assert respond_contents[0] == "ok done"
    assert any("every concrete action" in m for m in seen)
    assert result.done is True


def test_l4_silent_on_info_question() -> None:
    env = FakeEnv(script=[FakeResp("###STOP###", 1.0, True)])
    state = EpisodeState()
    calls = {"n": 0}
    texts = ["Can you confirm your travel dates first?"]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=5,
        reliability=ReliabilityConfig(completion_review=True),
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert respond_contents == ["Can you confirm your travel dates first?"]
    assert result.done is True


def test_l2_and_l4_independent_latches() -> None:
    env = FakeEnv(script=[
        FakeResp("user-reply", 0.0, False),
        FakeResp("###STOP###", 1.0, True),
    ])
    state = EpisodeState()
    led = WriteLedger()  # empty -> L2 fires on the success claim
    seen: list[str] = []
    calls = {"n": 0}
    texts = [
        "Your reservation is booked! Reservation ID X",   # turn 1 -> L2 nudge
        "I'm sorry, I am unable to complete the rest.",    # turn 2 -> L4 nudge
        "ok",                                              # turn 3 -> respond
        "done",                                            # turn 4 -> respond -> STOP
    ]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                seen.append(_text_of(kw["new_message"]))
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=8,
        reliability=ReliabilityConfig(verify_before_final=True, completion_review=True),
        ledger=led,
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert "Your reservation is booked! Reservation ID X" not in respond_contents
    assert "I'm sorry, I am unable to complete the rest." not in respond_contents
    assert respond_contents[0] == "ok"
    assert any("Re-check the tool results" in m for m in seen)   # L2 nudge delivered
    assert any("every concrete action" in m for m in seen)       # L4 nudge delivered
    assert result.done is True
