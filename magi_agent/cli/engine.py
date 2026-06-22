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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from magi_agent.cli.contracts import ControlRequest, EngineResult, Terminal
from magi_agent.runtime.events import RuntimeEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from magi_agent.cli.contracts import PermissionGate
    from magi_agent.runtime.error_recovery import (
        RecoverableError,
        RecoveryAttemptState,
        RecoveryEngine,
    )
    from magi_agent.runtime.empty_response_recovery import (
        EmptyResponseRecoveryConfig,
    )
    from magi_agent.runtime.goal_nudge import GoalNudge
    from magi_agent.runtime.output_continuation import OutputContinuationConfig


@dataclass(frozen=True)
class EngineRecoveryPolicy:
    """Live retry policy for the run invocation (PR12 genuine recovery seam).

    Holds the EXISTING :class:`RecoveryEngine` (activation, not reimpl) plus the
    per-turn attempt budget. Passed to :class:`MagiEngineDriver`; ``None`` (the
    default) disables the retry wrapper entirely so the OFF path is unchanged.
    """

    engine: "RecoveryEngine"
    max_attempts: int = 3


@dataclass(frozen=True, init=False)
class RunnerPolicyAssembly:
    """Local OSS runner policy assembled from first-party recipes.

    This object is intentionally public-metadata shaped. It can be threaded into
    harness state and emitted as a runtime event, but it cannot grant production
    write authority.
    """

    model_provider: str
    model_label: str
    selected_pack_ids: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    required_validators: tuple[str, ...]
    missing_evidence_action: str
    repair_policy: Mapping[str, object]
    attachment_flags: Mapping[str, bool]
    task_profile: Mapping[str, object]
    phase_routing: Mapping[str, object]
    provider_intents: tuple[str, ...]
    tool_intents: tuple[str, ...]
    channel_intents: tuple[str, ...]
    artifact_intents: tuple[str, ...]
    scheduler_intents: tuple[str, ...]

    def __init__(
        self,
        *,
        modelProvider: str | None = None,
        model_provider: str | None = None,
        modelLabel: str | None = None,
        model_label: str | None = None,
        selectedPackIds: tuple[str, ...] | list[str] = (),
        selected_pack_ids: tuple[str, ...] | list[str] = (),
        evidenceRequirements: tuple[str, ...] | list[str] = (),
        evidence_requirements: tuple[str, ...] | list[str] = (),
        requiredValidators: tuple[str, ...] | list[str] = (),
        required_validators: tuple[str, ...] | list[str] = (),
        missingEvidenceAction: str | None = None,
        missing_evidence_action: str | None = None,
        repairPolicy: Mapping[str, object] | None = None,
        repair_policy: Mapping[str, object] | None = None,
        attachmentFlags: Mapping[str, bool] | None = None,
        attachment_flags: Mapping[str, bool] | None = None,
        taskProfile: Mapping[str, object] | None = None,
        task_profile: Mapping[str, object] | None = None,
        phaseRouting: Mapping[str, object] | None = None,
        phase_routing: Mapping[str, object] | None = None,
        providerIntents: tuple[str, ...] | list[str] = (),
        provider_intents: tuple[str, ...] | list[str] = (),
        toolIntents: tuple[str, ...] | list[str] = (),
        tool_intents: tuple[str, ...] | list[str] = (),
        channelIntents: tuple[str, ...] | list[str] = (),
        channel_intents: tuple[str, ...] | list[str] = (),
        artifactIntents: tuple[str, ...] | list[str] = (),
        artifact_intents: tuple[str, ...] | list[str] = (),
        schedulerIntents: tuple[str, ...] | list[str] = (),
        scheduler_intents: tuple[str, ...] | list[str] = (),
    ) -> None:
        object.__setattr__(
            self,
            "model_provider",
            _non_empty_str(model_provider or modelProvider, "local"),
        )
        object.__setattr__(
            self,
            "model_label",
            _non_empty_str(model_label or modelLabel, "local-stub"),
        )
        object.__setattr__(
            self,
            "selected_pack_ids",
            _str_tuple(selected_pack_ids or selectedPackIds),
        )
        object.__setattr__(
            self,
            "evidence_requirements",
            _str_tuple(evidence_requirements or evidenceRequirements),
        )
        object.__setattr__(
            self,
            "required_validators",
            _str_tuple(required_validators or requiredValidators),
        )
        object.__setattr__(
            self,
            "missing_evidence_action",
            _non_empty_str(missing_evidence_action or missingEvidenceAction, "audit"),
        )
        object.__setattr__(
            self,
            "repair_policy",
            dict(repair_policy or repairPolicy or {}),
        )
        object.__setattr__(
            self,
            "attachment_flags",
            _authority_safe_attachment_flags(attachment_flags or attachmentFlags or {}),
        )
        object.__setattr__(
            self,
            "task_profile",
            dict(task_profile or taskProfile or {}),
        )
        object.__setattr__(
            self,
            "phase_routing",
            dict(phase_routing or phaseRouting or {}),
        )
        object.__setattr__(
            self,
            "provider_intents",
            _str_tuple(provider_intents or providerIntents),
        )
        object.__setattr__(
            self,
            "tool_intents",
            _str_tuple(tool_intents or toolIntents),
        )
        object.__setattr__(
            self,
            "channel_intents",
            _str_tuple(channel_intents or channelIntents),
        )
        object.__setattr__(
            self,
            "artifact_intents",
            _str_tuple(artifact_intents or artifactIntents),
        )
        object.__setattr__(
            self,
            "scheduler_intents",
            _str_tuple(scheduler_intents or schedulerIntents),
        )

    def to_public_payload(self) -> dict[str, object]:
        return {
            "modelProvider": self.model_provider,
            "modelLabel": self.model_label,
            "selectedPackIds": list(self.selected_pack_ids),
            "evidenceRequirements": list(self.evidence_requirements),
            "requiredValidators": list(self.required_validators),
            "missingEvidenceAction": self.missing_evidence_action,
            "repairPolicy": dict(self.repair_policy),
            "attachmentFlags": dict(self.attachment_flags),
            "taskProfile": dict(self.task_profile),
            "phaseRouting": dict(self.phase_routing),
            "providerIntents": list(self.provider_intents),
            "toolIntents": list(self.tool_intents),
            "channelIntents": list(self.channel_intents),
            "artifactIntents": list(self.artifact_intents),
            "schedulerIntents": list(self.scheduler_intents),
        }

    def phase_route_decision(self) -> dict[str, object] | None:
        """Distill the materialized phase routing into a consumable decision.

        ``phase_routing`` is the raw ``PhaseRoutingPlan.model_dump(by_alias=True)``
        threaded in by the recipe materializer; until D1 nothing read it. This
        normalizes it into the routing *hints* the engine/CLI surfaces actually
        consume (per-phase model selection, escalation policy, denial state).
        Returns ``None`` when no routing was materialized, so the OFF / stub path
        stays byte-identical.
        """

        routing = self.phase_routing
        if not routing:
            return None

        phase_routes = _routing_field(routing, "phaseRoutes", "phase_routes") or {}
        phase_models: dict[str, dict[str, str]] = {}
        escalation_policies: dict[str, str] = {}
        verifier_tiers: dict[str, str] = {}
        denied_phases: list[str] = []
        requires_stronger_verifier = False
        if isinstance(phase_routes, Mapping):
            for phase, route in phase_routes.items():
                if not isinstance(route, Mapping):
                    continue
                phase_key = str(phase)
                provider = route.get("provider")
                model = route.get("model")
                tier = route.get("tier")
                if (
                    isinstance(provider, str)
                    and isinstance(model, str)
                    and isinstance(tier, str)
                ):
                    phase_models[phase_key] = {
                        "provider": provider,
                        "model": model,
                        "tier": tier,
                    }
                policy = _routing_field(route, "escalationPolicy", "escalation_policy")
                if isinstance(policy, str) and policy:
                    escalation_policies[phase_key] = policy
                    if policy == "bounded_stronger_verifier":
                        requires_stronger_verifier = True
                verifier_tier = _routing_field(route, "verifierTier", "verifier_tier")
                if isinstance(verifier_tier, str) and verifier_tier:
                    verifier_tiers[phase_key] = verifier_tier
                if bool(_routing_field(route, "routeDenied", "route_denied")):
                    denied_phases.append(phase_key)

        max_sota = _routing_field(routing, "maxSotaEscalations", "max_sota_escalations")
        denial_reason = _routing_field(routing, "denialReason", "denial_reason")
        return {
            "routeDenied": bool(_routing_field(routing, "routeDenied", "route_denied")),
            "denialReason": denial_reason if isinstance(denial_reason, str) else None,
            "fallbackToTypeScript": bool(
                _routing_field(routing, "fallbackToTypeScript", "fallback_to_typescript")
            ),
            "maxSotaEscalations": (
                int(max_sota) if isinstance(max_sota, int) and not isinstance(max_sota, bool) else 0
            ),
            "phaseModels": phase_models,
            "escalationPolicies": escalation_policies,
            "verifierTiers": verifier_tiers,
            "deniedPhases": tuple(denied_phases),
            "requiresStrongerVerifier": requires_stronger_verifier,
        }


def build_engine_recovery_policy(env: object = None) -> "EngineRecoveryPolicy | None":
    """Build the recovery policy from env, or ``None`` when recovery is OFF.

    Reuses ``MAGI_ERROR_RECOVERY_ENABLED`` / ``MAGI_MAX_RECOVERY_ATTEMPTS`` (the
    single source of truth in ``config.env``) and the existing default
    ``RecoveryEngine``. Imports are deferred so ``import cli.engine`` stays
    cold-clean (no error_recovery import at module top is required, but these
    are pure-python anyway).
    """

    import os

    from magi_agent.config.env import parse_error_recovery_env
    from magi_agent.runtime.error_recovery import ErrorRecoveryConfig, RecoveryEngine

    mapping = env if isinstance(env, dict) else os.environ
    parsed = parse_error_recovery_env(mapping)
    if not parsed.enabled:
        return None
    config = ErrorRecoveryConfig(
        recovery_enabled=True,
        max_recovery_attempts=parsed.max_recovery_attempts,
    )
    return EngineRecoveryPolicy(
        engine=RecoveryEngine(config),
        max_attempts=parsed.max_recovery_attempts,
    )


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


def build_output_continuation_config(
    env: object = None,
) -> "OutputContinuationConfig | None":
    """Build the output-continuation config from env, or ``None`` when OFF.

    Reuses ``MAGI_OUTPUT_CONTINUATION_ENABLED`` / ``MAGI_MAX_OUTPUT_CONTINUATIONS``
    (single source of truth in ``config.env``). ``None`` leaves streaming
    byte-for-byte identical to the pre-continuation path.
    """

    import os

    from magi_agent.config.env import parse_output_continuation_env
    from magi_agent.runtime.output_continuation import OutputContinuationConfig

    mapping = env if isinstance(env, dict) else os.environ
    parsed = parse_output_continuation_env(mapping)
    if not parsed.enabled:
        return None
    return OutputContinuationConfig(
        enabled=True,
        max_continuations=parsed.max_continuations,
    )


def build_empty_response_recovery_config(
    env: object = None,
) -> "EmptyResponseRecoveryConfig | None":
    """Build the empty-response recovery config from env, or ``None`` when OFF.

    Reuses ``MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED`` /
    ``MAGI_EMPTY_RESPONSE_MAX_RECOVERIES`` (single source of truth in
    ``config.env``; strict truthy opt-in, default OFF). ``None`` leaves
    streaming byte-for-byte identical to the pre-recovery path.
    """

    import os

    from magi_agent.config.env import parse_empty_response_recovery_env
    from magi_agent.runtime.empty_response_recovery import (
        EmptyResponseRecoveryConfig,
    )

    mapping = env if isinstance(env, dict) else os.environ
    parsed = parse_empty_response_recovery_env(mapping)
    if not parsed.enabled:
        return None
    return EmptyResponseRecoveryConfig(
        enabled=True,
        max_recoveries=parsed.max_recoveries,
    )


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

# Map a projected public-event dict's "type" -> RuntimeEvent EventKind. Anything
# not listed defaults to "status".
_TOKEN_EVENT_TYPES = frozenset({"text_delta"})
_TOOL_EVENT_TYPES = frozenset({"tool_start", "tool_progress", "tool_end"})
_CONTROL_EVENT_TYPES = frozenset(
    {"control_event", "control_request", "control_replay_complete"}
)
_ARTIFACT_EVENT_TYPES = frozenset(
    {"source_inspected", "document_draft", "research_artifact_delta", "patch_preview"}
)
_ERROR_EVENT_TYPES = frozenset({"error"})

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


def should_reprompt_for_zero_edits(
    *, file_edits: int, already_reprompted: bool, enabled: bool
) -> bool:
    """Return True iff the zero-edit guard should fire a re-invocation.

    Pure helper — no side effects, fully unit-testable without driving the
    engine. The engine calls this after the main run loop concludes and before
    yielding the terminal EngineResult.

    Args:
        file_edits: number of file-mutating tool calls observed this turn.
        already_reprompted: True if we already fired the guard once this turn
            (prevents infinite re-invocation).
        enabled: value of ``parse_eval_zero_edit_guard_enabled(os.environ)``.
    """
    return bool(enabled and not already_reprompted and file_edits == 0)


def _is_turn_end_event(event: Mapping[str, object]) -> bool:
    return event.get("type") == "turn_end"


def _is_continuation_output_event(event: Mapping[str, object]) -> bool:
    event_type = event.get("type")
    if event_type in _TOKEN_EVENT_TYPES:
        return bool(event.get("delta"))
    if event_type in _TOOL_EVENT_TYPES:
        return True
    if event_type in _ARTIFACT_EVENT_TYPES:
        return True
    return False


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
    if event_type in _TOKEN_EVENT_TYPES:
        return "token"
    if event_type in _TOOL_EVENT_TYPES:
        return "tool"
    if event_type in _CONTROL_EVENT_TYPES:
        return "control"
    if event_type in _ARTIFACT_EVENT_TYPES:
        return "artifact"
    if event_type in _ERROR_EVENT_TYPES:
        return "error"
    return "status"


_CODING_TASK_TYPES = frozenset(
    {
        "coding",
        "code",
        "dev-coding",
        "developer",
        "software",
        "workspace",
        "file-edit",
        "patch",
    }
)
_NON_CODING_TASK_TYPES = frozenset(
    {
        "chat",
        "general",
        "conversation",
        "research",
        "readonly",
        "read-only",
        "planning",
        "plan",
    }
)
_CODING_PROMPT_MARKERS = frozenset(
    {
        "apply_patch",
        "bash",
        "bug",
        "build",
        "code",
        "commit",
        "compile",
        "debug",
        "diff",
        "edit",
        "file",
        "fix",
        "grep",
        "implement",
        "lint",
        "patch",
        "pytest",
        "refactor",
        "repo",
        "script",
        "test",
        "typescript",
        "코드",
        "파일",
        "테스트",
        "수정",
        "고쳐",
        "구현",
        "패치",
        "버그",
        "리팩터",
        "커밋",
    }
)


def _pre_final_gate_applies(
    *,
    assembly: RunnerPolicyAssembly,
    prompt: str,
    harness_state: object | None,
    coding_mutation_observed: bool,
    live_selected_pack_ids: Sequence[str] = (),
) -> bool:
    """Return whether the assembled policy should enforce the final gate.

    The local runner may assemble the dev-coding pack as an available first-party
    policy, but availability is not the same thing as routing every turn through
    a coding verification gate.  The dev-coding verification gate exists to
    confirm that *code mutations* were tested/grounded — so it only has something
    to enforce on a turn that actually mutated a file. A read-only / research
    turn (no file-mutating tool call) produces nothing to verify and must not be
    blocked, even when the prompt classifier would otherwise flag it as coding.
    """

    dev_coding_pack_id = "openmagi.dev-coding"
    base_selected = set(assembly.selected_pack_ids)
    selected = base_selected | set(live_selected_pack_ids)
    if dev_coding_pack_id not in selected:
        return True

    # Mutation-scope: the coding evidence gate only applies to turns that
    # actually changed code. No file mutation ⇒ nothing coding to verify.
    if not coding_mutation_observed:
        # If dev-coding is part of the PROFILE baseline, preserve main's exact
        # behavior: a no-mutation turn produces nothing to verify ⇒ no gate.
        # (When live_selected_pack_ids is empty this branch is always taken,
        # so the OFF-path stays byte-identical to main.)
        if dev_coding_pack_id in base_selected:
            return False
        # dev-coding arrived PURELY via live selection. Its (mutation-scoped)
        # coding obligation has nothing to verify on a no-mutation turn, but we
        # must NOT suppress the gate — that would drop the non-coding profile
        # baseline obligations. Defer to the baseline's own applies-decision
        # (i.e. what the gate would decide without the live dev-coding pack).
        return _pre_final_gate_applies(
            assembly=assembly,
            prompt=prompt,
            harness_state=harness_state,
            coding_mutation_observed=coding_mutation_observed,
            live_selected_pack_ids=(),
        )

    task_types = _extract_task_types(harness_state)
    if task_types:
        normalized = {_normalize_task_type(item) for item in task_types}
        if normalized & _CODING_TASK_TYPES:
            return True
        if normalized & _NON_CODING_TASK_TYPES:
            return False

    normalized_prompt = prompt.lower()
    return any(marker in normalized_prompt for marker in _CODING_PROMPT_MARKERS)


def _build_coding_repair_decision_payload(
    repair_policy: Mapping[str, object],
    *,
    attempt_count: int = 0,
    latest_test_evidence: Mapping[str, object] | None = None,
    is_coding_turn: bool = True,
) -> dict[str, object]:
    from magi_agent.coding.repair_loop import (
        CodingRepairLoopConfig,
        CodingRepairLoopState,
        evaluate_repair_decision,
        project_repair_decision_event,
        repair_max_attempts,
    )

    max_attempts = repair_max_attempts(repair_policy)
    decision = evaluate_repair_decision(
        config=CodingRepairLoopConfig(enabled=True, maxAttempts=max_attempts),
        state=CodingRepairLoopState(attemptCount=attempt_count),
        latest_test_evidence=latest_test_evidence,
        is_coding_turn=is_coding_turn,
    )
    return project_repair_decision_event(decision)


def _latest_coding_test_evidence(
    evidence_records: Sequence[object],
) -> Mapping[str, object] | None:
    latest: Mapping[str, object] | None = None
    latest_key: tuple[float, int] | None = None
    for index, record in enumerate(evidence_records):
        evidence = _evidence_mapping(record)
        if evidence is None or not _is_coding_test_evidence(evidence):
            continue
        key = (_evidence_observed_at(evidence), index)
        if latest_key is None or key > latest_key:
            latest = evidence
            latest_key = key
    return latest


def _evidence_mapping(record: object) -> Mapping[str, object] | None:
    if isinstance(record, Mapping):
        return record
    model_dump = getattr(record, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=True, mode="python", warnings=False)
        except TypeError:
            dumped = model_dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _is_coding_test_evidence(evidence: Mapping[str, object]) -> bool:
    haystack = " ".join(
        _string_values(
            evidence,
            (
                "type",
                "evidenceType",
                "evidence_type",
                "kind",
                "evidenceRef",
                "evidence_ref",
                "validatorRef",
                "validator_ref",
                "verifierId",
                "verifier_id",
                "id",
            ),
        )
    ).lower()
    return any(
        marker in haystack
        for marker in (
            "testrun",
            "test_run",
            "test-run",
            "test evidence",
            "test-evidence",
            "dev-coding:test-evidence",
        )
    )


def _string_values(source: Mapping[str, object], keys: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _evidence_observed_at(evidence: Mapping[str, object]) -> float:
    raw = evidence.get("observedAt", evidence.get("observed_at", 0.0))
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _coding_repair_loop_enabled() -> bool:
    from magi_agent.coding.repair_loop import coding_repair_loop_enabled

    return coding_repair_loop_enabled()


def _document_coverage_blocks(mode: str, failed_count: int) -> bool:
    """Whether failed document coverage should flip the pre-final decision.

    14-PR3 (C11): the gate is 3-state (``off`` | ``advisory`` | ``block``). Only
    ``block`` mode lets a failed-coverage count contribute to a ``"block"``
    decision; ``advisory`` records the count for telemetry but never blocks, and
    ``off`` is inert.
    """
    return mode == "block" and failed_count > 0


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


def _resolve_document_coverage_mode_with_preset() -> str:
    """Resolve the document-coverage gate mode, honoring the Customize opt-in seam.

    The base mode comes from ``MAGI_DOCUMENT_AUTHORING_COVERAGE``
    (``off``|``advisory``|``block``). An enabled ``document-authoring-coverage``
    Customize preset promotes an otherwise-``off`` gate to ``block`` for the
    runtime — the same opt-in pattern (env OR preset) as the other satisfier
    seams. Byte-identical when the preset is unset/disabled: the env-resolved
    mode is returned unchanged.
    """
    from magi_agent.config.env import (  # noqa: PLC0415
        resolve_document_authoring_coverage_mode,
    )

    mode = resolve_document_authoring_coverage_mode()
    if mode == "off":
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if preset_enabled("document-authoring-coverage", default=False):
            return "block"
    return mode


def _run_shacl_rules_for_turn(
    policy: object,
    evidence_records: "Sequence[object]",
    *,
    enabled: bool,
    observed_at: int,
) -> tuple[object, ...]:
    """Run enabled SHACL rules against turn evidence and return constraint-check records.

    Pure module-level helper for testability (mirrors the document-coverage
    pattern). Returns a tuple of ``EvidenceRecord`` objects — one per enabled
    ``shacl_constraint`` rule in ``policy``.

    Returns ``()`` when:
    - ``enabled`` is ``False`` (flag OFF → byte-identical to before),
    - ``policy`` is ``None`` (no policy set / MAGI_CUSTOMIZE_VERIFICATION_ENABLED OFF),
    - ``policy`` has no enabled shacl rules.

    Never raises: any per-rule exception is caught and skipped so a bad rule
    cannot break a turn. The ``run_shacl_rule`` producer is itself fail-safe
    (returns ``status="unknown"`` on any internal error), so the belt-and-suspenders
    guard here only catches unexpected attribute / type errors on policy access.
    """
    if not enabled:
        return ()
    if policy is None:
        return ()
    try:
        rules = policy.enabled_shacl_rules()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return ()
    if not rules:
        return ()
    from magi_agent.evidence.shacl_verifier import run_shacl_rule  # noqa: PLC0415

    results: list[object] = []
    for rule in rules:
        try:
            shape_ttl = rule.get("shapeTtl") if isinstance(rule, dict) else None
            rule_id = rule.get("ruleId") if isinstance(rule, dict) else None
            if not shape_ttl or not rule_id:
                continue
            record = run_shacl_rule(
                evidence_records,
                shape_ttl,
                rule_id,
                observed_at=observed_at,
            )
            results.append(record)
        except Exception:  # noqa: BLE001
            continue
    return tuple(results)


def _load_shacl_policy_if_enabled() -> tuple[bool, object]:
    """Resolve the SHACL gate state and load the customize policy when enabled.

    Returns ``(shacl_enabled, policy)`` where:
    - ``shacl_enabled`` is ``True`` only when **both** flags are ON:
      ``MAGI_SHACL_VERIFIER_ENABLED`` (``flag_bool``) AND
      ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` (``flag_profile_bool``).
    - ``policy`` is a ``CustomizeVerificationPolicy`` loaded from the store
      when ``shacl_enabled`` is ``True``, otherwise ``None``.

    Mirrors the precedent in ``magi_agent/customize/apply.py``
    (``apply_verification_overrides``) and ``magi_agent/customize/runtime_gate.py``
    (``preset_enabled``): **both** gate on ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED``
    before reading the store.

    Never raises: any exception → returns ``(False, None)`` (fail-safe).
    """
    try:
        from magi_agent.config.flags import flag_bool as _flag_bool  # noqa: PLC0415
        from magi_agent.config.flags import flag_profile_bool as _flag_profile_bool  # noqa: PLC0415

        shacl_enabled: bool = _flag_bool("MAGI_SHACL_VERIFIER_ENABLED") and _flag_profile_bool(
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED"
        )
        if shacl_enabled:
            from magi_agent.customize.store import load_overrides as _load_overrides  # noqa: PLC0415
            from magi_agent.customize.verification_policy import (  # noqa: PLC0415
                CustomizeVerificationPolicy as _CVP,
            )

            return True, _CVP.from_overrides(_load_overrides())
        return False, None
    except Exception:  # noqa: BLE001
        return False, None


def _coding_repair_max_attempts(repair_policy: Mapping[str, object]) -> int:
    from magi_agent.coding.repair_loop import repair_max_attempts

    return repair_max_attempts(repair_policy)


def _build_repair_continuation_message(
    *,
    missing_evidence: Sequence[str],
    missing_validators: Sequence[str],
    attempt: int,
    max_attempts: int,
) -> str:
    from magi_agent.coding.repair_loop import build_repair_continuation_message

    return build_repair_continuation_message(
        missing_evidence=tuple(missing_evidence),
        missing_validators=tuple(missing_validators),
        attempt=attempt,
        max_attempts=max_attempts,
    )


def _build_pre_final_verifier_bus_payload(
    *,
    decision: str,
    missing_evidence: list[str],
    missing_validators: list[str],
) -> dict[str, object]:
    """Project the live pre-final gate into public verifier-bus metadata."""

    from magi_agent.harness.verifier_bus import VerifierResultMetadata

    results: list[dict[str, object]] = []
    if missing_evidence:
        results.append(
            VerifierResultMetadata(
                verifierId="tool-evidence-contract",
                status="missing",
                publicSummary="missing required deterministic evidence",
                retryMessage="collect required evidence before final answer",
            ).model_dump(by_alias=True, mode="json", warnings=False)
        )
    if missing_validators:
        results.append(
            VerifierResultMetadata(
                verifierId="dev-coding-verification-audit",
                status="missing",
                publicSummary="missing required validator evidence",
                retryMessage="run required validation before final answer",
            ).model_dump(by_alias=True, mode="json", warnings=False)
        )
    if not results:
        results.append(
            VerifierResultMetadata(
                verifierId="pre-final-evidence-gate",
                status="pass" if decision == "pass" else "audit",
                publicSummary="pre-final evidence gate passed",
            ).model_dump(by_alias=True, mode="json", warnings=False)
        )
    return {
        "metadataOnly": True,
        "decision": decision,
        "results": results,
        "trafficAttached": False,
        "executionAttached": False,
        "failedDocumentCoverage": 0,
    }


def _extract_task_types(harness_state: object | None) -> tuple[str, ...]:
    if not isinstance(harness_state, Mapping):
        return ()
    profile = harness_state.get("taskProfile") or harness_state.get("task_profile")
    if not isinstance(profile, Mapping):
        return ()
    direct = profile.get("taskType") or profile.get("task_type")
    multi = profile.get("taskTypes") or profile.get("task_types")
    values: list[str] = []
    if isinstance(direct, str):
        values.append(direct)
    if isinstance(multi, str):
        values.append(multi)
    elif isinstance(multi, list | tuple):
        values.extend(item for item in multi if isinstance(item, str))
    return tuple(values)


def _normalize_task_type(value: str) -> str:
    return value.strip().lower().replace("_", "-")


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
    ) -> None:
        self._runner = runner
        # Optional wire profile for the HOSTED path (T4). ``None`` (default) keeps
        # the CLI path byte-for-byte unchanged — bridge is constructed without
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

    async def _maybe_llm_criterion_block(self, *, final_text: str) -> str | None:
        """Reason string if an enabled pre-final llm_criterion rule BLOCKS, else None.

        Flag-gated by ``MAGI_EGRESS_GATE_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``;
        returns ``None`` (no block) when off, no rules, or on any error (fail-open).
        Only ``action == "block"`` rules can block here (P3); other actions are
        recorded by validation but not enforced at pre-final in this phase.
        """
        from magi_agent.config.flags import flag_bool, flag_profile_bool  # noqa: PLC0415

        if not (
            flag_bool("MAGI_EGRESS_GATE_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        ):
            return None
        try:
            from magi_agent.customize.criterion_engine import evaluate_criterion
            from magi_agent.customize.store import load_overrides
            from magi_agent.customize.verification_policy import (
                CustomizeVerificationPolicy,
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
                passed, reason = await evaluate_criterion(
                    criterion=criterion,
                    draft_text=final_text,
                    model_factory=self._criterion_model_factory,
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
            goal_loop_judge_factory=self._goal_loop_judge_factory,
        )
        try:
            async for item in driver_gen:
                yield item  # RuntimeEvent OR the terminal EngineResult
        finally:
            # FIX 3 (global review): release() MUST run even if aclose() raises,
            # else the session's single-flight slot leaks and every future turn
            # for this session is rejected as ``active_session_turn``.
            try:
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
        goal_loop_judge_factory: Callable[..., object] | None = None,
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
            build_empty_response_message,
            build_grace_message,
            should_grace,
            should_recover_empty,
        )

        types = deps["types"]
        adapter = deps["OpenMagiRunnerAdapter"](runner=runner)  # type: ignore[operator]
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
        route_attach = self._attach_runner_policy_route(
            runner=runner,
            route_selection=route_selection,
        )

        cancelled = False
        engine_error: str | None = None
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
        # P3 zero-edit guard: count file-mutating tool calls this turn.
        # zero_edit_retry_done ensures we fire the guard at most once per turn.
        file_edit_calls = 0
        zero_edit_retry_done = False

        try:
            while True:
                # (Re-)invoke the run: a FRESH ``adapter.run_turn`` is a fresh
                # ``Runner.run_async`` and therefore a real model call. On the
                # first iteration this is the original invocation; on a recovery
                # retry it is the genuine second invocation.
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
                            # PR4 goal-nudge: reset the goal-mode latch whenever a
                            # tool fires so the next clean stop is eligible for a
                            # nudge again (re-arm).
                            if goal_nudge is not None and safe.get("type") == "tool_start":
                                goal_check_pending = False
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
                    attempt_error = exc
                finally:
                    await self._aclose_iter(adk_iter)
                    # Fold this attempt's usage into the turn total (SUM across
                    # re-invocations). Runs on every exit — exhaustion, error, and
                    # cancel — so partial usage survives on aborted turns too.
                    _fold_usage(usage, attempt_usage)

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
                        runner_input = runner_turn_input_cls(
                            userId=self._user_id,
                            sessionId=session_id,
                            turnId=turn_id,
                            invocationId=turn_id,
                            newMessage=types.Content(  # type: ignore[attr-defined]
                                role="user",
                                parts=[
                                    types.Part(  # type: ignore[attr-defined]
                                        text=build_empty_response_message()
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
                        if not _goal_is_met(
                            goal_nudge,
                            evidence_records=self._collect_evidence(turn_id),
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
                            yield RuntimeEvent(
                                type="status",
                                payload={
                                    "type": "goal_nudge",
                                    "mode": goal_nudge.mode,
                                    "nudge": nudges_used,
                                    "max": goal_nudge.max_nudges,
                                },
                                turn_id=turn_id,
                            )
                            continue  # re-invoke run_async (genuine new model call)
                    # PR-C goal-loop clean-break judge. Fires AFTER the legacy
                    # goal_nudge branch (above) and BEFORE the final break.
                    # Reads the per-turn policy ContextVar (PR-B). Absent
                    # policy → byte-identical to pre-PR-C (the existing
                    # ``break`` runs). Present policy → ask the cheap judge
                    # whether the original objective is complete and either
                    # terminate normally, drive a continuation, or terminate
                    # on the parse-failure budget.
                    from magi_agent.runtime.per_turn_goal_loop_context import (  # noqa: PLC0415
                        current_per_turn_goal_loop_policy,
                    )

                    goal_loop_policy = current_per_turn_goal_loop_policy()
                    if goal_loop_policy is not None:
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
                            break
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
                break
        finally:
            self._restore_runner_policy_route(route_attach)
            self._restore_user_hook_bus(hook_attach)
            self._restore_gate_callback(gate_attach)

        if cancelled:
            for safe in self._synthesize_orphan_tool_results(
                pending_tool_ids, turn_id=turn_id
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
            # Balance the transcript on a mid-tool failure too: a runner error
            # while a tool_use is pending would otherwise leave a dangling
            # tool_use that a resuming session cannot reconcile (same hazard the
            # cancel path guards against).
            for safe in self._synthesize_orphan_tool_results(
                pending_tool_ids, turn_id=turn_id
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

        # Model text produced DURING a bounded repair attempt is held here and
        # only delivered if that attempt actually un-blocks the gate. A failed
        # attempt's text is internal repair dialogue (often the model refusing
        # the synthetic repair continuation) — leaking it concatenated the whole
        # exchange into the user-visible reply.
        live_selected = await self._read_live_selected_recipe_pack_ids(session_id)

        # P3: custom llm_criterion gate (pre-final). Independent of the
        # deterministic verifier-bus loop below + the coding-repair loop. A clear
        # FAIL verdict from an enabled block rule aborts the turn with a custom
        # error (mirrors the deterministic block-error return). Flag-gated +
        # fail-open → byte-identical when off.
        llm_block_reason = await self._maybe_llm_criterion_block(final_text=emitted_text)
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
        while True:
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
                break
            yield RuntimeEvent(type="status", payload=pre_final_gate, turn_id=turn_id)
            if pre_final_gate["decision"] != "block":
                for buffered in repair_token_buffer:
                    yield buffered
                repair_token_buffer = []
                break
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
                repair_token_buffer = []

            repair_decision = pre_final_gate.get("repairDecision")
            repair_policy = pre_final_gate.get("repairPolicy")
            should_repair = (
                isinstance(repair_decision, Mapping)
                and repair_decision.get("action") == "continue_repair"
                and isinstance(repair_policy, Mapping)
                and _coding_repair_loop_enabled()
            )
            if not should_repair:
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
            retry_payload = {
                "type": "coding_repair_retry_scheduled",
                "turnId": turn_id,
                "attempt": next_repair_attempt,
                "maxAttempts": max_repair_attempts,
                "missingEvidence": missing_evidence,
                "missingValidators": missing_validators,
            }
            yield RuntimeEvent(type="status", payload=retry_payload, turn_id=turn_id)

            repair_message = _build_repair_continuation_message(
                missing_evidence=missing_evidence,
                missing_validators=missing_validators,
                attempt=next_repair_attempt,
                max_attempts=max_repair_attempts,
            )
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
                        yielded_events += 1
                        self._observe_event(safe, session_id, turn_id)
                        event_kind = _map_event_kind(safe.get("type"))
                        runtime_event = RuntimeEvent(
                            type=event_kind,
                            payload=safe,
                            turn_id=turn_id,
                        )
                        if event_kind == "token":
                            # Held until the gate re-evaluates: delivered on
                            # pass, suppressed on another block (see the
                            # repair_token_buffer handling at the loop top).
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
                self._restore_user_hook_bus(repair_hook_attach)
                self._restore_gate_callback(repair_gate_attach)
                _fold_usage(usage, _repair_usage)

            if cancelled:
                for safe in self._synthesize_orphan_tool_results(
                    pending_tool_ids, turn_id=turn_id
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
                for safe in self._synthesize_orphan_tool_results(
                    pending_tool_ids, turn_id=turn_id
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

        yield EngineResult(  # type: ignore[misc]
            terminal=Terminal.completed,
            usage=usage,
            cost_usd=0.0,
            error=None,
            session_id=session_id,
            turn_id=turn_id,
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
            with _suppress_cancel():
                await cancel_task
            result = next_task.result()
            return result

        # cancel fired first; abandon the in-flight pull.
        next_task.cancel()
        with _suppress_cancel():
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
        with _suppress_cancel():
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

    @staticmethod
    def _synthesize_orphan_tool_results(
        pending_tool_ids: dict[str, str],
        *,
        turn_id: str,
    ) -> list[dict[str, object]]:
        """Build interrupted ``tool_end`` events for any unmatched tool calls.

        These keep the transcript balanced (every tool_use gets a tool_result)
        so a resumed session does not see a dangling tool call.
        """

        results: list[dict[str, object]] = []
        for tool_id in pending_tool_ids:
            results.append(
                {
                    "type": "tool_end",
                    "id": tool_id,
                    "status": "error",
                    "output_preview": "tool interrupted by user cancellation",
                    "durationMs": 0,
                    "interrupted": True,
                }
            )
        pending_tool_ids.clear()
        return results

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
        phase = _select_policy_phase(
            phases=tuple(phase_routes.keys()),
            prompt=prompt,
            harness_state=harness_state,
            assembly=assembly,
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
        assembly = self._runner_policy_assembly
        if assembly is None:
            return None
        if not _pre_final_gate_applies(
            assembly=assembly,
            prompt=prompt,
            harness_state=harness_state,
            coding_mutation_observed=coding_mutation_observed,
            live_selected_pack_ids=live_selected_pack_ids,
        ):
            return None
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
        effective_required_validators = tuple(
            dict.fromkeys((*assembly.required_validators, *extra_validators))
        )
        effective_required_evidence = tuple(
            dict.fromkeys((*assembly.evidence_requirements, *extra_evidence))
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
        from magi_agent.evidence.ledger import (  # noqa: PLC0415
            _BEARER_TOKEN_RE,
            _GITHUB_TOKEN_RE,
            _OPENAI_TOKEN_RE,
            _STRIPE_TOKEN_RE,
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


class _GateAttachment:
    """Restoration handle for a gate ``before_tool_callback`` attachment."""

    __slots__ = ("agent", "original")

    def __init__(self, *, agent: object, original: object) -> None:
        self.agent = agent
        self.original = original


class _RunnerRouteAttachment:
    """Restoration handle for a local runner route attachment."""

    __slots__ = (
        "agent",
        "runner",
        "original_tools",
        "original_instruction",
        "original_agent_route",
        "original_runner_route",
    )

    def __init__(
        self,
        *,
        agent: object,
        runner: object,
        original_tools: object,
        original_instruction: object,
        original_agent_route: object,
        original_runner_route: object,
    ) -> None:
        self.agent = agent
        self.runner = runner
        self.original_tools = original_tools
        self.original_instruction = original_instruction
        self.original_agent_route = original_agent_route
        self.original_runner_route = original_runner_route


class _Sentinel:
    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self._name}>"


_EXHAUSTED = _Sentinel("adk_stream_exhausted")
_CANCELLED = _Sentinel("adk_stream_cancelled")
_MISSING = _Sentinel("missing")
_RUNNER_POLICY_ROUTING_ENV = "MAGI_RUNNER_POLICY_ROUTING_ENABLED"
_RUNNER_POLICY_ROUTE_BLOCKING_ENV = "MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED"
_RECIPE_INTENT_BINDING_ENV = "MAGI_RECIPE_INTENT_BINDING_ENABLED"
_CODING_PHASES = frozenset(
    {"code_search", "patch_planning", "patch_generation", "test_interpretation"}
)
_LOCAL_READONLY_TOOL_NAMES = frozenset(
    {
        "ArtifactList",
        "ArtifactRead",
        "Calculation",
        "Clock",
        "FileRead",
        "GitDiff",
        "Glob",
        "Grep",
    }
)
_TOOL_INTENT_ALIASES: Mapping[str, tuple[str, ...]] = {
    "tool:file.read": ("FileRead",),
    "tool:test.run": ("TestRun",),
    "tool:git.diff": ("GitDiff",),
    "tool:FileDeliver": ("FileDeliver",),
    "tool:FileSend": ("FileSend",),
    "tool:ChannelDispatcher": ("ChannelDispatcher",),
    "tool:NotifyUser": ("NotifyUser",),
}


def _non_empty_str(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _str_tuple(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,) if values else ()
    if not isinstance(values, list | tuple):
        return ()
    return tuple(str(value) for value in values if str(value))


def _routing_field(source: object, alias: str, snake: str) -> object:
    """Read a phase-routing field by its alias or snake_case key.

    The materialized plan is dumped ``by_alias=True`` (camelCase), but a
    hand-built plan or a future ``mode="python"`` dump may use snake_case.
    """

    if not isinstance(source, Mapping):
        return None
    if alias in source:
        return source[alias]
    return source.get(snake)


def _authority_safe_attachment_flags(flags: Mapping[str, bool]) -> dict[str, bool]:
    safe = {str(key): bool(value) for key, value in flags.items()}
    safe["productionWriteAllowed"] = False
    safe["userVisibleOutputAllowed"] = False
    return safe


def _runner_policy_routing_enabled() -> bool:
    """Whether runner-policy phase routing emits and attaches safe routes.

    The code-level default stays OFF. Installed/local full-runtime profiles and
    hosted canary profiles should enable this explicitly in their config/env.

    Reads through the canonical registry (I-2 PR A) so the truthy convention
    lives in exactly one place: explicit ``"1"/"true"/"yes"/"on"`` enables;
    any other value — including unset and unknown values like ``"enabled"``
    — keeps the default-OFF authority gate closed.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(_RUNNER_POLICY_ROUTING_ENV)


def _runner_policy_route_blocking_enabled() -> bool:
    """Whether denied materialized routes fail closed before provider calls.

    Route blocking is intentionally NOT part of the default full-runtime profile:
    materialized route denials can be stale or conservative while the configured
    model is still capable of finishing the user turn. By default, route denials
    are emitted as audit metadata and the turn continues on the configured model.
    Operators can explicitly re-arm the older hard-block boundary with this env.

    Reads through the canonical registry (I-2 PR A); strict allowlist semantics.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(_RUNNER_POLICY_ROUTE_BLOCKING_ENV)


def _recipe_intent_binding_enabled() -> bool:
    """Whether emit-only recipe intents bind to hint-level runner effects.

    Stage gate for doc 05 PR-3 (A1-G2). The code-level default stays OFF, so the
    emitted runner-policy route selection is byte-identical to origin/main unless
    an operator opts in. See ``parse_recipe_intent_binding_enabled``.

    Reads through the canonical registry (I-2 PR A); strict allowlist semantics.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(_RECIPE_INTENT_BINDING_ENV)


def compile_intent_bindings(
    assembly: RunnerPolicyAssembly,
    *,
    enabled: bool,
) -> dict[str, object]:
    """Bind the four emit-only recipe intent families to hint-level effects.

    ``provider_intents`` / ``channel_intents`` / ``artifact_intents`` /
    ``scheduler_intents`` are materialized as public-payload metadata but, unlike
    ``tool_intents``, have no consumer driving a runner effect. This compiles them
    into a hint-level binding payload that downstream seams (model selection,
    channel delivery, pre-final artifact requirements, the 03-always-on
    scheduler) can read.

    The binding is intentionally *hint* level: it never asserts production-write
    authority and never hard-forces a model/channel/provider (open-decision §6-2:
    hint, not force; hard enforcement deferred to 14-controlplane).

    When ``enabled`` is False this returns ``{}`` so the caller's emitted payload
    stays byte-identical to origin/main.
    """
    if not enabled:
        return {}
    bindings: dict[str, object] = {
        "schemaVersion": "magi.recipeIntentBinding.v1",
        "enforcement": "hint",
        "productionWriteAllowed": False,
    }
    if assembly.provider_intents:
        bindings["providerPreferenceHints"] = list(assembly.provider_intents)
    if assembly.channel_intents:
        bindings["channelDeliveryHints"] = list(assembly.channel_intents)
    if assembly.artifact_intents:
        bindings["artifactDeliveryRequirements"] = list(assembly.artifact_intents)
    if assembly.scheduler_intents:
        bindings["schedulerReadinessHints"] = list(assembly.scheduler_intents)
    return bindings


def _phase_routes(phase_routing: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw = phase_routing.get("phaseRoutes") or phase_routing.get("phase_routes")
    if not isinstance(raw, Mapping):
        return {}
    routes: dict[str, Mapping[str, object]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, Mapping):
            routes[key] = value
    return routes


def _select_policy_phase(
    *,
    phases: tuple[str, ...],
    prompt: str,
    harness_state: object | None,
    assembly: RunnerPolicyAssembly,
) -> str:
    phase_set = set(phases)
    # Only the live harness state describes the CURRENT task. ``assembly.task_profile``
    # is the bot's static CAPABILITY superset (every taskType it could ever do — it
    # always contains "coding"), so falling back to it made EVERY turn — including a
    # plain "hi" — classify as coding, select the ``patch_generation`` phase, and
    # (when the routed model lacks coding capability) fail-closed with
    # ``runner_policy_route_denied``. Derive the task from harness_state + the prompt
    # markers below only; with neither signal, fall through to the conversational
    # ``final_answer_drafting`` phase.
    task_types = {
        _normalize_task_type(item)
        for item in (_extract_task_types(harness_state) or ())
    }
    prompt_lower = prompt.lower()
    coding_requested = bool(task_types & _CODING_TASK_TYPES) or any(
        marker in prompt_lower for marker in _CODING_PROMPT_MARKERS
    )
    if coding_requested:
        for phase in ("patch_generation", "code_search", "test_interpretation"):
            if phase in phase_set:
                return phase

    research_requested = bool(
        task_types & {"research", "web-acquisition", "browser-automation"}
    ) or any(marker in prompt_lower for marker in ("research", "source", "cite", "web"))
    if research_requested:
        for phase in ("source_acquisition", "source_extraction"):
            if phase in phase_set:
                return phase

    if "final_answer_drafting" in phase_set:
        return "final_answer_drafting"
    if "intent_classification" in phase_set:
        return "intent_classification"
    return phases[0]


def _local_tool_names_for_route(
    *,
    runner: object,
    assembly: RunnerPolicyAssembly,
    phase: str,
    route: Mapping[str, object],
) -> tuple[str, ...]:
    available = _available_agent_tool_names(runner)
    if not available:
        return ()

    selected = set(assembly.selected_pack_ids)
    capabilities = set(_str_tuple(route.get("capabilities")))
    coding_route = (
        phase in _CODING_PHASES
        or "coding" in capabilities
        or "openmagi.dev-coding" in selected
    )
    if coding_route:
        return ()

    desired = set(_LOCAL_READONLY_TOOL_NAMES)
    for intent in assembly.tool_intents:
        desired.update(_tool_names_for_intent(intent))

    return tuple(name for name in available if name in desired)


def _available_agent_tool_names(runner: object) -> tuple[str, ...]:
    agent = getattr(runner, "agent", None)
    tools = getattr(agent, "tools", None)
    if not isinstance(tools, list):
        return ()
    return tuple(name for tool in tools if (name := _tool_name(tool)) is not None)


def _tool_name(tool: object) -> str | None:
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else None


def _tool_names_for_intent(intent: str) -> tuple[str, ...]:
    aliased = _TOOL_INTENT_ALIASES.get(intent)
    if aliased is not None:
        return aliased
    if not intent.startswith("tool:"):
        return ()
    raw = intent.removeprefix("tool:")
    if not raw:
        return ()
    if "." in raw:
        return ()
    return (raw,)


def _restore_attr(target: object, name: str, original: object) -> None:
    try:
        if original is _MISSING:
            delattr(target, name)
        else:
            setattr(target, name, original)
    except Exception:
        pass


class _suppress_cancel:
    """Context manager swallowing ``asyncio.CancelledError`` (and others)."""

    def __enter__(self) -> "_suppress_cancel":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(
            exc_type, (asyncio.CancelledError, Exception)
        )


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
