from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import Field, ValidationInfo, field_serializer, field_validator, model_validator

from magi_agent.evidence.builtin import ProducerSurface
from magi_agent.evidence.types import (
    EvidenceAgentRole,
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceRecord,
    EvidenceRunOn,
    EvidenceSourceKind,
    _freeze_mapping,
    _reject_empty_optional_string,
    _serialize_mapping,
    _validate_strict_bool,
)
from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.ops.safety import (
    MAX_PUBLIC_TEXT_CHARS as _PUBLIC_SUMMARY_MAX_STRING_LENGTH,
    PUBLIC_CREDENTIAL_KEY_NAMES as _PUBLIC_SUMMARY_SECRET_FIELD_NAMES,
    SECRET_KEY_FRAGMENTS as _SECRET_FIELD_FRAGMENTS,
    is_secret_key as _kernel_is_secret_key,
    redact_secret_tokens as _redact_public_summary_text,
)


# The secret-key grammar, token/KV patterns, and the public-summary text
# redactor now live in the single home magi_agent/ops/safety.py (imported
# above). _redact_public_summary_text is the kernel redactor: it adds the
# session-assignment step and the wider secret-key grammar the old local copy
# lacked (stricter only).
_REDACTED = "[redacted]"
EvidenceLedgerEntryKind = Literal[
    "evidence_record",
    "verifier_verdict",
    "transcript_ref",
    "artifact_ref",
    "control_ref",
    "source_summary",
]
EvidenceLedgerProducerSurface = ProducerSurface | Literal[
    "harness_engine",
    "hook_bus",
    "session_service",
    "task",
    "channel",
    "workspace",
]

# C-10: ``_PUBLIC_SUMMARY_MAX_STRING_LENGTH`` is now re-bound from
# :data:`magi_agent.ops.safety.MAX_PUBLIC_TEXT_CHARS` at import time (see import
# alias above). The local module-level rebind kept the public-summary clip
# identical to the pre-C-10 ``200`` literal but now cannot drift from
# ``harness/verifier_bus.py``'s sibling clip.
_VERIFIER_VERDICT_PAYLOAD_KEYS = frozenset(
    (
        "verdictId",
        "contractId",
        "state",
        "ok",
        "enforcement",
        "missingRequirements",
        "failures",
        "retryMessage",
        "matchedEvidenceRefs",
    )
)


class EvidenceLedgerEntry(FalseOnlyAuthorityModel):
    kind: EvidenceLedgerEntryKind
    sequence: int
    evidence_ref: str = Field(alias="evidenceRef")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    run_on: EvidenceRunOn = Field(alias="runOn")
    agent_role: EvidenceAgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")
    source_kind: EvidenceSourceKind = Field(alias="sourceKind")
    producer_surface: EvidenceLedgerProducerSurface | None = Field(
        default=None,
        alias="producerSurface",
    )
    payload: Mapping[str, object] = Field(default_factory=dict)
    metadata: Mapping[str, object] = Field(default_factory=dict)
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @field_validator(
        "traffic_attached",
        "execution_attached",
        "route_attached",
        mode="before",
    )
    @classmethod
    def _validate_attachment_flags(cls, value: object, info: object) -> object:
        field_name = getattr(info, "field_name", "attachment flag")
        return _validate_strict_bool(value, field_name)

    @field_validator("sequence", "spawn_depth")
    @classmethod
    def _validate_non_negative_ints(cls, value: int, info: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{getattr(info, 'field_name', 'integer metadata')} must be an integer")
        if value < 0:
            raise ValueError(f"{getattr(info, 'field_name', 'integer metadata')} must be non-negative")
        return value

    @field_validator("evidence_ref", "session_id", "turn_id")
    @classmethod
    def _reject_empty_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ledger identifiers must be non-empty")
        return value

    @field_validator("payload")
    @classmethod
    def _sanitize_and_freeze_payload(
        cls,
        value: Mapping[str, object],
        info: ValidationInfo,
    ) -> Mapping[str, object]:
        return _freeze_mapping(
            _sanitize_ledger_entry_payload(value, kind=info.data.get("kind")),
            "metadata",
        )

    @field_validator("metadata")
    @classmethod
    def _freeze_json_metadata(
        cls,
        value: Mapping[str, object],
        info: ValidationInfo,
    ) -> Mapping[str, object]:
        payload = info.data.get("payload")
        if (
            info.data.get("kind") == "source_summary"
            and isinstance(payload, Mapping)
            and "publicSummary" in payload
        ):
            value = _sanitize_public_summary_value(
                value,
                include_public_credential_keys=True,
            )
        return _freeze_mapping(value, "metadata")

    @field_serializer("payload", "metadata")
    def _serialize_json_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}


class EvidenceLedger(FalseOnlyAuthorityModel):
    ledger_id: str = Field(alias="ledgerId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    run_on: EvidenceRunOn = Field(alias="runOn")
    agent_role: EvidenceAgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")
    source_kind: EvidenceSourceKind = Field(alias="sourceKind")
    producer_surface: EvidenceLedgerProducerSurface | None = Field(
        default=None,
        alias="producerSurface",
    )
    entries: tuple[EvidenceLedgerEntry, ...] = ()
    metadata: Mapping[str, object] = Field(default_factory=dict)
    compaction_ref: str | None = Field(default=None, alias="compactionRef")
    replay_ref: str | None = Field(default=None, alias="replayRef")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @field_validator(
        "traffic_attached",
        "execution_attached",
        "route_attached",
        mode="before",
    )
    @classmethod
    def _validate_attachment_flags(cls, value: object, info: object) -> object:
        field_name = getattr(info, "field_name", "attachment flag")
        return _validate_strict_bool(value, field_name)

    @field_validator("ledger_id", "session_id", "turn_id")
    @classmethod
    def _reject_empty_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ledger identifiers must be non-empty")
        return value

    @field_validator("compaction_ref", "replay_ref")
    @classmethod
    def _reject_empty_optional_refs(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "ledger refs")

    @field_validator("spawn_depth")
    @classmethod
    def _validate_spawn_depth(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("spawnDepth must be an integer")
        if value < 0:
            raise ValueError("spawnDepth must be non-negative")
        return value

    @field_validator("metadata")
    @classmethod
    def _freeze_json_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _freeze_mapping(value, "metadata")

    @field_validator("entries")
    @classmethod
    def _revalidate_entries(
        cls,
        value: tuple[EvidenceLedgerEntry, ...],
    ) -> tuple[EvidenceLedgerEntry, ...]:
        return tuple(EvidenceLedgerEntry.model_validate(entry.model_dump()) for entry in value)

    @field_serializer("metadata")
    def _serialize_json_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}

    @model_validator(mode="after")
    def _validate_append_only_entry_order(self) -> Self:
        seen_evidence_record_refs: set[str] = set()
        for expected_sequence, entry in enumerate(self.entries, start=1):
            if entry.sequence != expected_sequence:
                raise ValueError("ledger entries must be sequential and append-only")
            expected_ref = self._evidence_ref_for(expected_sequence, entry.kind)
            if entry.evidence_ref != expected_ref:
                raise ValueError("ledger entry evidence refs must match ledger order")
            if entry.session_id != self.session_id or entry.turn_id != self.turn_id:
                raise ValueError("ledger entries must belong to the same session and turn")
            if entry.kind == "verifier_verdict":
                matched_refs = entry.payload.get("matchedEvidenceRefs", ())
                missing_refs = [
                    ref for ref in matched_refs if ref not in seen_evidence_record_refs
                ]
                if missing_refs:
                    raise ValueError(
                        "matchedEvidenceRefs must reference earlier evidence_record entries"
                    )
            if entry.kind == "evidence_record":
                seen_evidence_record_refs.add(entry.evidence_ref)
        return self

    def append_evidence_record(
        self,
        record: EvidenceRecord,
        *,
        metadata: Mapping[str, object] | None = None,
        producer_surface: EvidenceLedgerProducerSurface | None = None,
    ) -> Self:
        record = EvidenceRecord.model_validate(record.model_dump(by_alias=True))
        record_payload = record.model_dump(by_alias=True)
        preview = record_payload.get("preview")
        if isinstance(preview, str):
            record_payload["preview"] = _sanitize_public_summary_value(preview)
        return self._append(
            kind="evidence_record",
            source_kind=record.source.kind,
            producer_surface=producer_surface or self.producer_surface,
            payload={"record": record_payload},
            metadata=metadata,
        )

    def append_verifier_verdict(
        self,
        verdict: EvidenceContractVerdict,
        *,
        matched_evidence_refs: tuple[str, ...],
        verdict_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        if not verdict_id.strip():
            raise ValueError("verdict_id must be non-empty")
        verdict = EvidenceContractVerdict.model_validate(verdict.model_dump(by_alias=True))
        normalized_refs = tuple(ref.strip() for ref in matched_evidence_refs)
        if any(not ref for ref in normalized_refs):
            raise ValueError("matched_evidence_refs must contain only non-empty refs")
        if len(set(normalized_refs)) != len(normalized_refs):
            raise ValueError("matched_evidence_refs must not contain duplicate refs")
        if not normalized_refs and verdict.matched_evidence:
            raise ValueError("matched_evidence_refs must reference matched evidence records")
        evidence_entries_by_ref = {
            entry.evidence_ref: entry for entry in self.entries if entry.kind == "evidence_record"
        }
        missing_refs = [ref for ref in normalized_refs if ref not in evidence_entries_by_ref]
        if missing_refs:
            raise ValueError("matched_evidence_refs must reference existing evidence_record entries")
        expected_refs = _matched_evidence_refs_for_verdict(
            verdict,
            evidence_entries_by_ref,
        )
        if len(expected_refs) != len(verdict.matched_evidence):
            raise ValueError("matched_evidence_refs must match verdict matched evidence records")
        if normalized_refs and set(normalized_refs) != expected_refs:
            raise ValueError("matched_evidence_refs must match verdict matched evidence records")
        return self._append(
            kind="verifier_verdict",
            source_kind="verifier",
            producer_surface="verifier",
            payload={
                "verdictId": verdict_id,
                "contractId": verdict.contract_id,
                "state": verdict.state,
                "ok": verdict.ok,
                "enforcement": verdict.enforcement,
                "missingRequirements": [
                    requirement.model_dump(by_alias=True)
                    for requirement in verdict.missing_requirements
                ],
                "failures": [
                    _sanitize_verifier_failure_payload(failure)
                    for failure in verdict.failures
                ],
                "retryMessage": (
                    _sanitize_public_summary_value(verdict.retry_message)
                    if verdict.retry_message is not None
                    else None
                ),
                "matchedEvidenceRefs": list(normalized_refs),
            },
            metadata=metadata,
        )

    def append_transcript_ref(
        self,
        transcript_entry_id: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        return self._append_ref(
            kind="transcript_ref",
            ref_field="transcriptEntryId",
            ref_value=transcript_entry_id,
            source_kind="transcript",
            producer_surface="transcript",
            metadata=metadata,
        )

    def append_artifact_ref(
        self,
        artifact_id: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        return self._append_ref(
            kind="artifact_ref",
            ref_field="artifactId",
            ref_value=artifact_id,
            source_kind="artifact",
            producer_surface="artifact_service",
            metadata=metadata,
        )

    def append_control_ref(
        self,
        control_id: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        return self._append_ref(
            kind="control_ref",
            ref_field="controlId",
            ref_value=control_id,
            source_kind="execution_contract",
            producer_surface=self.producer_surface,
            metadata=metadata,
        )

    def append_source_summary(
        self,
        summary_id: str,
        *,
        metadata: Mapping[str, object] | None = None,
        public: bool = False,
    ) -> Self:
        if not summary_id.strip():
            raise ValueError("summary_id must be non-empty")
        payload: dict[str, object] = {"summaryId": summary_id}
        if public:
            payload["publicSummary"] = _truncate_public_strings(
                _redact_mapping(
                    dict(metadata or {}),
                    include_public_credential_keys=True,
                )
            )
            metadata = _sanitize_public_summary_value(
                metadata or {},
                include_public_credential_keys=True,
            )
        return self._append(
            kind="source_summary",
            source_kind=self.source_kind,
            producer_surface=self.producer_surface,
            payload=payload,
            metadata=metadata,
        )

    def _append_ref(
        self,
        *,
        kind: EvidenceLedgerEntryKind,
        ref_field: str,
        ref_value: str,
        source_kind: EvidenceSourceKind,
        producer_surface: EvidenceLedgerProducerSurface | None,
        metadata: Mapping[str, object] | None,
    ) -> Self:
        if not ref_value.strip():
            raise ValueError("ledger ref values must be non-empty")
        return self._append(
            kind=kind,
            source_kind=source_kind,
            producer_surface=producer_surface,
            payload={ref_field: ref_value},
            metadata=metadata,
        )

    def _append(
        self,
        *,
        kind: EvidenceLedgerEntryKind,
        source_kind: EvidenceSourceKind,
        producer_surface: EvidenceLedgerProducerSurface | None,
        payload: Mapping[str, object],
        metadata: Mapping[str, object] | None,
    ) -> Self:
        sequence = len(self.entries) + 1
        entry = EvidenceLedgerEntry(
            kind=kind,
            sequence=sequence,
            evidenceRef=self._evidence_ref_for(sequence, kind),
            sessionId=self.session_id,
            turnId=self.turn_id,
            runOn=self.run_on,
            agentRole=self.agent_role,
            spawnDepth=self.spawn_depth,
            sourceKind=source_kind,
            producerSurface=producer_surface,
            payload=payload,
            metadata=metadata or {},
            trafficAttached=False,
            executionAttached=False,
            routeAttached=False,
        )
        return self.model_copy(update={"entries": (*self.entries, entry)})

    def _evidence_ref_for(self, sequence: int, kind: EvidenceLedgerEntryKind) -> str:
        return f"{self.ledger_id}:{sequence:04d}:{kind}"


def _truncate_public_strings(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _truncate_public_strings(nested) for key, nested in value.items()}
    if isinstance(value, list | tuple):
        return [_truncate_public_strings(item) for item in value]
    if isinstance(value, str) and len(value) > _PUBLIC_SUMMARY_MAX_STRING_LENGTH:
        return f"{value[: _PUBLIC_SUMMARY_MAX_STRING_LENGTH - 3]}..."
    return value


def _redact_mapping(
    value: Mapping[str, object],
    *,
    include_public_credential_keys: bool = False,
) -> dict[str, object]:
    return {
        _validate_public_summary_key(key): _redact_value(
            key,
            nested,
            include_public_credential_keys=include_public_credential_keys,
        )
        for key, nested in value.items()
    }


def _validate_public_summary_key(key: object) -> str:
    if type(key) is not str:
        raise ValueError("metadata mapping keys must be strings")
    return key


def _redact_value(
    key: str,
    value: object,
    *,
    include_public_credential_keys: bool = False,
) -> object:
    if _is_secret_key(
        key,
        include_public_credential_keys=include_public_credential_keys,
    ):
        return _REDACTED
    if isinstance(value, Mapping):
        return _redact_mapping(
            value,
            include_public_credential_keys=include_public_credential_keys,
        )
    if isinstance(value, tuple | list):
        return [
            _redact_value(
                key,
                item,
                include_public_credential_keys=include_public_credential_keys,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _redact_public_summary_text(value)
    return value


def _is_secret_key(
    key: str,
    *,
    include_public_credential_keys: bool = False,
) -> bool:
    return _kernel_is_secret_key(
        key,
        include_public_credential_keys=include_public_credential_keys,
    )


def _sanitize_public_summary_value(
    value: object,
    *,
    include_public_credential_keys: bool = False,
) -> object:
    if isinstance(value, Mapping):
        return _truncate_public_strings(
            _redact_mapping(
                value,
                include_public_credential_keys=include_public_credential_keys,
            )
        )
    if isinstance(value, list | tuple):
        return [
            _sanitize_public_summary_value(
                item,
                include_public_credential_keys=include_public_credential_keys,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _truncate_public_strings(_redact_public_summary_text(value))
    return value


def _sanitize_ledger_entry_payload(
    value: Mapping[str, object],
    *,
    kind: object,
) -> Mapping[str, object]:
    sanitized = _sanitize_public_summary_payload(value)
    if kind == "verifier_verdict":
        sanitized = _sanitize_verifier_verdict_payload(sanitized)
    return sanitized


def _sanitize_public_summary_payload(value: Mapping[str, object]) -> Mapping[str, object]:
    has_public_summary = "publicSummary" in value
    record_value = value.get("record")
    has_record_preview = isinstance(record_value, Mapping) and isinstance(
        record_value.get("preview"),
        str,
    )
    if not has_public_summary and not has_record_preview:
        return value
    sanitized = dict(value)
    if has_public_summary:
        sanitized["publicSummary"] = _sanitize_public_summary_value(
            sanitized["publicSummary"],
            include_public_credential_keys=True,
        )
    if has_record_preview:
        record = dict(record_value)
        record["preview"] = _sanitize_public_summary_value(record["preview"])
        sanitized["record"] = record
    return sanitized


def _sanitize_verifier_verdict_payload(value: Mapping[str, object]) -> Mapping[str, object]:
    if "matchedEvidenceRefs" not in value:
        raise ValueError("matchedEvidenceRefs must be present")
    sanitized = {
        key: _sanitize_verifier_verdict_field(key, nested)
        for key, nested in value.items()
        if key in _VERIFIER_VERDICT_PAYLOAD_KEYS
    }
    sanitized["matchedEvidenceRefs"] = _normalize_matched_evidence_refs(
        value["matchedEvidenceRefs"]
    )
    _validate_replayed_verifier_verdict_invariants(sanitized)
    return sanitized


def _validate_replayed_verifier_verdict_invariants(
    payload: Mapping[str, object],
) -> None:
    matched_refs = payload.get("matchedEvidenceRefs", ())
    state = payload.get("state")
    ok = payload.get("ok")
    failures = payload.get("failures", ())
    missing_requirements = payload.get("missingRequirements", ())

    if not isinstance(ok, bool):
        raise ValueError("verifier verdict ok must be a boolean")
    if not isinstance(state, str):
        raise ValueError("verifier verdict state must be a string")
    if ok is True and not matched_refs:
        raise ValueError("ok verifier verdict matchedEvidenceRefs must not be empty")
    if state == "pass" and not matched_refs:
        raise ValueError("pass verifier verdict matchedEvidenceRefs must not be empty")
    if ok is True and (bool(failures) or bool(missing_requirements)):
        raise ValueError("ok verifier verdict must not include failures or missingRequirements")
    if ok is False and state == "pass":
        raise ValueError("failed verifier verdict must not use pass state")
    if not matched_refs and not bool(failures) and not bool(missing_requirements):
        raise ValueError(
            "no-ref verifier verdict must include failures or missingRequirements"
        )


def _sanitize_verifier_verdict_field(key: str, value: object) -> object:
    if key == "failures" and isinstance(value, list | tuple):
        return [
            _sanitize_verifier_failure_mapping(failure)
            if isinstance(failure, Mapping)
            else _sanitize_public_summary_value(failure)
            for failure in value
        ]
    if key == "matchedEvidenceRefs":
        return _normalize_matched_evidence_refs(value)
    return _sanitize_public_summary_value(value)


def _normalize_matched_evidence_refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("matchedEvidenceRefs must be a list of strings")
    normalized: list[str] = []
    for ref in value:
        if not isinstance(ref, str):
            raise ValueError("matchedEvidenceRefs must contain only strings")
        stripped = ref.strip()
        if not stripped:
            raise ValueError("matchedEvidenceRefs must contain only non-empty strings")
        normalized.append(stripped)
    if len(set(normalized)) != len(normalized):
        raise ValueError("matchedEvidenceRefs must not contain duplicates")
    return tuple(normalized)


def _sanitize_verifier_failure_payload(
    failure: EvidenceContractFailure,
) -> dict[str, object]:
    return _sanitize_verifier_failure_mapping(failure.model_dump(by_alias=True))


def _sanitize_verifier_failure_mapping(
    failure: Mapping[str, object],
) -> dict[str, object]:
    payload = dict(failure)
    message = payload.get("message")
    if isinstance(message, str):
        payload["message"] = _sanitize_public_summary_value(message)
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        payload["metadata"] = _sanitize_public_summary_value(
            metadata,
            include_public_credential_keys=True,
        )
    return payload


def _matched_evidence_refs_for_verdict(
    verdict: EvidenceContractVerdict,
    evidence_entries_by_ref: Mapping[str, EvidenceLedgerEntry],
) -> set[str]:
    expected_refs: set[str] = set()
    for record in verdict.matched_evidence:
        record_payload = _canonical_record_payload(record)
        for evidence_ref, entry in evidence_entries_by_ref.items():
            stored = entry.payload.get("record")
            if isinstance(stored, Mapping) and _canonical_record_payload(stored) == record_payload:
                expected_refs.add(evidence_ref)
                break
    return expected_refs


def _canonical_record_payload(record: EvidenceRecord | Mapping[str, object]) -> dict[str, object]:
    if isinstance(record, EvidenceRecord):
        payload = record.model_dump(by_alias=True, mode="json", warnings=False)
    else:
        payload = EvidenceRecord.model_validate(record).model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )
    preview = payload.get("preview")
    if isinstance(preview, str):
        payload["preview"] = _sanitize_public_summary_value(preview)
    return payload


__all__ = [
    "EvidenceLedger",
    "EvidenceLedgerEntry",
    "EvidenceLedgerEntryKind",
    "EvidenceLedgerProducerSurface",
]
