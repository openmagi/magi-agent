from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.web_acquisition.policy import normalize_public_url, url_policy_error


WebAcquisitionContractStatus = Literal["fetchable", "blocked", "approval_required"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")
_SENSITIVE_APPROVAL_MARKERS = frozenset({"login", "oauth", "paywall"})
_BLOCKED_MARKERS = frozenset({"captcha"})


class WebAcquisitionAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    network_accessed: Literal[False] = Field(default=False, alias="networkAccessed")
    browser_started: Literal[False] = Field(default=False, alias="browserStarted")
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


class WebAcquisitionContractRequest(BaseModel):
    model_config = _MODEL_CONFIG

    url: str = Field(repr=False)
    redirect_targets: tuple[str, ...] = Field(default=(), alias="redirectTargets", repr=False)
    flow_markers: tuple[str, ...] = Field(default=(), alias="flowMarkers")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("url is required")
        return cleaned

    @field_validator("redirect_targets")
    @classmethod
    def _validate_redirect_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item.strip() for item in value if item.strip())

    @field_validator("flow_markers")
    @classmethod
    def _validate_flow_markers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        markers = []
        for item in value:
            marker = re.sub(r"[^a-z0-9_-]", "", item.casefold())
            if marker:
                markers.append(marker)
        return tuple(markers)

    @field_serializer("url")
    def _serialize_url(self, value: str) -> str:
        return _digest(value)

    @field_serializer("redirect_targets")
    def _serialize_redirect_targets(self, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_digest(item) for item in value)


class WebAcquisitionContractDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: WebAcquisitionContractStatus
    fetchable: bool
    normalized_url_digest: str = Field(alias="normalizedUrlDigest")
    fetch_request_ref: str = Field(alias="fetchRequestRef")
    redirect_target_digests: tuple[str, ...] = Field(default=(), alias="redirectTargetDigests")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    adk_tool: Mapping[str, object] = Field(default_factory=dict, alias="adkTool")
    authority_flags: WebAcquisitionAuthorityFlags = Field(
        default_factory=WebAcquisitionAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("normalized_url_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("normalizedUrlDigest must be sha256")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REASON_CODE_RE.fullmatch(item):
                raise ValueError("reason codes must be public identifiers")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "fetchable": self.fetchable,
            "normalizedUrlDigest": self.normalized_url_digest,
            "fetchRequestRef": self.fetch_request_ref,
            "redirectTargetDigests": self.redirect_target_digests,
            "reasonCodes": self.reason_codes,
            "adkTool": dict(self.adk_tool),
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def classify_web_acquisition_request(
    request: WebAcquisitionContractRequest,
) -> WebAcquisitionContractDecision:
    primary_error = url_policy_error(request.url)
    normalized_digest = _normalized_url_digest(request.url, primary_error=primary_error)
    fetch_ref = _fetch_ref(
        {
            "normalizedUrlDigest": normalized_digest,
            "redirectTargetDigests": [_digest(item) for item in request.redirect_targets],
        }
    )
    if primary_error is not None:
        return _decision(
            "blocked",
            normalized_url_digest=normalized_digest,
            fetch_request_ref=fetch_ref,
            reason_codes=(primary_error,),
            redirect_target_digests=tuple(_digest(item) for item in request.redirect_targets),
        )

    redirect_error = _redirect_policy_error(request.redirect_targets)
    if redirect_error is not None:
        return _decision(
            "blocked",
            normalized_url_digest=normalized_digest,
            fetch_request_ref=fetch_ref,
            reason_codes=("unsafe_redirect_target_blocked", redirect_error),
            redirect_target_digests=tuple(_digest(item) for item in request.redirect_targets),
        )

    marker_set = set(request.flow_markers)
    blocked_markers = marker_set.intersection(_BLOCKED_MARKERS)
    if blocked_markers:
        return _decision(
            "blocked",
            normalized_url_digest=normalized_digest,
            fetch_request_ref=fetch_ref,
            reason_codes=(f"{sorted(blocked_markers)[0]}_flow_blocked",),
            redirect_target_digests=tuple(_digest(item) for item in request.redirect_targets),
        )
    sensitive_markers = marker_set.intersection(_SENSITIVE_APPROVAL_MARKERS)
    if sensitive_markers:
        return _decision(
            "approval_required",
            normalized_url_digest=normalized_digest,
            fetch_request_ref=fetch_ref,
            reason_codes=("sensitive_flow_requires_approval", sorted(sensitive_markers)[0]),
            redirect_target_digests=tuple(_digest(item) for item in request.redirect_targets),
        )

    return _decision(
        "fetchable",
        normalized_url_digest=normalized_digest,
        fetch_request_ref=fetch_ref,
        redirect_target_digests=tuple(_digest(item) for item in request.redirect_targets),
    )


def web_fetch_function_tool_metadata() -> dict[str, object]:
    return {
        "name": "WebFetch",
        "adkToolType": "FunctionTool",
        "enabledByDefault": False,
        "handlerAttached": False,
        "providerCallAttached": False,
        "description": "Represent a policy-approved web fetch request without provider dispatch.",
        "inputSchema": {
            "type": "object",
            "required": ["url"],
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string", "minLength": 1},
                "redirectTargets": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "flowMarkers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    }


def _decision(
    status: WebAcquisitionContractStatus,
    *,
    normalized_url_digest: str,
    fetch_request_ref: str,
    redirect_target_digests: tuple[str, ...],
    reason_codes: tuple[str, ...] = (),
) -> WebAcquisitionContractDecision:
    return WebAcquisitionContractDecision(
        status=status,
        fetchable=status == "fetchable",
        normalizedUrlDigest=normalized_url_digest,
        fetchRequestRef=fetch_request_ref,
        redirectTargetDigests=redirect_target_digests,
        reasonCodes=reason_codes,
        adkTool=web_fetch_function_tool_metadata(),
    )


def _redirect_policy_error(redirect_targets: tuple[str, ...]) -> str | None:
    for target in redirect_targets:
        error = url_policy_error(target)
        if error is not None:
            return error
    return None


def _normalized_url_digest(url: str, *, primary_error: str | None) -> str:
    if primary_error is None:
        return _digest(normalize_public_url(url))
    return _digest(url)


def _fetch_ref(material: object) -> str:
    return f"web-fetch:{_digest(material)}"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "WebAcquisitionAuthorityFlags",
    "WebAcquisitionContractDecision",
    "WebAcquisitionContractRequest",
    "classify_web_acquisition_request",
    "web_fetch_function_tool_metadata",
]
