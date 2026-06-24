"""Customize Tier 2 lifecycle audit gates (PR-F-UX1).

Two NEW audit-only ``custom_rule`` gate sites ride on top of the single
``run_governed_turn`` funnel that every governed turn (top-level serve, CLI
REPL, child agents) flows through:

* ``on_user_prompt_submit`` — invoked at the TOP of
  :func:`magi_agent.runtime.governed_turn.run_governed_turn`, BEFORE the
  engine stream is started. The inbound user prompt text (``ctx.prompt``) is
  audited against each enabled ``llm_criterion`` rule with
  ``firesAt == "on_user_prompt_submit"``. Verdicts are recorded audit-only
  (no block, no prompt mutation).
* ``on_subagent_stop`` — invoked at the END of ``run_governed_turn`` when the
  turn is a CHILD turn (``ctx.depth > 0``). The child's final assistant text
  is collected off the event stream (mirroring ``_BookendCollector``) and
  audited against each enabled ``llm_criterion`` rule with
  ``firesAt == "on_subagent_stop"``. Audit-only — the child output has
  already been emitted to the parent.

Both wires live in ``governed_turn`` (the canonical CLI/serve/child funnel)
so the audit FAN-OUT runs on real production turns rather than a dead ADK
callback adapter path.

Triple-gated:

* :func:`magi_agent.config.flags.flag_bool` ``MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED``
  (the strict-truthy F-UX1 master switch),
* profile-aware :func:`magi_agent.config.flags.flag_profile_bool`
  ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED``,
* profile-aware :func:`magi_agent.config.flags.flag_profile_bool`
  ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``.

Fail-open everywhere: any exception (missing module, broken overrides file,
critic model unavailable) returns silently so a buggy rule cannot wedge a
turn. Audit-only contract: this module never returns a "block" verdict — only
records the per-rule judgment.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

AuditRecord = dict[str, Any]

InvokeFn = Callable[[Any, str], Awaitable[str]]


def lifecycle_expansion_enabled(env: dict[str, str] | None = None) -> bool:
    """Triple-gate check used by both wire sites.

    Returns ``True`` only when:

    * ``MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED`` is strict-truthy ON,
    * ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` resolves ON via the profile-aware
      reader (full / lab profile; OFF under safe/eval),
    * ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` resolves ON via the profile-aware
      reader.

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import flag_bool, flag_profile_bool  # noqa: PLC0415
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", env=env)
        )
    except Exception:
        return False


def lifecycle_turn_hooks_enabled(env: dict[str, str] | None = None) -> bool:
    """PR-F-LIFE1 triple-gate check for the turn-boundary fan-outs.

    Mirrors :func:`lifecycle_expansion_enabled` but keys on the F-LIFE1 master
    switch ``MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED`` so the two slot
    families (Tier 2 prompt/subagent vs Tier 2 turn boundary) can be staged
    independently. Fail-open: any import error returns ``False`` so the call
    site stays a no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import flag_bool, flag_profile_bool  # noqa: PLC0415
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", env=env)
        )
    except Exception:
        return False


def llm_call_hooks_enabled(env: dict[str, str] | None = None) -> bool:
    """PR-F-LIFE2 triple-gate check for the per-LLM-call audit fan-outs.

    Mirrors :func:`lifecycle_turn_hooks_enabled` but keys on the F-LIFE2
    master switch ``MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED`` so the per-call
    slot family (before_llm_call + after_llm_call) can be staged
    independently. These slots fire on EVERY LLM call inside a turn so the
    OFF path must be byte-identical with zero per-call overhead — every wire
    site is required to check this helper FIRST and bail before any further
    work (the helper itself is cheap, but a single env miss avoids any policy
    load / criterion judge work on the per-call hot path).

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import flag_bool, flag_profile_bool  # noqa: PLC0415
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", env=env)
        )
    except Exception:
        return False


def _default_policy_loader() -> Any:
    from magi_agent.customize.store import load_overrides  # noqa: PLC0415
    from magi_agent.customize.verification_policy import (  # noqa: PLC0415
        CustomizeVerificationPolicy,
    )

    return CustomizeVerificationPolicy.from_overrides(load_overrides())


async def _audit_one_rule(
    rule: dict[str, Any],
    *,
    draft_text: str,
    model_factory: Callable[[], Any] | None,
    invoke: InvokeFn | None,
) -> AuditRecord:
    """Run a single ``llm_criterion`` rule against ``draft_text``; return audit dict.

    On any failure — bad payload, missing critic model, judge exception — the
    audit dict carries ``passed=True`` with a status describing why the rule
    short-circuited. This matches the fail-open contract from
    :func:`magi_agent.customize.criterion_engine.evaluate_criterion`.
    """
    rule_id = rule.get("id")
    payload = rule.get("what", {}).get("payload", {}) if isinstance(rule.get("what"), dict) else {}
    criterion = payload.get("criterion") if isinstance(payload, dict) else None
    if not isinstance(criterion, str) or not criterion.strip():
        return {
            "rule_id": rule_id,
            "passed": True,
            "reason": "rule has no criterion text",
            "status": "skipped",
        }
    # Finding #3 guard: an empty draft_text means there is nothing to judge
    # (no user prompt / no child final text). Short-circuit to a "skipped"
    # verdict rather than invoking the critic against an empty string, which
    # would emit meaningless "status=evaluated" records.
    if not isinstance(draft_text, str) or not draft_text.strip():
        return {
            "rule_id": rule_id,
            "passed": True,
            "reason": "no content to judge",
            "status": "skipped",
        }
    if model_factory is None:
        return {
            "rule_id": rule_id,
            "passed": True,
            "reason": "no critic model available",
            "status": "skipped",
        }
    try:
        from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
            evaluate_criterion,
        )

        passed, reason = await evaluate_criterion(
            criterion=criterion,
            draft_text=draft_text,
            model_factory=model_factory,
            invoke=invoke,
        )
        return {
            "rule_id": rule_id,
            "passed": bool(passed),
            "reason": reason or "",
            "status": "evaluated",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "rule_id": rule_id,
            "passed": True,
            "reason": f"audit short-circuited: {exc!r}",
            "status": "error",
        }


async def run_user_prompt_submit_audit(
    *,
    prompt_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """Audit fan-out for ``firesAt == "on_user_prompt_submit"`` llm_criterion rules.

    Called at the TOP of :func:`magi_agent.runtime.governed_turn.run_governed_turn`
    (the canonical CLI/serve/child funnel), BEFORE the engine stream starts.
    Returns the per-rule audit list (empty when the flag is OFF, when no rules
    are authored, or on any fail-open path). Never mutates ``prompt_text`` and
    never blocks turn execution.
    """
    if not lifecycle_expansion_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at="on_user_prompt_submit")
    except Exception:
        return []
    if not rules:
        return []
    audits: list[AuditRecord] = []
    for rule in rules:
        audit = await _audit_one_rule(
            rule,
            draft_text=prompt_text,
            model_factory=model_factory,
            invoke=invoke,
        )
        audits.append(audit)
    return audits


async def run_subagent_stop_audit(
    *,
    final_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """Audit fan-out for ``firesAt == "on_subagent_stop"`` llm_criterion rules.

    Called at the END of :func:`magi_agent.runtime.governed_turn.run_governed_turn`
    (the canonical funnel) WHEN the turn is a child turn (``ctx.depth > 0``).
    The child's final assistant text is collected off the event stream by the
    governed_turn wire and threaded in as ``final_text``. Returns the per-rule
    audit list (empty when the flag is OFF, when no rules are authored, when
    ``final_text`` is empty, or on any fail-open path). Never blocks emission —
    the child output is already on its way back to the parent.
    """
    if not lifecycle_expansion_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at="on_subagent_stop")
    except Exception:
        return []
    if not rules:
        return []
    audits: list[AuditRecord] = []
    for rule in rules:
        audit = await _audit_one_rule(
            rule,
            draft_text=final_text,
            model_factory=model_factory,
            invoke=invoke,
        )
        audits.append(audit)
    return audits


async def run_before_turn_start_audit(
    *,
    prompt_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE1 audit fan-out for ``firesAt == "before_turn_start"``.

    Called at the TOP of :func:`magi_agent.runtime.governed_turn.run_governed_turn`
    (the canonical CLI/serve/child funnel), BEFORE the engine stream starts
    AND BEFORE the sibling ``run_user_prompt_submit_audit`` fan-out. The
    inbound user prompt text is threaded in as ``prompt_text`` so the
    criterion judge has a draft to evaluate at top-level turn entry.
    Returns the per-rule audit list (empty when the flag is OFF, when no
    rules are authored, or on any fail-open path). Never mutates
    ``prompt_text`` and never blocks turn execution.
    """
    if not lifecycle_turn_hooks_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at="before_turn_start")
    except Exception:
        return []
    if not rules:
        return []
    audits: list[AuditRecord] = []
    for rule in rules:
        audit = await _audit_one_rule(
            rule,
            draft_text=prompt_text,
            model_factory=model_factory,
            invoke=invoke,
        )
        audits.append(audit)
    return audits


async def run_after_turn_end_audit(
    *,
    final_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE1 audit fan-out for ``firesAt == "after_turn_end"``.

    Called at the END of :func:`magi_agent.runtime.governed_turn.run_governed_turn`
    (the canonical funnel) on the top-level turn boundary — distinct from
    :func:`run_subagent_stop_audit`, which only fires for child turns
    (``ctx.depth > 0``). The top-level turn's final assistant text is
    collected off the event stream by the governed_turn wire and threaded in
    as ``final_text``. Returns the per-rule audit list (empty when the flag
    is OFF, when no rules are authored, when ``final_text`` is empty, or on
    any fail-open path). Audit-only — the top-level emission has already
    completed by the time this fan-out runs.
    """
    if not lifecycle_turn_hooks_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at="after_turn_end")
    except Exception:
        return []
    if not rules:
        return []
    audits: list[AuditRecord] = []
    for rule in rules:
        audit = await _audit_one_rule(
            rule,
            draft_text=final_text,
            model_factory=model_factory,
            invoke=invoke,
        )
        audits.append(audit)
    return audits


async def run_before_llm_call_audit(
    *,
    prompt_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
    critic_budget_remaining: int,
) -> list[AuditRecord]:
    """PR-F-LIFE2 audit fan-out for ``firesAt == "before_llm_call"``.

    Wired adjacent to the ADK ``before_model_callback`` boundary inside the
    runner stream. Fires on every LLM call within a turn but is hard-capped
    by ``critic_budget_remaining`` so a misbehaving rule cannot multiply
    critic cost without bound. The caller (the ADK plugin in
    :mod:`magi_agent.adk_bridge.lifecycle_llm_call_control`) maintains a
    per-(session, turn) counter initialised from
    ``MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET`` (default ``3``) and threads the
    remaining budget in.

    Each successful audit invocation costs ONE budget unit. When the budget
    is exhausted (``critic_budget_remaining <= 0``) the fan-out short-circuits
    to a single ``budget_exhausted`` skip record so the ledger captures the
    reason without invoking the critic. Triple-gated + fail-open via
    :func:`llm_call_hooks_enabled`.
    """
    if not llm_call_hooks_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at="before_llm_call")
    except Exception:
        return []
    if not rules:
        return []
    if critic_budget_remaining <= 0:
        # Budget exhausted: record ONE skip event so the audit ledger reflects
        # the cost-ceiling decision without invoking the critic. The caller
        # records this per-call; downstream the ledger sees a single
        # status="skipped" / reason="budget_exhausted" record.
        return [
            {
                "rule_id": None,
                "passed": True,
                "reason": "per-turn critic budget exhausted",
                "status": "budget_exhausted",
            }
        ]
    audits: list[AuditRecord] = []
    for rule in rules:
        audit = await _audit_one_rule(
            rule,
            draft_text=prompt_text,
            model_factory=model_factory,
            invoke=invoke,
        )
        audits.append(audit)
    return audits


async def run_after_llm_call_audit(
    *,
    draft_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
    critic_budget_remaining: int,
) -> list[AuditRecord]:
    """PR-F-LIFE2 audit fan-out for ``firesAt == "after_llm_call"``.

    Wired adjacent to the ADK ``after_model_callback`` boundary inside the
    runner stream. ``draft_text`` is the model's just-emitted text output
    (extracted by the caller from the ADK ``LlmResponse``). Subject to the
    same per-turn critic budget as :func:`run_before_llm_call_audit`; the
    caller decrements the shared counter so before/after combined never
    exceed the per-turn cap.

    Triple-gated + fail-open via :func:`llm_call_hooks_enabled`; returns a
    single ``budget_exhausted`` skip record when the caller's budget is
    depleted so the cost-ceiling decision is visible in the audit ledger.
    """
    if not llm_call_hooks_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at="after_llm_call")
    except Exception:
        return []
    if not rules:
        return []
    if critic_budget_remaining <= 0:
        return [
            {
                "rule_id": None,
                "passed": True,
                "reason": "per-turn critic budget exhausted",
                "status": "budget_exhausted",
            }
        ]
    audits: list[AuditRecord] = []
    for rule in rules:
        audit = await _audit_one_rule(
            rule,
            draft_text=draft_text,
            model_factory=model_factory,
            invoke=invoke,
        )
        audits.append(audit)
    return audits


__all__ = [
    "AuditRecord",
    "lifecycle_expansion_enabled",
    "lifecycle_turn_hooks_enabled",
    "llm_call_hooks_enabled",
    "run_user_prompt_submit_audit",
    "run_subagent_stop_audit",
    "run_before_turn_start_audit",
    "run_after_turn_end_audit",
    "run_before_llm_call_audit",
    "run_after_llm_call_audit",
]
