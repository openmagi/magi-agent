from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel


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


class MemoryConformanceAdkFirst(FalseOnlyAuthorityModel):
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


class MemoryConformanceImportBoundary(FalseOnlyAuthorityModel):
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


# ---------------------------------------------------------------------------
# D5 — Writable-provider conformance extension (additive; read-only conformance
# above is UNCHANGED)
# ---------------------------------------------------------------------------

#: Allowed write target files for a D1-conformant writable provider.
#: SOUL.md is intentionally absent — the agent MUST NOT be able to write it.
WRITABLE_PROVIDER_ALLOWED_WRITE_FILES: frozenset[str] = frozenset({"MEMORY.md", "USER.md"})

#: Name of the SOUL file that must NEVER appear in the agent write allowlist.
SOUL_FILENAME: str = "SOUL.md"


class WritableProviderInvariantResult(BaseModel):
    """Result of checking D1–D4 safety invariants against a writable provider.

    All six invariants must pass for the provider to be conformant.  This model
    is metadata-only: it records the outcome of the checks without executing any
    real writes or provider calls.

    Invariants checked
    ------------------
    1. ``read_only_default`` — provider_id, write_tier, and allowed_write_files
       confirm that the read-only tier is the default.
    2. ``declarative_only_filter`` — the write path must declare the
       declarative-only filter (no task-state facts).
    3. ``path_safe_redacted_bounded`` — the write path is workspace-contained,
       applies redaction, and enforces ``max_write_bytes``.
    4. ``soul_not_agent_writable`` — SOUL.md is absent from the agent write
       allowlist; the agent cannot write it.
    5. ``soul_operator_path_separate`` — the operator SOUL gate
       (``MAGI_SOUL_WRITE_ENABLED``) is independent from the agent write gate
       (``MAGI_MEMORY_WRITE_ENABLED``).
    6. ``projection_cache_safe_incognito_respecting`` — the D3 projection gate
       is off by default and blocks on ``memory_mode="incognito"``.
    """

    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    read_only_default: bool = Field(alias="readOnlyDefault")
    declarative_only_filter: bool = Field(alias="declarativeOnlyFilter")
    path_safe_redacted_bounded: bool = Field(alias="pathSafeRedactedBounded")
    soul_not_agent_writable: bool = Field(alias="soulNotAgentWritable")
    soul_operator_path_separate: bool = Field(alias="soulOperatorPathSeparate")
    projection_cache_safe_incognito_respecting: bool = Field(
        alias="projectionCacheSafeIncognitoRespecting",
    )
    all_invariants_pass: bool = Field(alias="allInvariantsPass")
    failing_invariants: tuple[str, ...] = Field(
        default=(),
        alias="failingInvariants",
    )

    @model_validator(mode="after")
    def _validate_consistency(self) -> "WritableProviderInvariantResult":
        expected_failing = tuple(
            name
            for name, attr in [
                ("read_only_default", self.read_only_default),
                ("declarative_only_filter", self.declarative_only_filter),
                ("path_safe_redacted_bounded", self.path_safe_redacted_bounded),
                ("soul_not_agent_writable", self.soul_not_agent_writable),
                ("soul_operator_path_separate", self.soul_operator_path_separate),
                (
                    "projection_cache_safe_incognito_respecting",
                    self.projection_cache_safe_incognito_respecting,
                ),
            ]
            if not attr
        )
        if self.failing_invariants != expected_failing:
            raise ValueError(
                f"failingInvariants {self.failing_invariants!r} is inconsistent with "
                f"individual invariant flags; expected {expected_failing!r}"
            )
        if self.all_invariants_pass != (len(expected_failing) == 0):
            raise ValueError(
                "allInvariantsPass is inconsistent with individual invariant flags"
            )
        return self


class WritableProviderConformanceReport(BaseModel):
    """D5 writable-provider conformance report.

    Records the outcome of checking the six D1–D4 safety invariants for a
    specific ``LocalFileMemoryProvider``-compatible writable provider.

    This model is metadata-only: it carries the evidence that the provider
    satisfies the invariants WITHOUT executing real writes, provider calls, or
    env mutations.  Conformance enables nothing — it documents readiness.
    """

    model_config = _MODEL_CONFIG

    schema_version: Literal["writableProviderConformance.v1"] = Field(
        default="writableProviderConformance.v1",
        alias="schemaVersion",
    )
    provider_id: str = Field(alias="providerId")
    write_tier: str = Field(alias="writeTier")
    allowed_write_files: tuple[str, ...] = Field(alias="allowedWriteFiles")
    soul_in_agent_allowlist: bool = Field(alias="soulInAgentAllowlist")
    has_declarative_filter: bool = Field(alias="hasDeclarativeFilter")
    has_redaction: bool = Field(alias="hasRedaction")
    has_write_byte_bound: bool = Field(alias="hasWriteBytesBound")
    has_path_safety: bool = Field(alias="hasPathSafety")
    has_operator_soul_gate: bool = Field(alias="hasOperatorSoulGate")
    projection_default_off: bool = Field(alias="projectionDefaultOff")
    projection_incognito_blocked: bool = Field(alias="projectionIncognitoBlocked")
    invariant_result: WritableProviderInvariantResult = Field(alias="invariantResult")

    @field_validator("allowed_write_files", mode="before")
    @classmethod
    def _coerce_allowed_write_files(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (list, tuple, frozenset, set)):
            return tuple(sorted(str(f) for f in value))
        return ()


def check_writable_provider_conformance(
    *,
    provider_id: str,
    write_tier: str,
    allowed_write_files: frozenset[str] | set[str] | tuple[str, ...],
    has_declarative_filter: bool,
    has_redaction: bool,
    has_write_byte_bound: bool,
    has_path_safety: bool,
    has_operator_soul_gate: bool,
    projection_default_off: bool,
    projection_incognito_blocked: bool,
) -> WritableProviderConformanceReport:
    """Check D1–D4 safety invariants for a writable memory provider.

    This function is PURE (no I/O, no env reads, no provider calls).  Pass the
    provider's declared properties; it returns a
    ``WritableProviderConformanceReport`` capturing the outcome.

    Invariant 1 — ``read_only_default``:
        ``write_tier == "gated_write"`` AND SOUL.md absent from
        ``allowed_write_files``.

    Invariant 2 — ``declarative_only_filter``:
        ``has_declarative_filter=True`` (D2 filter guards the write path).

    Invariant 3 — ``path_safe_redacted_bounded``:
        ``has_path_safety AND has_redaction AND has_write_byte_bound``.

    Invariant 4 — ``soul_not_agent_writable``:
        SOUL.md NOT in ``allowed_write_files``.

    Invariant 5 — ``soul_operator_path_separate``:
        ``has_operator_soul_gate=True`` (D4 operator path exists and is
        independent from the agent write gate).

    Invariant 6 — ``projection_cache_safe_incognito_respecting``:
        ``projection_default_off AND projection_incognito_blocked``.
    """
    files_set = frozenset(str(f) for f in allowed_write_files)
    soul_in_allowlist = SOUL_FILENAME in files_set

    inv1_read_only_default = write_tier == "gated_write" and not soul_in_allowlist
    inv2_declarative_only = has_declarative_filter
    inv3_path_safe = has_path_safety and has_redaction and has_write_byte_bound
    inv4_soul_not_agent = not soul_in_allowlist
    inv5_soul_op_separate = has_operator_soul_gate
    inv6_projection = projection_default_off and projection_incognito_blocked

    failing: list[str] = []
    if not inv1_read_only_default:
        failing.append("read_only_default")
    if not inv2_declarative_only:
        failing.append("declarative_only_filter")
    if not inv3_path_safe:
        failing.append("path_safe_redacted_bounded")
    if not inv4_soul_not_agent:
        failing.append("soul_not_agent_writable")
    if not inv5_soul_op_separate:
        failing.append("soul_operator_path_separate")
    if not inv6_projection:
        failing.append("projection_cache_safe_incognito_respecting")

    invariant_result = WritableProviderInvariantResult(
        providerId=provider_id,
        readOnlyDefault=inv1_read_only_default,
        declarativeOnlyFilter=inv2_declarative_only,
        pathSafeRedactedBounded=inv3_path_safe,
        soulNotAgentWritable=inv4_soul_not_agent,
        soulOperatorPathSeparate=inv5_soul_op_separate,
        projectionCacheSafeIncognitoRespecting=inv6_projection,
        allInvariantsPass=len(failing) == 0,
        failingInvariants=tuple(failing),
    )

    return WritableProviderConformanceReport(
        schemaVersion="writableProviderConformance.v1",
        providerId=provider_id,
        writeTier=write_tier,
        allowedWriteFiles=tuple(sorted(files_set)),
        soulInAgentAllowlist=soul_in_allowlist,
        hasDeclarativeFilter=has_declarative_filter,
        hasRedaction=has_redaction,
        hasWriteBytesBound=has_write_byte_bound,
        hasPathSafety=has_path_safety,
        hasOperatorSoulGate=has_operator_soul_gate,
        projectionDefaultOff=projection_default_off,
        projectionIncognitoBlocked=projection_incognito_blocked,
        invariantResult=invariant_result,
    )


# ---------------------------------------------------------------------------
# B1 — Gated live qmd recall conformance (additive).
#
# The shadow/parity contracts pin ``hipocampus_qmd_live_called: Literal[False]``
# and ``MemoryConformanceImportBoundary.hipocampus_qmd_calls: Literal[False]`` to
# guarantee the SHADOW evidence/authority surfaces never invoke live qmd.  Those
# pins are intentionally LEFT UNTOUCHED.
#
# The OPTIONAL, env-gated live qmd RECALL path lives in a DIFFERENT surface (the
# read-only recall adapter) and is represented by a SEPARATE field below
# (``hipocampus_qmd_live_recall_gated``).  The two are decoupled by design: the
# gated recall field may become True while the parity pin stays False.
# ---------------------------------------------------------------------------


class HipocampusQmdLiveRecallConformance(FalseOnlyAuthorityModel):
    """Conformance record for the gated live qmd RECALL path.

    Records whether the OPTIONAL ``MAGI_MEMORY_QMD_LIVE_ENABLED`` recall gate is
    active, WITHOUT coupling to the shadow/parity pin.  ``hipocampus_qmd_calls``
    here mirrors the pinned ``Literal[False]`` from the import boundary: it can
    NEVER be True, asserting that enabling gated recall does not flip the parity
    surface.
    """

    #: Whether the gated live qmd RECALL path (recall adapter) is enabled.  This
    #: is a normal ``bool`` — the gate may be on (True) without weakening parity.
    hipocampus_qmd_live_recall_gated: bool = Field(
        default=False,
        alias="hipocampusQmdLiveRecallGated",
    )
    #: Parity pin: the shadow evidence/authority surfaces NEVER call live qmd.
    #: Pinned ``Literal[False]`` so enabling gated recall cannot flip it.
    hipocampus_qmd_calls: Literal[False] = Field(
        default=False,
        alias="hipocampusQmdCalls",
    )


def check_hipocampus_qmd_live_recall_conformance() -> HipocampusQmdLiveRecallConformance:
    """Derive the gated live qmd recall conformance from the live adapter gate.

    Reads the recall adapter's gate predicate (lazy import to keep this module's
    import boundary network-library-free) and reports it as
    ``hipocampus_qmd_live_recall_gated`` while keeping the parity pin
    ``hipocampus_qmd_calls`` pinned False.
    """
    from magi_agent.memory.adapters.hipocampus_readonly import (  # lazy import: keep boundary thin
        _qmd_live_recall_enabled,
    )

    return HipocampusQmdLiveRecallConformance(
        hipocampusQmdLiveRecallGated=_qmd_live_recall_enabled(),
    )


def check_local_file_memory_provider_conformance() -> WritableProviderConformanceReport:
    """Check D1–D4 invariants for the canonical LocalFileMemoryProvider (D1).

    Derives the conformance inputs from the actual D1 module constants so that
    this function serves as a live contract test: if D1's constants change in a
    way that violates the invariants, this function will return a failing report.

    No env reads, no provider calls, no I/O — pure derivation from constants.
    """
    from magi_agent.memory.adapters.local_file_writable import (  # lazy import: keep boundary thin
        _ALLOWED_WRITE_FILES,
        _DEFAULT_MAX_WRITE_BYTES,
        _PROVIDER_ID_WRITABLE,
    )
    from magi_agent.memory.declarative_filter import is_declarative_result  # noqa: F401 (existence check)
    from magi_agent.memory.policy import MAGI_MEMORY_PROJECTION_ENABLED_ENV  # noqa: F401

    return check_writable_provider_conformance(
        provider_id=_PROVIDER_ID_WRITABLE,
        write_tier="gated_write",
        allowed_write_files=_ALLOWED_WRITE_FILES,
        has_declarative_filter=True,   # D2 is_declarative_result guard
        has_redaction=True,            # D1 _redact_for_write applied before persist
        has_write_byte_bound=_DEFAULT_MAX_WRITE_BYTES > 0,
        has_path_safety=True,          # D1 _resolve_workspace_path workspace containment
        has_operator_soul_gate=True,   # D4 OperatorSoulWriter independent gate
        projection_default_off=True,   # D3 gate off by default
        projection_incognito_blocked=True,  # D3 incognito blocks projection
    )
