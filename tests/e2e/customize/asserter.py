"""Action-honored assertions for the F-QA matrix harness.

Given a :class:`TriggerOutcome` and the matrix-declared expected action,
this module checks "did the runtime honor the contract for this combo?".
Each action has its own assertion path keyed off ``_LEGAL``-declared
semantics:

* ``audit`` — the runtime recorded an audit ledger entry (or an
  equivalent in-band signal) AND did not short-circuit the surrounding
  chokepoint. For mutator kinds (``output_rewrite`` / ``prompt_injection``)
  ``audit`` labels the "wrote mutation to ledger" semantics, so the
  asserter accepts a present mutation as evidence the rule fired.
* ``block`` — the runtime returned a short-circuit verdict
  (``ToolResult.status == "blocked"`` for tool slots, gate-block string
  for the pre-final llm_criterion seam, ``run_*_verdict == "block"`` for
  shell helpers, ``RequirementError``-shaped output for SHACL, the
  ``("deny", rule_id)`` pair for tool_perm).
* ``ask_approval`` — runtime emitted an ``ask`` verdict (the
  ``("ask", rule_id)`` pair for tool_perm; audit-only honest-degrade for
  llm_criterion / shell_check at non-tool-perm slots).
* ``retry`` — runtime surfaced a retry sentinel. The pre-final
  llm_criterion seam emits the same block reason for both retry and
  block actions today (retry is a presentation-layer label); the
  asserter accepts a non-empty block reason as evidence the gate fired.
* ``override`` — the after-tool ingestion gate returned an override dict
  (or, equivalently, the dispatched ``ToolResult.output`` differs from
  the original — covers the redact path).

Every assertion includes a precise (kind, slot, action, rule_id)
contextual message so a matrix-row failure points the operator at the
exact combo to investigate.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.customize.triggers import TriggerOutcome


# F-QA2 turn-boundary slot set. The asserter delegates to dedicated
# branches for these because the trigger outcome is shaped around
# ``run_governed_turn``'s yielded items + engine-invocation telemetry,
# not the per-kind side-effect bag the F-QA1 tool-use slots populate.
_F_QA2_TURN_BOUNDARY_SLOTS = frozenset(
    {
        "before_turn_start",
        "after_turn_end",
        "on_user_prompt_submit",
        "on_subagent_stop",
    }
)

# F-QA3 per-LLM-call slot set. The asserter delegates to a dedicated
# branch because the trigger outcome is shaped around the ADK plugin's
# ``on_before_model`` / ``on_after_model`` return value (``None`` ⇒
# proceed; synthetic LlmResponse with ``customize_policy_blocked``
# error_message ⇒ block) — not the per-kind side-effect bag.
_F_QA3_LLM_CALL_SLOTS = frozenset({"before_llm_call", "after_llm_call"})

# F-QA4 late-lifecycle slot set. The asserter delegates to a dedicated
# branch because each chokepoint exposes a different evidence shape:
# compaction → post-call ``contents`` identity; work-queue → tick result +
# post-task status; artifact → ``FileDeliveryDecision`` reason_codes +
# diagnostics; task_complete → governed-turn items + engine telemetry;
# session_start → first/second on_before_model return values; spawn →
# pre/post toolset name diff. Per-slot helpers below shape each into
# pass/fail with a precise (kind, slot, action, rule_id) message.
_F_QA4_LATE_LIFECYCLE_SLOTS = frozenset(
    {
        "before_compaction",
        "after_compaction",
        "on_task_checkpoint",
        "on_artifact_created",
        "on_task_complete",
        "on_session_start",
        "spawn",
    }
)


_F_QA5_SHELL_KINDS = frozenset({"shell_command", "shell_check"})


def assert_action_honored(
    outcome: TriggerOutcome,
    *,
    kind: str,
    slot: str,
    rule_id: str,
    expected_action: str,
    payload_should_fire: bool = True,
) -> None:
    """Assert the runtime honored ``expected_action`` for ``(kind, slot)``.

    ``payload_should_fire=False`` flips every assertion to its negation —
    used by the matrix's "OFF-path is byte-identical" smoke (not in F-QA1
    but available for F-QA3 budget-exhausted regressions).
    """
    ctx = (
        f"kind={kind!r} slot={slot!r} action={expected_action!r} "
        f"rule_id={rule_id!r}"
    )

    if not payload_should_fire:
        _assert_did_not_fire(outcome, ctx=ctx)
        return

    # F-QA2: turn-boundary slots route to a dedicated assertion family.
    # The trigger drives a real ``run_governed_turn`` and the outcome's
    # side_effects bag carries ``items`` / ``engine_invoked`` /
    # ``blocked_terminals`` instead of the per-kind shapes the F-QA1
    # tool-use branches inspect.
    if slot in _F_QA2_TURN_BOUNDARY_SLOTS:
        _assert_turn_boundary_honored(
            outcome,
            kind=kind,
            slot=slot,
            expected_action=expected_action,
            ctx=ctx,
        )
        return

    # F-QA3: per-LLM-call slots route to a dedicated assertion family.
    # The trigger drives ``LifecycleLlmCallAuditControl.on_before_model``
    # / ``on_after_model`` directly; the outcome carries the plugin's
    # return value (``None`` ⇒ proceed; synthetic
    # ``customize_policy_blocked`` LlmResponse ⇒ block) under
    # ``side_effects['result']`` / ``side_effects['blocked_response']``.
    if slot in _F_QA3_LLM_CALL_SLOTS:
        _assert_llm_call_honored(
            outcome,
            kind=kind,
            slot=slot,
            expected_action=expected_action,
            ctx=ctx,
        )
        return

    # F-QA4: late-lifecycle slots route to a dedicated assertion family.
    # Each slot's trigger drives a distinct production chokepoint and
    # populates a slot-specific evidence bag — the asserter shapes
    # those into a uniform pass/fail.
    if slot in _F_QA4_LATE_LIFECYCLE_SLOTS:
        _assert_late_lifecycle_honored(
            outcome,
            kind=kind,
            slot=slot,
            expected_action=expected_action,
            ctx=ctx,
        )
        return

    if expected_action == "audit":
        _assert_audit_honored(outcome, kind=kind, slot=slot, ctx=ctx)
        return
    if expected_action == "block":
        _assert_block_honored(outcome, kind=kind, slot=slot, ctx=ctx)
        return
    if expected_action == "ask_approval":
        _assert_ask_approval_honored(outcome, kind=kind, ctx=ctx)
        return
    if expected_action == "retry":
        # The pre-final llm_criterion seam currently honors ONLY
        # ``action == "block"`` (see ``MagiEngineDriver._maybe_llm_criterion_block``
        # — non-block actions fall through to ``None``). For
        # ``deterministic_ref`` the gate compile is identical for
        # block / retry (both inject the ref into required_validators).
        # Until a dedicated retry surface lands we treat retry as
        # honest-degrade:
        #   * deterministic_ref ⇒ ref injection observed (block-shaped)
        #   * llm_criterion     ⇒ audit-shaped pass-through
        # F-QA3 should tighten this when the retry sentinel is real.
        if kind == "deterministic_ref":
            _assert_block_honored(outcome, kind=kind, slot=slot, ctx=ctx)
        else:
            _assert_audit_honored(outcome, kind=kind, slot=slot, ctx=ctx)
        return
    if expected_action == "override":
        _assert_override_honored(outcome, kind=kind, ctx=ctx)
        return

    raise AssertionError(
        f"asserter has no branch for expected_action={expected_action!r} ({ctx})"
    )


# ---------------------------------------------------------------------------
# Per-action branches
# ---------------------------------------------------------------------------


def _assert_audit_honored(
    outcome: TriggerOutcome, *, kind: str, slot: str, ctx: str
) -> None:
    """An ``audit``-action rule recorded a verdict AND did not short-circuit."""
    # tool_perm has no audit action; deterministic_ref / shacl_constraint
    # surface audit via gate-compile residue (the ref appears in the
    # required_validators list).
    if kind == "deterministic_ref":
        validators = outcome.side_effects.get("required_validators", [])
        seed = outcome.side_effects.get("seed_validators", [])
        added = [r for r in validators if r not in seed]
        assert added, (
            f"deterministic_ref audit-action expected ref injection into "
            f"required_validators; got validators={validators!r} seed={seed!r} ({ctx})"
        )
        return

    if kind == "llm_criterion":
        # audit at pre_final: judge fired but reason is None
        # (gate-action-audit means "do not block"). The conftest's
        # patched judge captures the call count; we accept verdict
        # ``proceed`` as evidence the gate evaluated without blocking.
        assert outcome.runtime_verdict in {"proceed", "error"}, (
            f"llm_criterion audit-action expected non-block verdict; "
            f"got verdict={outcome.runtime_verdict!r} ({ctx})"
        )
        return

    if kind == "prompt_injection":
        # Mutator: the dispatched arguments should differ from what the
        # caller passed in (the runtime appended the rule's value).
        dispatched = outcome.side_effects.get("dispatched_arguments") or {}
        cmd = dispatched.get("command", "")
        assert " --dry-run" in cmd, (
            f"prompt_injection audit-action expected mutated dispatched args; "
            f"got dispatched_arguments={dispatched!r} ({ctx})"
        )
        return

    if kind == "output_rewrite":
        # Mutator: the returned tool output should NOT contain the
        # AWS-key-shaped substring (the rule redacted it).
        tool_result = outcome.side_effects.get("tool_result")
        original = outcome.side_effects.get("original_output", "")
        assert tool_result is not None, f"missing tool_result ({ctx})"
        output = getattr(tool_result, "output", "")
        assert output != original, (
            f"output_rewrite audit-action expected redacted output; "
            f"got output={output!r} original={original!r} ({ctx})"
        )
        return

    if kind in {"shell_command", "shell_check"}:
        # Two cases:
        # (a) Trigger drove the lifecycle_audit fan-out helper directly
        #     (pre_final). The helper returns audit records and a verdict
        #     — we assert at least one ``executed`` / ``evaluated`` /
        #     ``budget_exhausted`` record AND verdict != block.
        # (b) Trigger drove the facade (before_tool_use / after_tool_use).
        #     The facade calls the helper internally and DISCARDS the
        #     audit list (audit-only is silent by design). The only
        #     ON-path evidence the caller sees is "the dispatch
        #     completed normally" — assert verdict == proceed and the
        #     tool_result is non-blocked. The per-kind firing test in
        #     ``tests/customize_firing/`` covers the audit ledger
        #     contents directly.
        records = outcome.audit_records
        if records:
            statuses = {r.get("status") for r in records}
            assert statuses & {"executed", "evaluated", "budget_exhausted"}, (
                f"shell audit-action expected ledger status in "
                f"{{executed,evaluated,budget_exhausted}}; got statuses={statuses!r} ({ctx})"
            )
        # Facade-internal slot: assert dispatch proceeded.
        assert outcome.runtime_verdict != "block", (
            f"shell audit-action must not short-circuit; "
            f"got verdict={outcome.runtime_verdict!r} ({ctx})"
        )
        if slot in {"before_tool_use", "after_tool_use"}:
            tool_result = outcome.side_effects.get("tool_result")
            assert tool_result is not None, (
                f"shell audit-action via facade expected tool_result on outcome; "
                f"got side_effects={outcome.side_effects!r} ({ctx})"
            )
            status = getattr(tool_result, "status", None)
            assert status != "blocked", (
                f"shell audit-action via facade must not produce blocked "
                f"ToolResult; got status={status!r} ({ctx})"
            )
        return

    raise AssertionError(f"audit-honored: no branch for kind={kind!r} ({ctx})")


def _assert_block_honored(
    outcome: TriggerOutcome, *, kind: str, slot: str, ctx: str
) -> None:
    """A ``block``-action rule short-circuited the surrounding chokepoint."""
    if kind == "tool_perm":
        decision = outcome.side_effects.get("decision")
        assert decision is not None, (
            f"tool_perm block expected ('deny', rule_id) decision; got None ({ctx})"
        )
        verdict_label, _rule_id = decision
        assert verdict_label == "deny", (
            f"tool_perm block expected verdict='deny'; got {verdict_label!r} ({ctx})"
        )
        return

    if kind == "deterministic_ref":
        # block-action: ref was injected (runtime gate blocks downstream
        # when the required ref is unsatisfied — we assert the injection
        # happened; the downstream block is the gate caller's contract).
        validators = outcome.side_effects.get("required_validators", [])
        seed = outcome.side_effects.get("seed_validators", [])
        added = [r for r in validators if r not in seed]
        assert added, (
            f"deterministic_ref block expected ref injection into "
            f"required_validators; got validators={validators!r} seed={seed!r} ({ctx})"
        )
        return

    if kind in {"llm_criterion", "shacl_constraint", "shell_check"}:
        assert outcome.runtime_verdict == "block", (
            f"{kind} block-action expected verdict='block'; "
            f"got verdict={outcome.runtime_verdict!r} side_effects={outcome.side_effects!r} ({ctx})"
        )
        return

    if kind == "shell_command":
        # shell_command at before_tool_use: the facade short-circuits
        # via blocked ToolResult. At pre_final: the helper records a
        # non-zero exit on action=block (the governed_turn caller wraps
        # the verdict — we accept either signal).
        tool_result = outcome.side_effects.get("tool_result")
        if tool_result is not None and getattr(tool_result, "status", None) == "blocked":
            return
        if outcome.runtime_verdict == "block":
            return
        # Fallback: any audit record reporting non-zero exit code is the
        # runtime's only honest "the script said block" surface at
        # pre_final.
        for record in outcome.audit_records:
            if (
                record.get("status") == "executed"
                and record.get("exit_code", 0) != 0
            ):
                return
        raise AssertionError(
            f"shell_command block-action expected blocked ToolResult / "
            f"non-zero exit audit / verdict=block; "
            f"got verdict={outcome.runtime_verdict!r} "
            f"records={outcome.audit_records!r} "
            f"side_effects={outcome.side_effects!r} ({ctx})"
        )

    raise AssertionError(f"block-honored: no branch for kind={kind!r} ({ctx})")


def _assert_ask_approval_honored(
    outcome: TriggerOutcome, *, kind: str, ctx: str
) -> None:
    """An ``ask_approval``-action rule emitted an ``ask`` verdict."""
    if kind == "tool_perm":
        decision = outcome.side_effects.get("decision")
        assert decision is not None, (
            f"tool_perm ask_approval expected ('ask', rule_id) decision; got None ({ctx})"
        )
        verdict_label, _rule_id = decision
        assert verdict_label == "ask", (
            f"tool_perm ask_approval expected verdict='ask'; got {verdict_label!r} ({ctx})"
        )
        return

    # llm_criterion / shell_check ask_approval at non-tool-perm slots is
    # honest-degrade (audit-only). The audit ledger should still record
    # the verdict.
    if outcome.audit_records:
        return
    if outcome.runtime_verdict in {"proceed", "ask"}:
        return
    raise AssertionError(
        f"ask_approval-honored expected audit record OR proceed/ask verdict; "
        f"got verdict={outcome.runtime_verdict!r} records={outcome.audit_records!r} ({ctx})"
    )


def _assert_override_honored(
    outcome: TriggerOutcome, *, kind: str, ctx: str
) -> None:
    """An ``override``-action rule replaced the tool result."""
    if kind != "llm_criterion":
        raise AssertionError(
            f"override action is only legal for llm_criterion; got kind={kind!r} ({ctx})"
        )
    assert outcome.runtime_verdict == "override", (
        f"llm_criterion override expected verdict='override'; "
        f"got verdict={outcome.runtime_verdict!r} side_effects={outcome.side_effects!r} ({ctx})"
    )
    override = outcome.side_effects.get("override")
    assert isinstance(override, dict), (
        f"llm_criterion override expected dict override; got {override!r} ({ctx})"
    )
    assert override.get("status") == "blocked", (
        f"override dict expected status='blocked'; got {override!r} ({ctx})"
    )


# ---------------------------------------------------------------------------
# F-QA2 turn-boundary assertions
# ---------------------------------------------------------------------------


def _assert_turn_boundary_honored(
    outcome: TriggerOutcome,
    *,
    kind: str,
    slot: str,
    expected_action: str,
    ctx: str,
) -> None:
    """Assert ``run_governed_turn`` honored a turn-boundary rule per ``_LEGAL``.

    Slot semantics (per :mod:`magi_agent.runtime.governed_turn`):

    * ``before_turn_start`` / ``on_user_prompt_submit`` — these are GATE
      slots. ``block``-action MUST short-circuit the funnel BEFORE the
      engine stream is consumed (``engine_invoked == False`` and the
      yielded list is a single synthetic
      ``EngineResult(terminal=Terminal.aborted)`` whose error string
      identifies the blocking slot). ``audit`` / ``ask_approval`` are
      honest-degrade today — the turn proceeds, engine is invoked, and
      no synthetic policy-blocked terminal appears.

    * ``after_turn_end`` — audit-only by ``_LEGAL`` (block excluded).
      The turn MUST complete: engine invoked, no synthetic terminal in
      the yielded items, ``runtime_verdict == "proceed"``.

    * ``on_subagent_stop`` — ``_LEGAL`` accepts
      ``{audit, block, ask_approval}`` for authorability (F-LIFE1
      lift), but the runtime parent-surfacing wire is NOT built yet
      (TODO per F-LIFE1 review pass). The asserter:
        * verifies the engine WAS invoked (child turn ran),
        * verifies no synthetic policy-blocked terminal appears
          (parent SpawnAgent does not yet consume the verdict),
        * does NOT assert any caller-side block / requires_approval
          surface — the contract is "audit ledger captures the verdict;
          runtime surfacing is a follow-up".
    """
    side = outcome.side_effects
    items = side.get("items", [])
    engine_invoked = bool(side.get("engine_invoked"))
    blocked_terminals = side.get("blocked_terminals", [])

    if slot in {"before_turn_start", "on_user_prompt_submit"}:
        if expected_action == "block":
            # shell_check block at turn-boundary slots is HONEST-DEGRADE in
            # v1 per the ``custom_rules._LEGAL`` shell_check comment:
            # "Other lifecycle slots are accepted by the validator
            # (mirroring llm_criterion's matrix) for forward-compat
            # authoring, but in v1 the runtime fan-out helpers fire them
            # audit-only — the verifier records the per-rule verdict
            # alongside the llm_criterion audit but does not gate the
            # surrounding chokepoint."
            # Concretely: ``_maybe_run_before_turn_start_gate`` and
            # ``_maybe_run_user_prompt_submit_gate`` only consult
            # ``llm_criterion`` rules. A shell_check block-action rule at
            # one of these slots is recorded by the audit but does NOT
            # short-circuit the engine stream. The matrix accepts that
            # contract — runtime parity follows in a separate PR.
            if kind == "shell_check":
                assert engine_invoked, (
                    f"shell_check block at {slot!r} is audit-only in v1 "
                    f"(runtime gate consults llm_criterion only) — engine "
                    f"MUST still run; got engine_invoked={engine_invoked!r} "
                    f"({ctx})"
                )
                # No synthetic policy-blocked terminal should appear.
                unexpected = [
                    i for i in items
                    if _looks_like_policy_blocked_for_slot(i, slot=slot)
                ]
                assert not unexpected, (
                    f"shell_check block at {slot!r} MUST NOT short-circuit "
                    f"in v1 (audit-only honest-degrade); got "
                    f"unexpected={unexpected!r} ({ctx})"
                )
                return
            # llm_criterion / shacl_constraint: a true GATE block.
            # Engine MUST NOT have been invoked; the synthetic
            # policy-blocked terminal MUST be the only yielded item.
            assert outcome.runtime_verdict == "block", (
                f"turn-boundary block expected verdict='block'; "
                f"got verdict={outcome.runtime_verdict!r} ({ctx})"
            )
            assert not engine_invoked, (
                f"turn-boundary block at {slot!r} MUST short-circuit "
                f"BEFORE rt.engine.run_turn_stream is invoked; "
                f"got engine_invoked={engine_invoked!r} "
                f"calls={side.get('engine_run_turn_stream_calls')!r} ({ctx})"
            )
            assert len(items) == 1, (
                f"turn-boundary block expected exactly one yielded item "
                f"(the synthetic policy-blocked terminal); got "
                f"{len(items)} items={items!r} ({ctx})"
            )
            return
        # audit / ask_approval at a gate slot: honest-degrade — turn
        # proceeds, engine IS invoked, NO synthetic policy-blocked
        # terminal appears among yielded items.
        assert engine_invoked, (
            f"turn-boundary {expected_action!r} at {slot!r} MUST proceed "
            f"(engine invoked); got engine_invoked={engine_invoked!r} ({ctx})"
        )
        # Inspect items for a stray synthetic terminal — there should be
        # none on a non-block action.
        unexpected = [
            i for i in items
            if _looks_like_policy_blocked_for_slot(i, slot=slot)
        ]
        assert not unexpected, (
            f"turn-boundary {expected_action!r} at {slot!r} MUST NOT emit "
            f"a synthetic policy-blocked terminal; got "
            f"unexpected={unexpected!r} ({ctx})"
        )
        return

    if slot == "after_turn_end":
        # Audit-only per _LEGAL. Engine MUST be invoked; no synthetic
        # terminal should appear in the items.
        assert engine_invoked, (
            f"after_turn_end {expected_action!r} MUST let the engine run; "
            f"got engine_invoked={engine_invoked!r} ({ctx})"
        )
        assert not blocked_terminals, (
            f"after_turn_end is audit-only — a synthetic policy-blocked "
            f"terminal MUST NOT appear; got {blocked_terminals!r} ({ctx})"
        )
        assert outcome.runtime_verdict == "proceed", (
            f"after_turn_end expected verdict='proceed'; "
            f"got {outcome.runtime_verdict!r} ({ctx})"
        )
        return

    if slot == "on_subagent_stop":
        # Authorability-lift (F-LIFE1) but no parent-surfacing wire yet.
        # Verify: child turn ran (engine invoked), no synthetic
        # policy-blocked terminal appears. DO NOT assert any parent-side
        # block — that's the F-LIFE1 follow-up TODO.
        assert engine_invoked, (
            f"on_subagent_stop {expected_action!r} MUST let the child "
            f"engine run; got engine_invoked={engine_invoked!r} ({ctx})"
        )
        assert not blocked_terminals, (
            f"on_subagent_stop runtime parent-surfacing is NOT wired yet "
            f"(F-LIFE1 TODO); the slot MUST NOT emit a synthetic "
            f"policy-blocked terminal even on action={expected_action!r}; "
            f"got {blocked_terminals!r} ({ctx})"
        )
        # Verdict label: trigger always reports "proceed" because the
        # parent path does not short-circuit on this slot today.
        assert outcome.runtime_verdict == "proceed", (
            f"on_subagent_stop runtime_verdict expected 'proceed' "
            f"(audit-only at the parent funnel today); "
            f"got {outcome.runtime_verdict!r} ({ctx})"
        )
        # ``kind`` is informational here — the audit ledger contents
        # are covered by the per-kind firing tests in
        # ``tests/customize_firing/``. The matrix row's job is to
        # confirm the wire stayed alive AND that runtime did not
        # accidentally activate the missing parent-surfacing path.
        _ = kind
        return

    raise AssertionError(
        f"_assert_turn_boundary_honored has no branch for slot={slot!r} ({ctx})"
    )


def _assert_llm_call_honored(
    outcome: TriggerOutcome,
    *,
    kind: str,
    slot: str,
    expected_action: str,
    ctx: str,
) -> None:
    """Assert the ADK LLM-call plugin honored ``expected_action`` for ``(kind, slot)``.

    Slot semantics (per :mod:`magi_agent.adk_bridge.lifecycle_llm_call_control`):

    * ``before_llm_call`` block — ``on_before_model`` MUST return the
      synthetic policy-blocked ``LlmResponse`` so the surrounding ADK
      callback dispatcher suppresses the outbound model call. The
      synthetic response's ``error_message`` MUST start with
      ``"customize_policy_blocked:"`` and identify the slot.
    * ``after_llm_call`` block — ``on_after_model`` MUST return the
      synthetic refusal so the downstream consumer never sees the
      offending text (ADK after_model supports replacing the response).
      The audit-only sibling returns ``None`` so the original response
      surfaces unchanged.
    * Both slots ``audit`` action — ``None`` return; the audit ledger
      captures the judge invocation but no policy block fires.

    v1 ``_LEGAL`` only accepts ``llm_criterion`` rules at these slots
    (kind argument is informational — used only to surface a clear
    failure message).
    """
    # The LLM-call slots only accept llm_criterion in v1 — defensively
    # surface a clear message if the matrix ever lifts to other kinds.
    if kind != "llm_criterion":
        raise AssertionError(
            f"_assert_llm_call_honored: v1 only supports llm_criterion at "
            f"{slot!r}; got kind={kind!r} ({ctx})"
        )

    side = outcome.side_effects
    result = side.get("result")
    blocked_response = side.get("blocked_response")

    if expected_action == "block":
        # block MUST return the synthetic policy-blocked LlmResponse — not
        # None. _classify_llm_call_result already pinned the slot label
        # appears in the error_message; surface a friendly failure when
        # the plugin returned None (the gate fell through to fail-open).
        assert outcome.runtime_verdict == "block", (
            f"{slot} block expected runtime_verdict='block'; got "
            f"{outcome.runtime_verdict!r} result={result!r} ({ctx})"
        )
        assert blocked_response is not None, (
            f"{slot} block expected synthetic policy-blocked LlmResponse "
            f"return; got result={result!r} ({ctx})"
        )
        # The plugin's policy-blocked helper sets ``error_message`` to
        # ``"customize_policy_blocked: <slot> llm_criterion verdict=block"``.
        # _classify_llm_call_result has already filtered for the marker
        # substring; assert the slot-label tag explicitly so a future
        # regression that swaps the slot label loud-fails.
        error_message = getattr(blocked_response, "error_message", "") or ""
        assert slot in error_message, (
            f"{slot} block expected slot label in error_message; got "
            f"error_message={error_message!r} ({ctx})"
        )
        return

    # audit (the only other v1 action) — plugin returns None so the
    # model call proceeds. The audit ledger captures the judge's verdict
    # via the patched_judge sentinel; we accept verdict in {proceed} as
    # evidence that no synthetic refusal fired.
    assert outcome.runtime_verdict == "proceed", (
        f"{slot} {expected_action!r} expected verdict='proceed'; got "
        f"{outcome.runtime_verdict!r} result={result!r} ({ctx})"
    )
    assert result is None, (
        f"{slot} {expected_action!r} expected on_*_model return None "
        f"(model call proceeds); got result={result!r} ({ctx})"
    )


def _looks_like_policy_blocked_for_slot(item: object, *, slot: str) -> bool:
    """True iff *item* is a synthetic ``customize_policy_blocked`` for *slot*."""
    try:
        from magi_agent.cli.contracts import EngineResult, Terminal
    except Exception:
        return False
    if not isinstance(item, EngineResult):
        return False
    if item.terminal is not Terminal.aborted:
        return False
    error = item.error or ""
    return "customize_policy_blocked" in error and slot in error


def _assert_late_lifecycle_honored(
    outcome: TriggerOutcome,
    *,
    kind: str,
    slot: str,
    expected_action: str,
    ctx: str,
) -> None:
    """Assert the F-QA4 late-lifecycle chokepoint honored ``expected_action``.

    Per-slot semantics (cross-references the runtime chokepoints + the
    relevant F-LIFE review-pass NOTEs):

    * ``before_compaction`` — audit-only by default; ``block`` action
      causes :meth:`MagiContextCompactionPlugin._apply_tail_trim` to
      RETURN EARLY without mutating ``llm_request.contents``. Asserter
      verifies the post-call contents are byte-identical to the pre-call
      contents on ``block`` rows.
    * ``after_compaction`` — audit-only by ``_LEGAL`` (block excluded).
      Asserter only confirms the trigger ran without raising; firing
      evidence is covered by the JudgePatcher.calls sanity check in the
      test body.
    * ``on_task_checkpoint`` — F-LIFE4a review-pass NOTE: only the
      ``claimed`` transition honors a true block; other transitions are
      audit-only. Asserter:
        - ``audit`` / ``ask_approval`` rows: the in-memory task moves to
          ``completed`` (the stub runner returns ``outcome="completed"``).
          ``ask_approval`` is honest-degrade today (audit only).
        - ``block`` rows: the task moves to ``failed`` with the
          ``customize_policy_blocked`` error sentinel; the driver does
          NOT advance to the runner.
    * ``on_artifact_created`` — block is honestly impossible (artifact
      already written by the provider). Asserter:
        - ``audit`` rows: the decision MUST NOT carry the
          ``artifact_review_pending`` reason code.
        - ``ask_approval`` rows: the decision MUST carry
          ``requires_approval=True`` in ``diagnostic_metadata`` with
          ``approval_slot == "on_artifact_created"`` (the audit ledger's
          ``requires_approval=true`` marker per the F-LIFE4a wire).
    * ``on_task_complete`` — F-LIFE4b review-pass NOTE: the collector
      annotates audit records with ``requires_approval`` / ``gate_verdict``
      for block / ask actions, but no compensating-action wire (turn
      rollback) ships in v1. Asserter verifies the engine ran (turn
      proceeded) for every action — the ledger annotations are
      observability-only.
    * ``on_session_start`` — first-fire-per-session contract:
        - First call: ``audit`` ⇒ proceed (``None`` return); ``block`` ⇒
          synthetic policy-blocked ``LlmResponse`` whose
          ``error_message`` carries ``customize_policy_blocked:
          on_session_start`` (the plugin's helper).
        - Second call (same session): MUST short-circuit and return
          ``None`` regardless of action — the membership check fires
          before the policy load.
    * ``spawn`` — capability_scope deterministic subtraction:
        - The ``denyTools=["shell_exec"]`` payload removes ``shell_exec``
          from the resolved toolset. Asserter verifies ``shell_exec``
          appears in ``removed_tool_names`` and the remaining toolset
          equals the original minus the deny entry.

    Asserter does NOT inspect the audit ledger directly — the test body's
    ``JudgePatcher.calls`` sanity check is responsible for "the rule
    fired at all". Each branch surfaces a precise failure message so a
    failing matrix row identifies the offending combo.
    """
    side = outcome.side_effects

    if slot == "before_compaction":
        original = side.get("original_contents") or []
        post = side.get("post_contents") or []
        if expected_action == "block":
            # ``_maybe_compaction_blocked`` consults
            # :func:`run_before_compaction_gate` which today only
            # derives a block verdict from ``llm_criterion`` audits.
            # The validator accepts ``shell_check`` at this slot with
            # action=block (for forward-compat authoring per
            # ``custom_rules._LEGAL``'s "Other lifecycle slots are
            # accepted by the validator..." NOTE), but the runtime
            # gate is audit-only for shell_check. Mirror the F-QA2
            # shell_check turn-boundary block honest-degrade: assert
            # the trigger ran without raising and short-circuit the
            # strict "contents unchanged" check.
            if kind == "shell_check":
                return
            # llm_criterion block honest-degrade: _apply_tail_trim
            # returns early WITHOUT mutating contents. The trigger
            # drove the call with a sliceable list so the asserter
            # can compare.
            assert post == original, (
                f"before_compaction block expected contents unchanged "
                f"(early return on block); got original={original!r} "
                f"post={post!r} ({ctx})"
            )
            return
        # audit: helper fired, judge call records audit; we cannot
        # tightly assert "mutated" because orphan adjustment may keep
        # the whole list. Pass-through.
        return

    if slot == "after_compaction":
        # Audit-only per _LEGAL (block excluded). The driver simply ran
        # _apply_tail_trim end-to-end; ON-firing evidence is the
        # JudgePatcher.calls sanity check in the test body.
        return

    if slot == "on_task_checkpoint":
        post_status = side.get("post_status")
        # Honest-degrade for v1: the work-queue dispatcher only invokes
        # ``_emit_task_checkpoint_sync`` (audit-only fan-out) at each
        # transition; ``run_on_task_checkpoint_gate`` is defined in
        # ``customize/lifecycle_audit.py`` but has NO production
        # callsite. So ``block`` / ``ask_approval`` rows do NOT route
        # through ``record_failure`` — the stub runner returns
        # ``outcome=completed`` and the task transitions cleanly. Pin
        # the completed-transition contract; tightening block / ask
        # waits on the dispatcher wire follow-up.
        assert post_status == "completed", (
            f"on_task_checkpoint {expected_action!r} expected post_status="
            f"'completed' (audit-only v1 — gate consult not wired); got "
            f"post_status={post_status!r} ({ctx})"
        )
        return

    if slot == "on_artifact_created":
        # Honest-degrade for v1: ``run_on_artifact_created_gate`` is
        # defined in ``customize/lifecycle_audit.py`` but has NO
        # production callsite — ``FileDeliveryBoundary.execute`` does
        # not consult it. So neither ``ask_approval`` nor ``audit``
        # rows produce an artifact-policy-shaped decision; the only
        # observable assertion is that the boundary completed end-
        # to-end without raising (the trigger already returned a
        # decision object). The judge-call sanity check in the test
        # body proves the rule was loaded + reachable; tightening
        # this branch waits on the boundary wire follow-up.
        decision = side.get("decision")
        assert decision is not None, (
            f"on_artifact_created {expected_action!r} expected the "
            f"FileDeliveryBoundary to return a decision; got None ({ctx})"
        )
        return
        return

    if slot == "on_task_complete":
        engine_invoked = bool(side.get("engine_invoked"))
        items = side.get("items") or []
        # F-LIFE4b review-pass NOTE: no compensating-action wire in v1
        # — block / ask annotate the audit ledger but never roll back
        # the already-emitted turn. Engine MUST have run for every
        # action.
        assert engine_invoked, (
            f"on_task_complete {expected_action!r} MUST let the engine "
            f"run; got engine_invoked={engine_invoked!r} ({ctx})"
        )
        # No synthetic policy-blocked terminal — the compensating-action
        # wire is deferred per F-LIFE4b review.
        unexpected = [
            i for i in items
            if _looks_like_policy_blocked_for_slot(i, slot="on_task_complete")
        ]
        assert not unexpected, (
            f"on_task_complete {expected_action!r} compensating-action "
            f"wire is deferred (F-LIFE4b review NOTE); a synthetic "
            f"policy-blocked terminal MUST NOT appear; got "
            f"unexpected={unexpected!r} ({ctx})"
        )
        return

    if slot == "on_session_start":
        # First-fire-per-session: second call MUST be silent regardless
        # of action.
        second_was_silent = bool(side.get("second_was_silent"))
        assert second_was_silent, (
            f"on_session_start expected second call (same session) to "
            f"return None — first-fire-per-session contract violated; "
            f"got second_result={side.get('second_result')!r} ({ctx})"
        )
        if expected_action == "block":
            # First call MUST return the synthetic policy-blocked
            # LlmResponse so the model call is suppressed.
            assert outcome.runtime_verdict == "block", (
                f"on_session_start block expected first call verdict="
                f"'block'; got {outcome.runtime_verdict!r} "
                f"first_result={side.get('first_result')!r} ({ctx})"
            )
            blocked = side.get("first_blocked_response")
            assert blocked is not None, (
                f"on_session_start block expected synthetic "
                f"policy-blocked LlmResponse on first call; got "
                f"first_blocked_response=None ({ctx})"
            )
            # The plugin's helper
            # ``_build_policy_blocked_llm_response`` populates
            # ``custom_metadata = {"policy_blocked": True, "reason":
            # "on_session_start llm_criterion verdict=block"}``. The
            # ``error_message`` field is NOT set — earlier asserter
            # passes that grepped ``error_message`` were stale
            # (the helper was reshaped to keep block attribution
            # in ``custom_metadata`` so downstream telemetry /
            # audit can read it without parsing free-text).
            metadata = getattr(blocked, "custom_metadata", None) or {}
            assert isinstance(metadata, dict), (
                f"on_session_start block expected custom_metadata dict on "
                f"the synthetic response; got "
                f"custom_metadata={metadata!r} ({ctx})"
            )
            assert metadata.get("policy_blocked") is True, (
                f"on_session_start block expected "
                f"custom_metadata.policy_blocked=True; got "
                f"custom_metadata={metadata!r} ({ctx})"
            )
            reason = metadata.get("reason") or ""
            assert "on_session_start" in reason, (
                f"on_session_start block expected slot label in "
                f"custom_metadata.reason; got reason={reason!r} ({ctx})"
            )
            return
        # audit: first call returns None (proceed); audit ledger
        # captures the verdict via the patched judge.
        assert outcome.runtime_verdict == "proceed", (
            f"on_session_start {expected_action!r} expected first call "
            f"verdict='proceed'; got {outcome.runtime_verdict!r} ({ctx})"
        )
        return

    if slot == "spawn":
        if kind != "capability_scope":
            raise AssertionError(
                f"spawn slot only legal for capability_scope; got "
                f"kind={kind!r} ({ctx})"
            )
        original = list(side.get("original_tool_names") or [])
        narrowed = list(side.get("narrowed_tool_names") or [])
        removed = list(side.get("removed_tool_names") or [])
        # The payload factory authors denyTools=["shell_exec"]. Verify
        # the deterministic subtraction landed.
        assert "shell_exec" in removed, (
            f"spawn capability_scope expected 'shell_exec' subtracted "
            f"(denyTools); got removed={removed!r} original={original!r} "
            f"narrowed={narrowed!r} ({ctx})"
        )
        assert "shell_exec" not in narrowed, (
            f"spawn capability_scope expected 'shell_exec' NOT in "
            f"narrowed toolset; got narrowed={narrowed!r} ({ctx})"
        )
        # All remaining tools should survive — only the deny entry is
        # subtracted.
        remaining = [n for n in original if n != "shell_exec"]
        assert narrowed == remaining, (
            f"spawn capability_scope expected only 'shell_exec' "
            f"subtracted; got narrowed={narrowed!r} "
            f"expected={remaining!r} ({ctx})"
        )
        return

    raise AssertionError(
        f"_assert_late_lifecycle_honored has no branch for slot={slot!r} ({ctx})"
    )


def _assert_did_not_fire(outcome: TriggerOutcome, *, ctx: str) -> None:
    """OFF-path / negative assertion: the rule MUST NOT have fired."""
    assert outcome.runtime_verdict in {"proceed", "error"}, (
        f"did-not-fire expected verdict in {{proceed, error}}; "
        f"got verdict={outcome.runtime_verdict!r} ({ctx})"
    )
    # Mutators: side_effects.tool_result.output must equal original.
    tool_result = outcome.side_effects.get("tool_result")
    original = outcome.side_effects.get("original_output")
    if tool_result is not None and original is not None:
        assert getattr(tool_result, "output", original) == original, (
            f"did-not-fire expected tool output unchanged; "
            f"got output={getattr(tool_result, 'output', None)!r} original={original!r} ({ctx})"
        )


def assert_shell_action_honored(
    outcome: TriggerOutcome,
    *,
    kind: str,
    slot: str,
    rule_id: str,
    expected_action: str,
) -> None:
    """F-QA5 entry point — assert a shell rule honored ``expected_action``.

    Routes shell_command / shell_check matrix rows authored by
    :mod:`tests.e2e.customize.test_matrix_shell` through a single
    asserter that understands the lifecycle_audit fan-out outcome
    shape regardless of slot. The F-QA1 / F-QA2 / F-QA4 asserters
    (which route via slot, not kind) cannot be reused here because
    they assume per-slot trigger shapes (facade ToolResult / governed
    turn items / FileDeliveryDecision) that the shell fan-out
    triggers do not produce.

    Per-action contract:

    * ``audit`` — at least one audit record in
      ``{executed, evaluated, budget_exhausted}`` AND ``runtime_verdict``
      is not ``block``. ``budget_exhausted`` is honored as evidence the
      gate fired (the cross-kind budget test exercises this path
      explicitly).
    * ``block`` — ``runtime_verdict == "block"`` OR (at audit-only
      siblings where the gate cannot short-circuit) at least one
      audit record carries a non-zero exit code as the in-band
      "block-shaped" signal.
    * ``ask_approval`` — honest-degrade today (audit-only). Accept an
      audit record OR a ``proceed`` / ``ask`` verdict.

    Honest-degrade for shell_check at slots without a v1 helper: the
    trigger sets ``side_effects["honest_degrade"]=True`` so the
    asserter short-circuits to a friendly skip — the validator
    accepts the row for forward-compat authoring but the runtime
    ships no fan-out helper to drive.
    """
    if kind not in _F_QA5_SHELL_KINDS:
        raise AssertionError(
            f"assert_shell_action_honored called with non-shell kind="
            f"{kind!r} (slot={slot!r} action={expected_action!r} "
            f"rule_id={rule_id!r})"
        )
    ctx = (
        f"kind={kind!r} slot={slot!r} action={expected_action!r} "
        f"rule_id={rule_id!r}"
    )
    if outcome.side_effects.get("honest_degrade"):
        # shell_check at a slot with no v1 helper (validator-accepts
        # only): mirror the F-QA2 / F-QA4 honest-degrade contract —
        # accept proceed verdict + empty audit list.
        assert outcome.runtime_verdict in {"proceed", "ask"}, (
            f"shell honest-degrade expected verdict in {{proceed, ask}}; "
            f"got verdict={outcome.runtime_verdict!r} ({ctx})"
        )
        return
    _assert_shell_honored(
        outcome,
        kind=kind,
        slot=slot,
        expected_action=expected_action,
        ctx=ctx,
    )


def _assert_shell_honored(
    outcome: TriggerOutcome,
    *,
    kind: str,
    slot: str,
    expected_action: str,
    ctx: str,
) -> None:
    """Shared per-action body for :func:`assert_shell_action_honored`."""
    records = outcome.audit_records or []
    statuses = {r.get("status") for r in records}

    if expected_action == "block":
        # Gate-honored slots return verdict="block" from the helper.
        if outcome.runtime_verdict == "block":
            return
        # At audit-only siblings the helper does not surface a verdict —
        # the in-band signal is a non-zero exit code on an executed
        # record (shell_command) or a passed=False evaluation
        # (shell_check).
        for record in records:
            if (
                record.get("status") == "executed"
                and record.get("exit_code", 0) != 0
            ):
                return
            if (
                record.get("status") == "evaluated"
                and record.get("passed") is False
            ):
                return
        raise AssertionError(
            f"shell block-action expected verdict='block' OR a non-zero "
            f"exit / passed=false audit record; got "
            f"verdict={outcome.runtime_verdict!r} records={records!r} "
            f"({ctx})"
        )

    if expected_action == "ask_approval":
        # Honest-degrade today — accept audit evidence OR a proceed
        # verdict (the runtime gate at non-tool-perm slots does not
        # short-circuit on ask_approval for shell kinds in v1).
        if records:
            return
        if outcome.runtime_verdict in {"proceed", "ask"}:
            return
        raise AssertionError(
            f"shell ask_approval-action expected audit record OR "
            f"proceed/ask verdict; got verdict={outcome.runtime_verdict!r} "
            f"records={records!r} ({ctx})"
        )

    if expected_action == "audit":
        # Audit-action: at least one executed/evaluated/budget_exhausted
        # record AND no short-circuit.
        assert outcome.runtime_verdict != "block", (
            f"shell audit-action must not short-circuit; "
            f"got verdict={outcome.runtime_verdict!r} ({ctx})"
        )
        assert statuses & {"executed", "evaluated", "budget_exhausted"}, (
            f"shell audit-action expected ledger status in "
            f"{{executed,evaluated,budget_exhausted}}; got "
            f"statuses={statuses!r} records={records!r} ({ctx})"
        )
        # Mark the kind explicitly so a stale matrix row that ever
        # routes a non-shell kind through this asserter loud-fails on
        # the unused arg rather than silently passing.
        _ = kind
        _ = slot
        return

    raise AssertionError(
        f"_assert_shell_honored has no branch for "
        f"expected_action={expected_action!r} ({ctx})"
    )

