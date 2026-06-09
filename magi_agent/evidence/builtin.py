from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from .types import (
    BUILTIN_EVIDENCE_TYPES,
    EvidenceMetadataModel,
    EvidenceSourceKind,
    _validate_strict_bool,
    validate_evidence_type_name,
)


ProducerSurface = Literal[
    "tool_host",
    "artifact_service",
    "channel_adapter",
    "verifier",
    "plugin",
    "transcript",
    "adk_event",
]


class BuiltInEvidenceType(EvidenceMetadataModel):
    type: str
    description: str
    producer_surfaces: tuple[ProducerSurface, ...] = Field(alias="producerSurfaces")
    source_kinds: tuple[EvidenceSourceKind, ...] = Field(alias="sourceKinds")
    core_owned: Literal[True] = Field(default=True, alias="coreOwned")
    customizable: Literal[False] = False
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator(
        "core_owned",
        "customizable",
        "metadata_only",
        "traffic_attached",
        "execution_attached",
        mode="before",
    )
    @classmethod
    def _validate_strict_booleans(cls, value: object) -> object:
        return _validate_strict_bool(value, "built-in evidence boolean metadata")

    @field_validator("type")
    @classmethod
    def _validate_builtin_type(cls, value: str) -> str:
        validated = validate_evidence_type_name(value)
        if validated.startswith("custom:"):
            raise ValueError("custom evidence cannot be registered as a built-in catalog item")
        return validated

    @field_validator("description")
    @classmethod
    def _reject_empty_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("built-in evidence descriptions must be non-empty")
        return value

    @field_validator("producer_surfaces", "source_kinds")
    @classmethod
    def _reject_empty_tuples(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("built-in evidence metadata tuples must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_core_owned_metadata_only(self) -> Self:
        if not self.core_owned or self.customizable or not self.metadata_only:
            raise ValueError("built-in evidence catalog items must be core-owned metadata")
        return self


_BUILTIN_EVIDENCE_CATALOG: tuple[BuiltInEvidenceType, ...] = (
    BuiltInEvidenceType(
        type="GitDiff",
        description="Workspace diff evidence observed after code or document mutation.",
        producerSurfaces=("tool_host", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="TestRun",
        description="Command verification evidence, including command metadata and exit code.",
        producerSurfaces=("tool_host", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="CodeDiagnostics",
        description="Recorded code diagnostics metadata, including checker and zero-diagnostic result.",
        producerSurfaces=("tool_host", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="CommitCheckpoint",
        description="Commit/checkpoint metadata proving a durable workspace checkpoint was created.",
        producerSurfaces=("transcript", "adk_event"),
        sourceKinds=("transcript", "execution_contract"),
    ),
    BuiltInEvidenceType(
        type="FileDeliver",
        description="File or artifact delivery evidence normalized from delivery-capable tools.",
        producerSurfaces=("tool_host", "artifact_service", "channel_adapter", "transcript"),
        sourceKinds=("tool_trace", "artifact", "transcript"),
    ),
    BuiltInEvidenceType(
        type="ArtifactVerify",
        description="Artifact verification evidence owned by the artifact index and verifiers.",
        producerSurfaces=("artifact_service", "verifier", "transcript"),
        sourceKinds=("artifact", "verifier", "transcript"),
    ),
    BuiltInEvidenceType(
        type="DeterministicEvidenceVerifier",
        description="Deterministic verifier evidence produced by core-owned verifier metadata.",
        producerSurfaces=("verifier", "transcript"),
        sourceKinds=("verifier", "transcript"),
    ),
    BuiltInEvidenceType(
        type="WebSearch",
        description="Web search evidence normalized from ToolHost/plugin traces and ADK events.",
        producerSurfaces=("tool_host", "plugin", "transcript", "adk_event"),
        sourceKinds=("tool_trace", "adk_event", "transcript"),
    ),
    BuiltInEvidenceType(
        type="KnowledgeSearch",
        description="Knowledge base search evidence normalized from ToolHost/plugin traces.",
        producerSurfaces=("tool_host", "plugin", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="SourceInspection",
        description="Source inspection evidence for documents, URLs, and local files.",
        producerSurfaces=("tool_host", "verifier", "transcript"),
        sourceKinds=("tool_trace", "verifier", "transcript"),
    ),
    BuiltInEvidenceType(
        type="PlanVerifier",
        description="Plan verification evidence from verifier and artifact metadata.",
        producerSurfaces=("verifier", "artifact_service", "transcript"),
        sourceKinds=("verifier", "artifact", "transcript"),
    ),
    BuiltInEvidenceType(
        type="Calculation",
        description="Calculation evidence from deterministic tool traces or transcript metadata.",
        producerSurfaces=("tool_host", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="DateRange",
        description="Date range evidence from deterministic time/date tool traces.",
        producerSurfaces=("tool_host", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="Clock",
        description="Clock evidence from deterministic time lookup tool traces.",
        producerSurfaces=("tool_host", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="TelegramDeliveryAck",
        description="Telegram delivery acknowledgement evidence from channel metadata.",
        producerSurfaces=("channel_adapter", "transcript", "adk_event"),
        sourceKinds=("external_ack", "transcript", "adk_event"),
    ),
    BuiltInEvidenceType(
        type="PromptTransform",
        description=(
            "System-prompt transform evidence recorded when a beforeSystemPrompt "
            "hook replaces the assembled prompt sections (plugin-produced)."
        ),
        producerSurfaces=("plugin",),
        sourceKinds=("transcript",),
    ),
    BuiltInEvidenceType(
        type="EditMatch",
        description=(
            "Fuzzy file-edit match evidence produced by the core FileEdit tool "
            "boundary using digest-only matched-span metadata."
        ),
        producerSurfaces=("tool_host", "transcript"),
        sourceKinds=("tool_trace", "transcript"),
    ),
    BuiltInEvidenceType(
        type="DocumentCoverage",
        description=(
            "Deterministic source-content coverage evidence produced by the "
            "document-write boundary, verifying the rendered document contains "
            "the redacted source units using digest-only missing-unit metadata."
        ),
        producerSurfaces=("tool_host", "verifier", "transcript"),
        sourceKinds=("tool_trace", "verifier", "transcript"),
    ),
)

_BUILTIN_EVIDENCE_BY_TYPE: dict[str, BuiltInEvidenceType] = {
    item.type: item for item in _BUILTIN_EVIDENCE_CATALOG
}


def builtin_evidence_catalog() -> tuple[BuiltInEvidenceType, ...]:
    return tuple(item.model_copy(deep=True) for item in _BUILTIN_EVIDENCE_CATALOG)


def builtin_evidence_types() -> tuple[str, ...]:
    return BUILTIN_EVIDENCE_TYPES


def builtin_evidence_by_type(evidence_type: str) -> BuiltInEvidenceType | None:
    item = _BUILTIN_EVIDENCE_BY_TYPE.get(evidence_type)
    if item is None:
        return None
    return item.model_copy(deep=True)


__all__ = [
    "BuiltInEvidenceType",
    "ProducerSurface",
    "builtin_evidence_by_type",
    "builtin_evidence_catalog",
    "builtin_evidence_types",
]
