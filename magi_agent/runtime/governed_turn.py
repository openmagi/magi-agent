"""The single primitive every governed turn flows through.

All governed turns — top-level serve requests, CLI REPL turns, and child-agent
turns — funnel through ``run_governed_turn``.  The function accepts an optional
pre-built ``runtime`` so the CLI REPL can reuse its long-lived driver across
turns without rebuilding it per call.  The serve path and child paths pass
``runtime=None`` and receive a fresh runtime from ``_build_runtime``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Callable

from magi_agent.runtime.turn_context import TurnContext


async def run_governed_turn(
    ctx: TurnContext,
    *,
    runtime: object | None = None,
    cancel: asyncio.Event | None = None,
) -> AsyncGenerator[object, None]:
    """Yield every event produced by one governed turn.

    Parameters
    ----------
    ctx:
        Immutable description of the turn (prompt, session_id, turn_id, and
        any harness-state fields the engine router or verifiers need).
    runtime:
        A pre-built ``HeadlessRuntime``-compatible object exposing ``.engine``
        (a ``MagiEngineDriver``) and ``.gate``.  When provided it is **reused
        as-is** (the CLI REPL keeps one runtime alive across turns).  When
        ``None`` a fresh runtime is built from *ctx* via ``_build_runtime``.
    cancel:
        An optional external ``asyncio.Event`` for cancellation signaling.
        When provided, it is threaded into ``run_turn_stream`` so the caller
        can cancel the turn externally.  When ``None`` a fresh event is created
        (today's behavior).
    """
    # PR-F7: project operator-authored Customize budgets onto the live env
    # BEFORE the runtime/engine is built so the downstream readers
    # (CoreToolhostHandlerSet.from_env at bind_core_toolhost_handlers time,
    # parse_loop_guard_env at build_default_plugin time) see the seeded value.
    # No-op (and triple-gated) when the F7 flag is OFF; ``setdefault`` semantics
    # mean explicit operator env always wins. Fail-open so a malformed budget
    # map can never break a turn — see budgets_apply.apply_budgets_if_enabled.
    _maybe_apply_customize_budgets()

    # Managed-inference turn-boundary credit pre-check. Only active for the OSS
    # desktop managed tier (gated on MAGI_MANAGED_INFERENCE_ENABLED + proxy env);
    # inert for hosted bots / BYO key. Runs BEFORE any audit/gate/engine work so a
    # hard-stop avoids all downstream cost. Fails OPEN — only a definitive
    # insufficient balance yields a synthetic terminal; the api-proxy remains the
    # billing source of truth.
    blocked = await _maybe_run_managed_credit_precheck(ctx)
    if blocked is not None:
        yield blocked
        return

    # PR-F-EXEC1: publish the active (session, turn) identity for the
    # shell-command per-turn budget. The 9 lifecycle_audit shell fan-out
    # helpers consult this via :func:`shell_budget_for` so a single shared
    # counter caps spawns across ALL slots in a turn (the 6th spawn
    # short-circuits at the next slot, not the 6th spawn within one slot).
    # Fail-open: import error leaves identity unset, fan-out helpers see
    # ``remaining_budget=None`` (no cap) — byte-identical to today.
    _shell_identity_token = _maybe_set_shell_budget_identity(ctx)

    # PR-F-LIFE1: Tier 2 ``before_turn_start`` audit-only fan-out. Wired at
    # the TOP of the canonical funnel, BEFORE the sibling F-UX1 fan-out so
    # the two slot families fire in lifecycle order (turn-start → prompt-
    # submit). Triple-gated + fail-open by lifecycle_audit; OFF path is
    # byte-identical.
    await _maybe_run_before_turn_start_audit(ctx)

    # PR-F-EXEC1: shell_command fan-out at ``before_turn_start``. Audit-only;
    # threads the shared per-turn budget (cross-slot decrement). Triple-gated
    # + fail-open via the helper; OFF path is byte-identical.
    await _maybe_run_shell_command_at_before_turn_start(ctx)

    # PR-F-LIFE4a: ``before_turn_start`` gate consult. Runs immediately
    # AFTER the audit fan-out so the same criterion judge work covers both
    # the audit ledger and the gate decision. On ``"block"`` the funnel
    # short-circuits with a synthetic terminal EngineResult BEFORE the
    # engine stream is started; ``"ask"`` is honest-degrade today (logged
    # as requires_approval, turn still proceeds). Fail-open: any exception
    # in the gate path returns ``"proceed"`` so a misbehaving rule cannot
    # wedge a turn.
    blocked = await _maybe_run_before_turn_start_gate(ctx)
    if blocked is not None:
        yield blocked
        _maybe_reset_shell_budget_identity(_shell_identity_token)
        return

    # PR-F-UX1: Tier 2 ``on_user_prompt_submit`` audit-only fan-out. Wired at
    # the TOP of the canonical CLI/serve/child funnel (BEFORE the engine
    # stream starts) so it runs on every real governed turn. Triple-gated +
    # fail-open: returns silently when the master flag is OFF, when no rules
    # are authored, or on any error path — never blocks turn execution.
    await _maybe_run_user_prompt_submit_audit(ctx)

    # PR-F-EXEC1: shell_command fan-out at ``on_user_prompt_submit``. Audit-
    # only; shares the per-turn budget with sibling shell fan-outs.
    await _maybe_run_shell_command_at_on_user_prompt_submit(ctx)

    # PR-F-LIFE4a: ``on_user_prompt_submit`` gate consult. Same short-
    # circuit pattern as the sibling before_turn_start gate above. Both
    # gates run BEFORE ``rt.engine.run_turn_stream`` is invoked so the
    # block decision can avoid ALL engine-side cost (model call,
    # tool dispatch, etc.) when the criterion fails.
    blocked = await _maybe_run_user_prompt_submit_gate(ctx)
    if blocked is not None:
        yield blocked
        _maybe_reset_shell_budget_identity(_shell_identity_token)
        return

    rt = runtime if runtime is not None else _build_runtime(ctx)
    cancel = cancel if cancel is not None else asyncio.Event()

    # Evidence-grounded lifecycle audit: the same per-turn evidence source the
    # enforcement path uses (``LocalToolEvidenceCollector.collect_for_turn``,
    # wired into the engine driver in cli/wiring.py). Threaded into the
    # turn-boundary audit collectors so an ``after_turn_end`` /
    # ``on_subagent_stop`` llm_criterion that declares ``evidenceRefs`` is
    # judged against the records captured this turn. ``None`` (no runtime
    # collector) keeps the audit judge evidence-blind (byte-identical).
    _lifecycle_evidence_collector = getattr(
        getattr(rt, "local_tool_evidence", None), "collect_for_turn", None
    )
    stream = rt.engine.run_turn_stream(  # type: ignore[union-attr]
        None,
        ctx.to_turn_input(),
        cancel=cancel,
        gate=getattr(rt, "gate", None),
    )

    # Run-share bookend persistence (default-OFF). When the flag is OFF we skip
    # ALL accumulation below so the OFF path is byte-identical and zero-cost.
    bookend = _BookendCollector.maybe_create(ctx)

    # PR-F-UX1: Tier 2 ``on_subagent_stop`` final-text collector. Only created
    # for CHILD turns (``ctx.depth > 0``) when the master flag resolves ON so
    # the parent / top-level turn path stays byte-identical and zero-cost.
    subagent_collector = _SubagentStopCollector.maybe_create(
        ctx, evidence_collector=_lifecycle_evidence_collector
    )

    # PR-F-LIFE1: Tier 2 ``after_turn_end`` final-text collector. Distinct
    # from _SubagentStopCollector — this one ONLY fires for TOP-LEVEL turns
    # (``ctx.depth == 0``), so the two collectors do not overlap. Both share
    # the same response_clear / text_delta aggregation pattern.
    turn_end_collector = _AfterTurnEndCollector.maybe_create(
        ctx, evidence_collector=_lifecycle_evidence_collector
    )

    # PR-F-LIFE4b: Tier 2 ``on_task_complete`` final-text collector.
    # Distinct from _AfterTurnEndCollector (which fires every top-level
    # turn boundary) — this collector ONLY fires the audit when the
    # final assistant text carries a multi-turn-task-done signal
    # (``<task_done>`` marker). Honest-degrade: if no signal is
    # detectable, the emitter never fires (no false positives on every
    # turn end).
    task_complete_collector = _OnTaskCompleteCollector.maybe_create(ctx)

    # PR-F-EXEC1 shell_command lifecycle: TOP-LEVEL ``after_turn_end``
    # shell collector. Mirrors _AfterTurnEndCollector — fires shell_command
    # rules at the ``after_turn_end`` slot on top-level turn boundaries.
    shell_after_turn_end_collector = _ShellAfterTurnEndCollector.maybe_create(ctx)
    shell_pre_final_collector = _ShellPreFinalCollector.maybe_create(ctx)
    # PR-F-EXEC2: pre_final shell_check (verifier) collector, sibling of
    # the F-EXEC1 shell_command collector above. Both run on the FIRST
    # EngineResult so an operator authoring a mix of action + condition
    # rules at the same slot gets both verifiers; either ``block`` verdict
    # short-circuits to a synthetic policy-blocked terminal.
    shell_check_pre_final_collector = _ShellCheckPreFinalCollector.maybe_create(ctx)

    # PR-F-EXEC1 shell_command lifecycle: CHILD-only ``on_subagent_stop``
    # shell collector. Mirrors _SubagentStopCollector — fires shell_command
    # rules at ``on_subagent_stop`` for spawned child turns.
    shell_subagent_stop_collector = _ShellSubagentStopCollector.maybe_create(ctx)

    # PR-1: bounded-cadence stream-yield trace for the silent anthropic /
    # google dispatch hunt. Gated by MAGI_CHILD_RUNNER_EMPTY_DEBUG via the
    # helper imported below (we keep the trace surface in ONE module: see
    # child_runner_live._maybe_log_trace_engine_stream_yield). Cadence:
    # first five yields + the LAST one. Operator sees the early shape and
    # the terminal item without drowning in deltas. A zero-yield turn
    # (Kevin's anthropic/google 100~250ms case) will print ZERO
    # ``stream_yield`` lines even though ``drive_one_turn_enter`` and
    # ``drive_one_turn_exit`` will bookend it.
    import os as _os  # noqa: PLC0415

    from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
        _maybe_log_trace_engine_stream_yield,
        _maybe_log_trace_governed_yield_loop_exception,
        _maybe_log_trace_governed_yield_loop_exit,
    )

    _trace_env = _os.environ
    _stream_index = 0
    _last_index = -1
    _last_kind: object = None
    _last_has_text_delta = False
    _last_evidence_refs = 0
    # PR-H: track loop exit reason ("normal" vs "exception") and the
    # number of items yielded so the operator can distinguish
    # "stream ended cleanly but no terminal" from "loop raised mid-flight".
    _yield_loop_exit_reason = "normal"
    _items_yielded = 0
    try:
        async for item in stream:
            if bookend is not None:
                bookend.observe(item)
            if subagent_collector is not None:
                subagent_collector.observe(item)
            if turn_end_collector is not None:
                turn_end_collector.observe(item)
            if task_complete_collector is not None:
                task_complete_collector.observe(item)
            if shell_subagent_stop_collector is not None:
                shell_subagent_stop_collector.observe(item)
            if shell_after_turn_end_collector is not None:
                shell_after_turn_end_collector.observe(item)
            if shell_pre_final_collector is not None:
                shell_pre_final_collector.observe(item)
            if shell_check_pre_final_collector is not None:
                shell_check_pre_final_collector.observe(item)
            # PR-F-EXEC1/F-EXEC2: pre_final shell gate. Fires on the FIRST
            # sight of an EngineResult — the synthesis pass is about to
            # commit. If the operator's pre_final shell_command rule with
            # action=block exits non-zero, OR the shell_check rule reports
            # passed=false, replace the item with a synthetic
            # ``customize_policy_blocked`` terminal so downstream consumers
            # (CLI REPL, serve transport, telemetry) see the block
            # honestly. F-EXEC1 is checked FIRST so its semantics win when
            # both fire on the same EngineResult.
            replaced = None
            if shell_pre_final_collector is not None:
                replaced = await shell_pre_final_collector.maybe_replace(item, ctx)
            if replaced is None and shell_check_pre_final_collector is not None:
                replaced = await shell_check_pre_final_collector.maybe_replace(
                    item, ctx
                )
            _items_yielded += 1
            yield replaced if replaced is not None else item
    except Exception as exc:
        # PR-K: dedicated Exception branch (more specific than the prior
        # BaseException catch) so we can surface the actual exception
        # class + a sanitised first-80 chars of the message BEFORE the
        # re-raise. The PR-H ``yield_loop_exit`` finalize line only said
        # ``reason=exception``; this stamp closes the diagnostic gap by
        # naming WHAT raised. Re-raises so control flow is unchanged.
        _yield_loop_exit_reason = "exception"
        _maybe_log_trace_governed_yield_loop_exception(
            _trace_env, exception=exc
        )
        raise
    except BaseException:
        # PR-H: any non-normal exit (Exception OR CancelledError /
        # GeneratorExit) is recorded as "exception" for the finalize
        # trace. The re-raise preserves prior behavior; this branch only
        # flips the trace reason. CancelledError / GeneratorExit fall
        # through HERE (the PR-K Exception branch above caught real
        # Exception subclasses); they intentionally do NOT emit the
        # yield_loop_exception stamp because they are normal cancellation
        # / cleanup signals, not dispatch failures.
        _yield_loop_exit_reason = "exception"
        raise
    finally:
        # Emit the LAST yield separately when the stream produced more than
        # the first-five window already covered. Avoids a double-log on
        # short (<= 5) streams.
        if _last_index >= 5:
            _maybe_log_trace_engine_stream_yield(
                _trace_env,
                index=_last_index,
                kind=_last_kind,
                has_text_delta=_last_has_text_delta,
                evidence_refs_in_payload=_last_evidence_refs,
            )
        # PR-H: stamp the run_governed_turn finally so the operator can
        # see whether the yield loop completed normally or raised, and
        # how many items it forwarded before exiting. Default-OFF.
        _maybe_log_trace_governed_yield_loop_exit(
            _trace_env,
            reason=_yield_loop_exit_reason,
            items_yielded=_items_yielded,
        )
        if bookend is not None:
            bookend.persist()
        if subagent_collector is not None:
            await subagent_collector.run_audit()
        if turn_end_collector is not None:
            await turn_end_collector.run_audit()
        if task_complete_collector is not None:
            await task_complete_collector.run_audit()
        if shell_subagent_stop_collector is not None:
            await shell_subagent_stop_collector.run_audit()
        if shell_after_turn_end_collector is not None:
            await shell_after_turn_end_collector.run_audit()
        _maybe_reset_shell_budget_identity(_shell_identity_token)


def _describe_stream_item(item: object) -> tuple[object, bool, int]:
    """PR-1: extract a small (kind, has_text_delta, evidence_refs_count) tuple
    from an engine stream item for the ``stream_yield`` trace stamp.

    Fail-soft: on any malformed item the helper returns
    ``(None, False, 0)`` so the trace can NEVER break a turn.
    """
    try:
        payload = getattr(item, "payload", None)
        if not isinstance(payload, dict):
            return (type(item).__name__, False, 0)
        kind = payload.get("type")
        text_delta = payload.get("text_delta")
        if text_delta is None:
            text_delta = payload.get("delta")
        has_text_delta = isinstance(text_delta, str) and bool(text_delta)
        refs = payload.get("evidence_refs") or payload.get("evidenceRefs")
        refs_count = len(refs) if isinstance(refs, (list, tuple)) else 0
        return (kind, has_text_delta, refs_count)
    except Exception:  # noqa: BLE001
        return (None, False, 0)


class _BookendCollector:
    """Accumulates a turn's human-facing bookends off the event stream and
    persists ONE record to the durable evidence ledger when the turn ends.

    Created only when ``MAGI_PERSIST_RUN_BOOKENDS_ENABLED`` is on. Fully
    fail-open: any error in observe/persist is swallowed so a turn never breaks
    because of bookend bookkeeping.

    PR-U: multi-attempt turns emit multiple ``text_delta`` blocks separated by
    ``turn_end`` (or, in the tool-loop preliminary shape, ``tool_end``). The
    deferred boundary flag ensures ``_result_text`` reflects the FINAL block
    only, not the concatenation. See
    :mod:`magi_agent.runtime.child_governed_collector` module docstring for
    the full rationale.
    """

    __slots__ = ("_ctx", "_result_text", "_terminal", "_boundary_pending")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""
        self._terminal: object | None = None
        self._boundary_pending = False

    @classmethod
    def maybe_create(cls, ctx: TurnContext) -> "_BookendCollector | None":
        try:
            from magi_agent.config.env import is_persist_run_bookends_enabled

            if not is_persist_run_bookends_enabled():
                return None
            return cls(ctx)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            from magi_agent.engine.contracts import EngineResult

            if isinstance(item, EngineResult):
                self._terminal = item
                return
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
                self._boundary_pending = False
            elif kind == "turn_end" or kind == "tool_end":
                # PR-U: deferred response-block boundary. Clearing waits
                # for the next non-empty text_delta so a boundary with no
                # trailing text keeps the prior answer intact (fail-soft).
                self._boundary_pending = True
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    if self._boundary_pending:
                        self._result_text = ""
                        self._boundary_pending = False
                    self._result_text += delta
        except Exception:
            return

    def persist(self) -> None:
        try:
            from magi_agent.evidence.ledger_store import (
                resolve_evidence_ledger_dir,
                write_evidence_records,
            )
            from magi_agent.evidence.run_bookend import build_run_bookend_record

            base_dir = resolve_evidence_ledger_dir()
            if base_dir is None:  # durable sink disabled — nothing to do.
                return

            terminal = self._terminal
            usage = getattr(terminal, "usage", None)
            usage = usage if isinstance(usage, dict) else {}
            # EngineResult.usage carries ADK snake_case keys (input_tokens /
            # output_tokens), summed by engine._fold_usage from
            # _adk_usage_metadata. The builder re-emits them as camelCase.
            terminal_value = getattr(getattr(terminal, "terminal", None), "value", None)
            status = "ok" if terminal_value == "completed" else (terminal_value or "unknown")

            record = build_run_bookend_record(
                session_id=self._ctx.session_id,
                turn_id=self._ctx.turn_id,
                goal=self._ctx.prompt,
                result=self._result_text or None,
                status=status,
                model=self._ctx.model,
                provider=self._ctx.provider,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cost_usd=getattr(terminal, "cost_usd", None),
            )
            write_evidence_records(
                base_dir,
                session_id=self._ctx.session_id,
                turn_id=self._ctx.turn_id,
                records=[record],
            )
        except Exception:
            return


def _maybe_apply_customize_budgets() -> None:
    """PR-F7 applier hook called at the top of every governed turn.

    Loads the persisted Customize overrides, builds a resolved
    :class:`CustomizeVerificationPolicy`, and projects each authored budget
    onto ``os.environ`` via ``setdefault``. Triple-gated by
    :func:`magi_agent.customize.budgets_apply.apply_budgets_if_enabled`; the
    flag-OFF path bails before any I/O so the OFF behavior is byte-identical.
    All exceptions are swallowed — a broken customize.json must never break
    a turn.
    """
    try:
        import os  # noqa: PLC0415

        from magi_agent.customize.budgets_apply import (  # noqa: PLC0415
            apply_budgets_if_enabled,
        )
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        apply_budgets_if_enabled(env=os.environ, policy=policy)
    except Exception:
        return


def _build_lifecycle_critic_factory() -> object | None:
    """Build the Haiku-class critic model factory used by both Tier 2 audit
    fan-outs. Reuses the same constructor the engine uses for built-in /
    Customize llm_criterion rules (``cli.wiring._build_criterion_model_factory``).
    Returns ``None`` (and the audit fan-out then records ``status="skipped"``
    with reason ``no critic model available``) when the egress / custom-rules
    flags are off, when the provider key is missing, or on any import error.
    """
    try:
        from magi_agent.cli.wiring import (  # noqa: PLC0415
            _build_criterion_model_factory,
        )

        return _build_criterion_model_factory()
    except Exception:
        return None


async def _maybe_run_managed_credit_precheck(ctx: TurnContext) -> object | None:
    """Managed-inference turn-boundary credit gate.

    Returns a synthetic terminal ``EngineResult`` when the subscriber's balance
    cannot cover another turn, else ``None`` (turn proceeds). Inert for every
    non-managed caller. Fail-open on any error: a missing dep, unreachable
    api-proxy, or unexpected shape never blocks a turn — the api-proxy enforces
    the real per-request credit limit.
    """
    try:
        import os  # noqa: PLC0415

        from magi_agent.runtime.managed_credit_precheck import (  # noqa: PLC0415
            check_managed_credit_balance,
            resolve_managed_precheck_config,
        )

        config = resolve_managed_precheck_config(os.environ)
        if config is None:
            return None

        decision = await check_managed_credit_balance(config=config)
        if decision.block:
            return _build_policy_blocked_terminal(
                ctx=ctx,
                slot="managed_credit_precheck",
                reason="insufficient_credits",
            )
    except Exception:  # noqa: BLE001 — never wedge a turn on the pre-check path
        return None
    return None


def _build_policy_blocked_terminal(*, ctx: TurnContext, slot: str, reason: str) -> object:
    """PR-F-LIFE4a — synthesize a terminal ``EngineResult`` for a policy block.

    Returns an ``EngineResult(terminal=Terminal.aborted)`` with an error
    string identifying the blocking slot + reason so downstream consumers
    (CLI REPL, serve transport, telemetry) can render the block honestly.
    Mirrors the engine's own abort-path terminal shape so emit-side parsing
    is unchanged.
    """
    from magi_agent.engine.contracts import EngineResult, Terminal  # noqa: PLC0415

    return EngineResult(
        terminal=Terminal.aborted,
        usage={},
        cost_usd=0.0,
        error=f"customize_policy_blocked: slot={slot}; reason={reason}",
        session_id=ctx.session_id,
        turn_id=ctx.turn_id,
    )


async def _maybe_run_before_turn_start_gate(ctx: TurnContext) -> object | None:
    """PR-F-LIFE4a Tier 2 ``before_turn_start`` gate consult.

    Returns a synthetic terminal ``EngineResult`` to short-circuit the
    funnel when any block-action criterion fails; otherwise returns ``None``
    so the caller proceeds. Fail-open at every layer — never raises.
    """
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            lifecycle_turn_hooks_enabled,
            run_before_turn_start_gate,
        )

        if not lifecycle_turn_hooks_enabled():
            return None
        verdict = await run_before_turn_start_gate(
            prompt_text=ctx.prompt or "",
            model_factory=_build_lifecycle_critic_factory(),
        )
        if verdict == "block":
            return _build_policy_blocked_terminal(
                ctx=ctx,
                slot="before_turn_start",
                reason="llm_criterion verdict=block",
            )
        # "ask" is honest-degrade in v1: the audit ledger captures the
        # requires_approval flag (follow-up PR adds the surface); the turn
        # still proceeds so authoring an ask rule does not silently brick
        # the runtime.
        return None
    except Exception:
        return None


async def _maybe_run_user_prompt_submit_gate(ctx: TurnContext) -> object | None:
    """PR-F-LIFE4a Tier 2 ``on_user_prompt_submit`` gate consult.

    Mirrors :func:`_maybe_run_before_turn_start_gate` (same short-circuit
    pattern, different fires-at slot). Triple-gated by
    :func:`lifecycle_expansion_enabled` (the F-UX1 master flag covers both
    the audit and the gate consult for this slot).
    """
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            lifecycle_expansion_enabled,
            run_user_prompt_submit_gate,
        )

        if not lifecycle_expansion_enabled():
            return None
        verdict = await run_user_prompt_submit_gate(
            prompt_text=ctx.prompt or "",
            model_factory=_build_lifecycle_critic_factory(),
        )
        if verdict == "block":
            return _build_policy_blocked_terminal(
                ctx=ctx,
                slot="on_user_prompt_submit",
                reason="llm_criterion verdict=block",
            )
        return None
    except Exception:
        return None


async def _maybe_run_user_prompt_submit_audit(ctx: TurnContext) -> None:
    """PR-F-UX1 Tier 2 ``on_user_prompt_submit`` audit-only fan-out.

    Invoked at the TOP of :func:`run_governed_turn`. Triple-gated +
    fail-open: bails silently when the master flag resolves OFF, when no
    matching rules are authored, or on any exception. Never mutates
    ``ctx`` and never blocks turn execution — the audit verdicts are
    recorded by the lifecycle_audit module and discarded by this wire (a
    later PR will route them to a durable evidence sink).

    Threads the same critic model factory the engine builds for built-in
    llm_criterion rules so the audit actually invokes the judge when an
    operator has authored a rule + flipped the master flag + has a
    provider key configured.
    """
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            lifecycle_expansion_enabled,
            run_user_prompt_submit_audit,
        )

        if not lifecycle_expansion_enabled():
            return
        await run_user_prompt_submit_audit(
            prompt_text=ctx.prompt or "",
            model_factory=_build_lifecycle_critic_factory(),
        )
    except Exception:
        return


def _collect_lifecycle_evidence_records(
    evidence_collector: object | None, ctx: TurnContext
) -> tuple[object, ...] | None:
    """Collect this turn's evidence records for the lifecycle audit judge.

    Reuses the enforcement path's ``collect_for_turn`` callable. Gated on
    ``MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED`` so the OFF path never touches the
    collector (zero-cost, byte-identical); the audit wrapper still only projects
    when a rule declares ``evidenceRefs``. Fully fail-open: any fault returns
    ``None`` (evidence-blind judge) rather than perturbing the turn.
    """
    try:
        if evidence_collector is None or not callable(evidence_collector):
            return None
        from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

        if not flag_profile_bool("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED"):
            return None
        turn_id = getattr(ctx, "turn_id", None)
        if not isinstance(turn_id, str) or not turn_id:
            return None
        return tuple(evidence_collector(turn_id))
    except Exception:
        return None


class _SubagentStopCollector:
    """Accumulates the child's final assistant text off the event stream and
    runs the ``on_subagent_stop`` audit fan-out at turn end.

    Created only for CHILD turns (``ctx.depth > 0``) when the master flag
    resolves ON, so the parent / top-level path stays byte-identical and
    zero-cost. Reuses the same text-aggregation pattern as
    :class:`_BookendCollector` (``response_clear`` / ``text_delta`` events).
    Fully fail-open: any observation or audit error is swallowed so a
    governed turn never breaks because of audit bookkeeping.
    """

    __slots__ = ("_ctx", "_result_text", "_evidence_collector", "_boundary_pending")

    def __init__(
        self, ctx: TurnContext, evidence_collector: object | None = None
    ) -> None:
        self._ctx = ctx
        self._result_text = ""
        self._evidence_collector = evidence_collector
        self._boundary_pending = False

    @classmethod
    def maybe_create(
        cls, ctx: TurnContext, *, evidence_collector: object | None = None
    ) -> "_SubagentStopCollector | None":
        # Top-level turns (depth == 0) are NOT subagent turns — the
        # ``on_subagent_stop`` slot fires only for spawned child agents.
        if getattr(ctx, "depth", 0) <= 0:
            return None
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                lifecycle_expansion_enabled,
            )

            if not lifecycle_expansion_enabled():
                return None
            return cls(ctx, evidence_collector=evidence_collector)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
                self._boundary_pending = False
            elif kind == "turn_end" or kind == "tool_end":
                # PR-U: deferred boundary. See _BookendCollector.observe.
                self._boundary_pending = True
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    if self._boundary_pending:
                        self._result_text = ""
                        self._boundary_pending = False
                    self._result_text += delta
        except Exception:
            return

    async def run_audit(self) -> None:
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                run_subagent_stop_audit,
            )

            await run_subagent_stop_audit(
                final_text=self._result_text,
                model_factory=_build_lifecycle_critic_factory(),
                evidence_records=_collect_lifecycle_evidence_records(
                    self._evidence_collector, self._ctx
                ),
            )
        except Exception:
            return


async def _maybe_run_before_turn_start_audit(ctx: TurnContext) -> None:
    """PR-F-LIFE1 Tier 2 ``before_turn_start`` audit-only fan-out.

    Invoked at the TOP of :func:`run_governed_turn`, BEFORE the sibling
    ``on_user_prompt_submit`` fan-out so the two slot families fire in
    lifecycle order. Triple-gated + fail-open via
    :func:`lifecycle_turn_hooks_enabled`: bails silently when the F-LIFE1
    master flag resolves OFF, when no matching rules are authored, or on any
    exception. Never mutates ``ctx`` and never blocks turn execution — the
    audit verdicts are recorded by the lifecycle_audit module and discarded
    by this wire (a later PR will route them to a durable evidence sink).
    """
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            lifecycle_turn_hooks_enabled,
            run_before_turn_start_audit,
        )

        if not lifecycle_turn_hooks_enabled():
            return
        await run_before_turn_start_audit(
            prompt_text=ctx.prompt or "",
            model_factory=_build_lifecycle_critic_factory(),
        )
    except Exception:
        return


class _AfterTurnEndCollector:
    """PR-F-LIFE1 — accumulates the TOP-LEVEL turn's final assistant text off
    the event stream and runs the ``after_turn_end`` audit fan-out at turn
    end.

    Mirrors :class:`_SubagentStopCollector` exactly, only inverted on the
    ``ctx.depth`` axis: this collector is created ONLY for top-level turns
    (``ctx.depth == 0``) when the F-LIFE1 master flag resolves ON, so the
    sibling on_subagent_stop / after_turn_end fan-outs never overlap.
    Fully fail-open: any observation or audit error is swallowed so a
    governed turn never breaks because of audit bookkeeping.
    """

    __slots__ = ("_ctx", "_result_text", "_evidence_collector", "_boundary_pending")

    def __init__(
        self, ctx: TurnContext, evidence_collector: object | None = None
    ) -> None:
        self._ctx = ctx
        self._result_text = ""
        self._evidence_collector = evidence_collector
        self._boundary_pending = False

    @classmethod
    def maybe_create(
        cls, ctx: TurnContext, *, evidence_collector: object | None = None
    ) -> "_AfterTurnEndCollector | None":
        # Only the TOP-LEVEL turn (depth == 0) emits after_turn_end. Child
        # turns are covered by _SubagentStopCollector so the two slots stay
        # disjoint.
        if getattr(ctx, "depth", 0) > 0:
            return None
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                lifecycle_turn_hooks_enabled,
            )

            if not lifecycle_turn_hooks_enabled():
                return None
            return cls(ctx, evidence_collector=evidence_collector)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
                self._boundary_pending = False
            elif kind == "turn_end" or kind == "tool_end":
                # PR-U: deferred boundary. See _BookendCollector.observe.
                self._boundary_pending = True
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    if self._boundary_pending:
                        self._result_text = ""
                        self._boundary_pending = False
                    self._result_text += delta
        except Exception:
            return

    async def run_audit(self) -> None:
        # PR-H: stamp the after_turn_end audit fan-out. Confirms the
        # collector actually ran (vs being skipped at maybe_create time)
        # AND surfaces the accumulated result_text length so the operator
        # can see whether the top-level turn produced any final text at
        # all. Logged BEFORE the audit call so the line lands even if
        # the audit fan-out raises. Default-OFF.
        try:
            import os as _os  # noqa: PLC0415

            from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
                _maybe_log_trace_governed_turn_end_audit_fired,
            )

            _maybe_log_trace_governed_turn_end_audit_fired(
                _os.environ,
                session_id=getattr(self._ctx, "session_id", None),
                result_text_len=len(self._result_text),
            )
        except Exception:  # noqa: BLE001 - trace logging never breaks a turn.
            pass
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                run_after_turn_end_audit,
            )

            await run_after_turn_end_audit(
                final_text=self._result_text,
                model_factory=_build_lifecycle_critic_factory(),
                evidence_records=_collect_lifecycle_evidence_records(
                    self._evidence_collector, self._ctx
                ),
            )
        except Exception:
            return


#: PR-F-LIFE4b — substring marker the agent emits in its FINAL assistant
#: text to declare a multi-turn user task done. Cheap detector: a plain
#: regex matched against the aggregated turn text. PR-F-LIFE4b review
#: pass: the marker is LINE-ANCHORED (must occupy a line on its own,
#: trailing whitespace allowed) so prose mentioning the literal string
#: (e.g. "ask me about the <task_done> marker", pasted docs, chat
#: history) does not stale-fire the audit. The marker is opt-in for the
#: agent — if the agent never emits it on its own line the
#: on_task_complete slot never fires (honest-degrade: no false positives
#: on every-turn-end).
_TASK_DONE_MARKER_RE = re.compile(r"(?m)^\s*<task_done>\s*$")


class _OnTaskCompleteCollector:
    """PR-F-LIFE4b — accumulates the TOP-LEVEL turn's final assistant
    text and fires the ``on_task_complete`` audit at turn end IFF the
    text carries a multi-turn-task-done signal.

    Signal sources (v1):
    * The aggregated final assistant text contains the substring
      :data:`_TASK_DONE_MARKER` (``<task_done>``). The agent emits this
      marker explicitly when it judges the user's multi-turn task done.

    Created ONLY for top-level turns (``ctx.depth == 0``) when the
    F-LIFE4b master flag resolves ON, so child turns / OFF callers stay
    byte-identical and zero-cost. Reuses the same text-aggregation
    pattern as :class:`_BookendCollector` (``response_clear`` /
    ``text_delta`` events).

    Honest-degrade: when no signal is detectable in the aggregated text
    the ``run_audit`` body short-circuits and the audit ledger stays
    silent — operators authoring at this slot get no false positives
    on every-turn-end.

    Fully fail-open: any observation or audit error is swallowed so a
    governed turn never breaks because of audit bookkeeping.
    """

    __slots__ = ("_ctx", "_result_text", "_boundary_pending")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""
        self._boundary_pending = False

    @classmethod
    def maybe_create(cls, ctx: TurnContext) -> "_OnTaskCompleteCollector | None":
        # Only the TOP-LEVEL turn (depth == 0) emits on_task_complete.
        # Child turns are covered by _SubagentStopCollector so the two
        # slots stay disjoint.
        if getattr(ctx, "depth", 0) > 0:
            return None
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                session_task_emitters_enabled,
            )

            if not session_task_emitters_enabled():
                return None
            return cls(ctx)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
                self._boundary_pending = False
            elif kind == "turn_end" or kind == "tool_end":
                # PR-U: deferred boundary. See _BookendCollector.observe.
                self._boundary_pending = True
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    if self._boundary_pending:
                        self._result_text = ""
                        self._boundary_pending = False
                    self._result_text += delta
        except Exception:
            return

    def _signal_present(self) -> bool:
        """Return True iff the aggregated text declares task completion.

        v1: line-anchored regex match on :data:`_TASK_DONE_MARKER_RE`.
        The marker must occupy its own line (trailing whitespace allowed)
        so prose mentioning the literal string does not stale-fire. The
        marker is opt-in for the agent; absence is the honest "no signal,
        no fire" branch — see the class docstring.
        """
        return bool(_TASK_DONE_MARKER_RE.search(self._result_text or ""))

    async def run_audit(self) -> None:
        try:
            if not self._signal_present():
                # Honest-degrade: no signal, no emit. Operators get
                # audit-empty silence rather than false positives on
                # every turn end.
                return
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                derive_gate_verdict_from_audits,
                run_task_complete_audit,
                session_task_emitters_enabled,
            )

            audits = await run_task_complete_audit(
                final_text=self._result_text,
                model_factory=_build_lifecycle_critic_factory(),
            )
            # PR-F-LIFE4b review pass: _LEGAL exposes {audit, block,
            # ask_approval} at on_task_complete. Without a gate consult
            # the block / ask rules would silently behave like audit
            # (false promise of authorability). Derive the verdict from
            # the existing audits — no second criterion-judge call. ask
            # is honest-degrade today (proceeds + records
            # requires_approval=true in the audit ledger; approval
            # surfacing follows). block at this slot has no
            # post-emission revert wire yet, so the verdict is recorded
            # but the turn is not rolled back — the rule's verdict is
            # surfaced to the operator via the audit ledger.
            try:
                gate_verdict = derive_gate_verdict_from_audits(
                    audits,
                    fires_at="on_task_complete",
                    allowed_actions=frozenset({"block", "ask_approval"}),
                    enabled_fn=session_task_emitters_enabled,
                )
                if gate_verdict in {"block", "ask"}:
                    # Annotate the ledger so the follow-up approval
                    # surface (dashboard prompts, push notifications)
                    # can find these entries. Today this is observability
                    # only — no compensating-action wire ships in v1.
                    for audit in audits:
                        if isinstance(audit, dict):
                            audit["requires_approval"] = gate_verdict == "ask"
                            audit["gate_verdict"] = gate_verdict
            except Exception:
                pass
        except Exception:
            return


# ---------------------------------------------------------------------------
# PR-F-EXEC1: shell_command lifecycle fan-out helpers wired into run_governed_turn
# ---------------------------------------------------------------------------


def _maybe_set_shell_budget_identity(ctx: TurnContext) -> object | None:
    """Publish ``(ctx.session_id, ctx.turn_id)`` for the shell budget ContextVar.

    Returns the ContextVar reset token (or ``None`` on any import / set
    failure) so the caller can pair this with :func:`_maybe_reset_shell_budget_identity`
    in a finally block. Fail-open: never raises out of the governed-turn
    entry path.
    """
    try:
        from magi_agent.adk_bridge.lifecycle_shell_command_control import (  # noqa: PLC0415
            set_active_turn_identity,
        )

        return set_active_turn_identity(
            getattr(ctx, "session_id", None) or None,
            getattr(ctx, "turn_id", None) or None,
        )
    except Exception:
        return None


def _maybe_reset_shell_budget_identity(token: object | None) -> None:
    """Restore the shell budget identity ContextVar to its prior value."""
    if token is None:
        return
    try:
        from magi_agent.adk_bridge.lifecycle_shell_command_control import (  # noqa: PLC0415
            reset_active_turn_identity,
        )

        reset_active_turn_identity(token)  # type: ignore[arg-type]
    except Exception:
        return


def _resolve_shell_budget_for_ctx(
    ctx: TurnContext,
) -> tuple[int | None, "Callable[[], None]"]:
    """Resolve ``(remaining, decrement_fn)`` for the active turn.

    Returns ``(None, no_op)`` when the master flag is OFF or when identity
    cannot be resolved — fan-out helpers treat ``None`` as "no cap"
    (byte-identical to today). Fail-open: any exception returns the
    no-op tuple so a misbehaving budget reader cannot wedge a turn.
    """
    try:
        from magi_agent.adk_bridge.lifecycle_shell_command_control import (  # noqa: PLC0415
            shell_budget_for,
        )

        return shell_budget_for(
            getattr(ctx, "session_id", None) or None,
            getattr(ctx, "turn_id", None) or None,
        )
    except Exception:
        return (None, _shell_budget_noop)


def _shell_budget_noop() -> None:
    """No-op decrement used when the shell budget surface is unavailable."""
    return None


async def _maybe_run_shell_command_at_before_turn_start(ctx: TurnContext) -> None:
    """Fan-out helper: ``firesAt == "before_turn_start"`` shell_command rules."""
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            run_shell_command_at_before_turn_start,
            shell_command_enabled,
        )

        if not shell_command_enabled():
            return
        remaining, decrement_fn = _resolve_shell_budget_for_ctx(ctx)
        await run_shell_command_at_before_turn_start(
            prompt_text=ctx.prompt or "",
            remaining_budget=remaining,
            decrement_fn=decrement_fn,
        )
    except Exception:
        return


async def _maybe_run_shell_command_at_on_user_prompt_submit(ctx: TurnContext) -> None:
    """Fan-out helper: ``firesAt == "on_user_prompt_submit"`` shell_command rules."""
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            run_shell_command_at_on_user_prompt_submit,
            shell_command_enabled,
        )

        if not shell_command_enabled():
            return
        remaining, decrement_fn = _resolve_shell_budget_for_ctx(ctx)
        await run_shell_command_at_on_user_prompt_submit(
            prompt_text=ctx.prompt or "",
            remaining_budget=remaining,
            decrement_fn=decrement_fn,
        )
    except Exception:
        return


class _ShellAfterTurnEndCollector:
    """Accumulates the TOP-LEVEL turn's final assistant text and fires the
    ``after_turn_end`` shell_command fan-out at turn end.

    Mirrors :class:`_AfterTurnEndCollector` exactly — top-level turns only
    (``ctx.depth == 0``). Fully fail-open: any observation or fan-out error
    is swallowed so a governed turn never breaks because of shell hook
    bookkeeping.
    """

    __slots__ = ("_ctx", "_result_text")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""

    @classmethod
    def maybe_create(cls, ctx: TurnContext) -> "_ShellAfterTurnEndCollector | None":
        if getattr(ctx, "depth", 0) > 0:
            return None
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                shell_command_enabled,
            )

            if not shell_command_enabled():
                return None
            return cls(ctx)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str):
                    self._result_text += delta
        except Exception:
            return

    async def run_audit(self) -> None:
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                run_shell_command_at_after_turn_end,
            )

            remaining, decrement_fn = _resolve_shell_budget_for_ctx(self._ctx)
            await run_shell_command_at_after_turn_end(
                final_text=self._result_text,
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )
        except Exception:
            return


class _ShellSubagentStopCollector:
    """Accumulates the CHILD turn's final assistant text and fires the
    ``on_subagent_stop`` shell_command fan-out at turn end.

    Mirrors :class:`_SubagentStopCollector` exactly — child turns only
    (``ctx.depth > 0``). The two collectors stay disjoint with
    :class:`_ShellAfterTurnEndCollector` so a turn never double-fires both
    slots.
    """

    __slots__ = ("_ctx", "_result_text")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""

    @classmethod
    def maybe_create(cls, ctx: TurnContext) -> "_ShellSubagentStopCollector | None":
        if getattr(ctx, "depth", 0) <= 0:
            return None
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                shell_command_enabled,
            )

            if not shell_command_enabled():
                return None
            return cls(ctx)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str):
                    self._result_text += delta
        except Exception:
            return

    async def run_audit(self) -> None:
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                run_shell_command_at_on_subagent_stop,
            )

            remaining, decrement_fn = _resolve_shell_budget_for_ctx(self._ctx)
            await run_shell_command_at_on_subagent_stop(
                final_text=self._result_text,
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )
        except Exception:
            return


class _ShellPreFinalCollector:
    """Accumulates the in-progress final text and fires the ``pre_final``
    shell_command fan-out on the FIRST sight of an ``EngineResult`` so the
    pre-synthesis gate decision arrives BEFORE the final answer commits.

    Honors ``action == "block"``: when any pre_final rule with
    ``action=block`` exits non-zero, :meth:`maybe_replace` returns a
    synthetic ``customize_policy_blocked`` terminal that the caller yields
    in place of the original EngineResult. Otherwise (proceed verdict)
    returns ``None`` so the caller yields the original item verbatim.

    The audit fan-out is one-shot: only the FIRST EngineResult triggers it
    (subsequent results within the same turn fall through to the original
    yield). Fully fail-open.
    """

    __slots__ = ("_ctx", "_result_text", "_fired")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""
        self._fired = False

    @classmethod
    def maybe_create(cls, ctx: TurnContext) -> "_ShellPreFinalCollector | None":
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                shell_command_enabled,
            )

            if not shell_command_enabled():
                return None
            return cls(ctx)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str):
                    self._result_text += delta
        except Exception:
            return

    async def maybe_replace(
        self, item: object, ctx: TurnContext
    ) -> object | None:
        """If *item* is an EngineResult and pre_final shell verdict is
        ``block``, return a synthetic policy-blocked terminal.

        Otherwise return ``None`` (caller yields the original item).
        """
        if self._fired:
            return None
        try:
            from magi_agent.engine.contracts import EngineResult  # noqa: PLC0415
        except Exception:
            return None
        if not isinstance(item, EngineResult):
            return None
        # One-shot: even if the call below errors / proceeds, we don't
        # re-fire on subsequent EngineResults this turn.
        self._fired = True
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                run_shell_command_at_pre_final,
            )

            remaining, decrement_fn = _resolve_shell_budget_for_ctx(ctx)
            _, verdict = await run_shell_command_at_pre_final(
                draft_text=self._result_text,
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )
            if verdict == "block":
                return _build_policy_blocked_terminal(
                    ctx=ctx,
                    slot="pre_final",
                    reason="shell_command exit_code>0 with action=block",
                )
        except Exception:
            return None
        return None


class _ShellCheckPreFinalCollector:
    """F-EXEC2 sibling of :class:`_ShellPreFinalCollector` for shell_check.

    Accumulates the in-progress final text and fires the ``pre_final``
    shell_check fan-out on the FIRST sight of an ``EngineResult`` so the
    pre-synthesis gate decision arrives BEFORE the final answer commits.

    Honors ``action == "block"``: when any pre_final rule with
    ``action=block`` whose script reports ``passed=false`` (or exits
    non-zero in exit-code fallback mode) wins the verdict reduction,
    :meth:`maybe_replace` returns a synthetic ``customize_policy_blocked``
    terminal that the caller yields in place of the original EngineResult.
    Otherwise (proceed verdict) returns ``None`` so the caller yields the
    original item verbatim.

    Wired in parallel with the F-EXEC1 :class:`_ShellPreFinalCollector` so
    an operator authoring BOTH a shell_command and a shell_check at the
    same slot gets both verifiers; either can short-circuit. Shares the
    per-(session, turn) MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET counter via
    :func:`_resolve_shell_budget_for_ctx` so the cross-kind cap is
    honored even when only one kind is enabled.

    Fully fail-open: any exception in observe / maybe_replace falls back
    to "no replacement" so a turn never breaks because of audit
    bookkeeping.
    """

    __slots__ = ("_ctx", "_result_text", "_fired")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""
        self._fired = False

    @classmethod
    def maybe_create(
        cls, ctx: TurnContext
    ) -> "_ShellCheckPreFinalCollector | None":
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                shell_check_enabled,
            )

            if not shell_check_enabled():
                return None
            return cls(ctx)
        except Exception:
            return None

    def observe(self, item: object) -> None:
        try:
            payload = getattr(item, "payload", None)
            if not isinstance(payload, dict):
                return
            kind = payload.get("type")
            if kind == "response_clear":
                self._result_text = ""
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str):
                    self._result_text += delta
        except Exception:
            return

    async def maybe_replace(
        self, item: object, ctx: TurnContext
    ) -> object | None:
        """If *item* is an EngineResult and pre_final shell_check verdict is
        ``block``, return a synthetic policy-blocked terminal.

        Otherwise return ``None`` (caller yields the original item).
        """
        if self._fired:
            return None
        try:
            from magi_agent.engine.contracts import EngineResult  # noqa: PLC0415
        except Exception:
            return None
        if not isinstance(item, EngineResult):
            return None
        # One-shot: even if the call below errors / proceeds, we don't
        # re-fire on subsequent EngineResults this turn.
        self._fired = True
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                run_shell_check_at_pre_final,
            )

            remaining, decrement_fn = _resolve_shell_budget_for_ctx(ctx)
            _, verdict = await run_shell_check_at_pre_final(
                draft_text=self._result_text,
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )
            if verdict == "block":
                return _build_policy_blocked_terminal(
                    ctx=ctx,
                    slot="pre_final",
                    reason="shell_check passed=false with action=block",
                )
        except Exception:
            return None
        return None


def _build_runtime(ctx: TurnContext) -> object:
    """Build a minimal headless runtime from *ctx* (``runtime=None`` fallback).

    This is exercised by the serve path (Task 1.3) and future child-runner
    paths that do not pre-build a runtime.  The CLI REPL always passes its
    own runtime, so this path is not exercised by the REPL.

    All parameters have defaults in ``build_headless_runtime``; we forward the
    ones that ``TurnContext`` carries.  In particular ``permission_mode`` is
    threaded from ``ctx`` (A-8 fail-closed): the fallback no longer hard-codes
    ``bypassPermissions`` — it defaults to ``ctx.permission_mode`` (``"default"``
    = ask) so serve/child turns stop silently bypassing approvals. Callers that
    need ``cwd``, ``bot_id``, ``owner_user_id``, etc. should build the runtime
    themselves and pass it in.
    """
    from magi_agent.cli.wiring import build_headless_runtime  # local import to avoid circular

    return build_headless_runtime(
        permission_mode=ctx.permission_mode,
        session_id=ctx.session_id,
        model=ctx.model,
        # #1329 regression fix: gate ledger-first auto-continue on depth in the
        # runtime=None fallback. depth>0 is a child / delegated turn that must
        # not auto-continue / self-check-goal; only the top-level turn (depth==0)
        # keeps the parent auto-continue authority (still bounded by
        # MAGI_GOAL_LOOP_ENABLED inside build_headless_runtime).
        auto_continue_allowed=(ctx.depth == 0),
        # Depth>0 (child/subagent) turns are exempt from the finalizer for the
        # same containment reason: a child's structured return must not be
        # overwritten by forced chat text. Top-level turns get the backstop.
        no_tool_finalizer_allowed=(ctx.depth == 0),
    )
