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

    result = await dispatcher.dispatch(
        tool_name, arguments, context, mode=mode, exposed_tool_names=exposed_tool_names
    )

    after_result = hook_bus.run(
        point=HookPoint.AFTER_TOOL_USE,
        context=hook_context,
        harness_state=harness_state,
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
