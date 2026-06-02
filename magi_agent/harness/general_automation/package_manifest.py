from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.tools.manifest import (
    AdkToolType,
    ParallelSafety,
    PermissionClass,
    SideEffectClass,
)


ExecutionAttachment = Literal["none", "requested"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PACKAGE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}(?:[-+][A-Za-z0-9_.-]+)?$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,80}$")
_DEPENDENCY_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_CREDENTIAL_HANDLE_RE = re.compile(r"^credential:[A-Za-z0-9_.:-]{3,160}$")


class AutomationPackageDependency(BaseModel):
    model_config = _MODEL_CONFIG

    name: str
    version: str
    implicit_install: bool = Field(default=False, alias="implicitInstall")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _DEPENDENCY_NAME_RE.fullmatch(value):
            raise ValueError("dependency name must be a safe package identifier")
        return value

    @model_validator(mode="after")
    def _reject_implicit_install(self) -> Self:
        if self.implicit_install:
            raise ValueError("dependencies must not install implicitly")
        return self


class SealedCredentialHandle(BaseModel):
    model_config = _MODEL_CONFIG

    handle: str
    purpose: str

    @field_validator("handle")
    @classmethod
    def _validate_handle(cls, value: str) -> str:
        if not _CREDENTIAL_HANDLE_RE.fullmatch(value):
            raise ValueError("credential handles must be sealed public handles")
        return value

    @field_validator("purpose")
    @classmethod
    def _validate_purpose(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("credential purpose must be non-empty")
        return cleaned[:160]


class AutomationOutputBudget(BaseModel):
    model_config = _MODEL_CONFIG

    output_chars: int | None = Field(default=None, alias="outputChars", ge=1)
    transcript_chars: int | None = Field(default=None, alias="transcriptChars", ge=1)


class AutomationToolDeclaration(BaseModel):
    model_config = _MODEL_CONFIG

    name: str
    description: str
    input_schema: dict[str, object] = Field(alias="inputSchema")
    output_schema: dict[str, object] | None = Field(default=None, alias="outputSchema")
    permission: PermissionClass
    side_effect_class: SideEffectClass = Field(default="none", alias="sideEffectClass")
    parallel_safety: ParallelSafety = Field(default="unsafe", alias="parallelSafety")
    output_budget: AutomationOutputBudget = Field(
        default_factory=AutomationOutputBudget,
        alias="outputBudget",
    )
    credential_handles: tuple[SealedCredentialHandle, ...] = Field(
        default=(),
        alias="credentialHandles",
    )
    execution_attachment: ExecutionAttachment = Field(default="none", alias="executionAttachment")
    adk_tool_type: AdkToolType = Field(default="FunctionTool", alias="adkToolType")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _TOOL_NAME_RE.fullmatch(value):
            raise ValueError("automation tool names must be safe public identifiers")
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("automation tool description must be non-empty")
        return cleaned[:512]

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _copy_schema(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is None:
            return None
        if value.get("type") != "object":
            raise ValueError("automation tool schemas must be JSON object schemas")
        return copy.deepcopy(value)


class AutomationPackageManifest(BaseModel):
    model_config = _MODEL_CONFIG

    package_id: str = Field(alias="packageId")
    version: str
    publisher: str
    signed: bool = False
    signature_digest: str | None = Field(default=None, alias="signatureDigest")
    dependencies: tuple[AutomationPackageDependency, ...] = ()
    tools: tuple[AutomationToolDeclaration, ...] = ()

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        if not _PACKAGE_ID_RE.fullmatch(value):
            raise ValueError("packageId must be a dotted safe namespace")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if not _VERSION_RE.fullmatch(value):
            raise ValueError("version must be a simple semantic version")
        return value

    @field_validator("publisher")
    @classmethod
    def _validate_publisher(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("publisher must be non-empty")
        return cleaned[:120]

    @field_validator("signature_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        suffix = value.removeprefix("sha256:")
        if not value.startswith("sha256:") or len(suffix) != 64 or any(
            char not in "0123456789abcdef" for char in suffix
        ):
            raise ValueError("signatureDigest must be a sha256 digest")
        return value

    @model_validator(mode="after")
    def _validate_metadata_only_boundary(self) -> Self:
        if self.signed and self.signature_digest is None:
            raise ValueError("signed packages must include a signatureDigest")
        if not self.signed:
            requested = tuple(
                tool.name for tool in self.tools if tool.execution_attachment == "requested"
            )
            if requested:
                raise ValueError("unsigned packages cannot request execution attachment")
        return self

    @property
    def package_ref(self) -> str:
        return f"automation-package:{self.package_id}@{self.version}"


def parse_automation_package_manifest(data: Mapping[str, object]) -> AutomationPackageManifest:
    if not isinstance(data, Mapping):
        raise ValueError("automation package manifest input must be a mapping")
    return AutomationPackageManifest.model_validate(data)


__all__ = [
    "AutomationOutputBudget",
    "AutomationPackageDependency",
    "AutomationPackageManifest",
    "AutomationToolDeclaration",
    "SealedCredentialHandle",
    "parse_automation_package_manifest",
]
