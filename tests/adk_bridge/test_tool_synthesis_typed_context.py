"""Phase-6 typed-context migration of the tool-synthesis nudge (#512).

``_ToolSynthesisNudgeLoopControl`` gains the ``apply_after_tool`` typed-context
entry point (P5 pattern, same shape as ``_EditRetryLoopControl.apply_after_tool``).
The wrapped plugin is stateless — there is NO per-invocation tracking to move
onto S-C ``ctx.per_invocation`` — so the entry delegates to the plugin's pure
decision; behavior is byte-identical to ``on_after_tool``. The registered-LAST
ordering guarantee is owned by the pack manifest priority field (see the
bundled control_plane pack), not by this adapter.
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import _ToolSynthesisNudgeLoopControl
from magi_agent.adk_bridge.tool_synthesis_nudge import (
    TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY,
    MagiToolSynthesisNudgePlugin,
)
from magi_agent.packs.context import ControlPlaneContext
from magi_agent.runtime.tool_synthesis import TOOL_SYNTHESIS_NUDGE_TEXT


def _run(coro):
    return asyncio.run(coro)


class _FakeTool:
    name = "Bash"


def _apply(control: _ToolSynthesisNudgeLoopControl, result: object):
    return _run(
        control.apply_after_tool(
            ControlPlaneContext.minimal(),
            tool=_FakeTool(),
            args={"command": "ls"},
            tool_context=None,
            result=result,
        )
    )


def test_apply_after_tool_appends_nudge() -> None:
    control = _ToolSynthesisNudgeLoopControl(MagiToolSynthesisNudgePlugin())
    override = _apply(control, {"status": "ok", "output": "hello"})
    assert override is not None
    assert override[TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY] == TOOL_SYNTHESIS_NUDGE_TEXT
    assert override["status"] == "ok"


def test_apply_after_tool_keeps_skip_rules() -> None:
    control = _ToolSynthesisNudgeLoopControl(MagiToolSynthesisNudgePlugin())
    # synthetic injected response (anti-stacking) and truncated output both skip
    assert _apply(control, {"response_type": "MAGI_EDIT_RETRY_REFLECTION"}) is None
    assert _apply(control, {"status": "ok", "truncated": True}) is None
    assert _apply(control, "not a mapping") is None


def test_apply_matches_on_after_tool_byte_for_byte() -> None:
    control = _ToolSynthesisNudgeLoopControl(MagiToolSynthesisNudgePlugin())
    result = {"status": "ok", "output": "hello", "durationMs": 3}
    via_hook = _run(
        control.on_after_tool(
            tool=_FakeTool(), args={"command": "ls"}, tool_context=None,
            result=dict(result),
        )
    )
    via_apply = _apply(control, dict(result))
    assert via_hook == via_apply
