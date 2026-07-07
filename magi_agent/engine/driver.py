"""Real ADK-backed engine driver for the Magi headless CLI (PR-A2).

``MagiEngineDriver`` implements the :class:`EngineDriver` Protocol from
``cli.contracts``. It is the production runner-driving path: it drives a
single turn through the ADK runner via the adapter + bridge wiring and YIELDS
each projected public event incrementally as a ``RuntimeEvent`` instead of
accumulating-then-returning. The terminal ``EngineResult`` is delivered as the
FINAL yielded item, per the consumption convention documented in
``cli.contracts``.

Import-cleanliness
------------------
This module MUST import without ``google-adk`` / ``google-genai`` / ``textual``
installed. Every heavy symbol (``google.genai.types``, ``OpenMagiRunnerAdapter``,
``RunnerTurnInput``, ``OpenMagiEventBridge``, ``_sanitize_agent_event``) is
imported lazily inside ``_lazy_engine_deps`` which is only called the first time
``run_turn_stream`` is actually iterated. Nothing at module top pulls ADK in.

Single-flight
-------------
A second concurrent turn for the same session id is rejected. We reuse the real
``ActiveTurnRegistry`` from ``active_turn_registry`` (a thread-safe
session-key -> turn-id map). A per-driver default registry is shared across all
turns of a driver instance; on a concurrent turn we yield a terminal
``EngineResult(terminal=Terminal.aborted, error="active_session_turn")`` without
running the engine. The registry slot is always released in a ``finally`` (even
on cancel/exception).

Cancellation + orphan tool_result synthesis
-------------------------------------------
``cancel`` (an ``asyncio.Event``) is checked every iteration and the per-step
adapter pull is raced against ``cancel.wait()`` so a mid-step cancel is honored
promptly. As we stream we track tool-call ids (``tool_start``) and clear them on
the matching ``tool_end``. On cancel, for every still-pending (orphaned) tool
call we SYNTHESIZE and yield a ``tool`` ``RuntimeEvent`` representing an
interrupted ``tool_end`` (so the transcript stays balanced and the session can
resume), then emit an interruption status event and finally an aborted terminal.

Runner resolution
-----------------
``MagiEngineDriver(runner=...)`` accepts an explicit runner (tests always inject
a mock). When ``runner is None`` we resolve it from the ``runtime`` arg passed to
``run_turn_stream`` via ``getattr(runtime, "runner", runtime)`` — so a future
production caller (Stream F) can pass a wired runtime object. If no runner can be
resolved, the turn terminates with ``Terminal.error`` (``"no_runner"``) rather
than raising.

Genuine error recovery (PR12 honest retry seam)
-----------------------------------------------
This is THE live error-recovery seam. ``Runner.run_async`` owns the multi-step
model/tool loop; its ADK ``on_model_error_callback`` is a *substitute-the-
response* seam, NOT a *retry* seam — returning a content-less ``LlmResponse``
there ends the turn (ADK treats it as the final step) and no re-invocation
happens. So recovery is implemented HERE, around the run *invocation*: when the
ADK iteration raises a model error, :class:`MagiEngineDriver` classifies it via
the existing :class:`ErrorClassifier`, and for a retryable error (e.g. a 429)
applies backoff through the existing :class:`RecoveryEngine` (honoring
``Retry-After``) and then RE-INVOKES a fresh ``adapter.run_turn(...)`` — a
genuine second ``run_async`` (and therefore a real second model call).

Recovery is bounded by ``recovery_max_attempts`` and only fires BEFORE any agent
event has been streamed for the turn (so a mid-stream failure never replays
already-delivered output / duplicates tool effects). Terminal errors are not
retried (they propagate to a ``Terminal.error``); a prompt-too-long /
context-overflow error is NOT blind-retried here (it would just fail again) —
it is left to propagate (PR13 compaction territory). The whole wrapper is
flag-gated: with ``recovery=None`` (the default, and what the OFF env produces)
the streaming path is byte-for-byte identical to pre-PR12.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from magi_agent.engine.contracts import ControlRequest, EngineResult, Terminal
from magi_agent.engine.engine_routing import (
    _CANCELLED,
    _CODING_PHASES,
    _CODING_PROMPT_MARKERS,
    _EXHAUSTED,
    _LOCAL_READONLY_TOOL_NAMES,
    _MISSING,
    _authority_safe_attachment_flags,
    _available_agent_tool_names,
    _classify_policy_phase_with_softening,
    _GateAttachment,
    _local_tool_names_for_route,
    _non_empty_str,
    _phase_routes,
    _recipe_intent_binding_enabled,
    _restore_attr,
    _routing_field,
    _runner_policy_route_blocking_enabled,
    _runner_policy_routing_enabled,
    _RunnerRouteAttachment,
    _select_policy_phase,
    _Sentinel,
    _str_tuple,
    _tool_name,
    _tool_names_for_intent,
    compile_intent_bindings,
    RunnerPolicyAssembly,
)
from magi_agent.engine.engine_gates import (
    _build_coding_repair_decision_payload,
    _build_pre_final_verifier_bus_payload,
    _build_repair_continuation_message,
    _CODING_TASK_TYPES,
    _coding_repair_loop_enabled,
    _coding_repair_max_attempts,
    _document_coverage_blocks,
    _evidence_mapping,
    _evidence_observed_at,
    _extract_task_types,
    _is_coding_test_evidence,
    _is_research_recipe_scope,
    _latest_coding_test_evidence,
    _load_shacl_policy_if_enabled,
    _NON_CODING_TASK_TYPES,
    _normalize_task_type,
    _pre_final_gate_applies,
    _resolve_document_coverage_mode_with_preset,
    _run_shacl_rules_for_turn,
    _string_values,
)
from magi_agent.engine.engine_recovery import (
    _is_continuation_output_event,
    _suppress_cleanup_errors,
    build_empty_response_recovery_config,
    build_engine_recovery_policy,
    build_no_tool_finalizer_config,
    build_output_continuation_config,
    EngineRecoveryPolicy,
    should_reprompt_for_zero_edits,
)
from magi_agent.engine.engine_user_packs import (
    run_user_evidence_producers,
    run_user_validators,
)
from magi_agent.runtime.events import RuntimeEvent

# N-34: back-compat alias for the renamed cleanup-error suppression CM.
_suppress_cancel = _suppress_cleanup_errors

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from magi_agent.engine.contracts import PermissionGate
    from magi_agent.runtime.error_recovery import (
        RecoverableError,
        RecoveryAttemptState,
        RecoveryEngine,
    )
    from magi_agent.runtime.empty_response_recovery import (
        EmptyResponseRecoveryConfig,
    )
    from magi_agent.runtime.no_tool_finalizer import NoToolFinalizerConfig
    from magi_agent.runtime.goal_nudge import GoalNudge
    from magi_agent.runtime.output_continuation import OutputContinuationConfig
    from magi_agent.evidence.verify_audit import VerifyFinding


@dataclass
class _VerifyTurnState:
    """Per-turn verify-before-replying state (PR-V3).

    Small module-level dataclass threaded through one turn's pre-final loop.
    ``surfaced`` holds fingerprints already shown to the model this turn (the
    counterless-convergence dedup key, design 7.3 / A1); ``history`` accumulates
    every new finding for the terminal verdict record (PR-V4). The nudge fields
    drive the SHIP_AS_IS interception and the response_clear buffering. All
    counters are observability-only and never gate anything.
    """

    surfaced: set[str] = field(default_factory=set)
    history: list["VerifyFinding"] = field(default_factory=list)
    nudge_pending: bool = False
    nudge_round_active: bool = False
    pre_nudge_text: str = ""
    ship_marker_used: bool = False
    passes: int = 0
    nudge_rounds: int = 0
    loopback_tool_calls: int = 0
    skeptic_ran: bool = False
    skeptic_dropped: int = 0
    # Transient carry of the last audited pass's new-finding counts, read by the
    # nudge-setup status yield in the driver loop (the exit-site computes the
    # message string, these two feed the observability status event).
    pending_new_count: int = 0
    pending_high_count: int = 0


def _adk_invocation_id(event: object) -> str | None:
    """Extract the ADK ``invocation_id`` from a raw ADK event as a plain string.

    Duck-typed (``getattr``/``Mapping`` only) so ``engine.py`` names no
    ``google.*`` symbol at module scope. This is the id the CLI tool wrapper
    keys recorded evidence under (see ``cli/tool_runtime.py``); the engine notes
    it so the pre-final gate can reconcile it with the engine's static turn id.
    Returns ``None`` when absent/blank.
    """
    if isinstance(event, Mapping):
        value = event.get("invocation_id")
    else:
        value = getattr(event, "invocation_id", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _adk_finish_reason(event: object) -> str | None:
    """Extract the model finish reason from a raw ADK event as a plain string.

    ADK exposes ``finish_reason`` as a ``FinishReason`` enum (``.name``/``.value``)
    or occasionally a bare string. Returns ``None`` when absent.
    """
    finish_reason = getattr(event, "finish_reason", None)
    if finish_reason is None:
        return None
    value = getattr(finish_reason, "name", None) or getattr(finish_reason, "value", None)
    return value if isinstance(value, str) else str(finish_reason)


# Duck-typed ADK usage-metadata extraction now lives in the shared module
# ``magi_agent.shared.usage_metadata`` (single source) so the live
# context-compaction plugin reuses the SAME hardened logic. These thin aliases
# preserve the historical private names + call sites here with zero behaviour
# change; the shared module imports no ``google.*`` at module scope, so
# ``test_engine_import_clean_in_fresh_interpreter`` stays green.
from magi_agent.shared.usage_metadata import (
    adk_usage_metadata as _adk_usage_metadata,
)


def _fold_usage(turn_usage: dict[str, object], attempt_usage: Mapping[str, object]) -> None:
    """Sum one attempt's usage into the turn total (ADK usage resets per stream)."""
    for key, value in attempt_usage.items():
        try:
            turn_usage[key] = int(turn_usage.get(key, 0)) + int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue


def _turn_is_substantive(
    *,
    tool_ends_total: int,
    open_todos: int | None,
    new_evidence_records: int,
) -> bool:
    """Ambient continuation substance gate (design section 4.2, S1/S2/S3).

    A turn earns AMBIENT continuation authority iff it did real work:

    - S1: at least one ``tool_end`` occurred this turn (ok OR blocked), OR
    - S2: the durable plan ledger has at least one open todo, OR
    - S3: at least one new evidence record was collected this turn.

    Pure and I/O-free: the driver computes the three raw inputs at the call
    site (wired in a later unit); this helper only combines them via OR.
    ``open_todos`` is ``None`` when the ledger snapshot is empty (the shape
    returned by ``_open_todo_count``), which is NOT substantive on the S2 axis.
    """
    if tool_ends_total >= 1:
        return True
    if open_todos is not None and open_todos >= 1:
        return True
    return new_evidence_records >= 1


def _extract_objective_text(runner_input: object) -> str:
    """The original user text of a turn, read from the first ``runner_input``.

    Design section 5.1 / assumption A-1: the ambient GoalLoopPolicy objective is
    the ORIGINAL user message of the turn, captured ONCE at the top of ``_drive``
    from the FIRST ``runner_input`` before any re-invocation (recovery / nudge /
    continuation) replaces it. The first ``runner_input`` is built with the same
    ``RunnerTurnInput(newMessage=Content(role="user", parts=[Part(text=...)]))``
    shape as every re-invoke constructor, so the attribute path
    ``runner_input.newMessage.parts[i].text`` is stable. Non-text parts (image
    blocks built via ``Part.from_bytes``) carry no ``.text`` and are skipped;
    the remaining non-empty text parts are joined with newlines.

    Pure and fully defensive: any missing attribute / malformed part yields an
    empty string, which the ambient factory treats as "no objective -> behave
    exactly as today" (the ledger-first SEAM 2 path still covers the ledger
    case).
    """
    new_message = getattr(runner_input, "newMessage", None)
    parts = getattr(new_message, "parts", None)
    if not parts:
        return ""
    texts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            texts.append(text)
    return "\n".join(texts)


# A sane default cap so a runaway stream can't yield forever; headless can
# tolerate a generous bound on ADK events consumed per turn.
_DEFAULT_MAX_EVENT_COUNT = 4096


def _goal_is_met(nudge: "GoalNudge", *, evidence_records: object) -> bool:
    """Thin wrapper around :func:`~magi_agent.runtime.goal_nudge.goal_is_met`.

    Extracted as a module-level function so test suites can monkeypatch it
    without needing to stub the full evidence layer.  Import is deferred so
    ``import cli.engine`` stays cold-clean even when ``runtime.goal_nudge``
    is not installed.
    """
    from magi_agent.runtime.goal_nudge import goal_is_met  # noqa: PLC0415

    return goal_is_met(nudge, evidence_records=evidence_records)  # type: ignore[arg-type]


def _build_nudge_message(nudge: "GoalNudge") -> str:
    """Thin wrapper around :func:`~magi_agent.runtime.goal_nudge.build_nudge_message`.

    Extracted as a module-level function mirroring the ``_goal_is_met`` pattern
    so that the import is deferred (cold-clean at ``import cli.engine`` time) and
    test suites can monkeypatch it without stubbing the full goal_nudge module.
    """
    from magi_agent.runtime.goal_nudge import build_nudge_message  # noqa: PLC0415

    return build_nudge_message(nudge)

# G-2: the event-type sets and the "type -> EventKind" mapper live in
# cli.event_projection so this engine, cli/headless.py, and cli/tui/app.py
# all consult the same source. Aliased to the historical underscore-private
# names so the body of this module keeps reading exactly as it did.
from magi_agent.engine.event_projection import (
    ARTIFACT_EVENT_TYPES as _ARTIFACT_EVENT_TYPES,
    CONTROL_EVENT_TYPES as _CONTROL_EVENT_TYPES,
    ERROR_EVENT_TYPES as _ERROR_EVENT_TYPES,
    TOKEN_EVENT_TYPES as _TOKEN_EVENT_TYPES,
    TOOL_EVENT_TYPES as _TOOL_EVENT_TYPES,
    classify_event as _classify_event,
)

# ---------------------------------------------------------------------------
# P3 — zero-edit guard (eval mode): track file-mutating tool calls per turn
# and re-prompt once if a coding turn ends with no file edits.
# ---------------------------------------------------------------------------
# Tool names that perform file mutations (writes / edits / patches). When a
# turn ends and none of these were observed, the guard fires a single "apply
# it" re-invocation so the agent doesn't get away with just describing a fix.
_EDIT_CLASS_TOOLS = frozenset(
    {"FileEdit", "FileWrite", "Edit", "Write", "ApplyPatch", "PatchApply"}
)

#: source_citation.gate verdict -> RuleVerdict for the observability rule_check
#: event (Wave 4b). A fully cited answer passes; a partially cited answer is
#: advisory-pending (never a hard block, the gate is fail-open); an uncited
#: answer with high-risk claims is a violation. ``not_applicable`` is never
#: surfaced (no enforcement signal).
_CITATION_VERDICT_TO_RULE_VERDICT: dict[str, str] = {
    "cited": "ok",
    "partial": "pending",
    "uncited": "violation",
}

#: Auto-continue re-invocation prompt fed back to the SAME session when the
#: durable ledger still has open todos. Deliberately short + generic so the model
#: does not anchor on the wording and re-describe its plan; the original system
#: prompt + tool catalog + the durable ledger carry the objective. Mirrors the
#: goal-loop judge's continuation template shape (prefix-cache friendly).
_AUTO_CONTINUE_PROMPT = (
    "There are still open items in your task list. Continue executing the next "
    "concrete step now, using the available tools. Do not restate the plan or "
    "describe what you will do, just do it."
)

#: The single wrap-up prompt spent after two consecutive no-progress
#: continuations. Asks the model to REPORT (done vs not done) rather than keep
#: attempting, so a stalled loop ends with an honest account instead of silence.
_AUTO_CONTINUE_WRAP_UP_PROMPT = (
    "You have made no measurable progress on the last two attempts. Stop "
    "attempting further steps. In your final message, report concisely what is "
    "done, what is not done, and what is blocking the remaining work."
)


@dataclass(frozen=True)
class _AutoContinueExecResult:
    """Outcome of the SEAM 2 deterministic auto-continue executor.

    Carries the loop-control action back to ``_drive`` plus the three
    continuation counters the caller must fold back into its turn state. The
    executor itself is pure w.r.t. the loop (it never runs ``continue`` /
    ``break``); the caller applies ``action`` and the updated counters.
    """

    #: ``"continue"`` -> the caller sets ``runner_input`` and re-invokes the
    #: runner; ``"break"`` -> the caller falls through to the bare clean break.
    action: Literal["continue", "break"]
    #: The re-invocation input when ``action == "continue"`` (else ``None``).
    runner_input: object | None
    #: ``auto_continue_used`` after this decision (incremented on continue /
    #: wrap_up; unchanged otherwise).
    used: int
    #: ``auto_continue_no_progress_streak`` after folding in this attempt.
    no_progress_streak: int
    #: ``auto_continue_wrap_up_spent`` after this decision (set once a wrap-up
    #: invocation is spent).
    wrap_up_spent: bool


def _is_turn_end_event(event: Mapping[str, object]) -> bool:
    return event.get("type") == "turn_end"


def _unstreamed_text_delta(aggregate_text: str, emitted_text: str) -> str:
    if not emitted_text:
        return aggregate_text
    if aggregate_text.startswith(emitted_text):
        return aggregate_text[len(emitted_text) :]
    if emitted_text.endswith(aggregate_text):
        return ""
    max_overlap = min(len(aggregate_text), len(emitted_text))
    for size in range(max_overlap, 0, -1):
        if emitted_text.endswith(aggregate_text[:size]):
            return aggregate_text[size:]
    return aggregate_text


def _projected_events_with_transcript_text_fallback(
    projection: object,
    *,
    emitted_text: str,
) -> list[Mapping[str, object]]:
    agent_events = [
        event
        for event in getattr(projection, "agent_events", ())
        if isinstance(event, Mapping)
    ]
    if any(
        event.get("type") == "text_delta"
        and isinstance(event.get("delta"), str)
        and bool(event.get("delta"))
        for event in agent_events
    ):
        return agent_events

    fallback_events: list[Mapping[str, object]] = []
    seen_text = emitted_text
    for entry in getattr(projection, "transcript_entries", ()):
        if getattr(entry, "kind", None) != "assistant_text":
            continue
        text = getattr(entry, "text", None)
        if not isinstance(text, str) or not text:
            continue
        delta = _unstreamed_text_delta(text, seen_text)
        if not delta:
            continue
        fallback_events.append({"type": "text_delta", "delta": delta})
        seen_text += delta
    if not fallback_events:
        return agent_events

    insert_at = next(
        (
            index
            for index, event in enumerate(agent_events)
            if event.get("type") == "turn_end"
        ),
        len(agent_events),
    )
    return [
        *agent_events[:insert_at],
        *fallback_events,
        *agent_events[insert_at:],
    ]


def _map_event_kind(event_type: object) -> str:
    # G-2: route through ``cli.event_projection.classify_event``. The
    # underscore-private name is retained as a back-compat alias because the
    # engine's own call sites refer to ``_map_event_kind``; classification
    # logic is centralised so a new event-type lands in exactly one module.
    return _classify_event(event_type)


# C8 task-board-completion: taskboard statuses (lower-cased) that count as DONE.
# Any latest-per-title status outside this set marks the board incomplete.
_TASKBOARD_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"done", "complete", "completed", "cancelled", "canceled", "skipped"}
)


# C3 output-purity: canonical private/reasoning keys (mirrors
# shadow/gate3b_local_report._PRIVATE_KEYS) compiled as a JSON-KEY pattern so a
# bare prose mention ("explain hidden_reasoning") is NEVER matched — only quoted
# JSON-key appearances are. This is the conservative-pass pre-gate that skips
# the LLM call on a clean final_text.
_PRIVATE_KEY_JSON_RE = re.compile(
    r'"(?:hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|'
    r"private_tool_preview|private_tool_input|private_tool_output|"
    r"raw_tool_preview|raw_connector_credentials|child_private_records|"
    r'private_preview)"\s*:'
)

# C6 parallel-research: the research recipe packs whose turns the source-count
# cross-check applies to, and the minimum inspected sources required before
# synthesis. Scoping to these packs keeps a coding/chat turn that incidentally
# ran one search out of the check.
_RESEARCH_RECIPE_PACK_IDS: frozenset[str] = frozenset(
    {"openmagi.research", "openmagi.source-grounded", "openmagi.web-acquisition"}
)
_PARALLEL_RESEARCH_MIN_SOURCES = 2

# WS6 PR6a: the bare/prefixed validator labels that mark a recipe as having
# opted into the research evidence contract (so the soft research-governance
# notice applies). citation_support has no live satisfier (always unmet on an
# openmagi.research turn), so it is the load-bearing membership; fact_grounding
# and the named source-evidence verifier are research markers too.
_RESEARCH_CONTRACT_VALIDATOR_LABELS: frozenset[str] = frozenset(
    {"citation_support", "fact_grounding", "verifier:research-source-evidence"}
)
_RESEARCH_SOFT_NOTICE_CONTRACT_ID = "live-research-governance"
# Net-new WS6 code: the fixed-format trailing notice appended after the answer
# when an in-scope research turn could not be verified. Kept as a module-level
# constant for test stability. No em-dashes.
_RESEARCH_SOFT_NOTICE_TEXT = (
    "\n\n[Verification notice] Magi could not verify some claims in this answer "
    "against the opened sources. Treat the unverified statements with caution "
    "and confirm them before relying on them."
)
# Net-new WS6 PR6b code: the fixed-format trailing hedge notice appended after
# the answer when an in-scope research/contract turn could not be grounded
# against the collected evidence. Module-level constant for test stability.
# No em-dashes.
_EVIDENCE_HEDGE_NOTICE_TEXT = (
    "\n\n[Verification notice] Magi could not ground some specific values in this "
    "answer against the collected evidence. Treat the unverified figures as "
    "uncertain and confirm them before relying on them."
)


def _lazy_engine_deps() -> dict[str, object]:
    """Import every heavy ADK symbol lazily.

    Called only when a turn is actually iterated; keeps the module import-clean.
    """

    from google.genai import types

    from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
    from magi_agent.adk_bridge.runner_adapter import (
        OpenMagiRunnerAdapter,
        RunnerTurnInput,
    )
    from magi_agent.transport.sse import _sanitize_agent_event

    return {
        "types": types,
        "OpenMagiEventBridge": OpenMagiEventBridge,
        "OpenMagiRunnerAdapter": OpenMagiRunnerAdapter,
        "RunnerTurnInput": RunnerTurnInput,
        "sanitize_agent_event": _sanitize_agent_event,
    }


def _active_turn_registry():
    """Lazily build the real ActiveTurnRegistry (no ADK import needed).

    ``active_turn_registry`` is a standalone, ADK-free module, so importing it is
    import-clean — but we still defer the import to keep engine.py's module-load
    dependency graph minimal.
    """

    from magi_agent.runtime.active_turn_registry import (
        ActiveTurnRegistry,
    )

    return ActiveTurnRegistry()


# Role label used when rendering a resumed transcript line whose role is missing
# or unrecognized. ``user``/``assistant`` are passed through verbatim.
_RESUME_ROLE_LABELS = {"user": "User", "assistant": "Assistant"}


def _render_resume_prefix(initial_messages: object) -> str:
    """Render reconstructed prior messages as a transcript prefix for the prompt.

    ``initial_messages`` is the ``ResumeContext.initial_messages`` payload — a
    ``list[{"role","content"}]`` produced by
    :func:`session_log.reconstruct_messages`. We synthesize a compact, labeled
    transcript that is PREPENDED to the current user prompt so a resumed turn
    replays the prior conversation to the model. This is the lightweight
    JSONL-transcript rehydration path (no runner/ADK dependency).

    Pure + defensive:
    - Non-list / empty input -> ``""`` (byte-identical no-op for fresh turns).
    - Each entry must be a mapping with a string ``content``; malformed entries
      are skipped rather than raising (resume is best-effort).
    - Returns ``""`` when nothing usable remains, so the caller leaves the prompt
      untouched.
    """

    if not isinstance(initial_messages, list) or not initial_messages:
        return ""

    lines: list[str] = []
    for entry in initial_messages:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        if not isinstance(content, str) or not content:
            continue
        role = entry.get("role")
        label = _RESUME_ROLE_LABELS.get(
            role if isinstance(role, str) else "",
            str(role) if role else "Message",
        )
        lines.append(f"{label}: {content}")

    if not lines:
        return ""

    transcript = "\n".join(lines)
    return (
        "[Resumed conversation — prior turns for context]\n"
        f"{transcript}\n"
        "[End of prior conversation]\n\n"
    )


class MagiEngineDriver:
    """ADK-backed :class:`EngineDriver` for the headless CLI.

    Parameters
    ----------
    runner:
        An ADK runner object exposing ``run_async(...)`` (what
        ``OpenMagiRunnerAdapter`` calls). If ``None`` it is resolved from the
        ``runtime`` argument of :meth:`run_turn_stream`.
    max_event_count:
        Upper bound on the number of ADK events consumed before the stream is
        force-completed.
    user_id:
        ``userId`` to stamp on the ``RunnerTurnInput`` (defaults to ``"cli"``).
    """

    def __init__(
        self,
        *,
        runner: object | None = None,
        max_event_count: int = _DEFAULT_MAX_EVENT_COUNT,
        user_id: str = "cli",
        recovery: "EngineRecoveryPolicy | None" = None,
        runner_policy_assembly: RunnerPolicyAssembly | None = None,
        runner_policy_routing_enabled: bool | None = None,
        event_sink: object | None = None,
        goal_nudge: "GoalNudge | None" = None,
        output_continuation: "OutputContinuationConfig | None" = None,
        empty_response_recovery: "EmptyResponseRecoveryConfig | None" = None,
        no_tool_finalizer: "NoToolFinalizerConfig | None" = None,
        evidence_collector: Callable[[str], Sequence[object]] | None = None,
        user_hook_bus: object | None = None,
        criterion_model_factory: Callable[[], object] | None = None,
        wire_profile: object | None = None,
        # PR-C goal-loop judge factory. Builds a ``JudgeCaller`` (str -> async
        # str) from a :class:`GoalLoopPolicy`. ``None`` (default) means the
        # clean-break judge call is unavailable — the engine emits
        # ``goal_loop_judge_unavailable`` and terminates the turn as today.
        # Production callers (transport/chat_routes.py) inject a factory that
        # builds a cheap-tier LiteLlm completion caller from the deployment's
        # configured provider keys; tests inject a fake judge for hermetic
        # behavior.
        goal_loop_judge_factory: Callable[..., object] | None = None,
        # U5 ambient goal-loop synthesis (design 5.1, KD-1). Builds an ambient
        # ``GoalLoopPolicy`` from the captured turn objective at the clean break
        # when NO per-turn policy ContextVar was published (toggle off). ``None``
        # (default) keeps the driver byte-identical to pre-U5: no synthesis, so
        # the toggle-off / child / safe-profile paths are unchanged. Constructed
        # ONCE at the wiring site (env-pure ctor convention, mirroring
        # ``goal_loop_judge_factory``) and passed ``None`` for contained /
        # flag-off configurations so synthesis is structurally impossible there.
        ambient_goal_policy_factory: Callable[[str], object | None] | None = None,
        # WS3 PR3b evidence-first goal completion (default-OFF DI; all three
        # values resolve to their byte-identical OFF state when the flag is
        # unset). ``evidence_first`` is the master gate for ALL THREE seams in
        # ``_drive`` (each body is guarded ``if evidence_first``), so the
        # OFF-path byte-identical proof depends on it. ``plan_ledger_reader``
        # reads the durable todo snapshot for a session id (mirror
        # ``evidence_collector``); ``required_evidence`` is the engine-side
        # terminus of Reader 2 (``config.env.read_goal_required_evidence``),
        # consumed by ``resolve_pre_judge_outcome`` at both SEAM 1 and SEAM 2.
        evidence_first: bool = False,
        plan_ledger_reader: Callable[[str], Sequence[object]] | None = None,
        required_evidence: tuple[str, ...] = (),
        # Ledger-first auto-continue authority (profile-aware default-ON via
        # MAGI_GOAL_LOOP_ENABLED, resolved at the wiring site so the ctor stays
        # env-pure). When True, SEAM 2's already-computed "continue" verdict
        # actually re-invokes the runner (bounded by the measurable-progress
        # gate + generous ambient budgets) instead of degrading to a bare break.
        # False -> SEAM 2 keeps its historic bare-break behaviour byte-for-byte.
        auto_continue_enabled: bool = False,
        # Intensity of the auto-continue loop for THIS driver's turns. ``mission``
        # (the composer Goal-mission toggle) selects the higher MISSION budgets;
        # ambient (the default) uses the generous AMBIENT budgets. The toggle is
        # an intensity control, not the on/off master (that is the flag above).
        auto_continue_mission: bool = False,
        # B5: driver-owned durable-session fetch bound (anti-side-channel safe).
        # ``None`` (default) -> the CLI path and every non-durable hosted path
        # are byte-identical to pre-B5 (no GetSessionConfig on RunConfig).
        # Set by ``build_hosted_runtime`` when the durable session substrate is
        # active (mirroring the legacy gate5b4c3 activation condition). The
        # adapter receives this value at each turn invocation via
        # ``_drive``; external ``run_config`` is still blocked at the adapter.
        num_recent_events: int | None = None,
    ) -> None:
        self._runner = runner
        # B5: driver-owned fetch bound, threaded to the adapter at construction
        # time. ``None`` keeps the CLI and non-durable hosted paths unchanged.
        self._num_recent_events: int | None = num_recent_events
        # Optional wire profile for the HOSTED path (T4). ``None`` (default) keeps
        # the CLI path byte-for-byte unchanged -- bridge is constructed without
        # wire_profile.  When set (e.g. HOSTED_PROFILE), each turn's bridge is
        # constructed with ``wire_profile=self._wire_profile`` so projected events
        # carry the hosted wire shape (tu_<hash> ids, public_events field shapes).
        self._wire_profile = wire_profile
        self._max_event_count = max(1, int(max_event_count))
        self._user_id = user_id
        # Genuine error-recovery retry policy (PR12). ``None`` -> no retry
        # wrapper (the OFF path; byte-for-byte identical streaming). When set,
        # a classified-retryable model error raised by the run invocation is
        # backed-off and the run is RE-INVOKED (fresh run_async).
        self._recovery = recovery
        self._runner_policy_assembly = runner_policy_assembly
        self._runner_policy_routing_enabled = runner_policy_routing_enabled
        # Optional observability sink, called with (payload, session_id, turn_id)
        # for each sanitized public event. None keeps the default path a no-op.
        self._event_sink = event_sink
        # PR4 goal-nudge continuation. ``None`` (default) -> no nudge logic;
        # ``_drive`` behaves byte-identically to pre-PR4.
        self._goal_nudge: "GoalNudge | None" = goal_nudge
        # PR-C goal-loop judge factory (clean-break judge call). ``None``
        # (default) keeps the clean-break branch byte-identical to pre-PR-C —
        # the engine emits ``goal_loop_judge_unavailable`` and breaks when a
        # ``GoalLoopPolicy`` is active for the turn but no factory is wired.
        self._goal_loop_judge_factory: Callable[..., object] | None = (
            goal_loop_judge_factory
        )
        # U5 ambient goal-loop synthesis factory (design 5.1). ``None`` (default)
        # -> the clean-break synthesis is inert and ``_drive`` is byte-identical
        # to pre-U5. Non-None only for parent turns under a profile-ON
        # MAGI_GOAL_LOOP_ENABLED (the wiring passes ``None`` otherwise), and
        # synthesis is additionally gated on ``self._auto_continue_enabled`` at
        # the call site so a factory can never fire when auto-continue is off.
        self._ambient_goal_policy_factory: Callable[[str], object | None] | None = (
            ambient_goal_policy_factory
        )
        # WS3 PR3b: evidence-first goal completion. ``self._evidence_first``
        # False (default) -> all three seams in ``_drive`` are inert and the
        # clean-break control flow is byte-identical to pre-WS3. Resolved from
        # ``is_goal_completion_evidence_first_enabled()`` at the wiring site (NOT
        # read inside the engine, so the ctor stays env-pure / testable).
        self._evidence_first: bool = evidence_first
        self._plan_ledger_reader: Callable[[str], Sequence[object]] | None = (
            plan_ledger_reader
        )
        self._required_evidence: tuple[str, ...] = tuple(required_evidence)
        # Ledger-first auto-continue (the highest-leverage fix). Presence gives
        # SEAM 2 re-invocation authority; the intensity picks the budget set.
        self._auto_continue_enabled: bool = auto_continue_enabled
        self._auto_continue_mission: bool = auto_continue_mission
        # Output-continuation: resume a response truncated at the model's
        # per-response output-token cap by re-invoking and appending. ``None``
        # (default) -> no continuation logic; streaming is byte-identical.
        self._output_continuation: "OutputContinuationConfig | None" = (
            output_continuation
        )
        # R2 empty-response recovery (hermes mechanism 3): bounded corrective
        # re-invocation on a tools-ran-but-silent stop + one grace re-invocation
        # after event-budget exhaustion. ``None`` (default, flag
        # MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED OFF) -> no recovery logic;
        # ``_drive`` control flow is byte-identical to pre-R2.
        self._empty_response_recovery: "EmptyResponseRecoveryConfig | None" = (
            empty_response_recovery
        )
        # B9 backstop: one bounded tool-less finalizer pass when a tool-loop turn
        # commits blank. None (default) keeps _drive byte-identical.
        self._no_tool_finalizer: "NoToolFinalizerConfig | None" = no_tool_finalizer
        # Optional evidence-collector DI seam (PR4 follow-up). When set,
        # _collect_evidence delegates to this callable instead of returning ().
        # The engine driver does NOT own a ledger; the harness layer above wires
        # one here when it wants evidence-backed GoalNudge goals to be checkable.
        # When None (the default), _collect_evidence returns () — byte-identical
        # to pre-seam behaviour.
        self._evidence_collector: Callable[[str], Sequence[object]] | None = (
            evidence_collector
        )
        # Root-cause-1 reconciliation (live turn_id mismatch). The CLI tool
        # wrapper records evidence into the SHARED collector keyed on the ADK
        # ``invocation_id`` (e.g. ``"e-fbb68880-..."``), but the engine's gate
        # queries ``_collect_evidence`` with the engine's static ``turn_id``
        # (the ``"cli-turn"`` default from ``_turn_identity``). They never match,
        # so live evidence was invisible to the gate. ``_drive`` notes each ADK
        # ``invocation_id`` it observes on the live event stream here; the set is
        # RESET at the start of every ``_drive`` (per-turn scope — the
        # single-flight registry guarantees one active turn per session). The
        # engine's own ``turn_id`` is left UNCHANGED so every emitted event label
        # stays byte-identical (no coding/hosted regression).
        self._observed_invocation_ids: set[str] = set()
        # Shared across all turns of this driver instance: single-flight per
        # session id. Lazily built so construction stays cheap + import-clean.
        self._registry: object | None = None
        # Cluster doc 11 PR2: CC-style user ``settings.json`` HookBus. ``None``
        # (default, gate ``MAGI_USER_HOOKS_ENABLED`` OFF) -> no bridge attached
        # and every turn is byte-identical to today. When set, ``_drive``
        # bridges its BEFORE_TOOL_USE / AFTER_TOOL_USE hooks onto the runner's
        # ADK before/after-tool callbacks (command executor only). Built once by
        # the production wiring via ``cli.hook_wiring.build_user_hook_bus`` and
        # injected here; local CLI / self-host only (never hosted multi-tenant).
        self._user_hook_bus: object | None = user_hook_bus
        # P3: factory for the LLM criterion judge model (custom llm_criterion
        # rules at pre-final). ``None`` (default) -> llm_criterion rules are inert
        # (fail-open) so the turn is byte-identical. Built by the wiring from the
        # provider config when MAGI_EGRESS_GATE_ENABLED.
        self._criterion_model_factory: Callable[[], object] | None = (
            criterion_model_factory
        )

    async def _maybe_llm_criterion_block(
        self, *, final_text: str, turn_id: str = ""
    ) -> str | None:
        """Reason string if an enabled pre-final llm_criterion rule BLOCKS, else None.

        Flag-gated by ``MAGI_EGRESS_GATE_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``;
        returns ``None`` (no block) when off, no rules, or on any error (fail-open).
        Only ``action == "block"`` rules can block here (P3); other actions are
        recorded by validation but not enforced at pre-final in this phase.

        Evidence-grounded (``MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED``): when a
        rule's payload declares ``evidenceRefs``, the criterion is judged
        against a scoped projection of this turn's evidence ledger. Projection
        is best-effort (a fault falls back to the evidence-blind judge, never
        drops the block); byte-identical to before for rules without
        ``evidenceRefs``.
        """
        from magi_agent.config.flags import flag_bool, flag_profile_bool  # noqa: PLC0415

        if not (
            flag_bool("MAGI_EGRESS_GATE_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        ):
            return None
        try:
            from magi_agent.customize.criterion_engine import (
                evaluate_criterion,
                project_evidence_for_criterion,
            )
            from magi_agent.customize.store import load_overrides
            from magi_agent.customize.verification_policy import (
                CustomizeVerificationPolicy,
            )

            evidence_grounded = flag_profile_bool(
                "MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED"
            )
            policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
            rules = policy.enabled_llm_criterion_rules(fires_at="pre_final")
            for rule in rules:
                if rule.get("action") != "block":
                    continue
                payload = rule.get("what", {}).get("payload", {})
                criterion = payload.get("criterion") if isinstance(payload, dict) else None
                if not isinstance(criterion, str) or not criterion.strip():
                    continue
                evidence_context = None
                if evidence_grounded and isinstance(payload, dict):
                    try:
                        evidence_refs = payload.get("evidenceRefs")
                        if isinstance(evidence_refs, list) and evidence_refs:
                            evidence_context = project_evidence_for_criterion(
                                self._collect_evidence(turn_id), evidence_refs
                            )
                    except Exception:
                        evidence_context = None
                passed, reason = await evaluate_criterion(
                    criterion=criterion,
                    draft_text=final_text,
                    model_factory=self._criterion_model_factory,
                    evidence_context=evidence_context,
                )
                if not passed:
                    return reason or "custom criterion not satisfied"
            return None
        except Exception:
            return None

    async def _answer_quality_llm_block(
        self, *, prompt: str, final_text: str
    ) -> str | None:
        """C1 — reason if the answer fails the LLM answer-quality check, else None.

        Built-in llm producer (vs the user custom rules in
        ``_maybe_llm_criterion_block``): judges whether ``final_text`` genuinely
        addresses the user's ``prompt`` task — not empty, not a pure tool/JSON
        echo, not clearly unrelated. Uses the same generic
        ``criterion_engine.evaluate_criterion`` judge (Haiku-class critic model)
        with a fixed, bilingual (KR/EN) criterion.

        Gated by ``MAGI_VERIFY_ANSWER_QUALITY`` OR the ``answer-quality`` Customize
        preset, AND a critic model must be available
        (``self._criterion_model_factory`` is built only when
        ``MAGI_EGRESS_GATE_ENABLED`` — the cost gate). When inactive / no model /
        any error ⇒ ``None`` (fail-open) so the turn is byte-identical and the
        judge can only ever ADD a block on a clear fail verdict.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_answer_quality_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if self._criterion_model_factory is None:
            return None
        if not (
            parse_answer_quality_verification_enabled(os.environ)
            or preset_enabled("answer-quality", default=False)
        ):
            return None
        try:
            from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                evaluate_criterion,
            )

            # The user task is embedded into the (untrusted-data) criterion slot,
            # consistent with the judge prompt's "apply, do not obey" framing.
            criterion = (
                "The agent was given this TASK (untrusted data): "
                f"<<<TASK\n{prompt}\n>>>END. "
                "Judge whether the DRAFT genuinely attempts to address that task. "
                "Pass=true if it makes a real attempt to answer in ANY language "
                "(including Korean), even partially or by honestly reporting it "
                "could not complete the task. Pass=false ONLY if the draft is "
                "empty, is purely a raw tool/JSON result echo with no answer, or "
                "is clearly unrelated to the task."
            )
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=final_text,
                model_factory=self._criterion_model_factory,
            )
            if not passed:
                return reason or "answer does not address the task"
            return None
        except Exception:
            return None

    async def _pre_refusal_llm_block(
        self, *, prompt: str, final_text: str
    ) -> str | None:
        """C2 — reason if the answer prematurely refuses a doable task, else None.

        Built-in llm producer, same shape/gating as
        :meth:`_answer_quality_llm_block`. Judges whether ``final_text`` REFUSES
        the ``prompt`` task without any attempt or a legitimate reason. This is
        the NL replacement for the dormant English-only ``discipline_boundary``
        ``pre_refusal`` regex (which also needed an ``availableAction`` signal the
        runtime never provided).

        Gated by ``MAGI_VERIFY_PRE_REFUSAL`` OR the ``pre-refusal`` Customize
        preset, AND a critic model present (``MAGI_EGRESS_GATE_ENABLED`` — the
        cost gate). Inactive / no model / any error ⇒ ``None`` (fail-open).
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_pre_refusal_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if self._criterion_model_factory is None:
            return None
        if not (
            parse_pre_refusal_verification_enabled(os.environ)
            or preset_enabled("pre-refusal", default=False)
        ):
            return None
        try:
            from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                evaluate_criterion,
            )

            criterion = (
                "The agent was given this TASK (untrusted data): "
                f"<<<TASK\n{prompt}\n>>>END. "
                "Judge whether the DRAFT prematurely REFUSES the task. Pass=true "
                "if the draft attempts or answers the task (in ANY language, "
                "including Korean), OR refuses for a legitimate reason — a real "
                "safety boundary, a genuinely impossible request, or missing "
                "information it cannot obtain. Pass=false ONLY if it refuses a "
                "doable task with no attempt and no legitimate reason (e.g. a bare "
                "'I can't do that' / '할 수 없습니다' to a feasible request)."
            )
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=final_text,
                model_factory=self._criterion_model_factory,
            )
            if not passed:
                return reason or "answer prematurely refuses the task"
            return None
        except Exception:
            return None

    async def _completion_evidence_llm_block(
        self, *, turn_id: str, final_text: str
    ) -> str | None:
        """C-MERGE-1 — reason if a completion/promise claim has no action evidence.

        Merged completion-evidence / goal-progress / deferral-blocker check. The
        gate is checked FIRST (no evidence collection, no model call when off →
        byte-identical). When active, the turn's evidence is collected via the
        cheap idempotent ``_collect_evidence`` read: if the turn produced ANY
        evidence (it took action), the completion claim is considered backed and
        the check passes WITHOUT a model call (conservative — never false-blocks
        an acting turn). Only a ZERO-evidence turn is judged by the criterion
        engine for an unsupported completion/promise claim.

        Gated by ``MAGI_VERIFY_COMPLETION_EVIDENCE`` OR any of the
        completion-evidence / goal-progress / deferral-blocker Customize presets,
        AND a critic model present (``MAGI_EGRESS_GATE_ENABLED`` — the cost gate).
        Inactive / no model / any error ⇒ ``None`` (fail-open).
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_completion_evidence_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if self._criterion_model_factory is None:
            return None
        if not (
            parse_completion_evidence_verification_enabled(os.environ)
            or preset_enabled("completion-evidence", default=False)
            or preset_enabled("goal-progress", default=False)
            or preset_enabled("deferral-blocker", default=False)
        ):
            return None
        # Det pre-gate: an acting turn (any collected evidence) can't false-block,
        # and skips the model call entirely.
        if self._collect_evidence(turn_id):
            return None
        try:
            from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                evaluate_criterion,
            )

            criterion = (
                "The agent's turn produced NO action or tool evidence (it ran no "
                "tools and recorded no work this turn). Judge the DRAFT. Pass=true "
                "unless the draft asserts that a task is COMPLETE/done or PROMISES "
                "future delivery ('I'll do X later', '다음에 처리하겠습니다', "
                "'완료했습니다'). An honest report that it could NOT complete the "
                "task ('I was unable to…', '완료하지 못했습니다'), a clarifying "
                "question, or a plain informational answer is pass=true. Pass=false "
                "ONLY if it claims completion or promises future work despite the "
                "turn taking no action."
            )
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=final_text,
                model_factory=self._criterion_model_factory,
            )
            if not passed:
                return reason or "completion/promise claim has no action evidence"
            return None
        except Exception:
            return None

    async def _resource_claim_llm_block(
        self, *, turn_id: str, final_text: str
    ) -> str | None:
        """C-MERGE-2 — reason if a resource/self claim has no read evidence.

        Merged self-claim / resource-existence concern. The gate is checked FIRST
        (no evidence collection, no model call when off → byte-identical). When
        active, the turn's evidence is collected via the cheap idempotent
        ``_collect_evidence`` read: if the turn produced ANY SOURCE/READ evidence
        (a ``SourceInspection`` / ``WebSearch`` / ``KnowledgeSearch`` record —
        same types the source-ledger projector counts), the resource claim is
        considered backed and the check passes WITHOUT a model call (conservative
        — a turn that actually read something is never false-blocked). Only a
        zero-source-read turn is judged by the criterion engine for an
        unverified-resource claim.

        Gated by ``MAGI_VERIFY_RESOURCE_CLAIM`` OR either of the self-claim /
        resource-existence Customize presets, AND a critic model present
        (``MAGI_EGRESS_GATE_ENABLED`` — the cost gate). Inactive / no model / any
        error ⇒ ``None`` (fail-open).
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_resource_claim_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if self._criterion_model_factory is None:
            return None
        if not (
            parse_resource_claim_verification_enabled(os.environ)
            or preset_enabled("self-claim", default=False)
            or preset_enabled("resource-existence", default=False)
        ):
            return None
        # Det pre-gate: a turn that inspected ≥1 source can't false-block, and
        # skips the model call entirely. Uses the same source-evidence types as
        # the source-ledger projector.
        try:
            from magi_agent.evidence.final_output_gate import (  # noqa: PLC0415
                _SOURCE_EVIDENCE_TYPES,
            )

            for record in self._collect_evidence(turn_id):
                record_type = (
                    record.get("type")
                    if isinstance(record, Mapping)
                    else getattr(record, "type", None)
                )
                if isinstance(record_type, str) and record_type in _SOURCE_EVIDENCE_TYPES:
                    return None
        except Exception:
            logger.debug("resource-claim pre-gate failed", exc_info=True)
            return None
        try:
            from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                evaluate_criterion,
            )

            criterion = (
                "The agent's turn produced NO source/read evidence (it inspected "
                "no file, URL, or knowledge source this turn). Judge the DRAFT. "
                "Pass=true unless the draft ASSERTS that a specific resource "
                "exists, was read, or was checked — for example: a concrete file "
                "path ('/Users/...', '/home/...', 'utils.py contains...'), a URL "
                "('https://example.com says...'), or a self-claim about contents "
                "('I read the README and it says X', '문서를 확인했더니...'). A "
                "GENERAL answer that does not assert reading anything, an honest "
                "report that no resource was inspected, or a clarifying question "
                "is pass=true. Pass=false ONLY if the draft makes such a "
                "resource-existence or self-read claim despite the turn taking no "
                "such read."
            )
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=final_text,
                model_factory=self._criterion_model_factory,
            )
            if not passed:
                return reason or "resource/self claim has no read evidence"
            return None
        except Exception:
            return None

    async def _claim_citation_llm_block(self, *, final_text: str) -> str | None:
        """C4 — reason if factual claims are uncited, else None.

        Free-text claim-coverage check. Distinct from source-authority (which is
        anti-fab/det over declared ``src_N`` refs already in the answer): this
        judges whether the answer's claims warrant citations AT ALL.

        Det pre-gate: when ``final_text`` already contains any ``[src_N]``
        citation marker (the existing source-citation convention used by the
        research projection gate), skip the model call — the answer cited
        something and isn't a bare uncited claim. The criterion engine's prompt
        decides whether the cited claims are *sufficient* — but that's the
        source-authority concern (anti-fab), not claim-citation (coverage).

        Gated by ``MAGI_VERIFY_CLAIM_CITATION`` OR the ``claim-citation``
        Customize preset, AND a critic model present (``MAGI_EGRESS_GATE_ENABLED``
        — the cost gate). Inactive / no model / any error ⇒ ``None`` (fail-open).

        Wave 4b (Piece D): this judge is the ``source_citation.claim_coverage``
        member of the first-party ``source_citation`` policy (design 11.4), the
        staged semantic layer above the deterministic gate. It stays default-OFF
        (its flag is unchanged and NOT in the full profile), so the deterministic
        gate covers the motivating failure without a critic-model call.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_claim_citation_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if self._criterion_model_factory is None:
            return None
        if not (
            parse_claim_citation_verification_enabled(os.environ)
            or preset_enabled("claim-citation", default=False)
        ):
            return None
        # Det pre-gate: a cited answer skips the model call.
        try:
            from magi_agent.research.final_projection_gate import (  # noqa: PLC0415
                _SOURCE_CITATION_RE,
            )

            if _SOURCE_CITATION_RE.search(final_text):
                return None
        except Exception:
            logger.debug("claim-citation pre-gate failed", exc_info=True)
            return None
        try:
            from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                evaluate_criterion,
            )

            criterion = (
                "The DRAFT contains NO source citation markers (no [src_N]). "
                "Judge whether the draft makes specific factual claims that "
                "warrant a citation. Pass=true if the draft is a general answer, "
                "a clarifying question, an opinion/recommendation framed as such, "
                "an honest report that no source was inspected, or a procedural "
                "explanation without specific factual claims. This holds in ANY "
                "language including Korean. Pass=false ONLY if the draft asserts "
                "specific factual claims (numbers, dates, named entities' "
                "properties, historical events, citable facts) that a reader "
                "should be able to verify against a source — but no citation is "
                "provided."
            )
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=final_text,
                model_factory=self._criterion_model_factory,
            )
            if not passed:
                return reason or "factual claims are uncited"
            return None
        except Exception:
            return None

    async def _output_purity_llm_block(self, *, final_text: str) -> str | None:
        """C3 — reason if the answer leaks internal data / reasoning, else None.

        Det pre-gate (conservative pass): if ``final_text`` contains NO
        canonical private/reasoning key in JSON shape (e.g. ``"hidden_reasoning":``
        / ``"chain_of_thought":`` — patterns a clean answer never produces), skip
        the model call. The bare key word matches in PROSE ("explain
        chain-of-thought prompting") are not flagged — only quoted JSON-key
        appearances are.

        Suspicious answers reach the criterion judge with a fixed bilingual
        (KR/EN) criterion designed to distinguish a legitimate JSON answer to the
        user query from a raw internal-envelope leak. The criterion engine's
        anti-over-flag ("if unsure, pass=true") protects against false-positives
        on legitimate JSON outputs.

        Gated by ``MAGI_VERIFY_OUTPUT_PURITY`` OR the ``output-purity`` Customize
        preset, AND a critic model present (``MAGI_EGRESS_GATE_ENABLED`` — the
        cost gate). Inactive / no model / any error ⇒ ``None`` (fail-open).
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_output_purity_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if self._criterion_model_factory is None:
            return None
        if not (
            parse_output_purity_verification_enabled(os.environ)
            or preset_enabled("output-purity", default=False)
        ):
            return None
        # Det pre-gate: skip the model when no canonical private/reasoning key
        # appears as a JSON key in the answer. Pure prose mentions of these
        # words ("explain hidden_reasoning") are NOT matched.
        try:
            if _PRIVATE_KEY_JSON_RE.search(final_text) is None:
                return None
        except Exception:
            logger.debug("output-purity pre-gate failed", exc_info=True)
            return None
        try:
            from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                evaluate_criterion,
            )

            criterion = (
                "The DRAFT contains one or more canonical internal-payload keys "
                "in JSON-key form (e.g. \"hidden_reasoning\":, \"chain_of_thought\":, "
                "\"raw_tool_preview\":). Judge whether this is an internal-data "
                "LEAK. Pass=true if the JSON-keyed content is a LEGITIMATE answer "
                "to the user's query — the user asked for or is reasonably shown "
                "JSON describing such concepts (e.g. documenting an API schema, "
                "answering 'what does hidden_reasoning look like?', or showing a "
                "user's own data they pasted in). This holds in ANY language "
                "including Korean. Pass=false ONLY if the DRAFT exposes a raw "
                "tool-result envelope, an internal reasoning trace / scratchpad / "
                "chain-of-thought, or other runtime-internal payload that should "
                "have been sanitised out of the user-visible answer."
            )
            passed, reason = await evaluate_criterion(
                criterion=criterion,
                draft_text=final_text,
                model_factory=self._criterion_model_factory,
            )
            if not passed:
                return reason or "answer leaks internal data"
            return None
        except Exception:
            return None

    @property
    def runner(self) -> object | None:
        return self._runner

    @property
    def local_tool_evidence_collector(self) -> object | None:
        """The session-scoped ``LocalToolEvidenceCollector``, if the runner has one.

        Wave 3a source-citation: the transport terminal-frame composer reaches
        the live ``SessionSourceRegistry`` through this collector (via
        ``source_registry_for(session_id)``) to project citations onto the frame,
        rather than threading a collector through the streaming driver. Returns
        ``None`` when the runner exposes no collector (test stubs, child agents).
        """
        return getattr(self._runner, "local_tool_evidence_collector", None)

    def _maybe_citation_gate_audit(
        self, *, session_id: str, turn_id: str, prompt: str, final_text: str
    ) -> None:
        """Wave 4a AUDIT-mode source-citation gate (design 11.2).

        The deterministic pre-final citation gate in OBSERVE-ONLY mode: it emits
        one ``custom:CitationVerdict`` evidence record for the turn and NEVER
        alters the turn (no repair, no induce-search, no block). It runs BEFORE
        the LLM criterion pre-final rules (cheap first, ~0 model cost). Fully
        fail-soft: any error is a silent no-op, so the gate can never wedge a
        turn.

        Gated on ``MAGI_SOURCE_CITATION_ENABLED`` being on AND
        ``MAGI_SOURCE_CITATION_GATE_MODE == "audit"``. In ``repair`` mode the
        pre-final loop below owns evaluation, repair, and record emission (the
        ``repairDecision`` / ``repairPolicy`` seam), so this pre-loop hook is a
        no-op there to avoid a double record. Flag-OFF, mode ``off``, or mode
        ``repair`` => this method emits nothing here.
        """
        try:
            import os  # noqa: PLC0415

            from magi_agent.config.env import (  # noqa: PLC0415
                parse_source_citation_enabled,
                parse_source_citation_gate_mode,
            )

            if not parse_source_citation_enabled(os.environ):
                return
            if parse_source_citation_gate_mode(os.environ) != "audit":
                return
            result = self._evaluate_citation_gate_for_turn(
                session_id=session_id,
                turn_id=turn_id,
                prompt=prompt,
                final_text=final_text,
            )
            if result is None:
                return
            self._emit_citation_verdict_record(
                session_id=session_id, turn_id=turn_id, result=result
            )
        except Exception:
            return

    def _evaluate_citation_gate_for_turn(
        self, *, session_id: str, turn_id: str, prompt: str, final_text: str
    ) -> object | None:
        """Read the session registry and evaluate the deterministic gate.

        Shared by the audit hook and the repair path. Returns a
        ``CitationGateResult`` or None (no registry / any error). Never raises.
        """
        try:
            collector = self.local_tool_evidence_collector
            if collector is None:
                return None
            registry_getter = getattr(collector, "source_registry_for", None)
            if not callable(registry_getter):
                return None
            registry = registry_getter(session_id)
            if registry is None:
                return None
            snapshot = registry.snapshot()

            per_turn_ids: list[str] = []
            for record in snapshot:
                source_id = str(getattr(record, "source_id", ""))
                if not source_id:
                    continue
                record_turn = str(getattr(record, "turn_id", ""))
                if record_turn == turn_id or record_turn.startswith(
                    f"{turn_id}::spawn::"
                ):
                    per_turn_ids.append(source_id)

            from magi_agent.evidence.citation_gate import (  # noqa: PLC0415
                evaluate_citation_gate,
            )

            return evaluate_citation_gate(
                final_text,
                registry_snapshot=snapshot,
                per_turn_source_ids=tuple(per_turn_ids),
                user_input=prompt or "",
            )
        except Exception:
            return None

    def _citation_repair_active(self) -> bool:
        """True when the citation gate is in ``repair`` mode with the master on."""
        try:
            import os  # noqa: PLC0415

            from magi_agent.config.env import (  # noqa: PLC0415
                parse_source_citation_enabled,
                parse_source_citation_gate_mode,
            )

            return (
                parse_source_citation_enabled(os.environ)
                and parse_source_citation_gate_mode(os.environ) == "repair"
            )
        except Exception:
            return False

    def _citation_induce_availability(self) -> tuple[bool, bool]:
        """(web_available, kb_available) resolved from the runtime, not assumed.

        Web: a real search+fetch provider is configured
        (``direct_web_tools_available``). KB: the local qmd knowledge backend is
        resolvable. Both fail-quiet to False so a probe error degrades safely.
        """
        import os  # noqa: PLC0415

        web = False
        kb = False
        try:
            from magi_agent.tools.web_search_tools import (  # noqa: PLC0415
                direct_web_tools_available,
            )

            web = bool(direct_web_tools_available(os.environ))
        except Exception:
            web = False
        try:
            from magi_agent.knowledge.qmd_index import qmd_available  # noqa: PLC0415

            kb = bool(qmd_available())
        except Exception:
            kb = False
        return web, kb

    def _citation_repair_overlay(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        final_text: str,
        attempt_count: int,
    ) -> dict[str, object] | None:
        """Compute the citation repair overlay for the pre-final loop.

        Pure with respect to the turn text: reads the registry + flags, evaluates
        the gate, plans a repair, and returns a dict the driver loop consumes.
        Returns None when repair mode is off or nothing warrants a repair (a
        clean / advisory-only turn: the finalization hook emits the record). When
        a repair IS warranted it returns ``shouldBlock=True`` with a prebuilt
        repair ``message`` + ``kind`` + ``inducedSearch``; when the repair must
        DEGRADE (induce-search unavailable) it returns ``degrade=True`` and the
        loop does not block. Never raises."""
        try:
            if not self._citation_repair_active():
                return None
            result = self._evaluate_citation_gate_for_turn(
                session_id=session_id,
                turn_id=turn_id,
                prompt=prompt,
                final_text=final_text,
            )
            if result is None:
                return None

            import os  # noqa: PLC0415

            from magi_agent.config.env import (  # noqa: PLC0415
                parse_source_citation_induce_search_enabled,
                parse_source_citation_repair_max_attempts,
            )
            from magi_agent.evidence.citation_gate import (  # noqa: PLC0415
                build_attribution_repair_message,
                build_citation_fail_open_notice,
                build_induce_search_repair_message,
                plan_citation_repair,
            )

            web_available, kb_available = self._citation_induce_availability()
            induce_enabled = parse_source_citation_induce_search_enabled(os.environ)
            plan = plan_citation_repair(
                result,
                web_available=web_available,
                kb_available=kb_available,
                induce_search_enabled=induce_enabled,
            )
            max_attempts = parse_source_citation_repair_max_attempts(os.environ)
            overlay: dict[str, object] = {
                "result": result,
                "maxAttempts": max_attempts,
                "failOpenNotice": build_citation_fail_open_notice(result),
            }
            if plan is None:
                overlay["shouldBlock"] = False
                overlay["degrade"] = False
                return overlay
            if plan.degrade_to_advisory:
                overlay["shouldBlock"] = False
                overlay["degrade"] = True
                overlay["advisoryVerdict"] = plan.advisory_verdict
                return overlay
            if plan.kind == "induce_search":
                message = build_induce_search_repair_message(result)
            else:
                registry_getter = getattr(
                    self.local_tool_evidence_collector, "source_registry_for", None
                )
                snapshot: tuple[object, ...] = ()
                if callable(registry_getter):
                    registry = registry_getter(session_id)
                    if registry is not None:
                        snapshot = registry.snapshot()
                message = build_attribution_repair_message(result, snapshot)
            overlay["shouldBlock"] = True
            overlay["degrade"] = False
            overlay["kind"] = plan.kind
            overlay["inducedSearch"] = bool(plan.induced_search)
            overlay["message"] = message
            overlay["continueRepair"] = attempt_count < max_attempts
            return overlay
        except Exception:
            return None

    def _emit_citation_verdict_record(
        self,
        *,
        session_id: str,
        turn_id: str,
        result: object,
        repair_attempts: int = 0,
        induced_search: bool = False,
        fail_open: bool = False,
        verdict_override: str | None = None,
    ) -> None:
        """Emit the ``custom:CitationVerdict`` record (design Section 8).

        ``repair_attempts`` / ``induced_search`` / ``fail_open`` are set by the
        Wave 4b repair path (0 / False / False on the audit path). A
        ``verdict_override`` lets the repair path record a degraded advisory
        verdict (e.g. ``uncited`` when induce-search is unavailable). Durably
        persisted + gate-readable via ``record_audit_evidence_for_turn``; the
        Audit tab reads THIS backend verdict (design 12.1)."""
        try:
            import time  # noqa: PLC0415

            from magi_agent.evidence.citation_gate import CitationGateResult  # noqa: PLC0415
            from magi_agent.evidence.types import EvidenceRecord  # noqa: PLC0415

            if not isinstance(result, CitationGateResult):
                return
            collector = self.local_tool_evidence_collector
            if collector is None:
                return
            verdict = verdict_override or result.verdict
            record = EvidenceRecord.model_validate(
                {
                    "type": "custom:CitationVerdict",
                    "status": "ok",
                    "observedAt": int(time.time() * 1000),
                    "source": {"kind": "custom_extractor"},
                    "fields": {
                        "verdict": verdict,
                        "highRiskClaims": len(result.high_risk_claims),
                        "citedClaims": result.cited_claims,
                        "danglingRefs": list(result.dangling_refs),
                        "repairAttempts": int(repair_attempts),
                        "inducedSearch": bool(induced_search),
                        "failOpen": bool(fail_open),
                    },
                    "metadata": {
                        "producingRuleId": "source_citation.gate",
                        "zeroSourceTurn": result.zero_source_turn,
                        "highRiskClasses": [
                            claim.claim_class for claim in result.high_risk_claims
                        ],
                        "violations": [
                            {"kind": violation.kind, "detail": violation.detail}
                            for violation in result.violations
                        ],
                    },
                }
            )
            collector.record_audit_evidence_for_turn(
                session_id=session_id,
                turn_id=turn_id,
                tool_name="source_citation.gate",
                record=record,
                tool_call_id=f"source-citation-gate:{turn_id}",
                producing_rule_id="source_citation.gate",
            )
            self._emit_citation_verdict_observability(
                session_id=session_id,
                turn_id=turn_id,
                verdict=verdict,
                cited_claims=result.cited_claims,
                high_risk_claims=len(result.high_risk_claims),
                dangling_refs=len(result.dangling_refs),
                repair_attempts=int(repair_attempts),
                induced_search=bool(induced_search),
                fail_open=bool(fail_open),
            )
        except Exception:
            return

    def _emit_citation_verdict_observability(
        self,
        *,
        session_id: str,
        turn_id: str,
        verdict: str,
        cited_claims: int,
        high_risk_claims: int,
        dangling_refs: int,
        repair_attempts: int,
        induced_search: bool,
        fail_open: bool,
    ) -> None:
        """Surface the citation gate verdict on the observability audit feed.

        Emits a ``rule_check``-family public event (the durable evidence record
        alone never reaches the observability store, only the JSONL ledger /
        gate corpus). The Audit tab reads THIS event as a normal verdict row
        (design 12.1). ``not_applicable`` turns carry no enforcement signal, so
        they are not surfaced. The event ``verdict`` stays a valid RuleVerdict
        for the generic rule_check machinery while the raw citation verdict
        rides ``citationVerdict`` (source_type == "citation"); the affordance
        scalars ride flat fields the projector preserves. Fully fail-soft."""
        if getattr(self, "_event_sink", None) is None:
            return
        if verdict == "not_applicable":
            return
        try:
            from magi_agent.runtime.public_events import rule_check_event  # noqa: PLC0415

            rule_verdict = _CITATION_VERDICT_TO_RULE_VERDICT.get(verdict, "pending")
            affordances = []
            if repair_attempts > 0:
                affordances.append(f"repaired={repair_attempts}")
            if induced_search:
                affordances.append("induced_search")
            if fail_open:
                affordances.append("fail_open")
            detail = (
                f"source citation verdict={verdict}: "
                f"cited={cited_claims} high_risk={high_risk_claims} "
                f"dangling={dangling_refs}"
            )
            if affordances:
                detail = f"{detail} ({', '.join(affordances)})"
            event = rule_check_event(
                rule_id="source_citation.gate",
                verdict=rule_verdict,
                detail=detail,
                event_family="citation_gate_alias",
            )
            event["sourceType"] = "citation"
            event["citationVerdict"] = verdict
            event["repairAttempts"] = int(repair_attempts)
            event["inducedSearch"] = bool(induced_search)
            event["failOpen"] = bool(fail_open)
            self._observe_event(dict(event), session_id, turn_id)
        except Exception:
            return

    async def _verify_nudge_check(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        final_text: str,
        verify_state: "_VerifyTurnState",
    ) -> str | None:
        """Compute the verify-before-replying nudge at a loop exit site (PR-V3).

        Shaped like ``_citation_repair_overlay``: fail-soft (any error returns
        None), flag-gated, reads the evidence collector + registry. Runs the
        deterministic auditors over the candidate final text, emits one per-pass
        rule_check observability row ALWAYS (design 7.2 allows exactly this one
        store-side row, clean pass included), and returns a nudge continuation
        message when NEW findings exist, else None. Never blocks, never yields a
        terminal, never mutates repairDecision.
        """
        try:
            import os  # noqa: PLC0415

            from magi_agent.config.env import (  # noqa: PLC0415
                parse_verify_before_replying_enabled,
            )

            # 1. master flag OFF -> byte-identical short-circuit.
            if not parse_verify_before_replying_enabled(os.environ):
                return None
            # 2. empty candidate -> nothing to audit.
            if not final_text.strip():
                return None

            from magi_agent.evidence import verify_audit  # noqa: PLC0415

            # 3. collector presence (governed_turn.py:119 semantics).
            collector = self.local_tool_evidence_collector
            collector_present = collector is not None

            # 4. turn / session corpus (empty when the collector lacks the read
            #    path: a child agent or a test stub).
            turn_records: tuple[object, ...] = ()
            session_records: tuple[object, ...] = ()
            if collector is not None:
                collect_turn = getattr(collector, "collect_for_turn", None)
                if callable(collect_turn):
                    turn_records = tuple(collect_turn(turn_id))
                collect_session = getattr(collector, "collect_for_session", None)
                if callable(collect_session):
                    session_records = tuple(collect_session(session_id))

            # 5. citation member: only when the citation gate is NOT in repair
            #    mode (composition rule, design Section 7 decision 3). In repair
            #    mode the gate owns citation violations (blocks) and the
            #    budget-exhausted residue is covered record-only by A3 in PR-V4.
            gate_result = None
            if not self._citation_repair_active():
                gate_result = self._evaluate_citation_gate_for_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                    prompt=prompt,
                    final_text=final_text,
                )

            # 6. Skeptic member (PR-V5): LLM judge, advisory, default-OFF (D3).
            #    _build_critic_factory is NEVER imported or called when the flag
            #    is OFF; the inner import guard enforces the D3 binding.
            from magi_agent.config.env import (  # noqa: PLC0415
                parse_verify_before_replying_skeptic_enabled,
            )
            skeptic_verify_findings: tuple[object, ...] = ()
            if parse_verify_before_replying_skeptic_enabled(os.environ):
                from magi_agent.adk_bridge.lifecycle_llm_call_control import (  # noqa: PLC0415
                    _build_critic_factory,
                )
                from magi_agent.customize.criterion_engine import (  # noqa: PLC0415
                    _SKEPTIC_CRITERION,
                    evaluate_criterion_findings,
                    project_evidence_for_criterion,
                )
                from magi_agent.evidence.verify_audit import (  # noqa: PLC0415
                    VerifyFinding as _VerifyFinding,
                    filter_skeptic_findings,
                    fingerprint_finding,
                )

                critic_factory = _build_critic_factory()
                if critic_factory is not None:
                    evidence_view = project_evidence_for_criterion(turn_records, [])
                    skeptic_raw = await evaluate_criterion_findings(
                        criterion=_SKEPTIC_CRITERION,
                        draft_text=final_text,
                        model_factory=critic_factory,
                        evidence_context=evidence_view,
                    )
                    raw_vf: tuple[_VerifyFinding, ...] = tuple(
                        _VerifyFinding(
                            finding_id=fingerprint_finding(
                                "verify_before_replying.skeptic_review",
                                "skeptic_overconfidence",
                                canonical_value=r.get("span", ""),
                            ),
                            rule_id="verify_before_replying.skeptic_review",
                            confidence="advisory",
                            claim_class="skeptic_overconfidence",
                            claim_text=r.get("span", ""),
                            span=(0, len(r.get("span", ""))),
                            evidence_refs=(),
                            expected=None,
                            observed=None,
                            detail=r.get("concern", ""),
                            suggested_action="consider",
                        )
                        for r in skeptic_raw
                    )
                    kept_vf, dropped = filter_skeptic_findings(raw_vf, final_text)
                    skeptic_verify_findings = kept_vf
                    verify_state.skeptic_ran = True
                    verify_state.skeptic_dropped += dropped

            # 7. Run all deterministic auditors, deduped against this turn.
            result = verify_audit.audit_candidate(
                final_text=final_text,
                prompt=prompt,
                turn_records=turn_records,
                session_records=session_records,
                gate_result=gate_result,
                collector_present=collector_present,
                surfaced_fingerprints=verify_state.surfaced,
                skeptic_findings=skeptic_verify_findings,
                skeptic_ran=verify_state.skeptic_ran,
                skeptic_dropped=verify_state.skeptic_dropped,
            )

            # 8. one per-pass observability row ALWAYS (clean pass included).
            verify_state.passes += 1
            self._emit_verify_pass_observability(
                session_id=session_id,
                turn_id=turn_id,
                result=result,
                pass_index=verify_state.passes,
            )

            # 9. no NEW findings -> deliver (silence about an old finding is a
            #    ship decision, recorded in PR-V4; the loop terminates in at most
            #    distinct-findings + 1 passes with no counter).
            if not result.new_findings:
                return None

            # 10. mark fingerprints surfaced + extend the turn history.
            for finding in result.new_findings:
                verify_state.surfaced.add(finding.finding_id)
            verify_state.history.extend(result.new_findings)
            verify_state.pending_new_count = len(result.new_findings)
            verify_state.pending_high_count = sum(
                1 for finding in result.new_findings if finding.confidence == "high"
            )

            # 11. render the nudge continuation message.
            return verify_audit.build_nudge_message(result.new_findings)
        except Exception:
            return None

    def _emit_verify_pass_observability(
        self,
        *,
        session_id: str,
        turn_id: str,
        result: object,
        pass_index: int,
    ) -> None:
        """Surface one verify audit pass on the observability audit feed (PR-V3).

        Mirrors ``_emit_citation_verdict_observability``: emits a rule_check
        family event under the ``verify_audit_alias`` family with flat verify
        fields the projector preserves. Verdict maps ``ok`` / ``violation`` /
        ``pending`` (no findings / any high finding / advisory only). Store-side
        only (never streamed), so the clean-turn stream stays byte-identical
        (A4). Fully fail-soft."""
        if getattr(self, "_event_sink", None) is None:
            return
        try:
            from magi_agent.runtime.public_events import rule_check_event  # noqa: PLC0415

            high_count = int(getattr(result, "high_count", 0))
            advisory_count = int(getattr(result, "advisory_count", 0))
            new_findings = tuple(getattr(result, "new_findings", ()))
            if high_count > 0:
                rule_verdict = "violation"
            elif advisory_count > 0:
                rule_verdict = "pending"
            else:
                rule_verdict = "ok"
            detail = (
                f"verify pass {pass_index}: "
                f"high={high_count} advisory={advisory_count} "
                f"new={len(new_findings)}"
            )
            event = rule_check_event(
                rule_id="verify_before_replying.audit",
                verdict=rule_verdict,  # type: ignore[arg-type]
                detail=detail,
                event_family="verify_audit_alias",
            )
            event["sourceType"] = "verify"
            event["policyId"] = "verify_before_replying"
            event["passIndex"] = int(pass_index)
            event["newFindings"] = len(new_findings)
            event["findings"] = [
                {
                    "findingId": str(getattr(finding, "finding_id", "")),
                    "ruleId": str(getattr(finding, "rule_id", "")),
                    "confidence": str(getattr(finding, "confidence", "")),
                    "claimClass": str(getattr(finding, "claim_class", "")),
                    "detail": str(getattr(finding, "detail", "")),
                }
                for finding in new_findings
            ]
            event["skepticRan"] = bool(getattr(result, "skeptic_ran", False))
            event["skepticDropped"] = int(
                getattr(result, "skeptic_findings_dropped", 0)
            )
            self._observe_event(dict(event), session_id, turn_id)
        except Exception:
            return

    def _emit_verify_reply_verdict(
        self,
        *,
        session_id: str,
        turn_id: str,
        verify_state: "_VerifyTurnState",
        delivered_text: str,
        context: str | None = None,
    ) -> None:
        """Emit the ``custom:VerifyReplyVerdict`` evidence record (PR-V4, design Section 12).

        Mirrors ``_emit_citation_verdict_record``: builds an EvidenceRecord via
        record_audit_evidence_for_turn, fully fail-soft. Called at two sites: (a)
        the A3 citation fail-open branch (via ``_verify_fail_open_record_only``),
        with ``context='citation_fail_open'``; (b) the normal-completion path
        (guarded on master flag AND non-empty delivered_text).

        D4-R fields: deliveredText is truncated at 20000 chars with
        ``deliveredTextTruncated: true`` when cut; deliveredTextSha256 is the
        sha256 of the FULL untruncated text. These allow offline resolution-class
        recomputation.

        The verdict record is producer_control-stamped (the record_audit_evidence_for_turn
        write path sets this). Design Section 11 note 3: no policy should ever bind
        ``custom:VerifyReplyVerdict`` as a required evidence type (has_unlock_evidence
        invariant)."""
        try:
            import hashlib  # noqa: PLC0415
            import os  # noqa: PLC0415
            import time  # noqa: PLC0415

            from magi_agent.config.env import (  # noqa: PLC0415
                parse_verify_before_replying_enabled,
            )
            from magi_agent.evidence import verify_audit  # noqa: PLC0415
            from magi_agent.evidence.types import EvidenceRecord  # noqa: PLC0415

            if not parse_verify_before_replying_enabled(os.environ):
                return
            if not delivered_text.strip():
                return
            collector = self.local_tool_evidence_collector
            if collector is None:
                return
            record_audit = getattr(collector, "record_audit_evidence_for_turn", None)
            if not callable(record_audit):
                return

            # Build the evidence corpus for resolution computation.
            turn_records: tuple[object, ...] = ()
            session_records: tuple[object, ...] = ()
            collect_turn = getattr(collector, "collect_for_turn", None)
            collect_session = getattr(collector, "collect_for_session", None)
            if callable(collect_turn):
                turn_records = tuple(collect_turn(turn_id))
            if callable(collect_session):
                session_records = tuple(collect_session(session_id))

            # Citation member: skip when in repair mode (design Section 7 decision 3:
            # in repair mode the gate owns citation violations; the budget-exhausted
            # residue is covered record-only by A3 in _verify_fail_open_record_only).
            gate_result = None
            if not self._citation_repair_active():
                gate_result = self._evaluate_citation_gate_for_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                    prompt="",
                    final_text=delivered_text,
                )

            # Resolution: re-run detectors on the delivered text (design 12.3).
            resolutions = verify_audit.resolve_findings(
                verify_state.history,
                delivered_text,
                turn_records=turn_records,
                session_records=session_records,
                gate_result=gate_result,
                collector_present=True,
                ship_marker_used=bool(verify_state.ship_marker_used),
            )

            # Compute ignore-rate stats and turn-level verdict.
            stats = verify_audit.ignore_rate_summary(resolutions)
            high_total = int(stats["highTotal"])
            high_resolved = int(stats["highResolved"])
            high_acked = int(stats["highAcknowledged"])
            high_ignored = int(stats["highIgnored"])
            if high_total == 0:
                verdict = "verified_clean"
            elif high_ignored > 0:
                verdict = "nudge_ignored"
            elif bool(verify_state.ship_marker_used):
                verdict = "shipped_acknowledged"
            elif high_resolved > 0:
                verdict = "revised"
            else:
                verdict = "verified_clean"

            # D4-R: store truncated delivered text + full-text sha256.
            max_chars = 20000
            truncated = len(delivered_text) > max_chars
            stored_text = delivered_text[:max_chars] if truncated else delivered_text
            sha256_hex = hashlib.sha256(delivered_text.encode("utf-8")).hexdigest()

            findings_list = [
                {
                    "findingId": str(getattr(finding, "finding_id", "")),
                    "ruleId": str(getattr(finding, "rule_id", "")),
                    "confidence": str(getattr(finding, "confidence", "")),
                    "resolution": resolution,
                }
                for finding, resolution in resolutions
            ]

            fields: dict[str, object] = {
                "verdict": verdict,
                "turnId": turn_id,
                "passes": int(verify_state.passes),
                "findings": findings_list,
                "highTotal": high_total,
                "highResolved": high_resolved,
                "highAcknowledged": high_acked,
                "highIgnored": high_ignored,
                "shipMarkerUsed": bool(verify_state.ship_marker_used),
                "loopBackToolCalls": int(verify_state.loopback_tool_calls),
                "deliveredText": stored_text,
                "deliveredTextSha256": sha256_hex,
            }
            if truncated:
                fields["deliveredTextTruncated"] = True
            if context is not None:
                fields["context"] = context

            record = EvidenceRecord.model_validate(
                {
                    "type": "custom:VerifyReplyVerdict",
                    "status": "ok",
                    "observedAt": int(time.time() * 1000),
                    "source": {"kind": "custom_extractor"},
                    "fields": fields,
                    "metadata": {
                        "producingRuleId": "verify_before_replying.audit",
                    },
                }
            )
            record_audit(
                session_id=session_id,
                turn_id=turn_id,
                tool_name="verify_before_replying.audit",
                record=record,
                tool_call_id=f"verify-before-replying:{turn_id}",
                producing_rule_id="verify_before_replying.audit",
            )
        except Exception:
            return

    async def _verify_fail_open_record_only(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        emitted_text: str,
        verify_state: "_VerifyTurnState",
    ) -> None:
        """Run a record-only verify audit inside the citation fail-open branch (A3, PR-V4).

        Hedge-suffix exclusion invariant: emitted_text here is the primary answer
        text BEFORE the fail-open notice suffix is yielded. The notice, the
        soft-consequence suffix, and the hard-blocked notice are all yielded as
        token deltas WITHOUT mutating emitted_text, so emitted_text is by
        construction the suffix-free delivered answer at this call site. Any
        future mutation of emitted_text after this call would silently corrupt
        resolution stats (the PR-V1 test_hedge_notice_never_trips_detectors
        fixture is the tripwire for the claim detector side; the D4-R sha256 in
        the verdict record is the tripwire for the storage side).

        Runs audit_candidate (no dedup consumption beyond marking, no nudge, no
        continuation), increments verify_state.passes, emits one per-pass
        observability row via _emit_verify_pass_observability, then emits the
        verdict record via _emit_verify_reply_verdict with context='citation_fail_open'.
        Fully fail-soft."""
        try:
            import os  # noqa: PLC0415

            from magi_agent.config.env import (  # noqa: PLC0415
                parse_verify_before_replying_enabled,
            )
            from magi_agent.evidence import verify_audit  # noqa: PLC0415

            if not parse_verify_before_replying_enabled(os.environ):
                return
            if not emitted_text.strip():
                return

            collector = self.local_tool_evidence_collector
            collector_present = collector is not None

            turn_records: tuple[object, ...] = ()
            session_records: tuple[object, ...] = ()
            if collector is not None:
                collect_turn = getattr(collector, "collect_for_turn", None)
                collect_session = getattr(collector, "collect_for_session", None)
                if callable(collect_turn):
                    turn_records = tuple(collect_turn(turn_id))
                if callable(collect_session):
                    session_records = tuple(collect_session(session_id))

            # Citation member: always None in the fail-open branch (design Section 7
            # decision 3: citation repair mode is active here, so the gate owns the
            # citation violations; the verify layer observes without citation context).
            result = verify_audit.audit_candidate(
                final_text=emitted_text,
                prompt=prompt,
                turn_records=turn_records,
                session_records=session_records,
                gate_result=None,
                collector_present=collector_present,
                surfaced_fingerprints=verify_state.surfaced,
                skeptic_findings=(),
                skeptic_ran=verify_state.skeptic_ran,
                skeptic_dropped=verify_state.skeptic_dropped,
            )

            # Mark fingerprints surfaced and extend history (no nudge, no dedup
            # consumption beyond fingerprint marking).
            for finding in result.new_findings:
                verify_state.surfaced.add(finding.finding_id)
            verify_state.history.extend(result.new_findings)
            verify_state.passes += 1

            # One per-pass observability row (design 7.2).
            self._emit_verify_pass_observability(
                session_id=session_id,
                turn_id=turn_id,
                result=result,
                pass_index=verify_state.passes,
            )

            # Verdict record with context marker.
            self._emit_verify_reply_verdict(
                session_id=session_id,
                turn_id=turn_id,
                verify_state=verify_state,
                delivered_text=emitted_text,
                context="citation_fail_open",
            )
        except Exception:
            return

    @property
    def runner_policy_assembly(self) -> RunnerPolicyAssembly | None:
        return self._runner_policy_assembly

    def _is_runner_policy_routing_enabled(self) -> bool:
        if self._runner_policy_routing_enabled is not None:
            return self._runner_policy_routing_enabled
        return _runner_policy_routing_enabled()

    def _observe_event(self, payload: dict, session_id: str, turn_id: str) -> None:
        sink = self._event_sink
        if sink is None:
            return
        try:
            sink(payload, session_id, turn_id)
        except Exception:
            logger.debug("observability event sink failed", exc_info=True)

    def _get_registry(self) -> object:
        if self._registry is None:
            self._registry = _active_turn_registry()
        return self._registry

    def _resolve_runner(self, runtime: object) -> object | None:
        if self._runner is not None:
            return self._runner
        if runtime is None:
            return None
        # A wired runtime may expose `.runner`; otherwise treat `runtime` itself
        # as the runner (DI-friendly: tests can pass a bare mock runner).
        return getattr(runtime, "runner", runtime)

    @staticmethod
    def _turn_identity(turn_input: object) -> tuple[str, str, str]:
        """Derive (session_id, turn_id, prompt) from the headless turn_input.

        ``run_headless`` passes ``{"prompt": prompt}``; production callers may
        pass a richer object (a ``TurnInput`` dataclass or any attribute-bearing
        object). We accept either a mapping or an attribute-bearing object and
        fall back to sane defaults.
        """

        def _get(key: str, default: str) -> str:
            if isinstance(turn_input, dict):
                value = turn_input.get(key, default)
            else:
                value = getattr(turn_input, key, default)
            return value if isinstance(value, str) and value else default

        session_id = _get("session_id", "cli-session")
        turn_id = _get("turn_id", "cli-turn")
        prompt = _get("prompt", "")
        if not prompt:
            prompt = _get("message_text", "")
        return session_id, turn_id, prompt

    @staticmethod
    def _turn_extra(turn_input: object) -> tuple[object | None, list]:
        """Read the additive ``harness_state`` / ``initial_messages`` seams.

        Works for BOTH a bare dict (``run_headless`` passes ``{"prompt": ...}``)
        and a ``TurnInput`` dataclass / attribute-bearing object. When the key is
        absent (the dict case today) ``harness_state`` is ``None`` and
        ``initial_messages`` is ``[]`` — identical to pre-A3 behavior.
        """

        def _attr(key: str, default: object) -> object:
            if isinstance(turn_input, dict):
                return turn_input.get(key, default)
            return getattr(turn_input, key, default)

        harness_state = _attr("harness_state", None)
        initial_messages = _attr("initial_messages", [])
        if not isinstance(initial_messages, list):
            initial_messages = []
        return harness_state, initial_messages

    @staticmethod
    def _turn_images(turn_input: object) -> tuple[dict[str, object], ...]:
        """Read ``image_blocks`` from a TurnInput dataclass or a bare dict.

        Works the same dict-or-attr pattern as ``_turn_extra``. Returns an
        empty tuple when the field is absent (e.g. a bare ``{"prompt": "…"}``
        dict from ``run_headless``), which preserves pre-Task-2 behavior.
        """
        if isinstance(turn_input, dict):
            value = turn_input.get("image_blocks", ())
        else:
            value = getattr(turn_input, "image_blocks", ())
        return tuple(value or ())

    @staticmethod
    def _build_opening_parts(types: object, prompt: str, image_blocks: tuple) -> list:
        """Build the ``parts`` list for the opening user message in a turn.

        Always starts with a text part for ``prompt``, then appends one ADK
        image part per valid block in ``image_blocks`` (base64 blocks only;
        malformed / unsupported blocks are silently skipped by the gate5b4c3
        helper).  The text part uses the same ``types.Part(text=...)``
        constructor form used at all other build sites so that existing
        fake-types test doubles continue to work without modification.  The
        image factory (``types.Part.from_bytes``) is only referenced when
        there are actually image blocks to process, so empty-block callers
        never touch that attribute.
        """
        parts: list = [types.Part(text=prompt)]  # type: ignore[union-attr]
        if image_blocks:
            from magi_agent.shadow.gate5b4c3_image_parts import (  # noqa: PLC0415
                image_blocks_to_parts,
            )

            parts.extend(
                image_blocks_to_parts(
                    list(image_blocks),
                    part_factory=types.Part.from_bytes,  # type: ignore[union-attr]
                )
            )
        return parts

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: asyncio.Event,
        gate: "PermissionGate | None" = None,
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        # Stream F wires permission interception: ``gate`` (when not None) is
        # threaded into ``_drive``, which attaches an ADK ``before_tool_callback``
        # so the gate intercepts every tool BEFORE it executes. ``gate=None``
        # leaves behavior byte-for-byte identical to pre-F.
        session_id, turn_id, prompt = self._turn_identity(turn_input)
        harness_state, initial_messages = self._turn_extra(turn_input)
        image_blocks = self._turn_images(turn_input)

        registry = self._get_registry()
        acquired = registry.try_acquire(session_key=session_id, turn_id=turn_id)  # type: ignore[attr-defined]
        if not acquired:
            # A turn is already active for this session. Do NOT run.
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.aborted,
                usage={},
                cost_usd=0.0,
                error="active_session_turn",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        # async-for delegation does NOT propagate aclose()/GeneratorExit into the
        # sub-generator, so on an early/mid-stream consumer aclose() (interactive
        # cancel) `_drive`'s finally (which closes the ADK iterator) would be
        # deferred to GC. Hold the sub-generator and explicitly close it in a
        # finally so cleanup is prompt. The single-flight release is also in the
        # finally; it runs exactly once on every path (normal / cancel /
        # exception / early-aclose).
        driver_gen = self._drive(
            runtime=runtime,
            session_id=session_id,
            turn_id=turn_id,
            prompt=prompt,
            harness_state=harness_state,
            initial_messages=initial_messages,
            image_blocks=image_blocks,
            cancel=cancel,
            gate=gate,
            goal_nudge=self._goal_nudge,
            output_continuation=self._output_continuation,
            empty_response_recovery=self._empty_response_recovery,
            no_tool_finalizer=self._no_tool_finalizer,
            goal_loop_judge_factory=self._goal_loop_judge_factory,
            ambient_goal_policy_factory=self._ambient_goal_policy_factory,
            evidence_first=self._evidence_first,
            plan_ledger_reader=self._plan_ledger_reader,
            required_evidence=self._required_evidence,
        )
        # PR-H: track terminal kind + accumulated text-delta length + exit
        # cause so the engine.trace run_turn_stream_finalize stamp can tell
        # the operator WHICH layer finalized the turn. Default-OFF (gated
        # on existing MAGI_CHILD_RUNNER_EMPTY_DEBUG); zero-cost when unset.
        _finalize_terminal_cls: object = None
        _finalize_text_len = 0
        _finalize_exception_cls: type | None = None
        try:
            async for item in driver_gen:
                try:
                    payload = getattr(item, "payload", None)
                    if isinstance(payload, dict) and payload.get("type") == "text_delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str):
                            _finalize_text_len += len(delta)
                    if isinstance(item, EngineResult):
                        _finalize_terminal_cls = item.__class__.__name__
                except Exception:  # noqa: BLE001 - never let trace bookkeeping break the turn.
                    pass
                yield item  # RuntimeEvent OR the terminal EngineResult
        except BaseException as exc:
            _finalize_exception_cls = exc.__class__
            raise
        finally:
            # FIX 3 (global review): release() MUST run even if aclose() raises,
            # else the session's single-flight slot leaks and every future turn
            # for this session is rejected as ``active_session_turn``.
            try:
                # PR-H: stamp the canonical "did the engine finalize?" line.
                # Logged INSIDE the finally so it lands on every exit path
                # (normal completion, mid-stream raise, consumer aclose).
                try:
                    import os as _os  # noqa: PLC0415

                    from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
                        _maybe_log_trace_engine_run_turn_stream_finalize,
                    )

                    _maybe_log_trace_engine_run_turn_stream_finalize(
                        _os.environ,
                        turn_id=turn_id,
                        terminal=_finalize_terminal_cls,
                        text_len=_finalize_text_len,
                        exception=_finalize_exception_cls,
                    )
                except Exception:  # noqa: BLE001 - trace must never break a turn.
                    pass
                await driver_gen.aclose()
            finally:
                registry.release(session_key=session_id, turn_id=turn_id)  # type: ignore[attr-defined]

    async def _drive(
        self,
        *,
        runtime: object,
        session_id: str,
        turn_id: str,
        prompt: str,
        harness_state: object | None = None,
        initial_messages: list | None = None,
        image_blocks: tuple = (),
        cancel: asyncio.Event,
        gate: "PermissionGate | None" = None,
        goal_nudge: "GoalNudge | None" = None,
        output_continuation: "OutputContinuationConfig | None" = None,
        empty_response_recovery: "EmptyResponseRecoveryConfig | None" = None,
        no_tool_finalizer: "NoToolFinalizerConfig | None" = None,
        goal_loop_judge_factory: Callable[..., object] | None = None,
        ambient_goal_policy_factory: Callable[[str], object | None] | None = None,
        evidence_first: bool = False,
        plan_ledger_reader: Callable[[str], Sequence[object]] | None = None,
        required_evidence: tuple[str, ...] = (),
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        # PR-04-PR2 (resume rehydration): consume ``initial_messages`` by
        # synthesizing the prior transcript into a prefix on the opening user
        # prompt, so a ``--resume``/``--continue`` turn replays the prior
        # conversation to the model. ``ResumeContext.initial_messages`` is the
        # source (already reconstructed by session_log.reconstruct_messages).
        #
        # This is the lightweight JSONL-transcript path. The richer ADK-native
        # rehydration (importing a committed transcript into a live
        # SessionContinuityBoundary) is carried on ``ResumeContext`` but wired by
        # the SQLite-persistence PR; here we keep the no-runner-dependency path.
        #
        # No-op / byte-identical invariant: an empty (or non-list) value leaves
        # ``prompt`` untouched, so a fresh session is unchanged from pre-PR2.
        resume_prefix = _render_resume_prefix(initial_messages)
        if resume_prefix:
            prompt = f"{resume_prefix}{prompt}"

        # Root-cause-1: per-turn scope for observed ADK invocation ids. Reset at
        # the start of every drive so one turn's reconciliation set never leaks
        # into the next on a reused engine instance.
        self._observed_invocation_ids = set()

        runner = self._resolve_runner(runtime)
        if runner is None:
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.error,
                usage={},
                cost_usd=0.0,
                error="no_runner",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        route_selection = self._runner_policy_route_selection(
            runner=runner,
            prompt=prompt,
            harness_state=harness_state,
        )
        # Stage 3: emit a phase-reached evidence record for the phase the route
        # selection resolved. This is the ONLY live seam where a concrete phase
        # name is known together with (session_id, turn_id) and the evidence
        # collector. Flag-gated + fail-open inside the collector; a None route
        # selection (routing OFF / no phase routes) records nothing.
        if route_selection is not None:
            self._record_phase_reached(
                session_id=session_id,
                turn_id=turn_id,
                phase=route_selection.get("phase"),
            )
        policy_payload = self._runner_policy_payload()
        route_decision = (
            self._runner_policy_assembly.phase_route_decision()
            if self._runner_policy_assembly is not None
            else None
        )

        if policy_payload is not None:
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "runner_policy_assembly",
                    "turnId": turn_id,
                    **policy_payload,
                },
                turn_id=turn_id,
            )
        if route_selection is not None:
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "runner_policy_route_selection",
                    "turnId": turn_id,
                    **route_selection,
                },
                turn_id=turn_id,
            )

        # D1: consume the materialized phase route. The recipe materializer
        # already routes per-phase model/tier + verifier escalation into the
        # assembly. Surface a distilled routing decision so CLI / dashboard /
        # observability surfaces can act on it.
        if route_decision is not None:
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "phase_route_decision",
                    "turnId": turn_id,
                    **route_decision,
                },
                turn_id=turn_id,
            )

        # D1 active route consumption: a denied materialized route is projected
        # as an audit event by default, while the turn continues on the
        # configured model/tools. This keeps the route policy visible without
        # letting stale conservative cost/capability estimates break live turns.
        # Operators can explicitly re-arm fail-closed blocking with
        # MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED=1.
        route_block = self._runner_policy_route_block_payload(
            route_selection=route_selection,
            turn_id=turn_id,
            fail_closed=_runner_policy_route_blocking_enabled(),
        )
        if route_block is not None:
            yield RuntimeEvent(type="status", payload=route_block, turn_id=turn_id)
            if route_block.get("routeDecision") == "blocked_before_provider_call":
                yield EngineResult(  # type: ignore[misc]
                    terminal=Terminal.error,
                    usage={},
                    cost_usd=0.0,
                    error="runner_policy_route_denied",
                    session_id=session_id,
                    turn_id=turn_id,
                )
                return

        try:
            deps = _lazy_engine_deps()
        except Exception as exc:  # pragma: no cover - import failure path
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.error,
                usage={},
                cost_usd=0.0,
                error=f"engine_import_failed: {exc}",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        # Output-continuation helpers (pure, dependency-light). Imported here so
        # ``import cli.engine`` stays cold-clean.
        from magi_agent.runtime.output_continuation import (  # noqa: PLC0415
            build_continuation_message,
            should_continue,
            stop_reason_is_truncated,
        )

        # Empty-response recovery helpers (R2, pure, dependency-light). Same
        # deferred-import pattern as output_continuation above.
        from magi_agent.runtime.empty_response_recovery import (  # noqa: PLC0415
            build_blocked_notice,
            build_empty_response_message,
            build_grace_message,
            select_recovery_message,
            should_grace,
            should_recover_empty,
        )
        from magi_agent.runtime.no_tool_finalizer import (  # noqa: PLC0415
            should_run_no_tool_finalizer,
        )

        types = deps["types"]
        # B5: pass the driver-owned num_recent_events knob so the adapter can
        # inject GetSessionConfig on its RunConfig. ``None`` (default) keeps the
        # CLI and non-durable hosted paths byte-identical to pre-B5.
        adapter = deps["OpenMagiRunnerAdapter"](  # type: ignore[operator]
            runner=runner,
            num_recent_events=self._num_recent_events,
        )
        # Pass ``wire_profile`` ONLY when one is set (hosted path). On the CLI
        # path (``None``) we omit the kwarg entirely so the construction is
        # byte-identical to pre-wire-profile — and test doubles injected via the
        # ``deps`` seam (whose ``__init__`` predates this kwarg) keep working.
        bridge_kwargs: dict[str, object] = {"live_compatible": True}
        if self._wire_profile is not None:
            bridge_kwargs["wire_profile"] = self._wire_profile
        bridge = deps["OpenMagiEventBridge"](**bridge_kwargs)  # type: ignore[operator]
        sanitize = deps["sanitize_agent_event"]
        runner_turn_input_cls = deps["RunnerTurnInput"]
        effective_harness_state = self._with_runner_policy_harness_state(
            harness_state,
            route_selection=route_selection,
        )

        runner_input = runner_turn_input_cls(
            userId=self._user_id,
            sessionId=session_id,
            turnId=turn_id,
            invocationId=turn_id,
            newMessage=types.Content(  # type: ignore[attr-defined]
                role="user",
                parts=self._build_opening_parts(types, prompt, image_blocks),
            ),
            # Threaded from the turn_input (TurnInput.harness_state / dict key).
            # A plain dict without the key leaves this None — identical to today.
            harnessState=effective_harness_state,
        )
        # U5 (design 5.1 / A-1): capture the ORIGINAL user text of this turn ONCE,
        # from the FIRST runner_input, BEFORE any re-invocation (recovery / nudge /
        # continuation) replaces ``runner_input``. Used solely as the objective for
        # driver-side ambient GoalLoopPolicy synthesis at the clean break. An empty
        # capture -> the ambient factory returns ``None`` -> the turn is unchanged.
        objective_text = _extract_objective_text(runner_input)

        # Tracks tool_use ids we emitted (tool_start) but have not yet seen a
        # matching tool_end for. Used to synthesize orphan tool_results on cancel.
        pending_tool_ids: dict[str, str] = {}
        event_count = 0
        usage: dict[str, object] = {}
        observed_public_refs: set[str] = set()
        emitted_text = ""

        # Permission interception (Stream F): attach a before_tool_callback to
        # the runner's agent so the gate intercepts every tool BEFORE it runs.
        # The agent is per-RUNNER (not per-turn); two concurrent turns sharing
        # one runner but DIFFERENT gates would race on this attribute. The CLI
        # runs one turn at a time per session (the single-flight
        # ``ActiveTurnRegistry`` enforces this), so it is safe here — but a
        # shared-runner SERVER must NOT assume this. The original value is always
        # restored in the ``finally`` below, on every exit path.
        gate_attach = self._attach_gate_callback(
            runner=runner, gate=gate, turn_id=turn_id, cancel=cancel
        )
        # Cluster doc 11 PR2: bridge user settings.json hooks onto the agent's
        # before/after-tool callbacks AFTER the gate (so a gate deny still
        # short-circuits first; conflict-matrix order:
        # gate -> user hook -> control-plane -> runner_policy_route). No-op when
        # ``_user_hook_bus`` is None (gate OFF) -> byte-identical to today.
        hook_attach = self._attach_user_hook_bus(
            runner=runner, session_id=session_id, turn_id=turn_id
        )
        # Third agent-level bridge: authored customize tool-boundary rules,
        # appended AFTER the gate + user hook (order: gate -> user hook ->
        # customize). No-op when every customize slot is OFF. Restored in the
        # ``finally`` below, before the user-hook + gate restores (LIFO).
        customize_attach = self._attach_customize_rules(
            runner=runner, session_id=session_id, turn_id=turn_id
        )
        route_attach = self._attach_runner_policy_route(
            runner=runner,
            route_selection=route_selection,
        )

        cancelled = False
        engine_error: str | None = None
        # PR-3: when ``engine_error`` is set, also stash the structured upstream-
        # exception detail (errorClass + sanitized message + traceback preview)
        # so the orphan sweep + a new ``engine_error_detail`` status event can
        # surface the REAL trigger to the dashboard. ``None`` outside the error
        # path; reset alongside ``engine_error``.
        engine_error_detail: dict[str, object] | None = None
        # Number of agent RuntimeEvents actually yielded to the consumer across
        # ALL attempts. Recovery only re-invokes the run while this is 0, so a
        # mid-stream failure never replays already-delivered output.
        yielded_events = 0
        # Per-turn recovery attempt state (the existing RecoveryEngine threads
        # its per-strategy budget through this).
        recovery_state: "RecoveryAttemptState | None" = None
        recovery_attempts = 0
        repair_attempts = 0

        # PR4 goal-nudge state. Only active when goal_nudge is not None.
        # nudges_used: hard cap counter (anti-infinite-loop).
        # goal_check_pending: mode="goal" latch — True after one nudge fires per
        # consecutive clean stop; reset to False when a tool fires (re-arm).
        nudges_used = 0
        goal_check_pending = False
        # PR-C goal-loop state. Only active when a GoalLoopPolicy is published
        # on the per-turn ContextVar by PR-B (i.e. the user opted into the
        # composer's Goal-mission toggle AND MAGI_GOAL_LOOP_ENABLED is on).
        # Otherwise these counters never advance and the new branch is skipped.
        goal_loop_continuations = 0
        goal_loop_judge_parse_failures = 0
        goal_loop_judge_caller: object | None = None
        # Ledger-first auto-continue state (the highest-leverage fix). Gives
        # SEAM 2's already-computed "continue" verdict re-invocation authority so
        # a mid-multi-step-task clean break resumes instead of stopping with
        # "I'll continue...". Only advances when ``_auto_continue_enabled`` (the
        # profile-aware MAGI_GOAL_LOOP_ENABLED flag) is on; otherwise the SEAM 2
        # code below is byte-identical to the historic bare break.
        auto_continue_used = 0
        auto_continue_no_progress_streak = 0
        auto_continue_wrap_up_spent = False
        # U2 substance signal S1 (design 4.2): turn-cumulative tool_end counter.
        # Counts every tool_end this turn (ok OR blocked), across ALL
        # re-invocations (recovery / nudge / continuation), and is NEVER reset
        # per attempt. Inert in this unit; the ambient substance gate (U5) reads
        # it later. ``_last_turn_tool_ends_total`` mirrors it as a private
        # test-observation seam; no live branch reads it, so turn output stays
        # byte-identical.
        turn_tool_ends_total = 0
        self._last_turn_tool_ends_total = 0
        # Snapshots captured BEFORE each attempt so the progress gate can compute
        # a ledger delta / new-evidence delta across the just-finished attempt.
        auto_continue_prev_ledger: tuple[object, ...] = ()
        auto_continue_prev_evidence_count = 0
        # Per-attempt ok / blocked tool-end counters. Reset at the top of every
        # attempt; folded into the progress signal at the clean break. A blocked
        # or needs-approval tool end projects (public_events) as status != "ok",
        # so it never counts as progress.
        auto_continue_ok_tool_ends = 0
        auto_continue_blocked_tool_ends = 0
        # Monotonic turn clock for the wall-clock budget backstop.
        import time as _auto_continue_time  # noqa: PLC0415

        auto_continue_turn_start = _auto_continue_time.monotonic()
        # Output-continuation budget: how many times we've resumed a response
        # truncated at the model's per-response output-token cap this turn.
        continuations_used = 0
        # R2 empty-response recovery budgets. When empty_response_recovery is
        # None/disabled the decision helpers always return False and
        # grace_event_extra stays 0, so the budget comparison and control flow
        # below are byte-identical to pre-R2.
        recoveries_used = 0
        graces_used = 0
        grace_event_extra = 0
        # PR5b: monotonic "any NET user-visible text reached the consumer this
        # turn" flag. Set True on every streamed text_delta with a non-empty
        # delta and NEVER reset by a response_clear (unlike ``emitted_text``,
        # which a response_clear blanks). The terminal-side escalated_blank guard
        # uses this so a turn that streamed text and then cleared it is not
        # mis-classified as "never produced text". Stays False when escalation /
        # recovery is OFF, so the OFF path is byte-identical.
        net_user_text_streamed = False
        # P3 zero-edit guard: count file-mutating tool calls this turn.
        # zero_edit_retry_done ensures we fire the guard at most once per turn.
        file_edit_calls = 0
        zero_edit_retry_done = False

        # PR-K: per-turn 1-based dispatch-attempt counter for the
        # ``engine.trace llm_call_*`` stamps. Increments at every outer-loop
        # iteration of ``_drive`` (so output-continuation, goal-nudge,
        # grace, empty-response-recovery, and rate-limit recovery re-
        # invocations all advance it). Default-OFF for the trace; the
        # counter itself costs nothing.
        llm_call_attempt = 0

        try:
            while True:
                # (Re-)invoke the run: a FRESH ``adapter.run_turn`` is a fresh
                # ``Runner.run_async`` and therefore a real model call. On the
                # first iteration this is the original invocation; on a recovery
                # retry it is the genuine second invocation.
                llm_call_attempt += 1
                # PR-K: stamp BEFORE the adapter.run_turn dispatch. Paired
                # with the matching ``llm_call_completed`` / ``llm_call_
                # exception`` stamps below so the operator can see whether
                # the engine entered a fresh dispatch attempt at all, and
                # whether it exited that attempt normally vs by raising.
                try:
                    import os as _pr_k_os  # noqa: PLC0415

                    from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
                        _maybe_log_trace_engine_llm_call_start,
                    )

                    _maybe_log_trace_engine_llm_call_start(
                        _pr_k_os.environ,
                        attempt=llm_call_attempt,
                        turn_id=turn_id,
                    )
                except Exception:  # noqa: BLE001 - trace must never break a turn.
                    pass
                adk_iter: AsyncIterator[object] = (
                    adapter.run_turn(runner_input).__aiter__()  # type: ignore[union-attr]
                )
                attempt_error: Exception | None = None
                attempt_yielded = 0
                # Set when this attempt's final response stopped at the output
                # token cap (finish_reason length/max_tokens) — resumable.
                attempt_truncated = False
                # R2 per-attempt bookkeeping: did a tool run / was any
                # user-visible output emitted / did this attempt hit the event
                # budget. Only written when empty_response_recovery is set, so
                # the OFF path is untouched.
                attempt_tool_ran = False
                attempt_text_seen = False
                budget_exhausted = False
                # Auto-continue: reset the per-attempt ok / blocked tool-end
                # counters and capture the pre-attempt ledger + evidence-count
                # snapshots so the clean-break progress gate can diff them.
                auto_continue_ok_tool_ends = 0
                auto_continue_blocked_tool_ends = 0
                if self._auto_continue_enabled:
                    auto_continue_prev_ledger = (
                        tuple(plan_ledger_reader(session_id))
                        if plan_ledger_reader is not None
                        else ()
                    )
                    auto_continue_prev_evidence_count = len(
                        self._collect_evidence(turn_id)
                    )
                # Per-attempt token usage. ADK usage_metadata is cumulative WITHIN
                # one run_async stream, so we last-writer-wins into this dict here
                # and SUM it into the turn-level ``usage`` in the finally below.
                attempt_usage: dict[str, int] = {}
                try:
                    while True:
                        if cancel.is_set():
                            cancelled = True
                            break

                        step = await self._next_adk_event(adk_iter, cancel)
                        if step is _CANCELLED:
                            cancelled = True
                            break
                        if step is _EXHAUSTED:
                            break

                        adk_event = step
                        event_count += 1
                        # Root-cause-1: note the ADK invocation id so the
                        # pre-final gate can reconcile it with the engine turn id.
                        self._note_observed_invocation_id(
                            _adk_invocation_id(adk_event)
                        )
                        reading = _adk_usage_metadata(adk_event)
                        if reading:
                            attempt_usage.update(reading)
                        # Detect output-cap truncation from the RAW model finish
                        # reason — the source of truth. The bridge's turn_end
                        # projection can rewrite the reason (e.g. to
                        # ``missing_runtime_receipt``), so we must read it here.
                        if output_continuation is not None and not attempt_truncated:
                            if stop_reason_is_truncated(
                                _adk_finish_reason(adk_event)
                            ):
                                attempt_truncated = True
                        projection = bridge.project_adk_event(adk_event, turn_id=turn_id)  # type: ignore[union-attr]
                        projected_events: list[Mapping[str, object]] = []
                        for raw_event in _projected_events_with_transcript_text_fallback(
                            projection,
                            emitted_text=emitted_text,
                        ):
                            safe = sanitize(dict(raw_event))  # type: ignore[operator]
                            if safe is None:
                                continue
                            projected_events.append(safe)

                        will_continue_attempt = should_continue(
                            output_continuation,
                            truncated=attempt_truncated,
                            output_seen=attempt_yielded > 0 or any(
                                _is_continuation_output_event(event)
                                for event in projected_events
                            ),
                            continuations_used=continuations_used,
                        )
                        for safe in projected_events:
                            if will_continue_attempt and _is_turn_end_event(safe):
                                continue
                            self._collect_public_refs(safe, observed_public_refs)
                            self._track_pending_tool(safe, pending_tool_ids)
                            # P3 zero-edit guard: count file-mutating tool calls.
                            if safe.get("type") == "tool_start" and safe.get("name") in _EDIT_CLASS_TOOLS:
                                file_edit_calls += 1
                            if safe.get("type") == "response_clear":
                                emitted_text = ""
                            elif safe.get("type") == "text_delta":
                                delta = safe.get("delta")
                                if isinstance(delta, str):
                                    emitted_text += delta
                                    if delta:
                                        net_user_text_streamed = True
                            # PR4 goal-nudge: reset the goal-mode latch whenever a
                            # tool fires so the next clean stop is eligible for a
                            # nudge again (re-arm).
                            if goal_nudge is not None and safe.get("type") == "tool_start":
                                goal_check_pending = False
                            # Auto-continue progress gate: count ok vs blocked /
                            # needs-approval tool ends this attempt. The public
                            # projection normalizes tool_end.status to "ok" | "error"
                            # (a blocked / needs-approval / failed tool end is
                            # "error"), so an "error" status is exactly the
                            # non-progress family the gate must NOT count as work.
                            if self._auto_continue_enabled and safe.get("type") == "tool_end":
                                if safe.get("status") == "ok":
                                    auto_continue_ok_tool_ends += 1
                                else:
                                    auto_continue_blocked_tool_ends += 1
                            # U2 substance signal S1 (inert until U5): count every
                            # tool_end this turn, ok OR blocked, ungated by the
                            # auto-continue flag, and NEVER reset per attempt so
                            # the count stays turn-cumulative across re-invocations.
                            # Mirrored to a private attr purely so the inert counter
                            # is unit-testable; no live branch reads it, so turn
                            # output is byte-identical.
                            if safe.get("type") == "tool_end":
                                turn_tool_ends_total += 1
                                self._last_turn_tool_ends_total = turn_tool_ends_total
                            # R2: classify this attempt's activity for the
                            # empty-response decision. Tool events are tracked
                            # separately; "text seen" reuses the continuation
                            # output classifier minus the tool family.
                            if empty_response_recovery is not None:
                                if safe.get("type") == "tool_start":
                                    attempt_tool_ran = True
                                elif safe.get(
                                    "type"
                                ) not in _TOOL_EVENT_TYPES and _is_continuation_output_event(
                                    safe
                                ):
                                    attempt_text_seen = True
                            attempt_yielded += 1
                            yielded_events += 1
                            self._observe_event(safe, session_id, turn_id)
                            yield RuntimeEvent(
                                type=_map_event_kind(safe.get("type")),
                                payload=safe,
                                turn_id=turn_id,
                            )

                        # R2: grace_event_extra is 0 unless the single grace
                        # re-invocation fired, so the OFF-path comparison is
                        # unchanged. event_count is cumulative across attempts;
                        # the allowance is ADDED to the cap (a reset would
                        # re-break the grace attempt after one event).
                        if event_count >= self._max_event_count + grace_event_extra:
                            budget_exhausted = True
                            break
                except Exception as exc:  # noqa: BLE001 - surface as terminal error
                    # PR-K: stamp the dispatch-side exception BEFORE the
                    # existing recovery flow captures it into attempt_error.
                    # The recovery layer still decides whether to re-invoke;
                    # this trace fires independent of that decision so the
                    # operator sees the failure shape regardless of whether
                    # the engine retried or surfaced the terminal error.
                    try:
                        import os as _pr_k_os  # noqa: PLC0415

                        from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
                            _maybe_log_trace_engine_llm_call_exception,
                        )

                        _maybe_log_trace_engine_llm_call_exception(
                            _pr_k_os.environ,
                            attempt=llm_call_attempt,
                            turn_id=turn_id,
                            exception=exc,
                        )
                    except Exception:  # noqa: BLE001 - trace never breaks a turn.
                        pass
                    attempt_error = exc
                finally:
                    await self._aclose_iter(adk_iter)
                    # Fold this attempt's usage into the turn total (SUM across
                    # re-invocations). Runs on every exit — exhaustion, error, and
                    # cancel — so partial usage survives on aborted turns too.
                    _fold_usage(usage, attempt_usage)

                # PR-K: stamp the normal-completion path. Fires when the
                # adapter dispatch exited without raising AND the turn was
                # not cancelled mid-flight. Paired with the matching
                # ``llm_call_start`` so a turn that started a dispatch but
                # neither completed nor exceptioned is visible as a
                # zero-completion gap in the operator's log.
                if attempt_error is None and not cancelled:
                    try:
                        import os as _pr_k_os  # noqa: PLC0415

                        from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
                            _maybe_log_trace_engine_llm_call_completed,
                        )

                        _maybe_log_trace_engine_llm_call_completed(
                            _pr_k_os.environ,
                            attempt=llm_call_attempt,
                            turn_id=turn_id,
                        )
                    except Exception:  # noqa: BLE001 - trace never breaks a turn.
                        pass

                if cancelled:
                    break
                if attempt_error is None:
                    # Output-continuation: if the model stopped because it hit
                    # its per-response output-token cap (truncated mid-answer),
                    # resume by re-invoking with a "continue where you left off"
                    # message and appending — the only way past the single-
                    # response ceiling. Reuses the goal-nudge re-invocation
                    # machinery (post-output re-invoke is already safe here).
                    if should_continue(
                        output_continuation,
                        truncated=attempt_truncated,
                        output_seen=attempt_yielded > 0,
                        continuations_used=continuations_used,
                    ):
                        continuations_used += 1
                        runner_input = runner_turn_input_cls(
                            userId=self._user_id,
                            sessionId=session_id,
                            turnId=turn_id,
                            invocationId=turn_id,
                            newMessage=types.Content(  # type: ignore[attr-defined]
                                role="user",
                                parts=[
                                    types.Part(text=build_continuation_message())  # type: ignore[attr-defined]
                                ],
                            ),
                            harnessState=effective_harness_state,
                        )
                        yield RuntimeEvent(
                            type="status",
                            payload={
                                "type": "output_continuation",
                                "continuation": continuations_used,
                                "max": output_continuation.max_continuations,  # type: ignore[union-attr]
                            },
                            turn_id=turn_id,
                        )
                        continue  # re-invoke run_async to resume truncated output
                    # R2 empty-response recovery (hermes mechanism 3). Grace
                    # first: budget exhaustion means the attempt was cut off
                    # mid-task, so "produce your final answer now" outranks the
                    # narrower tools-ran-but-silent recovery. Both run BEFORE
                    # goal-nudge deliberately — an empty stop must get its
                    # specific corrective message, not the generic nudge (which
                    # would otherwise consume the stop). Config None/disabled →
                    # both helpers return False → byte-identical control flow.
                    if should_grace(
                        empty_response_recovery,
                        budget_exhausted=budget_exhausted,
                        text_seen=attempt_text_seen,
                        graces_used=graces_used,
                    ):
                        graces_used += 1
                        grace_event_extra = (
                            empty_response_recovery.grace_event_allowance  # type: ignore[union-attr]
                        )
                        runner_input = runner_turn_input_cls(
                            userId=self._user_id,
                            sessionId=session_id,
                            turnId=turn_id,
                            invocationId=turn_id,
                            newMessage=types.Content(  # type: ignore[attr-defined]
                                role="user",
                                parts=[
                                    types.Part(text=build_grace_message())  # type: ignore[attr-defined]
                                ],
                            ),
                            harnessState=effective_harness_state,
                        )
                        yield RuntimeEvent(
                            type="status",
                            payload={
                                "type": "empty_response_grace",
                                "grace": graces_used,
                                "max": 1,
                            },
                            turn_id=turn_id,
                        )
                        continue  # re-invoke run_async (genuine new model call)
                    # Recovery targets the model returning empty after a CLEAN
                    # stop. A budget-exhausted attempt was cut by US — only the
                    # single grace above may answer it; re-invoking against an
                    # already-exceeded cap would just re-break immediately.
                    if not budget_exhausted and should_recover_empty(
                        empty_response_recovery,
                        tool_ran=attempt_tool_ran,
                        text_seen=attempt_text_seen,
                        recoveries_used=recoveries_used,
                    ):
                        recoveries_used += 1
                        # PR5b: the equality below is POST-increment.
                        # ``recoveries_used`` was just bumped, so it equals
                        # ``max_recoveries`` exactly on the FINAL allowed
                        # recovery. Reading it pre-increment would fire the
                        # blocked-or-final message one attempt early/late.
                        # ``select_recovery_message`` returns the plain
                        # corrective message whenever escalate is False, so the
                        # escalation-OFF path is byte-identical.
                        is_final_recovery = (
                            recoveries_used
                            == empty_response_recovery.max_recoveries  # type: ignore[union-attr]
                        )
                        recovery_message = select_recovery_message(
                            escalate=empty_response_recovery.escalate,  # type: ignore[union-attr]
                            is_final=is_final_recovery,
                        )
                        runner_input = runner_turn_input_cls(
                            userId=self._user_id,
                            sessionId=session_id,
                            turnId=turn_id,
                            invocationId=turn_id,
                            newMessage=types.Content(  # type: ignore[attr-defined]
                                role="user",
                                parts=[
                                    types.Part(  # type: ignore[attr-defined]
                                        text=recovery_message
                                    )
                                ],
                            ),
                            harnessState=effective_harness_state,
                        )
                        yield RuntimeEvent(
                            type="status",
                            payload={
                                "type": "empty_response_recovery",
                                "recovery": recoveries_used,
                                "max": empty_response_recovery.max_recoveries,  # type: ignore[union-attr]
                            },
                            turn_id=turn_id,
                        )
                        continue  # re-invoke run_async (genuine new model call)
                    # PR4 goal-nudge: at the clean-break path, check whether a
                    # nudge re-invocation is warranted before breaking.
                    if goal_nudge is not None and nudges_used < goal_nudge.max_nudges:
                        # Collect ONCE so the (WS6 PR6c) enrichment below reads
                        # exactly the evidence records the gate decision read.
                        nudge_evidence_records = self._collect_evidence(turn_id)
                        if not _goal_is_met(
                            goal_nudge,
                            evidence_records=nudge_evidence_records,
                        ):
                            if goal_nudge.mode == "goal" and goal_check_pending:
                                # Latch has already fired once since the last
                                # tool event — break without another nudge.
                                break
                            # Arm the latch (goal mode) or keep it reset (grind).
                            goal_check_pending = True
                            nudges_used += 1
                            # Build a fresh runner_input with the nudge as the
                            # new message, reusing the SAME re-invocation
                            # machinery the recovery path uses (build + continue).
                            nudge_text = _build_nudge_message(goal_nudge)
                            runner_input = runner_turn_input_cls(
                                userId=self._user_id,
                                sessionId=session_id,
                                turnId=turn_id,
                                invocationId=turn_id,
                                newMessage=types.Content(  # type: ignore[attr-defined]
                                    role="user",
                                    parts=[types.Part(text=nudge_text)],  # type: ignore[attr-defined]
                                ),
                                harnessState=effective_harness_state,
                            )
                            # WS6 PR6c: terminal-free enrichment of the EXISTING
                            # goal_nudge status payload with evidence-reason
                            # fields (default-OFF; inert until WS3). The
                            # ``continue``-to-re-invoke control flow below is
                            # preserved EXACTLY (no terminal, no text_delta)
                            # suffix is added on this path.
                            nudge_status_payload: dict[str, object] = {
                                "type": "goal_nudge",
                                "mode": goal_nudge.mode,
                                "nudge": nudges_used,
                                "max": goal_nudge.max_nudges,
                            }
                            self._enrich_goal_nudge_status(
                                nudge_status_payload,
                                goal_nudge,
                                evidence_records=nudge_evidence_records,
                            )
                            yield RuntimeEvent(
                                type="status",
                                payload=nudge_status_payload,
                                turn_id=turn_id,
                            )
                            continue  # re-invoke run_async (genuine new model call)
                    # U4 unified clean-break ladder (design 5.2). Fires AFTER the
                    # legacy goal_nudge branch (above) and BEFORE the final break.
                    # Reads the per-turn policy ContextVar (PR-B). When a policy is
                    # present (mission intensity today) the branch runs ONE ladder:
                    # the evidence-first pre-judge resolves done/pause/continue/
                    # defer_to_judge; a "continue" outcome takes the shared
                    # DETERMINISTIC executor (no judge call, GAP-2), while the
                    # bounded LLM judge is confined to "defer_to_judge" (the
                    # no-ledger / ambiguous case) and is itself progress-braked
                    # (design 5.4). Absent policy falls through to SEAM 2 (ambient
                    # ledger authority) and then the bare break. The OFF path (no
                    # policy) is byte-identical to pre-PR-C.
                    from magi_agent.runtime.per_turn_goal_loop_context import (  # noqa: PLC0415
                        current_per_turn_goal_loop_policy,
                    )

                    goal_loop_policy = current_per_turn_goal_loop_policy()
                    # U5 ambient synthesis (design 5.1 / 5.2, KD-1). A ContextVar-
                    # published policy (explicit mission) ALWAYS wins; synthesis
                    # only fills the ``None`` case. When no policy was published,
                    # auto-continue is enabled (which already encodes
                    # is_goal_loop_enabled() AND auto_continue_allowed, so children
                    # / depth>0 / safe profiles never reach here), a factory is
                    # wired, and a real objective was captured, the driver
                    # synthesizes an AMBIENT GoalLoopPolicy so finish-the-job is the
                    # baseline. Mission intensity (the explicit toggle, or this
                    # driver's mission default) BYPASSES the substance gate (OD-6);
                    # otherwise the S1/S2/S3 substance gate (design 4.2) filters out
                    # conversational turns so a bare "hi" costs zero extra calls.
                    if (
                        goal_loop_policy is None
                        and self._auto_continue_enabled
                        and self._ambient_goal_policy_factory is not None
                        and objective_text
                    ):
                        from magi_agent.runtime.per_turn_goal_intensity import (  # noqa: PLC0415
                            current_per_turn_goal_mission,
                        )

                        ambient_mission = (
                            current_per_turn_goal_mission()
                            or self._auto_continue_mission
                        )
                        if ambient_mission:
                            ambient_substantive = True
                        else:
                            ambient_open_todos = (
                                self._open_todo_count(
                                    tuple(plan_ledger_reader(session_id))
                                )
                                if plan_ledger_reader is not None
                                else None
                            )
                            ambient_substantive = _turn_is_substantive(
                                tool_ends_total=turn_tool_ends_total,
                                open_todos=ambient_open_todos,
                                new_evidence_records=len(
                                    self._collect_evidence(turn_id)
                                ),
                            )
                        if ambient_substantive:
                            goal_loop_policy = self._ambient_goal_policy_factory(
                                objective_text
                            )
                    if goal_loop_policy is not None:
                        # SEAM 1 (WS3 PR3b, lab loop ON): evidence-first pre-judge
                        # short-circuit. Runs BEFORE the LLM judge; ``done`` /
                        # ``pause`` terminate deterministically with no model call.
                        # ``continue`` / ``defer_to_judge`` fall through to the
                        # UNCHANGED judge below, which enforces the max_turns /
                        # parse-budget bounds and drives the continuation, so the
                        # ledger short-circuit never introduces an unbounded loop.
                        # Guarded by ``evidence_first`` so the OFF path is the
                        # current code byte-for-byte.
                        if evidence_first and plan_ledger_reader is not None:
                            from magi_agent.runtime.goal_loop_evidence import (  # noqa: PLC0415
                                resolve_pre_judge_outcome,
                            )

                            pre_judge_snapshot = tuple(plan_ledger_reader(session_id))
                            pre_judge_outcome = resolve_pre_judge_outcome(
                                required_evidence=required_evidence,
                                evidence_records=self._collect_evidence(turn_id),
                                ledger_snapshot=pre_judge_snapshot,
                            )
                            if pre_judge_outcome == "done":
                                yield RuntimeEvent(
                                    type="status",
                                    payload={
                                        "type": "goal_loop_complete",
                                        "reason": "ledger_all_complete",
                                        "continuations": goal_loop_continuations,
                                    },
                                    turn_id=turn_id,
                                )
                                break
                            if pre_judge_outcome == "pause":
                                yield self._goal_paused_event(
                                    turn_id=turn_id,
                                    reason="evidence_unverifiable",
                                    objective=goal_loop_policy.objective,
                                    open_todos=self._open_todo_count(
                                        pre_judge_snapshot
                                    ),
                                )
                                break
                            # U4 unified ladder (design 5.2): a pre-judge
                            # "continue" outcome (open ledger) is DETERMINISTIC
                            # authority. Route it to the shared executor with NO
                            # judge call so the mission path stops burning one
                            # judge call per ledger step (GAP-2), matching how the
                            # ambient / SEAM 2 path already continues. The bounded
                            # judge below is reserved for "defer_to_judge" (no
                            # ledger signal, no evidence contract). Gated on
                            # auto_continue_enabled so contained turns (SpawnAgent
                            # children / depth>0) and the historic no-auto-continue
                            # configuration fall through to the judge exactly as
                            # before (byte-identical).
                            if (
                                pre_judge_outcome == "continue"
                                and self._auto_continue_enabled
                            ):
                                (
                                    ac_events,
                                    ac_result,
                                ) = self._run_deterministic_auto_continue(
                                    turn_id=turn_id,
                                    session_id=session_id,
                                    seam2_snapshot=pre_judge_snapshot,
                                    ok_tool_ends=auto_continue_ok_tool_ends,
                                    blocked_tool_ends=(
                                        auto_continue_blocked_tool_ends
                                    ),
                                    prev_ledger=auto_continue_prev_ledger,
                                    prev_evidence_count=(
                                        auto_continue_prev_evidence_count
                                    ),
                                    continuations_used=auto_continue_used,
                                    no_progress_streak=(
                                        auto_continue_no_progress_streak
                                    ),
                                    wrap_up_spent=auto_continue_wrap_up_spent,
                                    turn_start=auto_continue_turn_start,
                                    effective_harness_state=(
                                        effective_harness_state
                                    ),
                                    runner_turn_input_cls=runner_turn_input_cls,
                                    types_mod=types,
                                )
                                for _ac_event in ac_events:
                                    yield _ac_event
                                auto_continue_used = ac_result.used
                                auto_continue_no_progress_streak = (
                                    ac_result.no_progress_streak
                                )
                                auto_continue_wrap_up_spent = (
                                    ac_result.wrap_up_spent
                                )
                                if ac_result.action == "continue":
                                    runner_input = ac_result.runner_input
                                    # The response_clear boundary is emitted by the
                                    # auto-continue helper (in ac_events, yielded
                                    # just above); blank emitted_text here so the
                                    # continuation's answer does not concatenate
                                    # onto the prior attempt (the duplicated glue).
                                    emitted_text = ""
                                    continue  # re-invoke (genuine model call)
                                break  # terminal deterministic outcome
                            # "continue" (auto-continue disabled) /
                            # "defer_to_judge" -> fall through to the bounded judge
                            # below (unchanged).
                        from magi_agent.runtime.goal_loop_judge import (  # noqa: PLC0415
                            evaluate_goal_completion,
                        )

                        if goal_loop_continuations >= goal_loop_policy.max_turns:
                            yield RuntimeEvent(
                                type="status",
                                payload={
                                    "type": "goal_loop_exhausted",
                                    "continuations": goal_loop_continuations,
                                    "max": goal_loop_policy.max_turns,
                                },
                                turn_id=turn_id,
                            )
                            # SEAM 3 (WS3 PR3b, lab loop ON): additively emit the
                            # honest pause alongside the existing exhaustion event
                            # so the bare termination never masquerades as success.
                            if evidence_first:
                                yield self._goal_paused_event(
                                    turn_id=turn_id,
                                    reason="max_turns_exhausted",
                                    objective=goal_loop_policy.objective,
                                    open_todos=(
                                        self._open_todo_count(
                                            tuple(plan_ledger_reader(session_id))
                                        )
                                        if plan_ledger_reader is not None
                                        else None
                                    ),
                                )
                            break
                        if (
                            goal_loop_judge_parse_failures
                            >= goal_loop_policy.judge_parse_failures_budget
                        ):
                            yield RuntimeEvent(
                                type="status",
                                payload={
                                    "type": "goal_loop_judge_unavailable",
                                    "reason": "parse_failure_budget_exhausted",
                                    "parseFailures": goal_loop_judge_parse_failures,
                                    "budget": goal_loop_policy.judge_parse_failures_budget,
                                },
                                turn_id=turn_id,
                            )
                            # SEAM 3 (WS3 PR3b): additive honest pause.
                            if evidence_first:
                                yield self._goal_paused_event(
                                    turn_id=turn_id,
                                    reason="parse_failure_budget",
                                    objective=goal_loop_policy.objective,
                                    open_todos=(
                                        self._open_todo_count(
                                            tuple(plan_ledger_reader(session_id))
                                        )
                                        if plan_ledger_reader is not None
                                        else None
                                    ),
                                )
                            break
                        if goal_loop_judge_caller is None:
                            if goal_loop_judge_factory is None:
                                yield RuntimeEvent(
                                    type="status",
                                    payload={
                                        "type": "goal_loop_judge_unavailable",
                                        "reason": "no_judge_factory",
                                    },
                                    turn_id=turn_id,
                                )
                                break
                            try:
                                goal_loop_judge_caller = goal_loop_judge_factory(
                                    goal_loop_policy
                                )
                            except Exception:  # noqa: BLE001 — fail-soft: never crash the turn.
                                goal_loop_judge_caller = None
                            if goal_loop_judge_caller is None:
                                yield RuntimeEvent(
                                    type="status",
                                    payload={
                                        "type": "goal_loop_judge_unavailable",
                                        "reason": "judge_factory_returned_none",
                                    },
                                    turn_id=turn_id,
                                )
                                break
                        verdict = await evaluate_goal_completion(
                            policy=goal_loop_policy,
                            final_text=emitted_text,
                            judge_caller=goal_loop_judge_caller,  # type: ignore[arg-type]
                        )
                        if not verdict.parse_succeeded:
                            goal_loop_judge_parse_failures += 1
                        if verdict.complete:
                            yield RuntimeEvent(
                                type="status",
                                payload={
                                    "type": "goal_loop_complete",
                                    "reason": verdict.reason,
                                    "continuations": goal_loop_continuations,
                                },
                                turn_id=turn_id,
                            )
                            break
                        if (
                            goal_loop_judge_parse_failures
                            >= goal_loop_policy.judge_parse_failures_budget
                        ):
                            yield RuntimeEvent(
                                type="status",
                                payload={
                                    "type": "goal_loop_judge_unavailable",
                                    "reason": "parse_failure_budget_exhausted",
                                    "parseFailures": goal_loop_judge_parse_failures,
                                    "budget": goal_loop_policy.judge_parse_failures_budget,
                                },
                                turn_id=turn_id,
                            )
                            # SEAM 3 (WS3 PR3b): additive honest pause.
                            if evidence_first:
                                yield self._goal_paused_event(
                                    turn_id=turn_id,
                                    reason="parse_failure_budget",
                                    objective=goal_loop_policy.objective,
                                    open_todos=(
                                        self._open_todo_count(
                                            tuple(plan_ledger_reader(session_id))
                                        )
                                        if plan_ledger_reader is not None
                                        else None
                                    ),
                                )
                            break
                        # U4 progress brake on judge continuations (design 5.4):
                        # a judge "not complete" verdict that drives a
                        # continuation is gated through the SAME measurable-
                        # progress logic as the deterministic path, so a stalling
                        # mission spends its single wrap-up and pauses honestly
                        # instead of running to max_turns with zero progress. The
                        # judge keeps authority over COMPLETION; the brake removes
                        # its authority over UNBOUNDED SPEND. The no_progress
                        # streak / wrap-up / wall-clock state is SHARED with the
                        # deterministic path (one brake). Gated on
                        # auto_continue_enabled so the historic no-auto-continue
                        # configuration is byte-identical (raw continuation).
                        if self._auto_continue_enabled:
                            from magi_agent.runtime.goal_loop_auto_continue import (  # noqa: PLC0415
                                AttemptProgress,
                                budgets_for_intensity,
                                decide_auto_continue,
                                ledger_changed,
                            )
                            from magi_agent.runtime.per_turn_goal_intensity import (  # noqa: PLC0415
                                current_per_turn_goal_mission,
                            )
                            import time as _judge_brake_time  # noqa: PLC0415

                            judge_mission = (
                                current_per_turn_goal_mission()
                                or self._auto_continue_mission
                            )
                            judge_ledger_now = (
                                tuple(plan_ledger_reader(session_id))
                                if plan_ledger_reader is not None
                                else ()
                            )
                            judge_evidence_now = self._collect_evidence(turn_id)
                            judge_progress = AttemptProgress(
                                ok_tool_ends=auto_continue_ok_tool_ends,
                                blocked_tool_ends=auto_continue_blocked_tool_ends,
                                ledger_changed=ledger_changed(
                                    auto_continue_prev_ledger, judge_ledger_now
                                ),
                                new_evidence_records=max(
                                    0,
                                    len(judge_evidence_now)
                                    - auto_continue_prev_evidence_count,
                                ),
                            )
                            judge_brake = decide_auto_continue(
                                ledger_wants_continue=True,
                                progress=judge_progress,
                                continuations_used=goal_loop_continuations,
                                prior_no_progress_streak=(
                                    auto_continue_no_progress_streak
                                ),
                                elapsed_seconds=(
                                    _judge_brake_time.monotonic()
                                    - auto_continue_turn_start
                                ),
                                budgets=budgets_for_intensity(
                                    mission=judge_mission
                                ),
                                wrap_up_already_spent=(
                                    auto_continue_wrap_up_spent
                                ),
                            )
                            auto_continue_no_progress_streak = (
                                judge_brake.no_progress_streak
                            )
                            judge_open_todos = self._open_todo_count(
                                judge_ledger_now
                            )
                            if judge_brake.outcome == "stop_budget":
                                yield RuntimeEvent(
                                    type="status",
                                    payload={
                                        "type": "goal_loop_exhausted",
                                        "reason": judge_brake.reason,
                                        "continuations": goal_loop_continuations,
                                    },
                                    turn_id=turn_id,
                                )
                                yield self._goal_paused_event(
                                    turn_id=turn_id,
                                    reason="budget_exhausted",
                                    objective=goal_loop_policy.objective,
                                    open_todos=judge_open_todos,
                                )
                                break
                            if (
                                judge_brake.outcome
                                == "paused_waiting_on_approvals"
                            ):
                                yield self._goal_paused_event(
                                    turn_id=turn_id,
                                    reason="waiting_on_approvals",
                                    objective=goal_loop_policy.objective,
                                    open_todos=judge_open_todos,
                                )
                                break
                            if judge_brake.outcome == "paused_no_progress":
                                yield self._goal_paused_event(
                                    turn_id=turn_id,
                                    reason="no_progress",
                                    objective=goal_loop_policy.objective,
                                    open_todos=judge_open_todos,
                                )
                                break
                            # "continue" / "wrap_up" -> drive the judge
                            # continuation with the policy template, or spend the
                            # single honest wrap-up.
                            judge_is_wrap_up = (
                                judge_brake.outcome == "wrap_up"
                            )
                            if judge_is_wrap_up:
                                auto_continue_wrap_up_spent = True
                            goal_loop_continuations += 1
                            yield RuntimeEvent(
                                type="status",
                                payload={
                                    "type": (
                                        "goal_loop_wrap_up"
                                        if judge_is_wrap_up
                                        else "goal_loop_continuation"
                                    ),
                                    "continuation": goal_loop_continuations,
                                    "max": goal_loop_policy.max_turns,
                                    "judgeReason": verdict.reason,
                                    "reason": judge_brake.reason,
                                    "openTodos": judge_open_todos,
                                    "source": "judge",
                                },
                                turn_id=turn_id,
                            )
                            runner_input = runner_turn_input_cls(
                                userId=self._user_id,
                                sessionId=session_id,
                                turnId=turn_id,
                                invocationId=turn_id,
                                newMessage=types.Content(  # type: ignore[attr-defined]
                                    role="user",
                                    parts=[
                                        types.Part(  # type: ignore[attr-defined]
                                            text=(
                                                _AUTO_CONTINUE_WRAP_UP_PROMPT
                                                if judge_is_wrap_up
                                                else goal_loop_policy.continuation_template
                                            )
                                        )
                                    ],
                                ),
                                harnessState=effective_harness_state,
                            )
                            # Same re-answer boundary as the auto-continue helper:
                            # blank the prior attempt so the judge continuation
                            # does not concatenate onto it, and reset
                            # _turn_text_emitted (via response_clear at
                            # event_adapter :373) so pre-answer thinking is not
                            # over-suppressed on the continuation.
                            yield RuntimeEvent(
                                type=_map_event_kind("response_clear"),
                                payload={
                                    "type": "response_clear",
                                    "turnId": turn_id,
                                    "reason": "goal_loop_continuation",
                                },
                                turn_id=turn_id,
                            )
                            emitted_text = ""
                            continue  # re-invoke run_async (genuine model call)
                        # Historic (auto-continue disabled) raw continuation:
                        # byte-identical to pre-U4. This legacy path (auto-continue
                        # off) is knowingly left without the response_clear boundary
                        # to preserve the pre-U4 invariant; it is not exercised by
                        # local ``magi serve`` (which runs auto-continue enabled via
                        # the SEAM 2 helper above).
                        goal_loop_continuations += 1
                        yield RuntimeEvent(
                            type="status",
                            payload={
                                "type": "goal_loop_continuation",
                                "continuation": goal_loop_continuations,
                                "max": goal_loop_policy.max_turns,
                                "judgeReason": verdict.reason,
                            },
                            turn_id=turn_id,
                        )
                        runner_input = runner_turn_input_cls(
                            userId=self._user_id,
                            sessionId=session_id,
                            turnId=turn_id,
                            invocationId=turn_id,
                            newMessage=types.Content(  # type: ignore[attr-defined]
                                role="user",
                                parts=[
                                    types.Part(  # type: ignore[attr-defined]
                                        text=goal_loop_policy.continuation_template
                                    )
                                ],
                            ),
                            harnessState=effective_harness_state,
                        )
                        continue  # re-invoke run_async (genuine new model call)
                    # SEAM 2 (WS3 PR3b, the "full" profile deliverable): HOISTED
                    # OUTSIDE the ``if goal_loop_policy is not None:`` guard above,
                    # immediately before the bare ``break`` loop-OFF terminus.
                    # ``goal_loop_policy is None`` for "full" users (the lab loop
                    # flag is not seeded there), so this is the ONLY seam that
                    # delivers the ledger-all-complete short-circuit and the
                    # honest pre-judge pause to them. There is no judge in this
                    # branch: ``continue`` / ``defer_to_judge`` degrade to the bare
                    # break. The OFF branch (flag unset OR reader None OR a policy
                    # IS present) is byte-identical to the bare ``break`` below.
                    if (
                        evidence_first
                        and goal_loop_policy is None
                        and plan_ledger_reader is not None
                    ):
                        from magi_agent.runtime.goal_loop_evidence import (  # noqa: PLC0415
                            resolve_pre_judge_outcome,
                        )

                        seam2_snapshot = tuple(plan_ledger_reader(session_id))
                        seam2_outcome = resolve_pre_judge_outcome(
                            required_evidence=required_evidence,
                            evidence_records=self._collect_evidence(turn_id),
                            ledger_snapshot=seam2_snapshot,
                        )
                        if seam2_outcome == "done":
                            yield RuntimeEvent(
                                type="status",
                                payload={
                                    "type": "goal_loop_complete",
                                    "reason": "ledger_all_complete",
                                    "continuations": goal_loop_continuations,
                                },
                                turn_id=turn_id,
                            )
                            break
                        if seam2_outcome == "pause":
                            yield self._goal_paused_event(
                                turn_id=turn_id,
                                reason="evidence_unverifiable",
                                objective=None,
                                open_todos=self._open_todo_count(seam2_snapshot),
                            )
                            break
                        # "continue" -> give the ledger-first verdict re-invocation
                        # authority (the highest-leverage fix). Historically this
                        # degraded to a bare break because continuation authority
                        # lived only in the goal-loop judge (double-gated OFF); the
                        # agent stopped mid-multi-step-task and said "I'll
                        # continue...". Now, when auto-continue is enabled, a
                        # measurable-progress gate decides whether the computed
                        # "continue" actually re-invokes. "defer_to_judge" (no
                        # ledger signal, no evidence requirement) keeps the bare
                        # break so the no-ledger case is unchanged.
                        if (
                            seam2_outcome == "continue"
                            and self._auto_continue_enabled
                        ):
                            # Shared deterministic executor (design 5.3): the
                            # AttemptProgress assembly, the decide_auto_continue
                            # gate, the four outcome arms and the re-invoke
                            # construction live in one private helper so the U4
                            # policy-present ``continue`` arm and this legacy SEAM
                            # 2 arm run literally the same code. The helper is
                            # pure w.r.t. the loop (no ``continue`` / ``break``);
                            # it returns the events to yield plus the loop-control
                            # action and the updated continuation counters.
                            (
                                ac_events,
                                ac_result,
                            ) = self._run_deterministic_auto_continue(
                                turn_id=turn_id,
                                session_id=session_id,
                                seam2_snapshot=seam2_snapshot,
                                ok_tool_ends=auto_continue_ok_tool_ends,
                                blocked_tool_ends=(
                                    auto_continue_blocked_tool_ends
                                ),
                                prev_ledger=auto_continue_prev_ledger,
                                prev_evidence_count=(
                                    auto_continue_prev_evidence_count
                                ),
                                continuations_used=auto_continue_used,
                                no_progress_streak=(
                                    auto_continue_no_progress_streak
                                ),
                                wrap_up_spent=auto_continue_wrap_up_spent,
                                turn_start=auto_continue_turn_start,
                                effective_harness_state=effective_harness_state,
                                runner_turn_input_cls=runner_turn_input_cls,
                                types_mod=types,
                            )
                            for _ac_event in ac_events:
                                yield _ac_event
                            auto_continue_used = ac_result.used
                            auto_continue_no_progress_streak = (
                                ac_result.no_progress_streak
                            )
                            auto_continue_wrap_up_spent = (
                                ac_result.wrap_up_spent
                            )
                            if ac_result.action == "continue":
                                runner_input = ac_result.runner_input
                                # The response_clear boundary is emitted by the
                                # auto-continue helper (in ac_events, yielded just
                                # above); blank emitted_text so the continuation
                                # does not concatenate onto the prior attempt.
                                emitted_text = ""
                                continue  # re-invoke run_async (genuine model call)
                            # action == "break" (terminal outcome or the
                            # unreachable ledger "stop") -> fall through to the
                            # bare break below.
                        # "defer_to_judge" / auto-continue disabled -> bare break.
                    break

                # The run invocation raised. Decide whether to GENUINELY retry.
                # Only safe before any output was streamed (this turn AND this
                # attempt) so we never double-emit / duplicate tool effects.
                should_retry = (
                    self._recovery is not None
                    and yielded_events == 0
                    and attempt_yielded == 0
                    and recovery_attempts < self._recovery.max_attempts
                )
                if should_retry:
                    recovery_state, recovered = await self._attempt_run_recovery(
                        error=attempt_error,
                        session_id=session_id,
                        turn_id=turn_id,
                        state=recovery_state,
                    )
                    if recovered:
                        recovery_attempts += 1
                        continue  # re-invoke run_async (genuine 2nd model call)
                # Terminal / non-retryable / budget exhausted -> surface.
                engine_error = str(attempt_error) or attempt_error.__class__.__name__
                # Capture the structured detail so the orphan sweep + the new
                # engine_error_detail status event can name the real trigger
                # (errorClass + sanitized traceback). Lost-the-trace was the
                # exact gap that left "tool interrupted by user cancellation"
                # as the only signal on Kevin's 0.1.66 SOTA-spawn repro.
                engine_error_detail = self._capture_error_detail(attempt_error)
                break
        finally:
            self._restore_runner_policy_route(route_attach)
            self._restore_customize_rules(customize_attach)
            self._restore_user_hook_bus(hook_attach)
            self._restore_gate_callback(gate_attach)

        if cancelled:
            for safe in self._synthesize_orphan_tool_results(
                pending_tool_ids, turn_id=turn_id, reason="user_interrupt"
            ):
                yield RuntimeEvent(type="tool", payload=safe, turn_id=turn_id)
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "turn_end",
                    "turnId": turn_id,
                    "status": "aborted",
                    "reason": "user_interrupt",
                },
                turn_id=turn_id,
            )
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.aborted,
                usage=usage,
                cost_usd=0.0,
                error="cancelled",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        if engine_error is not None:
            # PR-3: surface the structured upstream-exception detail (errorClass
            # + sanitized message + traceback preview) BEFORE the orphan sweep
            # so the dashboard can render a single banner naming the real
            # trigger. The orphan tool_end events also carry per-tool detail
            # (same payload) so the Work pane can show why each specific tool
            # was swept.
            if engine_error_detail is not None:
                yield RuntimeEvent(
                    type="status",
                    payload={
                        "type": "engine_error_detail",
                        "turnId": turn_id,
                        **engine_error_detail,
                    },
                    turn_id=turn_id,
                )
            # Balance the transcript on a mid-tool failure too: a runner error
            # while a tool_use is pending would otherwise leave a dangling
            # tool_use that a resuming session cannot reconcile (same hazard the
            # cancel path guards against).
            for safe in self._synthesize_orphan_tool_results(
                pending_tool_ids,
                turn_id=turn_id,
                reason="engine_error",
                error_detail=engine_error_detail,
            ):
                yield RuntimeEvent(type="tool", payload=safe, turn_id=turn_id)
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.error,
                usage=usage,
                cost_usd=0.0,
                error=engine_error,
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        # P3 zero-edit guard: if the coding turn ended without any file-mutating
        # tool calls (agent described the fix but didn't apply it), re-invoke
        # ONCE with an explicit "apply it" message reusing the same re-invocation
        # seam as goal-nudge / output-continuation.  Gated by the eval flag so
        # non-eval sessions are byte-identical to pre-P3.
        import os as _os  # noqa: PLC0415
        from magi_agent.config.env import parse_eval_zero_edit_guard_enabled  # noqa: PLC0415

        if should_reprompt_for_zero_edits(
            file_edits=file_edit_calls,
            already_reprompted=zero_edit_retry_done,
            enabled=parse_eval_zero_edit_guard_enabled(_os.environ),
        ):
            zero_edit_retry_done = True
            _zero_edit_msg = "Apply the code change you described above by editing the file(s) now."
            zero_edit_runner_input = runner_turn_input_cls(
                userId=self._user_id,
                sessionId=session_id,
                turnId=turn_id,
                invocationId=turn_id,
                newMessage=types.Content(  # type: ignore[attr-defined]
                    role="user",
                    parts=[types.Part(text=_zero_edit_msg)],  # type: ignore[attr-defined]
                ),
                harnessState=effective_harness_state,
            )
            yield RuntimeEvent(
                type="status",
                payload={"type": "zero_edit_guard_retry", "turnId": turn_id},
                turn_id=turn_id,
            )
            zero_edit_gate_attach = self._attach_gate_callback(
                runner=runner, gate=gate, turn_id=turn_id, cancel=cancel
            )
            zero_edit_route_attach = self._attach_runner_policy_route(
                runner=runner,
                route_selection=route_selection,
            )
            zero_edit_iter: AsyncIterator[object] = adapter.run_turn(zero_edit_runner_input).__aiter__()  # type: ignore[union-attr]
            _ze_usage: dict[str, int] = {}
            try:
                while True:
                    if cancel.is_set():
                        cancelled = True
                        break
                    _zstep = await self._next_adk_event(zero_edit_iter, cancel)
                    if _zstep is _CANCELLED:
                        cancelled = True
                        break
                    if _zstep is _EXHAUSTED:
                        break
                    _zadk_event = _zstep
                    self._note_observed_invocation_id(
                        _adk_invocation_id(_zadk_event)
                    )
                    _ze_reading = _adk_usage_metadata(_zadk_event)
                    if _ze_reading:
                        _ze_usage.update(_ze_reading)
                    _zprojection = bridge.project_adk_event(_zadk_event, turn_id=turn_id)  # type: ignore[union-attr]
                    for _zraw in _zprojection.agent_events:  # type: ignore[union-attr]
                        _zsafe = sanitize(dict(_zraw))  # type: ignore[operator]
                        if _zsafe is None:
                            continue
                        self._collect_public_refs(_zsafe, observed_public_refs)
                        self._track_pending_tool(_zsafe, pending_tool_ids)
                        yielded_events += 1
                        self._observe_event(_zsafe, session_id, turn_id)
                        yield RuntimeEvent(
                            type=_map_event_kind(_zsafe.get("type")),
                            payload=_zsafe,
                            turn_id=turn_id,
                        )
            except Exception:  # noqa: BLE001 - fail-open: guard errors don't block the turn
                pass
            finally:
                await self._aclose_iter(zero_edit_iter)
                self._restore_runner_policy_route(zero_edit_route_attach)
                self._restore_gate_callback(zero_edit_gate_attach)
                _fold_usage(usage, _ze_usage)

            # If cancelled during the guard retry, fall through to the cancel
            # block below (cancelled flag is already set).

        # B9 no-tool finalizer: run-until-done backstop, ported from the legacy
        # gate5b boundary. ONE bounded tool-less pass when the turn is about to
        # commit blank (no visible answer text this turn). Runs AFTER every
        # ladder re-invocation (continuation / grace / recovery / nudge / goal
        # loop / auto-continue) and the zero-edit guard, and BEFORE the citation
        # audit + LLM criterion gates + pre-final gate, so those evaluate the
        # finalizer's answer and the citation / verify verdict records stay
        # truthful. Only clean-break Terminal.completed turns reach here (the
        # cancelled and engine_error paths returned above). config=None or a
        # non-blank turn is a byte-identical no-op.
        if not cancelled and should_run_no_tool_finalizer(
            no_tool_finalizer,
            emitted_text=emitted_text,
            recoveries_used=recoveries_used,
        ):
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "no_tool_finalizer",
                    "phase": "start",
                    "turnId": turn_id,
                    "toolEnds": turn_tool_ends_total,
                },
                turn_id=turn_id,
            )
            _finalizer_produced = False
            async for _fin_event in self._run_no_tool_finalizer_pass(
                adapter=adapter,
                bridge=bridge,
                sanitize=sanitize,
                runner=runner,
                types=types,
                runner_turn_input_cls=runner_turn_input_cls,
                session_id=session_id,
                turn_id=turn_id,
                effective_harness_state=effective_harness_state,
                event_allowance=no_tool_finalizer.event_allowance,
                usage=usage,
                cancel=cancel,
            ):
                _fin_payload = _fin_event.payload
                if isinstance(_fin_payload, dict) and _fin_payload.get("type") == "text_delta":
                    _fin_delta = _fin_payload.get("delta")
                    if isinstance(_fin_delta, str) and _fin_delta:
                        emitted_text += _fin_delta
                        net_user_text_streamed = True
                        _finalizer_produced = True
                yield _fin_event
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "no_tool_finalizer",
                    "phase": "end",
                    "turnId": turn_id,
                    "producedText": _finalizer_produced,
                },
                turn_id=turn_id,
            )

        # Model text produced DURING a bounded repair attempt is held here and
        # only delivered if that attempt actually un-blocks the gate. A failed
        # attempt's text is internal repair dialogue (often the model refusing
        # the synthetic repair continuation) — leaking it concatenated the whole
        # exchange into the user-visible reply.
        live_selected = await self._read_live_selected_recipe_pack_ids(session_id)

        # Wave 4a: deterministic source-citation gate in AUDIT (observe-only)
        # mode. Runs BEFORE the LLM criterion rules (cheap first). Emits a
        # custom:CitationVerdict record and NEVER alters the turn; flag-OFF or
        # gate-mode off is a byte-identical no-op. Wave 4b adds the repair
        # decision at the repairDecision seam in the pre-final loop below.
        self._maybe_citation_gate_audit(
            session_id=session_id,
            turn_id=turn_id,
            prompt=prompt,
            final_text=emitted_text,
        )

        # P3: custom llm_criterion gate (pre-final). Independent of the
        # deterministic verifier-bus loop below + the coding-repair loop. A clear
        # FAIL verdict from an enabled block rule aborts the turn with a custom
        # error (mirrors the deterministic block-error return). Flag-gated +
        # fail-open → byte-identical when off.
        llm_block_reason = await self._maybe_llm_criterion_block(
            final_text=emitted_text, turn_id=turn_id
        )
        # C1 — built-in answer-quality llm gate (independent of user custom rules).
        # Shares the same abort path; flag/preset + model gated, fail-open → None.
        if llm_block_reason is None:
            llm_block_reason = await self._answer_quality_llm_block(
                prompt=prompt, final_text=emitted_text
            )
        # C2 — built-in premature-refusal llm gate (same shape/gating as C1).
        if llm_block_reason is None:
            llm_block_reason = await self._pre_refusal_llm_block(
                prompt=prompt, final_text=emitted_text
            )
        # C-MERGE-1 — built-in completion/promise-without-action llm gate. Collects
        # the turn's evidence itself (only when its gate is on) for the det
        # pre-gate; fail-open → None.
        if llm_block_reason is None:
            llm_block_reason = await self._completion_evidence_llm_block(
                turn_id=turn_id, final_text=emitted_text
            )
        # C-MERGE-2 — built-in resource/self-claim llm gate. Same shape, but the
        # det pre-gate counts SOURCE/READ evidence (SourceInspection / WebSearch
        # / KnowledgeSearch), so a turn that actually inspected ≥1 source skips
        # the model call.
        if llm_block_reason is None:
            llm_block_reason = await self._resource_claim_llm_block(
                turn_id=turn_id, final_text=emitted_text
            )
        # C4 — built-in claim-citation (free-text claim-coverage) llm gate. Det
        # pre-gate keys off the answer text only (contains [src_N]?), so a turn
        # that already cited sources skips the model call.
        if llm_block_reason is None:
            llm_block_reason = await self._claim_citation_llm_block(
                final_text=emitted_text
            )
        # C3 — built-in output-purity llm gate. Det pre-gate skips the model call
        # unless the answer contains a canonical private/reasoning key in JSON
        # shape, then the criterion judge distinguishes a legitimate JSON answer
        # from a raw internal-envelope leak.
        if llm_block_reason is None:
            llm_block_reason = await self._output_purity_llm_block(
                final_text=emitted_text
            )
        if llm_block_reason is not None:
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "custom_llm_criterion_blocked",
                    "turnId": turn_id,
                    "reason": llm_block_reason,
                },
                turn_id=turn_id,
            )
            yield EngineResult(  # type: ignore[misc]
                terminal=Terminal.error,
                usage=usage,
                cost_usd=0.0,
                error="custom_llm_criterion_blocked",
                session_id=session_id,
                turn_id=turn_id,
            )
            return

        repair_token_buffer: list[RuntimeEvent] = []
        # Wave 4b P0 (fail-open no-blank): the most recent repair attempt's
        # buffered tokens, retained when the loop suppresses them on a re-block.
        # A live response_clear from that round may have blanked the UI, so the
        # citation fail-open branch flushes this to restore a coherent answer
        # (the no-blank invariant) before appending the hedge notice.
        last_repair_buffer: list[RuntimeEvent] = []
        # WS6 PR6a (MINOR-2): the soft notice may only append a suffix when the
        # live answer is on the wire. A repair-suppressed turn discards its
        # buffered answer, so the suffix invariant fails. Track suppression and
        # skip the soft branch when it has happened.
        repair_output_suppressed = False
        # Wave 4b source-citation repair tracking. These stay inert (0/False)
        # unless the shared loop drives a citation repair, so the OFF path and
        # the coding-repair path are byte-identical.
        citation_repair_attempts = 0
        citation_induced_search = False
        citation_record_emitted = False
        # PR-V3 verify-before-replying: per-turn nudge state. Inert (no flag) or
        # finding-free turns leave every branch below byte-identical to before.
        verify_state = _VerifyTurnState()
        while True:
            # PR-V3: SHIP_AS_IS interception at the loop top, BEFORE re-evaluating
            # the gates. A prior pass injected a verify nudge; this pass carries
            # the model's response to it. SHIP_AS_IS means "the original stands":
            # discard the buffered marker round and restore the pre-nudge text.
            # Any other response falls through to re-evaluate the gates and
            # re-audit the (possibly revised) candidate.
            if verify_state.nudge_pending:
                verify_state.nudge_pending = False
                verify_state.nudge_round_active = False
                if emitted_text.strip() == "SHIP_AS_IS":
                    repair_token_buffer = []
                    emitted_text = verify_state.pre_nudge_text
                    verify_state.ship_marker_used = True
                    break
            verify_nudge_message: str | None = None
            pre_final_gate = self._pre_final_gate_payload(
                session_id=session_id,
                turn_id=turn_id,
                prompt=prompt,
                harness_state=effective_harness_state,
                observed_public_refs=observed_public_refs,
                coding_mutation_observed=file_edit_calls > 0,
                repair_attempt_count=repair_attempts,
                final_text=emitted_text,
                live_selected_pack_ids=live_selected,
            )
            if pre_final_gate is None:
                # Wave 4b P0: a successful citation repair on a turn with no
                # coding gate (assembly is None OR the gate does not apply) clears
                # the violation, so ``_citation_only_gate_payload`` returns None
                # and the loop breaks HERE rather than through the pass-branch
                # below. Mirror that flush so the re-generated CITED tokens
                # buffered during the repair reach the consumer. Without it the
                # user keeps the original UNCITED streamed answer (or a blank one
                # when a repair round emitted a live response_clear) while the
                # CitationVerdict record, computed on the final emitted_text,
                # reports ``cited`` (answer/verdict divergence). Flush then clear
                # so a later break can never re-emit a stale buffer.
                for buffered in repair_token_buffer:
                    yield buffered
                repair_token_buffer = []
                # PR-V3: on a gate-less turn the verify audit runs HERE (SITE-A),
                # after the flush and before the break. A None nudge delivers as
                # today; a nudge message skips the status yield and the not-block
                # exit below and drops into the nudge setup (block-wrap else arm).
                verify_nudge_message = await self._verify_nudge_check(
                    session_id=session_id,
                    turn_id=turn_id,
                    prompt=prompt,
                    final_text=emitted_text,
                    verify_state=verify_state,
                )
                if verify_nudge_message is None:
                    break
            # PR-V3: the per-pass status yield and the not-block exit (SITE-B) run
            # only when no SITE-A nudge fired. When verify_nudge_message is None
            # here the payload is guaranteed non-None (today SITE-A always breaks
            # on a None payload), so this guard keeps the None payload out of the
            # status yield and is byte-identical on every path that reaches these
            # lines today.
            if verify_nudge_message is None:
                yield RuntimeEvent(
                    type="status", payload=pre_final_gate, turn_id=turn_id
                )
                if pre_final_gate["decision"] != "block":
                    for buffered in repair_token_buffer:
                        yield buffered
                    repair_token_buffer = []
                    # PR-V3: verify audit runs at SITE-B too (after the flush,
                    # before the break) on a passing-gate turn.
                    verify_nudge_message = await self._verify_nudge_check(
                        session_id=session_id,
                        turn_id=turn_id,
                        prompt=prompt,
                        final_text=emitted_text,
                        verify_state=verify_state,
                    )
                    if verify_nudge_message is None:
                        break
            if verify_nudge_message is None:
                if repair_token_buffer:
                    yield RuntimeEvent(
                        type="status",
                        payload={
                            "type": "coding_repair_output_suppressed",
                            "turnId": turn_id,
                            "attempt": repair_attempts,
                            "suppressedTokenEvents": len(repair_token_buffer),
                        },
                        turn_id=turn_id,
                    )
                    # Retain the suppressed attempt so a subsequent citation
                    # fail-open can restore a coherent answer (never blank).
                    last_repair_buffer = list(repair_token_buffer)
                    repair_token_buffer = []
                    repair_output_suppressed = True

                repair_decision = pre_final_gate.get("repairDecision")
                repair_policy = pre_final_gate.get("repairPolicy")
                is_citation_repair = (
                    isinstance(repair_policy, Mapping)
                    and repair_policy.get("source") == "source-citation"
                )
                should_repair = (
                    isinstance(repair_decision, Mapping)
                    and repair_decision.get("action") == "continue_repair"
                    and isinstance(repair_policy, Mapping)
                    and (_coding_repair_loop_enabled() or is_citation_repair)
                )
                if not should_repair:
                    # Wave 4b: citation repair budget exhausted -> FAIL-OPEN. The
                    # already-streamed answer stands; emit the CitationVerdict record
                    # with failOpen=true and append the deterministic one-line hedge
                    # notice (soft-append precedent). The turn always completes.
                    citation_repair = pre_final_gate.get("citationRepair")
                    if isinstance(citation_repair, Mapping) and citation_repair.get("kind"):
                        # P2 follow-up: if ``_evaluate_citation_gate_for_turn``
                        # returns None here (registry vanished mid-turn), the notice
                        # still streams but ``_emit_citation_verdict_record`` writes
                        # nothing (no CitationGateResult). A minimal synthetic
                        # failOpen record would close that observability gap; left as
                        # a follow-up to avoid widening this branch.
                        final_result = self._evaluate_citation_gate_for_turn(
                            session_id=session_id,
                            turn_id=turn_id,
                            prompt=prompt,
                            final_text=emitted_text,
                        )
                        self._emit_citation_verdict_record(
                            session_id=session_id,
                            turn_id=turn_id,
                            result=final_result,
                            repair_attempts=citation_repair_attempts,
                            induced_search=citation_induced_search,
                            fail_open=True,
                        )
                        citation_record_emitted = True
                        # PR-V4 A3: run verify audit record-only inside the citation
                        # fail-open branch. emitted_text at this call site is the
                        # primary answer text BEFORE the hedge notice is yielded below
                        # -- the hedge-suffix exclusion invariant is preserved because
                        # all notice yields are token deltas that do NOT mutate
                        # emitted_text. Any accidental mutation of emitted_text after
                        # this call would silently corrupt resolution stats; the PR-V1
                        # test_hedge_notice_never_trips_detectors fixture is the
                        # detector-side tripwire and the D4-R sha256 is the storage
                        # tripwire.
                        await self._verify_fail_open_record_only(
                            session_id=session_id,
                            turn_id=turn_id,
                            prompt=prompt,
                            emitted_text=emitted_text,
                            verify_state=verify_state,
                        )
                        # Fail-open keeps a coherent answer. A prior repair round's
                        # LIVE response_clear may have blanked the UI while that
                        # round's tokens were suppressed on the re-block; flush the
                        # last attempt so the hedge notice follows a real answer
                        # rather than leaving a blank turn (answer==verdict, no-blank
                        # invariant). When no repair round ran (buffer never
                        # suppressed) this is empty and the primary live answer
                        # already stands, so the notice is a pure suffix as before.
                        for buffered in last_repair_buffer:
                            yield buffered
                        last_repair_buffer = []
                        yield RuntimeEvent(
                            type="status",
                            payload={
                                "type": "source_citation_fail_open",
                                "turnId": turn_id,
                                "repairAttempts": citation_repair_attempts,
                                "inducedSearch": citation_induced_search,
                            },
                            turn_id=turn_id,
                        )
                        notice = str(citation_repair.get("failOpenNotice") or "")
                        if notice:
                            yield RuntimeEvent(
                                type="token",
                                payload={
                                    "type": "text_delta",
                                    "delta": "\n\n" + notice,
                                },
                                turn_id=turn_id,
                            )
                        yield EngineResult(  # type: ignore[misc]
                            terminal=Terminal.completed,
                            usage=usage,
                            cost_usd=0.0,
                            session_id=session_id,
                            turn_id=turn_id,
                        )
                        return
                    # WS6 PR6a/PR6b: convert the hard pre-final refuse into a SOFT
                    # appended notice (research-governance notice OR evidence hedge,
                    # one seam two reason families) when an in-scope research/contract
                    # recipe blocked under the relevant flag. The already-streamed
                    # answer is kept; only a trailing notice SUFFIX is appended.
                    # Skipped (existing hard refuse runs) when the flags are OFF, the
                    # recipe is out of scope, the missing set is empty, or a
                    # repair-suppressed turn discarded its answer (MINOR-2). Fail-open
                    # inside the seam.
                    if not repair_output_suppressed:
                        soft_consequence = self._apply_soft_verification_consequence(
                            turn_id=turn_id,
                            session_id=session_id,
                            final_text=emitted_text,
                            pre_final_gate=pre_final_gate,
                            live_selected_pack_ids=live_selected,
                        )
                        if soft_consequence is not None:
                            notice_suffix_text, status_event = soft_consequence
                            yield status_event
                            yield RuntimeEvent(
                                type="token",
                                payload={
                                    "type": "text_delta",
                                    "delta": notice_suffix_text,
                                },
                                turn_id=turn_id,
                            )
                            yield EngineResult(  # type: ignore[misc]
                                terminal=Terminal.completed,
                                usage=usage,
                                cost_usd=0.0,
                                session_id=session_id,
                                turn_id=turn_id,
                            )
                            return
                    yield EngineResult(  # type: ignore[misc]
                        terminal=Terminal.error,
                        usage=usage,
                        cost_usd=0.0,
                        error="pre_final_evidence_gate_blocked",
                        session_id=session_id,
                        turn_id=turn_id,
                    )
                    return

                max_repair_attempts = _coding_repair_max_attempts(repair_policy)
                next_repair_attempt = repair_attempts + 1
                repair_attempts = next_repair_attempt
                missing_evidence = [
                    str(ref)
                    for ref in pre_final_gate.get("missingEvidence") or []
                    if isinstance(ref, str)
                ]
                missing_validators = [
                    str(ref)
                    for ref in pre_final_gate.get("missingValidators") or []
                    if isinstance(ref, str)
                ]
                citation_repair = pre_final_gate.get("citationRepair")
                citation_repair_message = (
                    str(citation_repair["message"])
                    if isinstance(citation_repair, Mapping)
                    and citation_repair.get("message")
                    else None
                )
                retry_payload = {
                    "type": (
                        "source_citation_repair_scheduled"
                        if citation_repair_message is not None
                        else "coding_repair_retry_scheduled"
                    ),
                    "turnId": turn_id,
                    "attempt": next_repair_attempt,
                    "maxAttempts": max_repair_attempts,
                    "missingEvidence": missing_evidence,
                    "missingValidators": missing_validators,
                }
                yield RuntimeEvent(type="status", payload=retry_payload, turn_id=turn_id)

                if citation_repair_message is not None:
                    # Wave 4b: attribution / induce-search repair instruction (the
                    # citationRepair message replaces the coding continuation text).
                    repair_message = citation_repair_message
                    citation_repair_attempts = next_repair_attempt
                    if isinstance(citation_repair, Mapping) and citation_repair.get(
                        "inducedSearch"
                    ):
                        citation_induced_search = True
                else:
                    repair_message = _build_repair_continuation_message(
                        missing_evidence=missing_evidence,
                        missing_validators=missing_validators,
                        attempt=next_repair_attempt,
                        max_attempts=max_repair_attempts,
                    )
            else:
                # PR-V3: a verify nudge fired at SITE-A or SITE-B. Skip the
                # block-path repair machinery entirely (repair_attempts,
                # citation_repair_attempts, repairDecision and the suppression
                # handling are all inside the guarded arm above and stay
                # there) and set up a nudge continuation that reuses the shared
                # runner_input build below. Control falls into it exactly as a
                # repair round does.
                yield RuntimeEvent(
                    type="status",
                    payload={
                        "type": "verify_nudge_scheduled",
                        "turnId": turn_id,
                        "newFindings": verify_state.pending_new_count,
                        "highFindings": verify_state.pending_high_count,
                        "nudgeRound": verify_state.nudge_rounds,
                    },
                    turn_id=turn_id,
                )
                repair_message = verify_nudge_message
                verify_state.nudge_pending = True
                verify_state.nudge_round_active = True
                verify_state.pre_nudge_text = emitted_text
                verify_state.nudge_rounds += 1
            runner_input = runner_turn_input_cls(
                userId=self._user_id,
                sessionId=session_id,
                turnId=turn_id,
                invocationId=turn_id,
                newMessage=types.Content(  # type: ignore[attr-defined]
                    role="user",
                    parts=[types.Part(text=repair_message)],  # type: ignore[attr-defined]
                ),
                harnessState=effective_harness_state,
            )

            repair_gate_attach = self._attach_gate_callback(
                runner=runner, gate=gate, turn_id=turn_id, cancel=cancel
            )
            repair_hook_attach = self._attach_user_hook_bus(
                runner=runner, session_id=session_id, turn_id=turn_id
            )
            repair_customize_attach = self._attach_customize_rules(
                runner=runner, session_id=session_id, turn_id=turn_id
            )
            repair_route_attach = self._attach_runner_policy_route(
                runner=runner,
                route_selection=route_selection,
            )
            adk_iter = adapter.run_turn(runner_input).__aiter__()  # type: ignore[union-attr]
            attempt_error: Exception | None = None
            _repair_usage: dict[str, int] = {}
            try:
                while True:
                    if cancel.is_set():
                        cancelled = True
                        break

                    step = await self._next_adk_event(adk_iter, cancel)
                    if step is _CANCELLED:
                        cancelled = True
                        break
                    if step is _EXHAUSTED:
                        break

                    adk_event = step
                    event_count += 1
                    self._note_observed_invocation_id(
                        _adk_invocation_id(adk_event)
                    )
                    _repair_reading = _adk_usage_metadata(adk_event)
                    if _repair_reading:
                        _repair_usage.update(_repair_reading)
                    projection = bridge.project_adk_event(adk_event, turn_id=turn_id)  # type: ignore[union-attr]
                    for raw_event in _projected_events_with_transcript_text_fallback(
                        projection,
                        emitted_text=emitted_text,
                    ):
                        safe = sanitize(dict(raw_event))  # type: ignore[operator]
                        if safe is None:
                            continue
                        self._collect_public_refs(safe, observed_public_refs)
                        self._track_pending_tool(safe, pending_tool_ids)
                        if safe.get("type") == "response_clear":
                            emitted_text = ""
                        elif safe.get("type") == "text_delta":
                            delta = safe.get("delta")
                            if isinstance(delta, str):
                                emitted_text += delta
                                if delta:
                                    net_user_text_streamed = True
                        # PR-V3: count tool loop-backs during a verify nudge round
                        # (observability for the PR-V4 verdict record). Repair
                        # rounds leave this inert (nudge_round_active is False).
                        if (
                            verify_state.nudge_round_active
                            and safe.get("type") == "tool_start"
                        ):
                            verify_state.loopback_tool_calls += 1
                        yielded_events += 1
                        self._observe_event(safe, session_id, turn_id)
                        event_kind = _map_event_kind(safe.get("type"))
                        runtime_event = RuntimeEvent(
                            type=event_kind,
                            payload=safe,
                            turn_id=turn_id,
                        )
                        if event_kind == "token" or (
                            verify_state.nudge_round_active
                            and safe.get("type") == "response_clear"
                        ):
                            # Held until the gate re-evaluates: delivered on
                            # pass, suppressed on another block (see the
                            # repair_token_buffer handling at the loop top).
                            # PR-V3: during a verify nudge round the response_clear
                            # is buffered too (it classifies as status and would
                            # blank the UI live). SHIP_AS_IS discards the whole
                            # buffer (UI never blanked); a revision flushes
                            # clear-then-tokens in order. Repair rounds keep the
                            # live-clear behavior byte-identically.
                            repair_token_buffer.append(runtime_event)
                        else:
                            yield runtime_event

                    if event_count >= self._max_event_count:
                        break
            except Exception as exc:  # noqa: BLE001 - surface as terminal error
                attempt_error = exc
            finally:
                await self._aclose_iter(adk_iter)
                self._restore_runner_policy_route(repair_route_attach)
                self._restore_customize_rules(repair_customize_attach)
                self._restore_user_hook_bus(repair_hook_attach)
                self._restore_gate_callback(repair_gate_attach)
                _fold_usage(usage, _repair_usage)

            if cancelled:
                for safe in self._synthesize_orphan_tool_results(
                    pending_tool_ids, turn_id=turn_id, reason="user_interrupt"
                ):
                    yield RuntimeEvent(type="tool", payload=safe, turn_id=turn_id)
                yield RuntimeEvent(
                    type="status",
                    payload={
                        "type": "turn_end",
                        "turnId": turn_id,
                        "status": "aborted",
                        "reason": "user_interrupt",
                    },
                    turn_id=turn_id,
                )
                yield EngineResult(  # type: ignore[misc]
                    terminal=Terminal.aborted,
                    usage=usage,
                    cost_usd=0.0,
                    error="cancelled",
                    session_id=session_id,
                    turn_id=turn_id,
                )
                return
            if attempt_error is not None:
                # PR-3: same root-cause surface as the primary engine_error
                # path — capture the upstream class + sanitized traceback
                # so the dashboard can name the real repair-fork trigger
                # instead of showing a generic "interrupted by repair restart".
                _attempt_detail = self._capture_error_detail(attempt_error)
                yield RuntimeEvent(
                    type="status",
                    payload={
                        "type": "engine_error_detail",
                        "turnId": turn_id,
                        **_attempt_detail,
                    },
                    turn_id=turn_id,
                )
                for safe in self._synthesize_orphan_tool_results(
                    pending_tool_ids,
                    turn_id=turn_id,
                    reason="repair_fork",
                    error_detail=_attempt_detail,
                ):
                    yield RuntimeEvent(type="tool", payload=safe, turn_id=turn_id)
                yield EngineResult(  # type: ignore[misc]
                    terminal=Terminal.error,
                    usage=usage,
                    cost_usd=0.0,
                    error=str(attempt_error) or attempt_error.__class__.__name__,
                    session_id=session_id,
                    turn_id=turn_id,
                )
                return

        # PR5b terminal-side honest blocked notice. Computed HERE (not cached at
        # the recovery-loop exit) on the CURRENT ``emitted_text``, because the
        # zero-edit guard and the coding-repair fork above can mutate it and a
        # ``response_clear`` can blank it. Reaching this point means every
        # finalizer error-terminal pre-emption already returned: the cancel /
        # engine-error surfaces, the six LLM-block gates
        # (custom_llm_criterion_blocked), and the pre-final-gate / coding-repair
        # loop (pre_final_evidence_gate_blocked). So a gated turn correctly
        # surfaces Terminal.error and never reaches this notice (gates win).
        # When escalation is OFF the whole block is a no-op (escalated_blank is
        # False), so the OFF and PR5a paths stay byte-identical.
        escalated_blank = (
            bool(empty_response_recovery)
            and getattr(empty_response_recovery, "escalate", False)
            and recoveries_used > 0
            and not budget_exhausted
            and not emitted_text
            and not net_user_text_streamed
        )
        if escalated_blank:
            yield RuntimeEvent(
                type="status",
                payload={
                    "type": "empty_response_blocked",
                    "reason": "exhausted_empty",
                    # initial invocation + the corrective re-invocations.
                    "attempts": recoveries_used + 1,
                },
                turn_id=turn_id,
            )
            # The ONLY mechanism that suppresses the web fallback banner: a
            # synthetic text_delta (token-kind) RuntimeEvent carrying the
            # deterministic non-answer. EngineResult has no final_text field;
            # the streamed text_delta is the entire mechanism.
            yield RuntimeEvent(
                type="token",
                payload={
                    "type": "text_delta",
                    "delta": build_blocked_notice(),
                },
                turn_id=turn_id,
            )

        # Wave 4b: emit the single per-turn source-citation verdict record for a
        # repair-mode turn that reached normal completion (clean/cited,
        # advisory-degrade, or a successful repair). The fail-open branch already
        # emitted its own record; audit mode emits before the loop. Re-evaluate
        # on the FINAL emitted_text so a successful repair records `cited`.
        if not citation_record_emitted and self._citation_repair_active():
            final_citation_result = self._evaluate_citation_gate_for_turn(
                session_id=session_id,
                turn_id=turn_id,
                prompt=prompt,
                final_text=emitted_text,
            )
            if final_citation_result is not None:
                self._emit_citation_verdict_record(
                    session_id=session_id,
                    turn_id=turn_id,
                    result=final_citation_result,
                    repair_attempts=citation_repair_attempts,
                    induced_search=citation_induced_search,
                    fail_open=False,
                )

        # PR-V4: emit the per-turn verify reply verdict at normal completion.
        # Guarded inside _emit_verify_reply_verdict on master flag AND non-empty
        # delivered_text. Hedge-suffix exclusion: emitted_text here is the
        # suffix-free delivered answer (the fail-open hedge, soft-consequence
        # suffix, and hard-blocked notice are all yielded as token deltas that do
        # NOT mutate emitted_text). Terminal.error and aborted paths exit before
        # this point, so only Terminal.completed turns reach this emit.
        self._emit_verify_reply_verdict(
            session_id=session_id,
            turn_id=turn_id,
            verify_state=verify_state,
            delivered_text=emitted_text,
        )

        yield EngineResult(  # type: ignore[misc]
            terminal=Terminal.completed,
            usage=usage,
            cost_usd=0.0,
            error=None,
            session_id=session_id,
            turn_id=turn_id,
        )

    def _enrich_goal_nudge_status(
        self,
        payload: dict[str, object],
        nudge: "GoalNudge",
        *,
        evidence_records: tuple[object, ...],
    ) -> None:
        """WS6 PR6c: enrich the goal_nudge continue status with evidence reasons.

        When ``MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED`` is ON, add transport-safe
        ``missingValidators``/``requirementLabels``/``reasonCodes`` (derived from
        the SAME evidence records the goal_nudge gate read) so the client sees
        WHY the turn continued. Mutates ``payload`` in place.

        Terminal-free and side-effect-light: this NEVER changes the
        continue/re-invoke control flow and NEVER adds a reserved
        ``text``/``content``/``delta`` key (MINOR-1 transport collision guard).
        Strict default-OFF and fail-open: when the flag is unset, or anything
        faults, the payload is left byte-identical to today. Inert until WS3
        enables goal_nudge and sets ``required_evidence``.
        """
        import os  # noqa: PLC0415

        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                parse_evidence_hedge_on_guess_enabled,
            )

            if not parse_evidence_hedge_on_guess_enabled(os.environ):
                return
            from magi_agent.runtime.goal_nudge import (  # noqa: PLC0415
                goal_nudge_evidence_reasons,
            )

            reasons = goal_nudge_evidence_reasons(
                nudge, evidence_records=evidence_records
            )
            if not reasons.requirement_labels:
                return
            payload["missingValidators"] = list(reasons.missing_validators)
            payload["requirementLabels"] = list(reasons.requirement_labels)
            payload["reasonCodes"] = list(reasons.reason_codes)
        except Exception:
            # Fail-open: enrichment is best-effort; never wedge the nudge loop.
            logger.debug(
                "goal_nudge evidence-reason enrichment failed; status left unenriched",
                exc_info=True,
            )

    @staticmethod
    def _open_todo_count(ledger_snapshot: Sequence[object]) -> int | None:
        """Number of not-``completed`` todos in a ledger snapshot, or ``None``.

        ``None`` when the snapshot is empty (no ledger signal). WS3 PR3b helper
        for the ``goal_paused`` payload; never raises (defensive ``getattr``).
        """
        if not ledger_snapshot:
            return None
        return sum(
            1
            for item in ledger_snapshot
            if getattr(item, "status", None) != "completed"
        )

    def _goal_paused_event(
        self,
        *,
        turn_id: str,
        reason: str,
        objective: str | None,
        open_todos: int | None,
    ) -> RuntimeEvent:
        """Build the WS3 PR3b ``goal_paused`` honest-stop status event.

        Additive: this is a NEW sibling event the UI renders as "worked on this
        but could not confirm it is done". It does NOT delete output or append a
        synthetic success message; the turn still ends with the same ``break``.
        """
        return RuntimeEvent(
            type="status",
            payload={
                "type": "goal_paused",
                "reason": reason,
                "objective": objective,
                "openTodos": open_todos,
            },
            turn_id=turn_id,
        )

    def _run_deterministic_auto_continue(
        self,
        *,
        turn_id: str,
        session_id: str,
        seam2_snapshot: tuple[object, ...],
        ok_tool_ends: int,
        blocked_tool_ends: int,
        prev_ledger: tuple[object, ...],
        prev_evidence_count: int,
        continuations_used: int,
        no_progress_streak: int,
        wrap_up_spent: bool,
        turn_start: float,
        effective_harness_state: object,
        runner_turn_input_cls: Callable[..., object],
        types_mod: object,
    ) -> tuple[list[RuntimeEvent], _AutoContinueExecResult]:
        """SEAM 2 deterministic auto-continue executor (extracted, byte-identical).

        Behaviour-preserving extraction of the clean-break ``seam2_outcome ==
        "continue" and self._auto_continue_enabled`` arm (design 5.3). Assembles
        the :class:`AttemptProgress` from the caller's per-attempt counters plus
        the ledger / evidence deltas, runs the pure
        ``decide_auto_continue`` measurable-progress gate, and returns the events
        to yield plus the loop-control action and the updated continuation
        counters. This runs the SAME code the ambient path will call in U4. No
        model call, no ``await`` (all inputs are precomputed by the caller), so
        collecting the burst of events into a list and yielding them in order is
        byte-identical to the previous inline ``yield`` sequence.
        """
        from magi_agent.runtime.goal_loop_auto_continue import (  # noqa: PLC0415
            AttemptProgress,
            budgets_for_intensity,
            decide_auto_continue,
            ledger_changed,
        )
        from magi_agent.runtime.per_turn_goal_intensity import (  # noqa: PLC0415
            current_per_turn_goal_mission,
        )
        import time as _auto_continue_time  # noqa: PLC0415

        events: list[RuntimeEvent] = []

        # Mission intensity: the composer Goal-mission toggle (per-turn context)
        # raises the budget ceiling; the constructor default is the ambient
        # fallback.
        auto_continue_mission = (
            current_per_turn_goal_mission() or self._auto_continue_mission
        )
        evidence_now = self._collect_evidence(turn_id)
        progress = AttemptProgress(
            ok_tool_ends=ok_tool_ends,
            blocked_tool_ends=blocked_tool_ends,
            ledger_changed=ledger_changed(prev_ledger, seam2_snapshot),
            new_evidence_records=max(
                0,
                len(evidence_now) - prev_evidence_count,
            ),
        )
        decision = decide_auto_continue(
            ledger_wants_continue=True,
            progress=progress,
            continuations_used=continuations_used,
            prior_no_progress_streak=no_progress_streak,
            elapsed_seconds=(
                _auto_continue_time.monotonic() - turn_start
            ),
            budgets=budgets_for_intensity(mission=auto_continue_mission),
            wrap_up_already_spent=wrap_up_spent,
        )
        no_progress_streak = decision.no_progress_streak
        open_todos_now = self._open_todo_count(seam2_snapshot)
        if decision.outcome == "stop_budget":
            events.append(
                RuntimeEvent(
                    type="status",
                    payload={
                        "type": "goal_loop_exhausted",
                        "reason": decision.reason,
                        "continuations": continuations_used,
                    },
                    turn_id=turn_id,
                )
            )
            events.append(
                self._goal_paused_event(
                    turn_id=turn_id,
                    reason="budget_exhausted",
                    objective=None,
                    open_todos=open_todos_now,
                )
            )
            return events, _AutoContinueExecResult(
                action="break",
                runner_input=None,
                used=continuations_used,
                no_progress_streak=no_progress_streak,
                wrap_up_spent=wrap_up_spent,
            )
        if decision.outcome == "paused_waiting_on_approvals":
            events.append(
                self._goal_paused_event(
                    turn_id=turn_id,
                    reason="waiting_on_approvals",
                    objective=None,
                    open_todos=open_todos_now,
                )
            )
            return events, _AutoContinueExecResult(
                action="break",
                runner_input=None,
                used=continuations_used,
                no_progress_streak=no_progress_streak,
                wrap_up_spent=wrap_up_spent,
            )
        if decision.outcome == "paused_no_progress":
            events.append(
                self._goal_paused_event(
                    turn_id=turn_id,
                    reason="no_progress",
                    objective=None,
                    open_todos=open_todos_now,
                )
            )
            return events, _AutoContinueExecResult(
                action="break",
                runner_input=None,
                used=continuations_used,
                no_progress_streak=no_progress_streak,
                wrap_up_spent=wrap_up_spent,
            )
        if decision.outcome in {"continue", "wrap_up"}:
            is_wrap_up = decision.outcome == "wrap_up"
            if is_wrap_up:
                wrap_up_spent = True
            continuations_used += 1
            events.append(
                RuntimeEvent(
                    type="status",
                    payload={
                        "type": (
                            "goal_loop_wrap_up"
                            if is_wrap_up
                            else "goal_loop_continuation"
                        ),
                        "reason": decision.reason,
                        "continuation": continuations_used,
                        "openTodos": open_todos_now,
                        "source": "auto_continue",
                    },
                    turn_id=turn_id,
                )
            )
            # Boundary before the auto-continue re-invoke: blank the prior
            # attempt so the continuation's fresh answer does not concatenate
            # onto it (the duplicated-answer glue, bounded by max_turns). Both
            # consumers of this "continue" result yield these events and blank
            # their own ``emitted_text``. Mirrors the repair / final-gate rounds.
            events.append(
                RuntimeEvent(
                    type=_map_event_kind("response_clear"),
                    payload={
                        "type": "response_clear",
                        "turnId": turn_id,
                        "reason": "goal_loop_continuation",
                    },
                    turn_id=turn_id,
                )
            )
            runner_input = runner_turn_input_cls(
                userId=self._user_id,
                sessionId=session_id,
                turnId=turn_id,
                invocationId=turn_id,
                newMessage=types_mod.Content(  # type: ignore[attr-defined]
                    role="user",
                    parts=[
                        types_mod.Part(  # type: ignore[attr-defined]
                            text=(
                                _AUTO_CONTINUE_WRAP_UP_PROMPT
                                if is_wrap_up
                                else _AUTO_CONTINUE_PROMPT
                            )
                        )
                    ],
                ),
                harnessState=effective_harness_state,
            )
            return events, _AutoContinueExecResult(
                action="continue",
                runner_input=runner_input,
                used=continuations_used,
                no_progress_streak=no_progress_streak,
                wrap_up_spent=wrap_up_spent,
            )
        # decision.outcome == "stop" (ledger_wants_continue was True here so this
        # is unreachable in practice) -> fall through to the bare break.
        return events, _AutoContinueExecResult(
            action="break",
            runner_input=None,
            used=continuations_used,
            no_progress_streak=no_progress_streak,
            wrap_up_spent=wrap_up_spent,
        )

    def _collect_evidence(self, turn_id: str) -> tuple[object, ...]:
        """Return evidence records for the given turn.

        The engine driver does not own an evidence ledger (that lives at the
        recipe/harness layer above).  When no ``evidence_collector`` was
        provided at construction time, returns an empty tuple — ``_goal_is_met``
        then falls through to the ``required_evidence``-empty path (relying on
        the synthetic self-check turn), which is byte-identical to pre-seam
        behaviour.

        When the driver was constructed with an ``evidence_collector`` callable
        (the DI seam), delegates to it: ``evidence_collector(turn_id)`` → a
        sequence of evidence records → returned as a tuple.  The harness layer
        above the engine uses this seam to make evidence-backed :class:`GoalNudge`
        goals functional without coupling the engine to a concrete ledger type.
        """
        if self._evidence_collector is None:
            return ()
        # Always query the engine's own ``turn_id`` first (preserves the DI
        # contract + every existing caller/test that records and queries under
        # the same id — the coding/hosted shape).
        records: list[object] = list(self._evidence_collector(turn_id))
        # Root-cause-1 reconciliation: ALSO fold in records the collector stored
        # under any ADK ``invocation_id`` observed on this turn's live event
        # stream (which is what the CLI tool wrapper keys on). Deduped by object
        # identity so an observed id equal to ``turn_id`` never double-counts.
        # Purely additive: with no observed ids (the coding/hosted test shape)
        # this is byte-identical to the prior single-query behaviour.
        # Flag-gated (default-OFF) so existing coding/hosted live turns are
        # untouched unless source-grounded enforcement is explicitly enabled.
        import os as _recon_os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            is_dashboard_pack_authoring_enabled,
            parse_source_ledger_evidence_gate_enabled,
        )

        if self._observed_invocation_ids and (
            parse_source_ledger_evidence_gate_enabled(_recon_os.environ)
            or is_dashboard_pack_authoring_enabled(_recon_os.environ)
        ):
            seen_ids: set[int] = {id(record) for record in records}
            for invocation_id in self._observed_invocation_ids:
                if invocation_id == turn_id:
                    continue
                for extra in self._collect_for_id(invocation_id):
                    if id(extra) not in seen_ids:
                        seen_ids.add(id(extra))
                        records.append(extra)
        return tuple(records)

    def _note_observed_invocation_id(self, invocation_id: object) -> None:
        """Record an ADK ``invocation_id`` seen on this turn's live event stream.

        ``_drive`` calls this for each raw ADK event so the pre-final gate's
        ``_collect_evidence`` can reconcile the engine's static ``turn_id`` with
        the ADK invocation id that the CLI tool wrapper keys evidence under.
        Defensive: only non-empty strings are kept; anything else is ignored so
        a malformed event can never wedge the turn.
        """
        if isinstance(invocation_id, str) and invocation_id.strip():
            self._observed_invocation_ids.add(invocation_id.strip())

    def _collect_for_id(self, turn_id: str) -> tuple[object, ...]:
        """Query the wired collector for one turn/invocation id, fail-soft.

        Prefers the owning collector's ``collect_for_turn`` (recovered via the
        bound method's ``__self__``, the same pattern as ``_record_phase_reached``)
        and falls back to calling the DI callable directly. Any failure yields
        ``()`` so reconciliation can never break the gate.
        """
        collector = self._evidence_collector
        if collector is None:
            return ()
        owner = getattr(collector, "__self__", None)
        collect_for_turn = getattr(owner, "collect_for_turn", None)
        try:
            if callable(collect_for_turn):
                return tuple(collect_for_turn(turn_id))
            return tuple(collector(turn_id))
        except Exception:
            return ()

    def _record_phase_reached(
        self,
        *,
        session_id: str,
        turn_id: str,
        phase: object,
    ) -> None:
        """Feed the turn's resolved phase to the evidence collector (Stage 3).

        The collector is wired in as its ``collect_for_turn`` bound method (see
        ``cli/wiring.py``); recover the owning ``LocalToolEvidenceCollector``
        instance via ``__self__`` and delegate to its ``record_phase_reached``.
        Flag-gating + fail-open live in the collector, but this seam is also
        defensive: a missing collector / method / phase records nothing and
        never breaks the turn (byte-identical when no phase producer exists).
        """
        if not isinstance(phase, str) or not phase:
            return
        collector = self._evidence_collector
        if collector is None:
            return
        owner = getattr(collector, "__self__", None)
        record_phase = getattr(owner, "record_phase_reached", None)
        if not callable(record_phase):
            return
        try:
            record_phase(session_id, turn_id, phase)
        except Exception:
            logger.debug("phase-reached evidence record failed", exc_info=True)

    def _record_verifier_verdicts(
        self,
        *,
        session_id: str,
        turn_id: str,
        verifier_bus: Mapping[str, object],
    ) -> None:
        """Feed the turn's verifier-bus verdicts to the collector (Stage 2).

        The verifier bus (``execute_pre_final_verifier_bus``) returns a
        ``results`` list whose entries carry ``verifierId`` (the verifier
        stage/contract id) and ``status`` (pass/failed/missing/...). Each is
        recorded as a ``custom:VerifierVerdict`` evidence_record in the same
        per-``(session, turn)`` ledger so ``InspectSelfEvidence`` can project
        the REAL verdicts. Mirrors ``_record_phase_reached``: the collector is
        wired as its ``collect_for_turn`` bound method, so the owning
        ``LocalToolEvidenceCollector`` is recovered via ``__self__``. Flag-gating
        + fail-open live in the collector; this seam is also defensive (missing
        collector / method records nothing and never breaks the turn).
        """
        collector = self._evidence_collector
        if collector is None:
            return
        owner = getattr(collector, "__self__", None)
        record_verdict = getattr(owner, "record_verifier_verdict", None)
        if not callable(record_verdict):
            return
        results = verifier_bus.get("results")
        if not isinstance(results, list):
            return
        for result in results:
            if not isinstance(result, Mapping):
                continue
            stage = result.get("verifierId")
            status = result.get("status")
            if not isinstance(stage, str) or not stage:
                continue
            if not isinstance(status, str) or not status:
                continue
            try:
                record_verdict(session_id, turn_id, stage, status)
            except Exception:
                logger.debug("verifier-verdict evidence record failed", exc_info=True)

    async def _attempt_run_recovery(
        self,
        *,
        error: Exception,
        session_id: str,
        turn_id: str,
        state: "RecoveryAttemptState | None",
    ) -> "tuple[RecoveryAttemptState | None, bool]":
        """Classify a run-invocation error and apply backoff for a retryable one.

        Returns ``(updated_state, recovered)``. ``recovered=True`` means a
        strategy succeeded (e.g. RateLimit slept the Retry-After delay) and the
        caller should RE-INVOKE the run. ``recovered=False`` means the error is
        terminal, is prompt-too-long / context-overflow (NOT blind-retried —
        it would just fail again; PR13 compaction territory), or no strategy
        applied — so the caller surfaces it as a terminal error.

        This activates the EXISTING ``ErrorClassifier`` + ``RecoveryEngine``
        (not a reimplementation). The substitute-the-response
        ``on_model_error_callback`` seam in ``resilience_plugin`` is deliberately
        NOT used for retry (it cannot re-invoke the model); recovery lives here,
        at the genuine run-invocation boundary.
        """

        recovery = self._recovery
        if recovery is None:  # pragma: no cover - guarded by caller
            return state, False

        from magi_agent.runtime.error_recovery import (  # noqa: PLC0415
            ErrorClassifier,
            ErrorKind,
            RecoverableError,
        )

        classified = ErrorClassifier.classify(error)
        if not isinstance(classified, RecoverableError):
            return state, False  # terminal -> propagate
        if classified.kind == ErrorKind.PROMPT_TOO_LONG:
            # Re-issuing the identical (over-long) request would just fail again.
            # Do NOT blind-retry; leave it to propagate (PR13 compaction seam).
            return state, False

        result, new_state = await recovery.engine.attempt_recovery(
            error=classified,
            messages=[],
            session_key=session_id,
            turn_id=turn_id,
            state=state,
        )
        return new_state, bool(result.success)

    async def _next_adk_event(
        self,
        adk_iter: AsyncIterator[object],
        cancel: asyncio.Event,
    ) -> object:
        """Pull the next ADK event, racing it against ``cancel.wait()``.

        Returns the event, or the ``_EXHAUSTED`` / ``_CANCELLED`` sentinels.
        """

        next_task = asyncio.ensure_future(self._anext(adk_iter))
        cancel_task = asyncio.ensure_future(cancel.wait())
        try:
            done, _pending = await asyncio.wait(
                {next_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:  # pragma: no cover - propagate cleanup
            next_task.cancel()
            cancel_task.cancel()
            raise

        if next_task in done:
            cancel_task.cancel()
            with _suppress_cleanup_errors():
                await cancel_task
            result = next_task.result()
            return result

        # cancel fired first; abandon the in-flight pull.
        next_task.cancel()
        with _suppress_cleanup_errors():
            await next_task
        return _CANCELLED

    @staticmethod
    async def _anext(adk_iter: AsyncIterator[object]) -> object:
        try:
            return await adk_iter.__anext__()
        except StopAsyncIteration:
            return _EXHAUSTED

    @staticmethod
    async def _aclose_iter(adk_iter: AsyncIterator[object]) -> None:
        aclose = getattr(adk_iter, "aclose", None)
        if aclose is None:
            return
        with _suppress_cleanup_errors():
            try:
                await aclose()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    @staticmethod
    def _track_pending_tool(
        safe: dict[str, object],
        pending_tool_ids: dict[str, str],
    ) -> None:
        event_type = safe.get("type")
        tool_id = safe.get("id")
        if not isinstance(tool_id, str):
            return
        if event_type == "tool_start":
            pending_tool_ids[tool_id] = str(safe.get("name") or "tool")
        elif event_type == "tool_end":
            pending_tool_ids.pop(tool_id, None)

    #: Reason → human-readable ``output_preview`` for orphan ``tool_end`` sweeps.
    #: The pre-fix code hard-coded the user-cancellation phrasing on every path
    #: (including engine error and repair-fork abort) — Kevin saw "interrupted
    #: by user cancellation" on tools nobody cancelled. The structured ``reason``
    #: field on each event is the authoritative signal for new consumers; the
    #: preview string is kept for back-compat surfaces (exports, downstream
    #: transcripts) and now matches what actually happened.
    _ORPHAN_TOOL_PREVIEW_BY_REASON: dict[str, str] = {
        "user_interrupt": "tool interrupted by user cancellation",
        "engine_error": "tool interrupted by engine error",
        "repair_fork": "tool interrupted by repair restart",
    }

    @staticmethod
    def _synthesize_orphan_tool_results(
        pending_tool_ids: dict[str, str],
        *,
        turn_id: str,
        reason: str = "user_interrupt",
        error_detail: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        """Build interrupted ``tool_end`` events for any unmatched tool calls.

        These keep the transcript balanced (every tool_use gets a tool_result)
        so a resumed session does not see a dangling tool call.

        ``reason`` names what actually caused the sweep so the
        ``output_preview`` does not falsely blame the user when the engine
        errored or a repair fork aborted. Unknown reasons fall through to a
        neutral phrasing (still not the user-cancellation literal) and the
        raw reason is carried in the event's ``reason`` field for accurate
        downstream rendering.

        ``error_detail`` (optional) attaches the sanitized upstream-exception
        detail produced by :meth:`_capture_error_detail` so the dashboard can
        show the REAL trigger per tool instead of a generic "interrupted by
        engine error" prose line. Omitted on user-interrupt paths (no
        upstream exception to report) and on legacy callers that haven't
        adopted the detail capture yet.
        """

        preview = MagiEngineDriver._ORPHAN_TOOL_PREVIEW_BY_REASON.get(
            reason, f"tool interrupted: {reason}"
        )
        results: list[dict[str, object]] = []
        for tool_id in pending_tool_ids:
            event: dict[str, object] = {
                "type": "tool_end",
                "id": tool_id,
                "status": "error",
                "output_preview": preview,
                "reason": reason,
                "durationMs": 0,
                "interrupted": True,
            }
            if error_detail is not None:
                # Copy so a mutation downstream cannot poison the shared dict.
                event["errorDetail"] = dict(error_detail)
            results.append(event)
        pending_tool_ids.clear()
        return results

    #: Hard cap on the sanitized traceback we attach to status payloads. Wide
    #: enough to keep the inner frame + the message (where the real signal is)
    #: but bounded so a runaway exception text cannot balloon the SSE stream.
    _SAFE_TRACEBACK_MAX_CHARS: int = 2048

    #: Regex of leaky shapes the orphan-sweep / engine_error_detail status
    #: payload must NOT carry to the dashboard. Borrowed from the gate5b
    #: boundary redactor and trimmed to the patterns that actually appear in
    #: a Python traceback (paths, keys, tokens). Conservative + fail-soft;
    #: never raises.
    _SAFE_TRACEBACK_REDACTION_RE = re.compile(
        r"(?:"
        r"sk-[A-Za-z0-9_-]{8,}|"
        r"AIza[A-Za-z0-9_-]{20,}|"
        r"AKIA[0-9A-Z]{8,}|"
        r"xox[a-z]-[A-Za-z0-9-]{8,}|"
        r"gh[opusr]_[A-Za-z0-9_]+|"
        r"github_pat_[A-Za-z0-9_]+|"
        r"Bearer\s+[^\s,;\"']+|"
        r"/Users/[^\s,;\"'\\)]*|"
        r"/home/[^\s,;\"'\\)]*|"
        r"/workspace/[^\s,;\"'\\)]*|"
        r"/data/bots/[^\s,;\"'\\)]*"
        r")",
        re.IGNORECASE,
    )

    @staticmethod
    def _sanitize_traceback_for_status(raw: object) -> str:
        """Redact leaky shapes + cap length for a status-payload-safe string.

        Empty / non-string inputs return ``""``. Truncation appends a clear
        cap marker so the consumer can tell the trace was shortened (vs.
        actually ended there).
        """
        if not isinstance(raw, str) or not raw:
            return ""
        redacted = MagiEngineDriver._SAFE_TRACEBACK_REDACTION_RE.sub(
            "[redacted]", raw
        )
        cap = MagiEngineDriver._SAFE_TRACEBACK_MAX_CHARS
        if len(redacted) <= cap:
            return redacted
        # Keep the HEAD (where the entry frame + class are) — that is what
        # names the actual trigger. The tail (deep stack inside ADK / litellm)
        # is the part that bloats and rarely helps a first triage.
        head = redacted[: cap - 32]
        return f"{head}… [truncated, {len(redacted)} chars total]"

    @staticmethod
    def _capture_error_detail(exc: BaseException) -> dict[str, object]:
        """Capture the upstream exception's class + message + sanitized trace.

        The engine's pre-existing ``engine_error = str(attempt_error) or
        attempt_error.__class__.__name__`` collapsed all three into a single
        string and lost the rest. This helper preserves the class (the
        single most useful triage signal) plus a redacted preview of the
        traceback so the operator can name the real trigger on the first
        repro instead of having to dig through console logs.

        Never raises: defensive coercions guard every read.
        """
        import traceback as _traceback  # noqa: PLC0415

        error_class = exc.__class__.__name__
        try:
            message_raw = str(exc)
        except Exception:  # noqa: BLE001 — exotic exceptions may raise in __str__.
            message_raw = ""
        message = MagiEngineDriver._sanitize_traceback_for_status(message_raw)
        try:
            tb_text = "".join(
                _traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        except Exception:  # noqa: BLE001 — formatting failure must not crash sweep.
            tb_text = ""
        return {
            "errorClass": error_class,
            "message": message,
            "tracebackPreview": MagiEngineDriver._sanitize_traceback_for_status(tb_text),
        }

    def _runner_policy_payload(self) -> dict[str, object] | None:
        if self._runner_policy_assembly is None:
            return None
        return self._runner_policy_assembly.to_public_payload()

    def _with_runner_policy_harness_state(
        self,
        harness_state: object | None,
        *,
        route_selection: Mapping[str, object] | None = None,
    ) -> object | None:
        policy_payload = self._runner_policy_payload()
        if policy_payload is None and route_selection is None:
            return harness_state
        additions: dict[str, object] = {}
        if policy_payload is not None:
            additions["runnerPolicyAssembly"] = policy_payload
        if route_selection is not None and route_selection.get("routeDenied") is not True:
            additions["activeRunnerRoute"] = dict(route_selection)
        if harness_state is None:
            return additions
        if isinstance(harness_state, Mapping):
            merged = dict(harness_state)
            for key, value in additions.items():
                merged.setdefault(key, value)
            return merged
        return {
            "resolvedHarnessStateType": harness_state.__class__.__name__,
            **additions,
        }

    def _runner_policy_route_selection(
        self,
        *,
        runner: object,
        prompt: str,
        harness_state: object | None,
    ) -> dict[str, object] | None:
        assembly = self._runner_policy_assembly
        if assembly is None or not self._is_runner_policy_routing_enabled():
            return None
        phase_routes = _phase_routes(assembly.phase_routing)
        if not phase_routes:
            return None
        phase, classified_pick = _classify_policy_phase_with_softening(
            phases=tuple(phase_routes.keys()),
            prompt=prompt,
            harness_state=harness_state,
            assembly=assembly,
            phase_routes=phase_routes,
        )
        route = phase_routes.get(phase)
        if not isinstance(route, Mapping):
            return None
        phase_route_denied = bool(route.get("routeDenied") or route.get("route_denied"))
        phase_reason_codes = list(
            _str_tuple(route.get("reasonCodes") or route.get("reason_codes"))
        )
        plan_route_denied = bool(
            _routing_field(assembly.phase_routing, "routeDenied", "route_denied")
        )
        plan_reason_codes = list(
            _str_tuple(_routing_field(assembly.phase_routing, "reasonCodes", "reason_codes"))
        )
        local_tool_names = _local_tool_names_for_route(
            runner=runner,
            assembly=assembly,
            phase=phase,
            route=route,
        )
        intent_bindings = compile_intent_bindings(
            assembly, enabled=_recipe_intent_binding_enabled()
        )
        selection: dict[str, object] = {
            "schemaVersion": "openmagi.localRunnerRouteSelection.v1",
            "source": "recipe-materializer.phase-routing",
            "phase": phase,
            "modelProvider": _non_empty_str(route.get("provider"), assembly.model_provider),
            "modelLabel": _non_empty_str(route.get("model"), assembly.model_label),
            "modelTier": _non_empty_str(route.get("tier"), "standard"),
            "runtimeSurface": "local_oss_cli",
            "toolIntents": list(assembly.tool_intents),
            "providerIntents": list(assembly.provider_intents),
            "localToolNames": list(local_tool_names),
            "routeDenied": phase_route_denied or plan_route_denied,
            "phaseRouteDenied": phase_route_denied,
            "planRouteDenied": plan_route_denied,
            "denialReason": _non_empty_str(
                _routing_field(assembly.phase_routing, "denialReason", "denial_reason"),
                "",
            ),
            "reasonCodes": list(dict.fromkeys([*phase_reason_codes, *plan_reason_codes])),
            "authority": {
                "providerCalled": False,
                "productionWriteAllowed": False,
                "externalIntegrationAttached": False,
            },
        }
        # E-8: when the keyword classifier picked a denied phase and we
        # soft-failed to the conversational fallback, surface both so
        # observability sees the softening event without losing the
        # "what was classified" signal.
        if classified_pick is not None and classified_pick != phase:
            selection["phaseSoftened"] = True
            selection["phaseClassified"] = classified_pick
        if intent_bindings:
            selection["intentBindings"] = intent_bindings
        return selection

    @staticmethod
    def _runner_policy_route_block_payload(
        *,
        route_selection: Mapping[str, object] | None,
        turn_id: str,
        fail_closed: bool = False,
    ) -> dict[str, object] | None:
        if route_selection is None or route_selection.get("routeDenied") is not True:
            return None
        reason_codes = list(_str_tuple(route_selection.get("reasonCodes")))
        return {
            "type": "runner_policy_route_blocked",
            "turnId": turn_id,
            "phase": _non_empty_str(route_selection.get("phase"), "unknown"),
            "reasonCodes": reason_codes,
            "routeDecision": (
                "blocked_before_provider_call"
                if fail_closed
                else "audited_configured_model_continues"
            ),
            "authority": {
                "providerCalled": False,
                "configuredModelContinues": not fail_closed,
                "productionWriteAllowed": False,
                "externalIntegrationAttached": False,
            },
        }

    def _attach_runner_policy_route(
        self,
        *,
        runner: object,
        route_selection: Mapping[str, object] | None,
    ) -> "_RunnerRouteAttachment | None":
        if route_selection is None or route_selection.get("routeDenied") is True:
            return None
        agent = getattr(runner, "agent", None)
        if agent is None:
            return None

        original_tools = getattr(agent, "tools", _MISSING)
        original_instruction = getattr(agent, "instruction", _MISSING)
        original_agent_route = getattr(agent, "_magi_active_runner_route_selection", _MISSING)
        original_runner_route = getattr(runner, "_magi_active_runner_route_selection", _MISSING)

        local_tool_names = set(_str_tuple(route_selection.get("localToolNames")))
        if isinstance(original_tools, list) and local_tool_names:
            routed_tools = [
                tool for tool in original_tools if _tool_name(tool) in local_tool_names
            ]
            if routed_tools:
                try:
                    agent.tools = routed_tools
                except Exception:
                    pass

        if isinstance(original_instruction, str):
            try:
                agent.instruction = (
                    f"{original_instruction}\n\n"
                    f"<runner_policy_route>\n"
                    f"Local recipe route phase: {route_selection.get('phase')}. "
                    f"Policy model route: {route_selection.get('modelProvider')}/"
                    f"{route_selection.get('modelLabel')}. "
                    "Use only the tools exposed by this local route. "
                    "This route does not grant production write authority or "
                    "external integration authority.\n"
                    f"</runner_policy_route>"
                )
            except Exception:
                pass

        for target in (agent, runner):
            try:
                setattr(
                    target,
                    "_magi_active_runner_route_selection",
                    dict(route_selection),
                )
            except Exception:
                pass

        return _RunnerRouteAttachment(
            agent=agent,
            runner=runner,
            original_tools=original_tools,
            original_instruction=original_instruction,
            original_agent_route=original_agent_route,
            original_runner_route=original_runner_route,
        )

    @staticmethod
    def _restore_runner_policy_route(attachment: "_RunnerRouteAttachment | None") -> None:
        if attachment is None:
            return
        _restore_attr(attachment.agent, "tools", attachment.original_tools)
        _restore_attr(attachment.agent, "instruction", attachment.original_instruction)
        _restore_attr(
            attachment.agent,
            "_magi_active_runner_route_selection",
            attachment.original_agent_route,
        )
        _restore_attr(
            attachment.runner,
            "_magi_active_runner_route_selection",
            attachment.original_runner_route,
        )

    async def _read_live_selected_recipe_pack_ids(self, session_id: str) -> tuple[str, ...]:
        """Read accumulated select_recipe picks from ADK session state. Fail-open → ()."""
        try:
            from magi_agent.config.env import recipe_routing_llm_enabled  # noqa: PLC0415
            if not recipe_routing_llm_enabled():
                return ()
            from magi_agent.recipes.recipe_routing import (  # noqa: PLC0415
                SELECTED_RECIPE_PACK_IDS_STATE_KEY,
            )
            runner = self._runner
            svc = getattr(runner, "_session_service", None)
            app_name = getattr(runner, "_app_name", None)
            user_id = getattr(runner, "_default_user_id", "cli-user")
            if svc is None or app_name is None:
                return ()
            session = await svc.get_session(
                app_name=app_name, user_id=user_id, session_id=session_id
            )
            state = getattr(session, "state", None)
            if state is None or not hasattr(state, "get"):
                return ()
            existing = state.get(SELECTED_RECIPE_PACK_IDS_STATE_KEY)
            if isinstance(existing, (tuple, list)):
                return tuple(str(item) for item in existing)
        except Exception:  # noqa: BLE001
            return ()
        return ()

    def _run_user_validators(
        self,
        *,
        required_validators: tuple[str, ...],
        observed_public_refs: set[str],
        session_id: str,
        turn_id: str,
        final_text: str,
    ) -> list[dict[str, object]]:
        """Execute user VALIDATOR pack impls over the produced artifact (PR2).

        Default OFF: returns ``[]`` and never imports the pack pipeline, so the
        caller's gate payload is byte-identical to before. When ON, for each
        required validator ref whose impl is loaded, build a ``ValidatorCtx`` over
        the produced artifact, call the impl, read its verdict, and:

        * a PASSING verdict adds the ref to ``observed_public_refs`` (in place) so
          ``required_validators`` is satisfied for that ref;
        * a FAILING verdict leaves the ref missing (the caller blocks) and the
          detail is returned for the bus payload.

        Fail-closed: an impl that raises is treated as a failing verdict with an
        error detail, so a broken user validator blocks rather than silently
        passing. Returns the list of verdict dicts (``ref``/``passed``/``detail``)
        for surfacing on ``verifierBus``.

        Delegates to ``engine_user_packs.run_user_validators`` (pure move,
        PR-G1); the method is retained so the pack characterization suites keep
        exercising the gate through a driver instance.
        """
        return run_user_validators(
            required_validators=required_validators,
            observed_public_refs=observed_public_refs,
            session_id=session_id,
            turn_id=turn_id,
            final_text=final_text,
        )

    def _run_user_evidence_producers(
        self,
        *,
        required_evidence: tuple[str, ...],
        observed_public_refs: set[str],
        session_id: str,
        turn_id: str,
    ) -> list[dict[str, object]]:
        """Run user EVIDENCE_PRODUCER pack runtime emitters at the gate (PR3).

        Default OFF: returns ``[]`` and never imports the pack pipeline, so the
        caller's gate payload is byte-identical to before. When ON, for each
        required evidence ref that a loaded USER producer provides (keyed by its
        ``ProducerSpec.public_ref``), build an ``EvidenceProducerCtx`` over the
        live session, call the pack's optional ``emit_evidence`` runtime emitter,
        and for every record it emits whose ``evidence_type`` matches the spec,
        add the spec's ``public_ref`` to ``observed_public_refs`` (in place) so
        ``required_evidence`` is satisfied for that ref.

        Fail-safe: a producer whose emitter raises (or that ships no emitter, or
        emits nothing) leaves the ref unobserved (the caller blocks) and never
        crashes the turn. Returns the list of emitted-record dicts
        (``ref``/``evidenceType``/``payload``) for surfacing on ``verifierBus``.

        Delegates to ``engine_user_packs.run_user_evidence_producers`` (pure
        move, PR-G1); the method is retained so the pack characterization suites
        keep exercising the gate through a driver instance.
        """
        return run_user_evidence_producers(
            required_evidence=required_evidence,
            observed_public_refs=observed_public_refs,
            session_id=session_id,
            turn_id=turn_id,
        )

    def _citation_only_gate_payload(
        self, overlay: dict[str, object] | None, turn_id: str
    ) -> dict[str, object] | None:
        """A citation-only pre-final block payload for turns with no coding gate.

        Returns None unless the overlay actually warrants a block, so a plain
        chat turn with no coding assembly and no citation violation stays a
        byte-identical no-op (the loop breaks and finalization emits the record).
        """
        if not (isinstance(overlay, Mapping) and overlay.get("shouldBlock")):
            return None
        max_attempts = int(overlay.get("maxAttempts", 2) or 2)
        continue_repair = bool(overlay.get("continueRepair"))
        payload: dict[str, object] = {
            "type": "pre_final_evidence_gate",
            "turnId": turn_id,
            "decision": "block",
            "matchedRefs": [],
            "missingEvidence": [],
            "missingValidators": [],
            "missingEvidenceAction": "repair_required",
            "repairPolicy": {
                "action": "repair_required",
                "maxAttempts": max_attempts,
                "source": "source-citation",
            },
            "attachmentFlags": {},
            "citationRepair": {
                "kind": overlay.get("kind"),
                "message": overlay.get("message"),
                "inducedSearch": bool(overlay.get("inducedSearch")),
                "maxAttempts": max_attempts,
                "failOpenNotice": overlay.get("failOpenNotice"),
            },
            "repairDecision": {
                "action": "continue_repair" if continue_repair else "abstain",
            },
            "verifierBus": _build_pre_final_verifier_bus_payload(
                decision="block", missing_evidence=[], missing_validators=[]
            ),
        }
        return payload

    def _overlay_citation_block(
        self,
        payload: dict[str, object],
        overlay: dict[str, object] | None,
        turn_id: str,
    ) -> dict[str, object]:
        """Overlay a citation block onto a coding gate payload that would PASS.

        Coding precedence: when the coding gate already blocks, citation defers
        (it re-evaluates on the next turn); it only overlays a citation repair
        onto an otherwise-passing coding decision. Byte-identical when the
        overlay does not warrant a block.
        """
        if not (isinstance(overlay, Mapping) and overlay.get("shouldBlock")):
            return payload
        if payload.get("decision") == "block":
            return payload
        citation_payload = self._citation_only_gate_payload(overlay, turn_id)
        if citation_payload is None:
            return payload
        # Preserve any refs the coding pass already matched for observability.
        citation_payload["matchedRefs"] = payload.get("matchedRefs", [])
        citation_payload["attachmentFlags"] = payload.get("attachmentFlags", {})
        return citation_payload

    def _pre_final_gate_payload(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        harness_state: object | None,
        observed_public_refs: set[str],
        coding_mutation_observed: bool = False,
        repair_attempt_count: int = 0,
        final_text: str = "",
        live_selected_pack_ids: tuple[str, ...] = (),
    ) -> dict[str, object] | None:
        citation_overlay = self._citation_repair_overlay(
            session_id=session_id,
            turn_id=turn_id,
            prompt=prompt,
            final_text=final_text,
            attempt_count=repair_attempt_count,
        )
        assembly = self._runner_policy_assembly
        if assembly is None:
            return self._citation_only_gate_payload(citation_overlay, turn_id)
        if not _pre_final_gate_applies(
            assembly=assembly,
            prompt=prompt,
            harness_state=harness_state,
            coding_mutation_observed=coding_mutation_observed,
            live_selected_pack_ids=live_selected_pack_ids,
        ):
            return self._citation_only_gate_payload(citation_overlay, turn_id)
        # Union live-selected recipe obligations into the baseline gate requirements.
        # Fail-open: any error resolving the registry keeps extra_* as () so the
        # effective sets equal the baseline (byte-identical OFF-path behavior).
        extra_validators: tuple[str, ...] = ()
        extra_evidence: tuple[str, ...] = ()
        if live_selected_pack_ids:
            try:
                from magi_agent.config.env import recipe_routing_llm_enabled  # noqa: PLC0415
                if recipe_routing_llm_enabled():
                    from magi_agent.recipes.kernel_recipe_packs import build_runtime_pack_registry  # noqa: PLC0415
                    from magi_agent.recipes import recipe_routing as _recipe_routing  # noqa: PLC0415
                    extra_validators, extra_evidence = _recipe_routing.build_recipe_obligation_scope(
                        build_runtime_pack_registry()
                    ).obligations_for(live_selected_pack_ids)
                    # Mutation-scope: dev-coding's test-evidence validator only
                    # has something to verify when code was actually mutated. On
                    # a no-mutation turn drop it so the (non-coding) baseline
                    # stays enforced without falsely requiring coding evidence.
                    if not coding_mutation_observed:
                        extra_validators = tuple(
                            ref
                            for ref in extra_validators
                            if ref != _recipe_routing._DEV_CODING_EVIDENCE_VALIDATOR
                        )
            except Exception:  # noqa: BLE001
                extra_validators, extra_evidence = (), ()
        # PR-D2: an active mode may force-require additional deterministic-ref
        # validators for this turn via its scoped_policy_ids (a policy that is
        # otherwise globally off). Resolved here at the universal per-turn
        # pre-final choke point so it applies regardless of how the assembly was
        # built. Flag-gated + empty when no mode is active ⇒ byte-identical.
        from magi_agent.customize.scoped_policy import (  # noqa: PLC0415
            scoped_prefinal_validator_refs,
        )

        _scoped_validator_refs = scoped_prefinal_validator_refs()
        effective_required_validators = tuple(
            dict.fromkeys(
                (*assembly.required_validators, *extra_validators, *_scoped_validator_refs)
            )
        )
        effective_required_evidence = tuple(
            dict.fromkeys((*assembly.evidence_requirements, *extra_evidence))
        )
        # PR2: run user-authored VALIDATOR impls over the produced artifact.
        # Default OFF (and a no-op when no required validator has a loaded impl),
        # so when OFF the gate payload is byte-identical to before: a required but
        # unobserved user validator ref still blocks (the pre-PR2 block-only
        # behavior). When ON, a passing verdict adds the ref to
        # observed_public_refs (satisfies required_validators); a failing verdict
        # leaves the ref missing (blocks) and its detail surfaces on the bus.
        user_validator_verdicts = self._run_user_validators(
            required_validators=effective_required_validators,
            observed_public_refs=observed_public_refs,
            session_id=session_id,
            turn_id=turn_id,
            final_text=final_text,
        )
        # PR3: run user-authored EVIDENCE_PRODUCER runtime emitters. Default OFF
        # (and a no-op when no required evidence ref has a loaded USER producer),
        # so when OFF the gate payload is byte-identical to before: a required but
        # unemitted user evidence ref still blocks. When ON, a producer's emitter
        # runs over the live session and each emitted record's public_ref is added
        # to observed_public_refs BEFORE the verifier bus is computed below, so it
        # is echoed into matchedRefs and satisfies required_evidence.
        user_evidence_records = self._run_user_evidence_producers(
            required_evidence=effective_required_evidence,
            observed_public_refs=observed_public_refs,
            session_id=session_id,
            turn_id=turn_id,
        )
        evidence_records: tuple[object, ...] = ()
        verifier_bus: dict[str, object] | None = None
        # Task C — OPTIONAL BLOCKING document-authoring coverage gate. Default OFF
        # and env-gated; when off the bus call is behavior-identical to before and
        # DocumentCoverage evidence stays audit-only. 14-PR3 (C11) makes the gate
        # 3-state: ``advisory`` still computes the failed-coverage count (for
        # false-block-rate telemetry) but the engine does not block on it; only
        # ``block`` flips the pre-final decision.
        document_coverage_mode = _resolve_document_coverage_mode_with_preset()
        document_coverage_gate_enabled = document_coverage_mode != "off"
        # Task 2.3 — OPTIONAL BLOCKING SHACL constraint gate (default-OFF).
        # Mirror of the document-coverage pattern: when OFF (shacl_enabled=False),
        # shacl_records=() and shacl_gate_enabled=False → the bus call is
        # byte-identical to before; existing callers/tests are unaffected.
        # Both flags are required (Finding 1 fix): MAGI_SHACL_VERIFIER_ENABLED AND
        # MAGI_CUSTOMIZE_VERIFICATION_ENABLED must both be ON before the store is read.
        # This mirrors apply_verification_overrides and runtime_gate.preset_enabled.
        shacl_enabled, _shacl_policy = _load_shacl_policy_if_enabled()
        failed_document_coverage = 0
        if self._evidence_collector is not None:
            from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus

            evidence_records = self._collect_evidence(turn_id)
            # Run enabled SHACL rules against the turn's evidence (belt-and-suspenders:
            # _run_shacl_rules_for_turn is itself fail-safe and never raises).
            import time as _time  # noqa: PLC0415  # only import once per turn block

            _shacl_observed_at = int(_time.time() * 1000)
            shacl_records = _run_shacl_rules_for_turn(
                _shacl_policy,
                evidence_records,
                enabled=shacl_enabled,
                observed_at=_shacl_observed_at,
            )
            shacl_gate_enabled = shacl_enabled and bool(shacl_records)
            from magi_agent.config.env import (  # noqa: PLC0415
                is_dashboard_pack_authoring_enabled,
            )

            verifier_bus = execute_pre_final_verifier_bus(
                required_evidence=effective_required_evidence,
                required_validators=effective_required_validators,
                observed_public_refs=tuple(sorted(observed_public_refs)),
                evidence_records=(*evidence_records, *shacl_records),
                document_coverage_gate_enabled=document_coverage_gate_enabled,
                shacl_gate_enabled=shacl_gate_enabled,
                dashboard_gate_enabled=is_dashboard_pack_authoring_enabled(),
            )
            matched_refs = verifier_bus.get("matchedRefs")
            if isinstance(matched_refs, list):
                observed_public_refs = {ref for ref in matched_refs if isinstance(ref, str)}
            raw_failed_coverage = verifier_bus.get("failedDocumentCoverage")
            if isinstance(raw_failed_coverage, int):
                failed_document_coverage = raw_failed_coverage
            self._record_verifier_verdicts(
                session_id=session_id,
                turn_id=turn_id,
                verifier_bus=verifier_bus,
            )
        observed_public_refs.update(
            self._ga_deliverable_matched_requirement_labels(evidence_records)
        )
        observed_public_refs.update(
            self._fact_grounding_matched_requirement_labels(
                final_text=final_text,
                evidence_records=evidence_records,
            )
        )
        observed_public_refs.update(
            self._source_ledger_matched_requirement_refs(evidence_records)
        )
        observed_public_refs.update(
            self._hard_redaction_matched_requirement_labels(final_text=final_text)
        )
        observed_public_refs.update(
            self._evidence_pack_matched_requirement_labels(evidence_records)
        )
        missing_evidence = [
            ref for ref in effective_required_evidence if ref not in observed_public_refs
        ]
        missing_validators = [
            ref for ref in effective_required_validators if ref not in observed_public_refs
        ]
        # A4 — flag-gated GA deliverable completion gate. Promotes the Track 19
        # PR3 receipt-grounded check (an artifact deliverable receipt must
        # actually exist for the turn, not just a label match) onto this LIVE
        # pre-final seam. Default OFF ⇒ empty ⇒ payload byte-identical to main.
        missing_evidence.extend(
            self._ga_deliverable_missing_labels(evidence_records)
        )
        # C8 — flag/preset-gated taskboard-completion gate (reads the workspace
        # .magi/taskboard.jsonl). Default OFF ⇒ no file read ⇒ byte-identical.
        missing_evidence.extend(
            self._task_board_completion_block_labels()
        )
        # C6 — flag/preset-gated parallel-research source-count cross-check.
        # Default OFF / non-research turn ⇒ empty ⇒ payload byte-identical to main.
        missing_evidence.extend(
            self._parallel_research_missing_labels(evidence_records)
        )
        # C9 — flag/preset-gated response-language policy gate. Default OFF / no
        # policy configured ⇒ empty ⇒ payload byte-identical to main.
        missing_evidence.extend(
            self._response_language_block_labels(final_text=final_text)
        )
        document_coverage_blocks = _document_coverage_blocks(
            document_coverage_mode, failed_document_coverage
        )
        decision = (
            "block"
            if (missing_evidence or missing_validators or document_coverage_blocks)
            else "pass"
        )

        # D1: consume the phase route's verifier-escalation decision. When the
        # materialized route requires a bounded stronger verifier for a review
        # phase, an already-blocking gate upgrades its remediation from a weak
        # "audit" to "repair_required". This NEVER changes pass→block (it only
        # fires on a turn the gate already blocks), so the default-on behavior is
        # a safe routing/policy hint, not new authority.
        route_decision = assembly.phase_route_decision()
        effective_action = assembly.missing_evidence_action
        effective_repair_policy = dict(assembly.repair_policy)
        phase_route_escalation = (
            route_decision is not None
            and decision == "block"
            and bool(route_decision["requiresStrongerVerifier"])
            and effective_action != "repair_required"
        )
        if phase_route_escalation:
            effective_action = "repair_required"
            effective_repair_policy["action"] = "repair_required"
            effective_repair_policy["phaseRouteEscalation"] = True
            effective_repair_policy.setdefault("source", "phase-route-escalation")

        payload: dict[str, object] = {
            "type": "pre_final_evidence_gate",
            "turnId": turn_id,
            "decision": decision,
            "matchedRefs": sorted(observed_public_refs),
            "missingEvidence": missing_evidence,
            "missingValidators": missing_validators,
            "missingEvidenceAction": effective_action,
            "repairPolicy": effective_repair_policy,
            "attachmentFlags": dict(assembly.attachment_flags),
        }
        if route_decision is not None:
            payload["phaseRoute"] = {
                "routeDenied": route_decision["routeDenied"],
                "denialReason": route_decision["denialReason"],
                "requiresStrongerVerifier": route_decision["requiresStrongerVerifier"],
                "escalationPolicies": route_decision["escalationPolicies"],
                "deniedPhases": route_decision["deniedPhases"],
            }
        if phase_route_escalation:
            payload["phaseRouteEscalation"] = True
        if verifier_bus is None:
            verifier_bus = _build_pre_final_verifier_bus_payload(
                decision=decision,
                missing_evidence=missing_evidence,
                missing_validators=missing_validators,
            )
        else:
            verifier_bus["decision"] = decision
            verifier_bus["missingEvidence"] = missing_evidence
            verifier_bus["missingValidators"] = missing_validators
            verifier_bus["failedDocumentCoverage"] = failed_document_coverage
            verifier_bus.setdefault("evidenceRecordCount", len(evidence_records))
        # PR2: surface user validator verdicts (empty list when none ran, so the
        # OFF path keeps the bus payload byte-identical).
        if user_validator_verdicts:
            verifier_bus["userValidatorVerdicts"] = user_validator_verdicts
        # PR3: surface emitted user evidence records (empty list when none ran, so
        # the OFF path keeps the bus payload byte-identical).
        if user_evidence_records:
            verifier_bus["userEvidenceRecords"] = user_evidence_records
        payload["verifierBus"] = verifier_bus
        if decision == "block" and effective_action == "repair_required":
            latest_test_evidence = (
                _latest_coding_test_evidence(evidence_records)
                if _coding_repair_loop_enabled()
                else None
            )
            # Coding scope check (lab fix): the repair loop's "bounded repair
            # attempt N/M" preamble must NEVER reach the model on a non-coding
            # turn. The turn is coding only when dev-coding was actually engaged
            # (baseline or live) AND a file-mutating tool ran. Otherwise the
            # callee short-circuits to ``abstain`` so no repair preamble is
            # injected into the next turn's prompt.
            dev_coding_pack_id = "openmagi.dev-coding"
            effective_selected = set(assembly.selected_pack_ids) | set(
                live_selected_pack_ids
            )
            is_coding_turn = (
                dev_coding_pack_id in effective_selected
                and coding_mutation_observed
            )
            payload["repairDecision"] = _build_coding_repair_decision_payload(
                effective_repair_policy,
                attempt_count=repair_attempt_count,
                latest_test_evidence=latest_test_evidence,
                is_coding_turn=is_coding_turn,
            )
        # Overlay a citation repair only when the coding gate did NOT block
        # (coding precedence); byte-identical when citation warrants no block.
        payload = self._overlay_citation_block(payload, citation_overlay, turn_id)
        return payload

    def _ga_deliverable_missing_labels(
        self,
        evidence_records: Sequence[object],
    ) -> list[str]:
        """A4 — still-owed GA deliverable labels for the live pre-final gate.

        Behind strict default-OFF ``MAGI_GA_DELIVERABLE_GATE_ENABLED``. When ON
        and the assembled policy's evidence labels require an artifact
        deliverable (any label mentioning ``"artifact"``), the turn's collected
        evidence records — which include the GA receipt-ledger entries and,
        with the flag ON, the ``localArtifactReceipt`` projection emitted by
        the spreadsheet write tool — must contain an actual artifact ref.
        Missing ⇒ ``["ga_deliverable:artifactRef"]``, a blocked-reason the
        model can act on (produce the artifact and emit its receipt). Reuses
        the previously-dormant Track 19 PR3 verifier logic; no new policy is
        invented here.

        Gated by ``MAGI_GA_DELIVERABLE_GATE_ENABLED`` OR an enabled
        ``artifact-delivery`` Customize preset — the SAME activeness gate as the
        deliverable satisfier (``_ga_deliverable_matched_requirement_labels``),
        so toggling the preset wires BOTH halves of the seam: the satisfier (can
        clear the deliverable label) and this completion check (adds the owed
        ``ga_deliverable:`` reason). Both OFF ⇒ ``[]`` ⇒ byte-identical to main.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_ga_deliverable_gate_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_ga_deliverable_gate_enabled(os.environ)
            or preset_enabled("artifact-delivery", default=False)
        ):
            return []
        assembly = self._runner_policy_assembly
        if assembly is None:
            return []
        from magi_agent.harness.general_automation.task_completion import (  # noqa: PLC0415
            missing_deliverable_labels,
            required_deliverable_evidence_from_labels,
        )

        required = required_deliverable_evidence_from_labels(
            tuple(getattr(assembly, "evidence_requirements", ()) or ())
        )
        if required.is_empty():
            return []
        return [
            f"ga_deliverable:{label}"
            for label in missing_deliverable_labels(required, evidence_records)
        ]

    def _task_board_completion_block_labels(self) -> list[str]:
        """C8 — block completion while the taskboard has incomplete tasks.

        Behind strict default-OFF ``MAGI_VERIFY_TASKBOARD_COMPLETION`` OR an
        enabled ``task-board-completion`` Customize preset. When active, reads the
        workspace taskboard ledger ``<cwd>/.magi/taskboard.jsonl`` (where the
        ``TaskBoard`` native tool appends ``{action,title,status}`` records),
        folds by title to the latest status, and — if any title's latest status
        is NON-terminal — returns the actionable reason
        ``task_board:incomplete_tasks``.

        DELIBERATE bus-contract deviation (founder sign-off): unlike every other
        pre-final satisfier, this reads a workspace FILE rather than the collected
        evidence corpus, because the ``TaskBoard`` tool emits no evidence record
        and the per-item status lives only in the ledger. Scoped to the local
        CLI's cwd workspace and FAIL-OPEN in the safe direction: a missing /
        unreadable / empty ledger ⇒ ``[]`` (no block), so the worst case is
        under-enforcement, never a false block. Both gates off ⇒ no file read ⇒
        byte-identical to main.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_taskboard_completion_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_taskboard_completion_verification_enabled(os.environ)
            or preset_enabled("task-board-completion", default=False)
        ):
            return []
        try:
            import json  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415

            ledger = Path.cwd() / ".magi" / "taskboard.jsonl"
            if not ledger.is_file():
                return []
            latest_status: dict[str, str] = {}
            for line in ledger.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                title = record.get("title")
                status = record.get("status")
                if isinstance(title, str) and isinstance(status, str):
                    latest_status[title] = status.strip().lower()
            has_incomplete = any(
                status not in _TASKBOARD_TERMINAL_STATUSES
                for status in latest_status.values()
            )
            if has_incomplete:
                return ["task_board:incomplete_tasks"]
            return []
        except Exception:
            logger.debug("task-board completion check failed", exc_info=True)
            return []

    def _parallel_research_missing_labels(
        self,
        evidence_records: Sequence[object],
    ) -> list[str]:
        """C6 — block a research turn that synthesized from too few sources.

        Behind strict default-OFF ``MAGI_VERIFY_PARALLEL_RESEARCH`` OR an enabled
        ``parallel-research`` Customize preset. When active AND a research recipe
        pack is selected, the turn's inspected-source evidence records
        (``SourceInspection`` / ``WebSearch`` / ``KnowledgeSearch`` — the same
        types the source-ledger projector counts) are counted; fewer than
        ``_PARALLEL_RESEARCH_MIN_SOURCES`` yields the actionable block reason
        ``parallel_research:insufficient_sources``.

        Scoped to research packs ONLY (``selected_pack_ids`` ∩ the research packs)
        so a coding/chat turn that incidentally ran one search is never blocked —
        the count heuristic is not a research signal on its own. Both gates OFF,
        or a non-research turn ⇒ ``[]`` ⇒ byte-identical to main. Fail-open: any
        error returns ``[]`` so the check can only ever ADD a block on a genuine
        research turn, never wedge an unrelated one.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_parallel_research_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_parallel_research_verification_enabled(os.environ)
            or preset_enabled("parallel-research", default=False)
        ):
            return []
        assembly = self._runner_policy_assembly
        if assembly is None:
            return []
        try:
            selected = set(getattr(assembly, "selected_pack_ids", ()) or ())
            if not (selected & _RESEARCH_RECIPE_PACK_IDS):
                return []
            from magi_agent.evidence.final_output_gate import (  # noqa: PLC0415
                _SOURCE_EVIDENCE_TYPES,
            )

            source_count = 0
            for record in evidence_records:
                record_type = (
                    record.get("type")
                    if isinstance(record, Mapping)
                    else getattr(record, "type", None)
                )
                if isinstance(record_type, str) and record_type in _SOURCE_EVIDENCE_TYPES:
                    source_count += 1
            if source_count < _PARALLEL_RESEARCH_MIN_SOURCES:
                return ["parallel_research:insufficient_sources"]
            return []
        except Exception:
            logger.debug("parallel-research check failed", exc_info=True)
            return []

    def _response_language_block_labels(self, *, final_text: str) -> list[str]:
        """C9 — block a final answer that violates the configured language policy.

        Behind strict default-OFF ``MAGI_VERIFY_RESPONSE_LANGUAGE`` OR an enabled
        ``response-language`` Customize preset. Wires the previously-dormant
        ``discipline_boundary.response_language`` check (only ``harness/__init__``
        imported it; no live consumer) to the live pre-final gate: when active AND
        a policy is configured (``MAGI_RESPONSE_LANGUAGE``, e.g. ``"ko"``), the
        boundary verdict on ``final_text`` decides. A ``blocked`` verdict yields
        the actionable reason ``response_language:policy_violation``.

        No policy configured ⇒ ``[]`` (no fake toggle: enforces only an
        explicitly-set language). The boundary itself is diagnostic-only
        (authority pinned False); this engine gate holds the blocking authority.
        Both gates off / no policy ⇒ ``[]`` ⇒ byte-identical to main. Fail-open.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_response_language_verification_enabled,
            response_language_policy,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_response_language_verification_enabled(os.environ)
            or preset_enabled("response-language", default=False)
        ):
            return []
        policy = response_language_policy(os.environ)
        if not policy:
            return []
        try:
            from magi_agent.harness.discipline_boundary import (  # noqa: PLC0415
                DisciplineBoundary,
                DisciplineBoundaryConfig,
                DisciplineRequest,
            )

            boundary = DisciplineBoundary(DisciplineBoundaryConfig(enabled=True))
            decision = boundary.evaluate(
                DisciplineRequest(
                    requestId="response-language",
                    turnId="pre-final",
                    check="response_language",
                    outputText=final_text,
                    metadata={"expectedLanguage": policy},
                )
            )
            if decision.status == "blocked":
                return ["response_language:policy_violation"]
            return []
        except Exception:
            logger.debug("response-language check failed", exc_info=True)
            return []

    def _ga_deliverable_matched_requirement_labels(
        self,
        evidence_records: Sequence[object],
    ) -> list[str]:
        """Evidence-requirement labels satisfied by real GA deliverable refs.

        The pre-final bus treats ``artifact_delivery_ref`` as a policy label,
        not as a public ``evidence:`` ref. When the strict GA deliverable gate is
        enabled, real artifact delivery evidence satisfies that label directly;
        missing evidence still appends the actionable
        ``ga_deliverable:artifactRef`` reason below.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_ga_deliverable_gate_enabled,
        )

        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_ga_deliverable_gate_enabled(os.environ)
            or preset_enabled("artifact-delivery", default=False)
        ):
            return []
        assembly = self._runner_policy_assembly
        if assembly is None:
            return []
        labels = tuple(getattr(assembly, "evidence_requirements", ()) or ())
        if not labels:
            return []
        from magi_agent.harness.general_automation.task_completion import (  # noqa: PLC0415
            missing_deliverable_labels,
            required_deliverable_evidence_from_labels,
        )

        required = required_deliverable_evidence_from_labels(labels)
        if required.is_empty():
            return []
        if missing_deliverable_labels(required, evidence_records):
            return []
        return [label for label in labels if "artifact" in label]

    def _fact_grounding_matched_requirement_labels(
        self,
        *,
        final_text: str,
        evidence_records: Sequence[object],
    ) -> list[str]:
        """Required-validator labels satisfied by a GROUNDED final answer.

        Behind strict default-OFF ``MAGI_FACT_GROUNDING_VERIFICATION_ENABLED``.
        When OFF this returns ``[]`` so the gate is byte-identical to main: the
        bare ``fact_grounding`` required-validator behaves exactly as it does
        today. When ON, and the assembled policy actually carries that bare
        ``fact_grounding`` label, the turn's final answer is grounded against the
        collected evidence corpus with the deterministic
        ``evaluate_answer_grounding`` detector (via
        :class:`~magi_agent.evidence.claim_grounding.FactGroundingEvidenceProducer`):

        * grounded (value supported, or no specific value to ground — the G4
          boundary) ⇒ ``["fact_grounding"]`` ⇒ the requirement is satisfied and
          the gate does not block on it;
        * guess (a specific numeric/identifier value with no corroborating
          evidence) ⇒ ``[]`` ⇒ ``fact_grounding`` stays missing ⇒ the gate
          blocks.

        Only the ``fact_grounding`` label is ever satisfied here; an unrelated
        missing validator is untouched. Fail-open: any error grounds nothing
        (returns ``[]``) so the satisfier can never wedge a turn — it can only
        REMOVE a block, never add one.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_fact_grounding_verification_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_fact_grounding_verification_enabled(os.environ)
            or preset_enabled("fact-grounding", default=False)
        ):
            return []
        assembly = self._runner_policy_assembly
        if assembly is None:
            return []
        try:
            from magi_agent.evidence.claim_grounding import (  # noqa: PLC0415
                FACT_GROUNDING_REQUIREMENT_LABEL,
                FactGroundingEvidenceProducer,
            )

            if FACT_GROUNDING_REQUIREMENT_LABEL not in assembly.required_validators:
                return []
            return list(
                FactGroundingEvidenceProducer().satisfied_requirement_labels(
                    final_text=final_text,
                    evidence_records=evidence_records,
                )
            )
        except Exception:
            logger.debug("fact-grounding satisfier failed", exc_info=True)
            return []

    def _apply_soft_verification_consequence(
        self,
        *,
        turn_id: str,
        session_id: str,
        final_text: str,
        pre_final_gate: Mapping[str, object],
        live_selected_pack_ids: Sequence[str] = (),
    ) -> tuple[str, RuntimeEvent] | None:
        """Resolve a hard pre-final block into a SOFT appended notice, or
        ``None`` to fall through to the existing hard refuse.

        Design: WS6 deterministic-verification activation, PR6a + PR6b. ONE seam,
        TWO reason families behind their own strict default-OFF flags:

        * research-governance notice (PR6a,
          ``MAGI_RESEARCH_GOVERNANCE_SOFT_BLOCK_ENABLED``): a
          ``research_governance_notice`` status enriched with the richer research
          final-gate reason codes;
        * evidence hedge (PR6b, ``MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED``): an
          ``evidence_hedge_applied`` status carrying the fact-grounding verdict /
          full missing validator set.

        Fires only when at least one flag is ON, the assembled recipe opted into
        the research/contract evidence contract, and the gate decision is
        ``block`` with a NON-EMPTY missing validator/evidence set. The
        reason-discrimination is label-agnostic (it does NOT key on a specific
        label), because ``openmagi.research`` blocks on the satisfier-less
        ``citation_support`` whether or not ``fact_grounding`` is also unmet (the
        design 1a correction).

        Returns ``(notice_suffix_text, status_event)`` to yield (the caller emits
        the status event, then a single ``text_delta`` carrying the suffix, then
        ``Terminal.completed``), or ``None`` when no soft consequence applies.
        Fail-open: any internal fault returns ``None`` (existing hard behavior),
        never a wedge. When OFF (neither flag set) it returns ``None`` before any
        scope work, so the OFF path is byte-identical to today.
        """
        import os  # noqa: PLC0415

        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                parse_evidence_hedge_on_guess_enabled,
                parse_research_governance_soft_block_enabled,
            )

            research_on = parse_research_governance_soft_block_enabled(os.environ)
            hedge_on = parse_evidence_hedge_on_guess_enabled(os.environ)
            if not (research_on or hedge_on):
                return None
            assembly = self._runner_policy_assembly
            if assembly is None:
                return None
            if not _is_research_recipe_scope(assembly, live_selected_pack_ids):
                return None
            missing_validators = [
                str(ref)
                for ref in pre_final_gate.get("missingValidators") or []
                if isinstance(ref, str)
            ]
            missing_evidence = [
                str(ref)
                for ref in pre_final_gate.get("missingEvidence") or []
                if isinstance(ref, str)
            ]
            if not (missing_validators or missing_evidence):
                return None

            # Reason family 1: research-governance notice (PR6a). Preferred when
            # its flag is ON (it surfaces the richer research final-gate reason
            # codes for a research turn).
            if research_on:
                try:
                    built = self._build_research_governance_notice(
                        turn_id=turn_id,
                        session_id=session_id,
                        final_text=final_text,
                        missing_validators=missing_validators,
                    )
                except Exception:
                    # Independent fail-open per family: a research-notice fault must
                    # NOT deny the hedge family its attempt (fall through to family
                    # 2, not straight to the hard refuse).
                    logger.debug(
                        "research-governance soft notice failed; trying hedge family",
                        exc_info=True,
                    )
                    built = None
                if built is not None:
                    return built
            # Reason family 2: evidence hedge (PR6b).
            if hedge_on:
                built = self._build_evidence_hedge_notice(
                    turn_id=turn_id,
                    final_text=final_text,
                    missing_validators=missing_validators,
                )
                if built is not None:
                    return built
            return None
        except Exception:
            logger.debug(
                "soft verification consequence failed; falling through to hard branch",
                exc_info=True,
            )
            return None

    def _build_research_governance_notice(
        self,
        *,
        turn_id: str,
        session_id: str,
        final_text: str,
        missing_validators: Sequence[str],
    ) -> tuple[str, RuntimeEvent] | None:
        """Build the PR6a research-governance soft notice ``(suffix, status)``.

        Evaluates the richer research final gate over NORMALIZED ids/refs to
        enrich the notice with deterministic reason codes. A construction
        ``ValueError`` would be a wiring bug (un-normalized input), not a runtime
        condition; the builder normalizes the live ``turn_id``/cited refs upstream
        (design 3.3 / 3.6) so a digit/hex ``turn_id`` or raw URL refs never raise
        into the seam's fail-open wrap.
        """
        from magi_agent.research.live_research_final_gate import (  # noqa: PLC0415
            evaluate_live_research_final_gate,
        )

        result = evaluate_live_research_final_gate(
            contract_id=_RESEARCH_SOFT_NOTICE_CONTRACT_ID,
            turn_id=turn_id,
            session_id=session_id,
            final_text=final_text,
            evidence_records=self._collect_evidence(turn_id),
        )
        reason_codes = list(result.reason_codes) if result.block_intent else []
        cited_without_source = list(result.output_link_digests)

        status_payload: dict[str, object] = {
            "type": "research_governance_notice",
            "turnId": turn_id,
            "mode": "local_block_intent",
            "reasonCodes": reason_codes,
            "missingValidators": list(missing_validators),
            "citedWithoutSource": cited_without_source,
            "noticeAppended": True,
        }
        status_event = RuntimeEvent(
            type="status", payload=status_payload, turn_id=turn_id
        )
        return (_RESEARCH_SOFT_NOTICE_TEXT, status_event)

    def _build_evidence_hedge_notice(
        self,
        *,
        turn_id: str,
        final_text: str,
        missing_validators: Sequence[str],
    ) -> tuple[str, RuntimeEvent] | None:
        """Build the PR6b evidence-hedge soft notice ``(suffix, status)``.

        When ``fact_grounding`` is in the unmet set, runs the deterministic
        ``FactGroundingEvidenceProducer`` to obtain the ``guess`` verdict and its
        ``extracted_value``. The extracted value is SANITIZED via
        ``_safe_public_ref`` (which RETURNS an audit-safe ``ref:<digest>`` for
        any unsafe input and never raises), so a path/secret marker is never
        emitted verbatim. ``_reject_private_text`` (which RAISES) is deliberately
        NOT called inline, so its ``ValueError`` can never propagate into the soft
        branch and degrade to the hard refuse (design 3.6 / MINOR-4). When the
        unmet set has no ``fact_grounding`` label (e.g. ``citation_support`` only)
        the verdict is ``None`` and ``extractedValue`` is omitted.
        """
        from magi_agent.evidence.claim_grounding import (  # noqa: PLC0415
            FACT_GROUNDING_REQUIREMENT_LABEL,
            FactGroundingEvidenceProducer,
        )
        from magi_agent.evidence.research_final_gate import (  # noqa: PLC0415
            _safe_public_ref,
        )

        verdict_value: str | None = None
        extracted_value: str | None = None
        if FACT_GROUNDING_REQUIREMENT_LABEL in set(missing_validators):
            verdict = FactGroundingEvidenceProducer().evaluate(
                final_text=final_text,
                evidence_records=self._collect_evidence(turn_id),
            )
            if not verdict.grounded:
                verdict_value = "guess"
                if verdict.extracted_value is not None:
                    # _safe_public_ref returns a digest for unsafe input and never
                    # raises -> never the raw secret, never a propagating error.
                    extracted_value = _safe_public_ref(str(verdict.extracted_value))

        requirement_labels = list(dict.fromkeys(missing_validators))
        status_payload: dict[str, object] = {
            "type": "evidence_hedge_applied",
            "turnId": turn_id,
            "mode": "local_block_intent",
            "verdict": verdict_value,
            "missingValidators": list(missing_validators),
            "requirementLabels": requirement_labels,
            "hedgeApplied": True,
        }
        if extracted_value is not None:
            status_payload["extractedValue"] = extracted_value
        status_event = RuntimeEvent(
            type="status", payload=status_payload, turn_id=turn_id
        )
        return (_EVIDENCE_HEDGE_NOTICE_TEXT, status_event)

    def _source_ledger_matched_requirement_refs(
        self,
        evidence_records: Sequence[object],
    ) -> list[str]:
        """Named public ref harvested from the live turn's inspected sources.

        Behind strict default-OFF ``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED``.
        When OFF this returns ``[]`` so the gate is byte-identical to main: today
        only ``sha256:`` source receipts reach ``_collect_public_refs``; the NAMED
        ref ``verifier:research-source-evidence`` is never emitted on the live
        path (only ``research/research_first_canary`` emits it), so a recipe
        requiring it always blocks. When ON, and the assembled policy actually
        requires that named ref, the turn's already-collected evidence records
        are scanned for at least one inspected source (a ``SourceInspection`` /
        ``WebSearch`` / ``KnowledgeSearch`` evidence record, the same source
        evidence types the recipe-layer ``final_output_gate`` keys off). If found,
        the named ref is returned and merged into ``observed_public_refs`` so the
        requirement is satisfied; a source-less turn yields ``[]`` and the gate
        blocks on the named ref.

        Only the ``verifier:research-source-evidence`` ref is ever satisfied here;
        an unrelated missing validator is untouched. Fail-open: any error matches
        nothing (returns ``[]``) so the projector can never wedge a turn — it can
        only REMOVE a block, never add one.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_source_ledger_evidence_gate_enabled,
        )

        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_source_ledger_evidence_gate_enabled(os.environ)
            or preset_enabled("source-authority", default=False)
        ):
            return []
        assembly = self._runner_policy_assembly
        if assembly is None:
            return []
        try:
            from magi_agent.evidence.final_output_gate import (  # noqa: PLC0415
                _SOURCE_EVIDENCE_TYPES,
            )

            # All source-read refs that ONE inspected-source evidence record
            # legitimately satisfies. Each is emitted ONLY when actually in the
            # assembled requirement set (same ``if ref not in required: skip``
            # guard pattern as the redaction satisfier), so this never invents a
            # requirement — it can only REMOVE a block on a turn that read >=1
            # inspected source.
            #   * ``verifier:research-source-evidence`` (validator) — the named
            #     source-evidence verifier the source-grounded / research recipes
            #     require.
            #   * ``evidence:inspected-source`` (evidence) — the inspected-source
            #     evidence requirement the same recipes carry; before this it had
            #     no live producer so the gate blocked even on a real read.
            #   * ``verifier:sourceOpened@1`` (validator) — the "at least one
            #     source opened" verifier; satisfied by the same single record
            #     when a recipe requires it.
            named_validator = "verifier:research-source-evidence"
            inspected_evidence = "evidence:inspected-source"
            source_opened = "verifier:sourceOpened@1"
            required_validators = tuple(
                getattr(assembly, "required_validators", ()) or ()
            )
            required_evidence = tuple(
                getattr(assembly, "evidence_requirements", ()) or ()
            )
            wants_validator = named_validator in required_validators
            wants_inspected = inspected_evidence in required_evidence
            wants_opened = source_opened in required_validators
            if not (wants_validator or wants_inspected or wants_opened):
                return []
            has_source_record = False
            for record in evidence_records:
                record_type = (
                    record.get("type")
                    if isinstance(record, Mapping)
                    else getattr(record, "type", None)
                )
                if isinstance(record_type, str) and record_type in _SOURCE_EVIDENCE_TYPES:
                    has_source_record = True
                    break
            if not has_source_record:
                return []
            matched: list[str] = []
            if wants_validator:
                matched.append(named_validator)
            if wants_inspected:
                matched.append(inspected_evidence)
            if wants_opened:
                matched.append(source_opened)
            return matched
        except Exception:
            logger.debug("source-ledger evidence projector failed", exc_info=True)
            return []

    def _hard_redaction_matched_requirement_labels(
        self,
        *,
        final_text: str,
    ) -> list[str]:
        """BARE hard validator/evidence labels satisfied on a clean turn.

        ``recipes/reliability_policy`` force-merges three BARE refs into every
        recipe's final-gate policy: the validators ``no_production_attachment``
        / ``public_redaction`` and the evidence label ``redaction_audit``. They
        carry no public-ref prefix and (before this satisfier) had no live
        producer, so the pre-final gate always blocked — even on a perfect
        non-coding turn. This satisfier makes them legitimately satisfiable.

        Behind the same strict default-OFF flag as the source-ledger projector
        (``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED``). When OFF this returns
        ``[]`` so the gate is byte-identical to main (the bare hard refs stay
        missing ⇒ block). Each label is only emitted when it is actually in the
        assembled requirement set, mirroring the source-ledger satisfier's
        ``if label not in required: return []`` guard.

        Semantics (founder sign-off):

        * ``no_production_attachment`` — emitted when the genuine config
          invariant holds (no production tool-host attachment). The invariant is
          enforced at config time (``parse_python_toolhost_attachment_env``
          raises if the production-attachment env is set; the model pins
          ``Literal[False]``). We read that accessor live; if it raises we emit
          nothing (block) rather than hardcoding the answer.
        * ``public_redaction`` — the turn's ``final_text`` is scanned for
          CREDENTIALS ONLY (API keys / tokens / JWTs / bearer values) reusing
          the existing credential detectors. No credential ⇒ emitted; a
          credential ⇒ NOT emitted ⇒ the gate BLOCKS. Block-only: the output is
          never rewritten/redacted here.
        * ``redaction_audit`` — emitted iff the redaction scan ran and found no
          credential (reuses the ``public_redaction`` result).

        Fail-open per label: any error emits nothing, so the satisfier can only
        REMOVE a block, never add one.

        Activeness gate: ``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED`` OR an enabled
        ``redaction`` Customize preset (opt-in seam).
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_source_ledger_evidence_gate_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_source_ledger_evidence_gate_enabled(os.environ)
            or preset_enabled("redaction", default=False)
        ):
            return []
        assembly = self._runner_policy_assembly
        if assembly is None:
            return []
        matched: list[str] = []
        required_validators = tuple(getattr(assembly, "required_validators", ()) or ())
        required_evidence = tuple(getattr(assembly, "evidence_requirements", ()) or ())

        # no_production_attachment — read the genuine config invariant. The
        # mandatory ``openmagi.context-safety`` pack ALSO requires the PREFIXED
        # alias ``validator:context-safety:no-production-attachment`` for the
        # SAME invariant (compiler.py:2014-2019); it is the same check, so emit
        # both under the single invariant read (each guarded on membership).
        no_prod_bare = "no_production_attachment"
        no_prod_prefixed = "validator:context-safety:no-production-attachment"
        wants_no_prod_bare = no_prod_bare in required_validators
        wants_no_prod_prefixed = no_prod_prefixed in required_validators
        if wants_no_prod_bare or wants_no_prod_prefixed:
            try:
                from magi_agent.config.env import (  # noqa: PLC0415
                    parse_python_toolhost_attachment_env,
                )

                toolhost = parse_python_toolhost_attachment_env(os.environ)
                if toolhost.production_attachment_enabled is False:
                    if wants_no_prod_bare:
                        matched.append(no_prod_bare)
                    if wants_no_prod_prefixed:
                        matched.append(no_prod_prefixed)
            except Exception:
                logger.debug(
                    "no-production-attachment invariant read failed", exc_info=True
                )

        # public_redaction / redaction_audit — credential-only scan of
        # final_text. The mandatory ``openmagi.context-safety`` pack carries
        # PREFIXED aliases for the SAME redaction check
        # (``validator:context-safety:public-redaction`` ==
        # ``public_redaction``; ``evidence:context-safety-redaction`` ==
        # ``redaction_audit``; compiler.py:2018-2024). It also requires the bare
        # ``no_raw_evidence_payload`` validator (reliability_policy.py:111),
        # whose honest deterministic condition is the SAME credential-clean
        # scan (a final answer carrying no raw credential material). All four
        # are emitted from the single scan, each guarded on membership, so no
        # new logic is introduced.
        bare_public_redaction = "public_redaction"
        prefixed_public_redaction = "validator:context-safety:public-redaction"
        no_raw_payload = "no_raw_evidence_payload"
        bare_redaction_audit = "redaction_audit"
        prefixed_redaction_audit = "evidence:context-safety-redaction"
        wants_bare_redaction = bare_public_redaction in required_validators
        wants_prefixed_redaction = prefixed_public_redaction in required_validators
        wants_no_raw_payload = no_raw_payload in required_validators
        wants_bare_audit = bare_redaction_audit in required_evidence
        wants_prefixed_audit = prefixed_redaction_audit in required_evidence
        if (
            wants_bare_redaction
            or wants_prefixed_redaction
            or wants_no_raw_payload
            or wants_bare_audit
            or wants_prefixed_audit
        ):
            try:
                if not self._final_text_contains_credential(final_text):
                    if wants_bare_redaction:
                        matched.append(bare_public_redaction)
                    if wants_prefixed_redaction:
                        matched.append(prefixed_public_redaction)
                    if wants_no_raw_payload:
                        matched.append(no_raw_payload)
                    if wants_bare_audit:
                        matched.append(bare_redaction_audit)
                    if wants_prefixed_audit:
                        matched.append(prefixed_redaction_audit)
            except Exception:
                logger.debug("public-redaction credential scan failed", exc_info=True)
        return matched

    def _evidence_pack_matched_requirement_labels(
        self,
        evidence_records: tuple[object, ...],
    ) -> list[str]:
        """Satisfiers for the mandatory ``openmagi.evidence`` pack's refs.

        ``openmagi.evidence`` is a hard-safety, non-opt-out pack
        (``compiler.py:2037-2042``), so its refs are ALWAYS required on the live
        gate yet had no live producer, blocking every turn. This emits them from
        EXISTING deterministic conditions only (no new enforcement logic), each
        guarded on membership in the assembled requirement set so OFF / absent
        requirements stay byte-identical:

        * ``runtime_evidence_record`` (bare) + ``evidence:runtime-issued-record``
          (prefixed) — the SAME "the runtime issued at least one evidence
          record this turn" attestation. Honest deterministic condition: the
          turn's already-collected ``evidence_records`` is non-empty (auto-
          attest from the real per-turn ledger the engine already passes in).
          No records ⇒ not emitted ⇒ the gate keeps blocking.
        * ``validator:evidence:no-block-mode`` — a structural attestation that
          the evidence verification subsystem runs in AUDIT mode, never
          block-mode. Read from the existing ``block_mode = Literal[False]``
          config invariant on ``CitationAuditResult`` (``citation_audit.py:88``);
          if that invariant ever flips to True the ref is NOT emitted.

        Activeness gate: ``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED`` OR an enabled
        ``evidence-pack`` Customize preset (opt-in seam). Fail-open per ref.
        """
        import os  # noqa: PLC0415

        from magi_agent.config.env import (  # noqa: PLC0415
            parse_source_ledger_evidence_gate_enabled,
        )
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if not (
            parse_source_ledger_evidence_gate_enabled(os.environ)
            or preset_enabled("evidence-pack", default=False)
        ):
            return []
        assembly = self._runner_policy_assembly
        if assembly is None:
            return []
        matched: list[str] = []
        required_validators = tuple(getattr(assembly, "required_validators", ()) or ())
        required_evidence = tuple(getattr(assembly, "evidence_requirements", ()) or ())

        # runtime_evidence_record / evidence:runtime-issued-record — emit when
        # the turn actually collected >=1 evidence record.
        bare_runtime = "runtime_evidence_record"
        prefixed_runtime = "evidence:runtime-issued-record"
        wants_bare_runtime = bare_runtime in required_evidence
        wants_prefixed_runtime = prefixed_runtime in required_evidence
        if (wants_bare_runtime or wants_prefixed_runtime) and len(evidence_records) >= 1:
            if wants_bare_runtime:
                matched.append(bare_runtime)
            if wants_prefixed_runtime:
                matched.append(prefixed_runtime)

        # validator:evidence:no-block-mode — structural audit-mode invariant.
        no_block_mode = "validator:evidence:no-block-mode"
        if no_block_mode in required_validators:
            try:
                from magi_agent.evidence.citation_audit import (  # noqa: PLC0415
                    CitationAuditResult,
                )

                block_mode_field = CitationAuditResult.model_fields["block_mode"]
                if block_mode_field.default is False:
                    matched.append(no_block_mode)
            except Exception:
                logger.debug("no-block-mode invariant read failed", exc_info=True)
        return matched

    @staticmethod
    def _final_text_contains_credential(final_text: str) -> bool:
        """True if ``final_text`` leaks a CREDENTIAL (key / token / JWT / bearer).

        Reuses the existing credential-value detectors only: ``_JWT_LIKE_RE``
        (``evidence/validator_taxonomy.py``) and the cloud/API-key + bearer
        regexes from ``evidence/ledger.py`` (``_GITHUB_TOKEN_RE``,
        ``_OPENAI_TOKEN_RE``, ``_STRIPE_TOKEN_RE``, ``_BEARER_TOKEN_RE``). These
        match actual secret MATERIAL — NOT bare filesystem paths (``/Users/``),
        emails, or the bare word ``token`` (those are explicitly out of scope to
        avoid false positives).
        """
        if not final_text:
            return False
        # B2/#1228: the token/bearer regexes moved to the single home
        # ``magi_agent.ops.safety`` (``evidence/ledger.py`` was rewired onto the
        # kernel and no longer re-exports the ``_BEARER_TOKEN_RE`` etc. private
        # names). Import them from the kernel directly — the patterns are
        # byte-identical to the pre-move ledger copies, so the credential scan is
        # unchanged. (Previously this import raised ``ImportError`` post-move and
        # was swallowed by the ``except`` below, silently dropping the
        # ``public_redaction`` / ``redaction_audit`` / ``no_raw_evidence_payload``
        # satisfiers so every clean turn failed the hard redaction ref.)
        from magi_agent.ops.safety import (  # noqa: PLC0415
            BEARER_TOKEN_RE as _BEARER_TOKEN_RE,
            GITHUB_TOKEN_RE as _GITHUB_TOKEN_RE,
            OPENAI_TOKEN_RE as _OPENAI_TOKEN_RE,
            STRIPE_TOKEN_RE as _STRIPE_TOKEN_RE,
        )
        from magi_agent.evidence.validator_taxonomy import _JWT_LIKE_RE  # noqa: PLC0415

        for pattern in (
            _GITHUB_TOKEN_RE,
            _OPENAI_TOKEN_RE,
            _STRIPE_TOKEN_RE,
            _BEARER_TOKEN_RE,
            _JWT_LIKE_RE,
        ):
            if pattern.search(final_text):
                return True
        return False

    @staticmethod
    def _collect_public_refs(value: object, refs: set[str]) -> None:
        if isinstance(value, str):
            if value.startswith(("evidence:", "verifier:", "receipt:sha256:", "sha256:")):
                refs.add(value)
            return
        if isinstance(value, Mapping):
            for nested in value.values():
                MagiEngineDriver._collect_public_refs(nested, refs)
            return
        if isinstance(value, list | tuple):
            for nested in value:
                MagiEngineDriver._collect_public_refs(nested, refs)

    # -- B9 no-tool finalizer -----------------------------------------------
    def _attach_deny_all_tools(self, *, runner: object) -> "_GateAttachment | None":
        """Prepend a deny-all ``before_tool_callback`` for the finalizer pass.

        Every tool call is SKIPPED and returns a blocked dict result (verified
        ADK contract: a dict return short-circuits the tool). This gives the
        hosted finalizer's ``tools: ()`` guarantee without rebuilding the agent
        (the driver owns a live runner, not agent kwargs). Prepended FIRST so it
        wins over any pre-existing callback; restore via ``_restore_gate_callback``
        in a finally. No-op (None) when the runner has no agent (agentless
        ``MockRunner`` tests stay green: the finalizer prompt alone governs them).
        """
        agent = getattr(runner, "agent", None)
        if agent is None:
            return None
        original = getattr(agent, "before_tool_callback", None)
        if original is None:
            original_as_list: list = []
        elif isinstance(original, list):
            original_as_list = list(original)
        else:
            original_as_list = [original]

        async def _deny_all(*, tool, args=None, tool_context=None):
            _ = (args, tool_context)
            return {
                "status": "blocked",
                "error": "no_tool_finalizer_pass",
                "tool": getattr(tool, "name", "tool"),
                "feedback": (
                    "This is a finalizer pass. Do not call tools; write the "
                    "final answer as text using the evidence already gathered."
                ),
            }

        agent.before_tool_callback = [_deny_all, *original_as_list]
        return _GateAttachment(agent=agent, original=original)

    async def _run_no_tool_finalizer_pass(
        self,
        *,
        adapter: object,
        bridge: object,
        sanitize: Callable[[dict], "dict | None"],
        runner: object,
        types: object,
        runner_turn_input_cls: object,
        session_id: str,
        turn_id: str,
        effective_harness_state: object,
        event_allowance: int,
        usage: dict,
        cancel: asyncio.Event,
    ) -> AsyncIterator["RuntimeEvent"]:
        """One bounded tool-less pass that forces a final answer (B9 backstop).

        Re-invokes the SAME runner (same ADK session, so it sees the just-run
        tool/function response events) with a finalizer user message, under a
        deny-all tool overlay. Streams the finalizer text live via the same
        projection pipeline as the main loop, so the UI renders it as a normal
        answer. Fail-open: any exception yields whatever streamed and never turns
        a completed-blank turn into an error. Exactly one pass (no back-edge);
        bounded by ``event_allowance``.
        """
        from magi_agent.runtime.no_tool_finalizer import (  # noqa: PLC0415
            build_no_tool_finalizer_message,
        )

        finalizer_input = runner_turn_input_cls(
            userId=self._user_id,
            sessionId=session_id,
            turnId=turn_id,
            invocationId=turn_id,
            newMessage=types.Content(  # type: ignore[attr-defined]
                role="user",
                parts=[types.Part(text=build_no_tool_finalizer_message())],  # type: ignore[attr-defined]
            ),
            harnessState=effective_harness_state,
        )
        # Attach + iterator creation live INSIDE the try so a synchronous raise
        # there (a) never propagates out of this pass (fail-open: a finalizer
        # error must not turn a completed-blank turn into an error) and (b) can
        # never leak the deny-all overlay into the next turn on the same
        # long-lived runner (the finally always restores it).
        deny_attach: "_GateAttachment | None" = None
        finalizer_iter: "AsyncIterator[object] | None" = None
        _fin_usage: dict[str, int] = {}
        _fin_events = 0
        try:
            deny_attach = self._attach_deny_all_tools(runner=runner)
            finalizer_iter = adapter.run_turn(finalizer_input).__aiter__()  # type: ignore[union-attr]
            while _fin_events < event_allowance:
                if cancel.is_set():
                    break
                _fstep = await self._next_adk_event(finalizer_iter, cancel)
                if _fstep is _CANCELLED or _fstep is _EXHAUSTED:
                    break
                _fadk_event = _fstep
                self._note_observed_invocation_id(_adk_invocation_id(_fadk_event))
                _fin_reading = _adk_usage_metadata(_fadk_event)
                if _fin_reading:
                    _fin_usage.update(_fin_reading)
                _fprojection = bridge.project_adk_event(_fadk_event, turn_id=turn_id)  # type: ignore[union-attr]
                for _fraw in _fprojection.agent_events:  # type: ignore[union-attr]
                    _fsafe = sanitize(dict(_fraw))  # type: ignore[operator]
                    if _fsafe is None:
                        continue
                    _fin_events += 1
                    self._observe_event(_fsafe, session_id, turn_id)
                    yield RuntimeEvent(
                        type=_map_event_kind(_fsafe.get("type")),
                        payload=_fsafe,
                        turn_id=turn_id,
                    )
        except Exception:  # noqa: BLE001 - fail-open: a finalizer error never breaks the turn
            logger.debug("no_tool_finalizer pass failed", exc_info=True)
        finally:
            if finalizer_iter is not None:
                await self._aclose_iter(finalizer_iter)
            self._restore_gate_callback(deny_attach)
            _fold_usage(usage, _fin_usage)

    # -- Permission gate wiring (Stream F) ----------------------------------
    def _attach_gate_callback(
        self,
        *,
        runner: object,
        gate: "PermissionGate | None",
        turn_id: str,
        cancel: asyncio.Event,
    ) -> "_GateAttachment | None":
        """Attach a gate ``before_tool_callback`` to the runner's agent.

        Returns a restoration handle (or None when nothing was attached). When
        ``gate`` is None, or the runner exposes no ``agent``, this is a no-op and
        behavior is identical to today (keeps the agentless ``MockRunner`` tests
        green).

        Composes WITHOUT clobbering: the gate callback is prepended (FIRST) to
        any pre-existing ``before_tool_callback`` so a deny short-circuits before
        other callbacks run. ADK normalizes a single callable / a list / None via
        ``canonical_before_tool_callbacks``; we mirror that normalization.
        """
        if gate is None:
            return None
        agent = getattr(runner, "agent", None)
        if agent is None:
            return None

        original = getattr(agent, "before_tool_callback", None)
        if original is None:
            original_as_list: list = []
        elif isinstance(original, list):
            original_as_list = list(original)
        else:
            original_as_list = [original]

        callback = self._build_gate_before_tool(
            gate=gate, turn_id=turn_id, cancel=cancel
        )
        agent.before_tool_callback = [callback, *original_as_list]
        return _GateAttachment(agent=agent, original=original)

    @staticmethod
    def _restore_gate_callback(attachment: "_GateAttachment | None") -> None:
        if attachment is None:
            return
        try:
            attachment.agent.before_tool_callback = attachment.original
        except Exception:  # noqa: BLE001 - best-effort restore
            pass

    def _attach_user_hook_bus(
        self,
        *,
        runner: object,
        session_id: str,
        turn_id: str,
    ) -> object | None:
        """Attach the user (settings.json) HookBus tool-callback bridge.

        No-op (returns ``None``) when ``_user_hook_bus`` is None (gate
        ``MAGI_USER_HOOKS_ENABLED`` OFF) or the runner has no ``agent`` — so the
        agentless ``MockRunner`` tests and the gate-OFF path are byte-identical.
        The bridge is appended AFTER the gate callback (conflict-matrix order).
        """
        bus = self._user_hook_bus
        if bus is None:
            return None
        from magi_agent.cli.hook_wiring import attach_hook_bus_tool_callbacks
        from magi_agent.hooks.context import HookContext

        hook_context = HookContext(
            bot_id=self._user_id,
            session_id=session_id,
            turn_id=turn_id,
        )
        return attach_hook_bus_tool_callbacks(
            runner=runner, bus=bus, hook_context=hook_context
        )

    @staticmethod
    def _restore_user_hook_bus(attachment: object | None) -> None:
        if attachment is None:
            return
        from magi_agent.cli.hook_wiring import restore_hook_bus_tool_callbacks

        restore_hook_bus_tool_callbacks(attachment)  # type: ignore[arg-type]

    def _attach_customize_rules(
        self, *, runner: object, session_id: str, turn_id: str
    ) -> object | None:
        """Attach the customize tool-boundary bridge (third agent-level layer).

        Fires the authored customize rules (prompt_injection / shell_command /
        shell_check / output_rewrite) at the live ADK tool boundary. Appended
        AFTER the gate and user-hook bridges so a gate deny short-circuits
        first. No-op when no customize slot is ON or the runner has no agent.
        """
        from magi_agent.cli.customize_tool_wiring import (
            attach_customize_tool_callbacks,
        )

        return attach_customize_tool_callbacks(
            runner=runner, session_id=session_id, turn_id=turn_id
        )

    @staticmethod
    def _restore_customize_rules(attachment: object | None) -> None:
        if attachment is None:
            return
        from magi_agent.cli.customize_tool_wiring import (
            restore_customize_tool_callbacks,
        )

        restore_customize_tool_callbacks(attachment)  # type: ignore[arg-type]

    @staticmethod
    def _build_gate_before_tool(
        *,
        gate: "PermissionGate",
        turn_id: str,
        cancel: asyncio.Event,
    ):
        """Build the async ADK ``before_tool_callback`` enforcing ``gate``.

        ADK contract (verified against the installed
        ``google/adk/flows/llm_flows/functions.py``): the callback is invoked as
        ``callback(tool=..., args=<mutable dict>, tool_context=...)``. Returning a
        dict SKIPS the tool and uses the dict as the tool result (DENY). Returning
        None lets the tool run. Mutating ``args`` in place rewrites the tool input
        (UPDATED_INPUT). The callback may be async.
        """
        seq = 0

        def _deny_result(tool_name: str, feedback: str | None) -> dict[str, object]:
            result: dict[str, object] = {
                "status": "blocked",
                "error": "permission_denied",
                "tool": tool_name,
            }
            if feedback is not None:
                result["feedback"] = feedback
            return result

        async def _gate_before_tool(*, tool, args, tool_context=None):
            nonlocal seq
            _ = tool_context
            tool_name = getattr(tool, "name", "tool")
            seq += 1
            req = ControlRequest(
                requestId=f"{turn_id}:{tool_name}:{seq}",
                turnId=turn_id,
                toolName=tool_name,
                arguments=dict(args),
                reason="tool_use",
            )
            decision = await gate.check(req)

            if decision.kind == "deny":
                if decision.interrupt:
                    cancel.set()
                return _deny_result(tool_name, decision.feedback)

            # allow.
            updated = decision.updated_input
            if isinstance(updated, dict):
                # Re-validate the rewrite BEFORE applying it: a sink that rewrites
                # an allowed call into a forbidden one must NOT escalate past the
                # rules engine. (Closes the allow-then-rewrite-to-forbidden gap.)
                rules = getattr(gate, "rules", None)
                if rules is not None:
                    seq += 1
                    req2 = ControlRequest(
                        requestId=f"{turn_id}:{tool_name}:{seq}",
                        turnId=turn_id,
                        toolName=tool_name,
                        arguments=dict(updated),
                        reason="tool_use",
                    )
                    if rules.evaluate(req2) == "deny":
                        return _deny_result(tool_name, decision.feedback)
                # Apply the rewrite IN PLACE so the tool receives the new args.
                args.clear()
                args.update(updated)

            return None  # tool runs (with original or rewritten args)

        return _gate_before_tool


def build_smart_approve_gate(
    *,
    provider_config: object = None,
    tool_registry: object = None,
    evidence_sink=None,
) -> "PermissionGate":
    """Build a ``RulesPermissionGate`` with the SmartApprove classifier wired in.

    This is the ONLY code path that activates the optional ``smartApprove``
    permission mode (parallel to goose's ``SmartApprove``). The caller is
    responsible for passing this gate into ``run_turn_stream(gate=...)`` when
    the mode is selected. The default mode leaves ``smart_approve=None``
    (OFF), so default behavior is byte-identical to today.

    Parameters
    ----------
    provider_config:
        Optional ``ProviderConfig`` — forwarded to ``ReadOnlyClassifier`` so it
        can build a real LiteLlm model when no ``model_factory`` is injected.
    tool_registry:
        Optional ``ToolRegistry`` — forwarded so the classifier can make
        manifest-first decisions without any LLM call for known tools.
    evidence_sink:
        Optional callable for evidence logging; forwarded to the classifier.
    """
    # Deferred imports keep this module import-clean (no ADK at module load).
    from magi_agent.cli.permissions import RulesPermissionGate  # noqa: PLC0415
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier  # noqa: PLC0415

    classifier = ReadOnlyClassifier(
        registry=tool_registry,  # type: ignore[arg-type]
        provider_config=provider_config,
        evidence_sink=evidence_sink,
    )
    return RulesPermissionGate(smart_approve=classifier)


__all__ = [
    "EngineRecoveryPolicy",
    "MagiEngineDriver",
    "RunnerPolicyAssembly",
    "build_engine_recovery_policy",
    "build_smart_approve_gate",
]
