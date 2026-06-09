from __future__ import annotations

import time
from collections.abc import Mapping

from magi_agent.evidence.extraction import evidence_from_tool_result
from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.types import EvidenceRecord
from magi_agent.tools.result import ToolResult


_PUBLIC_REF_PREFIXES = ("evidence:", "verifier:", "receipt:sha256:", "sha256:")
_RECEIPT_METADATA_KEYS = (
    "toolExecutionReceipt",
    "codingMutationReceipt",
    "codeDiagnosticsReceipt",
    "gate5bFullToolhostReceipt",
    "generalAutomationReceipt",
    "generalAutomationReceiptLedgerEntry",
)
_TEST_COMMAND_PREFIXES = (
    "pytest",
    "python -m pytest",
    "npm test",
    "npm run test",
    "pnpm test",
    "pnpm run test",
    "yarn test",
)
# Cap on how many per-turn EvidenceLedgers ``evidence_ledgers_for_session``
# exposes to the live self-evidence view. The CLI ``tool_context_factory``
# calls it on EVERY tool dispatch and ``ToolContext.freeze_source_ledger``
# deep-freezes the whole returned tuple each time, so returning ALL ledgers is
# O(N^2) over a long session (N turns x M dispatches). Capping to the most
# recent K turns bounds per-dispatch cost while keeping recent cross-turn
# introspection ("did you read X earlier this session"). Older turns are
# deliberately dropped from the live self-evidence view — a lean cap, not an
# audit store.
_MAX_SESSION_LEDGERS = 25


class LocalToolEvidenceCollector:
    """Local-only collector for CLI/dashboard tool receipts.

    The engine consumes a ``Callable[[turn_id], Sequence[object]]``. This class
    keeps that interface while retaining sanitized tool evidence produced by the
    OSS CLI/local dashboard tool path. It does not call providers, mutate hosted
    storage, or grant write authority.
    """

    def __init__(self, *, general_automation_receipts: object | None = None) -> None:
        self._records: dict[tuple[str, str], list[object]] = {}
        self._general_automation_receipts = general_automation_receipts
        # Per-(session_id, turn_id) immutable EvidenceLedgers, built lazily on
        # the first recorded tool result for a key when the lifecycle flag is on.
        # Reused by ``evidence_ledgers_for_session`` to thread onto a
        # ``ToolContext.source_ledger`` so ``InspectSelfEvidence`` projects REAL
        # tool calls. Empty (and never built) when the flag is off.
        self._ledgers: dict[tuple[str, str], EvidenceLedger] = {}

    def record_tool_result(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        result: ToolResult | Mapping[str, object],
        arguments: Mapping[str, object] | None = None,
    ) -> tuple[object, ...]:
        tool_result = (
            result
            if isinstance(result, ToolResult)
            else ToolResult.model_validate(result)
        )
        records: list[object] = []

        explicit = evidence_from_tool_result(
            tool_result,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        if explicit is not None:
            records.append(explicit)

        receipt = None
        if not self._has_canonical_general_automation_entry(
            turn_id=turn_id,
            metadata=tool_result.metadata,
        ):
            receipt = _local_receipt_projection(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=tool_result,
                arguments=arguments or {},
                synthesize_execution_receipt=explicit is None,
            )
        if receipt is not None:
            records.append(receipt)

        if records:
            self._records.setdefault((session_id, turn_id), []).extend(records)

        self._maybe_append_evidence_ledger_record(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            status=tool_result.status,
        )
        return tuple(records)

    def evidence_ledgers_for_session(
        self,
        session_id: str,
    ) -> tuple[EvidenceLedger, ...]:
        """Return the per-(session, turn) EvidenceLedgers built for a session.

        Used by the CLI tool-context factories to populate
        ``ToolContext.source_ledger`` so ``InspectSelfEvidence`` can project the
        REAL tool calls recorded so far. Returns an empty tuple when the
        lifecycle flag is off (no ledgers are ever built) or no tool result has
        been recorded for the session yet.

        Bounded to the most recent ``_MAX_SESSION_LEDGERS`` turns (older turns
        dropped from the live self-evidence view) to keep the per-dispatch cost
        constant — see ``_MAX_SESSION_LEDGERS``. ``self._ledgers`` is a dict
        keyed by ``(session_id, turn_id)`` whose insertion order is the order
        each turn was first recorded; we keep that turn order and slice the
        trailing K, so the result is deterministic and preserves order.
        """
        matching = tuple(
            ledger
            for (stored_session_id, _turn_id), ledger in self._ledgers.items()
            if stored_session_id == session_id
        )
        return matching[-_MAX_SESSION_LEDGERS:]

    def record_phase_reached(
        self,
        session_id: str,
        turn_id: str,
        phase_name: str,
    ) -> None:
        """Record that the turn reached ``phase_name`` as ledger evidence.

        Appends a ``custom:PhaseReached`` ``EvidenceRecord`` into the SAME
        per-``(session, turn)`` EvidenceLedger that Stage 1's tool-trace records
        share, so ``InspectSelfEvidence`` can project the REAL phases the agent
        went through this turn. Flag-gated on
        ``MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED`` (default OFF -> no record,
        byte-identical) and fail-open (never breaks a turn). The record carries
        NO ``toolName`` so the shared tool-call normalizer ignores it; the phase
        projection keys off the ``custom:PhaseReached`` type instead.
        """
        # Fail-open: phase synthesis/append must NEVER break a turn. Mirrors the
        # ``_maybe_append_evidence_ledger_record`` convention.
        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                is_evidence_ledger_lifecycle_enabled,
            )

            if not is_evidence_ledger_lifecycle_enabled():
                return
            if not session_id or not turn_id or not phase_name:
                return
            self._append_turn_record(
                session_id=session_id,
                turn_id=turn_id,
                record=_synthesize_phase_reached_record(phase_name=phase_name),
            )
        except Exception:
            return

    def record_verifier_verdict(
        self,
        session_id: str,
        turn_id: str,
        stage: str,
        result: str,
    ) -> None:
        """Record a verifier verdict for the turn as ledger evidence.

        Appends a ``custom:VerifierVerdict`` ``EvidenceRecord`` into the SAME
        per-``(session, turn)`` EvidenceLedger that Stage 1's tool-trace and
        Stage 3's phase records share, so ``InspectSelfEvidence`` can project
        the REAL verifier verdicts the turn produced. Flag-gated on
        ``MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED`` (default OFF -> no record,
        byte-identical) and fail-open (never breaks a turn).

        Deliberately bypasses ``EvidenceLedger.append_verifier_verdict`` (which
        requires ``matched_evidence_refs`` pointing at evidence_records in the
        same per-turn ledger — the verifier bus produces public ref strings, not
        ledger evidence_refs, so that path would skip-on-mismatch and yield
        near-always-empty verdicts). The record carries NO ``toolName`` so the
        shared tool-call normalizer ignores it; the verdict projection keys off
        the ``custom:VerifierVerdict`` type and reads ``fields.stage`` /
        ``fields.result``.
        """
        # Fail-open: verdict synthesis/append must NEVER break a turn. Mirrors
        # the ``record_phase_reached`` convention.
        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                is_evidence_ledger_lifecycle_enabled,
            )

            if not is_evidence_ledger_lifecycle_enabled():
                return
            if not session_id or not turn_id or not stage or not result:
                return
            self._append_turn_record(
                session_id=session_id,
                turn_id=turn_id,
                record=_synthesize_verifier_verdict_record(
                    stage=stage,
                    result=result,
                ),
            )
        except Exception:
            return

    def _maybe_append_evidence_ledger_record(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
    ) -> None:
        # Fail-open: ledger synthesis/append must NEVER break a tool call. Any
        # failure (flag read, record validation, immutable append) is swallowed
        # and the per-key ledger is simply left untouched, matching the existing
        # ``except Exception`` convention in this file / tool_runtime.py.
        try:
            from magi_agent.config.env import (  # noqa: PLC0415
                is_evidence_ledger_lifecycle_enabled,
            )

            if not is_evidence_ledger_lifecycle_enabled():
                return
            if not session_id or not turn_id or not tool_name:
                return
            self._append_turn_record(
                session_id=session_id,
                turn_id=turn_id,
                record=_synthesize_tool_trace_record(
                    tool_name=tool_name,
                    status=status,
                ),
            )
        except Exception:
            return

    def _append_turn_record(
        self,
        *,
        session_id: str,
        turn_id: str,
        record: EvidenceRecord,
    ) -> None:
        # Shared lazy-ledger construction: phase markers and tool traces append
        # into ONE single-turn ledger per ``(session, turn)`` so the contiguous
        # append-only sequence + single-turn constraint hold across both kinds.
        # Immutable reassign (``append_evidence_record`` returns a fresh ledger).
        key = (session_id, turn_id)
        ledger = self._ledgers.get(key) or _new_tool_trace_ledger(
            session_id=session_id,
            turn_id=turn_id,
        )
        self._ledgers[key] = ledger.append_evidence_record(record)

    def collect_for_turn(self, turn_id: str) -> tuple[object, ...]:
        local = tuple(
            record
            for (_session_id, stored_turn_id), records in self._records.items()
            if stored_turn_id == turn_id
            for record in records
        )
        external = self._general_automation_entries_for_turn(turn_id)
        return (*local, *external)

    def __call__(self, turn_id: str) -> tuple[object, ...]:
        return self.collect_for_turn(turn_id)

    def _general_automation_entries_for_turn(self, turn_id: str) -> tuple[object, ...]:
        entries_for_turn = getattr(
            self._general_automation_receipts,
            "entries_for_turn",
            None,
        )
        if not callable(entries_for_turn):
            return ()
        try:
            return tuple(entries_for_turn(turn_id))
        except Exception:
            return ()

    def _has_canonical_general_automation_entry(
        self,
        *,
        turn_id: str,
        metadata: Mapping[str, object],
    ) -> bool:
        if not any(
            key in metadata
            for key in ("generalAutomationReceipt", "generalAutomationReceiptLedgerEntry")
        ):
            return False
        return bool(self._general_automation_entries_for_turn(turn_id))


def _local_receipt_projection(
    *,
    session_id: str,
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    result: ToolResult,
    arguments: Mapping[str, object],
    synthesize_execution_receipt: bool,
) -> dict[str, object] | None:
    metadata: Mapping[str, object] = result.metadata
    if result.coding_mutation_receipt is not None and "codingMutationReceipt" not in metadata:
        metadata = {
            **result.metadata,
            "codingMutationReceipt": result.coding_mutation_receipt,
        }
    receipts = _receipt_projections(metadata)
    execution_receipt = receipts.get("toolExecutionReceipt")
    if (
        synthesize_execution_receipt
        and (not isinstance(execution_receipt, Mapping) or not execution_receipt)
    ):
        receipts["toolExecutionReceipt"] = _tool_execution_receipt(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
            arguments=arguments,
        )
    refs = set(_top_level_metadata_refs(metadata))
    refs.update(_public_refs(result.artifact_refs))
    refs.update(_public_refs(result.file_refs))
    refs.update(_public_refs(result.delivery_receipts))
    refs.update(_public_refs(receipts))
    refs.update(_inferred_refs(tool_name=tool_name, result=result, arguments=arguments))
    if not refs and not receipts:
        return None

    evidence_refs = sorted(ref for ref in refs if ref.startswith("evidence:"))
    validator_refs = sorted(ref for ref in refs if ref.startswith("verifier:"))
    receipt_refs = sorted(
        ref for ref in refs if ref.startswith(("receipt:sha256:", "sha256:"))
    )
    return {
        "schemaVersion": "openmagi.localToolEvidenceReceipt.v1",
        "sessionId": session_id,
        "turnId": turn_id,
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "status": result.status,
        "metadataOnly": True,
        "trafficAttached": False,
        "executionAttached": True,
        "productionWriteAllowed": False,
        "evidenceRefs": evidence_refs,
        "validatorRefs": validator_refs,
        "receiptRefs": receipt_refs,
        "receipts": receipts,
    }


def _receipt_projections(metadata: Mapping[str, object]) -> dict[str, object]:
    receipts: dict[str, object] = {}
    for key in _RECEIPT_METADATA_KEYS:
        value = metadata.get(key)
        if value is None:
            continue
        receipts[key] = _receipt_value(value)
    return receipts


def _tool_execution_receipt(
    *,
    tool_call_id: str,
    tool_name: str,
    result: ToolResult,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schemaVersion": "openmagi.localToolExecutionReceipt.v1",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "status": result.status,
        "argumentKeys": sorted(
            str(key) for key in arguments if _public_receipt_key(str(key))
        ),
    }


def _top_level_metadata_refs(metadata: Mapping[str, object]) -> tuple[str, ...]:
    candidates: list[object] = []
    for key in (
        "evidenceRef",
        "evidenceRefs",
        "validatorRef",
        "validatorRefs",
        "receiptRef",
        "receiptRefs",
    ):
        if key in metadata:
            candidates.append(metadata[key])
    return _public_refs(candidates)


def _receipt_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _receipt_value(nested)
            for key, nested in value.items()
            if _public_receipt_key(str(key))
        }
    if isinstance(value, list | tuple):
        return [_receipt_value(item) for item in value]
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)[:240]


def _public_receipt_key(key: str) -> bool:
    normalized = key.replace("_", "").replace("-", "").lower()
    if normalized.startswith("raw"):
        return False
    if any(term in normalized for term in ("authorization", "cookie", "secret", "token")):
        return False
    if normalized in {"output", "content", "log", "logs", "stdout", "stderr"}:
        return False
    return True


def _public_refs(value: object) -> tuple[str, ...]:
    refs: list[str] = []
    _collect_public_refs(value, refs, depth=0)
    return tuple(dict.fromkeys(refs))


def _collect_public_refs(value: object, refs: list[str], *, depth: int) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        if value.startswith(_PUBLIC_REF_PREFIXES):
            refs.append(value)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_public_refs(nested, refs, depth=depth + 1)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for nested in value:
            _collect_public_refs(nested, refs, depth=depth + 1)


def _inferred_refs(
    *,
    tool_name: str,
    result: ToolResult,
    arguments: Mapping[str, object],
) -> tuple[str, ...]:
    if result.status != "ok":
        return ()
    refs: list[str] = []
    if tool_name == "GitDiff":
        refs.append("evidence:git-diff")
    if tool_name in {"Bash", "SafeCommand", "TestRun"} and _is_test_command(
        _command_from(arguments, result)
    ):
        refs.append("evidence:test-run")
        refs.append("verifier:dev-coding:test-evidence")
    return tuple(refs)


def _command_from(arguments: Mapping[str, object], result: ToolResult) -> str | None:
    for key in ("command", "cmd", "shellCommand", "shell_command"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    fields = result.metadata.get("fields")
    if isinstance(fields, Mapping):
        value = fields.get("command")
        if isinstance(value, str):
            return value
    return None


def _is_test_command(command: str | None) -> bool:
    if command is None:
        return False
    normalized = " ".join(command.strip().split()).lower()
    return any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in _TEST_COMMAND_PREFIXES
    )


# EvidenceStatus is Literal["ok", "failed", "unknown"]; ToolResult.status is
# Literal["ok", "error", "blocked", "needs_approval"]. Map onto the evidence
# vocabulary so the downstream ``normalize_tool_status`` consumer
# (introspection/mapping.py) projects the right canonical token: "ok"->"ok",
# "error"->"failed"(->"error"), everything else -> "unknown".
_TOOL_STATUS_TO_EVIDENCE_STATUS: Mapping[str, str] = {
    "ok": "ok",
    "error": "failed",
}


def _new_tool_trace_ledger(*, session_id: str, turn_id: str) -> EvidenceLedger:
    return EvidenceLedger.model_validate(
        {
            "ledgerId": f"{session_id}:{turn_id}:evidence",
            "sessionId": session_id,
            "turnId": turn_id,
            "runOn": "main",
            "agentRole": "general",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
            "metadata": {},
        }
    )


def _synthesize_tool_trace_record(*, tool_name: str, status: str) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "custom:ToolTrace",
            "status": _TOOL_STATUS_TO_EVIDENCE_STATUS.get(status, "unknown"),
            "observedAt": time.time(),
            "source": {"kind": "tool_trace", "toolName": tool_name},
        }
    )


def _synthesize_phase_reached_record(*, phase_name: str) -> EvidenceRecord:
    # "phase"/"runtime" are NOT valid EvidenceSourceKind values; reuse the
    # "tool_trace" kind (the only local-origin source kind) but deliberately set
    # NO ``toolName`` so the shared ``tool_call_from_evidence_record`` normalizer
    # skips it. The phase projection discriminates on the ``custom:PhaseReached``
    # type and reads ``fields.phaseName`` / ``fields.reached``.
    return EvidenceRecord.model_validate(
        {
            "type": "custom:PhaseReached",
            "status": "ok",
            "observedAt": time.time(),
            "source": {"kind": "tool_trace"},
            "fields": {"phaseName": phase_name, "reached": True},
        }
    )


def _synthesize_verifier_verdict_record(*, stage: str, result: str) -> EvidenceRecord:
    # Mirror the phase-reached pattern: "verifier"/"runtime" are NOT valid
    # EvidenceSourceKind values; reuse the "tool_trace" kind (the only
    # local-origin source kind) but deliberately set NO ``toolName`` so the
    # shared ``tool_call_from_evidence_record`` normalizer skips it. The verdict
    # projection discriminates on the ``custom:VerifierVerdict`` type and reads
    # ``fields.stage`` / ``fields.result``.
    return EvidenceRecord.model_validate(
        {
            "type": "custom:VerifierVerdict",
            "status": "ok",
            "observedAt": time.time(),
            "source": {"kind": "tool_trace"},
            "fields": {"stage": stage, "result": result},
        }
    )


__all__ = ["LocalToolEvidenceCollector"]
