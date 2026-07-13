"""High-level entry-point facades that compose existing modules.

Each facade saves the caller >= 3 lines versus calling modules directly
while adding zero duplicated logic.
"""

from __future__ import annotations

from typing import Any

from magi_agent.harness.resolved import ResolvedHarnessPresetState
from magi_agent.hooks.bus import HookBus, HookBusRunResult
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookPoint
from magi_agent.hooks.replace_payloads import (
    AfterToolUseReplace,
    BeforeToolUseReplace,
    coerce_replace_payload,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import RuntimeMode
from magi_agent.tools.result import ToolResult


async def apply_customize_before_tool_stages(
    *,
    tool_name: str,
    arguments: dict[str, object],
    session_id: str | None = None,
    turn_id: str | None = None,
) -> tuple[dict[str, object], ToolResult | None]:
    """Run the customize before-tool-boundary rule stages in order.

    Sequence (identical to the historical inline order in
    :func:`execute_tool_with_hooks`):

    1. F-MUT1 ``prompt_injection`` mutator (appends into the dispatch args).
    2. F-EXEC1 ``shell_command`` action at ``before_tool_use`` (block short
       circuits dispatch).
    3. F-EXEC2 ``shell_check`` condition at ``before_tool_use`` (block short
       circuits dispatch).

    Returns ``(arguments, blocked)``. When ``blocked`` is non-None the caller
    MUST skip dispatch and treat it as the tool result. Each stage is
    triple-gated + fail-open, so an OFF flag / no authored rule / any error
    leaves ``arguments`` unchanged and returns ``(arguments, None)``.

    ``session_id`` / ``turn_id`` are threaded to
    :func:`magi_agent.adk_bridge.lifecycle_shell_command_control.shell_budget_for`
    so the live ADK bridge can name the per-turn shell budget explicitly; the
    facade path passes ``(None, None)`` and lets ``shell_budget_for`` fall back
    to the active-turn ContextVar (byte-identical to today).
    """
    arguments = _maybe_apply_prompt_injection_to_tool_args(
        arguments=arguments, tool_name=tool_name
    )
    blocked = await _maybe_apply_shell_command_before_tool(
        tool_name=tool_name,
        arguments=arguments,
        session_id=session_id,
        turn_id=turn_id,
    )
    if blocked is not None:
        return arguments, blocked
    blocked = await _maybe_apply_shell_check_before_tool(
        tool_name=tool_name,
        arguments=arguments,
        session_id=session_id,
        turn_id=turn_id,
    )
    if blocked is not None:
        return arguments, blocked
    return arguments, None


async def apply_customize_after_tool_stages(
    *,
    tool_name: str,
    result: ToolResult,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> ToolResult:
    """Run the customize after-tool-boundary rule stages in order.

    Sequence (identical to the historical inline order in
    :func:`execute_tool_with_hooks`):

    1. F-MUT2 ``output_rewrite`` mutator (redacts the result text).
    2. F-EXEC1 ``shell_command`` after-audit (audit-only; never un-executes).

    Each stage is triple-gated + fail-open. ``session_id`` / ``turn_id`` are
    threaded to ``shell_budget_for`` as in
    :func:`apply_customize_before_tool_stages`.
    """
    result = _maybe_apply_output_rewrite(result=result, tool_name=tool_name)
    await _maybe_apply_shell_command_after_tool(
        tool_name=tool_name,
        result_output=_result_output_text(result),
        session_id=session_id,
        turn_id=turn_id,
    )
    return result


async def execute_tool_with_hooks(
    dispatcher: ToolDispatcher,
    hook_bus: HookBus,
    *,
    tool_name: str,
    arguments: dict[str, object],
    context: ToolContext,
    hook_context: HookContext,
    harness_state: ResolvedHarnessPresetState,
    mode: RuntimeMode,
    exposed_tool_names: tuple[str, ...] | None = None,
) -> tuple[ToolResult, HookBusRunResult | None, HookBusRunResult | None]:
    """Tool dispatch through beforeToolUse hooks -> dispatch -> afterToolUse hooks.

    Returns ``(tool_result, before_hook_result, after_hook_result)``.
    If *beforeToolUse* blocks, returns a blocked ``ToolResult`` with the
    *before_hook_result* and ``None`` for after.

    F-MUT-AUDIT / F-MUT1: when ``before_result.final_action == "replace"`` the
    typed payload :class:`BeforeToolUseReplace` is projected onto the dispatch
    ``arguments`` dict. A malformed replace value fails safe to the original
    arguments (same fail-safe-original contract as
    :func:`magi_agent.runtime.message_builder._apply_prompt_transform`).
    Additionally, when the F-MUT1 master flag
    ``MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED`` is ON, any enabled
    ``prompt_injection`` rules with ``firesAt == "before_tool_use"`` get a
    chance to append into the args via
    :func:`magi_agent.customize.prompt_injection.apply_prompt_injection_to_tool_args`.
    The HookBus replace branch runs FIRST so a hook-authored full replacement
    composes deterministically with the rule-driven append.

    F-MUT2: symmetric wire on the AFTER_TOOL_USE side. When the after-hook
    bus returns ``final_action == "replace"`` the typed payload
    :class:`AfterToolUseReplace` overlays its non-None fields
    (``result_text`` → ``output``, ``status``, ``structured_data`` →
    ``metadata``) onto the dispatched :class:`ToolResult`. Then, when the
    F-MUT2 master flag ``MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED`` is ON, any
    enabled ``output_rewrite`` rules get a chance to rewrite the result text
    via
    :func:`magi_agent.customize.output_rewrite.apply_output_rewrite_to_tool_result`.
    The HookBus replace branch runs FIRST so a hook-authored full overlay
    composes deterministically with the rule-driven redact.
    """
    before_result = hook_bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=hook_context,
        harness_state=harness_state,
    )
    if before_result.final_action == "block":
        return (
            ToolResult(status="blocked", metadata={"blocked_by": "beforeToolUse_hook"}),
            before_result,
            None,
        )

    # F-MUT-AUDIT replace consumer. Iterate the per-hook results so each one's
    # typed payload is validated independently; an invalid payload from one
    # hook never poisons the rest. Mirrors message_builder.py's per-result
    # loop pattern. Last-write-wins between multiple valid replace values.
    if before_result.final_action == "replace":
        for hook_result in before_result.results:
            if hook_result.action != "replace":
                continue
            payload = coerce_replace_payload(
                HookPoint.BEFORE_TOOL_USE, hook_result.value
            )
            if isinstance(payload, BeforeToolUseReplace):
                arguments = dict(payload.arguments)
            # On non-None mismatch (coerce returned None for a non-dict / bad
            # schema), fail safe to the original arguments — same contract as
            # message_builder._apply_prompt_transform's "failing safe to
            # original sections" branch.

    # Customize before-tool-boundary stages (F-MUT1 prompt_injection ->
    # F-EXEC1 shell_command block -> F-EXEC2 shell_check block). Extracted into
    # :func:`apply_customize_before_tool_stages` so the same seam is consumed by
    # both this composed facade AND the live ADK before_tool_callback bridge in
    # :mod:`magi_agent.cli.customize_tool_wiring`. The facade path passes no
    # explicit ``(session_id, turn_id)`` so ``shell_budget_for`` resolves the
    # per-turn identity from the ContextVar (byte-identical to today).
    arguments, blocked = await apply_customize_before_tool_stages(
        tool_name=tool_name, arguments=arguments
    )
    if blocked is not None:
        return blocked, before_result, None

    result = await dispatcher.dispatch(
        tool_name, arguments, context, mode=mode, exposed_tool_names=exposed_tool_names
    )

    after_result = hook_bus.run(
        point=HookPoint.AFTER_TOOL_USE,
        context=hook_context,
        harness_state=harness_state,
    )

    # F-MUT2 replace consumer. Symmetric with the BEFORE_TOOL_USE branch
    # above: iterate per-hook results, validate each one independently, and
    # overlay non-None typed fields onto the ToolResult. Multiple valid
    # replace values compose last-write-wins per field. A non-dict /
    # bad-schema value fails safe to the original result — same
    # fail-safe-original contract as message_builder._apply_prompt_transform.
    if after_result.final_action == "replace":
        for hook_result in after_result.results:
            if hook_result.action != "replace":
                continue
            payload = coerce_replace_payload(
                HookPoint.AFTER_TOOL_USE, hook_result.value
            )
            if isinstance(payload, AfterToolUseReplace):
                update: dict[str, object] = {}
                if payload.result_text is not None:
                    update["output"] = payload.result_text
                if payload.status is not None:
                    update["status"] = payload.status
                if payload.structured_data is not None:
                    update["metadata"] = payload.structured_data
                if update:
                    result = result.model_copy(update=update)

    # Customize after-tool-boundary stages (F-MUT2 output_rewrite ->
    # F-EXEC1 shell_command after audit). Extracted into
    # :func:`apply_customize_after_tool_stages` so the same seam is consumed by
    # both this composed facade AND the live ADK after_tool_callback bridge in
    # :mod:`magi_agent.cli.customize_tool_wiring`.
    result = await apply_customize_after_tool_stages(
        tool_name=tool_name, result=result
    )

    return result, before_result, after_result


def _maybe_apply_prompt_injection_to_tool_args(
    *, arguments: dict[str, object], tool_name: str
) -> dict[str, object]:
    """F-MUT1 helper: project ``prompt_injection`` rules onto ``arguments``.

    Triple-gated by :func:`_prompt_injection_enabled` (master flag +
    verification + custom_rules). Fail-open: any import / I/O / validation
    error returns the unmodified ``arguments``. Returns the original mapping
    when no rules apply (byte-identical to today's behavior).
    """
    try:
        if not _prompt_injection_enabled():
            return arguments
        # Lazy imports keep the facades module's hot-path free of customize/
        # transitive imports when the flag is OFF.
        from magi_agent.customize.authored_prompt_append import (  # noqa: PLC0415
            apply_prompt_injection_to_tool_args,
        )
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        rules = policy.enabled_prompt_injection_rules(fires_at="before_tool_use")
        if not rules:
            return arguments
        return apply_prompt_injection_to_tool_args(arguments, rules, tool_name)
    except Exception:  # noqa: BLE001 — fail-open
        return arguments


def _maybe_apply_output_rewrite(
    *, result: ToolResult, tool_name: str
) -> ToolResult:
    """F-MUT2 helper: project ``output_rewrite`` rules onto ``result``.

    Triple-gated by :func:`_output_rewrite_enabled` (master flag +
    verification + custom_rules). Fail-open: any import / I/O / validation
    error returns the unmodified ``result``. Returns the original instance
    when no rules apply (byte-identical to today's behavior).
    """
    try:
        if not _output_rewrite_enabled():
            return result
        # Lazy imports keep the facades module's hot-path free of
        # customize/ transitive imports when the flag is OFF.
        from magi_agent.customize.output_rewrite import (  # noqa: PLC0415
            apply_output_rewrite_to_tool_result,
        )
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        rules = policy.enabled_output_rewrite_rules(fires_at="after_tool_use")
        if not rules:
            return result
        return apply_output_rewrite_to_tool_result(result, rules, tool_name)
    except Exception:  # noqa: BLE001 — fail-open
        return result


def _output_rewrite_enabled() -> bool:
    """Triple-gate check used by the F-MUT2 facades wire.

    Returns ``True`` only when:

    * ``MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED`` is strict-truthy ON,
    * ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` resolves ON via the profile-aware
      reader (full / lab profile; OFF under safe/eval),
    * ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` resolves ON via the profile-aware
      reader.

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import (  # noqa: PLC0415
            flag_bool,
            flag_profile_bool,
        )
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        )
    except Exception:
        return False


def _result_output_text(result: ToolResult) -> str:
    """Return ``result.output`` coerced to a string for shell stdin context."""
    out = getattr(result, "output", None)
    return out if isinstance(out, str) else ""


async def _maybe_apply_shell_command_before_tool(
    *,
    tool_name: str,
    arguments: dict[str, object],
    session_id: str | None = None,
    turn_id: str | None = None,
) -> ToolResult | None:
    """F-EXEC1 facades helper: before-dispatch ``shell_command`` consumer.

    Triple-gated by :func:`_shell_command_enabled`. Delegates to
    :func:`magi_agent.customize.lifecycle_audit.run_shell_command_at_before_tool_use`
    so the per-(session, turn) ``shell_budget_for`` counter caps
    tool-boundary spawns alongside the 9 turn / llm / compaction slots.
    Returns a blocked :class:`ToolResult` on a ``block`` verdict; ``None``
    otherwise. Fail-open: any unexpected exception returns ``None``.

    ``session_id`` / ``turn_id`` are passed through to ``shell_budget_for``
    (explicit identity for the live ADK bridge); ``(None, None)`` falls back to
    the active-turn ContextVar.
    """
    try:
        if not _shell_command_enabled():
            return None
        from magi_agent.adk_bridge.lifecycle_shell_command_control import (  # noqa: PLC0415
            shell_budget_for,
        )
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            run_shell_command_at_before_tool_use,
        )

        safe_args: dict[str, Any] | None = None
        if isinstance(arguments, dict):
            snapshot = _safe_json(arguments)
            if isinstance(snapshot, dict):
                safe_args = snapshot
        remaining, decrement_fn = shell_budget_for(session_id, turn_id)
        audits, verdict = await run_shell_command_at_before_tool_use(
            tool_name=tool_name,
            tool_args=safe_args,
            remaining_budget=remaining,
            decrement_fn=decrement_fn,
        )
        if verdict == "block":
            blocking = next(
                (
                    audit
                    for audit in audits
                    if isinstance(audit, dict) and audit.get("passed") is False
                ),
                {},
            )
            return ToolResult(
                status="blocked",
                metadata={
                    "blocked_by": "shell_command_rule",
                    "rule_id": blocking.get("rule_id"),
                    "exit_code": blocking.get("exit_code"),
                },
            )
        return None
    except Exception:  # noqa: BLE001 — fail-open
        return None


async def _maybe_apply_shell_command_after_tool(
    *,
    tool_name: str,
    result_output: str,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> None:
    """F-EXEC1 facades helper: after-dispatch ``shell_command`` consumer.

    Triple-gated by :func:`_shell_command_enabled`. Delegates to
    :func:`magi_agent.customize.lifecycle_audit.run_shell_command_at_after_tool_use`
    so the per-(session, turn) ``shell_budget_for`` counter caps
    tool-boundary spawns alongside the 9 turn / llm / compaction slots.
    Audit-only — the tool has already returned, so any ``block`` rule is
    recorded as audit + ``passed: false`` without un-executing the call.
    Fail-open on any exception.

    ``session_id`` / ``turn_id`` are passed through to ``shell_budget_for``.
    """
    try:
        if not _shell_command_enabled():
            return
        from magi_agent.adk_bridge.lifecycle_shell_command_control import (  # noqa: PLC0415
            shell_budget_for,
        )
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            run_shell_command_at_after_tool_use,
        )

        remaining, decrement_fn = shell_budget_for(session_id, turn_id)
        await run_shell_command_at_after_tool_use(
            tool_name=tool_name,
            tool_output=result_output,
            remaining_budget=remaining,
            decrement_fn=decrement_fn,
        )
    except Exception:  # noqa: BLE001 — fail-open
        return


async def _maybe_apply_shell_check_before_tool(
    *,
    tool_name: str,
    arguments: dict[str, object],
    session_id: str | None = None,
    turn_id: str | None = None,
) -> ToolResult | None:
    """F-EXEC2 facades helper: before-dispatch ``shell_check`` consumer.

    Triple-gated by :func:`_shell_check_enabled`. Delegates to the
    :func:`magi_agent.customize.lifecycle_audit.run_shell_check_at_before_tool_use`
    fan-out helper so the budget plumbing, rule loading, and verdict
    reduction stay in one place. Threads the shared per-(session, turn)
    shell budget through :func:`magi_agent.adk_bridge
    .lifecycle_shell_command_control.shell_budget_for`. When ``session_id`` /
    ``turn_id`` are not given, ``shell_budget_for`` resolves identity from the
    active-turn ContextVar published by ``run_governed_turn``.
    Returns a blocked :class:`ToolResult` on a ``block`` verdict; ``None``
    otherwise. Fail-open: any unexpected exception returns ``None``.
    """
    try:
        if not _shell_check_enabled():
            return None
        from magi_agent.adk_bridge.lifecycle_shell_command_control import (  # noqa: PLC0415
            shell_budget_for,
        )
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            run_shell_check_at_before_tool_use,
        )

        remaining, decrement_fn = shell_budget_for(session_id, turn_id)
        # _safe_json returns either the original dict (when serialisable) or a
        # str fallback; the fan-out helper expects ``dict | None`` so we coerce
        # the fallback case to ``None`` and let the script see only the
        # lifecycle / tool_name keys.
        safe_args: dict[str, Any] | None = None
        if isinstance(arguments, dict):
            snapshot = _safe_json(arguments)
            if isinstance(snapshot, dict):
                safe_args = snapshot
        audits, verdict = await run_shell_check_at_before_tool_use(
            tool_name=tool_name,
            tool_args=safe_args,
            remaining_budget=remaining,
            decrement_fn=decrement_fn,
        )
        if verdict == "block":
            blocking = next(
                (
                    audit
                    for audit in audits
                    if isinstance(audit, dict) and audit.get("passed") is False
                ),
                {},
            )
            return ToolResult(
                status="blocked",
                metadata={
                    "blocked_by": "shell_check_rule",
                    "rule_id": blocking.get("rule_id"),
                    "exit_code": blocking.get("exit_code"),
                    "reason": blocking.get("reason"),
                },
            )
        return None
    except Exception:  # noqa: BLE001 — fail-open
        return None


def _safe_json(obj: object) -> object:
    """Best-effort JSON-friendly snapshot of ``obj`` (cap dict / list depth)."""
    try:
        import json  # noqa: PLC0415

        json.dumps(obj)
        return obj
    except Exception:
        try:
            return str(obj)[:1024]
        except Exception:
            return "<unserializable>"


def _shell_command_enabled() -> bool:
    """Triple-gate check used by the F-EXEC1 facades wire.

    Returns ``True`` only when:

    * ``MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED`` is strict-truthy ON,
    * ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` resolves ON via the profile-aware
      reader (full / lab profile; OFF under safe/eval),
    * ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` resolves ON via the profile-aware
      reader.

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import (  # noqa: PLC0415
            flag_bool,
            flag_profile_bool,
        )
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        )
    except Exception:
        return False


def _shell_check_enabled() -> bool:
    """Triple-gate check used by the F-EXEC2 facades wire.

    Returns ``True`` only when:

    * ``MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED`` is strict-truthy ON,
    * ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` resolves ON via the profile-aware
      reader (full / lab profile; OFF under safe/eval),
    * ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` resolves ON via the profile-aware
      reader.

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import (  # noqa: PLC0415
            flag_bool,
            flag_profile_bool,
        )
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        )
    except Exception:
        return False


def _prompt_injection_enabled() -> bool:
    """Triple-gate check used by the F-MUT1 facades wire.

    Returns ``True`` only when:

    * ``MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED`` is strict-truthy ON,
    * ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` resolves ON via the profile-aware
      reader (full / lab profile; OFF under safe/eval),
    * ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` resolves ON via the profile-aware
      reader.

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import (  # noqa: PLC0415
            flag_bool,
            flag_profile_bool,
        )
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        )
    except Exception:
        return False


# Public rename-aliases for the two after-tool sub-stages. The live ADK
# after_tool_callback bridge in ``cli/customize_tool_wiring.py`` consumes these
# individually (per response key for output_rewrite, exactly once for the shell
# after-audit) so the redact runs per str key without spawning the shell audit
# once per key. The facade's :func:`apply_customize_after_tool_stages` keeps
# calling the underscored originals.
apply_output_rewrite_stage = _maybe_apply_output_rewrite
apply_shell_command_after_stage = _maybe_apply_shell_command_after_tool


__all__ = [
    "execute_tool_with_hooks",
    "apply_customize_before_tool_stages",
    "apply_customize_after_tool_stages",
    "apply_output_rewrite_stage",
    "apply_shell_command_after_stage",
]
