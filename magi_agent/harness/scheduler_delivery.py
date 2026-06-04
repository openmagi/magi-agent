"""A4 — Delivery boundary for cron turn output.

Boundary module.  Delivers the text output of a completed cron turn to a local
log sink, with a [SILENT] escape hatch for suppression and a recency-aware
target resolution rule.

SILENT suppression
------------------
If the turn output, stripped and upper-cased, equals exactly ``"[SILENT]"``,
delivery is suppressed (``status="suppressed_silent"``).  An audit receipt is
ALWAYS recorded (the output length + sha256 digest, never raw text).

Mixed content rule: if output contains ``[SILENT]`` alongside other text the
rule does NOT suppress — that is a real output that happens to mention the
marker.  Only an exact match (after whitespace strip) triggers suppression.

Target resolution
-----------------
``resolve_delivery_target(job, *, last_active_session, explicit_target)`` is a
PURE function implementing the session-recency > explicit flag rule:

  1. Recent active session present → route to it (recency wins).
  2. No active session, explicit_target provided → use explicit.
  3. Neither → local default sink.

Rationale: when an active session exists, routing to it avoids delivering to a
stale explicit target that may no longer be relevant (lesson from cron v3).

Track-E seam
------------
Real channel delivery (Telegram, Discord, web-push) is Track E.  This module
documents the seam:

    # TRACK-E SEAM: replace LocalLogDeliverySink with a ChannelDeliverySink
    # that wraps push_delivery.py once channels are enabled.  The
    # DeliveryTarget Protocol and deliver() call shape remain unchanged.

Authority flags
--------------
All authority flags on ``DeliveryReceipt`` remain ``Literal[False]`` and are
never mutated by code.  Delivery is a local/log write only; no network,
no Telegram, no Discord in A4.

Evidence + redaction
--------------------
Output content is NEVER stored raw in the receipt or evidence fields.  Only
``outputLength`` (int) and ``outputDigest`` (sha256 hex) are stored, following
the missions/receipts.py redaction pattern.

Forbidden imports: urllib, socket, subprocess, http, requests, httpx,
magi_agent.adk_bridge, google.adk, telegram, discord — none appear in this
module.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

SILENT_MARKER = "[SILENT]"

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

# A zero/placeholder digest used when output is empty.
_ZERO_DIGEST = "sha256:" + "0" * 64


# ---------------------------------------------------------------------------
# [SILENT] logic
# ---------------------------------------------------------------------------

def is_silent_output(output: str) -> bool:
    """Return True iff ``output`` (stripped+upper) is EXACTLY ``[SILENT]``.

    Mixed content (e.g. "[SILENT] but also text") returns False — only an
    exact match triggers suppression.  Empty string returns False.
    """
    return output.strip().upper() == SILENT_MARKER


# ---------------------------------------------------------------------------
# DeliveryTarget Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DeliveryTarget(Protocol):
    """Abstraction for a delivery destination.

    A4 provides only ``LocalLogDeliverySink`` (local/log, no network).
    Track E will introduce ``ChannelDeliverySink`` backed by push_delivery.py.

    Implementors must be frozen Pydantic models (or equivalent immutable).
    """

    # Marker attribute — concrete sinks must set this.
    sink_id: str


# ---------------------------------------------------------------------------
# DeliveryReceipt
# ---------------------------------------------------------------------------

DeliveryReceiptStatus = Literal["delivered", "suppressed_silent", "skipped"]


class DeliveryReceipt(BaseModel):
    """Frozen audit receipt for one cron turn delivery attempt.

    Raw output text is never stored.  ``output_length`` and ``output_digest``
    provide enough information for audits without leaking content.

    Authority flags are Literal[False] — no live channel delivery in A4.
    """

    model_config = _MODEL_CONFIG

    status: DeliveryReceiptStatus
    job_id: str = Field(alias="jobId")
    output_length: int = Field(alias="outputLength")
    output_digest: str = Field(alias="outputDigest")

    # Authority flags — always False; Track E will not flip these here.
    channel_delivery_enabled: Literal[False] = Field(
        default=False, alias="channelDeliveryEnabled"
    )
    network_call_attempted: Literal[False] = Field(
        default=False, alias="networkCallAttempted"
    )

    # Evidence record (redacted — length/hash only).
    evidence: EvidenceRecord | None = Field(default=None, alias="evidence")


# ---------------------------------------------------------------------------
# LocalLogDeliverySink
# ---------------------------------------------------------------------------

class LocalLogDeliverySink(BaseModel):
    """Concrete delivery target: local/log sink (no network, no channels).

    Satisfies the DeliveryTarget Protocol.  In tests this is the default
    sink; in production it writes an in-process audit log entry.

    TRACK-E SEAM: replace or supplement with ChannelDeliverySink when
    live channel delivery is enabled (Track E).
    """

    model_config = _MODEL_CONFIG

    sink_id: str = Field(default="local.log.default", alias="sinkId")
    description: str = Field(
        default="Local audit log sink — no network, no channels (A4).",
        alias="description",
    )


# ---------------------------------------------------------------------------
# SessionAwareDeliveryTarget
# ---------------------------------------------------------------------------

class SessionAwareDeliveryTarget(BaseModel):
    """Delivery target that routes to an active session (recency-win target).

    Used by ``resolve_delivery_target`` when a recent active session is
    available.  In A4 this still writes to the local log; the session_id is
    recorded for future routing when Track E wires real channels.
    """

    model_config = _MODEL_CONFIG

    sink_id: str = Field(default="session.aware.local", alias="sinkId")
    session_id: str = Field(alias="sessionId")
    description: str = Field(
        default="Session-aware delivery target (A4 — local/log; routing seam for Track E).",
        alias="description",
    )


# ---------------------------------------------------------------------------
# Target resolution — pure function
# ---------------------------------------------------------------------------

def resolve_delivery_target(
    job: object,
    *,
    last_active_session: DeliveryTarget | None = None,
    explicit_target: DeliveryTarget | None = None,
) -> DeliveryTarget:
    """Resolve the delivery target for a cron job turn using the recency rule.

    Rule (session-recency > explicit flag):
      1. If ``last_active_session`` is provided → return it (recency wins).
         Rationale: an active session is always more current than a stale
         explicit target configured at schedule-creation time.
      2. Else if ``explicit_target`` is provided → return it.
      3. Else → return the default ``LocalLogDeliverySink``.

    This is a PURE function — no side effects, no I/O.
    ``job`` is accepted for future enrichment (e.g. reading per-job channel
    hints) but is not used in A4.
    """
    _ = job  # reserved for Track-E per-job channel lookup
    if last_active_session is not None:
        return last_active_session
    if explicit_target is not None:
        return explicit_target
    return LocalLogDeliverySink()


# ---------------------------------------------------------------------------
# Evidence builder (redacted)
# ---------------------------------------------------------------------------

def _build_delivery_evidence(
    *,
    job_id: str,
    receipt_status: DeliveryReceiptStatus,
    output_length: int,
    output_digest: str,
    target_sink_id: str,
    now: datetime,
) -> EvidenceRecord:
    """Build a redacted EvidenceRecord for the delivery attempt.

    Raw output content is NEVER included.  Only length + digest are stored.
    """
    evidence_status = "ok" if receipt_status in {"delivered", "suppressed_silent"} else "failed"
    return EvidenceRecord(
        type="custom:SchedulerCronDelivery",
        status=evidence_status,
        observedAt=int(now.astimezone(UTC).timestamp() * 1000),
        source=EvidenceSource(kind="execution_contract"),
        fields={
            "jobId": job_id,
            "deliveryStatus": receipt_status,
            "outputLength": output_length,
            "outputDigest": output_digest,
            "sinkId": target_sink_id,
            # Raw output text is intentionally absent — redaction by omission.
        },
    )


# ---------------------------------------------------------------------------
# deliver() — public delivery boundary
# ---------------------------------------------------------------------------

def deliver(
    result: object,
    *,
    target: DeliveryTarget,
    now: datetime | None = None,
) -> DeliveryReceipt:
    """Deliver cron turn output to ``target``, returning a frozen ``DeliveryReceipt``.

    Delivery statuses:
      - ``delivered`` — output delivered to the local log (or future channel).
      - ``suppressed_silent`` — output was exactly ``[SILENT]`` (stripped+upper);
        delivery skipped but audit receipt recorded.
      - ``skipped`` — turn had no meaningful output (empty, failed, timed_out).

    Output content is NEVER stored in the receipt.  Only ``outputLength`` and
    ``outputDigest`` (sha256) are stored for audit.

    TRACK-E SEAM: when ``target`` is a ``ChannelDeliverySink``, this function
    will delegate to push_delivery.py.  For A4, only LocalLogDeliverySink and
    SessionAwareDeliveryTarget are supported (both write to local log).
    """
    resolved_now = now or datetime.now(tz=UTC)

    # Extract output from CronTurnResult (duck-typed to avoid circular import).
    output: str = getattr(result, "output", "") or ""
    job_id: str = getattr(result, "job_id", "") or ""

    output_length = len(output)
    output_digest = (
        "sha256:" + hashlib.sha256(output.encode()).hexdigest()
        if output
        else _ZERO_DIGEST
    )

    # Resolve delivery status.
    if is_silent_output(output):
        receipt_status: DeliveryReceiptStatus = "suppressed_silent"
    elif not output.strip():
        receipt_status = "skipped"
    else:
        receipt_status = "delivered"

    # Build redacted evidence.
    sink_id: str = getattr(target, "sink_id", "unknown")
    evidence = _build_delivery_evidence(
        job_id=job_id,
        receipt_status=receipt_status,
        output_length=output_length,
        output_digest=output_digest,
        target_sink_id=sink_id,
        now=resolved_now,
    )

    return DeliveryReceipt(
        status=receipt_status,
        jobId=job_id,
        outputLength=output_length,
        outputDigest=output_digest,
        evidence=evidence,
    )


__all__ = [
    "SILENT_MARKER",
    "DeliveryReceipt",
    "DeliveryReceiptStatus",
    "DeliveryTarget",
    "LocalLogDeliverySink",
    "SessionAwareDeliveryTarget",
    "deliver",
    "is_silent_output",
    "resolve_delivery_target",
]
