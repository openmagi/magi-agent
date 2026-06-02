from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.ops.safety import (
    require_digest,
    require_safe_ref,
    safe_metadata,
    serialize_safe_value,
)
from magi_agent.storage.durable_store import ArtifactIndexRecord


RenderFormat = Literal["pdf", "html", "png", "txt", "docx", "hwpx", "xlsx", "csv", "json"]
RenderVerificationStatus = Literal["verified_local_fake", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_ZERO_DIGEST = "sha256:" + "0" * 64


class LocalFakeRenderVerificationProvider:
    openmagi_local_fake_provider = True

    def __init__(self, result: Mapping[str, object] | None = None) -> None:
        self._result = dict(result or {})
        self.calls: list[dict[str, str]] = []

    def verify_render(self, request: ArtifactRenderRequest) -> Mapping[str, object]:
        request_digest = _digest_json(request.model_dump(by_alias=True, mode="json"))
        self.calls.append({"requestId": request.request_id, "requestDigest": request_digest})
        return dict(self._result)


class RenderVerificationConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_renderer_enabled: bool = Field(default=False, alias="localFakeRendererEnabled")
    production_storage_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionStorageWritesEnabled",
    )
    user_visible_render_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleRenderEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["productionStorageWritesEnabled"] = False
        values.pop("production_storage_writes_enabled", None)
        values["userVisibleRenderEnabled"] = False
        values.pop("user_visible_render_enabled", None)
        values["routeAttached"] = False
        values.pop("route_attached", None)
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        payload["productionStorageWritesEnabled"] = False
        payload["userVisibleRenderEnabled"] = False
        payload["routeAttached"] = False
        _ = deep
        return type(self).model_validate(payload)


class RenderVerificationAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    renderer_executed: Literal[False] = Field(default=False, alias="rendererExecuted")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    user_visible_render_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleRenderAllowed",
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

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "adk_artifact_service_attached",
        "renderer_executed",
        "production_storage_written",
        "user_visible_render_allowed",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ArtifactRenderRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    artifact_id: str = Field(alias="artifactId")
    artifact_ref: str = Field(alias="artifactRef")
    content_digest: str = Field(alias="contentDigest")
    render_format: RenderFormat = Field(alias="renderFormat")
    renderer_ref: str = Field(alias="rendererRef")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "artifact_id", "artifact_ref", "renderer_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="artifact render ref")

    @field_validator("content_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)


class RenderVerificationReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    artifact_id: str = Field(alias="artifactId")
    artifact_ref: str = Field(alias="artifactRef")
    content_digest: str = Field(alias="contentDigest")
    render_format: RenderFormat = Field(alias="renderFormat")
    renderer_ref: str = Field(alias="rendererRef")
    renderer_version_digest: str = Field(alias="rendererVersionDigest")
    render_output_digest: str = Field(alias="renderOutputDigest")
    render_preview_ref: str = Field(alias="renderPreviewRef")
    status: RenderVerificationStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    verified_at: datetime = Field(alias="verifiedAt")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    request_digest: str = Field(default=_ZERO_DIGEST, alias="requestDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    authority_flags: RenderVerificationAuthorityFlags = Field(
        default_factory=RenderVerificationAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = RenderVerificationAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="python")
        if update:
            payload.update(update)
        payload["authorityFlags"] = RenderVerificationAuthorityFlags()
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("request_id", "artifact_id", "artifact_ref", "renderer_ref", "render_preview_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="render receipt ref")

    @field_validator(
        "content_digest",
        "renderer_version_digest",
        "render_output_digest",
        "policy_snapshot_digest",
        "request_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(require_safe_ref(item, field_name="reason code") for item in value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @model_validator(mode="after")
    def _validate_verified_state(self) -> Self:
        if self.status == "verified_local_fake" and (
            self.renderer_version_digest == _ZERO_DIGEST
            or self.render_output_digest == _ZERO_DIGEST
            or self.request_digest == _ZERO_DIGEST
        ):
            raise ValueError("verified render receipt requires concrete digests")
        return self

    @property
    def render_receipt_digest(self) -> str:
        return _digest_json(self._digest_payload())

    def public_projection(self) -> dict[str, object]:
        verified_at = _iso_z(self.verified_at)
        return {
            "type": "artifact_render_verification",
            "requestId": self.request_id,
            "artifactId": self.artifact_id,
            "artifactRef": self.artifact_ref,
            "contentDigest": self.content_digest,
            "renderFormat": self.render_format,
            "rendererRef": self.renderer_ref,
            "rendererVersionDigest": self.renderer_version_digest,
            "renderOutputDigest": self.render_output_digest,
            "renderPreviewRef": self.render_preview_ref,
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "verifiedAt": verified_at,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "requestDigest": self.request_digest,
            "renderReceiptDigest": self.render_receipt_digest,
            "metadata": {
                key: serialize_safe_value(value)
                for key, value in safe_metadata(self.metadata).items()
            },
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }

    def to_artifact_index_record(self, *, blob_ref: str, size_bytes: int) -> ArtifactIndexRecord:
        if self.status != "verified_local_fake":
            raise ValueError("artifact index requires verified render receipt")
        return ArtifactIndexRecord(
            artifactId=self.artifact_id,
            contentDigest=self.content_digest,
            blobRef=blob_ref,
            sizeBytes=size_bytes,
            renderReceiptDigest=self.render_receipt_digest,
            metadata={
                "renderReceiptDigest": self.render_receipt_digest,
                "requestDigest": self.request_digest,
            },
        )

    def _digest_payload(self) -> dict[str, object]:
        return {
            "requestId": self.request_id,
            "artifactId": self.artifact_id,
            "artifactRef": self.artifact_ref,
            "contentDigest": self.content_digest,
            "renderFormat": self.render_format,
            "rendererRef": self.renderer_ref,
            "rendererVersionDigest": self.renderer_version_digest,
            "renderOutputDigest": self.render_output_digest,
            "renderPreviewRef": self.render_preview_ref,
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "verifiedAt": _iso_z(self.verified_at),
            "policySnapshotDigest": self.policy_snapshot_digest,
            "requestDigest": self.request_digest,
            "metadata": {
                key: serialize_safe_value(value)
                for key, value in safe_metadata(self.metadata).items()
            },
        }


class RenderVerificationBoundary:
    def __init__(self, config: RenderVerificationConfig | Mapping[str, object] | None = None) -> None:
        self.config = RenderVerificationConfig.model_validate(config or {})

    def verify(
        self,
        request: ArtifactRenderRequest,
        *,
        renderer: LocalFakeRenderVerificationProvider | None = None,
        now: datetime | None = None,
    ) -> RenderVerificationReceipt:
        if not self.config.enabled:
            return self._blocked(
                request,
                reason_codes=("render_verification_disabled",),
                now=now,
            )
        if not self.config.local_fake_renderer_enabled:
            return self._blocked(
                request,
                reason_codes=("local_fake_renderer_disabled",),
                now=now,
            )
        if renderer is None:
            return self._blocked(
                request,
                reason_codes=("local_fake_renderer_required",),
                now=now,
            )
        if type(renderer) is not LocalFakeRenderVerificationProvider:
            return self._blocked(
                request,
                reason_codes=("local_fake_renderer_untrusted",),
                now=now,
            )

        try:
            result = dict(renderer.verify_render(request))
            status = str(result.get("status", "error"))
        except Exception:
            return self._blocked(
                request,
                reason_codes=("renderer_receipt_invalid",),
                now=now,
            )
        if status != "ok":
            return self._blocked(
                request,
                reason_codes=("renderer_status_blocked",),
                now=now,
            )
        try:
            renderer_version_digest = require_digest(str(result.get("rendererVersionDigest", "")))
            render_output_digest = require_digest(str(result.get("renderOutputDigest", "")))
            render_preview_ref = require_safe_ref(
                str(result.get("renderPreviewRef", "")),
                field_name="render preview ref",
            )
        except ValueError:
            return self._blocked(
                request,
                reason_codes=("renderer_receipt_invalid",),
                now=now,
            )
        return RenderVerificationReceipt(
            requestId=request.request_id,
            artifactId=request.artifact_id,
            artifactRef=request.artifact_ref,
            contentDigest=request.content_digest,
            renderFormat=request.render_format,
            rendererRef=request.renderer_ref,
            rendererVersionDigest=renderer_version_digest,
            renderOutputDigest=render_output_digest,
            renderPreviewRef=render_preview_ref,
            status="verified_local_fake",
            reasonCodes=("render_verified_local_fake",),
            verifiedAt=now or datetime.now(UTC),
            policySnapshotDigest=request.policy_snapshot_digest,
            requestDigest=_digest_json(request.model_dump(by_alias=True, mode="json")),
            metadata=request.metadata,
            authorityFlags=RenderVerificationAuthorityFlags(),
        )

    def _blocked(
        self,
        request: ArtifactRenderRequest,
        *,
        reason_codes: tuple[str, ...],
        now: datetime | None,
    ) -> RenderVerificationReceipt:
        return RenderVerificationReceipt(
            requestId=request.request_id,
            artifactId=request.artifact_id,
            artifactRef=request.artifact_ref,
            contentDigest=request.content_digest,
            renderFormat=request.render_format,
            rendererRef=request.renderer_ref,
            rendererVersionDigest=_ZERO_DIGEST,
            renderOutputDigest=_ZERO_DIGEST,
            renderPreviewRef="render-preview:none",
            status="blocked",
            reasonCodes=reason_codes,
            verifiedAt=now or datetime.now(UTC),
            policySnapshotDigest=request.policy_snapshot_digest,
            requestDigest=_digest_json(request.model_dump(by_alias=True, mode="json")),
            metadata=request.metadata,
            authorityFlags=RenderVerificationAuthorityFlags(),
        )


def _digest_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _iso_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
