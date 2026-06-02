from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WorkflowStatus = Literal["draft", "staging", "active", "deprecated", "disabled"]
_DIGEST_PREFIX = "sha256:"


class WorkflowRegistryEntry(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    workflow_id: str = Field(alias="workflowId")
    version: str
    owner_ref: str = Field(alias="ownerRef")
    status: WorkflowStatus
    source_digest: str = Field(alias="sourceDigest")
    promotion_history: tuple[str, ...] = Field(default=(), alias="promotionHistory")
    compatible_runtime_contract_version: str = Field(alias="compatibleRuntimeContractVersion")

    @field_validator("workflow_id", "version", "owner_ref", "compatible_runtime_contract_version")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workflow registry identifiers must be non-empty")
        return value

    @field_validator("source_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value, "sourceDigest")

    @field_validator("promotion_history", mode="before")
    @classmethod
    def _normalize_history(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("promotionHistory must be an array of non-empty strings")
        values = tuple(value or ())  # type: ignore[arg-type]
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("promotionHistory must contain non-empty strings")
        return values


class WorkflowRegistry(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    entries: tuple[WorkflowRegistryEntry, ...]

    @model_validator(mode="after")
    def _reject_duplicate_versions(self) -> Self:
        seen: set[tuple[str, str]] = set()
        for entry in self.entries:
            key = (entry.workflow_id, entry.version)
            if key in seen:
                raise ValueError("duplicate workflow version")
            seen.add(key)
        return self


def build_workflow_registry(entries: tuple[WorkflowRegistryEntry, ...]) -> WorkflowRegistry:
    return WorkflowRegistry(entries=entries)


def require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value
