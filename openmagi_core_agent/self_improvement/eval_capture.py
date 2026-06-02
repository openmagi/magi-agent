from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from openmagi_core_agent.adk_bridge.primitives import AdkPrimitiveBoundary
from openmagi_core_agent.runtime.receipt_utils import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_ref,
    sanitize_public_text,
    sha256_ref,
)


EvalTerminalState: TypeAlias = Literal["passed", "failed", "blocked", "cancelled", "timeout", "error"]
EvalValidatorStatus: TypeAlias = Literal["pass", "fail", "blocked", "skipped", "error"]
EvalCaptureStatus: TypeAlias = Literal["disabled", "blocked", "captured_local_fake"]
EvalMutationDecision: TypeAlias = Literal["denied"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(
    r"^(?:adk-eval|eval|eval-metric|evidence|failure-cluster|metric|mutation|"
    r"observation|policy|policy-snapshot|recipe|ref|run|sha256|turn|validator):"
    r"[A-Za-z0-9_.:/=@-]{1,191}$"
)
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SAFE_MUTATION_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")
_SHA256_REF_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_KNOWN_MUTATION_TYPES = frozenset(
    {
        "code_patch",
        "config_update",
        "deploy_change",
        "secret_change",
    }
)
_ADK_EVALUATION_PRIMITIVE = (
    AdkPrimitiveBoundary.declared().evaluator or "google.adk.evaluation.AgentEvaluator"
)


class EvalCaptureAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    production_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionMutationEnabled",
    )
    code_mutation_enabled: Literal[False] = Field(default=False, alias="codeMutationEnabled")
    config_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="configMutationEnabled",
    )
    deploy_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="deployMutationEnabled",
    )
    secret_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="secretMutationEnabled",
    )
    route_activation_enabled: Literal[False] = Field(
        default=False,
        alias="routeActivationEnabled",
    )
    model_call_enabled: Literal[False] = Field(default=False, alias="modelCallEnabled")
    live_evaluation_enabled: Literal[False] = Field(
        default=False,
        alias="liveEvaluationEnabled",
    )
    tool_execution_enabled: Literal[False] = Field(default=False, alias="toolExecutionEnabled")
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

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

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return type(self)()

    @field_serializer(
        "production_mutation_enabled",
        "code_mutation_enabled",
        "config_mutation_enabled",
        "deploy_mutation_enabled",
        "secret_mutation_enabled",
        "route_activation_enabled",
        "model_call_enabled",
        "live_evaluation_enabled",
        "tool_execution_enabled",
        "user_visible_output_enabled",
        "production_write_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class EvalCaptureConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_capture_enabled: bool = Field(default=False, alias="localFakeCaptureEnabled")
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    live_adk_evaluation_enabled: Literal[False] = Field(
        default=False,
        alias="liveAdkEvaluationEnabled",
    )
    automatic_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="automaticMutationEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_live_flags_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionWriteEnabled"] = False
        payload.pop("production_write_enabled", None)
        payload["liveAdkEvaluationEnabled"] = False
        payload.pop("live_adk_evaluation_enabled", None)
        payload["automaticMutationEnabled"] = False
        payload.pop("automatic_mutation_enabled", None)
        return payload

    @field_serializer(
        "production_write_enabled",
        "live_adk_evaluation_enabled",
        "automatic_mutation_enabled",
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
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, deep
        return self.model_copy(update=update)


class EvalValidatorResult(BaseModel):
    model_config = _MODEL_CONFIG

    validator_id: str = Field(alias="validatorId")
    status: EvalValidatorStatus
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    summary: str | None = None

    @field_validator("validator_id")
    @classmethod
    def _validate_validator_id(cls, value: str) -> str:
        return _safe_ref(value, "validatorId", prefixes=("validator:",))

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        safe_refs = tuple(_safe_ref(ref, "evidenceRefs", prefixes=("evidence:", "ref:", "sha256:")) for ref in refs)
        return safe_refs

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_eval_reason_code(item) for item in _string_tuple(value))

    @field_validator("summary")
    @classmethod
    def _sanitize_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        safe = sanitize_public_text(value)
        return safe[:280] or None


class EvalCaptureRequest(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementEvalCaptureRequest.v1"] = Field(
        default="selfImprovementEvalCaptureRequest.v1",
        alias="schemaVersion",
    )
    eval_id: str = Field(alias="evalId")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    recipe_id: str = Field(alias="recipeId")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    terminal_state: EvalTerminalState = Field(alias="terminalState")
    validator_results: tuple[EvalValidatorResult, ...] = Field(alias="validatorResults")
    metric_refs: tuple[str, ...] = Field(default=(), alias="metricRefs")
    public_summary: str | None = Field(default=None, alias="publicSummary")
    adk_evaluation_ref: str | None = Field(default=None, alias="adkEvaluationRef")
    requested_mutations: tuple[str, ...] = Field(default=(), alias="requestedMutations")
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True, repr=False)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True, repr=False)
    raw_private_path: str | None = Field(
        default=None,
        alias="rawPrivatePath",
        exclude=True,
        repr=False,
    )
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True, repr=False)
    auth_headers: Mapping[str, object] | None = Field(
        default=None,
        alias="authHeaders",
        exclude=True,
        repr=False,
    )
    cookies: Mapping[str, object] | None = Field(default=None, exclude=True, repr=False)
    secret_material: str | None = Field(
        default=None,
        alias="secretMaterial",
        exclude=True,
        repr=False,
    )
    hidden_reasoning: str | None = Field(
        default=None,
        alias="hiddenReasoning",
        exclude=True,
        repr=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_field_name_updates(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        aliases = {
            "eval_id": "evalId",
            "run_id": "runId",
            "turn_id": "turnId",
            "recipe_id": "recipeId",
            "policy_snapshot_digest": "policySnapshotDigest",
            "terminal_state": "terminalState",
            "validator_results": "validatorResults",
            "metric_refs": "metricRefs",
            "public_summary": "publicSummary",
            "adk_evaluation_ref": "adkEvaluationRef",
            "requested_mutations": "requestedMutations",
        }
        for field_name, alias in aliases.items():
            if field_name in payload:
                payload[alias] = payload.pop(field_name)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        payload = {
            "evalId": sanitize_public_ref(str(values.get("evalId", values.get("eval_id", "eval:forged")))),
            "runId": sanitize_public_ref(str(values.get("runId", values.get("run_id", "run:forged")))),
            "turnId": sanitize_public_ref(str(values.get("turnId", values.get("turn_id", "turn:forged")))),
            "recipeId": sanitize_public_ref(
                str(values.get("recipeId", values.get("recipe_id", "recipe:forged")))
            ),
            "policySnapshotDigest": _coerce_digest(
                values.get("policySnapshotDigest", values.get("policy_snapshot_digest"))
            ),
            "terminalState": _coerce_terminal_state(
                values.get("terminalState", values.get("terminal_state"))
            ),
            "validatorResults": tuple(
                item.model_dump(by_alias=True)
                if isinstance(item, EvalValidatorResult)
                else item
                for item in _object_tuple(
                    values.get("validatorResults", values.get("validator_results"))
                )
            ),
            "metricRefs": tuple(
                sanitize_public_ref(str(item))
                for item in _object_tuple(values.get("metricRefs", values.get("metric_refs")))
            ),
            "publicSummary": sanitize_public_text(
                str(values.get("publicSummary", values.get("public_summary", "")) or "")
            )
            or None,
            "adkEvaluationRef": (
                sanitize_public_ref(
                    str(values.get("adkEvaluationRef", values.get("adk_evaluation_ref")))
                )
                if values.get("adkEvaluationRef", values.get("adk_evaluation_ref"))
                else None
            ),
            "requestedMutations": tuple(
                str(item)
                for item in _object_tuple(
                    values.get("requestedMutations", values.get("requested_mutations"))
                )
            ),
        }
        return cls.model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        raise ValueError("copy is disabled for EvalCaptureRequest")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    @field_validator("eval_id")
    @classmethod
    def _validate_eval_id(cls, value: str) -> str:
        return _safe_ref(value, "evalId", prefixes=("eval:",))

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _safe_ref(value, "runId", prefixes=("run:",))

    @field_validator("turn_id")
    @classmethod
    def _validate_turn_id(cls, value: str) -> str:
        return _safe_ref(value, "turnId", prefixes=("turn:",))

    @field_validator("recipe_id")
    @classmethod
    def _validate_recipe_id(cls, value: str) -> str:
        return _safe_ref(value, "recipeId", prefixes=("recipe:",))

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_policy_snapshot_digest(cls, value: str) -> str:
        if not _SHA256_REF_RE.fullmatch(value):
            raise ValueError("policySnapshotDigest must be sha256:<64 lowercase hex>")
        return value

    @field_validator("validator_results")
    @classmethod
    def _require_validator_results(
        cls,
        value: tuple[EvalValidatorResult, ...],
    ) -> tuple[EvalValidatorResult, ...]:
        if not value:
            raise ValueError("validatorResults must not be empty")
        return value

    @field_validator("metric_refs", mode="before")
    @classmethod
    def _validate_metric_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_ref(ref, "metricRefs", prefixes=("eval-metric:", "metric:", "ref:", "sha256:")) for ref in _string_tuple(value))

    @field_validator("public_summary")
    @classmethod
    def _sanitize_public_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        safe = sanitize_public_text(value)
        return safe[:400] or None

    @field_validator("adk_evaluation_ref")
    @classmethod
    def _validate_adk_evaluation_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value, "adkEvaluationRef", prefixes=("adk-eval:", "eval:", "ref:", "sha256:"))

    @field_validator("requested_mutations", mode="before")
    @classmethod
    def _sanitize_requested_mutations(cls, value: object) -> tuple[str, ...]:
        sanitized = []
        for item in _string_tuple(value):
            token = item.strip().lower().replace(" ", "_")
            if token in _KNOWN_MUTATION_TYPES:
                sanitized.append(token)
            elif not token or not _SAFE_MUTATION_RE.fullmatch(token) or has_unsafe_marker(token):
                sanitized.append("blocked_mutation")
            else:
                sanitized.append(token)
        return tuple(sanitized)


class EvalObservation(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementEvalObservation.v1"] = Field(
        default="selfImprovementEvalObservation.v1",
        alias="schemaVersion",
    )
    observation_id: str = Field(alias="observationId")
    observation_digest: str = Field(alias="observationDigest")
    eval_id: str = Field(alias="evalId")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    recipe_id: str = Field(alias="recipeId")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    terminal_state: EvalTerminalState = Field(alias="terminalState")
    validator_results: tuple[EvalValidatorResult, ...] = Field(alias="validatorResults")
    metric_refs: tuple[str, ...] = Field(default=(), alias="metricRefs")
    public_summary: str | None = Field(default=None, alias="publicSummary")
    adk_evaluation_ref: str | None = Field(default=None, alias="adkEvaluationRef")
    failure_signature_digest: str = Field(alias="failureSignatureDigest")
    denied_mutation_refs: tuple[str, ...] = Field(default=(), alias="deniedMutationRefs")
    mutation_decision: EvalMutationDecision = Field(default="denied", alias="mutationDecision")
    authority_flags: EvalCaptureAuthorityFlags = Field(
        default_factory=EvalCaptureAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_field_name_updates(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        if "denied_mutation_refs" in payload:
            payload["deniedMutationRefs"] = payload.pop("denied_mutation_refs")
        return payload

    @field_validator("observation_id")
    @classmethod
    def _validate_observation_id(cls, value: str) -> str:
        return _safe_ref(value, "observationId", prefixes=("observation:",))

    @field_validator("eval_id")
    @classmethod
    def _validate_eval_id(cls, value: str) -> str:
        return _safe_ref(value, "evalId", prefixes=("eval:",))

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _safe_ref(value, "runId", prefixes=("run:",))

    @field_validator("turn_id")
    @classmethod
    def _validate_turn_id(cls, value: str) -> str:
        return _safe_ref(value, "turnId", prefixes=("turn:",))

    @field_validator("recipe_id")
    @classmethod
    def _validate_recipe_id(cls, value: str) -> str:
        return _safe_ref(value, "recipeId", prefixes=("recipe:",))

    @field_validator("policy_snapshot_digest", "failure_signature_digest")
    @classmethod
    def _validate_digest_ref(cls, value: str) -> str:
        if not _SHA256_REF_RE.fullmatch(value):
            raise ValueError("digest refs must be sha256:<64 lowercase hex>")
        return value

    @field_validator("metric_refs", mode="before")
    @classmethod
    def _validate_metric_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(
            _safe_ref(ref, "metricRefs", prefixes=("eval-metric:", "metric:", "ref:", "sha256:"))
            for ref in _string_tuple(value)
        )

    @field_validator("public_summary")
    @classmethod
    def _sanitize_public_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        safe = sanitize_public_text(value)
        if safe != value:
            raise ValueError("publicSummary must already be sanitized")
        return safe[:400] or None

    @field_validator("adk_evaluation_ref")
    @classmethod
    def _validate_adk_evaluation_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value, "adkEvaluationRef", prefixes=("adk-eval:", "eval:", "ref:", "sha256:"))

    @field_validator("denied_mutation_refs", mode="before")
    @classmethod
    def _validate_denied_mutation_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_denied_mutation_ref(item) for item in _string_tuple(value))

    @model_validator(mode="after")
    def _validate_digests(self) -> Self:
        expected_observation = _observation_digest_payload(self)
        if self.observation_digest != canonical_digest(expected_observation):
            raise ValueError("observationDigest mismatch")
        expected_signature = _failure_signature_digest_payload(
            self.terminal_state,
            self.validator_results,
        )
        if self.failure_signature_digest != canonical_digest(expected_signature):
            raise ValueError("failureSignatureDigest mismatch")
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        validator_results = tuple(
            item
            if isinstance(item, EvalValidatorResult)
            else EvalValidatorResult.model_validate(item)
            for item in _object_tuple(values.get("validatorResults"))
        )
        payload = {
            "schemaVersion": "selfImprovementEvalObservation.v1",
            "observationId": sanitize_public_ref(str(values.get("observationId") or "observation:forged")),
            "evalId": sanitize_public_ref(str(values.get("evalId") or "eval:forged")),
            "runId": sanitize_public_ref(str(values.get("runId") or "run:forged")),
            "turnId": sanitize_public_ref(str(values.get("turnId") or "turn:forged")),
            "recipeId": sanitize_public_ref(str(values.get("recipeId") or "recipe:forged")),
            "policySnapshotDigest": _coerce_digest(values.get("policySnapshotDigest")),
            "terminalState": _coerce_terminal_state(values.get("terminalState")),
            "validatorResults": tuple(result.model_dump(by_alias=True) for result in validator_results),
            "metricRefs": tuple(
                sanitize_public_ref(str(item)) for item in _object_tuple(values.get("metricRefs"))
            ),
            "publicSummary": sanitize_public_text(str(values.get("publicSummary") or "")) or None,
            "adkEvaluationRef": (
                sanitize_public_ref(str(values["adkEvaluationRef"]))
                if values.get("adkEvaluationRef")
                else None
            ),
            "deniedMutationRefs": tuple(
                _safe_denied_mutation_ref(item)
                for item in _object_tuple(values.get("deniedMutationRefs"))
            ),
            "mutationDecision": "denied",
            "authorityFlags": EvalCaptureAuthorityFlags().model_dump(by_alias=True),
        }
        failure_signature_payload = _failure_signature_digest_payload(
            payload["terminalState"],
            validator_results,
        )
        payload["failureSignatureDigest"] = canonical_digest(failure_signature_payload)
        payload["observationDigest"] = canonical_digest(payload)
        return cls.model_validate(payload)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        raise ValueError("model_copy is disabled for EvalObservation")

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        raise ValueError("copy is disabled for EvalObservation")


class EvalCaptureResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementEvalCaptureResult.v1"] = Field(
        default="selfImprovementEvalCaptureResult.v1",
        alias="schemaVersion",
    )
    status: EvalCaptureStatus
    observation: EvalObservation | None = None
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    mutation_decision: EvalMutationDecision = Field(default="denied", alias="mutationDecision")
    denied_mutation_refs: tuple[str, ...] = Field(default=(), alias="deniedMutationRefs")
    authority_flags: EvalCaptureAuthorityFlags = Field(
        default_factory=EvalCaptureAuthorityFlags,
        alias="authorityFlags",
    )
    adk_primitive: Literal["google.adk.evaluation.AgentEvaluator"] = Field(
        default=_ADK_EVALUATION_PRIMITIVE,
        alias="adkPrimitive",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_denied_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "denied_mutation_refs" in payload:
            payload["deniedMutationRefs"] = payload.pop("denied_mutation_refs")
        if "blocked_reason" in payload:
            payload["blockedReason"] = payload.pop("blocked_reason")
        payload["mutationDecision"] = "denied"
        payload.pop("mutation_decision", None)
        payload["authorityFlags"] = EvalCaptureAuthorityFlags().model_dump(by_alias=True)
        payload.pop("authority_flags", None)
        return payload

    @field_validator("blocked_reason")
    @classmethod
    def _validate_blocked_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        safe = sanitize_public_text(value)
        token = safe.strip().lower().replace(" ", "_")
        if (
            token
            and _SAFE_REASON_RE.fullmatch(token)
            and not has_unsafe_marker(token)
            and safe == value
        ):
            return token
        raise ValueError("blockedReason must be a safe reason code")

    @field_validator("denied_mutation_refs", mode="before")
    @classmethod
    def _validate_denied_mutation_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_denied_mutation_ref(item) for item in _string_tuple(value))

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, deep
        return self.model_copy(update=update)


class SelfImprovementEvalCapture:
    def __init__(self, config: EvalCaptureConfig | Mapping[str, object] | None = None) -> None:
        self.config = (
            EvalCaptureConfig.model_validate(config.model_dump(by_alias=True))
            if isinstance(config, EvalCaptureConfig)
            else EvalCaptureConfig.model_validate(config or {})
        )

    def capture(self, request: EvalCaptureRequest | Mapping[str, object]) -> EvalCaptureResult:
        capture_request = (
            EvalCaptureRequest.model_validate(request.model_dump(by_alias=True))
            if isinstance(request, EvalCaptureRequest)
            else EvalCaptureRequest.model_validate(request)
        )
        denied_mutation_refs = tuple(
            f"mutation:{mutation}" for mutation in capture_request.requested_mutations
        )
        if not self.config.enabled:
            return EvalCaptureResult(
                status="disabled",
                blockedReason="self_improvement_eval_capture_disabled",
                deniedMutationRefs=denied_mutation_refs,
            )
        if not self.config.local_fake_capture_enabled:
            return EvalCaptureResult(
                status="blocked",
                blockedReason="self_improvement_local_fake_capture_disabled",
                deniedMutationRefs=denied_mutation_refs,
            )
        observation = _build_observation(capture_request, denied_mutation_refs)
        return EvalCaptureResult(
            status="captured_local_fake",
            observation=observation,
            deniedMutationRefs=denied_mutation_refs,
        )


def _build_observation(
    request: EvalCaptureRequest,
    denied_mutation_refs: tuple[str, ...],
) -> EvalObservation:
    failure_signature_payload = _failure_signature_digest_payload(
        request.terminal_state,
        request.validator_results,
    )
    payload = {
        "schemaVersion": "selfImprovementEvalObservation.v1",
        "observationId": "observation:" + sha256_ref(
            "|".join(
                (
                    request.eval_id,
                    request.run_id,
                    request.turn_id,
                    request.recipe_id,
                    request.policy_snapshot_digest,
                )
            )
        ).removeprefix("sha256:"),
        "evalId": request.eval_id,
        "runId": request.run_id,
        "turnId": request.turn_id,
        "recipeId": request.recipe_id,
        "policySnapshotDigest": request.policy_snapshot_digest,
        "terminalState": request.terminal_state,
        "validatorResults": tuple(
            result.model_dump(by_alias=True) for result in request.validator_results
        ),
        "metricRefs": request.metric_refs,
        "publicSummary": request.public_summary,
        "adkEvaluationRef": request.adk_evaluation_ref,
        "failureSignatureDigest": canonical_digest(failure_signature_payload),
        "deniedMutationRefs": denied_mutation_refs,
        "mutationDecision": "denied",
        "authorityFlags": EvalCaptureAuthorityFlags().model_dump(by_alias=True),
    }
    observation_digest = canonical_digest(payload)
    return EvalObservation.model_validate(payload | {"observationDigest": observation_digest})


def _observation_digest_payload(observation: EvalObservation) -> dict[str, object]:
    return observation.model_dump(
        by_alias=True,
        exclude={"observation_digest"},
        exclude_none=False,
    )


def _failure_signature_digest_payload(
    terminal_state: EvalTerminalState,
    validator_results: Sequence[EvalValidatorResult],
) -> dict[str, object]:
    failed_results = [
        result
        for result in validator_results
        if result.status in {"fail", "blocked", "error"}
    ]
    if not failed_results and terminal_state != "passed":
        failed_results = list(validator_results)
    return {
        "terminalState": terminal_state,
        "validatorIds": sorted({result.validator_id for result in failed_results}),
        "reasonCodes": sorted({reason for result in failed_results for reason in result.reason_codes}),
        "statuses": sorted({result.status for result in failed_results}),
    }


def _safe_ref(value: str, field_name: str, *, prefixes: tuple[str, ...]) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field_name} must not be empty")
    if has_unsafe_marker(raw):
        raise ValueError(f"{field_name} contains unsafe marker")
    if sanitize_public_text(raw) != raw:
        raise ValueError(f"{field_name} contains private or secret material")
    if not raw.startswith(prefixes):
        raise ValueError(f"{field_name} must use an allowed ref prefix")
    if not _SAFE_REF_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be a safe public ref")
    return raw


def _sanitize_eval_reason_code(value: str) -> str:
    raw = str(value).strip().lower().replace(" ", "_")
    if raw and _SAFE_REASON_RE.fullmatch(raw) and not has_unsafe_marker(raw):
        return raw
    safe = sanitize_public_text(value).strip().lower().replace(" ", "_")
    if safe and _SAFE_REASON_RE.fullmatch(safe) and not has_unsafe_marker(safe):
        return safe
    return "eval_validator_reason"


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


def _safe_denied_mutation_ref(value: object) -> str:
    raw = str(value).strip()
    token = raw.removeprefix("mutation:").strip().lower().replace(" ", "_")
    if token in _KNOWN_MUTATION_TYPES or token == "blocked_mutation":
        return f"mutation:{token}"
    if (
        not token
        or not _SAFE_MUTATION_RE.fullmatch(token)
        or has_unsafe_marker(token)
        or sanitize_public_text(token) != token
    ):
        raise ValueError("deniedMutationRefs must contain safe mutation refs")
    return f"mutation:{token}"


def _object_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return (value,)


def _coerce_digest(value: object) -> str:
    raw = str(value or "")
    if _SHA256_REF_RE.fullmatch(raw):
        return raw
    return sha256_ref(raw)


def _coerce_terminal_state(value: object) -> EvalTerminalState:
    raw = str(value or "error")
    if raw in {"passed", "failed", "blocked", "cancelled", "timeout", "error"}:
        return raw  # type: ignore[return-value]
    return "error"


__all__ = [
    "EvalCaptureAuthorityFlags",
    "EvalCaptureConfig",
    "EvalCaptureRequest",
    "EvalCaptureResult",
    "EvalCaptureStatus",
    "EvalMutationDecision",
    "EvalObservation",
    "EvalTerminalState",
    "EvalValidatorResult",
    "EvalValidatorStatus",
    "SelfImprovementEvalCapture",
]
