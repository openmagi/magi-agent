"""Learning-layer LIVE adapters — PR7 (local-fake → real promotion).

This module is the ONE place where the Learning Layer's local-fake stand-ins
(PR1–PR6) become REAL, and it does so EXACTLY the way
``harness/workflow_executor.py`` promotes spec→live:

    spec/local-fake  ──readiness gate (gates/learning_live_readiness.py)──▶
        disabled  → keep local-fake (byte-identical to PR1–PR6)
        shadow    → COMPUTE real adapter outputs, WRITE / INJECT nothing
        live      → bind real adapters (recall injects, writes persist)

Crucially, promotion is introduced WITHOUT flipping any ``Literal[False]``
authority flag.  Those flags stay a permanent attestation that the
reflection-executor / memory-recall / memory-write CORE never self-attaches live
authority.  Live behaviour is its OWN, separately-gated layer:

    * ``RealTranscriptSource`` — implements the ``TranscriptSource`` Protocol by
      reading ACTUAL persisted sessions from ``storage.session_store`` (the real
      durable session-persistence surface), watermark-incremental on
      ``updated_at``.  No core edits — it reads the store read-only.
    * ``LlmBackedLabeler`` — implements the ``Labeler`` Protocol by calling a
      real model client (the ``LabelerModelClient`` Protocol seam).  In tests a
      deterministic real-Protocol stub is injected; NO real network/LLM call.
    * ``build_live_learning_binding`` — the gated promotion entrypoint.  It
      resolves the rollout mode from the readiness gate and either keeps
      local-fake (disabled) or binds the real adapters (shadow/live), emitting a
      ``LearningLiveAuditRecord`` of the authority promotion (what got promoted,
      when, gate/readiness state).

No core files are touched (``message_builder``, ``openmagi_runtime``,
``adk_bridge``, ADK runner).  This is the harness/learning layer only.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.gates.learning_live_readiness import (
    LearningLiveExecutionMode,
    LearningLiveReadinessConfig,
    _sha256_text_digest,
    learning_live_readiness_health_metadata,
)
from magi_agent.learning.candidates import (
    LocalFakeTranscriptSource,
    SessionTrace,
    TranscriptSource,
)
from magi_agent.learning.labeler import (
    LabeledLearning,
    LabelType,
    Labeler,
    LocalFakeLabeler,
    _SIGNAL_LABEL_MAP,
)
from magi_agent.learning.signals import Signal


# ---------------------------------------------------------------------------
# Real transcript source — reads persisted sessions (real durable surface)
# ---------------------------------------------------------------------------


# Default cap on how many sessions a single reflection read pulls from the
# durable store.  Without this the watermark was applied in Python AFTER a
# ``SELECT *`` of the whole sessions table; the cap + ``WHERE updated_at > ?``
# push the bound to SQL so reflection never scans the full table.
_DEFAULT_REFLECTION_READ_CAP = 500


@runtime_checkable
class SessionPersistenceReader(Protocol):
    """Read-only view of the durable session store needed by the real source.

    Satisfied by ``storage.session_store.SessionSqliteStore`` (its ``list_sync``
    returns persisted sessions ordered by ``updated_at DESC``).  Declaring a
    narrow Protocol keeps ``learning/live.py`` decoupled from the concrete store
    (and lets tests inject an in-memory fake) without importing core surfaces.
    """

    def list_sync(
        self,
        app_name: str,
        user_id: str | None = None,
        *,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        ...  # pragma: no cover


class RealTranscriptSource:
    """REAL ``TranscriptSource`` reading ACTUAL persisted sessions.

    Reads sessions from the durable ``SessionSqliteStore`` (via the
    ``SessionPersistenceReader`` Protocol) and maps each persisted session's
    ``state`` + ``updated_at`` to a ``SessionTrace``.  Watermark-incremental:
    ``read_since(watermark)`` returns only traces with ``ts > watermark`` —
    the watermark is the session's ``updated_at`` normalized to UTC ``Z`` form.

    No network, no model.  Reads the store read-only — the agent core is never
    touched.  Conforms to the ``TranscriptSource`` Protocol so it drops straight
    into ``run_reflection(source=...)``.
    """

    def __init__(
        self,
        *,
        store: SessionPersistenceReader,
        app_name: str = "magi",
        user_id: str | None = None,
        read_cap: int = _DEFAULT_REFLECTION_READ_CAP,
    ) -> None:
        self._store = store
        self._app_name = app_name
        self._user_id = user_id
        self._read_cap = read_cap

    async def read_since(
        self, watermark: str | None
    ) -> tuple[SessionTrace, ...]:
        # Push the watermark + cap to the SQL layer so reflection never scans
        # the whole sessions table.  ``since`` is a COARSE pre-filter (the DB's
        # persisted ``updated_at`` precision may differ from the normalized
        # watermark); the precise ``trace.ts > watermark`` check below is the
        # authority and never lets an equal/older trace through.
        rows = self._store.list_sync(
            self._app_name,
            self._user_id,
            since=watermark,
            limit=self._read_cap,
        )
        traces: list[SessionTrace] = []
        for row in rows:
            trace = _row_to_trace(row)
            if trace is None:
                continue
            if watermark is not None and not (trace.ts > watermark):
                continue
            traces.append(trace)
        return tuple(traces)


def _normalize_ts(raw: object) -> str | None:
    """Normalize a persisted ``updated_at`` to UTC ``Z`` form for SessionTrace.

    Returns ``None`` when the value can't be normalized so a malformed row is
    skipped rather than crashing the read.
    """
    if not isinstance(raw, str) or not raw:
        return None
    # Always re-parse and re-emit canonical 6-digit-microsecond ``Z`` form so a
    # ``Z`` timestamp WITHOUT microseconds (``...:00Z``) compares correctly,
    # lexicographically, against one WITH (``...:00.000000Z``).  A naive
    # short-circuit on ``endswith("Z")`` mis-filters same-second sessions
    # because ``Z`` (0x5A) sorts after ``.`` (0x2E).
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_trace(row: Mapping[str, Any]) -> SessionTrace | None:
    """Map a persisted session row to a ``SessionTrace`` (or None to skip)."""
    ts = _normalize_ts(row.get("updated_at"))
    if ts is None:
        return None
    state = row.get("state")
    if not isinstance(state, Mapping):
        state = {}
    raw_turns = state.get("turns")
    if isinstance(raw_turns, (list, tuple)):
        turns = tuple(t for t in raw_turns if isinstance(t, Mapping))
    else:
        turns = ()
    final_output = str(state.get("finalOutput") or state.get("final_output") or "")
    draft_raw = state.get("draftOutput") or state.get("draft_output")
    draft_output = str(draft_raw) if draft_raw is not None else None
    try:
        return SessionTrace(
            sessionId=str(row.get("id") or "unknown-session"),
            turns=turns,
            finalOutput=final_output,
            draftOutput=draft_output,
            ts=ts,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Real labeler — LLM-backed binding (real-Protocol model client seam)
# ---------------------------------------------------------------------------


@runtime_checkable
class LabelerModelClient(Protocol):
    """Minimal real model-client seam for the LLM-backed labeler.

    The runtime's model surface (``runtime/provider_execution``) is core and
    off-limits to edit, so the live labeler binds to THIS narrow Protocol.  A
    real provider adapter implements it behind the gate; tests inject a
    deterministic real-Protocol stub (no network/LLM call).
    """

    def label_signal(self, *, signal_kind: str, summary: str) -> Mapping[str, str]:
        ...  # pragma: no cover


_VALID_LABEL_TYPES: frozenset[LabelType] = frozenset(
    ("fact", "citation", "style", "strategy")
)


class LlmBackedLabeler:
    """REAL ``Labeler`` backed by a model client (PR7 promotion of LocalFakeLabeler).

    Implements the ``Labeler`` Protocol: each signal is sent to the injected
    ``LabelerModelClient`` which returns a label type + lesson.  The candidate
    kind is taken from the deterministic ``_SIGNAL_LABEL_MAP`` (same kind mapping
    the local fake uses) so promotion does not change downstream pipeline shape.
    A malformed / failed model response falls back to the deterministic local
    fake so a live labeler never crashes a reflection pass.
    """

    def __init__(self, *, model_client: LabelerModelClient) -> None:
        self._model_client = model_client
        self._fallback = LocalFakeLabeler()

    def label(self, signal: Signal, trace: SessionTrace) -> LabeledLearning | None:
        mapping = _SIGNAL_LABEL_MAP.get(signal.kind)
        if mapping is None:  # pragma: no cover - all kinds mapped
            return None
        _default_type, candidate_kind = mapping
        try:
            response = self._model_client.label_signal(
                signal_kind=signal.kind, summary=signal.summary
            )
        except Exception:
            return self._fallback.label(signal, trace)
        if not isinstance(response, Mapping):
            return self._fallback.label(signal, trace)
        label_type_raw = str(response.get("type") or "").strip()
        label_type: LabelType = (
            label_type_raw if label_type_raw in _VALID_LABEL_TYPES else _default_type  # type: ignore[assignment]
        )
        lesson = str(response.get("lesson") or "").strip() or f"[{signal.kind}] {signal.summary}"
        return LabeledLearning(
            type=label_type,
            lesson=lesson,
            candidateKind=candidate_kind,
            content={
                "situation": signal.summary,
                "behavior": lesson,
            },
        )


# ---------------------------------------------------------------------------
# Audit record — the authority-promotion attestation (NOT a flag flip)
# ---------------------------------------------------------------------------


class LearningLiveAuditRecord(BaseModel):
    """Audit of a learning-layer authority promotion.

    This record — NOT a flipped ``Literal[False]`` flag — is how PR7 attests
    that real authority was introduced.  Mirrors how ``workflow_executor``
    records live promotion via its evidence/telemetry surface rather than by
    mutating the frozen config flags.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    execution_mode: LearningLiveExecutionMode = Field(alias="executionMode")
    gate_enabled: bool = Field(alias="gateEnabled")
    readiness_ready: bool = Field(alias="readinessReady")
    promoted_adapters: tuple[str, ...] = Field(alias="promotedAdapters")
    promoted_at: str = Field(alias="promotedAt")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    # Scope identity for PR8 fleet/canary rollout telemetry — never the raw
    # user_id (only a sha256 digest), so audit logs stay PII-free.
    bot_id: str = Field(alias="botId")
    tenant_id: str = Field(alias="tenantId")
    user_id_digest: str = Field(alias="userIdDigest")


# ---------------------------------------------------------------------------
# Live binding — the gated promotion entrypoint (mirrors execute_workflow gate)
# ---------------------------------------------------------------------------


class LearningLiveBinding(BaseModel):
    """Resolved learning-layer adapter binding for a single rollout decision.

    ``mode`` is the readiness-resolved stage.  In ``disabled`` mode the
    local-fake adapters are retained (byte-identical to PR1–PR6) and ``audit`` is
    ``None``.  In ``shadow``/``live`` modes the REAL adapters are bound; in
    ``shadow`` they are observe-only (no recall inject / no store write), in
    ``live`` they are fully bound.  In both shadow and live an ``audit`` record
    is emitted.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    mode: LearningLiveExecutionMode
    transcript_source: TranscriptSource = Field(alias="transcriptSource")
    labeler: Labeler
    recall_live_bound: bool = Field(alias="recallLiveBound")
    write_live_bound: bool = Field(alias="writeLiveBound")
    recall_harness: Any | None = Field(default=None, alias="recallHarness")
    write_harness: Any | None = Field(default=None, alias="writeHarness")
    audit: LearningLiveAuditRecord | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_live_learning_binding(
    *,
    session_store: SessionPersistenceReader,
    learning_store: object | None,
    model_client: LabelerModelClient,
    readiness: LearningLiveReadinessConfig,
    bot_id: str,
    user_id: str,
    app_name: str = "magi",
    tenant_id: str = "local",
) -> LearningLiveBinding:
    """Resolve + bind the learning-layer adapters behind the LIVE gate.

    Mirrors ``execute_workflow``'s gate flow:

    1. Resolve the rollout mode from the readiness gate
       (``learning_live_readiness_health_metadata``), which itself short-circuits
       to ``disabled`` whenever ``MAGI_LEARNING_LIVE_ENABLED`` is OFF.
    2. ``disabled`` → return the LOCAL-FAKE adapters (empty
       ``LocalFakeTranscriptSource`` + ``LocalFakeLabeler``), no audit — this is
       byte-identical to PR1–PR6.
    3. ``shadow`` → bind the REAL transcript source + REAL labeler (observe-only:
       recall/write stay UNBOUND so nothing is injected or written), emit an
       audit recording the shadow promotion.
    4. ``live`` → bind the REAL transcript source + REAL labeler AND the live
       recall + write harnesses, emit an audit recording the live promotion.

    The frozen ``Literal[False]`` authority flags on the reflection executor /
    memory-recall / memory-write configs are NEVER flipped — the promotion is
    recorded ONLY in the returned ``LearningLiveAuditRecord``.
    """
    meta = learning_live_readiness_health_metadata(
        readiness, bot_id=bot_id, user_id=user_id
    )
    mode: LearningLiveExecutionMode = meta["executionMode"]  # type: ignore[assignment]
    reason_codes = tuple(meta["reasonCodes"])  # type: ignore[arg-type]

    if mode == "disabled":
        # Keep local-fake — byte-identical to PR1–PR6.  No real binding, no audit.
        return LearningLiveBinding(
            mode="disabled",
            transcriptSource=LocalFakeTranscriptSource(traces=()),
            labeler=LocalFakeLabeler(),
            recallLiveBound=False,
            writeLiveBound=False,
            recallHarness=None,
            writeHarness=None,
            audit=None,
        )

    # shadow / live → bind the REAL transcript source + REAL labeler.
    real_source = RealTranscriptSource(
        store=session_store, app_name=app_name, user_id=user_id
    )
    real_labeler = LlmBackedLabeler(model_client=model_client)

    is_live = mode == "live"
    promoted: list[str] = ["transcript_source", "labeler"]
    recall_harness = None
    write_harness = None
    if is_live:
        # LIVE: bind the real recall + write harnesses behind the gate.  Authority
        # flags on these harness configs stay frozen-False — the bind is recorded
        # in the audit, not by flipping a flag.
        from magi_agent.harness.memory_recall import build_learning_recall_harness
        from magi_agent.harness.memory_write import MemoryWriteHarness

        recall_harness = build_learning_recall_harness(
            store=learning_store,
            tenant_id=tenant_id,
            enabled=True,
            local_fake_adapter_enabled=True,
        )
        write_harness = MemoryWriteHarness(
            {"enabled": True, "localFakeAdapterEnabled": True}
        )
        promoted.extend(["memory_recall", "memory_write"])

    audit = LearningLiveAuditRecord(
        executionMode=mode,
        gateEnabled=bool(meta["enabled"]),
        readinessReady=bool(meta["readinessReady"]),
        promotedAdapters=tuple(promoted),
        promotedAt=_now_iso(),
        reasonCodes=reason_codes,
        botId=bot_id,
        tenantId=tenant_id,
        userIdDigest=_sha256_text_digest(user_id),
    )

    # PR8 rollout staging telemetry: surface the promotion + the readiness
    # ladder stage as tenant-scoped, PII-free (hashed owner) events.  Both
    # emitters are default-OFF (``MAGI_LEARNING_TELEMETRY_ENABLED``) so a
    # disabled telemetry surface stays byte-quiet; emission NEVER flips a frozen
    # authority flag and NEVER carries a raw user id.
    from magi_agent.learning.telemetry import (
        emit_learning_promotion_event,
        emit_learning_rollout_staging_event,
    )

    emit_learning_promotion_event(audit)
    emit_learning_rollout_staging_event(
        tenant_id=tenant_id,
        bot_id=bot_id,
        execution_mode=mode,
        promoted_gate=int(meta["promotedGate"]),  # type: ignore[arg-type]
        canary_live_gate=int(meta["canaryLiveGate"]),  # type: ignore[arg-type]
        user_id_digest=_sha256_text_digest(user_id),
    )

    return LearningLiveBinding(
        mode=mode,
        transcriptSource=real_source,
        labeler=real_labeler,
        recallLiveBound=is_live,
        writeLiveBound=is_live,
        recallHarness=recall_harness,
        writeHarness=write_harness,
        audit=audit,
    )


__all__ = [
    "LabelerModelClient",
    "LearningLiveAuditRecord",
    "LearningLiveBinding",
    "LlmBackedLabeler",
    "RealTranscriptSource",
    "SessionPersistenceReader",
    "build_live_learning_binding",
]
