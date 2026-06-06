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

``BaseLoopControl`` — abstract base with no-op defaults for all three hooks;
concrete controls override only the hooks they need.

``ControlPlane`` — ordered registry of ``LoopControl`` instances. Fan-out:

* ``_before_tool``: ordered; first deny short-circuits; rewrite mutates args
  in-place and continues; allow passes through.
* ``_after_tool``: ordered; first non-``None`` override wins.
* ``_before_model``: all controls run (mutations accumulate); always returns
  ``None``.

``ControlPlanePlugin`` — thin ADK ``BasePlugin`` wrapper that forwards each ADK
callback to the ``ControlPlane``. Registered in ``App(plugins=[...])`` once per
runner build; ADK's ``PluginManager`` fans it out to every tool/model event.

Ordering with the permission gate
-----------------------------------
``engine.py:_attach_gate_callback`` prepends the permission gate to
``agent.before_tool_callback`` (agent-level) per-turn. ADK runs *agent-level*
``before_tool_callback`` **before** *plugin-level* ``before_tool_callback``, so
the permission gate always fires first and a deny short-circuits before the
plane ever sees the call. The gate is intentionally kept as-is (agent-level,
per-turn wiring) — wrapping it as a LoopControl is out of scope for this PR.

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

The plane covers ``before_tool`` (deny/rewrite), ``after_tool`` (override), and
``before_model`` (mutation, incl. tool-disable) ONLY.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, runtime_checkable

from typing import Protocol

from google.adk.plugins.base_plugin import BasePlugin

CONTROL_PLANE_PLUGIN_NAME = "magi_control_plane"

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


# ---------------------------------------------------------------------------
# ControlPlane
# ---------------------------------------------------------------------------


class ControlPlane:
    """Ordered registry of LoopControl instances with fan-out dispatch."""

    def __init__(self) -> None:
        self._controls: list[LoopControl] = []

    def register(self, control: LoopControl) -> "ControlPlane":
        """Register a control and return self for chainable building."""
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
) -> ControlPlane:
    """Build the default ControlPlane from environment flags.

    Used by BOTH ``local_runner.py`` and ``real_runner.py`` so they cannot
    drift. Each flag-gated control uses the same env var as before, preserving
    default-OFF behavior for all existing controls.

    Args:
        os_environ: Environment mapping (defaults to ``os.environ``). Injectable
            for tests.

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
    )
    from magi_agent.adk_bridge.context_compaction import build_context_compaction_plugin
    from magi_agent.adk_bridge.edit_retry_reflection import build_edit_retry_reflection_plugin
    from magi_agent.adk_bridge.resilience_plugin import build_resilience_plugin

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

    # 3. Context compaction (MAGI_CONTEXT_COMPACTION_ENABLED, default OFF).
    compaction_env = parse_context_compaction_env(env)
    compaction_plugin = build_context_compaction_plugin(
        enabled=compaction_env.enabled,
        token_threshold=compaction_env.token_threshold,
        tail_events=compaction_env.tail_events,
    )
    if compaction_plugin is not None:
        plane.register(_CompactionLoopControl(compaction_plugin))

    # 4. MaxStepsBrake (MAGI_MAX_STEPS_BRAKE_ENABLED, default OFF — new seam).
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

    return plane


def build_default_plugin(
    os_environ: dict[str, str] | None = None,
) -> _ExtendedControlPlanePlugin:
    """Build the single ControlPlanePlugin for runner construction.

    Returns an ``_ExtendedControlPlanePlugin`` that forwards all three
    extended callbacks (on_tool_error_callback, on_model_error_callback,
    after_run_callback) via generic fan-out over the plane's registered controls.
    """
    env = os_environ if os_environ is not None else dict(os.environ)
    plane = build_default_plane(os_environ=env)
    return _ExtendedControlPlanePlugin(plane)


def _is_true(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


__all__ = [
    "BaseLoopControl",
    "CONTROL_PLANE_PLUGIN_NAME",
    "ControlPlane",
    "ControlPlanePlugin",
    "LoopControl",
    "MAX_STEPS_BRAKE_CONTROL_NAME",
    "MAX_STEPS_BRAKE_ENABLED_ENV",
    "MaxStepsBrakeControl",
    "ToolDecision",
    "build_default_plane",
    "build_default_plugin",
]
