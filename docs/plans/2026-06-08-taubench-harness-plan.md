# τ-bench Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A default-OFF τ-bench measurement harness that drives magi's real ADK runner as the τ-bench agent and reports pass^k for magi-full (control-plane flags ON) vs magi-vanilla (bare), plus a published-reference comparison.

**Architecture:** Pure, tau-bench-free core (scorer, episode-loop, env-tool translation, config) tested with a FakeEnv + fake runner; a thin `agent.py` that subclasses tau-bench's `Agent` and delegates to the pure loop; a CLI that gates on `MAGI_TAUBENCH_ENABLED`, binds the agent to Sonnet 4.5 and the user-sim to gpt-4o, and runs the real `build_cli_model_runner`.

**Tech Stack:** Python 3.11+, `uv` for env/test, pytest, pydantic v2, Google ADK (`google.adk`, `google.genai.types`), tau-bench (cloned, MIT, NOT vendored into git).

**Reference files to mirror (read before starting):**
- `magi_agent/benchmarks/gaia/harness.py` — the in-process `build_cli_model_runner` + `run_async` drive pattern (the template).
- `magi_agent/cli/real_runner.py` — `build_cli_model_runner(config, *, instruction, tools, model_factory, workspace_root, user_id, session_id)` and `CliModelRunner.run_async(user_id, session_id, new_message)`.
- `magi_agent/adk_bridge/tool_adapter.py` — `_json_schema_to_genai_schema` (reuse) and the `invoke(arguments, tool_context)` + `FunctionTool(callable, require_confirmation=False)` pattern.
- `magi_agent/adk_bridge/control_plane.py` — the six `*_ENABLED` env flags (the determinism controls).
- `magi_agent/benchmarks/legalbench/cli.py` — gate + CLI pattern; `magi_agent/benchmarks/gaia/scorer.py` — scorer style.
- Spec: `docs/plans/2026-06-08-taubench-harness-design.md`.
- τ-bench API: `Agent.solve(env, task_index, max_num_steps) -> SolveResult`; `env.reset(task_index)` (first user msg + `.wiki` + `.tools_info`); `env.step(Action(name, kwargs)) -> env_response(.observation/.reward/.done)`; `Action(name: str, kwargs: dict)`; `RESPOND_ACTION_NAME = "respond"`.

**Conventions:**
- `from __future__ import annotations`; frozen pydantic models (`ConfigDict(frozen=True, extra="forbid")`); explicit return types.
- Tests under `tests/benchmarks/taubench/`; run with `uv run --extra cli --extra dev pytest <path> -v`.
- **No test may import `tau_bench` or hit the network.** The pure modules (`scorer`, `episode`, `tau_env`, `config`) must not import `tau_bench`; only `agent.py` does.
- Commit after every task.

**Shared vocabulary (defined in Task 1–3, used throughout):**
- `pass_hat_k(successes_per_task: list[int], trials: int, k: int) -> float`
- `EpisodeState` — mutable holder of the latest `(reward, done)` seen across `env.step` calls in one episode.
- `EpisodeResult` — frozen: `reward: float`, `done: bool`, `turns: int`, `infra_error: bool`.
- `run_episode(env, task_index, *, runner_factory, action_factory, respond_action_name, max_steps) -> EpisodeResult` — the tau-bench-free multi-turn loop.
- `Config = Literal["full", "vanilla"]`; `FULL_CAPABILITY_FLAGS: dict[str, str]` (the six control-plane env flags → "1").

---

## Task 0: Baseline + tau-bench setup doc

**Files:**
- Create: `magi_agent/benchmarks/taubench/__init__.py` (empty)
- Create: `data/taubench/README.md`

- [ ] **Step 1: Confirm clean baseline**

Run: `uv run --extra cli --extra dev pytest magi_agent/benchmarks -q`
Expected: PASS or "no tests ran". If failures, STOP and report.

- [ ] **Step 2: Create the package init** — empty file `magi_agent/benchmarks/taubench/__init__.py`.

- [ ] **Step 3: Write `data/taubench/README.md`** documenting the live-run setup (tau-bench is clone-only, MIT, NOT committed here):

```markdown
# τ-bench setup (live runs only)

tau-bench is clone-only (MIT), not vendored into this repo. Tests use a fake env
and need none of this. For a LIVE run:

    git clone https://github.com/sierra-research/tau-bench /path/to/tau-bench
    pip install -e /path/to/tau-bench   # into the same env as magi-agent

Set `TAUBENCH_PATH=/path/to/tau-bench` (or install it importable as `tau_bench`).
Keys: ANTHROPIC_API_KEY (agent = Sonnet 4.5), OPENAI_API_KEY (user-sim = gpt-4o).
Enable the harness: MAGI_TAUBENCH_ENABLED=1.
```

- [ ] **Step 4: Commit**

```bash
git add magi_agent/benchmarks/taubench/__init__.py data/taubench/README.md
git commit -m "chore(taubench): package init + setup doc"
```

---

## Task 1: Pure pass^k scorer

**Files:**
- Create: `magi_agent/benchmarks/taubench/scorer.py`
- Test: `tests/benchmarks/taubench/test_scorer.py`

`pass^k` = mean over tasks of `C(successes, k) / C(trials, k)` (τ-bench paper formula).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_scorer.py -v`
Expected: FAIL — `ModuleNotFoundError: ...taubench.scorer`

- [ ] **Step 3: Write `scorer.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_scorer.py -v`
Expected: PASS (note: the test calls `pass_hat_k([4,2], trials=4, k=1)` — update the test's calls to keyword form `pass_hat_k([4, 2], trials=4, k=1)` to match the signature; the snippet above already uses keywords).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/scorer.py tests/benchmarks/taubench/test_scorer.py
git commit -m "feat(taubench): pure pass^k scorer"
```

---

## Task 2: Config flags (full vs vanilla)

**Files:**
- Create: `magi_agent/benchmarks/taubench/config.py`
- Test: `tests/benchmarks/taubench/test_config.py`

`full` = the six control-plane flags set to "1"; `vanilla` = none. Provided as an
explicit env-overlay dict so the harness can pass it when building the runner.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmarks/taubench/test_config.py
from __future__ import annotations

from magi_agent.benchmarks.taubench.config import FULL_CAPABILITY_FLAGS, flags_for


def test_full_sets_the_six_control_plane_flags() -> None:
    flags = flags_for("full")
    assert flags == FULL_CAPABILITY_FLAGS
    assert flags["MAGI_SELF_REVIEW_ENABLED"] == "1"
    assert set(flags) == {
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
        "MAGI_SELF_REVIEW_ENABLED",
    }


def test_vanilla_sets_no_flags() -> None:
    assert flags_for("vanilla") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `config.py`** (confirm the six flag names against `magi_agent/adk_bridge/control_plane.py` — they are the `*_ENABLED` env constants there)

```python
# magi_agent/benchmarks/taubench/config.py
from __future__ import annotations

from typing import Literal

Config = Literal["full", "vanilla"]

# The six default-OFF control-plane flags that build_default_plane reads.
FULL_CAPABILITY_FLAGS: dict[str, str] = {
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
    "MAGI_LOOP_GUARD_ENABLED": "1",
    "MAGI_ERROR_RECOVERY_ENABLED": "1",
    "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
    "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
    "MAGI_SELF_REVIEW_ENABLED": "1",
}


def flags_for(config: Config) -> dict[str, str]:
    return dict(FULL_CAPABILITY_FLAGS) if config == "full" else {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/config.py tests/benchmarks/taubench/test_config.py
git commit -m "feat(taubench): full-vs-vanilla control-plane flag set"
```

---

## Task 3: Episode loop (tau-bench-free, the core)

**Files:**
- Create: `magi_agent/benchmarks/taubench/episode.py`
- Test: `tests/benchmarks/taubench/test_episode.py`

The multi-turn loop, with NO `tau_bench` import — it receives an `action_factory`
(callable `(name, kwargs) -> Action`) and `respond_action_name`, so it is testable
with a fake env + fake action + fake runner. One `run_async` generator = one agent
turn (ADK loops tool calls internally until the agent yields final text). After the
turn, the agent's accumulated text is sent to the user via `respond`, and the
user-sim reply becomes the next message.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_episode.py -v`
Expected: FAIL — `ModuleNotFoundError: ...taubench.episode`

- [ ] **Step 3: Write `episode.py`**

```python
# magi_agent/benchmarks/taubench/episode.py
"""tau-bench-free multi-turn episode loop. No tau_bench import, no network."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from google.genai import types


class EpisodeState:
    """Tracks the latest (reward, done) seen across env.step calls in one episode."""

    def __init__(self) -> None:
        self.reward: float = 0.0
        self.done: bool = False

    def observe(self, reward: float, done: bool) -> None:
        self.reward = reward
        self.done = done


class EpisodeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reward: float
    done: bool
    turns: int
    infra_error: bool = False


def _user_content(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def run_episode(
    env: Any,
    task_index: int,
    *,
    state: EpisodeState,
    runner_factory: Callable[..., Any],
    action_factory: Callable[[str, dict], Any],
    respond_action_name: str,
    max_steps: int,
    instruction: str | None = None,
    tools: list[object] | None = None,
) -> EpisodeResult:
    # `state` is SHARED: the env tools (built by tau_env) record (reward, done) into
    # it on every tool env.step, and this loop records the respond steps. Either can
    # set done. The caller owns it so the tools and the loop see the same object.
    reset = env.reset(task_index)
    obs = reset.observation
    runner = runner_factory(instruction=instruction or env.wiki, tools=tools)

    async def _run_turn(message: str) -> str:
        texts: list[str] = []
        async for event in runner.run_async(
            user_id="taubench", session_id=f"task-{task_index}", new_message=_user_content(message)
        ):
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", None) or []:
                t = getattr(part, "text", None)
                if isinstance(t, str) and t:
                    texts.append(t)
        return "\n".join(texts)

    turns = 0
    while not state.done and turns < max_steps:
        try:
            agent_text = asyncio.run(_run_turn(obs))
        except Exception:
            return EpisodeResult(reward=0.0, done=False, turns=turns, infra_error=True)
        turns += 1
        # the agent's tool calls already hit env.step during the turn (via FunctionTools,
        # which call state.observe). Now route the agent's user-facing text as a respond.
        resp = env.step(action_factory(respond_action_name, {"content": agent_text}))
        state.observe(resp.reward, resp.done)
        obs = resp.observation
    return EpisodeResult(reward=state.reward, done=state.done, turns=turns)
```

Note: the FunctionTools built in Task 4 call `state.observe` on each tool `env.step`.
For this task's test, the fake runner calls `state.observe` itself (simulating that).
The loop owns the `respond` step + termination. `asyncio.run` per turn matches GAIA's
single-turn pattern; the session persists across turns via the same `session_id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_episode.py -v`
Expected: PASS

- [ ] **Step 5: Add an infra-error + max-steps test**

```python
# append to tests/benchmarks/taubench/test_episode.py
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_episode.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add magi_agent/benchmarks/taubench/episode.py tests/benchmarks/taubench/test_episode.py
git commit -m "feat(taubench): tau-bench-free multi-turn episode loop"
```

---

## Task 4: Env→ADK tool translation

**Files:**
- Create: `magi_agent/benchmarks/taubench/tau_env.py`
- Test: `tests/benchmarks/taubench/test_tau_env.py`

Build one ADK `FunctionTool` per τ-bench tool; each tool's callable invokes
`env.step(Action(name, kwargs))`, records `(reward, done)` into `EpisodeState`, and
returns the observation string to the agent. **Mirror the existing magi pattern** in
`adk_bridge/tool_adapter.py`: a callable `invoke(arguments, tool_context)` with
`__name__`/`__doc__` set, the `_json_schema_to_genai_schema` converter reused for the
OpenAI-tool `parameters` schema, wrapped in `FunctionTool(callable, require_confirmation=False)`.

- [ ] **Step 1: Write the failing test** (no tau_bench; fake env + fake action)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_tau_env.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `tau_env.py`** — the callable factory (pure: no tau_bench import; the ADK `FunctionTool` wrapping is a separate function so the callable is unit-testable)

```python
# magi_agent/benchmarks/taubench/tau_env.py
"""Translate a τ-bench env's tools into ADK FunctionTools that route to env.step.

No tau_bench import here — the Action constructor is injected (action_factory)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from magi_agent.benchmarks.taubench.episode import EpisodeState


def _tool_specs(env: Any) -> list[dict]:
    specs = []
    for entry in env.tools_info:
        fn = entry.get("function", entry) if isinstance(entry, dict) else {}
        name = fn.get("name")
        if name:
            specs.append({"name": name, "description": fn.get("description", ""),
                          "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
    return specs


def build_env_tool_callables(
    env: Any, *, state: EpisodeState, action_factory: Callable[[str, dict], Any]
) -> dict[str, Callable]:
    """One async callable per tool. Each calls env.step(Action(name, kwargs)),
    records (reward, done) into state, returns the observation."""
    callables: dict[str, Callable] = {}
    for spec in _tool_specs(env):
        name = spec["name"]

        def _make(tool_name: str) -> Callable:
            async def invoke(arguments: dict, tool_context: object = None) -> str:
                resp = env.step(action_factory(tool_name, dict(arguments or {})))
                state.observe(resp.reward, resp.done)
                return resp.observation
            invoke.__name__ = tool_name
            return invoke

        callables[name] = _make(name)
    return callables


def build_env_function_tools(
    env: Any, *, state: EpisodeState, action_factory: Callable[[str, dict], Any]
) -> list[object]:
    """Wrap each callable as an ADK FunctionTool with the τ-bench parameter schema.

    MIRROR `magi_agent/adk_bridge/tool_adapter.py`:
    - reuse `_json_schema_to_genai_schema(spec["parameters"])` for the declaration,
    - set `invoke.__doc__ = description`,
    - wrap via `google.adk.tools.FunctionTool(invoke, require_confirmation=False)`.
    CONFIRM the exact way tool_adapter attaches the explicit genai Schema to the
    FunctionTool (it does so for core tools) and replicate it here. Imports of
    google.adk / google.genai stay inside this function (cold-start discipline)."""
    from google.adk.tools import FunctionTool  # noqa: PLC0415

    callables = build_env_tool_callables(env, state=state, action_factory=action_factory)
    specs = {s["name"]: s for s in _tool_specs(env)}
    tools: list[object] = []
    for name, invoke in callables.items():
        invoke.__doc__ = specs[name]["description"]
        tools.append(FunctionTool(invoke, require_confirmation=False))
    return tools
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_tau_env.py -v`
Expected: PASS (only `build_env_tool_callables` is unit-tested; `build_env_function_tools` needs ADK and is exercised in the live smoke).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/tau_env.py tests/benchmarks/taubench/test_tau_env.py
git commit -m "feat(taubench): env tool -> ADK FunctionTool translation routing to env.step"
```

---

## Task 5: tau-bench agent adapter (thin; imports tau_bench)

**Files:**
- Create: `magi_agent/benchmarks/taubench/agent.py`

This is the ONLY module importing `tau_bench`. It subclasses tau-bench's `Agent`,
builds the real runner, and delegates to `run_episode`. Not unit-tested (requires
tau_bench); exercised by the live smoke.

- [ ] **Step 1: Write `agent.py`** — READ the cloned `tau_bench/agents/base.py` and `tau_bench/types.py` to confirm `Agent.solve` signature, `Action`, `RESPOND_ACTION_NAME`, and `SolveResult` fields, then implement:

```python
# magi_agent/benchmarks/taubench/agent.py
"""MagiTauAgent: drives magi's real runner as a tau-bench agent."""
from __future__ import annotations

from typing import Any, Callable

from magi_agent.benchmarks.taubench.episode import EpisodeState, run_episode
from magi_agent.benchmarks.taubench.tau_env import build_env_function_tools


def build_magi_tau_agent(*, runner_factory: Callable[..., Any]) -> Any:
    """Construct a tau_bench Agent subclass bound to a magi runner_factory.

    runner_factory(*, instruction, tools) -> object with .run_async(...). In prod
    this wraps build_cli_model_runner; tests inject a fake."""
    from tau_bench.agents.base import Agent  # noqa: PLC0415
    from tau_bench.types import Action, RESPOND_ACTION_NAME, SolveResult  # noqa: PLC0415

    class MagiTauAgent(Agent):
        def solve(self, env: Any, task_index: int | None = None, max_num_steps: int = 30) -> Any:
            state = EpisodeState()
            # tools share `state`; run_episode uses the SAME state so tool-step
            # done/reward and respond-step done/reward are seen by one object.
            tools = build_env_function_tools(env, state=state, action_factory=Action)
            result = run_episode(
                env, task_index or 0,
                state=state,
                runner_factory=runner_factory,  # (*, instruction, tools) -> runner
                action_factory=Action,
                respond_action_name=RESPOND_ACTION_NAME,
                max_steps=max_num_steps,
                instruction=env.wiki,
                tools=tools,
            )
            return SolveResult(reward=result.reward, info={"turns": result.turns}, messages=[], total_cost=0.0)

    return MagiTauAgent()
```

Note: confirm `SolveResult`'s exact required fields against the cloned repo and
adjust the constructor call. If `SolveResult` differs, fix this one call site.

- [ ] **Step 2: Verify it imports without tau_bench breaking the rest** — there is no unit test here (it imports tau_bench lazily inside `build_magi_tau_agent`, so the module imports cleanly without tau_bench installed):

Run: `uv run --extra dev python -c "import magi_agent.benchmarks.taubench.agent"`
Expected: clean import (no tau_bench needed at import time).

- [ ] **Step 3: Commit**

```bash
git add magi_agent/benchmarks/taubench/agent.py
git commit -m "feat(taubench): MagiTauAgent adapter delegating to the episode loop"
```

---

## Task 6: Harness + CLI (gate, provider binding, report)

**Files:**
- Create: `magi_agent/benchmarks/taubench/harness.py`
- Create: `magi_agent/benchmarks/taubench/cli.py`
- Test: `tests/benchmarks/taubench/test_cli.py`

- [ ] **Step 1: Write the failing gate test**

```python
# tests/benchmarks/taubench/test_cli.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.taubench.cli import GateDisabledError, ensure_enabled


def test_gate_blocks_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_TAUBENCH_ENABLED", raising=False)
    with pytest.raises(GateDisabledError):
        ensure_enabled()


def test_gate_allows_when_set(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TAUBENCH_ENABLED", "1")
    ensure_enabled()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/benchmarks/taubench/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `harness.py`** (aggregates per-task successes from `EpisodeResult`s; pure orchestration over an injected `solve_one` callable so it's testable)

```python
# magi_agent/benchmarks/taubench/harness.py
from __future__ import annotations

from collections.abc import Callable

from magi_agent.benchmarks.taubench.scorer import TauReport, score


def aggregate(results_per_task: list[list[bool]], *, trials: int) -> TauReport:
    """results_per_task[i] = list of per-trial success bools for task i."""
    successes = [sum(1 for ok in trials_list if ok) for trials_list in results_per_task]
    rewards = [1.0 if ok else 0.0 for tl in results_per_task for ok in tl]
    return score(successes_per_task=successes, trials=trials, rewards=rewards)


def run_subset(
    task_indices: list[int], *, trials: int, solve_one: Callable[[int, int], bool]
) -> TauReport:
    """solve_one(task_index, trial) -> success bool. Live wiring injects the real
    tau-bench env + MagiTauAgent; tests inject a deterministic fake."""
    results = [[solve_one(t, trial) for trial in range(trials)] for t in task_indices]
    return aggregate(results, trials=trials)
```

- [ ] **Step 4: Add a harness aggregation test**

```python
# tests/benchmarks/taubench/test_harness.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.taubench.harness import run_subset


def test_run_subset_aggregates_pass_hat_k() -> None:
    # task 0 succeeds every trial; task 1 succeeds on even trials
    def solve_one(task: int, trial: int) -> bool:
        return True if task == 0 else (trial % 2 == 0)

    report = run_subset([0, 1], trials=4, solve_one=solve_one)
    # task0 4/4, task1 2/4 -> pass^1 = (1.0 + 0.5)/2 = 0.75
    assert report.pass_hat_k[1] == pytest.approx(0.75)
    assert report.trials == 4
```

- [ ] **Step 5: Write `cli.py`** — gate + live wiring. The live `solve_one` builds the real runner (Sonnet 4.5 agent) + sets the user-sim to gpt-4o on the tau-bench env, and sets the control-plane flags for `config="full"`. Mirror `benchmarks/legalbench/cli.py` for the gate/provider pattern and `benchmarks/gaia/harness.py` for `build_cli_model_runner`.

```python
# magi_agent/benchmarks/taubench/cli.py
from __future__ import annotations

import os

_GATE_ENV = "MAGI_TAUBENCH_ENABLED"


class GateDisabledError(RuntimeError):
    pass


def ensure_enabled() -> None:
    if os.environ.get(_GATE_ENV) != "1":
        raise GateDisabledError(f"τ-bench harness is gated off. Set {_GATE_ENV}=1 to run.")
```

The full `run_eval(...)` live entry (gated; sets `flags_for(config)` into the
environment before building the runner; constructs the tau-bench env for the domain
with `user_model="gpt-4o"`, builds `MagiTauAgent` via `build_magi_tau_agent` with a
`runner_factory` that calls `build_cli_model_runner(ProviderConfig("anthropic",
"claude-sonnet-4-5", api_key), instruction=..., tools=...)`, runs `run_subset`, prints
the `TauReport` as JSON) is wired here. READ `tau_bench/envs/__init__.py` (or the
domain env constructor) in the cloned repo to confirm how to instantiate an env with
a chosen `user_model`/`user_provider`, and the `--task-split test` selection. Behind
`ensure_enabled()`; importlib-guard `tau_bench` and `litellm` with a clean error if
missing (mirror the legalbench litellm guard). Add `--domain/--max-tasks/--trials/--config`.

**Infra-error handling (per spec):** the live `solve_one` inspects the
`EpisodeResult.infra_error` flag from `run_episode`. On infra-error, retry that
(task, trial) once; if it still infra-errors, count it as a non-success AND
increment a surfaced `infra_error_count` in the report (so infra noise is visible,
never silently a model failure). This keeps `trials` uniform across tasks (so the
`pass_hat_k` formula holds); add `infra_error_count: int` to the printed JSON.

- [ ] **Step 6: Run gate + harness tests**

Run: `uv run --extra dev pytest tests/benchmarks/taubench -v`
Expected: PASS (scorer, config, episode, tau_env, cli gate, harness)

- [ ] **Step 7: Commit**

```bash
git add magi_agent/benchmarks/taubench/harness.py magi_agent/benchmarks/taubench/cli.py tests/benchmarks/taubench/test_cli.py tests/benchmarks/taubench/test_harness.py
git commit -m "feat(taubench): harness aggregation + default-OFF CLI gate + live wiring"
```

---

## Task 7: Live smoke (gated, manual) + final verification

**Files:** none (verification)

- [ ] **Step 1: Full suite green, no network**

Run: `uv run --extra cli --extra dev pytest tests/benchmarks/taubench -v`
Expected: PASS; confirm no test imports `tau_bench` (grep): `! grep -rn "import tau_bench" tests/benchmarks/taubench`

- [ ] **Step 2: Lint** the new dir per repo norm:

Run: `uv run --extra dev ruff check magi_agent/benchmarks/taubench` (and the repo type checker if configured).

- [ ] **Step 3: Adapter validation (gated, manual; needs tau_bench clone + keys)** — 2 tasks × 1 trial, vanilla:

Run: `MAGI_TAUBENCH_ENABLED=1 uv run --extra cli --extra providers magi-taubench --domain airline --max-tasks 2 --trials 1 --config vanilla` (or the module entry wired in Task 6)
Expected: completes an episode end-to-end, prints a TauReport JSON. If the turn-boundary loop hangs or never terminates, debug the handshake before scaling up.

- [ ] **Step 4: v1 run (operator-gated, costs money)** — airline 50 × 4 trials, both configs:
  run `--config vanilla` then `--config full`; record both TauReports; compare pass^1..4 and to the published Sonnet 4.5 airline ≈ 0.70.

- [ ] **Step 5: Confirm gate default-OFF** — with `MAGI_TAUBENCH_ENABLED` unset, the CLI refuses to run.

---

## Notes for the implementer
- The riskiest task is **Task 3 + the live loop**: the turn-boundary handshake (one `run_async` = one agent turn; the agent's final text → `respond`). If episodes don't terminate, the agent may not be emitting a clear user-facing message — inspect the event stream during the adapter-validation smoke.
- `build_env_function_tools` (Task 4) and `agent.py` (Task 5) are the ADK/tau-bench-coupled seams; their exact API calls must be confirmed against the live ADK `FunctionTool` schema-attachment mechanism and the cloned `tau_bench` types. Everything else is fully unit-tested without either dependency.
```
