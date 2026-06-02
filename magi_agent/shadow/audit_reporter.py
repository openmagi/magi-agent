from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.reports import (
    PublicEvidenceRecordReport,
    PublicEvidenceVerdictReport,
    public_evidence_metadata_report,
    public_evidence_record_report,
    public_evidence_verdict_report,
)
from magi_agent.evidence.types import EvidenceContractVerdict, EvidenceRecord


_AUDIT_REPORT_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)


class _Gate2AuditEvidenceModel(BaseModel):
    model_config = _AUDIT_REPORT_MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate2AuditEvidenceOutputFlags(_Gate2AuditEvidenceModel):
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")
    production_attached: Literal[False] = Field(default=False, alias="productionAttached")
    user_visible: Literal[False] = Field(default=False, alias="userVisible")
    production_transcript_append: Literal[False] = Field(
        default=False,
        alias="productionTranscriptAppend",
    )
    network_sse: Literal[False] = Field(default=False, alias="networkSse")
    block_mode_enabled_for_live_traffic: Literal[False] = Field(
        default=False,
        alias="blockModeEnabledForLiveTraffic",
    )

    @field_serializer(
        "traffic_attached",
        "execution_attached",
        "route_attached",
        "canary_attached",
        "production_attached",
        "user_visible",
        "production_transcript_append",
        "network_sse",
        "block_mode_enabled_for_live_traffic",
    )
    def _serialize_false_flag(self, value: object) -> bool:
        return False

    @model_validator(mode="before")
    @classmethod
    def _reject_non_false_flags(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        for flag_value in value.values():
            if flag_value is not False:
                raise ValueError("Gate 2 audit evidence output flags must be JSON false")
        return value

    @field_validator(
        "traffic_attached",
        "execution_attached",
        "route_attached",
        "canary_attached",
        "production_attached",
        "user_visible",
        "production_transcript_append",
        "network_sse",
        "block_mode_enabled_for_live_traffic",
        mode="before",
    )
    @classmethod
    def _reject_non_false_flag(cls, value: object) -> object:
        if value is not False:
            raise ValueError("Gate 2 audit evidence output flags must be JSON false")
        return value


class Gate2AuditBlockReadiness(_Gate2AuditEvidenceModel):
    block_ready: bool = Field(default=False, alias="blockReady")
    enforcements: tuple[Literal["block_final_answer"], ...] = ()


class Gate2AuditVerifierEntryReport(_Gate2AuditEvidenceModel):
    evidence_ref: str = Field(alias="evidenceRef")
    verdict_id: str | None = Field(default=None, alias="verdictId")
    contract_id: str | None = Field(default=None, alias="contractId")
    state: str | None = None
    enforcement: str | None = None
    ok: bool | None = None
    matched_evidence_refs: tuple[str, ...] = Field(
        default=(),
        alias="matchedEvidenceRefs",
    )
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _sanitize_metadata(cls, value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError("verifier entry metadata must be a mapping")
        return public_evidence_metadata_report(value)


class Gate2AuditEvidenceReport(_Gate2AuditEvidenceModel):
    posture: Literal["diagnostic_non_authoritative"] = "diagnostic_non_authoritative"
    scope: Literal["local_fixture_only"] = "local_fixture_only"
    authority: Literal["audit_only"] = "audit_only"
    ledger_id: str = Field(alias="ledgerId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    run_on: str = Field(alias="runOn")
    agent_role: str = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")
    output_flags: Gate2AuditEvidenceOutputFlags = Field(
        default_factory=Gate2AuditEvidenceOutputFlags,
        alias="outputFlags",
    )
    evidence_records: tuple[PublicEvidenceRecordReport, ...] = Field(
        default=(),
        alias="evidenceRecords",
    )
    verifier_entries: tuple[Gate2AuditVerifierEntryReport, ...] = Field(
        default=(),
        alias="verifierEntries",
    )
    verifier_verdicts: tuple[PublicEvidenceVerdictReport, ...] = Field(
        default=(),
        alias="verifierVerdicts",
    )
    block_readiness: Gate2AuditBlockReadiness = Field(
        default_factory=Gate2AuditBlockReadiness,
        alias="blockReadiness",
    )
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )

    @field_validator("output_flags", mode="before")
    @classmethod
    def _force_local_only_output_flags(cls, value: object) -> Gate2AuditEvidenceOutputFlags:
        if value is None:
            return Gate2AuditEvidenceOutputFlags()
        return Gate2AuditEvidenceOutputFlags.model_validate(value)

    @field_serializer("output_flags")
    def _serialize_output_flags(self, value: object) -> dict[str, bool]:
        return Gate2AuditEvidenceOutputFlags().model_dump(by_alias=True, mode="python")

    @field_validator("diagnostic_metadata", mode="before")
    @classmethod
    def _sanitize_diagnostic_metadata(cls, value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError("diagnostic metadata must be a mapping")
        return public_evidence_metadata_report(value)


def build_gate2_audit_evidence_report(
    ledger: EvidenceLedger,
    *,
    verifier_verdicts: Mapping[str, EvidenceContractVerdict] | None = None,
    diagnostic_metadata: Mapping[str, object] | None = None,
) -> Gate2AuditEvidenceReport:
    safe_ledger = EvidenceLedger.model_validate(ledger.model_dump(by_alias=True))
    verdicts_by_id = _verdicts_by_id(verifier_verdicts)
    public_records: list[PublicEvidenceRecordReport] = []
    verifier_entries: list[Gate2AuditVerifierEntryReport] = []
    public_verdicts: list[PublicEvidenceVerdictReport] = []

    for entry in safe_ledger.entries:
        if entry.kind == "evidence_record":
            record_payload = entry.payload.get("record")
            if isinstance(record_payload, Mapping):
                public_records.append(
                    public_evidence_record_report(EvidenceRecord.model_validate(record_payload))
                )
            continue
        if entry.kind != "verifier_verdict":
            continue

        verdict_id = _optional_string(entry.payload.get("verdictId"))
        verifier_entries.append(
            Gate2AuditVerifierEntryReport(
                evidenceRef=entry.evidence_ref,
                verdictId=verdict_id,
                contractId=_optional_string(entry.payload.get("contractId")),
                state=_optional_string(entry.payload.get("state")),
                enforcement=_optional_string(entry.payload.get("enforcement")),
                ok=_optional_bool(entry.payload.get("ok")),
                matchedEvidenceRefs=_string_tuple(entry.payload.get("matchedEvidenceRefs")),
                metadata=entry.metadata,
            )
        )
        if verdict_id is not None and verdict_id in verdicts_by_id:
            public_verdicts.append(public_evidence_verdict_report(verdicts_by_id[verdict_id]))

    return Gate2AuditEvidenceReport(
        ledgerId=safe_ledger.ledger_id,
        sessionId=safe_ledger.session_id,
        turnId=safe_ledger.turn_id,
        runOn=safe_ledger.run_on,
        agentRole=safe_ledger.agent_role,
        spawnDepth=safe_ledger.spawn_depth,
        outputFlags=Gate2AuditEvidenceOutputFlags(),
        evidenceRecords=tuple(public_records),
        verifierEntries=tuple(verifier_entries),
        verifierVerdicts=tuple(public_verdicts),
        blockReadiness=_block_readiness(verifier_entries, public_verdicts),
        diagnosticMetadata=diagnostic_metadata or {},
    )


def _verdicts_by_id(
    verdicts: Mapping[str, EvidenceContractVerdict] | None,
) -> dict[str, EvidenceContractVerdict]:
    if verdicts is None:
        return {}
    if not isinstance(verdicts, Mapping):
        raise ValueError("verifier_verdicts must be a mapping from verdict id to verdict")
    return {
        verdict_id: EvidenceContractVerdict.model_validate(
            verdict.model_dump(by_alias=True)
        )
        for verdict_id, verdict in verdicts.items()
    }


def _block_readiness(
    verifier_entries: Iterable[Gate2AuditVerifierEntryReport],
    public_verdicts: Iterable[PublicEvidenceVerdictReport],
) -> Gate2AuditBlockReadiness:
    enforcements = {
        "block_final_answer"
        for verdict in public_verdicts
        if verdict.enforcement == "block_final_answer"
    }
    block_ready = any(verdict.state == "block_ready" for verdict in public_verdicts)
    block_ready = block_ready or any(entry.state == "block_ready" for entry in verifier_entries)
    return Gate2AuditBlockReadiness(
        blockReady=block_ready,
        enforcements=tuple(sorted(enforcements)),
    )


def _optional_string(value: object) -> str | None:
    return value if type(value) is str else None


def _optional_bool(value: object) -> bool | None:
    return value if type(value) is bool else None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple | list):
        return ()
    return tuple(item for item in value if type(item) is str)


__all__ = [
    "Gate2AuditBlockReadiness",
    "Gate2AuditEvidenceOutputFlags",
    "Gate2AuditEvidenceReport",
    "Gate2AuditVerifierEntryReport",
    "build_gate2_audit_evidence_report",
]
