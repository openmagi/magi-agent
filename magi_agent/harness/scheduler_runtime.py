from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.channels.contract import ChannelRef
from magi_agent.channels.dispatcher import (
    ChannelDispatchDecision,
    ChannelDispatchRequest,
    ChannelDispatcher,
    ChannelDispatchProviderPort,
)
from magi_agent.channels.runtime_boundary import ChannelRuntimeReceipt
from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.runtime.provider_receipts import provider_digest


SchedulerRuntimeStatus = Literal[
    "disabled",
    "blocked",
    "tick_intent",
    "tick_recorded_local_fake",
    "delivery_intent",
    "delivery_recorded_local_fake",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"(?:authorization|cookie|set-cookie|password|token|secret|credential|api[_-]?key)"
    r"\s*[:=]\s*[^,\s}{\n]{3,})",
    re.IGNORECASE,
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|/workspace(?:/[^,\s\"']*)?|"
    r"/data/bots(?:/[^,\s\"']*)?|/var/lib/kubelet(?:/[^,\s\"']*)?|"
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args)|hidden[_ -]?reasoning)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "auth",
    "cookie",
    "credential",
    "hidden",
    "key",
    "password",
    "path",
    "private",
    "production",
    "raw",
    "route",
    "secret",
    "token",
    "attached",
    "enabled",
    "authority",
)


class SchedulerRuntimeConfig(FalseOnlyAuthorityModel):
    enabled: bool = False
    local_fake_scheduler_enabled: bool = Field(default=False, alias="localFakeSchedulerEnabled")
    background_scheduler_attached: Literal[False] = Field(default=False, alias="backgroundSchedulerAttached")
    production_channel_write_enabled: Literal[False] = Field(default=False, alias="productionChannelWriteEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class SchedulerAuthorityFlags(FalseOnlyAuthorityModel):
    background_scheduler_attached: Literal[False] = Field(default=False, alias="backgroundSchedulerAttached")
    background_task_started: Literal[False] = Field(default=False, alias="backgroundTaskStarted")
    production_channel_write: Literal[False] = Field(default=False, alias="productionChannelWrite")
    channel_delivery_performed: Literal[False] = Field(default=False, alias="channelDeliveryPerformed")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class SchedulerLease(BaseModel):
    model_config = _MODEL_CONFIG

    lease_id: str = Field(alias="leaseId")
    owner_digest: str = Field(alias="ownerDigest")
    acquired_at: int = Field(alias="acquiredAt", ge=0)
    expires_at: int = Field(alias="expiresAt", ge=0)

    @field_validator("lease_id", "owner_digest")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)


class SchedulerTickRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    now: int = Field(ge=0)
    owner_digest: str = Field(alias="ownerDigest")
    due_refs: tuple[str, ...] = Field(default=(), alias="dueRefs")
    lease: SchedulerLease | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "owner_digest")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("due_refs")
    @classmethod
    def _validate_due_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)


class SchedulerDeliveryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    owner_digest: str = Field(alias="ownerDigest")
    source_ref: str = Field(alias="sourceRef")
    channel: ChannelRef
    provider_name: str = Field(alias="providerName")
    text: str
    bot_id_digest: str = Field(alias="botIdDigest")
    session_key_digest: str = Field(alias="sessionKeyDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "owner_digest", "source_ref", "provider_name", "bot_id_digest", "session_key_digest")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)


class SchedulerDueTurn(FalseOnlyAuthorityModel):
    source_ref: str = Field(alias="sourceRef")
    turn_ref: str = Field(alias="turnRef")
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")

    @field_validator("source_ref", "turn_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": _public_ref(self.source_ref, "source"),
            "turnRef": _public_ref(self.turn_ref, "turn"),
            "executionAllowed": False,
        }


class SchedulerTickDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: SchedulerRuntimeStatus
    request_digest: str = Field(alias="requestDigest")
    due_turns: tuple[SchedulerDueTurn, ...] = Field(default=(), alias="dueTurns")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: SchedulerAuthorityFlags = Field(default_factory=SchedulerAuthorityFlags, alias="authorityFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "requestDigest": self.request_digest,
            "dueTurns": [turn.public_projection() for turn in self.due_turns],
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class SchedulerDeliveryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: SchedulerRuntimeStatus
    request_digest: str = Field(alias="requestDigest")
    delivery_receipt: ChannelRuntimeReceipt | None = Field(default=None, alias="deliveryReceipt")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: SchedulerAuthorityFlags = Field(default_factory=SchedulerAuthorityFlags, alias="authorityFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "requestDigest": self.request_digest,
            "deliveryReceipt": None if self.delivery_receipt is None else self.delivery_receipt.public_projection(),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class SchedulerRuntimeBoundary:
    """Default-off scheduler decision boundary. It never starts a worker loop."""

    def __init__(self, config: SchedulerRuntimeConfig) -> None:
        self.config = config

    def tick(self, request: SchedulerTickRequest) -> SchedulerTickDecision:
        diagnostics = _diagnostics(self.config, request.metadata)
        digest = provider_digest({"requestId": request.request_id, "dueRefs": request.due_refs, "now": request.now})
        if not self.config.enabled:
            return _tick_decision("disabled", digest, ("scheduler_runtime_disabled",), diagnostics)
        if not self.config.local_fake_scheduler_enabled:
            return _tick_decision("tick_intent", digest, ("local_fake_scheduler_disabled",), diagnostics)
        lease_state = validate_scheduler_lease(
            request.lease, now_ms=request.now, owner_digest=request.owner_digest
        )
        if lease_state == "missing":
            return _tick_decision("blocked", digest, ("scheduler_lease_required",), diagnostics)
        if lease_state == "owner_mismatch":
            return _tick_decision("blocked", digest, ("scheduler_lease_owner_mismatch",), diagnostics)
        if lease_state == "stale":
            return _tick_decision("blocked", digest, ("scheduler_lease_stale",), diagnostics)
        seen: set[str] = set()
        turns: list[SchedulerDueTurn] = []
        for due_ref in request.due_refs:
            if due_ref in seen:
                continue
            seen.add(due_ref)
            turns.append(
                SchedulerDueTurn(
                    sourceRef=due_ref,
                    turnRef=f"scheduled-turn:{_short_digest(f'{due_ref}:{request.now}')}",
                )
            )
        return _tick_decision("tick_recorded_local_fake", digest, ("local_fake_scheduler_tick_receipt_only",), diagnostics, tuple(turns))

    def deliver(
        self,
        request: SchedulerDeliveryRequest,
        *,
        dispatcher: ChannelDispatcher,
        provider: ChannelDispatchProviderPort | None,
    ) -> SchedulerDeliveryDecision:
        diagnostics = _diagnostics(self.config, request.metadata)
        digest = provider_digest({"requestId": request.request_id, "sourceRef": request.source_ref, "channel": request.channel.model_dump(by_alias=True)})
        if not self.config.enabled:
            return _delivery_decision("disabled", digest, ("scheduler_runtime_disabled",), diagnostics)
        if not self.config.local_fake_scheduler_enabled:
            return _delivery_decision("delivery_intent", digest, ("local_fake_scheduler_disabled",), diagnostics)
        dispatch = dispatcher.dispatch(
            ChannelDispatchRequest(
                operation="dispatch.message",
                requestId=f"{request.request_id}:channel-dispatch",
                channel=request.channel,
                providerName=request.provider_name,
                botIdDigest=request.bot_id_digest,
                userIdDigest=request.owner_digest,
                sessionKeyDigest=request.session_key_digest,
                text=request.text,
            ),
            provider=provider,
        )
        return self._delivery_decision_from_dispatch(request, dispatch)

    def _delivery_decision_from_dispatch(
        self,
        request: SchedulerDeliveryRequest,
        dispatch: ChannelDispatchDecision,
    ) -> SchedulerDeliveryDecision:
        diagnostics = _diagnostics(self.config, request.metadata)
        digest = provider_digest({"requestId": request.request_id, "sourceRef": request.source_ref, "dispatch": dispatch.request_digest})
        if dispatch.status != "recorded_local_fake" or dispatch.receipt is None:
            return _delivery_decision(
                "blocked",
                digest,
                ("scheduled_channel_delivery_receipt_required",),
                diagnostics,
            )
        return _delivery_decision(
            "delivery_recorded_local_fake",
            digest,
            ("scheduled_channel_delivery_receipt_only",),
            diagnostics,
            dispatch.receipt,
        )


def _tick_decision(
    status: SchedulerRuntimeStatus,
    request_digest: str,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    due_turns: tuple[SchedulerDueTurn, ...] = (),
) -> SchedulerTickDecision:
    return SchedulerTickDecision(
        status=status,
        requestDigest=request_digest,
        dueTurns=due_turns,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=SchedulerAuthorityFlags(),
    )


def _delivery_decision(
    status: SchedulerRuntimeStatus,
    request_digest: str,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    receipt: ChannelRuntimeReceipt | None = None,
) -> SchedulerDeliveryDecision:
    return SchedulerDeliveryDecision(
        status=status,
        requestDigest=request_digest,
        deliveryReceipt=receipt,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=SchedulerAuthorityFlags(),
    )


def _diagnostics(config: SchedulerRuntimeConfig, metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeSchedulerEnabled": config.local_fake_scheduler_enabled,
        "backgroundSchedulerAttached": False,
        "productionChannelWriteEnabled": False,
        "routeAttached": False,
        **dict(metadata),
    }


def _safe_ref(value: str) -> str:
    clean = _safe_text(value.strip())
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("scheduler refs must be public identifiers")
    return clean


def _safe_text(value: str) -> str:
    if _SECRET_TEXT_RE.search(value) or _PRIVATE_TEXT_RE.search(value):
        return "[redacted]"
    return value[:4096]


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        raw_key = str(key)
        normalized = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
        if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean != "[redacted]":
                safe[raw_key[:80]] = clean
        elif isinstance(value, bool | int | float) or value is None:
            safe[raw_key[:80]] = value
    return safe


def _public_ref(value: str, prefix: str) -> str:
    return f"{prefix}:{_short_digest(value)}"


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def validate_scheduler_lease(
    lease: SchedulerLease | None,
    *,
    now_ms: int,
    owner_digest: str,
) -> str:
    """Shared three-branch lease validation used by both SchedulerRuntimeBoundary.tick()
    and scheduler_executor.tick().

    Returns one of: "valid", "missing", "owner_mismatch", "stale".
    """
    if lease is None:
        return "missing"
    if lease.owner_digest != owner_digest:
        return "owner_mismatch"
    if now_ms >= lease.expires_at:
        return "stale"
    return "valid"


__all__ = [
    "SchedulerAuthorityFlags",
    "SchedulerDeliveryDecision",
    "SchedulerDeliveryRequest",
    "SchedulerDueTurn",
    "SchedulerLease",
    "SchedulerRuntimeBoundary",
    "SchedulerRuntimeConfig",
    "SchedulerTickDecision",
    "SchedulerTickRequest",
    "validate_scheduler_lease",
]
