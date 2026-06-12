"""Bridge CC-style user ``settings.json`` hooks into the CLI engine's ADK
before/after-tool callbacks (cluster doc 11 PR2).

This is the **wiring** layer that connects the (already-complete) HookBus
infrastructure and the (PR1) ``settings.json`` loader to the live engine turn
flow. It is **command-executor only** for now — http/llm executors are deferred
to a later PR (the bus is constructed with ``http_executor=None`` /
``llm_executor=None`` so those hook kinds fail open).

Activation
----------
Everything here is gated by ``MAGI_USER_HOOKS_ENABLED`` (default OFF). When the
gate is OFF, :func:`build_user_hook_bus` returns ``None`` and the engine never
attaches any bridge — a turn is byte-identical to today. The gate must remain
OFF in hosted multi-tenant deployments: command hooks run operator-supplied
``bash -c`` and are intended for **self-host / local CLI only**.

Callback order (the conflict matrix, doc 11 §5)
-----------------------------------------------
The engine attaches several ``before_tool_callback`` layers. The fixed order is::

    gate (PermissionGate)   →  user hook (HookBus bridge)  →  control-plane  →  runner_policy_route

i.e. the **gate is prepended FIRST** (engine ``_attach_gate_callback``) so a
permission deny short-circuits before anything else; the HookBus bridge is then
attached *after* the gate (appended to the gate's list) so a user PreToolUse
hook runs only on calls the gate already allowed. ``after_tool_callback`` runs
the ``PostToolUse`` (AFTER_TOOL_USE) bridge, which observes the tool result and
never blocks it.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from magi_agent.config.env import is_user_hooks_enabled
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, RegisteredHook
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.executors.command_executor import CommandHookExecutor
from magi_agent.hooks.external_config import ExternalHookConfig
from magi_agent.hooks.manifest import HookPoint
from magi_agent.hooks.settings_loader import load_settings_hooks

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "build_user_hook_bus",
    "attach_hook_bus_tool_callbacks",
    "restore_hook_bus_tool_callbacks",
]


def _user_settings_path() -> str:
    """Global ``~/.magi/settings.json`` path (``HOME`` honoured for tests)."""
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".magi", "settings.json")


def _workspace_settings_path(workspace_root: str | None) -> str | None:
    if not workspace_root:
        return None
    return os.path.join(workspace_root, ".magi", "settings.json")


def build_user_hook_bus(*, workspace_root: str | None = None) -> HookBus | None:
    """Load user + workspace ``settings.json`` hooks and build a command-wired
    :class:`HookBus`.

    Returns ``None`` when:
    - ``MAGI_USER_HOOKS_ENABLED`` is OFF (default), or
    - neither settings file declares any hooks.

    Loading order (doc 11 §6 decision 2): the **global** user hooks are loaded
    first, then the **workspace** hooks are appended so a project's hooks layer
    on top of the user's (both are active; explicit override is a follow-up).

    The bus is wired with the command executor only; http/llm executors are left
    ``None`` (deferred PR) so those hook kinds fail open.
    """
    if not is_user_hooks_enabled():
        return None

    config = ExternalHookConfig.from_env()

    hooks: list[RegisteredHook] = []
    user_path = _user_settings_path()
    hooks.extend(load_settings_hooks(user_path, config))

    workspace_path = _workspace_settings_path(workspace_root)
    if workspace_path is not None:
        hooks.extend(load_settings_hooks(workspace_path, config))

    if not hooks:
        return None

    logger.info("user hooks: building HookBus with %d hook(s)", len(hooks))
    return HookBus(
        hooks=tuple(hooks),
        command_executor=CommandHookExecutor(),
        http_executor=None,
        llm_executor=None,
    )


class _HookAttachment:
    """Restoration handle for a HookBus before/after-tool bridge attachment."""

    __slots__ = ("agent", "original_before", "original_after")

    def __init__(
        self,
        *,
        agent: object,
        original_before: object,
        original_after: object,
    ) -> None:
        self.agent = agent
        self.original_before = original_before
        self.original_after = original_after


def _as_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _deny_result(tool_name: str, reason: str | None) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "blocked",
        "error": "hook_blocked",
        "tool": tool_name,
    }
    if reason is not None:
        result["feedback"] = reason
    return result


def _build_before_tool_bridge(bus: HookBus, hook_context: HookContext):
    """Async ADK ``before_tool_callback`` that runs the BEFORE_TOOL_USE hooks.

    Returns a deny dict (skips the tool) when the bus's final action is
    ``block`` or a ``deny`` permission decision; returns ``None`` (tool runs)
    otherwise. Fail-open is owned by the bus / command executor.
    """
    harness_state = build_default_resolved_harness_state()

    async def _before_tool(*, tool, args, tool_context=None):
        _ = (args, tool_context)
        tool_name = getattr(tool, "name", "tool")
        try:
            run_result = await bus.run_async(
                point=HookPoint.BEFORE_TOOL_USE,
                context=hook_context,
                harness_state=harness_state,
            )
        except Exception:  # noqa: BLE001 - hooks must never break the turn
            logger.debug("user hook before-tool bridge raised; failing open", exc_info=True)
            return None

        boundary = run_result.permission_boundary
        if boundary is not None and boundary.decision == "deny":
            return _deny_result(tool_name, boundary.reason)
        if run_result.final_action == "block":
            reason = None
            for res in run_result.results:
                if res.action == "block":
                    reason = res.reason
                    break
            return _deny_result(tool_name, reason)
        return None

    return _before_tool


def _build_after_tool_bridge(bus: HookBus, hook_context: HookContext):
    """Async ADK ``after_tool_callback`` that runs the AFTER_TOOL_USE hooks.

    Observe-only: always returns ``None`` so the tool result is never rewritten
    or blocked by a PostToolUse hook in this PR.
    """
    harness_state = build_default_resolved_harness_state()

    async def _after_tool(*, tool, args, tool_context=None, tool_response=None):
        _ = (tool, args, tool_context, tool_response)
        try:
            await bus.run_async(
                point=HookPoint.AFTER_TOOL_USE,
                context=hook_context,
                harness_state=harness_state,
            )
        except Exception:  # noqa: BLE001 - observe-only, never break the turn
            logger.debug("user hook after-tool bridge raised; ignoring", exc_info=True)
        return None

    return _after_tool


def attach_hook_bus_tool_callbacks(
    *,
    runner: object,
    bus: HookBus | None,
    hook_context: HookContext,
) -> _HookAttachment | None:
    """Bridge *bus* onto the runner's agent ``before_tool_callback`` /
    ``after_tool_callback``.

    No-op (returns ``None``) when *bus* is ``None`` or the runner exposes no
    ``agent`` — keeps the agentless ``MockRunner`` tests and the gate-OFF path
    byte-identical.

    Composition: the before-tool bridge is **appended AFTER** any pre-existing
    callbacks (e.g. the gate prepended by ``_attach_gate_callback``), so a gate
    deny still short-circuits first (doc 11 §5 conflict matrix). The original
    callbacks are captured for ``finally`` restore on every exit path.
    """
    if bus is None:
        return None
    agent = getattr(runner, "agent", None)
    if agent is None:
        return None

    original_before = getattr(agent, "before_tool_callback", None)
    original_after = getattr(agent, "after_tool_callback", None)

    before_bridge = _build_before_tool_bridge(bus, hook_context)
    after_bridge = _build_after_tool_bridge(bus, hook_context)

    agent.before_tool_callback = [*_as_list(original_before), before_bridge]
    agent.after_tool_callback = [*_as_list(original_after), after_bridge]

    return _HookAttachment(
        agent=agent,
        original_before=original_before,
        original_after=original_after,
    )


def restore_hook_bus_tool_callbacks(attachment: _HookAttachment | None) -> None:
    """Restore the original before/after-tool callbacks (``finally`` cleanup)."""
    if attachment is None:
        return
    try:
        attachment.agent.before_tool_callback = attachment.original_before
    except Exception:  # noqa: BLE001 - best-effort restore
        pass
    try:
        attachment.agent.after_tool_callback = attachment.original_after
    except Exception:  # noqa: BLE001 - best-effort restore
        pass
