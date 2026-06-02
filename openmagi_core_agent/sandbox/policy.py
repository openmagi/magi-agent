from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


SandboxOperation = Literal[
    "read",
    "write",
    "execute",
    "network",
    "browser",
    "child_workspace",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@+-]{0,180}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,80}$")
_UNSAFE_TEXT_RE = re.compile(
    r"authorization|bearer|cookie|session|token|secret|credential|password|"
    r"private[_-]?key|api[_-]?key|connector[_-]?token|raw[_ -]?prompt|"
    r"raw[_ -]?output|hidden[_ -]?reasoning|/Users/|/\\.ssh|/\\.kube|"
    r"\\.env|AKIA[A-Z0-9]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9._-]{8,}",
    re.IGNORECASE,
)


def digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def digest_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def require_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("sandbox fields must use sha256 digests")
    return value


def require_safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not _SAFE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a safe public ref")
    reject_private_text(clean, field_name=field_name)
    return clean


def require_reason_code(value: str) -> str:
    clean = value.strip()
    if not _SAFE_REASON_RE.fullmatch(clean):
        raise ValueError("reason code must be a safe public ref")
    return clean


def reject_private_text(value: str, *, field_name: str) -> None:
    if _UNSAFE_TEXT_RE.search(value) or "\\" in value or value.startswith(("~", ".")):
        raise ValueError(f"{field_name} must not expose raw, private, or credential material")


def sanitize_validation_error(exc: ValidationError, *, title: str) -> ValidationError:
    sanitized_errors = []
    for _error in exc.errors(include_input=False):
        sanitized_errors.append(
            {
                "type": "value_error",
                "loc": ("sandbox",),
                "input": None,
                "ctx": {"error": ValueError("sandbox validation failed")},
            }
        )
    return ValidationError.from_exception_data(title, sanitized_errors)


class _SandboxModel(BaseModel):
    model_config = _MODEL_CONFIG

    def __init__(self, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=type(self).__name__) from None

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *args: object,
        **kwargs: object,
    ) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
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
            raise ValueError(f"copy update/include/exclude is disabled for {type(self).__name__}")
        return self.model_copy(deep=deep)


class SandboxAuthorityFlags(_SandboxModel):
    execution_attempted: Literal[False] = Field(default=False, alias="executionAttempted")
    filesystem_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAllowed",
    )
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    process_spawn_allowed: Literal[False] = Field(default=False, alias="processSpawnAllowed")
    browser_action_allowed: Literal[False] = Field(default=False, alias="browserActionAllowed")
    child_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="childWorkspaceMutationAllowed",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

    def public_projection(self) -> dict[str, bool]:
        return {
            "executionAttempted": False,
            "filesystemMutationAllowed": False,
            "networkCallAllowed": False,
            "processSpawnAllowed": False,
            "browserActionAllowed": False,
            "childWorkspaceMutationAllowed": False,
            "productionAuthority": False,
        }


class SandboxPolicy(_SandboxModel):
    workspace_root_digest: str = Field(alias="workspaceRootDigest")
    workspace_root: str = Field(alias="workspaceRoot", exclude=True)
    sealed_basenames: tuple[str, ...] = Field(
        default=("AGENTS.md", "CLAUDE.md", "HEARTBEAT.md", "SOUL.md", "TOOLS.md"),
        alias="sealedBasenames",
    )
    allow_network: bool = Field(default=False, alias="allowNetwork")
    network_allowlist: tuple[str, ...] = Field(default=(), alias="networkAllowlist")
    allow_process: bool = Field(default=False, alias="allowProcess")
    allowed_processes: tuple[str, ...] = Field(default=(), alias="allowedProcesses")
    require_process_sandbox: bool = Field(default=True, alias="requireProcessSandbox")
    max_wall_clock_ms: int = Field(default=120_000, alias="maxWallClockMs", ge=1_000)
    max_output_bytes: int = Field(default=1_000_000, alias="maxOutputBytes", ge=1)
    authority_flags: SandboxAuthorityFlags = Field(
        default_factory=SandboxAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def local_default(cls, *, workspaceRoot: str, **overrides: object) -> "SandboxPolicy":
        payload = {
            "workspaceRoot": workspaceRoot,
            "workspaceRootDigest": digest_text(_normalize_root(workspaceRoot)),
            "allowNetwork": False,
            "allowProcess": False,
            "requireProcessSandbox": True,
        }
        payload.update(overrides)
        return cls(**payload)

    @field_validator("workspace_root")
    @classmethod
    def _validate_workspace_root(cls, value: str) -> str:
        normalized = _normalize_root(value)
        if not normalized.startswith("/"):
            raise ValueError("workspaceRoot must be absolute")
        return normalized

    @field_validator("workspace_root_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("sealed_basenames", "network_allowlist", "allowed_processes")
    @classmethod
    def _validate_safe_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(require_safe_ref(item, field_name="policyRef") for item in value)


class SandboxDecision(_SandboxModel):
    allowed: bool
    operation: SandboxOperation
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    target_digest: str = Field(alias="targetDigest")
    target_kind: str = Field(default="sandbox_target", alias="targetKind")
    host: str | None = None
    sandbox_required: bool = Field(default=True, alias="sandboxRequired")
    max_wall_clock_ms: int | None = Field(default=None, alias="maxWallClockMs")
    max_output_bytes: int | None = Field(default=None, alias="maxOutputBytes")
    execution_attempted: Literal[False] = Field(default=False, alias="executionAttempted")
    filesystem_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAllowed",
    )
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    process_spawn_allowed: Literal[False] = Field(default=False, alias="processSpawnAllowed")
    browser_action_allowed: Literal[False] = Field(default=False, alias="browserActionAllowed")
    child_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="childWorkspaceMutationAllowed",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    authority_flags: SandboxAuthorityFlags = Field(
        default_factory=SandboxAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for alias in (
            "executionAttempted",
            "filesystemMutationAllowed",
            "networkCallAllowed",
            "processSpawnAllowed",
            "browserActionAllowed",
            "childWorkspaceMutationAllowed",
            "productionAuthority",
        ):
            payload[alias] = False
        return payload

    @field_validator("target_digest")
    @classmethod
    def _validate_target_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("target_kind", "host")
    @classmethod
    def _validate_safe_optional_ref(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(require_reason_code(reason) for reason in value)

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.sandbox.decision.public.v1",
            "allowed": self.allowed,
            "operation": self.operation,
            "reasonCodes": list(self.reason_codes),
            "targetDigest": self.target_digest,
            "targetKind": self.target_kind,
            "host": self.host,
            "sandboxRequired": self.sandbox_required,
            "maxWallClockMs": self.max_wall_clock_ms,
            "maxOutputBytes": self.max_output_bytes,
            "authorityFlags": self.authority_flags.public_projection(),
            "executionAttempted": False,
            "filesystemMutationAllowed": False,
            "networkCallAllowed": False,
            "processSpawnAllowed": False,
            "browserActionAllowed": False,
            "childWorkspaceMutationAllowed": False,
            "productionAuthority": False,
        }


def build_decision(
    *,
    allowed: bool,
    operation: SandboxOperation,
    reason_codes: tuple[str, ...],
    target_digest: str,
    target_kind: str,
    policy: SandboxPolicy,
    host: str | None = None,
) -> SandboxDecision:
    return SandboxDecision(
        allowed=allowed,
        operation=operation,
        reasonCodes=tuple(dict.fromkeys(reason_codes)),
        targetDigest=target_digest,
        targetKind=target_kind,
        host=host,
        sandboxRequired=True,
        maxWallClockMs=policy.max_wall_clock_ms,
        maxOutputBytes=policy.max_output_bytes,
    )


def _normalize_root(value: str) -> str:
    clean = value.replace("\\", "/").strip()
    while "//" in clean:
        clean = clean.replace("//", "/")
    return clean.rstrip("/") or "/"


__all__ = [
    "SandboxAuthorityFlags",
    "SandboxDecision",
    "SandboxOperation",
    "SandboxPolicy",
    "build_decision",
    "digest_payload",
    "digest_text",
    "reject_private_text",
    "require_digest",
    "require_reason_code",
    "require_safe_ref",
]
