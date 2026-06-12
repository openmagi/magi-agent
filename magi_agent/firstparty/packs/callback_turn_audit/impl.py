"""First-party turn-start audit callback provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``CallbackProvideContext`` (D5) and registers a
``HookManifest`` + handler. ``blocking=False`` makes this a fire-and-forget audit
that can never alter the turn outcome — the safe live-default for an authored
callback. The projector (``magi_agent/packs/hook_projection.py``) exposes the
previously-unexposed ``HookRegistry`` into the live ``HookBus``.
"""
from __future__ import annotations

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.packs.context import CallbackProvideContext
from magi_agent.tools.manifest import ToolSource


def _turn_audit_handler(context: HookContext) -> HookResult:
    # Pure-observe, non-blocking audit; never blocks the turn.
    return HookResult(action="continue", reason="turn-audit observed")


def provide_callback(context: CallbackProvideContext) -> None:
    context.register(
        HookManifest(
            name="turn-audit",
            point=HookPoint.BEFORE_TURN_START,
            description="Record a per-turn audit marker at turn start.",
            source=ToolSource(kind="native-plugin", package="magi_agent.firstparty"),
            priority=100,
            blocking=False,
            opt_out=True,
        ),
        _turn_audit_handler,
    )
