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


def lifecycle_extra_emitters_enabled(env: dict[str, str] | None = None) -> bool:
    """PR-F-LIFE3 triple-gate check for the four new emitter slots.

    Mirrors :func:`llm_call_hooks_enabled` but keys on the F-LIFE3 master
    switch ``MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED`` so the four
    new slot families (before_compaction / after_compaction /
    on_task_checkpoint / on_artifact_created) can be staged independently
    of the F-LIFE1/2 lifecycle expansions. Each emit site (compaction
    plugin / work-queue driver / file-delivery boundary) calls this helper
    FIRST so the OFF path is byte-identical (no policy load, no critic
    factory build).

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import flag_bool, flag_profile_bool  # noqa: PLC0415
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", env=env)
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", env=env)
        )
    except Exception:
        return False


def session_task_emitters_enabled(env: dict[str, str] | None = None) -> bool:
    """PR-F-LIFE4b triple-gate check for the three task / session boundary
    emitter slots.

    Mirrors :func:`lifecycle_extra_emitters_enabled` but keys on the F-LIFE4b
    master switch ``MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED``
    so the three new slot families (on_task_complete / on_session_start /
    on_session_end) can be staged independently of the F-LIFE3 four-emitter
    family. Each emit site (governed_turn finally block / ADK
    before_model first-fire detection / transport session-end hook when
    wired) calls this helper FIRST so the OFF path is byte-identical (no
    policy load, no critic factory build, no per-session OrderedDict
    bookkeeping).

    Fail-open: any import error returns ``False`` so the call site stays a
    no-op when the flag layer cannot be read.
    """
    try:
        from magi_agent.config.flags import flag_bool, flag_profile_bool  # noqa: PLC0415
    except Exception:
        return False
    try:
        return (
            flag_bool(
                "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED", env=env
            )
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


async def _run_extra_emitter_audit(
    *,
    fires_at: str,
    draft_text: str,
    model_factory: Callable[[], Any] | None,
    invoke: InvokeFn | None,
    policy_loader: Callable[[], Any] | None,
    env: dict[str, str] | None,
) -> list[AuditRecord]:
    """Shared body for the four PR-F-LIFE3 audit fan-out helpers.

    The four emitters (before_compaction / after_compaction /
    on_task_checkpoint / on_artifact_created) all share the same shape
    (gate → policy load → per-rule judge) so the body is factored here.
    The caller picks the ``fires_at`` slot and threads in the relevant
    ``draft_text`` (a bounded textual summary of the emitter's event —
    e.g. a compaction count, a task-status sentence, an artifact ref).
    Fail-open on every step: any failure returns an empty list so a
    misbehaving rule cannot wedge the surrounding runtime chokepoint.
    """
    if not lifecycle_extra_emitters_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at=fires_at)
    except Exception:
        return []
    if not rules:
        return []
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


async def run_before_compaction_audit(
    *,
    pre_compaction_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE3 audit fan-out for ``firesAt == "before_compaction"``.

    Wired immediately before
    :meth:`magi_agent.adk_bridge.context_compaction.MagiContextCompactionPlugin._apply_tail_trim`
    runs (covers both the automatic threshold-breach decision path and
    the manual ``/compact`` force path). ``pre_compaction_text`` is a
    bounded textual summary of the about-to-be-trimmed context (e.g.
    "pre-compaction: 42 contents, model=gpt-5"). Audit-only — never
    mutates ``llm_request`` and never blocks the compaction call (the
    compaction plugin's own try/except envelope additionally fails open
    if this fan-out raises).
    """
    return await _run_extra_emitter_audit(
        fires_at="before_compaction",
        draft_text=pre_compaction_text,
        model_factory=model_factory,
        invoke=invoke,
        policy_loader=policy_loader,
        env=env,
    )


async def run_after_compaction_audit(
    *,
    summary_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE3 audit fan-out for ``firesAt == "after_compaction"``.

    Wired immediately after a successful tail-drop returns from
    :meth:`magi_agent.adk_bridge.context_compaction.MagiContextCompactionPlugin._apply_tail_trim`.
    ``summary_text`` is a bounded textual summary of the post-compaction
    state (e.g. "post-compaction: dropped=30, kept=12, summary_ref=…").
    Audit-only — the compaction has already taken effect on
    ``llm_request.contents`` by the time this fan-out runs.
    """
    return await _run_extra_emitter_audit(
        fires_at="after_compaction",
        draft_text=summary_text,
        model_factory=model_factory,
        invoke=invoke,
        policy_loader=policy_loader,
        env=env,
    )


async def run_task_checkpoint_audit(
    *,
    task_id: str,
    checkpoint_kind: str,
    summary_text: str = "",
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE3 audit fan-out for ``firesAt == "on_task_checkpoint"``.

    Wired at each work-queue task status transition (claimed / completed
    / failed) inside
    :meth:`magi_agent.missions.work_queue.driver.WorkQueueDriver.run_once`.
    ``checkpoint_kind`` is one of ``"claimed"`` / ``"completed"`` /
    ``"failed"`` / ``"short_circuited"``; ``summary_text`` is the
    task's result / error / title (bounded by the caller so a giant
    result payload never reaches the critic). The fan-out composes a
    short ``draft_text`` that includes the task id + checkpoint kind +
    summary so the criterion judge has a deterministic frame.
    Audit-only — never aborts the dispatcher tick (the work-queue
    driver wraps the call in its own try/except so a fan-out raise
    never breaks dispatch).
    """
    # Compose a bounded draft text frame. Cap each field so a runaway
    # task body / result cannot blow past the critic's context window.
    frame = (
        f"task_id={task_id[:128]}\n"
        f"checkpoint={checkpoint_kind[:32]}\n"
        f"summary={(summary_text or '')[:1024]}"
    )
    return await _run_extra_emitter_audit(
        fires_at="on_task_checkpoint",
        draft_text=frame,
        model_factory=model_factory,
        invoke=invoke,
        policy_loader=policy_loader,
        env=env,
    )


async def run_artifact_created_audit(
    *,
    artifact_ref: str,
    artifact_excerpt: str = "",
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE3 audit fan-out for ``firesAt == "on_artifact_created"``.

    Wired immediately after a successful ``artifact_provider.write_artifact``
    returns ``status="ok"`` inside
    :meth:`magi_agent.artifacts.file_delivery.FileDeliveryBoundary.execute`.
    ``artifact_ref`` is the resolved artifact reference (e.g. the digest
    ref returned by the provider); ``artifact_excerpt`` is an optional
    bounded textual summary of the artifact payload (caller-bounded —
    the boundary does not slurp the full artifact bytes into the
    critic). Audit-only — the artifact has already been written by the
    time this fan-out runs.
    """
    frame = (
        f"artifact_ref={artifact_ref[:256]}\n"
        f"excerpt={(artifact_excerpt or '')[:1024]}"
    )
    return await _run_extra_emitter_audit(
        fires_at="on_artifact_created",
        draft_text=frame,
        model_factory=model_factory,
        invoke=invoke,
        policy_loader=policy_loader,
        env=env,
    )


# ---------------------------------------------------------------------------
# PR-F-LIFE4a — gate fan-out helpers
# ---------------------------------------------------------------------------
#
# The audit fan-outs above record per-rule verdicts but never act on them. The
# F-LIFE4a "gate" helpers wrap the same fan-out machinery and reduce the
# per-rule audit list to a single worst-of-N decision string so the calling
# runtime site can short-circuit (block / ask) the surrounding operation.
#
# Decision precedence (worst-wins): ``block`` > ``ask`` > ``proceed``.
# Only rules whose persisted ``action`` is NOT ``audit`` participate in the
# gate decision — pure audit-recording rules continue to flow through the
# parallel ``run_X_audit`` helpers above and never block.
#
# Fail-open invariant: any exception (import failure, policy load error,
# critic failure) returns ``"proceed"`` so a misbehaving rule cannot wedge a
# turn. The caller is expected to wrap each call in its own try/except as a
# second belt — gate verdicts MUST NOT travel via exceptions.
#
# Honest-degrade for ``ask``: until a real approval surface lands, the
# runtime treats ``ask`` as ``audit`` (proceed but record ``requires_approval
# =true`` on the audit ledger). The gate STILL returns the literal string
# ``"ask"`` so the call site can layer richer treatment (e.g. surface a
# directive in the receipt) when it knows how.

GateVerdict = str  # one of: "proceed", "block", "ask"


def _gate_decision_from_audits(
    rules: list[dict[str, Any]],
    audits: list[AuditRecord],
    *,
    allowed_actions: frozenset[str],
) -> GateVerdict:
    """Reduce per-rule audits to one worst-of-N gate verdict.

    Only rules whose persisted ``action`` is in ``allowed_actions`` participate
    (``action == "audit"`` rules are ignored — they record verdicts via the
    sibling audit fan-out and never block). A rule's verdict counts as a
    block / ask only when the audit ``status == "evaluated"`` AND
    ``passed`` is False (the criterion judge actually rejected the draft).
    Any other status (skipped / error / budget_exhausted) is fail-open and
    does NOT contribute to a block — the audit fan-out's existing fail-open
    contract carries through.
    """
    rule_by_id: dict[Any, dict[str, Any]] = {r.get("id"): r for r in rules}
    worst: GateVerdict = "proceed"
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        if audit.get("status") != "evaluated":
            continue
        if audit.get("passed", True):
            continue
        rule = rule_by_id.get(audit.get("rule_id"))
        if not isinstance(rule, dict):
            continue
        action = rule.get("action")
        if action not in allowed_actions:
            continue
        if action == "block":
            return "block"
        if action == "ask_approval" and worst == "proceed":
            worst = "ask"
    return worst


def derive_gate_verdict_from_audits(
    audits: list[AuditRecord],
    *,
    fires_at: str,
    allowed_actions: frozenset[str],
    enabled_fn: Callable[[dict[str, str] | None], bool],
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a review pass — derive a gate verdict from EXISTING audit records.

    Used by hot-path callers (e.g.
    :class:`magi_agent.adk_bridge.lifecycle_llm_call_control.LifecycleLlmCallAuditControl`)
    that already invoked the audit fan-out and want to AVOID paying a
    second criterion-judge invocation just to derive a gate verdict.
    The audit list is reduced via :func:`_gate_decision_from_audits` using
    the same allowed-action filter the matching gate helper would use.

    Fail-open semantics match :func:`_gate_via_audit`: any exception
    (flag-read failure, policy load failure, malformed record) returns
    ``"proceed"`` so a buggy rule cannot block a turn.
    """
    try:
        if not enabled_fn(env):
            return "proceed"
    except Exception:
        return "proceed"
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at=fires_at)
    except Exception:
        return "proceed"
    gating_rules = [
        r for r in rules
        if isinstance(r, dict) and r.get("action") in allowed_actions
    ]
    if not gating_rules:
        return "proceed"
    try:
        return _gate_decision_from_audits(
            gating_rules,
            audits,
            allowed_actions=allowed_actions,
        )
    except Exception:
        return "proceed"


async def _gate_via_audit(
    fan_out: Callable[..., Awaitable[list[AuditRecord]]],
    *,
    fires_at: str,
    enabled_fn: Callable[[dict[str, str] | None], bool],
    policy_loader: Callable[[], Any] | None,
    env: dict[str, str] | None,
    allowed_actions: frozenset[str],
    fan_out_kwargs: dict[str, Any],
) -> GateVerdict:
    """Shared body for the F-LIFE4a gate helpers.

    Loads the policy, fan-outs to the same ``_audit_one_rule`` machinery as
    the audit helpers above, then reduces to one verdict via
    :func:`_gate_decision_from_audits`. Fail-open everywhere: any exception
    returns ``"proceed"`` so a buggy rule cannot block a turn.
    """
    try:
        if not enabled_fn(env):
            return "proceed"
    except Exception:
        return "proceed"
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at=fires_at)
    except Exception:
        return "proceed"
    # Pre-filter to non-audit rules so the fan-out skips work entirely when no
    # gating rule exists (the audit-only siblings already covered audit rules).
    gating_rules = [
        r for r in rules
        if isinstance(r, dict) and r.get("action") in allowed_actions
    ]
    if not gating_rules:
        return "proceed"
    try:
        audits = await fan_out(**fan_out_kwargs)
    except Exception:
        return "proceed"
    try:
        return _gate_decision_from_audits(
            gating_rules,
            audits,
            allowed_actions=allowed_actions,
        )
    except Exception:
        return "proceed"


async def run_user_prompt_submit_gate(
    *,
    prompt_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a gate for ``on_user_prompt_submit``.

    Returns ``"block"`` when any enabled ``llm_criterion`` rule with
    ``action == "block"`` reports a failed (passed=False) evaluated verdict;
    ``"proceed"`` otherwise. Fail-open at every layer — never raises.
    """
    return await _gate_via_audit(
        run_user_prompt_submit_audit,
        fires_at="on_user_prompt_submit",
        enabled_fn=lifecycle_expansion_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block"}),
        fan_out_kwargs={
            "prompt_text": prompt_text,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
        },
    )


async def run_before_turn_start_gate(
    *,
    prompt_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a gate for ``before_turn_start``.

    Supports both ``block`` and ``ask_approval`` actions. The caller (the
    governed-turn entry) is expected to short-circuit the engine stream on
    ``"block"`` and route ``"ask"`` to an approval surface (in v1 the
    runtime treats ask as audit + requires_approval — see helper note).
    """
    return await _gate_via_audit(
        run_before_turn_start_audit,
        fires_at="before_turn_start",
        enabled_fn=lifecycle_turn_hooks_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block", "ask_approval"}),
        fan_out_kwargs={
            "prompt_text": prompt_text,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
        },
    )


async def run_before_llm_call_gate(
    *,
    prompt_text: str,
    critic_budget_remaining: int,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a gate for ``before_llm_call``.

    Returns ``"block"`` when any block-action criterion fails. Inherits the
    per-turn critic budget from :func:`run_before_llm_call_audit` — when the
    budget is exhausted the underlying fan-out emits a ``budget_exhausted``
    skip record and the gate returns ``"proceed"`` (fail-open: cannot block
    on an audit that never ran).
    """
    if critic_budget_remaining <= 0:
        # Mirror the audit helper's budget gate — never block on a call the
        # critic was never paid to evaluate.
        return "proceed"
    return await _gate_via_audit(
        run_before_llm_call_audit,
        fires_at="before_llm_call",
        enabled_fn=llm_call_hooks_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block"}),
        fan_out_kwargs={
            "prompt_text": prompt_text,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
            "critic_budget_remaining": critic_budget_remaining,
        },
    )


async def run_after_llm_call_gate(
    *,
    draft_text: str,
    critic_budget_remaining: int,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a gate for ``after_llm_call``.

    Block verdict signals the caller (the ADK after_model boundary) to
    suppress the just-emitted response. Same per-turn critic budget
    treatment as :func:`run_before_llm_call_gate`.
    """
    if critic_budget_remaining <= 0:
        return "proceed"
    return await _gate_via_audit(
        run_after_llm_call_audit,
        fires_at="after_llm_call",
        enabled_fn=llm_call_hooks_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block"}),
        fan_out_kwargs={
            "draft_text": draft_text,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
            "critic_budget_remaining": critic_budget_remaining,
        },
    )


async def run_before_compaction_gate(
    *,
    pre_compaction_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a gate for ``before_compaction``.

    Block verdict tells the compaction plugin to skip the tail-drop. The
    surrounding plugin's ``try/except`` envelope still absorbs unexpected
    failures so a malformed rule cannot wedge live compaction.
    """
    return await _gate_via_audit(
        run_before_compaction_audit,
        fires_at="before_compaction",
        enabled_fn=lifecycle_extra_emitters_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block"}),
        fan_out_kwargs={
            "pre_compaction_text": pre_compaction_text,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
        },
    )


async def run_on_task_checkpoint_gate(
    *,
    task_id: str,
    checkpoint_kind: str,
    summary_text: str = "",
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a gate for ``on_task_checkpoint``.

    Block / ask verdict signals the work-queue driver to halt further state
    advancement for this task. The audit fires post-transition today, so the
    "block" here is enforced as "do not propagate the result downstream"
    rather than rolling back the just-committed store transition (a true
    pre-transition gate is a separate follow-up — see the design doc).
    """
    return await _gate_via_audit(
        run_task_checkpoint_audit,
        fires_at="on_task_checkpoint",
        enabled_fn=lifecycle_extra_emitters_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block", "ask_approval"}),
        fan_out_kwargs={
            "task_id": task_id,
            "checkpoint_kind": checkpoint_kind,
            "summary_text": summary_text,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
        },
    )


async def run_on_artifact_created_gate(
    *,
    artifact_ref: str,
    artifact_excerpt: str = "",
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4a gate for ``on_artifact_created``.

    Only ``ask_approval`` is exposed (the artifact has already been written
    by the time the audit fires — a true block is impossible without moving
    the emit before ``write_artifact``). The caller augments the receipt
    with ``requires_approval=true`` on ``"ask"`` so a follow-up approval
    surface can hold delivery.
    """
    return await _gate_via_audit(
        run_artifact_created_audit,
        fires_at="on_artifact_created",
        enabled_fn=lifecycle_extra_emitters_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"ask_approval"}),
        fan_out_kwargs={
            "artifact_ref": artifact_ref,
            "artifact_excerpt": artifact_excerpt,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
        },
    )


# ---------------------------------------------------------------------------
# PR-F-LIFE4b — task / session boundary audit + gate helpers
# ---------------------------------------------------------------------------
#
# The three new slots ride on different runtime chokepoints than F-LIFE1/2/3
# (governed_turn finally block, ADK before_model first-fire detection, and
# the eventual transport session-end hook). Their fan-out shapes still match
# the F-LIFE3 ``_run_extra_emitter_audit`` pattern: triple-gate → policy
# load → per-rule criterion judge → audit record list. The gate helpers
# inherit :func:`_gate_via_audit` so the worst-of-N reducer treats them
# identically to the F-LIFE4a gates already in production.


async def _run_session_task_audit(
    *,
    fires_at: str,
    draft_text: str,
    model_factory: Callable[[], Any] | None,
    invoke: InvokeFn | None,
    policy_loader: Callable[[], Any] | None,
    env: dict[str, str] | None,
) -> list[AuditRecord]:
    """Shared body for the three PR-F-LIFE4b audit fan-out helpers.

    Triple-gated on :func:`session_task_emitters_enabled`; fail-open at
    every step. Mirror of :func:`_run_extra_emitter_audit` but keyed on
    the F-LIFE4b master flag so the new slot family stages independently
    of the F-LIFE3 four-emitter family.
    """
    if not session_task_emitters_enabled(env=env):
        return []
    try:
        loader = policy_loader or _default_policy_loader
        policy = loader()
        rules = policy.enabled_llm_criterion_rules(fires_at=fires_at)
    except Exception:
        return []
    if not rules:
        return []
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


async def run_task_complete_audit(
    *,
    final_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE4b audit fan-out for ``firesAt == "on_task_complete"``.

    Fires when the agent declares a multi-turn user task done. v1 signal
    source (see :mod:`magi_agent.runtime.governed_turn`):

    * The top-level turn's final assistant text contains an explicit
      ``<task_done>`` marker on its own line (line-anchored regex; see
      ``_TASK_DONE_MARKER_RE``). Operator must instruct the agent to emit
      this marker as a control signal (via system prompt / recipe) for
      the audit to fire.

    Honest-degrade: if no marker is detected the emitter never fires
    (the operator-authored rule stays inert — no false positives on
    every-turn-end). Triple-gated + fail-open via
    :func:`session_task_emitters_enabled`.

    Future signal extensions (work-queue root task transitions, ADK
    Terminal.completed + no follow-up heuristic) are NOT wired in v1.
    """
    return await _run_session_task_audit(
        fires_at="on_task_complete",
        draft_text=final_text,
        model_factory=model_factory,
        invoke=invoke,
        policy_loader=policy_loader,
        env=env,
    )


async def run_session_start_audit(
    *,
    prompt_text: str = "",
    session_id: str = "",
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE4b audit fan-out for ``firesAt == "on_session_start"``.

    Fires once per session on the FIRST model call (subsequent model
    calls within the same session do NOT re-fire). Wired by
    :class:`magi_agent.adk_bridge.lifecycle_session_control
    .LifecycleSessionControl` via a FIFO-bounded per-session "seen"
    OrderedDict (cap 128).

    ``prompt_text`` is the just-extracted user-role chunk from the
    inbound ADK ``LlmRequest`` (the helper composes a small frame
    including ``session_id`` so the critic sees both signals).
    Triple-gated + fail-open via :func:`session_task_emitters_enabled`.
    """
    frame = (
        f"session_id={session_id[:128]}\n"
        f"first_prompt={(prompt_text or '')[:1024]}"
    )
    return await _run_session_task_audit(
        fires_at="on_session_start",
        draft_text=frame,
        model_factory=model_factory,
        invoke=invoke,
        policy_loader=policy_loader,
        env=env,
    )


async def run_session_end_audit(
    *,
    summary_text: str = "",
    session_id: str = "",
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[AuditRecord]:
    """PR-F-LIFE4b audit fan-out for ``firesAt == "on_session_end"``.

    Fires when a session is gracefully closed or evicted (graceful CLI
    shutdown, serve session-pool eviction, app lifespan drain).
    Audit-only — the session has already ended by the time this fan-out
    runs, so a block / ask verdict would have no honest runtime target
    (the validator matrix accepts ``audit`` only).

    v1 honest-degrade: this PR does NOT yet ship a transport-side emit
    wire. The helper is exposed so an operator-authored rule round-
    trips through the validator and the policy store; once the
    transport wire lands (follow-up) the audit ledger will start
    receiving entries. Triple-gated + fail-open via
    :func:`session_task_emitters_enabled`.
    """
    frame = (
        f"session_id={session_id[:128]}\n"
        f"summary={(summary_text or '')[:1024]}"
    )
    return await _run_session_task_audit(
        fires_at="on_session_end",
        draft_text=frame,
        model_factory=model_factory,
        invoke=invoke,
        policy_loader=policy_loader,
        env=env,
    )


async def run_on_task_complete_gate(
    *,
    final_text: str,
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4b gate for ``on_task_complete``.

    Supports ``block`` and ``ask_approval``. Block verdict signals the
    caller (the governed-turn finally block) that the agent's
    claimed-done state failed the criterion — v1 records the audit
    ledger entry but does NOT roll back the already-emitted final
    turn (matches the ``on_subagent_stop`` honest-degrade pattern; a
    true pre-emit gate is a follow-up). Fail-open at every layer —
    never raises.
    """
    return await _gate_via_audit(
        run_task_complete_audit,
        fires_at="on_task_complete",
        enabled_fn=session_task_emitters_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block", "ask_approval"}),
        fan_out_kwargs={
            "final_text": final_text,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
        },
    )


async def run_on_session_start_gate(
    *,
    prompt_text: str = "",
    session_id: str = "",
    model_factory: Callable[[], Any] | None = None,
    invoke: InvokeFn | None = None,
    policy_loader: Callable[[], Any] | None = None,
    env: dict[str, str] | None = None,
) -> GateVerdict:
    """PR-F-LIFE4b gate for ``on_session_start``.

    Only ``block`` is exposed (the matrix accepts {audit, block}). Block
    verdict signals the caller (the ADK before_model first-fire
    detector inside :class:`LifecycleSessionControl`) to short-circuit
    the model call by returning a synthetic policy-blocked response —
    refuses the session. Fail-open at every layer — never raises.
    """
    return await _gate_via_audit(
        run_session_start_audit,
        fires_at="on_session_start",
        enabled_fn=session_task_emitters_enabled,
        policy_loader=policy_loader,
        env=env,
        allowed_actions=frozenset({"block"}),
        fan_out_kwargs={
            "prompt_text": prompt_text,
            "session_id": session_id,
            "model_factory": model_factory,
            "invoke": invoke,
            "policy_loader": policy_loader,
            "env": env,
        },
    )


__all__ = [
    "AuditRecord",
    "lifecycle_expansion_enabled",
    "lifecycle_turn_hooks_enabled",
    "llm_call_hooks_enabled",
    "lifecycle_extra_emitters_enabled",
    "session_task_emitters_enabled",
    "run_user_prompt_submit_audit",
    "run_subagent_stop_audit",
    "run_before_turn_start_audit",
    "run_after_turn_end_audit",
    "run_before_llm_call_audit",
    "run_after_llm_call_audit",
    "run_before_compaction_audit",
    "run_after_compaction_audit",
    "run_task_checkpoint_audit",
    "run_artifact_created_audit",
    "run_task_complete_audit",
    "run_session_start_audit",
    "run_session_end_audit",
    "run_user_prompt_submit_gate",
    "run_before_turn_start_gate",
    "run_before_llm_call_gate",
    "run_after_llm_call_gate",
    "run_before_compaction_gate",
    "run_on_task_checkpoint_gate",
    "run_on_artifact_created_gate",
    "run_on_task_complete_gate",
    "run_on_session_start_gate",
    "derive_gate_verdict_from_audits",
]
