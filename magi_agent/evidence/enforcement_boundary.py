from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.evidence.contracts import evaluate_evidence_contract
from magi_agent.evidence.reports import public_evidence_verdict_report
from magi_agent.evidence.types import (
    EvidenceContract,
    EvidenceContractVerdict,
    EvidenceRecord,
)


EvidenceEnforcementDomain = Literal["research", "coding", "completion", "general"]
EvidenceEnforcementStatus = Literal[
    "disabled",
    "evaluation_intent",
    "pass",
    "audit_missing",
    "repair_required",
    "escalate_required",
    "block_ready_local_fake",
]
EvidenceEnforcementAction = Literal[
    "audit",
    "pass",
    "repair",
    "escalate",
    "block_intent",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args|browser|child)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"reasoning[_ -]?trace|model[_ -]?internal|authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_KEY_MARKERS = (
    "raw",
    "token",
    "secret",
    "credential",
    "auth",
    "authoritative",
    "trust",
    "trusted",
    "verified",
    "valid",
    "password",
    "cookie",
    "path",
    "transcript",
    "hidden",
    "reasoning",
    "production",
    "attached",
    "enabled",
    "allowed",
    "performed",
    "authority",
    "route",
    "called",
    "fetched",
    "executed",
    "injected",
    "network",
)


class EvidenceEnforcementConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_evaluation_enabled: bool = Field(
        default=False,
        alias="localFakeEvaluationEnabled",
    )
    evidence_block_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockEnabled",
    )
    final_answer_blocking_enabled: Literal[False] = Field(
        default=False,
        alias="finalAnswerBlockingEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class EvidenceEnforcementAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    evidence_block_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockEnabled",
    )
    final_answer_blocked: Literal[False] = Field(
        default=False,
        alias="finalAnswerBlocked",
    )
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_git_or_test_executed: Literal[False] = Field(
        default=False,
        alias="shellGitOrTestExecuted",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "evidence_block_enabled",
        "final_answer_blocked",
        "live_tool_dispatched",
        "shell_git_or_test_executed",
        "production_writes_enabled",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class EvidenceEnforcementRequest(BaseModel):
    model_config = _MODEL_CONFIG

    domain: EvidenceEnforcementDomain
    contract: EvidenceContract
    evidence_records: tuple[EvidenceRecord, ...] = Field(
        default=(),
        alias="evidenceRecords",
    )
    repair_allowed: bool = Field(default=False, alias="repairAllowed")
    escalation_allowed: bool = Field(default=False, alias="escalationAllowed")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("contract")
    @classmethod
    def _revalidate_contract(cls, value: EvidenceContract) -> EvidenceContract:
        return EvidenceContract.model_validate(
            value.model_dump(by_alias=True, mode="python", warnings=False)
        )

    @field_validator("evidence_records")
    @classmethod
    def _revalidate_records(
        cls,
        value: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        return tuple(
            EvidenceRecord.model_validate(
                record.model_dump(by_alias=True, mode="python", warnings=False)
            )
            for record in value
        )


class EvidenceEnforcementDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: EvidenceEnforcementStatus
    action: EvidenceEnforcementAction
    domain: EvidenceEnforcementDomain
    verdict: EvidenceContractVerdict | None = None
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: EvidenceEnforcementAuthorityFlags = Field(
        default_factory=EvidenceEnforcementAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = EvidenceEnforcementAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data["authorityFlags"] = EvidenceEnforcementAuthorityFlags()
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "action": self.action,
            "domain": self.domain,
            "verdict": None
            if self.verdict is None
            else _safe_public_object(
                public_evidence_verdict_report(self.verdict).model_dump(by_alias=True)
            ),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class EvidenceEnforcementBoundary:
    """Runtime-facing evidence decision boundary without live enforcement authority."""

    def __init__(self, config: EvidenceEnforcementConfig) -> None:
        self.config = config

    def evaluate(self, request: EvidenceEnforcementRequest) -> EvidenceEnforcementDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeEvaluationEnabled": self.config.local_fake_evaluation_enabled,
            "evidenceBlockEnabled": False,
            "finalAnswerBlockingEnabled": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                "audit",
                reason_codes=("evidence_enforcement_disabled",),
                diagnostics=diagnostics,
            )
        if not self.config.local_fake_evaluation_enabled:
            return _decision(
                request,
                "evaluation_intent",
                "audit",
                reason_codes=("local_evidence_evaluation_disabled",),
                diagnostics=diagnostics,
            )

        verdict = evaluate_evidence_contract(request.contract, request.evidence_records)
        if verdict.ok:
            return _decision(
                request,
                "pass",
                "pass",
                verdict=verdict,
                reason_codes=("evidence_contract_passed",),
                diagnostics=diagnostics,
            )
        if verdict.state == "block_ready":
            if request.repair_allowed:
                return _decision(
                    request,
                    "repair_required",
                    "repair",
                    verdict=verdict,
                    reason_codes=("evidence_repair_required",),
                    diagnostics=diagnostics,
                )
            if request.escalation_allowed:
                return _decision(
                    request,
                    "escalate_required",
                    "escalate",
                    verdict=verdict,
                    reason_codes=("evidence_escalation_required",),
                    diagnostics=diagnostics,
                )
            return _decision(
                request,
                "block_ready_local_fake",
                "block_intent",
                verdict=verdict,
                reason_codes=("evidence_block_intent_recorded",),
                diagnostics=diagnostics,
            )
        return _decision(
            request,
            "audit_missing",
            "audit",
            verdict=verdict,
            reason_codes=("evidence_audit_missing_or_failed",),
            diagnostics=diagnostics,
        )


def _decision(
    request: EvidenceEnforcementRequest,
    status: EvidenceEnforcementStatus,
    action: EvidenceEnforcementAction,
    *,
    verdict: EvidenceContractVerdict | None = None,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> EvidenceEnforcementDecision:
    return EvidenceEnforcementDecision(
        status=status,
        action=action,
        domain=request.domain,
        verdict=verdict,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=EvidenceEnforcementAuthorityFlags(),
    )


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_METADATA_KEY_MARKERS):
            continue
        if isinstance(value, str):
            safe[str(key)] = _sanitize_public_text(value)
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _safe_public_object(value: object, *, parent_key: str = "") -> object:
    if isinstance(value, Mapping):
        safe: dict[str, object] = {}
        for key, nested in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if any(marker in normalized_key for marker in _SENSITIVE_METADATA_KEY_MARKERS):
                continue
            safe[str(key)] = _safe_public_object(nested, parent_key=str(key))
        return safe
    if isinstance(value, tuple | list):
        return [_safe_public_object(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        return _sanitize_public_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return "[redacted]"


def _sanitize_public_text(value: str) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None
    ]
    value = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", value)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()[:500]


__all__ = [
    "EvidenceEnforcementAuthorityFlags",
    "EvidenceEnforcementBoundary",
    "EvidenceEnforcementConfig",
    "EvidenceEnforcementDecision",
    "EvidenceEnforcementRequest",
]
