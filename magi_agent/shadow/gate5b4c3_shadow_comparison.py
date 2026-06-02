from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationAuthorityFlags,
    Gate5B4C3ShadowGenerationRequest,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_report import (
    Gate5B4C3ShadowGenerationRunnerReport,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class _Gate5B4C3ComparisonModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)


class Gate5B4C3ShadowComparisonArtifact(_Gate5B4C3ComparisonModel):
    schema_version: Literal["gate5b4c3.shadowComparisonArtifact.v1"] = Field(
        default="gate5b4c3.shadowComparisonArtifact.v1",
        alias="schemaVersion",
    )
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fail_open: Literal[True] = Field(default=True, alias="failOpen")
    comparison_status: Literal[
        "ready",
        "missing_typescript_digest",
        "missing_python_output",
        "python_output_rejected",
    ] = Field(alias="comparisonStatus")
    shadow_generation_id: str = Field(alias="shadowGenerationId")
    request_id_digest: str = Field(alias="requestIdDigest")
    trace_id_digest: str = Field(alias="traceIdDigest")
    type_script_final_answer_digest: str | None = Field(
        default=None,
        alias="typeScriptFinalAnswerDigest",
    )
    type_script_terminal_status: str | None = Field(
        default=None,
        alias="typeScriptTerminalStatus",
    )
    python_output_digest: str | None = Field(default=None, alias="pythonOutputDigest")
    python_report_status: str = Field(alias="pythonReportStatus")
    python_report_reason: str = Field(alias="pythonReportReason")
    output_accepted: bool = Field(default=False, alias="outputAccepted")
    output_truncated: bool = Field(default=False, alias="outputTruncated")
    output_redaction_applied: bool = Field(default=False, alias="outputRedactionApplied")
    user_visible_output: None = Field(default=None, alias="userVisibleOutput")
    production_write_targets: tuple[()] = Field(default=(), alias="productionWriteTargets")
    artifact_digest: str = Field(alias="artifactDigest")
    authority: Gate5B4C3ShadowGenerationAuthorityFlags = Field(
        default_factory=Gate5B4C3ShadowGenerationAuthorityFlags,
    )

    @model_validator(mode="before")
    @classmethod
    def _force_non_authoritative(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "typescript"
        data["diagnosticOnly"] = True
        data["localOnly"] = True
        data["failOpen"] = True
        data["userVisibleOutput"] = None
        data["productionWriteTargets"] = ()
        return data

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


def build_gate5b4c3_shadow_comparison_artifact(
    request: Gate5B4C3ShadowGenerationRequest,
    report: Gate5B4C3ShadowGenerationRunnerReport,
) -> Gate5B4C3ShadowComparisonArtifact:
    status = _comparison_status(request, report)
    payload: dict[str, object] = {
        "schemaVersion": "gate5b4c3.shadowComparisonArtifact.v1",
        "responseAuthority": "typescript",
        "diagnosticOnly": True,
        "localOnly": True,
        "failOpen": True,
        "comparisonStatus": status,
        "shadowGenerationId": request.shadow_generation_id,
        "requestIdDigest": request.request_id_digest,
        "traceIdDigest": request.trace_id_digest,
        "typeScriptFinalAnswerDigest": (
            request.comparison.type_script_final_answer_digest
            if request.comparison is not None
            else None
        ),
        "typeScriptTerminalStatus": (
            request.comparison.type_script_terminal_status
            if request.comparison is not None
            else None
        ),
        "pythonOutputDigest": report.output_digest if report.output_accepted else None,
        "pythonReportStatus": report.status,
        "pythonReportReason": report.reason,
        "outputAccepted": report.output_accepted,
        "outputTruncated": report.output_truncated,
        "outputRedactionApplied": report.output_redaction_applied,
        "userVisibleOutput": None,
        "productionWriteTargets": (),
        "authority": Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        ),
    }
    payload["artifactDigest"] = _artifact_digest(payload)
    return Gate5B4C3ShadowComparisonArtifact.model_validate(payload)


def _comparison_status(
    request: Gate5B4C3ShadowGenerationRequest,
    report: Gate5B4C3ShadowGenerationRunnerReport,
) -> str:
    if request.comparison is None or request.comparison.type_script_final_answer_digest is None:
        return "missing_typescript_digest"
    if report.output_digest is None:
        return "missing_python_output"
    if not report.output_accepted:
        return "python_output_rejected"
    return "ready"


def _artifact_digest(payload: Mapping[str, object]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key != "artifactDigest"
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        default=list,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "Gate5B4C3ShadowComparisonArtifact",
    "build_gate5b4c3_shadow_comparison_artifact",
]
