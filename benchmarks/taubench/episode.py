# benchmarks/taubench/episode.py
"""tau-bench-free multi-turn episode loop. No tau_bench import, no network."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from google.genai import types

from benchmarks.taubench.reliability import (
    ReliabilityConfig,
    WriteLedger,
    completion_review_nudge,
    is_conclusion,
    open_items_review_prompt,
    verify_final,
)


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
    session_id: str | None = None,
    reliability: ReliabilityConfig | None = None,
    ledger: WriteLedger | None = None,
) -> EpisodeResult:
    # `state` is SHARED: the env tools (built by tau_env) record (reward, done) into
    # it on every tool env.step, and this loop records the respond steps. Either can
    # set done. The caller owns it so the tools and the loop see the same object.
    reset = env.reset(task_index)
    obs = reset.observation
    runner = runner_factory(instruction=instruction or env.wiki, tools=tools)
    # One session_id for the whole episode: ADK's Runner.run_async REQUIRES it
    # (CliModelRunner forwards kwargs raw, so it must be passed). Unique per episode
    # so trials stay isolated; constant across this episode's turns so multi-turn
    # conversation history is preserved.
    episode_session_id = session_id or f"taubench-{uuid.uuid4().hex}"
    cfg = reliability or ReliabilityConfig()
    led = ledger if ledger is not None else WriteLedger()
    nudged = False
    reviewed = False
    items_reviewed = False

    async def _run_turn(message: str) -> str:
        texts: list[str] = []
        async for event in runner.run_async(
            user_id="taubench",
            session_id=episode_session_id,
            new_message=_user_content(message),
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
        if cfg.verify_before_final and not nudged:
            try:
                nudge = verify_final(led, agent_text)
            except Exception:
                nudge = None
            if nudge:
                nudged = True
                obs = nudge
                continue  # give the agent one grounded turn; skip this respond
        if cfg.completion_review and not reviewed:
            try:
                conclude = is_conclusion(agent_text)
            except Exception:
                conclude = False
            if conclude:
                reviewed = True
                obs = completion_review_nudge()
                continue  # one grounded turn to complete/scope-correct; skip respond
        # Lever precedence per turn is L2 -> L4 -> L6, each one-shot per episode.
        if cfg.open_items_review and not items_reviewed:
            try:
                conclude = is_conclusion(agent_text)
            except Exception:
                conclude = False
            if conclude:
                items_reviewed = True
                obs = open_items_review_prompt()
                continue
        # the agent's tool calls already hit env.step during the turn (via FunctionTools,
        # which call state.observe). Now route the agent's user-facing text as a respond.
        resp = env.step(
            action_factory(name=respond_action_name, kwargs={"content": agent_text})
        )
        state.observe(resp.reward, resp.done)
        obs = resp.observation
    return EpisodeResult(reward=state.reward, done=state.done, turns=turns)
