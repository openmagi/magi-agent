from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import re
from math import isfinite
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_serializer, field_validator


RuntimeEventType = Literal[
    "route_decision",
    "context_projection",
    "model_call",
    "tool_call",
    "guardrail_result",
    "approval",
    "projection",
    "delivery",
    "checkpoint",
]
RedactionStatus = Literal["redacted", "not_required"]
_RUNTIME_EVENT_TYPES = set(RuntimeEventType.__args__)
_REDACTION_STATUSES = set(RedactionStatus.__args__)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_PREFIX = "sha256:"
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:=.-]{1,180}$")
_SECRET_SHAPED_PATTERNS = (
    re.compile(r"^sk_(?:live|test)_[A-Za-z0-9_=-]{12,}$"),
    re.compile(r"^sk-[A-Za-z0-9_-]{12,}$"),
    re.compile(r"^rk_(?:live|test)_[A-Za-z0-9_=-]{12,}$"),
    re.compile(r"^gh[pousr]_[A-Za-z0-9_]{20,}$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^AIza[0-9A-Za-z_-]{20,}$"),
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]{20,}$"),
    re.compile(r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}$"),
)
_PROTECTED_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "pro" + "mpt",
    "sess" + "ion",
    "priv" + "ate",
    "bearer",
    "credential",
    "auth",
    "oauth",
)
_RAW_MARKERS = (
    "rawprompt",
    "raw prompt",
    "rawmodeloutput",
    "raw model output",
    "raw:",
    "rawref",
    "rawtoollog",
    "rawchildtranscript",
    "childrawtoollog",
    "rawoutput",
    "rawresult",
    "hiddenreasoning",
    "privatememory",
)
_PROTECTED_COMPACT_MARKERS = tuple(
    "".join(character for character in marker if character.isalnum())
    for marker in _PROTECTED_FRAGMENTS + _RAW_MARKERS
)
_PATHLIKE_COMPACT_MARKERS = ("users", "home", "ssh", "idrsa", "env", "kube", "kubeconfig", "varlib", "databots")


class _FrozenNoUpdateModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for deterministic runtime events")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))

    def copy(
        self,
        *,
        include: object = None,
        exclude: object = None,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        if update or include is not None or exclude is not None:
            raise ValueError("copy update/include/exclude is disabled for deterministic runtime events")
        return self.model_copy(deep=deep)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for deterministic runtime events")


class DeterministicRuntimeEvent(_FrozenNoUpdateModel):
    def __init__(self, **data: object) -> None:
        _preflight_event_payload(data)
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise _sanitize_validation_error(exc) from None

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        _preflight_event_payload(obj)
        try:
            return super().model_validate(obj, *args, **kwargs)
        except ValidationError as exc:
            raise _sanitize_validation_error(exc) from None

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, *args: object, **kwargs: object) -> Self:
        try:
            raw_json = json_data.decode() if isinstance(json_data, bytes | bytearray) else json_data
            _preflight_event_payload(json.loads(raw_json))
        except ValidationError:
            raise
        except Exception:
            try:
                return super().model_validate_json(json_data, *args, **kwargs)
            except ValidationError as exc:
                raise _sanitize_validation_error(exc) from None
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except ValidationError as exc:
            raise _sanitize_validation_error(exc) from None

    def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
        _preflight_event_payload(_raw_event_payload(self))
        return super().model_dump(*args, **kwargs)

    def model_dump_json(self, *args: object, **kwargs: object) -> str:
        _preflight_event_payload(_raw_event_payload(self))
        return super().model_dump_json(*args, **kwargs)

    event_id: str = Field(alias="eventId")
    run_id: str = Field(alias="runId")
    workflow_id: str = Field(alias="workflowId")
    step_id: str = Field(alias="stepId")
    event_type: RuntimeEventType = Field(alias="eventType")
    route_decision: str = Field(alias="routeDecision")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    ledger_head_digest: str = Field(alias="ledgerHeadDigest")
    checkpoint_id: str | None = Field(default=None, alias="checkpointId")
    validator_statuses: tuple[str, ...] = Field(default=(), alias="validatorStatuses")
    approval_gate_refs: tuple[str, ...] = Field(default=(), alias="approvalGateRefs")
    repair_attempt: StrictInt = Field(alias="repairAttempt", ge=0, le=100)
    projection_mode: str = Field(alias="projectionMode")
    terminal_state: str | None = Field(default=None, alias="terminalState")
    redaction_status: RedactionStatus = Field(alias="redactionStatus")
    activation_enabled: Literal[False] = Field(default=False, alias="activationEnabled")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator(
        "event_id",
        "run_id",
        "workflow_id",
        "step_id",
        "route_decision",
        "checkpoint_id",
        "projection_mode",
        "terminal_state",
    )
    @classmethod
    def _validate_optional_refs(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "event ref")
        return _safe_ref(value, field_name=field_name)

    @field_validator("validator_statuses", "approval_gate_refs", mode="before")
    @classmethod
    def _normalize_ref_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("event refs must be arrays of safe strings")
        return tuple(value)  # type: ignore[arg-type]

    @field_validator("validator_statuses", "approval_gate_refs")
    @classmethod
    def _validate_ref_tuple(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "event refs")
        return tuple(_safe_ref(ref, field_name=field_name) for ref in value)

    @field_validator("effective_policy_snapshot_digest", "ledger_head_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _reject_raw_private_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(_canonical_metadata(value))

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return {key: _serialize_metadata_value(item) for key, item in _canonical_metadata(value).items()}

    @field_serializer(
        "event_id",
        "run_id",
        "workflow_id",
        "step_id",
        "route_decision",
        "checkpoint_id",
        "projection_mode",
        "terminal_state",
    )
    def _serialize_ref_field(self, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _safe_ref(value, field_name=getattr(info, "field_name", "event ref"))

    @field_serializer("validator_statuses", "approval_gate_refs")
    def _serialize_ref_tuple_field(self, value: tuple[str, ...], info: object) -> list[str]:
        field_name = getattr(info, "field_name", "event refs")
        return [_safe_ref(item, field_name=field_name) for item in value]

    @field_serializer("effective_policy_snapshot_digest", "ledger_head_digest")
    def _serialize_digest_field(self, value: str) -> str:
        return _require_digest(value)

    @field_serializer("event_type")
    def _serialize_event_type(self, value: str) -> str:
        if value not in _RUNTIME_EVENT_TYPES:
            raise ValueError("eventType value is not supported")
        return value

    @field_serializer("redaction_status")
    def _serialize_redaction_status(self, value: str) -> str:
        if value not in _REDACTION_STATUSES:
            raise ValueError("redactionStatus value is not supported")
        return value

    @field_serializer("activation_enabled")
    def _serialize_activation_enabled(self, value: bool) -> bool:
        if value is not False:
            raise ValueError("activationEnabled must remain false")
        return False


def project_event_for_dashboard(event: DeterministicRuntimeEvent) -> dict[str, object]:
    validated = DeterministicRuntimeEvent.model_validate(event.model_dump(by_alias=True, mode="json"))
    return validated.model_dump(by_alias=True, mode="json", exclude={"metadata"})


def _require_digest(value: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError("event digest fields must be sha256 digests")
    return value


def _safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not clean or not _SAFE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a safe public reference")
    _reject_private_text(clean, field_name)
    return clean


def _reject_private_text(value: str, field_name: str) -> None:
    lowered = value.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    if (
        any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS)
        or any(marker in lowered for marker in _RAW_MARKERS)
        or any(marker in compact for marker in _PROTECTED_COMPACT_MARKERS)
        or _looks_path_like(value, compact)
        or _looks_secret_shaped(value)
        or "/" in value
        or "\\" in value
        or value.strip().startswith(("~", "."))
    ):
        raise ValueError(f"runtime events must not contain {field_name}")


def _looks_path_like(value: str, compact: str) -> bool:
    if not any(sep in value for sep in (":", ".", "-")):
        return False
    if "users" in compact or "home" in compact:
        return True
    return any(
        marker in compact
        for marker in ("ssh", "idrsa", "kube", "kubeconfig", "varlib", "databots", "etcpasswd")
    ) or ("passwd" in compact and "etc" in compact) or (
        "env" in compact and any(marker in compact for marker in _PATHLIKE_COMPACT_MARKERS)
    )


def _looks_secret_shaped(value: str) -> bool:
    return any(pattern.fullmatch(value) for pattern in _SECRET_SHAPED_PATTERNS)


def _preflight_event_payload(payload: object) -> None:
    if isinstance(payload, DeterministicRuntimeEvent):
        payload = _raw_event_payload(payload)
    if not isinstance(payload, Mapping):
        raise _sanitized_validation_error("event", "event payload must be a mapping")

    allowed_aliases = {
        "eventId",
        "runId",
        "workflowId",
        "stepId",
        "eventType",
        "routeDecision",
        "effectivePolicySnapshotDigest",
        "ledgerHeadDigest",
        "checkpointId",
        "validatorStatuses",
        "approvalGateRefs",
        "repairAttempt",
        "projectionMode",
        "terminalState",
        "redactionStatus",
        "activationEnabled",
        "metadata",
    }
    allowed_names = {
        "event_id",
        "run_id",
        "workflow_id",
        "step_id",
        "event_type",
        "route_decision",
        "effective_policy_snapshot_digest",
        "ledger_head_digest",
        "checkpoint_id",
        "validator_statuses",
        "approval_gate_refs",
        "repair_attempt",
        "projection_mode",
        "terminal_state",
        "redaction_status",
        "activation_enabled",
        "metadata",
    }
    for key in payload:
        if not isinstance(key, str) or key not in allowed_aliases | allowed_names:
            raise _sanitized_validation_error("event", "event contains unsupported field")

    for alias, field_name, allow_none in (
        ("eventId", "event_id", False),
        ("runId", "run_id", False),
        ("workflowId", "workflow_id", False),
        ("stepId", "step_id", False),
        ("routeDecision", "route_decision", False),
        ("checkpointId", "checkpoint_id", True),
        ("projectionMode", "projection_mode", False),
        ("terminalState", "terminal_state", True),
    ):
        if alias in payload or field_name in payload:
            value = payload.get(alias, payload.get(field_name))
            if value is None and allow_none:
                continue
            if not isinstance(value, str):
                raise _sanitized_validation_error(alias, f"{alias} must be a safe public reference")
            try:
                _safe_ref(value, field_name=alias)
            except ValueError as exc:
                raise _sanitized_validation_error(alias, str(exc)) from None

    for alias, field_name in (
        ("effectivePolicySnapshotDigest", "effective_policy_snapshot_digest"),
        ("ledgerHeadDigest", "ledger_head_digest"),
    ):
        if alias in payload or field_name in payload:
            value = payload.get(alias, payload.get(field_name))
            if not isinstance(value, str):
                raise _sanitized_validation_error(alias, f"{alias} must be a sha256 digest")
            try:
                _require_digest(value)
            except ValueError as exc:
                raise _sanitized_validation_error(alias, str(exc)) from None

    _preflight_literal(payload, "eventType", "event_type", _RUNTIME_EVENT_TYPES)
    _preflight_literal(payload, "redactionStatus", "redaction_status", _REDACTION_STATUSES)

    if "activationEnabled" in payload or "activation_enabled" in payload:
        if payload.get("activationEnabled", payload.get("activation_enabled")) is not False:
            raise _sanitized_validation_error("activationEnabled", "activationEnabled must remain false")

    if "repairAttempt" in payload or "repair_attempt" in payload:
        value = payload.get("repairAttempt", payload.get("repair_attempt"))
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 100:
            raise _sanitized_validation_error("repairAttempt", "repairAttempt must be an integer from 0 to 100")

    for alias, field_name in (("validatorStatuses", "validator_statuses"), ("approvalGateRefs", "approval_gate_refs")):
        if alias not in payload and field_name not in payload:
            continue
        value = payload.get(alias, payload.get(field_name))
        if value is None:
            continue
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise _sanitized_validation_error(alias, f"{alias} must be safe public references")
        for item in value:
            if not isinstance(item, str):
                raise _sanitized_validation_error(alias, f"{alias} must be safe public references")
            try:
                _safe_ref(item, field_name=alias)
            except ValueError as exc:
                raise _sanitized_validation_error(alias, str(exc)) from None

    if "metadata" not in payload:
        return
    metadata = payload["metadata"]
    if not isinstance(metadata, Mapping):
        raise _sanitized_validation_error("metadata", "metadata must be a mapping")
    try:
        _canonical_metadata(metadata)
    except ValueError as exc:
        raise _sanitized_validation_error("metadata", str(exc)) from None


def _preflight_literal(payload: Mapping[object, object], alias: str, field_name: str, allowed: set[str]) -> None:
    if alias not in payload and field_name not in payload:
        return
    value = payload.get(alias, payload.get(field_name))
    if not isinstance(value, str) or value not in allowed:
        raise _sanitized_validation_error(alias, f"{alias} value is not supported")


def _sanitized_validation_error(field_name: str, message: str) -> ValidationError:
    return ValidationError.from_exception_data(
        "DeterministicRuntimeEvent",
        [
            {
                "type": "value_error",
                "loc": (field_name,),
                "input": None,
                "ctx": {"error": ValueError(message)},
            }
        ],
    )


def _sanitize_validation_error(exc: ValidationError) -> ValidationError:
    sanitized_errors = []
    for error in exc.errors():
        loc = error.get("loc") or ("event",)
        if isinstance(loc, str):
            loc = (loc,)
        sanitized_errors.append(
            {
                "type": "value_error",
                "loc": tuple(str(item) for item in loc),
                "input": None,
                "ctx": {"error": ValueError("event validation failed")},
            }
        )
    return ValidationError.from_exception_data(type(exc).__name__, sanitized_errors)


def _canonical_metadata(value: Mapping[str, object]) -> dict[str, object]:
    safe_metadata: dict[str, object] = {}
    for key in sorted(value, key=str):
        if not isinstance(key, str):
            raise ValueError("metadata keys must be safe public references")
        _reject_private_text(key, "raw prompt/output or credentials")
        safe_key = _safe_ref(key, field_name="metadata")
        safe_metadata[safe_key] = _safe_metadata_value(value[key])
    return safe_metadata


def _safe_metadata_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("metadata must contain only digest refs or safe primitive values")
        return value
    if isinstance(value, str):
        _reject_private_text(value, "raw prompt/output or credentials")
        if value.startswith(_DIGEST_PREFIX):
            return _require_digest(value)
        if _SAFE_REF_RE.fullmatch(value):
            return _safe_ref(value, field_name="metadata")
        raise ValueError("metadata must contain only digest refs or safe primitive values")
    if isinstance(value, tuple | list):
        return tuple(_safe_metadata_value(item) for item in value)
    raise ValueError("metadata must contain only digest refs or safe primitive values")


def _serialize_metadata_value(value: object) -> object:
    if isinstance(value, tuple | list):
        return [_serialize_metadata_value(item) for item in value]
    return value


def _raw_event_payload(event: DeterministicRuntimeEvent) -> dict[str, object]:
    return {
        "eventId": object.__getattribute__(event, "event_id"),
        "runId": object.__getattribute__(event, "run_id"),
        "workflowId": object.__getattribute__(event, "workflow_id"),
        "stepId": object.__getattribute__(event, "step_id"),
        "eventType": object.__getattribute__(event, "event_type"),
        "routeDecision": object.__getattribute__(event, "route_decision"),
        "effectivePolicySnapshotDigest": object.__getattribute__(event, "effective_policy_snapshot_digest"),
        "ledgerHeadDigest": object.__getattribute__(event, "ledger_head_digest"),
        "checkpointId": object.__getattribute__(event, "checkpoint_id"),
        "validatorStatuses": object.__getattribute__(event, "validator_statuses"),
        "approvalGateRefs": object.__getattribute__(event, "approval_gate_refs"),
        "repairAttempt": object.__getattribute__(event, "repair_attempt"),
        "projectionMode": object.__getattribute__(event, "projection_mode"),
        "terminalState": object.__getattribute__(event, "terminal_state"),
        "redactionStatus": object.__getattribute__(event, "redaction_status"),
        "activationEnabled": object.__getattribute__(event, "activation_enabled"),
        "metadata": object.__getattribute__(event, "metadata"),
    }
