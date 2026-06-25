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
from collections.abc import AsyncGenerator

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

    # PR-F-LIFE1: Tier 2 ``before_turn_start`` audit-only fan-out. Wired at
    # the TOP of the canonical funnel, BEFORE the sibling F-UX1 fan-out so
    # the two slot families fire in lifecycle order (turn-start → prompt-
    # submit). Triple-gated + fail-open by lifecycle_audit; OFF path is
    # byte-identical.
    await _maybe_run_before_turn_start_audit(ctx)

    # PR-F-UX1: Tier 2 ``on_user_prompt_submit`` audit-only fan-out. Wired at
    # the TOP of the canonical CLI/serve/child funnel (BEFORE the engine
    # stream starts) so it runs on every real governed turn. Triple-gated +
    # fail-open: returns silently when the master flag is OFF, when no rules
    # are authored, or on any error path — never blocks turn execution.
    await _maybe_run_user_prompt_submit_audit(ctx)

    rt = runtime if runtime is not None else _build_runtime(ctx)
    cancel = cancel if cancel is not None else asyncio.Event()
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
    subagent_collector = _SubagentStopCollector.maybe_create(ctx)

    # PR-F-LIFE1: Tier 2 ``after_turn_end`` final-text collector. Distinct
    # from _SubagentStopCollector — this one ONLY fires for TOP-LEVEL turns
    # (``ctx.depth == 0``), so the two collectors do not overlap. Both share
    # the same response_clear / text_delta aggregation pattern.
    turn_end_collector = _AfterTurnEndCollector.maybe_create(ctx)

    # PR-F-LIFE4b: Tier 2 ``on_task_complete`` final-text collector.
    # Distinct from _AfterTurnEndCollector (which fires every top-level
    # turn boundary) — this collector ONLY fires the audit when the
    # final assistant text carries a multi-turn-task-done signal
    # (``<task_done>`` marker). Honest-degrade: if no signal is
    # detectable, the emitter never fires (no false positives on every
    # turn end).
    task_complete_collector = _OnTaskCompleteCollector.maybe_create(ctx)

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
            yield item
    finally:
        if bookend is not None:
            bookend.persist()
        if subagent_collector is not None:
            await subagent_collector.run_audit()
        if turn_end_collector is not None:
            await turn_end_collector.run_audit()
        if task_complete_collector is not None:
            await task_complete_collector.run_audit()


class _BookendCollector:
    """Accumulates a turn's human-facing bookends off the event stream and
    persists ONE record to the durable evidence ledger when the turn ends.

    Created only when ``MAGI_PERSIST_RUN_BOOKENDS_ENABLED`` is on. Fully
    fail-open: any error in observe/persist is swallowed so a turn never breaks
    because of bookend bookkeeping.
    """

    __slots__ = ("_ctx", "_result_text", "_terminal")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""
        self._terminal: object | None = None

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
            from magi_agent.cli.contracts import EngineResult

            if isinstance(item, EngineResult):
                self._terminal = item
                return
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

    __slots__ = ("_ctx", "_result_text")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""

    @classmethod
    def maybe_create(cls, ctx: TurnContext) -> "_SubagentStopCollector | None":
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
                run_subagent_stop_audit,
            )

            await run_subagent_stop_audit(
                final_text=self._result_text,
                model_factory=_build_lifecycle_critic_factory(),
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

    __slots__ = ("_ctx", "_result_text")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""

    @classmethod
    def maybe_create(cls, ctx: TurnContext) -> "_AfterTurnEndCollector | None":
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
                run_after_turn_end_audit,
            )

            await run_after_turn_end_audit(
                final_text=self._result_text,
                model_factory=_build_lifecycle_critic_factory(),
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

    __slots__ = ("_ctx", "_result_text")

    def __init__(self, ctx: TurnContext) -> None:
        self._ctx = ctx
        self._result_text = ""

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
            elif kind == "text_delta":
                delta = payload.get("delta")
                if isinstance(delta, str):
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
                            audit["requires_approval"] = (
                                gate_verdict == "ask"
                            )
                            audit["gate_verdict"] = gate_verdict
            except Exception:
                pass
        except Exception:
            return


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
    )
