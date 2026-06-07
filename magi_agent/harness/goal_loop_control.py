"""B3 — Continuation loop control + after-turn hook (the Ralph loop).

This module wires the decision that fires AFTER an agent turn completes and
decides whether the persistent goal loop CONTINUES (re-run the goal with a
re-injected continuation prompt) or STOPS (with a reason).  It is the loop
CONTROL layer only — it does NOT execute the next agent turn.  The runner /
loop driver consumes the returned :class:`LoopControlResult` and, when the
decision is ``continue``, re-injects ``result.continuation_prompt`` as the next
turn's USER message (mirroring how the scheduler returns a decision the driver
acts on, see ``scheduler_job_execution.execute_due_jobs``).

State machine (priority order)
------------------------------
1. **Gate OFF** (``MAGI_GOAL_LOOP_ENABLED`` unset/false): no-op ``stop`` with
   reason ``disabled``.  The judge is NEVER invoked — zero behavior change.
2. **Spend cap hit**: ``stop`` reason ``spend_capped`` regardless of goal status
   (cost safety for an autonomous loop — checked before any other branch so a
   runaway loop cannot keep spending).
3. **Goal status terminal** (satisfied/exhausted/preempted/cleared): ``stop``
   with the matching reason.
4. **New user message pending**: ``stop`` reason ``preempted`` (the user steers
   away — the loop yields to live interaction).
5. **Judge satisfied**: set status ``satisfied`` (idempotent), ``stop`` reason
   ``satisfied``.
6. **Judge NOT satisfied**:
     - if ``advance`` would exhaust (``turns_used + 1 >= max_turns``): set status
       ``exhausted``, ``stop`` reason ``exhausted``.
     - else ``advance`` the goal + ``continue`` with a ``CONTINUATION_PROMPT``.
7. **Judge parse-failure** (unparseable output OR judge raised): fail-open
   ``continue`` (advance), but the B2 parse-failure budget is threaded — on the
   Nth consecutive failure the loop ``stop``s with reason ``judge_budget``.

Counter reset
-------------
B2's ``run_judge`` does NOT auto-reset the consecutive-parse-failure counter on
a successful parse — the caller (this module) owns that.  On any SUCCESSFUL
parse (satisfied OR not-satisfied) we reset ``consecutive_parse_failures_after``
to 0.  On a parse failure we propagate B2's incremented count.

Prefix-cache invariant (cite prompt/splitter.py)
------------------------------------------------
``split_system_prompt`` documents the cache-optimised system-prompt layout: the
STATIC prefix (``rendered_identity`` = soul/identity/tools, then
``DEFERRAL_PREVENTION_BLOCK`` and ``OUTPUT_RULES_BLOCK``) precedes the
``__MAGI_PROMPT_DYNAMIC_BOUNDARY__`` marker and is byte-identical across turns so
the provider caches it.  The continuation prompt this module emits is a
**USER-role message** — it enters the per-turn message list, NOT the system
prompt and NOT the toolset.  Therefore re-injecting it leaves the cached static
prefix byte-for-byte unchanged (cache hit preserved).  ``LoopControlResult``
deliberately exposes no system-prompt or toolset mutation field so a driver
cannot accidentally invalidate the prefix via this seam.

Shadow
------
``LoopControlInput.shadow`` (honoring B2's ``run_judge`` shadow contract): when
True the decision is computed and recorded but ``observe_only`` is set so the
driver treats it as observe-only (no real re-injection).  Default OFF behavior
is governed by the env gate, not shadow.

Authority
---------
This module computes a decision and advances ``GoalState`` (B1 store).  It does
NOT execute a turn, spawn agents, send to channels, or mutate any authority
flag.  ``GoalLoopPolicy``'s ``traffic_attached`` / ``execution_attached`` remain
``Literal[False]`` (B5 owns live promotion).

Forbidden imports: google.adk, adk_bridge, urllib, socket, requests, http,
subprocess — none appear at top level (verified by test).
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.harness.goal_judge import GoalJudge, run_judge
from magi_agent.harness.goal_state import GoalState, GoalStateStore
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.tools.manifest import ToolSource


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOAL_LOOP_ENABLED_ENV_VAR = "MAGI_GOAL_LOOP_ENABLED"
"""Master gate. Default OFF — when unset/false the hook/decision is a no-op."""

#: USER-role continuation prompt template re-injected when the loop continues.
#: This is a *user* message (not a system change) — see the prefix-cache
#: invariant in the module docstring.  ``{goal}`` is the only substitution.
CONTINUATION_PROMPT_TEMPLATE = (
    "Continue working toward this goal until it is fully satisfied:\n"
    "{goal}\n\n"
    "Review what has been done so far, then take the next concrete step. "
    "If the goal is already met, say so explicitly."
)

LoopDecision = Literal["continue", "stop"]
LoopStopReason = Literal[
    "disabled",
    "spend_capped",
    "satisfied",
    "exhausted",
    "preempted",
    "cleared",
    "judge_budget",
]
LoopContinueReason = Literal["not_satisfied", "parse_failure_fail_open"]
LoopReason = Literal[
    "disabled",
    "spend_capped",
    "satisfied",
    "exhausted",
    "preempted",
    "cleared",
    "judge_budget",
    "not_satisfied",
    "parse_failure_fail_open",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    arbitrary_types_allowed=True,
)


# ---------------------------------------------------------------------------
# Spend-cap seam
# ---------------------------------------------------------------------------


@runtime_checkable
class SpendCapProbe(Protocol):
    """Minimal seam over ``magi_agent.billing.spend_guard``.

    The control layer only needs a yes/no "is the spend cap hit?" signal — it
    does NOT construct ``SpendReservationRequest``/``TenantContext`` objects
    (the driver owns full spend_guard wiring).  The real implementation queries
    a reserved/committed spend ledger and returns True once the configured cap
    is reached.  Tests inject a fake.  Keeping this a probe avoids coupling the
    loop-control state machine to billing internals (YAGNI / boundary).
    """

    def is_capped(self) -> bool:
        """Return True if the autonomous-loop spend cap has been reached."""
        ...


# ---------------------------------------------------------------------------
# Input / result models
# ---------------------------------------------------------------------------


class LoopControlInput(BaseModel):
    """Inputs to one after-turn loop-control decision.

    ``store`` + ``judge`` + ``spend_probe`` are injected seams (B1 store, B2
    judge, spend-cap probe).  ``arbitrary_types_allowed`` is required because
    these are Protocol-typed non-pydantic objects.
    """

    model_config = _MODEL_CONFIG

    store: GoalStateStore
    judge: GoalJudge
    session_id: str = Field(alias="sessionId")
    transcript_excerpt: str = Field(alias="transcriptExcerpt")
    consecutive_parse_failures: int = Field(
        default=0, alias="consecutiveParseFailures", ge=0
    )
    user_message_pending: bool = Field(default=False, alias="userMessagePending")
    spend_probe: SpendCapProbe = Field(alias="spendProbe")
    enabled: bool = Field(default=False)
    shadow: bool | None = Field(default=None)


class LoopControlResult(BaseModel):
    """Frozen decision carrying the loop control outcome.

    ``continuation_prompt`` is a USER-role string (``continuation_role`` is
    always ``"user"``) — present only when ``decision == "continue"``.  There is
    deliberately NO system-prompt or toolset field here so the prefix cache
    cannot be invalidated through this seam.
    """

    model_config = _MODEL_CONFIG

    decision: LoopDecision
    reason: LoopReason
    continuation_prompt: str | None = Field(default=None, alias="continuationPrompt")
    continuation_role: Literal["user"] = Field(default="user", alias="continuationRole")
    goal_state_after: GoalState = Field(alias="goalStateAfter")
    consecutive_parse_failures_after: int = Field(
        alias="consecutiveParseFailuresAfter", ge=0
    )
    observe_only: bool = Field(default=False, alias="observeOnly")
    evidence: EvidenceRecord | None = Field(default=None)


# ---------------------------------------------------------------------------
# Continuation prompt builder
# ---------------------------------------------------------------------------


def build_continuation_prompt(goal: str) -> str:
    """Render the USER-role continuation prompt for *goal*.

    Pure formatting — no system-prompt or toolset reference (prefix-cache safe).
    """
    return CONTINUATION_PROMPT_TEMPLATE.format(goal=goal)


# ---------------------------------------------------------------------------
# Evidence (redacted)
# ---------------------------------------------------------------------------


def _build_loop_evidence(
    *,
    goal: str,
    transcript_excerpt: str,
    decision: LoopDecision,
    reason: LoopReason,
    goal_state_after: GoalState,
    consecutive_parse_failures_after: int,
    observe_only: bool,
    now: datetime | None = None,
) -> EvidenceRecord:
    """Build a redacted EvidenceRecord for one loop decision.

    Raw goal text and raw transcript are NEVER stored — only SHA-256 digests and
    the transcript byte-length (mirrors B2 ``build_judge_evidence``).
    """
    ts = now or datetime.now(UTC)
    observed_at = int(ts.astimezone(UTC).timestamp() * 1000)
    goal_digest = "sha256:" + hashlib.sha256(goal.encode()).hexdigest()
    transcript_digest = "sha256:" + hashlib.sha256(transcript_excerpt.encode()).hexdigest()

    return EvidenceRecord(
        type="custom:GoalLoopDecision",
        status="ok",
        observedAt=observed_at,
        source=EvidenceSource(kind="verifier"),
        fields={
            "decision": decision,
            "reason": reason,
            "goalDigest": goal_digest,
            "transcriptDigest": transcript_digest,
            "transcriptLen": len(transcript_excerpt),
            "turnsUsed": goal_state_after.turns_used,
            "maxTurns": goal_state_after.max_turns,
            "statusAfter": goal_state_after.status,
            "consecutiveParseFailuresAfter": consecutive_parse_failures_after,
            "observeOnly": observe_only,
        },
    )


def _set_status(store: GoalStateStore, session_id: str, status: str) -> GoalState:
    """Persist a terminal status transition via model_copy on the stored state.

    Reads the current state, derives an updated copy with the new status, and
    writes it back through the public ``store.upsert`` seam so both InMemory
    and Sqlite backends are covered without accessing private attributes.
    """
    current = store.get_goal(session_id)
    if current is None:  # pragma: no cover - guarded by callers
        raise KeyError(session_id)
    updated = current.model_copy(update={"status": status})
    store.upsert(updated)
    return updated


# ---------------------------------------------------------------------------
# Loop-control decision (pure over injected seams)
# ---------------------------------------------------------------------------


def decide_loop_continuation(loop_input: LoopControlInput) -> LoopControlResult:
    """Decide whether the Ralph loop continues or stops after a turn.

    See module docstring for the full priority-ordered state machine.  This
    function advances the B1 ``GoalState`` store as a side effect (no-op on
    terminal states per B1 ``advance``) but executes NO agent turn.
    """
    store = loop_input.store
    session_id = loop_input.session_id
    failure_count_in = loop_input.consecutive_parse_failures

    state = store.get_goal(session_id)
    if state is None:
        raise KeyError(f"no goal set for session: {session_id!r}")

    # 1. Gate OFF — no-op, judge never called.
    if not loop_input.enabled:
        return _result(
            loop_input,
            decision="stop",
            reason="disabled",
            goal_state_after=state,
            failure_count_after=failure_count_in,
            observe_only=False,
        )

    # 2. Spend cap — cost safety first, short-circuits everything.
    if loop_input.spend_probe.is_capped():
        return _result(
            loop_input,
            decision="stop",
            reason="spend_capped",
            goal_state_after=state,
            failure_count_after=failure_count_in,
            observe_only=False,
        )

    # 3. Terminal goal status.
    if state.status != "active":
        return _result(
            loop_input,
            decision="stop",
            reason=state.status,  # type: ignore[arg-type]
            goal_state_after=state,
            failure_count_after=failure_count_in,
            observe_only=False,
        )

    # 4. New user message pending — user steers away.
    if loop_input.user_message_pending:
        return _result(
            loop_input,
            decision="stop",
            reason="preempted",
            goal_state_after=state,
            failure_count_after=failure_count_in,
            observe_only=False,
        )

    # 5/6/7. Run the B2 judge (shadow-gated). run_judge handles fail-open +
    #         budget; it does NOT reset failure_count on success — we do below.
    decision = run_judge(
        loop_input.judge,
        goal=state.goal,
        transcript_excerpt=loop_input.transcript_excerpt,
        consecutive_parse_failures=failure_count_in,
        shadow=loop_input.shadow,
    )
    observe_only = not decision.acted

    verdict = decision.verdict
    if verdict is not None:
        # Successful parse — reset the consecutive-parse-failure counter (B2 will
        # not auto-reset; the caller owns this).
        failure_count_after = 0
        if verdict.satisfied:
            updated = _set_status(store, session_id, "satisfied")
            return _result(
                loop_input,
                decision="stop",
                reason="satisfied",
                goal_state_after=updated,
                failure_count_after=failure_count_after,
                observe_only=observe_only,
            )
        # Not satisfied — advance, or exhaust if this advance hits the cap.
        if state.turns_used + 1 >= state.max_turns:
            updated = store.advance(session_id)  # B1 sets status="exhausted"
            return _result(
                loop_input,
                decision="stop",
                reason="exhausted",
                goal_state_after=updated,
                failure_count_after=failure_count_after,
                observe_only=observe_only,
            )
        updated = store.advance(session_id)
        return _result(
            loop_input,
            decision="continue",
            reason="not_satisfied",
            goal_state_after=updated,
            failure_count_after=failure_count_after,
            observe_only=observe_only,
            continuation_prompt=build_continuation_prompt(state.goal),
        )

    # Parse failure (unparseable OR judge raised). B2 threaded the budget:
    # decision.failure_count is the post-increment count; if the policy gives up
    # at the Nth failure, run_judge surfaced reason "parse_failure_budget_exhausted".
    failure_count_after = decision.failure_count
    if decision.reason == "parse_failure_budget_exhausted":
        return _result(
            loop_input,
            decision="stop",
            reason="judge_budget",
            goal_state_after=state,
            failure_count_after=failure_count_after,
            observe_only=observe_only,
        )

    # Fail-open continue: advance and re-inject the continuation prompt — unless
    # advancing would exhaust the budget (treat like exhaustion).
    if state.turns_used + 1 >= state.max_turns:
        updated = store.advance(session_id)
        return _result(
            loop_input,
            decision="stop",
            reason="exhausted",
            goal_state_after=updated,
            failure_count_after=failure_count_after,
            observe_only=observe_only,
        )
    updated = store.advance(session_id)
    return _result(
        loop_input,
        decision="continue",
        reason="parse_failure_fail_open",
        goal_state_after=updated,
        failure_count_after=failure_count_after,
        observe_only=observe_only,
        continuation_prompt=build_continuation_prompt(state.goal),
    )


def _result(
    loop_input: LoopControlInput,
    *,
    decision: LoopDecision,
    reason: LoopReason,
    goal_state_after: GoalState,
    failure_count_after: int,
    observe_only: bool,
    continuation_prompt: str | None = None,
) -> LoopControlResult:
    evidence = _build_loop_evidence(
        goal=goal_state_after.goal,
        transcript_excerpt=loop_input.transcript_excerpt,
        decision=decision,
        reason=reason,
        goal_state_after=goal_state_after,
        consecutive_parse_failures_after=failure_count_after,
        observe_only=observe_only,
    )
    return LoopControlResult(
        decision=decision,
        reason=reason,
        continuationPrompt=continuation_prompt,
        goalStateAfter=goal_state_after,
        consecutiveParseFailuresAfter=failure_count_after,
        observeOnly=observe_only,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# After-turn hook
# ---------------------------------------------------------------------------

#: Provider seam: given the HookContext for a just-finished turn, return the
#: LoopControlInput for that session's active goal loop, or None when there is
#: no active goal loop (or the gate is off).  The driver owns this provider — it
#: has the per-session store/judge/spend-probe + transcript + pending-message
#: state that the frozen HookContext does not carry.
LoopControlInputProvider = Callable[[HookContext], "LoopControlInput | None"]

#: Sink seam: the driver registers this to receive the loop decision so it can
#: act on a ``continue`` (re-inject ``continuation_prompt`` as the next turn).
LoopControlDecisionSink = Callable[["LoopControlResult"], None]


def build_after_turn_goal_loop_hook(
    *,
    input_provider: LoopControlInputProvider,
    decision_sink: LoopControlDecisionSink | None = None,
) -> tuple[HookManifest, Callable[[HookContext], HookResult]]:
    """Build the AFTER_TURN_END hook that drives the Ralph loop.

    Returns ``(manifest, handler)``.  The handler:
      - asks ``input_provider`` for this session's LoopControlInput (None → no
        active goal loop → ``continue`` no-op),
      - computes the decision via ``decide_loop_continuation``,
      - records it via ``decision_sink`` (the driver re-injects the
        continuation prompt — the hook bus itself carries no such payload, so
        the loop is driven through the sink, mirroring the scheduler returning a
        decision the driver consumes),
      - ALWAYS returns ``HookResult(action="continue")`` — this hook never blocks
        or mutates the turn; it only emits a continuation decision.

    The hook is non-blocking and fail-open: any provider/decision error is
    swallowed so a goal-loop bug can never break a normal turn.
    """

    def _handler(context: HookContext) -> HookResult:
        try:
            loop_input = input_provider(context)
        except Exception:  # noqa: BLE001 — fail-open: never break a normal turn
            return HookResult(action="continue", reason="goal_loop_provider_error")
        if loop_input is None:
            return HookResult(action="continue", reason="no_active_goal_loop")
        try:
            result = decide_loop_continuation(loop_input)
        except Exception:  # noqa: BLE001 — fail-open
            return HookResult(action="continue", reason="goal_loop_decision_error")
        if decision_sink is not None:
            try:
                decision_sink(result)
            except Exception:  # noqa: BLE001 — sink failure must not break the turn
                logging.getLogger(__name__).warning(
                    "goal_loop: decision_sink raised", exc_info=True
                )
        return HookResult(
            action="continue",
            reason=f"goal_loop:{result.decision}:{result.reason}",
            metadata={
                "decision": result.decision,
                "loopReason": result.reason,
                "observeOnly": result.observe_only,
            },
        )

    manifest = HookManifest(
        name="goal-loop.after-turn",
        point=HookPoint.AFTER_TURN_END,
        description=(
            "Persistent goal loop (Ralph loop) controller: after a turn, decides "
            "whether to continue (re-inject continuation prompt) or stop. "
            "Default OFF via MAGI_GOAL_LOOP_ENABLED; non-blocking + fail-open."
        ),
        source=ToolSource(kind="builtin", package="magi_agent.harness.goal_loop_control"),
        priority=200,
        blocking=False,
        failOpen=True,
        enabled=False,  # default OFF — gated by env at the driver
    )
    return manifest, _handler


__all__ = [
    "CONTINUATION_PROMPT_TEMPLATE",
    "GOAL_LOOP_ENABLED_ENV_VAR",
    "LoopControlDecisionSink",
    "LoopControlInput",
    "LoopControlInputProvider",
    "LoopControlResult",
    "LoopContinueReason",
    "LoopDecision",
    "LoopReason",
    "LoopStopReason",
    "SpendCapProbe",
    "build_after_turn_goal_loop_hook",
    "build_continuation_prompt",
    "decide_loop_continuation",
]
