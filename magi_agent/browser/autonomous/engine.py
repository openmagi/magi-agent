"""Async wrapper that runs a browser-use Agent loop with an SSRF step guard.

Design goals
------------
- Default-OFF optional tool: ``browser_use`` is imported lazily, only inside
  ``_default_agent_factory``. Importing this module must not require the
  ``browser`` extra.
- Unit-testable without network/Chromium: the agent is built via an injected
  ``agent_factory`` whose contract is intentionally browser-use-agnostic --
  ``factory(*, task, chat_model, on_step, profile_dir)`` returning an object
  with an awaitable ``run(max_steps=...)``.

Navigation enforcement (verified against browser-use 0.11.13)
-------------------------------------------------------------
The plan asked whether returning from ``register_new_step_callback`` can stop a
run, and if not, what the cleanest hard-abort is. Reading the installed source
(``browser_use.agent.service.Agent``) the answer is:

  * ``register_new_step_callback`` is fired from ``_handle_post_llm_processing``
    (after the LLM produced the next action, BEFORE that action executes). Its
    return value is ignored, so returning a block reason does nothing on its
    own.
  * Raising from the callback does NOT cleanly abort: ``step()`` wraps the call
    in ``except Exception -> _handle_step_error``, which (for a generic error)
    just increments ``consecutive_failures`` and continues until ``max_failures``
    (default 5). So a bare raise leaks ~5 more steps and is not "clean".
  * The clean cooperative stop is ``register_should_stop_callback`` (async
    ``() -> bool``). When it returns True, ``_check_stop_or_pause`` sets
    ``state.stopped`` and the run loop ``break``s.
  * Crucially, ``_get_next_action`` runs ``_check_stop_or_pause`` on the line
    immediately AFTER firing the step callback (service.py: 1051 then 1054).
    So if the step callback sets a stop flag, that same step's
    ``_check_stop_or_pause`` honors it and raises ``InterruptedError`` BEFORE
    the just-decided (blocked) action is executed.

Therefore the enforcement we wire is a pair of cooperating callbacks owned by
``_default_agent_factory``:
  1. ``register_new_step_callback`` -> our URL guard. On a block it records the
     violation (never silent) and arms a stop flag.
  2. ``register_should_stop_callback`` -> returns that flag, giving a clean,
     same-step abort with no executed blocked action and no failure churn.

The engine's own ``on_step`` seam is the simple ``(url: str) -> str | None``
guard (reason-out). The factory adapts browser-use's 3-arg
``(state, output, step)`` callback down to that seam, so the injected test fake
never needs to know browser-use specifics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from magi_agent.browser.autonomous.safety_hooks import navigation_block_reason


@dataclass(frozen=True)
class BrowserRunResult:
    status: str  # "ok" | "blocked" | "error"
    summary: str = ""
    steps_used: int = 0
    error_code: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


# on_step contract handed to the factory: url-in, block-reason-out (None = safe).
OnStep = Callable[[str], Optional[str]]


def _default_agent_factory(
    *,
    task: str,
    chat_model: object,
    on_step: OnStep,
    profile_dir: str,
) -> Any:
    """Build a real browser-use Agent, lazily importing the optional extra.

    Adapts browser-use's ``(BrowserStateSummary, AgentOutput, step)`` step
    callback down to our simple ``on_step(url) -> reason|None`` seam, and pairs
    it with a ``register_should_stop_callback`` so a blocked URL aborts the run
    cleanly in the same step (see module docstring for the verified mechanism).
    """
    from browser_use import Agent, BrowserProfile  # noqa: PLC0415  (lazy: optional extra)

    # Shared state between the two cooperating callbacks. Only "stop"/"reason"
    # are read: "stop" by the should-stop callback (clean same-step abort) and
    # "reason" for parity/debugging. The authoritative violation record lives in
    # the ``guard`` closure inside ``BrowserEngine.run`` (which IS this on_step),
    # so we deliberately do NOT keep a second violations list here.
    stop_state: dict[str, object] = {"stop": False, "reason": None}

    def _new_step_callback(state: Any, _output: Any, _step: int) -> None:
        # BrowserStateSummary exposes the current URL as ``state.url`` (confirmed
        # dataclass field in browser_use.browser.views.BrowserStateSummary).
        url = getattr(state, "url", None)
        if not url:
            return
        reason = on_step(url)
        if reason:
            # on_step (the engine's guard) already recorded the violation; here
            # we only arm the cooperative stop so the blocked action never runs.
            stop_state["stop"] = True
            stop_state["reason"] = reason

    async def _should_stop_callback() -> bool:
        return bool(stop_state["stop"])

    # Per-workspace browser profile isolation: BrowserProfile(user_data_dir=...)
    # is a pydantic model (browser_use 0.11.13) whose ``user_data_dir`` accepts
    # ``str | Path | None``; Agent(...) accepts a ``browser_profile=`` kwarg.
    # Both confirmed by introspection (see _api_notes.py section 5). Constructing
    # the profile does NOT launch Chromium (only Agent.run() does).
    browser_profile = BrowserProfile(user_data_dir=profile_dir)

    return Agent(
        task=task,
        llm=chat_model,
        browser_profile=browser_profile,
        register_new_step_callback=_new_step_callback,
        register_should_stop_callback=_should_stop_callback,
    )


class BrowserEngine:
    def __init__(
        self,
        *,
        agent_factory: Callable[..., Any] = _default_agent_factory,
    ) -> None:
        self._agent_factory = agent_factory

    async def run(
        self,
        *,
        task: str,
        chat_model: object,
        max_steps: int,
        profile_dir: str,
        start_url: str | None = None,
    ) -> BrowserRunResult:
        # 1. Pre-flight SSRF on start_url (deterministic): block BEFORE building
        #    the agent so a bad start URL never spins up Chromium.
        if start_url is not None:
            reason = navigation_block_reason(start_url)
            if reason:
                return BrowserRunResult(status="blocked", error_code=reason)

        # 2. Fold an allowed start_url into the task instruction (0.11.x Agent
        #    has no separate start-url kwarg; it is driven by the task string).
        effective_task = task
        if start_url is not None:
            effective_task = f"Start by navigating to {start_url}. {task}"

        # 3. Per-step navigation guard (url-in / reason-out). The factory adapts
        #    browser-use's 3-arg step callback down to this seam.
        #
        #    run() OWNS the violation record. The factory wires our returned
        #    reason into ``register_should_stop_callback`` (clean same-step
        #    abort, unchanged), but because the guard ALSO records the violation
        #    here, a mid-run block can never be silent: after the run we surface
        #    it as status="blocked" regardless of what the (interrupted) history
        #    looks like.
        violations: list[dict[str, str]] = []

        def guard(url: str) -> str | None:
            reason = navigation_block_reason(url)
            if reason:
                violations.append({"url": url, "reason": reason})
            return reason

        # 4. + 5. Build + run, wrapping construction and the run in try/except.
        try:
            agent = self._agent_factory(
                task=effective_task,
                chat_model=chat_model,
                on_step=guard,
                profile_dir=profile_dir,
            )
            outcome = await agent.run(max_steps=max_steps)
        except Exception as exc:  # noqa: BLE001 (normalize any backend failure)
            return BrowserRunResult(
                status="error",
                error_code="browser_run_failed",
                summary=str(exc),
            )

        # 6a. A mid-run navigation was blocked by the SSRF guard. The run was
        #     aborted in the same step; surface it as blocked (never silent),
        #     carrying the first violation reason as the error_code.
        if violations:
            first = violations[0]
            return BrowserRunResult(
                status="blocked",
                error_code=first["reason"],
                metadata={"violations": list(violations)},
            )

        # 6b. No violation: normal duck-typed result extraction, so the test
        #     fake and the real AgentHistoryList both work.
        return _normalize_outcome(outcome)


def _normalize_outcome(outcome: Any) -> BrowserRunResult:
    final = getattr(outcome, "final_result", None)
    if callable(final):
        summary = final()
        steps_fn = getattr(outcome, "number_of_steps", None)
        steps = steps_fn() if callable(steps_fn) else 0
        return BrowserRunResult(
            status="ok",
            summary=summary if isinstance(summary, str) else "",
            steps_used=steps if isinstance(steps, int) else 0,
        )
    if isinstance(outcome, dict):
        summary = outcome.get("final", "")
        steps = outcome.get("steps", 0)
        return BrowserRunResult(
            status="ok",
            summary=summary if isinstance(summary, str) else "",
            steps_used=steps if isinstance(steps, int) else 0,
        )
    # Unknown shape: still "ok" but with no extractable detail.
    return BrowserRunResult(status="ok")
