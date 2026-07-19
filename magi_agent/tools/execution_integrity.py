"""Live policy adapter for execution-integrity admission and observations.

This is intentionally a narrow adapter around the central tool boundary.  It
does not trust tool arguments as authority: it canonicalizes only a digest,
requires the existing read-ledger authority for workspace mutations, consumes a
durable one-shot attempt identity, and commits pre/post audit records to
the hash-chained SQLite authority journal.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import hmac
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from uuid import uuid4
import secrets

from magi_agent.config.flags import flag_str
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest
from magi_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from magi_agent.execution_authority.journal_sqlite import SQLiteAuthorityJournal


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _canonical_request_digest(name: str, arguments: dict[str, object]) -> str:
    try:
        encoded = json.dumps(
            {"arguments": arguments, "tool": name},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
            default=lambda value: {"type": type(value).__name__},
        )
    except (TypeError, ValueError):
        encoded = json.dumps({"tool": name, "arguments": "unserializable"}, sort_keys=True)
    return _digest(encoded)


def execution_integrity_mode(env: Mapping[str, str] | None = None) -> str:
    raw = (flag_str("MAGI_EXECUTION_INTEGRITY_MODE", env=env) or "").strip().lower()
    return raw if raw in {"off", "audit", "enforce"} else "audit"


@dataclass(frozen=True)
class ExecutionIntegrityDecision:
    mode: str
    effect_capable: bool
    blocked: bool
    error_code: str | None
    reason_codes: tuple[str, ...]
    request_digest: str | None
    attempt_key: str | None
    session_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class ExecutionAdmissionGrant:
    request_digest: str
    attempt_key: str
    effect_class: str
    read_authorized: bool
    grant_digest: str


class ExecutionIntegrityBoundary:
    """Mode-aware preflight/observation adapter used by ``ToolDispatcher``."""

    def __init__(self) -> None:
        self._journal: SQLiteAuthorityJournal | None = None
        self._journal_lock = Lock()
        self._grant_key = secrets.token_bytes(32)

    @staticmethod
    def _effect_capable(manifest: ToolManifest) -> bool:
        return bool(
            manifest.side_effect_class != "none"
            or manifest.mutates_workspace
            or manifest.dangerous
            or manifest.permission in {"write", "execute", "net", "computer"}
        )

    def preflight(
        self,
        manifest: ToolManifest,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        grant: ExecutionAdmissionGrant | None = None,
    ) -> ExecutionIntegrityDecision:
        from magi_agent.execution_authority.journal import JournalConflict  # noqa: PLC0415

        mode = execution_integrity_mode()
        effect_capable = self._effect_capable(manifest)
        if mode == "off" or not effect_capable:
            return ExecutionIntegrityDecision(
                mode,
                effect_capable,
                False,
                None,
                (),
                None,
                None,
                context.session_id,
                context.turn_id,
            )

        request_digest = _canonical_request_digest(manifest.name, arguments)
        attempt_key = self._attempt_key(manifest.name, request_digest, context)
        reasons: list[str] = []

        if grant is None or not self._verify_grant(
            grant=grant,
            manifest=manifest,
            request_digest=request_digest,
            attempt_key=attempt_key,
        ):
            reasons.append("exact_authority_grant_missing_or_invalid")
            return self._deny(
                mode=mode,
                context=context,
                request_digest=request_digest,
                attempt_key=attempt_key,
                error_code="execution_integrity_authority_required",
                reasons=reasons,
            )

        try:
            if self._attempt_seen(context.session_id, attempt_key):
                reasons.append("authority_attempt_already_consumed")
                return self._deny(
                    mode=mode,
                    context=context,
                    request_digest=request_digest,
                    attempt_key=attempt_key,
                    error_code="execution_integrity_authority_consumed",
                    reasons=reasons,
                )
        except Exception:  # noqa: BLE001 - mode decides fail-open vs fail-closed
            reasons.append("authority_journal_unavailable")
            if mode == "enforce":
                return ExecutionIntegrityDecision(
                    mode,
                    True,
                    True,
                    "execution_integrity_journal_unavailable",
                    tuple(reasons),
                    request_digest,
                    attempt_key,
                    context.session_id,
                    context.turn_id,
                )

        if not context.session_id or not context.turn_id or not context.tool_use_id:
            reasons.append("authority_identity_missing")
            if mode == "enforce":
                return ExecutionIntegrityDecision(
                    mode,
                    True,
                    True,
                    "execution_integrity_identity_required",
                    tuple(reasons),
                    request_digest,
                    attempt_key,
                    context.session_id,
                    context.turn_id,
                )

        if manifest.mutates_workspace and not grant.read_authorized:
            reasons.append("fresh_full_read_not_authorized")
            return self._deny(
                mode=mode,
                context=context,
                request_digest=request_digest,
                attempt_key=attempt_key,
                error_code="execution_integrity_read_required",
                reasons=reasons,
            )

        try:
            self._record(
                phase="authority_consumed",
                manifest=manifest,
                context=context,
                request_digest=request_digest,
                attempt_key=attempt_key,
                outcome="admitted",
                reason_codes=tuple(reasons),
            )
        except JournalConflict:
            reasons.append("authority_attempt_already_consumed")
            return self._deny(
                mode=mode,
                context=context,
                request_digest=request_digest,
                attempt_key=attempt_key,
                error_code="execution_integrity_authority_consumed",
                reasons=reasons,
            )
        except Exception:  # noqa: BLE001
            reasons.append("authority_journal_unavailable")
            if mode == "enforce":
                return ExecutionIntegrityDecision(
                    mode,
                    True,
                    True,
                    "execution_integrity_journal_unavailable",
                    tuple(reasons),
                    request_digest,
                    attempt_key,
                    context.session_id,
                    context.turn_id,
                )

        return ExecutionIntegrityDecision(
            mode,
            True,
            False,
            None,
            tuple(reasons),
            request_digest,
            attempt_key,
            context.session_id,
            context.turn_id,
        )

    def issue_grant(
        self,
        manifest: ToolManifest,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        permission_metadata: Mapping[str, object],
    ) -> ExecutionAdmissionGrant:
        """Issue an exact one-call grant after the permission policy allowed it."""

        request_digest = _canonical_request_digest(manifest.name, arguments)
        attempt_key = self._attempt_key(manifest.name, request_digest, context)
        preflight_projection = permission_metadata.get("preflight")
        read_projection = (
            preflight_projection.get("readLedger")
            if isinstance(preflight_projection, Mapping)
            else permission_metadata.get("readLedger")
        )
        read_authorized = not manifest.mutates_workspace or (
            isinstance(read_projection, Mapping) and read_projection.get("status") == "ok"
        )
        material = "|".join(
            (request_digest, attempt_key, manifest.side_effect_class, str(read_authorized))
        )
        return ExecutionAdmissionGrant(
            request_digest=request_digest,
            attempt_key=attempt_key,
            effect_class=manifest.side_effect_class,
            read_authorized=read_authorized,
            grant_digest="sha256:"
            + hmac.digest(self._grant_key, material.encode("utf-8"), "sha256").hex(),
        )

    def _verify_grant(
        self,
        *,
        grant: ExecutionAdmissionGrant,
        manifest: ToolManifest,
        request_digest: str,
        attempt_key: str,
    ) -> bool:
        material = "|".join(
            (
                request_digest,
                attempt_key,
                manifest.side_effect_class,
                str(grant.read_authorized),
            )
        )
        expected = (
            "sha256:" + hmac.digest(self._grant_key, material.encode("utf-8"), "sha256").hex()
        )
        return bool(
            grant.request_digest == request_digest
            and grant.attempt_key == attempt_key
            and grant.effect_class == manifest.side_effect_class
            and hmac.compare_digest(grant.grant_digest, expected)
        )

    @staticmethod
    def _attempt_key(name: str, request_digest: str, context: ToolContext) -> str:
        return _digest(
            "|".join(
                (
                    context.session_id or "unknown-session",
                    context.turn_id or "unknown-turn",
                    context.tool_use_id or "unknown-tool-use",
                    name,
                    request_digest,
                )
            )
        )

    def observe(self, decision: ExecutionIntegrityDecision, result: ToolResult) -> ToolResult:
        if not decision.effect_capable or decision.request_digest is None:
            return result
        reasons = list(decision.reason_codes)
        try:
            # The preflight event already durably names this exact attempt.  The
            # observation event closes it with the backend-visible result only.
            self._record_observation(decision=decision, result=result)
        except Exception:  # noqa: BLE001 - execution already happened
            reasons.append("authority_observation_journal_unavailable")
        metadata = dict(result.metadata)
        metadata["executionIntegrity"] = {
            "mode": decision.mode,
            "requestDigest": decision.request_digest,
            "attemptKey": decision.attempt_key,
            "reasonCodes": reasons,
            "observedStatus": result.status,
        }
        return result.model_copy(update={"metadata": metadata})

    def observe_exception(
        self, decision: ExecutionIntegrityDecision, exception: BaseException
    ) -> None:
        if not decision.effect_capable or decision.request_digest is None:
            return
        try:
            self._record_observation(
                decision=decision,
                result=ToolResult(
                    status="error",
                    errorCode="tool_handler_exception",
                    errorMessage=type(exception).__name__,
                ),
            )
        except Exception:  # noqa: BLE001 - preserve the original handler exception
            return

    def _deny(
        self,
        *,
        mode: str,
        context: ToolContext,
        request_digest: str,
        attempt_key: str,
        error_code: str,
        reasons: list[str],
    ) -> ExecutionIntegrityDecision:
        try:
            self._append(
                phase="denied",
                tool_name="effect-admission",
                context=context,
                request_digest=request_digest,
                attempt_key=attempt_key,
                outcome="denied",
                reason_codes=tuple(reasons),
            )
        except Exception:  # noqa: BLE001 - denial remains valid without telemetry
            pass
        return ExecutionIntegrityDecision(
            mode=mode,
            effect_capable=True,
            blocked=mode == "enforce",
            error_code=error_code if mode == "enforce" else None,
            reason_codes=tuple(reasons),
            request_digest=request_digest,
            attempt_key=attempt_key,
            session_id=context.session_id,
            turn_id=context.turn_id,
        )

    def _attempt_seen(self, session_id: str | None, attempt_key: str) -> bool:
        from magi_agent.execution_authority.journal_integrity import (  # noqa: PLC0415
            ReadPartitionRequest,
        )

        if not session_id:
            return False
        journal = self._journal_instance()
        partition = f"runtime:{session_id}"
        after_sequence = 0
        while True:
            receipt = journal.read_partition(
                ReadPartitionRequest(
                    partitionId=partition, afterSequence=after_sequence, limit=1_000
                )
            )
            for event in receipt.events:
                if event.event_type != "audit.execution_integrity":
                    continue
                payload = json.loads(event.payload_json)
                if (
                    payload.get("attemptKey") == attempt_key
                    and payload.get("phase") == "authority_consumed"
                ):
                    return True
            if not receipt.has_more or not receipt.events:
                return False
            after_sequence = receipt.events[-1].sequence

    def _journal_instance(self) -> SQLiteAuthorityJournal:
        from magi_agent.execution_authority.journal_sqlite import (  # noqa: PLC0415
            SQLiteAuthorityJournal,
        )

        if self._journal is None:
            raw = flag_str("MAGI_EXECUTION_AUTHORITY_DB")
            path = Path(raw) if raw else Path.home() / ".magi" / "execution-authority.db"
            # Tool dispatch is latency-sensitive. Contention is surfaced to the
            # policy (audit fail-open / enforce fail-closed) instead of parking
            # the event loop for the journal adapter's 5s administrative default.
            self._journal = SQLiteAuthorityJournal(path, busy_timeout_ms=100)
        return self._journal

    def _record(
        self,
        *,
        phase: str,
        manifest: ToolManifest,
        context: ToolContext,
        request_digest: str,
        attempt_key: str,
        outcome: str,
        reason_codes: tuple[str, ...],
    ) -> None:
        self._append(
            phase=phase,
            tool_name=manifest.name,
            context=context,
            request_digest=request_digest,
            attempt_key=attempt_key,
            outcome=outcome,
            reason_codes=reason_codes,
        )

    def _record_observation(
        self, *, decision: ExecutionIntegrityDecision, result: ToolResult
    ) -> None:
        # Context-free observation identity remains bound to the request and
        # attempt; no result body is persisted.
        self._append(
            phase="observation",
            tool_name="tool-result",
            context=ToolContext(
                botId="runtime",
                sessionId=decision.session_id,
                turnId=decision.turn_id,
            ),
            request_digest=decision.request_digest or _digest("missing"),
            attempt_key=decision.attempt_key or _digest("missing"),
            outcome=result.status,
            reason_codes=decision.reason_codes,
        )

    def _append(
        self,
        *,
        phase: str,
        tool_name: str,
        context: ToolContext,
        request_digest: str,
        attempt_key: str,
        outcome: str,
        reason_codes: tuple[str, ...],
    ) -> None:
        from magi_agent.execution_authority.envelopes import (  # noqa: PLC0415
            OutboxDraft,
            draft_journal_event,
        )
        from magi_agent.execution_authority.journal_integrity import (  # noqa: PLC0415
            AppendWithOutboxRequest,
            canonical_safe_object_json,
        )

        journal = self._journal_instance()
        partition = f"runtime:{context.session_id or 'local'}"
        task_digest = _digest(partition)
        policy_digest = _digest("execution-integrity-policy-v1")
        identity_digest = _digest(context.bot_id or "runtime")
        payload = {
            "attemptKey": attempt_key,
            "outcome": outcome,
            "phase": phase,
            "reasonCodes": list(reason_codes),
            "toolName": tool_name,
        }
        event_id = (
            f"execution-consumed-{attempt_key.removeprefix('sha256:')}"
            if phase == "authority_consumed"
            else f"execution-integrity-{uuid4().hex}"
        )
        with self._journal_lock:
            head = journal.head(partition)
            draft = draft_journal_event(
                event_id=event_id,
                partition_id=partition,
                event_type="audit.execution_integrity",
                action_id=attempt_key,
                attempt_id=attempt_key,
                task_contract_id=partition,
                task_version=1,
                task_contract_digest=task_digest,
                completion_epoch_id=f"epoch:{context.turn_id or 'local'}",
                admission_sequence=head.sequence,
                authority_contract_id=None,
                request_digest=request_digest,
                idempotency_key_digest=attempt_key,
                fencing_token=0,
                actor_id=context.bot_id or "runtime",
                policy_digest=policy_digest,
                causation_id=context.turn_id or "local-turn",
                correlation_id=context.session_id or "local-session",
                identity_digest=identity_digest,
                payload=payload,
            )
            outbox_json = canonical_safe_object_json({"eventId": event_id, "phase": phase})
            journal.append_with_outbox(
                AppendWithOutboxRequest(
                    draft=draft,
                    outbox=OutboxDraft(
                        outboxId=f"outbox-{uuid4().hex}",
                        partitionId=partition,
                        subjectId=attempt_key,
                        subjectDigest=request_digest,
                        kind="diagnostic_delivery",
                        payloadDigest=_digest(outbox_json),
                        payloadJson=outbox_json,
                    ),
                    expectedJournalHead=head,
                )
            )


def unclosed_execution_attempts(session_id: str, turn_id: str) -> tuple[str, ...] | None:
    """Return open effect attempts, or ``None`` when closure cannot be proven."""

    from magi_agent.execution_authority.journal_integrity import (  # noqa: PLC0415
        ReadPartitionRequest,
    )
    from magi_agent.execution_authority.journal_sqlite import (  # noqa: PLC0415
        SQLiteAuthorityJournal,
    )

    if not session_id or not turn_id:
        return None
    raw = flag_str("MAGI_EXECUTION_AUTHORITY_DB")
    path = Path(raw) if raw else Path.home() / ".magi" / "execution-authority.db"
    if not path.exists():
        return ()
    try:
        journal = SQLiteAuthorityJournal(path, busy_timeout_ms=100)
        partition = f"runtime:{session_id}"
        opened: set[str] = set()
        observed: set[str] = set()
        after_sequence = 0
        while True:
            receipt = journal.read_partition(
                ReadPartitionRequest(
                    partitionId=partition, afterSequence=after_sequence, limit=1_000
                )
            )
            for event in receipt.events:
                if event.causation_id != turn_id or event.event_type != "audit.execution_integrity":
                    continue
                payload = json.loads(event.payload_json)
                attempt = payload.get("attemptKey")
                phase = payload.get("phase")
                if not isinstance(attempt, str):
                    continue
                if phase == "authority_consumed":
                    opened.add(attempt)
                elif phase == "observation":
                    observed.add(attempt)
            if not receipt.has_more or not receipt.events:
                break
            after_sequence = receipt.events[-1].sequence
        return tuple(sorted(opened - observed))
    except Exception:  # noqa: BLE001 - caller chooses audit/enforce semantics
        return None


__all__ = [
    "ExecutionIntegrityBoundary",
    "ExecutionIntegrityDecision",
    "execution_integrity_mode",
    "unclosed_execution_attempts",
]
