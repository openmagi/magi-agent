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
        # session_id omitted: the runner's per-trial default (set by the caller) keeps trials independent
        async for event in runner.run_async(
            user_id="taubench", new_message=_user_content(message)
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
        except (KeyboardInterrupt, SystemExit):
            raise
        except AssertionError:
            raise
        except Exception:
            return EpisodeResult(reward=0.0, done=False, turns=turns, infra_error=True)
        turns += 1
        if state.done:
            break
        # the agent's tool calls already hit env.step during the turn (via FunctionTools,
        # which call state.observe). Now route the agent's user-facing text as a respond.
        resp = env.step(action_factory(respond_action_name, {"content": agent_text}))
        state.observe(resp.reward, resp.done)
        obs = resp.observation
    return EpisodeResult(reward=state.reward, done=state.done, turns=turns)
