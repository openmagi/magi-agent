from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# C-9: credential-vocab consolidation. The lease-ref regex AND the
# sensitive-fragment denylist used to live here verbatim AND in
# ``sandbox/network.py`` (as ``_CREDENTIAL_QUERY_KEYS``) AND were
# implicit in ``connectors/credential_lease.py``. They are now homed in the
# stdlib-only leaf :mod:`magi_agent.security.credential_vocab` so the
# validator side (this file) and the SSRF side
# (:mod:`magi_agent.security.ssrf`) cannot drift on what counts as
# credential-shaped. The union direction means every lease ref this
# validator REJECTED before keeps getting rejected; new shapes only ADD to
# the rejection set.
from magi_agent.security.credential_vocab import (
    LEASE_REF_RE as _LEASE_RE,
    SENSITIVE_LEASE_FRAGMENTS as _SENSITIVE_LEASE_FRAGMENTS,
)


CredentialSource = Literal["platform", "user", "plugin", "environment"]
CredentialDestination = Literal["sandbox", "tool", "provider", "mcp"]

_CREDENTIAL_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_PUBLIC_REASON_CODES = {
    "credential_lease_allowed",
    "credential_lease_required",
    "credential_not_allowlisted",
    "invalid_credential_lease_ref",
    "invalid_credential_name",
    "raw_credential_value_rejected",
}
_CREDENTIAL_SHAPE_RES = (
    re.compile(r"^A[KS]IA[0-9A-Z]{16}$"),
)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    credential_name: str = Field(alias="credentialName")
    source: CredentialSource
    destination: CredentialDestination
    lease_ref: str | None = Field(default=None, alias="leaseRef")
    raw_value: str | None = Field(default=None, alias="rawValue")

    def __init__(self, **data: object) -> None:
        if data.get("rawValue") is not None or data.get("raw_value") is not None:
            raise ValueError(
                "raw credential values are not accepted by security policy contracts",
            )
        super().__init__(**data)

    @field_validator("credential_name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if _public_credential_name(value) == "redacted":
            raise ValueError("credential name must be an uppercase env-style ref")
        return value

    @field_validator("lease_ref")
    @classmethod
    def _validate_lease_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _LEASE_RE.fullmatch(value):
            raise ValueError("credential lease ref must use credential-lease:<scope>")
        return value

    @model_validator(mode="after")
    def _reject_raw_value(self) -> CredentialRequest:
        if self.raw_value is not None:
            raise ValueError(
                "raw credential values are not accepted by security policy contracts",
            )
        return self


class CredentialPassThroughPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    allowed_names: tuple[str, ...] = Field(default=(), alias="allowedNames")

    @field_validator("allowed_names")
    @classmethod
    def _validate_allowed_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for credential_name in value:
            if _public_credential_name(credential_name) == "redacted":
                raise ValueError("allowed credential names must be env-style refs")
        return value


class CredentialDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    allowed: bool
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    request: CredentialRequest
    decision_digest: str = Field(default="", alias="decisionDigest")

    def public_projection(self) -> dict[str, object]:
        credential_name = _public_credential_name(
            getattr(self.request, "credential_name", "redacted"),
        )
        source = _public_source(getattr(self.request, "source", "unknown"))
        destination = _public_destination(
            getattr(self.request, "destination", "unknown"),
        )
        lease_ref = _public_lease_ref(getattr(self.request, "lease_ref", None))
        reason_codes = _public_reason_codes(self.reason_codes)
        allowed = (
            self.allowed is True
            and credential_name != "redacted"
            and source != "unknown"
            and destination != "unknown"
            and lease_ref is not None
            and reason_codes == ["credential_lease_allowed"]
            and getattr(self.request, "raw_value", None) is None
            and self.decision_digest
            == _decision_digest(True, ("credential_lease_allowed",), self.request)
        )
        projection: dict[str, object] = {
            "credentialName": credential_name,
            "source": source,
            "destination": destination,
            "allowed": allowed,
            "reasonCodes": reason_codes,
        }
        if allowed and lease_ref is not None:
            projection["leaseRef"] = lease_ref
        return projection


def evaluate_credential_request(
    request: CredentialRequest,
    policy: CredentialPassThroughPolicy,
) -> CredentialDecision:
    if getattr(request, "raw_value", None) is not None:
        return _make_decision(
            allowed=False,
            reason_codes=("raw_credential_value_rejected",),
            request=request,
        )
    if _public_credential_name(request.credential_name) == "redacted":
        return _make_decision(
            allowed=False,
            reason_codes=("invalid_credential_name",),
            request=request,
        )
    allowed_names = (
        policy.allowed_names if isinstance(policy.allowed_names, tuple) else ()
    )
    public_allowed_names = {
        credential_name
        for credential_name in allowed_names
        if _public_credential_name(credential_name) != "redacted"
    }
    if request.credential_name not in public_allowed_names:
        return _make_decision(
            allowed=False,
            reason_codes=("credential_not_allowlisted",),
            request=request,
        )
    if request.lease_ref is None:
        return _make_decision(
            allowed=False,
            reason_codes=("credential_lease_required",),
            request=request,
        )
    if _public_lease_ref(request.lease_ref) is None:
        return _make_decision(
            allowed=False,
            reason_codes=("invalid_credential_lease_ref",),
            request=request,
        )
    return _make_decision(
        allowed=True,
        reason_codes=("credential_lease_allowed",),
        request=request,
    )


def _make_decision(
    *,
    allowed: bool,
    reason_codes: tuple[str, ...],
    request: CredentialRequest,
) -> CredentialDecision:
    return CredentialDecision(
        allowed=allowed,
        reasonCodes=reason_codes,
        request=request,
        decisionDigest=_decision_digest(allowed, reason_codes, request),
    )


def _decision_digest(
    allowed: bool,
    reason_codes: tuple[str, ...],
    request: CredentialRequest,
) -> str:
    payload = {
        "allowed": allowed,
        "credentialName": _public_credential_name(
            getattr(request, "credential_name", "redacted"),
        ),
        "destination": _public_destination(getattr(request, "destination", "unknown")),
        "leaseRef": _public_lease_ref(getattr(request, "lease_ref", None)),
        "rawValuePresent": getattr(request, "raw_value", None) is not None,
        "reasonCodes": _public_reason_codes(reason_codes),
        "schema": "openmagi.credentialDecision.v1",
        "source": _public_source(getattr(request, "source", "unknown")),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _public_credential_name(credential_name: object) -> str:
    value = str(credential_name)
    if _CREDENTIAL_NAME_RE.fullmatch(value) and not _looks_secret_shaped_name(value):
        return value
    return "redacted"


def _public_source(source: object) -> str:
    value = str(source)
    if value in {"platform", "user", "plugin", "environment"}:
        return value
    return "unknown"


def _public_destination(destination: object) -> str:
    value = str(destination)
    if value in {"sandbox", "tool", "provider", "mcp"}:
        return value
    return "unknown"


def _public_lease_ref(lease_ref: object) -> str | None:
    if lease_ref is None:
        return None
    value = str(lease_ref)
    if _LEASE_RE.fullmatch(value) and not _looks_sensitive_lease_ref(value):
        return value
    return None


def _public_reason_codes(reason_codes: object) -> list[str]:
    if not isinstance(reason_codes, tuple):
        return ["redacted"]
    public: list[str] = []
    for reason_code in reason_codes:
        value = str(reason_code)
        if value in _PUBLIC_REASON_CODES:
            public.append(value)
        else:
            public.append("redacted")
    return list(dict.fromkeys(public))


def _looks_sensitive_lease_ref(lease_ref: str) -> bool:
    normalized = lease_ref.casefold()
    return any(fragment in normalized for fragment in _SENSITIVE_LEASE_FRAGMENTS)


def _looks_secret_shaped_name(credential_name: str) -> bool:
    return any(pattern.fullmatch(credential_name) for pattern in _CREDENTIAL_SHAPE_RES)
