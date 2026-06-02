from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


DisciplineCheck = Literal[
    "debug_checkpoint",
    "discipline_prompt_block",
    "coding_hard_mode",
    "self_claim",
    "pre_refusal",
    "output_purity",
    "response_language",
]
DisciplineStatus = Literal["disabled", "passed", "checkpoint_recorded", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test|discipline)?[-_A-Za-z0-9]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
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
_EVIDENCE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SELF_CLAIM_RE = re.compile(
    r"\b(tests?\s+passed|verified|i\s+ran|completed|implementation\s+complete|fixed)\b",
    re.IGNORECASE,
)
_REFUSAL_RE = re.compile(r"^\s*(?:i\s+can(?:not|'t)|unable\s+to|sorry,\s*i\s+can(?:not|'t))", re.IGNORECASE)
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")


class DisciplineBoundaryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    adk_callbacks_attached: Literal[False] = Field(default=False, alias="adkCallbacksAttached")
    adk_evals_attached: Literal[False] = Field(default=False, alias="adkEvalsAttached")
    prompt_injection_enabled: Literal[False] = Field(default=False, alias="promptInjectionEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class DisciplineAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_callback_invoked: Literal[False] = Field(default=False, alias="adkCallbackInvoked")
    adk_eval_invoked: Literal[False] = Field(default=False, alias="adkEvalInvoked")
    prompt_injected: Literal[False] = Field(default=False, alias="promptInjected")
    user_visible_output_blocked: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputBlocked",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "adk_callback_invoked",
        "adk_eval_invoked",
        "prompt_injected",
        "user_visible_output_blocked",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class DisciplineRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    turn_id: str = Field(alias="turnId")
    check: DisciplineCheck
    output_text: str = Field(default="", alias="outputText")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(ref, "evidence") for ref in value)


class DisciplineDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: DisciplineStatus
    check: DisciplineCheck
    checkpoint_ref: str | None = Field(default=None, alias="checkpointRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: DisciplineAuthorityFlags = Field(
        default_factory=DisciplineAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = DisciplineAuthorityFlags()
        return cls.model_validate(values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "check": self.check,
            "checkpointRef": self.checkpoint_ref,
            "reasonCodes": list(self.reason_codes),
            "evidenceRefs": [_safe_ref(ref, "evidence") for ref in self.evidence_refs],
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class DisciplineBoundary:
    """Default-off DebugWorkflow and discipline gate boundary."""

    def __init__(self, config: DisciplineBoundaryConfig) -> None:
        self.config = config

    def evaluate(self, request: DisciplineRequest) -> DisciplineDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "adkCallbacksAttached": False,
            "adkEvalsAttached": False,
            "promptInjectionEnabled": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        if not self.config.enabled:
            return _decision(request, "disabled", ("discipline_boundary_disabled",), diagnostics)
        private_reason = _private_payload_reason(request)
        if private_reason is not None:
            return _decision(request, "blocked", (private_reason,), diagnostics)
        if request.check == "self_claim":
            if _SELF_CLAIM_RE.search(request.output_text) and not request.evidence_refs:
                return _decision(request, "blocked", ("self_claim_requires_evidence",), diagnostics)
            return _decision(request, "passed", ("self_claim_evidence_satisfied",), diagnostics)
        if request.check == "coding_hard_mode":
            if _SELF_CLAIM_RE.search(request.output_text) and not request.evidence_refs:
                return _decision(
                    request,
                    "blocked",
                    ("coding_hard_mode_evidence_required",),
                    diagnostics,
                )
            return _decision(request, "passed", ("coding_hard_mode_passed",), diagnostics)
        if request.check == "pre_refusal":
            if _REFUSAL_RE.search(request.output_text) and request.metadata.get("availableAction") is True:
                return _decision(
                    request,
                    "blocked",
                    ("premature_refusal_requires_alternative",),
                    diagnostics,
                )
            return _decision(request, "passed", ("pre_refusal_passed",), diagnostics)
        if request.check == "output_purity":
            return _decision(request, "passed", ("output_purity_passed",), diagnostics)
        if request.check == "response_language":
            expected_language = str(request.metadata.get("expectedLanguage") or "")
            if expected_language == "ko" and _HANGUL_RE.search(request.output_text) is None:
                return _decision(request, "blocked", ("response_language_mismatch",), diagnostics)
            return _decision(request, "passed", ("response_language_passed",), diagnostics)
        if request.check in {"debug_checkpoint", "discipline_prompt_block"}:
            checkpoint_ref = _checkpoint_ref(request)
            return _decision(
                request,
                "checkpoint_recorded",
                (f"{request.check}_metadata_only",),
                diagnostics,
                checkpoint_ref=checkpoint_ref,
            )
        return _decision(request, "passed", ("discipline_check_passed",), diagnostics)


def _decision(
    request: DisciplineRequest,
    status: DisciplineStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    checkpoint_ref: str | None = None,
) -> DisciplineDecision:
    return DisciplineDecision(
        status=status,
        check=request.check,
        checkpointRef=checkpoint_ref,
        reasonCodes=reason_codes,
        evidenceRefs=request.evidence_refs,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=DisciplineAuthorityFlags(),
    )


def _private_payload_reason(request: DisciplineRequest) -> str | None:
    if _contains_private_payload(request.output_text):
        if request.check == "debug_checkpoint":
            return "private_debug_payload_blocked"
        return "private_output_purity_violation"
    for ref in request.evidence_refs:
        if _contains_private_payload(ref):
            if request.check == "debug_checkpoint":
                return "private_debug_payload_blocked"
            return "private_evidence_ref_blocked"
    return None


def _checkpoint_ref(request: DisciplineRequest) -> str:
    candidate = request.metadata.get("checkpointId")
    if isinstance(candidate, str):
        return _safe_ref(candidate, "checkpoint")
    seed = f"{request.turn_id}:{request.check}:{request.output_text}"
    return f"{request.check}:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(
            marker in normalized_key
            for marker in ("raw", "secret", "token", "credential", "password", "hidden", "prompt")
        ):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[str(key)] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _safe_ref(value: str, prefix: str) -> str:
    clean = _safe_text(value)
    if clean and _EVIDENCE_REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _safe_text(value: str) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None and not _PRIVATE_PATH_RE.search(line)
    ]
    clean = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


def _contains_private_payload(value: str) -> bool:
    return bool(_RAW_PRIVATE_LINE_RE.search(value) or _PRIVATE_PATH_RE.search(value) or _SECRET_TEXT_RE.search(value))


__all__ = [
    "DisciplineAuthorityFlags",
    "DisciplineBoundary",
    "DisciplineBoundaryConfig",
    "DisciplineDecision",
    "DisciplineRequest",
]
