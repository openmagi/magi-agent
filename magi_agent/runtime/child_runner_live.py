"""A REAL, model-backed local child runner for the Child Runner boundary.

This module supplies :class:`RealLocalChildRunner` — the genuine, model-backed
child-execution surface that ``LocalChildRunnerBoundary``'s *live* branch
(``liveChildRunnerEnabled`` + a trusted ``openmagi_live_provider`` runner)
admits and drives via ``run_child(request)``.

It reuses the existing in-process turn-execution machinery
(``build_cli_model_runner`` / ``CliModelRunner`` from
``magi_agent.cli.real_runner``) to run ONE sub-agent turn — the SAME seam the
GAIA/discovery harnesses reuse (see ``discovery/orchestrator.drive_runner_once``,
the precedent followed here, including the injectable ``model_factory`` test
seam so tests pass a fake ``BaseLlm`` and NO real model call / API key is made).

Default OFF
-----------
The boundary's ``live_child_runner_enabled`` config flag is the authority gate;
this module additionally exposes a parallel call-time env gate
(``is_live_child_runner_enabled``) mirroring ``file_delivery_live`` so a caller
(Task C ``spawn_agent`` wiring) can decide whether to construct/attach a real
runner at all. Default OFF; the kill-switch wins.

Safety
------
* The boundary (Task A) owns spawn-depth / total-agents / output-ref caps; this
  runner just executes one turn.
* v1 scope: TEXT-ONLY child turn — NO workspace-mutating tools are passed
  (an empty toolset). Tool-enabled children are a follow-up.
* ``run_child`` NEVER raises: every failure path returns a degraded mapping
  (``status="blocked"`` / ``"failed"``) that the boundary then sanitises through
  ``_envelope_from_output`` (so no secrets/paths/raw transcript leak).
* Unknown model route (not in ``ModelTierRegistry``) → blocked.
* No provider key resolvable → blocked (``child_provider_key_missing``); the
  runner is NOT executed.

Import-clean by design
----------------------
No module-top imports of ``litellm`` / ``google.adk`` / heavy runner internals.
``build_cli_model_runner`` / ``resolve_provider_config`` are imported lazily
INSIDE the methods so importing this module stays light and the Task C tool
wiring (``subagents.py``) keeps an import-clean surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import re
from collections.abc import Callable, Mapping
from typing import Any

# PR-G: the trace channel is file-backed (``~/.openmagi/trace.log`` or
# ``MAGI_TRACE_LOG_PATH``). PR #994's ``print(..., file=sys.stderr, flush=True)``
# substrate worked for short sessions but Kevin's 0.1.86 long-running repro
# showed the uvicorn stderr FD wedging mid-session (log mtime froze at
# 21:59:24 while SQLite kept getting writes until 22:04:34). The dedicated
# sink FD is distinct from the uvicorn stdout/stderr handles so a wedged
# uvicorn FD no longer freezes the diagnostic channel.
from magi_agent.runtime.child_missing_tool_guard import (
    MissingToolStreak,
    classify_missing_tool_response,
    resolve_missing_tool_streak_cap,
)
from magi_agent.runtime.trace_sink import _emit_trace

#: Operator opt-in env for verbose child-runner empty-stream diagnostics.
#: Default-OFF. When set to a truthy value, the empty-result and dispatch-
#: trace helpers emit one line per event to the file-backed sink (default
#: ``~/.openmagi/trace.log``; override with ``MAGI_TRACE_LOG_PATH``). Reason
#: it does not use ``logging``: ``magi-serve`` never calls
#: ``logging.basicConfig`` / ``dictConfig``, so a ``_logger.warning(...)``
#: would never reach the operator (exactly the 0.1.84 repro shape). The
#: file-backed sink lives in :mod:`magi_agent.runtime.trace_sink`.
CHILD_RUNNER_EMPTY_DEBUG_ENV = "MAGI_CHILD_RUNNER_EMPTY_DEBUG"


def _empty_debug_enabled(env: Mapping[str, str]) -> bool:
    raw = env.get(CHILD_RUNNER_EMPTY_DEBUG_ENV, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _maybe_log_governed_collect_result(
    env: Mapping[str, str],
    *,
    provider: object,
    model: object,
    summary: str,
    evidence_refs: tuple[str, ...],
    status: str,
) -> None:
    """Surface the governed-branch collector's actual output to the serve log
    so the operator can see whether the empty-response guard about to be
    checked will fire AND, if not, exactly why. Default-OFF; opts in via
    ``MAGI_CHILD_RUNNER_EMPTY_DEBUG``."""
    if not _empty_debug_enabled(env):
        return
    try:
        first_ref = evidence_refs[0] if evidence_refs else None
        _emit_trace(
            f"[child_runner.empty_debug] governed_branch "
            f"provider={provider} model={model} summary_len={len(summary)} "
            f"summary_stripped_len={len(summary.strip())} "
            f"evidence_refs_count={len(evidence_refs)} "
            f"first_ref={first_ref!r} status={status}"
        )
    except Exception:  # noqa: BLE001 (logging must never break a turn).
        return


def _maybe_log_legacy_collect_result(
    env: Mapping[str, str],
    *,
    provider: object,
    model: object,
    text_chunks: int,
    text_total_len: int,
    evidence_refs: tuple[str, ...],
) -> None:
    """Same as :func:`_maybe_log_governed_collect_result` for the legacy
    bare-run path. Logs per-chunk count + total length (which can be 0 even
    when chunks were yielded with empty text)."""
    if not _empty_debug_enabled(env):
        return
    try:
        first_ref = evidence_refs[0] if evidence_refs else None
        _emit_trace(
            f"[child_runner.empty_debug] legacy_branch "
            f"provider={provider} model={model} text_chunks={text_chunks} "
            f"text_total_len={text_total_len} "
            f"evidence_refs_count={len(evidence_refs)} first_ref={first_ref!r}"
        )
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Dispatch-path TRACE helpers (gated on the SAME empty-debug env). Kevin's
# 0.1.82 repro with empty-debug=1 produced ZERO governed/legacy collector
# log lines for the silent-empty cases (proving the collector itself never
# ran). These helpers cover the path BEFORE the collector: run_child entry,
# route resolution outcome, key resolution outcome, _drive_one_turn enter +
# exit, and the degraded ``_blocked``/``_failed`` emissions. When the next
# repro fires, the operator sees exactly which step terminated the dispatch.
# Default-OFF; helpers swallow every internal exception so logging can never
# break a turn.
# ---------------------------------------------------------------------------


def _maybe_log_trace_entry(
    env: Mapping[str, str],
    *,
    provider: object,
    model: object,
) -> None:
    """Log on RealLocalChildRunner.run_child entry (what the parent sent)."""
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(f"[child_runner.trace] entry req_provider={provider!r} req_model={model!r}")
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_route(
    env: Mapping[str, str],
    *,
    provider: object,
    model: object,
    validated: object,
) -> None:
    """Log AFTER ``_resolve_route`` + ``_validate_route`` so the operator
    sees both the canonical route and whether the registry accepted it."""
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[child_runner.trace] route_resolved provider={provider!r} "
            f"model={model!r} validated={validated}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_key(
    env: Mapping[str, str],
    *,
    provider: object,
    model: object,
    key_resolved: object,
) -> None:
    """Log AFTER ``_resolve_provider_config``. True iff a usable key was
    located (file/env/injected); false routes the dispatch to ``_blocked``."""
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[child_runner.trace] key_resolved provider={provider!r} "
            f"model={model!r} resolved={key_resolved}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_turn_enter(
    env: Mapping[str, str],
    *,
    provider: object,
    model: object,
) -> None:
    """Log JUST BEFORE ``_drive_one_turn``. If turn_enter prints but the
    collector emits nothing, the failure is INSIDE the turn (ADK stream /
    governed collector); if turn_enter never prints, the failure is in
    route/key resolution above."""
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(f"[child_runner.trace] turn_enter provider={provider!r} model={model!r}")
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_turn_exit(
    env: Mapping[str, str],
    *,
    provider: object,
    model: object,
    final_text_len: int,
    evidence_refs_count: int,
) -> None:
    """Log AFTER ``_drive_one_turn`` returns normally. The empty-summary
    success path the existing #918 collector loggers already catch from
    inside the collector. Logged separately here so the trace remains
    coherent (every entry has a matching exit) when both fire."""
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[child_runner.trace] turn_exit provider={provider!r} model={model!r} "
            f"final_text_len={final_text_len} evidence_refs={evidence_refs_count}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_degraded(
    env: Mapping[str, str],
    *,
    status: object,
    reason: object,
) -> None:
    """Log every ``_degraded`` emission (blocked/failed). This is the most
    informative single line for Kevin's empty-result hunt: it tells him
    whether the dispatch died at route_unknown / key_missing / timeout /
    turn_error / llm_empty_response."""
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(f"[child_runner.trace] degraded status={status} reason={reason}")
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_drive_one_turn(
    env: Mapping[str, str],
    *,
    phase: str,
    provider: object,
    model: object,
    config_id: object,
) -> None:
    """PR-1: log ``_drive_one_turn`` enter/exit with the LIVE config's
    provider / model / ``id(config)``.

    The pre-existing ``_maybe_log_trace_turn_enter`` / ``_turn_exit`` pair
    logs the ROUTE provider/model (what we asked for). This helper logs the
    ACTUAL ``config`` argument the dispatch ran with, including
    ``id(config)`` so two sibling spawns (anthropic vs google) can be
    disambiguated even when the provider / model strings match. PR-1's
    investigation hypothesis is that the silent-empty dispatches received a
    config whose ``provider`` / ``model`` mismatches the route, which would
    explain why ``self._provider_config`` (the init field) prints ``None``
    while the route prints anthropic / google.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[child_runner.trace] drive_one_turn_{phase} "
            f"provider={provider!r} model={model!r} config_id={config_id}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_engine_stream_yield(
    env: Mapping[str, str],
    *,
    index: int,
    kind: object,
    has_text_delta: bool,
    evidence_refs_in_payload: int,
) -> None:
    """PR-1: log one event-stream yield in the governed-turn loop.

    Imported by :mod:`magi_agent.runtime.governed_turn` so the trace surface
    stays in ONE module (no parallel ``_emit_trace`` / env-gate copies). The
    governed-turn caller decides cadence (first five + the last) so this
    helper itself is cadence-agnostic. The operator's repro never drowns in
    stream-yield lines but ALWAYS sees zero-yield divergence (i.e.
    ``drive_one_turn_enter`` printed, ``stream_yield`` never did, and the
    governed-empty-response guard tripped).
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[governed_turn.trace] stream_yield i={index} kind={kind} "
            f"has_text_delta={has_text_delta} "
            f"evidence_refs_in_payload={evidence_refs_in_payload}"
        )
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# PR-H: main-turn FINALIZE-path TRACE helpers (gated on the SAME empty-debug
# env). Kevin's 0.1.86 Tesla 10-K capture produced 39 ``tool_end`` SQLite
# rows for the main turn but ZERO ``turn_end`` events. The dashboard then
# rendered "Work started, but no final answer text arrived. Please try
# again." -- yet no layer in the finalize path logged WHICH stage swallowed
# the result. The helpers below stamp every step of that path:
#
# * ``chat_routes.trace turn_start`` / ``turn_handler_exit`` (transport)
# * ``turn_engine.trace stream_consumed`` (channels run_channel_turn_async)
# * ``governed_turn.trace yield_loop_exit`` (run_governed_turn finally)
# * ``governed_turn.trace turn_end_audit_fired`` (_AfterTurnEndCollector)
# * ``engine.trace run_turn_stream_finalize`` (MagiEngineDriver.run_turn_
#   stream finally)
#
# All default-OFF (silent unless ``MAGI_CHILD_RUNNER_EMPTY_DEBUG`` is
# truthy). All wrap their own emit in try/except so trace logging can
# never break a turn. Exception fields log only ``exc.__class__.__name__``
# (the message can carry user data).
# ---------------------------------------------------------------------------


def _maybe_log_trace_chat_turn_start(
    env: Mapping[str, str],
    *,
    session_id: object,
    turn_id: object,
) -> None:
    """Log on ``POST /v1/chat/stream`` handler entry (after id resolution).

    Lets the operator see whether the request even reached the handler
    before a downstream finalize layer ate the turn. Pairs with
    :func:`_maybe_log_trace_chat_turn_handler_exit`.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(f"[chat_routes.trace] turn_start session_id={session_id!r} turn_id={turn_id!r}")
    except Exception:  # noqa: BLE001 (logging must never break a turn).
        return


def _maybe_log_trace_chat_turn_handler_exit(
    env: Mapping[str, str],
    *,
    session_id: object,
    turn_id: object,
    final_text_len: int,
    exception: type | None,
) -> None:
    """Log on ``POST /v1/chat/stream`` handler exit (both normal AND raise).

    ``final_text_len`` is bytes streamed to the client when the wrapper
    sits over the SSE body (0 for early-return paths). ``exception`` is the
    raising class (or ``None`` on normal exit); the message is intentionally
    NOT logged because it can echo user prompt data.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        exc_name = exception.__name__ if exception is not None else None
        _emit_trace(
            f"[chat_routes.trace] turn_handler_exit "
            f"session_id={session_id!r} turn_id={turn_id!r} "
            f"final_text_len={final_text_len} exception={exc_name}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_turn_engine_stream_consumed(
    env: Mapping[str, str],
    *,
    turn_id: object,
    items: int,
    terminal_kind: object,
) -> None:
    """Log at the end of the channel turn-engine stream-consumption loop.

    ``items`` counts non-terminal events drained from the stream;
    ``terminal_kind`` is the class name of the terminal envelope (or
    ``None`` when the consumer returned without seeing one).
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[turn_engine.trace] stream_consumed "
            f"turn_id={turn_id!r} items={items} "
            f"terminal_kind={terminal_kind!r}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_governed_yield_loop_exit(
    env: Mapping[str, str],
    *,
    reason: object,
    items_yielded: int,
) -> None:
    """Log in the ``run_governed_turn`` finally block.

    ``reason`` is ``"normal"`` (the ``async for`` completed) or
    ``"exception"`` (the loop raised). ``items_yielded`` counts the events
    the loop forwarded BEFORE exiting. The pair narrows the finalize
    failure to either "stream ended cleanly with no terminal" vs "stream
    raised mid-loop".
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[governed_turn.trace] yield_loop_exit reason={reason!r} items_yielded={items_yielded}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_governed_turn_end_audit_fired(
    env: Mapping[str, str],
    *,
    session_id: object,
    result_text_len: int,
) -> None:
    """Log inside ``_AfterTurnEndCollector.run_audit`` after it ran.

    Confirms the ``after_turn_end`` lifecycle audit fan-out actually fired
    for the top-level turn (depth==0). A 0.1.86-shaped capture where this
    line never prints means the collector itself was skipped (lifecycle
    hooks disabled / collector not created).
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(
            f"[governed_turn.trace] turn_end_audit_fired "
            f"session_id={session_id!r} result_text_len={result_text_len}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_engine_run_turn_stream_finalize(
    env: Mapping[str, str],
    *,
    turn_id: object,
    terminal: object,
    text_len: int,
    exception: type | None,
) -> None:
    """Log in :py:meth:`MagiEngineDriver.run_turn_stream` finally.

    The canonical "did the engine finalize?" stamp. ``terminal`` is the
    class name of the last terminal observed (or ``None`` if the stream
    ended without one). ``exception`` is the raising class (or ``None``).
    Message bodies are NEVER logged.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        exc_name = exception.__name__ if exception is not None else None
        _emit_trace(
            f"[engine.trace] run_turn_stream_finalize "
            f"turn_id={turn_id!r} terminal={terminal!r} "
            f"text_len={text_len} exception={exc_name}"
        )
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# PR-K: deeper trace at the collector terminal + run_governed_turn exception
# path + engine LLM dispatch site. Kevin's 0.1.88 trace pinpointed
# status=failed reason=child_llm_collector_status_failed for anthropic /
# google / fireworks SpawnAgent children but did NOT say WHAT made the
# Terminal != completed. The helpers below close that diagnostic gap:
#
# * ``governed_collector.trace terminal_consumed`` (collect_governed_child_turn
#   right before return). Surfaces the actual Terminal enum + items_yielded
#   + any error_code / reason fields the EngineResult carries.
# * ``governed_turn.trace yield_loop_exception`` (run_governed_turn except
#   Exception branch). Surfaces the exception class + sanitized first-80
#   chars of the message BEFORE the re-raise.
# * ``engine.trace llm_call_start`` / ``llm_call_completed`` / ``llm_call_
#   exception`` (MagiEngineDriver._drive at the adapter.run_turn dispatch
#   site). Covers entry, normal completion, and the exception path.
#
# All default-OFF (silent unless ``MAGI_CHILD_RUNNER_EMPTY_DEBUG`` is
# truthy). All wrap their own emit in try/except so trace logging can
# never break a turn. Exception fields log only ``exc.__class__.__name__``
# plus the FIRST 80 chars of ``str(exc)`` stripped. The message body can
# carry user prompt data, so we never log it unbounded.
# ---------------------------------------------------------------------------


def _maybe_log_trace_governed_collector_terminal(
    env: Mapping[str, str],
    *,
    terminal: object,
    status: object,
    summary_len: int,
    evidence_refs_count: int,
    items_yielded: int,
) -> None:
    """Stamp at the END of :func:`collect_governed_child_turn`.

    Surfaces:
      * the actual ``Terminal`` enum NAME (``completed`` / ``aborted`` /
        ``max_turns`` / ``error``). The existing collector only emitted
        the computed ``status`` (``completed`` or ``failed``), which lost
        the distinction between ``aborted`` / ``max_turns`` / ``error`` as
        the underlying terminal kind.
      * the computed ``status`` token the collector returns.
      * ``summary_len`` / ``evidence_refs`` counts so the operator can see
        whether the stream produced text / refs BEFORE the terminal.
      * ``items_yielded``: number of non-terminal events drained from the
        stream (zero-yield is the silent-empty hallmark).
      * ``error_code`` / ``reason`` / ``error``: defensively read via
        :func:`getattr` (today's :class:`~magi_agent.cli.contracts.EngineResult`
        carries only ``error``; future engine versions may carry the
        other two). Fail-soft so the trace stays useful as the contract
        evolves.

    Default-OFF (gated on the same ``MAGI_CHILD_RUNNER_EMPTY_DEBUG`` env as
    every other PR-1/PR-H/PR-K trace helper). Fail-safe: the entire body
    is wrapped in ``try / except`` so a malformed terminal object can
    never break a turn through logging.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        # The collector calls us with the EngineResult instance as
        # ``terminal``; the actual Terminal enum lives on ``terminal.terminal``.
        # Fall back to ``repr(terminal_obj)`` when ``.name`` is missing so
        # exotic test doubles still produce a readable line.
        terminal_obj = getattr(terminal, "terminal", None)
        terminal_name = getattr(terminal_obj, "name", None) or repr(terminal_obj)
        error_code = getattr(terminal, "error_code", None)
        reason = getattr(terminal, "reason", None)
        error = getattr(terminal, "error", None)
        _emit_trace(
            f"[governed_collector.trace] terminal_consumed "
            f"terminal={terminal_name} status={status} "
            f"summary_len={summary_len} evidence_refs={evidence_refs_count} "
            f"items_yielded={items_yielded} "
            f"error_code={error_code!r} reason={reason!r} error={error!r}"
        )
    except Exception:  # noqa: BLE001 (logging must never break a turn).
        return


def _sanitize_exception_message_first80(exception: BaseException) -> str:
    """Return the first 80 chars of ``str(exception)`` stripped, fail-soft.

    The message body can echo user prompt data, so we cap it BEFORE
    logging. Stripped of leading/trailing whitespace so multi-line
    exception messages render on one line. Returns the empty string on
    any failure (e.g. exotic ``__str__`` that raises) so the caller can
    always log a value.
    """
    try:
        message = str(exception)
    except Exception:  # noqa: BLE001
        return ""
    try:
        return message[:80].strip()
    except Exception:  # noqa: BLE001
        return ""


def _maybe_log_trace_governed_yield_loop_exception(
    env: Mapping[str, str],
    *,
    exception: BaseException,
) -> None:
    """Stamp inside :func:`run_governed_turn` ``except Exception`` branch.

    Sibling of :func:`_maybe_log_trace_governed_yield_loop_exit`. The
    PR-H ``yield_loop_exit`` line told the operator the loop ended in
    ``reason='exception'`` but did not name the exception class or
    surface any message. This helper closes that gap: it logs the
    exception ``__class__.__name__`` plus the FIRST 80 chars of
    ``str(exc)`` (stripped) so the operator can see WHAT made the loop
    raise BEFORE the re-raise propagates upward.

    Default-OFF. Fail-safe. Logged values are bounded so an attacker-
    controlled exception message cannot blow up the serve log.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        exc_class = exception.__class__.__name__
        message = _sanitize_exception_message_first80(exception)
        _emit_trace(
            f"[governed_turn.trace] yield_loop_exception "
            f"exception={exc_class} message_first80={message!r}"
        )
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_engine_llm_call_start(
    env: Mapping[str, str],
    *,
    attempt: int,
    turn_id: object,
) -> None:
    """Stamp on entry to one ``adapter.run_turn`` dispatch in
    :py:meth:`MagiEngineDriver._drive` (the canonical LLM call site).

    Paired with :func:`_maybe_log_trace_engine_llm_call_completed` and
    :func:`_maybe_log_trace_engine_llm_call_exception`. The operator can
    see whether the engine ENTERED a fresh dispatch attempt at all (a
    zero-``llm_call_start`` turn proves the failure is upstream of the
    dispatch loop; a ``start`` without a matching ``completed`` /
    ``exception`` proves the engine wedged INSIDE the dispatch).

    ``attempt`` is the per-turn 1-based attempt counter (incremented at
    every outer-loop iteration of ``_drive``; recoveries, output-
    continuations, goal-nudges, and grace re-invocations all bump it).
    Default-OFF.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(f"[engine.trace] llm_call_start attempt={attempt} turn_id={turn_id!r}")
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_engine_llm_call_completed(
    env: Mapping[str, str],
    *,
    attempt: int,
    turn_id: object,
) -> None:
    """Stamp on normal completion of one ``adapter.run_turn`` dispatch.

    Logged AFTER the inner ADK event loop drains naturally (exhaustion
    or cancel). NOT logged when the dispatch raised. The operator gets
    the matching ``llm_call_exception`` line on that path. Paired with
    :func:`_maybe_log_trace_engine_llm_call_start`.

    Default-OFF.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        _emit_trace(f"[engine.trace] llm_call_completed attempt={attempt} turn_id={turn_id!r}")
    except Exception:  # noqa: BLE001
        return


def _maybe_log_trace_engine_llm_call_exception(
    env: Mapping[str, str],
    *,
    attempt: int,
    turn_id: object,
    exception: BaseException,
) -> None:
    """Stamp inside the engine's adapter-dispatch ``except`` branch.

    Surfaces the actual exception class + a bounded (first-80 chars,
    stripped) sanitisation of ``str(exc)``. The engine then captures
    the exception into its existing ``attempt_error`` slot and lets the
    recovery layer decide whether to re-invoke; this stamp fires
    BEFORE that recovery decision so the operator sees the dispatch-
    side failure independent of the recovery outcome.

    Message bodies are NEVER logged unbounded (they can echo user
    prompt data). Default-OFF.
    """
    if not _empty_debug_enabled(env):
        return
    try:
        exc_class = exception.__class__.__name__
        message = _sanitize_exception_message_first80(exception)
        _emit_trace(
            f"[engine.trace] llm_call_exception "
            f"attempt={attempt} turn_id={turn_id!r} "
            f"exception={exc_class} message_first80={message!r}"
        )
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Env-gate constants and helper (mirrors artifacts/file_delivery_live.py)
# ---------------------------------------------------------------------------

LIVE_CHILD_RUNNER_ENABLED_ENV = "MAGI_CHILD_RUNNER_LIVE_ENABLED"
LIVE_CHILD_RUNNER_KILL_SWITCH_ENV = "MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH"

_TRUTHY = {"1", "true", "yes", "on"}


def is_live_child_runner_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True iff the live child runner is enabled and not kill-switched.

    Evaluated at call time (not import time) so tests can patch ``os.environ``
    without a module reload. Both flags use explicit allowlisting against
    ``_TRUTHY`` (case-insensitive after strip); any other value (including the
    empty string) is treated as false. The kill-switch wins over enabled.

    :param env: Optional explicit env mapping; defaults to ``os.environ``.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    enabled_raw = source.get(LIVE_CHILD_RUNNER_ENABLED_ENV, "")
    kill_raw = source.get(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, "")
    enabled = str(enabled_raw).strip().lower() in _TRUTHY
    killed = str(kill_raw).strip().lower() in _TRUTHY
    return enabled and not killed


# ---------------------------------------------------------------------------
# Default child-route fallback (only used when neither the request nor an
# injected provider_config carries a provider/model).
# ---------------------------------------------------------------------------

_DEFAULT_CHILD_PROVIDER = "anthropic"
_DEFAULT_CHILD_MODEL = "claude-sonnet-5"

#: Max chars of final text we forward as the envelope ``summary``. The boundary
#: re-sanitises and re-trims to 512, so this is just a pre-trim guard against
#: pushing a megabyte of text through the seam.
_MAX_SUMMARY_CHARS = 2000

#: Provider-alias normalisation applied BEFORE delegating to
#: ``cli.providers.resolve_provider_config``. The ``ModelTierRegistry`` records
#: the gemini model under the ``"google"`` provider (and ``ChildRunnerConfig``
#: defaults ``child_provider="google"``), but the litellm/provider name in
#: ``cli.providers.SUPPORTED_PROVIDERS`` is ``"gemini"``. Without this alias a
#: default-routed child would be silently blocked (``child_provider_key_missing``)
#: even with a Gemini key present. Tier validation still runs against the
#: registry's OWN provider name (unaliased) so the vetted route is unchanged.
_PROVIDER_ALIAS: dict[str, str] = {"google": "gemini"}

#: Minimal child instruction so a TEXT-ONLY (tools=[]) child is NOT handed the
#: full filesystem-tool system prompt that ``build_cli_model_runner`` would
#: otherwise synthesise for a tool-enabled agent.
_CHILD_INSTRUCTION = "Complete the following delegated subtask. Respond with the answer only."

# Degrade-reason tokens (fixed, non-leaking). Used by the degrade returns below
# and referenced by tests, so they live as module constants in ONE place.
_DEGRADE_ROUTE_UNKNOWN = "child_model_route_unknown"
_DEGRADE_KEY_MISSING = "child_provider_key_missing"
_DEGRADE_TURN_ERROR = "child_turn_error"
_DEGRADE_TIMEOUT = "child_turn_timeout"
#: Common public prefix for "the provider/litellm dispatch produced an error
#: event (no text)". The suffix is a sanitised, length-bounded slug of the
#: original ``error_code`` so the operator can act on the actual class of
#: failure (rate_limit / model_not_found / bad_request) instead of a single
#: opaque ``child_turn_error`` blob.
_DEGRADE_LLM_ERROR_PREFIX = "child_llm_"


class _ChildLlmTurnError(Exception):
    """Raised when the ADK model emitted a non-benign ``error_code`` event.

    Carries a sanitised, fixed-shape reason token (``child_llm_<slug>``) that
    ``run_child`` surfaces as the failed-envelope reason — replacing the prior
    silent ``completed`` + empty-summary degrade that masked every provider
    failure (the 0.1.62 multi-provider SpawnAgent bug where Anthropic / Gemini
    children returned ``status=ok`` in 60-130ms with no result text).
    """

    def __init__(self, reason: str, *, partial_text: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        # Best-effort partial answer the child produced BEFORE the non-completed
        # terminal (e.g. a real answer that ended on an internal cap). Carried
        # so ``run_child`` can return it as ``partialSummary`` instead of
        # throwing it away: a slow-but-answered child should not look like a
        # total failure. Empty for genuinely empty / no-output failures.
        self.partial_text = partial_text


def _classify_child_event_error(event: object) -> str | None:
    """Return a sanitised failure reason if ``event`` reports a non-benign
    provider error, else ``None``.

    Reuses ``adk_bridge.event_adapter._all_error_fields_benign`` (the engine-
    side classifier) so a normal finish signal like ``error_code="STOP"`` /
    ``"end_turn"`` — which some providers populate on the LAST event — is
    correctly NOT treated as a failure here either. Fail-soft: a classifier
    import failure degrades to "no error" so the existing text-collection path
    is byte-identical (we never NEW-fail a turn just because we couldn't decide).
    """
    error_code = getattr(event, "error_code", None)
    error_message = getattr(event, "error_message", None)
    if not (error_code or error_message):
        return None
    try:
        from magi_agent.adk_bridge.event_adapter import (  # noqa: PLC0415
            _all_error_fields_benign,
        )
    except Exception:  # noqa: BLE001 — fail-soft: leave handling to outer guard.
        return None
    if _all_error_fields_benign(error_code, error_message):
        return None
    raw = ""
    if isinstance(error_code, str) and error_code.strip():
        raw = error_code.strip()
    elif isinstance(error_message, str) and error_message.strip():
        raw = error_message.strip()
    slug = re.sub(r"[^a-z0-9_]+", "_", raw.lower())[:60].strip("_") or "error"
    return f"{_DEGRADE_LLM_ERROR_PREFIX}{slug}"


#: Hard ceiling for a single child turn (seconds), regardless of the request's
#: ``budget_ms``. Keeps a runaway/huge budget from blocking indefinitely.
_MAX_TURN_TIMEOUT_S = 600.0

#: Default bound for a child turn when the parent passes NO positive
#: ``budget_ms`` (the common case: the model rarely sets one). Delegated
#: subtasks should be TIGHT by default: without this a "compute 1+1" child that
#: spirals (ramble hundreds of deltas, call tools it lacks, loop internal turns)
#: ran the full 600s ceiling before ending non-completed, which starved the
#: parent turn and surfaced as ``child_turn_timeout`` /
#: ``child_llm_collector_status_failed``. Generous but finite (generous-budget
#: policy: comfortably fits a legitimate multi-fetch deep-research or coding
#: child, while still killing a "1+1" child that spirals for minutes);
#: env-tunable via ``MAGI_CHILD_TURN_TIMEOUT_S``. Still clamped to
#: ``_MAX_TURN_TIMEOUT_S`` (and lowered by ``MAGI_MODEL_TIMEOUT_S`` when set).
_DEFAULT_CHILD_TURN_TIMEOUT_S = 300.0
_CHILD_TURN_TIMEOUT_ENV = "MAGI_CHILD_TURN_TIMEOUT_S"


class RealLocalChildRunner:
    """REAL, model-backed local child runner driving ONE sub-agent turn.

    Satisfies the boundary's live contract:
      * ``openmagi_live_provider = True`` (trusted-live marker), and
      * ``async def run_child(request) -> Mapping`` returning the output keys
        ``_envelope_from_output`` consumes (``childExecutionId``, ``status``,
        ``summary``, ``evidenceRefs``, ``artifactRefs``, ``auditEventRefs``).

    The genuine model runner is built via ``build_cli_model_runner`` (text-only
    toolset). For tests, an injected ``model_factory`` (a ``ProviderConfig ->
    BaseLlm`` callable yielding canned events) OR a fully-injected ``runner``
    (anything exposing ``run_async(**kwargs)``) avoids any network/API key.
    """

    openmagi_live_provider = True

    def __init__(
        self,
        *,
        provider_config: object | None = None,
        model_factory: Callable[[object], object] | None = None,
        runner: object | None = None,
        tools: list[object] | None = None,
        toolset_profile: str = "none",
        evidence_collector: object | None = None,
        workspace_root: str | None = None,
        progress_sink: Callable[[Mapping[str, object]], object] | None = None,
        full_text_sink: Callable[[str], None] | None = None,
        env: Mapping[str, str] | None = None,
        spawn_cap: tuple[str, ...] | None = None,
    ) -> None:
        #: Optional pre-resolved provider config (a ``ProviderConfig``). When
        #: supplied AND it carries a key, it short-circuits key resolution.
        self._provider_config = provider_config
        #: Test seam: a ``ProviderConfig -> BaseLlm`` factory. Forwarded to
        #: ``build_cli_model_runner`` so tests inject a fake LLM (no network).
        self._model_factory = model_factory
        #: Test seam: a fully pre-built runner (exposing ``run_async``). When
        #: supplied it is used directly, bypassing ``build_cli_model_runner``.
        self._injected_runner = runner
        #: Explicit caller-supplied toolset override. When ``None`` the toolset
        #: is derived from ``toolset_profile`` (PR1); an EMPTY/``none`` profile
        #: keeps the historical text-only (``tools=[]``) behaviour byte-for-byte.
        self._tools: list[object] | None = list(tools) if tools is not None else None
        #: PR1 (doc 07): the resolved toolset profile — ``"none"`` (default,
        #: text-only, byte-identical to v1), ``"readonly"`` (FileRead/Glob/Grep/
        #: GitDiff only), or ``"full"`` (whole core toolset; gated upstream by
        #: doc 09 permissions). The profile drives toolset construction inside
        #: ``_collect_turn_text`` ONLY when no toolset/runner is injected.
        self._toolset_profile = toolset_profile
        #: PR1: optional tool-call evidence collector. When supplied it is wired
        #: into the built toolset so each tool-call records a public
        #: ``evidence:`` ref that is promoted onto the child's ``evidenceRefs``.
        self._evidence_collector = evidence_collector
        self._workspace_root = workspace_root
        self._progress_sink = progress_sink
        #: B1 full-text seam (U3, deep-solve pipeline): optional sink that
        #: receives the UNTRIMMED final_text BEFORE the ``_MAX_SUMMARY_CHARS``
        #: pre-trim.  Default ``None`` → byte-identical to pre-seam behavior
        #: (the envelope/summary path is untouched regardless).
        #: The sink is documented parent-runtime-internal; it MUST NOT be
        #: propagated to any public event or SSE channel.  A raising sink is
        #: swallowed (never-raise contract preserved).
        self._full_text_sink: Callable[[str], None] | None = full_text_sink
        self._env: Mapping[str, str] = os.environ if env is None else env
        #: Orchestrator-imposed tool-name ceiling (Seam 2b). Stored for a future
        #: task (Seam 4) that will intersect the child's toolset against it.
        #: ``None`` means no ceiling — default behaviour is byte-identical.
        self._spawn_cap: tuple[str, ...] | None = spawn_cap

    async def run_child(self, request: object) -> Mapping[str, object]:
        """Drive ONE model-backed child turn; NEVER raise.

        Returns a mapping with exactly the keys ``_envelope_from_output``
        consumes. Any failure (unknown route, missing key, model/turn error)
        degrades to a ``blocked``/``failed`` mapping with a clear, non-leaking
        reason; the boundary re-sanitises the output.
        """
        child_execution_id = self._child_execution_id(request)
        # Operator-opt-in dispatch trace (MAGI_CHILD_RUNNER_EMPTY_DEBUG=1).
        # The trace is BEFORE the try because seeing the entry log with no
        # subsequent route_resolved line tells us _resolve_route itself
        # raised (extremely unlikely; included for completeness).
        _maybe_log_trace_entry(
            self._env,
            provider=getattr(request, "provider", None),
            model=getattr(request, "model", None),
        )
        try:
            # --- Resolve + validate the child's model route -------------------
            provider, model = self._resolve_route(request)
            route = self._validate_route(provider, model)
            _maybe_log_trace_route(
                self._env, provider=provider, model=model, validated=route is not None
            )
            if route is None:
                return self._blocked(
                    child_execution_id,
                    reason=_DEGRADE_ROUTE_UNKNOWN,
                )

            # Thread the VALIDATED/normalised route (canonical casefolded
            # provider/model from the registry) into provider-config resolution
            # and the litellm re-pin, so the vetted route and the litellm route
            # string always agree (no mixed-case drift).
            route_provider = _clean_str(getattr(route, "provider", None)) or provider
            route_model = _clean_str(getattr(route, "model", None)) or model

            # --- Resolve the provider key (degrade if absent) -----------------
            config = self._resolve_provider_config(route_provider, route_model)
            _maybe_log_trace_key(
                self._env,
                provider=route_provider,
                model=route_model,
                key_resolved=config is not None,
            )
            if config is None:
                return self._blocked(
                    child_execution_id,
                    reason=_DEGRADE_KEY_MISSING,
                )

            # --- Drive ONE turn and collect the final text + evidence ---------
            _maybe_log_trace_turn_enter(self._env, provider=route_provider, model=route_model)
            final_text, evidence_refs = await self._drive_one_turn(config, request)
            _maybe_log_trace_turn_exit(
                self._env,
                provider=route_provider,
                model=route_model,
                final_text_len=len(final_text or ""),
                evidence_refs_count=len(evidence_refs or ()),
            )
        except asyncio.TimeoutError:
            # Hung/slow model exceeded the turn budget — degrade (never raise).
            return self._failed(
                child_execution_id,
                reason=_DEGRADE_TIMEOUT,
            )
        except asyncio.CancelledError:
            # Cooperative cancellation MUST propagate — never convert it to a
            # failed mapping (it is BaseException in 3.11 so the broad ``except
            # Exception`` below won't catch it; this is explicit for robustness).
            raise
        except _ChildLlmTurnError as exc:
            # Surface the actual provider-error class (rate_limit / model_not_found
            # / bad_request / ...) instead of collapsing it into the generic
            # child_turn_error blob the next branch ships. Preserve any partial
            # answer the child produced so the parent gets its best work.
            return self._failed(
                child_execution_id,
                reason=exc.reason,
                partial_text=getattr(exc, "partial_text", "") or "",
            )
        except Exception:  # noqa: BLE001 — NEVER raise across the seam.
            return self._failed(
                child_execution_id,
                reason=_DEGRADE_TURN_ERROR,
            )

        # B1 full-text seam: invoke sink with the UNTRIMMED text BEFORE the
        # _MAX_SUMMARY_CHARS pre-trim.  Default None → byte-identical behavior.
        # A raising sink is swallowed so the never-raise contract is preserved.
        if self._full_text_sink is not None:
            try:
                self._full_text_sink((final_text or "").strip())
            except Exception:  # noqa: BLE001 — sink failure must never abort run_child
                pass

        summary = (final_text or "").strip()[:_MAX_SUMMARY_CHARS]
        return {
            "childExecutionId": child_execution_id,
            "status": "completed",
            # PR1: tool-call receipts collected during the turn are promoted to
            # the child's evidenceRefs (empty when text-only / no toolset).
            "evidenceRefs": evidence_refs,
            "summary": summary,
            "artifactRefs": (),
            "auditEventRefs": (),
        }

    # ------------------------------------------------------------------ #
    # Route resolution / validation                                       #
    # ------------------------------------------------------------------ #

    def _resolve_route(self, request: object) -> tuple[str, str]:
        """A per-task override wins, then an injected provider_config, then the
        historical default child route.

        Parents sometimes pack the route into the ``model`` field alone using
        the colon or slash convention (``"anthropic:claude-sonnet-4-6"`` /
        ``"openai/gpt-5.5"``) — the wire forms they see elsewhere (LiteLLM,
        the operator allowlist env). Pre-fix, those flowed verbatim into the
        registry lookup, producing the ``anthropic:anthropic:claude-sonnet-4-6``
        double-prefix route Kevin saw on 0.1.66+. Normalize here so either
        convention works and the registry sees a canonical ``(provider, model)``.
        """
        req_provider = _clean_str(getattr(request, "provider", None))
        req_model = _clean_str(getattr(request, "model", None))
        # Split a packed ``provider:model`` / ``provider/model`` in the model
        # field BEFORE applying provider precedence. An explicit ``provider``
        # field still wins; the split here just strips a redundant prefix so
        # we never assemble the double-prefix route.
        if req_model:
            split_provider, split_model = _split_packed_route(req_model)
            if split_model is not None:
                if not req_provider and split_provider:
                    req_provider = split_provider
                req_model = split_model
        cfg_provider = _clean_str(getattr(self._provider_config, "provider", None))
        cfg_model = _clean_str(getattr(self._provider_config, "model", None))
        provider = req_provider or cfg_provider or _DEFAULT_CHILD_PROVIDER
        model = req_model or cfg_model or _DEFAULT_CHILD_MODEL
        return provider, model

    def _validate_route(self, provider: str, model: str) -> object | None:
        """Accept the route via the canonical ``resolve_child_route`` authority.

        Delegates to :func:`magi_agent.runtime.model_tiers.resolve_child_route`,
        the SINGLE source the route-listing (SpawnAgent guidance / system-prompt
        block via ``available_child_model_routes``) is also bound to — so what the
        model is told it can use can never drift from what the runner accepts. A
        route is accepted iff it resolves in the built-in registry (returned
        normalised) OR is in the operator deployment allowlist; else ``None`` and
        the caller blocks.
        """
        from magi_agent.runtime.model_tiers import (  # noqa: PLC0415
            resolve_child_route,
        )

        return resolve_child_route(provider, model, os.environ)

    def _resolve_provider_config(self, provider: str, model: str) -> object | None:
        """Return a ``ProviderConfig`` with a usable key, or ``None``.

        ``provider``/``model`` here are the VALIDATED/normalised route from the
        ``ModelTierRegistry`` (canonical casefolded). The registry records the
        gemini model under ``"google"`` while ``cli.providers`` knows it as
        ``"gemini"``; we normalise via ``_PROVIDER_ALIAS`` at THIS seam so a
        default-routed (``provider="google"``) child resolves a Gemini key
        instead of being silently blocked. Tier validation upstream still ran
        against the registry's own (unaliased) provider name.

        Prefers an injected ``provider_config`` that already carries a key
        (tests / explicit callers). Otherwise delegates to
        ``resolve_provider_config`` (config file + env). NO key → ``None``
        (the caller degrades to blocked; never crashes).
        """
        # Map the registry-name provider to the litellm/provider name used by
        # ``cli.providers`` (e.g. ``"google"`` -> ``"gemini"``).
        provider_key = _PROVIDER_ALIAS.get(provider, provider)

        injected_key = _clean_str(getattr(self._provider_config, "api_key", None))
        injected_provider = _clean_str(getattr(self._provider_config, "provider", None))
        # An injected config may carry either the registry name or the litellm
        # name; accept a match against either form.
        if injected_key and injected_provider in {provider, provider_key}:
            return self._provider_config

        from magi_agent.engine.providers import (  # noqa: PLC0415
            ProviderConfig,
            SUPPORTED_PROVIDERS,
            UnknownProviderError,
            resolve_provider_config,
        )

        # ``resolve_provider_config`` honours MAGI_PROVIDER/config; force the
        # child's chosen provider via an env overlay so the resolved key matches
        # the route we validated.
        overlay = dict(self._env)
        overlay["MAGI_PROVIDER"] = provider_key
        try:
            resolved = resolve_provider_config(model_override=model, env=overlay)
        except UnknownProviderError:
            return None
        if resolved is None:
            return None
        # ``resolve_provider_config`` uses provider-default models when no
        # override resolves; re-pin the validated model + ensure the supported
        # provider so the litellm route is exactly what we vetted.
        if resolved.provider not in SUPPORTED_PROVIDERS:
            return None
        return ProviderConfig(
            provider=resolved.provider,
            model=model,
            api_key=resolved.api_key,
        )

    # ------------------------------------------------------------------ #
    # Turn drive (mirrors discovery/orchestrator.drive_runner_once)       #
    # ------------------------------------------------------------------ #

    async def _drive_one_turn(self, config: object, request: object) -> tuple[str, tuple[str, ...]]:
        """Build/reuse a ``CliModelRunner`` and drive ONE turn.

        Returns ``(final_text, evidence_refs)`` — the collected tool-call
        receipt refs (``evidence:...``) are empty for a text-only child.

        Heavy ADK imports are LOCAL so importing this module never triggers
        them. Mirrors the discovery orchestrator's message construction +
        event-text collection.

        The turn is ALWAYS bounded (by ``request.budget_ms`` when set, else the
        default ceiling); on expiry ``asyncio.wait_for`` raises
        ``asyncio.TimeoutError`` which the caller maps to a degraded
        ``child_turn_timeout`` result. ``asyncio.CancelledError`` is NEVER
        swallowed; it propagates.
        """
        # PR-1: operator-opt-in dispatch trace (MAGI_CHILD_RUNNER_EMPTY_DEBUG=1).
        # The enter/exit pair surfaces the LIVE ``config`` the dispatch ran
        # with, including ``id(config)`` so two sibling spawns can be
        # disambiguated even when the provider/model strings match. Closes
        # the gap between the route-resolved ``turn_enter`` and the
        # collector-side observation that prints AFTER the engine stream
        # completes.
        _maybe_log_trace_drive_one_turn(
            self._env,
            phase="enter",
            provider=getattr(config, "provider", None),
            model=getattr(config, "model", None),
            config_id=id(config),
        )
        try:
            return await asyncio.wait_for(
                self._collect_turn_text(config, request),
                timeout=self._turn_timeout_s(request),
            )
        finally:
            _maybe_log_trace_drive_one_turn(
                self._env,
                phase="exit",
                provider=getattr(config, "provider", None),
                model=getattr(config, "model", None),
                config_id=id(config),
            )

    async def _collect_turn_text(
        self, config: object, request: object
    ) -> tuple[str, tuple[str, ...]]:
        # Task 2A.6: when MAGI_SUBAGENT_GOVERNED_TURN_ENABLED is ON, drive the
        # governed-turn primitive instead of the bare run_async loop.  When OFF
        # the existing path runs unchanged (byte-identical).
        from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415

        if flag_profile_bool("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", env=self._env):
            return await self._collect_turn_text_governed(config, request)
        return await self._collect_turn_text_legacy(config, request)

    async def _collect_turn_text_governed(
        self, config: object, request: object
    ) -> tuple[str, tuple[str, ...]]:
        """Governed-turn branch (MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1).

        Security invariant: the child's RESTRICTED toolset (from
        ``_resolve_turn_toolset``) is always forwarded to
        ``build_headless_runtime(tools=...)`` so the governed runner never
        receives the full default toolset.

        The 600s wait_for ceiling is preserved: this method is called from
        ``_collect_turn_text`` which is in turn called from ``_drive_one_turn``
        which wraps the whole call in ``asyncio.wait_for``.
        """
        import tempfile  # noqa: PLC0415

        from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415
        from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415
        from magi_agent.runtime.child_derive import derive  # noqa: PLC0415
        from magi_agent.runtime.child_governed_collector import (  # noqa: PLC0415
            collect_governed_child_turn,
        )
        from magi_agent.runtime.governed_turn import run_governed_turn  # noqa: PLC0415

        session_id = self._child_session_id(request)

        # --- Restricted toolset (security invariant) -------------------------
        # _resolve_turn_toolset returns the SAME restricted toolset the legacy
        # path builds; we pass it directly to build_headless_runtime so the
        # governed runner is also restricted to the child's profile.
        # request is forwarded for Task 2B.3 tighten-only parent_cap filtering.
        tools, _evidence_collector = self._resolve_turn_toolset(session_id, request=request)

        # --- Resolve memory mode + depth from request metadata ---------------
        # parent_memory_mode: read from request.metadata["parentMemoryMode"]
        # (written by the producer in subagents.py, Task F1).  Absent or falsy
        # ⇒ "incognito" (safe default; byte-identical to today when the producer
        # did not set it, i.e. gate-OFF spawn paths).
        memory_inherit_enabled = flag_profile_bool("MAGI_CHILD_MEMORY_INHERIT_ENABLED", env=self._env)

        # spawnDepth in request.metadata becomes parent_depth for derive().
        metadata = getattr(request, "metadata", None) or {}
        raw_depth = metadata.get("spawnDepth") if isinstance(metadata, dict) else None
        parent_depth = (
            int(raw_depth) if isinstance(raw_depth, int) and not isinstance(raw_depth, bool) else 0
        )
        parent_memory_mode: str = (
            str(metadata.get("parentMemoryMode") or "incognito")
            if isinstance(metadata, dict)
            else "incognito"
        )

        # --- Extract recipeRefs → pinned_recipe_pack_ids (TY2) ---------------
        # When MAGI_SPAWN_RECIPE_BIND_ENABLED is ON and the request carries
        # recipeRefs, thread them through as pinned_recipe_pack_ids so the
        # child's ProfileResolver binds the parent-supplied recipe packs.
        # Flag OFF → () → byte-identical to today (same as the kwarg default).
        raw_refs = metadata.get("recipeRefs") if isinstance(metadata, dict) else None
        pinned_refs: tuple[str, ...] = (
            tuple(raw_refs)
            if (raw_refs and flag_profile_bool("MAGI_SPAWN_RECIPE_BIND_ENABLED", env=self._env))
            else ()
        )

        # --- Derive the child TurnContext FIRST (single source of memory_mode) -
        # derive() → _child_memory_mode() is the canonical authority for the
        # child's memory_mode.  We call it before build_headless_runtime so the
        # runtime receives the SAME value the TurnContext carries — eliminating
        # any divergence (e.g. the old "normal" expression when inherit is ON).
        ctx = derive(
            request,
            parent_memory_mode=parent_memory_mode,
            parent_depth=parent_depth,
            memory_inherit_enabled=memory_inherit_enabled,
            child_session_id=session_id,
        )

        # --- Build the child's governed runtime (restricted toolset) ---------
        workspace = self._workspace_root or tempfile.mkdtemp()
        # PR-M (TRUE root fix for the multi-session silent-empty saga,
        # 0.1.62 .. 0.1.90): prefer ``config.litellm_model`` (the
        # ``<provider>/<model>`` slug) over the bare ``config.model``.
        # ``build_headless_runtime`` re-resolves the provider downstream via
        # ``cli.providers.resolve_provider_config(model_override=model)``; a
        # bare model id (e.g. ``"claude-opus-4-8"``) falls into the openai
        # auto-detect branch and the catalog lookup fails, which causes PR-L
        # (#1130) to hit its byte-identical pass-through and emit
        # ``reasoning_effort=medium`` on a model the OpenAI provider validator
        # rejects ("openai does not support parameters: ['reasoning_effort'],
        # for model=claude-opus-4-8"). The slug form preserves provider
        # attribution end-to-end. Fallback to ``config.model`` keeps
        # byte-identical behaviour for test fixtures whose minimal mock
        # configs do not implement the ``litellm_model`` property.
        route_model = _clean_str(getattr(config, "litellm_model", None)) or _clean_str(
            getattr(config, "model", None)
        )
        rt = build_headless_runtime(
            cwd=workspace,
            session_id=session_id,
            model=route_model,
            tools=tools,
            # Prompt/tool alignment: advertise EXACTLY the child's forwarded
            # tools so the system prompt never induces the child to call a tool
            # it lacks (the tool_not_found hallucination spiral). A governed
            # ``tools == []`` child gets an empty allowlist, which suppresses the
            # whole tool catalog and every tool-usage block.
            advertised_tool_names=[
                n for n in (_tool_name(t) for t in tools) if n is not None
            ],
            memory_mode=ctx.memory_mode,  # single source: derived TurnContext
            # A-8 fail-closed: thread the child's derived permission_mode
            # (default deny/ask) instead of a hard-coded bypass.
            permission_mode=ctx.permission_mode,
            pinned_recipe_pack_ids=pinned_refs,
            # #1329 regression fix: a SpawnAgent child is a bounded, parent
            # orchestrated single-objective execution. It must answer the
            # delegated subtask once and return, NEVER auto-continue /
            # self-check-goal / re-invoke. Force auto-continue OFF for the child
            # engine regardless of MAGI_GOAL_LOOP_ENABLED (a top-level / parent
            # concern). Without this the child answered e.g. "2", the
            # auto-continue loop fired a goal-completion self-check, the model
            # replied "Yes." / "The goal has been fully met.", and the collector
            # took that last block as the child summary -> parent got "Yes."
            auto_continue_allowed=False,
            # A child's deliverable is its structured tool-only / last-block
            # return (child_governed_collector); forcing chat text would corrupt
            # it (same containment reason as auto_continue_allowed, see #1329).
            no_tool_finalizer_allowed=False,
            # F2-A containment: a child does NOT run the pre-final LLM criterion
            # gate chain. A gate blocking the child's already-streamed correct
            # answer turns a valid sub-answer into a false
            # child_llm_collector_status_failed (the incident) and burns 1-6
            # sequential critic calls per child (the 179-238s child latency). The
            # parent's own pre-final gates still audit the composed user-facing
            # answer, so no honesty is lost.
            pre_final_llm_gates_allowed=False,
        )

        # --- Drive the governed turn + collect summary + evidence_refs -------
        cancel = asyncio.Event()
        summary, evidence_refs, _status, _trip_reason = await collect_governed_child_turn(
            run_governed_turn(ctx, runtime=rt, cancel=cancel),
            # Fix F backstop: trip a missing-tool spiral (child calling tools it
            # does not have). cap<=0 disables (byte-identical collection).
            missing_tool_streak_cap=resolve_missing_tool_streak_cap(self._env),
            cancel=cancel,
        )
        # Debug observability (default-OFF). Kevin's 0.1.77 repro showed
        # status=ok with empty content even though the guard below should
        # have raised — meaning ONE of {summary, evidence_refs} was non-empty
        # in a way we can't see from the dashboard. With
        # ``MAGI_CHILD_RUNNER_EMPTY_DEBUG=1`` the very next repro logs the
        # actual lengths + the first ref so the silent path is visible. No
        # behavior change when the flag is off.
        # PR-1: the provider/model trace args MUST come from the live ``config``
        # argument (the canonical, validated route handed to this method by
        # ``_drive_one_turn``). The original wiring read ``self._provider_config``
        # which is the constructor-time INIT field. On the live dispatch path
        # it is ``None``, so the trace always printed ``provider=None
        # model=None`` regardless of which route actually ran. That made the
        # 0.1.85 SOTA-spawn trace useless for distinguishing anthropic / google
        # / openai dispatches.
        _maybe_log_governed_collect_result(
            self._env,
            provider=getattr(config, "provider", None),
            model=getattr(config, "model", None),
            summary=summary,
            evidence_refs=evidence_refs,
            status=_status,
        )
        # PR-3 (Containment hardening): honor the collector's authoritative
        # status verdict BEFORE the AND-condition empty-shape guard below.
        # The collector returns ``(summary, evidence_refs, status)`` and
        # ``status`` is "completed" iff the terminal ``EngineResult`` was
        # ``Terminal.completed``; anything else (e.g. ``Terminal.failed``)
        # MUST surface as a typed failure. Pre-PR-3 the third tuple element
        # was bound to ``_status`` and never read, so the only protection
        # against a silent ship-as-completed was the AND-condition guard
        # ``if not summary and not evidence_refs`` below. Kevin's 0.1.85
        # repro fingerprint had evidence_refs=18refs + summary="" + status=
        # failed; the AND-guard passed (refs were non-empty) and the failed
        # turn shipped as ``status="completed"`` (the silent-empty bug class).
        # Raising the typed exception here routes through ``run_child``'s
        # existing catch as ``status="failed"`` reason
        # ``child_llm_collector_status_<status>`` (sanitised slug).
        if _trip_reason:
            # Fix F: the child spiraled on tools it does not have. Surface the
            # TYPED reason (not the generic collector_status_failed the cancel
            # terminal would otherwise map to) plus best-effort text (#1458).
            raise _ChildLlmTurnError(
                f"{_DEGRADE_LLM_ERROR_PREFIX}missing_tool_streak_exhausted",
                partial_text=summary or "",
            )
        if _status != "completed":
            # Preserve any real answer the child produced before the
            # non-completed terminal so the parent still gets its best work.
            raise _ChildLlmTurnError(
                f"{_DEGRADE_LLM_ERROR_PREFIX}collector_status_{_status}",
                partial_text=summary or "",
            )
        # Silent-no-op detection (governed-path parity with PR #854's legacy
        # guard). The governed collector returned ``("", (), "completed")``
        # which is the same shape as the anthropic/google 100ms repro Kevin
        # chased on 0.1.74 (under MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1, which
        # lab profile auto-enables, the legacy collector's empty-response
        # guard is bypassed, so the same protection has to live here too).
        # Raises the typed exception that ``run_child`` routes to a typed
        # ``failed`` envelope with reason ``child_llm_empty_response``.
        if not summary and not evidence_refs:
            raise _ChildLlmTurnError(f"{_DEGRADE_LLM_ERROR_PREFIX}empty_response")
        return summary, evidence_refs

    async def _collect_turn_text_legacy(
        self, config: object, request: object
    ) -> tuple[str, tuple[str, ...]]:
        """Legacy bare run_async path (flag OFF — byte-identical to pre-2A.6)."""
        import tempfile  # noqa: PLC0415

        from google.genai import types  # noqa: PLC0415

        from magi_agent.engine.model_runner import (  # noqa: PLC0415
            build_cli_model_runner,
        )
        from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415

        # m-2: compute the child session id ONCE and reuse it.
        session_id = self._child_session_id(request)
        runner = self._injected_runner
        # PR1: resolve the toolset (and tool-call evidence collector) ONCE so the
        # same collector instance is wired into the builder and queried after.
        # request is forwarded for Task 2B.3 tighten-only parent_cap filtering.
        tools, evidence_collector = self._resolve_turn_toolset(session_id, request=request)
        if runner is None:
            # --- Extract recipeRefs → pinned_recipe_pack_ids (TY2) -----------
            legacy_metadata = getattr(request, "metadata", None) or {}
            legacy_raw_refs = (
                legacy_metadata.get("recipeRefs") if isinstance(legacy_metadata, dict) else None
            )
            legacy_pinned_refs: tuple[str, ...] = (
                tuple(legacy_raw_refs)
                if (legacy_raw_refs and flag_profile_bool("MAGI_SPAWN_RECIPE_BIND_ENABLED", env=self._env))
                else ()
            )
            workspace = self._workspace_root or tempfile.mkdtemp()
            runner = build_cli_model_runner(
                config,  # type: ignore[arg-type]
                tools=tools,
                # m-3: a tools=[] child should NOT get the full filesystem-tool
                # system prompt — give it a minimal delegated-subtask
                # instruction. A tool-enabled child keeps the default tool
                # system prompt so it knows how to use the forwarded tools.
                instruction=_CHILD_INSTRUCTION if not tools else None,
                # Prompt/tool alignment: advertise exactly the forwarded tools
                # so the tool-enabled child's prompt never names a tool it lacks.
                # (The tools=[] branch already takes _CHILD_INSTRUCTION, so this
                # value is unused there; None keeps the expression total.)
                advertised_tool_names=(
                    [n for n in (_tool_name(t) for t in tools) if n is not None]
                    if tools
                    else None
                ),
                model_factory=self._model_factory,
                workspace_root=workspace,
                # Child runners may intentionally share the parent workspace for
                # read-only tool access, but they must not build memory
                # snapshots from production-mounted workspace paths. The parent
                # prompt already carries the delegation context.
                memory_mode="incognito",
                session_id=session_id,
                local_tool_evidence_collector=evidence_collector,
                pinned_recipe_pack_ids=legacy_pinned_refs,
            )

        prompt = _child_prompt(request)
        new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
        texts: list[str] = []
        # Fix F backstop: trip if the child spirals on tools it does not have
        # (consecutive tool_not_found / tool_not_exposed responses). cap<=0
        # disables (byte-identical collection).
        _missing_tool_streak = MissingToolStreak(
            resolve_missing_tool_streak_cap(self._env)
        )
        async for event in runner.run_async(
            user_id=self._child_user_id(request),
            session_id=session_id,
            new_message=new_message,
        ):
            error_reason = _classify_child_event_error(event)
            if error_reason is not None:
                # Provider/litellm dispatch produced a non-benign error_code
                # event (no text). Surface it as a typed failure instead of
                # silently collecting no text + returning status=ok — the
                # latter masquerades the failed turn as a successful empty
                # answer (the 0.1.62 multi-provider SpawnAgent bug).
                #
                # No partial_text is threaded here (unlike the governed
                # non-completed path): a mid-stream PROVIDER error is a hard
                # dispatch failure, not an "answered-then-capped" turn, so any
                # bytes collected before it are not a usable answer.
                raise _ChildLlmTurnError(error_reason)
            content = getattr(event, "content", None)
            event_texts: list[str] = []
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    texts.append(text)
                    event_texts.append(text)
                # PR2: forward the child's tool lifecycle into the parent's
                # progress stream so the per-subagent panel shows real activity
                # ("Tool: WebSearch | start") instead of a fixed placeholder.
                # NAME ONLY — args/results stay private (gate5b sanitization
                # contract still holds; we only surface the public-schema name).
                fcall_name = _safe_tool_phase_name(getattr(part, "function_call", None))
                if fcall_name is not None:
                    await self._emit_progress(
                        {
                            "type": "child_progress",
                            "detail": f"Tool: {fcall_name} | start",
                        }
                    )
                function_response = getattr(part, "function_response", None)
                fresp_name = _safe_tool_phase_name(function_response)
                if fresp_name is not None:
                    await self._emit_progress(
                        {
                            "type": "child_progress",
                            "detail": f"Tool: {fresp_name} | end",
                        }
                    )
                # Fix F: fold this tool response into the missing-tool streak.
                if function_response is not None:
                    tripped = _missing_tool_streak.update(
                        classify_missing_tool_response(
                            getattr(function_response, "response", None)
                        )
                    )
                    if tripped:
                        # A runaway hallucination spiral: fail fast with the
                        # typed reason + best-effort text (composes with #1458).
                        raise _ChildLlmTurnError(
                            f"{_DEGRADE_LLM_ERROR_PREFIX}missing_tool_streak_exhausted",
                            partial_text="\n".join(texts),
                        )
            if event_texts:
                await self._emit_progress(
                    {
                        "type": "child_progress",
                        "detail": _child_stream_progress_detail("".join(event_texts)),
                    }
                )
        evidence_refs = self._collect_evidence_refs(evidence_collector, session_id)
        # Debug observability (default-OFF) — see _maybe_log_governed_collect_result
        # at the governed branch for the same diagnostic on the other path. The
        # legacy branch logs ``texts`` count + total length instead of joined
        # summary (which can be 0-length but the count tells us how many
        # chunks contributed).
        # PR-1: see the matching comment in ``_collect_turn_text_governed``.
        # Provider/model come from the live ``config`` argument, not
        # ``self._provider_config`` (which is the constructor-time init field
        # and is ``None`` on every live dispatch path).
        _maybe_log_legacy_collect_result(
            self._env,
            provider=getattr(config, "provider", None),
            model=getattr(config, "model", None),
            text_chunks=len(texts),
            text_total_len=sum(len(t) for t in texts),
            evidence_refs=evidence_refs,
        )
        # Silent-no-op detection. The ADK stream completed with ZERO collected
        # text AND no tool-call evidence (either the runner yielded no events
        # at all, the anthropic/gemini 100ms repro Kevin chased for days, or
        # it yielded only thought-only / signature-only parts that the text
        # extractor cannot turn into a user answer). PR #827 caught the ADK
        # ``error_code`` event shape via the in-loop classifier; this guard
        # closes the still-silent shape (no events, no errors, no text, no
        # tool calls). Raising the typed exception routes through the same
        # ``run_child`` catch path as a real ``error_code``, so SpawnAgent
        # ends up status="failed" with reason ``child_llm_empty_response``
        # instead of the dangerous status="ok" summary="" that triggered
        # the parent agent's 43-action chaotic filesystem/DB spelunking
        # on 0.1.66+.
        #
        # PR-3 asymmetry note: the governed branch above honours a
        # collector-reported ``status != "completed"`` via a typed-error
        # raise just before its own empty-shape guard. The legacy branch
        # here drives ADK ``runner.run_async`` directly and never receives
        # a terminal-status signal (ADK's stream model has no ``Terminal``
        # equivalent), so the in-loop ``_classify_child_event_error``
        # raises on ANY non-benign ``error_code`` event and the
        # empty-shape guard below covers the still-silent shape. No
        # status-aware raise is added here because there is no status to
        # honour on this path.
        if not texts and not evidence_refs:
            raise _ChildLlmTurnError(f"{_DEGRADE_LLM_ERROR_PREFIX}empty_response")
        return "\n".join(texts), evidence_refs

    async def _emit_progress(self, event: Mapping[str, object]) -> None:
        if self._progress_sink is None:
            return
        try:
            result = self._progress_sink(dict(event))
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    # ------------------------------------------------------------------ #
    # PR1: toolset resolution + tool-call evidence promotion              #
    # ------------------------------------------------------------------ #

    def _resolve_turn_toolset(
        self, session_id: str, request: object = None
    ) -> tuple[list[object], object | None]:
        """Resolve the child's toolset + evidence collector for this turn.

        Precedence:
        1. An explicit caller-supplied ``tools`` override (``self._tools``) wins
           and is used verbatim (with the supplied/derived collector).
        2. Otherwise the resolved ``toolset_profile`` decides:
           * ``none``    → empty toolset (byte-identical text-only v1; NO collector).
           * ``readonly`` → core toolset filtered to the read-only allowlist.
           * ``inherit`` → core toolset intersected with parent's ``parentToolNames``
                           (unconditional structural enforcement; empty-parent-cap
                           falls back to readonly floor).
           * ``full``    → the whole core toolset (authorisation is upstream's job).

        For tool-enabled profiles a ``LocalToolEvidenceCollector`` is created (or
        the injected one reused) and threaded into ``build_cli_adk_tools`` so
        each tool-call records a public ``evidence:`` ref.

        Task 2B.3 — tighten-only intersection (MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED):
        When the flag is ON and ``request.metadata["parentToolNames"]`` is non-empty,
        the resolved profile tools are filtered to those whose name is in parent_cap.
        When the flag is OFF or parent_cap is empty, the profile tools are returned
        UNCHANGED (byte-identical to pre-2B.3).
        # NOTE: this governs FIRST-PARTY tools only; Composio MCP is a separate
        # default-OFF attachment seam and is out of scope here.
        """
        from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415
        from magi_agent.runtime.child_bash import (  # noqa: PLC0415
            ChildBashSandbox,
            child_bash_sandbox_enabled,
            wrap_child_bash_tool,
        )
        from magi_agent.runtime.child_toolset import (  # noqa: PLC0415
            toolset_allowlist,
        )

        # Explicit override (tests / advanced callers) — use verbatim.
        if self._tools is not None:
            collector = self._evidence_collector if self._tools else None
            return list(self._tools), collector

        # PR-S: pass the runner's env so the readonly allowlist can expand to
        # include ``Bash`` when MAGI_CHILD_BASH_SANDBOX_ENABLED is on. Tests
        # flip the flag deterministically without touching os.environ.
        allowlist = toolset_allowlist(self._toolset_profile, env=self._env)  # () | names | None
        if allowlist == ():
            # ``none`` profile — historical text-only child (empty toolset). No
            # collector is built so the no-toolset path stays byte-identical.
            return [], None

        # A real toolset is requested: build (or reuse) the evidence collector
        # FIRST so it can be wired into the tools and queried after the turn.
        collector = self._evidence_collector or self._build_evidence_collector()
        tools = self._build_core_tools(session_id, collector)

        # PR-S: when the sandbox flag is ON, swap the built-in ``Bash`` ADK
        # tool's ``func`` for a sandboxed callable BEFORE any name-filtering
        # happens. Byte-identical when the flag is OFF (the wrap is skipped
        # entirely). When ON the child sees ``Bash`` under the same name it
        # learned from parent-symmetric training, but hits the allowlist +
        # tempdir + env-stripped surface instead of the parent gate5b Bash.
        if child_bash_sandbox_enabled(self._env):
            tools = wrap_child_bash_tool(tools, sandbox=ChildBashSandbox())

        if allowlist is None:
            # ``full`` or ``inherit`` profile - start with the whole core toolset.
            profile_tools: list[object] = list(tools)
        else:
            # ``readonly`` profile — filter to the read-only allowlist by tool name.
            allowed = set(allowlist)
            profile_tools = [tool for tool in tools if _tool_name(tool) in allowed]

        # ``inherit`` profile: intersect with parent's forwarded tool names so the
        # child never exceeds the parent's capability. Applied UNCONDITIONALLY (not
        # flag-gated) - this is structural, not a feature flag.
        #
        # Safety chain (D1/D2 from design doc):
        #   1. ``parentToolNames`` is always populated by all producer paths
        #      (wiring.py, tool_runtime.py, gate5b) unconditionally.
        #   2. Empty-parent-cap fallback → ``readonly`` floor (never full, never
        #      none) so the child is never silently over-privileged.
        #   3. MUTATING_TOOL_NAMES are stripped from an inherit child whose parent
        #      did NOT have them - so a read-only parent cannot spawn a writing
        #      child even if the core toolset contains those tools.
        if self._toolset_profile == "inherit":
            from magi_agent.runtime.child_toolset import (  # noqa: PLC0415
                MUTATING_TOOL_NAMES,
                READONLY_TOOL_NAMES,
            )

            metadata = getattr(request, "metadata", None) or {}
            raw_parent_cap = (
                metadata.get("parentToolNames") if isinstance(metadata, dict) else None
            )
            parent_cap = frozenset(raw_parent_cap) if raw_parent_cap else frozenset()

            if parent_cap:
                # Intersect with parent capability. Strip mutating tools when the
                # parent itself did not have them (tighten-only, never widen).
                profile_tools = [t for t in profile_tools if _tool_name(t) in parent_cap]
            else:
                # Empty-parent-cap fallback: apply readonly floor (D2).
                readonly_floor = set(READONLY_TOOL_NAMES)
                profile_tools = [
                    t for t in profile_tools if _tool_name(t) in readonly_floor
                ]

        # Task 2B.3: tighten-only intersection — apply AFTER profile filtering.
        # When the flag is ON and parent_cap is non-empty, intersect with the
        # parent's tool names so the child never exceeds the parent's capability.
        # When the flag is OFF or parent_cap is empty, return profile_tools unchanged.
        if flag_profile_bool("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", env=self._env):
            metadata = getattr(request, "metadata", None) or {}
            raw_cap = metadata.get("parentToolNames") if isinstance(metadata, dict) else None
            parent_cap = frozenset(raw_cap) if raw_cap else frozenset()
            if parent_cap:
                profile_tools = [t for t in profile_tools if _tool_name(t) in parent_cap]

        # F4: operator-authored capability_scope custom rules (denyTools / max
        # permission class). Sits AFTER parent-cap and BEFORE the orchestrator's
        # per-task allowedTools/spawn_cap grants so the tighten-only chain is
        # cleanly composed: profile ⟶ parent_cap ⟶ capability_scope ⟶ allowedTools
        # ⟶ spawn_cap. Triple-gated: strict default-OFF F4 flag PLUS profile-aware
        # customize master flags (verification + custom_rules). Fail-open on any
        # customize-store fault so a broken overrides file never breaks a spawn.
        from magi_agent.config.flags import flag_profile_bool  #  # noqa: PLC0415

        if (
            flag_bool("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", env=self._env)
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", env=self._env)
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", env=self._env)
        ):
            try:
                from magi_agent.customize.capability_scope import (  # noqa: PLC0415
                    apply_capability_scope,
                    apply_permission_class_filter,
                )
                from magi_agent.customize.store import load_overrides  # noqa: PLC0415
                from magi_agent.customize.verification_policy import (  # noqa: PLC0415
                    CustomizeVerificationPolicy,
                )

                policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
                rules = policy.enabled_capability_scope_rules()
                if rules:
                    profile_tools, capped_class = apply_capability_scope(
                        profile_tools,
                        rules=rules,
                        tool_name_fn=_tool_name,
                    )
                    # F4 honesty contract: when a rule narrows the permission
                    # class, intersect the toolset with the class's allowed
                    # tool-name set so the UI promise (``Subagents capped at
                    # readonly permission class``) is REAL — not just a label.
                    # Tighten-only: this can only subtract, never widen.
                    # ``capped_class is None`` (no rule asked for a cap) →
                    # no-op, byte-identical to denyTools-only rules.
                    if capped_class is not None:
                        profile_tools = apply_permission_class_filter(
                            profile_tools,
                            permission_class=capped_class,
                            tool_name_fn=_tool_name,
                        )
            except Exception:
                # Fail-open: a broken customize.json or import failure must never
                # block a spawn. The runtime stays byte-identical to pre-F4.
                pass

        # Seam P2-T3: allowedTools is the orchestrator's explicit per-task grant.
        # Apply after parent-cap, before spawn_cap. Gated by same default-OFF flag.
        if flag_profile_bool("MAGI_SPAWN_RECIPE_CAP_ENABLED", env=self._env):
            metadata = getattr(request, "metadata", None) or {}
            raw_allowed = metadata.get("allowedTools") if isinstance(metadata, dict) else None
            allowed = frozenset(raw_allowed) if raw_allowed else frozenset()
            if allowed:
                profile_tools = [t for t in profile_tools if _tool_name(t) in allowed]

        # Seam 4: spawn_cap is the orchestrator's hard grant ceiling. Apply as the
        # innermost cap, after profile and parent-cap. Gated default-OFF.
        if self._spawn_cap and flag_profile_bool("MAGI_SPAWN_RECIPE_CAP_ENABLED", env=self._env):
            cap = frozenset(self._spawn_cap)
            profile_tools = [t for t in profile_tools if _tool_name(t) in cap]

        return profile_tools, collector

    @staticmethod
    def _build_evidence_collector() -> object | None:
        try:
            from magi_agent.evidence.local_tool_collector import (  # noqa: PLC0415
                LocalToolEvidenceCollector,
            )

            return LocalToolEvidenceCollector()
        except Exception:  # noqa: BLE001 — evidence is best-effort, never fatal.
            return None

    def _build_core_tools(self, session_id: str, collector: object | None) -> list[object]:
        import tempfile  # noqa: PLC0415

        from magi_agent.cli.tool_runtime import (  # noqa: PLC0415
            build_cli_adk_tools,
        )

        workspace = self._workspace_root or tempfile.mkdtemp()
        return list(
            build_cli_adk_tools(
                workspace_root=workspace,
                session_id=session_id,
                local_tool_evidence_collector=collector,
                include_local_full_handlers=self._toolset_profile != "readonly",
            )
        )

    @staticmethod
    def _collect_evidence_refs(collector: object | None, session_id: str) -> tuple[str, ...]:
        """Project the collector's recorded tool-call receipts to public
        ``evidence:`` refs for the child envelope. Best-effort: any failure
        yields an empty tuple (never breaks the turn)."""
        if collector is None:
            return ()
        # Preferred lightweight accessor (test fakes implement this directly).
        accessor = getattr(collector, "evidence_refs_for_session", None)
        if callable(accessor):
            try:
                refs = accessor(session_id)
            except Exception:  # noqa: BLE001 — evidence is best-effort.
                return ()
            return _public_evidence_refs(refs)
        # Fall back to the real collector's per-session evidence ledgers.
        ledgers_accessor = getattr(collector, "evidence_ledgers_for_session", None)
        if not callable(ledgers_accessor):
            return ()
        try:
            ledgers = ledgers_accessor(session_id)
        except Exception:  # noqa: BLE001
            return ()
        refs: list[str] = []
        for ledger in ledgers or ():
            for entry in getattr(ledger, "entries", ()) or ():
                ref = getattr(entry, "evidence_ref", None)
                if isinstance(ref, str) and ref:
                    refs.append(ref)
        return _public_evidence_refs(refs)

    def _turn_timeout_s(self, request: object) -> float:
        """Resolve the per-turn timeout (seconds) from ``request.budget_ms``.

        EVERY child turn is bounded — a turn that never finishes would otherwise
        hang the parent turn forever (the spawn_agent tool awaits the child
        boundary inline on the dispatch loop with no outer bound). When no
        positive ``budget_ms`` is present the bound falls back to the TIGHT
        per-turn default (``_DEFAULT_CHILD_TURN_TIMEOUT_S``, env-tunable via
        ``MAGI_CHILD_TURN_TIMEOUT_S``) rather than the full ceiling: a delegated
        subtask should be tight by default so a runaway child cannot burn the
        whole 600s. A positive ``budget_ms`` is clamped to ``[0, ceiling]``.
        Everything is still clamped to ``_MAX_TURN_TIMEOUT_S`` (lowered by
        ``MAGI_MODEL_TIMEOUT_S`` when set).
        """
        ceiling = _MAX_TURN_TIMEOUT_S
        env_ceiling = _clean_str(self._env.get("MAGI_MODEL_TIMEOUT_S"))
        if env_ceiling is not None:
            try:
                parsed = float(env_ceiling)
            except ValueError:
                parsed = 0.0
            if parsed > 0:
                ceiling = min(ceiling, parsed)
        raw = getattr(request, "budget_ms", None)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            # No caller budget: use the tight default (env-tunable), clamped to
            # the ceiling so MAGI_MODEL_TIMEOUT_S still lowers it when set.
            default_s = _DEFAULT_CHILD_TURN_TIMEOUT_S
            env_default = _clean_str(self._env.get(_CHILD_TURN_TIMEOUT_ENV))
            if env_default is not None:
                try:
                    parsed_default = float(env_default)
                except ValueError:
                    parsed_default = 0.0
                if parsed_default > 0:
                    default_s = parsed_default
            return min(default_s, ceiling)
        return min(raw / 1000.0, ceiling)

    # ------------------------------------------------------------------ #
    # Degraded-output builders + id helpers                               #
    # ------------------------------------------------------------------ #

    def _blocked(self, child_execution_id: str, *, reason: str) -> dict[str, object]:
        _maybe_log_trace_degraded(self._env, status="blocked", reason=reason)
        return self._degraded(child_execution_id, status="blocked", reason=reason)

    def _failed(
        self, child_execution_id: str, *, reason: str, partial_text: str = ""
    ) -> dict[str, object]:
        _maybe_log_trace_degraded(self._env, status="failed", reason=reason)
        return self._degraded(
            child_execution_id, status="failed", reason=reason, partial_text=partial_text
        )

    @staticmethod
    def _degraded(
        child_execution_id: str, *, status: str, reason: str, partial_text: str = ""
    ) -> dict[str, object]:
        mapping: dict[str, object] = {
            "childExecutionId": child_execution_id,
            "status": status,
            # The reason is a safe, fixed token (no raw error text) — the
            # boundary sanitises ``summary`` regardless. ``summary`` keeps
            # carrying the REASON so the parent-facing reason projection is
            # unchanged.
            "summary": reason,
            "evidenceRefs": (),
            "artifactRefs": (),
            "auditEventRefs": (),
        }
        # Best-effort partial answer travels on a SEPARATE channel so it never
        # collides with the reason on ``summary`` (the boundary re-sanitises it).
        if isinstance(partial_text, str) and partial_text.strip():
            mapping["partialSummary"] = partial_text
        return mapping

    @staticmethod
    def _child_execution_id(request: object) -> str:
        seed = (
            f"{_clean_str(getattr(request, 'parent_execution_id', None)) or 'parent'}:"
            f"{_clean_str(getattr(request, 'task_id', None)) or 'task'}"
        )
        return f"child-exec-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _child_session_id(request: object) -> str:
        seed = (
            f"{_clean_str(getattr(request, 'parent_execution_id', None)) or 'parent'}:"
            f"{_clean_str(getattr(request, 'turn_id', None)) or 'turn'}:"
            f"{_clean_str(getattr(request, 'task_id', None)) or 'task'}"
        )
        return f"child-session-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _child_user_id(request: object) -> str:
        seed = _clean_str(getattr(request, "parent_execution_id", None)) or "parent"
        return f"child-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _child_prompt(request: object) -> str:
    """Form the child's user message from the request's objective.

    Falls back to a neutral instruction if no objective is present. Role is
    included as light context.
    """
    objective = _clean_str(getattr(request, "objective", None)) or "Complete the delegated subtask."
    role = _clean_str(getattr(request, "role", None)) or "general"
    return f"[child role: {role}]\n{objective}"


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _split_packed_route(model: str) -> tuple[str | None, str | None]:
    """Split ``provider:model`` / ``provider/model`` packed into a single string.

    Returns ``(provider, model)`` when a split was detected (so the caller
    can decide whether to apply it given the explicit ``provider`` field).
    Returns ``(None, None)`` when there is no delimiter — the caller treats
    the value as a bare model id.

    Both segments are stripped + lowercased to match the registry's
    canonical case; an empty provider segment (``":model"``) returns
    ``None`` for the provider so the caller falls back to its default.
    The split looks for the FIRST delimiter only — model ids may legitimately
    contain a second slash (``accounts/fireworks/models/...``), so we never
    rsplit.
    """
    if not isinstance(model, str) or not model.strip():
        return None, None
    raw = model.strip()
    if ":" in raw:
        head, sep, tail = raw.partition(":")
    elif "/" in raw:
        head, sep, tail = raw.partition("/")
    else:
        return None, None
    if not sep:
        return None, None
    provider = head.strip().lower() or None
    rest = tail.strip()
    if not rest:
        # Provider extracted but the model segment is empty — caller falls
        # back to its default model rather than using "" as the model id.
        return provider, None
    return provider, rest


def _child_stream_progress_detail(text: str) -> str:
    return f"Child model streamed output chunk ({len(text)} chars)"


_SAFE_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")


def _safe_tool_phase_name(part_attr: object) -> str | None:
    """Return the public-safe tool name from a function_call / function_response
    part attribute, or ``None`` when missing / unsafe.

    PR2 surfaces NAME ONLY into the parent progress stream — never args or
    response payloads.  The name still passes a strict regex guard so a
    malformed ADK shape cannot bleed arbitrary text into the public event.
    """
    if part_attr is None:
        return None
    name = getattr(part_attr, "name", None)
    if not isinstance(name, str):
        return None
    candidate = name.strip()
    if not candidate or not _SAFE_TOOL_NAME_RE.fullmatch(candidate):
        return None
    return candidate


def _tool_name(tool: object) -> str | None:
    """Return an ADK tool's ``name`` attribute, or ``None`` when absent."""
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else None


def _public_evidence_refs(refs: object) -> tuple[str, ...]:
    """Filter ``refs`` to deduplicated public ``evidence:`` ref strings.

    The boundary only accepts child output refs in the ``evidence:<token>``
    namespace; anything else is dropped here so a malformed receipt can never
    poison the envelope.
    """
    if not isinstance(refs, (list, tuple)):
        return ()
    out: list[str] = []
    for ref in refs:
        if isinstance(ref, str) and ref.startswith("evidence:") and ref not in out:
            out.append(ref)
    return tuple(out)


__all__ = [
    "LIVE_CHILD_RUNNER_ENABLED_ENV",
    "LIVE_CHILD_RUNNER_KILL_SWITCH_ENV",
    "RealLocalChildRunner",
    "is_live_child_runner_enabled",
]
