"""Learning-layer rollout staging telemetry — PR8.

Emits deterministic, tenant-scoped, PII-free events for the key learning-layer
lifecycle moments so a fleet operator can observe rollout progression:

    * reflection run (counts)
    * candidate proposed
    * eval observation (pass / fail)
    * approval (who / role)
    * live promotion (the PR7 ``LearningLiveAuditRecord``)
    * rollout staging (the PR7 ``disabled / shadow / live`` readiness ladder)

Every event is a :class:`magi_agent.telemetry.deterministic_events.DeterministicRuntimeEvent`,
so it is run through the SAME digest / ``_reject_raw_private_metadata`` /
``activationEnabled == False`` invariants as the rest of the runtime telemetry.

PII rule (consistent with PR7's ``user_id_digest``): a raw user id is NEVER
placed in an event — only its ``sha256:`` digest.  Tenant ids and item ids are
public routing keys and are recorded as safe refs.

Default-OFF: emission is gated on ``MAGI_LEARNING_TELEMETRY_ENABLED`` (default
OFF).  When OFF every ``emit_*`` is a no-op returning ``None`` — the telemetry
surface stays byte-quiet, matching the default-OFF posture of the whole layer.

This module touches NO agent-core surface — it only constructs events and hands
them to an injected ``sink`` (default: a module-level logger record builder).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from magi_agent.learning.config import resolve_learning_config
from magi_agent.telemetry.deterministic_events import DeterministicRuntimeEvent
from magi_agent.telemetry.logging import log_record

if TYPE_CHECKING:
    from magi_agent.learning.live import LearningLiveAuditRecord


logger = logging.getLogger(__name__)


_TELEMETRY_ENV_VAR = "MAGI_LEARNING_TELEMETRY_ENABLED"
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: Stable, never-mutating digests used to satisfy the event's required
#: ``effectivePolicySnapshotDigest`` / ``ledgerHeadDigest`` shape.  The learning
#: telemetry layer has no policy/ledger of its own; these are fixed sentinels so
#: events validate without ever carrying real policy/ledger state.
_NULL_DIGEST = "sha256:" + "0" * 64

#: A type-callable sink that accepts one event.  Default sink routes through the
#: stdlib-ish ``log_record`` helper so emission is observable in logs.
EventSink = Callable[[DeterministicRuntimeEvent], None]


def learning_telemetry_enabled() -> bool:
    """True when learning telemetry emission is enabled.

    PR9a layered opt-out: telemetry is now ON **by default** (safe tier) — it
    only constructs PII-free, deterministic events and hands them to a sink, so
    it incurs no model cost and changes no behaviour.  It is OFF only when the
    master switch ``MAGI_LEARNING_ENABLED`` is explicitly falsy or
    ``MAGI_LEARNING_TELEMETRY_ENABLED`` is explicitly falsy.  Resolution flows
    through :func:`resolve_learning_config`; master-off restores the byte-quiet
    PR1–PR8 no-emission state.
    """
    return resolve_learning_config().telemetry_effective


def _sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _default_sink(event: DeterministicRuntimeEvent) -> None:
    log_record(
        "info",
        "learning.telemetry",
        event=event.model_dump(by_alias=True, mode="json"),
    )


def _emit(
    *,
    event_id: str,
    step_id: str,
    event_type: str,
    route_decision: str,
    metadata: Mapping[str, object],
    sink: EventSink | None,
) -> DeterministicRuntimeEvent | None:
    """Build + dispatch one learning telemetry event, or no-op when default-OFF.

    All learning telemetry events share the same fixed scaffold (null
    policy/ledger digests, ``activationEnabled=False`` — enforced by the model,
    ``redactionStatus="redacted"``).  The caller supplies only the event-type,
    a public ``route_decision`` discriminator, and a PII-free metadata mapping.
    """
    if not learning_telemetry_enabled():
        return None
    # Telemetry must never crash the caller: if the strict event model rejects
    # any field (e.g. an operator/bot id carrying an odd or model-protected
    # token), log a WARNING and return None instead of propagating.
    try:
        event = DeterministicRuntimeEvent(
            eventId=event_id,
            runId="learning.rollout",
            workflowId="openmagi.learning",
            stepId=step_id,
            eventType=event_type,
            routeDecision=route_decision,
            effectivePolicySnapshotDigest=_NULL_DIGEST,
            ledgerHeadDigest=_NULL_DIGEST,
            repairAttempt=0,
            projectionMode="learning_rollout_staging",
            redactionStatus="redacted",
            metadata=dict(metadata),
        )
    except Exception:
        logger.warning(
            "learning telemetry event construction failed; dropping event "
            "(event_type=%s, route_decision=%s)",
            event_type,
            route_decision,
        )
        return None
    (sink or _default_sink)(event)
    return event


# ---------------------------------------------------------------------------
# Lifecycle event emitters
# ---------------------------------------------------------------------------


def emit_learning_reflection_event(
    *,
    tenant_id: str,
    candidates_produced: int,
    items_proposed: int,
    items_activated: int,
    sink: EventSink | None = None,
) -> DeterministicRuntimeEvent | None:
    """Emit a reflection-run telemetry event (counts only, tenant-scoped)."""
    return _emit(
        event_id="learning.reflection.run",
        step_id="learning.reflection",
        event_type="checkpoint",
        route_decision="learning_reflection_run",
        metadata={
            "tenantId": tenant_id,
            "candidatesProduced": int(candidates_produced),
            "itemsProposed": int(items_proposed),
            "itemsActivated": int(items_activated),
        },
        sink=sink,
    )


def emit_learning_candidate_proposed_event(
    *,
    tenant_id: str,
    item_id: str,
    kind: str,
    sink: EventSink | None = None,
) -> DeterministicRuntimeEvent | None:
    """Emit a candidate-proposed telemetry event (tenant + item + kind)."""
    return _emit(
        event_id="learning.candidate.proposed",
        step_id="learning.candidate",
        event_type="checkpoint",
        route_decision="learning_candidate_proposed",
        metadata={
            "tenantId": tenant_id,
            "itemId": item_id,
            "kind": kind,
        },
        sink=sink,
    )


def emit_learning_eval_observation_event(
    *,
    tenant_id: str,
    item_id: str,
    passed: bool,
    sample_n: int,
    sink: EventSink | None = None,
) -> DeterministicRuntimeEvent | None:
    """Emit an eval-observation telemetry event (pass/fail + sample size)."""
    return _emit(
        event_id="learning.eval.observation",
        step_id="learning.eval",
        event_type="guardrail_result",
        route_decision="learning_eval_observation",
        metadata={
            "tenantId": tenant_id,
            "itemId": item_id,
            "passed": bool(passed),
            "sampleN": int(sample_n),
        },
        sink=sink,
    )


def emit_learning_approval_event(
    *,
    tenant_id: str,
    item_id: str,
    approver_role: str,
    user_id: str,
    sink: EventSink | None = None,
) -> DeterministicRuntimeEvent | None:
    """Emit an approval telemetry event (tenant + item + role + HASHED user).

    The approver's user id is recorded ONLY as a ``sha256:`` digest — never the
    raw identity — so approval telemetry stays PII-free.
    """
    return _emit(
        event_id="learning.approval.granted",
        step_id="learning.approval",
        event_type="approval",
        route_decision="learning_approval_granted",
        metadata={
            "tenantId": tenant_id,
            "itemId": item_id,
            "approverRole": approver_role,
            "approverUserIdDigest": _sha256_text_digest(user_id),
        },
        sink=sink,
    )


def emit_learning_promotion_event(
    audit: LearningLiveAuditRecord,
    *,
    sink: EventSink | None = None,
) -> DeterministicRuntimeEvent | None:
    """Emit a live-promotion telemetry event from a PR7 audit record.

    The audit already carries a ``user_id_digest`` (PII-free) and a public
    ``tenant_id`` / ``bot_id``; this surfaces that promotion as a tenant-scoped
    telemetry event so a fleet operator sees authority promotions.
    """
    return _emit(
        event_id="learning.live.promotion",
        step_id="learning.promotion",
        event_type="approval",
        route_decision="learning_live_promotion",
        metadata={
            "tenantId": audit.tenant_id,
            "botId": audit.bot_id,
            "executionMode": audit.execution_mode,
            "gateEnabled": bool(audit.gate_enabled),
            "readinessReady": bool(audit.readiness_ready),
            "promotedAdapters": tuple(audit.promoted_adapters),
            "ownerUserIdDigest": audit.user_id_digest,
        },
        sink=sink,
    )


def emit_learning_rollout_staging_event(
    *,
    tenant_id: str,
    bot_id: str,
    execution_mode: str,
    promoted_gate: int,
    canary_live_gate: int,
    user_id_digest: str,
    sink: EventSink | None = None,
) -> DeterministicRuntimeEvent | None:
    """Emit the PR7 readiness ladder (disabled/shadow/live) as a staging event.

    Surfaces gate stage + canary scope GENERICALLY (OSS) — a hashed owner digest
    and an opaque ``bot_id``, NEVER a hardcoded production bot id.  The real
    fleet/canary wiring lives in the hosted monorepo, out of OSS scope.
    """
    return _emit(
        event_id="learning.rollout.staging",
        step_id="learning.staging",
        event_type="checkpoint",
        route_decision="learning_rollout_staging",
        metadata={
            "tenantId": tenant_id,
            "botId": bot_id,
            "executionMode": execution_mode,
            "promotedGate": int(promoted_gate),
            "canaryLiveGate": int(canary_live_gate),
            "ownerUserIdDigest": user_id_digest,
        },
        sink=sink,
    )


__all__ = [
    "EventSink",
    "emit_learning_approval_event",
    "emit_learning_candidate_proposed_event",
    "emit_learning_eval_observation_event",
    "emit_learning_promotion_event",
    "emit_learning_reflection_event",
    "emit_learning_rollout_staging_event",
    "learning_telemetry_enabled",
]
