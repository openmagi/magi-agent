"""Custom verification-rule schema + validation (spec §9.1).

A custom rule (``verification.custom_rules[]`` item):
    {id, scope, enabled, what:{kind, payload}, firesAt, action, projection}

``validate_custom_rule`` returns a list of human-readable errors (empty = valid).
The PUT verb rejects with 400 on any error (no silent drop). This is the full
contract for all three kinds; P1 only *compiles* ``deterministic_ref`` rules into
the gate — ``tool_perm`` (P2) and ``llm_criterion`` (P3/P4) persist but stay inert
until their phase wires them.
"""

from __future__ import annotations

import re
from typing import Any

from magi_agent.customize.what_menu import allowed_actions_for, is_known_ref

CRITERION_MAX = 2000

# Cap on the number of evidence types/refs an ``llm_criterion`` may declare it
# reads (see the evidence-grounded-judge seam). Keeps the projected critic
# prompt bounded and the "this judge reads: X, Y" UI summary short.
EVIDENCE_REFS_MAX = 12

# A custom rule's ``id`` (when the client supplies one) must be a reference-safe
# token: no ``:`` (the ``custom_rule:<id>`` prefix separator used by mode
# scoped_policy_ids and the dashboard's unified policy index), no whitespace,
# bounded length. The transport backfills ``cr_<uuid.hex>`` when absent, which
# conforms. Validating a supplied id keeps ``custom_rule:<id>`` referencing
# unambiguous. The length bound matches the guided-wizard Policy-ID contract
# (frontend ``^[a-z0-9][a-z0-9_-]{0,127}$``) so a UI-authored id never round-trips
# to a 400; the char class is a touch wider (allows uppercase) to accept any
# pre-existing ids.
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

# Fail-closed actions a session-evidence gate takes when the required evidence
# is unavailable (could not read) OR absent. Default is ``deny`` (a security
# gate must not silently open when it cannot verify); ``allow`` is an explicit
# opt-out for a non-security use.
_ON_EVIDENCE_UNAVAILABLE = frozenset({"deny", "ask", "allow"})


def _validate_require_evidence(require: object) -> list[str]:
    """Validate a tool_perm ``requireEvidence`` block (the session unlock gate).

    ``{evidenceType: "custom:...", producerRuleId: "<rule>", scope?: "session",
       onEvidenceUnavailable?: "deny"|"ask"|"allow"}``. The evidence type must be
    an operator-named ``custom:`` type (never a runtime-reserved builtin, so a
    producer cannot mint a trusted-typed unlock key); ``producerRuleId`` binds
    the gate to a SPECIFIC producer by identity (the unlock join is by producer,
    not type name)."""
    errors: list[str] = []
    if not isinstance(require, dict):
        return ["tool_perm.payload.requireEvidence must be an object"]
    etype = require.get("evidenceType")
    if not (isinstance(etype, str) and etype.startswith("custom:") and etype.strip()):
        errors.append(
            "requireEvidence.evidenceType must be an operator-named custom: type"
        )
    producer = require.get("producerRuleId")
    if not (isinstance(producer, str) and _RULE_ID_RE.fullmatch(producer)):
        errors.append(
            "requireEvidence.producerRuleId must be a reference-safe rule id (no ':')"
        )
    scope = require.get("scope")
    if scope is not None and scope != "session":
        errors.append("requireEvidence.scope must be 'session' (v1)")
    on_unavailable = require.get("onEvidenceUnavailable")
    if on_unavailable is not None and on_unavailable not in _ON_EVIDENCE_UNAVAILABLE:
        errors.append(
            f"requireEvidence.onEvidenceUnavailable must be one of "
            f"{sorted(_ON_EVIDENCE_UNAVAILABLE)}"
        )
    return errors


SCOPES = frozenset({"always", "coding", "research", "delivery", "memory", "task"})
KINDS = frozenset(
    {
        "deterministic_ref",
        "tool_perm",
        "llm_criterion",
        "shacl_constraint",
        "capability_scope",
        # F-MUT1: prompt_injection — mutator that either rewrites a tool call's
        # arguments before dispatch (firesAt=before_tool_use) or appends a
        # section to the assembled system prompt (firesAt=on_user_prompt_submit).
        # v1 is append-only; replace mode is deferred to v2 with an admin-tier
        # flag. See magi_agent/customize/prompt_injection.py for the validator
        # + apply helpers.
        "prompt_injection",
        # F-MUT2: output_rewrite — mutator that rewrites a tool's output text
        # AFTER dispatch but BEFORE the model reads it. v1 ships a single
        # ``redact`` mode (re.sub-based pattern → replacement); summarize/replace
        # are deferred to v2 with an admin-tier flag. See
        # magi_agent/customize/output_rewrite.py for the validator + apply
        # helpers, and magi_agent/cli/customize_tool_wiring.py (engine
        # _attach_customize_rules) for the live agent-level ADK
        # after_tool_callback wire, which magi_agent.facades.execute_tool_with_hooks
        # also composes (parallel to the F-MUT1 BEFORE_TOOL_USE consumer).
        "output_rewrite",
        # F-EXEC1: shell_command — operator-authored subprocess action. The
        # rule payload conforms to
        # :class:`magi_agent.customize.shell_runner.ShellPayload` (inline /
        # file source, timeout, env_vars whitelist, shell selector). The live
        # agent-level ADK bridge magi_agent/cli/customize_tool_wiring.py (which
        # execute_tool_with_hooks also composes) fires at ``before_tool_use``
        # (action=block honored
        # when the script exits non-zero) and ``after_tool_use`` (audit-only
        # — the dispatch already happened). Lifecycle_audit fan-outs cover
        # 9 additional audit-only slots (pre_final + 8 Tier 2 emitters);
        # per-turn cost is bounded by a shared per-(session, turn) budget
        # enforced by the LifecycleShellCommandControl ADK plugin. Trust
        # class: Operator-defined (F-EXEC3 ships the visual badge).
        "shell_command",
        # F-EXEC2: shell_check — operator-authored subprocess verifier. The
        # rule payload conforms to the SAME
        # :class:`magi_agent.customize.shell_runner.ShellPayload` as
        # shell_command, but the runtime treats the result as a verdict
        # source rather than an action: stdout is parsed as
        # ``{passed: bool, reason?: str}`` JSON, with exit code 0 → passed
        # as a deterministic fallback when stdout is not JSON. v1 wires
        # two gate slots (``pre_final`` and ``before_tool_use``); other
        # lifecycle slots are accepted by the validator but inert today
        # (audit-only fan-out helpers fire alongside the llm_criterion
        # audit but record no verdict gating). Per-turn cost is bounded
        # by the SAME ``MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET`` counter
        # shared with F-EXEC1 (single ceiling across both kinds). Trust
        # class: Operator-defined (same as shell_command).
        "shell_check",
    }
)
ACTIONS = frozenset({"block", "retry", "ask_approval", "audit", "override"})
# ``spawn`` is the lifecycle slot for capability_scope rules — when the runtime
# derives the toolset for a spawned child agent (F4). The action is always
# ``block`` (semantically: "apply the cap" — the rule subtracts/caps and the
# spawn proceeds with the narrowed toolset).
#
# PR-F-UX1: Tier 2 lifecycle expansion. Two new audit-only slots ride on top of
# bus events that already fire in the runtime but did not previously have a
# custom_rule path:
# * ``on_user_prompt_submit`` — wired in ``runtime/message_builder._apply_prompt_transform``
#   (BEFORE_SYSTEM_PROMPT hook). Audit-only (action=audit): records the inbound
#   prompt against the rule without mutating the assembled system prompt; a
#   block-action equivalent would touch the message builder's byte-identical
#   contract and is therefore deferred.
# * ``on_subagent_stop`` — wired adjacent to the AFTER_TURN_END callback in the
#   child runner (governed turn end). Audit-only: the turn has already been
#   emitted, so a block would be a false promise; the rule records a verdict
#   for post-turn review.
FIRES_AT = frozenset(
    {
        "pre_final",
        "before_tool_use",
        "after_tool_use",
        "spawn",
        # PR-F-UX1 Tier 2 — bus-emitted gates with new custom_rule paths.
        "on_user_prompt_submit",
        "on_subagent_stop",
        # PR-F-LIFE1 Tier 2 — top-level turn-boundary gates. Wired in
        # ``runtime/governed_turn.run_governed_turn`` (TOP for
        # before_turn_start; ``finally`` block for after_turn_end). Both are
        # audit-only by default for llm_criterion / deterministic_ref; the
        # ``on_subagent_stop`` slot additionally accepts block / ask actions
        # so an operator can author a "subagent must produce a summary"-style
        # rule whose verdict the parent caller can act on.
        "before_turn_start",
        "after_turn_end",
        # PR-F-LIFE2 Tier 2 — per-LLM-call gates. Wired adjacent to the ADK
        # before_model_callback / after_model_callback boundaries inside the
        # runner stream (see
        # ``magi_agent/adk_bridge/lifecycle_llm_call_control.py``). Audit-only
        # in v1 because every emit fires on the hot per-call path; the
        # surrounding plugin maintains a per-(session, turn) critic budget
        # (env ``MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET``, default 3) that hard-
        # caps fan-out cost. Honest-degrade: deterministic_ref / mutator
        # kinds are NOT exposed here in v1 (no runtime fan-out).
        "before_llm_call",
        "after_llm_call",
        # PR-F-LIFE3 Tier 2 — four NEW emitter slots that ride on existing
        # runtime chokepoints but did not previously have a custom_rule path.
        # Audit-only by default (same honest-degrade rationale as F-LIFE1/2):
        # the runtime fan-out only consumes ``llm_criterion`` rules and
        # records verdicts; deterministic_ref / tool_perm / mutator kinds are
        # NOT exposed here (no runtime fan-out → exposing them would let the
        # validator accept an inert rule). All four sites are gated by the
        # ``MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED`` master switch +
        # triple-gate so OFF callers stay byte-identical.
        # * ``before_compaction`` — fires immediately before
        #   ``MagiContextCompactionPlugin._apply_tail_trim`` runs (covers
        #   BOTH the automatic threshold/real-token decision path AND the
        #   manual /compact force path). Use to inspect the pre-compaction
        #   context window size / count.
        # * ``after_compaction`` — fires immediately after a successful
        #   tail-drop returns from ``_apply_tail_trim``. Use to inspect the
        #   summary head / dropped-event count.
        # * ``on_task_checkpoint`` — fires at each work-queue task status
        #   transition (claimed / completed / failed) inside
        #   ``WorkQueueDriver.run_once``. Use to inspect terminal-state
        #   summaries / errors.
        # * ``on_artifact_created`` — fires after a successful
        #   ``artifact_provider.write_artifact`` inside
        #   ``FileDeliveryBoundary.execute`` (ok-status branch only). Use
        #   to inspect newly-written artifact refs / content digests.
        "before_compaction",
        "after_compaction",
        "on_task_checkpoint",
        "on_artifact_created",
        # PR-F-LIFE4b — task / session boundary slots. All three accept
        # ``llm_criterion`` only (audit-fan-out shape inherited from F-LIFE3
        # — there is no honest deterministic_ref / tool_perm / mutator
        # consumer at these chokepoints in v1). Triple-gated by the new
        # ``MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED`` master
        # switch so OFF callers stay byte-identical.
        # * ``on_task_complete`` — fires when the agent declares a
        #   multi-turn user task done. v1 signal: top-level governed turn
        #   finishes with a ``<task_done>`` marker on its own line in the
        #   final assistant text. Operator must instruct the agent to emit
        #   the marker as a control signal (via system prompt / recipe).
        #   Honest-degrade: if no marker is detected the emitter never
        #   fires (the operator-authored rule stays inert).
        # * ``on_session_start`` — fires once per session on the FIRST
        #   model call (subsequent calls within the same session do not
        #   re-fire). Wired by :class:`magi_agent.adk_bridge
        #   .lifecycle_session_control.LifecycleSessionControl` via a
        #   FIFO-bounded per-session "seen" OrderedDict (mirror of the
        #   F-LIFE2 per-turn budget bookkeeping shape).
        # * ``on_session_end`` — fires when a session is explicitly closed
        #   or its transport hosts the wire (graceful CLI shutdown, serve
        #   session-pool eviction). v1 honest-degrade: no transport-side
        #   emit wire ships in this PR — the wizard exposes the slot so
        #   operators can author rules ahead of the wire, and the audit
        #   ledger stays silent until a follow-up adds the emit.
        "on_task_complete",
        "on_session_start",
        "on_session_end",
    }
)

# Allowed least-privilege projection slices (spec §9.1). ``conversation`` (full
# session.events) is intentionally NOT allowed.
_PROJECTION_BASE = frozenset({"result", "args", "scope"})


def _projection_slice_ok(slice_: str) -> bool:
    return slice_ in _PROJECTION_BASE or slice_.startswith("evidence:")


def _validate_content_match(content_match: Any, fires_at: Any) -> list[str]:
    """Validate a P4 after-tool ``contentMatch`` pre-filter payload."""
    errs: list[str] = []
    if fires_at != "after_tool_use":
        errs.append("contentMatch is only valid for after_tool_use rules")
    if not isinstance(content_match, dict):
        return [*errs, "contentMatch must be an object"]
    pattern = content_match.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        errs.append("contentMatch.pattern is required")
    for key in ("isRegex", "negate"):
        if key in content_match and not isinstance(content_match[key], bool):
            errs.append(f"contentMatch.{key} must be a boolean")
    if content_match.get("isRegex") and isinstance(pattern, str) and pattern.strip():
        try:
            re.compile(pattern)
        except re.error:
            errs.append("contentMatch.pattern is not a valid regex")
    return errs


# Legal (kind -> firesAt -> allowed actions) matrix (spec §9.1 table).
_LEGAL: dict[str, dict[str, frozenset[str]]] = {
    "deterministic_ref": {
        "pre_final": frozenset({"block", "retry", "audit"}),
        # PR-F-LIFE1 NOTE: before_turn_start / after_turn_end have a fan-out
        # only for `llm_criterion` (see lifecycle_audit.run_before_turn_start_audit
        # and run_after_turn_end_audit). Authoring `deterministic_ref` at those
        # slots would be inert — the validator would accept the rule but the
        # runtime has no consumer. Honest-degrade: omit until a runtime
        # fan-out lands.
    },
    "tool_perm": {"before_tool_use": frozenset({"block", "ask_approval"})},
    "llm_criterion": {
        "pre_final": frozenset({"block", "retry", "audit"}),
        # WS-B — ``after_tool_use`` adds ``audit`` alongside ``override``. The
        # CustomizeAfterToolControl after-tool ingestion gate consumes both: an
        # ``override`` rule strips the tool result (block), while an ``audit``
        # rule leaves the result untouched and instead emits a typed
        # ``custom:CustomizeAudit`` EvidenceRecord to the durable ledger (the
        # "trace" leg of rule/policy/trace). ``block`` stays illegal — the gate
        # cannot retroactively block an already-dispatched tool result.
        "after_tool_use": frozenset({"override", "audit"}),
        # PR-F-UX1 Tier 2 — audit-only at the two new bus-emitted gates.
        # ``block`` would require a runtime contract change at message_builder
        # (would mutate the byte-identical prompt assembly invariant) and
        # post-turn-end (turn already emitted), so the conservative wire is
        # audit-only: the criterion judge is invoked and the verdict recorded,
        # the surrounding runtime contract is unchanged.
        "on_user_prompt_submit": frozenset({"audit"}),
        # PR-F-LIFE1 — ``on_subagent_stop`` validator accepts
        # ``block`` / ``ask_approval`` IN ADDITION to ``audit`` so an operator
        # can author a "subagent must produce a summary"-style rule. The audit
        # fan-out still runs and the verdict is recorded.
        # TODO(F-LIFE1 follow-up): the parent-surfacing wire (turn the verdict
        # into a directive consumed by the SpawnAgent parent caller) is NOT
        # built in this PR. Today the verdict is captured by the audit ledger
        # but the parent does not act on a block/ask_approval action. This is
        # authorability-lift-only; runtime surfacing arrives in a follow-up.
        # ``audit`` stays the conservative honest action.
        "on_subagent_stop": frozenset({"audit", "block", "ask_approval"}),
        # PR-F-LIFE1 — audit-only at the new turn-boundary slots. See the
        # deterministic_ref note above; the rationale matches.
        "before_turn_start": frozenset({"audit"}),
        "after_turn_end": frozenset({"audit"}),
        # PR-F-LIFE2 — audit-only at the new per-LLM-call slots. The
        # surrounding ADK plugin caps fan-out at
        # ``MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET`` invocations per turn
        # (default 3) to prevent runaway critic cost — block / retry are
        # deferred until a stricter cost-ceiling story lands. As with the
        # turn-boundary slots, deterministic_ref / mutator kinds are NOT
        # added here (honest-degrade — no runtime fan-out).
        "before_llm_call": frozenset({"audit"}),
        "after_llm_call": frozenset({"audit"}),
        # PR-F-LIFE3 — audit-only at the four new emitter slots. The
        # surrounding runtime sites (context_compaction plugin / work-queue
        # driver / file-delivery boundary) call the lifecycle_audit fan-out
        # helpers behind a try/except envelope so an audit failure cannot
        # break the live compaction / task dispatch / artifact write. As
        # with the turn-boundary and per-LLM-call slots,
        # deterministic_ref / tool_perm / mutator kinds are NOT added
        # here (honest-degrade — no runtime fan-out at these chokepoints).
        "before_compaction": frozenset({"audit"}),
        "after_compaction": frozenset({"audit"}),
        "on_task_checkpoint": frozenset({"audit", "block", "ask_approval"}),
        "on_artifact_created": frozenset({"audit", "ask_approval"}),
        # PR-F-LIFE4b — task / session boundary slots. Action sets per
        # honest runtime contract at each chokepoint:
        # * ``on_task_complete``: {audit, block, ask_approval}. Block
        #   semantically means "the agent claimed task done but the
        #   criterion judges otherwise" — the gate caller (governed_turn
        #   finally block) records the audit ledger entry; in v1 the
        #   already-emitted final turn is not rolled back (honest-degrade,
        #   matches the on_subagent_stop pattern). ``ask_approval``
        #   surfaces a ``requires_approval=true`` directive for follow-up
        #   review surfaces.
        # * ``on_session_start``: {audit, block}. Block at the first
        #   model call refuses the session (the
        #   :class:`LifecycleSessionControl` short-circuits the model
        #   call via the ADK before_model boundary by returning a
        #   synthetic policy-blocked response).
        # * ``on_session_end``: {audit} only. The session has already
        #   ended by the time the emit fires, so block / ask have no
        #   honest runtime target (mirrors ``after_turn_end`` /
        #   ``after_compaction``).
        "on_task_complete": frozenset({"audit", "block", "ask_approval"}),
        "on_session_start": frozenset({"audit", "block"}),
        "on_session_end": frozenset({"audit"}),
    },
    # audit/retry deferred: runtime always blocks on a failed shacl record regardless
    # of the stored action, so promising audit/retry here is a false contract.
    "shacl_constraint": {"pre_final": frozenset({"block"})},
    # F4: capability_scope fires at the spawn lifecycle slot. ``block`` is the
    # only semantically meaningful action — the rule subtracts denied tools and
    # caps the permission class; audit/retry have no spawn-time analogue.
    "capability_scope": {"spawn": frozenset({"block"})},
    # F-MUT1: prompt_injection is a mutator (rewrites/augments inbound data).
    # The persisted action is ``audit`` at both slots — the runtime applier
    # records the mutation as an audit event (no separate block/retry verdict
    # because the mutation already happened). Distinct from llm_criterion's
    # audit-at-Tier-2 slots: there the action labels a verdict; here it labels
    # the mutator's "wrote the mutation event to the audit ledger".
    "prompt_injection": {
        "before_tool_use": frozenset({"audit"}),
        "on_user_prompt_submit": frozenset({"audit"}),
    },
    # F-MUT2: output_rewrite is a mutator (rewrites tool output text). The
    # persisted action is ``audit`` — the runtime applier records the
    # mutation as an audit event (no separate block/retry verdict because
    # the mutation already happened by the time the audit record is
    # written). Only legal at after_tool_use (the rewrite has no honest
    # target at other lifecycle slots: no tool result text exists yet at
    # before_tool_use; pre_final already passed by the time the final
    # answer commits).
    "output_rewrite": {
        "after_tool_use": frozenset({"audit"}),
    },
    # F-EXEC1: shell_command — operator-authored subprocess. v1 ships 11
    # lifecycle slots; ``block`` is exposed at the two slots with an honest
    # pre-execution gate target (``before_tool_use`` runs before dispatch;
    # ``pre_final`` runs before the final answer commits). All other slots
    # are audit-only because by the time the runtime sees them the action
    # the rule could "block" has already occurred (after_tool_use →
    # dispatcher already returned; on_user_prompt_submit → prompt already
    # assembled by the time the audit fires; on_subagent_stop / after-turn
    # / after-compaction / on_task_checkpoint / on_artifact_created →
    # event already emitted). v1 explicitly excludes before_llm_call /
    # after_llm_call (per-LLM-call hot path, even with a budget cap
    # operator-shell on every model call is too costly) and ``spawn``
    # (capability_scope owns the spawn-time gate).
    "shell_command": {
        "pre_final": frozenset({"block", "audit"}),
        "before_tool_use": frozenset({"block", "audit"}),
        "after_tool_use": frozenset({"audit"}),
        "on_user_prompt_submit": frozenset({"audit"}),
        "on_subagent_stop": frozenset({"audit"}),
        "before_turn_start": frozenset({"audit"}),
        "after_turn_end": frozenset({"audit"}),
        "before_compaction": frozenset({"audit"}),
        "after_compaction": frozenset({"audit"}),
        "on_task_checkpoint": frozenset({"audit"}),
        "on_artifact_created": frozenset({"audit"}),
    },
    # F-EXEC2: shell_check — operator-authored subprocess VERIFIER. Same
    # action semantics as llm_criterion: the rule emits a verdict
    # (passed/failed + reason) and the surrounding runtime treats that as
    # a deterministic-shaped condition source. The two primary v1 wire
    # slots are ``pre_final`` (block honored — non-zero exit / passed=false
    # short-circuits final-answer commit) and ``before_tool_use`` (block
    # honored — short-circuits dispatch). Other lifecycle slots are
    # accepted by the validator (mirroring llm_criterion's matrix) for
    # forward-compat authoring, but in v1 the runtime fan-out helpers fire
    # them audit-only — the verifier records the per-rule verdict alongside
    # the llm_criterion audit but does not gate the surrounding chokepoint.
    "shell_check": {
        "pre_final": frozenset({"block", "audit"}),
        "before_tool_use": frozenset({"block", "audit"}),
        "after_tool_use": frozenset({"audit"}),
        "on_user_prompt_submit": frozenset({"audit", "block"}),
        "on_subagent_stop": frozenset({"audit", "block", "ask_approval"}),
        "before_turn_start": frozenset({"audit", "block", "ask_approval"}),
        "after_turn_end": frozenset({"audit"}),
        "before_compaction": frozenset({"audit", "block"}),
        "after_compaction": frozenset({"audit"}),
        "on_task_checkpoint": frozenset({"audit", "block", "ask_approval"}),
        "on_artifact_created": frozenset({"audit", "ask_approval"}),
    },
}


def validate_custom_rule(rule: Any) -> list[str]:
    """Return a list of validation errors for a custom rule (empty = valid)."""
    errors: list[str] = []
    if not isinstance(rule, dict):
        return ["rule must be an object"]

    # Optional ``id``: absent is valid (the transport backfills ``cr_<uuid>``).
    # When present it must be a reference-safe token so ``custom_rule:<id>``
    # (mode scoped_policy_ids + the dashboard unified index) parses unambiguously.
    if "id" in rule:
        rid = rule.get("id")
        if not isinstance(rid, str) or _RULE_ID_RE.fullmatch(rid) is None:
            errors.append(
                "id must be a token matching [A-Za-z0-9][A-Za-z0-9_-]{0,127} "
                "(no ':' or whitespace) when provided"
            )

    # PR-P5.0: scope is now OPTIONAL. The auto turn-scope axis
    # (coding/research/delivery) is retired: rules apply globally, or only while
    # a user-selected mode scopes them in. An absent/None scope means "always"
    # (global). A present value must still be a known scope for back-compat with
    # rules authored before the wizard stopped sending it.
    scope = rule.get("scope")
    if scope is not None and scope not in SCOPES:
        errors.append(f"scope must be one of {sorted(SCOPES)}")

    # PR-F-UX6: optional ``groupId`` (non-empty string when present). Rules
    # sharing a groupId are surfaced in the dashboard as one logical policy
    # (hybrid composition: e.g. regex pre-filter + LLM critic). Backend gates
    # still evaluate each rule independently — no new runtime gate. groupId is
    # orthogonal to scope/firesAt/kind/action and does not enter the ``_LEGAL``
    # matrix. Absent groupId is valid (ungrouped rule).
    gid = rule.get("groupId")
    if gid is not None and (not isinstance(gid, str) or not gid.strip()):
        errors.append("groupId must be a non-empty string if provided")

    what = rule.get("what")
    if not isinstance(what, dict):
        return [*errors, "what must be an object with kind+payload"]
    kind = what.get("kind")
    payload = what.get("payload")
    if kind not in KINDS:
        return [*errors, f"kind must be one of {sorted(KINDS)}"]
    if not isinstance(payload, dict):
        errors.append("what.payload must be an object")
        payload = {}

    fires_at = rule.get("firesAt")
    action = rule.get("action")
    if fires_at not in FIRES_AT:
        errors.append(f"firesAt must be one of {sorted(FIRES_AT)}")
    if action not in ACTIONS:
        errors.append(f"action must be one of {sorted(ACTIONS)}")

    # (c) legal (kind × firesAt × action) matrix
    legal_for_kind = _LEGAL.get(kind, {})
    if fires_at not in legal_for_kind:
        errors.append(f"kind {kind!r} cannot fire at {fires_at!r}")
    elif action not in legal_for_kind[fires_at]:
        errors.append(
            f"kind {kind!r} at {fires_at!r} allows actions "
            f"{sorted(legal_for_kind[fires_at])}, not {action!r}"
        )

    # (b/d/e/g) kind-specific payload
    if kind == "deterministic_ref":
        ref = payload.get("ref")
        if not isinstance(ref, str) or not is_known_ref(ref):
            errors.append("deterministic_ref.payload.ref must be a known WHAT-menu ref")
        elif isinstance(action, str) and action not in allowed_actions_for(ref):
            errors.append(f"action {action!r} not allowed for ref {ref!r}")
    elif kind == "tool_perm":
        match = payload.get("match")
        if not isinstance(match, dict) or not (
            {"tool", "domain", "domainAllowlist", "path", "pathAllowlist"} & set(match)
        ):
            errors.append(
                "tool_perm.payload.match must specify tool, domain, domainAllowlist, "
                "path, or pathAllowlist"
            )
        elif "domainAllowlist" in match and (
            not isinstance(match["domainAllowlist"], list)
            or not match["domainAllowlist"]
            or not all(isinstance(d, str) for d in match["domainAllowlist"])
        ):
            errors.append(
                "tool_perm.payload.match.domainAllowlist must be a non-empty string list"
            )
        elif "path" in match and not (
            isinstance(match["path"], str) and match["path"].strip()
        ):
            errors.append("tool_perm.payload.match.path must be a non-empty string")
        elif "pathAllowlist" in match and (
            not isinstance(match["pathAllowlist"], list)
            or not match["pathAllowlist"]
            or not all(
                isinstance(p, str) and p.strip() for p in match["pathAllowlist"]
            )
        ):
            errors.append(
                "tool_perm.payload.match.pathAllowlist must be a non-empty string list"
            )
        if payload.get("decision") not in {"deny", "ask"}:
            errors.append("tool_perm.payload.decision must be 'deny' or 'ask'")
        # Optional session-evidence gate (the policy-abstraction unlock gate):
        # the rule's decision applies UNLESS a bound producer recorded the named
        # evidence this session. Absent keeps the historic unconditional gate
        # (fully back-compat).
        require = payload.get("requireEvidence")
        if require is not None:
            errors.extend(_validate_require_evidence(require))
    elif kind == "llm_criterion":
        criterion = payload.get("criterion")
        has_criterion = isinstance(criterion, str) and bool(criterion.strip())
        if has_criterion and len(criterion) > CRITERION_MAX:
            errors.append(f"criterion exceeds the {CRITERION_MAX}-char cap")

        # Optional evidence-grounding: the criterion declares which evidence
        # types/refs the judge should read (evidence-grounded-judge seam). Absent
        # keeps the judge evidence-blind (fully back-compat).
        evidence_refs = payload.get("evidenceRefs")
        if evidence_refs is not None:
            if not isinstance(evidence_refs, list) or not all(
                isinstance(r, str) and r.strip() for r in evidence_refs
            ):
                errors.append(
                    "llm_criterion.payload.evidenceRefs must be a list of non-empty strings"
                )
            elif len(evidence_refs) > EVIDENCE_REFS_MAX:
                errors.append(
                    f"llm_criterion.payload.evidenceRefs exceeds the {EVIDENCE_REFS_MAX}-ref cap"
                )

        # P4: an after-tool rule may carry a deterministic contentMatch pre-filter
        # (substring/regex on the tool result). With contentMatch present the
        # criterion is optional — contentMatch alone is a cheap, model-free
        # ingestion gate; with both, contentMatch gates the (costly) LLM call.
        content_match = payload.get("contentMatch")
        has_content = False
        if content_match is not None:
            errors.extend(_validate_content_match(content_match, fires_at))
            has_content = (
                isinstance(content_match, dict)
                and isinstance(content_match.get("pattern"), str)
                and bool(content_match["pattern"].strip())
            )

        if fires_at == "after_tool_use":
            tool_match = payload.get("toolMatch")
            if not isinstance(tool_match, list) or not tool_match:
                errors.append("after_tool_use llm_criterion requires a non-empty toolMatch")
            if not has_criterion and not has_content:
                errors.append(
                    "after_tool_use llm_criterion requires a criterion or a contentMatch pre-filter"
                )
        elif not has_criterion:
            errors.append("llm_criterion.payload.criterion is required")
    elif kind == "shacl_constraint":
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl  # noqa: PLC0415

        shape_ttl = payload.get("shapeTtl")
        if not isinstance(shape_ttl, str) or not shape_ttl.strip():
            errors.append("shacl_constraint.payload.shapeTtl is required (non-empty string)")
        else:
            errors.extend(validate_shape_ttl(shape_ttl))
        # optional ruleId — if absent, the rule's top-level id is used (no error)
        rule_id = payload.get("ruleId")
        if rule_id is not None and not isinstance(rule_id, str):
            errors.append("shacl_constraint.payload.ruleId must be a string if provided")
    elif kind == "capability_scope":
        # F4: operator-authored spawn-time toolset cap. Lazy import keeps the
        # capability_scope module optional at import-time (mirrors shacl).
        from magi_agent.customize.capability_scope import (  # noqa: PLC0415
            validate_capability_scope_payload,
        )

        errors.extend(validate_capability_scope_payload(payload))
    elif kind == "prompt_injection":
        # F-MUT1: operator-authored mutator (append to tool args or system
        # prompt). The shape varies by firesAt slot, so the validator takes
        # the resolved fires_at as input.
        from magi_agent.customize.prompt_injection import (  # noqa: PLC0415
            validate_prompt_injection_payload,
        )

        errors.extend(validate_prompt_injection_payload(payload, fires_at))
    elif kind == "output_rewrite":
        # F-MUT2: operator-authored mutator (re.sub-based redact of tool
        # output text BEFORE the model reads it). Only valid at
        # after_tool_use; the validator returns the slot mismatch as a
        # first-class error string so the wizard surfaces it inline.
        from magi_agent.customize.output_rewrite import (  # noqa: PLC0415
            validate_output_rewrite_payload,
        )

        errors.extend(validate_output_rewrite_payload(payload, fires_at))
    elif kind == "shell_command":
        # F-EXEC1: operator-authored subprocess action. The payload is a
        # ``ShellPayload`` dict (source=inline|file + inline?/path? +
        # timeout_seconds + env_vars + shell). The shell_runner module
        # ships the validator (F-EXEC-AUDIT baseline); we re-export it
        # here so the rule round-trips through the standard
        # custom_rules.validate_custom_rule entry-point. Lazy import keeps
        # facades / store / etc. hot-path-light when the operator never
        # authors a shell rule.
        from magi_agent.customize.shell_runner import (  # noqa: PLC0415
            validate_shell_payload,
        )

        errors.extend(validate_shell_payload(payload, fires_at))
    elif kind == "shell_check":
        # F-EXEC2: operator-authored subprocess VERIFIER. Same payload
        # shape as shell_command (validated by the same shell_runner
        # validator). The runtime apply helper
        # :func:`magi_agent.customize.shell_check.apply_shell_check` parses
        # stdout as ``{passed, reason?}`` JSON (with exit-code 0 ⇒ passed
        # fallback when stdout is not parseable JSON). Lazy import for the
        # same hot-path-light reason as shell_command.
        from magi_agent.customize.shell_runner import (  # noqa: PLC0415
            validate_shell_payload,
        )

        errors.extend(validate_shell_payload(payload, fires_at))

    # (f) projection ⊆ whitelist (conversation rejected)
    projection = rule.get("projection")
    if projection is not None:
        if not isinstance(projection, list):
            errors.append("projection must be a list")
        else:
            bad = [s for s in projection if not (isinstance(s, str) and _projection_slice_ok(s))]
            if bad:
                errors.append(
                    f"projection slices {bad} not allowed (conversation/full history forbidden)"
                )

    return errors
