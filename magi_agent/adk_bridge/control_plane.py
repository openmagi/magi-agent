"""ADK loop control-plane abstraction (PR2, goose-parity).

Motivation
----------
goose *owns* its agent loop so loop controls (turn-cap, stop-hooks, retry,
"disable tools on the final iteration") are inline and immediate. magi
delegates the loop to Google ADK's ``Runner.run_async``, so every such control
must be hand-wired as a bespoke ADK callback or plugin across multiple build
sites — and before this PR they had *drifted*: ``real_runner.py`` built
``App(..., plugins=[])`` while ``local_runner.py`` assembled 3 plugins. Those
controls never reached the production CLI runner.

This module adds a thin, typed control-plane applied **once** at runner-build
time via a single fan-out plugin, used by BOTH runners through one shared
helper (``build_default_plane``), so they cannot drift again.

Design
------
``LoopControl`` — a ``@runtime_checkable Protocol`` with three optional hooks:

* ``on_before_tool`` — may deny or rewrite the call (returns ``ToolDecision``).
* ``on_after_tool``  — may override the tool result (returns ``dict | None``).
* ``on_before_model`` — may mutate the outgoing ``LlmRequest`` in place (returns
  ``None``).
* ``on_after_agent`` — may observe the completed turn (returns ``None``).

``BaseLoopControl`` — abstract base with no-op defaults for all hooks;
concrete controls override only the hooks they need.

``ControlPlane`` — ordered registry of ``LoopControl`` instances. Fan-out:

* ``_before_tool``: ordered; first deny short-circuits; rewrite mutates args
  in-place and continues; allow passes through.
* ``_after_tool``: ordered; first non-``None`` override wins.
* ``_before_model``: all controls run (mutations accumulate); always returns
  ``None``.
* ``_after_agent``: all observers run; always returns ``None``.

``ControlPlanePlugin`` — thin ADK ``BasePlugin`` wrapper that forwards each ADK
callback to the ``ControlPlane``. Registered in ``App(plugins=[...])`` once per
runner build; ADK's ``PluginManager`` fans it out to every tool/model event.

Ordering with the permission gate
-----------------------------------
**ADK's real callback ordering** (verified against ADK 1.33
``google/adk/flows/llm_flows/functions.py``):

1. **Plugin-level** ``before_tool_callback`` runs FIRST (``plugin_manager.run_before_tool_callback``).
   If it returns a non-None dict the tool call is short-circuited immediately.
2. **Agent-level** ``before_tool_callback`` runs ONLY IF the plugin step returned None.

``engine.py:_attach_gate_callback`` attaches the permission gate **agent-level**
(Step 2). ``ControlPlanePlugin`` is a plugin-level callback (Step 1). This means:

* **Today (safe):** none of the registered ``LoopControl`` implementations override
  ``on_before_tool``, so ``ControlPlanePlugin.before_tool_callback`` always returns
  ``None``, and the agent-level permission gate (Step 2) always runs.
* **Future footgun:** a ``LoopControl`` that overrides ``on_before_tool`` and returns
  a deny or rewrite ``ToolDecision`` would execute at the plugin level (Step 1),
  SHORT-CIRCUITING the agent-level permission gate — the gate would NEVER run.
  A rewrite would additionally mutate tool args before the gate sees them.

To prevent this, ``ControlPlane.register`` raises ``ValueError`` if the control
overrides ``on_before_tool``. Such controls are forbidden until the permission gate
is moved to the plugin level (or re-checked after the plane). This fails loud at
registration rather than silently bypassing security at runtime.

Known ADK limitations (do NOT try to force into the plane)
-----------------------------------------------------------
The following controls CANNOT be expressed via ADK callbacks and remain
``engine.py`` outer-driver concerns:

* **Hard turn-cap counting** — requires external state counting
  ``Runner.run_async`` invocations; no ADK callback fires at run entry/exit
  with a running turn count.
* **stop-hook-deny → re-iteration** — ADK has no "force loop re-entry" callback.
* **stop-on-goal re-entry after end_turn** — ``end_turn`` finalises the ADK
  runner; re-entry requires a new ``run_async`` call.

The plane covers plugin-level callbacks that can be expressed as one-way
fan-out without bypassing the permission gate.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, runtime_checkable

from typing import Protocol

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.tools.manifest import ToolSource

CONTROL_PLANE_PLUGIN_NAME = "magi_control_plane"
SELF_REVIEW_AFTER_TURN_CONTROL_NAME = "magi_self_review_after_turn"
SELF_REVIEW_ENABLED_ENV = "MAGI_SELF_REVIEW_ENABLED"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ToolDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDecision:
    """Decision returned by ``LoopControl.on_before_tool``.

    ``action`` values:
    * ``"allow"``   — proceed with the original (or already-mutated) args.
    * ``"deny"``    — short-circuit; ``deny_result`` becomes the tool response.
    * ``"rewrite"`` — mutate args in-place to ``updated_args`` and continue.
    """

    action: Literal["allow", "deny", "rewrite"] = "allow"
    deny_result: dict[str, Any] | None = None
    updated_args: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# LoopControl Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LoopControl(Protocol):
    """Protocol for a single loop-control policy hook set.

    Controls only need to override the hooks they use; provide a
    ``BaseLoopControl`` with no-op defaults so each control overrides one hook.
    """

    name: str

    async def on_before_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
    ) -> ToolDecision | None:
        ...

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        ...

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        ...

    async def on_after_agent(
        self,
        *,
        agent: Any,
        callback_context: Any,
    ) -> None:
        ...


# ---------------------------------------------------------------------------
# BaseLoopControl
# ---------------------------------------------------------------------------


class BaseLoopControl:
    """Abstract base providing no-op defaults for all LoopControl hooks.

    Subclass and override only the hooks you need.
    """

    name: str = "base_loop_control"

    async def on_before_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
    ) -> ToolDecision | None:
        return None

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        return None

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        return None

    async def on_after_agent(
        self,
        *,
        agent: Any,
        callback_context: Any,
    ) -> None:
        return None


# ---------------------------------------------------------------------------
# ControlPlane
# ---------------------------------------------------------------------------


class ControlPlane:
    """Ordered registry of LoopControl instances with fan-out dispatch."""

    def __init__(self) -> None:
        self._controls: list[LoopControl] = []

    def register(self, control: LoopControl) -> "ControlPlane":
        """Register a control and return self for chainable building.

        Raises:
            ValueError: If ``control`` overrides ``on_before_tool`` with a
                non-default implementation. Such controls are forbidden under the
                current architecture because ``ControlPlanePlugin`` runs at the
                ADK **plugin level** (Step 1), while the permission gate is wired
                **agent-level** (Step 2, engine.py ``_attach_gate_callback``). A
                plugin-level ``on_before_tool`` that returns deny/rewrite would
                short-circuit Step 2 and bypass the permission gate entirely.
                Move the gate to the plugin level (or re-check it after the plane)
                before introducing deny/rewrite-capable ``on_before_tool`` controls.
        """
        if type(control).on_before_tool is not BaseLoopControl.on_before_tool:
            raise ValueError(
                f"LoopControl '{getattr(control, 'name', type(control).__name__)}' "
                f"overrides on_before_tool, which is forbidden under the current "
                f"agent-level permission-gate ordering. "
                f"ControlPlanePlugin runs at ADK plugin level (before the agent-level "
                f"permission gate), so a deny or rewrite returned from on_before_tool "
                f"would bypass the gate entirely. "
                f"Move the permission gate to plugin level before registering "
                f"deny/rewrite-capable on_before_tool controls."
            )
        self._controls.append(control)
        return self

    async def _before_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
    ) -> dict[str, Any] | None:
        """Fan-out before_tool: first deny wins; rewrite mutates args; allow continues."""
        for control in self._controls:
            decision = await control.on_before_tool(
                tool=tool, args=args, tool_context=tool_context
            )
            if decision is None or decision.action == "allow":
                continue
            if decision.action == "deny":
                return decision.deny_result
            if decision.action == "rewrite" and decision.updated_args is not None:
                # Mutate args in-place so subsequent controls see the rewritten args.
                args.clear()
                args.update(decision.updated_args)
                # Continue to next controls (no short-circuit on rewrite).
        return None

    async def _after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """Fan-out after_tool: first non-None override wins."""
        for control in self._controls:
            override = await control.on_after_tool(
                tool=tool, args=args, tool_context=tool_context, result=result
            )
            if override is not None:
                return override
        return None

    async def _before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        """Fan-out before_model: all controls run (mutations accumulate)."""
        for control in self._controls:
            await control.on_before_model(
                callback_context=callback_context, llm_request=llm_request
            )
        return None

    async def _after_agent(
        self,
        *,
        agent: Any,
        callback_context: Any,
    ) -> None:
        """Fan-out after_agent observers; they cannot alter the ADK response."""
        for control in self._controls:
            await control.on_after_agent(
                agent=agent,
                callback_context=callback_context,
            )
        return None


# ---------------------------------------------------------------------------
# ControlPlanePlugin
# ---------------------------------------------------------------------------


class ControlPlanePlugin(BasePlugin):
    """Single ADK BasePlugin that fans all callbacks out to a ControlPlane.

    Registered once per runner build via ``App(plugins=[ControlPlanePlugin(plane)])``.
    ADK's PluginManager dispatches each callback to this plugin, which in turn
    fans it to every registered LoopControl.

    ADK 1.33 verified callback signatures (installed package authoritative):
    - before_tool_callback(self, *, tool, tool_args, tool_context) -> Optional[dict]
    - after_tool_callback(self, *, tool, tool_args, tool_context, result) -> Optional[dict]
    - before_model_callback(self, *, callback_context, llm_request) -> Optional[LlmResponse]

    Note on before_model_callback: this plugin always returns None (mutation only).
    ADK before_model_callback returning a non-None LlmResponse would short-circuit
    all remaining plugins — we never do that here.
    """

    def __init__(self, plane: ControlPlane) -> None:
        super().__init__(CONTROL_PLANE_PLUGIN_NAME)
        self._p = plane

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> dict[str, Any] | None:
        """Forward to plane._before_tool with ADK's ``tool_args`` mapped to ``args``."""
        return await self._p._before_tool(
            tool=tool, args=tool_args, tool_context=tool_context
        )

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """Forward to plane._after_tool."""
        return await self._p._after_tool(
            tool=tool, args=tool_args, tool_context=tool_context, result=result
        )

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        """Forward to plane._before_model; always returns None (mutations only)."""
        await self._p._before_model(
            callback_context=callback_context, llm_request=llm_request
        )
        return None

    async def after_agent_callback(
        self,
        *,
        agent: Any,
        callback_context: Any,
    ) -> None:
        """Forward ADK's post-agent callback to the control plane."""
        await self._p._after_agent(
            agent=agent,
            callback_context=callback_context,
        )
        return None


# ---------------------------------------------------------------------------
# MaxStepsBrakeControl (default-OFF seam)
# ---------------------------------------------------------------------------

MAX_STEPS_BRAKE_CONTROL_NAME = "magi_max_steps_brake"
MAX_STEPS_BRAKE_ENABLED_ENV = "MAGI_MAX_STEPS_BRAKE_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class MaxStepsBrakeControl(BaseLoopControl):
    """Wrap-up brake that fires on the final allowed model iteration.

    Mirrors OpenCode's ``max-steps.txt`` graceful termination brake.

    When ``iteration >= max_iterations - 1`` (and ``max_iterations > 0``):
    1. Appends the wrap-up instruction as a ``{"role": "user", "content": MSG}``
       dict into ``llm_request.contents`` — or as a ``google.genai.types.Content``
       if the contents list is populated with ADK Content objects.
    2. Clears ``llm_request.config.tools`` so no further tool calls can be issued.

    Default-OFF: registered only when ``MAGI_MAX_STEPS_BRAKE_ENABLED=1``.

    This wires the intentionally-dormant seam in
    ``magi_agent.runtime.turn_policy.maybe_apply_max_steps_brake`` but adapts it
    to the ADK LlmRequest shape (llm_request.contents + llm_request.config.tools)
    rather than raw message dicts + tool schemas.
    """

    name = MAX_STEPS_BRAKE_CONTROL_NAME

    def __init__(
        self,
        *,
        max_iterations: int,
        iteration: int = 0,
    ) -> None:
        self.max_iterations = max_iterations
        self.iteration = iteration

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        from magi_agent.runtime.turn_policy import MAX_STEPS_WRAP_UP_MESSAGE

        if self.max_iterations <= 0:
            return None
        if self.iteration < self.max_iterations - 1:
            return None

        # Final (or beyond-final) iteration: inject wrap-up.
        contents = getattr(llm_request, "contents", None)
        if isinstance(contents, list):
            # Try ADK Content object first; fall back to plain dict.
            try:
                from google.genai import types as _genai_types
                wrap_up = _genai_types.Content(
                    role="user",
                    parts=[_genai_types.Part(text=MAX_STEPS_WRAP_UP_MESSAGE)],
                )
                contents.append(wrap_up)
            except Exception:
                contents.append({"role": "user", "content": MAX_STEPS_WRAP_UP_MESSAGE})
        elif isinstance(llm_request, dict):
            # dict-based fake for tests.
            llm_request.setdefault("contents", [])
            llm_request["contents"].append(
                {"role": "user", "content": MAX_STEPS_WRAP_UP_MESSAGE}
            )

        # Clear tools so no tool calls can be issued on this final iteration.
        config = getattr(llm_request, "config", None)
        if config is not None:
            tools = getattr(config, "tools", None)
            if tools is not None:
                try:
                    config.tools = []
                except Exception:
                    pass
        return None


# ---------------------------------------------------------------------------
# GaConstraintReinjectionControl (default-OFF seam — Track 19 PR6 wiring)
# ---------------------------------------------------------------------------

GA_CONSTRAINT_REINJECTION_CONTROL_NAME = "magi_ga_constraint_reinjection"


class GaConstraintReinjectionControl(BaseLoopControl):
    """Per-turn GA constraint reminder, wired at the live ``on_before_model`` seam.

    Each turn this resolves the active turn's (session_id, turn_id), reads the
    immutable per-turn evidence ledger and any open ``approval_required`` controls
    from the :class:`GeneralAutomationReceiptLedgerStore`, and delegates to
    :func:`ga_constraint_reinjection` (no reminder logic is duplicated here). When
    that returns a non-empty reminder it is **appended** to ``llm_request.contents``
    (mirroring :class:`MaxStepsBrakeControl`'s inject mechanism) — but, unlike the
    max-steps brake, tools are NOT cleared.

    Default-OFF / inert: ``ga_constraint_reinjection`` itself returns ``None`` when
    ``MAGI_GA_LIVE_ENABLED`` is OFF or ``agent_role != "general"`` or nothing is
    owed, so this control is a pure no-op in those cases. It is registered ONLY
    when both a receipts store and a contract requirement are provided, so all
    no-arg ``build_default_plugin()`` callers are byte-identical to ``main``.
    """

    name = GA_CONSTRAINT_REINJECTION_CONTROL_NAME

    def __init__(
        self,
        *,
        receipts: Any,
        contract_required: Any,
        agent_role: str = "general",
        env: dict[str, str] | None = None,
    ) -> None:
        self._receipts = receipts
        self._contract_required = contract_required
        self._agent_role = agent_role
        self._env = env

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        from magi_agent.harness.general_automation.constraint_reinjection import (
            ga_constraint_reinjection,
        )

        session = getattr(callback_context, "session", None)
        session_id = _non_empty_str(getattr(session, "id", None))
        turn_id = _non_empty_str(getattr(callback_context, "invocation_id", None))
        if turn_id is None:
            turn_id = _latest_event_invocation_id(session)
        if session_id is None or turn_id is None:
            return None

        ledger = self._receipts.ledger_for_turn(
            session_id=session_id, turn_id=turn_id
        )
        if ledger is None:
            return None
        open_controls = self._receipts.open_controls_for_turn(
            session_id=session_id, turn_id=turn_id
        )

        reminder = ga_constraint_reinjection(
            contract_required=self._contract_required,
            ledger=ledger,
            open_controls=open_controls,
            agent_role=self._agent_role,
            env=self._env if self._env is not None else dict(os.environ),
        )
        if not reminder:
            return None

        # Append the reminder; mirror MaxStepsBrakeControl's inject mechanism but
        # do NOT clear tools (this control only adds context, never disables tools).
        contents = getattr(llm_request, "contents", None)
        if isinstance(contents, list):
            try:
                from google.genai import types as _genai_types

                contents.append(
                    _genai_types.Content(
                        role="user",
                        parts=[_genai_types.Part(text=reminder)],
                    )
                )
            except Exception:
                contents.append({"role": "user", "content": reminder})
        elif isinstance(llm_request, dict):
            llm_request.setdefault("contents", [])
            llm_request["contents"].append({"role": "user", "content": reminder})
        return None


# ---------------------------------------------------------------------------
# SelfReviewAfterTurnControl (default-OFF, shadow-first, no writes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SelfReviewTurnSnapshot:
    session_id: str
    turn_id: str
    system_prompt_blocks: list[dict[str, Any]]
    parent_assistant_message: dict[str, Any]


class _NoopSelfReviewCandidateSink:
    def receive(self, _candidate: object) -> None:
        return None


class SelfReviewAfterTurnControl(BaseLoopControl):
    """ADK post-turn adapter for C1 self-review.

    The control is registered only when ``MAGI_SELF_REVIEW_ENABLED`` is true.
    It schedules the C1 hook in the background and returns ``None`` immediately,
    so the ADK post-turn callback remains observational and cannot alter the
    parent turn. The default sink is a no-op, preserving C1's no-write contract
    until a later stage injects a real candidate sink.
    """

    name = SELF_REVIEW_AFTER_TURN_CONTROL_NAME

    def __init__(
        self,
        *,
        fork_runner: Any | None = None,
        candidate_sink: Any | None = None,
        config: Any | None = None,
        now: datetime | None = None,
        scheduler: Callable[[Coroutine[Any, Any, None]], None] | None = None,
    ) -> None:
        self.manifest = _self_review_after_turn_manifest()
        self._fork_runner = fork_runner
        self._candidate_sink = candidate_sink or _NoopSelfReviewCandidateSink()
        self._config = config
        self._now = now
        self._scheduler = scheduler
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def on_after_agent(
        self,
        *,
        agent: Any,
        callback_context: Any,
    ) -> None:
        try:
            snapshot = _extract_self_review_turn_snapshot(
                agent=agent,
                callback_context=callback_context,
            )
        except Exception:
            logger.debug(
                "self-review after-turn context extraction failed",
                exc_info=True,
            )
            return None
        if snapshot is None:
            return None

        self._schedule(self._run_self_review(snapshot))
        return None

    def _schedule(self, coro: Coroutine[Any, Any, None]) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler(coro)
            except Exception:
                coro.close()
                logger.debug("self-review after-turn scheduler failed", exc_info=True)
            return

        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            logger.debug(
                "self-review after-turn schedule skipped: no running loop",
                exc_info=True,
            )
            return
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    def _on_background_task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("self-review after-turn background task failed", exc_info=True)

    async def _run_self_review(self, snapshot: _SelfReviewTurnSnapshot) -> None:
        from magi_agent.harness.self_review import run_self_review_hook

        await run_self_review_hook(
            session_id=snapshot.session_id,
            turn_id=snapshot.turn_id,
            system_prompt_blocks=snapshot.system_prompt_blocks,
            parent_assistant_message=snapshot.parent_assistant_message,
            fork_runner=self._fork_runner_or_default(),
            candidate_sink=self._candidate_sink,
            config=self._config,
            now=self._now,
        )

    def _fork_runner_or_default(self) -> Any:
        if self._fork_runner is None:
            from magi_agent.runtime.fork_runner import ForkRunner

            self._fork_runner = ForkRunner()
        return self._fork_runner


def _extract_self_review_turn_snapshot(
    *,
    agent: Any,
    callback_context: Any,
) -> _SelfReviewTurnSnapshot | None:
    session = getattr(callback_context, "session", None)
    session_id = _non_empty_str(getattr(session, "id", None))
    turn_id = _non_empty_str(getattr(callback_context, "invocation_id", None))
    if turn_id is None:
        turn_id = _latest_event_invocation_id(session)
    if session_id is None or turn_id is None:
        return None

    parent_message = _latest_assistant_message(session=session, turn_id=turn_id)
    if parent_message is None:
        return None

    return _SelfReviewTurnSnapshot(
        session_id=session_id,
        turn_id=turn_id,
        system_prompt_blocks=_system_prompt_blocks_from_agent(agent),
        parent_assistant_message=parent_message,
    )


def _self_review_after_turn_manifest() -> HookManifest:
    return HookManifest(
        name="builtin:self-review-after-turn",
        point=HookPoint.AFTER_TURN_END,
        description="Runs the C1 self-review fork after an agent turn.",
        source=ToolSource(
            kind="builtin",
            package="magi_agent.harness.self_review",
        ),
        executionType="handler",
        enabled=True,
        blocking=False,
        failOpen=True,
        priority=80,
        optOut=True,
    )


def _system_prompt_blocks_from_agent(agent: Any) -> list[dict[str, Any]]:
    instruction = getattr(agent, "instruction", None)
    if isinstance(instruction, str) and instruction.strip():
        return [{"type": "text", "text": instruction}]
    return []


def _latest_event_invocation_id(session: Any) -> str | None:
    events = getattr(session, "events", None)
    if not isinstance(events, list | tuple):
        return None
    for event in reversed(events):
        turn_id = _non_empty_str(getattr(event, "invocation_id", None))
        if turn_id is not None:
            return turn_id
    return None


def _latest_assistant_message(
    *,
    session: Any,
    turn_id: str,
) -> dict[str, Any] | None:
    events = getattr(session, "events", None)
    if not isinstance(events, list | tuple):
        return None

    for event in reversed(events):
        event_turn_id = _non_empty_str(getattr(event, "invocation_id", None))
        if event_turn_id is not None and event_turn_id != turn_id:
            continue
        if _is_user_event(event):
            continue
        message = _content_to_assistant_message(getattr(event, "content", None))
        if message is not None:
            return message
    return None


def _is_user_event(event: Any) -> bool:
    author = _non_empty_str(getattr(event, "author", None))
    content = getattr(event, "content", None)
    role = _non_empty_str(getattr(content, "role", None))
    return author == "user" or role == "user"


def _content_to_assistant_message(content: Any) -> dict[str, Any] | None:
    if _non_empty_str(getattr(content, "role", None)) == "user":
        return None
    parts = getattr(content, "parts", None)
    if not isinstance(parts, list | tuple):
        return None

    blocks: list[dict[str, Any]] = []
    for part in parts:
        text = _non_empty_str(getattr(part, "text", None))
        if text is not None:
            blocks.append({"type": "text", "text": text})
            continue
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            blocks.append(
                _function_call_block(function_call, fallback_index=len(blocks))
            )

    if not blocks:
        return None
    return {"role": "assistant", "content": blocks}


def _function_call_block(function_call: Any, *, fallback_index: int) -> dict[str, Any]:
    name = _non_empty_str(getattr(function_call, "name", None)) or "tool"
    raw_args = getattr(function_call, "args", None)
    args = dict(raw_args) if isinstance(raw_args, dict) else {}
    tool_id = (
        _non_empty_str(getattr(function_call, "id", None))
        or f"{name}-{fallback_index}"
    )
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": name,
        "input": args,
    }


def _non_empty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


# ---------------------------------------------------------------------------
# Adapters wrapping existing plugins as LoopControls
# ---------------------------------------------------------------------------


class _EditRetryLoopControl(BaseLoopControl):
    """Thin LoopControl adapter delegating to MagiEditRetryReflectionPlugin.

    Wires only ``after_tool_callback`` (error-dict path) from the existing plugin
    into the ControlPlane's ``on_after_tool`` hook.

    The plugin's ``on_tool_error_callback`` (raise path — the live primary path
    for gate5b FileEdit ``ValueError``) is NOT a LoopControl hook; it is
    forwarded at the plugin level by ``_ExtendedControlPlanePlugin``, which calls
    it directly so ADK's ``run_on_tool_error_callback`` path is preserved.
    """

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._plugin, "name", "magi_edit_retry_reflection_control")

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        return await self._plugin.after_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context, result=result
        )


class _ToolExceptionReflectionLoopControl(BaseLoopControl):
    """Thin LoopControl adapter exposing MagiToolExceptionReflectionPlugin.

    The plugin only implements the raise path (``on_tool_error_callback``)
    plus the ``after_run_callback`` sweep — neither is a LoopControl hook;
    both are forwarded at the plugin level by ``_ExtendedControlPlanePlugin``.
    This adapter exists solely to expose ``._plugin`` to that fan-out.
    """

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._plugin, "name", "magi_tool_exception_reflection_control")


class _ResilienceLoopControl(BaseLoopControl):
    """Thin LoopControl adapter delegating ``after_tool_callback`` to MagiResiliencePlugin."""

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._plugin, "name", "magi_resilience_control")

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        return await self._plugin.after_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context, result=result
        )


class _ToolSynthesisNudgeLoopControl(BaseLoopControl):
    """Thin LoopControl adapter delegating to MagiToolSynthesisNudgePlugin.

    Registered LAST in ``build_default_plane`` so edit-retry / resilience
    overrides win the plane's first-non-None-wins after-tool fan-out; the
    nudge only rides on results no other control replaced.
    """

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._plugin, "name", "magi_tool_synthesis_nudge_control")

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        return await self._plugin.after_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context, result=result
        )


class _CompactionLoopControl(BaseLoopControl):
    """Thin LoopControl adapter delegating to MagiContextCompactionPlugin."""

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._plugin, "name", "magi_context_compaction_control")

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        await self._plugin.before_model_callback(
            callback_context=callback_context, llm_request=llm_request
        )
        return None


# ---------------------------------------------------------------------------
# Extended ControlPlanePlugin — also forwards resilience-only callbacks
# ---------------------------------------------------------------------------


class _ExtendedControlPlanePlugin(ControlPlanePlugin):
    """ControlPlanePlugin that also forwards plugin-level callbacks with no LoopControl equivalent.

    Covers three plugin-level hooks that operate outside the LoopControl protocol:

    * ``on_tool_error_callback`` — fires when a tool *raises* an exception (as
      opposed to returning an error-shaped dict, which goes through
      ``after_tool_callback``). The edit-retry plugin's raise-path
      (gate5b ``FileEdit`` ``ValueError``) lives here. We fan out to every
      registered adapter whose underlying ``_plugin`` implements this callback,
      preserving the same "first non-None wins" short-circuit that ADK's own
      ``PluginManager`` uses.

    * ``on_model_error_callback`` — resilience plugin classification/telemetry
      on model-call errors.

    * ``after_run_callback`` — sweeps per-invocation state for all wrapped
      plugins so nothing grows unbounded across turns.
    """

    def __init__(self, plane: ControlPlane, resilience_plugin: Any | None = None) -> None:
        super().__init__(plane)
        # resilience_plugin param kept for call-site compatibility; no longer stored.

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> dict[str, Any] | None:
        """Forward to any registered adapter whose plugin implements on_tool_error_callback.

        Fan-out policy: first non-None return wins (mirrors ADK PluginManager
        behaviour and is consistent with the after_tool_callback override
        semantics already established for the edit-retry plugin).
        """
        for ctrl in self._p._controls:
            plugin = getattr(ctrl, "_plugin", None)
            if plugin is None:
                continue
            handler = getattr(plugin, "on_tool_error_callback", None)
            if not callable(handler):
                continue
            result = await handler(
                tool=tool,
                tool_args=tool_args,
                tool_context=tool_context,
                error=error,
            )
            if result is not None:
                return result
        return None

    async def on_model_error_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
        error: Exception,
    ) -> Any:
        """Forward to the first registered adapter whose plugin implements on_model_error_callback.

        Fan-out policy: first non-None return wins (consistent with on_tool_error_callback
        and ADK PluginManager behaviour). ADK 1.33 verified signature:
            async def on_model_error_callback(self, *, callback_context, llm_request, error)
            -> Optional[LlmResponse]
        """
        for ctrl in self._p._controls:
            plugin = getattr(ctrl, "_plugin", None)
            if plugin is None:
                continue
            handler = getattr(plugin, "on_model_error_callback", None)
            if not callable(handler):
                continue
            result = await handler(
                callback_context=callback_context,
                llm_request=llm_request,
                error=error,
            )
            if result is not None:
                return result
        return None

    async def after_run_callback(
        self,
        *,
        invocation_context: Any,
    ) -> None:
        # Sweep edit-retry and resilience state.
        for ctrl in self._p._controls:
            plugin = getattr(ctrl, "_plugin", None)
            if plugin is not None:
                after_run = getattr(plugin, "after_run_callback", None)
                if callable(after_run):
                    await after_run(invocation_context=invocation_context)


# ---------------------------------------------------------------------------
# build_default_plane — shared helper used by BOTH runners
# ---------------------------------------------------------------------------


def build_default_plane(
    os_environ: dict[str, str] | None = None,
    *,
    general_automation_receipts: Any | None = None,
    contract_required: Any | None = None,
    agent_role: str = "general",
    self_review_fork_runner: Any | None = None,
    self_review_candidate_sink: Any | None = None,
    self_review_config: Any | None = None,
    self_review_now: datetime | None = None,
    self_review_scheduler: Callable[[Coroutine[Any, Any, None]], None] | None = None,
    tool_synthesis_model_label: str | None = None,
) -> ControlPlane:
    """Build the default ControlPlane from environment flags.

    Used by BOTH ``local_runner.py`` and ``real_runner.py`` so they cannot
    drift. Each flag-gated control uses the same env var as before, preserving
    default-OFF behavior for all existing controls.

    Args:
        os_environ: Environment mapping (defaults to ``os.environ``). Injectable
            for tests.
        general_automation_receipts: Optional per-turn GA receipt/control store.
            Together with ``contract_required`` this enables the GA constraint
            reminder control. When either is ``None`` the control is NOT
            registered, so no-arg callers stay byte-identical to ``main``.
        contract_required: Optional ``RequiredDeliverableEvidence`` describing the
            active GA contract's owed deliverables. See above.
        agent_role: Agent role passed to the reminder gate (default ``"general"``;
            the reminder is inert for any non-general role).
        self_review_*: Optional collaborators for the self-review after-turn
            control. Omitted values preserve the default safe runtime behavior:
            lazy ``ForkRunner`` construction, no-op candidate sink, env-derived
            config/time, and background scheduling on the active event loop.
        tool_synthesis_model_label: The runner's configured litellm model label
            (``provider/model``), used ONLY by the default-OFF tool-synthesis
            reflection nudge (``MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED`` + frontier
            tier). ``None`` (default — all pre-existing callers) skips the
            control entirely so the plane stays byte-identical.

    Returns:
        A configured ``ControlPlane`` with all enabled controls registered.
    """
    env = os_environ if os_environ is not None else dict(os.environ)

    # Avoid circular import: import config.env here (local).
    from magi_agent.config.env import (
        parse_context_compaction_env,
        parse_edit_retry_reflection_env,
        parse_error_recovery_env,
        parse_loop_guard_env,
        parse_tool_exception_reflection_env,
        parse_tool_schema_feedback_env,
    )
    from magi_agent.adk_bridge.context_compaction import build_context_compaction_plugin
    from magi_agent.adk_bridge.edit_retry_reflection import build_edit_retry_reflection_plugin
    from magi_agent.adk_bridge.resilience_plugin import build_resilience_plugin
    from magi_agent.adk_bridge.schema_feedback import build_schema_feedback_control
    from magi_agent.adk_bridge.tool_exception_reflection import (
        build_tool_exception_reflection_plugin,
    )

    plane = ControlPlane()

    # 1. Edit-retry reflection (MAGI_EDIT_RETRY_REFLECTION_ENABLED, default OFF).
    edit_retry_env = parse_edit_retry_reflection_env(env)
    edit_retry_plugin = build_edit_retry_reflection_plugin(
        enabled=edit_retry_env.enabled,
        max_attempts=edit_retry_env.max_attempts,
    )
    if edit_retry_plugin is not None:
        plane.register(_EditRetryLoopControl(edit_retry_plugin))

    # 2. Resilience (MAGI_LOOP_GUARD_ENABLED + MAGI_ERROR_RECOVERY_ENABLED, default OFF).
    loop_guard_env = parse_loop_guard_env(env)
    error_recovery_env = parse_error_recovery_env(env)
    resilience_plugin = build_resilience_plugin(
        loop_guard_enabled=loop_guard_env.enabled,
        loop_guard_soft_threshold=loop_guard_env.soft_threshold,
        loop_guard_hard_threshold=loop_guard_env.hard_threshold,
        loop_guard_frequency_soft_threshold=loop_guard_env.frequency_soft_threshold,
        loop_guard_frequency_hard_threshold=loop_guard_env.frequency_hard_threshold,
        error_recovery_enabled=error_recovery_env.enabled,
        recovery_max_attempts=error_recovery_env.max_recovery_attempts,
    )
    if resilience_plugin is not None:
        plane.register(_ResilienceLoopControl(resilience_plugin))

    # 3. Generic tool-exception reflection (MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED,
    #    strict default OFF, profile-independent). Registered AFTER the edit-retry
    #    control so edit-retry keeps fan-out priority (first non-None wins) for
    #    FileEdit/PatchApply when both are on; the generic plugin additionally
    #    hard-skips those tools.
    tool_exception_env = parse_tool_exception_reflection_env(env)
    tool_exception_plugin = build_tool_exception_reflection_plugin(
        enabled=tool_exception_env.enabled,
        max_attempts=tool_exception_env.max_attempts,
    )
    if tool_exception_plugin is not None:
        plane.register(_ToolExceptionReflectionLoopControl(tool_exception_plugin))

    # 3b. Schema-invalid argument feedback (MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED,
    #     strict default OFF, profile-independent). Registered AFTER the
    #     edit-retry and resilience controls: ControlPlane._after_tool fan-out
    #     is first-non-None, so FileEdit/PatchApply schema failures keep going
    #     to edit-retry first (its _error_reason_from_result matches the
    #     blocked status and wins — intended) and the loop-detector's ordering
    #     is unchanged. This control IS the plugin (a BaseLoopControl with a
    #     native on_after_tool hook, no adapter needed); it exposes
    #     ``._plugin = self`` so the generic _ExtendedControlPlanePlugin
    #     after_run_callback sweep clears its per-invocation attempt counters.
    schema_feedback_env = parse_tool_schema_feedback_env(env)
    schema_feedback_control = build_schema_feedback_control(
        enabled=schema_feedback_env.enabled,
        max_attempts=schema_feedback_env.max_attempts,
    )
    if schema_feedback_control is not None:
        plane.register(schema_feedback_control)

    # 4. Context compaction (MAGI_CONTEXT_COMPACTION_ENABLED, default OFF).
    compaction_env = parse_context_compaction_env(env)
    compaction_plugin = build_context_compaction_plugin(
        enabled=compaction_env.enabled,
        token_threshold=compaction_env.token_threshold,
        tail_events=compaction_env.tail_events,
    )
    if compaction_plugin is not None:
        plane.register(_CompactionLoopControl(compaction_plugin))

    # 5. MaxStepsBrake (MAGI_MAX_STEPS_BRAKE_ENABLED, default OFF — new seam).
    if _is_true(env.get(MAX_STEPS_BRAKE_ENABLED_ENV, "")):
        # Iteration tracking is per-invocation; default max_iterations is 0 (no-op)
        # until a runner sets a real budget. The control wires the seam; the runner
        # must update iteration/max_iterations per invocation for real brake behavior.
        # For the plane registration we use a sentinel instance — the runner injects
        # a per-turn instance via on_before_model with the current iteration count.
        # Simplest correct approach: register with iteration=0, max_iterations=0
        # (no-op until the runner updates it). Turn-level iteration tracking remains
        # an engine.py concern (PR4 scope); here we only prove the seam is wired.
        plane.register(MaxStepsBrakeControl(max_iterations=0, iteration=0))

    # 6. Self-review C1 (MAGI_SELF_REVIEW_ENABLED, default OFF).
    if _is_true(env.get(SELF_REVIEW_ENABLED_ENV, "")):
        plane.register(
            SelfReviewAfterTurnControl(
                fork_runner=self_review_fork_runner,
                candidate_sink=self_review_candidate_sink,
                config=self_review_config,
                now=self_review_now,
                scheduler=self_review_scheduler,
            )
        )

    # 7. GA constraint reminder (MAGI_GA_LIVE_ENABLED + general role).
    # Registered ONLY when BOTH a receipts store and a contract requirement are
    # provided and the runtime profile enables GA live controls. Full local
    # profile defaults ON; safe/minimal profiles or explicit false values keep
    # the plane conservative. The control itself is also role/owed-gated at
    # runtime via ga_constraint_reinjection, so registration alone remains inert
    # when nothing is owed.
    if (
        general_automation_receipts is not None
        and contract_required is not None
        and general_automation_live_enabled(env)
    ):
        plane.register(
            GaConstraintReinjectionControl(
                receipts=general_automation_receipts,
                contract_required=contract_required,
                agent_role=agent_role,
                env=env,
            )
        )

    # 7. Facts-survey replanning (MAGI_FACTS_REPLAN_ENABLED, default OFF).
    # Imported here (like the other adk_bridge builders above) to avoid a
    # circular import: facts_replan_control imports BaseLoopControl from this
    # module.
    from magi_agent.adk_bridge.facts_replan_control import build_facts_replan_control

    facts_replan = build_facts_replan_control(env)
    if facts_replan is not None:
        plane.register(facts_replan)

    # 8. Tool-synthesis reflection nudge (MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED,
    # default OFF + frontier-tier model only). Registered LAST so edit-retry /
    # resilience overrides win the first-non-None-wins after-tool fan-out.
    # Callers that do not pass a model label (all pre-existing build sites)
    # skip this branch entirely — byte-identical plane.
    if tool_synthesis_model_label is not None:
        from magi_agent.adk_bridge.tool_synthesis_nudge import (  # noqa: PLC0415
            build_tool_synthesis_nudge_plugin,
        )
        from magi_agent.runtime.tool_synthesis import (  # noqa: PLC0415
            tool_synthesis_nudge_active,
        )

        nudge_plugin = build_tool_synthesis_nudge_plugin(
            enabled=tool_synthesis_nudge_active(
                model_label=tool_synthesis_model_label,
                env=env,
            )
        )
        if nudge_plugin is not None:
            plane.register(_ToolSynthesisNudgeLoopControl(nudge_plugin))

    return plane


def build_default_plugin(
    os_environ: dict[str, str] | None = None,
    *,
    general_automation_receipts: Any | None = None,
    contract_required: Any | None = None,
    agent_role: str = "general",
    self_review_fork_runner: Any | None = None,
    self_review_candidate_sink: Any | None = None,
    self_review_config: Any | None = None,
    self_review_now: datetime | None = None,
    self_review_scheduler: Callable[[Coroutine[Any, Any, None]], None] | None = None,
    tool_synthesis_model_label: str | None = None,
) -> _ExtendedControlPlanePlugin:
    """Build the single ControlPlanePlugin for runner construction.

    Returns an ``_ExtendedControlPlanePlugin`` that forwards all three
    extended callbacks (on_tool_error_callback, on_model_error_callback,
    after_run_callback) via generic fan-out over the plane's registered controls.

    Optional ``general_automation_receipts`` / ``contract_required`` enable the GA
    constraint reminder control (see :func:`build_default_plane`). When omitted
    the plugin is byte-identical to ``main``. ``tool_synthesis_model_label``
    feeds the default-OFF tool-synthesis nudge gate (see
    :func:`build_default_plane`); ``None`` skips it entirely.
    """
    env = os_environ if os_environ is not None else dict(os.environ)
    plane = build_default_plane(
        os_environ=env,
        general_automation_receipts=general_automation_receipts,
        contract_required=contract_required,
        agent_role=agent_role,
        self_review_fork_runner=self_review_fork_runner,
        self_review_candidate_sink=self_review_candidate_sink,
        self_review_config=self_review_config,
        self_review_now=self_review_now,
        self_review_scheduler=self_review_scheduler,
        tool_synthesis_model_label=tool_synthesis_model_label,
    )
    return _ExtendedControlPlanePlugin(plane)


def _is_true(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


__all__ = [
    "BaseLoopControl",
    "CONTROL_PLANE_PLUGIN_NAME",
    "ControlPlane",
    "ControlPlanePlugin",
    "GA_CONSTRAINT_REINJECTION_CONTROL_NAME",
    "GaConstraintReinjectionControl",
    "LoopControl",
    "MAX_STEPS_BRAKE_CONTROL_NAME",
    "MAX_STEPS_BRAKE_ENABLED_ENV",
    "MaxStepsBrakeControl",
    "SELF_REVIEW_AFTER_TURN_CONTROL_NAME",
    "SELF_REVIEW_ENABLED_ENV",
    "SelfReviewAfterTurnControl",
    "ToolDecision",
    "build_default_plane",
    "build_default_plugin",
]
