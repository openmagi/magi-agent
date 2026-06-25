"""High-level entry-point facades that compose existing modules.

Each facade saves the caller >= 3 lines versus calling modules directly
while adding zero duplicated logic.
"""

from __future__ import annotations

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

    # F-MUT1 prompt_injection rule mutator. Triple-gated + fail-open: bails
    # silently when the master flag is OFF, when no rules are authored, or on
    # any exception. Runs AFTER the HookBus replace branch so a hook-authored
    # full replacement composes deterministically with rule-driven appends.
    arguments = _maybe_apply_prompt_injection_to_tool_args(
        arguments=arguments, tool_name=tool_name
    )

    # F-EXEC1 shell_command rule action at ``before_tool_use``. Triple-gated +
    # fail-open. Runs AFTER the F-MUT1 prompt_injection mutator so the
    # operator-authored shell hook sees the final dispatch arguments. A rule
    # with ``action == "block"`` that exits with a non-zero exit code returns
    # a blocked ToolResult immediately (no dispatch, no after-hook). Rules
    # with ``action == "audit"`` always run to completion and never block.
    blocked = await _maybe_apply_shell_command_before_tool(
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

    # F-MUT2 output_rewrite rule mutator. Triple-gated + fail-open: bails
    # silently when the master flag is OFF, when no rules are authored, or
    # on any exception. Runs AFTER the HookBus replace branch so a
    # hook-authored full overlay composes deterministically with the
    # rule-driven redact (the rule sees the hook's overlaid text and may
    # redact additional patterns from it).
    result = _maybe_apply_output_rewrite(result=result, tool_name=tool_name)

    # F-EXEC1 shell_command rule action at ``after_tool_use``. Audit-only:
    # the dispatch already returned, so a "block" action has no honest
    # semantics here. Runs the operator-authored scripts (subject to the
    # per-turn budget cap enforced by the LifecycleShellCommandControl
    # plugin) and silently discards any verdict.
    await _maybe_apply_shell_command_after_tool(
        tool_name=tool_name, result_output=_result_output_text(result)
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
        from magi_agent.customize.prompt_injection import (  # noqa: PLC0415
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
    *, tool_name: str, arguments: dict[str, object]
) -> ToolResult | None:
    """F-EXEC1 facades helper: before-dispatch ``shell_command`` consumer.

    Triple-gated by :func:`_shell_command_enabled`. Returns a blocked
    :class:`ToolResult` when any enabled ``shell_command`` rule with
    ``firesAt == "before_tool_use"`` AND ``action == "block"`` exits with a
    non-zero code (first failing rule wins). Returns ``None`` otherwise —
    audit-action rules run silently; ``proceed`` verdict means dispatch
    continues normally. Fail-open: any unexpected exception returns ``None``
    (no block).
    """
    try:
        if not _shell_command_enabled():
            return None
        from magi_agent.customize.shell_command import (  # noqa: PLC0415
            apply_shell_command_rule,
        )
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        rules = policy.enabled_shell_command_rules(fires_at="before_tool_use")
        if not rules:
            return None

        stdin_json = {
            "lifecycle": "before_tool_use",
            "tool_name": tool_name,
            "tool_args": _safe_json(arguments),
        }

        for rule in rules:
            audit, verdict = await apply_shell_command_rule(
                rule,
                tool_name=tool_name,
                stdin_json=stdin_json,
                honor_block_action=True,
            )
            if verdict == "block":
                return ToolResult(
                    status="blocked",
                    metadata={
                        "blocked_by": "shell_command_rule",
                        "rule_id": audit.get("rule_id"),
                        "exit_code": audit.get("exit_code"),
                    },
                )
        return None
    except Exception:  # noqa: BLE001 — fail-open
        return None


async def _maybe_apply_shell_command_after_tool(
    *, tool_name: str, result_output: str
) -> None:
    """F-EXEC1 facades helper: after-dispatch ``shell_command`` consumer.

    Triple-gated by :func:`_shell_command_enabled`. Audit-only — the tool
    has already returned by the time we observe it. Iterates every enabled
    ``shell_command`` rule with ``firesAt == "after_tool_use"`` and invokes
    the runner under the shared per-turn budget cap (the budget itself is
    maintained by
    :class:`magi_agent.adk_bridge.lifecycle_shell_command_control
    .LifecycleShellCommandControl`; facades stays stateless). Fail-open on
    any exception.
    """
    try:
        if not _shell_command_enabled():
            return
        from magi_agent.customize.shell_command import (  # noqa: PLC0415
            apply_shell_command_rule,
        )
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        rules = policy.enabled_shell_command_rules(fires_at="after_tool_use")
        if not rules:
            return

        stdin_json = {
            "lifecycle": "after_tool_use",
            "tool_name": tool_name,
            "tool_output": result_output[:4096],
        }
        for rule in rules:
            await apply_shell_command_rule(
                rule,
                tool_name=tool_name,
                stdin_json=stdin_json,
                honor_block_action=False,
            )
    except Exception:  # noqa: BLE001 — fail-open
        return


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


__all__ = [
    "execute_tool_with_hooks",
]
