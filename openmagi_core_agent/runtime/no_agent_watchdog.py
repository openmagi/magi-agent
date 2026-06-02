from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from openmagi_core_agent.ops.safety import reject_private_text, safe_metadata


NoAgentWatchdogStatus = Literal[
    "silent_healthy",
    "alert_output",
    "alert_failure",
    "alert_timeout",
    "blocked_recursive_scheduler",
]
NoAgentWatchdogAlertKind = Literal[
    "none",
    "output",
    "failure",
    "timeout",
    "recursive_scheduler_denied",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@+-]{1,180}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,80}$")
_AUTHORITY_REF_PREFIXES = (
    "activation",
    "authority",
    "browser",
    "channel",
    "child",
    "db",
    "env",
    "gate2",
    "gate8",
    "k8s",
    "kubernetes",
    "live",
    "memory",
    "missionruntime",
    "model",
    "provider",
    "route",
    "scheduler",
    "tool",
    "traffic",
    "workspace",
)
_AUTHORITY_METADATA_PREFIXES = _AUTHORITY_REF_PREFIXES + (
    "capability",
    "execution",
    "permission",
    "production",
    "script",
    "wakeagent",
)
_SECRET_TEXT_RE = re.compile(
    r"(?:authorization\s*:\s*bearer\s+\S+|bearer\s+\S+|"
    r"gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
    r"xox[a-z]-[A-Za-z0-9._-]{8,}|AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]{8,}|sk-(?:live|test)?[-_A-Za-z0-9]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_OUTPUT_RE = re.compile(
    r"raw[_ -]?(?:output|result|tool|prompt|transcript|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning",
    re.IGNORECASE,
)


class NoAgentWatchdogAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    wake_agent: Literal[False] = Field(default=False, alias="wakeAgent")
    model_call_enabled: Literal[False] = Field(default=False, alias="modelCallEnabled")
    provider_call_enabled: Literal[False] = Field(default=False, alias="providerCallEnabled")
    tool_execution_enabled: Literal[False] = Field(default=False, alias="toolExecutionEnabled")
    child_execution_enabled: Literal[False] = Field(default=False, alias="childExecutionEnabled")
    channel_delivery_enabled: Literal[False] = Field(default=False, alias="channelDeliveryEnabled")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_write_enabled: Literal[False] = Field(default=False, alias="memoryWriteEnabled")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for alias, field_name in (
            ("wakeAgent", "wake_agent"),
            ("modelCallEnabled", "model_call_enabled"),
            ("providerCallEnabled", "provider_call_enabled"),
            ("toolExecutionEnabled", "tool_execution_enabled"),
            ("childExecutionEnabled", "child_execution_enabled"),
            ("channelDeliveryEnabled", "channel_delivery_enabled"),
            ("schedulerAttached", "scheduler_attached"),
            ("workspaceMutationEnabled", "workspace_mutation_enabled"),
            ("memoryWriteEnabled", "memory_write_enabled"),
            ("productionWritesEnabled", "production_writes_enabled"),
        ):
            payload[alias] = False
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

    @field_serializer(
        "wake_agent",
        "model_call_enabled",
        "provider_call_enabled",
        "tool_execution_enabled",
        "child_execution_enabled",
        "channel_delivery_enabled",
        "scheduler_attached",
        "workspace_mutation_enabled",
        "memory_write_enabled",
        "production_writes_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class NoAgentWatchdogRequest(BaseModel):
    model_config = _MODEL_CONFIG

    watchdog_id: str = Field(alias="watchdogId")
    tick_id: str = Field(alias="tickId")
    job_ref: str = Field(alias="jobRef")
    stdout: str = ""
    exit_code: int = Field(default=0, alias="exitCode", ge=0, le=255)
    timed_out: bool = Field(default=False, alias="timedOut")
    wake_agent: Literal[False] = Field(default=False, alias="wakeAgent")
    recursive_scheduler_requested: bool = Field(
        default=False,
        alias="recursiveSchedulerRequested",
    )
    duration_ms: int = Field(default=0, alias="durationMs", ge=0, le=86_400_000)
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _force_no_agent_mode(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        payload["wakeAgent"] = False
        payload.pop("wake_agent", None)
        return payload

    @field_validator("watchdog_id")
    @classmethod
    def _validate_watchdog_id(cls, value: str) -> str:
        return _safe_prefixed_ref(value, field_name="watchdogId", prefix="watchdog:")

    @field_validator("tick_id")
    @classmethod
    def _validate_tick_id(cls, value: str) -> str:
        return _safe_prefixed_ref(value, field_name="tickId", prefix="tick:")

    @field_validator("job_ref")
    @classmethod
    def _validate_job_ref(cls, value: str) -> str:
        return _safe_prefixed_ref(value, field_name="jobRef", prefix="job:")

    @field_validator("stdout")
    @classmethod
    def _sanitize_stdout(cls, value: str) -> str:
        return _sanitize_output(value)[:500]

    @field_validator("exit_code", "duration_ms", mode="before")
    @classmethod
    def _reject_bool_ints(cls, value: object, info: object) -> object:
        if isinstance(value, bool):
            raise ValueError(f"{getattr(info, 'field_name', 'integer')} must be an integer")
        return value

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        for key in value:
            if not isinstance(key, str):
                raise ValueError("metadata keys must be strings")
            if _is_authority_shaped(key):
                raise ValueError("metadata keys must not imply live authority")
        return safe_metadata(value)


class NoAgentWatchdogDecision(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["openmagi.runtime.no_agent_watchdog.v1"] = Field(
        default="openmagi.runtime.no_agent_watchdog.v1",
        alias="schemaVersion",
    )
    status: NoAgentWatchdogStatus
    alert_kind: NoAgentWatchdogAlertKind = Field(alias="alertKind")
    watchdog_id: str = Field(alias="watchdogId")
    tick_id: str = Field(alias="tickId")
    job_ref: str = Field(alias="jobRef")
    wake_agent: Literal[False] = Field(default=False, alias="wakeAgent")
    alert_required: bool = Field(alias="alertRequired")
    stdout_digest: str | None = Field(default=None, alias="stdoutDigest")
    stdout_preview: str | None = Field(default=None, alias="stdoutPreview")
    exit_code: int = Field(alias="exitCode", ge=0, le=255)
    timed_out: bool = Field(alias="timedOut")
    recursive_scheduler_denied: bool = Field(
        default=False,
        alias="recursiveSchedulerDenied",
    )
    duration_ms: int = Field(default=0, alias="durationMs", ge=0)
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: NoAgentWatchdogAuthorityFlags = Field(
        default_factory=NoAgentWatchdogAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_no_agent_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        payload["wakeAgent"] = False
        payload.pop("wake_agent", None)
        payload["authorityFlags"] = NoAgentWatchdogAuthorityFlags()
        return payload

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
        if update:
            raise ValueError("watchdog decisions do not accept unsafe copy updates")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="python"))

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if include is not None or exclude is not None:
            raise ValueError("watchdog decisions do not accept unsafe copy projections")
        return self.model_copy(update=update, deep=deep)

    @field_validator("watchdog_id")
    @classmethod
    def _validate_watchdog_id(cls, value: str) -> str:
        return _safe_prefixed_ref(value, field_name="watchdogId", prefix="watchdog:")

    @field_validator("tick_id")
    @classmethod
    def _validate_tick_id(cls, value: str) -> str:
        return _safe_prefixed_ref(value, field_name="tickId", prefix="tick:")

    @field_validator("job_ref")
    @classmethod
    def _validate_job_ref(cls, value: str) -> str:
        return _safe_prefixed_ref(value, field_name="jobRef", prefix="job:")

    @field_validator("stdout_preview")
    @classmethod
    def _sanitize_stdout_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sanitize_output(value)[:240]

    @field_validator("stdout_digest")
    @classmethod
    def _validate_stdout_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _DIGEST_RE.fullmatch(value) is None:
            raise ValueError("stdoutDigest must be a sha256 digest")
        return value

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or isinstance(value, Mapping):
            raise ValueError("reasonCodes must be an array of public reason codes")
        reason_codes: list[str] = []
        for item in tuple(value):
            if not isinstance(item, str):
                raise ValueError("reasonCodes must contain public reason codes")
            clean = item.strip()
            if _REASON_CODE_RE.fullmatch(clean) is None or _is_authority_shaped(clean):
                raise ValueError("reasonCodes must contain public reason codes")
            reason_codes.append(clean)
        return tuple(reason_codes)

    def public_projection(self) -> dict[str, object]:
        stdout_preview = (
            None
            if self.stdout_preview is None
            else _sanitize_output(str(self.stdout_preview))[:240]
        )
        return {
            "schemaVersion": "openmagi.runtime.no_agent_watchdog.public.v1",
            "status": self.status,
            "alertKind": self.alert_kind,
            "watchdogId": _public_prefixed_ref(
                str(self.watchdog_id),
                field_name="watchdogId",
                prefix="watchdog:",
            ),
            "tickId": _public_prefixed_ref(
                str(self.tick_id),
                field_name="tickId",
                prefix="tick:",
            ),
            "jobRef": _public_prefixed_ref(
                str(self.job_ref),
                field_name="jobRef",
                prefix="job:",
            ),
            "wakeAgent": False,
            "alertRequired": self.alert_required,
            "stdoutDigest": _public_digest(self.stdout_digest, fallback=stdout_preview),
            "stdoutPreview": None,
            "exitCode": self.exit_code,
            "timedOut": self.timed_out,
            "recursiveSchedulerDenied": self.recursive_scheduler_denied,
            "durationMs": self.duration_ms,
            "reasonCodes": [_public_reason_code(item) for item in self.reason_codes],
            "publicSafe": True,
            "liveAuthority": False,
            "trafficAttached": False,
            "authorityFlags": _authority_flags_projection(self.authority_flags),
        }


def evaluate_no_agent_watchdog(request: NoAgentWatchdogRequest) -> NoAgentWatchdogDecision:
    output = request.stdout.strip()
    output_digest = _digest_text(output) if output else None
    output_preview = output[:240] if output else None

    if request.recursive_scheduler_requested:
        return _decision(
            request,
            status="blocked_recursive_scheduler",
            alert_kind="recursive_scheduler_denied",
            alert_required=True,
            stdout_digest=output_digest,
            stdout_preview=output_preview,
            recursive_scheduler_denied=True,
            reason_codes=("recursive_scheduler_denied",),
        )
    if request.timed_out:
        return _decision(
            request,
            status="alert_timeout",
            alert_kind="timeout",
            alert_required=True,
            reason_codes=("timeout_failure",),
        )
    if request.exit_code != 0:
        return _decision(
            request,
            status="alert_failure",
            alert_kind="failure",
            alert_required=True,
            reason_codes=("non_zero_exit",),
        )
    if output:
        return _decision(
            request,
            status="alert_output",
            alert_kind="output",
            alert_required=True,
            stdout_digest=output_digest,
            stdout_preview=output_preview,
            reason_codes=("non_empty_output",),
        )
    return _decision(
        request,
        status="silent_healthy",
        alert_kind="none",
        alert_required=False,
        reason_codes=("empty_output_success",),
    )


def _decision(
    request: NoAgentWatchdogRequest,
    *,
    status: NoAgentWatchdogStatus,
    alert_kind: NoAgentWatchdogAlertKind,
    alert_required: bool,
    reason_codes: tuple[str, ...],
    stdout_digest: str | None = None,
    stdout_preview: str | None = None,
    recursive_scheduler_denied: bool = False,
) -> NoAgentWatchdogDecision:
    return NoAgentWatchdogDecision(
        status=status,
        alertKind=alert_kind,
        watchdogId=request.watchdog_id,
        tickId=request.tick_id,
        jobRef=request.job_ref,
        wakeAgent=False,
        alertRequired=alert_required,
        stdoutDigest=stdout_digest,
        stdoutPreview=stdout_preview,
        exitCode=request.exit_code,
        timedOut=request.timed_out,
        recursiveSchedulerDenied=recursive_scheduler_denied,
        durationMs=request.duration_ms,
        reasonCodes=reason_codes,
        authorityFlags=NoAgentWatchdogAuthorityFlags(),
    )


def _safe_prefixed_ref(value: str, *, field_name: str, prefix: str) -> str:
    clean = value.strip()
    reject_private_text(clean, field_name=field_name)
    if not clean.startswith(prefix) or len(clean) == len(prefix):
        raise ValueError(f"{field_name} must use {prefix} public ref")
    if _PUBLIC_REF_RE.fullmatch(clean) is None:
        raise ValueError(f"{field_name} must be a safe public ref")
    suffix = clean.removeprefix(prefix)
    if _is_authority_shaped(suffix):
        raise ValueError(f"{field_name} must not imply live authority")
    return clean


def _public_prefixed_ref(value: str, *, field_name: str, prefix: str) -> str:
    try:
        return _safe_prefixed_ref(value, field_name=field_name, prefix=prefix)
    except ValueError:
        return f"{prefix}{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _sanitize_output(value: str) -> str:
    stripped = value.strip()
    stripped = _RAW_OUTPUT_RE.sub("watchdog-output", stripped)
    stripped = _SECRET_TEXT_RE.sub("[redacted-secret]", stripped)
    stripped = _PRIVATE_PATH_RE.sub("[redacted-path]", stripped)
    return " ".join(stripped.split())


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _public_digest(value: str | None, *, fallback: object) -> str | None:
    if value is not None and _DIGEST_RE.fullmatch(str(value)):
        return str(value)
    if fallback is None:
        return None
    return _digest_text(str(fallback))


def _public_reason_code(value: str) -> str:
    clean = str(value).strip()
    if _REASON_CODE_RE.fullmatch(clean) is not None and not _is_authority_shaped(clean):
        return clean
    return f"reason:{hashlib.sha1(clean.encode('utf-8')).hexdigest()[:16]}"


def _authority_flags_projection(value: object) -> dict[str, bool]:
    if isinstance(value, NoAgentWatchdogAuthorityFlags):
        return value.model_dump(by_alias=True)
    return NoAgentWatchdogAuthorityFlags().model_dump(by_alias=True)


def _is_authority_shaped(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", value.strip().lower())
    return any(normalized.startswith(prefix) for prefix in _AUTHORITY_METADATA_PREFIXES)


__all__ = [
    "NoAgentWatchdogAuthorityFlags",
    "NoAgentWatchdogDecision",
    "NoAgentWatchdogRequest",
    "evaluate_no_agent_watchdog",
]
