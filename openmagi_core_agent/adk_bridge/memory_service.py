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


class MemoryBoundaryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    adk_memory_service_attached: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceAttached",
    )
    recall_allowed: bool = Field(default=False, alias="recallAllowed")
    write_allowed: bool = Field(default=False, alias="writeAllowed")
    prompt_projection_allowed: bool = Field(
        default=False,
        alias="promptProjectionAllowed",
    )

    @model_validator(mode="after")
    def _deny_write(self) -> Self:
        if self.write_allowed:
            raise ValueError("memory write is disabled in this boundary")
        if self.prompt_projection_allowed:
            raise ValueError("memory prompt projection is disabled in this boundary")
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


class MemoryServiceBoundary:
    """Default-off OpenMagi boundary around ADK MemoryService attachment."""

    def __init__(self, config: MemoryBoundaryConfig) -> None:
        self.config = config

    def public_projection(self) -> dict[str, object]:
        reason_codes = []
        if not self.config.enabled:
            reason_codes.append("memory_service_boundary_disabled")
        elif not self.config.recall_allowed:
            reason_codes.append("memory_recall_not_admitted")
        return {
            "enabled": self.config.enabled,
            "adkMemoryServiceAttached": False,
            "recallAllowed": self.config.recall_allowed and self.config.enabled,
            "writeAllowed": False,
            "promptProjectionAllowed": False,
            "reasonCodes": reason_codes,
        }


class MemoryAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_memory_service_attached: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceAttached",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
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
        "adk_memory_service_attached",
        "memory_write_allowed",
        "prompt_projection_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False
