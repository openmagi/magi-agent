from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ConformancePhase = Literal["phase_0", "phase_1"]
StorageModel = Literal["file_snapshot", "external", "vector", "graph", "hybrid", "sql", "object"]
MemoryOperation = Literal[
    "remember",
    "search",
    "compact",
    "decay",
    "delete",
    "export",
    "conflict_resolve",
]
OperationSupport = Literal["metadata_only", "unsupported"]

OPERATION_ORDER: tuple[MemoryOperation, ...] = (
    "remember",
    "search",
    "compact",
    "decay",
    "delete",
    "export",
    "conflict_resolve",
)

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class MemoryConformanceAdkFirst(BaseModel):
    model_config = _MODEL_CONFIG

    adk_owns: list[str] = Field(alias="adkOwns")
    openmagi_owns: list[str] = Field(alias="openMagiOwns")
    memory_service_replacement_allowed: Literal[False] = Field(
        default=False,
        alias="memoryServiceReplacementAllowed",
    )
    provider_attachment_allowed: Literal[False] = Field(
        default=False,
        alias="providerAttachmentAllowed",
    )


class MemoryConformanceImportBoundary(BaseModel):
    model_config = _MODEL_CONFIG

    adk_memory_service_replaced: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceReplaced",
    )
    adk_memory_service_attached: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceAttached",
    )
    live_provider_calls: Literal[False] = Field(default=False, alias="liveProviderCalls")
    provider_sdk_imports: Literal[False] = Field(default=False, alias="providerSdkImports")
    agent_memory_calls: Literal[False] = Field(default=False, alias="agentMemoryCalls")
    hipocampus_qmd_calls: Literal[False] = Field(default=False, alias="hipocampusQmdCalls")
    prompt_projection: Literal[False] = Field(default=False, alias="promptProjection")
    memory_writes: Literal[False] = Field(default=False, alias="memoryWrites")
    routes_attached: Literal[False] = Field(default=False, alias="routesAttached")
    production_storage: Literal[False] = Field(default=False, alias="productionStorage")


class BitemporalFactContract(BaseModel):
    model_config = _MODEL_CONFIG

    bitemporal: bool
    valid_time: Literal["declared"] = Field(alias="validTime")
    transaction_time: Literal["declared"] = Field(alias="transactionTime")
    tenant_scope: Literal["required"] = Field(alias="tenantScope")
    source_authority: Literal["required"] = Field(alias="sourceAuthority")
    redaction_status: Literal["required_before_projection"] = Field(alias="redactionStatus")
    receipt_semantics: Literal["required_before_write_claim"] = Field(alias="receiptSemantics")
    audit_evidence_ref: str = Field(alias="auditEvidenceRef")


class OperationEnvelope(BaseModel):
    model_config = _MODEL_CONFIG

    operation: MemoryOperation
    support: OperationSupport
    failure_code: str = Field(alias="failureCode")
    dry_run: bool = Field(alias="dryRun")
    executes_provider: bool = Field(default=False, alias="executesProvider")
    mutates_memory: bool = Field(default=False, alias="mutatesMemory")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @model_validator(mode="after")
    def _validate_metadata_only_envelope(self) -> "OperationEnvelope":
        if self.executes_provider:
            raise ValueError("operation envelopes cannot execute provider calls")
        if self.mutates_memory:
            raise ValueError("operation envelopes cannot allow mutation")
        if not self.failure_code.startswith("memory_provider_"):
            raise ValueError("operation failure codes must use memory_provider_ prefix")
        if self.support == "unsupported" and self.dry_run:
            raise ValueError("unsupported operations cannot claim dry-run support")
        return self


class MemoryProviderConformance(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    phase: ConformancePhase
    storage_model: StorageModel = Field(alias="storageModel")
    provider_call_allowed: bool = Field(default=False, alias="providerCallAllowed")
    import_or_sdk_allowed: bool = Field(default=False, alias="importOrSdkAllowed")
    prompt_projection_allowed: bool = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    memory_write_allowed: bool = Field(default=False, alias="memoryWriteAllowed")
    fact_contract: BitemporalFactContract = Field(alias="factContract")
    lifecycle_tiers: tuple[Literal["hot", "warm", "cold", "tombstone"], ...] = Field(
        alias="lifecycleTiers",
    )
    dry_run_maintenance: dict[str, str] = Field(alias="dryRunMaintenance")
    operation_envelopes: tuple[OperationEnvelope, ...] = Field(alias="operationEnvelopes")

    @property
    def operations(self) -> tuple[MemoryOperation, ...]:
        return tuple(envelope.operation for envelope in self.operation_envelopes)

    @model_validator(mode="after")
    def _validate_provider_metadata_only(self) -> "MemoryProviderConformance":
        if self.provider_call_allowed:
            raise ValueError("provider calls are disabled for conformance metadata")
        if self.import_or_sdk_allowed:
            raise ValueError("provider SDK imports are disabled for conformance metadata")
        if self.prompt_projection_allowed:
            raise ValueError("prompt projection is disabled for conformance metadata")
        if self.memory_write_allowed:
            raise ValueError("memory writes are disabled for conformance metadata")
        if self.operations != OPERATION_ORDER:
            raise ValueError("provider operation envelopes must use canonical operation order")
        if self.lifecycle_tiers != ("hot", "warm", "cold", "tombstone"):
            raise ValueError("lifecycle tiers must declare hot/warm/cold/tombstone metadata")
        if not self.fact_contract.bitemporal:
            raise ValueError("provider conformance must declare bitemporal fact metadata")
        return self


class MemoryProviderConformanceFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["memoryProviderConformanceMatrix.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["python-adk-parity-fixture"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    phase: Literal["phase_0_1_metadata_only"]
    adk_first: MemoryConformanceAdkFirst = Field(alias="adkFirst")
    import_boundary: MemoryConformanceImportBoundary = Field(alias="importBoundary")
    providers: tuple[MemoryProviderConformance, ...]

    @model_validator(mode="after")
    def _validate_fixture_metadata_only(self) -> "MemoryProviderConformanceFixture":
        if any(self.import_boundary.model_dump(by_alias=True).values()):
            raise ValueError("import boundary must disable all live memory runtime flags")
        if not self.providers:
            raise ValueError("provider conformance fixture must include providers")
        provider_ids = [provider.provider_id for provider in self.providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider ids must be unique")
        return self


class MemoryProviderConformanceProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    provider_ids: tuple[str, ...] = Field(alias="providerIds")
    operation_order: tuple[MemoryOperation, ...] = Field(alias="operationOrder")
    provider_count_by_phase: dict[str, int] = Field(alias="providerCountByPhase")
    metadata_only: bool = Field(alias="metadataOnly")
    no_live_runtime: bool = Field(alias="noLiveRuntime")
    support_matrix: dict[str, dict[str, dict[str, object]]] = Field(alias="supportMatrix")


def load_memory_provider_conformance_fixture(
    filename: str,
    *,
    fixture_root: Path,
) -> MemoryProviderConformanceFixture:
    payload = json.loads((fixture_root / filename).read_text(encoding="utf-8"))
    return MemoryProviderConformanceFixture.model_validate(payload)


def project_memory_provider_conformance_fixture(
    fixture: MemoryProviderConformanceFixture,
) -> MemoryProviderConformanceProjection:
    support_matrix: dict[str, dict[str, dict[str, object]]] = {}
    for provider in fixture.providers:
        support_matrix[provider.provider_id] = {
            envelope.operation: {
                "support": envelope.support,
                "failureCode": envelope.failure_code,
                "dryRun": envelope.dry_run,
            }
            for envelope in provider.operation_envelopes
        }

    no_live_runtime = not any(fixture.import_boundary.model_dump(by_alias=True).values())
    metadata_only = no_live_runtime and all(
        not provider.provider_call_allowed
        and not provider.import_or_sdk_allowed
        and not provider.prompt_projection_allowed
        and not provider.memory_write_allowed
        and all(
            not envelope.executes_provider and not envelope.mutates_memory
            for envelope in provider.operation_envelopes
        )
        for provider in fixture.providers
    )
    phase_counts = Counter(provider.phase for provider in fixture.providers)

    return MemoryProviderConformanceProjection(
        fixture_id=fixture.fixture_id,
        provider_ids=tuple(provider.provider_id for provider in fixture.providers),
        operation_order=OPERATION_ORDER,
        provider_count_by_phase=dict(sorted(phase_counts.items())),
        metadata_only=metadata_only,
        no_live_runtime=no_live_runtime,
        support_matrix=support_matrix,
    )
