"""Bridge authored customize tool-boundary rules onto the CLI engine's ADK
before/after-tool callbacks (N-01).

This is the THIRD agent-level tool-callback bridge, wired after the permission
gate (``engine.py`` ``_attach_gate_callback``) and the user settings.json
HookBus bridge (``cli/hook_wiring.py`` ``attach_hook_bus_tool_callbacks``). It
makes the four customize tool-boundary slots advertised by the customize wizard
fire at the live tool boundary instead of only inside the composed
``magi_agent.facades.execute_tool_with_hooks`` facade (which has no production
caller):

* ``prompt_injection`` at ``before_tool_use`` (append into tool args),
* ``shell_command`` at ``before_tool_use`` / ``after_tool_use`` (block / audit),
* ``shell_check`` at ``before_tool_use`` (block),
* ``output_rewrite`` at ``after_tool_use`` (redact the tool response text).

Callback contract (the canonical reference is the ``engine.py``
``_build_gate_before_tool`` docstring, verified against the installed
``google/adk/flows/llm_flows/functions.py``): a before callback returning a dict
SKIPS the tool and uses the dict as the result (DENY); returning None lets the
tool run (ALLOW); mutating ``args`` in place rewrites the tool input
(UPDATED_INPUT). An after callback returning a non-None dict replaces the tool
response; returning None keeps the original.

Composition order
-----------------
Both bridges are APPENDED after any pre-existing callbacks, so a gate deny (or a
user hook block) still short-circuits first::

    gate -> user hook -> customize rules

Activation
----------
There is no dedicated wiring flag. The bridge attaches only when at least one of
the four customize master flags resolves ON (via the same triple-gated
``facades`` helpers that guard the stages themselves); when all four are OFF the
attach is a no-op and a turn is byte-identical to today. Each stage is
additionally triple-gated + fail-open inside the facades seam, and the bridges
wrap the whole call in a fail-open ``try`` as a second line of defense so a
customize rule can never break a turn.
"""

from __future__ import annotations

import logging

from magi_agent.tools.result import ToolResult

logger = logging.getLogger(__name__)

__all__ = [
    "customize_tool_boundary_enabled",
    "attach_customize_tool_callbacks",
    "restore_customize_tool_callbacks",
]


def customize_tool_boundary_enabled() -> bool:
    """Return True when at least one customize tool-boundary slot is ON.

    Reuses the triple-gated ``facades`` enablement helpers so this check tracks
    the exact same master-flag + verification + custom_rules gate the stages
    use. Fail-open: any import / read error returns False so the bridge simply
    does not attach.
    """
    try:
        from magi_agent.facades import (  # noqa: PLC0415
            _output_rewrite_enabled,
            _prompt_injection_enabled,
            _shell_check_enabled,
            _shell_command_enabled,
        )

        return (
            _prompt_injection_enabled()
            or _shell_command_enabled()
            or _shell_check_enabled()
            or _output_rewrite_enabled()
        )
    except Exception:  # noqa: BLE001 - fail-open
        return False


class _CustomizeAttachment:
    """Restoration handle for a customize before/after-tool bridge attachment."""

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


def _build_before_tool_bridge(*, session_id: str | None, turn_id: str | None):
    """Async ADK ``before_tool_callback`` firing the customize before stages.

    Returns a deny dict (DENY, skips the tool) on a block verdict, mutates
    ``args`` in place on a prompt_injection append (UPDATED_INPUT), and returns
    None (ALLOW) otherwise. Fail-open on any error.
    """

    async def _before_tool(*, tool, args, tool_context=None):
        _ = tool_context
        tool_name = getattr(tool, "name", "tool")
        try:
            from magi_agent.facades import (  # noqa: PLC0415
                apply_customize_before_tool_stages,
            )

            snapshot = dict(args)
            new_args, blocked = await apply_customize_before_tool_stages(
                tool_name=tool_name,
                arguments=snapshot,
                session_id=session_id,
                turn_id=turn_id,
            )
            if blocked is not None:
                deny: dict[str, object] = {
                    "status": "blocked",
                    "error": "customize_rule_blocked",
                    "tool": tool_name,
                }
                metadata = getattr(blocked, "metadata", None) or {}
                for key in ("blocked_by", "rule_id", "exit_code", "reason"):
                    value = metadata.get(key)
                    if value is not None:
                        deny[key] = value
                return deny
            if new_args != dict(args):
                args.clear()
                args.update(new_args)
            return None
        except Exception:  # noqa: BLE001 - customize rules must never break a turn
            logger.debug(
                "customize before-tool bridge raised; failing open", exc_info=True
            )
            return None

    return _before_tool


def _build_after_tool_bridge(*, session_id: str | None, turn_id: str | None):
    """Async ADK ``after_tool_callback`` firing the customize after stages.

    Only dict tool responses are rewritten. ``output_rewrite`` runs per string
    key (``output`` / ``llmOutput`` / ``transcriptOutput``) so a redact rule
    scrubs each surfaced text; the ``shell_command`` after-audit runs EXACTLY
    ONCE per tool call on the representative ``output`` text (not once per key)
    so a noisy after-audit rule stays within the per-turn shell budget. Returns
    the mutated dict when any key changed, else None (keeps the original).
    Fail-open on any error.
    """

    async def _after_tool(*, tool, args, tool_context=None, tool_response=None):
        _ = (args, tool_context)
        tool_name = getattr(tool, "name", "tool")
        try:
            from magi_agent.facades import (  # noqa: PLC0415
                apply_output_rewrite_stage,
                apply_shell_command_after_stage,
            )

            if not isinstance(tool_response, dict):
                # No dict response to rewrite; still run the shell after-audit
                # once (parity with the facade after-stage) on empty text.
                await apply_shell_command_after_stage(
                    tool_name=tool_name,
                    result_output="",
                    session_id=session_id,
                    turn_id=turn_id,
                )
                return None

            updated: dict[str, object] = dict(tool_response)
            changed = False
            for key in ("output", "llmOutput", "transcriptOutput"):
                text = updated.get(key)
                if not isinstance(text, str):
                    continue
                rewritten = apply_output_rewrite_stage(
                    result=ToolResult(status="ok", output=text),
                    tool_name=tool_name,
                )
                if isinstance(rewritten.output, str) and rewritten.output != text:
                    updated[key] = rewritten.output
                    changed = True

            # Shell after-audit fires exactly once on the representative text.
            representative = updated.get("output")
            await apply_shell_command_after_stage(
                tool_name=tool_name,
                result_output=(
                    representative if isinstance(representative, str) else ""
                ),
                session_id=session_id,
                turn_id=turn_id,
            )
            return updated if changed else None
        except Exception:  # noqa: BLE001 - customize rules must never break a turn
            logger.debug(
                "customize after-tool bridge raised; failing open", exc_info=True
            )
            return None

    return _after_tool


def attach_customize_tool_callbacks(
    *,
    runner: object,
    session_id: str | None,
    turn_id: str | None,
) -> _CustomizeAttachment | None:
    """Attach the customize before/after-tool bridges onto the runner's agent.

    No-op (returns ``None``) when no customize tool-boundary slot is ON
    (:func:`customize_tool_boundary_enabled`) or the runner exposes no
    ``agent`` — so the agentless test runners and the all-OFF path stay
    byte-identical.

    Both bridges are APPENDED after any pre-existing callbacks (gate, user
    hook), giving the order ``gate -> user hook -> customize rules``. The
    original callbacks are captured for ``finally`` restore.
    """
    if not customize_tool_boundary_enabled():
        return None
    agent = getattr(runner, "agent", None)
    if agent is None:
        return None

    original_before = getattr(agent, "before_tool_callback", None)
    original_after = getattr(agent, "after_tool_callback", None)

    before_bridge = _build_before_tool_bridge(
        session_id=session_id, turn_id=turn_id
    )
    after_bridge = _build_after_tool_bridge(
        session_id=session_id, turn_id=turn_id
    )

    agent.before_tool_callback = [*_as_list(original_before), before_bridge]
    agent.after_tool_callback = [*_as_list(original_after), after_bridge]

    return _CustomizeAttachment(
        agent=agent,
        original_before=original_before,
        original_after=original_after,
    )


def restore_customize_tool_callbacks(
    attachment: _CustomizeAttachment | None,
) -> None:
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
