from __future__ import annotations

from collections.abc import Mapping
import hashlib
import inspect
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.memory.contracts import RecallRequest, RecallResult
from magi_agent.memory.namespaces import (
    MemoryNamespaceAdmission,
    MemoryNamespacePolicy,
    admit_recall_result_to_namespace,
)
from magi_agent.memory.policy import MemoryPolicy
from magi_agent.memory.projection import (
    MemoryBoundaryProjection,
    project_memory_boundary,
    project_namespaced_memory_boundary,
)


MemoryRecallStatus = Literal["disabled", "allowed", "blocked"]
MemoryRecallRedactionStatus = Literal["verified", "not_required", "failed"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/private/|/var/folders/|/workspace/|/data/bots/|"
    r"/var/lib/kubelet/|"
    r"authorization|cookie|set-cookie|bearer|token|secret|api[_-]?key|"
    r"password|credential|session[_-]?key|connector[_-]?token|private[_-]?key|"
    r"raw[_-]?(?:prompt|output|tool|child|transcript|result|log|args)|"
    r"hidden[_-]?reasoning|private[_-]?memory|sk-(?:live|test)|gh[opusr]_|"
    r"github_pat_|xox[a-z]-|AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]+|"
    r"(?:users|home|private|var[_:-]?folders|workspace|data[_:-]?bots|"
    r"var[_:-]?lib[_:-]?kubelet)[_:-].*"
    r"(?:private[_:-]?path|leaked[_:-]?path|session[_:-]?key|credential|secret))",
    re.IGNORECASE,
)
_SENSITIVE_REASON_RE = re.compile(
    r"(?:authorization|cookie|set-cookie|bearer|session[_-]?key|connector[_-]?token|"
    r"api[_-]?key|secret|credential|password|private[_-]?key|token|sk-(?:live|test)|"
    r"gh[opusr]_|github_pat_|xox[a-z]-|AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]+|"
    r"raw[_-]?(?:prompt|output|tool|child|transcript|result|log|args)|"
    r"(?:child|subagent)[_-]?(?:prompt|output|transcript)|"
    r"tool[_-]?(?:log|args|result)|hidden[_-]?reasoning|chain[_-]?of[_-]?thought|"
    r"/Users/|/home/|/private/|/var/folders/|/workspace/|/data/bots/|"
    r"/var/lib/kubelet/|"
    r"(?:users|home|private|var[_:-]?folders|workspace|data[_:-]?bots|"
    r"var[_:-]?lib[_:-]?kubelet)[_:-].*"
    r"(?:private[_:-]?path|leaked[_:-]?path|session[_:-]?key|credential|secret)|"
    r"private[_-]?memory[_:-]?(?:payload|body|raw|secret))",
    re.IGNORECASE,
)
_FORCED_FALSE_AUTHORITY_FIELDS = (
    "liveProviderCalled",
    "adkRunnerCalled",
    "adkMemoryServiceCalled",
    "modelCalled",
    "networkCalled",
    "promptProjectionAllowed",
    "memoryWriteAllowed",
    "productionWriteAllowed",
    "trafficAttached",
    "userVisibleOutputAllowed",
)


class MemoryRecallProjectionPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    latest_user_text: str = Field(repr=False, alias="latestUserText")
    max_bytes: int = Field(default=16_384, ge=1, le=65_536, alias="maxBytes")
    policy_snapshot_ref: str | None = Field(default=None, alias="policySnapshotRef")
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["promptProjectionAllowed"] = False
        payload.pop("prompt_projection_allowed", None)
        payload["memoryWriteAllowed"] = False
        payload.pop("memory_write_allowed", None)
        payload["productionWriteAllowed"] = False
        payload.pop("production_write_allowed", None)
        return payload

    @field_validator("policy_snapshot_ref", mode="before")
    @classmethod
    def _sanitize_policy_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return _safe_public_ref(str(value), prefix="policy-snapshot")

    @field_serializer(
        "prompt_projection_allowed",
        "memory_write_allowed",
        "production_write_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values.update(
            {
                "promptProjectionAllowed": False,
                "memoryWriteAllowed": False,
                "productionWriteAllowed": False,
            }
        )
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        payload.update(
            {
                "promptProjectionAllowed": False,
                "memoryWriteAllowed": False,
                "productionWriteAllowed": False,
            }
        )
        _ = deep
        return type(self).model_validate(payload)


class MemoryRecallAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    local_adapter_called: bool = Field(default=False, alias="localAdapterCalled")
    live_provider_called: Literal[False] = Field(default=False, alias="liveProviderCalled")
    adk_runner_called: Literal[False] = Field(default=False, alias="adkRunnerCalled")
    adk_memory_service_called: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceCalled",
    )
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    network_called: Literal[False] = Field(default=False, alias="networkCalled")
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority(cls, value: object) -> dict[str, object]:
        if isinstance(value, cls):
            payload = value.model_dump(by_alias=True, mode="python", warnings=False)
        elif isinstance(value, Mapping):
            payload = dict(value)
        else:
            payload = {}
        local_adapter_called = payload.get(
            "localAdapterCalled",
            payload.get("local_adapter_called", False),
        )
        return {
            "localAdapterCalled": bool(local_adapter_called),
            **{key: False for key in _FORCED_FALSE_AUTHORITY_FIELDS},
        }

    @field_serializer(
        "live_provider_called",
        "adk_runner_called",
        "adk_memory_service_called",
        "model_called",
        "network_called",
        "prompt_projection_allowed",
        "memory_write_allowed",
        "production_write_allowed",
        "traffic_attached",
        "user_visible_output_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        for key in _FORCED_FALSE_AUTHORITY_FIELDS:
            values[key] = False
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        for key in _FORCED_FALSE_AUTHORITY_FIELDS:
            payload[key] = False
        _ = deep
        return type(self).model_validate(payload)


class MemoryRecallReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    status: MemoryRecallStatus
    input_digest: str = Field(alias="inputDigest")
    output_digest: str = Field(alias="outputDigest")
    redaction_status: MemoryRecallRedactionStatus = Field(alias="redactionStatus")
    source_authority: str = Field(alias="sourceAuthority")
    namespace_ref: str | None = Field(default=None, alias="namespaceRef")
    decision_counts: dict[str, int] = Field(alias="decisionCounts")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    policy_snapshot_ref: str | None = Field(default=None, alias="policySnapshotRef")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    authority_flags: MemoryRecallAuthorityFlags = Field(alias="authorityFlags")

    @field_validator("input_digest", "output_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return value if _DIGEST_RE.fullmatch(value) else _zero_digest()

    @field_validator("namespace_ref", "policy_snapshot_ref", mode="before")
    @classmethod
    def _sanitize_optional_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return _safe_public_ref(str(value), prefix="memory-ref")

    @field_validator("source_authority", mode="before")
    @classmethod
    def _sanitize_source_authority(cls, value: object) -> str:
        return _safe_reason_code(str(value or "unknown_source_authority"))

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _sanitize_reason_codes(value)

    @model_validator(mode="before")
    @classmethod
    def _coerce_authority_flags(cls, value: object) -> dict[str, object]:
        if isinstance(value, cls):
            payload = value.model_dump(by_alias=True, mode="python", warnings=False)
        elif isinstance(value, Mapping):
            payload = dict(value)
        else:
            payload = {}
        payload["authorityFlags"] = MemoryRecallAuthorityFlags.model_validate(
            payload.get("authorityFlags") or payload.get("authority_flags") or {}
        )
        payload.pop("authority_flags", None)
        return payload

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "inputDigest": self.input_digest,
            "outputDigest": self.output_digest,
            "redactionStatus": self.redaction_status,
            "sourceAuthority": self.source_authority,
            "namespaceRef": self.namespace_ref,
            "decisionCounts": dict(self.decision_counts),
            "policySnapshotDigest": self.policy_snapshot_digest,
            "policySnapshotRef": self.policy_snapshot_ref,
            "reasonCodes": list(_sanitize_reason_codes(self.reason_codes)),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True, mode="json"),
        }


class MemoryRecallRecipeResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: MemoryRecallStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    projection: MemoryBoundaryProjection
    receipt: MemoryRecallReceipt

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _sanitize_reason_codes(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": list(_sanitize_reason_codes(self.reason_codes)),
            "receipt": self.receipt.public_projection(),
            "projection": _sanitize_public_payload(
                self.projection.model_dump(by_alias=True, mode="json")
            ),
        }


async def execute_readonly_memory_recall(
    *,
    request: RecallRequest | Mapping[str, object],
    namespace_policy: MemoryNamespacePolicy | Mapping[str, object] | None,
    projection_policy: MemoryRecallProjectionPolicy | Mapping[str, object] | None,
    adapter: object | None,
    enabled: bool,
    local_fake_adapter_enabled: bool,
) -> MemoryRecallRecipeResult:
    parsed_request = RecallRequest.model_validate(request)
    parsed_namespace = (
        namespace_policy
        if isinstance(namespace_policy, MemoryNamespacePolicy)
        else MemoryNamespacePolicy.model_validate(namespace_policy)
        if namespace_policy is not None
        else None
    )
    parsed_projection = (
        projection_policy
        if isinstance(projection_policy, MemoryRecallProjectionPolicy)
        else MemoryRecallProjectionPolicy.model_validate(projection_policy)
        if projection_policy is not None
        else None
    )
    missing_reasons = _missing_policy_reasons(parsed_namespace, parsed_projection)
    if not enabled:
        return _blocked_result(
            "disabled",
            parsed_request,
            parsed_namespace,
            parsed_projection,
            reason_codes=("memory_recall_recipe_disabled", *missing_reasons),
            local_adapter_called=False,
        )
    if missing_reasons:
        return _blocked_result(
            "blocked",
            parsed_request,
            parsed_namespace,
            parsed_projection,
            reason_codes=missing_reasons,
            local_adapter_called=False,
        )
    assert parsed_namespace is not None
    assert parsed_projection is not None

    if not local_fake_adapter_enabled:
        return _blocked_result(
            "blocked",
            parsed_request,
            parsed_namespace,
            parsed_projection,
            reason_codes=("local_fake_memory_adapter_disabled",),
            local_adapter_called=False,
        )
    if adapter is None or getattr(adapter, "openmagi_local_fake_provider", False) is not True:
        return _blocked_result(
            "blocked",
            parsed_request,
            parsed_namespace,
            parsed_projection,
            reason_codes=("local_fake_memory_adapter_required",),
            local_adapter_called=False,
        )

    raw_result = await _recall_from_adapter(
        adapter,
        parsed_request,
        namespace_policy=parsed_namespace,
    )
    admission = admit_recall_result_to_namespace(raw_result, parsed_namespace)
    decision_counts = _decision_counts(admission)
    blocking_reasons = _blocking_reasons(admission)
    projection = project_namespaced_memory_boundary(
        raw_result,
        namespace_policy=parsed_namespace,
        latest_user_text=parsed_projection.latest_user_text,
        max_bytes=parsed_projection.max_bytes,
    )
    status: MemoryRecallStatus = "allowed"
    reason_codes = tuple(
        dict.fromkeys(
            (
                *admission.reason_codes,
                *projection.diagnostics.reason_codes,
            )
        )
    )

    if blocking_reasons or not admission.result.recall_allowed:
        status = "blocked"
        reason_codes = tuple(dict.fromkeys((*reason_codes, *blocking_reasons)))
        projection = _empty_projection(
            parsed_namespace,
            parsed_projection,
            reason_codes=reason_codes,
        )
    elif projection.diagnostics.rejected_records > 0:
        status = "blocked"
        reason_codes = tuple(
            dict.fromkeys(
                (
                    *reason_codes,
                    "memory_projection_rejected_records",
                )
            )
        )
        projection = _empty_projection(
            parsed_namespace,
            parsed_projection,
            reason_codes=reason_codes,
        )
    elif not projection.references:
        status = "blocked"
        reason_codes = tuple(dict.fromkeys((*reason_codes, "empty_public_memory_projection")))
        projection = _empty_projection(
            parsed_namespace,
            parsed_projection,
            reason_codes=reason_codes,
        )

    return _result(
        status,
        parsed_request,
        parsed_namespace,
        parsed_projection,
        projection,
        reason_codes=reason_codes,
        decision_counts=decision_counts,
        local_adapter_called=True,
    )


async def _recall_from_adapter(
    adapter: object,
    request: RecallRequest,
    *,
    namespace_policy: MemoryNamespacePolicy,
) -> RecallResult:
    policy = MemoryPolicy(
        memory_mode=namespace_policy.memory_mode,
        source_authority=namespace_policy.source_authority,
    )
    recall = getattr(adapter, "recall", None)
    if recall is None:
        return RecallResult(
            providerId="local-fake-memory",
            records=(),
            recallAllowed=False,
            writeAllowed=False,
            promptProjectionAllowed=False,
            publicProjectionAllowed=False,
            reasonCodes=("local_fake_memory_adapter_missing_recall",),
        )
    value = recall(request, policy=policy)
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, RecallResult):
        return RecallResult(
            providerId="local-fake-memory",
            records=(),
            recallAllowed=False,
            writeAllowed=False,
            promptProjectionAllowed=False,
            publicProjectionAllowed=False,
            reasonCodes=("local_fake_memory_adapter_invalid_result",),
        )
    return value.model_copy(
        update={
            "writeAllowed": False,
            "promptProjectionAllowed": False,
        }
    )


def _blocked_result(
    status: MemoryRecallStatus,
    request: RecallRequest,
    namespace_policy: MemoryNamespacePolicy | None,
    projection_policy: MemoryRecallProjectionPolicy | None,
    *,
    reason_codes: tuple[str, ...],
    local_adapter_called: bool,
) -> MemoryRecallRecipeResult:
    effective_projection = projection_policy or MemoryRecallProjectionPolicy(
        latestUserText="",
        maxBytes=1,
    )
    projection = _empty_projection(
        namespace_policy,
        effective_projection,
        reason_codes=reason_codes,
    )
    return _result(
        status,
        request,
        namespace_policy,
        projection_policy,
        projection,
        reason_codes=reason_codes,
        decision_counts=_empty_decision_counts(),
        local_adapter_called=local_adapter_called,
    )


def _result(
    status: MemoryRecallStatus,
    request: RecallRequest,
    namespace_policy: MemoryNamespacePolicy | None,
    projection_policy: MemoryRecallProjectionPolicy | None,
    projection: MemoryBoundaryProjection,
    *,
    reason_codes: tuple[str, ...],
    decision_counts: dict[str, int],
    local_adapter_called: bool,
) -> MemoryRecallRecipeResult:
    projection_payload = projection.model_dump(by_alias=True, mode="json")
    receipt = MemoryRecallReceipt(
        status=status,
        inputDigest=_digest_json(
            {
                "request": request.model_dump(by_alias=True, mode="json"),
                "namespacePolicy": _namespace_policy_payload(namespace_policy),
                "projectionPolicy": _projection_policy_payload(projection_policy),
            }
        ),
        outputDigest=_digest_json(projection_payload),
        redactionStatus=_redaction_status(namespace_policy, reason_codes),
        sourceAuthority=(
            namespace_policy.source_authority
            if namespace_policy is not None
            else "missing_memory_namespace_policy"
        ),
        namespaceRef=namespace_policy.namespace_ref if namespace_policy is not None else None,
        decisionCounts=decision_counts,
        policySnapshotDigest=_digest_json(
            {
                "namespacePolicy": _namespace_policy_payload(namespace_policy),
                "projectionPolicy": _projection_policy_payload(projection_policy),
            }
        ),
        policySnapshotRef=(
            projection_policy.policy_snapshot_ref if projection_policy is not None else None
        ),
        reasonCodes=reason_codes,
        authorityFlags=MemoryRecallAuthorityFlags(localAdapterCalled=local_adapter_called),
    )
    return MemoryRecallRecipeResult(
        status=status,
        reasonCodes=reason_codes,
        projection=projection,
        receipt=receipt,
    )


def _empty_projection(
    namespace_policy: MemoryNamespacePolicy | None,
    projection_policy: MemoryRecallProjectionPolicy,
    *,
    reason_codes: tuple[str, ...],
) -> MemoryBoundaryProjection:
    recall = RecallResult(
        providerId="memory-recall-recipe",
        records=(),
        recallAllowed=False,
        writeAllowed=False,
        promptProjectionAllowed=False,
        publicProjectionAllowed=False,
        reasonCodes=reason_codes,
    )
    if namespace_policy is not None:
        return project_namespaced_memory_boundary(
            recall,
            namespace_policy=namespace_policy,
            latest_user_text=projection_policy.latest_user_text,
            max_bytes=projection_policy.max_bytes,
        )
    return project_memory_boundary(
        recall,
        latest_user_text="",
        policy=MemoryPolicy(memory_mode="incognito", source_authority="long_term_disabled"),
        max_bytes=projection_policy.max_bytes,
    )


def _missing_policy_reasons(
    namespace_policy: MemoryNamespacePolicy | None,
    projection_policy: MemoryRecallProjectionPolicy | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if namespace_policy is None:
        reasons.append("missing_memory_namespace_policy")
    if projection_policy is None:
        reasons.append("missing_memory_projection_policy")
    return tuple(reasons)


def _blocking_reasons(admission: MemoryNamespaceAdmission) -> tuple[str, ...]:
    reasons: list[str] = []
    for decision in admission.decisions:
        if decision.status != "allowed":
            reasons.extend(decision.reason_codes)
    for reason_code in admission.reason_codes:
        if reason_code in {
            "source_authority_disables_long_term_memory",
            "child_memory_scope_isolated",
            "memory_redact_authority_supersedes_provider",
            "memory_redaction_not_verified",
            "memory_retention_not_active",
            "memory_erase_state_blocks_projection",
            "incognito_blocks_recall",
        }:
            reasons.append(reason_code)
    return tuple(dict.fromkeys(reasons))


def _decision_counts(admission: MemoryNamespaceAdmission) -> dict[str, int]:
    counts = _empty_decision_counts()
    for decision in admission.decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    return counts


def _empty_decision_counts() -> dict[str, int]:
    return {"allowed": 0, "blocked": 0, "background_only": 0}


def _redaction_status(
    namespace_policy: MemoryNamespacePolicy | None,
    reason_codes: tuple[str, ...],
) -> MemoryRecallRedactionStatus:
    if namespace_policy is None:
        return "failed"
    if "memory_redaction_not_verified" in reason_codes:
        return "failed"
    if namespace_policy.redaction_state == "not_required":
        return "not_required"
    return "verified"


def _namespace_policy_payload(namespace_policy: MemoryNamespacePolicy | None) -> object:
    if namespace_policy is None:
        return None
    return namespace_policy.model_dump(by_alias=True, mode="json")


def _projection_policy_payload(projection_policy: MemoryRecallProjectionPolicy | None) -> object:
    if projection_policy is None:
        return None
    return {
        "latestUserTextDigest": _digest_text(projection_policy.latest_user_text),
        "maxBytes": projection_policy.max_bytes,
        "policySnapshotRef": projection_policy.policy_snapshot_ref,
        "promptProjectionAllowed": False,
        "memoryWriteAllowed": False,
        "productionWriteAllowed": False,
    }


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _zero_digest() -> str:
    return "sha256:" + ("0" * 64)


def _safe_public_ref(value: str, *, prefix: str) -> str:
    normalized = value.strip()
    if _SAFE_REF_RE.fullmatch(normalized) and _PRIVATE_TEXT_RE.search(normalized) is None:
        return normalized
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _safe_reason_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.:-]+", "_", value.strip().lower()).strip("_")
    if _SENSITIVE_REASON_RE.search(value) or _SENSITIVE_REASON_RE.search(normalized):
        return f"reason:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    if _SAFE_REASON_RE.fullmatch(normalized):
        return normalized
    return f"reason:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _sanitize_reason_codes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = (value,)
    elif isinstance(value, list | tuple):
        candidates = tuple(value)
    else:
        candidates = (str(value),)
    return tuple(dict.fromkeys(_safe_reason_code(str(candidate)) for candidate in candidates))


def _sanitize_public_payload(value: object) -> object:
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text == "reasonCodes":
                sanitized[key_text] = list(_sanitize_reason_codes(item))
            elif key_text in {"recordId", "providerId", "sourceRef", "evidenceRef"}:
                sanitized[key_text] = (
                    None
                    if item is None
                    else _safe_public_ref(str(item), prefix=_public_ref_prefix(key_text))
                )
            else:
                sanitized[key_text] = _sanitize_public_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_public_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_public_payload(item) for item in value]
    if isinstance(value, str) and _PRIVATE_TEXT_RE.search(value):
        return f"redacted:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return value


def _public_ref_prefix(key: str) -> str:
    if key == "providerId":
        return "provider"
    if key == "evidenceRef":
        return "evidence"
    return "memory"


__all__ = [
    "MemoryRecallAuthorityFlags",
    "MemoryRecallProjectionPolicy",
    "MemoryRecallReceipt",
    "MemoryRecallRecipeResult",
    "MemoryRecallRedactionStatus",
    "MemoryRecallStatus",
    "execute_readonly_memory_recall",
]
