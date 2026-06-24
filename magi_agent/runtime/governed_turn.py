"""The single primitive every governed turn flows through.

All governed turns — top-level serve requests, CLI REPL turns, and child-agent
turns — funnel through ``run_governed_turn``.  The function accepts an optional
pre-built ``runtime`` so the CLI REPL can reuse its long-lived driver across
turns without rebuilding it per call.  The serve path and child paths pass
``runtime=None`` and receive a fresh runtime from ``_build_runtime``.
"""
from __future__ import annotations

import asyncio
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

    try:
        async for item in stream:
            if bookend is not None:
                bookend.observe(item)
            yield item
    finally:
        if bookend is not None:
            bookend.persist()


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
