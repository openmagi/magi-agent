from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class ArtifactBoundaryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    artifact_write_allowed: bool = Field(
        default=False,
        alias="artifactWriteAllowed",
    )
    blob_storage_location: Literal["external_ref_only"] = Field(
        default="external_ref_only",
        alias="blobStorageLocation",
    )

    @model_validator(mode="after")
    def _deny_artifact_write(self) -> Self:
        if self.artifact_write_allowed:
            raise ValueError("artifact write is disabled in this boundary")
        return self

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
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class ArtifactServiceBoundary:
    """Default-off OpenMagi boundary around ADK ArtifactService attachment."""

    def __init__(self, config: ArtifactBoundaryConfig) -> None:
        self.config = config

    def public_projection(self) -> dict[str, object]:
        reason_codes = []
        if not self.config.enabled:
            reason_codes.append("artifact_service_boundary_disabled")
        return {
            "enabled": self.config.enabled,
            "adkArtifactServiceAttached": False,
            "artifactWriteAllowed": False,
            "blobStorageLocation": "external_ref_only",
            "reasonCodes": reason_codes,
        }


class ArtifactAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    artifact_write_allowed: Literal[False] = Field(
        default=False,
        alias="artifactWriteAllowed",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, _value: object) -> dict[str, bool]:
        return {}

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
        "artifact_write_allowed",
        "production_storage_written",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False
