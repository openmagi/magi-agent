from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from openmagi_core_agent.harness.verifier_bus import VerifierResultMetadata
from openmagi_core_agent.meta_orchestration.final_assembly import MetaFinalAssemblyPlan
from openmagi_core_agent.meta_orchestration.task_plan import (
    _copy_update_alias,
    _validate_public_ref,
    _validate_ref_tuple,
)


MetaVerifierChainResult = Literal["passed", "blocked"]

_BEFORE_COMMIT_VERDICT_TOKEN = object()
_RUNTIME_VERIFIER_RESULT_TOKEN = object()

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class _MetaBeforeCommitModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for meta beforeCommit contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class MetaBeforeCommitVerdict(_MetaBeforeCommitModel):
    verdict_id: str = Field(alias="verdictId")
    assembly_id: str = Field(alias="assemblyId")
    assembly_digest: str = Field(alias="assemblyDigest")
    verifier_chain_result: MetaVerifierChainResult = Field(alias="verifierChainResult")
    verifier_result_refs: tuple[str, ...] = Field(alias="verifierResultRefs")
    blocked_reasons: tuple[str, ...] = Field(alias="blockedReasons")
    retryable_reasons: tuple[str, ...] = Field(alias="retryableReasons")
    final_projection_eligible: bool = Field(alias="finalProjectionEligible")
    commit_intent_refs: tuple[str, ...] = Field(default=(), alias="commitIntentRefs")
    commit_executed: Literal[False] = Field(default=False, alias="commitExecuted")
    transcript_written: Literal[False] = Field(default=False, alias="transcriptWritten")
    sse_written: Literal[False] = Field(default=False, alias="sseWritten")
    control_written: Literal[False] = Field(default=False, alias="controlWritten")
    tool_execution_attached: Literal[False] = Field(
        default=False,
        alias="toolExecutionAttached",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    _canonical_payload_digest: str = PrivateAttr(default="")

    def __init__(self, **data: Any) -> None:
        token = data.pop("_before_commit_verdict_token", None)
        if token is not _BEFORE_COMMIT_VERDICT_TOKEN:
            raise TypeError("beforeCommit verdicts must be produced by the harness adapter")
        super().__init__(**data)
        object.__setattr__(self, "_canonical_payload_digest", _verdict_payload_digest(self))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        if update:
            raise TypeError("beforeCommit verdicts cannot be updated after evaluation")
        return self

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: str | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        _ = obj, strict, extra, from_attributes, context, by_alias, by_name
        raise TypeError("beforeCommit verdicts must be produced by the harness adapter")

    @classmethod
    def _from_evaluation(
        cls,
        *,
        verdict_id: str,
        assembly: MetaFinalAssemblyPlan,
        verifier_result_refs: Sequence[str],
        blocked_reasons: Sequence[str],
        retryable_reasons: Sequence[str],
        final_projection_eligible: bool,
    ) -> Self:
        parsed_blocked = _validate_ref_tuple(blocked_reasons, "blockedReasons")
        parsed_retryable = _validate_ref_tuple(retryable_reasons, "retryableReasons")
        eligible = final_projection_eligible and not parsed_blocked
        return cls(
            _before_commit_verdict_token=_BEFORE_COMMIT_VERDICT_TOKEN,
            verdictId=_validate_public_ref(verdict_id, "verdictId"),
            assemblyId=assembly.assembly_id,
            assemblyDigest=assembly.final_output_digest,
            verifierChainResult="passed" if eligible else "blocked",
            verifierResultRefs=_validate_ref_tuple(verifier_result_refs, "verifierResultRefs"),
            blockedReasons=parsed_blocked,
            retryableReasons=parsed_retryable,
            finalProjectionEligible=eligible,
            commitIntentRefs=(),
            commitExecuted=False,
            transcriptWritten=False,
            sseWritten=False,
            controlWritten=False,
            toolExecutionAttached=False,
            defaultOff=True,
        )

    @field_validator("verdict_id", "assembly_id", "assembly_digest")
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        clean = _validate_public_ref(value, info.field_name)
        if info.field_name == "assembly_digest" and (
            not clean.startswith("sha256:") or len(clean) != len("sha256:") + 64
        ):
            raise ValueError("assemblyDigest must be a sha256 digest ref")
        return clean

    @field_validator(
        "verifier_result_refs",
        "blocked_reasons",
        "retryable_reasons",
        "commit_intent_refs",
    )
    @classmethod
    def _validate_refs(cls, value: Sequence[str], info: Any) -> tuple[str, ...]:
        return _validate_ref_tuple(value, info.field_name)

    @field_validator(
        "commit_executed",
        "transcript_written",
        "sse_written",
        "control_written",
        "tool_execution_attached",
        mode="before",
    )
    @classmethod
    def _validate_false_only_fields(cls, value: object) -> object:
        if value is not False:
            raise ValueError("beforeCommit adapter side-effect flags must remain false")
        return value

    @field_validator("default_off", mode="before")
    @classmethod
    def _validate_default_off(cls, value: object) -> object:
        if value is not True:
            raise ValueError("defaultOff must remain true")
        return value

    def public_projection(self) -> dict[str, object]:
        if self._canonical_payload_digest != _verdict_payload_digest(self):
            raise ValueError("beforeCommit verdict was mutated after evaluation")
        return {
            "verdictId": self.verdict_id,
            "assemblyId": self.assembly_id,
            "assemblyDigest": self.assembly_digest,
            "verifierChainResult": self.verifier_chain_result,
            "verifierResultRefCount": len(self.verifier_result_refs),
            "blockedReasons": self.blocked_reasons,
            "retryableReasons": self.retryable_reasons,
            "finalProjectionEligible": self.final_projection_eligible,
            "commitIntentRefCount": len(self.commit_intent_refs),
            "commitExecuted": self.commit_executed,
            "transcriptWritten": self.transcript_written,
            "sseWritten": self.sse_written,
            "controlWritten": self.control_written,
            "toolExecutionAttached": self.tool_execution_attached,
            "defaultOff": self.default_off,
        }


class RuntimeIssuedMetaVerifierResult(_MetaBeforeCommitModel):
    result: VerifierResultMetadata
    assembly_id: str = Field(alias="assemblyId")
    assembly_digest: str = Field(alias="assemblyDigest")
    verifier_bus_run_id: str = Field(alias="verifierBusRunId")
    policy_snapshot_id: str = Field(alias="policySnapshotId")
    issuer: Literal["openmagi-verifier-bus"] = "openmagi-verifier-bus"
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    _canonical_payload_digest: str = PrivateAttr(default="")

    def __init__(self, **data: Any) -> None:
        token = data.pop("_runtime_verifier_result_token", None)
        if token is not _RUNTIME_VERIFIER_RESULT_TOKEN:
            raise TypeError("verifier results must be issued by the verifier bus adapter")
        super().__init__(**data)
        object.__setattr__(self, "_canonical_payload_digest", _runtime_result_payload_digest(self))

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: str | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        _ = obj, strict, extra, from_attributes, context, by_alias, by_name
        raise TypeError("verifier results must be issued by the verifier bus adapter")

    @classmethod
    def _from_runtime(
        cls,
        *,
        assembly: MetaFinalAssemblyPlan,
        result: VerifierResultMetadata,
        verifier_bus_run_id: str,
        policy_snapshot_id: str,
    ) -> Self:
        parsed_result = _revalidate_verifier_result(result)
        return cls(
            _runtime_verifier_result_token=_RUNTIME_VERIFIER_RESULT_TOKEN,
            result=parsed_result,
            assemblyId=assembly.assembly_id,
            assemblyDigest=assembly.final_output_digest,
            verifierBusRunId=_validate_public_ref(verifier_bus_run_id, "verifierBusRunId"),
            policySnapshotId=_validate_public_ref(policy_snapshot_id, "policySnapshotId"),
            issuer="openmagi-verifier-bus",
            metadataOnly=True,
            trafficAttached=False,
            executionAttached=False,
        )

    @field_validator("result")
    @classmethod
    def _validate_result(cls, value: VerifierResultMetadata) -> VerifierResultMetadata:
        return _revalidate_verifier_result(value)

    @field_validator(
        "assembly_id",
        "assembly_digest",
        "verifier_bus_run_id",
        "policy_snapshot_id",
    )
    @classmethod
    def _validate_binding_refs(cls, value: str, info: Any) -> str:
        clean = _validate_public_ref(value, info.field_name)
        if info.field_name == "assembly_digest" and (
            not clean.startswith("sha256:") or len(clean) != len("sha256:") + 64
        ):
            raise ValueError("assemblyDigest must be a sha256 digest ref")
        return clean

    @field_validator("metadata_only", mode="before")
    @classmethod
    def _validate_metadata_only(cls, value: object) -> object:
        if value is not True:
            raise ValueError("metadataOnly must remain true")
        return value

    @field_validator("traffic_attached", "execution_attached", mode="before")
    @classmethod
    def _validate_no_runtime_attachment(cls, value: object) -> object:
        if value is not False:
            raise ValueError("runtime verifier result attachment flags must remain false")
        return value

    def result_for_assembly(self, assembly: MetaFinalAssemblyPlan) -> VerifierResultMetadata:
        if self._canonical_payload_digest != _runtime_result_payload_digest(self):
            raise ValueError("runtime-issued verifier result was mutated after issuance")
        if self.assembly_id != assembly.assembly_id:
            raise ValueError("verifier result is not bound to the current assembly")
        if self.assembly_digest != assembly.final_output_digest:
            raise ValueError("verifier result digest does not match the current assembly")
        if self.metadata_only is not True or self.traffic_attached is not False:
            raise ValueError("verifier result must remain metadata-only")
        if self.execution_attached is not False:
            raise ValueError("verifier result must not attach execution")
        return _revalidate_verifier_result(self.result)


def evaluate_before_commit_for_assembly(
    verdict_id: str,
    assembly: MetaFinalAssemblyPlan,
    *,
    verifier_results: Sequence[RuntimeIssuedMetaVerifierResult],
) -> MetaBeforeCommitVerdict:
    parsed_assembly = _parse_assembly(assembly)
    parsed_results = _parse_verifier_results(parsed_assembly, verifier_results)
    verifier_result_refs = tuple(_verifier_result_ref(result) for result in parsed_results)
    blocked_reasons = _blocked_reasons(parsed_assembly, parsed_results)
    retryable_reasons = _retryable_reasons(parsed_results)
    final_projection_eligible = (
        parsed_assembly.projection_mode == "ready_for_projection"
        and bool(parsed_assembly.required_verifier_refs)
        and not blocked_reasons
    )

    return MetaBeforeCommitVerdict._from_evaluation(
        verdict_id=verdict_id,
        assembly=parsed_assembly,
        verifier_result_refs=verifier_result_refs,
        blocked_reasons=blocked_reasons,
        retryable_reasons=retryable_reasons,
        final_projection_eligible=final_projection_eligible,
    )


def issue_runtime_verifier_result_for_assembly(
    assembly: MetaFinalAssemblyPlan,
    result: VerifierResultMetadata,
    *,
    verifier_bus_run_id: str,
    policy_snapshot_id: str,
) -> RuntimeIssuedMetaVerifierResult:
    parsed_assembly = _parse_assembly(assembly)
    return RuntimeIssuedMetaVerifierResult._from_runtime(
        assembly=parsed_assembly,
        result=result,
        verifier_bus_run_id=verifier_bus_run_id,
        policy_snapshot_id=policy_snapshot_id,
    )


def _parse_assembly(assembly: MetaFinalAssemblyPlan) -> MetaFinalAssemblyPlan:
    if not isinstance(assembly, MetaFinalAssemblyPlan):
        raise ValueError("beforeCommit adapter requires a MetaFinalAssemblyPlan")
    assembly.public_projection()
    return assembly


def _parse_verifier_results(
    assembly: MetaFinalAssemblyPlan,
    verifier_results: Sequence[RuntimeIssuedMetaVerifierResult],
) -> tuple[VerifierResultMetadata, ...]:
    parsed: list[VerifierResultMetadata] = []
    for result in verifier_results:
        if not isinstance(result, RuntimeIssuedMetaVerifierResult):
            raise ValueError("verifier results must be runtime-issued metadata envelopes")
        parsed_result = result.result_for_assembly(assembly)
        _validate_public_ref(parsed_result.verifier_id, "verifierId")
        parsed.append(parsed_result)
    verifier_ids = tuple(result.verifier_id for result in parsed)
    if len(set(verifier_ids)) != len(verifier_ids):
        raise ValueError("verifier results must not contain duplicate verifierIds")
    return tuple(parsed)


def _revalidate_verifier_result(result: VerifierResultMetadata) -> VerifierResultMetadata:
    if not isinstance(result, VerifierResultMetadata):
        raise ValueError("verifier result must be VerifierResultMetadata")
    return VerifierResultMetadata.model_validate(
        result.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _blocked_reasons(
    assembly: MetaFinalAssemblyPlan,
    verifier_results: tuple[VerifierResultMetadata, ...],
) -> tuple[str, ...]:
    result_by_id = {result.verifier_id: result for result in verifier_results}
    reasons: list[str] = []

    if assembly.projection_mode != "ready_for_projection":
        reasons.append(f"assembly_projection_{assembly.projection_mode}")
    if not assembly.required_verifier_refs:
        reasons.append("verifier_chain_missing")

    for verifier_id in assembly.required_verifier_refs:
        result = result_by_id.get(verifier_id)
        if result is None:
            reasons.append(f"verifier_missing:{verifier_id}")
        elif result.status != "pass":
            reasons.append(f"verifier_{result.status}:{verifier_id}")

    for result in verifier_results:
        if result.verifier_id not in assembly.required_verifier_refs and result.status != "pass":
            reasons.append(f"verifier_{result.status}:{result.verifier_id}")

    return _validate_ref_tuple(reasons, "blockedReasons")


def _retryable_reasons(
    verifier_results: tuple[VerifierResultMetadata, ...],
) -> tuple[str, ...]:
    return _validate_ref_tuple(
        (
            f"verifier_retryable:{result.verifier_id}"
            for result in verifier_results
            if result.status != "pass" and result.retry_message
        ),
        "retryableReasons",
    )


def _verifier_result_ref(result: VerifierResultMetadata) -> str:
    return _validate_public_ref(
        f"verifier-result:{result.verifier_id}:{result.status}",
        "verifierResultRefs",
    )


def _runtime_result_payload_digest(result: RuntimeIssuedMetaVerifierResult) -> str:
    payload = json.dumps(
        {
            "result": result.result.model_dump(by_alias=True, mode="python", warnings=False),
            "assemblyId": result.assembly_id,
            "assemblyDigest": result.assembly_digest,
            "verifierBusRunId": result.verifier_bus_run_id,
            "policySnapshotId": result.policy_snapshot_id,
            "issuer": result.issuer,
            "metadataOnly": result.metadata_only,
            "trafficAttached": result.traffic_attached,
            "executionAttached": result.execution_attached,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verdict_payload_digest(verdict: MetaBeforeCommitVerdict) -> str:
    payload = json.dumps(
        {
            "verdictId": verdict.verdict_id,
            "assemblyId": verdict.assembly_id,
            "assemblyDigest": verdict.assembly_digest,
            "verifierChainResult": verdict.verifier_chain_result,
            "verifierResultRefs": verdict.verifier_result_refs,
            "blockedReasons": verdict.blocked_reasons,
            "retryableReasons": verdict.retryable_reasons,
            "finalProjectionEligible": verdict.final_projection_eligible,
            "commitIntentRefs": verdict.commit_intent_refs,
            "commitExecuted": verdict.commit_executed,
            "transcriptWritten": verdict.transcript_written,
            "sseWritten": verdict.sse_written,
            "controlWritten": verdict.control_written,
            "toolExecutionAttached": verdict.tool_execution_attached,
            "defaultOff": verdict.default_off,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "MetaBeforeCommitVerdict",
    "MetaVerifierChainResult",
    "RuntimeIssuedMetaVerifierResult",
    "evaluate_before_commit_for_assembly",
    "issue_runtime_verifier_result_for_assembly",
]
